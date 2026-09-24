"use strict";
(function(){
  function q(s){return document.querySelector(s);}
  var txt=q("#text"),analyseBtn=q("#analyse"),listen=q("#listen"),record=q("#record"),status=q("#status");
  var syllables=q("#syllables"),speed=q("#speed"),speedValue=q("#speedValue"),standardAudio=q("#standardAudio"),userAudio=q("#userAudio");
  var playTarget=q("#playTarget"),evalRecord=q("#evalRecord"),evalStatus=q("#evalStatus"),results=q("#results");
  var targetChar=q("#targetChar"),targetJyutping=q("#targetJyutping"),targetName=q("#targetName");
  var currentItems=[],selectedIndex=-1,currentUrl=null,userUrl=null;
  var activeCapture=null,calibration=loadCalibration();

  var toneNames={1:"高平 55",2:"高升 25",3:"中平 33",4:"低降 21",5:"低升 23",6:"低平 22"};

  function esc(v){return String(v==null?"":v).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c];});}
  function toneOf(jp){var m=String(jp||"").match(/([1-6])$/);return m?Number(m[1]):null;}
  function median(a){if(!a.length)return NaN;var b=a.slice().sort(function(x,y){return x-y;}),m=Math.floor(b.length/2);return b.length%2?b[m]:(b[m-1]+b[m])/2;}
  function st(hz){return 12*Math.log2(hz);}

  async function ttsBlob(textValue,rate){
    var r=await fetch("/api/tts",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({text:textValue,speed:rate||.9})});
    if(!r.ok){var e=await r.json().catch(function(){return {};});throw new Error(e.error||("HTTP "+r.status));}
    var b=await r.blob();if(!b.size)throw new Error("收到空音訊");return b;
  }
  async function playTts(textValue){
    status.textContent="正在生成標準粵語…";
    var blob=await ttsBlob(textValue,Number(speed.value));
    if(currentUrl)URL.revokeObjectURL(currentUrl);currentUrl=URL.createObjectURL(blob);
    standardAudio.src=currentUrl;standardAudio.classList.remove("hidden");
    try{await standardAudio.play();status.textContent="正在播放固定 VITS 標準音。";}catch(e){status.textContent="音訊已生成，請按播放器 ▶。";}
  }

  function analyse(){
    try{
      if(typeof CantoJpMin==="undefined")throw new Error("本地粵拼庫沒有載入");
      var arr=CantoJpMin.toJyutpingArray(txt.value);
      currentItems=arr.map(function(item,index){var opts=Array.isArray(item.jyutpings)?item.jyutpings:[],jp=opts[0]||"";return {index:index,char:item.original||"",jp:jp,tone:toneOf(jp)};});
      syllables.innerHTML=currentItems.map(function(item){
        var cls="sy"+(item.index===selectedIndex?" selected":"");
        return '<div class="'+cls+'" data-index="'+item.index+'"><b>'+esc(item.char)+'</b><small>'+esc(item.jp||"—")+'</small></div>';
      }).join("");
      Array.prototype.forEach.call(syllables.querySelectorAll(".sy"),function(el){
        el.addEventListener("click",function(){var i=Number(el.dataset.index);if(currentItems[i]&&currentItems[i].tone)selectTarget(i);});
      });
      if(selectedIndex<0||!currentItems[selectedIndex]||!currentItems[selectedIndex].tone){
        var first=currentItems.find(function(x){return x.tone;});if(first)selectTarget(first.index);
      }
      status.textContent="粵拼已在本地解析。點任意字可送到 Tone Lab。";
    }catch(e){status.textContent="粵拼錯誤："+e.message;}
  }
  function selectTarget(i){
    selectedIndex=i;var item=currentItems[i];if(!item||!item.tone)return;
    targetChar.textContent=item.char;targetJyutping.textContent=item.jp;targetName.textContent="Tone "+item.tone+" · "+toneNames[item.tone];
    playTarget.disabled=false;renderSyllableSelection();refreshEvalAvailability();
  }
  function renderSyllableSelection(){
    Array.prototype.forEach.call(syllables.querySelectorAll(".sy"),function(el){el.classList.toggle("selected",Number(el.dataset.index)===selectedIndex);});
  }

  analyseBtn.addEventListener("click",analyse);txt.addEventListener("change",function(){selectedIndex=-1;analyse();});
  speed.addEventListener("input",function(){speedValue.textContent=Number(speed.value).toFixed(2)+"×";});
  listen.addEventListener("click",async function(){listen.disabled=true;try{await playTts(txt.value.trim());}catch(e){status.textContent="VITS 發音錯誤："+e.message;}finally{listen.disabled=false;}});
  playTarget.addEventListener("click",async function(){var item=currentItems[selectedIndex];if(!item)return;playTarget.disabled=true;try{await playTts(item.char);}catch(e){status.textContent="VITS 發音錯誤："+e.message;}finally{playTarget.disabled=false;}});

  function loadCalibration(){try{return JSON.parse(localStorage.getItem("cantonese-tone-cal-v1")||"{}");}catch(e){return {};}}
  function saveCalibration(){try{localStorage.setItem("cantonese-tone-cal-v1",JSON.stringify(calibration));}catch(e){}}
  function calValid(){
    var h=calibration["1"],m=calibration["3"],l=calibration["6"];
    return Number.isFinite(h)&&Number.isFinite(m)&&Number.isFinite(l)&&h>m+.35&&m>l+.2;
  }
  function renderCalibration(){
    [1,3,6].forEach(function(t){
      var el=q("#cal"+t),v=calibration[String(t)];
      el.textContent=Number.isFinite(v)?("已校準 · "+v.toFixed(1)+" st"):"未校準";
    });
    var h=calibration["1"],m=calibration["3"],l=calibration["6"];
    if(calValid()){
      q("#calStatus").textContent="校準完成：高−中 "+(h-m).toFixed(1)+" st；中−低 "+(m-l).toFixed(1)+" st。Tone Lab 已啟用。";
    }else if([h,m,l].every(Number.isFinite)){
      q("#calStatus").textContent="三個值已錄到，但高 / 中 / 低分離太小或順序不對。請先聽標準音，再重錄有問題的項目。";
    }else{
      q("#calStatus").textContent="完成三個平調後，精細聲調評估才會啟用。";
    }
    refreshEvalAvailability();
  }
  q("#resetCal").addEventListener("click",function(){calibration={};saveCalibration();renderCalibration();results.classList.add("hidden");});
  Array.prototype.forEach.call(document.querySelectorAll(".calListen"),function(btn){
    btn.addEventListener("click",async function(){btn.disabled=true;try{await playTts(btn.dataset.text);}catch(e){status.textContent="VITS 發音錯誤："+e.message;}finally{btn.disabled=false;}});
  });
  Array.prototype.forEach.call(document.querySelectorAll(".calRecord"),function(btn){
    btn.addEventListener("click",function(){toggleCapture({type:"cal",tone:Number(btn.dataset.tone),button:btn});});
  });
  evalRecord.addEventListener("click",function(){toggleCapture({type:"eval",button:evalRecord});});
  record.addEventListener("click",function(){toggleCapture({type:"phrase",button:record});});

  function refreshEvalAvailability(){
    var item=currentItems[selectedIndex];
    evalRecord.disabled=!(calValid()&&item&&item.tone);
    evalStatus.textContent=calValid()?(item&&item.tone?"準備好：只念上面選中的一個字。":"請先選擇一個有粵拼的字。"):"請先完成三點音域校準。";
  }

  async function toggleCapture(ctx){
    if(activeCapture){
      if(activeCapture.button===ctx.button){activeCapture.recorder.stop();}
      return;
    }
    try{
      var stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:false,noiseSuppression:false,autoGainControl:false}});
      if(!window.MediaRecorder)throw new Error("此瀏覽器不支援 MediaRecorder");
      var mime="";
      if(MediaRecorder.isTypeSupported("audio/webm;codecs=opus"))mime="audio/webm;codecs=opus";
      else if(MediaRecorder.isTypeSupported("audio/mp4"))mime="audio/mp4";
      var mr=mime?new MediaRecorder(stream,{mimeType:mime}):new MediaRecorder(stream),parts=[];
      activeCapture={recorder:mr,stream:stream,parts:parts,ctx:ctx,button:ctx.button,oldText:ctx.button.textContent};
      mr.ondataavailable=function(e){if(e.data&&e.data.size)parts.push(e.data);};
      mr.onstop=async function(){
        var cap=activeCapture;activeCapture=null;stream.getTracks().forEach(function(t){t.stop();});
        cap.button.textContent=cap.oldText;
        var blob=new Blob(parts,{type:mr.mimeType||"audio/webm"});
        try{
          if(cap.ctx.type==="phrase"){showPhraseRecording(blob);}
          else if(cap.ctx.type==="cal"){await processCalibration(blob,cap.ctx.tone);}
          else if(cap.ctx.type==="eval"){await processEvaluation(blob);}
        }catch(e){(cap.ctx.type==="eval"?evalStatus:q("#calStatus")).textContent="分析失敗："+e.message;}
      };
      mr.start();ctx.button.textContent="■ 停止";
      if(ctx.type==="eval")evalStatus.textContent="正在錄音…只念選中的一個字，約 0.5–1.5 秒。";
      else if(ctx.type==="cal")q("#calStatus").textContent="正在錄音…保持自然，不要唱。";
      else status.textContent="正在錄音整句…";
    }catch(e){status.textContent="錄音錯誤："+e.message;}
  }

  function showPhraseRecording(blob){
    if(userUrl)URL.revokeObjectURL(userUrl);userUrl=URL.createObjectURL(blob);userAudio.src=userUrl;userAudio.classList.remove("hidden");status.textContent="整句錄音完成，可以回放比較。";
  }

  async function decodeBlob(blob){
    var buf=await blob.arrayBuffer(),AC=window.AudioContext||window.webkitAudioContext;
    if(!AC)throw new Error("瀏覽器不支援 AudioContext");
    var ac=new AC();
    try{return await ac.decodeAudioData(buf.slice(0));}finally{await ac.close();}
  }
  function downsample(input,inRate,outRate){
    if(inRate<=outRate)return input;
    var ratio=inRate/outRate,n=Math.floor(input.length/ratio),out=new Float32Array(n);
    for(var i=0;i<n;i++){var p=i*ratio,j=Math.floor(p),f=p-j,a=input[j]||0,b=input[Math.min(j+1,input.length-1)]||0;out[i]=a+(b-a)*f;}return out;
  }
  function extractPitch(audioBuffer){
    var sr=16000,x=downsample(audioBuffer.getChannelData(0),audioBuffer.sampleRate,sr),frame=640,hop=160,minLag=Math.floor(sr/500),maxLag=Math.ceil(sr/70),track=[];
    for(var start=0;start+frame<x.length;start+=hop){
      var rms=0,mean=0,i;for(i=0;i<frame;i++)mean+=x[start+i];mean/=frame;
      for(i=0;i<frame;i++){var z=x[start+i]-mean;rms+=z*z;}rms=Math.sqrt(rms/frame);if(rms<.008)continue;
      var bestLag=0,best=-1,corrs=[];
      for(var lag=minLag;lag<=maxLag;lag++){
        var num=0,a2=0,b2=0,limit=frame-lag;
        for(i=0;i<limit;i++){var a=x[start+i]-mean,b=x[start+i+lag]-mean;num+=a*b;a2+=a*a;b2+=b*b;}
        var c=num/(Math.sqrt(a2*b2)+1e-9);corrs[lag]=c;if(c>best){best=c;bestLag=lag;}
      }
      if(best<.55||!bestLag)continue;
      var lagF=bestLag,c0=corrs[bestLag-1],c1=corrs[bestLag],c2=corrs[bestLag+1];
      if(Number.isFinite(c0)&&Number.isFinite(c2)){var den=(c0-2*c1+c2);if(Math.abs(den)>1e-6)lagF=bestLag+.5*(c0-c2)/den;}
      var hz=sr/lagF;if(hz>=70&&hz<=500)track.push({time:start/sr,hz:hz,conf:best});
    }
    if(track.length<6)throw new Error("有效有聲片段太短；請靠近麥克風，完整念一個音節。");
    var semis=track.map(function(p){return st(p.hz);}),fixed=[];
    for(var k=0;k<semis.length;k++){
      var v=semis[k];
      if(fixed.length){var prev=fixed[fixed.length-1];while(v-prev>7)v-=12;while(prev-v>7)v+=12;if(Math.abs(v-prev)>7)continue;}
      fixed.push(v);
    }
    if(fixed.length<6)throw new Error("音高追蹤不穩定，請重新錄一次。");
    var smooth=fixed.map(function(v,i){var s=Math.max(0,i-1),e=Math.min(fixed.length,i+2);return median(fixed.slice(s,e));});
    return smooth;
  }
  function resample(values,n){
    if(values.length===1)return Array(n).fill(values[0]);var out=[];
    for(var i=0;i<n;i++){var p=i*(values.length-1)/(n-1),j=Math.floor(p),f=p-j;out.push(values[j]*(1-f)+values[Math.min(j+1,values.length-1)]*f);}return out;
  }
  function robustCenter(values){
    var a=values.slice(),cut=Math.max(1,Math.floor(a.length*.15));if(a.length>2*cut)a=a.slice(cut,a.length-cut);return median(a);
  }
  async function processCalibration(blob,tone){
    var audio=await decodeBlob(blob),track=extractPitch(audio),value=robustCenter(track);
    calibration[String(tone)]=value;saveCalibration();renderCalibration();
  }
  function semiToLevel(s){
    var h=calibration["1"],m=calibration["3"],l=calibration["6"];
    if(s>=m)return 3+2*(s-m)/(h-m);
    return 3-(m-s)/(m-l);
  }
  function template(tone,n){
    var out=[];for(var i=0;i<n;i++){var t=i/(n-1),v;
      if(tone===1)v=5;
      else if(tone===2)v=2+3*Math.pow(t,1.35);
      else if(tone===3)v=3;
      else if(tone===4)v=2-t;
      else if(tone===5)v=2+Math.pow(t,1.35);
      else v=2;
      out.push(v);
    }return out;
  }
  function rmse(a,b){var s=0;for(var i=0;i<a.length;i++){var d=a[i]-b[i];s+=d*d;}return Math.sqrt(s/a.length);}
  function avg(a,s,e){var x=a.slice(s,e);return x.reduce(function(p,c){return p+c;},0)/x.length;}
  function softmaxDistances(ds){
    var sigma=.72,raw=ds.map(function(d){return Math.exp(-.5*Math.pow(d/sigma,2));}),sum=raw.reduce(function(a,b){return a+b;},0);
    return raw.map(function(x){return x/sum;});
  }
  async function processEvaluation(blob){
    if(!calValid())throw new Error("音域校準尚未完成");
    var item=currentItems[selectedIndex];if(!item||!item.tone)throw new Error("沒有選擇目標字");
    evalStatus.textContent="正在分析 F0 曲線…";
    var audio=await decodeBlob(blob),semis=extractPitch(audio),semi20=resample(semis,20),levels=semi20.map(semiToLevel).map(function(v){return Math.max(.4,Math.min(5.6,v));});
    var templates=[1,2,3,4,5,6].map(function(t){return template(t,20);});
    var ds=templates.map(function(t){return rmse(levels,t);}),probs=softmaxDistances(ds),best=ds.indexOf(Math.min.apply(null,ds))+1,target=item.tone,targetCurve=templates[target-1],dist=ds[target-1];
    var quality=Math.round(100*Math.exp(-.5*Math.pow(dist/.80,2)));
    var us=avg(levels,0,4),ue=avg(levels,16,20),ts=avg(targetCurve,0,4),te=avg(targetCurve,16,20),uSlope=ue-us,tSlope=te-ts;
    showEvaluation({item:item,levels:levels,targetCurve:targetCurve,distances:ds,probs:probs,best:best,quality:quality,startError:us-ts,endError:ue-te,slopeError:uSlope-tSlope,userSlope:uSlope,targetSlope:tSlope});
    evalStatus.textContent="分析完成。這個分數是連續 F0 品質分，不只是六選一分類。";
  }
  function signed(v){return (v>0?"+":"")+v.toFixed(2);}
  function showEvaluation(r){
    results.classList.remove("hidden");q("#qualityScore").textContent=r.quality;q("#predTone").textContent="T"+r.best;q("#goalTone").textContent="T"+r.item.tone;
    q("#startErr").textContent=signed(r.startError);q("#endErr").textContent=signed(r.endError);q("#slopeErr").textContent=signed(r.slopeError);
    q("#probabilities").innerHTML=r.probs.map(function(p,i){return '<span class="prob '+(i+1===r.best?"top":"")+'">T'+(i+1)+' '+Math.round(p*100)+'%</span>';}).join("");
    var fb=[];
    if(r.best===r.item.tone)fb.push('<div class="good">✓ 聲調類別仍是 Tone '+r.item.tone+'，接下來看細節。</div>');
    else fb.push('<div class="bad">更接近 Tone '+r.best+'，目標是 Tone '+r.item.tone+'。</div>');
    if(Math.abs(r.startError)>.45)fb.push('<div>起點'+(r.startError>0?"偏高":"偏低")+'約 '+Math.abs(r.startError).toFixed(1)+' 個調值。</div>');
    if(Math.abs(r.endError)>.45)fb.push('<div>終點'+(r.endError>0?"偏高":"偏低")+'約 '+Math.abs(r.endError).toFixed(1)+' 個調值。</div>');
    if(Math.abs(r.slopeError)>.45){
      if(Math.abs(r.targetSlope)<.35)fb.push('<div>目標應較平，但你的音高有較明顯的'+(r.userSlope>0?"上升":"下降")+'。</div>');
      else fb.push('<div>'+((Math.abs(r.userSlope)<Math.abs(r.targetSlope))?"升降幅度不足":"升降幅度過大")+'。</div>');
    }
    if(r.item.tone===2&&r.endError<-.45)fb.push('<div>這是 T2 常見的小錯：終點不夠高，會向 T5 靠近。</div>');
    if(r.item.tone===5&&r.endError>.55)fb.push('<div>T5 上升得偏高，會向 T2 靠近。</div>');
    if(r.item.tone===4&&r.slopeError>.4)fb.push('<div>T4 下降不足，容易向 T6 靠近。</div>');
    if(r.item.tone===6&&r.userSlope<-.45)fb.push('<div>T6 末尾下降過多，容易向 T4 靠近。</div>');
    if(fb.length===1&&r.best===r.item.tone)fb.push('<div class="good">起點、終點和輪廓都接近目標。</div>');
    q("#feedback").innerHTML=fb.join("");
    drawChart(r.levels,r.targetCurve);
  }
  function drawChart(user,target){
    var c=q("#pitchChart"),ctx=c.getContext("2d"),w=c.width,h=c.height,pad=38;ctx.clearRect(0,0,w,h);ctx.font="12px sans-serif";
    ctx.strokeStyle="#dedbd1";ctx.fillStyle="#73766f";ctx.lineWidth=1;
    for(var level=1;level<=5;level++){var y=pad+(5-level)*(h-2*pad)/4;ctx.beginPath();ctx.moveTo(pad,y);ctx.lineTo(w-pad,y);ctx.stroke();ctx.fillText(String(level),12,y+4);}
    function line(arr,color,width){ctx.strokeStyle=color;ctx.lineWidth=width;ctx.beginPath();arr.forEach(function(v,i){var x=pad+i*(w-2*pad)/(arr.length-1),y=pad+(5-Math.max(1,Math.min(5,v)))*(h-2*pad)/4;if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);});ctx.stroke();}
    line(target,"#a34237",4);line(user,"#1c5d4f",4);
    ctx.fillStyle="#a34237";ctx.fillText("目標",w-100,20);ctx.fillStyle="#1c5d4f";ctx.fillText("你的 F0",w-50,20);
  }

  async function health(){
    try{var r=await fetch("/api/health",{cache:"no-store"}),d=await r.json();if(!r.ok||!d.ok)throw new Error("模型未就緒");status.textContent="VITS 已就緒 · "+Math.round(d.model_bytes/1024/1024)+" MB · "+d.sample_rate+" Hz";analyse();renderCalibration();}
    catch(e){status.textContent="模型狀態錯誤："+e.message;}
  }
  health();
})();