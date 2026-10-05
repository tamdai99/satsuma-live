const $=id=>document.getElementById(id);
let stream,context,source,worklet,socket,timer,heartbeat,running=false,ready=false,retries=0,seq=0,sent=0,dropped=0,lastAck=0,runId,captureAt=0,settings,capturing=false;
function status(t,error=false){$('status').textContent=t;$('status').className=error?'error':(ready?'live':'');}
function controls(){const captured=!!worklet;$('start').disabled=!captured||running||!$('verified').checked;$('stop').disabled=!running;$('preview').disabled=captured||running||capturing;$('refresh').disabled=captured||running;$('release').disabled=!captured||running;$('device').disabled=captured||running;$('url').disabled=running;$('token').disabled=running;$('verified').disabled=running;}
function safeError(e){return e?.name==='NotAllowedError'?'マイクへのアクセスを許可してください。':e?.name==='OverconstrainedError'?'このデバイスは2チャンネル入力に対応していません。':(e?.message||'処理に失敗しました。');}
async function devices(){let permission;try{permission=await navigator.mediaDevices.getUserMedia({audio:true});const list=await navigator.mediaDevices.enumerateDevices();$('device').replaceChildren(...list.filter(x=>x.kind==='audioinput').map(x=>new Option(x.label||'入力デバイス',x.deviceId)));$('hardware').textContent='USBインターフェースを選び、「入力テスト」を押してください。';}catch(e){status(safeError(e),true);}finally{permission?.getTracks().forEach(t=>t.stop());}}
async function capture(){
 if(capturing||worklet)return;capturing=true;controls();
 try{
  stream=await navigator.mediaDevices.getUserMedia({audio:{deviceId:{exact:$('device').value},channelCount:{exact:2},echoCancellation:false,noiseSuppression:false,autoGainControl:false},video:false});
  const track=stream.getAudioTracks()[0];settings=track.getSettings();
  if(settings.channelCount!==2)throw Error('ブラウザが2チャンネル入力を確認できませんでした。');
  if(settings.echoCancellation===true||settings.noiseSuppression===true||settings.autoGainControl===true)throw Error('音声の自動処理を解除できませんでした。');
  context=new AudioContext({sampleRate:48000,latencyHint:'interactive'});await context.resume();
  await context.audioWorklet.addModule('./capture.js');source=context.createMediaStreamSource(stream);
  worklet=new AudioWorkletNode(context,'stereo-capture',{numberOfInputs:1,numberOfOutputs:1,outputChannelCount:[1],channelCountMode:'max',channelInterpretation:'discrete'});
  worklet.port.onmessage=({data})=>{
   if(data.error){status(data.error,true);stop();release();return;}
   captureAt=Date.now();data.peaks.forEach((p,i)=>{$(`meter${i+1}`).value=p;$(`level${i+1}`).textContent=(p?Math.max(-99,20*Math.log10(p)).toFixed(1):'−∞')+' dBFS'+(p>=.98?' · 入力過大':'');});
   if(!running)return;
   const n=seq++;
   if(!ready||!socket||socket.readyState!==WebSocket.OPEN||socket.bufferedAmount>128000){dropped+=2;update();return;}
   for(const [i,a] of [data.left,data.right].entries()){
    const bytes=new Uint8Array(a);let text='';for(const b of bytes)text+=String.fromCharCode(b);
    socket.send(JSON.stringify({type:'audio',run_id:runId,channel:i+1,seq:n,sample_rate:24000,format:'pcm_s16le',audio:btoa(text)}));sent++;
   }update();
  };
  source.connect(worklet);worklet.connect(context.destination); // worklet output is silence
  track.onended=()=>{stop();release();status('入力デバイスとの接続が切れました。再度選択してください。',true);};
  context.onstatechange=()=>{if(context.state==='suspended'&&running){stop();status('音声入力が停止しました。入力テストから再開してください。',true);}};
  $('hardware').textContent=`2入力確認済み · ${context.sampleRate} Hz → 24,000 Hz · 自動音量調整なし`;
  status('入力テスト中。片方ずつマイクで話し、対応するメーターだけが動くことを確認してください。');controls();
 }catch(e){await release();status(safeError(e),true);}
 finally{capturing=false;controls();}
}
function update(){$('frames').textContent=`送信 ${sent} パケット`;$('drops').textContent=`欠落 ${dropped} パケット`;$('ack').textContent=lastAck?'サーバー応答 '+new Date(lastAck).toLocaleTimeString():'サーバー応答 —';}
function urlCheck(value){const u=new URL(value);if(u.username||u.password||u.hash||u.search)throw Error('URLに認証情報・クエリ・フラグメントを含めないでください。');if(u.protocol!=='wss:'&&!(u.protocol==='ws:'&&['localhost','127.0.0.1','[::1]'].includes(u.hostname)))throw Error('外部サーバーには wss:// を使用してください。');return u.href;}
function connect(url,token){
 if(!running)return;ready=false;status(retries?'再接続中。切断中の音声は再送しません。':'サーバーに接続中…');
 const ws=new WebSocket(url);socket=ws;
 const timeout=setTimeout(()=>{if(socket===ws&&!ready)ws.close();},30000);
 ws.onopen=()=>{if(!running){ws.close();return;}ws.send(JSON.stringify({type:'auth',protocol:'satsuma-audio-v1',token,run_id:runId,channels:2,sample_rate:24000,format:'pcm_s16le'}));};
 ws.onmessage=({data})=>{if(socket!==ws)return;let e;try{e=JSON.parse(data);}catch{return;}if(e.type==='error'){if(e.code==='sender_busy'){ws.close();return;}stop();status('サーバーが接続を拒否しました。トークンと通信仕様を確認してください。',true);return;}if(['ready','ack','pong'].includes(e.type)){lastAck=Date.now();update();}if(e.type==='ready'){clearTimeout(timeout);ready=true;retries=0;status('送信中 · INPUT 1 中国語 / INPUT 2 日本語');}};
 ws.onclose=()=>{clearTimeout(timeout);if(socket!==ws)return;ready=false;if(running){status('接続が切れました。自動再接続中…',true);timer=setTimeout(()=>connect(url,token),Math.min(15000,1000*2**Math.min(retries++,4)));}};
 ws.onerror=()=>{};
}
function start(){try{const url=urlCheck($('url').value.trim()),token=$('token').value;if(!token)throw Error('送信トークンを入力してください。');if(!worklet||!$('verified').checked)throw Error('入力テストと確認チェックが必要です。');running=true;runId=crypto.randomUUID();seq=sent=dropped=lastAck=retries=0;update();connect(url,token);heartbeat=setInterval(()=>{if(Date.now()-captureAt>3000){stop();status('音声入力が3秒以上途絶えました。入力テストをやり直してください。',true);return;}if(socket?.readyState===WebSocket.OPEN&&ready){if(Date.now()-lastAck>30000){socket.close();return;}socket.send(JSON.stringify({type:'ping'}));}},5000);controls();}catch(e){status(safeError(e),true);}}
function stop(){running=false;ready=false;clearTimeout(timer);clearInterval(heartbeat);const ws=socket;socket=null;if(ws?.readyState===WebSocket.OPEN){ws.send(JSON.stringify({type:'stop',run_id:runId,last_seq:seq-1}));setTimeout(()=>ws.close(),300);}else ws?.close();status('送信停止 · 入力メーターは引き続き確認できます');controls();}
async function release(){if(running)stop();if(stream){stream.getTracks().forEach(t=>{t.onended=null;t.stop();});stream=null;}source?.disconnect();worklet?.disconnect();source=worklet=null;if(context){context.onstatechange=null;await context.close();context=null;}$('verified').checked=false;for(const i of [1,2]){$(`meter${i}`).value=0;$(`level${i}`).textContent='−∞ dBFS';}controls();}
$('refresh').onclick=devices;$('preview').onclick=capture;$('release').onclick=release;$('start').onclick=start;$('stop').onclick=stop;$('verified').onchange=controls;
window.addEventListener('beforeunload',e=>{if(running){e.preventDefault();e.returnValue='';}});
controls();
// Render-hosted sender uses the same origin; local sender can override the URL.
if(location.protocol==='https:'&&!$('url').value)$('url').value='wss://'+location.host+'/ingest';
