```text
 █████╗ ███████╗ ██████╗██╗██╗     ██╗███╗   ██╗███████╗  ██
██╔══██╗██╔════╝██╔════╝██║██║     ██║████╗  ██║██╔════╝  █████
███████║███████╗██║     ██║██║     ██║██╔██╗ ██║█████╗    ████████
██╔══██║╚════██║██║     ██║██║     ██║██║╚██╗██║██╔══╝    ████████
██║  ██║███████║╚██████╗██║███████╗██║██║ ╚████║███████╗  █████
╚═╝  ╚═╝╚══════╝ ╚═════╝╚═╝╚══════╝╚═╝╚═╝  ╚═══╝╚══════╝  ██

```
<a href="https://trendshift.io/repositories/50861?utm_source=trendshift-badge&amp;utm_medium=badge&amp;utm_campaign=badge-trendshift-50861" target="_blank" rel="noopener noreferrer"><img src="https://trendshift.io/api/badge/trendshift/repositories/50861/daily?language=Python" alt="YusufB5%2FASCILINE | Trendshift" width="250" height="55"/></a>
<a href="https://trendshift.io/repositories/50861?utm_source=trendshift-badge&amp;utm_medium=badge&amp;utm_campaign=badge-trendshift-50861" target="_blank" rel="noopener noreferrer"><img src="https://trendshift.io/api/badge/trendshift/repositories/50861/weekly?language=Python" alt="YusufB5%2FASCILINE | Trendshift" width="250" height="55"/></a>
<a href="https://trendshift.io/repositories/50861?utm_source=trendshift-badge&amp;utm_medium=badge&amp;utm_campaign=badge-trendshift-50861" target="_blank" rel="noopener noreferrer"><img src="https://trendshift.io/api/badge/trendshift/repositories/50861/daily" alt="YusufB5%2FASCILINE | Trendshift" width="250" height="55"/></a>

**ASCILINE** is a high-performance, cross-platform real-time ASCII video rendering engine. It maps pixels to text-based representations and streams the result over a low-overhead binary protocol, turning the browser canvas into a typographic display surface.

