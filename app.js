/**
 * ASCILINE ENGINE - Pure & Performant Logic
 * =========================================
 * No decorative animations. Pure WebSocket streaming
 * and high-performance canvas rendering.
 * Includes an "Invisible Selection Layer" for text selection.
 */

const player    = document.getElementById('ascii-player');
const canvas    = document.getElementById('ascii-canvas');
const ctx       = canvas.getContext('2d');
const statusEl  = document.getElementById('status');
const container = document.getElementById('player-container');
const overlay   = document.getElementById('play-overlay');
const audioEl   = document.getElementById('ascii-audio');
const volumeSlider = document.getElementById('volume-slider');

const playPauseBtn = document.getElementById('play-pause-btn');
const seekBar = document.getElementById('seek-slider');
const timeCurrent = document.getElementById('time-current');
const timeTotal = document.getElementById('time-total');

// Added controls: skip buttons, played fill, and the hover scrub preview
const btnBack = document.getElementById('btn-back');
const btnFwd = document.getElementById('btn-fwd');
const seekPlayed = document.getElementById('seek-played');
const seekWrap = document.querySelector('.seek-wrap');
const seekPreview = document.getElementById('seek-preview');
const seekPreviewImg = document.getElementById('seek-preview-img');
const seekPreviewTime = document.getElementById('seek-preview-time');
const btnCopy = document.getElementById('btn-copy');
let scrubMeta = null; // hover sprite layout from /scrub
let copyFeedbackTimer = null;

function formatTime(seconds) {
    if (isNaN(seconds) || seconds < 0) return "00:00";
    const m = Math.floor(seconds / 60).toString().padStart(2, '0');
    const s = Math.floor(seconds % 60).toString().padStart(2, '0');
    return `${m}:${s}`;
}

// ── STATE ──
let state = 'IDLE'; // IDLE | PLAYING | PAUSED
let ws = null;
let bufferReportTimer = null; // periodic backlog report to the server (backpressure)
const frameBuffer = [];
const BUFFER_SIZE = 4;
let codecDecoder = null; // Adaptive codec decoder (codec.js)
let targetFps = 24;
let frameInterval = 1000 / targetFps;
let renderMode = 1;
let pixelMode = false;
let readyToRender = false;
let pauseStartTime = 0;
let duration = 0;
let isSeeking = false;
let currentQueueIdx = 0;
let audioOffset = 0;
let isWebcamStream = false; // skips audio-sync gate and locks pause


// Grid & Dimensions
let gridCols = 0, gridRows = 0;
let charWidth = 0, charHeight = 0;
let xPos = null, yPos = null;

// Pixel Mode (--pixel) — ImageData pixel buffer
let dotImageData = null;

// Selection Layer optimization
const textDecoder = new TextDecoder();
let selectionBuffer = null;

// Timing & Metrics
let lastRenderTime = 0;
let frameCount = 0, currentFps = 0, lastFpsUpdate = 0;
let streamStartTime = 0;
let streamEpoch = 0; // Incremented on every seek/reinit to cancel stale async audio loads
let lastUiUpdateTime = 0;
let lastFormattedTime = "";

const CHAR_LUT = new Array(128);
for (let i = 0; i < 128; i++) CHAR_LUT[i] = String.fromCharCode(i);

// ═══════════════════════════════════════
//  CANVAS SETUP
// ═══════════════════════════════════════

function buildCanvas(cols, rows) {
    gridCols = cols;
    gridRows = rows;

    // Sizing and positioning for both layers
    const syncSize = (el) => {
        el.style.width  = container.clientWidth + 'px';
        el.style.height = container.clientHeight + 'px';
        el.style.objectFit = 'contain';
        el.style.position = 'absolute';
        el.style.top = '0';
        el.style.left = '0';
    };

    if (pixelMode) {
        // ── DOT MODE: 1 canvas pixel = 1 grid cell ──
        canvas.width  = cols;
        canvas.height = rows;
        canvas.style.display = 'block';
        canvas.style.imageRendering = 'pixelated';
        dotImageData = ctx.createImageData(cols, rows);
        // Pre-fill alpha channel to 255 (fully opaque)
        const d = dotImageData.data;
        for (let i = 3; i < d.length; i += 4) d[i] = 255;
        syncSize(canvas);
        // Hide selection layer — no text to select in dot mode
        player.style.display = 'none';
    } else {
        // ── STANDARD ASCII MODES (1-5) ──
        canvas.style.imageRendering = '';
        dotImageData = null;
        ctx.font = 'bold 8px Courier New';
        charWidth = ctx.measureText('M').width;
        charHeight = 8;
        canvas.width  = cols * charWidth;
        canvas.height = rows * charHeight;
        canvas.style.display = 'block';

        // Selection Layer Buffer
        selectionBuffer = new Uint8Array((cols + 1) * rows);
        for (let r = 0; r < rows; r++) selectionBuffer[r * (cols + 1) + cols] = 10;

        syncSize(canvas);

        // Selection layer: match canvas object-fit:contain position exactly
        const containerW = container.clientWidth;
        const containerH = container.clientHeight;
        const fitScaleX = containerW / canvas.width;
        const fitScaleY = containerH / canvas.height;
        const fitScale  = Math.min(fitScaleX, fitScaleY);
        const renderedW = canvas.width  * fitScale;
        const renderedH = canvas.height * fitScale;
        const offsetX   = (containerW - renderedW) / 2;
        const offsetY   = (containerH - renderedH) / 2;

        player.style.width  = canvas.width + 'px';
        player.style.height = canvas.height + 'px';
        player.style.position = 'absolute';
        player.style.top = '0';
        player.style.left = '0';
        player.style.transformOrigin = 'top left';
        player.style.transform = `translate(${offsetX}px, ${offsetY}px) scale(${fitScale})`;
        player.style.fontSize = '8px';
        player.style.lineHeight = '8px';

        ctx.font = 'bold 8px Courier New';
        ctx.textBaseline = 'top';
        xPos = new Float32Array(cols);
        yPos = new Float32Array(rows);
        for (let c = 0; c < cols; c++) xPos[c] = c * charWidth;
        for (let r = 0; r < rows; r++) yPos[r] = r * charHeight;
    }
}

