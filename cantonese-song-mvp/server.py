from __future__ import annotations
import io,json,math,os,pathlib,re,threading,time,urllib.error,urllib.request,wave
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlparse,parse_qs
import numpy as np
import sherpa_onnx
from opencc import OpenCC

ROOT=pathlib.Path(__file__).resolve().parent
PORT=int(os.environ.get("PORT","10000"))
ASR_DIR=ROOT/"model"/"asr"
TTS_URL=os.environ.get("TTS_URL","https://terry-cantonese-tts-stable.onrender.com/api/tts")
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

asr=sherpa_onnx.OfflineRecognizer.from_wenet_ctc(
 model=str(ASR_DIR/"model.int8.onnx"),
 tokens=str(ASR_DIR/"tokens.txt"),
 num_threads=2,sample_rate=16000,feature_dim=80,
 decoding_method="greedy_search",provider="cpu")
asr_lock=threading.Lock()
REFERENCE={}
reference_lock=threading.Lock()

def proxy_tts(text:str,speed:float=.9)->bytes:
    text=text.strip()
    if not text:raise ValueError("文字為空")
    payload=json.dumps({"text":text,"speed":max(.65,min(1.35,float(speed)))},ensure_ascii=False).encode("utf-8")
    waits=[0,2,4,8,12,20]
    last=None
    for attempt,wait in enumerate(waits):
        if wait:time.sleep(wait)
        req=urllib.request.Request(TTS_URL,data=payload,headers={"Content-Type":"application/json","User-Agent":"cantonese-song-coach/1.0"},method="POST")
        try:
            with urllib.request.urlopen(req,timeout=120) as r:
                raw=r.read()
            if len(raw)<1000:raise RuntimeError("VITS 標準音回傳異常")
            if attempt:print(f"[tts-proxy] recovered after retry {attempt}",flush=True)
            return raw
        except urllib.error.HTTPError as e:
            last=e
            print(f"[tts-proxy] attempt {attempt+1} HTTP {e.code}",flush=True)
            if e.code not in (502,503,504):raise
        except Exception as e:
            last=e
            print(f"[tts-proxy] attempt {attempt+1} {type(e).__name__}: {e}",flush=True)
    raise RuntimeError(f"VITS 服務暫時不可用：{last}")


def read_wav(raw:bytes):
    with wave.open(io.BytesIO(raw),"rb") as w:
        if w.getsampwidth()!=2: raise ValueError("錄音必須是 16-bit WAV")
        sr=w.getframerate();ch=w.getnchannels();data=np.frombuffer(w.readframes(w.getnframes()),dtype="<i2").astype(np.float32)/32768.0
    if ch>1:data=data.reshape(-1,ch).mean(axis=1)
    return data,sr

