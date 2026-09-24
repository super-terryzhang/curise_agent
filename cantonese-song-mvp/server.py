from __future__ import annotations
import ctypes,gc,io,json,math,os,pathlib,re,threading,time,urllib.error,urllib.request,wave
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlparse,parse_qs
import numpy as np
import sherpa_onnx
from opencc import OpenCC

ROOT=pathlib.Path(__file__).resolve().parent
PORT=int(os.environ.get("PORT","10000"))
ASR_DIR=ROOT/"model"/"asr"
LOCAL_TTS_DIR=ROOT/"model"/"_tts_build"
TTS_URLS=[
    x.strip() for x in os.environ.get(
        "TTS_URLS",
        "https://terry-cantonese-tts-stable.onrender.com/api/tts,"
        "https://terry-cantonese-vits.onrender.com/api/tts,"
        "https://terry-cantonese-tonelab.onrender.com/api/tts"
    ).split(",") if x.strip()
]
FIXED_AUDIO={
    0:{0.60:ROOT/"reference"/"line0_slow.wav",0.75:ROOT/"reference"/"line0_clear.wav",0.88:ROOT/"reference"/"line0.wav"},
    1:{0.60:ROOT/"reference"/"line1_slow.wav",0.75:ROOT/"reference"/"line1_clear.wav",0.88:ROOT/"reference"/"line1.wav"},
}
t2s=OpenCC("t2s")
s2t=OpenCC("s2t")
jp_js=(ROOT.parent/"cantonese-coach-mvp"/"vendor"/"cantojpmin_data.js").read_text(encoding="utf-8")
jp_dict=json.loads(jp_js[jp_js.index("{"):jp_js.rfind("}")+1])

SONG=[
 {"id":0,"text":"流水像清得沒帶半顆沙","jyutping":["lau4","seoi2","zoeng6","cing1","dak1","mut6","daai3","bun3","fo2","saa1"]},
 {"id":1,"text":"前身被擱在上游風化","jyutping":["cin4","san1","bei6","gok3","zoi6","soeng6","jau4","fung1","faa3"]},
]
for line in SONG:
    line["chars"]=list(line["text"])
    line["tones"]=[int(x[-1]) for x in line["jyutping"]]

engine_lock=threading.RLock()
asr_lock=threading.Lock()
asr=None

def release_native_memory():
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass

def create_asr():
    print("[engine] loading Cantonese ASR",flush=True)
    obj=sherpa_onnx.OfflineRecognizer.from_wenet_ctc(
        model=str(ASR_DIR/"model.int8.onnx"),
        tokens=str(ASR_DIR/"tokens.txt"),
        num_threads=2,sample_rate=16000,feature_dim=80,
        decoding_method="greedy_search",provider="cpu")
    print("[engine] ASR ready",flush=True)
    return obj

def ensure_asr():
    global asr
    if asr is None:asr=create_asr()
    return asr

def unload_asr():
    global asr
    if asr is not None:
        print("[engine] unloading ASR for local TTS",flush=True)
        asr=None
        release_native_memory()

asr=create_asr()
REFERENCE={}
reference_lock=threading.Lock()
CLICK_REFERENCE={}
click_reference_lock=threading.Lock()

def fixed_audio(text:str,speed:float):
    cleaned=text.strip();requested=float(speed)
    for i,line in enumerate(SONG):
        if cleaned!=line["text"]:continue
        for variant_speed,path in FIXED_AUDIO.get(i,{}).items():
            if abs(requested-variant_speed)<.025 and path.is_file() and path.stat().st_size>3000:
                return path.read_bytes()
    return None

def create_local_tts():
    required=[
        LOCAL_TTS_DIR/"vits-cantonese-hf-xiaomaiiwn.onnx",
        LOCAL_TTS_DIR/"lexicon.txt",
        LOCAL_TTS_DIR/"tokens.txt",
        LOCAL_TTS_DIR/"rule.fst",
    ]
    if not all(p.is_file() for p in required):
        raise RuntimeError("本地 VITS 模型檔案不存在")
    cfg=sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                model=str(required[0]),lexicon=str(required[1]),tokens=str(required[2]),length_scale=1.0),
            provider="cpu",debug=False,num_threads=1),
        rule_fsts=str(required[3]),rule_fars="",max_num_sentences=1)
    if not cfg.validate():raise RuntimeError("本地 VITS 設定無效")
    return sherpa_onnx.OfflineTts(cfg)

def generate_local_variants(text):
    # ASR and VITS each fit the free instance independently, but not together.
    # Serialize all engine work, unload ASR, generate all learning speeds once,
    # release VITS, then restore ASR.
    speeds=(.60,.75,.88)
    with engine_lock:
        missing=[]
        with tts_cache_lock:
            for sp in speeds:
                if (text,round(sp,2)) not in TTS_CACHE:missing.append(sp)
        if not missing:return

        unload_asr()
        tts=None
        started=time.time()
        try:
            print(f"[local-tts] loading VITS chars={len(text)} variants={missing}",flush=True)
            tts=create_local_tts()
            for sp in missing:
                audio=tts.generate(text=t2s.convert(text),sid=0,speed=sp)
                samples=np.asarray(audio.samples,dtype=np.float32)
                if samples.size<100:raise RuntimeError("本地 VITS 生成空音訊")
                raw=wav_bytes(samples,int(audio.sample_rate))
                with tts_cache_lock:
                    if len(TTS_CACHE)>=96:TTS_CACHE.pop(next(iter(TTS_CACHE)))
                    TTS_CACHE[(text,round(sp,2))]=raw
                print(f"[local-tts] cached speed={sp:.2f} bytes={len(raw)}",flush=True)
        finally:
            if tts is not None:del tts
            release_native_memory()
            ensure_asr()
            print(f"[local-tts] engine restored elapsed={time.time()-started:.2f}s",flush=True)

def proxy_tts(text:str,speed:float=.9)->bytes:
    text=text.strip()
    if not text:raise ValueError("文字為空")
    local=fixed_audio(text,speed)
    if local is not None:return local
    cache_key=(text,round(float(speed),2))
    with tts_cache_lock:cached=TTS_CACHE.get(cache_key)
    if cached is not None:return cached

    # Dynamic lyrics use the same local VITS model as the fixed demo. This
    # avoids dependence on sleeping external Render services.
    try:
        generate_local_variants(text)
        with tts_cache_lock:cached=TTS_CACHE.get(cache_key)
        if cached is not None:return cached
    except Exception as local_error:
        print(f"[local-tts] failed, falling back to remote: {type(local_error).__name__}: {local_error}",flush=True)

    payload=json.dumps({"text":text,"speed":max(.60,min(1.35,float(speed)))},ensure_ascii=False).encode("utf-8")
    waits=[0,3,7,12,20,30]
    last=None
    for round_idx,wait in enumerate(waits):
        if wait:time.sleep(wait)
        for url in TTS_URLS:
            req=urllib.request.Request(url,data=payload,headers={"Content-Type":"application/json","User-Agent":"cantonese-song-coach/1.0"},method="POST")
            try:
                with urllib.request.urlopen(req,timeout=120) as r:raw=r.read()
                if len(raw)<1000:raise RuntimeError("VITS 標準音回傳異常")
                with tts_cache_lock:
                    if len(TTS_CACHE)>=96:TTS_CACHE.pop(next(iter(TTS_CACHE)))
                    TTS_CACHE[cache_key]=raw
                if round_idx:
                    print(f"[tts-failover] recovered round={round_idx+1} host={url.split('/')[2]}",flush=True)
                return raw
            except urllib.error.HTTPError as e:
                last=e
                if e.code not in (502,503,504):break
            except Exception as e:
                last=e
        print(f"[tts-failover] round {round_idx+1} unavailable across {len(TTS_URLS)} services",flush=True)
    raise RuntimeError(f"VITS 服務暫時不可用：{last}")