[![Python](https://img.shields.io/badge/python-3.9%2B-blue?logo=python&logoColor=white)](#0-requirements)
[![JavaScript](https://img.shields.io/badge/JavaScript-Vanilla-F7DF1E?logo=javascript&logoColor=black)](https://developer.mozilla.org/en-US/docs/Web/JavaScript)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![OpenCV](https://img.shields.io/badge/OpenCV-5C3EE8?logo=opencv&logoColor=white)](https://opencv.org/)
[![NumPy](https://img.shields.io/badge/NumPy-013243?logo=numpy&logoColor=white)](https://numpy.org/)
[![HTML5 Canvas](https://img.shields.io/badge/HTML5_Canvas-E34F26?logo=html5&logoColor=white)](https://developer.mozilla.org/en-US/docs/Web/API/Canvas_API)
[![License: AGPL v3](https://img.shields.io/badge/Engine_License-AGPL_v3-blue.svg)](LICENSE-AGPL)
[![License: MIT](https://img.shields.io/badge/SDK_License-MIT-green.svg)](LICENSE-MIT)

| Output | Details |
| :--- | :--- |
| <img src="https://github.com/user-attachments/assets/ccc727c9-c697-49f2-85e1-6f8c366f2019" width="400" alt="Original Source" /> | **Original Source**<br>Standard MP4 video file. |
| <img src="https://github.com/user-attachments/assets/6bd7f5c0-81de-49fe-ba0d-9a8872ec8ae3" width="400" alt="ASCII Mode" /> | **ASCII Mode**<br>Rendered using Mode 4 (32K colors) from a 30fps source. |
| <img src="https://github.com/user-attachments/assets/1fd88c3d-97d1-441a-a071-16de24ea82c0" width="400" alt="PIXEL Mode" /> | **PIXEL Mode**<br>Rendered using the `--pixel` flag for high fidelity colored blocks █ . |

## Table of Contents

- [Design Goals](#design-goals)
- [Technical Features](#technical-features)
- [Architecture](#architecture)
- [Adaptive Frame Codec (opt-in, ASCII modes 2-6)](#adaptive-frame-codec-opt-in-ascii-modes-2-6)
- [Standalone Static Web Player](#standalone-static-web-player)
- [JavaScript SDK (asciline-player)](#javascript-sdk-asciline-player)
- [Installation](#installation)
- [Running with Docker](#running-with-docker)
- [Customization](#customization)
- [Troubleshooting](#troubleshooting)
- [Live Demo](#live-demo)
- [Star History](#star-history)
- [Support ❤️](#support)
- [License](#license)
- [Community](#community)
- [Contact](#contact)

## Design Goals

1. **Programmable representation:** expose characters, colors and cells for fonts, palettes, selection layers, effects and experimental interfaces.
2. **Choose detail and cost:** grid size, color depth and transport trade appearance, payload and processing cost. ASCII and pixel have different rendering costs.
3. **Multiple delivery paths:** live server, SDK, terminal and static ASCF provide different ways to use the representation.

ASCILINE's purpose is this representation and the control it enables. Rust and
DCT support that purpose; comparisons quantify costs rather than making standard
codec replacement the goal. Live video draws through Canvas. Source media still
needs server decoding, pixel DCT is reconstructed in JavaScript, and audio remains
subject to browser playback policy. Hardware use and performance depend on grid,
content, browser and machine.

> **Roadmap idea, not implemented yet:** because ASCII output is already a compact, structured text representation, it could in principle serve as a lightweight input for downstream text/LLM processing instead of feeding raw pixel streams to a vision model. Nothing in the current codebase does this — flagging it here as a direction, not a shipped feature.

## Technical Features

- **Python playback paths** for Windows, macOS and Linux; current Rust integration is verified on Windows.
- **Real-time ASCII and pixel streaming**: characters/color cells for typography and BGR grids for direct ImageData drawing.
- **Configurable FPS ceilings**: ASCII 30, Python pixel 30, Rust pixel 60 by default; higher-rate sources are sampled, without interpolation.
- **Synchronized startup and seek**: audio clock when available, wall-clock fallback otherwise, with timeline markers and predictor resets.
- **Custom transport**: raw BGR, adaptive ASCII RAW/ZLIB/DELTA/RLE, and opt-in lossy pixel DCT shared by Python/Rust and local SDK.
- **Optional Rust acceleration**: FFmpeg source decoding, character mapping and native encoding. A bounded decode-ahead queue can overlap source work with ordered encoding.
- **Multiple color modes**: from black & white up to 16M-color high fidelity.
- **Flexible video management**: JSON playlists (per-video mode & volume), folder-based auto-queuing, single-file mode, infinite loop — all via CLI flags.

## Architecture

1. **Backend:** Python/FastAPI owns sessions and endpoints; Python/OpenCV or optional Rust/FFmpeg supplies source decode and encoding.
2. **Clients:** root demo and JavaScript SDK reconstruct ordered packets and draw text/pixels on Canvas.
3. **Sync:** INIT negotiates settings; capable clients use preroll, playback-ready and matching seek markers. Live DCT is capability-negotiated.

ASCILINE uses a modular architecture that separates the core rendering engine from its delivery methods.

```text
ASCILINE/
# --- 1. Core Processing & Codec Engine ---
├── ascii_video_player2.py       # Core VideoDecoder, AsciiMapper & standalone terminal player
├── codec.py                     # Master Python encoder (RAW/ZLIB/DELTA/RLE/DCT)
├── codec.js                     # Root JS decoder (Optimized for Live WebSocket streaming)
│
# --- 2. Live Streaming Web Client ---
├── index.html                   # Web client UI for the live streaming server
├── app.js                       # Frontend WebSocket connection and Canvas render loop
├── style.css                    # UI styling, responsive layout, and real-time FX
│
# --- 3. Standalone Ecosystem & Compilers ---
├── compiler.py                  # CLI Python compiler: Converts videos into .ascf format
├── 📁 static_player/            # Web player for .ascf files
│   ├── index.html               # Main UI for the static web player
│   ├── reader.js                # .ascf file parser, chunk loader, and buffer manager
│   ├── codec.js                 # Standalone JS decoder (Optimized for static buffers)
│   └── 📁 studio/               # Browser-based compiler IDE
│       ├── index.html           # Studio UI with built-in preview and seekbar
│       └── encoder.js           # Client-side encoder to compile videos locally
│
# --- 4. Server & Backend Services ---
├── stream_server.py             # FastAPI sessions, pacing, audio and control endpoints
├── asciline/                    # Engine adapter, source queue, playback policy and timing
├── rust_core/                   # Optional native engine, sources and local build script
├── src/                         # AsciiPlayer SDK, LiveSession and HTML component
├── docs/                        # Live usage, SDK integration and performance guides
├── ytdl.py                      # yt-dlp integration for dynamic YouTube/URL fetching
│
# --- 5. Development & Testing ---
├── 📁 experiments/              # Codec benchmarks, test vectors & experimental scripts
├── 📁 test/                     # E2E tests, unit tests & backpressure validation
│
# --- 6. CLI Assets & Cache ---
├── logo.py                      # ASCII branding banner displayed on startup
├── 📁 videos/                   # Auto-managed local cache directory for downloaded media
│
# --- 7. Configuration & Infrastructure ---
├── playlist.json                # Playback queue and per-video overrides
├── Dockerfile                   # Docker container configuration
├── docker-compose.yml           # Multi-service Docker setup
├── pyproject.toml               # Python project metadata, dependencies & optional extras
└── requirements.txt             # Python dependencies
```

Further protocol and session-ownership details are in [ARCHITECTURE.md](ARCHITECTURE.md).

## Adaptive Frame Codec (opt-in, ASCII modes 2-6)

The original protocol re-sends the full grid every frame. An opt-in adaptive codec picks the smallest of several encodings per frame and tags it with a 1-byte header, without changing the rendered output:

| tag | encoding | best for |
| :-- | :------- | :------- |
| `0`&nbsp;RAW | framebuffer as-is (legacy) | incompressible frames |
| `1`&nbsp;ZLIB | `zlib(framebuffer)` | general motion |
| `2`&nbsp;DELTA | only the cells that changed since the last frame | static / low-motion |
| `3`&nbsp;RLE_FULL | run-length encoded framebuffer | large flat-color regions |
| `4`&nbsp;DCT | Lossy pixel prediction/DCT | Opt-in live pixel transport for capable clients, and static ASCF profile. |

Tags 0–3 describe adaptive ASCII. Tag 4 is a separately negotiated pixel profile: the server enables `--pixel-codec dct`, and a compatible client requests `codec=adaptive&sync=1&pixel_codec=dct-v1`. DCT activates only for pixel file playback. Root demo and local SDK support this negotiation.

Clients opt in with `/ws?codec=adaptive`; omit it and you get the original protocol byte-for-byte, so existing clients are unaffected. Periodic keyframes reset prediction. Predictive packets still require ordered decoding; sync resets predictors explicitly after seek/reinit.

`codec.js` (the shared decoder used by both the live player and the test suite) understands tags 0–4. **Not every encoder produces every tag**, though: the Python side (`codec.py`, used by the live server and by `compiler.py`) can emit RLE_FULL when it wins the size comparison. The browser-side JS encoder (`static_player/studio/encoder.js`, used by the client-only Studio compiler) intentionally only emits RAW/ZLIB/DELTA — it doesn't implement RLE run-building, to keep the in-browser encoder simple. RAW/ZLIB/DELTA already cover most cases reasonably well, so this is a deliberate simplicity/size trade-off, not a bug — decoders stay permissive, encoders stay conservative.

**Measured wire savings** (mode 6, 200×80 grid):

| content | vs. legacy |
| :------ | :--------- |
| static screen / slideshow | **0.3%** (≈375×) |
| high-motion / full-frame change | 63% (never worse than legacy) |

An optional `--quality {lossless,high,balanced,low}` enables lossy *temporal delta*: a color cell is only re-sent once it drifts past a tolerance from what the viewer already sees (the character plane stays exact), cutting the hard cases a further ~15–30% with content-dependent color loss. Default is `lossless` (bit-exact). The tolerance presets concern ASCII adaptive coding; DCT quality is a separate setting.

**Monitor bandwidth and playback:** pass `--debug` to see RAW vs WIRE rates and compression ratios, plus `[PERF]` reports for delivered FPS, frame production time, delivery lag, and skipped source frames. The root player also reports average decode/draw times and late-frame drops. A slow producer skips overdue source frames before encoding to stay near the audio clock; the requested FPS is a ceiling, not a guarantee on every machine or scene.

> Verified two independent ways, both bit-exact: Python-encoded vectors decoded by `codec.js` in Node (`experiments/gen_vectors.py` → `experiments/check_vectors.js`), and a live `adaptive`-vs-`legacy` WebSocket diff (`experiments/test_e2e.js`). Generate test clips with `experiments/make_test_clips.sh`.

**LAN / network streaming:** use `--host` to expose the server on your network.
```bash
python stream_server.py video.mp4 --host 0.0.0.0
```

## Standalone Static Web Player

ASCILINE can compile a video into a self-contained `.ascf` (ASCII Compressed Format) file and play it back with a static HTML page — no Python backend at runtime, hostable anywhere (GitHub Pages, Vercel, Netlify).

> **Trade-off:** ASCF stores ASCILINE's representation, which can be larger than standard video. ASCII selection overlays and direct cell/pixel drawing support representation-specific interaction. Video uses a custom JavaScript decoder; audio playback policy still applies.

There are two ways to produce a `.ascf` file:

### 1. Python compiler (more capable and faster — the recommended default)

```bash
python compiler.py your_video.mp4 --cols 250 --pixel --quantize 2
```

- `--quantize 0-3`: drops color bits to reduce file size (0 = lossless, 3 = aggressive).
- `--profile`: Enables Discrete Cosine Transform (Tag 4) spatial compression. Provides the engine's highest compression ratio, significantly reducing the final `.ascf` payload size at the cost of higher encode times and lossy quantization. Automatically enforces `--pixel`.
- `--qf 1-100`: Quality factor for the DCT profile (default: 70). Higher means better quality and larger file.
- `--tolerance`: color drift tolerance before a pixel update is sent, to skip invisible changes.
- `--hard`: max zlib compression (level 9) — slower to compile, smaller output.

This is what powers the live demo at [asciline.dev](https://www.asciline.dev): the static clips there are compiled with this Python path.

<a id="browser-studio"></a>
### 2. Browser Studio — compile & watch without installing anything

`static_player/studio/` is a standalone page (`index.html` + `encoder.js`, using `pako` from a CDN) that compiles a video to `.ascf` entirely client-side — drop a video in, get a `.ascf` out, nothing ever leaves your browser, no Python required.

u can try it from - https://yusufb5.github.io/ASCILINE/static_player/studio/

The page includes a built-in preview with a **custom seekbar**, allowing you to instantly scrub through your compiled clip. Because it shares the main `codec.js`, this studio player natively decodes all advanced compression tags (including Tag 4 DCT).

*(Note: While it can play all tags, the client-side encoder itself is conservative and only emits RAW/ZLIB/DELTA for speed. For production output or maximum compression with RLE/DCT, use the Python compiler).*

<a id="playing-a-compiled-file"></a>
### Playing a compiled file (the full player)

For the full experience — audio sync and ASCII/pixel mode support — use the main player at `static_player/index.html`.

**Method A: Drag & Drop (No server needed!)**
Simply open `static_player/index.html` in your browser and drag your `.ascf` file (along with an optional `.mp3` file for audio) directly onto the page. The player reads the selected local files without an ASCILINE backend. Browser file loading and audio playback policy still apply.

**Method B: Local File Server**
If you prefer to load files via URL instead of drag-and-drop, serve the folder through a plain static server:

```bash
python -m http.server
```

> **Buffering:** a rolling decoded-frame buffer limits retained frames. File loading, seek, decoder state and browser memory still have costs; this is not an unlimited-duration or near-zero-memory guarantee.

## JavaScript SDK (`asciline-player`)

### Live Rust/Python + DCT support in this checkout

The local SDK now negotiates synchronized playback and the `dct-v1` pixel
profile. The server selects Python/Rust and RAW/DCT; no engine-specific SDK
option is required. Older servers without synchronized INIT fields retain the
legacy path. These changes are in this checkout, not yet an npm release.

Start your server, then open
`http://localhost:8000/static/examples/sdk-live.html` to test the local SDK.
It provides play/pause, seek, audio enable and disconnect/reconnect controls.
When hosting the SDK manually, copy the whole `src/` directory: the player
imports `live-session.js`. The npm package's existing `src` inclusion covers it.

Live sessions serialize DCT decoding, discard stale seek/reinit completions,
wait for audio startup (or use a wall clock when audio is unavailable), and
report playback metrics to servers started with `--perf-record`. Static ASCF
files use their existing separate playback path.

The official JavaScript SDK ships as the `asciline-player` npm package (MIT license). It provides two complementary APIs: a full-featured `AsciiPlayer` class for programmatic control, and a zero-config `<ascf-player>` HTML element for drop-in embedding — both backed by the same high-performance render engine.

### Install

```bash
npm install asciline-player
```

Or use the CDN directly (no install):

```html
<script type="module" src="https://cdn.jsdelivr.net/npm/asciline-player/src/asciline-player.js"></script>
```

---

### 1. JS API — Live WebSocket Stream

Connect to a running `stream_server.py` backend and render in real time:

```html
<div id="player-box" style="position:relative; width:100%; aspect-ratio:16/9; background:#000;">
  <canvas id="ascii-canvas"></canvas>
</div>

<script type="module">
  // Bundler/import-map setup; for plain HTML use './src/asciline-player.js'.
  import { AsciiPlayer } from 'asciline-player';

  const player = new AsciiPlayer('#ascii-canvas', {
    url: 'ws://localhost:8000/ws', // live WebSocket stream
    container: '#player-box',
    audio: true,           // synchronized audio
    selectionLayer: true,  // copyable text overlay
    playOverlay: true,     // auto ▶ button
  });
</script>
```

---

### 2. JS API — Static ASCF File (no backend required)

Play a pre-compiled `.ascf` file directly from any static host (GitHub Pages, Vercel, Netlify…):

```html
<div id="player-box" style="position:relative; width:100%; aspect-ratio:16/9; background:#000;">
  <canvas id="ascii-canvas"></canvas>
</div>

<script type="module">
  // Bundler/import-map setup; for plain HTML use './src/asciline-player.js'.
  import { AsciiPlayer } from 'asciline-player';

  const player = new AsciiPlayer('#ascii-canvas', {
    src:      'demo.ascf',  // .ascf file URL — no WebSocket server needed
    audioSrc: 'demo.mp3',   // optional paired audio
    container: '#player-box',
    loop:      true,
    playOverlay: true,
  });

  // Or imperatively:
  // player.play('demo.ascf', 'demo.mp3');
</script>
```

> Both modes share identical playback controls: `play()`, `pause()`, `resume()`, `togglePlay()`, `mute()`, `unmute()`, `setVolume()`, `setFilters()`, `destroy()`.

---

### 3. HTML Tag — `<ascf-player>`

Embed a clip with a single HTML tag. No JavaScript needed:

```html
<!-- Load the element once (CDN or local) -->
<script type="module" src="https://cdn.jsdelivr.net/npm/asciline-player/src/ascf-element.js"></script>

<!-- Then use it anywhere on the page -->
<ascf-player
  src="demo.ascf"
  audio="demo.mp3"
  loop
  style="width:100%; aspect-ratio:16/9;">
</ascf-player>
```

For live streaming, use the `ws` attribute instead of `src`:

```html
<ascf-player ws="ws://localhost:8000/ws" style="width:100%; aspect-ratio:16/9;"></ascf-player>
```

**Supported attributes:**

| Attribute | Description |
| :-- | :-- |
| `src` | URL of the `.ascf` file |
| `audio` | URL of the paired `.mp3` audio file (optional) |
| `ws` | WebSocket URL for live streaming (alternative to `src`) |
| `autoplay` | Start playback immediately (boolean) |
| `loop` | Restart on finish — only with `src` (boolean) |
| `muted` | Start with audio muted (boolean) |

DOM events are dispatched with the `ascf-` prefix (e.g. `ascf-playing`, `ascf-ended`, `ascf-timeupdate`).

---

### Constructor Options Reference

| Option | Default | Description |
| :-- | :-- | :-- |
| `url` | `null` | WebSocket URL for live streaming |
| `src` | `null` | Static `.ascf` file URL (no backend needed) |
| `audioSrc` | `null` | Static audio URL paired with `src` |
| `loop` | `false` | Loop ASCF playback on completion |
| `audio` | `true` | Enable audio (boolean / selector / HTMLAudioElement) |
| `container` | parent | Sizing container element or CSS selector |
| `autoplay` | `false` | Begin playback immediately |
| `muted` | `false` | Start playback with audio muted |
| `playOverlay` | `true` | Auto-create the ▶ overlay button |
| `muteButton` | `true` | Auto-create the mute/unmute toggle |
| `clickToPlayPause` | `true` | Click canvas to toggle play/pause |
| `keyboardShortcuts` | `true` | Spacebar toggles play/pause |
| `selectionLayer` | `null` | Enable copyable text overlay |
| `bufferSize` | `4` | Buffer-related sizing; synchronized live preroll stays four frames |
| `filters` | `{}` | Initial filter values (contrast, gamma, brightness…) |

---

### Programmatic API

```js
player.play('clip.ascf', 'clip.mp3'); // static file
player.play();                         // live WS (uses options.url)
player.pause();
player.resume();
player.togglePlay();
player.mute();
player.unmute();
player.setVolume(0.8);        // 0–1
player.setFilters({ contrast: 1.2, invert: true });
player.setRenderMode(4);      // switch color depth live (WS only)
player.seek(30);              // jump to 30s (live or static)
player.getMasterClock();      // current position in seconds
player.getState();            // 'IDLE' | 'CONNECTING' | 'PLAYING' | 'PAUSED' | 'ENDED' | 'ERROR'
player.destroy();             // clean up all resources

// Event listener API
player.on('init',        ({ fps, cols, rows, duration }) => { ... });
player.on('timeupdate',  (seconds) => { ... });
player.on('fps',         ({ fps, targetFps, buffered }) => { ... });
player.on('statechange', (state) => { ... });
player.on('ended',       () => { ... });
player.on('error',       (err) => { ... });
player.off('timeupdate', handler); // remove listener
```

Additional module-hosting, timeline and lifecycle details are in the [SDK reference](docs/SDK.md).

## Installation

### 0. Requirements

- **Python 3.9+**
- FFmpeg & FFprobe (see below — required for audio and thumbnails)
- A modern browser for the web player (any browser with Canvas + WebSocket support)

### 1. Clone the repository
```bash
git clone https://github.com/YusufB5/ASCILINE.git
cd ASCILINE
```

### 2. Install dependencies

ASCILINE's dependencies are defined in `pyproject.toml`. Install the base package with:
```bash
pip install .
```

Or, if you prefer the plain requirements file:
```bash
pip install fastapi uvicorn opencv-python numpy websockets
```

Running headless (server / no display, e.g. a VPS or container)? `opencv-python-headless` is a lighter drop-in replacement for `opencv-python` and avoids pulling in GUI dependencies you won't use.

**Optional — play from YouTube (and other yt-dlp sites):**
```bash
pip install ".[ytdlp]"
```
This installs the `ytdlp` extra defined in `pyproject.toml`, pulling in `yt-dlp` for URL streaming. Only needed if you pass a URL instead of a local file — local playback works without it. URL playback also uses FFmpeg (see below) to normalize downloads.

### FFmpeg & FFprobe (required for audio and thumbnails)

**Package manager (recommended):**
- Windows: `winget install ffmpeg`
- macOS: `brew install ffmpeg`
- Linux: `sudo apt install ffmpeg`

**Manual (Windows):** if you hit a `FileNotFoundError` or don't want to touch system variables, download the [FFmpeg ZIP](https://github.com/BtbN/FFmpeg-Builds/releases/latest), keep `bin/` on PATH or set `ASCILINE_FFMPEG_DIR` to the extracted root. Rust additionally needs shared development headers/libraries; see [Rust installation](rust_core/README.md).

### 3. Run the web server

**Single video:**
```bash
python stream_server.py video.mp4 --cols 240
```

With automatic rows (the default), the server retains its performance caps:
125,000 pixels or 12,000 ASCII cells, plus row and portrait limits. To preserve
the requested columns, add `--no-resolution-limit` (alias: `--no-limit`). For
example, a 1280x720 video with `--pixel --cols 750` uses 471x265 by default,
or 750x422 with the flag. This applies to both Python and Rust engines and
persists when switching ASCII/pixel modes. Larger grids increase server,
network and browser work; lower `--cols` if playback falls behind. The flag
does not remove FPS limits or native allocation checks. Explicit `--rows`
continues to use the supplied dimensions, as before.

```bash
python stream_server.py video.mp4 --engine rust --pixel --cols 750 --no-resolution-limit
```

### Optional Rust engine

The base Python install does not build Rust. On Windows, install Cargo and
Visual Studio x64 C++ Build Tools, and extract a stable FFmpeg 8.1 **shared
development** package containing `include/`, `lib/` and `bin/`. Keep the matching
shared libraries available after compilation. Build and run with the same
Python interpreter; this checkout was verified with Python 3.11 on Windows.
An executable-only FFmpeg package is sufficient for audio commands but cannot
build the native engine.

```powershell
python rust_core/build.py --ffmpeg-dir "C:/dependencies/ffmpeg-shared"
python -m asciline.engines --engine rust
```

Replace the path with the extracted development root. Cargo is offline by
default; add `--online` on a fresh machine without cached dependencies. A
successful engine check reports Rust, its module path and no fallback reason.
Native Linux/macOS build paths are available but not validated in this integration.

After building, use `--engine rust` for
60 FPS pixel playback, or `--engine auto` to allow Python fallback. Python remains
the default. Build/runtime troubleshooting and the tested FFmpeg archive are documented in [the native build reference](rust_core/README.md).

For Rust file playback, `--decode-threads 2` requests two source-decoder threads;
`--decode-threads 0` requests automatic selection. Omitting the option preserves
the codec default. This controls source decoding, independently of DCT encoding
and the `--decode-ahead 3` source queue. Rebuild the native module before using
the option. Python fallback and webcam input do not support this override.

Live FPS defaults are **30 for ASCII with either engine**, **60 for Rust pixel**,
and **30 for Python pixel**. Use `--fps N` to override the FPS ceiling separately
from engine and resolution. For example, `--engine rust --mode 6 --fps 60`
opts into 60 FPS ASCII; `--engine rust --pixel --fps 30` limits pixel playback.
`--no-limit` only changes resolution. Switching ASCII/pixel modes recalculates
the default FPS; an explicit `--fps` stays in effect. Frames are sampled at
uniform source intervals, so 59.94 FPS becomes 29.97 with a 30 FPS ceiling,
and 50 FPS becomes 25. Lower-rate sources are not interpolated. Higher FPS
increases browser work and is not a guarantee of smooth playback.

**Opt-in live DCT (pixel only):** the root demo and local SDK negotiate the existing
lossy tag-4 profile. Enable it with `--pixel-codec dct`; `--dct-quality 70`
sets quality from 1 to 100 (lower is smaller/lossier; 100 is not lossless). The raw transport
remains the default. For the locally verified 60 FPS configuration, enable the bounded source queue:

```bash
python stream_server.py video.mp4 --engine rust --pixel --pixel-codec dct --dct-quality 70 --cols 450 --fps 60 --no-thumbnails --decode-ahead 3
```

The grid rounds up to multiples of 16 for the DCT/YUV420 planes; the server
prints the actual size. Seek and mode changes restart the predictor with a
keyframe. Clients without the explicit DCT capability, including older
npm SDK releases, keep receiving raw pixels; this checkout's SDK negotiates DCT.
Python can encode the same profile with
`--engine python`; Rust uses the native implementation after rebuilding.
DCT reduces bandwidth at the cost of image fidelity and extra encode/decode
work, so raw-pixel FPS does not predict DCT FPS. See [performance evidence](docs/PERFORMANCE.md) for measured gains, costs and recording. Historical [initial integration notes](experiments/rust_audit/DCT_INTEGRATION.md) describe earlier results before later optimizations.

**What the integration achieved:** on the tested local 60 FPS source, the Rust/DCT
pipeline plays near the source rate at a 464x256 coded grid. Seek, pause/resume,
ASCII/pixel switching and SDK reconnection are covered by the automated live
tests and owner playback checks. The browser still reconstructs DCT in
JavaScript; Rust accelerates the source and server encoding stages.

Native DCT optimization measured approximately **1.83–1.99x faster encoding**
on three identical-input windows, with matching packet and reconstruction hashes.
Decode-ahead reduces consumer waiting by overlapping source preparation and
encoding. Thread overrides remain optional because faster source decode can
compete with encoding. These results apply to the measured workload rather than
all sources and machines. The [performance reference](docs/PERFORMANCE.md)
contains methods, results and recording instructions; the [live reference](docs/LIVE_STREAMING.md)
collects the tuning defaults explained above.

### Other source and queue options

**YouTube / URL (requires the `ytdlp` extra):**
```bash
python stream_server.py "https://youtu.be/VIDEO_ID" --cols 240
python stream_server.py "https://www.youtube.com/playlist?list=..." --cols 220 --loop
```

**Garbage collection for cached downloads:** ASCILINE includes an LRU cache limiter for on-demand YouTube downloads so disk usage doesn't grow unbounded.
```bash
python stream_server.py --cache-limit 5000   # cap the video cache at 5 GB (default 10240 MB)
```

**How caching works:**
- ASCII rendering only needs a small grid, so yt-dlp fetches at ≤480p to save bandwidth.
- Downloads are cached by video ID in `videos/` — replays are instant.
- Playlist/channel URLs and `playlist.json` expand into a queue and fetch on demand; the server starts immediately instead of waiting for bulk downloads.
- Downloaded videos are normalized to H.264/AAC constant frame rate. Normalization may lower source FPS; use a local original when measuring 60 FPS.

**Folder mode** — drop videos into `videos/` and run:
```bash
python stream_server.py --folder videos --cols 200
python stream_server.py --folder videos --cols 230 --loop
python stream_server.py --folder videos --pixel --cols 320 --vol 2
```
Folder mode builds its queue from files present at startup. Restart after changing files to rebuild the queue.

**JSON playlist** — per-video overrides:
```bash
python stream_server.py --playlist playlist.json --cols 220
python stream_server.py --playlist playlist.json --cols 220 --loop
```

Open `http://localhost:8000` in your browser.

### Player controls

Hover previews are built once per video on first hover, in a single ffmpeg pass, kept in memory — nothing written to disk. Disable with `--no-thumbnails`. To use a prebuilt sprite instead, point the `/scrub` route at it.

### Live webcam streaming

```bash
python stream_server.py --webcam --cols 240

# Different camera device and target FPS
python stream_server.py --webcam --webcam-device 1 --webcam-fps 60

# Disable the automatic horizontal mirror
python stream_server.py --webcam --no-mirror
```

### 4. Run directly in a terminal (standalone)

Bypass the web interface and render inside an ANSI-capable terminal with true-color support:
```bash
python ascii_video_player2.py video.mp4 --cols 100 --quality 0

# Webcam directly in the terminal
python ascii_video_player2.py --webcam --cols 100
```

> Don't resize the terminal window during playback — dynamic text wrapping will corrupt the layout.

## Running with Docker

ASCILINE ships with a `Dockerfile` and `docker-compose.yml` for running the live streaming server without installing Python, FFmpeg, or any dependency on the host. The image is based on `python:3.11-slim`, installs FFmpeg/FFprobe and CA certificates, and swaps `opencv-python` for `opencv-python-headless` at build time (the container has no display, so this is the lighter drop-in — see [Requirements](#0-requirements)). Note that webcam and terminal-standalone (`ascii_video_player2.py`) modes aren't practical inside a container — Docker is intended for the web streaming server (`stream_server.py`), which by default runs in **folder mode**, loading files from `videos/` at startup. This recipe does not build Rust.

### Docker Compose (recommended)

```bash
docker compose up --build
```

This builds the image and starts `stream_server.py --folder videos --host 0.0.0.0 --port 8000`, exposing the web UI on `http://localhost:8000`. `docker-compose.yml` mounts `./videos` on the host to `/app/videos` in the container — drop your `.mp4`/`.mkv`/etc. files into your local `videos/` folder and restart the service to rebuild its queue, without rebuilding the image. `stdin_open`/`tty` are enabled for interactive terminal commands. High-FPS sources are automatically sampled to the selected FPS ceiling.

### Plain Docker

```bash
docker build -t asciline .
docker run -p 8000:8000 -v $(pwd)/videos:/app/videos asciline
```

Pass any `stream_server.py` CLI flags after the image name to override the default `--folder videos --host 0.0.0.0 --port 8000`, e.g.:
```bash
docker run -p 8000:8000 -v $(pwd)/videos:/app/videos asciline --folder videos --cols 220 --loop
```

> **YouTube/URL playback in Docker:** the image installs from `requirements.txt` only, so `yt-dlp` is **not** included by default. To enable URL playback inside the container, add `RUN pip install ".[ytdlp]"` (or `pip install yt-dlp`) to the `Dockerfile` before building, or install it in a custom layer on top of the base image.

### Health checks

`GET /health` returns HTTP 200 with `{"status":"ok"}` when the HTTP server is
responsive. It does not open a WebSocket, access videos, or decode frames.

```bash
curl -i http://localhost:8000/health
```

The Docker image checks this endpoint on container port 8000 every 30 seconds
using Python's standard library. Check the container's health status with
`docker compose ps`; a running server should report `healthy` after a successful
check. If you change the server's internal port, update the health check URL too.

### Server information

`GET /info` returns a JSON snapshot of server metadata:

```json
{"queue_size":1,"current_index":0,"cols":200,"rows":0,"loop":false,"debug":false}
```

`current_index` is zero-based. `cols` and `rows` describe the server's configured
grid, with `rows: 0` meaning automatic sizing; individual sessions can use
different dimensions. If the app is imported without CLI initialization,
numeric fields default to 0 and flags to false. Pixel mode is scoped to each
WebSocket session and is not included. The endpoint reads existing server
state without opening videos or decoding frames.

## Customization

### Styling
Edit `style.css` to change accent colors and typography via CSS variables:
```css
:root {
    --accent-color: #00ff41; /* Classic Matrix Green */
    --bg-color: #050505;
}
```

### Real-time frontend filters & palettes (ASCII modes)

Click **FX** on the player controls (or press **F**) to open the filter overlay.

- **Contrast** — adjust the difference between light and dark areas
- **Brightness** — control the overall lightness of the output
- **Gamma** — recover detail from dark/washed-out sources
- **Sharpen** — Unsharp Mask, levels 0–10
- **Invert** — instantly invert all brightness values
- **Palettes** — swap character sets live:
  - `Default`: full detailed ASCII ramp
  - `Flat/Anime`: shortened, minimalist ramp (good for animation)
  - `Block`: chunky, dense characters for a retro-terminal look

### Rendering modes

```bash
python stream_server.py --mode 6 --cols 240 --rows 100

# For pixel mode, simply pass the flag (no mode number required):
python stream_server.py video.mp4 --pixel --cols 560
```
- `1`: Black & White (DOM mode)
- `2`: 64 colors
- `3`: 512 colors
- `4`: 32K colors
- `5`: 262K colors
- `6`: 16M colors (ultra)

*(Note: The `--pixel` flag operates independently and automatically applies the highest color fidelity, rendering `--mode` unnecessary when used).*

### Resolution & auto-scaling

Specify only `--cols`; ASCILINE derives `--rows` from the source aspect ratio.

- ASCII mode: `--cols 200`–`240` (recommended starting point for the best balance of detail and 30 FPS performance; can be increased if your hardware allows).
- Pixel mode: `--cols 600`–`900` (recommended starting point for near-HD quality; performance depends heavily on CPU).
- If `--cols` isn't set, defaults are `450` in pixel mode and `200` in ASCII mode.
- **Hardware limits & A/V sync:** pushing `--cols` beyond what your machine can encode/send in time causes the video to fall behind the audio (desync). If you see this, lower `--cols`.

```bash
python stream_server.py video.mp4 --mode 6 --cols 240
# Terminal shows: [AUTO] 1920x1080 → grid 240x67
```

### Server-side volume control

`--vol` (0–5). At `0`, FFmpeg's audio path never runs — saves CPU and bandwidth.

| `--vol` | Multiplier | |
|---------|------------|---|
| `0` | — | Muted (no processing) |
| `1` | 1.0× | Normal (default) |
| `3` | 1.5× | Loud |
| `5` | 2.0× | Double volume |

```bash
python stream_server.py video.mp4 --pixel --cols 560 --vol 0   # silent
python stream_server.py video.mp4 --cols 220 --vol 3           # loud
```

### Playlist format (`playlist.json`)

Each entry can override the global `--mode`, `--pixel`, `--vol`, and `--cols`:
```json
[
    { "video": "intro.mp4",  "mode": 1, "vol": 1 },
    { "video": "main.mp4",   "pixel": true, "vol": 3, "cols": 520 },
    { "video": "https://youtu.be/VIDEO_ID", "mode": 4, "vol": 2, "cols": 240 }
]
```
Paths are resolved automatically — the project root and `videos/` are both checked, so a filename alone is usually enough.

## Troubleshooting

Quick fixes for the most common issues. Full protocol/technical details will live in a separate technical guide (coming soon).

- **Audio and video fall out of sync** — you've pushed `--cols` higher than your machine can encode/send in time. Lower `--cols` until playback keeps up. See [Resolution & auto-scaling](#resolution--auto-scaling).
- **`FileNotFoundError` for `ffmpeg`/`ffprobe` (usually Windows)** — FFmpeg isn't on your PATH. Either install it via `winget install ffmpeg`, or manually drop `ffmpeg.exe`/`ffprobe.exe` next to `stream_server.py`. See [FFmpeg & FFprobe](#ffmpeg--ffprobe-required-for-audio-and-thumbnails).
- **Terminal playback layout breaks / garbles mid-video** — don't resize the terminal window while `ascii_video_player2.py` is running; dynamic text wrapping corrupts the fixed-grid layout.
- **YouTube/URL playback fails or hangs** — make sure the `ytdlp` extra is installed (`pip install ".[ytdlp]"`); it's optional and isn't required for local file playback.
- **First-run YouTube video is slow to start** — the server downloads and normalizes it to H.264/AAC first; every replay afterward is served instantly from the `videos/` cache.
- **Disk filling up from cached downloads** — set a lower `--cache-limit` (in MB) to cap the LRU video cache.
- **Studio (browser compiler) output is bigger than expected, or compiling takes a long time** — the browser-side encoder only emits RAW/ZLIB/DELTA (no RLE_FULL) and is meant for short clips. For long or size-sensitive videos, use the Python compiler (`compiler.py`) instead. See [Browser Studio](#browser-studio) and [Playing a compiled file](#playing-a-compiled-file) for the two preview options.

## Live Demo

Live, browser-based showcase across multiple rendering modes: **[asciline.dev](https://www.asciline.dev)**

## Star History
[![Star History Chart](https://stars.unv.one/svg/YusufB5/ASCILINE?theme=dark)](https://github.com/YusufB5/ASCILINE)

<a id="support"></a>
## Support ❤️

If you find this project useful, you can support its development:
[![GitHub Sponsors](https://img.shields.io/badge/Sponsor-GitHub-ea4aaa?style=flat&logo=github)](https://github.com/sponsors/YusufB5)
- **Solana (SOL / USDC):** `H1wSQAhjgsu7AxenF4e5ZBYiBjkhDLVzkKaZuVPcrE14`
- **Ethereum (ETH / USDT):** `0x85B2f970045c0F7c282089Ab6CF897C20230e086`
- **Bitcoin (BTC):** `bc1qvtcl55v54gkzwnp2zxn70usea3gf5ncncqa0fv`

## License

ASCILINE is distributed under a split-licensing model:

- **Core Engine & Streaming Server:** [GNU AGPLv3 or later (AGPL-3.0-or-later)](LICENSE-AGPL) — See [LICENSE-AGPL](LICENSE-AGPL) for full text.
- **Client SDK & Web Decoders (`asciline-player`, `codec.js`):** [MIT License](LICENSE-MIT) — See [LICENSE-MIT](LICENSE-MIT) for full text.

## Community

Join the [ASCILINE Discord Server](https://discord.gg/9bpWwx9EHV) to share ideas, or contribute to ASCILINE.

## Contact

[asciline.engine@gmail.com](mailto:asciline.engine@gmail.com)
