# 🌌 ASCILINE Engine

**ASCILINE** is a high-performance, cross-platform real-time ASCII video rendering engine. **Our core objective is to transform the web into a highly dynamic and interactive typographic canvas.** By mapping pixels to text-based representations, we unlock new possibilities for web media delivery.

| Output | Details |
| :--- | :--- |
| <img src="https://github.com/user-attachments/assets/ccc727c9-c697-49f2-85e1-6f8c366f2019" width="400" alt="Original Source" /> | **Original Source**<br>Standard MP4 video file. |
| <img src="https://github.com/user-attachments/assets/6bd7f5c0-81de-49fe-ba0d-9a8872ec8ae3" width="400" alt="ASCII Mode" /> | **ASCII Mode**<br>Showcases rendered using Mode 3 (32K Colors) from a 30fps source. |
| <img src="https://github.com/user-attachments/assets/1fd88c3d-97d1-441a-a071-16de24ea82c0" width="400" alt="PIXEL Mode" /> | **PIXEL Mode**<br>Showcases rendered using Mode 5 (16m Colors) combined with the `--pixel` flag for ultra-high fidelity. |

## 🎯 Strategic Vision & Core Capabilities

1. **Pure Typographic Manipulation**: The visual stream is not a standard media file—it's raw HTML/Canvas text. This makes the impossible possible: you can apply real-time CSS filters (neon glows, text shadows, animations) to video content.
2. **Local AI & LLM Ready**: By reducing complex pixel streams into structured logical strings, ASCILINE acts as a perfect bridge for AI. Instead of feeding heavy computer vision models, lightweight LLMs can process semantic video summaries.
3. **Ultra-Low Bandwidth & IoT Compatibility (valid for ASCII MOD)**: Standard codecs (H.264/VP9) choke microcontrollers and weak networks. ASCILINE processes the heavy lifting once on the backend, streaming only a few kilobytes per frame.
4. **Bypassing Browser Constraints**: Modern browsers aggressively throttle autoplay videos, and ad-blockers restrict traditional media frames. To the browser, ASCILINE is simply "JavaScript updating a canvas"—completely invisible to media restrictions.

## 🚀 Technical Features

-   **Cross-Platform**: Runs seamlessly on Windows, macOS, and Linux.
-   **Real-Time ASCII Streaming**: Low-latency video-to-ASCII conversion.
-   **Real-Time Pixel Streaming**: Replaces characters with colored blocks, approaching 360p video quality.
-   **High Performance**: Uses **HTML5 Canvas** for rendering, optimized for cinematic 24-30 FPS playback. High-FPS sources are automatically decimated for stability.
-   **Master Clock Sync**: The audio track acts as the absolute master clock, guaranteeing perfect A/V synchronization.
-   **Zero-Copy Binary Protocol**: Frames are streamed as raw binary (`Uint8Array`) directly to the canvas, saving bandwidth and CPU.
-   **Multiple Color Modes**: Supports everything from classic B&W to 16M color ultra-fidelity.
-   **Flexible Video Management**: Supports JSON playlists (per-video mode & volume), 
      folder-based auto-queuing (filesystem order), single-file mode, and infinite loop 
      playback — all controlled via CLI arguments.

## 🛠️ Architecture

1.  **Backend (Python/FastAPI)**: Decodes video using OpenCV, maps pixels to ASCII characters via NumPy, and streams binary data.
2.  **Frontend (Vanilla JS)**: Receives binary frames via WebSockets, manages a jitter buffer, and renders to a Canvas grid.
3.  **Communication**: Optimized WebSocket protocol with a custom `INIT` handshake for dynamic resolution/FPS adjustment.

## 📦 Installation

### 1. Clone the repository
```bash
git clone https://github.com/YusufB5/ASCILINE.git
cd ASCILINE
```

### 2. Install dependencies
For development and verification:
```bash
python -m pip install -e ".[dev]"
python -m pytest -q
node --check app.js
```

For a quick runtime-only setup:
```bash
pip install fastapi uvicorn opencv-python numpy websockets
```
### 🔈 Audio Support (FFmpeg Required)
To enable server-side audio processing (Volume 1-5), you must have FFmpeg installed.

**Option 1: Package Manager (Recommended)**
- **Windows:** `winget install ffmpeg`
- **macOS:** `brew install ffmpeg`
- **Linux:** `sudo apt install ffmpeg`

