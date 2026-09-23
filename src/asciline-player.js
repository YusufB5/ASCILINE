/**
 * SPDX-License-Identifier: MIT
 * Part of ASCILINE — Licensed under MIT (see LICENSE-MIT)
 *
 * ASCILINE Player SDK (asciline-player.js)
 * ========================================
 * High-performance, zero-dependency ASCII Video Player and WebSocket Stream Client.
 *
 * Usage:
 *   import { AsciiPlayer } from './src/asciline-player.js';
 *   const player = new AsciiPlayer('#my-canvas', { url: 'ws://localhost:8000/ws' });
 *   player.play();
 */

// Embed or import AscilineCodec
let AscilineCodecApi = (typeof AscilineCodec !== 'undefined') ? AscilineCodec : null;

if (!AscilineCodecApi) {
  const TAG_RAW = 0, TAG_ZLIB = 1, TAG_DELTA = 2, TAG_RLE_FULL = 3, TAG_PROFILE = 4;

  async function inflate(bytes) {
    const ds = new DecompressionStream('deflate');
    const writer = ds.writable.getWriter();
    const reader = ds.readable.getReader();

    writer.write(bytes);
    writer.close();

    const chunks = [];
    let totalLen = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      totalLen += value.length;
    }

    if (chunks.length === 1) return chunks[0];
    const out = new Uint8Array(totalLen);
    let off = 0;
    for (const c of chunks) { out.set(c, off); off += c.length; }
    return out;
  }

  const _P_MI = Int32Array.from([23,23,23,23,23,23,23,23,31,27,18,6,-6,-18,-27,-31,30,12,-12,-30,-30,-12,12,30,
    27,-6,-31,-18,18,31,6,-27,23,-23,-23,23,23,-23,-23,23,18,-31,6,27,-27,-6,31,-18,
    12,-30,30,-12,-12,30,-30,12,6,-18,27,-31,31,-27,18,-6]);
  const _P_ZZ = Int32Array.from([0,1,8,16,9,2,3,10,17,24,32,25,18,11,4,5,12,19,26,33,40,48,41,34,27,20,13,6,7,14,
    21,28,35,42,49,56,57,50,43,36,29,22,15,23,30,37,44,51,58,59,52,45,38,31,39,46,53,60,61,54,47,55,62,63]);
  const _P_QLB=[16,11,10,16,24,40,51,61,12,12,14,19,26,58,60,55,14,13,16,24,40,57,69,56,14,17,22,29,51,87,80,62,
    18,22,37,56,68,109,103,77,24,35,55,64,81,104,113,92,49,64,78,87,103,121,120,101,72,92,95,98,112,100,103,99];
  const _P_QCB=[17,18,24,47,99,99,99,99,18,21,26,66,99,99,99,99,24,26,56,99,99,99,99,99,47,66,99,99,99,99,99,99,
    99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99];
  function _pqtables(QF){const S=QF<50?5000/QF:200-2*QF;const f=b=>{const o=new Int32Array(64);for(let i=0;i<64;i++){let v=Math.floor((b[i]*S+50)/100);o[i]=v<1?1:(v>255?255:v);}return o;};return [f(_P_QLB),f(_P_QCB)];}
  const _pT = new Float64Array(64);
  const _pO = new Int32Array(64);
  const _pZ = new Int32Array(64);
  const _pC = new Int32Array(64);
  function _pidct(C){
    for(let u=0;u<8;u++)for(let x=0;x<8;x++){let s=0;for(let v=0;v<8;v++)s+=C[u*8+v]*_P_MI[v*8+x];_pT[u*8+x]=s;}
    for(let y=0;y<8;y++)for(let x=0;x<8;x++){let s=0;for(let u=0;u<8;u++)s+=_P_MI[u*8+y]*_pT[u*8+x];_pO[y*8+x]=Math.floor((s+2048)/4096);}
    return _pO;
  }
  function _pDecodePlane(data,off,P,NP,ft,useMv,qm){
    const W=P.w,H=P.h,nbx=W>>3,nby=H>>3,nb=nbx*nby;
    let skip=null; if(ft===1){const mb=(nb+7)>>3;skip=data.subarray(off,off+mb);off+=mb;}
    let bi=0,dcPred=0;
    for(let by=0;by<nby;by++)for(let bx=0;bx<nbx;bx++){
      if(ft===1 && (skip[bi>>3]&(128>>(bi&7)))){bi++;continue;}
      let dx=0,dy=0;
      if(ft===1&&useMv){dx=(data[off]<<24>>24);dy=(data[off+1]<<24>>24);off+=2;}
      const nP=data[off++];
      _pZ.fill(0);
      let pos=0,lastNz=-1;
      for(let k=0;k<nP;k++){const run=data[off++];let v=data[off]|(data[off+1]<<8);off+=2;if(v&0x8000)v-=0x10000;pos+=run;_pZ[pos]=v;lastNz=pos;pos++;}
      _pZ[0]+=dcPred; dcPred=_pZ[0];
      let res=null,flat=0;
      if(lastNz<=0){ flat=Math.floor((529*(_pZ[0]*qm[0])+2048)/4096); }
      else { for(let k=0;k<64;k++){const id=_P_ZZ[k]; _pC[id]=_pZ[k]*qm[id];} res=_pidct(_pC); }
      for(let y=0;y<8;y++){
        const row=(by*8+y)*W;
        for(let x=0;x<8;x++){
          let pred;
          if(ft===0)pred=128;
          else{let sx=bx*8+x+dx,sy=by*8+y+dy;sx=sx<0?0:(sx>=W?W-1:sx);sy=sy<0?0:(sy>=H?H-1:sy);pred=P.buf[sy*W+sx];}
          const val=pred+(res===null?flat:res[y*8+x]);
          NP.buf[row+bx*8+x]=val<0?0:(val>255?255:val);
        }
      }
      bi++;
    }
    return off;
  }
  function _pYuvToBgr(Y,Cb,Cr,W,H){const out=new Uint8Array(W*H*3);const cW=W>>1;
    for(let y=0;y<H;y++){const cy=y>>1;for(let x=0;x<W;x++){const cx=x>>1;const yy=Y[y*W+x];const cb=Cb[cy*cW+cx]-128;const cr=Cr[cy*cW+cx]-128;
      let R=yy+((359*cr+128)>>8),G=yy-((88*cb+183*cr+128)>>8),B=yy+((454*cb+128)>>8);const o=(y*W+x)*3;
      out[o]=B<0?0:(B>255?255:B);out[o+1]=G<0?0:(G>255?255:G);out[o+2]=R<0?0:(R>255?255:R);}}
    return out;}
  function makeProfileDecoder(){
    let W=0,H=0,cW=0,cH=0,planes=null,spare=null,QL=null,QC=null;
    const alloc=()=>[{w:W,h:H,buf:new Uint8Array(W*H)},{w:cW,h:cH,buf:new Uint8Array(cW*cH)},{w:cW,h:cH,buf:new Uint8Array(cW*cH)}];
    async function decode(message){
      const b=message instanceof Uint8Array?message:new Uint8Array(message);
      const dv=new DataView(b.buffer,b.byteOffset,b.byteLength);
      const idx=dv.getUint32(0,false); const payload=await inflate(b.subarray(5)); const ft=payload[0];
      let off=1;
      if(ft===0){
        const QF=payload[1]; const cols=(payload[2]<<8)|payload[3]; const rows=(payload[4]<<8)|payload[5]; off=6;
        const q=_pqtables(QF); QL=q[0]; QC=q[1];
        if(planes===null||W!==cols||H!==rows){W=cols;H=rows;cW=W>>1;cH=H>>1;planes=alloc();spare=alloc();}
      }
      const out=spare;
      for(let i=0;i<3;i++) out[i].buf.set(planes[i].buf);
      for(let pi=0;pi<3;pi++) off=_pDecodePlane(payload,off,planes[pi],out[pi],ft,pi===0,pi===0?QL:QC);
      spare=planes; planes=out;
      return {frameIndex:idx, frame:_pYuvToBgr(planes[0].buf,planes[1].buf,planes[2].buf,W,H)};
    }
    return {decode, reset(){planes=null;spare=null;QL=QC=null;}};
  }

  function makeDecoder(cellBytes = 4) {
    let prev = null;
    let profileDec = null;

    async function decode(message) {
      const bytes = new Uint8Array(message);
      const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
      const frameIndex = view.getUint32(0, false);
      const tag = bytes[4];
      if (tag === TAG_PROFILE) {
        if (!profileDec) profileDec = makeProfileDecoder();
        return await profileDec.decode(bytes);
      }
      const payload = bytes.subarray(5);

      let frame;
      if (tag === TAG_RAW) {
        frame = payload.slice();
      } else if (tag === TAG_ZLIB) {
        frame = await inflate(payload);
      } else if (tag === TAG_DELTA) {
        const body = await inflate(payload);
        const k = body.length / (4 + cellBytes);
        const idx = new DataView(body.buffer, body.byteOffset, body.byteLength);
        frame = prev ? prev.slice() : new Uint8Array(k * cellBytes);
        const valuesOffset = k * 4;
        for (let j = 0; j < k; j++) {
          const cell = idx.getUint32(j * 4, true);
          const dst = cell * cellBytes;
          const src = valuesOffset + j * cellBytes;
          for (let c = 0; c < cellBytes; c++) frame[dst + c] = body[src + c];
        }
      } else if (tag === TAG_RLE_FULL) {
        const body = await inflate(payload);
        const bodyView = new DataView(body.buffer, body.byteOffset, body.byteLength);
        let totalCells = 0;
        let offset = 0;
        while (offset < body.length) {
          totalCells += bodyView.getUint16(offset, true);
          offset += 2 + cellBytes;
        }
        frame = new Uint8Array(totalCells * cellBytes);
        offset = 0;
        let dst = 0;
        while (offset < body.length) {
          const count = bodyView.getUint16(offset, true);
          const valOffset = offset + 2;
          if (cellBytes === 4) {
            const v0 = body[valOffset], v1 = body[valOffset+1], v2 = body[valOffset+2], v3 = body[valOffset+3];
            for (let i = 0; i < count; i++) {
              frame[dst++] = v0; frame[dst++] = v1; frame[dst++] = v2; frame[dst++] = v3;
            }
          } else if (cellBytes === 3) {
            const v0 = body[valOffset], v1 = body[valOffset+1], v2 = body[valOffset+2];
            for (let i = 0; i < count; i++) {
              frame[dst++] = v0; frame[dst++] = v1; frame[dst++] = v2;
            }
          } else {
            for (let i = 0; i < count; i++) {
              for (let c = 0; c < cellBytes; c++) frame[dst++] = body[valOffset + c];
            }
          }
          offset += 2 + cellBytes;
        }
      } else {
        if (prev) return { frameIndex, frame: prev };
        throw new Error('Unknown ASCILINE codec tag: ' + tag);
      }
      prev = frame;
      return { frameIndex, frame };
    }

    return { decode, reset() { prev = null; profileDec = null; } };
  }

  AscilineCodecApi = { makeDecoder, makeProfileDecoder, inflate, TAG_RAW, TAG_ZLIB, TAG_DELTA, TAG_RLE_FULL, TAG_PROFILE };
}

