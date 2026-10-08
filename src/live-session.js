// SPDX-License-Identifier: MIT
// Live transport/timeline ownership, independent of the server's engine.
export class LiveSession {
    constructor(player, codec, url) {
        this.p = player;
        this.codec = codec;
        this.url = url;
        this.syncId = null;
        this.pendingSeek = null;
        this.audioClock = false;
        this.attempt = 0;
        this.cancelStart = null;
        this.disposed = false;
        this.closed = false;
    }

    send(message) {
        if (this.p.ws?.readyState === WebSocket.OPEN) this.p.ws.send(JSON.stringify(message));
    }

    resetStart() {
        this.attempt++;
        this.cancelStart?.();
        this.cancelStart = null;
        this.p.readyToRender = false;
        this.p._stopRendering();
        this.p._stopBufferReports();
    }

    resetFrames() {
        const p = this.p;
        p.frameBuffer.length = 0;
        p.framesInFlight = 0;
        p.decodeQueue = Promise.resolve();
        p.codecDecoder = p.renderMode > 1 && (this.tagged !== false || p.pixelCodec === 'dct') && (!p.pixelMode || p.pixelCodec === 'dct')
            ? this.codec.makeDecoder(p.pixelMode ? 3 : 4) : null;
        this.metrics = { decodeMs: 0, decoded: 0, renderMs: 0, rendered: 0,
            lateDrops: 0, decodeErrors: 0, displayTime: null };
    }

    loadAudio() {
        const p = this.p;
        this.audioClock = false;
        if (!p.audioEl || p.isWebcamStream) return;
        p.audioEl.pause();
        p.audioEl.src = p._getAudioUrl(`v=${p.currentQueueIdx}&start=${p.audioOffset}&epoch=${p.streamEpoch}&t=${Date.now()}`);
        p.audioEl.load();
    }