def read_wav(raw:bytes):
    with wave.open(io.BytesIO(raw),"rb") as w:
        if w.getsampwidth()!=2: raise ValueError("錄音必須是 16-bit WAV")
        sr=w.getframerate();ch=w.getnchannels();data=np.frombuffer(w.readframes(w.getnframes()),dtype="<i2").astype(np.float32)/32768.0
    if ch>1:data=data.reshape(-1,ch).mean(axis=1)
    return data,sr

def wav_bytes(samples,sr):
    samples=np.asarray(samples,dtype=np.float32)
    if samples.size==0:raise ValueError("空音訊")
    pcm=(np.clip(samples,-1,1)*32767).astype("<i2")
    out=io.BytesIO()
    with wave.open(out,"wb") as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(int(sr));w.writeframes(pcm.tobytes())
    return out.getvalue()

def padded_clip(samples,sr,start,end):
    a=max(0,int(float(start)*sr));b=min(len(samples),int(float(end)*sr))
    if b<=a:return None
    seg=np.asarray(samples[a:b],dtype=np.float32).copy()
    fade=max(1,int(.008*sr))
    if seg.size>2*fade:
        ramp=np.linspace(0,1,fade,dtype=np.float32)
        seg[:fade]*=ramp;seg[-fade:]*=ramp[::-1]
    lead=np.zeros(int(.045*sr),dtype=np.float32)
    tail=np.zeros(int(.090*sr),dtype=np.float32)
    return wav_bytes(np.concatenate([lead,seg,tail]),sr)

def recognize(samples,sr):
    with engine_lock:
        recognizer=ensure_asr()
        st=recognizer.create_stream();st.accept_waveform(sr,samples)
        with asr_lock:recognizer.decode_stream(st)
        r=st.result
        return {
        "text":str(r.text),
        "tokens":list(r.tokens),
        "timestamps":[float(x) for x in r.timestamps],
        "durations":[float(x) for x in getattr(r,"durations",[])],
        "log_probs":[float(x) for x in getattr(r,"ys_log_probs",[])],
    }

def flatten_tokens(tokens,times,log_probs=None,durations=None):
    log_probs=log_probs or [];durations=durations or []
    out=[];ts=[];lps=[];durs=[]
    for i,tok in enumerate(tokens):
        chars=list(str(tok).strip())
        if not chars: continue
        start=times[i] if i<len(times) else (ts[-1] if ts else 0.0)
        total_dur=durations[i] if i<len(durations) and durations[i]>0 else (
            (times[i+1]-start) if i+1<len(times) else .22
        )
        per=max(.03,total_dur/max(1,len(chars)))
        lp=log_probs[i] if i<len(log_probs) else None
        for j,ch in enumerate(chars):
            out.append(ch);ts.append(start+per*j);lps.append(lp);durs.append(per)
    return out,ts,lps,durs

def jp_parts(jp):
    m=re.fullmatch(r"([a-z]+?)([1-6])",str(jp or "").strip().lower())
    return (m.group(1),int(m.group(2))) if m else (None,None)

INITIALS=("gw","kw","ng","b","p","m","f","d","t","n","l","g","k","h","w","z","c","s","j")

def split_jyutping(jp):
    base,tone=jp_parts(jp)
    if not base:return {"base":None,"tone":None,"initial":None,"final":None}
    if base in ("m","ng"):
        return {"base":base,"tone":tone,"initial":"∅","final":base}
    initial="∅"
    for x in INITIALS:
        if base.startswith(x):
            initial=x
            break
    final=base if initial=="∅" else base[len(initial):]
    return {"base":base,"tone":tone,"initial":initial,"final":final or "∅"}

def recognized_jyutping(chars):
    out=[]
    for ch in chars:
        variants=None
        for key in (ch,s2t.convert(ch),t2s.convert(ch)):
            raw=jp_dict.get(key)
            if raw:
                variants=re.split(r"[/.]",raw)
                break
        out.append(variants[0] if variants else None)
    return out

MAX_LYRIC_LINES=60
MAX_LINE_CHARS=120
DYNAMIC_REFERENCE={}
DYNAMIC_CLICK_REFERENCE={}
dynamic_reference_lock=threading.Lock()
dynamic_click_lock=threading.Lock()
TTS_CACHE={}
tts_cache_lock=threading.Lock()

def lookup_jyutping_char(ch):
    for key in (ch,s2t.convert(ch),t2s.convert(ch)):
        raw=jp_dict.get(key)
        if not raw:continue
        for candidate in re.split(r"[/.]",raw):
            candidate=str(candidate or "").strip().lower()
            if jp_parts(candidate)[1] is not None:return candidate
    return None

def make_line(text):
    display=str(text or "").strip()
    if not display:raise ValueError("歌詞行為空")
    if len(display)>MAX_LINE_CHARS:raise ValueError(f"單行歌詞最多 {MAX_LINE_CHARS} 個字元")
    chars=[];jyutping=[];source_indices=[];unsupported=[]
    for idx,ch in enumerate(display):
        jp=lookup_jyutping_char(ch)
        if jp:
            chars.append(ch);jyutping.append(jp);source_indices.append(idx)
        elif ch.strip() and not re.fullmatch(r"[，。！？、；：,.!?;:'\"“”‘’（）()\[\]【】《》〈〉…—\-·~～]",ch):
            unsupported.append(ch)
    if not chars:raise ValueError("這一行沒有找到可評估的粵語漢字")
    return {
        "text":display,"chars":chars,"jyutping":jyutping,
        "tones":[int(x[-1]) for x in jyutping],
        "source_indices":source_indices,"unsupported":unsupported,
    }

def line_key(line):
    return line["text"]+"\n"+" ".join(line["jyutping"])

def parse_lyrics_text(lyrics):
    raw=str(lyrics or "").replace("\r\n","\n").replace("\r","\n")
    texts=[x.strip() for x in raw.split("\n") if x.strip()]
    if not texts:raise ValueError("請先貼上歌詞")
    if len(texts)>MAX_LYRIC_LINES:raise ValueError(f"目前最多支援 {MAX_LYRIC_LINES} 行歌詞")
    lines=[];warnings=[]
    for idx,t in enumerate(texts):
        try:
            line=make_line(t);line["id"]=len(lines);line["source_line"]=idx+1;lines.append(line)
            if line["unsupported"]:
                warnings.append({"line":idx+1,"chars":line["unsupported"]})
        except ValueError as e:
            warnings.append({"line":idx+1,"error":str(e)})
    if not lines:raise ValueError("沒有可建立的粵語歌詞行")
    return lines,warnings

