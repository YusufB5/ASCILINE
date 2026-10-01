// Compare native encoder's reconstructed state with the shipped JS decoder.
const fs = require('node:fs');
const codec = require('../../codec.cjs');
(async () => {
  const vectors = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const decoders = new Map();
  const counts = {};
  for (const vector of vectors) {
    if (!decoders.has(vector.group)) decoders.set(vector.group, codec.makeDecoder(vector.channels));
    counts[vector.group] ||= {frames: 0, mismatchingFrames: 0, mismatchingBytes: 0, firstMismatch: null};
    const encoded = Uint8Array.from(Buffer.from(vector.message, 'hex'));
    const expected = Buffer.from(vector.shown, 'hex');
    const {frameIndex, frame} = await decoders.get(vector.group).decode(encoded.buffer);
    const count = counts[vector.group];
    count.frames++;
    let mismatches = Math.abs(frame.length - expected.length);
    for (let i = 0; i < Math.min(frame.length, expected.length); i++) mismatches += frame[i] !== expected[i];
    if (mismatches) {
      count.mismatchingFrames++;
      count.mismatchingBytes += mismatches;
      count.firstMismatch ??= frameIndex;
    }
  }
  const report = JSON.stringify(counts, null, 2);
  if (process.argv[3]) fs.writeFileSync(process.argv[3], report + '\n');
  console.log(report);
})().catch(error => { console.error(error); process.exitCode = 1; });
