/**
 * Codec decode-order regression test — dependency-free (no video fixtures).
 *
 * Encodes a keyframe + DELTA stream in the exact ASCILINE wire format using
 * Node's zlib (RFC1950, the same format Python's zlib.compress emits), then:
 *   1. decodes it through a SERIALIZED chain (the shipped app.js pattern) and
 *      asserts every frame is byte-exact AND in order, and
 *   2. shows a DELTA decoded before its keyframe throws — i.e. arrival order is
 *      load-bearing, which is exactly why the decoder must be serialized.
 *
 * Usage: node experiments/test_decode_order.js
 */
const codec = require('../codec.js');
const zlib = require('zlib');

const ROWS = 16, COLS = 16, C = 4, CELLS = ROWS * COLS, FB = CELLS * C;
const N = 12, KEYFRAME_INTERVAL = 48;

function be32(n) { const b = Buffer.alloc(4); b.writeUInt32BE(n >>> 0, 0); return b; }
function le32(n) { const b = Buffer.alloc(4); b.writeUInt32LE(n >>> 0, 0); return b; }
function ab(buf) { return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength); }

// Deterministic frames: a varied (poorly-compressible) frame 0, then a few
// changed cells per frame so DELTA decisively beats full-frame zlib.
function makeFrames() {
  const frames = [];
  const f0 = new Uint8Array(FB);
  for (let i = 0; i < FB; i++) f0[i] = (i * 7 + (i >> 2) * 13) & 0xff;
  frames.push(f0);
  for (let i = 1; i < N; i++) {
    const nf = frames[i - 1].slice();
    for (let j = 0; j < 2; j++) {
      const cell = (i * 5 + j * 37) % CELLS;
      for (let b = 0; b < C; b++) nf[cell * C + b] = (i * 31 + cell * 17 + b * 11) & 0xff;
    }
    frames.push(nf);
  }
  return frames;
}

// Mirror of codec.py's encoder (lossless): smallest of RAW / ZLIB / DELTA.
function encode(frames) {
  const msgs = []; let prev = null;
  for (let i = 0; i < frames.length; i++) {
    const raw = Buffer.from(frames[i]);
    if (prev === null || i % KEYFRAME_INTERVAL === 0) {
      const z = zlib.deflateSync(raw);
      const tag = z.length < raw.length ? 1 : 0;
      msgs.push(Buffer.concat([be32(i), Buffer.from([tag]), tag === 1 ? z : raw]));
      prev = frames[i]; continue;
    }
    const idxs = [];
    for (let c = 0; c < CELLS; c++) {
      for (let b = 0; b < C; b++) {
        if (frames[i][c * C + b] !== prev[c * C + b]) { idxs.push(c); break; }
      }
    }
    const body = Buffer.concat([
      ...idxs.map(le32),
      ...idxs.map(c => Buffer.from(frames[i].slice(c * C, c * C + C))),
    ]);
    const dz = zlib.deflateSync(body);
    const fz = zlib.deflateSync(raw);
    let tag, payload;
    if (dz.length <= fz.length) { tag = 2; payload = dz; } else { tag = 1; payload = fz; }
    if (raw.length < payload.length) { tag = 0; payload = raw; }
    msgs.push(Buffer.concat([be32(i), Buffer.from([tag]), payload]));
    prev = frames[i];
  }
  return msgs;
}

async function decodeSerial(msgs, cellBytes) {
  const dec = codec.makeDecoder(cellBytes);
  const out = [];
  let chain = Promise.resolve();
  for (const m of msgs) {
    chain = chain.then(async () => {
      const { frameIndex, frame } = await dec.decode(ab(m));
      out.push({ frameIndex, frame });
    });
  }
  await chain;
  return out;
}

(async () => {
  const frames = makeFrames();
  const msgs = encode(frames);
  const tags = msgs.map(m => m[4]).join('');
  console.log(`frames: ${frames.length}  tags: ${tags}  (0=RAW 1=ZLIB 2=DELTA)`);
  if (!tags.includes('2')) { console.error('FAIL: no DELTA frames produced — test is not exercising the stateful path'); process.exit(1); }

  const out = await decodeSerial(msgs, C);
  let bad = 0;
  for (let i = 0; i < frames.length; i++) {
    if (out[i].frameIndex !== i) { bad++; console.log(`order FAIL at ${i}: got index ${out[i].frameIndex}`); continue; }
    const got = out[i].frame, want = frames[i];
    if (got.length !== want.length) { bad++; console.log(`len FAIL frame ${i}`); continue; }
    for (let j = 0; j < want.length; j++) if (got[j] !== want[j]) { bad++; console.log(`byte FAIL frame ${i} @ ${j}`); break; }
  }
  console.log(`serialized decode : ${bad === 0 ? 'PASS (bit-exact + in-order)' : 'FAIL (' + bad + ')'}`);

  // A DELTA decoded with no prior keyframe must fail — proving order matters.
  let threw = false;
  try { await codec.makeDecoder(C).decode(ab(msgs.find(m => m[4] === 2))); }
  catch { threw = true; }
  console.log(`order load-bearing: ${threw ? 'YES — delta-before-keyframe throws (this is what the concurrency bug caused)' : 'NO'}`);

  process.exit(bad === 0 && threw ? 0 : 1);
})().catch(e => { console.error('ERROR', e); process.exit(2); });
