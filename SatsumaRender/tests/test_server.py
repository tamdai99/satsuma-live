import asyncio,base64,json,pathlib,sys,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
from app import Config,create_app
TOKEN='example-test-token-123456'
class Tests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.upstreams=[]
  async def fake(request):
   ws=web.WebSocketResponse();await ws.prepare(request);self.upstreams.append(ws)
   await ws.send_json({'type':'session.created'})
   lang=None
   async for m in ws:
    e=json.loads(m.data)
    if e['type']=='session.update':
     lang=e['session']['audio']['output']['language'];await ws.send_json({'type':'session.updated'})
    elif e['type']=='session.input_audio_buffer.append':
     assert len(base64.b64decode(e['audio']))==4800
     await ws.send_json({'type':'session.output_transcript.delta','delta':'日本語。' if lang=='ja' else '汉语。'})
    elif e['type']=='session.close':
     await ws.send_json({'type':'session.output_transcript.delta','delta':'終端。'})
     await ws.send_json({'type':'session.closed'});await ws.close();break
   return ws
  upstream=web.Application();upstream.router.add_get('/translate',fake)
  self.upstream=TestServer(upstream);await self.upstream.start_server()
  self.app=create_app(Config('fake-api-key',TOKEN,str(self.upstream.make_url('/translate')).replace('http:','ws:')))
  self.client=TestClient(TestServer(self.app));await self.client.start_server()
 async def asyncTearDown(self):
  await self.client.close();await self.upstream.close()
 async def auth(self,token=TOKEN,run='unit-test'):
  ws=await self.client.ws_connect('/ingest');await ws.send_json({'type':'auth','protocol':'satsuma-audio-v1','token':token,'run_id':run});return ws,await ws.receive_json()
 async def test_routes_and_authentication(self):
  for path in ['/','/health','/sender/index.html','/live/ja','/live/zh']:
   r=await self.client.get(path);self.assertEqual(r.status,200);await r.read()
  r=await self.client.get('/.env');self.assertEqual(r.status,404)
  ws,e=await self.auth('wrong');self.assertEqual(e['code'],'auth_failed');await ws.close();self.assertEqual(len(self.upstreams),0)
 async def test_two_languages_fanout_and_drain(self):
  ws,e=await self.auth();self.assertEqual(e['type'],'ready')
  readers=[]
  for i in range(50):
   r=await self.client.get('/events/'+('ja' if i%2==0 else 'zh'));self.assertEqual(r.status,200);readers.append(r)
   await r.content.readuntil(b'\n\n')
  for ch in [1,2]:
   await ws.send_json({'type':'audio','run_id':'unit-test','channel':ch,'seq':0,'sample_rate':24000,'format':'pcm_s16le','audio':base64.b64encode(bytes(4800)).decode()})
   self.assertEqual((await ws.receive_json())['channel'],ch)
  for i,r in enumerate(readers):
   event=(await asyncio.wait_for(r.content.readuntil(b'\n\n'),2)).decode()
   self.assertIn('日本語' if i%2==0 else '漢語',event)
   r.close()
  await ws.send_json({'type':'ping'});self.assertEqual((await ws.receive_json())['type'],'pong')
  await ws.send_json({'type':'stop'});self.assertEqual((await ws.receive_json())['type'],'stopped')
  await asyncio.sleep(.1);self.assertIn('終端',self.app['hub'].text[1]);self.assertIn('終端',self.app['hub'].text[2]);self.assertEqual(self.app['hub'].state,'stopped')
 async def test_second_sender_is_refused(self):
  ws,e=await self.auth();other,e=await self.auth(run='another');self.assertEqual(e['code'],'sender_busy')
  await other.close();await ws.send_json({'type':'stop'});await ws.close()
 async def test_invalid_pcm_closes_and_releases_lock(self):
  ws,e=await self.auth();await ws.send_json({'type':'audio','run_id':'unit-test','channel':1,'seq':0,'sample_rate':24000,'format':'pcm_s16le','audio':'AA=='})
  await ws.receive();await ws.close();await asyncio.sleep(.1);self.assertIsNone(self.app['hub'].sender)
 async def test_disallowed_origin(self):
  with self.assertRaises(Exception):await self.client.ws_connect('/ingest',headers={'Origin':'https://unrelated.example'})
if __name__=='__main__':unittest.main()
