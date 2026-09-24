"use strict";
(function(){
  function q(s){return document.querySelector(s);}
  var key=q("#key"), txt=q("#text"), form=q("#practiceForm"), btnA=q("#analyse"), btnL=q("#listen"), btnR=q("#record");
  var status=q("#status"), syllables=q("#syllables"), audio=q("#audio");
  var jyutping="", recorder=null;

  function report(kind,message){
    try{
      fetch("/api/client-error",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({kind:kind,message:String(message)}),keepalive:true});
    }catch(e){}
  }
  window.addEventListener("error",function(e){report("window-error",e.message+" @ "+e.filename+":"+e.lineno);});
  window.addEventListener("unhandledrejection",function(e){report("unhandledrejection",e.reason&&e.reason.message?e.reason.message:String(e.reason));});

  if(!key||!txt||!form||!btnA||!btnL||!btnR||!status){report("init","missing DOM element");return;}

  try{key.value=sessionStorage.getItem("cai-key")||"";}catch(e){}
  key.addEventListener("input",function(){try{sessionStorage.setItem("cai-key",key.value);}catch(e){}});

  async function jsonPost(url,body,timeoutMs){
    var c=new AbortController(), t=setTimeout(function(){c.abort();},timeoutMs||10000);
    try{
      var r=await fetch(url,{method:"POST",headers:{"content-type":"application/json","x-cantonese-key":key.value.trim()},body:JSON.stringify(body),signal:c.signal});
      var d=await r.json().catch(function(){return {error:"伺服器回應不是 JSON"};});
      if(!r.ok)throw new Error(d.error||("HTTP "+r.status));
      return d;
    }catch(e){
      if(e&&e.name==="AbortError")throw new Error("請求超時");
      throw e;
    }finally{clearTimeout(t);}
  }

  function esc(v){return String(v==null?"":v).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c];});}

  async function analyse(){
    btnA.disabled=true; status.textContent="正在拆成粵拼…";
    try{
      var d=await jsonPost("/api/jyutping",{text:txt.value},10000);
      jyutping=d.jyutping||"";
      var list=Array.isArray(d.list)?d.list:[];
      syllables.innerHTML=list.map(function(x){
        var ch=x.character||x.text||x.word||x.char||"";
        var jp=x.jyutping||x.romanization||"";
        return '<div class="sy"><b>'+esc(ch)+'</b><small>'+esc(jp||"—")+'</small></div>';
      }).join("");
      btnL.disabled=!jyutping; btnR.disabled=!jyutping;
      status.textContent="標準粵拼："+(jyutping||"（沒有結果）");
    }catch(e){
      status.textContent="粵拼錯誤："+e.message;
      report("jyutping",e.message);
    }finally{btnA.disabled=false;}
  }

  form.addEventListener("submit",function(e){e.preventDefault();analyse();});

  btnL.addEventListener("click",async function(){
    if(!key.value.trim()){status.textContent="請先填 Cantonese.ai API Key。";return;}
    if(!jyutping){status.textContent="請先按「拆成粵拼」。";return;}
    btnL.disabled=true; status.textContent="TTS 請求已送出，正在生成標準音…";
    var c=new AbortController(), t=setTimeout(function(){c.abort();},25000);
    try{
      var r=await fetch("/api/tts",{method:"POST",headers:{"content-type":"application/json","x-cantonese-key":key.value.trim()},body:JSON.stringify({text:txt.value,jyutping:jyutping}),signal:c.signal});
      if(!r.ok){var d=await r.json().catch(function(){return {};});throw new Error(d.error||("HTTP "+r.status));}
      var b=await r.blob(); if(!b.size)throw new Error("收到空音訊");
      var u=URL.createObjectURL(b); audio.src=u; audio.classList.remove("hidden");
      try{await audio.play();status.textContent="正在播放標準音。";}catch(e){status.textContent="音訊已生成，請按播放器的 ▶。";}
      audio.onended=function(){status.textContent="播放完成。現在跟讀一次。";URL.revokeObjectURL(u);};
    }catch(e){
      status.textContent=e&&e.name==="AbortError"?"TTS 錯誤：25 秒內沒有收到音訊。":"TTS 錯誤："+e.message;
      report("tts",e.message);
    }finally{clearTimeout(t);btnL.disabled=false;}
  });

  btnR.addEventListener("click",async function(){
    if(recorder){await stopRecording();return;}
    if(!key.value.trim()){status.textContent="請先填 Cantonese.ai API Key。";return;}
    if(!jyutping){status.textContent="請先按「拆成粵拼」。";return;}
    try{await startRecording();}catch(e){status.textContent="麥克風錯誤："+e.message;report("microphone",e.message);}
  });

  async function startRecording(){
    var stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:false}});
    var AC=window.AudioContext||window.webkitAudioContext; if(!AC)throw new Error("此瀏覽器不支援 AudioContext");
    var ctx=new AC(), src=ctx.createMediaStreamSource(stream), proc=ctx.createScriptProcessor(4096,1,1), chunks=[];
    proc.onaudioprocess=function(e){chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));};
    src.connect(proc);proc.connect(ctx.destination);
    recorder={stream:stream,ctx:ctx,src:src,proc:proc,chunks:chunks};
    btnR.textContent="■ 停止並評分"; status.textContent="正在錄音…請正常說，不要唱。";
  }

  async function stopRecording(){
    var r=recorder; recorder=null; r.proc.disconnect();r.src.disconnect();r.stream.getTracks().forEach(function(t){t.stop();});
    var rate=r.ctx.sampleRate;await r.ctx.close();
    var n=r.chunks.reduce(function(a,c){return a+c.length;},0), samples=new Float32Array(n),o=0;
    r.chunks.forEach(function(c){samples.set(c,o);o+=c.length;});
    var wav=encodeWav(samples,rate), blob=new Blob([wav],{type:"audio/wav"});
    btnR.textContent="● 開始跟讀"; status.textContent="正在評分…";
    try{
      var b64=await toB64(blob), d=await jsonPost("/api/score",{text:txt.value,audioBase64:b64},25000);
      renderScore(d); status.textContent="評分完成。可以再試一次。";
    }catch(e){status.textContent="評分錯誤："+e.message;report("score",e.message);}
  }

  function encodeWav(s,rate){
    var b=new ArrayBuffer(44+s.length*2),v=new DataView(b);
    function w(o,x){for(var i=0;i<x.length;i++)v.setUint8(o+i,x.charCodeAt(i));}
    w(0,"RIFF");v.setUint32(4,36+s.length*2,true);w(8,"WAVE");w(12,"fmt ");v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,rate,true);v.setUint32(28,rate*2,true);v.setUint16(32,2,true);v.setUint16(34,16,true);w(36,"data");v.setUint32(40,s.length*2,true);
    for(var i=0,o=44;i<s.length;i++,o+=2){var x=Math.max(-1,Math.min(1,s[i]));v.setInt16(o,x<0?x*32768:x*32767,true);}return b;
  }
  async function toB64(blob){
    var a=await blob.arrayBuffer(),u=new Uint8Array(a),str="",step=32768;
    for(var i=0;i<u.length;i+=step)str+=String.fromCharCode.apply(null,u.subarray(i,i+step));return btoa(str);
  }
  function parts(x){var m=String(x||"").match(/^(.*?)([1-6])$/);return {raw:x||"∅",base:m?m[1]:x,tone:m?m[2]:null};}
  function renderScore(d){
    q("#result").classList.remove("hidden");q("#score").textContent=Math.round(d.score||0);
    var p=q("#pass");p.textContent=d.passed?"發音通過":"需要再練";p.className="badge"+(d.passed?"":" bad");
    q("#expected").textContent=d.expectedJyutping||"—";q("#heard").textContent=d.transcribedJyutping||"—";
    var a=String(d.expectedJyutping||"").trim().split(/\s+/).filter(Boolean),b=String(d.transcribedJyutping||"").trim().split(/\s+/).filter(Boolean),n=Math.max(a.length,b.length),html="",hit=0,total=0;
    for(var i=0;i<n;i++){var e=parts(a[i]),h=parts(b[i]),exact=e.raw===h.raw,base=e.base===h.base,tone=e.tone&&e.tone===h.tone;if(e.tone){total++;if(tone)hit++;}var msg=exact?"完全一致":base&&!tone?("音節對，但聲調 "+e.tone+" → "+h.tone):(!a[i]?"多讀":!b[i]?"漏讀":tone?"聲調對，但音節不同":"音節與聲調都有差異");html+='<div class="diag"><code>'+esc(e.raw)+'</code><code class="'+(exact?"ok":"bad")+'">'+esc(h.raw)+'</code><span class="'+(exact?"ok":"bad")+'">'+esc(msg)+'</span></div>';}
    q("#diags").innerHTML=html;q("#tone").textContent=total?Math.round(hit/total*100)+"%":"—";q("#result").scrollIntoView({behavior:"smooth"});
  }

  status.textContent="前端已載入。請按「拆成粵拼」開始。";
  btnA.disabled=false;
  report("init","ready");
})();