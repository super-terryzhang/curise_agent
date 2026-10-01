"use strict";(function(){
let SONG=[];
const lessons=document.querySelector("#lessons"),health=document.querySelector("#health");
const lyricsInput=document.querySelector("#lyricsInput"),buildBtn=document.querySelector("#buildSong"),buildStatus=document.querySelector("#buildStatus");
let active=null,urls={},activeSyl=null;

function esc(s){return String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));}
function card(i){return document.querySelector("#line"+i);}
function cleanupUrls(){Object.values(urls).forEach(u=>{try{URL.revokeObjectURL(u)}catch(e){}});urls={};}

function render(){
 if(!SONG.length){lessons.innerHTML='<div class="empty">還沒有歌詞。把歌詞貼到上面，每個換行會變成一句練習。</div>';return;}
 lessons.innerHTML=SONG.map((l,i)=>{
   const skipped=(l.unsupported||[]).length?'<div class="lineWarn">暫不評估：'+esc((l.unsupported||[]).join(" "))+'</div>':'';
   const units=l.chars.map((ch,j)=>'<button type="button" class="syl" data-line="'+i+'" data-char="'+j+'" aria-label="播放 '+esc(ch)+' '+esc(l.jp[j])+'"><b>'+esc(ch)+'</b><code>'+esc(l.jp[j])+'</code><span class="tapHint">點擊聽</span></button>').join("");
   return '<section class="card lesson" id="line'+i+'"><div class="lineNo">第 '+(i+1)+' 句</div><div class="lyric">'+esc(l.text)+'</div><div class="syls">'+units+'</div>'+skipped+'<div class="speedRow"><label>朗讀速度</label><select class="speechSpeed"><option value="0.60" selected>教學慢速 · 0.60×</option><option value="0.75">清晰 · 0.75×</option><option value="0.88">自然 · 0.88×</option></select></div><div class="buttons"><button class="listen" data-i="'+i+'">▶ 標準粵語</button><button class="record" data-i="'+i+'">● 跟讀並評分</button></div><audio class="reference hidden" controls></audio><audio class="mine hidden" controls></audio><audio class="charAudio hidden" preload="none"></audio><div class="status">先點字聽單字，或播放整句；第一次使用新句子時可能需要先生成語音。</div><div class="result hidden"></div></section>';
 }).join("");
 document.querySelectorAll(".listen").forEach(b=>b.onclick=()=>listen(+b.dataset.i,b));
 document.querySelectorAll(".record").forEach(b=>b.onclick=()=>toggleRecord(+b.dataset.i,b));
 document.querySelectorAll(".syl").forEach(b=>b.onclick=()=>playCharacter(+b.dataset.line,+b.dataset.char,b));
}

async function buildLessons(){
 if(active){buildStatus.textContent="請先停止目前的錄音，再重新建立歌詞。";return;}
 const lyrics=lyricsInput.value.trim();
 if(!lyrics){buildStatus.textContent="請先貼上歌詞。";return;}
 buildBtn.disabled=true;buildStatus.textContent="正在拆分歌詞並生成粵拼…";
 try{
   const r=await fetch("/api/parse-lyrics",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({lyrics})});
   const d=await r.json();if(!r.ok)throw new Error(d.error||("HTTP "+r.status));
   cleanupUrls();
   SONG=(d.lines||[]).map(x=>({text:x.text,chars:x.chars||[],jp:x.jyutping||[],unsupported:x.unsupported||[]}));
   render();
   const warnings=(d.warnings||[]).length;
   buildStatus.textContent="已建立 "+SONG.length+" 句歌詞"+(warnings?"；有 "+warnings+" 行/字元需要略過或檢查。":"。")+" 每句會在第一次播放/評估時建立快取。";
 }catch(e){buildStatus.textContent="建立失敗："+e.message;}
 finally{buildBtn.disabled=false;}
}

