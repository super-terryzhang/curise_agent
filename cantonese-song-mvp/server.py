from __future__ import annotations
import io,json,math,os,pathlib,threading,time,wave
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlparse,parse_qs
import numpy as np
import sherpa_onnx
from opencc import OpenCC

ROOT=pathlib.Path(__file__).resolve().parent
PORT=int(os.environ.get("PORT","10000"))
TTS_DIR=ROOT/"model"/"tts"
ASR_DIR=ROOT/"model"/"asr"
t2s=OpenCC("t2s")

SONG=[
 {"id":0,"text":"流水像清得沒帶半顆沙","jyutping":["lau4","seoi2","zoeng6","cing1","dak1","mut6","daai3","bun3","fo2","saa1"]},
 {"id":1,"text":"前身被擱在上游風化","jyutping":["cin4","san1","bei6","gok3","zoi6","soeng6","jau4","fung1","faa3"]},
]
for line in SONG:
    line["chars"]=list(line["text"])
    line["tones"]=[int(x[-1]) for x in line["jyutping"]]

tts_cfg=sherpa_onnx.OfflineTtsConfig(
 model=sherpa_onnx.OfflineTtsModelConfig(
  vits=sherpa_onnx.OfflineTtsVitsModelConfig(
   model=str(TTS_DIR/"vits-cantonese-hf-xiaomaiiwn.onnx"),
   lexicon=str(TTS_DIR/"lexicon.txt"),tokens=str(TTS_DIR/"tokens.txt"),length_scale=1.0),
  provider="cpu",debug=False,num_threads=1),
 rule_fsts=str(TTS_DIR/"rule.fst"),rule_fars="",max_num_sentences=1)
if not tts_cfg.validate(): raise RuntimeError("TTS config invalid")
tts=sherpa_onnx.OfflineTts(tts_cfg); tts_lock=threading.Lock()

asr=sherpa_onnx.OfflineRecognizer.from_wenet_ctc(
 model=str(ASR_DIR/"model.int8.onnx"),
 tokens=str(ASR_DIR/"tokens.txt"),
 num_threads=2,sample_rate=16000,feature_dim=80,
 decoding_method="greedy_search",provider="cpu")
asr_lock=threading.Lock()

def wav_bytes(samples:np.ndarray,sr:int)->bytes:
    pcm=(np.clip(samples,-1,1)*32767).astype("<i2")
    out=io.BytesIO()
    with wave.open(out,"wb") as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(sr);w.writeframes(pcm.tobytes())
    return out.getvalue()

def synth(text:str,speed:float=.9):
    text=text.strip()
    if not text: raise ValueError("文字為空")
    with tts_lock: a=tts.generate(text=t2s.convert(text),sid=0,speed=max(.65,min(1.35,float(speed))))
    s=np.asarray(a.samples,dtype=np.float32)
    if s.size<100: raise RuntimeError("TTS 生成空音訊")
    return wav_bytes(s,int(a.sample_rate))

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
    return {"text":str(r.text),"tokens":list(r.tokens),"timestamps":[float(x) for x in r.timestamps]}

def flatten_tokens(tokens,times):
    out=[];ts=[]
    for i,tok in enumerate(tokens):
        chars=list(str(tok).strip())
        if not chars: continue
        start=times[i] if i<len(times) else (ts[-1] if ts else 0.0)
        nxt=times[i+1] if i+1<len(times) else start+.22
        for j,ch in enumerate(chars):
            out.append(ch);ts.append(start+(nxt-start)*j/max(1,len(chars)))
    return out,ts

def align(target,rec):
    n,m=len(target),len(rec)
    dp=[[0]*(m+1) for _ in range(n+1)]
    bt=[[None]*(m+1) for _ in range(n+1)]
    for i in range(1,n+1):dp[i][0]=i;bt[i][0]="del"
    for j in range(1,m+1):dp[0][j]=j;bt[0][j]="ins"
    for i in range(1,n+1):
        for j in range(1,m+1):
            same=t2s.convert(target[i-1])==t2s.convert(rec[j-1])
            opts=[(dp[i-1][j]+1,"del"),(dp[i][j-1]+1,"ins"),(dp[i-1][j-1]+(0 if same else 1),"same" if same else "sub")]
            dp[i][j],bt[i][j]=min(opts,key=lambda x:x[0])
    rows=[];insertions=[];i,j=n,m
    while i or j:
        op=bt[i][j]
        if op in ("same","sub"):
            rows.append({"target_index":i-1,"rec_index":j-1,"op":op});i-=1;j-=1
        elif op=="del":
            rows.append({"target_index":i-1,"rec_index":None,"op":"del"});i-=1
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