    connect() {
        const p = this.p;
        const base = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws`;
        const url = new URL(!this.url || this.url === 'auto' ? base : this.url, base);
        if (!url.searchParams.has('codec')) url.searchParams.set('codec', 'adaptive');
        url.searchParams.set('sync', '1');
        if (this.codec.TAG_PROFILE === 4) url.searchParams.set('pixel_codec', 'dct-v1');
        p.resolvedWsUrl = url.href;
        this.tagged = url.searchParams.get('codec') === 'adaptive';
        const socket = p.ws = new WebSocket(url.href);
        socket.binaryType = 'arraybuffer';
        const current = () => !this.disposed && p.ws === socket;
        socket.onopen = () => { if (current()) p.emit('buffering'); };
        socket.onmessage = event => {
            if (!current()) return;
            try { this.message(event.data); } catch (error) { this.fail(error); }
        };
        socket.onerror = error => { if (current()) this.fail(error); };
        socket.onclose = event => {
            if (!current()) return;
            if (event.code !== 1000) { this.fail(new Error(`WebSocket closed: ${event.code}`)); return; }
            this.closed = true;
            this.start();
            const drain = () => {
                if (!current()) return;
                if (!p.framesInFlight && !p.frameBuffer.length) p._finishStream('ENDED');
                else this.drainTimer = setTimeout(drain, 50);
            };
            drain();
        };
    }

    message(data) {
        const p = this.p;
        if (typeof data === 'string' && data.startsWith('Error:')) throw new Error(data);
        if (typeof data === 'string' && data.startsWith('SEEKED:')) {
            const [, id, actual] = data.split(':');
            if (Number(id) !== this.pendingSeek) return;
            this.pendingSeek = null;
            this.syncId = Number(id);
            p.audioOffset = Number(actual);
            this.resetFrames();
            p.streamStartTime = performance.now() - p.audioOffset * 1000;
            if (p.state === 'PAUSED') p.pauseStartTime = performance.now();
            this.loadAudio();
            return;
        }
        if (typeof data === 'string' && data.startsWith('INIT:')) {
            const parts = data.split(':');
            this.resetStart();
            p.streamEpoch++;
            p.targetFps = Number(parts[1]);
            p.frameInterval = 1000 / p.targetFps;
            p.renderMode = Number(parts[2]);
            p.pixelMode = parts[5] === '1';
            p.currentQueueIdx = Number(parts[6] || 0);
            p.duration = Number(parts[7] || 0);
            p.audioOffset = Number(parts[8] || 0);
            p.isWebcamStream = parts[9] === '1';
            this.syncId = !p.isWebcamStream && parts.length > 10 ? Number(parts[10]) : null;
            this.pendingSeek = null;
            p.pixelCodec = p.pixelMode && parts[11] === 'dct' ? 'dct' : 'raw';
            p._buildCanvas(Number(parts[3]), Number(parts[4]));
            this.resetFrames();
            if (!this.tagged && p.pixelCodec !== 'dct') p.codecDecoder = null;
            p.streamStartTime = performance.now() - p.audioOffset * 1000;
            if (p.state !== 'PAUSED') p._setState('PLAYING');
            else p.pauseStartTime = performance.now();
            this.loadAudio();
            p.setFilters(p.currentFilters);
            p.emit('init', { fps: p.targetFps, cols: p.gridCols, rows: p.gridRows,
                duration: p.duration, pixelMode: p.pixelMode, renderMode: p.renderMode,
                queueIdx: p.currentQueueIdx, isWebcam: p.isWebcamStream, pixelCodec: p.pixelCodec });
            return;
        }
        if (this.pendingSeek !== null) return;
        const epoch = p.streamEpoch;
        const enqueue = (index, frame) => {
            if (this.disposed || epoch !== p.streamEpoch) return;
            p.frameBuffer.push({ data: frame, time: index / p.targetFps });
            while (p.frameBuffer.length > Math.max(20, p.options.bufferSize * 5)) p.frameBuffer.shift();
            this.start();
        };
        if (typeof data === 'string') {
            const newline = data.indexOf('\n');
            const index = Number(data.slice(0, newline));
            if (newline >= 0 && Number.isFinite(index)) enqueue(index, data.slice(newline + 1));
        } else if (p.codecDecoder) {
            const decoder = p.codecDecoder;
            p.framesInFlight++;
            p.decodeQueue = p.decodeQueue.then(async () => {
                if (this.disposed || epoch !== p.streamEpoch) return;
                const tick = performance.now();
                const { frameIndex, frame } = await decoder.decode(data);
                if (this.disposed || epoch !== p.streamEpoch) return;
                p.framesInFlight--;
                this.metrics.decodeMs += performance.now() - tick;
                this.metrics.decoded++;
                enqueue(frameIndex, frame);
            }).catch(error => {
                if (this.disposed || epoch !== p.streamEpoch) return;
                p.framesInFlight--;
                this.metrics.decodeErrors++;
                this.fail(error);
            });
        } else {
            const index = new DataView(data).getUint32(0, false);
            enqueue(index, new Uint8Array(data, 4));
        }
    }

    start() {
        const p = this.p;
        if (this.disposed || p.state !== 'PLAYING' || p.readyToRender || this.cancelStart ||
            this.pendingSeek !== null || !p.frameBuffer.length) return;
        // The sync protocol's preroll is four frames, independent of SDK bufferSize.
        if (this.syncId !== null && !this.closed && p.frameBuffer.length < 4) return;
        if (!p.audioEl || p.isWebcamStream) { this.begin(false); return; }
        const epoch = p.streamEpoch, attempt = ++this.attempt, audio = p.audioEl, src = audio.src;
        const current = () => !this.disposed && epoch === p.streamEpoch && attempt === this.attempt && p.state === 'PLAYING';
        const playing = () => {
            if (current() && audio.currentSrc === src && audio.readyState >= 3 && !audio.paused) this.begin(true);
        };
        const unavailable = () => {
            if (!current()) return;
            audio.pause();
            this.begin(false);
        };
        const timer = setTimeout(unavailable, 2000);
        this.cancelStart = () => {
            clearTimeout(timer);
            audio.removeEventListener('playing', playing);
            audio.removeEventListener('error', unavailable);
        };
        audio.addEventListener('playing', playing);
        audio.addEventListener('error', unavailable);
        Promise.resolve(audio.play()).then(playing).catch(unavailable);
    }

    begin(audioClock) {
        const p = this.p;
        this.cancelStart?.();
        this.cancelStart = null;
        this.attempt++;
        this.audioClock = audioClock;
        p._audioGated = !!p.audioEl && !audioClock;
        p._updateMuteButton();
        p.readyToRender = true;
        p.streamStartTime = performance.now() - (p.frameBuffer[0]?.time ?? p.audioOffset) * 1000;
        p.frameCount = 0;
        p.lastRenderTime = p.lastFpsUpdate = performance.now();
        if (this.syncId !== null) this.send({ type: 'playback-ready', requestId: this.syncId, time: this.clock() });
        p._scheduleRender();
        p._startBufferReports();
    }

    clock() {
        const p = this.p;
        if (this.pendingSeek !== null) return p.audioOffset;
        if (this.audioClock && p.audioEl?.readyState >= 1) return p.audioEl.currentTime + p.audioOffset;
        if (!p.readyToRender) return p.audioOffset;
        return ((p.state === 'PAUSED' ? p.pauseStartTime : performance.now()) - p.streamStartTime) / 1000;
    }

    seek(target) {
        const p = this.p;
        if (!Number.isFinite(target) || p.isWebcamStream || !p.ws || this.closed) return;
        target = Math.max(0, p.duration ? Math.min(target, p.duration) : target);
        this.resetStart();
        p.streamEpoch++;
        p.audioOffset = target;
        this.audioClock = false;
        p.audioEl?.pause();
        this.resetFrames();
        this.pendingSeek = this.syncId === null ? null : p.streamEpoch;
        this.send({ type: 'seek', time: target, requestId: p.streamEpoch });
        if (this.pendingSeek === null) this.loadAudio();
        p.emit('seek', target);
    }

    pause() {
        const p = this.p;
        p.audioOffset = this.clock();
        this.resetStart();
        this.audioClock = false;
        p._setState('PAUSED');
        p.pauseStartTime = performance.now();
        p.audioEl?.pause();
        this.send({ type: 'pause', paused: true });
        p._showPauseOverlay();
    }

    resume() {
        const p = this.p;
        p._setState('PLAYING');
        p._hidePauseOverlay();
        this.send({ type: 'pause', paused: false });
        // Re-seek aligns the new audio range, queued frames and server clock.
        if (!this.closed) this.seek(p.audioOffset);
        else this.start();
    }

    unmute() {
        const p = this.p;
        p.audioEl.muted = false;
        if (p.state === 'PLAYING' && !this.audioClock) this.seek(this.clock());
        p._updateMuteButton();
    }

    reinit(values) {
        const time = this.clock();
        this.resetStart();
        this.p.streamEpoch++;
        this.p.audioOffset = time;
        this.audioClock = false;
        this.p.audioEl?.pause();
        this.resetFrames();
        this.pendingSeek = -1; // Discard old frames until the new INIT.
        this.send({ type: 'reinit', ...values, time });
    }

    fail(error) {
        if (this.disposed) return;
        this.p.emit('error', error);
        this.p._finishStream('ERROR');
    }

    dispose() {
        this.disposed = true;
        this.resetStart();
        clearTimeout(this.drainTimer);
        this.p.streamEpoch++;
        this.p.frameBuffer.length = 0;
        this.p.framesInFlight = 0;
        this.p.decodeQueue = Promise.resolve();
        const socket = this.p.ws;
        if (socket) {
            socket.onopen = socket.onmessage = socket.onerror = socket.onclose = null;
            socket.close();
            this.p.ws = null;
        }
    }
}
