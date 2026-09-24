import http from "node:http";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PORT = Number(process.env.PORT || 3000);
const CAI = "https://cantonese.ai/api";
const DIR = path.dirname(fileURLToPath(import.meta.url));
const INDEX = await fs.readFile(path.join(DIR,"index.html"),"utf8");
const APP = await fs.readFile(path.join(DIR,"app.js"),"utf8");
new Function(APP);
if(APP.includes("x-cantonese-key")) throw new Error("Unsafe API-key header transport still present in app.js");

function send(res,status,body,type){
  res.writeHead(status,{
    "content-type":type||"application/json; charset=utf-8",
    "cache-control":"no-store, no-cache, must-revalidate",
    "pragma":"no-cache",
    "x-content-type-options":"nosniff"
  });
  res.end(body);
}
function json(res,status,obj){send(res,status,JSON.stringify(obj),"application/json; charset=utf-8");}
function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
async function readJson(req,max=15*1024*1024){
  const parts=[];let n=0;
  for await(const c of req){n+=c.length;if(n>max)throw new Error("too large");parts.push(c);}
  const raw=Buffer.concat(parts).toString("utf8");
  return raw?JSON.parse(raw):{};
}
async function parseUpstream(r){
  const ct=r.headers.get("content-type")||"";
  if(ct.includes("json"))return await r.json().catch(()=>null);
  return await r.text().catch(()=>null);
}
function upstreamError(x,fallback){
  if(!x)return fallback;
  if(typeof x==="string")return x.slice(0,500)||fallback;
  return (x.error&&x.error.message)||x.message||x.error||fallback;
}
async function toJyutping(text,outputType="list"){
  const r=await fetch(CAI+"/text-to-jyutping",{
    method:"POST",
    headers:{"content-type":"application/json"},
    body:JSON.stringify({text,outputType}),
    signal:AbortSignal.timeout(8000)
  });
  const x=await parseUpstream(r);
  if(!r.ok||!x||x.success===false)throw new Error(upstreamError(x,"粵拼轉換失敗"));
  return x.result;
}
function jyutpingPayload(result){
  if(Array.isArray(result)){
    const jp=result.map(z=>z&&z.jyutping).filter(Boolean).join(" ");
    return {list:result,jyutping:jp};
  }
  return {list:[],jyutping:String(result||"")};
}

