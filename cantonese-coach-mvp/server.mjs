import http from 'http';

const PORT = process.env.PORT || 3000;
const CAI = 'https://cantonese.ai/api';

const PAGE = `<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>粵語發音教練</title>
<style>
:root{--bg:#f3f1ea;--card:#fffdfa;--ink:#171916;--muted:#73766f;--line:#dedbd1;--green:#1c5d4f;--red:#a34237}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,-apple-system,BlinkMacSystemFont,"PingFang HK","Noto Sans TC",sans-serif}.wrap{max-width:820px;margin:auto;padding:48px 20px 90px}h1{font-size:clamp(42px,8vw,72px);letter-spacing:-.055em;line-height:.95;margin:8px 0 14px}p{color:var(--muted);line-height:1.6}.ey{font-size:11px;letter-spacing:.16em;color:var(--muted);font-weight:800}.card{background:var(--card);border:1px solid var(--line);border-radius:22px;padding:22px;margin:16px 0;box-shadow:0 14px 40px rgba(20,20,15,.045)}label{font-size:13px;font-weight:800;display:block;margin:12px 0 7px}input{width:100%;font:inherit;padding:13px 14px;border:1px solid var(--line);border-radius:12px;background:#fff;font-size:18px}button{font:inherit;font-weight:800;padding:12px 15px;border:0;border-radius:12px;background:var(--ink);color:#fff;cursor:pointer}button:disabled{opacity:.35;cursor:not-allowed}.row{display:grid;grid-template-columns:1fr auto;gap:9px}.buttons{display:flex;gap:9px;flex-wrap:wrap;margin-top:16px}.green{background:var(--green)}.red{background:var(--red)}.syllables{display:flex;gap:7px;flex-wrap:wrap;margin:16px 0}.sy{border:1px solid var(--line);background:#fff;border-radius:11px;padding:8px 10px;text-align:center;min-width:57px}.sy b{display:block;font-size:20px}.sy small{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--green)}.status{font-size:12px;color:var(--muted);margin-top:11px}.hidden{display:none}.score{font-size:76px;font-weight:800;letter-spacing:-.06em}.head{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line);padding-bottom:18px}.badge{padding:8px 11px;border-radius:999px;background:#e8f0ec;color:var(--green);font-weight:800;font-size:12px}.badge.bad{background:#f7e8e5;color:var(--red)}.pair{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin:18px 0}.pair div{background:#f5f3ed;padding:13px;border-radius:12px}.pair span{display:block;font-size:11px;color:var(--muted);margin-bottom:6px}.pair strong{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}.diag{display:grid;grid-template-columns:1fr 1fr 2fr;gap:8px;padding:9px 0;border-bottom:1px solid var(--line);align-items:center}.diag code{font-weight:800}.ok{color:var(--green)}.bad{color:var(--red)}.tone{display:flex;justify-content:space-between;background:#f5f3ed;padding:13px 15px;border-radius:12px;margin-top:16px}.tone strong{font-size:26px}.note{font-size:12px;color:var(--muted)}@media(max-width:600px){.row,.pair{grid-template-columns:1fr}.diag{grid-template-columns:1fr 1fr}.diag span{grid-column:1/-1}.buttons button{flex:1}}
</style></head>
<body><main class="wrap">
<div class="ey">CANTONESE PRONUNCIATION COACH</div><h1>粵語發音教練</h1>
<p>先聽標準粵語，再正常說一次。系統比較目標粵拼與它實際聽到的粵拼，特別檢查 1–6 聲調。</p>

<section class="card">
<label>Cantonese.ai API Key</label><input id="key" type="password" placeholder="只保存在此分頁，不寫入伺服器">
<label>練習文字</label><div class="row"><input id="text" value="你好嗎" maxlength="120"><button id="analyse">拆成粵拼</button></div>
<div id="syllables" class="syllables"></div>
<div class="buttons"><button id="listen" class="green" disabled>▶ 聽標準音</button><button id="record" class="red" disabled>● 開始跟讀</button></div>
<div id="status" class="status">正在載入粵拼…</div>
</section>

<section id="result" class="card hidden">
<div class="head"><div><div class="ey">PRONUNCIATION SCORE</div><div id="score" class="score">—</div></div><div id="pass" class="badge">—</div></div>
<div class="pair"><div><span>目標粵拼</span><strong id="expected">—</strong></div><div><span>系統聽到</span><strong id="heard">—</strong></div></div>
<label>逐音節診斷</label><div id="diags"></div>
<div class="tone"><span>聲調命中率</span><strong id="tone">—</strong></div>
</section>

<section class="card"><b>這個版本怎麼判斷？</b>
<p class="note">標準音：Cantonese.ai v6 TTS（用粵拼約束發音）。評分：Cantonese.ai Pronunciation Score。聲調診斷：把 expectedJyutping 與 transcribedJyutping 的每個音節拆成「音節主體 + tone 1–6」後比較。錄音只在記憶體中轉發，不落盤；API key 只存在 browser sessionStorage。</p></section>
</main><audio id="audio"></audio>

<script>
var $=function(s){return document.querySelector(s)}, jy='', list=[], rec=null;
var key=$('#key'), txt=$('#text'), status=$('#status'), btnA=$('#analyse'), btnL=$('#listen'), btnR=$('#record');
try{key.value=sessionStorage.getItem('cai-key')||''}catch(e){} key.oninput=function(){try{sessionStorage.setItem('cai-key',key.value)}catch(e){}};
function esc(s){return String(s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
async function api(url,body){
 var c=new AbortController(),timer=setTimeout(function(){c.abort()},10000),r;
 try{r=await fetch(url,{method:'POST',headers:{'content-type':'application/json','x-cantonese-key':key.value.trim()},body:JSON.stringify(body),signal:c.signal})}
 catch(e){if(e&&e.name==='AbortError')throw Error('請求超時（10 秒）');throw e}finally{clearTimeout(timer)}
 var d=await r.json().catch(function(){return {error:'Invalid response'}}); if(!r.ok)throw Error(d.error||('HTTP '+r.status)); return d;
}
async function analyse(){
 btnA.disabled=true; status.textContent='分析中…';
 try{var d=await api('/api/jyutping',{text:txt.value});jy=d.jyutping;list=d.list||[];$('#syllables').innerHTML=list.map(function(x){return '<div class="sy"><b>'+esc(x.character||'')+'</b><small>'+esc(x.jyutping||'—')+'</small></div>'}).join('');btnL.disabled=false;btnR.disabled=false;status.textContent='標準粵拼：'+jy}
 catch(e){status.textContent='錯誤：'+e.message}finally{btnA.disabled=false}
}
btnA.onclick=analyse; txt.onchange=analyse;
btnL.onclick=async function(){
 if(!key.value.trim()){status.textContent='先填 Cantonese.ai API key。';return}
 btnL.disabled=true;status.textContent='正在生成標準音…';
 try{var r=await fetch('/api/tts',{method:'POST',headers:{'content-type':'application/json','x-cantonese-key':key.value.trim()},body:JSON.stringify({text:txt.value,jyutping:jy})});if(!r.ok){var d=await r.json();throw Error(d.error||'TTS failed')}var b=await r.blob(),u=URL.createObjectURL(b),a=$('#audio');a.src=u;await a.play();a.onended=function(){URL.revokeObjectURL(u)};status.textContent='播放完成。現在跟讀一次。'}catch(e){status.textContent='TTS 錯誤：'+e.message}finally{btnL.disabled=false}
};
btnR.onclick=async function(){if(rec){stopRec();return}if(!key.value.trim()){status.textContent='先填 Cantonese.ai API key。';return}try{await startRec()}catch(e){status.textContent='麥克風錯誤：'+e.message}};
async function startRec(){
 var stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:false}});
 var ctx=new AudioContext(),src=ctx.createMediaStreamSource(stream),proc=ctx.createScriptProcessor(4096,1,1),chunks=[];
 proc.onaudioprocess=function(e){chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)))};src.connect(proc);proc.connect(ctx.destination);
 rec={stream:stream,ctx:ctx,src:src,proc:proc,chunks:chunks};btnR.textContent='■ 停止並評分';status.textContent='正在錄音…請正常說，不要唱。';
}
async function stopRec(){
 var r=rec;rec=null;r.proc.disconnect();r.src.disconnect();r.stream.getTracks().forEach(function(t){t.stop()});var rate=r.ctx.sampleRate;await r.ctx.close();
 var n=r.chunks.reduce(function(a,c){return a+c.length},0),samples=new Float32Array(n),o=0;r.chunks.forEach(function(c){samples.set(c,o);o+=c.length});
 var wav=encodeWav(samples,rate),blob=new Blob([wav],{type:'audio/wav'});btnR.textContent='● 開始跟讀';status.textContent='正在評分…';
 try{var b64=await toB64(blob),d=await api('/api/score',{text:txt.value,audioBase64:b64});render(d);status.textContent='評分完成。可以再試一次。'}catch(e){status.textContent='評分錯誤：'+e.message}
}
function encodeWav(s,rate){var b=new ArrayBuffer(44+s.length*2),v=new DataView(b),w=function(o,x){for(var i=0;i<x.length;i++)v.setUint8(o+i,x.charCodeAt(i))};w(0,'RIFF');v.setUint32(4,36+s.length*2,true);w(8,'WAVE');w(12,'fmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,rate,true);v.setUint32(28,rate*2,true);v.setUint16(32,2,true);v.setUint16(34,16,true);w(36,'data');v.setUint32(40,s.length*2,true);for(var i=0,o=44;i<s.length;i++,o+=2){var x=Math.max(-1,Math.min(1,s[i]));v.setInt16(o,x<0?x*32768:x*32767,true)}return b}
async function toB64(blob){var a=await blob.arrayBuffer(),u=new Uint8Array(a),str='',step=32768;for(var i=0;i<u.length;i+=step)str+=String.fromCharCode.apply(null,u.subarray(i,i+step));return btoa(str)}
function parts(x){var m=String(x||'').match(/^(.*?)([1-6])$/);return {raw:x||'∅',base:m?m[1]:x,tone:m?m[2]:null}}
function render(d){
 $('#result').classList.remove('hidden');$('#score').textContent=Math.round(d.score||0);var p=$('#pass');p.textContent=d.passed?'發音通過':'需要再練';p.className='badge'+(d.passed?'':' bad');$('#expected').textContent=d.expectedJyutping||'—';$('#heard').textContent=d.transcribedJyutping||'—';
 var a=String(d.expectedJyutping||'').trim().split(/\\s+/).filter(Boolean),b=String(d.transcribedJyutping||'').trim().split(/\\s+/).filter(Boolean),n=Math.max(a.length,b.length),html='',hit=0,total=0;
 for(var i=0;i<n;i++){var e=parts(a[i]),h=parts(b[i]),exact=e.raw===h.raw,base=e.base===h.base,tone=e.tone&&e.tone===h.tone;if(e.tone){total++;if(tone)hit++}var msg=exact?'完全一致':base&&!tone?('音節對，但聲調 '+e.tone+' → '+h.tone):(!a[i]?'多讀':!b[i]?'漏讀':tone?'聲調對，但音節不同':'音節與聲調都有差異');html+='<div class="diag"><code>'+esc(e.raw)+'</code><code class="'+(exact?'ok':'bad')+'">'+esc(h.raw)+'</code><span class="'+(exact?'ok':'bad')+'">'+esc(msg)+'</span></div>'}
 $('#diags').innerHTML=html;$('#tone').textContent=total?Math.round(hit/total*100)+'%':'—';$('#result').scrollIntoView({behavior:'smooth'})
}
analyse();
</script></body></html>`;

