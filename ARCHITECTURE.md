# ASCILINE architecture

This describes the source checkout. Commands are in the [live guide](docs/LIVE_STREAMING.md), [SDK guide](docs/SDK.md) and [Rust build guide](rust_core/README.md). Historical benchmark notes describe particular experiments, not current defaults.

## 0. Philosophy

ASCILINE turns moving images into programmable characters and color cells. Developers work with fonts, palettes, cells, selection layers and time-based effects. Pixel mode retains direct image-grid access through Canvas ImageData. Rust and DCT make these representations practical to produce and transport.

The purpose is creative and algorithmic control through this representation. Standard video can also be processed on Canvas; claims of distinctive control should identify the concrete advantage of characters/cells. Performance claims require source, grid, quality and runtime conditions. The live player draws without an HTML video element, while source media uses a server decoder, DCT reconstruction uses JavaScript and audio uses a browser media element. Canvas acceleration depends on the browser/platform; zero GPU is not a verified guarantee.

## 1. Components and governance

| Component | Source | Responsibility |
| --- | --- | --- |
| Server | `stream_server.py` | FastAPI/endpoints, source queue configuration, socket session and pacing |
| Engine adapter | `asciline/engines.py` | Python/Rust/auto and native loading |
| Native engine | `rust_core/src/` | File decode, mapping and encoding |
| Source queue | `asciline/source_queue.py` | Bounded worker owning decoder reads |
| Pixel profile | `asciline/pixel_profile.py` | Session predictor, alignment and live indices |
| Playback | `asciline/playback.py`, `asciline/commands.py` | FPS sampling and pending seek coalescing |
| Runtime/diagnostics | `asciline/media_runtime.py`, `asciline/perf_trace.py` | FFmpeg path and JSONL timing |
| Root client | `app.js`, `codec.js`, `index.html` | Standalone showcase session/renderer |
| SDK | `src/asciline-player.js`, `src/live-session.js` | Reusable player and live transport/timeline |
| Component | `src/ascf-element.js` | Packaged HTML wrapper |
| Static | `compiler.py`, `static_player/` | Compile, reader/player and browser Studio |

**Golden Master governance:** root `app.js`, `codec.js` and `index.html` are the standalone showcase reference. Refactoring or migrating this client into the SDK requires explicit maintainer approval. Protocol parity does not imply the root UI has been replaced by the SDK.

## 2. Live pipeline

```text
Source file
  -> Python/OpenCV or Rust/FFmpeg decode + resize + BGR conversion
  -> optional bounded source queue (Rust pixel files only)
  -> ASCII map/adaptive encode OR raw BGR OR pixel DCT
  -> session WebSocket packets
  -> ordered JavaScript reconstruction
  -> Canvas text or BGR-to-RGBA ImageData

Source audio -> /audio FFmpeg process -> audio element -> playback clock
```

Python is default; Rust optional; auto permits fallback. Webcam uses OpenCV. Source-file compression, ASCILINE encoding and browser reconstruction have different costs. Native source PTS is exposed, but live packets currently schedule by nominal effective FPS; constant-rate sources are the validated baseline.

Mode 1 sends text; 2–6 use character/RGB cells with quantized values. Pixel mode draws BGR through ImageData, without glyphs, and defaults to raw. ASCII ceiling is 30 FPS, Rust pixel 60, Python pixel 30. Explicit FPS samples without interpolation. Automatic dimensions are capped; DCT aligns upward to multiples of 16.

## 3. Handshake and packets

### Capabilities

Adaptive ASCII requests `codec=adaptive`; sync requests `sync=1`. Pixel DCT additionally requests `pixel_codec=dct-v1`, with server codec dct and active pixel file input. Otherwise the supported legacy/raw path is used. Current root client/local SDK advertise DCT; older published clients may stay raw.

### INIT

```text
INIT:<fps>:<mode>:<cols>:<rows>:<pixel>:<queue>:<duration>:<offset>:<webcam>[:<syncId>[:<pixelCodec>]]
```