def align(target_chars,target_jp,rec_chars,rec_jp):
    n,m=len(target_chars),len(rec_chars)
    dp=[[0.0]*(m+1) for _ in range(n+1)]
    bt=[[None]*(m+1) for _ in range(n+1)]
    for i in range(1,n+1):dp[i][0]=float(i);bt[i][0]="del"
    for j in range(1,m+1):dp[0][j]=float(j);bt[0][j]="ins"

    def relation(i,j):
        tc,rc=target_chars[i],rec_chars[j]
        same_char=t2s.convert(tc)==t2s.convert(rc)
        tp=split_jyutping(target_jp[i]);rp=split_jyutping(rec_jp[j] if j<len(rec_jp) else None)
        tb,tt=tp["base"],tp["tone"];rb,rt=rp["base"],rp["tone"]
        same_base=bool(tb and rb and tb==rb)
        same_tone=bool(same_base and tt==rt)
        initial_match=bool(tp["initial"] and rp["initial"] and tp["initial"]==rp["initial"])
        final_match=bool(tp["final"] and rp["final"] and tp["final"]==rp["final"])
        segmental=bool(same_char or same_base)
        homophone=bool((not same_char) and same_tone)
        if same_tone:cost=0.0
        elif same_base:cost=0.12
        elif same_char:cost=0.20
        elif initial_match and final_match:cost=0.20
        elif initial_match or final_match:cost=0.58
        else:cost=1.0
        return cost,same_char,segmental,same_tone,homophone,tp,rp,initial_match,final_match

    for i in range(1,n+1):
        for j in range(1,m+1):
            sub,*_=relation(i-1,j-1)
            opts=[(dp[i-1][j]+1.0,"del"),(dp[i][j-1]+1.0,"ins"),(dp[i-1][j-1]+sub,"pair")]
            dp[i][j],bt[i][j]=min(opts,key=lambda x:x[0])

    rows=[];insertions=[];i,j=n,m
    while i or j:
        op=bt[i][j]
        if op=="pair":
            cost,same_char,segmental,same_tone,homophone,tp,rp,initial_match,final_match=relation(i-1,j-1)
            rows.append({"target_index":i-1,"rec_index":j-1,"op":"pair","cost":round(cost,3),
                         "same_char":same_char,"segmental_match":segmental,"asr_tone_match":same_tone,
                         "homophone":homophone,"target_base":tp["base"],"target_tone":tp["tone"],
                         "target_initial":tp["initial"],"target_final":tp["final"],
                         "rec_base":rp["base"],"rec_tone":rp["tone"],
                         "rec_initial":rp["initial"],"rec_final":rp["final"],
                         "initial_match":initial_match,"final_match":final_match})
            i-=1;j-=1
        elif op=="del":
            tp=split_jyutping(target_jp[i-1])
            rows.append({"target_index":i-1,"rec_index":None,"op":"del","cost":1.0,
                         "same_char":False,"segmental_match":False,"asr_tone_match":False,"homophone":False,
                         "target_base":tp["base"],"target_tone":tp["tone"],
                         "target_initial":tp["initial"],"target_final":tp["final"],
                         "rec_base":None,"rec_tone":None,"rec_initial":None,"rec_final":None,
                         "initial_match":False,"final_match":False})
            i-=1
        else:
            insertions.append(j-1);j-=1
    rows.reverse();insertions.reverse()
    return rows,insertions

def _mel(hz):
    return 2595.0*math.log10(1.0+hz/700.0)

def _inv_mel(m):
    return 700.0*(10**(m/2595.0)-1.0)

def acoustic_fingerprint(samples,sr,start,end,time_bins=12,mel_bins=24):
    a=max(0,int(start*sr));b=min(len(samples),int(end*sr))
    x=np.asarray(samples[a:b],dtype=np.float32)
    if x.size<int(.07*sr):return None
    x=x-float(np.mean(x))
    peak=float(np.max(np.abs(x)))+1e-9
    x=x/peak
    frame=max(128,int(.025*sr));hop=max(64,int(.010*sr))
    nfft=1
    while nfft<frame:nfft*=2
    nfft=max(512,nfft)
    if x.size<frame:x=np.pad(x,(0,frame-x.size))
    starts=list(range(0,max(1,x.size-frame+1),hop))
    if not starts:starts=[0]
    win=np.hanning(frame).astype(np.float32)
    fmax=min(7000.0,sr*.46);fmin=180.0
    mel_edges=np.linspace(_mel(fmin),_mel(fmax),mel_bins+2)
    hz_edges=np.asarray([_inv_mel(v) for v in mel_edges])
    freqs=np.fft.rfftfreq(nfft,1.0/sr)
    filters=[]
    for k in range(mel_bins):
        l,c,r=hz_edges[k:k+3]
        w=np.zeros_like(freqs)
        left=(freqs>=l)&(freqs<=c);right=(freqs>=c)&(freqs<=r)
        if c>l:w[left]=(freqs[left]-l)/(c-l)
        if r>c:w[right]=(r-freqs[right])/(r-c)
        filters.append(w)
    fb=np.asarray(filters)
    frames=[]
    for st in starts:
        y=x[st:st+frame]
        if y.size<frame:y=np.pad(y,(0,frame-y.size))
        spec=np.abs(np.fft.rfft(y*win,nfft))**2
        bands=np.log(np.maximum(fb@spec,1e-8))
        bands=bands-float(np.mean(bands))
        sd=float(np.std(bands))
        if sd>1e-6:bands=bands/sd
        frames.append(bands)
    mat=np.asarray(frames,dtype=np.float32)
    old=np.linspace(0,1,mat.shape[0]);new=np.linspace(0,1,time_bins)
    fixed=np.stack([np.interp(new,old,mat[:,k]) for k in range(mat.shape[1])],axis=1)
    v=fixed.reshape(-1)
    norm=float(np.linalg.norm(v))
    if norm<1e-6:return None
    return (v/norm).astype(np.float32)

def acoustic_score(a,b):
    if a is None or b is None:return None
    sim=float(np.dot(a,b))
    # Conservative mapping: same reference ~=100; cross-speaker same syllable should
    # still score well after per-frame spectral normalization.
    return int(round(max(0,min(100,100*(sim-.20)/.80))))

