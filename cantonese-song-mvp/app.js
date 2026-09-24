"use strict";(function(){
const SONG=[
 {text:"流水像清得沒帶半顆沙",jp:["lau4","seoi2","zoeng6","cing1","dak1","mut6","daai3","bun3","fo2","saa1"]},
 {text:"前身被擱在上游風化",jp:["cin4","san1","bei6","gok3","zoi6","soeng6","jau4","fung1","faa3"]}
];
const lessons=document.querySelector("#lessons"),health=document.querySelector("#health");
let active=null,urls={};
function esc(s){return String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));}
function render(){
 lessons.innerHTML=SONG.map((l,i)=>'<section class="card" id="line'+i+'"><div class="lineNo">第 '+(i+1)+' 句</div><div class="lyric">'+esc(l.text)+'</div><div class="syls">'+[...l.text].map((ch,j)=>'<div class="syl"><b>'+esc(ch)+'</b><code>'+l.jp[j]+'</code></div>').join("")+'</div><div class="buttons"><button class="listen" data-i="'+i+'">▶ 標準粵語</button><button class="record" data-i="'+i+'">● 跟讀並評分</button></div><audio class="reference hidden" controls></audio><audio class="mine hidden" controls></audio><div class="status">準備好後先聽，再跟讀整句。</div><div class="result hidden"></div></section>').join("");
 document.querySelectorAll(".listen").forEach(b=>b.onclick=()=>listen(+b.dataset.i,b));
 document.querySelectorAll(".record").forEach(b=>b.onclick=()=>toggleRecord(+b.dataset.i,b));
}
function card(i){return document.querySelector("#line"+i);}
async function listen(i,btn){
 const c=card(i),st=c.querySelector(".status"),a=c.querySelector(".reference");btn.disabled=true;st.textContent="正在生成標準粵語…";
 try{const r=await fetch("/api/tts",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({text:SONG[i].text,speed:.88})});if(!r.ok)throw new Error("TTS HTTP "+r.status);const blob=await r.blob();if(urls["ref"+i])URL.revokeObjectURL(urls["ref"+i]);urls["ref"+i]=URL.createObjectURL(blob);a.src=urls["ref"+i];a.classList.remove("hidden");await a.play();st.textContent="正在播放標準粵語。";}
 catch(e){st.textContent="標準音錯誤："+e.message;}finally{btn.disabled=false;}
}
async function toggleRecord(i,btn){
 if(active){if(active.i===i){active.mr.stop();}return;}
 const c=card(i),st=c.querySelector(".status");
 try{
  const stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:false,noiseSuppression:false,autoGainControl:false}});
  const mime=MediaRecorder.isTypeSupported("audio/webm;codecs=opus")?"audio/webm;codecs=opus":(MediaRecorder.isTypeSupported("audio/mp4")?"audio/mp4":"");
  const mr=mime?new MediaRecorder(stream,{mimeType:mime}):new MediaRecorder(stream),parts=[];
  active={i,mr,stream,btn,old:btn.textContent};mr.ondataavailable=e=>{if(e.data?.size)parts.push(e.data);};
  mr.onstop=async()=>{const cap=active;active=null;cap.stream.getTracks().forEach(t=>t.stop());cap.btn.textContent=cap.old;const blob=new Blob(parts,{type:mr.mimeType||"audio/webm"});await evaluate(i,blob);};
  mr.start();btn.textContent="■ 停止並評分";st.textContent="正在錄音…完整念這一句，念完後按停止。";
 }catch(e){st.textContent="錄音錯誤："+e.message;}
}
async function wav16k(blob){
 const buf=await blob.arrayBuffer(),AC=window.AudioContext||window.webkitAudioContext,ac=new AC();
 try{
  const audio=await ac.decodeAudioData(buf.slice(0)),len=Math.ceil(audio.duration*16000),out=new Float32Array(len);
  const chans=[];for(let c=0;c<audio.numberOfChannels;c++)chans.push(audio.getChannelData(c));
  for(let i=0;i<len;i++){const p=i*audio.sampleRate/16000,j=Math.floor(p),f=p-j;let v=0;for(const ch of chans){const x=ch[j]||0,y=ch[Math.min(j+1,ch.length-1)]||0;v+=x+(y-x)*f;}out[i]=v/chans.length;}
  const ab=new ArrayBuffer(44+out.length*2),v=new DataView(ab);const w=(o,s)=>{for(let k=0;k<s.length;k++)v.setUint8(o+k,s.charCodeAt(k));};w(0,"RIFF");v.setUint32(4,36+out.length*2,true);w(8,"WAVE");w(12,"fmt ");v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,16000,true);v.setUint32(28,32000,true);v.setUint16(32,2,true);v.setUint16(34,16,true);w(36,"data");v.setUint32(40,out.length*2,true);for(let i=0,o=44;i<out.length;i++,o+=2){const x=Math.max(-1,Math.min(1,out[i]));v.setInt16(o,x<0?x*32768:x*32767,true);}return ab;
 }finally{await ac.close();}
}
async function evaluate(i,blob){
 const c=card(i),st=c.querySelector(".status"),mine=c.querySelector(".mine"),result=c.querySelector(".result");st.textContent="正在做逐字粵語 ASR + 聲調分析…";
 if(urls["mine"+i])URL.revokeObjectURL(urls["mine"+i]);urls["mine"+i]=URL.createObjectURL(blob);mine.src=urls["mine"+i];mine.classList.remove("hidden");
 try{
  const wav=await wav16k(blob),r=await fetch("/api/evaluate?line="+i,{method:"POST",headers:{"content-type":"audio/wav"},body:wav}),d=await r.json();if(!r.ok)throw new Error(d.error||("HTTP "+r.status));showResult(i,d);st.textContent="評估完成。評估完成。每個音節都可點開看聲母、韻母、聲調、清晰度與時長。";
 }catch(e){st.textContent="評估錯誤："+e.message;}
}
function showResult(i,d){
 const c=card(i),box=c.querySelector(".result");box.classList.remove("hidden");
 const attention=d.attention_count??d.items.filter(x=>["wrong","tone","segmental"].includes(x.status)).length;
 box.innerHTML='<div class="summary"><div class="metric"><span>整句分數</span><strong>'+d.overall_score+'</strong></div><div class="metric"><span>音節匹配</span><strong>'+d.syllable_accuracy+'%</strong></div><div class="metric"><span>明確需注意</span><strong>'+attention+' 音節</strong></div></div><div class="status">ASR 聽到：'+esc(d.recognized||"（沒有辨識結果）")+'<br>粵拼候選：'+esc(d.recognized_jyutping||"—")+'<br><small>同音異字不算錯；藍色位置代表 ASR 對標準音本身也會混淆，不會武斷判錯。</small></div><div class="legend"><span><i class="dot g"></i>字 + 聲調穩定</span><span><i class="dot a"></i>字正確，但聲調需調整</span><span><i class="dot r"></i>高置信度讀錯 / 漏讀</span><span><i class="dot b"></i>ASR 基線不確定</span></div><div class="chars">'+d.items.map(x=>'<div class="charResult '+x.status+'" data-index="'+x.index+'"><b>'+esc(x.char)+'</b><code>'+esc(x.jyutping)+'</code><small>總 '+x.score+'</small><div class="miniParts"><span>聲 '+fmt(x.initial_score)+'</span><span>韻 '+fmt(x.final_score)+'</span><span>調 '+fmt(x.tone?.tone_score)+'</span></div></div>').join("")+'</div><div class="detail">點一個字查看細節。</div>';
 box.querySelectorAll(".charResult").forEach(el=>el.onclick=()=>detail(box,d.items[+el.dataset.index]));
}
function fmt(v){return v==null?"—":Math.round(v);}
function detail(box,x){
 let s='<b>'+esc(x.char)+' · '+esc(x.jyutping)+' · 總分 '+x.score+'</b><br>';
 s+='<b>聲母：</b>'+esc(x.target_initial||"—")+' → '+(x.initial_score==null?'基線盲區':fmt(x.initial_score)+' 分');
 if(x.heard_initial&&x.heard_initial!==x.target_initial)s+='（ASR 聽到 '+esc(x.heard_initial)+'）';
 s+='<br><b>韻母：</b>'+esc(x.target_final||"—")+' → '+(x.final_score==null?'基線盲區':fmt(x.final_score)+' 分');
 if(x.heard_final&&x.heard_final!==x.target_final)s+='（ASR 聽到 '+esc(x.heard_final)+'）';
 s+='<br><b>聲調：</b>T'+x.expected_tone+' → '+(x.tone?fmt(x.tone.tone_score)+' 分':'沒有足夠 F0');
 if(x.tone){s+='；F0 最接近 T'+x.tone.predicted_tone;if(x.tone.notes?.length)s+='；'+x.tone.notes.map(esc).join("、");}
 s+='<br><b>音節聲學置信：</b>'+(x.acoustic_score==null?'—':fmt(x.acoustic_score)+' 分');
 s+='<br><b>時長：</b>'+(x.duration_score==null?'—':fmt(x.duration_score)+' 分');
 if(x.baseline_confusion)s+='<br><b>藍色基線盲區：</b>標準音本身在此位置也會被 ASR 系統性誤聽，因此不武斷判聲母/韻母錯。';
 else if(!x.accepted_match)s+='<br><b>ASR 候選：</b>'+esc(x.heard||"∅")+' '+esc(x.heard_jyutping||"");
 if(x.issues?.length)s+='<br><b>主要問題：</b>'+x.issues.map(esc).join("、");
 if(x.certainty==="low")s+='<br><b>判定置信度：低，建議重錄一次。</b>';
 box.querySelector(".detail").innerHTML=s;
}
async function boot(){render();try{const r=await fetch("/api/health",{cache:"no-store"}),d=await r.json();if(!d.ok)throw new Error("not ready");health.textContent="✓ VITS + 粵語 Zipformer ASR 已就緒。錄音只送到這個 Demo 伺服器即時計算，不保存。";}catch(e){health.textContent="模型尚未就緒："+e.message;}}
boot();
})();