async function playCharacter(lineIndex,charIndex,el){
 const line=SONG[lineIndex];if(!line)return;
 const c=card(lineIndex),st=c.querySelector(".status"),audio=c.querySelector(".charAudio");
 if(activeSyl)activeSyl.classList.remove("playing");
 activeSyl=el;el.classList.add("playing");audio.pause();
 audio.src="/api/char?text="+encodeURIComponent(line.text)+"&index="+charIndex+"&v=dynamic1";
 audio.currentTime=0;
 const ch=line.chars[charIndex],jp=line.jp[charIndex];
 st.textContent="準備單字："+ch+" · "+jp+(audio.readyState?"":"");
 audio.onplaying=()=>{st.textContent="播放單字："+ch+" · "+jp;};
 audio.onended=()=>{el.classList.remove("playing");if(activeSyl===el)activeSyl=null;};
 audio.onerror=()=>{el.classList.remove("playing");st.textContent="單字音訊載入失敗；新句子第一次可能需要較久，請再點一次。";};
 try{await audio.play();}catch(e){st.textContent="正在準備「"+ch+"」；若沒有自動播放，請再點一次。";}
}

async function listen(i,btn){
 const line=SONG[i],c=card(i),st=c.querySelector(".status"),a=c.querySelector(".reference"),speed=Number(c.querySelector(".speechSpeed").value||.60);
 if(!line)return;btn.disabled=true;st.textContent="正在準備這一句的標準粵語…";
 try{
  const r=await fetch("/api/tts",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({text:line.text,speed})});
  if(!r.ok){const e=await r.json().catch(()=>({}));throw new Error(e.error||("HTTP "+r.status));}
  const blob=await r.blob(),key="ref"+i+"-"+speed;
  if(urls[key])URL.revokeObjectURL(urls[key]);urls[key]=URL.createObjectURL(blob);
  a.src=urls[key];a.classList.remove("hidden");await a.play();
  const label=speed<=.61?"教學慢速":speed<=.76?"清晰速度":"自然速度";
  st.textContent="正在播放"+label+"（"+speed.toFixed(2)+"×）。";
 }catch(e){st.textContent="標準音錯誤："+e.message;}finally{btn.disabled=false;}
}

async function toggleRecord(i,btn){
 if(active){if(active.i===i)active.mr.stop();return;}
 const line=SONG[i],c=card(i),st=c.querySelector(".status");if(!line)return;
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
 const line=SONG[i],c=card(i),st=c.querySelector(".status"),mine=c.querySelector(".mine"),result=c.querySelector(".result");
 if(!line)return;st.textContent="正在建立/載入這一句的評分基準，然後做逐音節分析…";
 const key="mine"+i;if(urls[key])URL.revokeObjectURL(urls[key]);urls[key]=URL.createObjectURL(blob);mine.src=urls[key];mine.classList.remove("hidden");
 try{
  const wav=await wav16k(blob);
  const r=await fetch("/api/evaluate?text="+encodeURIComponent(line.text),{method:"POST",headers:{"content-type":"audio/wav"},body:wav});
  const d=await r.json();if(!r.ok)throw new Error(d.error||("HTTP "+r.status));
  showResult(i,d);st.textContent="評估完成。只有有足夠證據的音節才會計分；系統不確定的位置不扣分。";
 }catch(e){st.textContent="評估錯誤："+e.message;}
}