def extract_acoustic_segments(samples,sr,rows,rec_times,rec_durations,duration):
    out={}
    for row in rows:
        ri=row.get("rec_index");ti=row["target_index"]
        if ri is None or ri>=len(rec_times):continue
        st=max(0.0,float(rec_times[ri])-.02)
        if ri<len(rec_durations) and rec_durations[ri] and rec_durations[ri]>.035:
            en=min(duration,st+float(rec_durations[ri])+.04)
        else:
            en=min(duration,(rec_times[ri+1] if ri+1<len(rec_times) else duration)+.02)
        if en-st<.08:en=min(duration,st+.18)
        dur=max(.08,en-st)
        onset_end=min(en,st+.42*dur)
        rhyme_start=max(st,en-.72*dur)
        out[ti]={
            "whole":acoustic_fingerprint(samples,sr,st,en),
            "initial":acoustic_fingerprint(samples,sr,st,onset_end),
            "final":acoustic_fingerprint(samples,sr,rhyme_start,en),
            "start":st,"end":en
        }
    return out

def f0_track(samples,sr,start,end):
    a=max(0,int(start*sr));b=min(len(samples),int(end*sr))
    x=samples[a:b]
    if len(x)<int(.09*sr):return []
    frame=int(.04*sr);hop=int(.01*sr);lo=max(1,int(sr/500));hi=max(lo+2,int(sr/70));vals=[]
    for pos in range(0,max(1,len(x)-frame+1),hop):
        y=x[pos:pos+frame]
        if len(y)<frame:break
        y=y-y.mean();rms=float(np.sqrt(np.mean(y*y)))
        if rms<.006:continue
        best_c=-1.0;best_l=0
        for lag in range(lo,min(hi,frame-2)+1):
            p=y[:-lag];q=y[lag:];den=float(np.sqrt(np.dot(p,p)*np.dot(q,q)))+1e-9
            c=float(np.dot(p,q))/den
            if c>best_c:best_c=c;best_l=lag
        if best_c>=.48 and best_l:
            hz=sr/best_l
            if 70<=hz<=500:vals.append(12*math.log2(hz))
    if len(vals)<3:return []
    fixed=[vals[0]]
    for v in vals[1:]:
        prev=fixed[-1]
        while v-prev>7:v-=12
        while prev-v>7:v+=12
        if abs(v-prev)<=7:fixed.append(v)
    if len(fixed)<3:return []
    arr=np.asarray(fixed,dtype=float)
    if len(arr)>=3:arr=np.convolve(arr,np.ones(3)/3,mode="same")[1:-1] if len(arr)>4 else arr
    return arr.tolist()

def resample(v,n=20):
    if not v:return []
    if len(v)==1:return [v[0]]*n
    xp=np.linspace(0,1,len(v));x=np.linspace(0,1,n)
    return np.interp(x,xp,np.asarray(v)).tolist()

def tmpl(t,n=20):
    x=np.linspace(0,1,n)
    if t==1:y=np.full(n,5.0)
    elif t==2:y=2+3*(x**1.35)
    elif t==3:y=np.full(n,3.0)
    elif t==4:y=2-x
    elif t==5:y=2+(x**1.35)
    else:y=np.full(n,2.0)
    return y

def extract_pitch_segments(samples,sr,rows,rec_times,duration,segmental_only=False):
    raw={}
    for row in rows:
        ri=row["rec_index"];ti=row["target_index"]
        if ri is None or ri>=len(rec_times):continue
        if segmental_only and not row.get("segmental_match"):continue
        st=max(0.0,rec_times[ri]-0.025)
        en=(rec_times[ri+1] if ri+1<len(rec_times) else duration)+0.025
        en=min(duration,en)
        if en-st<.08:en=min(duration,st+.18)
        tr=f0_track(samples,sr,st,en)
        if tr:raw[ti]={"semi":tr,"start":st,"end":en}
    return raw

def pitch_features(raw):
    allsemi=[v for d in raw.values() for v in d["semi"]]
    if len(allsemi)<4:return {},None
    center=float(np.median(np.asarray(allsemi,dtype=float)))
    lo,hi=np.percentile(np.asarray(allsemi,dtype=float),[10,90]).tolist()
    if hi-lo<1.0:lo=center-1.5;hi=center+1.5
    out={}
    for ti,d in raw.items():
        semi=np.asarray(resample(d["semi"],20),dtype=float)
        levels=np.clip(1+4*(semi-lo)/(hi-lo),0.3,5.7)
        relative=semi-center
        shape=semi-float(np.mean(semi))
        out[ti]={
            "levels":levels,
            "relative":relative,
            "shape":shape,
            "delta_st":float(np.mean(semi[-4:])-np.mean(semi[:4])),
            "mean_rel":float(np.mean(relative))
        }
    return out,center

def normalize_pitch_segments(raw):
    features,_=pitch_features(raw)
    return {k:v["levels"] for k,v in features.items()}

def canonical_tone_details(levels,goal):
    distances=[];targets=[]
    for t in range(1,7):
        z=tmpl(t);targets.append(z);distances.append(float(np.sqrt(np.mean((levels-z)**2))))
    pred=int(np.argmin(distances))+1
    z=targets[goal-1]
    us=float(np.mean(levels[:4]));ue=float(np.mean(levels[-4:]))
    ts=float(np.mean(z[:4]));te=float(np.mean(z[-4:]))
    return pred,us,ue,ts,te

def reference_tone_details(feature,ref_feature,goal):
    levels=feature["levels"];ref_levels=ref_feature["levels"]
    shape_rmse=float(np.sqrt(np.mean((feature["shape"]-ref_feature["shape"])**2)))
    register_error=float(feature["mean_rel"]-ref_feature["mean_rel"])
    slope_error=float(feature["delta_st"]-ref_feature["delta_st"])
    shape_score=100*math.exp(-.5*(shape_rmse/1.05)**2)
    register_score=100*math.exp(-.5*(register_error/1.6)**2)
    slope_score=100*math.exp(-.5*(slope_error/1.15)**2)
    quality=int(round(.45*shape_score+.30*register_score+.25*slope_score))
    pred,_,_,_,_=canonical_tone_details(levels,goal)
    notes=[]
    if abs(register_error)>.65:notes.append("整體音高相對標準音偏"+("高" if register_error>0 else "低"))
    if abs(slope_error)>.65:notes.append("升降幅度"+("過大" if abs(feature["delta_st"])>abs(ref_feature["delta_st"]) else "不足"))
    if shape_rmse>1.0:notes.append("音高輪廓形狀偏離標準音")
    return {
        "tone_score":quality,"predicted_tone":pred,
        "register_error_st":round(register_error,2),"slope_error_st":round(slope_error,2),
        "shape_rmse_st":round(shape_rmse,2),
        "shape_score":round(shape_score),"register_score":round(register_score),"slope_score":round(slope_score),
        "notes":notes,"f0_levels":[round(float(x),2) for x in levels],
        "reference_levels":[round(float(x),2) for x in ref_levels],
        "reference_based":True
    }

