"""Single-instance audio bridge and caption fanout for Render."""
import asyncio, base64, hashlib, hmac, json, logging, os, pathlib, time
from dataclasses import dataclass
from aiohttp import ClientSession, ClientTimeout, WSMsgType, web
from opencc import OpenCC

PUBLIC=pathlib.Path(__file__).parent/'public'
OPENAI_URL='wss://api.openai.com/v1/realtime/translations?model=gpt-realtime-translate'
log=logging.getLogger('satsuma')
LANG={1:'ja',2:'zh'}

@dataclass
class Config:
    api_key: str
    sender_token: str
    upstream: str=OPENAI_URL
    mock: bool=False

class Hub:
    def __init__(self):
        self.text={1:'',2:''};self.state='waiting';self.updated={1:None,2:None}
        self.watchers={1:set(),2:set()};self.sender=None;self.run_id=None;self.version=0
        self.converter=OpenCC('s2tw')
    def snapshot(self,ch):
        self.version+=1
        text=self.text[ch]
        if ch==2:text=self.converter.convert(text)
        return {'id':self.version,'text':text,'state':self.state,'updated':self.updated[ch],'channel':ch}
    def notify(self,ch=None):
        for channel in ([ch] if ch else [1,2]):
            snap=self.snapshot(channel)
            for q in tuple(self.watchers[channel]):
                if q.full():q.get_nowait()
                q.put_nowait(snap)
    def set_state(self,state):self.state=state;self.notify()
    def append(self,ch,delta):
        self.text[ch]=(self.text[ch]+delta)[-6000:];self.updated[ch]=time.time();self.notify(ch)

class Bridge:
    def __init__(self,client,config,hub,ingress,ch):
        self.client=client;self.config=config;self.hub=hub;self.ingress=ingress;self.ch=ch
        self.socket=None;self.task=None;self.closing=False
    async def open(self):
        self.socket=await self.client.ws_connect(self.config.upstream,headers={
            'Authorization':'Bearer '+self.config.api_key,
            'OpenAI-Safety-Identifier':hashlib.sha256(self.config.sender_token.encode()).hexdigest()
        },heartbeat=20,max_msg_size=4*1024*1024)
        e=await asyncio.wait_for(self.socket.receive_json(),10)
        if e.get('type')!='session.created':raise RuntimeError('upstream initialization failed')
        await self.socket.send_json({'type':'session.update','session':{'audio':{'output':{'language':LANG[self.ch]}}}})
        while True:
            e=await asyncio.wait_for(self.socket.receive_json(),10)
            if e.get('type')=='error':raise RuntimeError('upstream configuration failed')
            if e.get('type')=='session.updated':break
        self.task=asyncio.create_task(self.read())
    async def read(self):
        try:
            async for message in self.socket:
                if message.type!=WSMsgType.TEXT:continue
                event=json.loads(message.data);kind=event.get('type')
                if kind=='session.output_transcript.delta':
                    self.hub.append(self.ch,event.get('delta',''))
                elif kind=='error':raise RuntimeError('translation service error')
                elif kind=='session.closed':break
        except Exception as e:
            log.warning('translation channel %s failed: %s',self.ch,type(e).__name__)
        finally:
            if not self.closing and not self.ingress.closed:
                self.hub.set_state('reconnecting')
                await self.ingress.close(code=1011,message=b'translator disconnected')
    async def send(self,audio):
        await asyncio.wait_for(self.socket.send_json({'type':'session.input_audio_buffer.append','audio':audio}),2)
    async def close(self):
        self.closing=True
        if self.socket and not self.socket.closed:
            try:
                await self.socket.send_json({'type':'session.close'})
                if self.task:await asyncio.wait_for(asyncio.shield(self.task),8)
            except (Exception,asyncio.CancelledError):pass
            await self.socket.close()
        if self.task and not self.task.done():
            self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)