// ═══════════════════════════════════════
//  STARTUP SYNC LOGIC (VIDEO GATE)
// ═══════════════════════════════════════
const beginRendering = () => {
    if (readyToRender) return;
    readyToRender = true;
    streamStartTime = performance.now() - (audioOffset * 1000.0);
    lastRenderTime = performance.now();
    lastFpsUpdate = lastRenderTime;
    requestAnimationFrame(renderFrame);
    startBufferReports();
};

const triggerPlaybackStart = (epochToMatch) => {
    if (readyToRender || state !== 'PLAYING') return;
    if (isWebcamStream) {
        beginRendering();
        return;
    }
    if (audioEl) {
        // The very first video frame has arrived and is ready.
        // Now it is safe to start the audio clock.
        audioEl.play().catch(() => {});
        // Audio Gate: Wait for actual playback so clocks match exactly
        if (audioEl.readyState >= 3) {
            beginRendering();
        } else {
            audioEl.addEventListener('playing', () => {
                if (epochToMatch !== streamEpoch) return;
                beginRendering();
            }, { once: true });
            setTimeout(() => { 
                if (epochToMatch !== streamEpoch) return;
                if (!readyToRender) beginRendering(); 
            }, 500);
        }
    } else {
        beginRendering();
    }
};

// ═══════════════════════════════════════
//  STREAM CONTROL
// ═══════════════════════════════════════

function startStream() {
    if (state !== 'IDLE') return;
    overlay.classList.add('hidden');
    statusEl.textContent = 'Connecting...';
    statusEl.style.color = 'var(--accent-color)';
    connectWebSocket();
}