**Option 2: Manual Installation (Windows)**
If you get a `FileNotFoundError` or don't want to modify system variables:
1. Download [FFmpeg ZIP](https://github.com/BtbN/FFmpeg-Builds/releases/latest).
2. Extract `ffmpeg.exe` from the `bin` folder.
3. Drop it directly into your `ASCILINE` project folder alongside `stream_server.py`.
### 3. Run the Web Server

**Single video:**
```bash
python stream_server.py video.mp4 --cols 240
```

**Folder mode — drop your videos into `videos/` and run:**
```bash
python stream_server.py --folder videos --cols 200
python stream_server.py --folder videos --cols 230 --loop          # infinite loop
python stream_server.py --folder videos --mode 5 --pixel --cols 320 --vol 2  # all videos same settings
```
Videos play in **filesystem order** (top to bottom as they appear in the folder, not alphabetically). Just add/remove files from the `videos/` folder to control the queue.

**JSON Playlist — full control per video:**
```bash
python stream_server.py --playlist playlist.json --cols 220
python stream_server.py --playlist playlist.json --cols 220 --loop
```
Use `playlist.json` when you need different `--mode` or `--vol` settings for each video.

> 💡 **Windows Users:** You can use the included `serve.bat` shortcut for quicker typing: `.\serve video.mp4 --cols 240`

Open `http://localhost:8000` in your browser.

### 4. Run directly in Terminal (Standalone)
If you prefer to bypass the web interface, you can render the video directly inside an ANSI-supported terminal (zero-flicker, true color):
```bash
python ascii_video_player2.py video.mp4 --cols 100 --quality 0
```
> 💡 **Windows Users:** Use the shortcut `.\play video.mp4 -c 100 -q 0`
> 
> ⚠️ **Note:** Do not resize your terminal window during playback, as dynamic text wrapping will corrupt the ASCII layout.

## 🎨 Customization

You can easily customize the look and feel of the engine:

### Styling
Edit `style.css` to change the accent colors and typography using CSS variables:
```css
:root {
    --accent-color: #00ff41; /* Classic Matrix Green */
    --bg-color: #050505;
}
```

### Rendering Modes
The engine supports different fidelity levels via the `--mode` flag:
- `1`: Black & White (DOM mode)
- `2`: 512 Colors
- `3`: 32K Colors
- `4`: 262K Colors
- `5`: 16M Colors (Ultra)

```bash
python stream_server.py --mode 5 --cols 240 --rows 100
```
### 📐 Resolution & Auto-Scaling
By default, you only need to specify the width (`--cols`). ASCILINE will automatically calculate the correct `--rows` based on the source video's aspect ratio to prevent stretching.

- **ASCII Mode Recommended:** `--cols 200` to `--cols 240` (Best balance of text detail and cinematic 30 FPS performance).
- **Pixel Mode Recommended:** `--cols 600` to `--cols 900` (Provides near-HD visual quality. Performance heavily depends on your machine's CPU/VRAM).
- > **Smart Defaults:** If you do not specify a `--cols` value, ASCILINE automatically defaults to `450` when Pixel Mode is enabled, and `200` for standard ASCII text mode. 
- > ⚠️ **Hardware Limits & A/V Sync:** If you push the `--cols` too high for your specific hardware (e.g., `1350` on a laptop vs a gaming desktop), the Python backend won't be able to encode and send the massive frames fast enough. When the video stream lags behind the audio, you will experience A/V desync (audio finishing early). If this happens, simply lower your `--cols` value!
- ASCILINE rejects extreme grid sizes before playback to avoid accidental memory spikes. Keep `--cols` at or below `1200`, explicit `--rows` at or below `800`, and explicit grids at or below 500,000 cells.
```bash
python stream_server.py video.mp4 --mode 5 --cols 240
# Terminal will show: [AUTO] 1920x1080 → grid 240x67
```
### Server-Side Volume Control
Volume is controlled at the server level via the `--vol` flag (scale 0–5).
When set to `0`, the audio engine (FFmpeg) **never runs**, saving CPU and bandwidth.

| `--vol` | FFmpeg Multiplier | Description |
|---------|------------------|-------------|
| `0`     | —                | Muted (no processing) |
| `1`     | 1.0×             | Normal (default) |
| `3`     | 1.5×             | Loud |
| `5`     | 2.0×             | Double volume |

```bash
python stream_server.py video.mp4 --pixel --cols 560 --vol 0   # Silent
python stream_server.py video.mp4 --cols 220 --vol 3   # Loud
```

### Playlist Format (`playlist.json`)
Each entry can override the global `--mode`, `--pixel`, `--vol`, and `--cols` defaults:
```json
[
    { "video": "intro.mp4",  "mode": 1, "vol": 1 },
    { "video": "main.mp4",   "mode": 5, "pixel": true, "vol": 3, "cols": 520 },
    { "video": "outro.mp4",  "mode": 3, "vol": 2, "cols": 240 }
]
```
Video paths are resolved automatically — the engine checks the project root and the `videos/` subfolder, so you can write just the filename.

## 📜 License & Ethical Guardrails

**MIT License (with Anti-Ad Restriction)**

ASCILINE is distributed under the MIT License, but with a strict ethical guardrail.
Because this engine bypasses standard browser constraints and ad-blockers (by rendering pure text instead of video), we strictly prohibit its use by ad-networks to serve unblockable advertisements. 

See the [LICENSE](LICENSE) file for the full text, which includes the **ANTI-ADVERTISEMENT RESTRICTION** clause.