def tone_analysis(samples,sr,line,rows,rec_times,duration):
    raw={}
    for row in rows:
        ri=row["rec_index"];ti=row["target_index"]
        if ri is None or ri>=len(rec_times):continue
        st=rec_times[ri]
        en=rec_times[ri+1] if ri+1<len(rec_times) else duration
        if en-st<.08:en=min(duration,st+.18)
        tr=f0_track(samples,sr,st,en)
        if tr:raw[ti]={"semi":tr,"start":st,"end":en}
    anchors={1:[],3:[],6:[]};allsemi=[]
    for ti,d in raw.items():
        tone=line["tones"][ti];center=float(np.median(d["semi"]));allsemi.extend(d["semi"])
        if tone in anchors:anchors[tone].append(center)
    if not allsemi:return raw,{}
    p15,p50,p85=np.percentile(np.asarray(allsemi),[15,50,85]).tolist()
    high=float(np.median(anchors[1])) if anchors[1] else p85
    mid=float(np.median(anchors[3])) if anchors[3] else p50
    low=float(np.median(anchors[6])) if anchors[6] else p15
    if not(high>mid+.25 and mid>low+.15):
        high,mid,low=p85,p50,p15
        if high-mid<.25:high=mid+.75
        if mid-low<.15:low=mid-.55
    def level(s):
        if s>=mid:return 3+2*(s-mid)/(high-mid)
        return 3-(mid-s)/(mid-low)
    out={}
    for ti,d in raw.items():
        vals=np.clip(np.asarray(resample(d["semi"],20)),low-2,high+2)
        levels=np.asarray([level(float(x)) for x in vals])
        distances=[];targets=[]
        for t in range(1,7):
            z=tmpl(t);targets.append(z);distances.append(float(np.sqrt(np.mean((levels-z)**2))))
        pred=int(np.argmin(distances))+1;goal=line["tones"][ti];dist=distances[goal-1]
        quality=int(round(100*math.exp(-.5*(dist/.82)**2)))
        z=targets[goal-1]
        us=float(np.mean(levels[:4]));ue=float(np.mean(levels[-4:]));ts=float(np.mean(z[:4]));te=float(np.mean(z[-4:]))
        slope=(ue-us)-(te-ts)
        probs=np.exp(-.5*(np.asarray(distances)/.72)**2);probs=(probs/probs.sum()).tolist()
        notes=[]
        if abs(us-ts)>.5:notes.append("起點偏"+("高" if us>ts else "低"))
        if abs(ue-te)>.5:notes.append("終點偏"+("高" if ue>te else "低"))
        if abs(slope)>.5:notes.append("升降幅度"+("過大" if abs(ue-us)>abs(te-ts) else "不足"))
        out[ti]={"tone_score":quality,"predicted_tone":pred,"tone_probs":[round(x,3) for x in probs],
                 "start_error":round(us-ts,2),"end_error":round(ue-te,2),"slope_error":round(slope,2),
                 "notes":notes,"f0_levels":[round(float(x),2) for x in levels]}
    return raw,out

def evaluate(raw,line_id):
    if line_id not in (0,1):raise ValueError("未知歌詞行")
    samples,sr=read_wav(raw);duration=len(samples)/sr
    if duration<.4 or duration>15:raise ValueError("錄音長度不合適")
    rec=recognize(samples,sr);tokens,times=flatten_tokens(rec["tokens"],rec["timestamps"])
    rows,insertions=align(SONG[line_id]["chars"],tokens)
    _,tones=tone_analysis(samples,sr,SONG[line_id],rows,times,duration)
    items=[];matches=0
    for row in rows:
        ti=row["target_index"];ri=row["rec_index"];same=row["op"]=="same"
        if same:matches+=1
        tone=tones.get(ti)
        content_score=100 if same else (25 if row["op"]=="sub" else 0)
        tone_score=tone["tone_score"] if tone else None
        score=round(.7*content_score+.3*(tone_score if tone_score is not None else 50))
        if same and tone_score is not None and tone_score<68:status="tone"
        elif same:status="ok"
        else:status="wrong"
        items.append({"index":ti,"char":SONG[line_id]["chars"][ti],"jyutping":SONG[line_id]["jyutping"][ti],
                      "expected_tone":SONG[line_id]["tones"][ti],"heard":tokens[ri] if ri is not None and ri<len(tokens) else "",
                      "op":row["op"],"status":status,"score":score,"tone":tone})
    overall=round(sum(x["score"] for x in items)/len(items)) if items else 0
    return {"ok":True,"line_id":line_id,"target":SONG[line_id]["text"],"recognized":"".join(tokens),
            "asr_text":rec["text"],"content_accuracy":round(100*matches/len(SONG[line_id]["chars"])),
            "overall_score":overall,"items":items,"insertions":[tokens[i] for i in insertions if i<len(tokens)]}

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
  if p=="/api/health":return self.js(200,{"ok":True,"version":"song-lesson-2","tts":True,"asr":True,"external_api":False})
  item=STATIC.get(p)
  if not item:return self.sendb(404,b"Not found","text/plain")
  return self.sendb(200,item[0].read_bytes(),item[1])
 def do_POST(self):
  u=urlparse(self.path);length=int(self.headers.get("Content-Length","0"))
  if length<=0 or length>12*1024*1024:return self.js(400,{"error":"請求大小不正確"})
  raw=self.rfile.read(length)
  try:
   if u.path=="/api/tts":
    d=json.loads(raw.decode());return self.sendb(200,synth(str(d.get("text","")),float(d.get("speed",.9))),"audio/wav")
   if u.path=="/api/evaluate":
    line_id=int(parse_qs(u.query).get("line",["-1"])[0]);return self.js(200,evaluate(raw,line_id))
   return self.js(404,{"error":"Not found"})
  except Exception as e:
   print("[error]",type(e).__name__,e,flush=True);return self.js(400,{"error":str(e)})
 def do_HEAD(self):
  self.send_response(200);self.end_headers()

print("[selftest] TTS + Cantonese ASR",flush=True)
for i,line in enumerate(SONG):
    raw=synth(line["text"],1.0);s,sr=read_wav(raw);r=recognize(s,sr)
    print(f"[selftest] line{i+1} target={line['text']} asr={r['text']}",flush=True)
    if not r["tokens"]:raise RuntimeError("ASR selftest returned no tokens")
print("[boot] song lesson ready",flush=True)
ThreadingHTTPServer(("0.0.0.0",PORT),H).serve_forever()