async def ingest(request):
    hub=request.app['hub'];cfg=request.app['config']
    origin=request.headers.get('Origin','')
    allowed=request.app['origins']
    # Same site on Render or localhost sender. Optional fixed ALLOWED_ORIGINS overrides this.
    if origin:
        from urllib.parse import urlsplit
        parsed=urlsplit(origin)
        permitted=origin in allowed if allowed else (parsed.hostname in ['127.0.0.1','localhost'] or parsed.netloc==request.host)
        if not permitted:raise web.HTTPForbidden(text='origin not allowed')
    ws=web.WebSocketResponse(heartbeat=20,max_msg_size=16000);await ws.prepare(request)
    bridges=[];owner=False;fault=False;graceful=False
    try:
        auth=await asyncio.wait_for(ws.receive_json(),8)
        if (auth.get('type')!='auth' or auth.get('protocol')!='satsuma-audio-v1'
            or not hmac.compare_digest(str(auth.get('token','')),cfg.sender_token)
            or not isinstance(auth.get('run_id'),str) or not 1<=len(auth['run_id'])<=100):
            await ws.send_json({'type':'error','code':'auth_failed'});return ws
        if hub.sender is not None:
            await ws.send_json({'type':'error','code':'sender_busy'});return ws
        hub.sender=ws;owner=True
        if hub.run_id!=auth['run_id']:
            hub.text={1:'',2:''};hub.updated={1:None,2:None};hub.run_id=auth['run_id']
        hub.set_state('connecting')
        if not cfg.mock:
            for ch in [1,2]:
                b=Bridge(request.app['client'],cfg,hub,ws,ch);bridges.append(b);await b.open()
        hub.set_state('live');await ws.send_json({'type':'ready'})
        previous={1:-1,2:-1};rate_window=time.monotonic();packets=0
        while not ws.closed:
            message=await asyncio.wait_for(ws.receive(),15)
            if message.type!=WSMsgType.TEXT:break
            msg=json.loads(message.data);kind=msg.get('type')
            if kind=='ping':await ws.send_json({'type':'pong'});continue
            if kind=='stop':
                graceful=True;await ws.send_json({'type':'stopped'});break
            if kind!='audio':raise ValueError('invalid type')
            now=time.monotonic()
            if now-rate_window>=1:rate_window=now;packets=0
            packets+=1
            if packets>60:raise ValueError('too many packets')
            ch=msg.get('channel');seq=msg.get('seq')
            if (ch not in [1,2] or not isinstance(seq,int) or seq<=previous[ch]
                or msg.get('run_id')!=auth['run_id'] or msg.get('sample_rate')!=24000
                or msg.get('format')!='pcm_s16le'):raise ValueError('invalid audio metadata')
            audio=msg.get('audio','');pcm=base64.b64decode(audio,validate=True)
            if len(pcm)!=4800:raise ValueError('invalid audio size')
            previous[ch]=seq
            if cfg.mock:
                if seq%10==0:hub.append(ch,'【接続試験】日本語字幕。' if ch==1 else '【连接测试】汉语字幕。')
            else:await bridges[ch-1].send(audio)
            await ws.send_json({'type':'ack','channel':ch,'seq':seq})
    except Exception as e:
        fault=True
        log.warning('ingress failed: %s',type(e).__name__)
    finally:
        if owner:
            hub.set_state('draining')
            await asyncio.gather(*(b.close() for b in bridges),return_exceptions=True)
            hub.sender=None;hub.set_state('unavailable' if fault or not graceful else 'stopped')
        if not ws.closed:await ws.close(code=1011 if fault else 1000)
    return ws

async def captions(request):
    hub=request.app['hub'];language=request.match_info['lang']
    if language not in ('ja','zh'):raise web.HTTPNotFound()
    ch=1 if language=='ja' else 2
    if sum(len(x) for x in hub.watchers.values())>=100:raise web.HTTPServiceUnavailable(text='viewer limit')
    response=web.StreamResponse(headers={'Content-Type':'text/event-stream','Cache-Control':'no-cache','X-Accel-Buffering':'no'})
    q=asyncio.Queue(maxsize=1);hub.watchers[ch].add(q)
    try:
        await response.prepare(request);q.put_nowait(hub.snapshot(ch))
        while True:
            try:
                snap=await asyncio.wait_for(q.get(),10)
                payload='id: '+str(snap['id'])+'\ndata: '+json.dumps(snap,ensure_ascii=False)+'\n\n'
            except asyncio.TimeoutError:payload=': heartbeat\n\n'
            await asyncio.wait_for(response.write(payload.encode()),5)
    except (ConnectionError,asyncio.TimeoutError,asyncio.CancelledError):pass
    finally:hub.watchers[ch].discard(q)
    return response

async def caption_page(request):
    if request.match_info['lang'] not in ('ja','zh'):raise web.HTTPNotFound()
    return web.FileResponse(PUBLIC/'captions.html')
async def home(request):return web.FileResponse(PUBLIC/'index.html')
async def health(request):return web.json_response({'ok':True,'mode':'test' if request.app['config'].mock else 'translation'})
async def lifecycle(app):
    app['client']=ClientSession(timeout=ClientTimeout(total=None,sock_connect=10))
    yield
    if app['hub'].sender:await app['hub'].sender.close(code=1001)
    await app['client'].close()

def create_app(config=None):
    config=config or Config(os.environ.get('OPENAI_API_KEY',''),os.environ.get('SENDER_TOKEN',''),mock=os.environ.get('MOCK_TRANSLATION')=='1')
    if len(config.sender_token)<16:raise RuntimeError('SENDER_TOKEN must contain at least 16 characters')
    if not config.mock and not config.api_key:raise RuntimeError('OPENAI_API_KEY is required')
    app=web.Application(client_max_size=16000);app['hub']=Hub();app['config']=config
    app['origins']=set(filter(None,os.environ.get('ALLOWED_ORIGINS','').split(',')))
    app.cleanup_ctx.append(lifecycle)
    app.router.add_get('/',home);app.router.add_get('/health',health)
    app.router.add_get('/ingest',ingest);app.router.add_get('/events/{lang}',captions)
    app.router.add_get('/live/{lang}',caption_page)
    app.router.add_static('/sender/',PUBLIC/'sender',show_index=False)
    return app
if __name__=='__main__':
    logging.basicConfig(level=logging.WARNING)
    web.run_app(create_app(),host='0.0.0.0',port=int(os.environ.get('PORT','10000')),access_log=None)