def build_reference(line_id):
    line=SONG[line_id]
    raw=proxy_tts(line["text"],.88)
    samples,sr=read_wav(raw);duration=len(samples)/sr
    rec=recognize(samples,sr)
    tokens,times,lps,durs=flatten_tokens(rec["tokens"],rec["timestamps"],rec.get("log_probs"),rec.get("durations"))
    rec_jp=recognized_jyutping(tokens)
    rows,_=align(line["chars"],line["jyutping"],tokens,rec_jp)
    pitch_raw=extract_pitch_segments(samples,sr,rows,times,duration,False)
    pitch,_=pitch_features(pitch_raw)
    acoustic=extract_acoustic_segments(samples,sr,rows,times,durs,duration)
    baseline={}
    for row in rows:
        ti=row["target_index"];ri=row["rec_index"]
        if ri is None:continue
        baseline[ti]={
            "char":tokens[ri] if ri<len(tokens) else "",
            "jyutping":rec_jp[ri] if ri<len(rec_jp) else None,
            "base":row.get("rec_base"),"tone":row.get("rec_tone"),
            "initial":row.get("rec_initial"),"final":row.get("rec_final"),
            "log_prob":lps[ri] if ri<len(lps) else None,
            "duration":durs[ri] if ri<len(durs) else None,
            "direct_match":bool(row.get("segmental_match"))
        }
    profile={"wav":raw,"recognized":"".join(tokens),"recognized_jyutping":" ".join(x or "?" for x in rec_jp),
             "baseline":baseline,"pitch":pitch,"acoustic":acoustic}
    with reference_lock:REFERENCE[line_id]=profile
    print(f"[reference] line{line_id+1} asr={profile['recognized']} jp={profile['recognized_jyutping']} pitch={len(pitch)}/{len(line['chars'])}",flush=True)
    return profile

def build_click_reference(line_id):
    line=SONG[line_id]
    raw=fixed_audio(line["text"],.60)
    if raw is None:raw=proxy_tts(line["text"],.60)
    samples,sr=read_wav(raw);duration=len(samples)/sr
    rec=recognize(samples,sr)
    tokens,times,lps,durs=flatten_tokens(rec["tokens"],rec["timestamps"],rec.get("log_probs"),rec.get("durations"))
    rec_jp=recognized_jyutping(tokens)
    rows,_=align(line["chars"],line["jyutping"],tokens,rec_jp)
    segments={}
    for row in rows:
        ti=row["target_index"];ri=row["rec_index"]
        if ri is None:continue
        st=max(0.0,(times[ri] if ri<len(times) else 0.0)-.025)
        if ri<len(durs) and durs[ri] and durs[ri]>.035:
            en=min(duration,st+float(durs[ri])+.055)
        else:
            nxt=times[ri+1] if ri+1<len(times) else duration
            en=min(duration,float(nxt)+.025)
        if en-st<.12:en=min(duration,st+.22)
        segments[ti]={"start":st,"end":en,"heard":tokens[ri] if ri<len(tokens) else ""}
    # Fallback for any target ASR failed to align: proportional slice of the slow sentence.
    n=max(1,len(line["chars"]))
    for ti in range(n):
        if ti not in segments:
            st=max(0.0,duration*(ti/n)-.02)
            en=min(duration,duration*((ti+1)/n)+.02)
            segments[ti]={"start":st,"end":en,"heard":""}
    profile={"samples":samples,"sr":sr,"duration":duration,"segments":segments}
    with click_reference_lock:CLICK_REFERENCE[line_id]=profile
    print(f"[click-audio] line{line_id+1} segments={len(segments)}/{len(line['chars'])}",flush=True)
    return profile

def get_click_reference(line_id):
    with click_reference_lock:
        p=CLICK_REFERENCE.get(line_id)
    if p is not None:return p
    return build_click_reference(line_id)

def character_audio(line_id,char_index):
    if line_id not in (0,1):raise ValueError("未知歌詞行")
    if char_index<0 or char_index>=len(SONG[line_id]["chars"]):raise ValueError("未知字位置")
    p=get_click_reference(line_id)
    seg=p["segments"].get(char_index)
    if not seg:raise RuntimeError("此字暫時沒有可用音訊")
    raw=padded_clip(p["samples"],p["sr"],seg["start"],seg["end"])
    if raw is None or len(raw)<800:raise RuntimeError("單字音訊切片失敗")
    return raw

def get_reference(line_id):
    with reference_lock:
        p=REFERENCE.get(line_id)
    return p

def tone_analysis(samples,sr,line,rows,rec_times,duration,reference=None):
    raw=extract_pitch_segments(samples,sr,rows,rec_times,duration,False)
    features,_=pitch_features(raw)
    out={}
    for ti,feature in features.items():
        goal=line["tones"][ti]
        ref_feature=reference.get("pitch",{}).get(ti) if reference else None
        if ref_feature is not None:
            out[ti]=reference_tone_details(feature,ref_feature,goal)
            continue
        levels=feature["levels"]
        pred,us,ue,ts,te=canonical_tone_details(levels,goal)
        z=tmpl(goal);dist=float(np.sqrt(np.mean((levels-z)**2)))
        quality=int(round(100*math.exp(-.5*(dist/.82)**2)))
        slope=(ue-us)-(te-ts);notes=[]
        if abs(us-ts)>.55:notes.append("起點偏"+("高" if us>ts else "低"))
        if abs(ue-te)>.55:notes.append("終點偏"+("高" if ue>te else "低"))
        if abs(slope)>.55:notes.append("升降幅度"+("過大" if abs(ue-us)>abs(te-ts) else "不足"))
        out[ti]={"tone_score":quality,"predicted_tone":pred,
                 "slope_error_st":round(slope,2),"shape_rmse_st":None,"register_error_st":None,
                 "notes":notes,"f0_levels":[round(float(x),2) for x in levels],"reference_based":False}
    return raw,out

def confidence_score(user_lp,ref_lp):
    if user_lp is None:return None
    if ref_lp is None:
        return int(round(max(0,min(100,100*math.exp(min(0.0,user_lp)/2.5)))))
    delta=float(user_lp)-float(ref_lp)
    return int(round(max(0,min(100,100*math.exp(min(0.0,delta)/1.4)))))

def duration_score(user_dur,ref_dur):
    if not user_dur or not ref_dur or user_dur<=0 or ref_dur<=0:return None
    ratio=float(user_dur)/float(ref_dur)
    return int(round(100*math.exp(-abs(math.log(max(.15,min(6.0,ratio))))/.75)))

def component_score(match,confidence,baseline_uncertain=False):
    if baseline_uncertain:return None
    base=100 if match else 18
    if confidence is None:return base
    return int(round(.72*base+.28*confidence))

