import {StereoResampler,pcm16} from './dsp.js';
class Capture extends AudioWorkletProcessor {
 constructor(){super();this.resampler=new StereoResampler(sampleRate);this.pending=[[],[]];this.report=0;}
 process(inputs){
  const c=inputs[0];
  if(!c||c.length===0)return true;
  if(c.length!==2){if(++this.report%100===1)this.port.postMessage({error:'2チャンネル入力を取得できません。送信を停止してください。'});return true;}
  const peaks=c.map(a=>{let p=0;for(const x of a)p=Math.max(p,Math.abs(x));return p;});
  const r=this.resampler.push(c[0],c[1]);
  this.pending[0].push(...r[0]);this.pending[1].push(...r[1]);
  while(this.pending[0].length>=2400){
   const a=pcm16(this.pending[0].splice(0,2400)),b=pcm16(this.pending[1].splice(0,2400));
   this.port.postMessage({left:a.buffer,right:b.buffer,peaks},[a.buffer,b.buffer]);
  }
  return true;
 }
}
registerProcessor('stereo-capture',Capture);
