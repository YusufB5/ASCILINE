import assert from 'node:assert/strict';
const native = { WebSocket:globalThis.WebSocket, performance:globalThis.performance,
    setTimeout, clearTimeout, setInterval, clearInterval };

const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b; }); return {promise,resolve,reject}; };
let now=0, id=0;
const rafs=new Map(), timers=new Map(), sockets=[], decodes=[];
globalThis.location={protocol:'http:',host:'localhost:8000'};
globalThis.HTMLElement=class {};
globalThis.getComputedStyle=()=>({position:'relative'});
globalThis.HTMLAudioElement=class extends HTMLElement {
    constructor(){ super(); this.events=new Map(); this.plays=[]; this.paused=true; this.readyState=0; this.currentTime=0; this.src=''; }
    addEventListener(n,f){ if(!this.events.has(n))this.events.set(n,new Set());this.events.get(n).add(f); }
    removeEventListener(n,f){this.events.get(n)?.delete(f);}
    pause(){this.paused=true;}
    load(){this.readyState=0;this.currentTime=0;}
    play(){const d=deferred();this.plays.push(d);return d.promise;}
    playing(){this.paused=false;this.readyState=4;this.currentSrc=this.src;for(const f of [...(this.events.get('playing')||[])])f();this.plays.at(-1).resolve();}
};
const element=()=>({style:{},clientWidth:640,clientHeight:360,appendChild(){},remove(){},addEventListener(){},removeEventListener(){},setAttribute(){},classList:{add(){},remove(){}}});
globalThis.window={addEventListener(){},removeEventListener(){}};
globalThis.document={hidden:false,createElement:element,body:element()};
globalThis.WebSocket=class {
    static OPEN=1;
    constructor(url){this.url=url;this.readyState=1;this.sent=[];sockets.push(this);}
    send(s){this.sent.push(JSON.parse(s));}
    close(){this.readyState=3;}
    deliver(data){this.onmessage?.({data});}
};
globalThis.AscilineCodec={TAG_PROFILE:4,makeDecoder(bytes){return {decode(data){const d=deferred();decodes.push({...d,bytes,index:new DataView(data).getUint32(0)});return d.promise;}}}};
const {AsciiPlayer}=await import('../src/asciline-player.js');
Object.defineProperty(globalThis,'performance',{value:{now:()=>now},configurable:true,writable:true});
globalThis.requestAnimationFrame=f=>{rafs.set(++id,f);return id;};
globalThis.cancelAnimationFrame=id=>rafs.delete(id);
globalThis.setTimeout=(f,ms)=>{timers.set(++id,{f,at:now+ms});return id;};
globalThis.clearTimeout=id=>timers.delete(id);
globalThis.setInterval=()=>++id;
globalThis.clearInterval=()=>{};
const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
function tick(ms){now+=ms;for(const [i,t]of [...timers])if(t.at<=now){timers.delete(i);t.f();}const work=[...rafs.values()];rafs.clear();work.forEach(f=>f(now));}
function player(audio=false, Class=AsciiPlayer){
    const canvas=element();canvas.parentElement=element();canvas.getContext=()=>({createImageData:(w,h)=>({data:new Uint8ClampedArray(w*h*4)}),measureText:()=>({width:4}),putImageData(){},clearRect(){},fillRect(){},fillText(){}});
    return new Class(canvas,{audio,url:'ws://localhost:8000/ws?token=abc&pixel_codec=raw',selectionLayer:false,playOverlay:false,muteButton:false,clickToPlayPause:false,keyboardShortcuts:false});
}
function init(p,codec='raw',sync=true){p.play();sockets.at(-1).deliver(`INIT:60:6:2:1:1:0:60:0:0${sync?`:0:${codec}`:''}`);return sockets.at(-1);}
function packet(n){const b=new ArrayBuffer(10);new DataView(b).setUint32(0,n);return b;}
function frames(ws,start=0,count=4){for(let i=0;i<count;i++)ws.deliver(packet(start+i));}