Pixel/webcam are 0/1; offset is seconds. Sync ID is added for synchronized files. Profile-capable clients get raw/dct suffix. New INIT rebuilds dimensions and changes the client's timeline epoch.

| Representation | Packet |
| --- | --- |
| Text | `<frameIndex>\n<lines>` |
| Legacy ASCII color | BE uint32 index + character/RGB cells |
| Live raw pixels | BE uint32 index + BGR cells |
| Adaptive ASCII | BE uint32 index + uint8 tag + payload |
| DCT pixels | BE uint32 playback index + tag 4 + zlib profile |

Tags 0/1/2/3 are RAW/ZLIB/DELTA/RLE_FULL. Tag 4 is the lossy pixel profile. Untagged live RAW pixels differ from adaptive packets; decode according to negotiated representation.

### DCT internals

BGR becomes full-range Y/Cb/Cr with 4:2:0 chroma. Planes use 8x8 blocks. Keyframes predict a constant; predictive frames use previous reconstructed planes. Luma searches integer motion within radius three; chroma uses its colocated previous block. Residuals are transformed and quantized by quality tables/dead-zone handling, zigzag/run-length coded with differential DC values. Skipped predictive blocks reuse reconstruction. Zlib compresses the payload; keyframes carry quality/dimensions.

Encoder reconstruction is the next predictor. Original source planes would diverge from decoder state. Python/Rust/JavaScript agreement is checked by packets and reconstructed BGR. Normal keyframe interval is 48 encoded frames. Live indices are rewritten to playback indices: encoder counters are not media clocks. Every socket owns its predictor, reset on seek/reinit.

## 4. Synchronization and cancellation

Sync sends four preroll frames and waits for matching playback-ready. Client waits for audio playing or uses a wall-clock fallback. Server pacing anchors to the client clock. Clean short EOF can start with fewer preroll frames.

```text
Client -> {type: seek, time: seconds, requestId: newId}
Server -> SEEKED:<requestId>:<actual seconds>
Server -> new keyframe/timeline packets
Client -> {type: playback-ready, requestId: newId, time: clock}
```

Seek stops/joins the source worker before repositioning, resets prediction/pacing/backlog, and starts a segment. Pending seeks are coalesced to latest target. This does not cancel a seek already executing inside FFmpeg; decoder access remains serialized.

LiveSession serializes decoding and discards older-epoch completions. Reinit creates a decoder after INIT. Resume re-seeks to align audio/frames. Dispose cancels startup/render/reports and socket handlers. Draw-late frames can be discarded after reconstruction; predictive packets still require ordered decoding. Source catch-up happens before ordered encoding.

Source queue bounds prepared/in-progress frames plus one consumer frame. It contains no predictors or encoded packets. Source work overlaps encode; consumer source timing becomes queue wait. FFmpeg threads control source decoding separately from queue capacity and DCT.

## 5. Static ASCF and validation

Current compiler output has an 18-byte big-endian header: ASC2 magic, float32 FPS, uint8 mode, uint8 pixel, uint16 columns/rows, uint32 total frames. Packets follow with BE uint32 length prefixes. Total frames are patched at byte offset 14. Legacy ASCF uses a shorter header. Audio is a paired file, not embedded; the header is binary, not JSON.

Static pixel packets use the compiler's tagged path, unlike untagged live RAW pixels. `--profile` enables pixel tag 4. The compiler still uses Python. Browser Studio encodes supported RAW/ZLIB/DELTA and can play other supported tags. Static playback needs no ASCILINE backend, but still has file/loading/buffer/render costs; zero latency and near-zero memory are not guarantees.

See [validation](docs/PERFORMANCE.md#scope-of-validation) and [measurements](docs/PERFORMANCE.md). Windows native build, automated controls/protocol and maintainer playback are verified. Native Linux/macOS, broad device/codec coverage and multi-client capacity need separate measurements. LLM directions remain research ideas, not shipped semantic understanding.