def evaluate(raw,line_id,reference=None):
    if line_id not in (0,1):raise ValueError("未知歌詞行")
    samples,sr=read_wav(raw);duration=len(samples)/sr
    if duration<.4 or duration>15:raise ValueError("錄音長度不合適")
    line=SONG[line_id]
    if reference is None:reference=get_reference(line_id)
    rec=recognize(samples,sr)
    tokens,times,lps,durs=flatten_tokens(rec["tokens"],rec["timestamps"],rec.get("log_probs"),rec.get("durations"))
    rec_jp=recognized_jyutping(tokens)
    rows,insertions=align(line["chars"],line["jyutping"],tokens,rec_jp)
    _,tones=tone_analysis(samples,sr,line,rows,times,duration,reference)
    user_acoustic=extract_acoustic_segments(samples,sr,rows,times,durs,duration)

    items=[];rated_count=0;stable=0;attention=0;unrated=0
    refbase=reference.get("baseline",{}) if reference else {}
    refac=reference.get("acoustic",{}) if reference else {}

    for row in rows:
        ti=row["target_index"];ri=row["rec_index"]
        heard=tokens[ri] if ri is not None and ri<len(tokens) else ""
        heard_jp=rec_jp[ri] if ri is not None and ri<len(rec_jp) else None
        user_lp=lps[ri] if ri is not None and ri<len(lps) else None
        user_dur=durs[ri] if ri is not None and ri<len(durs) else None
        baseline=refbase.get(ti,{})
        ua=user_acoustic.get(ti,{})
        ra=refac.get(ti,{})
        whole_ac=acoustic_score(ua.get("whole"),ra.get("whole"))
        init_ac=acoustic_score(ua.get("initial"),ra.get("initial"))
        final_ac=acoustic_score(ua.get("final"),ra.get("final"))

        direct=bool(row.get("segmental_match"))
        baseline_confusion=bool(
            ri is not None and not direct and row.get("rec_base") and
            baseline.get("base") and row.get("rec_base")==baseline.get("base") and
            not baseline.get("direct_match",False)
        )

        # Second judge: reference-audio spectral shape. This is specifically used
        # to resolve positions where free ASR is known to be unreliable.
        acoustic_accept=whole_ac is not None and whole_ac>=58
        acoustic_reject=whole_ac is not None and whole_ac<=38
        accepted=bool(direct or acoustic_accept)

        acoustic=confidence_score(user_lp,baseline.get("log_prob"))
        timing=duration_score(user_dur,baseline.get("duration"))
        tone=tones.get(ti) if (accepted or baseline_confusion) else None
        tone_score=tone["tone_score"] if tone else None

        # Initial/final scores blend phonetic alignment with direct acoustic
        # comparison to the fixed reference syllable.
        initial_match=bool(row.get("initial_match")) or direct
        final_match=bool(row.get("final_match")) or direct
        asr_initial=100 if initial_match else 18
        asr_final=100 if final_match else 18
        initial_score=(
            round(.35*asr_initial+.65*init_ac) if init_ac is not None
            else (asr_initial if not baseline_confusion else None)
        )
        final_score=(
            round(.30*asr_final+.70*final_ac) if final_ac is not None
            else (asr_final if not baseline_confusion else None)
        )

        unresolved=bool(
            not direct and not acoustic_accept and not acoustic_reject and
            (baseline_confusion or whole_ac is None or (38<whole_ac<58))
        )

        issues=[]
        if unresolved:
            status="unrated";certainty="system";score=None;unrated+=1
        else:
            rated_count+=1
            if initial_score is not None and initial_score<58:issues.append("聲母")
            if final_score is not None and final_score<58:issues.append("韻母")
            if tone_score is not None and tone_score<70:issues.append("聲調")
            if timing is not None and timing<50:issues.append("時長")

            if acoustic_reject and not direct:
                status="segmental";certainty="high"
            elif "聲母" in issues or "韻母" in issues:
                status="segmental";certainty="medium"
            elif "聲調" in issues:
                status="tone";certainty="high"
            else:
                status="ok";certainty="high"

            segmental_parts=[x for x in (initial_score,final_score,whole_ac) if x is not None]
            segmental_score=round(sum(segmental_parts)/len(segmental_parts)) if segmental_parts else (100 if direct else 60)
            tone_component=tone_score if tone_score is not None else 78
            timing_component=timing if timing is not None else 85
            score=round(.50*segmental_score+.40*tone_component+.10*timing_component)
            if status=="ok":stable+=1
            else:attention+=1

        items.append({
            "index":ti,"char":line["chars"][ti],"jyutping":line["jyutping"][ti],
            "expected_tone":line["tones"][ti],"heard":heard,"heard_jyutping":heard_jp,
            "same_char":bool(row.get("same_char")),"segmental_match":direct,
            "acoustic_accept":acoustic_accept,"baseline_confusion":baseline_confusion,
            "certainty":certainty,"homophone":bool(row.get("homophone")),
            "target_initial":row.get("target_initial"),"target_final":row.get("target_final"),
            "heard_initial":row.get("rec_initial"),"heard_final":row.get("rec_final"),
            "initial_score":initial_score,"final_score":final_score,
            "reference_acoustic_score":whole_ac,
            "acoustic_score":acoustic,"duration_score":timing,
            "status":status,"issues":issues,"score":score,"tone":tone
        })

    scored=[x["score"] for x in items if x["score"] is not None]
    overall=round(sum(scored)/len(scored)) if scored else None
    coverage=round(100*rated_count/len(line["chars"])) if line["chars"] else 0
    return {
        "ok":True,"version":"teaching-speed-1","line_id":line_id,"target":line["text"],
        "recognized":"".join(tokens),"recognized_jyutping":" ".join(x or "?" for x in rec_jp),
        "overall_score":overall,"coverage":coverage,
        "stable_count":stable,"attention_count":attention,"unrated_count":unrated,
        "items":items,
        "insertions":[{"char":tokens[i],"jyutping":rec_jp[i] if i<len(rec_jp) else None} for i in insertions if i<len(tokens)]
    }

def build_reference_dynamic(line):
    key=line_key(line)
    raw=proxy_tts(line["text"],.88)
    samples,sr=read_wav(raw);duration=len(samples)/sr
    rec=recognize(samples,sr)
    tokens,times,lps,durs=flatten_tokens(rec["tokens"],rec["timestamps"],rec.get("log_probs"),rec.get("durations"))
    rec_jp=recognized_jyutping(tokens)
    rows,_=align(line["chars"],line["jyutping"],tokens,rec_jp)
    pitch_raw=extract_pitch_segments(samples,sr,rows,times,duration,False)
    pitch,_=pitch_features(pitch_raw)
    acoustic=extract_acoustic_segments(samples,sr,rows,times,durs,duration)
    baseline={}
    for row in rows:
        ti=row["target_index"];ri=row["rec_index"]
        if ri is None:continue
        baseline[ti]={
            "char":tokens[ri] if ri<len(tokens) else "",
            "jyutping":rec_jp[ri] if ri<len(rec_jp) else None,
            "base":row.get("rec_base"),"tone":row.get("rec_tone"),
            "initial":row.get("rec_initial"),"final":row.get("rec_final"),
            "log_prob":lps[ri] if ri<len(lps) else None,
            "duration":durs[ri] if ri<len(durs) else None,
            "direct_match":bool(row.get("segmental_match"))
        }
    profile={"wav":raw,"recognized":"".join(tokens),"recognized_jyutping":" ".join(x or "?" for x in rec_jp),
             "baseline":baseline,"pitch":pitch,"acoustic":acoustic}
    with dynamic_reference_lock:
        if len(DYNAMIC_REFERENCE)>=80:DYNAMIC_REFERENCE.pop(next(iter(DYNAMIC_REFERENCE)))
        DYNAMIC_REFERENCE[key]=profile
    print(f"[dynamic-reference] chars={len(line['chars'])} pitch={len(pitch)} text={line['text'][:24]}",flush=True)
    return profile