const CHAR_LUT = new Array(128);
for (let i = 0; i < 128; i++) CHAR_LUT[i] = String.fromCharCode(i);

/**
 * _ByteStreamParser — lightweight ring buffer for binary ASCF parsing.
 * Used internally by _connectAscf() to incrementally read header + frame packets.
 */
class _ByteStreamParser {
    constructor() { this.buffer = new Uint8Array(0); }
    push(chunk) {
        const n = new Uint8Array(this.buffer.length + chunk.length);
        n.set(this.buffer, 0);
        n.set(chunk, this.buffer.length);
        this.buffer = n;
    }
    read(bytes) {
        if (this.buffer.length < bytes) return null;
        const data = this.buffer.subarray(0, bytes).slice(); // owned copy
        this.buffer = this.buffer.subarray(bytes);
        return data;
    }
    unshift(chunk) {
        const n = new Uint8Array(chunk.length + this.buffer.length);
        n.set(chunk, 0);
        n.set(this.buffer, chunk.length);
        this.buffer = n;
    }
    peek32BE() {
        if (this.buffer.length < 4) return null;
        return (this.buffer[0] << 24 | this.buffer[1] << 16 | this.buffer[2] << 8 | this.buffer[3]) >>> 0;
    }
    get length() { return this.buffer.length; }
}

/**
 * AsciiPlayer Class
 */
