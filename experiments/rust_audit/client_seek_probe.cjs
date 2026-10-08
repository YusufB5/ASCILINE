/* Couple the shipped root app to a running real server, with a controlled
 * media clock. This is a protocol/lifecycle test, not a real audio browser.
 * node experiments/rust_audit/client_seek_probe.cjs [port]
 */
const assert = require('node:assert/strict');
const WebSocket = global.WebSocket || require('ws');
const { harness } = require('../../test/test_live_player_timing.cjs');
const { performance } = require('node:perf_hooks');

const h = harness({ WebSocket, host: `127.0.0.1:${process.argv[2] || 8000}` });
let last = performance.now(), playingAt = null, playCount = 0;
const ticker = setInterval(() => {
    const now = performance.now(), delta = now - last;
    last = now;
    if (h.audio.playCalls.length > playCount) {
        playCount = h.audio.playCalls.length;
        playingAt = now + 150; // Emulate audio startup after its source changes.
    }
    if (playingAt !== null && now >= playingAt) {
        playingAt = null;
        h.audio.playing();
    } else if (!h.audio.paused) {
        h.audio.currentTime += delta / 1000;
    }
    h.advance(delta);
}, 16);

async function waitFor(check, label, seconds = 5) {
    const deadline = performance.now() + seconds * 1000;
    while (!check()) {
        if (performance.now() >= deadline) {
            throw new Error(`${label} timed out: ` + h.run("JSON.stringify({state,readyToRender,pendingSeekId,syncId,buffer:frameBuffer.length,clock:getMasterClock()})"));
        }
        await new Promise(resolve => setTimeout(resolve, 20));
    }
}

(async () => {
    h.run('startStream()');
    await waitFor(() => h.drawn.length > 10, 'initial playback');
    console.log('initial playback OK');
    for (const target of [10, 3, 20, 10, 35, 4]) {
        const before = h.drawn.length, started = performance.now();
        h.run(`doSeek(${target})`);
        await waitFor(() => h.run('pendingSeekId === null && readyToRender') && h.drawn.length > before + 10,
                      `seek ${target}`);
        assert(Math.abs(h.run('getMasterClock()') - target) < 1);
        console.log(`seek ${target}: resumed after ${(performance.now()-started).toFixed(0)}ms, ` +
                    h.run('JSON.stringify({clock:getMasterClock(),buffer:frameBuffer.length,fps:targetFps})'));
    }
    h.run('togglePause()');
    h.run('doSeek(15)');
    await waitFor(() => h.run('pendingSeekId === null'), 'paused seek');
    h.run('togglePause()');
    const before = h.drawn.length;
    await waitFor(() => h.drawn.length > before + 10, 'resume after paused seek');
    console.log('paused seek/resume OK');
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => {
    clearInterval(ticker); h.run('finishStream()');
});