def get_reference_dynamic(line):
    key=line_key(line)
    with dynamic_reference_lock:p=DYNAMIC_REFERENCE.get(key)
    return p if p is not None else build_reference_dynamic(line)

def build_click_reference_dynamic(line):
    key=line_key(line)
    raw=proxy_tts(line["text"],.60)
    samples,sr=read_wav(raw);duration=len(samples)/sr
    rec=recognize(samples,sr)
    tokens,times,lps,durs=flatten_tokens(rec["tokens"],rec["timestamps"],rec.get("log_probs"),rec.get("durations"))
    rec_jp=recognized_jyutping(tokens)
    rows,_=align(line["chars"],line["jyutping"],tokens,rec_jp)
    segments={}
    for row in rows:
        ti=row["target_index"];ri=row["rec_index"]
        if ri is None:continue
        st=max(0.0,(times[ri] if ri<len(times) else 0.0)-.025)
        if ri<len(durs) and durs[ri] and durs[ri]>.035:
            en=min(duration,st+float(durs[ri])+.055)
        else:
            nxt=times[ri+1] if ri+1<len(times) else duration
            en=min(duration,float(nxt)+.025)
        if en-st<.12:en=min(duration,st+.22)
        segments[ti]={"start":st,"end":en}
    n=max(1,len(line["chars"]))
    for ti in range(n):
        if ti not in segments:
            st=max(0.0,duration*(ti/n)-.02);en=min(duration,duration*((ti+1)/n)+.02)
            segments[ti]={"start":st,"end":en}
    profile={"samples":samples,"sr":sr,"duration":duration,"segments":segments}
    with dynamic_click_lock:
        if len(DYNAMIC_CLICK_REFERENCE)>=80:DYNAMIC_CLICK_REFERENCE.pop(next(iter(DYNAMIC_CLICK_REFERENCE)))
        DYNAMIC_CLICK_REFERENCE[key]=profile
    print(f"[dynamic-click] segments={len(segments)}/{len(line['chars'])} text={line['text'][:24]}",flush=True)
    return profile

def get_click_reference_dynamic(line):
    key=line_key(line)
    with dynamic_click_lock:p=DYNAMIC_CLICK_REFERENCE.get(key)
    return p if p is not None else build_click_reference_dynamic(line)

def character_audio_dynamic(line,char_index):
    if char_index<0 or char_index>=len(line["chars"]):raise ValueError("未知字位置")
    p=get_click_reference_dynamic(line);seg=p["segments"].get(char_index)
    if not seg:raise RuntimeError("此字暫時沒有可用音訊")
    raw=padded_clip(p["samples"],p["sr"],seg["start"],seg["end"])
    if raw is None or len(raw)<800:raise RuntimeError("單字音訊切片失敗")
    return raw

def evaluate_dynamic(raw,line,reference=None):
    samples,sr=read_wav(raw);duration=len(samples)/sr
    if duration<.4 or duration>15:raise ValueError("錄音長度不合適")
    if reference is None:reference=get_reference_dynamic(line)
    rec=recognize(samples,sr)
    tokens,times,lps,durs=flatten_tokens(rec["tokens"],rec["timestamps"],rec.get("log_probs"),rec.get("durations"))
    rec_jp=recognized_jyutping(tokens)
    rows,insertions=align(line["chars"],line["jyutping"],tokens,rec_jp)
    _,tones=tone_analysis(samples,sr,line,rows,times,duration,reference)
    user_acoustic=extract_acoustic_segments(samples,sr,rows,times,durs,duration)

    items=[];rated_count=0;stable=0;attention=0;unrated=0
    refbase=reference.get("baseline",{}) if reference else {}
    refac=reference.get("acoustic",{}) if reference else {}

    for row in rows:
        ti=row["target_index"];ri=row["rec_index"]
        heard=tokens[ri] if ri is not None and ri<len(tokens) else ""
        heard_jp=rec_jp[ri] if ri is not None and ri<len(rec_jp) else None
        user_lp=lps[ri] if ri is not None and ri<len(lps) else None
        user_dur=durs[ri] if ri is not None and ri<len(durs) else None
        baseline=refbase.get(ti,{})
        ua=user_acoustic.get(ti,{})
        ra=refac.get(ti,{})
        whole_ac=acoustic_score(ua.get("whole"),ra.get("whole"))
        init_ac=acoustic_score(ua.get("initial"),ra.get("initial"))
        final_ac=acoustic_score(ua.get("final"),ra.get("final"))

        direct=bool(row.get("segmental_match"))
        baseline_confusion=bool(
            ri is not None and not direct and row.get("rec_base") and
            baseline.get("base") and row.get("rec_base")==baseline.get("base") and
            not baseline.get("direct_match",False)
        )

        # Second judge: reference-audio spectral shape. This is specifically used
        # to resolve positions where free ASR is known to be unreliable.
        acoustic_accept=whole_ac is not None and whole_ac>=58
        acoustic_reject=whole_ac is not None and whole_ac<=38
        accepted=bool(direct or acoustic_accept)

        acoustic=confidence_score(user_lp,baseline.get("log_prob"))
        timing=duration_score(user_dur,baseline.get("duration"))
        tone=tones.get(ti) if (accepted or baseline_confusion) else None
        tone_score=tone["tone_score"] if tone else None

        # Initial/final scores blend phonetic alignment with direct acoustic
        # comparison to the fixed reference syllable.
        initial_match=bool(row.get("initial_match")) or direct
        final_match=bool(row.get("final_match")) or direct
        asr_initial=100 if initial_match else 18
        asr_final=100 if final_match else 18
        initial_score=(
            round(.35*asr_initial+.65*init_ac) if init_ac is not None
            else (asr_initial if not baseline_confusion else None)
        )
        final_score=(
            round(.30*asr_final+.70*final_ac) if final_ac is not None
            else (asr_final if not baseline_confusion else None)
        )

        unresolved=bool(
            not direct and not acoustic_accept and not acoustic_reject and
            (baseline_confusion or whole_ac is None or (38<whole_ac<58))
        )

        issues=[]
        if unresolved:
            status="unrated";certainty="system";score=None;unrated+=1
        else:
            rated_count+=1
            if initial_score is not None and initial_score<58:issues.append("聲母")
            if final_score is not None and final_score<58:issues.append("韻母")
            if tone_score is not None and tone_score<70:issues.append("聲調")
            if timing is not None and timing<50:issues.append("時長")

            if acoustic_reject and not direct:
                status="segmental";certainty="high"
            elif "聲母" in issues or "韻母" in issues:
                status="segmental";certainty="medium"
            elif "聲調" in issues:
                status="tone";certainty="high"
            else:
                status="ok";certainty="high"

            segmental_parts=[x for x in (initial_score,final_score,whole_ac) if x is not None]
            segmental_score=round(sum(segmental_parts)/len(segmental_parts)) if segmental_parts else (100 if direct else 60)
            tone_component=tone_score if tone_score is not None else 78
            timing_component=timing if timing is not None else 85
            score=round(.50*segmental_score+.40*tone_component+.10*timing_component)
            if status=="ok":stable+=1
            else:attention+=1

        items.append({
            "index":ti,"char":line["chars"][ti],"jyutping":line["jyutping"][ti],
            "expected_tone":line["tones"][ti],"heard":heard,"heard_jyutping":heard_jp,
            "same_char":bool(row.get("same_char")),"segmental_match":direct,
            "acoustic_accept":acoustic_accept,"baseline_confusion":baseline_confusion,
            "certainty":certainty,"homophone":bool(row.get("homophone")),
            "target_initial":row.get("target_initial"),"target_final":row.get("target_final"),
            "heard_initial":row.get("rec_initial"),"heard_final":row.get("rec_final"),
            "initial_score":initial_score,"final_score":final_score,
            "reference_acoustic_score":whole_ac,
            "acoustic_score":acoustic,"duration_score":timing,
            "status":status,"issues":issues,"score":score,"tone":tone
        })

    scored=[x["score"] for x in items if x["score"] is not None]
    overall=round(sum(scored)/len(scored)) if scored else None
    coverage=round(100*rated_count/len(line["chars"])) if line["chars"] else 0
    return {
        "ok":True,"version":"dynamic-song-1","line_id":None,"target":line["text"],
        "recognized":"".join(tokens),"recognized_jyutping":" ".join(x or "?" for x in rec_jp),
        "overall_score":overall,"coverage":coverage,
        "stable_count":stable,"attention_count":attention,"unrated_count":unrated,
        "items":items,
        "insertions":[{"char":tokens[i],"jyutping":rec_jp[i] if i<len(rec_jp) else None} for i in insertions if i<len(tokens)]
    }