function send(res,status,body,type){res.writeHead(status,{'content-type':type||'application/json; charset=utf-8','cache-control':'no-store','x-content-type-options':'nosniff'});res.end(body)}
function js(res,status,obj){send(res,status,JSON.stringify(obj),'application/json; charset=utf-8')}
async function body(req){var a=[],n=0;for await(var c of req){n+=c.length;if(n>15*1024*1024)throw Error('too large');a.push(c)}return JSON.parse(Buffer.concat(a).toString()||'{}')}
function key(req){return String(req.headers['x-cantonese-key']||'').trim()}
async function upstream(r){var ct=r.headers.get('content-type')||'';return ct.includes('json')?r.json().catch(function(){return null}):r.text().catch(function(){return null})}
function err(d,f){return (d&&((d.error&&d.error.message)||d.message||d.error))||f}

http.createServer(async function(req,res){
 var u=new URL(req.url,'http://localhost'); console.log('[request]',req.method,u.pathname);
 if(req.method==='GET'&&u.pathname==='/'){return send(res,200,PAGE,'text/html; charset=utf-8')}
 if(req.method==='GET'&&u.pathname==='/api/health'){return js(res,200,{ok:true})}
 if(req.method!=='POST'||!u.pathname.startsWith('/api/'))return js(res,404,{error:'Not found'});
 var d;try{d=await body(req)}catch(e){return js(res,400,{error:'Bad request'})}
 try{
  if(u.pathname==='/api/jyutping'){
   var text=String(d.text||'').trim();if(!text)return js(res,400,{error:'請先輸入文字。'});
   var r=await fetch(CAI+'/text-to-jyutping',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({text:text,outputType:'list'}),signal:AbortSignal.timeout(8000)}),x=await upstream(r);if(!r.ok||!x.success)return js(res,r.status,{error:err(x,'粵拼轉換失敗')});
   var list=Array.isArray(x.result)?x.result:[];return js(res,200,{success:true,list:list,jyutping:list.map(function(z){return z.jyutping}).filter(Boolean).join(' ')})
  }
  if(u.pathname==='/api/tts'){
   var k=key(req);if(!k)return js(res,401,{error:'請先輸入 API key。'});
   var r=await fetch(CAI+'/tts',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({api_key:k,text:String(d.text||''),jyutping:String(d.jyutping||''),model_id:'v6',frame_rate:'24000',speed:.92,pitch:0,language:'cantonese',output_extension:'wav',should_return_timestamp:false})});
   if(!r.ok){var x=await upstream(r);return js(res,r.status,{error:err(x,'TTS 失敗')})}var buf=Buffer.from(await r.arrayBuffer());return send(res,200,buf,r.headers.get('content-type')||'audio/wav')
  }
  if(u.pathname==='/api/score'){
   var k=key(req);if(!k)return js(res,401,{error:'請先輸入 API key。'});var raw=Buffer.from(String(d.audioBase64||''),'base64');if(!raw.length||raw.length>10*1024*1024)return js(res,400,{error:'錄音無效'});
   var f=new FormData();f.append('api_key',k);f.append('text',String(d.text||''));f.append('language','cantonese');f.append('audio',new Blob([raw],{type:'audio/wav'}),'recording.wav');
   var r=await fetch(CAI+'/score-pronunciation',{method:'POST',body:f}),x=await upstream(r);if(!r.ok||!x.success)return js(res,r.status,{error:err(x,'評分失敗')});return js(res,200,x)
  }
  return js(res,404,{error:'Not found'})
 }catch(e){return js(res,502,{error:'上游服務連接失敗：'+e.message})}
}).listen(PORT,'0.0.0.0',function(){
 console.log('Cantonese Coach on '+PORT);
 fetch(CAI+'/text-to-jyutping',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({text:'你好嗎',outputType:'text'}),signal:AbortSignal.timeout(8000)})
  .then(async function(r){console.log('[selftest] jyutping',r.status,await r.text())})
  .catch(function(e){console.error('[selftest] jyutping failed',e.name,e.message)});
});