export class AsciiPlayer {
    /**
     * @param {HTMLCanvasElement|string} canvas - Canvas element or CSS selector
     * @param {Object} options - Configuration options
     */
    constructor(canvas, options = {}) {
        this.canvas = typeof canvas === 'string' ? document.querySelector(canvas) : canvas;
        if (!this.canvas) throw new Error(`AsciiPlayer: Canvas element '${canvas}' not found.`);
        this.ctx = this.canvas.getContext('2d');

        // Options & Defaults
        this.options = Object.assign({
            url: null,                  // WebSocket URL or auto-derived
            src: null,                  // Static .ascf file URL (no backend required)
            audioSrc: null,             // Static audio file URL paired with src
            loop: false,                // Loop ASCF static playback when finished
            audio: true,                // Audio element / selector / boolean
            selectionLayer: null,       // Text element for copyable selection
            container: null,            // Sizing container (defaults to canvas parent)
            renderMode: 1,              // 1: Text, 2: 64 Color, 3: 512 Color, etc.
            pixelMode: false,           // Raw pixel mode
            autoplay: false,
            playOverlay: true,          // Auto-create a ▶ play button overlay (set false to manage manually)
            muteButton: true,           // Auto-create a mute/unmute toggle when audio is enabled
            clickToPlayPause: true,     // Click video to toggle Play / Pause
            keyboardShortcuts: true,    // Spacebar to toggle Play / Pause
            bufferSize: 4,
            filters: {
                contrast: 1.0,
                gamma: 1.0,
                brightness: 0,
                sharpness: 0,
                invert: false,
                palette: 'default'
            }
        }, options);

        // Container setup
        this.container = typeof this.options.container === 'string'
            ? document.querySelector(this.options.container)
            : (this.options.container || this.canvas.parentElement || document.body);

        // Selection Layer setup (Invisible Text Selection Overlay)
        this.selectionEnabled = this.options.selectionLayer !== false;
        if (typeof this.options.selectionLayer === 'string') {
            this.selectionLayer = document.querySelector(this.options.selectionLayer);
        } else if (this.options.selectionLayer instanceof HTMLElement) {
            this.selectionLayer = this.options.selectionLayer;
        } else if (this.selectionEnabled) {
            // Auto-create invisible selection overlay on top of canvas
            this.selectionLayer = document.createElement('pre');
            this.selectionLayer.className = 'ascii-selection-layer';
            this.selectionLayer.style.position = 'absolute';
            this.selectionLayer.style.top = '0';
            this.selectionLayer.style.left = '0';
            this.selectionLayer.style.margin = '0';
            this.selectionLayer.style.padding = '0';
            this.selectionLayer.style.fontFamily = 'Courier New, monospace';
            this.selectionLayer.style.fontWeight = 'bold';
            this.selectionLayer.style.fontSize = '8px';
            this.selectionLayer.style.lineHeight = '8px';
            this.selectionLayer.style.whiteSpace = 'pre';
            this.selectionLayer.style.overflow = 'hidden';
            this.selectionLayer.style.userSelect = 'text';
            this.selectionLayer.style.webkitUserSelect = 'text';
            this.selectionLayer.style.color = 'transparent';
            this.selectionLayer.style.pointerEvents = 'auto';
            this.selectionLayer.style.zIndex = '2';
            this.selectionLayer.style.display = 'none';

            if (this.canvas.parentElement) {
                if (getComputedStyle(this.canvas.parentElement).position === 'static') {
                    this.canvas.parentElement.style.position = 'relative';
                }
                this.canvas.parentElement.appendChild(this.selectionLayer);
            }
        } else {
            this.selectionLayer = null;
        }

        // Audio element setup
        if (typeof this.options.audio === 'string') {
            this.audioEl = document.querySelector(this.options.audio);
        } else if (this.options.audio instanceof HTMLAudioElement) {
            this.audioEl = this.options.audio;
        } else if (this.options.audio === true) {
            this.audioEl = document.createElement('audio');
            this.audioEl.preload = 'none';
        } else {
            this.audioEl = null;
        }

        // Internal State
        this.state = 'IDLE'; // IDLE | CONNECTING | PLAYING | PAUSED | ENDED | ERROR
        this.ws = null;
        this.frameBuffer = [];
        this.codecDecoder = null;
        this.decodeQueue = Promise.resolve();
        this.framesInFlight = 0;
        this.bufferReportTimer = null;

        // Playback Metrics
        this.targetFps = 24;
        this.frameInterval = 1000 / this.targetFps;
        this.renderMode = this.options.renderMode;
        this.pixelMode = this.options.pixelMode;
        this.readyToRender = false;
        this.pauseStartTime = 0;
        this.duration = 0;
        this.audioOffset = 0;
        this.isWebcamStream = false;
        this.currentQueueIdx = 0;

        // Canvas & Dimensions
        this.gridCols = 0;
        this.gridRows = 0;
        this.charWidth = 0;
        this.charHeight = 8;
        this.xPos = null;
        this.yPos = null;
        this.dotImageData = null;
        this.textDecoder = new TextDecoder();
        this.selectionBuffer = null;

        // Timing
        this.lastRenderTime = 0;
        this.frameCount = 0;
        this.currentFps = 0;
        this.lastFpsUpdate = 0;
        this.streamStartTime = 0;
        this.streamEpoch = 0;
        this._lastUiUpdate = 0;

        // Filters
        this.currentFilters = Object.assign({}, this.options.filters);
        this.filterSendTimer = null;

        // Event Emitter
        this._listeners = {};

        // Bound loops
        this._renderBound = this._renderFrame.bind(this);
        this._resizeBound = this.resize.bind(this);

        window.addEventListener('resize', this._resizeBound);

        // Build the ▶ play overlay unless opted out or autoplay is on
        if (this.options.playOverlay && !this.options.autoplay) {
            this._createPlayOverlay();
        }

        // Build the mute/unmute toggle button if enabled
        if (this.options.muteButton && this.audioEl) {
            this._createMuteButton();
        }

        // Click on video to toggle Play / Pause
        if (this.options.clickToPlayPause !== false && this.container) {
            this._containerClickHandler = (e) => {
                if (e.target.closest('.ascii-mute-btn') || e.target.closest('.ascii-play-overlay') || e.target.closest('.ascii-pause-overlay')) {
                    return;
                }
                if (window.getSelection && window.getSelection().toString().trim().length > 0) {
                    return;
                }
                this.togglePlay();
            };
            this.container.addEventListener('click', this._containerClickHandler);
        }

        // Keyboard Spacebar to toggle Play / Pause
        if (this.options.keyboardShortcuts !== false) {
            this._keydownHandler = (e) => {
                if (e.code === 'Space' && e.target.tagName !== 'INPUT' && e.target.tagName !== 'TEXTAREA') {
                    if (this.state === 'PLAYING' || this.state === 'PAUSED') {
                        e.preventDefault();
                        this.togglePlay();
                    }
                }
            };
            window.addEventListener('keydown', this._keydownHandler);
        }

        if (this.options.autoplay) {
            this.play();
        }
    }

    // ── EVENT EMITTER ──
    on(event, callback) {
        if (!this._listeners[event]) this._listeners[event] = [];
        this._listeners[event].push(callback);
        return this;
    }

    off(event, callback) {
        if (!this._listeners[event]) return this;
        this._listeners[event] = this._listeners[event].filter(cb => cb !== callback);
        return this;
    }

    emit(event, ...args) {
        if (this._listeners[event]) {
            for (const cb of this._listeners[event]) {
                try { cb(...args); } catch (e) { console.error(`AsciiPlayer event error [${event}]`, e); }
            }
        }
    }

    _setState(newState) {
        if (this.state !== newState) {
            this.state = newState;
            this.emit('statechange', newState);
            this.emit(newState.toLowerCase());
        }
    }

    getState() {
        return this.state;
    }

    // ── PUBLIC PLAYBACK CONTROL ──

    play(srcUrl, audioUrl) {
        if (this.state === 'PAUSED') {
            this.resume();
            return;
        }
        if (this.state !== 'IDLE' && this.state !== 'ENDED' && this.state !== 'ERROR') return;
        this._hidePlayOverlay();
        this._setState('CONNECTING');

        // Resolve source: explicit arg → options.src → options.url (WS)
        const resolvedSrc   = srcUrl   || this.options.src   || this.options.url;
        const resolvedAudio = audioUrl || this.options.audioSrc || null;

        // Route to static ASCF player when a .ascf URL / path is given
        if (this._isStaticSrc(resolvedSrc)) {
            this._connectAscf(resolvedSrc, resolvedAudio);
            return;
        }

        // ── Live WebSocket mode (original behaviour, unchanged) ──
        // Unlock audio context while we're inside a user-gesture call stack.
        // Browsers require audio.play() to be called synchronously from a user
        // gesture (click, tap, keydown). By the time the first INIT frame arrives
        // over WebSocket (~200 ms later), the gesture context is already gone.
        // Calling play() here — even on an empty src — permanently marks the
        // page as audio-allowed, so the real play() inside _triggerPlaybackStart
        // will succeed without being blocked by autoplay policy.
        if (this.audioEl && !this._audioUnlocked) {
            this.audioEl.play().then(() => {
                this._audioUnlocked = true;
            }).catch(() => {
                // Even a rejection still unlocks audio on most browsers.
                this._audioUnlocked = true;
            });
        }

        this._connectWebSocket();
    }

