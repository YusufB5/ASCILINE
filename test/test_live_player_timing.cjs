/* Run the shipped root player with a controlled media clock and fake DOM.
 * Exercise real event handlers, including late audio/decode completions.
 * node test/test_live_player_timing.cjs
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}

function harness(options = {}) {
    let now = 0, serial = 0;
    const rafs = new Map(), timers = new Map(), intervals = new Map(), elements = new Map();
    const drawn = [], sockets = [], decodes = [];
    const context2d = {
        createImageData: (cols, rows) => ({ data: new Uint8Array(cols * rows * 4) }),
        measureText: () => ({ width: 4 }),
        putImageData: image => drawn.push(image.data[0]),
        clearRect() {}, fillRect() {}, fillText() {},
    };
    function element(id) {
        if (elements.has(id)) return elements.get(id);
        const listeners = new Map();
        const el = {
            id, style: {}, dataset: {}, value: '1', textContent: '', checked: false,
            clientWidth: 600, clientHeight: 400,
            classList: { add() {}, remove() {}, toggle() {} },
            addEventListener: (name, callback) => {
                if (!listeners.has(name)) listeners.set(name, new Set());
                listeners.get(name).add(callback);
            },
            removeEventListener: (name, callback) => listeners.get(name)?.delete(callback),
            emit: name => { for (const fn of [...(listeners.get(name) || [])]) fn({}); },
            getContext: () => context2d,
            getBoundingClientRect: () => ({ left: 0, width: 600 }),
            appendChild() {}, closest: () => null,
        };
        elements.set(id, el);
        return el;
    }
    const audio = element('ascii-audio');
    Object.assign(audio, {
        currentTime: 0, paused: true, readyState: 0, error: null, playCalls: [], loads: 0,
        pause() { this.paused = true; },
        load() { this.loads++; this.currentTime = 0; this.readyState = 0; this.error = null; },
        play() { const call = deferred(); this.playCalls.push(call); return call.promise; },
        playing() {
            this.paused = false; this.readyState = 4;
            this.currentSrc = this.src;
            this.emit('playing'); this.playCalls.at(-1).resolve();
        },
    });
    class FakeWebSocket {
        static OPEN = 1;
        constructor(url) { this.url = url; this.readyState = 1; this.sent = []; sockets.push(this); }
        send(value) { this.sent.push(JSON.parse(value)); }
        close() { this.readyState = 3; }
        deliver(data) { this.onmessage({ data }); }
    }
    const WireSocket = options.WebSocket ? class extends options.WebSocket {
        constructor(url) { super(url); this.sent = []; sockets.push(this); }
        send(value) { this.sent.push(JSON.parse(value)); return super.send(value); }
    } : FakeWebSocket;
    const sandbox = {
        console, Uint8Array, Float32Array, ArrayBuffer, DataView, TextDecoder,
        document: {
            getElementById: element, querySelector: element, querySelectorAll: () => [],
            addEventListener() {}, createElement: () => element('created'),
        },
        window: { addEventListener() {}, getSelection: () => ({ toString: () => '' }) },
        localStorage: { getItem: () => null, setItem() {} },
        location: { protocol: 'http:', host: options.host || 'localhost:8000' },
        performance: { now: () => now }, WebSocket: WireSocket,
        requestAnimationFrame: callback => { const id = ++serial; rafs.set(id, callback); return id; },
        cancelAnimationFrame: id => rafs.delete(id),
        setTimeout: (callback, delay) => { const id = ++serial; timers.set(id, { callback, at: now + delay }); return id; },
        clearTimeout: id => timers.delete(id),
        setInterval: (callback, delay) => {
            const id = ++serial; intervals.set(id, { callback, delay, at: now + delay }); return id;
        },
        clearInterval: id => intervals.delete(id),
        fetch: async () => ({ json: async () => ({ available: false }) }),
        AscilineCodec: { makeDecoder: () => ({ decode(data) {
            const work = deferred();
            decodes.push({ ...work, index: new DataView(data).getUint32(0) });
            return work.promise;
        } }) },
    };
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../app.js'), 'utf8'), sandbox);
    const run = code => vm.runInContext(code, sandbox);
    function advance(ms, draw = true) {
        now += ms;
        for (const [id, timer] of [...timers]) {
            if (timer.at <= now) { timers.delete(id); timer.callback(); }
        }
        for (const timer of intervals.values()) {
            if (timer.at <= now) { timer.at = now + timer.delay; timer.callback(); }
        }
        if (draw) {
            const batch = [...rafs]; rafs.clear();
            for (const [, callback] of batch) callback(now);
        }
    }
    function frame(index, value = index % 200) {
        const buffer = new ArrayBuffer(10);
        new DataView(buffer).setUint32(0, index);
        new Uint8Array(buffer, 4).fill(value);
        sockets.at(-1).deliver(buffer);
    }
    function init(pixel = true, pixelCodec = 'raw') {
        run('startStream()');
        sockets.at(-1).deliver(`INIT:60:6:2:1:${Number(pixel)}:0:60:0:0:0:${pixelCodec}`);
    }
    return { run, advance, frame, init, audio, sockets, rafs, drawn, decodes };
}

async function flush() { for (let i = 0; i < 5; i++) await Promise.resolve(); }

async function testDelayedAudioAndRepeatedSeeks() {
    const h = harness(); h.init();
    for (let i = 0; i < 4; i++) h.frame(i);
    assert.equal(h.audio.playCalls.length, 1);
    // Slow MP3 startup must not trigger the former 500ms wall-clock fallback.
    h.advance(700);
    assert.equal(h.run('readyToRender'), false);
    assert.equal(h.rafs.size, 0);
    h.audio.playing(); await flush(); h.advance(16);
    assert.equal(h.drawn.at(-1), 0);
    assert.equal(h.rafs.size, 1);
    assert(h.sockets[0].sent.some(m => m.type === 'playback-ready' && m.requestId === 0));

    for (const [target, firstIndex] of [[10, 600], [4, 240], [25, 1500], [2, 120]]) {
        const oldPlays = h.audio.playCalls.length;
        h.run(`doSeek(${target})`);
        const seek = h.sockets[0].sent.at(-1);
        assert.equal(seek.type, 'seek');
        h.frame(firstIndex + 50); // A pre-marker frame belongs to the old stream.
        h.advance(700);
        assert.equal(h.audio.playCalls.length, oldPlays);
        assert.equal(h.run('frameBuffer.length'), 0);
        h.sockets[0].deliver(`SEEKED:${seek.requestId}:${target}`);
        for (let i = 0; i < 4; i++) h.frame(firstIndex + i, i + target);
        assert.equal(h.audio.playCalls.length, oldPlays + 1);
        h.audio.playing(); await flush(); h.advance(16);
        assert.equal(h.drawn.at(-1), target);
        assert.equal(h.rafs.size, 1, 'Only one render loop may survive each seek');
    }
    // A full second after the last seek should draw all source frames.
    const before = h.drawn.length;
    for (let i = 4; i < 64; i++) {
        h.audio.currentTime = (i - 3) / 60;
        h.frame(120 + i); h.advance(1000 / 60);
    }
    assert(h.drawn.length - before >= 58, 'Playback must recover the source frame rate after seeking');
    h.run('togglePause()');
    assert.equal(h.rafs.size, 0);
    h.run('togglePause()');
    h.frame(184); // Complete the preroll sent by the server on resume.
    h.audio.playing(); await flush();
    assert.equal(h.rafs.size, 1);
}

async function testStaleSeekAndAudioCompletion() {
    const h = harness(); h.init();
    for (let i = 0; i < 4; i++) h.frame(i);
    const oldPlay = h.audio.playCalls[0];
    h.run('doSeek(10)');
    const oldId = h.sockets[0].sent.at(-1).requestId;
    h.run('doSeek(20)');
    const currentId = h.sockets[0].sent.at(-1).requestId;
    oldPlay.resolve(); await flush();
    assert.equal(h.run('readyToRender'), false);
    h.sockets[0].deliver(`SEEKED:${oldId}:10`);
    h.frame(600);
    assert.equal(h.run('pendingSeekId'), currentId);
    assert.equal(h.run('frameBuffer.length'), 0);
    h.sockets[0].deliver(`SEEKED:${currentId}:20`);
    for (let i = 0; i < 4; i++) h.frame(1200 + i, 99);
    h.run('togglePause()'); // Pause while the new audio is still loading.
    h.audio.playCalls.at(-1).resolve(); await flush();
    assert.equal(h.run('readyToRender'), false);
    assert.equal(h.rafs.size, 0);
    h.run('togglePause()');
    h.audio.playing(); await flush(); h.advance(16);
    assert.equal(h.drawn.at(-1), 99);
    assert.equal(h.rafs.size, 1);
}

async function testLateAsciiDecodeCannotCrossSeek() {
    const h = harness(); h.init(false);
    h.frame(0); await flush();
    assert.equal(h.decodes.length, 1);
    h.run('doSeek(10)');
    const id = h.sockets[0].sent.at(-1).requestId;
    h.sockets[0].deliver(`SEEKED:${id}:10`);
    h.decodes[0].resolve({ frameIndex: 0, frame: new Uint8Array(8) });
    await flush();
    assert.equal(h.run('frameBuffer.length'), 0);
    assert.equal(h.run('framesInFlight'), 0);
    h.frame(600); await flush();
    h.decodes.at(-1).resolve({ frameIndex: 600, frame: new Uint8Array(8) });
    await flush();
    assert.equal(h.run('frameBuffer[0].time'), 10);
    h.run('finishStream()');
    assert.equal(h.rafs.size, 0);
}

async function testUnavailableAudioUsesOneStableClock() {
    const h = harness(); h.init();
    for (let i = 0; i < 4; i++) h.frame(i);
    h.audio.playCalls[0].reject(new Error('No audio track'));
    await flush(); h.advance(16);
    assert.equal(h.run('audioClockActive'), false);
    assert.equal(h.run('readyToRender'), true);
    assert.equal(h.drawn.at(-1), 0);
    h.audio.readyState = 1; // Late metadata must not switch back to frozen time 0.
    h.advance(200);
    assert(h.run('getMasterClock()') >= 0.2);
    h.run('togglePause()');
    const pausedClock = h.run('getMasterClock()');
    h.advance(800);
    assert.equal(h.run('getMasterClock()'), pausedClock);
    h.run('togglePause()');
    const seek = h.sockets[0].sent.at(-1);
    assert.equal(seek.type, 'seek');
    assert.equal(seek.time, pausedClock);
    const index = Math.ceil(pausedClock * 60);
    h.sockets[0].deliver(`SEEKED:${seek.requestId}:${index / 60}`);
    for (let i = 0; i < 4; i++) h.frame(index + i, 77);
    h.audio.playCalls.at(-1).reject(new Error('Still no audio'));
    await flush(); h.advance(16);
    assert.equal(h.drawn.at(-1), 77);
    assert.equal(h.rafs.size, 1);
    h.run('finishStream()');
    assert.equal(h.rafs.size, 0);
}

async function testOldAudioPlayingEventCannotStartNewSeek() {
    const h = harness(); h.init();
    for (let i = 0; i < 4; i++) h.frame(i);
    h.audio.playing(); await flush(); h.advance(16);
    const oldSource = h.audio.src;
    h.run('doSeek(10)');
    const id = h.sockets[0].sent.at(-1).requestId;
    h.sockets[0].deliver(`SEEKED:${id}:10`);
    for (let i = 0; i < 4; i++) h.frame(600 + i, 99);
    // The element is reused. An event queued by its old resource can arrive
    // after the new listener was installed, with the previous currentTime.
    h.audio.currentSrc = oldSource;
    h.audio.currentTime = 5;
    h.audio.readyState = 4;
    h.audio.paused = false;
    h.audio.emit('playing');
    assert.equal(h.run('readyToRender'), false);
    assert.equal(h.rafs.size, 0);
    h.audio.currentTime = 0;
    h.audio.playing(); await flush(); h.advance(16);
    assert.equal(h.drawn.at(-1), 99);
    assert.equal(h.rafs.size, 1);
}

module.exports = { harness, flush };

async function testProfileSeekUsesFreshDecoderAndIgnoresLateFrames() {
    const h = harness(); h.init(true, 'dct');
    assert.ok(h.sockets[0].url.includes('pixel_codec=dct-v1'));
    assert.equal(h.run('pixelCodec'), 'dct');
    assert.ok(h.run('codecDecoder !== null'));
    h.frame(0); await flush();
    const oldDecode = h.decodes[0];
    h.run('doSeek(1)');
    const id = h.sockets[0].sent.at(-1).requestId;
    h.sockets[0].deliver(`SEEKED:${id}:1`);
    oldDecode.resolve({ frameIndex: 0, frame: new Uint8Array(6) });
    await flush();
    assert.equal(h.run('frameBuffer.length'), 0);
    assert.equal(h.run('playbackMetrics.decoded'), 0);
    for (let i = 60; i < 64; i++) {
        h.frame(i); await flush();
        h.decodes.at(-1).resolve({ frameIndex: i, frame: new Uint8Array(6).fill(90) });
        await flush();
    }
    h.audio.playing(); await flush(); h.advance(16);
    assert.equal(h.drawn.at(-1), 90);
    h.sockets[0].deliver('INIT:60:6:2:1:1:0:60:0:0:2:raw');
    assert.equal(h.run('codecDecoder'), null);
}

async function testLateFramesAndClientTimingReports() {
    const h = harness(); h.init(true, 'dct');
    for (let i = 0; i < 4; i++) {
        h.frame(i); await flush();
        h.advance(5, false);
        h.decodes.at(-1).resolve({ frameIndex: i, frame: new Uint8Array(6).fill(10) });
        await flush();
    }
    h.audio.playing(); await flush(); h.advance(16);
    h.audio.currentTime = 1;
    h.advance(250);
    assert.equal(h.run('frameBuffer.length'), 0);
    assert.equal(h.run('playbackMetrics.lateDrops'), 3);
    h.advance(250);
    const report = h.sockets[0].sent.filter(m => m.type === 'buffer').at(-1);
    assert.equal(report.depth, 0, 'A producer can be late with no pending decoder work');
    assert.equal(report.decodeMs, 5);
    assert.equal(report.renderMs, 0);
    assert.equal(report.lateDrops, 3);
    assert.equal(report.decodeErrors, 0);
    assert.equal(report.lagMs, 950);
    // A source-frame jump can restore rendering without resetting the audio clock.
    h.frame(60); await flush();
    h.decodes.at(-1).resolve({ frameIndex: 60, frame: new Uint8Array(6).fill(99) });
    await flush(); h.advance(16);
    assert.equal(h.drawn.at(-1), 99);
    assert.equal(h.audio.currentTime, 1);
    h.run('doSeek(2)');
    assert.equal(h.run('playbackMetrics.lateDrops'), 0);
    assert.equal(h.run('playbackMetrics.decoded'), 0);
}

if (require.main === module) (async () => {
    for (const test of [testDelayedAudioAndRepeatedSeeks, testStaleSeekAndAudioCompletion,
                        testLateAsciiDecodeCannotCrossSeek, testUnavailableAudioUsesOneStableClock,
                        testOldAudioPlayingEventCannotStartNewSeek,
                        testProfileSeekUsesFreshDecoderAndIgnoresLateFrames,
                        testLateFramesAndClientTimingReports]) {
        await test(); console.log(`[PASS] ${test.name}`);
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