def recognize(samples,sr):
    st=asr.create_stream();st.accept_waveform(sr,samples)
    with asr_lock: asr.decode_stream(st)
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
             "baseline":baseline,"pitch":pitch}
    with reference_lock:REFERENCE[line_id]=profile
    print(f"[reference] line{line_id+1} asr={profile['recognized']} jp={profile['recognized_jyutping']} pitch={len(pitch)}/{len(line['chars'])}",flush=True)
    return profile

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

    items=[];accepted_matches=0;uncertain_count=0
    refbase=reference.get("baseline",{}) if reference else {}
    for row in rows:
        ti=row["target_index"];ri=row["rec_index"]
        heard=tokens[ri] if ri is not None and ri<len(tokens) else ""
        heard_jp=rec_jp[ri] if ri is not None and ri<len(rec_jp) else None
        user_lp=lps[ri] if ri is not None and ri<len(lps) else None
        user_dur=durs[ri] if ri is not None and ri<len(durs) else None
        baseline=refbase.get(ti,{})
        baseline_confusion=bool(
            ri is not None and not row.get("segmental_match") and row.get("rec_base") and
            baseline.get("base") and row.get("rec_base")==baseline.get("base") and
            not baseline.get("direct_match",False)
        )
        accepted=bool(row.get("segmental_match") or baseline_confusion)
        if accepted:accepted_matches+=1
        if baseline_confusion:uncertain_count+=1

        acoustic=confidence_score(user_lp,baseline.get("log_prob"))
        timing=duration_score(user_dur,baseline.get("duration"))
        tone=tones.get(ti) if accepted else None
        tone_score=tone["tone_score"] if tone else None

        initial_match=bool(row.get("initial_match"))
        final_match=bool(row.get("final_match"))
        if row.get("segmental_match"):
            initial_match=True;final_match=True

        initial_score=component_score(initial_match,acoustic,baseline_confusion)
        final_score=component_score(final_match,acoustic,baseline_confusion)

        if baseline_confusion:
            segmental_score=acoustic if acoustic is not None else 82
        elif row.get("segmental_match"):
            segmental_score=acoustic if acoustic is not None else 100
        else:
            pieces=[x for x in (initial_score,final_score) if x is not None]
            segmental_score=round(sum(pieces)/len(pieces)) if pieces else 0

        tone_component=tone_score if tone_score is not None else (75 if accepted else 0)
        timing_component=timing if timing is not None else 85
        score=round(.50*segmental_score+.40*tone_component+.10*timing_component)

        if not accepted and acoustic is not None and acoustic>=72:
            status="wrong";certainty="high"
        elif not accepted:
            status="uncertain";certainty="low";uncertain_count+=1
        elif baseline_confusion:
            status="uncertain";certainty="low"
        elif tone_score is not None and tone_score<70:
            status="tone";certainty="high"
        elif acoustic is not None and acoustic<62:
            status="segmental";certainty="medium"
        else:
            status="ok";certainty="high"

        issues=[]
        if not baseline_confusion:
            if initial_score is not None and initial_score<65:issues.append("聲母")
            if final_score is not None and final_score<65:issues.append("韻母")
        if tone_score is not None and tone_score<70:issues.append("聲調")
        if acoustic is not None and acoustic<62 and not issues:issues.append("音節清晰度")
        if timing is not None and timing<55:issues.append("時長")

        items.append({
            "index":ti,"char":line["chars"][ti],"jyutping":line["jyutping"][ti],
            "expected_tone":line["tones"][ti],"heard":heard,"heard_jyutping":heard_jp,
            "same_char":bool(row.get("same_char")),"segmental_match":bool(row.get("segmental_match")),
            "accepted_match":accepted,"baseline_confusion":baseline_confusion,"certainty":certainty,
            "homophone":bool(row.get("homophone")),
            "target_initial":row.get("target_initial"),"target_final":row.get("target_final"),
            "heard_initial":row.get("rec_initial"),"heard_final":row.get("rec_final"),
            "initial_score":initial_score,"final_score":final_score,
            "acoustic_score":acoustic,"duration_score":timing,
            "asr_log_prob":round(user_lp,3) if user_lp is not None else None,
            "reference_log_prob":round(float(baseline.get("log_prob")),3) if baseline.get("log_prob") is not None else None,
            "duration":round(user_dur,3) if user_dur is not None else None,
            "reference_duration":round(float(baseline.get("duration")),3) if baseline.get("duration") is not None else None,
            "status":status,"issues":issues,"score":score,"tone":tone
        })

    overall=round(sum(x["score"] for x in items)/len(items)) if items else 0
    accepted_accuracy=round(100*accepted_matches/len(line["chars"]))
    stable=sum(1 for x in items if x["status"]=="ok")
    attention=sum(1 for x in items if x["status"] in ("wrong","tone","segmental"))
    return {
        "ok":True,"version":"phoneme-eval-1","line_id":line_id,"target":line["text"],
        "recognized":"".join(tokens),"recognized_jyutping":" ".join(x or "?" for x in rec_jp),
        "syllable_accuracy":accepted_accuracy,"overall_score":overall,
        "stable_count":stable,"attention_count":attention,"uncertain_count":uncertain_count,
        "has_token_log_probs":any(x is not None for x in lps),
        "has_token_durations":any(x is not None for x in durs),
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
  p=urlparse(self.path).path
  if p=="/api/health":return self.js(200,{"ok":True,"version":"phoneme-eval-1","tts":True,"asr":True,"external_api":False,"split_runtime":True})
  item=STATIC.get(p)
  if not item:return self.sendb(404,b"Not found","text/plain")
  return self.sendb(200,item[0].read_bytes(),item[1])
 def do_POST(self):
  u=urlparse(self.path);length=int(self.headers.get("Content-Length","0"))
  if length<=0 or length>12*1024*1024:return self.js(400,{"error":"請求大小不正確"})
  raw=self.rfile.read(length)
  try:
   if u.path=="/api/tts":
    d=json.loads(raw.decode());return self.sendb(200,proxy_tts(str(d.get("text","")),float(d.get("speed",.9))),"audio/wav")
   if u.path=="/api/evaluate":
    line_id=int(parse_qs(u.query).get("line",["-1"])[0]);return self.js(200,evaluate(raw,line_id))
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
            ev=evaluate(ref["wav"],i,ref)
            print(f"[selftest] line{i+1} target={line['text']} asr={ev['recognized']} accepted={ev['syllable_accuracy']} stable={ev['stable_count']} attention={ev['attention_count']} uncertain={ev['uncertain_count']} score={ev['overall_score']} logp={ev['has_token_log_probs']} dur={ev['has_token_durations']}",flush=True)
        except Exception as e:
            print(f"[selftest] line{i+1} deferred: {type(e).__name__}: {e}",flush=True)

print("[boot] song lesson ASR ready",flush=True)
threading.Thread(target=background_selftest,daemon=True).start()
ThreadingHTTPServer(("0.0.0.0",PORT),H).serve_forever()