// URL parameters, fixed protocol preroll, pause/resume and latest seek marker.
{
 const p=player();p.options.bufferSize=12;const ws=init(p);
 const url=new URL(ws.url);assert.equal(url.searchParams.get('token'),'abc');assert.equal(url.searchParams.get('codec'),'adaptive');assert.equal(url.searchParams.get('pixel_codec'),'dct-v1');
 frames(ws);assert(p.readyToRender);assert.equal(rafs.size,1);assert(ws.sent.some(m=>m.type==='playback-ready'));
 p.seek(10);const first=ws.sent.at(-1).requestId;p.seek(2);const second=ws.sent.at(-1).requestId;
 ws.deliver(`SEEKED:${first}:10`);frames(ws,600);assert.equal(p.frameBuffer.length,0);
 ws.deliver(`SEEKED:${second}:2.002`);frames(ws,120);assert.equal(p.audioOffset,2.002);assert(p.readyToRender);
 p.pause();assert.equal(p.state,'PAUSED');const frozen=p.getMasterClock();tick(1000);assert.equal(p.getMasterClock(),frozen);
 p.resume();const seek=ws.sent.at(-1);assert.equal(seek.type,'seek');ws.deliver(`SEEKED:${seek.requestId}:${seek.time}`);frames(ws,120);assert.equal(rafs.size,1);
 p.setPixelMode(false);frames(ws,200);assert.equal(p.frameBuffer.length,0);ws.deliver('INIT:30:6:2:1:0:0:60:2:0:5:raw');assert(p.codecDecoder);
 p.destroy();assert.equal(rafs.size,0);assert.equal(ws.onmessage,null);
}
// Stale async decoder completions cannot overwrite a new DCT predictor/epoch.
{
 const p=player();const ws=init(p,'dct');ws.deliver(packet(0));await flush();const old=decodes.at(-1);assert.equal(old.bytes,3);
 p.seek(5);const seek=ws.sent.at(-1);ws.deliver(`SEEKED:${seek.requestId}:5`);ws.deliver(packet(300));await flush();const fresh=decodes.at(-1);
 old.resolve({frameIndex:0,frame:new Uint8Array(6)});await flush();assert.equal(p.framesInFlight,1);assert.equal(p.frameBuffer.length,0);
 fresh.resolve({frameIndex:300,frame:new Uint8Array(6)});await flush();assert.equal(p.framesInFlight,0);assert.equal(p.frameBuffer[0].time,5);
 ws.deliver(packet(301));await flush();const doomed=decodes.at(-1);p.destroy();doomed.resolve({frameIndex:301,frame:new Uint8Array(6)});await flush();assert.equal(p.frameBuffer.length,0);
 p.play();assert.notEqual(p.ws,ws);p.destroy();
}
// Delayed audio does not start wall clock at metadata; stale promises cannot resume.
{
 const audio=new HTMLAudioElement();const p=player(audio);const ws=init(p);frames(ws);assert.equal(audio.plays.length,1);
 tick(700);assert(!p.readyToRender);audio.playing();await flush();assert(p.readyToRender);
 p.seek(3);let seek=ws.sent.at(-1);ws.deliver(`SEEKED:${seek.requestId}:3`);frames(ws,180);const stale=audio.plays.at(-1);
 p.seek(4);stale.reject(new Error('old'));await flush();assert(!p.readyToRender);assert.equal(p._audioGated,false);
 seek=ws.sent.at(-1);ws.deliver(`SEEKED:${seek.requestId}:4`);frames(ws,240);audio.plays.at(-1).reject(new Error('blocked'));await flush();assert(p.readyToRender);assert(p._audioGated);
 const clock=p.getMasterClock();await p.unmute();assert.equal(ws.sent.at(-1).time,clock);assert(!p.readyToRender);
 p.destroy();tick(3000);assert.equal(rafs.size,0);
}
// Short EOF preroll drains, and legacy INIT needs no ready handshake.
{
 const p=player();const ws=init(p);frames(ws,0,2);assert(!p.readyToRender);ws.onclose({code:1000});assert(p.readyToRender);tick(17);tick(17);tick(100);assert.equal(p.state,'ENDED');p.destroy();
 const old=player();const socket=init(old,'raw',false);frames(socket,0,1);assert(old.readyToRender);assert(!socket.sent.some(m=>m.type==='playback-ready'));old.destroy();
}
// Actual embedded SDK codec, not the controlled decoder above.
{
 const p=player();p.state='PAUSED';p._ascfSrc='clip.ascf';p.frameBuffer=[{data:new Uint8Array(6),time:0}];
 p.resume();assert.equal(p.frameBuffer.length,1);assert.equal(rafs.size,1);p.destroy();
 const live=player();live.play('ws://localhost:9000/ws?token=other');assert.equal(new URL(sockets.at(-1).url).port,'9000');live.destroy();
}
delete globalThis.AscilineCodec;
const {AsciiPlayer:RealPlayer}=await import('../src/asciline-player.js?actual-codec');
if(process.argv[2]?.startsWith('ws:')){
 Object.assign(globalThis,native);
 if(!globalThis.WebSocket)globalThis.WebSocket=(await import('ws')).default;
 globalThis.requestAnimationFrame=f=>setTimeout(()=>f(performance.now()),16);
 globalThis.cancelAnimationFrame=clearTimeout;
 const p=player(false,RealPlayer);p.options.url=process.argv[2];const errors=[];p.on('error',e=>errors.push(e));
 const wait=async predicate=>{const end=performance.now()+5000;while(!predicate()){assert.equal(errors.length,0,String(errors));assert(performance.now()<end,'SDK live timeout');await new Promise(r=>setTimeout(r,10));}};
 try{
  p.play();await wait(()=>p.readyToRender&&p._live?.metrics.decoded>=4);assert.equal(p.pixelCodec,'dct');
  p.seek(1);await wait(()=>p.readyToRender&&p.audioOffset>=1);
  p.pause();await new Promise(r=>setTimeout(r,50));p.resume();await wait(()=>p.readyToRender);
  p.setPixelMode(false);await wait(()=>!p.pixelMode&&p.readyToRender);
  p.setPixelMode(true);await wait(()=>p.pixelMode&&p.readyToRender);assert.equal(p.pixelCodec,'dct');
  p.destroy();p.play();await wait(()=>p.readyToRender);assert.equal(errors.length,0);
  console.log('PASS real WebSocket SDK DCT startup, seek, resume, ASCII/DCT switch and reconnect');
 }finally{p.destroy();}
}else if(process.argv[2]){
 const {readFileSync}=await import('node:fs');const cases=JSON.parse(readFileSync(process.argv[2],'utf8'));
 for(const entry of cases){const p=player(false,RealPlayer);const ws=init(p,'dct');for(const item of entry.frames){const bytes=Uint8Array.from(Buffer.from(item.packet,'base64'));ws.deliver(bytes.buffer);await p.decodeQueue;assert.deepEqual(Buffer.from(p.frameBuffer.at(-1).data),Buffer.from(item.expected,'base64'));}p.destroy();}
}
console.log('PASS SDK live negotiation, timing, epoch isolation, seek, pause, mode switch, destroy, replay, legacy and EOF');