const server=http.createServer(async(req,res)=>{
  const u=new URL(req.url,"http://localhost");
  console.log("[request]",req.method,u.pathname);
  try{
    if(req.method==="GET"&&u.pathname==="/")return send(res,200,INDEX,"text/html; charset=utf-8");
    if(req.method==="GET"&&u.pathname==="/app.js")return send(res,200,APP,"application/javascript; charset=utf-8");
    if(req.method==="GET"&&u.pathname==="/api/health")return json(res,200,{ok:true,version:"20260924h",keyTransport:"json-body"});

    if(req.method==="GET"&&u.pathname==="/practice"){
      const text=String(u.searchParams.get("text")||"").trim();
      if(!text)return send(res,400,"<h1>沒有文字</h1><p><a href='/'>返回</a></p>","text/html; charset=utf-8");
      let jp="";
      try{jp=String(await toJyutping(text,"text")||"");}
      catch(e){return send(res,502,"<h1>粵拼轉換失敗</h1><pre>"+esc(e.message)+"</pre><p><a href='/'>返回</a></p>","text/html; charset=utf-8");}
      const html="<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>粵拼結果</title><style>body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:720px;margin:60px auto;padding:20px;background:#f3f1ea}main{background:white;padding:28px;border-radius:20px}h1{font-size:44px}code{font-size:24px;color:#1c5d4f}</style><main><h1>"+esc(text)+"</h1><p>標準粵拼</p><code>"+esc(jp)+"</code><p><a href='/?v=20260924f'>← 返回發音教練</a></p></main>";
      return send(res,200,html,"text/html; charset=utf-8");
    }

    if(req.method==="POST"&&u.pathname==="/api/client-error"){
      const d=await readJson(req,50*1024).catch(()=>({}));
      console.error("[client]",d.kind||"unknown",String(d.message||"").slice(0,1000));
      return json(res,200,{ok:true});
    }

    if(req.method!=="POST"||!u.pathname.startsWith("/api/"))return json(res,404,{error:"Not found"});
    const d=await readJson(req);

    if(u.pathname==="/api/jyutping"){
      const text=String(d.text||"").trim();
      if(!text)return json(res,400,{error:"請先輸入文字。"});
      const result=await toJyutping(text,"list");
      return json(res,200,{success:true,...jyutpingPayload(result)});
    }

    if(u.pathname==="/api/tts"){
      const k=String(d.apiKey||"").trim();
      if(!k)return json(res,401,{error:"請先輸入 API Key。"});

      const basePayload={
        api_key:k,
        text:String(d.text||""),
        frame_rate:"24000",
        speed:0.92,
        pitch:0,
        language:"cantonese",
        output_extension:"wav",
        should_return_timestamp:false
      };
      const candidates=[
        {label:"v6",payload:{...basePayload,model_id:"v6",jyutping:String(d.jyutping||"")}},
        {label:"v5",payload:{...basePayload,model_id:"v5",jyutping:String(d.jyutping||"")}},
        {label:"default",payload:{...basePayload}}
      ];

      let lastError=null;
      for(const candidate of candidates){
        console.log("[tts] try",candidate.label,{textLength:basePayload.text.length,jyutpingLength:String(d.jyutping||"").length});
        const t0=Date.now();
        const r=await fetch(CAI+"/tts",{
          method:"POST",
          headers:{"content-type":"application/json"},
          body:JSON.stringify(candidate.payload),
          signal:AbortSignal.timeout(20000)
        });
        const ct=r.headers.get("content-type")||"";
        console.log("[tts] response",candidate.label,{status:r.status,contentType:ct,ms:Date.now()-t0});

        if(r.ok){
          const buf=Buffer.from(await r.arrayBuffer());
          console.log("[tts] success",candidate.label,{bytes:buf.length});
          res.writeHead(200,{
            "content-type":ct||"audio/wav",
            "cache-control":"no-store",
            "x-content-type-options":"nosniff",
            "x-tts-model-used":candidate.label
          });
          return res.end(buf);
        }

        const x=await parseUpstream(r);
        const message=String(upstreamError(x,"TTS 失敗"));
        console.error("[tts] error",candidate.label,r.status,message.slice(0,500));
        lastError={status:r.status,message};

        // Auth/quota errors will not be fixed by trying another model.
        if([401,403,429].includes(r.status)){
          return json(res,r.status,{error:message});
        }

        // Only fall through to the next model when the upstream rejects model selection.
        const modelRejected=/model/i.test(message)&&/(invalid|unsupported|not found|unknown|unavailable)/i.test(message);
        if(candidate.label!=="default"&&modelRejected)continue;

        // Some deployments return a bare "Invalid model id" as a generic 400/422.
        if(candidate.label!=="default"&&[400,422].includes(r.status)&&/model/i.test(message))continue;

        return json(res,r.status||502,{error:message});
      }

      return json(res,lastError?.status||502,{error:lastError?.message||"TTS 失敗"});
    }

    if(u.pathname==="/api/score"){
      const k=String(d.apiKey||"").trim();
      if(!k)return json(res,401,{error:"請先輸入 API Key。"});
      const raw=Buffer.from(String(d.audioBase64||""),"base64");
      if(!raw.length||raw.length>10*1024*1024)return json(res,400,{error:"錄音無效"});
      const form=new FormData();
      form.append("api_key",k);
      form.append("text",String(d.text||""));
      form.append("language","cantonese");
      form.append("audio",new Blob([raw],{type:"audio/wav"}),"recording.wav");
      const r=await fetch(CAI+"/score-pronunciation",{method:"POST",body:form,signal:AbortSignal.timeout(20000)});
      const x=await parseUpstream(r);
      if(!r.ok||!x||x.success===false)return json(res,r.status||502,{error:upstreamError(x,"評分失敗")});
      return json(res,200,x);
    }

    return json(res,404,{error:"Not found"});
  }catch(e){
    console.error("[server error]",e&&e.stack?e.stack:e);
    return json(res,502,{error:e&&e.name==="TimeoutError"?"上游服務超時":String(e&&e.message||e)});
  }
});

server.listen(PORT,"0.0.0.0",()=>{
  console.log("Cantonese Coach on",PORT);
  fetch(CAI+"/text-to-jyutping",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({text:"你好嗎",outputType:"text"}),signal:AbortSignal.timeout(8000)})
    .then(async r=>console.log("[selftest] jyutping",r.status,await r.text()))
    .catch(e=>console.error("[selftest] failed",e.name,e.message));
});
