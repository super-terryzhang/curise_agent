"use strict";
(function(){
  function q(s){return document.querySelector(s);}
  var txt=q("#text"),analyseBtn=q("#analyse"),listen=q("#listen"),record=q("#record"),status=q("#status");
  var syllables=q("#syllables"),speed=q("#speed"),speedValue=q("#speedValue"),standardAudio=q("#standardAudio"),userAudio=q("#userAudio");
  var mediaRecorder=null,chunks=[],stream=null,currentUrl=null,userUrl=null;

  function esc(v){return String(v==null?"":v).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c];});}
  function analyse(){
    try{
      if(typeof CantoJpMin==="undefined")throw new Error("本地粵拼庫沒有載入");
      var arr=CantoJpMin.toJyutpingArray(txt.value),parts=[];
      syllables.innerHTML=arr.map(function(item){
        var opts=Array.isArray(item.jyutpings)?item.jyutpings:[],jp=opts[0]||"";
        if(jp)parts.push(jp);
        return '<div class="sy"><b>'+esc(item.original||"")+'</b><small>'+esc(jp||"—")+'</small></div>';
      }).join("");
      status.textContent="粵拼："+parts.join(" ");
    }catch(e){status.textContent="粵拼錯誤："+e.message;}
  }
  analyseBtn.addEventListener("click",analyse);
  txt.addEventListener("change",analyse);
  speed.addEventListener("input",function(){speedValue.textContent=Number(speed.value).toFixed(2)+"×";});

  listen.addEventListener("click",async function(){
    var text=txt.value.trim();if(!text){status.textContent="請輸入文字。";return;}
    listen.disabled=true;status.textContent="模型正在生成標準粵語…首次可能需要數秒。";
    try{
      var r=await fetch("/api/tts",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({text:text,speed:Number(speed.value)})});
      if(!r.ok){var e=await r.json().catch(function(){return {}});throw new Error(e.error||("HTTP "+r.status));}
      var blob=await r.blob();if(!blob.size)throw new Error("收到空音訊");
      if(currentUrl)URL.revokeObjectURL(currentUrl);
      currentUrl=URL.createObjectURL(blob);standardAudio.src=currentUrl;standardAudio.classList.remove("hidden");
      try{await standardAudio.play();status.textContent="正在播放固定 VITS 標準音。";}catch(e){status.textContent="音訊已生成，請按播放器 ▶。";}
      standardAudio.onended=function(){status.textContent="播放完成。可以開始跟讀。";};
    }catch(e){status.textContent="VITS 發音錯誤："+e.message;}
    finally{listen.disabled=false;}
  });

  record.addEventListener("click",async function(){
    if(mediaRecorder&&mediaRecorder.state==="recording"){mediaRecorder.stop();return;}
    try{
      stream=await navigator.mediaDevices.getUserMedia({audio:true});
      if(!window.MediaRecorder)throw new Error("此瀏覽器不支援 MediaRecorder");
      var mime="";
      if(MediaRecorder.isTypeSupported("audio/webm;codecs=opus"))mime="audio/webm;codecs=opus";
      else if(MediaRecorder.isTypeSupported("audio/mp4"))mime="audio/mp4";
      mediaRecorder=mime?new MediaRecorder(stream,{mimeType:mime}):new MediaRecorder(stream);chunks=[];
      mediaRecorder.ondataavailable=function(e){if(e.data&&e.data.size)chunks.push(e.data);};
      mediaRecorder.onstop=function(){
        var blob=new Blob(chunks,{type:mediaRecorder.mimeType||"audio/webm"});
        if(userUrl)URL.revokeObjectURL(userUrl);userUrl=URL.createObjectURL(blob);
        userAudio.src=userUrl;userAudio.classList.remove("hidden");
        if(stream)stream.getTracks().forEach(function(t){t.stop();});
        record.textContent="● 再錄一次";status.textContent="錄音完成。可以對照標準音回放。";
      };
      mediaRecorder.start();record.textContent="■ 停止錄音";status.textContent="正在錄音…正常說，不要唱。";
    }catch(e){status.textContent="錄音錯誤："+e.message;}
  });

  async function health(){
    try{
      var r=await fetch("/api/health",{cache:"no-store"}),d=await r.json();
      if(!r.ok||!d.ok)throw new Error("模型未就緒");
      status.textContent="VITS 模型已就緒 · "+Math.round(d.model_bytes/1024/1024)+" MB · "+d.sample_rate+" Hz";
      analyse();
    }catch(e){status.textContent="模型狀態錯誤："+e.message;}
  }
  health();
})();