function showResult(i,d){
 const c=card(i),box=c.querySelector(".result");box.classList.remove("hidden");
 const attention=d.attention_count??d.items.filter(x=>["wrong","tone","segmental"].includes(x.status)).length;
 const overall=d.overall_score==null?"—":d.overall_score;
 box.innerHTML='<div class="summary"><div class="metric"><span>整句分數</span><strong>'+overall+'</strong></div><div class="metric"><span>有效判定</span><strong>'+d.coverage+'%</strong></div><div class="metric"><span>需要練習</span><strong>'+attention+' 音節</strong></div></div><div class="status">ASR 聽到：'+esc(d.recognized||"（沒有辨識結果）")+'<br>粵拼候選：'+esc(d.recognized_jyutping||"—")+'<br><small>同音異字不算錯；ASR 不可靠的位置會再用標準音聲學相似度判斷。若仍無法判定，該音節不計分。</small></div><div class="legend"><span><i class="dot g"></i>字 + 聲調穩定</span><span><i class="dot a"></i>字正確，但聲調需調整</span><span><i class="dot r"></i>聲母 / 韻母需練習</span><span><i class="dot u"></i>系統未能判定 · 不計分</span></div><div class="chars">'+d.items.map(x=>'<div class="charResult '+x.status+'" data-index="'+x.index+'"><b>'+esc(x.char)+'</b><code>'+esc(x.jyutping)+'</code><small>'+(x.score==null?'未判定':'總 '+x.score)+'</small><div class="miniParts"><span>聲 '+fmt(x.initial_score)+'</span><span>韻 '+fmt(x.final_score)+'</span><span>調 '+fmt(x.tone?.tone_score)+'</span></div></div>').join("")+'</div><div class="detail">點一個字查看細節。</div>';
 box.querySelectorAll(".charResult").forEach(el=>el.onclick=()=>detail(box,d.items[+el.dataset.index]));
}
function fmt(v){return v==null?"—":Math.round(v);}
function detail(box,x){
 let s='<b>'+esc(x.char)+' · '+esc(x.jyutping)+'</b><br>';
 if(x.status==="unrated"){
   s+='<b>本次：系統未能可靠判定，因此這個音節不計入總分。</b><br>這不代表你讀錯；可以再錄一次。';
   if(x.reference_acoustic_score!=null)s+='<br>標準音聲學相似度：'+fmt(x.reference_acoustic_score);
   box.querySelector(".detail").innerHTML=s;return;
 }
 s+='總分：'+fmt(x.score);
 s+='<br><b>聲母：</b>'+esc(x.target_initial||"—")+' → '+fmt(x.initial_score)+' 分';
 if(x.heard_initial&&x.heard_initial!==x.target_initial)s+='（ASR 候選 '+esc(x.heard_initial)+'）';
 s+='<br><b>韻母：</b>'+esc(x.target_final||"—")+' → '+fmt(x.final_score)+' 分';
 if(x.heard_final&&x.heard_final!==x.target_final)s+='（ASR 候選 '+esc(x.heard_final)+'）';
 s+='<br><b>聲調：</b>T'+x.expected_tone+' → '+(x.tone?fmt(x.tone.tone_score)+' 分':'沒有足夠 F0');
 if(x.tone){s+='；F0 最接近 T'+x.tone.predicted_tone;if(x.tone.notes?.length)s+='；'+x.tone.notes.map(esc).join("、");}
 if(x.reference_acoustic_score!=null)s+='<br><b>標準音聲學相似：</b>'+fmt(x.reference_acoustic_score)+' 分';
 if(x.duration_score!=null)s+='<br><b>時長：</b>'+fmt(x.duration_score)+' 分';
 if(x.issues?.length)s+='<br><b>主要問題：</b>'+x.issues.map(esc).join("、");
 box.querySelector(".detail").innerHTML=s;
}

buildBtn.addEventListener("click",buildLessons);
lyricsInput.addEventListener("keydown",e=>{if((e.metaKey||e.ctrlKey)&&e.key==="Enter")buildLessons();});

async function boot(){
 try{
  const r=await fetch("/api/health",{cache:"no-store"}),d=await r.json();if(!d.ok)throw new Error("not ready");
  health.textContent="✓ 粵語 TTS + 逐音節評估引擎已就緒。歌詞只在這個 Demo 中即時計算；錄音不保存。";
 }catch(e){health.textContent="模型尚未就緒："+e.message;}
 await buildLessons();
}
boot();
})();