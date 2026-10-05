// Stateful windowed-sinc resampler. Channels are never mixed.
export class StereoResampler {
  constructor(rate, outputRate=24000) {
    this.ratio=rate/outputRate; this.radius=24;
    this.cutoff=Math.min(1,outputRate/rate)*0.85;
    this.buffers=[Array(this.radius).fill(0),Array(this.radius).fill(0)];
    this.position=this.radius;
  }
  push(left,right) {
    if(left.length!==right.length) throw Error('channel length mismatch');
    for(let i=0;i<left.length;i++){this.buffers[0].push(left[i]);this.buffers[1].push(right[i]);}
    const out=[[],[]];
    while(this.position+this.radius<this.buffers[0].length){
      const base=Math.floor(this.position);let a=0,b=0,total=0;
      for(let i=base-this.radius+1;i<=base+this.radius;i++){
        const x=i-this.position;
        const sinc=Math.abs(x)<1e-9?this.cutoff:Math.sin(Math.PI*this.cutoff*x)/(Math.PI*x);
        const w=sinc*(0.5+0.5*Math.cos(Math.PI*x/this.radius));
        total+=w;a+=this.buffers[0][i]*w;b+=this.buffers[1][i]*w;
      }
      out[0].push(a/total);out[1].push(b/total);this.position+=this.ratio;
    }
    const trim=Math.max(0,Math.floor(this.position)-this.radius);
    if(trim){this.buffers[0].splice(0,trim);this.buffers[1].splice(0,trim);this.position-=trim;}
    return out;
  }
}
export function pcm16(samples){
  const bytes=new Uint8Array(samples.length*2), view=new DataView(bytes.buffer);
  for(let i=0;i<samples.length;i++){
    const x=Math.max(-1,Math.min(1,samples[i]));
    view.setInt16(i*2,Math.round(x*(x<0?32768:32767)),true);
  }
  return bytes;
}
