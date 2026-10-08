/* ascf-player — bundled global build. Defines <ascf-player>. */
(function () {
/**
 * decoder.js — DOM-free ASCILINE .ascf decoder.
 *
 * No document, no canvas, no fetch: give it a ReadableStream<Uint8Array> (or a
 * Uint8Array) and it hands back the header plus an async iterator of frames.
 * Runs in a browser, a worker, or Node 18+.
 *
 * Container:
 *   header 18B  'ASC2' | fps f32 | mode u8 | pixel u8 | cols u16 | rows u16 | totalFrames u32  (all BE)
 *   header 14B  'ASCF' — legacy, no totalFrames
 *   then per frame: [len u32 BE][message]
 *
 * Message (mode > 1):
 *   [frameIndex u32 BE][tag u8][payload]
 *   0 RAW   raw framebuffer
 *   1 ZLIB  deflate(framebuffer)
 *   2 DELTA deflate(indices u32 LE ++ changed cells) patched onto previous frame
 *   3 RLE   deflate([count u16 LE][cell]...)
 * Mode 1 is plain text: "frameIndex\n" + the ASCII art.
 *
 * Frames must be decoded in order — DELTA patches the previous frame.
 */

const TAG_RAW = 0, TAG_ZLIB = 1, TAG_DELTA = 2, TAG_RLE_FULL = 3, TAG_PROFILE = 4;

const td = new TextDecoder();

async function inflate(bytes) {
  const ds = new DecompressionStream('deflate');
  const w = ds.writable.getWriter();
  w.write(bytes);
  w.close();
  const reader = ds.readable.getReader();
  const chunks = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    total += value.length;
  }
  if (chunks.length === 1) return chunks[0];
  const out = new Uint8Array(total);
  let off = 0;
  for (const c of chunks) { out.set(c, off); off += c.length; }
  return out;
}

function parseHeader(bytes) {
  const magic = String.fromCharCode(bytes[0], bytes[1], bytes[2], bytes[3]);
  if (magic !== 'ASC2' && magic !== 'ASCF') throw new Error('not an .ascf file (bad magic)');
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const h = {
    fps: dv.getFloat32(4, false),
    renderMode: bytes[8],
    pixelMode: bytes[9] === 1,
    cols: dv.getUint16(10, false),
    rows: dv.getUint16(12, false),
    totalFrames: magic === 'ASC2' ? dv.getUint32(14, false) : 0,
    headerSize: magic === 'ASC2' ? 18 : 14,
  };
  h.cellBytes = h.pixelMode ? 3 : 4;
  h.duration = h.totalFrames / h.fps || 0;
  return h;
}

/** Stateful frame decoder. cellBytes: 3 pixel (BGR), 4 ASCII ([char,R,G,B]). */
function makeDecoder(cellBytes) {
  let prev = null;

  async function decode(message) {
    const bytes = message instanceof Uint8Array ? message : new Uint8Array(message);
    const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const frameIndex = dv.getUint32(0, false);
    const tag = bytes[4];
    const payload = bytes.subarray(5);
    let frame;

    if (tag === TAG_RAW) {
      frame = payload.slice();
    } else if (tag === TAG_ZLIB) {
      frame = await inflate(payload);
    } else if (tag === TAG_DELTA) {
      if (!prev) throw new Error('DELTA frame before any keyframe');
      const body = await inflate(payload);
      const n = body.length / (4 + cellBytes);
      const idx = new DataView(body.buffer, body.byteOffset, body.byteLength);
      frame = prev.slice();
      const valuesOffset = n * 4;
      for (let j = 0; j < n; j++) {
        const dst = idx.getUint32(j * 4, true) * cellBytes;
        const src = valuesOffset + j * cellBytes;
        for (let c = 0; c < cellBytes; c++) frame[dst + c] = body[src + c];
      }
    } else if (tag === TAG_RLE_FULL) {
      const body = await inflate(payload);
      const bv = new DataView(body.buffer, body.byteOffset, body.byteLength);
      const stride = 2 + cellBytes;
      let cells = 0;
      for (let off = 0; off < body.length; off += stride) cells += bv.getUint16(off, true);
      frame = new Uint8Array(cells * cellBytes);
      let dst = 0;
      for (let off = 0; off < body.length; off += stride) {
        const count = bv.getUint16(off, true);
        const v = off + 2;
        for (let i = 0; i < count; i++) for (let c = 0; c < cellBytes; c++) frame[dst++] = body[v + c];
      }
    } else if (tag === TAG_PROFILE) {
      // ponytail: lossy DCT profile frames unsupported — repeat last frame rather
      // than die. Port _pDecodePlane from ASCILINE's codec.js if you compile with --profile.
      if (prev) return { frameIndex, frame: prev };
      throw new Error('PROFILE (tag 4) frames are not supported by this decoder');
    } else {
      throw new Error('unknown ASCILINE codec tag: ' + tag);
    }

    prev = frame;
    return { frameIndex, frame };
  }

  return { decode, reset() { prev = null; } };
}

function concat(a, b) {
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

/**
 * Open a .ascf stream.
 * @param {ReadableStream<Uint8Array>|Uint8Array|ArrayBuffer} source
 * @returns header fields plus `frames()` — an async generator of
 *          `{index, time, data}` (data = full framebuffer) or `{index, time, text}`
 *          for render mode 1 — and `cancel()`.
 */
async function openAscf(source) {
  let buf = new Uint8Array(0);
  let done = false;
  let reader = null;

  if (source instanceof Uint8Array || source instanceof ArrayBuffer) {
    buf = source instanceof Uint8Array ? source : new Uint8Array(source);
    done = true;
  } else {
    reader = source.getReader();
  }
  const pull = async () => {
    if (done || !reader) return true;
    const { done: d, value } = await reader.read();
    if (value) buf = concat(buf, value);
    return d;
  };
  const need = async (n) => {
    while (buf.length < n && !done) done = await pull();
    return buf.length >= n;
  };

  if (!(await need(18)) && buf.length < 14) throw new Error('truncated .ascf header');
  const header = parseHeader(buf);
  buf = buf.subarray(header.headerSize);
  const dec = makeDecoder(header.cellBytes);

  async function* frames() {
    for (;;) {
      if (!(await need(4))) return;
      const len = new DataView(buf.buffer, buf.byteOffset, 4).getUint32(0, false);
      if (!(await need(4 + len))) return;
      const msg = buf.subarray(4, 4 + len);
      buf = buf.subarray(4 + len);

      if (header.renderMode === 1) {
        const s = td.decode(msg);
        const nl = s.indexOf('\n');
        const index = parseInt(s.slice(0, nl), 10);
        yield { index, time: index / header.fps, text: s.slice(nl + 1) };
      } else {
        const { frameIndex, frame } = await dec.decode(msg);
        yield { index: frameIndex, time: frameIndex / header.fps, data: frame };
      }
    }
  }

  return { ...header, frames, cancel: () => reader && reader.cancel().catch(() => {}) };
}

/**
 * <ascf-player> — plays ASCILINE .ascf clips on a canvas.
 *
 *   <ascf-player src="clip.ascf" audio="clip.mp3" controls loop autoplay></ascf-player>
 *
 * Decoding lives in ./decoder.js (DOM-free); this file only fetches, paints and
 * exposes the media API. Visuals autoplay freely — it's a canvas, not <video> —
 * audio still needs a user gesture, so a blocked audio track leaves the picture
 * running and flags the unmute button.
 */

const TEMPLATE = `
<style>
  :host {
    display: block;
    position: relative;
    background: var(--ascf-background, #050505);
    border-radius: var(--ascf-radius, 0);
    overflow: hidden;
    color: var(--ascf-color, #d0d0d0);
    font-family: var(--ascf-ui-font, system-ui, sans-serif);
  }
  :host([hidden]) { display: none; }
  [hidden] { display: none !important; }   /* all:unset below would otherwise revive hidden buttons */
  canvas {
    display: block;
    width: 100%;
    height: 100%;
    object-fit: var(--ascf-fit, contain);
    image-rendering: var(--ascf-image-rendering, pixelated);
  }
  .controls {
    position: absolute;
    inset: auto 0 0 0;
    display: flex;
    align-items: center;
    gap: .5rem;
    padding: var(--ascf-controls-padding, .4rem .6rem);
    background: var(--ascf-controls-background, linear-gradient(transparent, rgba(0,0,0,.65)));
    color: var(--ascf-controls-color, currentColor);
    font: var(--ascf-controls-font, 500 12px/1 ui-monospace, monospace);
    opacity: var(--ascf-controls-opacity, 1);
    transition: opacity .2s;
  }
  .controls[hidden] { display: none; }
  button {
    all: unset;
    cursor: pointer;
    padding: 0 .25rem;
    line-height: 1;
    font-size: var(--ascf-button-size, 14px);
  }
  button:focus-visible { outline: 2px solid var(--ascf-accent, #4af); }
  input[type=range] {
    flex: 1;
    min-width: 3rem;
    accent-color: var(--ascf-accent, #4af);
    cursor: pointer;
  }
  .time { font-variant-numeric: tabular-nums; white-space: nowrap; }
</style>
<canvas part="canvas"></canvas>
<div class="controls" part="controls" hidden>
  <button part="button play" class="pp" aria-label="Play">&#9654;</button>
  <input part="seek" class="seek" type="range" min="0" max="1" step="0.01" value="0" aria-label="Seek">
  <span part="time" class="time">0:00</span>
  <button part="button mute" class="mute" aria-label="Mute" hidden>&#128266;</button>
</div>
<audio class="audio" hidden></audio>
`;

const fmt = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`;

class AscfPlayer extends HTMLElement {
  static observedAttributes = ['src', 'audio', 'controls', 'muted', 'loop'];

  #root; #canvas; #ctx; #audioEl; #ui;
  #header = null;
  #buf = [];
  #streamDone = true;
  #abort = null;
  #raf = 0;
  #paused = true;
  #base = 0;          // clock value when the current play run started
  #t0 = 0;            // performance.now() at that moment
  #time = 0;
  #skipUntil = 0;     // frames before this are decoded but not shown (seek)
  #audioBlocked = false;
  #lastEmit = 0;
  #lastTick = 0;
  #starting = null;
  #imageData = null;
  #charW = 0;
  #font = '';

  constructor() {
    super();
    this.#root = this.attachShadow({ mode: 'open' });
    this.#root.innerHTML = TEMPLATE;
    this.#canvas = this.#root.querySelector('canvas');
    this.#ctx = this.#canvas.getContext('2d');
    this.#audioEl = this.#root.querySelector('.audio');
    this.#ui = {
      bar: this.#root.querySelector('.controls'),
      pp: this.#root.querySelector('.pp'),
      seek: this.#root.querySelector('.seek'),
      time: this.#root.querySelector('.time'),
      mute: this.#root.querySelector('.mute'),
    };

    this.#ui.pp.addEventListener('click', () => (this.paused ? this.play() : this.pause()));
    this.#ui.mute.addEventListener('click', () => { this.muted = !this.muted; });
    this.#ui.seek.addEventListener('input', (e) => {
      if (this.duration) this.currentTime = Number(e.target.value) * this.duration;
    });
    // A click anywhere is the gesture that lets blocked audio start.
    this.addEventListener('click', (e) => {
      if (this.#audioBlocked) this.#startAudio();
      else if (!e.composedPath().includes(this.#ui.bar) && !this.hasAttribute('controls')) {
        this.paused ? this.play() : this.pause();
      }
    });
  }

  connectedCallback() {
    this.#ui.bar.hidden = !this.hasAttribute('controls');
    if (this.src && this.hasAttribute('autoplay')) this.play();
  }

  disconnectedCallback() { this.#stop(); }

  attributeChangedCallback(name, old, val) {
    if (old === val) return;
    if (name === 'src') {
      this.#stop();
      this.#header = null;
      this.#time = 0;
      if (this.isConnected && this.hasAttribute('autoplay')) this.play();
    }
    if (name === 'audio') this.#audioEl.src = val || '';
    if (name === 'controls') this.#ui.bar.hidden = val === null;
    if (name === 'muted') this.#audioEl.muted = val !== null;
  }

  // ---- media API ----
  get src() { return this.getAttribute('src'); }
  set src(v) { this.setAttribute('src', v); }
  get audio() { return this.getAttribute('audio'); }
  set audio(v) { v == null ? this.removeAttribute('audio') : this.setAttribute('audio', v); }
  get loop() { return this.hasAttribute('loop'); }
  set loop(v) { this.toggleAttribute('loop', !!v); }
  get muted() { return this.hasAttribute('muted'); }
  set muted(v) { this.toggleAttribute('muted', !!v); this.#syncUI(); }
  get volume() { return this.#audioEl.volume; }
  set volume(v) { this.#audioEl.volume = v; }
  get paused() { return this.#paused; }
  get duration() { return this.#header ? this.#header.duration : 0; }
  get currentTime() { return this.#time; }
  set currentTime(t) { this.#seek(t); }
  /** Frames per second and grid size of the loaded clip (null until metadata). */
  get metadata() { return this.#header; }

  async play() {
    if (!this.src) return;
    // Serialised: connectedCallback and attributeChangedCallback can both land here,
    // and two concurrent opens would abort each other's stream.
    if (this.#starting) return this.#starting;
    this.#paused = false;
    this.#starting = (async () => {
      if (!this.#header || (this.#streamDone && !this.#buf.length)) await this.#open(this.#time);
      this.#t0 = performance.now();
      this.#base = this.#time;
      this.#startAudio();
      this.#syncUI();
      this.dispatchEvent(new Event('play'));
      if (!this.#raf && !this.#paused) this.#raf = requestAnimationFrame(this.#tick);
    })();
    try { await this.#starting; } finally { this.#starting = null; }
  }

  pause() {
    if (this.#paused) return;
    this.#time = this.#clock(performance.now());
    this.#paused = true;
    cancelAnimationFrame(this.#raf);
    this.#raf = 0;
    this.#audioEl.pause();
    this.#syncUI();
    this.dispatchEvent(new Event('pause'));
  }

  // ---- internals ----
  #startAudio() {
    if (!this.audio) return;
    if (this.#audioEl.src !== new URL(this.audio, location.href).href) this.#audioEl.src = this.audio;
    this.#audioEl.muted = this.muted;
    if (Math.abs(this.#audioEl.currentTime - this.#time) > 0.3) this.#audioEl.currentTime = this.#time;
    this.#audioEl.play().then(
      () => { this.#audioBlocked = false; this.#syncUI(); },
      () => { this.#audioBlocked = true; this.#syncUI(); }, // needs a user gesture; picture keeps playing
    );
  }

  #stop() {
    cancelAnimationFrame(this.#raf);
    this.#raf = 0;
    if (this.#abort) this.#abort.abort();
    this.#abort = null;
    this.#buf = [];
    this.#streamDone = true;
    this.#audioEl.pause();
  }

  async #open(startAt = 0) {
    this.#stop();
    this.#paused = false;
    this.#skipUntil = startAt;
    this.#time = startAt;
    const abort = new AbortController();
    this.#abort = abort;
    let clip;
    try {
      const res = await fetch(this.src, { signal: abort.signal });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      clip = await openAscf(res.body);
    } catch (err) {
      if (err.name !== 'AbortError') {
        this.dispatchEvent(new CustomEvent('error', { detail: err }));
        console.error('[ascf-player]', err);
      }
      return;
    }
    if (abort.signal.aborted) return;
    this.#header = clip;
    this.#setupCanvas(clip);
    this.dispatchEvent(new Event('loadedmetadata'));
    this.#streamDone = false;
    this.#pump(clip, abort);
  }

  async #pump(clip, abort) {
    try {
      for await (const frame of clip.frames()) {
        if (abort.signal.aborted) break;
        if (frame.time < this.#skipUntil) continue;   // decoded for state, not shown
        this.#buf.push(frame);
        while (this.#buf.length > 90 && !abort.signal.aborted) {
          await new Promise((r) => setTimeout(r, 40));
        }
      }
    } catch (err) {
      if (!abort.signal.aborted) console.error('[ascf-player]', err);
    }
    if (!abort.signal.aborted) this.#streamDone = true;
  }

  #seek(t) {
    const dur = this.duration;
    t = Math.max(0, dur ? Math.min(t, dur) : t);
    this.#time = t;
    this.#base = t;
    this.#t0 = performance.now();
    if (this.audio) this.#audioEl.currentTime = t;
    const buffered = this.#buf.length && this.#buf[0].time <= t && this.#buf[this.#buf.length - 1].time >= t;
    if (buffered) {
      while (this.#buf.length > 1 && this.#buf[0].time < t) this.#buf.shift();
    } else {
      // ponytail: .ascf has no seek index, so an out-of-buffer seek restarts the
      // stream and re-decodes up to the target. Add a keyframe index to the format
      // if scrubbing long clips has to be instant.
      const wasPaused = this.#paused;
      this.#open(t).then(() => { if (wasPaused) this.pause(); else this.play(); });
    }
    this.#syncUI();
    this.dispatchEvent(new Event('seeked'));
  }

  #clock(now) {
    if (this.audio && !this.#audioEl.paused && this.#audioEl.readyState >= 1) return this.#audioEl.currentTime;
    return this.#base + (now - this.#t0) / 1000;
  }

  #tick = (now) => {
    if (this.#paused) { this.#raf = 0; return; }
    this.#raf = requestAnimationFrame(this.#tick);
    const clock = this.#clock(now);

    if (!this.#buf.length) {
      // #abort is only set while a stream is live, so a tick that beats the first
      // fetch can't be mistaken for the end of the clip.
      if (this.#streamDone && this.#abort) this.#end();
      else { this.#t0 += now - (this.#lastTick || now); }   // stall the clock while buffering
      this.#lastTick = now;
      return;
    }
    this.#lastTick = now;

    while (this.#buf.length > 1 && this.#buf[0].time < clock - 0.1) this.#buf.shift();
    if (this.#buf[0].time > clock + 0.05) return;

    const frame = this.#buf.shift();
    this.#time = frame.time;
    this.#draw(frame);

    if (now - this.#lastEmit > 250) {
      this.#lastEmit = now;
      this.#syncUI();
      this.dispatchEvent(new Event('timeupdate'));
    }
  };

  #end() {
    if (this.loop) {
      this.#time = 0;
      this.#stop();          // play() reopens from the top and restarts the rAF loop
      this.play();
      return;
    }
    this.pause();
    this.dispatchEvent(new Event('ended'));
  }

  #setupCanvas(h) {
    const cs = getComputedStyle(this);
    const v = (name, fallback) => (cs.getPropertyValue(name).trim() || fallback);
    if (h.pixelMode) {
      this.#canvas.width = h.cols;
      this.#canvas.height = h.rows;
      this.#imageData = this.#ctx.createImageData(h.cols, h.rows);
      const d = this.#imageData.data;
      for (let i = 3; i < d.length; i += 4) d[i] = 255;
    } else {
      this.#font = `${v('--ascf-font-weight', 'bold')} 8px ${v('--ascf-font-family', '"Courier New", monospace')}`;
      this.#ctx.font = this.#font;
      this.#charW = this.#ctx.measureText('M').width;
      this.#canvas.width = Math.round(h.cols * this.#charW);
      this.#canvas.height = h.rows * 8;
      this.#imageData = null;
    }
    this.style.aspectRatio ||= `${this.#canvas.width} / ${this.#canvas.height}`;
    this.#ctx.clearRect(0, 0, this.#canvas.width, this.#canvas.height);
  }

  #draw(frame) {
    const h = this.#header;
    const ctx = this.#ctx;
    if (h.pixelMode) {
      const src = frame.data, dst = this.#imageData.data;
      for (let s = 0, d = 0; s < src.length; s += 3, d += 4) {
        dst[d] = src[s + 2]; dst[d + 1] = src[s + 1]; dst[d + 2] = src[s];   // BGR -> RGB
      }
      ctx.putImageData(this.#imageData, 0, 0);
      return;
    }

    const cs = getComputedStyle(this);
    ctx.fillStyle = cs.getPropertyValue('--ascf-background').trim() || '#050505';
    ctx.fillRect(0, 0, this.#canvas.width, this.#canvas.height);
    ctx.font = this.#font;
    ctx.textBaseline = 'top';

    if (h.renderMode === 1) {
      ctx.fillStyle = cs.color;
      frame.text.split('\n').forEach((line, r) => ctx.fillText(line, 0, r * 8));
      return;
    }

    const cells = frame.data;
    let col = 0, row = 0, prev = -1;
    for (let i = 0; i < cells.length; i += 4) {
      const packed = (cells[i + 1] << 16) | (cells[i + 2] << 8) | cells[i + 3];
      if (packed !== prev) {
        ctx.fillStyle = `rgb(${cells[i + 1]},${cells[i + 2]},${cells[i + 3]})`;
        prev = packed;
      }
      ctx.fillText(String.fromCharCode(cells[i]), col * this.#charW, row * 8);
      if (++col >= h.cols) { col = 0; row++; }
    }
  }

  #syncUI() {
    const { pp, seek, time, mute } = this.#ui;
    pp.innerHTML = this.#paused ? '&#9654;' : '&#10074;&#10074;';
    pp.setAttribute('aria-label', this.#paused ? 'Play' : 'Pause');
    mute.hidden = !this.audio;
    mute.innerHTML = this.muted || this.#audioBlocked ? '&#128263;' : '&#128266;';
    mute.setAttribute('aria-label', this.muted || this.#audioBlocked ? 'Unmute' : 'Mute');
    const dur = this.duration;
    if (document.activeElement !== this) seek.value = dur ? this.#time / dur : 0;
    time.textContent = dur ? `${fmt(this.#time)} / ${fmt(dur)}` : fmt(this.#time);
    this.#audioEl.muted = this.muted;
  }
}

if (!customElements.get('ascf-player')) customElements.define('ascf-player', AscfPlayer);


globalThis.AscfPlayer = AscfPlayer;
globalThis.AscfDecoder = { openAscf, makeDecoder, parseHeader };
})();