function connectWebSocket() {
    frameBuffer.length = 0;
    frameCount = 0;
    currentFps = 0;

    // Audio is loaded later in INIT handler (Audio Ready Gate).
    // Don't preload here — causes race conditions with vol=0 (204 response).

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${location.host}/ws?codec=adaptive`);
    ws.binaryType = 'arraybuffer';

    ws.onmessage = (event) => {
        if (typeof event.data === 'string') {
            if (event.data.startsWith('Error:')) {
                hideToast();  // drop any "fetching..." notice; the fetch failed
                statusEl.textContent = event.data;
                statusEl.style.color = '#ff0000';
                if (ws) ws.close();
                setTimeout(() => finishStream(), 3000);
                return;
            }
            if (event.data.startsWith('INIT:')) {
                // First INIT means frames are about to flow — drop any
                // "fetching..." notification the server sent earlier.
                hideToast();
                const p = event.data.split(':');
                targetFps = parseFloat(p[1]);
                frameInterval = 1000 / targetFps;
                renderMode = parseInt(p[2]);
                pixelMode = (p.length > 5 && parseInt(p[5]) === 1);
                if (btnCopy) {
                    btnCopy.disabled = pixelMode;
                    btnCopy.title = pixelMode
                        ? 'Copy text is only available in ASCII modes'
                        : 'Copy current ASCII frame';
                }
                const currentQueueIndex = (p.length > 6) ? parseInt(p[6]) : null;
                duration = (p.length > 7) ? parseFloat(p[7]) : 0;
                const startOffset = (p.length > 8) ? parseFloat(p[8]) : 0;
                const isWebcam = (p.length > 9 && parseInt(p[9]) === 1);
                isWebcamStream = isWebcam;
                currentQueueIdx = currentQueueIndex !== null ? currentQueueIndex : 0;
                
                if (seekBar) {
                    seekBar.max = duration;
                    seekBar.value = 0;
                }
                if (timeTotal) timeTotal.textContent = formatTime(duration);
                if (timeCurrent) timeCurrent.textContent = "00:00";
                if (seekPlayed) seekPlayed.style.transform = 'scaleX(0)';
                
                if (typeof filterPixelBtn !== 'undefined' && filterPixelBtn) {
                    filterPixelBtn.dataset.active = pixelMode ? 'true' : 'false';
                    filterPixelBtn.textContent = pixelMode ? 'ON' : 'OFF';
                    
                    if (isWebcam) {
                        filterPixelBtn.disabled = true;
                        filterPixelBtn.style.opacity = '0.5';
                        filterPixelBtn.style.cursor = 'not-allowed';
                        filterPixelBtn.title = 'Pixel mode toggle is disabled during live webcam feed';
                    } else {
                        filterPixelBtn.disabled = false;
                        filterPixelBtn.style.opacity = '1';
                        filterPixelBtn.style.cursor = 'pointer';
                        filterPixelBtn.title = '';
                    }
                }

                audioOffset = startOffset;
                frameBuffer.length = 0;
                framesInFlight = 0;
                streamEpoch++; // Invalidate any pending audio loads
                scrubMeta = null; // reset so new video gets fresh thumbnails
                // Lazy-load hover thumbnails: only fetch on first hover
                const qIdx = currentQueueIdx;
                if (seekWrap && !scrubMeta) {
                    seekWrap.addEventListener('mouseenter', () => {
                        if (!scrubMeta) setupScrub(qIdx);
                    }, { once: true });
                }
                
                buildCanvas(parseInt(p[3]), parseInt(p[4]));

                // Initialize adaptive codec decoder (pixel=3 bytes, ASCII color=4 bytes)
                // Pixel mode explicitly bypasses the codec for maximum raw throughput
                if (typeof AscilineCodec !== 'undefined' && renderMode > 1 && !pixelMode) {
                    codecDecoder = AscilineCodec.makeDecoder(4);
                } else {
                    codecDecoder = null;
                }

                // Sequential decode queue — reset on each new stream so deltas
                // never race ahead of keyframes across playlist transitions.
                decodeQueue = Promise.resolve();

                // ── AUDIO READY GATE ──
                // Hold rendering until audio actually starts. Without this, the
                // master clock (audioEl.currentTime) snaps forward the moment audio
                // loads, making renderFrame think it's behind and draining the whole
                // buffer in one go — visible as a sudden freeze on heavy footage.
                // The decodeQueue above already prevents delta/keyframe races, so
                // restoring the gate does NOT bring back the old startup stutter.
                const wasPaused = (state === 'PAUSED');
                readyToRender = false;
                if (!wasPaused) state = 'PLAYING';



                    if (audioEl && !isWebcam) {
                        audioEl.pause();
                        const qs = currentQueueIndex !== null ? `v=${currentQueueIndex}&` : '';
                        const st = startOffset > 0 ? `start=${startOffset}&` : '';
                        audioEl.src = `/audio?${qs}${st}t=${Date.now()}`;
                        audioEl.volume = volumeSlider ? volumeSlider.value : 1.0;
                        audioEl.load();
                        // VIDEO GATE: Do not play audio or start rendering yet!
                        // We will wait until the very first video frame has arrived
                        // and been decoded into the frameBuffer.
                    }

                // Re-push prefs after INIT — server filter state starts at defaults
                // on each new stream, so a restored UI alone would not affect frames.
                sendFilters();

                return;

            }

            // STATUS:<CODE>:<human-readable text> — e.g. the server tells us a
            // YouTube/ytdlp fetch is starting. MUST return here: any string that
            // falls through below is parsed as a Mode-1 text frame.
            if (event.data.startsWith('STATUS:')) {
                const msgStart = event.data.indexOf(':', 7);
                if (msgStart !== -1) showToast(event.data.slice(msgStart + 1), true);
                return;
            }

            // Mode 1: Text Frame with Timestamp
            const text = event.data;
            const newlineIdx = text.indexOf('\n');
            const frameIndex = parseInt(text.substring(0, newlineIdx));
            const frameTime = frameIndex / targetFps;
            const frameData = text.substring(newlineIdx + 1);
            frameBuffer.push({ data: frameData, time: frameTime });
            triggerPlaybackStart(streamEpoch);
        } else {
            // Binary Frames — decoded via adaptive codec (raw/zlib/delta)
            if (codecDecoder) {
                framesInFlight++;
                // Chain onto the sequential queue so deltas always patch the
                // correct preceding frame, never racing ahead of a keyframe.
                decodeQueue = decodeQueue.then(() =>
                    codecDecoder.decode(event.data).then(({ frameIndex, frame }) => {
                        framesInFlight--;
                        const frameTime = frameIndex / targetFps;
                        frameBuffer.push({ data: frame, time: frameTime });
                        triggerPlaybackStart(streamEpoch);
                    }).catch(e => {
                        framesInFlight--;
                        console.error("Decode error", e);
                    })
                );
            } else {
                // Fallback: legacy 4-byte header
                const buffer = event.data;
                const view = new DataView(buffer);
                const frameIndex = view.getUint32(0, false);
                const frameTime = frameIndex / targetFps;
                const frameData = new Uint8Array(buffer, 4);
                frameBuffer.push({ data: frameData, time: frameTime });
                triggerPlaybackStart(streamEpoch);
            }
        }

        while (frameBuffer.length > BUFFER_SIZE * 5) frameBuffer.shift();
    };

    ws.onopen = () => { statusEl.textContent = 'Buffering...'; };

    ws.onclose = (event) => {
        if (state === 'PLAYING' || state === 'PAUSED') {
            // 1000 = server closed cleanly after finishing the queue. Anything
            // else (1006 dropped TCP, 1001/1012 server shutdown) is a real drop.
            const ended = event.code === 1000;
            if (!ended) showToast('Connection lost.');
            statusEl.textContent = ended ? 'Stream Ended.' : 'Connection lost.';
            statusEl.style.color = '#888';
            if (audioEl) audioEl.pause();
            setTimeout(() => finishStream(), 800);
        }
    };

    ws.onerror = () => {
        showToast('Connection lost.');
        statusEl.textContent = 'Connection Error!';
        statusEl.style.color = '#ff0000';
        setTimeout(() => finishStream(), 2000);
    };
}

// ═══════════════════════════════════════
//  RENDER LOOP
// ═══════════════════════════════════════

function renderFrame(now) {
    if (state !== 'PLAYING' || !readyToRender) return;
    requestAnimationFrame(renderFrame);

    const masterClock = getMasterClock();

    if (!isSeeking && seekBar) {
        if (now - lastUiUpdateTime >= 100) {
            seekBar.value = masterClock;
            if (seekPlayed && duration) seekPlayed.style.transform = `scaleX(${Math.min(1, masterClock / duration)})`;
            lastUiUpdateTime = now;
        }
        const formattedTime = formatTime(masterClock);
        if (timeCurrent && formattedTime !== lastFormattedTime) {
            timeCurrent.textContent = formattedTime;
            lastFormattedTime = formattedTime;
        }
    }

    if (frameBuffer.length === 0) return;

    let frameObj;

    if (isWebcamStream) {
        // ZERO-LATENCY MODE: Grab the absolute newest frame, trash all older ones.
        frameObj = frameBuffer.pop();
        frameBuffer.length = 0; // Empty the buffer
    } else {
        // A/V Sync: Drop frames that are too far behind the master clock (catch up)
        while (frameBuffer.length > 0 && frameBuffer[0].time < masterClock - 0.1) {
            frameBuffer.shift();
        }
        
        if (frameBuffer.length === 0) return;

        // A/V Sync: Wait if the frame is in the future
        if (frameBuffer[0].time > masterClock + 0.05) {
            return;
        }

        frameObj = frameBuffer.shift();
    }

    const frame = frameObj.data;

    frameCount++;
    if (now - lastFpsUpdate >= 1000) {
        currentFps = frameCount;
        frameCount = 0;
        lastFpsUpdate = now;
        const modes = { 2: '64 Color', 3: '512 Color', 4: '32K Color', 5: '262K Color', 6: '16M Ultra' };
        const label = (modes[renderMode] || 'B&W') + (pixelMode ? ' PIXEL' : '');
        statusEl.textContent = `FPS: ${currentFps}/${Math.round(targetFps)} | Buf: ${frameBuffer.length} | ${label}`;
    }

    lastRenderTime = now;

    if (pixelMode) {
        // ── ZERO-COPY PIXEL MODE ──
        // Server sends raw BGR (3 bytes/pixel). We swap B↔R here.
        const view = frame; // Already a Uint8Array
        const data = dotImageData.data;
        // view: [B,G,R, B,G,R, ...] → data: [R,G,B,A, R,G,B,A, ...]
        for (let src = 0, dst = 0; src < view.length; src += 3, dst += 4) {
            data[dst]     = view[src + 2]; // R (from BGR)
            data[dst + 1] = view[src + 1]; // G
            data[dst + 2] = view[src];     // B
            // Alpha already set to 255 in buildCanvas
        }
        ctx.putImageData(dotImageData, 0, 0);
    } else if (renderMode === 1) {
        player.style.display = 'block';
        player.style.color = '#fff';
        player.textContent = frame;
    } else {
        // ── STANDARD COLOR MODES (2-5): fillText per character ──
        const view = frame; // Already a Uint8Array
        
        // 1. Draw Canvas (Background)
        ctx.fillStyle = '#050505';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.font = 'bold 8px Courier New';
        ctx.textBaseline = 'top';

        let col = 0, row = 0, prevPacked = -1;
        for (let idx = 0; idx < view.length; idx += 4) {
            const packed = (view[idx+1] << 16) | (view[idx+2] << 8) | view[idx+3];
            if (packed !== prevPacked) {
                ctx.fillStyle = `rgb(${view[idx+1]},${view[idx+2]},${view[idx+3]})`;
                prevPacked = packed;
            }
            ctx.fillText(CHAR_LUT[view[idx]], xPos[col], yPos[row]);
            
            // Fill Selection Buffer (char code is at view[idx])
            selectionBuffer[row * (gridCols + 1) + col] = view[idx];

            col++;
            if (col >= gridCols) { col = 0; row++; }
        }

        // 2. Update Selection Layer (Foreground)
        player.style.display = 'block';
        player.style.color = 'transparent';
        player.textContent = textDecoder.decode(selectionBuffer);
    }
}

// ═══════════════════════════════════════
//  CLEANUP
// ═══════════════════════════════════════

// ── BACKPRESSURE REPORTING ──
// Tell the server how many frames are currently stuck in the decode pipeline
// (framesInFlight). When it grows, the client is CPU-bound, and the server 
// drops frames instead of making us inflate+delta-patch them.
let framesInFlight = 0;
// Sequential promise chain that serialises async codec decodes so a fast
// Delta never races ahead of a slow Keyframe/ZLIB inflate.
let decodeQueue = Promise.resolve();

function startBufferReports() {
    stopBufferReports();
    bufferReportTimer = setInterval(() => {
        if (ws && ws.readyState === WebSocket.OPEN && state === 'PLAYING') {
            ws.send(JSON.stringify({ type: 'buffer', depth: framesInFlight }));
        }
    }, 250);
}

function stopBufferReports() {
    if (bufferReportTimer) { clearInterval(bufferReportTimer); bufferReportTimer = null; }
}

// Unexpected disconnect toast. Lazy-created; safe to call twice (onerror+onclose).
// Pass sticky=true to keep it up until hideToast() (e.g. a fetch in progress).
let toastHideTimer = null;
let toastSticky = false;
function showToast(msg, sticky) {
    let el = document.getElementById('connection-toast');
    if (!el) {
        el = document.createElement('div');
        el.id = 'connection-toast';
        el.className = 'toast';
        el.setAttribute('role', 'status');
        el.setAttribute('aria-live', 'polite');
        container.appendChild(el);
    }
    el.textContent = msg;
    el.classList.add('show');
    toastSticky = !!sticky;
    clearTimeout(toastHideTimer);
    toastHideTimer = null;
    if (!sticky) {
        toastHideTimer = setTimeout(() => el.classList.remove('show'), 4000);
    }
}

function hideToast() {
    clearTimeout(toastHideTimer);
    toastHideTimer = null;
    toastSticky = false;
    const el = document.getElementById('connection-toast');
    if (el) el.classList.remove('show');
}

// Clear only a sticky (fetch-in-progress) toast — leaves a transient
// "Connection lost." toast to run out its own timer.
function hideStickyToast() {
    if (toastSticky) hideToast();
}

function finishStream() {
    state = 'IDLE';
    stopBufferReports();
    hideStickyToast();  // a fetch notice must never outlive the stream
    if (ws) { ws.onclose = null; ws.close(); ws = null; }
    if (audioEl) { audioEl.pause(); audioEl.src = ''; }
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    player.textContent = '';
    player.style.display = 'none';
    container.classList.remove('paused');
    overlay.classList.remove('hidden');
    statusEl.textContent = 'Ready';
    statusEl.style.color = 'rgba(255,255,255,0.6)';
    if (playPauseBtn) playPauseBtn.textContent = '▶';
    readyToRender = false;
    pauseStartTime = 0;
    frameBuffer.length = 0;
}

// ═══════════════════════════════════════
//  PAUSE / RESUME
// ═══════════════════════════════════════

function togglePause() {
    if (isWebcamStream) return; // Can't pause a live broadcast!
    if (state === 'PLAYING') {
        state = 'PAUSED';
        pauseStartTime = performance.now();
        
        if (audioEl && !audioEl.paused) {
            audioEl.pause();
        }
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'pause', paused: true }));
        }
        container.classList.add('paused');
        if (playPauseBtn) playPauseBtn.textContent = '▶';
        statusEl.textContent = '❚❚ PAUSED';
        statusEl.style.color = '#888';
    } else if (state === 'PAUSED') {
        state = 'PLAYING';
        readyToRender = true; // resuming an existing stream — don't block on audio gate
        
        // Update streamStartTime to account for the pause duration
        const pauseDuration = performance.now() - pauseStartTime;
        streamStartTime += pauseDuration;
        pauseStartTime = 0;
        
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'pause', paused: false }));
        }
        
        // Restore audio playback
        if (audioEl && audioEl.paused) {
            audioEl.play().catch(() => {});
        }

        // Flush stale buffer frames — A/V sync catch-up handles the rest
        frameBuffer.length = 0;
        
        container.classList.remove('paused');
        statusEl.textContent = 'Resuming...';
        statusEl.style.color = 'var(--accent-color)';
        
        // Restart render loop
        if (playPauseBtn) playPauseBtn.textContent = '❚❚';
        lastRenderTime = performance.now();
        lastFpsUpdate = performance.now();
        frameCount = 0;
        requestAnimationFrame(renderFrame);
    }
}

if (playPauseBtn) {
    playPauseBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (state === 'IDLE') startStream();
        else togglePause();
    });
}

// Seek to an absolute time. Reuses the live seek (tell the server, then reload
// the audio from that point). Shared by the slider and the skip buttons.
function doSeek(targetSec) {
    if (isWebcamStream) return; // Can't seek a live broadcast!
    if (duration) targetSec = Math.max(0, Math.min(targetSec, duration));
    if (seekBar) seekBar.value = targetSec;
    if (seekPlayed && duration) seekPlayed.style.transform = `scaleX(${Math.min(1, targetSec / duration)})`;

    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'seek', time: targetSec }));
    }

    // Drop stale frames, then restart audio from the seek point
    frameBuffer.length = 0;
    audioOffset = targetSec;

    if (audioEl) {
        audioEl.pause();
        streamEpoch++;
        const myEpoch = streamEpoch;
        audioEl.src = `/audio?v=${currentQueueIdx}&start=${targetSec}&t=${Date.now()}`;
        audioEl.load();

        if (state === 'PLAYING') {
            readyToRender = false;
            audioEl.play().catch(() => {});
            const onAudioStart = () => {
                if (!readyToRender) {
                    readyToRender = true;
                    streamStartTime = performance.now() - (targetSec * 1000.0);
                    lastRenderTime = performance.now();
                    lastFpsUpdate = performance.now();
                    frameCount = 0;
                    requestAnimationFrame(renderFrame);
                }
            };
            if (audioEl.readyState >= 3) onAudioStart();
            else {
                audioEl.addEventListener('playing', () => {
                    if (myEpoch !== streamEpoch) return;
                    onAudioStart();
                }, { once: true });
                setTimeout(() => {
                    if (myEpoch !== streamEpoch) return;
                    onAudioStart();
                }, 500);
            }
        } else {
            streamStartTime = performance.now() - (targetSec * 1000.0);
            if (state === 'PAUSED') pauseStartTime = performance.now();
        }
    } else {
        streamStartTime = performance.now() - (targetSec * 1000.0);
        if (state === 'PAUSED') pauseStartTime = performance.now();
    }
}

function getMasterClock() {
    // audioEl.currentTime is frozen when paused — correct in both states.
    // Only fall back to the wall-clock estimate when audio hasn't loaded yet.
    if (audioEl && audioEl.readyState >= 1) return audioEl.currentTime + audioOffset;
    return (performance.now() - streamStartTime) / 1000.0;
}

function skip(delta) {
    if (state !== 'PLAYING' && state !== 'PAUSED') return;
    if (!duration) return;
    doSeek(getMasterClock() + delta);
}

// Pull the hover thumbnail sprite for this video (built lazily by the server).
function setupScrub(v) {
    scrubMeta = null;
    if (seekPreviewImg) seekPreviewImg.style.backgroundImage = '';
    fetch('/scrub?v=' + (v || 0) + '&t=' + Date.now()).then(r => r.json()).then(m => {
        if (!m || !m.available || !seekPreviewImg) return;
        scrubMeta = m;
        seekPreviewImg.style.width = m.cellW + 'px';
        seekPreviewImg.style.height = m.cellH + 'px';
        seekPreviewImg.style.backgroundImage = `url(${m.sprite})`;
        seekPreviewImg.style.backgroundSize = (m.gridCols * m.cellW) + 'px ' + (m.gridRows * m.cellH) + 'px';
    }).catch(() => {});
}

function onSeekHover(e) {
    if (!scrubMeta || !duration || !seekWrap) return;
    const rect = seekWrap.getBoundingClientRect();
    const x = Math.max(0, Math.min(e.clientX - rect.left, rect.width));
    const time = (x / rect.width) * duration;
    const idx = Math.max(0, Math.min(Math.floor(time / scrubMeta.interval), scrubMeta.count - 1));
    const col = idx % scrubMeta.gridCols, row = Math.floor(idx / scrubMeta.gridCols);
    seekPreviewImg.style.backgroundPosition = `-${col * scrubMeta.cellW}px -${row * scrubMeta.cellH}px`;
    seekPreviewTime.textContent = formatTime(time);
    const half = scrubMeta.cellW / 2;
    seekPreview.style.left = Math.max(half, Math.min(x, rect.width - half)) + 'px';
    seekPreview.classList.add('show');
}

if (seekBar) {
    seekBar.addEventListener('input', () => {
        isSeeking = true;
        if (timeCurrent) timeCurrent.textContent = formatTime(seekBar.value);
    });
    seekBar.addEventListener('change', () => {
        doSeek(parseFloat(seekBar.value));
        isSeeking = false;
    });
}

if (btnBack) btnBack.addEventListener('click', (e) => { e.stopPropagation(); skip(-10); });
if (btnFwd)  btnFwd.addEventListener('click', (e) => { e.stopPropagation(); skip(10); });

if (btnCopy) {
    btnCopy.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (pixelMode) return;

        try {
            await navigator.clipboard.writeText(player.textContent);
            btnCopy.textContent = 'COPIED!';
        } catch (error) {
            console.error('Failed to copy ASCII frame:', error);
            btnCopy.textContent = 'COPY FAILED';
        }

        clearTimeout(copyFeedbackTimer);
        copyFeedbackTimer = setTimeout(() => { btnCopy.textContent = 'COPY'; }, 1500);
    });
}

if (seekWrap) {
    seekWrap.addEventListener('mousemove', onSeekHover);
    seekWrap.addEventListener('mouseleave', () => { if (seekPreview) seekPreview.classList.remove('show'); });
}

// ── EVENT LISTENERS ──
overlay.addEventListener('click', (e) => {
    e.stopPropagation();
    startStream();
});

// ── PAUSE TOGGLE (click on player area) ──
container.addEventListener('click', (e) => {
    if (e.target.closest('#play-overlay')) return;
    if (window.getSelection().toString().length > 0) return;
    togglePause();
});

// ── KEYBOARD: Space to pause, Arrows to seek, F for filters ──
document.addEventListener('keydown', (e) => {
    if (state === 'PLAYING' || state === 'PAUSED') {
        if (e.code === 'Space') {
            e.preventDefault();
            togglePause();
        } else if (e.code === 'ArrowRight') {
            e.preventDefault();
            btnFwd.click(); // Trigger forward 10s logic
        } else if (e.code === 'ArrowLeft') {
            e.preventDefault();
            btnBack.click(); // Trigger backward 10s logic
        }
    }
});

const PREF_VOLUME = 'asciline_volume';
const PREF_FILTERS = 'asciline_filters';

if (volumeSlider) {
    const savedVol = localStorage.getItem(PREF_VOLUME);
    if (savedVol !== null) {
        const vol = Math.min(1, Math.max(0, parseFloat(savedVol)));
        if (!Number.isNaN(vol)) {
            volumeSlider.value = vol;
            if (audioEl) audioEl.volume = vol;
        }
    }
    volumeSlider.addEventListener('input', () => {
        if (audioEl) audioEl.volume = volumeSlider.value;
        localStorage.setItem(PREF_VOLUME, volumeSlider.value);
    });
}

window.addEventListener('resize', () => {
    const syncSize = (el) => {
        if (!el) return;
        el.style.width  = container.clientWidth + 'px';
        el.style.height = container.clientHeight + 'px';
    };
    syncSize(canvas);
    syncSize(player);
});

// ═══════════════════════════════════════
//  FILTER MENU (Contrast / Gamma / Brightness / Invert / Palette)
// ═══════════════════════════════════════

const filterMenu       = document.getElementById('filter-menu');
const btnFilters       = document.getElementById('btn-filters');
const filterClose      = document.getElementById('filter-close');
const filterContrast   = document.getElementById('filter-contrast');
const filterGamma      = document.getElementById('filter-gamma');
const filterBrightness = document.getElementById('filter-brightness');
const filterSharpness  = document.getElementById('filter-sharpness');
const filterInvertBtn  = document.getElementById('filter-invert');
const filterPixelBtn   = document.getElementById('filter-pixel');
const contrastVal      = document.getElementById('filter-contrast-val');
const gammaVal         = document.getElementById('filter-gamma-val');
const brightnessVal    = document.getElementById('filter-brightness-val');
const sharpnessVal     = document.getElementById('filter-sharpness-val');
const filterReset      = document.getElementById('filter-reset');
const paletteRadios    = document.querySelectorAll('input[name="palette"]');

let currentFilters = { contrast: 1.0, gamma: 1.0, brightness: 0, invert: false, sharpness: 0, palette: 'default' };
let filterSendTimer = null;

function syncFilterUI() {
    if (filterContrast)   filterContrast.value = currentFilters.contrast;
    if (filterGamma)      filterGamma.value = currentFilters.gamma;
    if (filterBrightness) filterBrightness.value = currentFilters.brightness;
    if (filterSharpness)  filterSharpness.value = currentFilters.sharpness;
    if (contrastVal)      contrastVal.textContent = Number(currentFilters.contrast).toFixed(2);
    if (gammaVal)         gammaVal.textContent = Number(currentFilters.gamma).toFixed(2);
    if (brightnessVal) {
        const v = currentFilters.brightness;
        brightnessVal.textContent = (v > 0 ? '+' : '') + v;
    }
    if (sharpnessVal) sharpnessVal.textContent = String(currentFilters.sharpness);
    if (filterInvertBtn) {
        filterInvertBtn.dataset.active = currentFilters.invert ? 'true' : 'false';
        filterInvertBtn.textContent = currentFilters.invert ? 'ON' : 'OFF';
    }
    paletteRadios.forEach(r => { r.checked = (r.value === currentFilters.palette); });
}

// Restore filter prefs before any user interaction (WS re-apply happens on INIT).
(() => {
    const raw = localStorage.getItem(PREF_FILTERS);
    if (!raw) return;
    try {
        const f = JSON.parse(raw);
        const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));
        const contrast = clamp(parseFloat(f.contrast), 0.1, 3.0);
        const gamma = clamp(parseFloat(f.gamma), 0.1, 3.0);
        const brightness = clamp(parseInt(f.brightness, 10), -100, 100);
        const sharpness = clamp(parseInt(f.sharpness, 10), 0, 10);
        const palette = ['default', 'flat', 'block'].includes(f.palette) ? f.palette : 'default';
        if ([contrast, gamma, brightness, sharpness].some(Number.isNaN)) return;
        currentFilters = {
            contrast, gamma, brightness, sharpness, palette,
            invert: Boolean(f.invert)
        };
        syncFilterUI();
    } catch (_) { /* ignore corrupt prefs */ }
})();

function toggleFilterMenu() {
    if (filterMenu) filterMenu.classList.toggle('open');
}

if (btnFilters) btnFilters.addEventListener('click', (e) => { e.stopPropagation(); toggleFilterMenu(); });
if (filterClose) filterClose.addEventListener('click', (e) => { e.stopPropagation(); toggleFilterMenu(); });

// Prevent clicks inside the filter menu from toggling pause
if (filterMenu) filterMenu.addEventListener('click', (e) => e.stopPropagation());

// Debounced filter send — batches rapid slider drags into one WS message
function sendFilters() {
    localStorage.setItem(PREF_FILTERS, JSON.stringify(currentFilters));
    if (filterSendTimer) clearTimeout(filterSendTimer);
    filterSendTimer = setTimeout(() => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type:       'filter',
                contrast:   currentFilters.contrast,
                gamma:      currentFilters.gamma,
                brightness: currentFilters.brightness,
                invert:     currentFilters.invert,
                sharpness:  currentFilters.sharpness,
                palette:    currentFilters.palette
            }));
        }
        filterSendTimer = null;
    }, 60);
}

if (filterContrast) {
    filterContrast.addEventListener('input', () => {
        currentFilters.contrast = parseFloat(filterContrast.value);
        if (contrastVal) contrastVal.textContent = currentFilters.contrast.toFixed(2);
        sendFilters();
    });
}

if (filterGamma) {
    filterGamma.addEventListener('input', () => {
        currentFilters.gamma = parseFloat(filterGamma.value);
        if (gammaVal) gammaVal.textContent = currentFilters.gamma.toFixed(2);
        sendFilters();
    });
}

if (filterBrightness) {
    filterBrightness.addEventListener('input', () => {
        currentFilters.brightness = parseInt(filterBrightness.value, 10);
        if (brightnessVal) {
            const v = currentFilters.brightness;
            brightnessVal.textContent = (v > 0 ? '+' : '') + v;
        }
        sendFilters();
    });
}

if (filterSharpness) {
    filterSharpness.addEventListener('input', () => {
        currentFilters.sharpness = parseInt(filterSharpness.value, 10);
        if (sharpnessVal) sharpnessVal.textContent = currentFilters.sharpness;
        sendFilters();
    });
}

if (filterInvertBtn) {
    filterInvertBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        currentFilters.invert = !currentFilters.invert;
        filterInvertBtn.dataset.active = currentFilters.invert ? 'true' : 'false';
        filterInvertBtn.textContent = currentFilters.invert ? 'ON' : 'OFF';
        sendFilters();
    });
}

if (filterPixelBtn) {
    filterPixelBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (ws && ws.readyState === WebSocket.OPEN) {
            const nextMode = !pixelMode;
            filterPixelBtn.dataset.active = nextMode ? 'true' : 'false';
            filterPixelBtn.textContent = nextMode ? 'ON' : 'OFF';
            const currentAbsTime = getMasterClock();
            ws.send(JSON.stringify({
                type: 'reinit',
                pixel: nextMode,
                time: currentAbsTime
            }));
        }
    });
}

paletteRadios.forEach(radio => {
    radio.addEventListener('change', () => {
        currentFilters.palette = radio.value;
        sendFilters();
    });
});

if (filterReset) {
    filterReset.addEventListener('click', (e) => {
        e.stopPropagation();
        currentFilters = { contrast: 1.0, gamma: 1.0, brightness: 0, invert: false, sharpness: 0, palette: 'default' };
        syncFilterUI();
        sendFilters();
    });
}

// Keyboard shortcut: 'F' to toggle filter menu
document.addEventListener('keydown', (e) => {
    if (e.code === 'KeyF' && !e.ctrlKey && !e.metaKey && !e.altKey) {
        if (state === 'PLAYING' || state === 'PAUSED') {
            e.preventDefault();
            toggleFilterMenu();
        }
    }
});
