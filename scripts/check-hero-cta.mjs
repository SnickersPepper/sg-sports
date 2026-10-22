#!/usr/bin/env node
// Pixel check for the focused hero CTA at 1920x1080: counts dark label/glyph pixels inside the white pill. A blank pill reports ~0.
import fs from 'fs';import zlib from 'zlib';
function dec(f){const b=fs.readFileSync(f);let o=8,w,h,ct,idat=[];while(o<b.length){const l=b.readUInt32BE(o),t=b.toString('ascii',o+4,o+8);if(t==='IHDR'){w=b.readUInt32BE(o+8);h=b.readUInt32BE(o+12);ct=b[o+17]}if(t==='IDAT')idat.push(b.subarray(o+8,o+8+l));o+=12+l}
const bpp=ct===6?4:3;const raw=zlib.inflateSync(Buffer.concat(idat));const px=Buffer.alloc(w*h*bpp);const st=w*bpp;
for(let y=0;y<h;y++){const ft=raw[y*(st+1)];for(let x=0;x<st;x++){const r=raw[y*(st+1)+1+x];const a=x>=bpp?px[y*st+x-bpp]:0,u=y?px[(y-1)*st+x]:0,c=(x>=bpp&&y)?px[(y-1)*st+x-bpp]:0;let v;switch(ft){case 0:v=r;break;case 1:v=r+a;break;case 2:v=r+u;break;case 3:v=r+((a+u)>>1);break;default:{const p=a+u-c,pa=Math.abs(p-a),pb=Math.abs(p-u),pc=Math.abs(p-c);v=r+(pa<=pb&&pa<=pc?a:pb<=pc?u:c)}}px[y*st+x]=v&255}}
return{w,h,bpp,px}}
for(const f of process.argv.slice(2)){const{w,h,bpp,px}=dec(f);let dark=0,light=0;for(let y=650;y<712;y++)for(let x=890;x<1195;x++){const i=(y*w+x)*bpp;const l=(px[i]+px[i+1]+px[i+2])/3;if(l<90)dark++;else if(l>200)light++}console.log(f.split('/').pop(),w+'x'+h,'darkPx(label/glyph)=',dark,'lightPx(pill)=',light)}
