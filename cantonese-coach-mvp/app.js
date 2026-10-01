"use strict";
(function(){
  function q(s){return document.querySelector(s);}
  var txt=q("#text"),btnA=q("#analyse"),btnL=q("#listen"),btnR=q("#record");
  var status=q("#status"),syllables=q("#syllables"),voiceSelect=q("#voiceSelect"),voiceInfo=q("#voiceInfo"),userAudio=q("#userAudio");
  var jyutping="",voices=[],cantoVoices=[],mediaRecorder=null,chunks=[],stream=null;
  function esc(v){return String(v==null?"":v).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c];});}
  function normLang(v){return String(v||"").toLowerCase().replace(/_/g,"-");}
  function isCantoneseVoice(v){var lang=normLang(v.lang),name=String(v.name||"").toLowerCase();return lang.indexOf("yue")===0||lang==="zh-hk"||name.includes("cantonese")||name.includes("hong kong")||name.includes("香港")||name.includes("粵");}
  function analyse(){
    try{
      if(typeof CantoJpMin==="undefined")throw new Error("本地粵拼庫沒有載入");
      var arr=CantoJpMin.toJyutpingArray(txt.value),parts=[];
      syllables.innerHTML=arr.map(function(item){var opts=Array.isArray(item.jyutpings)?item.jyutpings:[],jp=opts[0]||"";if(jp)parts.push(jp);return '<div class="sy"><b>'+esc(item.original||"")+'</b><small>'+esc(jp||"—")+'</small></div>';}).join("");
      jyutping=parts.join(" ");status.textContent="本地粵拼："+jyutping;btnL.disabled=false;btnR.disabled=false;
    }catch(e){status.textContent="粵拼錯誤："+e.message;btnL.disabled=true;btnR.disabled=true;}
  }
  function loadVoices(){
    if(!("speechSynthesis" in window)){voiceInfo.textContent="這個瀏覽器不支援 Web Speech TTS。";voiceSelect.innerHTML='<option value="">不支援</option>';btnL.disabled=true;return;}
    voices=window.speechSynthesis.getVoices()||[];cantoVoices=voices.filter(isCantoneseVoice);voiceSelect.innerHTML="";
    if(cantoVoices.length){
      cantoVoices.forEach(function(v,i){var o=document.createElement("option");o.value=String(i);o.textContent=v.name+" · "+v.lang+" · "+(v.localService?"本機":"系統/網路");voiceSelect.appendChild(o);});
      var v=cantoVoices[0];voiceInfo.innerHTML='<span class="good">✓ 找到粵語 voice：</span>'+esc(v.name)+' ('+esc(v.lang)+') · '+(v.localService?"本機":"系統/網路");
    }else{
      var o=document.createElement("option");o.value="";o.textContent="未找到明確的 yue / zh-HK voice";voiceSelect.appendChild(o);
      voiceInfo.innerHTML='<span class="warn">⚠ 未檢測到明確粵語 voice。</span> 仍可嘗試 yue-HK 語言標籤，但裝置可能改用其他中文 voice。';
    }
  }
  btnA.addEventListener("click",analyse);txt.addEventListener("change",analyse);
  btnL.addEventListener("click",function(){
    if(!("speechSynthesis" in window)){status.textContent="此瀏覽器不支援 TTS。";return;}
    var text=txt.value.trim();if(!text){status.textContent="請先輸入文字。";return;}
    var u=new SpeechSynthesisUtterance(text),idx=Number(voiceSelect.value);
    if(cantoVoices.length&&Number.isFinite(idx)&&cantoVoices[idx]){u.voice=cantoVoices[idx];u.lang=cantoVoices[idx].lang;}else{u.lang="yue-HK";}
    u.rate=.86;u.pitch=1;u.volume=1;u.onstart=function(){status.textContent="正在播放系統粵語標準音…";};u.onend=function(){status.textContent="播放完成。可以開始跟讀。";};u.onerror=function(e){status.textContent="TTS 錯誤："+(e.error||"unknown");};
    window.speechSynthesis.cancel();window.speechSynthesis.speak(u);
  });
  btnR.addEventListener("click",async function(){
    if(mediaRecorder&&mediaRecorder.state==="recording"){mediaRecorder.stop();return;}
    try{
      stream=await navigator.mediaDevices.getUserMedia({audio:true});if(!window.MediaRecorder)throw new Error("此瀏覽器不支援 MediaRecorder");
      var mime="";if(MediaRecorder.isTypeSupported("audio/webm;codecs=opus"))mime="audio/webm;codecs=opus";else if(MediaRecorder.isTypeSupported("audio/mp4"))mime="audio/mp4";
      mediaRecorder=mime?new MediaRecorder(stream,{mimeType:mime}):new MediaRecorder(stream);chunks=[];
      mediaRecorder.ondataavailable=function(e){if(e.data&&e.data.size)chunks.push(e.data);};
      mediaRecorder.onstop=function(){var blob=new Blob(chunks,{type:mediaRecorder.mimeType||"audio/webm"}),url=URL.createObjectURL(blob);userAudio.src=url;userAudio.classList.remove("hidden");if(stream)stream.getTracks().forEach(function(t){t.stop();});btnR.textContent="● 再錄一次";status.textContent="錄音完成。請回放，和標準音比較。";};
      mediaRecorder.start();btnR.textContent="■ 停止錄音";status.textContent="正在錄音…正常說，不要唱。";
    }catch(e){status.textContent="錄音錯誤："+e.message;}
  });
  if("speechSynthesis" in window){loadVoices();if("onvoiceschanged" in window.speechSynthesis)window.speechSynthesis.onvoiceschanged=loadVoices;setTimeout(loadVoices,500);setTimeout(loadVoices,1500);}
  if(typeof CantoJpMin!=="undefined"){status.textContent="Zero External Service Demo 已載入。";analyse();}else{status.textContent="本地粵拼庫載入失敗。";}
})();