    pause() {
        if (this.state !== 'PLAYING' || this.isWebcamStream) return;
        this._setState('PAUSED');
        this.pauseStartTime = performance.now();

        if (this.audioEl && !this.audioEl.paused) {
            this.audioEl.pause();
        }
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type: 'pause', paused: true }));
        }
        this._showPauseOverlay();
    }

    resume() {
        if (this.state !== 'PAUSED') return;
        this._setState('PLAYING');
        this.readyToRender = true;
        this._hidePauseOverlay();

        const pauseDuration = performance.now() - this.pauseStartTime;
        this.streamStartTime += pauseDuration;
        this.pauseStartTime = 0;

        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type: 'pause', paused: false }));
        }

        // Always call play() even when muted — the audio element's currentTime
        // is the master clock for video sync. If we don't call play() it stays
        // frozen and the render loop never advances frames.
        if (this.audioEl && this.audioEl.paused && !this._audioGated) {
            this.audioEl.play().catch(() => {});
        }

        // WS mode: clear stale frames — the server will push fresh ones from
        // the current position after receiving the 'pause: false' message.
        // ASCF static mode: keep the buffered frames. The file stream is already
        // ahead of the audio clock; discarding the buffer creates a gap where
        // the render loop has nothing to draw (video freezes while audio plays).
        if (!this._ascfSrc) {
            this.frameBuffer.length = 0;
        }

        this.lastRenderTime = performance.now();
        this.lastFpsUpdate = performance.now();
        this.frameCount = 0;
        requestAnimationFrame(this._renderBound);
    }

    togglePlay() {
        if (this.state === 'PLAYING') this.pause();
        else if (this.state === 'PAUSED') this.resume();
        else if (this.state === 'IDLE' || this.state === 'ENDED' || this.state === 'ERROR') this.play();
    }

    // Returns true when the URL points to a static .ascf file (not a WebSocket).
    _isStaticSrc(url) {
        if (!url) return false;
        const lower = url.toLowerCase();
        if (lower.startsWith('ws://') || lower.startsWith('wss://')) return false;
        return lower.endsWith('.ascf') || lower.includes('.ascf?') || lower.includes('.ascf#');
    }

    seek(targetSec) {
        if (this.isWebcamStream) return;
        if (this.duration) targetSec = Math.max(0, Math.min(targetSec, this.duration));

        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type: 'seek', time: targetSec }));
        }

        this.frameBuffer.length = 0;
        this.audioOffset = targetSec;

        if (this.audioEl) {
            this.audioEl.pause();
            this.streamEpoch++;
            const myEpoch = this.streamEpoch;
            this.audioEl.src = this._getAudioUrl(`v=${this.currentQueueIdx}&start=${targetSec}&t=${Date.now()}`);
            this.audioEl.load();

            if (this.state === 'PLAYING') {
                this.readyToRender = false;
                this.audioEl.play().catch(() => {});
                const onAudioStart = () => {
                    if (!this.readyToRender) {
                        this.readyToRender = true;
                        this.streamStartTime = performance.now() - (targetSec * 1000.0);
                        this.lastRenderTime = performance.now();
                        this.lastFpsUpdate = performance.now();
                        this.frameCount = 0;
                        requestAnimationFrame(this._renderBound);
                    }
                };
                if (this.audioEl.readyState >= 3) onAudioStart();
                else {
                    this.audioEl.addEventListener('playing', () => {
                        if (myEpoch !== this.streamEpoch) return;
                        onAudioStart();
                    }, { once: true });
                    setTimeout(() => {
                        if (myEpoch !== this.streamEpoch) return;
                        onAudioStart();
                    }, 500);
                }
            } else {
                this.streamStartTime = performance.now() - (targetSec * 1000.0);
                if (this.state === 'PAUSED') this.pauseStartTime = performance.now();
            }
        } else {
            this.streamStartTime = performance.now() - (targetSec * 1000.0);
            if (this.state === 'PAUSED') this.pauseStartTime = performance.now();
        }

        this.emit('seek', targetSec);
    }

    skip(deltaSec) {
        this.seek(this.getMasterClock() + deltaSec);
    }

    setVolume(volume) {
        const vol = Math.max(0, Math.min(1, volume));
        if (this.audioEl) this.audioEl.volume = vol;
    }

    mute() {
        if (!this.audioEl) return;
        this.audioEl.muted = true;
        this._updateMuteButton();
    }

    async unmute() {
        if (!this.audioEl) return;
        // If video has been running on wall-clock while audio was blocked,
        // seek the audio stream to the current video position so they sync up instantly.
        if (this._audioGated && this.readyToRender && !this.isWebcamStream) {
            const currentSec = (this.state === 'PAUSED' && this.pauseStartTime)
                ? (this.pauseStartTime - this.streamStartTime) / 1000.0
                : (performance.now() - this.streamStartTime) / 1000.0;
            if (currentSec > 0.5) {
                this.audioOffset = currentSec;
                this.audioEl.src = this._getAudioUrl(
                    `v=${this.currentQueueIdx}&start=${currentSec.toFixed(2)}&t=${Date.now()}`
                );
                this.audioEl.load();
            }
        }
        this._audioGated = false;
        this.audioEl.muted = false;
        this._updateMuteButton();

        // If the player is currently PAUSED, we must NOT start playing audio!
        // Audio will start synchronously when resume() is called.
        if (this.state === 'PLAYING') {
            const playPromise = this.audioEl.play();
            if (playPromise) {
                playPromise.then(() => this._updateMuteButton()).catch(() => {});
            }
            return playPromise;
        } else {
            this.audioEl.pause();
            return Promise.resolve();
        }
    }

    getMasterClock() {
        // Audio is the master clock as long as it has loaded metadata (readyState >= 1).
        if (this.audioEl && this.audioEl.readyState >= 1) {
            if (this._audioGated && this.audioEl.paused && this.audioEl.currentTime === 0) {
                // Autoplay blocked: run on wall-clock so video doesn't freeze
                const now = (this.state === 'PAUSED' && this.pauseStartTime) ? this.pauseStartTime : performance.now();
                return (now - this.streamStartTime) / 1000.0;
            }
            return this.audioEl.currentTime + this.audioOffset;
        }
        const now = (this.state === 'PAUSED' && this.pauseStartTime) ? this.pauseStartTime : performance.now();
        return (now - this.streamStartTime) / 1000.0;
    }

    setFilters(filters) {
        Object.assign(this.currentFilters, filters);
        if (this.filterSendTimer) clearTimeout(this.filterSendTimer);
        this.filterSendTimer = setTimeout(() => {
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify(Object.assign({ type: 'filter' }, this.currentFilters)));
            }
            this.filterSendTimer = null;
        }, 60);
    }

    setPixelMode(enable) {
        this.pixelMode = Boolean(enable);
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({
                type: 'reinit',
                pixel: this.pixelMode,
                time: this.getMasterClock()
            }));
        }
    }

    setRenderMode(mode) {
        this.renderMode = mode;
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({
                type: 'reinit',
                mode: this.renderMode,
                time: this.getMasterClock()
            }));
        }
    }

    setSelectionLayer(enable) {
        this.selectionEnabled = Boolean(enable);
        if (this.selectionLayer) {
            this.selectionLayer.style.display = (this.selectionEnabled && !this.pixelMode) ? 'block' : 'none';
        }
    }

    toggleSelectionLayer() {
        this.setSelectionLayer(!this.selectionEnabled);
        return this.selectionEnabled;
    }

    // ── PLAY OVERLAY ──

    _createPlayOverlay() {
        if (this._playOverlay) return; // Already exists

        const overlay = document.createElement('div');
        overlay.className = 'ascii-play-overlay';
        overlay.style.cssText = [
            'position:absolute', 'inset:0', 'display:flex',
            'align-items:center', 'justify-content:center',
            'cursor:pointer', 'z-index:10',
            'background:rgba(0,0,0,0.45)',
            'transition:opacity 0.15s ease',
            'user-select:none', '-webkit-user-select:none',
        ].join(';');

        const btn = document.createElement('div');
        btn.className = 'ascii-play-btn';
        btn.innerHTML = '&#9654;'; // ▶
        btn.style.cssText = [
            'width:72px', 'height:72px', 'border-radius:50%',
            'background:rgba(255,255,255,0.18)',
            'border:3px solid rgba(255,255,255,0.7)',
            'display:flex', 'align-items:center', 'justify-content:center',
            'font-size:28px', 'color:#fff',
            'padding-left:5px', // optical center for ▶
            'pointer-events:none',
            'transition:transform 0.1s ease, background 0.1s ease',
        ].join(';');

        overlay.addEventListener('mouseenter', () => {
            btn.style.background = 'rgba(255,255,255,0.30)';
            btn.style.transform = 'scale(1.08)';
        });
        overlay.addEventListener('mouseleave', () => {
            btn.style.background = 'rgba(255,255,255,0.18)';
            btn.style.transform = 'scale(1)';
        });

        overlay.appendChild(btn);
        overlay.addEventListener('click', () => this.play(), { once: true });

        // Ensure container is positioned
        if (getComputedStyle(this.container).position === 'static') {
            this.container.style.position = 'relative';
        }

        this.container.appendChild(overlay);
        this._playOverlay = overlay;
    }

    _hidePlayOverlay() {
        if (!this._playOverlay) return;
        this._playOverlay.remove();
        this._playOverlay = null;
        if (this._muteBtn) {
            this._muteBtn.style.display = 'flex';
        }
    }

    // ── PAUSE OVERLAY (Minimalist Center Indicator) ──

    _showPauseOverlay() {
        if (this._pauseOverlay) {
            this._pauseOverlay.style.display = 'flex';
            return;
        }

        const overlay = document.createElement('div');
        overlay.className = 'ascii-pause-overlay';
        overlay.style.cssText = [
            'position:absolute', 'inset:0', 'display:flex',
            'align-items:center', 'justify-content:center',
            'cursor:pointer', 'z-index:15',
            'background:rgba(0,0,0,0.38)',
            'backdrop-filter:blur(3px)',
            '-webkit-backdrop-filter:blur(3px)',
            'transition:opacity 0.15s ease',
            'user-select:none', '-webkit-user-select:none',
        ].join(';');

        const btn = document.createElement('div');
        btn.className = 'ascii-pause-btn';
        btn.innerHTML = '<svg width="28" height="28" viewBox="0 0 24 24" fill="#ffffff" style="margin-left:3px;display:block;"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>';
        btn.style.cssText = [
            'width:64px', 'height:64px', 'border-radius:50%',
            'background:rgba(20,20,20,0.68)',
            'border:2px solid rgba(255,255,255,0.65)',
            'display:flex', 'align-items:center', 'justify-content:center',
            'pointer-events:none',
            'box-shadow:0 6px 20px rgba(0,0,0,0.6)',
            'transition:transform 0.1s ease, background 0.1s ease',
            'backdrop-filter:blur(8px)',
            '-webkit-backdrop-filter:blur(8px)',
        ].join(';');

        overlay.addEventListener('mouseenter', () => {
            btn.style.background = 'rgba(30,30,30,0.88)';
            btn.style.transform = 'scale(1.08)';
        });
        overlay.addEventListener('mouseleave', () => {
            btn.style.background = 'rgba(20,20,20,0.68)';
            btn.style.transform = 'scale(1)';
        });

        overlay.appendChild(btn);
        overlay.addEventListener('click', (e) => {
            e.stopPropagation();
            this.resume();
        });

        if (getComputedStyle(this.container).position === 'static') {
            this.container.style.position = 'relative';
        }

        this.container.appendChild(overlay);
        this._pauseOverlay = overlay;
    }

    _hidePauseOverlay() {
        if (!this._pauseOverlay) return;
        this._pauseOverlay.style.display = 'none';
    }

    _removePauseOverlay() {
        if (!this._pauseOverlay) return;
        this._pauseOverlay.remove();
        this._pauseOverlay = null;
    }

    // ── MUTE BUTTON (Sleek Video Player Vector UI) ──

    _createMuteButton() {
        if (this._muteBtn) return;
        if (!this.options.muteButton || !this.audioEl) return;

        const SVG_MUTED = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:block"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><line x1="23" y1="9" x2="17" y2="15"></line><line x1="17" y1="9" x2="23" y2="15"></line></svg>';
        const SVG_UNMUTED = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:block"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"></path></svg>';

        this._svgMuted = SVG_MUTED;
        this._svgUnmuted = SVG_UNMUTED;

        const btn = document.createElement('button');
        btn.className = 'ascii-mute-btn';
        btn.setAttribute('aria-label', 'Unmute');
        btn.innerHTML = SVG_MUTED;
        btn.style.cssText = [
            'position:absolute',
            'bottom:20px',
            'right:20px',
            'z-index:9999',
            'width:40px',
            'height:40px',
            'border-radius:50%',
            'border:1px solid rgba(255,255,255,0.2)',
            'background:rgba(18,18,18,0.72)',
            'color:#ffffff',
            'display:' + (this._playOverlay ? 'none' : 'flex'),
            'align-items:center',
            'justify-content:center',
            'cursor:pointer',
            'user-select:none',
            '-webkit-user-select:none',
            'transition:transform 0.15s ease, background 0.15s ease, border-color 0.15s ease',
            'backdrop-filter:blur(8px)',
            '-webkit-backdrop-filter:blur(8px)',
            'box-shadow:0 4px 14px rgba(0,0,0,0.5)',
            'padding:0',
            'outline:none',
        ].join(';');

        btn.addEventListener('mouseenter', () => {
            btn.style.background = 'rgba(30,30,30,0.92)';
            btn.style.borderColor = 'rgba(255,255,255,0.4)';
            btn.style.transform = 'scale(1.08)';
        });
        btn.addEventListener('mouseleave', () => {
            btn.style.background = 'rgba(18,18,18,0.72)';
            btn.style.borderColor = 'rgba(255,255,255,0.2)';
            btn.style.transform = 'scale(1)';
        });

        btn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (!this.audioEl) return;
            const isMuted = Boolean(
                this._audioGated ||
                this.audioEl.muted ||
                this.audioEl.volume === 0
            );
            if (isMuted) {
                this.unmute().catch(() => {});
            } else {
                this.mute();
            }
        });

        // Ensure container is positioned
        if (getComputedStyle(this.container).position === 'static') {
            this.container.style.position = 'relative';
        }

        this.container.appendChild(btn);
        this._muteBtn = btn;
        this._updateMuteButton();
    }

    _updateMuteButton() {
        if (!this._muteBtn) return;
        const isMuted = Boolean(
            this._audioGated ||
            !this.audioEl ||
            this.audioEl.muted ||
            this.audioEl.volume === 0
        );
        this._muteBtn.innerHTML = isMuted ? this._svgMuted : this._svgUnmuted;
        this._muteBtn.setAttribute('aria-label', isMuted ? 'Unmute' : 'Mute');
    }

    _removeMuteButton() {
        if (!this._muteBtn) return;
        this._muteBtn.remove();
        this._muteBtn = null;
    }

    destroy() {
        this._hidePlayOverlay();
        this._removePauseOverlay();
        this._removeMuteButton();
        this._stopBufferReports();
        if (this._containerClickHandler && this.container) {
            this.container.removeEventListener('click', this._containerClickHandler);
            this._containerClickHandler = null;
        }
        if (this._keydownHandler) {
            window.removeEventListener('keydown', this._keydownHandler);
            this._keydownHandler = null;
        }
        window.removeEventListener('resize', this._resizeBound);
        // Abort any in-flight ASCF HTTP stream
        if (this._ascfAbortController) {
            this._ascfAbortController.abort();
            this._ascfAbortController = null;
            this._ascfIsStreaming = false;
        }
        if (this.ws) {
            this.ws.onclose = null;
            this.ws.close();
            this.ws = null;
        }
        if (this.audioEl) {
            this.audioEl.pause();
            this.audioEl.src = '';
        }
        this._setState('IDLE');
        this._listeners = {};
    }

    _getAudioUrl(queryString = '') {
        if (typeof this.options.audioUrl === 'string') {
            const sep = this.options.audioUrl.includes('?') ? '&' : '?';
            return queryString ? `${this.options.audioUrl}${sep}${queryString}` : this.options.audioUrl;
        }
        let base = '';
        const target = this.resolvedWsUrl || this.options.url || '';
        try {
            if (target && (target.startsWith('ws://') || target.startsWith('wss://') || target.startsWith('http://') || target.startsWith('https://'))) {
                const u = new URL(target);
                const httpProto = u.protocol === 'wss:' ? 'https:' : (u.protocol === 'ws:' ? 'http:' : u.protocol);
                base = `${httpProto}//${u.host}`;
            }
        } catch (_) {}
        const prefix = base ? `${base}/audio` : '/audio';
        return queryString ? `${prefix}?${queryString}` : prefix;
    }

    // ── INTERNAL WEBSOCKET & RENDER ──

    _connectWebSocket() {
        this.frameBuffer.length = 0;
        this.frameCount = 0;
        this.currentFps = 0;

        let wsUrl = this.options.url;
        if (!wsUrl || wsUrl === 'auto') {
            const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            wsUrl = `${protocol}//${location.host}/ws?codec=adaptive`;
        }
        // Auto-append ?codec=adaptive if the caller didn't specify it.
        // Without this flag the server sends untagged binary frames that the
        // inlined decoder cannot parse, resulting in a silent black screen.
        if (!wsUrl.includes('codec=')) {
            wsUrl += (wsUrl.includes('?') ? '&' : '?') + 'codec=adaptive';
        }
        this.resolvedWsUrl = wsUrl;

        this.ws = new WebSocket(wsUrl);
        this.ws.binaryType = 'arraybuffer';

        this.ws.onopen = () => {
            this.emit('buffering');
        };

        this.ws.onmessage = (event) => {
            if (typeof event.data === 'string') {
                if (event.data.startsWith('Error:')) {
                    this.emit('error', event.data);
                    if (this.ws) this.ws.close();
                    this._finishStream('ERROR');
                    return;
                }

                if (event.data.startsWith('INIT:')) {
                    const p = event.data.split(':');
                    this.targetFps = parseFloat(p[1]);
                    this.frameInterval = 1000 / this.targetFps;
                    this.renderMode = parseInt(p[2]);
                    this.pixelMode = (p.length > 5 && parseInt(p[5]) === 1);
                    this.currentQueueIdx = (p.length > 6) ? parseInt(p[6]) : 0;
                    this.duration = (p.length > 7) ? parseFloat(p[7]) : 0;
                    const startOffset = (p.length > 8) ? parseFloat(p[8]) : 0;
                    this.isWebcamStream = (p.length > 9 && parseInt(p[9]) === 1);

                    this.audioOffset = startOffset;
                    this.frameBuffer.length = 0;
                    this.framesInFlight = 0;
                    this.streamEpoch++;

                    this._buildCanvas(parseInt(p[3]), parseInt(p[4]));

                    if (this.renderMode > 1 && !this.pixelMode) {
                        this.codecDecoder = AscilineCodecApi.makeDecoder(4);
                    } else {
                        this.codecDecoder = null;
                    }

                    this.decodeQueue = Promise.resolve();
                    const wasPaused = (this.state === 'PAUSED');
                    this.readyToRender = false;
                    if (!wasPaused) this._setState('PLAYING');

                    if (this.audioEl && !this.isWebcamStream) {
                        this.audioEl.pause();
                        const qs = `v=${this.currentQueueIdx}&`;
                        const st = startOffset > 0 ? `start=${startOffset}&` : '';
                        this.audioEl.src = this._getAudioUrl(`${qs}${st}t=${Date.now()}`);
                        this.audioEl.load();
                    }

                    this.setFilters(this.currentFilters);
                    this.emit('init', {
                        fps: this.targetFps,
                        cols: parseInt(p[3]),
                        rows: parseInt(p[4]),
                        duration: this.duration,
                        pixelMode: this.pixelMode,
                        renderMode: this.renderMode,
                        queueIdx: this.currentQueueIdx,
                        isWebcam: this.isWebcamStream
                    });
                    return;
                }

                // Text Frame (Mode 1)
                const text = event.data;
                const newlineIdx = text.indexOf('\n');
                const frameIndex = parseInt(text.substring(0, newlineIdx));
                const frameTime = frameIndex / this.targetFps;
                const frameData = text.substring(newlineIdx + 1);
                this.frameBuffer.push({ data: frameData, time: frameTime });
                this._triggerPlaybackStart(this.streamEpoch);
            } else {
                // Binary Frame
                if (this.codecDecoder) {
                    this.framesInFlight++;
                    this.decodeQueue = this.decodeQueue.then(() =>
                        this.codecDecoder.decode(event.data).then(({ frameIndex, frame }) => {
                            this.framesInFlight--;
                            const frameTime = frameIndex / this.targetFps;
                            this.frameBuffer.push({ data: frame, time: frameTime });
                            this._triggerPlaybackStart(this.streamEpoch);
                        }).catch(e => {
                            this.framesInFlight--;
                            console.error("Decode error", e);
                        })
                    );
                } else {
                    const buffer = event.data;
                    const view = new DataView(buffer);
                    const frameIndex = view.getUint32(0, false);
                    const frameTime = frameIndex / this.targetFps;
                    const frameData = new Uint8Array(buffer, 4);
                    this.frameBuffer.push({ data: frameData, time: frameTime });
                    this._triggerPlaybackStart(this.streamEpoch);
                }
            }

            while (this.frameBuffer.length > this.options.bufferSize * 5) {
                this.frameBuffer.shift();
            }
        };

        this.ws.onclose = (event) => {
            const endedCleanly = event.code === 1000;
            this._finishStream(endedCleanly ? 'ENDED' : 'ERROR');
        };

        this.ws.onerror = (e) => {
            this.emit('error', e);
            this._finishStream('ERROR');
        };
    }

    // ── STATIC ASCF FILE PLAYBACK ──

    /**
     * Fetches and plays a pre-compiled .ascf file without any backend server.
     * Streams the file via HTTP (or from a local URL), parses the binary header
     * and frame packets, then feeds them into the same render pipeline used for
     * live WebSocket streams — including audio sync, codec decoding, and loop.
     *
     * @param {string} ascfUrl  - URL of the .ascf file
     * @param {string|null} audioUrl - Optional URL of a paired .mp3 audio file
     */
    async _connectAscf(ascfUrl, audioUrl) {
        this.frameBuffer.length = 0;
        this.framesInFlight = 0;
        this.frameCount = 0;
        this.currentFps = 0;
        this.streamEpoch++;
        this._ascfIsStreaming = true;
        this._ascfSrc   = ascfUrl;
        this._ascfAudio = audioUrl;
        this._ascfAbortController = new AbortController();

        const textDec = new TextDecoder();

        try {
            const response = await fetch(ascfUrl, { signal: this._ascfAbortController.signal });
            if (!response.ok) {
                this.emit('error', `ASCF fetch failed: HTTP ${response.status}`);
                this._finishStream('ERROR');
                return;
            }

            // Prepare audio element with the static file (same as WS path)
            if (audioUrl && this.audioEl) {
                this.audioEl.pause();
                this.audioEl.src = audioUrl;
                this.audioEl.currentTime = 0;
                this.audioEl.load();
            }

            const reader   = response.body.getReader();
            const parser   = new _ByteStreamParser();
            const myEpoch  = this.streamEpoch;
            let headerParsed = false;

            while (this._ascfIsStreaming) {
                // Back-pressure: pause reading if buffer is full
                if ((this.frameBuffer.length + this.framesInFlight) >= 90) {
                    await new Promise(r => setTimeout(r, 50));
                    if (!this._ascfIsStreaming || myEpoch !== this.streamEpoch) return;
                    continue;
                }

                const { done, value } = await reader.read();
                if (value) parser.push(value);
                if (myEpoch !== this.streamEpoch) return; // superseded

                // ── Parse header once we have 18 bytes ──
                if (!headerParsed && parser.length >= 18) {
                    const headerBytes = parser.read(18);
                    const magic = textDec.decode(headerBytes.subarray(0, 4));
                    if (magic !== 'ASCF' && magic !== 'ASC2') {
                        this.emit('error', 'Invalid ASCF file: bad magic');
                        this._finishStream('ERROR');
                        return;
                    }

                    const hv = new DataView(headerBytes.buffer, headerBytes.byteOffset, headerBytes.byteLength);
                    this.targetFps   = hv.getFloat32(4, false);
                    this.frameInterval = 1000 / this.targetFps;
                    this.renderMode  = hv.getUint8(8);
                    this.pixelMode   = hv.getUint8(9) === 1;
                    const cols       = hv.getUint16(10, false);
                    const rows       = hv.getUint16(12, false);

                    if (magic === 'ASC2') {
                        const totalFrames = hv.getUint32(14, false);
                        this.duration = totalFrames > 0 ? totalFrames / this.targetFps : 0;
                    } else {
                        // Legacy ASCF (14-byte header): push extra 4 bytes back as frame data
                        this.duration = 0;
                        parser.unshift(headerBytes.subarray(14, 18));
                    }

                    this._buildCanvas(cols, rows);

                    // Codec decoder: 4 bytes/cell (char+RGB) for ASCII, 3 bytes/cell (BGR) for pixel
                    if (this.renderMode > 1) {
                        this.codecDecoder = AscilineCodecApi.makeDecoder(this.pixelMode ? 3 : 4);
                    } else {
                        this.codecDecoder = null;
                    }
                    this.decodeQueue = Promise.resolve();

                    this._setState('PLAYING');
                    this.emit('init', {
                        fps: this.targetFps, cols, rows,
                        duration: this.duration,
                        pixelMode: this.pixelMode,
                        renderMode: this.renderMode,
                        isWebcam: false
                    });

                    // Start render pipeline (audio-gated if audio is present)
                    if (audioUrl && this.audioEl) {
                        this._triggerPlaybackStart(myEpoch);
                    } else {
                        this._beginRendering();
                    }

                    headerParsed = true;
                }

                // ── Parse frame packets: [uint32 length][payload] ──
                if (headerParsed) {
                    while (parser.length >= 4) {
                        const frameLen = parser.peek32BE();
                        if (frameLen === null || parser.length < 4 + frameLen) break;

                        parser.read(4); // consume length prefix
                        const frameBytes = parser.read(frameLen);

                        if (this.renderMode === 1) {
                            // Mode 1: text frame — "{frameIndex}\n{ascii lines}"
                            const text = textDec.decode(frameBytes);
                            const nl   = text.indexOf('\n');
                            const frameIndex = parseInt(text.substring(0, nl));
                            this.frameBuffer.push({ data: text.substring(nl + 1), time: frameIndex / this.targetFps });
                        } else if (this.codecDecoder) {
                            // Binary codec frame — decode asynchronously to avoid blocking rAF
                            const buf = frameBytes.buffer; // owned copy from _ByteStreamParser.read()
                            this.framesInFlight++;
                            this.decodeQueue = this.decodeQueue.then(async () => {
                                await new Promise(r => setTimeout(r, 0)); // yield to rAF
                                try {
                                    const { frameIndex, frame } = await this.codecDecoder.decode(buf);
                                    if (myEpoch === this.streamEpoch) {
                                        this.frameBuffer.push({ data: frame, time: frameIndex / this.targetFps });
                                    }
                                } finally {
                                    this.framesInFlight--;
                                }
                            });
                        }
                    }
                }

                if (done) {
                    this._ascfIsStreaming = false;
                    break;
                }
            }

            // Wait for in-flight codec decodes to complete
            await this.decodeQueue;
            if (myEpoch !== this.streamEpoch) return; // superseded by seek/restart

            if (this.options.loop) {
                // Loop: reset decoder and replay from the beginning
                if (this.codecDecoder && this.codecDecoder.reset) this.codecDecoder.reset();
                this.readyToRender = false;
                this._ascfAbortController = null;
                this._connectAscf(ascfUrl, audioUrl);
            } else {
                // Wait for the render queue to drain before calling ENDED
                const drain = () => new Promise(resolve => {
                    const check = () => (this.frameBuffer.length === 0 && this.framesInFlight === 0)
                        ? resolve()
                        : setTimeout(check, 50);
                    check();
                });
                await drain();
                if (myEpoch === this.streamEpoch) this._finishStream('ENDED');
            }

        } catch (err) {
            if (err && err.name !== 'AbortError') {
                this.emit('error', err);
                this._finishStream('ERROR');
            }
        }
    }


    _triggerPlaybackStart(epochToMatch) {
        if (this.readyToRender || this.state !== 'PLAYING') return;
        if (this.isWebcamStream || !this.audioEl) {
            this._beginRendering();
            return;
        }

        // Try to start audio. If play() was called from a user gesture (▶ button),
        // the browser grants permission and audio starts in sync with video (t=0).
        // If blocked by autoplay policy, we set _audioGated=true so getMasterClock()
        // falls back to wall-clock — video plays freely until user triggers unmute().
        this._audioGated = false;
        this.audioEl.play().then(() => {
            this._audioGated = false;
            if (epochToMatch === this.streamEpoch && !this.readyToRender) {
                this._beginRendering();
            }
        }).catch(() => {
            // Autoplay blocked — set flag so getMasterClock uses wall-clock.
            // Video plays freely on wall-clock. Show the Instagram-style mute button
            // so the user gets a clear, visible affordance to enable audio.
            this._audioGated = true;
            this._createMuteButton();
            this._updateMuteButton();
            if (epochToMatch === this.streamEpoch && !this.readyToRender) {
                this._beginRendering();
            }
        });

        // Safety fallback: if audio hasn't resolved play/reject after 300ms, start anyway
        setTimeout(() => {
            if (epochToMatch === this.streamEpoch && !this.readyToRender) {
                this._beginRendering();
            }
        }, 300);
    }

    _beginRendering() {
        if (this.readyToRender) return;
        this.readyToRender = true;
        this.streamStartTime = performance.now() - (this.audioOffset * 1000.0);
        this.lastRenderTime = performance.now();
        this.lastFpsUpdate = this.lastRenderTime;
        requestAnimationFrame(this._renderBound);
        this._startBufferReports();
    }

    _startBufferReports() {
        this._stopBufferReports();
        this.bufferReportTimer = setInterval(() => {
            if (this.ws && this.ws.readyState === WebSocket.OPEN && this.state === 'PLAYING') {
                this.ws.send(JSON.stringify({ type: 'buffer', depth: this.framesInFlight }));
            }
        }, 250);
    }

    _stopBufferReports() {
        if (this.bufferReportTimer) {
            clearInterval(this.bufferReportTimer);
            this.bufferReportTimer = null;
        }
    }

    _buildCanvas(cols, rows) {
        this.gridCols = cols;
        this.gridRows = rows;
        this.canvas.style.display = 'block';
        this.canvas.style.position = 'absolute';
        this.canvas.style.top = '0';
        this.canvas.style.left = '0';

        if (this.pixelMode) {
            this.canvas.width = cols;
            this.canvas.height = rows;
            this.canvas.style.imageRendering = 'pixelated';
            this.dotImageData = this.ctx.createImageData(cols, rows);
            const d = this.dotImageData.data;
            for (let i = 3; i < d.length; i += 4) d[i] = 255;
            if (this.selectionLayer) this.selectionLayer.style.display = 'none';
        } else {
            this.canvas.style.imageRendering = '';
            this.dotImageData = null;
            this.ctx.font = 'bold 8px Courier New';
            this.charWidth = this.ctx.measureText('M').width;
            this.charHeight = 8;
            this.canvas.width = cols * this.charWidth;
            this.canvas.height = rows * this.charHeight;

            if (this.selectionLayer) {
                this.selectionBuffer = new Uint8Array((cols + 1) * rows);
                for (let r = 0; r < rows; r++) this.selectionBuffer[r * (cols + 1) + cols] = 10;
            }

            this.ctx.font = 'bold 8px Courier New';
            this.ctx.textBaseline = 'top';
            this.xPos = new Float32Array(cols);
            this.yPos = new Float32Array(rows);
            for (let c = 0; c < cols; c++) this.xPos[c] = c * this.charWidth;
            for (let r = 0; r < rows; r++) this.yPos[r] = r * this.charHeight;
        }

        this.resize();
        if (this.options.muteButton && this.audioEl && !this.isWebcamStream) {
            this._createMuteButton();
        }
    }

    resize() {
        const containerW = this.container.clientWidth || window.innerWidth;
        const containerH = this.container.clientHeight || window.innerHeight;

        this.canvas.style.width = containerW + 'px';
        this.canvas.style.height = containerH + 'px';
        this.canvas.style.objectFit = 'contain';

        if (this.selectionLayer && !this.pixelMode && this.canvas.width > 0) {
            const fitScaleX = containerW / this.canvas.width;
            const fitScaleY = containerH / this.canvas.height;
            const fitScale = Math.min(fitScaleX, fitScaleY);
            const renderedW = this.canvas.width * fitScale;
            const renderedH = this.canvas.height * fitScale;
            const offsetX = (containerW - renderedW) / 2;
            const offsetY = (containerH - renderedH) / 2;

            this.selectionLayer.style.width = this.canvas.width + 'px';
            this.selectionLayer.style.height = this.canvas.height + 'px';
            this.selectionLayer.style.position = 'absolute';
            this.selectionLayer.style.top = '0';
            this.selectionLayer.style.left = '0';
            this.selectionLayer.style.transformOrigin = 'top left';
            this.selectionLayer.style.transform = `translate(${offsetX}px, ${offsetY}px) scale(${fitScale})`;
            this.selectionLayer.style.fontSize = '8px';
            this.selectionLayer.style.lineHeight = '8px';
        }
    }

    _renderFrame(now) {
        if (this.state !== 'PLAYING' || !this.readyToRender) return;
        requestAnimationFrame(this._renderBound);

        const masterClock = this.getMasterClock();

        // Throttle timeupdate to ~100ms to avoid flooding UI listeners
        if (now - this._lastUiUpdate >= 100) {
            this._lastUiUpdate = now;
            this.emit('timeupdate', masterClock);
        }

        if (this.frameBuffer.length === 0) return;

        let frameObj;
        if (this.isWebcamStream) {
            frameObj = this.frameBuffer.pop();
            this.frameBuffer.length = 0;
        } else {
            while (this.frameBuffer.length > 0 && this.frameBuffer[0].time < masterClock - 0.1) {
                this.frameBuffer.shift();
            }
            if (this.frameBuffer.length === 0) return;
            if (this.frameBuffer[0].time > masterClock + 0.05) return;
            frameObj = this.frameBuffer.shift();
        }

        const frame = frameObj.data;

        this.frameCount++;
        if (now - this.lastFpsUpdate >= 1000) {
            this.currentFps = this.frameCount;
            this.frameCount = 0;
            this.lastFpsUpdate = now;
            this.emit('fps', {
                fps: this.currentFps,
                targetFps: this.targetFps,
                buffered: this.frameBuffer.length,
                mode: this.renderMode,
                pixel: this.pixelMode
            });
        }

        this.lastRenderTime = now;

        if (this.pixelMode) {
            const view = frame;
            const data = this.dotImageData.data;
            for (let src = 0, dst = 0; src < view.length; src += 3, dst += 4) {
                data[dst] = view[src + 2];     // R (from BGR)
                data[dst + 1] = view[src + 1]; // G
                data[dst + 2] = view[src];     // B
            }
            this.ctx.putImageData(this.dotImageData, 0, 0);
        } else if (this.renderMode === 1) {
            // Mode 1: Black & White Plain ASCII Text
            this.ctx.fillStyle = '#050505';
            this.ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
            this.ctx.fillStyle = '#ffffff';
            this.ctx.font = 'bold 8px Courier New';
            this.ctx.textBaseline = 'top';

            if (typeof frame === 'string') {
                const lines = frame.split('\n');
                for (let r = 0; r < lines.length && r < this.gridRows; r++) {
                    const y = (this.yPos && this.yPos[r] !== undefined) ? this.yPos[r] : r * this.charHeight;
                    this.ctx.fillText(lines[r], 0, y);
                }
            }

            if (this.selectionLayer) {
                if (this.selectionEnabled) {
                    this.selectionLayer.style.display = 'block';
                    this.selectionLayer.style.color = 'transparent';
                    this.selectionLayer.textContent = frame;
                } else {
                    this.selectionLayer.style.display = 'none';
                }
            }
        } else {
            const view = frame;
            this.ctx.fillStyle = '#050505';
            this.ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
            this.ctx.font = 'bold 8px Courier New';
            this.ctx.textBaseline = 'top';

            let col = 0, row = 0, prevPacked = -1;
            for (let idx = 0; idx < view.length; idx += 4) {
                const packed = (view[idx+1] << 16) | (view[idx+2] << 8) | view[idx+3];
                if (packed !== prevPacked) {
                    this.ctx.fillStyle = `rgb(${view[idx+1]},${view[idx+2]},${view[idx+3]})`;
                    prevPacked = packed;
                }
                this.ctx.fillText(CHAR_LUT[view[idx]], this.xPos[col], this.yPos[row]);

                if (this.selectionBuffer) {
                    this.selectionBuffer[row * (this.gridCols + 1) + col] = view[idx];
                }

                col++;
                if (col >= this.gridCols) { col = 0; row++; }
            }

            if (this.selectionLayer && this.selectionBuffer) {
                if (this.selectionEnabled) {
                    this.selectionLayer.style.display = 'block';
                    this.selectionLayer.style.color = 'transparent';
                    this.selectionLayer.textContent = this.textDecoder.decode(this.selectionBuffer);
                } else {
                    this.selectionLayer.style.display = 'none';
                }
            }
        }
    }

    _finishStream(finalState = 'IDLE') {
        this._setState(finalState);
        this._stopBufferReports();
        if (this.ws) {
            this.ws.onclose = null;
            this.ws.close();
            this.ws = null;
        }
        if (this.audioEl) {
            this.audioEl.pause();
            this.audioEl.src = '';
        }
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this.canvas.style.display = 'none';
        this._hidePauseOverlay();
        if (this.selectionLayer) {
            this.selectionLayer.textContent = '';
            this.selectionLayer.style.display = 'none';
        }
        this.readyToRender = false;
        this.pauseStartTime = 0;
        this.frameBuffer.length = 0;
    }
}

// Support CommonJS and window global
if (typeof window !== 'undefined') {
    window.AsciiPlayer = AsciiPlayer;
}

export default AsciiPlayer;