STATIC={
 "/":(ROOT/"index.html","text/html; charset=utf-8"),
 "/app.js":(ROOT/"app.js","application/javascript; charset=utf-8"),
 "/vendor/cantojpmin_data.js":(ROOT.parent/"cantonese-coach-mvp"/"vendor"/"cantojpmin_data.js","application/javascript; charset=utf-8"),
 "/vendor/cantojpmin_functions.js":(ROOT.parent/"cantonese-coach-mvp"/"vendor"/"cantojpmin_functions.js","application/javascript; charset=utf-8"),
}
class H(BaseHTTPRequestHandler):
 def log_message(self,fmt,*args):print("[http] "+fmt%args,flush=True)
 def sendb(self,status,b,ct):
  self.send_response(status);self.send_header("Content-Type",ct);self.send_header("Content-Length",str(len(b)));self.send_header("Cache-Control","no-store");self.send_header("X-Content-Type-Options","nosniff");self.end_headers();self.wfile.write(b)
 def js(self,status,obj):self.sendb(status,json.dumps(obj,ensure_ascii=False).encode(),"application/json; charset=utf-8")
 def do_GET(self):
  u=urlparse(self.path);p=u.path
  if p=="/api/health":return self.js(200,{"ok":True,"version":"dynamic-song-1","tts":True,"asr":True,"external_api":False,"char_audio":True,"dynamic_lyrics":True})
  if p=="/api/char":
   try:
    qs=parse_qs(u.query)
    if qs.get("text"):
     line=make_line(qs.get("text",[""])[0]);char_index=int(qs.get("index",["-1"])[0])
     return self.sendb(200,character_audio_dynamic(line,char_index),"audio/wav")
    line_id=int(qs.get("line",["-1"])[0]);char_index=int(qs.get("index",["-1"])[0])
    return self.sendb(200,character_audio(line_id,char_index),"audio/wav")
   except Exception as e:
    print("[char-audio]",type(e).__name__,e,flush=True);return self.js(400,{"error":str(e)})
  item=STATIC.get(p)
  if not item:return self.sendb(404,b"Not found","text/plain")
  return self.sendb(200,item[0].read_bytes(),item[1])
 def do_POST(self):
  u=urlparse(self.path);length=int(self.headers.get("Content-Length","0"))
  if length<=0 or length>12*1024*1024:return self.js(400,{"error":"請求大小不正確"})
  raw=self.rfile.read(length)
  try:
   if u.path=="/api/parse-lyrics":
    d=json.loads(raw.decode());lines,warnings=parse_lyrics_text(d.get("lyrics",""))
    public=[{"id":i,"text":x["text"],"chars":x["chars"],"jyutping":x["jyutping"],"source_indices":x["source_indices"],"unsupported":x["unsupported"]} for i,x in enumerate(lines)]
    return self.js(200,{"ok":True,"lines":public,"warnings":warnings})
   if u.path=="/api/tts":
    d=json.loads(raw.decode());return self.sendb(200,proxy_tts(str(d.get("text","")),float(d.get("speed",.9))),"audio/wav")
   if u.path=="/api/evaluate":
    qs=parse_qs(u.query)
    if qs.get("text"):
     line=make_line(qs.get("text",[""])[0]);return self.js(200,evaluate_dynamic(raw,line))
    line_id=int(qs.get("line",["-1"])[0]);return self.js(200,evaluate(raw,line_id))
   return self.js(404,{"error":"Not found"})
  except Exception as e:
   print("[error]",type(e).__name__,e,flush=True);return self.js(400,{"error":str(e)})
 def do_HEAD(self):
  self.send_response(200);self.end_headers()

def background_selftest():
    print("[selftest] building sentence-specific reference profiles",flush=True)
    for i,line in enumerate(SONG):
        try:
            ref=build_reference(i)
            click_ref=build_click_reference(i)
            ev=evaluate(ref["wav"],i,ref)
            char_test=character_audio(i,0)
            print(f"[selftest] fixed line{i+1} target={line['text']} coverage={ev['coverage']} score={ev['overall_score']} char_segments={len(click_ref['segments'])} char_bytes={len(char_test)}",flush=True)
        except Exception as e:
            print(f"[selftest] fixed line{i+1} deferred: {type(e).__name__}: {e}",flush=True)

    # Full dynamic-song acceptance test: text that is not part of the hard-coded demo.
    try:
        parsed,warnings=parse_lyrics_text("今天我想學粵語\n你好，世界！")
        line=parsed[0]
        ref=get_reference_dynamic(line)
        click_ref=get_click_reference_dynamic(line)
        ev=evaluate_dynamic(ref["wav"],line,ref)
        char_test=character_audio_dynamic(line,0)
        print(
            f"[selftest-dynamic] parsed={len(parsed)} chars={len(line['chars'])} jp={' '.join(line['jyutping'])} "
            f"coverage={ev['coverage']} score={ev['overall_score']} segments={len(click_ref['segments'])} "
            f"char_bytes={len(char_test)} warnings={len(warnings)}",
            flush=True,
        )
    except Exception as e:
        print(f"[selftest-dynamic] FAILED {type(e).__name__}: {e}",flush=True)

print("[boot] song lesson ASR ready",flush=True)
threading.Thread(target=background_selftest,daemon=True).start()
ThreadingHTTPServer(("0.0.0.0",PORT),H).serve_forever()
