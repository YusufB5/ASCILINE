// Exercise the exact browser codec.js, rather than a separately maintained copy.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const context = {
  module: { exports: {} }, DecompressionStream,
  Uint8Array, Int32Array, Float64Array, DataView,
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../codec.js'), 'utf8'), context);
const codec = context.module.exports;
const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

(async () => {
  const decoder = codec.makeDecoder(3);
  let checked = 0;
  for (const sequence of cases) {
    decoder.reset(); // new session, seek or dimension change
    for (const entry of sequence) {
      const message = Uint8Array.from(Buffer.from(entry.packet, 'base64'));
      const actual = await decoder.decode(message);
      assert.equal(actual.frameIndex, entry.index);
      const expected = Buffer.from(entry.shown, 'base64');
      assert.deepEqual(Buffer.from(actual.frame), expected, `frame ${entry.index}`);
      checked++;
    }
  }
  console.log(JSON.stringify({ checked }));
})().catch(error => { console.error(error); process.exitCode = 1; });
