// Node timing is a codec baseline, not browser FPS or Canvas performance.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const dir = process.argv[2] || path.join(__dirname, 'output/profile');
const meta = JSON.parse(fs.readFileSync(path.join(dir, 'meta.json')));
const data = fs.readFileSync(path.join(dir, 'packets.bin'));
const ctx = { module: { exports: {} }, Uint8Array, Int32Array, Float64Array, DataView, DecompressionStream };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../codec.js'), 'utf8'), ctx);
(async () => {
  const decoder = ctx.module.exports.makeDecoder(3), times = [];
  let offset = 0;
  for (let i = 0; i < meta.frames; i++) {
    const size = data.readUInt32BE(offset); offset += 4;
    const packet = Uint8Array.from(data.subarray(offset, offset + size)); offset += size;
    const before = performance.now();
    const { frame, frameIndex } = await decoder.decode(packet);
    times.push(performance.now() - before);
    assert.equal(frameIndex, i);
    assert.equal(crypto.createHash('sha256').update(frame).digest('hex'), meta.hashes[i]);
  }
  const sorted = times.toSorted((a, b) => a - b);
  const report = { verified_frames: times.length, node_decode_ms: {
    mean: times.reduce((a, b) => a + b) / times.length,
    p95: sorted[Math.floor(sorted.length * 0.95)], max: sorted.at(-1) } };
  fs.writeFileSync(path.join(dir, 'node-report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
