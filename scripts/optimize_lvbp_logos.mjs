// Reproducible LVBP logo preparation: decode official PNG downloads, drop every ancillary chunk
// (metadata), optionally area-average downscale (never upscale), and re-encode as plain RGBA.
// usage: node scripts/optimize_lvbp_logos.mjs SRC.png DST.png [maxEdge]
import {readFileSync,writeFileSync} from 'node:fs';import zlib from 'node:zlib';
export function decode(buf){
 let p=8,w,h,bd,ct,il,plte,trns;const idat=[];
 while(p<buf.length){const len=buf.readUInt32BE(p),type=buf.toString('latin1',p+4,p+8),d=buf.subarray(p+8,p+8+len);p+=12+len;
  if(type==='IHDR'){w=d.readUInt32BE(0);h=d.readUInt32BE(4);bd=d[8];ct=d[9];il=d[12];}
  else if(type==='PLTE')plte=d;else if(type==='tRNS')trns=d;else if(type==='IDAT')idat.push(d);}
 if(bd!==8||il!==0||![2,3,6].includes(ct))throw new Error(`unsupported png bd=${bd} ct=${ct} il=${il}`);
 const bpp={2:3,3:1,6:4}[ct],stride=w*bpp,raw=zlib.inflateSync(Buffer.concat(idat)),out=Buffer.alloc(h*stride);
 for(let y=0;y<h;y++){const f=raw[y*(stride+1)];
  for(let x=0;x<stride;x++){const v=raw[y*(stride+1)+1+x],a=x>=bpp?out[y*stride+x-bpp]:0,b=y?out[(y-1)*stride+x]:0,c=(x>=bpp&&y)?out[(y-1)*stride+x-bpp]:0;
   let r;switch(f){case 0:r=v;break;case 1:r=v+a;break;case 2:r=v+b;break;case 3:r=v+((a+b)>>1);break;
    case 4:{const q=a+b-c,pa=Math.abs(q-a),pb=Math.abs(q-b),pc=Math.abs(q-c);r=v+(pa<=pb&&pa<=pc?a:pb<=pc?b:c);break;}default:throw new Error('filter');}
   out[y*stride+x]=r&255;}}
 const rgba=Buffer.alloc(w*h*4);
 for(let i=0;i<w*h;i++){let R,G,B,A=255;
  if(ct===6){[R,G,B,A]=[out[i*4],out[i*4+1],out[i*4+2],out[i*4+3]];}
  else if(ct===2){[R,G,B]=[out[i*3],out[i*3+1],out[i*3+2]];}
  else{const k=out[i];[R,G,B]=[plte[k*3],plte[k*3+1],plte[k*3+2]];A=trns&&k<trns.length?trns[k]:255;}
  rgba.set([R,G,B,A],i*4);}
 return {w,h,rgba};
}
function resize({w,h,rgba},max){
 if(Math.max(w,h)<=max)return {w,h,rgba};
 const s=max/Math.max(w,h),nw=Math.max(1,Math.round(w*s)),nh=Math.max(1,Math.round(h*s)),out=Buffer.alloc(nw*nh*4);
 for(let y=0;y<nh;y++)for(let x=0;x<nw;x++){
  const x0=x/s,x1=(x+1)/s,y0=y/s,y1=(y+1)/s;let r=0,g=0,b=0,a=0,t=0;
  for(let yy=Math.floor(y0);yy<Math.min(h,Math.ceil(y1));yy++)for(let xx=Math.floor(x0);xx<Math.min(w,Math.ceil(x1));xx++){
   const wt=(Math.min(xx+1,x1)-Math.max(xx,x0))*(Math.min(yy+1,y1)-Math.max(yy,y0)),i=(yy*w+xx)*4,al=rgba[i+3]/255;
   r+=rgba[i]*al*wt;g+=rgba[i+1]*al*wt;b+=rgba[i+2]*al*wt;a+=al*wt;t+=wt;}
  const o=(y*nw+x)*4;if(a>0){out[o]=Math.round(r/a);out[o+1]=Math.round(g/a);out[o+2]=Math.round(b/a);}out[o+3]=Math.round(255*a/t);}
 return {w:nw,h:nh,rgba:out};
}
const crcT=Array.from({length:256},(_,n)=>{let c=n;for(let k=0;k<8;k++)c=c&1?0xedb88320^(c>>>1):c>>>1;return c>>>0;});
const crc=b=>{let c=~0;for(const x of b)c=crcT[(c^x)&255]^(c>>>8);return ~c>>>0;};
function chunk(t,d){const b=Buffer.alloc(12+d.length);b.writeUInt32BE(d.length,0);b.write(t,4,'latin1');d.copy(b,8);b.writeUInt32BE(crc(b.subarray(4,8+d.length)),8+d.length);return b;}
export function encode({w,h,rgba}){
 const raw=Buffer.alloc(h*(w*4+1));for(let y=0;y<h;y++){raw[y*(w*4+1)]=0;rgba.copy(raw,y*(w*4+1)+1,y*w*4,(y+1)*w*4);}
 const ih=Buffer.alloc(13);ih.writeUInt32BE(w,0);ih.writeUInt32BE(h,4);ih[8]=8;ih[9]=6;
 return Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',ih),chunk('IDAT',zlib.deflateSync(raw,{level:9})),chunk('IEND',Buffer.alloc(0))]);
}
if(process.argv[1]?.endsWith('optimize_lvbp_logos.mjs')){
 const [,,src,dst,max='4096']=process.argv;const img=resize(decode(readFileSync(src)),+max);
 writeFileSync(dst,encode(img));console.log(`${dst} ${img.w}x${img.h}`);
}
