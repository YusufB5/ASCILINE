"""
stream_server.py
================
Streams the core Video-to-ASCII engine to the web via HTTP/WebSocket.
Dependencies: pip install fastapi uvicorn websockets

Priority Order:
  1. --playlist playlist.json  → JSON file (per-video vol, mode, path)
  2. --folder ./videos         → folder scan (filesystem order, not alphabetical)
  3. positional video arg      → single video (legacy behavior)
"""
try:
    import asyncio
    import subprocess
    import sys
    import threading
    import time
    import math
    import cv2
    import ytdl
    import json
    import numpy as np
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
    from fastapi.staticfiles import StaticFiles
    import uvicorn
    import os
    from urllib.parse import urlparse
    from websockets.exceptions import ConnectionClosed
    from contextlib import asynccontextmanager

    # Import the existing engine (ascii_video_player2.py)
    from ascii_video_player2 import VideoDecoder, AsciiMapper
    from codec import encode_frame

    # ── FILTER PALETTES ──────────────────────────────────────────────────────────
    # Named character palettes that the client can switch between at runtime.
    FILTER_PALETTES = {
        "default": list(" `.-':_,^=;><+!rc*/z?sLTv)J7(|Fi{C}fI31tlu[neoZ5Yxjya]2ESwqkP6h9d4VpOGbUAKXHm8RD#$Bg0MNWQ%&@"),
        "flat":    list(" .:-=+*#%@"),
        "block":   list(" .+o#@"),
    }

    def _build_gray_lut(
        contrast: float,
        gamma: float,
        brightness: float = 0.0,
        invert: bool = False,
    ) -> np.ndarray:
        """Build a 256-element uint8 LUT that applies brightness, contrast, gamma, invert.

        The LUT is precomputed once per parameter change and applied via
        cv2.LUT() — a single memcpy-speed table lookup per frame (O(pixels),
        but effectively free compared to decode/encode).

        Formula per value *v* (in order):
            1. Brightness: v'   = clip(v + brightness_offset, 0, 255)
            2. Contrast:   v''  = clip(128 + contrast * (v' - 128), 0, 255)
            3. Gamma:      v''' = 255 * (v'' / 255) ^ (1 / gamma)
            4. Invert:     v    = 255 - v'''
        Returns None if all params are identity (skip LUT call entirely).
        """
        lut = np.arange(256, dtype=np.float64)
        # 1. Brightness — additive shift in the 0-255 space
        if brightness != 0.0:
            lut = lut + brightness * 2.55  # -100..+100 → -255..+255
        np.clip(lut, 0, 255, out=lut)
        # 2. Contrast (linear stretch around midpoint 128)
        if contrast != 1.0:
            lut = 128.0 + contrast * (lut - 128.0)
            np.clip(lut, 0, 255, out=lut)
        # 3. Gamma (power curve; values near 1.0 are identity)
        if gamma != 1.0:
            lut = 255.0 * np.power(np.maximum(lut, 0) / 255.0, 1.0 / gamma)
            np.clip(lut, 0, 255, out=lut)
        # 4. Invert
        if invert:
            lut = 255.0 - lut
        return lut.astype(np.uint8)

    _download_locks = {}

    async def safe_resolve_video_path(vid: str):
        """Safely downloads a video without blocking the event loop and prevents concurrent downloads of the same video."""
        if not ytdl.is_url(vid):
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, resolve_video_path, vid)
            
        if vid not in _download_locks:
            _download_locks[vid] = asyncio.Lock()
        async with _download_locks[vid]:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, resolve_video_path, vid)

    async def prefetch_worker():
        """Background task that ensures the next video in the queue is downloaded."""
        while True:
            try:
                queue = getattr(app.state, "queue", [])
                idx = getattr(app.state, "current_index", 0)
                loop_opt = getattr(app.state, "loop", False)
                
                if len(queue) > 0:
                # download queue guard
                    current_entry = queue[idx]
                    if ytdl.is_url(current_entry["video"]):
                        await asyncio.sleep(2)
                        continue

                    next_idx = idx + 1
                    if next_idx >= len(queue) and loop_opt:
                        next_idx = 0
                        
                    if next_idx < len(queue):
                        next_entry = queue[next_idx]
                        vid = next_entry["video"]
                        if ytdl.is_url(vid):
                            print(f"[YT] pre-fetching ({next_idx + 1}/{len(queue)}) in background...")
                            local_path = await safe_resolve_video_path(vid)
                            next_entry["video"] = local_path
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[WARN] prefetch_worker error: {e}")
            await asyncio.sleep(2)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Suppress ConnectionResetError tracebacks in Windows asyncio (e.g. from seek/audio aborts)
        loop = asyncio.get_running_loop()
        def handle_exception(loop, context):
            exc = context.get("exception")
            if isinstance(exc, ConnectionResetError):
                return
            loop.default_exception_handler(context)
        loop.set_exception_handler(handle_exception)

        task = asyncio.create_task(prefetch_worker())
        yield
        task.cancel()

    app = FastAPI(lifespan=lifespan)


    def get_video_dimensions(path: str) -> tuple[int, int]:
        """Quickly probe a video file to get (width, height) without decoding frames."""
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            raise FileNotFoundError(f"Could not open video file: {path!r}")
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        return w, h


    def calc_auto_dimensions(cols: int, vid_w: int, vid_h: int, pixel_mode: bool) -> tuple[int, int]:
        """
        Calculate (cols, rows) from video aspect ratio.
        ASCII mode: characters are ~2x taller than wide, so divide by 2.
        Pixel mode: cells are square (CSS stretches), no correction needed.
        """
        # Pixel mode uses GPU-accelerated fillRect → generous cap
        # ASCII mode uses CPU fillText per cell → tight cap to prevent stutter on vertical videos
        MAX_ROWS = 1080 if pixel_mode else 300
        ratio = vid_w / max(vid_h, 1)
        
        if pixel_mode:
            rows = max(1, round(cols / ratio))
        else:
            rows = max(1, round(cols / ratio / 2))
            
        if rows > MAX_ROWS:
            # Scale down BOTH cols and rows to preserve aspect ratio
            scale = MAX_ROWS / rows
            rows = MAX_ROWS
            cols = max(1, round(cols * scale))
            
        return cols, rows

    # Serve only whitelisted static files (security: prevents directory traversal)
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    STATIC_WHITELIST = {"app.js", "style.css", "codec.js"}

    @app.get("/static/{filename}")
    async def serve_static(filename: str):
        if filename not in STATIC_WHITELIST:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Not found")
        filepath = os.path.join(BASE_DIR, filename)
        return FileResponse(filepath)

    def get_html_content():
        html_path = os.path.join(os.path.dirname(__file__), "index.html")
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()

    def resolve_video_path(video: str) -> str:
        """
        Resolves a video path by checking multiple locations in order:
        0. If it's a URL (YouTube, etc.) -> download via yt-dlp and use that file
        1. As-is (absolute or relative to CWD)
        2. Inside the project root (BASE_DIR)
        3. Inside BASE_DIR/videos/ subfolder
        Returns the first path that exists, or the original string if none found.
        """
        if ytdl.is_url(video):
            cache_limit = getattr(app.state, "cache_limit", 10 * 1024**3)
            return ytdl.download(video, cache_dir=os.path.join(BASE_DIR, "videos"), cache_limit=cache_limit)

        candidates = [
            video,
            os.path.join(BASE_DIR, video),
            os.path.join(BASE_DIR, "videos", os.path.basename(video)),
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
        return video  # Return original; error will be caught during playback

    def load_playlist(playlist_path: str) -> list[dict]:
        """
        Loads a playlist from a JSON file and resolves local video paths.

        URL entries (YouTube, etc.) are left unresolved on purpose: resolving a
        URL means downloading it, and eagerly downloading every link would block
        startup until the whole playlist is on disk. Unresolved URLs are fetched
        lazily by the playback loop the first time each one is about to play.
        """
        with open(playlist_path, "r", encoding="utf-8") as f:
            items = json.load(f)
        for item in items:
            if not ytdl.is_url(item["video"]):
                item["video"] = resolve_video_path(item["video"])
        return items

    def load_folder(folder_path: str, default_mode: int, default_vol: int) -> list[dict]:
        """
        Scans a folder for video files in filesystem order (top to bottom,
        as they appear in the directory — not alphabetically sorted).
        """
        supported = (".mp4", ".mkv", ".avi", ".mov", ".webm")
        entries = []
        with os.scandir(folder_path) as it:
            for entry in it:
                if entry.is_file() and entry.name.lower().endswith(supported):
                    entries.append({
                        "video": entry.path,
                        "mode":  default_mode,
                        "vol":   default_vol
                    })
        # Filesystem order (no sort applied)
        return entries

    def build_queue(args) -> list[dict]:
        """
        Builds the video queue based on argument priority:
        1. --webcam flag
        2. --playlist JSON file
        3. --folder directory
        4. Single positional video argument
        """
        if args.webcam:
            print(f"[WEBCAM] Device: {args.webcam_device} | Target FPS: {args.webcam_fps}")
            item = {
                "video": args.webcam_device,
                "is_webcam": True,
                "mirror": not args.no_mirror,
                "fallback_fps": args.webcam_fps,
                "mode": args.mode,
                "vol": args.vol,
                "pixel": args.pixel,
                "rows": args.rows
            }
            if args.cols is not None:
                item["cols_override"] = args.cols
            return [item]
            
        if args.playlist:
            print(f"[PLAYLIST] Loading: {args.playlist}")
            items = load_playlist(args.playlist)
            # Fill missing fields with global defaults
            for item in items:
                item.setdefault("mode", args.mode)
                item.setdefault("vol",  args.vol)
                item.setdefault("pixel", args.pixel)
                item.setdefault("rows", args.rows)
                if args.cols is not None:
                    item["cols_override"] = args.cols
                elif "cols" in item:
                    item["cols_override"] = item.pop("cols") # Move it to cols_override
            return items

        if args.folder:
            print(f"[FOLDER] Scanning: {args.folder}")
            items = load_folder(args.folder, args.mode, args.vol)
            for item in items:
                item["pixel"] = args.pixel
                item["rows"] = args.rows
                if args.cols is not None:
                    item["cols_override"] = args.cols
            return items

        # Single positional argument: a local file/path, or a URL.
        # A URL may be a playlist/channel → expand it into one entry per video.
        base = {"mode": args.mode, "vol": args.vol, "pixel": args.pixel, "rows": args.rows}
        if args.cols is not None:
            base["cols_override"] = args.cols

        if ytdl.is_url(args.video):
            urls = ytdl.expand_playlist(args.video)
            if len(urls) > 1:
                print(f"[YT] playlist expanded → {len(urls)} videos "
                    f"(each downloaded on demand as it plays)")
            # Keep URLs unresolved; the playback loop downloads each lazily so a
            # long playlist doesn't block startup, and the cache makes replays
            # (and --loop) instant.
            return [{"video": u, **base} for u in urls]

        return [{"video": resolve_video_path(args.video), **base}]


    # ── APP STATE ──────────────────────────────────────────────
    # Queue is stored in app.state so the WebSocket endpoint can read it.
    # current_index tracks which video is playing.
    # loop flag controls infinite playback.
    # ──────────────────────────────────────────────────────────

    @app.get("/")
    async def root():
        """Serves the Frontend (HTML/JS/CSS) file to the client."""
        return HTMLResponse(get_html_content())


    @app.get("/audio")
    async def audio_stream(v: int | None = None, start: float = 0.0):
        """
        Extracts and streams audio from the currently active video entry.
        Server-side volume control via the entry's 'vol' field (0-5 scale).
        0 = Muted (FFmpeg never runs)
        1 = Normal (1.0x)
        5 = Double  (2.0x)
        Per-session: ?v=<index> selects which queue entry to serve audio for.
        """
        queue = getattr(app.state, "queue", [])
        idx   = getattr(app.state, "current_index", 0)
        if v is not None and 0 <= v < len(queue):
            idx = v
        entry = queue[idx] if queue and 0 <= idx < len(queue) else {}

        vol_level  = entry.get("vol", 1)
        video_path = entry.get("video", "video.mp4")

        # Webcam has no audio file — return silence immediately
        if entry.get("is_webcam", False) or not isinstance(video_path, str):
            from fastapi import Response
            return Response(status_code=204)

        # vol 0 → skip audio entirely, no FFmpeg process
        if vol_level <= 0:
            from fastapi import Response
            return Response(status_code=204)

        # If it's a URL, it hasn't been downloaded yet by the playback loop.
        if ytdl.is_url(video_path) or not os.path.exists(video_path):
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Video file not downloaded or found")

        # Map 1-5 → 1.0x-2.0x FFmpeg volume
        ffmpeg_vol = 1.0 + (vol_level - 1) * 0.25

        async def audio_generator():
            ffmpeg_cmd = [
                "ffmpeg",
                "-nostdin"
            ]
            if start > 0:
                ffmpeg_cmd.extend(["-ss", str(start)])
            
            ffmpeg_cmd.extend([
                "-i", video_path,
                "-vn",
                "-filter:a", f"volume={ffmpeg_vol}",
                "-acodec", "libmp3lame",
                "-ab", "128k",
                "-ar", "44100",
                "-f", "mp3",
                "-loglevel", "quiet",
                "pipe:1"
            ])
            
            process = await asyncio.create_subprocess_exec(
                *ffmpeg_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            try:
                while True:
                    chunk = await process.stdout.read(4096)
                    if not chunk:
                        break
                    yield chunk
            except asyncio.CancelledError:
                pass
            finally:
                try:
                    process.terminate()
                    await asyncio.wait_for(process.wait(), timeout=1.0)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass

        return StreamingResponse(
            audio_generator(),
            media_type="audio/mpeg",
            headers={"Accept-Ranges": "bytes"}
        )


    # ── Scrub-preview sprite (powers the hover thumbnails on the seek bar) ──
    # A grid of small frames sampled across the video, like a YouTube preview strip.
    # Built once per video on first request and kept in memory only (no disk cache).
    # If you'd rather serve a sprite from the static compiler, just point /scrub at it.
    _scrub_cache: dict = {}  # video_path -> {"meta": {...}, "jpeg": bytes} or None


    def _build_scrub_sprite(video_path: str, max_count: int = 64, cell_w: int = 160):
        import math
        # Probe size + duration quickly (metadata only, no frame decoding).
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None
        fps      = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total    = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        w0       = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h0       = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        duration = (total / fps) if fps else 0
        if duration <= 0 or w0 <= 0 or h0 <= 0:
            return None

        cell_h = max(1, round(cell_w * h0 / w0))
        n      = max(1, min(max_count, int(duration)))   # roughly one frame per second
        cols   = max(1, math.ceil(math.sqrt(n)))
        rows   = max(1, math.ceil(n / cols))
        interval = duration / n

        # One ffmpeg pass: sample frames, scale them, tile into a single grid image.
        # Sequential decode (no per-frame seeking), so this is fast even on long clips.
        vf = f"fps={n}/{duration:.3f},scale={cell_w}:{cell_h},tile={cols}x{rows}"
        try:
            proc = subprocess.run(
                ["ffmpeg", "-nostdin", "-i", video_path, "-vf", vf,
                "-frames:v", "1", "-q:v", "4", "-f", "image2", "-c:v", "mjpeg",
                "-loglevel", "error", "pipe:1"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, timeout=120,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0 or not proc.stdout:
            return None

        return {
            "meta": {"available": True, "count": n, "gridCols": cols, "gridRows": rows,
                    "cellW": cell_w, "cellH": cell_h, "interval": interval, "duration": duration},
            "jpeg": proc.stdout,
        }


    def _scrub_video_path(v: int | None) -> str:
        queue = getattr(app.state, "queue", [])
        idx   = getattr(app.state, "current_index", 0)
        if v is not None and 0 <= v < len(queue):
            idx = v
        entry = queue[idx] if queue and 0 <= idx < len(queue) else {}
        return entry.get("video", "")


    @app.get("/scrub")
    async def scrub_meta(v: int | None = None):
        """Layout for the hover thumbnails. Builds the sprite lazily (off the event
        loop) the first time it's asked for, then reuses it from memory."""
        from fastapi import Response
        import json as _json
        # Thumbnails are on by default; --no-thumbnails turns the whole thing off.
        if not getattr(app.state, "thumbnails", True):
            return Response(content='{"available": false}', media_type="application/json")
        video_path = _scrub_video_path(v)
        if not video_path:
            return Response(content='{"available": false}', media_type="application/json")
            
        # If it's a URL, it hasn't been downloaded yet by the playback loop.
        if ytdl.is_url(video_path) or not os.path.exists(video_path):
            return Response(content='{"available": false}', media_type="application/json")
            
        if video_path not in _scrub_cache:
            loop = asyncio.get_event_loop()
            _scrub_cache[video_path] = await loop.run_in_executor(None, _build_scrub_sprite, video_path)
        built = _scrub_cache.get(video_path)
        if not built:
            return Response(content='{"available": false}', media_type="application/json")
        meta = dict(built["meta"])
        vid_id = os.path.basename(video_path)
        meta["sprite"] = f"/scrub_sprite?v={v if v is not None else 0}&id={vid_id}"
        return Response(content=_json.dumps(meta), media_type="application/json")


    @app.get("/scrub_sprite")
    async def scrub_sprite(v: int | None = None):
        from fastapi import Response, HTTPException
        built = _scrub_cache.get(_scrub_video_path(v))
        if not built:
            raise HTTPException(status_code=404, detail="Not found")
        return Response(content=built["jpeg"], media_type="image/jpeg")


    def _origin_allowed(origin: str | None, host_header: str | None = None) -> bool:
        """Reject cross-site WebSocket hijacking while allowing localhost and LAN same-origin."""
        if not origin:
            return True  # non-browser clients / test harness send no Origin
        try:
            origin_host = urlparse(origin).hostname
        except ValueError:
            return False
        if origin_host in {"localhost", "127.0.0.1"}:
            return True
        # Same-origin: the page was served by THIS server. Covers LAN mode
        # (--host 0.0.0.0), where the Origin host is the server's own LAN IP.
        if host_header and origin_host == host_header.split(":")[0]:
            return True
        return False

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        """
        Streams ASCII frames for every video in the queue.
        Advances to the next entry automatically when a video ends.
        Loops back to the start if --loop is set.
        """
        # ── Origin Check (prevents cross-site WebSocket hijacking) ──
        origin = websocket.headers.get("origin")
        if not _origin_allowed(origin, websocket.headers.get("host")):
            await websocket.close(code=1008)
            return

        await websocket.accept()

        # Opt-in adaptive codec (raw/zlib/delta). Legacy clients omit it and get
        # the original uncompressed binary protocol, byte-for-byte unchanged.
        adaptive = websocket.query_params.get("codec") == "adaptive"
        tolerance = getattr(app.state, "tolerance", 0)  # lossy colour drift budget
        # Backwards compatibility if clients send depth etc.

        queue = getattr(app.state, "queue", [])
        loop  = getattr(app.state, "loop", False)

        if not queue:
            await websocket.send_text("Error: No video in queue!")
            await websocket.close()
            return

        queue_index = 0  # local index; advances through the queue

        try:
            while True:
                entry      = queue[queue_index]
                video_path = entry["video"]

                # Lazy resolve: an unresolved URL entry (a single URL, an expanded
                # playlist item, or a URL from a playlist.json) is downloaded the
                # first time it is about to play, then the local path is cached back
                # into the queue so /audio and any --loop replay reuse the file
                # instead of re-downloading.
                if isinstance(video_path, str) and ytdl.is_url(video_path):
                    print(f"[YT] fetching ({queue_index + 1}/{len(queue)}) {video_path}")
                    try:
                        video_path = await safe_resolve_video_path(video_path)
                        entry["video"] = video_path
                    except Exception as e:
                        await websocket.send_text(f"Error: could not fetch '{video_path}': {e}")
                        queue_index += 1
                        if queue_index >= len(queue):
                            if loop:
                                queue_index = 0
                            else:
                                break
                        continue

                render_mode= entry["mode"]
                pixel_mode = entry.get("pixel", False)
                if render_mode == 1: #extra security layer for mod-1 in json (its fixes json override mechanism problem )
                    pixel_mode = False
                
                cols_override = entry.get("cols_override")
                cols = cols_override if cols_override is not None else (450 if pixel_mode else 200)
                rows_cfg   = entry.get("rows", 0)

                # IMPORTANT: Update current_index BEFORE sending INIT so that
                # when the client reloads /audio in response to INIT, the endpoint
                # already serves the correct video's audio.
                app.state.current_index = queue_index

                print(f"[PLAYING] ({queue_index + 1}/{len(queue)}) {video_path}  "
                    f"mode={render_mode}  pixel={pixel_mode}  vol={entry['vol']}")

                # ── Initialize Decoder & Auto-calculate rows ──
                is_webcam = entry.get("is_webcam", False)
                mirror = entry.get("mirror", False)
                fallback_fps = entry.get("fallback_fps", 0)

                try:
                    # Initialize decoder with dummy size to fetch dimensions without double-probing
                    decoder = VideoDecoder(
                        path=video_path,
                        cols=2,
                        rows=2,
                        skip_gray=pixel_mode,
                        mirror=mirror,
                        fallback_fps=fallback_fps
                    )
                except FileNotFoundError:
                    await websocket.send_text(f"Error: '{video_path}' not found!")
                    queue_index += 1
                    if queue_index >= len(queue):
                        if loop:
                            queue_index = 0
                        else:
                            break
                    continue

                vid_w, vid_h = decoder.vid_w, decoder.vid_h

                if rows_cfg == 0:
                    cols, rows = calc_auto_dimensions(cols, vid_w, vid_h, pixel_mode)
                    print(f"[AUTO] {vid_w}x{vid_h} → grid {cols}x{rows}")
                else:
                    rows = rows_cfg

                decoder._size = (cols, rows)  # Apply calculated size
                mapper       = AsciiMapper()
                source_fps   = decoder.fps
                MAX_FPS      = 30
                char_byte_lut= np.array([ord(c) for c in mapper._lut], dtype=np.uint8)

                # ── RUNTIME FILTERS (contrast / gamma / brightness / invert / sharpness / palette) ──
                # These are mutated by the "filter" command from the client.
                # gray_lut is a cached 256-byte LUT rebuilt only on value change.
                filter_contrast   = 1.0
                filter_gamma      = 1.0
                filter_brightness = 0.0   # range: -100 .. +100
                filter_invert     = False
                filter_sharpness  = 0     # range: 0 .. 10
                sharpness_kernel  = None
                filter_palette    = "default"
                gray_lut          = None  # None = identity (skip cv2.LUT call)
                qb           = {6: 0, 5: 2, 4: 3, 3: 5, 2: 6}.get(render_mode, 0)

                # ── FPS DECIMATION ──
                # If source > 30 FPS, skip every Nth frame using grab() (no decode).
                # This halves CPU load for 60 FPS sources.
                if source_fps > MAX_FPS:
                    skip_n = round(source_fps / MAX_FPS)  # e.g. 60/30 = 2
                    effective_fps = source_fps / skip_n
                else:
                    skip_n = 1
                    effective_fps = source_fps
                frame_t = 1.0 / effective_fps

                duration = decoder.frame_count / decoder.fps if decoder.fps > 0 else 0
                await websocket.send_text(f"INIT:{effective_fps}:{render_mode}:{cols}:{rows}:{int(pixel_mode)}:{queue_index}:{duration:.3f}:0:{int(is_webcam)}")
                if skip_n > 1:
                    print(f"[FPS CAP] {source_fps} FPS → {effective_fps} FPS (skip every {skip_n} frames)")

                frame_buf = np.empty((rows, cols, 4), dtype=np.uint8) if render_mode > 1 else None

                import struct
                import time
                start_time = asyncio.get_event_loop().time()
                bw_start_time = time.time()
                bw_bytes_sent = 0
                bw_raw_bytes = 0
                debug_mode = getattr(app.state, "debug", False)
                frame_index = 0
                prev_frame = None  # previous framebuffer snapshot for delta coding

                # Pre-allocate send buffer WITH header space to avoid per-frame concat
                if pixel_mode:
                    # Zero-Copy Pixel: 4-byte header + raw BGR (3 bytes per pixel)
                    pixel_send_buf = bytearray(4 + rows * cols * 3)
                elif render_mode > 1:
                    # ASCII Color: 4-byte header + [char,R,G,B] per pixel
                    ascii_send_buf = bytearray(4 + rows * cols * 4)

                cmd_queue = asyncio.Queue()
                is_paused = False

                async def receive_commands():
                    try:
                        while True:
                            msg = await websocket.receive_json()
                            await cmd_queue.put(msg)
                    except Exception:
                        pass
                
                receive_task = asyncio.create_task(receive_commands())

                raw_frame_num = 0

                # ── THREAD-OFFLOADED FRAME PRODUCER ──
                # Bundles ALL CPU work (decode + process + encode) into one
                # closure that runs in a thread pool, keeping the asyncio
                # event loop 100% free for I/O (WebSocket send) and timing.
                def produce(pf, fi):
                    """Decode, process, encode one frame. Returns None on EOF.
                    pf = prev_frame, fi = frame_index."""
                    for _ in range(skip_n - 1):
                        if not decoder.grab():
                            return None
                    try:
                        gray_frame, bgr_frame = next(decoder)
                    except StopIteration:
                        return None

                    if pixel_mode:
                        raw_sz = 4 + rows * cols * 3
                        struct.pack_into(">I", pixel_send_buf, 0, fi)
                        pixel_send_buf[4:] = bgr_frame.tobytes()
                        buf = bytes(pixel_send_buf)
                        return ('bytes', buf, pf, raw_sz, len(buf))
                    else:
                        # ── APPLY SHARPNESS (float32 to avoid uint8 clamping artifacts) ──
                        if sharpness_kernel is not None:
                            sharp_f = cv2.filter2D(gray_frame.astype(np.float32), -1, sharpness_kernel)
                            gray_frame = np.clip(sharp_f, 0, 255).astype(np.uint8)

                        # ── APPLY CONTRAST / GAMMA FILTER ──
                        if gray_lut is not None:
                            gray_frame = cv2.LUT(gray_frame, gray_lut)
                        
                        # Proportional mapping: evenly distribute 0-255 across 0 to (_n - 1)
                        indices = (gray_frame.astype(np.uint16) * (mapper._n - 1)) // 255
                        np.clip(indices, 0, mapper._n - 1, out=indices) # Defensive clip

                        if render_mode == 1:
                            char_matrix = mapper._lut[indices]
                            lines = [''.join(row) for row in char_matrix]
                            payload = f"{fi}\n" + '\n'.join(lines)
                            sz = len(payload.encode('utf-8'))
                            return ('text', payload, pf, sz, sz)
                        else:
                            char_codes = char_byte_lut[indices]
                            rgb = bgr_frame[:, :, ::-1]
                            if qb > 0:
                                rgb = (rgb >> qb) << qb
                            frame_buf[:, :, 0] = char_codes
                            frame_buf[:, :, 1:] = rgb
                            raw_sz = 4 + rows * cols * 4
                            if adaptive:
                                msg, npf = encode_frame(
                                    frame_buf.copy(), pf, fi, 3, tolerance)
                                return ('bytes', msg, npf, raw_sz, len(msg))
                            else:
                                struct.pack_into(">I", ascii_send_buf, 0, fi)
                                ascii_send_buf[4:] = frame_buf.tobytes()
                                buf = bytes(ascii_send_buf)
                                return ('bytes', buf, pf, raw_sz, len(buf))

                # ── BACKPRESSURE FRAME-DROP ──
                # Cheaply advance the source by one effective frame WITHOUT decoding,
                # processing, encoding, or sending it. Used when the client reports a
                # growing backlog: we skip the frame instead of making the client pay
                # the inflate+delta-patch cost for a frame it would only drop after.
                # prev_frame is intentionally left untouched by the caller, so the next
                # SENT frame is a correct delta across the gap (deltas are always
                # relative to the last sent frame). Returns False at EOF.
                def advance_one():
                    for _ in range(skip_n):
                        if not decoder.grab():
                            return False
                    return True

                # Drop once the client's decoded-frame backlog exceeds this. The client
                # render loop keeps a ~BUFFER_SIZE (4) jitter buffer, so 8 is one extra
                # buffer of slack before we start shedding. MAX_CONSEC_DROPS guarantees
                # liveness: we always send a real frame at least this often, so a stalled
                # or non-reporting client can never be starved and a large delta gap is
                # bounded.
                BACKLOG_HIGH = 15
                MAX_CONSEC_DROPS = max(1, int(round(effective_fps * 0.3)))  # ~300ms of frames
                client_backlog = 0   # latest depth reported by the client (0 = unknown/healthy)
                consec_high_reports = 0 # hysteresis: consecutive reports exceeding BACKLOG_HIGH
                consec_drops = 0

                _loop = asyncio.get_event_loop()

                try:
                    while True:
                        while not cmd_queue.empty():
                            msg = cmd_queue.get_nowait()
                            if msg.get("type") == "pause":
                                is_paused = msg.get("paused", False)
                                if not is_paused:
                                    start_time = _loop.time() - (frame_index * frame_t)
                                    bw_start_time = time.time()
                            elif msg.get("type") == "seek":
                                target_sec = float(msg.get("time", 0))
                                await _loop.run_in_executor(None, decoder.seek, target_sec)
                                prev_frame = None
                                frame_index = int(target_sec * effective_fps)
                                start_time = _loop.time() - (frame_index * frame_t)
                                bw_start_time = time.time()
                                client_backlog = 0  # stale across a seek
                                consec_high_reports = 0
                                consec_drops = 0
                            elif msg.get("type") == "buffer":
                                # Client's current decoded-frame backlog (frameBuffer.length).
                                try:
                                    client_backlog = max(0, int(msg.get("depth", 0)))
                                    if client_backlog > BACKLOG_HIGH:
                                        consec_high_reports += 1
                                    else:
                                        consec_high_reports = 0
                                except (TypeError, ValueError):
                                    client_backlog = 0
                                    consec_high_reports = 0
                            elif msg.get("type") == "reinit":
                                # Soft reload: Toggle pixel mode and send new INIT
                                pixel_mode = bool(msg.get("pixel", pixel_mode))
                                
                                cols_override = entry.get("cols_override")
                                cols = cols_override if cols_override is not None else (450 if pixel_mode else 200)
                                
                                if rows_cfg == 0:
                                    cols, rows = calc_auto_dimensions(cols, vid_w, vid_h, pixel_mode)
                                    print(f"[REINIT] {vid_w}x{vid_h} → grid {cols}x{rows}")
                                else:
                                    rows = rows_cfg
                                
                                decoder._size = (cols, rows)
                                decoder._skip_gray = pixel_mode
                                if render_mode > 1:
                                    frame_buf = np.empty((rows, cols, 4), dtype=np.uint8)
                                if pixel_mode:
                                    pixel_send_buf = bytearray(4 + rows * cols * 3)
                                
                                duration = decoder.frame_count / decoder.fps if decoder.fps > 0 else 0
                                target_sec = float(msg.get("time", 0))
                                await websocket.send_text(f"INIT:{effective_fps}:{render_mode}:{cols}:{rows}:{int(pixel_mode)}:{queue_index}:{duration:.3f}:{target_sec}:{int(is_webcam)}")
                                
                                await _loop.run_in_executor(None, decoder.seek, target_sec)
                                prev_frame = None
                                frame_index = int(target_sec * effective_fps)
                                start_time = _loop.time() - (frame_index * frame_t)
                                bw_start_time = time.time()
                                client_backlog = 0
                                consec_high_reports = 0
                                consec_drops = 0
                            elif msg.get("type") == "filter":
                                # ── RUNTIME FILTER UPDATE ──
                                # Rebuild the mapper / LUT only when values actually change.
                                new_contrast   = float(msg.get("contrast",   filter_contrast))
                                new_gamma      = float(msg.get("gamma",      filter_gamma))
                                new_brightness = float(msg.get("brightness", filter_brightness))
                                new_invert     = bool(msg.get("invert",      filter_invert))
                                new_sharpness  = int(msg.get("sharpness",    filter_sharpness))
                                new_palette    = str(msg.get("palette",      filter_palette))

                                # Clamp to safe ranges
                                new_contrast   = max(0.1, min(3.0, new_contrast))
                                new_gamma      = max(0.1, min(3.0, new_gamma))
                                new_brightness = max(-100.0, min(100.0, new_brightness))
                                new_sharpness  = max(0, min(10, new_sharpness))

                                # Sharpness Kernel update
                                if new_sharpness != filter_sharpness:
                                    filter_sharpness = new_sharpness
                                    if filter_sharpness == 0:
                                        sharpness_kernel = None
                                    else:
                                        # Unsharp mask: sharpen = original + alpha * (original - blur)
                                        # Kernel form: center gets (1 + 4*alpha), neighbours get -alpha
                                        # We use much larger alpha values (0.5 -> 5.0) for visible effect
                                        alpha = filter_sharpness * 0.5  # 0.5 at level 1, up to 5.0 at level 10
                                        sharpness_kernel = np.array([
                                            [-alpha,      -alpha,      -alpha],
                                            [-alpha,  1 + 8*alpha,    -alpha],
                                            [-alpha,      -alpha,      -alpha]
                                        ], dtype=np.float32)

                                # Palette switch
                                if new_palette != filter_palette and new_palette in FILTER_PALETTES:
                                    filter_palette = new_palette
                                    mapper = AsciiMapper(palette=FILTER_PALETTES[filter_palette])
                                    char_byte_lut = np.array(
                                        [ord(c) for c in mapper._lut], dtype=np.uint8)
                                    prev_frame = None  # force keyframe after palette change

                                # Rebuild gray LUT when any scalar filter changes
                                lut_changed = (
                                    new_contrast   != filter_contrast   or
                                    new_gamma      != filter_gamma      or
                                    new_brightness != filter_brightness or
                                    new_invert     != filter_invert
                                )
                                if lut_changed:
                                    filter_contrast   = new_contrast
                                    filter_gamma      = new_gamma
                                    filter_brightness = new_brightness
                                    filter_invert     = new_invert
                                    is_identity = (
                                        filter_contrast   == 1.0  and
                                        filter_gamma      == 1.0  and
                                        filter_brightness == 0.0  and
                                        not filter_invert
                                    )
                                    gray_lut = None if is_identity else _build_gray_lut(
                                        filter_contrast, filter_gamma,
                                        filter_brightness, filter_invert
                                    )
                                    prev_frame = None  # force keyframe

                        if is_paused:
                            await asyncio.sleep(0.1)
                            continue

                        # ── BACKPRESSURE ──
                        # If the client is behind, skip this frame instead of sending one
                        # it will only decode-then-drop. Advancing the source keeps video
                        # time-aligned with the audio/wall clock; prev_frame is held so the
                        # next sent frame is a correct delta across the gap. MAX_CONSEC_DROPS
                        # caps the gap and guarantees we never starve the client.
                        if consec_high_reports >= 2 and consec_drops < MAX_CONSEC_DROPS:
                            print(f"[Backpressure] dropping frame {frame_index}, client_backlog={client_backlog}, consec_drops={consec_drops}", flush=True)
                            advanced = await _loop.run_in_executor(None, advance_one)
                            if not advanced:
                                break
                            client_backlog -= 1   # optimistic; corrected by next report
                            consec_drops += 1
                            frame_index += 1
                            elapsed = _loop.time() - start_time
                            wait = (frame_index * frame_t) - elapsed
                            if wait > 0:
                                await asyncio.sleep(wait)
                            continue
                        consec_drops = 0


                        # ALL CPU work in thread pool — event loop stays 100% free
                        result = await _loop.run_in_executor(
                            None, produce, prev_frame, frame_index)

                        if result is None:
                            break

                        send_type, data, prev_frame, raw_size, wire_size = result

                        if send_type == 'text':
                            await websocket.send_text(data)
                        else:
                            await websocket.send_bytes(data)

                        bw_bytes_sent += wire_size
                        bw_raw_bytes += raw_size

                        current_time = time.time()
                        if debug_mode and current_time - bw_start_time >= 1.0:
                            raw_kbps = bw_raw_bytes / 1024
                            wire_kbps = bw_bytes_sent / 1024
                            ratio = raw_kbps / wire_kbps if wire_kbps > 0 else 0
                            print(f"[BW] RAW: {raw_kbps:.1f} KB/s | WIRE: {wire_kbps:.1f} KB/s | {ratio:.1f}x compression")
                            bw_start_time = current_time
                            bw_bytes_sent = 0
                            bw_raw_bytes = 0

                        elapsed = _loop.time() - start_time
                        wait = (frame_index * frame_t) - elapsed
                        if wait > 0:
                            await asyncio.sleep(wait)

                        frame_index += 1

                finally:
                    receive_task.cancel()
                    decoder.release()

                # Video finished → advance queue
                queue_index += 1
                if queue_index >= len(queue):
                    if loop:
                        print("[LOOP] Restarting queue from the beginning.")
                        queue_index = 0
                    else:
                        print("[DONE] All videos finished.")
                        break

        except (WebSocketDisconnect, ConnectionClosed, RuntimeError):
            print("Client disconnected from the stream.")


    ASCII_LOGO = "\033[36m" + r"""
    █████╗ ███████╗ ██████╗██╗██╗     ██╗███╗   ██╗███████╗  ██
    ██╔══██╗██╔════╝██╔════╝██║██║     ██║████╗  ██║██╔════╝  █████
    ███████║███████╗██║     ██║██║     ██║██╔██╗ ██║█████╗    ████████
    ██╔══██║╚════██║██║     ██║██║     ██║██║╚██╗██║██╔══╝    ████████
    ██║  ██║███████║╚██████╗██║███████╗██║██║ ╚████║███████╗  █████
    ╚═╝  ╚═╝╚══════╝ ╚═════╝╚═╝╚══════╝╚═╝╚═╝  ╚═══╝╚══════╝  ██

    """ + "\033[0m"

    HELP_TEXT = "\033[1;37m" + """
    ╔═══════════════════════════════════════════════════╗
    ║               ASCILINE  —  COMMANDS               ║
    ╠═══════════════════════════════════════════════════╣
    ║                                                   ║
    ║  \033[36m/help\033[1;37m      Show this help message               ║
    ║  \033[36m/status\033[1;37m    Show current server & playback info  ║
    ║  \033[36m/quit\033[1;37m      Stop the server and exit             ║
    ║                                                   ║
    ╠═══════════════════════════════════════════════════╣
    ║             CLI LAUNCH OPTIONS                    ║
    ╠═══════════════════════════════════════════════════╣
    ║                                                   ║
    ║  \033[33m─── Source ───\033[1;37m                                  ║
    ║  \033[32mvideo\033[1;37m          Path to a single video file      ║
    ║  \033[32m--playlist\033[1;37m     JSON playlist file               ║
    ║  \033[32m--folder\033[1;37m       Play all videos in a folder      ║
    ║                                                   ║
    ║  \033[33m─── Render ───\033[1;37m                                  ║
    ║  \033[32m--mode\033[1;37m  \033[35m1-6\033[1;37m    Color quality                    ║
    ║     1=B&W  2=64c  3=512c  4=32Kc  5=262Kc  6=16M  ║
    ║  \033[32m--pixel\033[1;37m        Pixel block mode                 ║
    ║  \033[32m--cols\033[1;37m  \033[35mN\033[1;37m      Grid columns  (default: 200)     ║
    ║  \033[32m--rows\033[1;37m  \033[35mN\033[1;37m      Grid rows     (default: auto)    ║
    ║                                                   ║
    ║  \033[33m─── Playback ───\033[1;37m                                ║
    ║  \033[32m--vol\033[1;37m   \033[35m0-5\033[1;37m    Volume (0=mute, 1=normal, 5=2x)  ║
    ║  \033[32m--loop\033[1;37m         Loop the playlist infinitely     ║
    ║  \033[32m--quality\033[1;37m \033[35mlvl\033[1;37m  Codec quality (lossless,low,etc) ║
    ║                                                   ║
    ║  \033[33m─── Server ───\033[1;37m                                  ║
    ║  \033[32m--port\033[1;37m  \033[35mN\033[1;37m      Server port    (default: 8000)    ║
    ║  \033[32m--debug\033[1;37m        Show bandwidth stats (RAW/WIRE)  ║
    ║                                                   ║
    ╚═══════════════════════════════════════════════════╝
    """ + "\033[0m"


    def print_status():
        """Prints current server status."""
        queue = getattr(app.state, "queue", [])
        idx   = getattr(app.state, "current_index", 0)
        loop  = getattr(app.state, "loop", False)
        cols  = getattr(app.state, "cols", 0)
        rows  = getattr(app.state, "rows", 0)

        print(f"\n\033[1;37m{'═'*55}\033[0m")
        print(f" \033[32m▶\033[0m \033[1mQueue\033[0m      : {len(queue)} video(s)")
        print(f" \033[32m▶\033[0m \033[1mNow Playing\033[0m: {idx + 1}/{len(queue)}")
        if queue and idx < len(queue):
            entry = queue[idx]
            px = ' \033[35m[PIXEL]\033[0m' if entry.get('pixel') else ''
            cols_override = entry.get('cols_override')
            cols = cols_override if cols_override is not None else (450 if entry.get('pixel') else 200)
            rows = entry.get('rows', rows)
            print(f" \033[32m▶\033[0m \033[1mVideo\033[0m      : \033[36m{entry['video']}\033[0m")
            print(f" \033[32m▶\033[0m \033[1mSettings\033[0m   : mode={entry['mode']}{px} vol={entry['vol']}")
        res_str = f"{cols}x{rows}" if rows > 0 else f"{cols}x(auto)"
        print(f" \033[32m▶\033[0m \033[1mResolution\033[0m : {res_str}")
        print(f" \033[32m▶\033[0m \033[1mLoop\033[0m       : {'ON' if loop else 'OFF'}")
        print(f"\033[1;37m{'═'*55}\033[0m\n")


    def command_loop():
        """Interactive command listener — runs in main thread alongside uvicorn."""
        print(f" \033[90mType \033[36m/help\033[90m for available commands.\033[0m\n")
        while True:
            try:
                cmd = input().strip().lower()
                if cmd in ('/help', 'help'):
                    print(HELP_TEXT)
                elif cmd in ('/status', 'status'):
                    print_status()
                elif cmd in ('/quit', 'quit', 'exit'):
                    print("\n \033[33m⏹  Shutting down ASCILINE...\033[0m\n")
                    os._exit(0)
                elif cmd:
                    print(f" \033[90mUnknown command: '{cmd}'. Type \033[36m/help\033[90m for options.\033[0m")
            except (EOFError, KeyboardInterrupt):
                print("\n \033[33m⏹  Shutting down ASCILINE...\033[0m\n")
                os._exit(0)


    if __name__ == "__main__":
    
        import argparse
        import os
        import threading
        
        # Enable ANSI escape sequences on Windows
        os.system("")

        parser = argparse.ArgumentParser(
            description=f"{ASCII_LOGO}\nReal-Time ASCII Web Server\n"
                        "Stream local videos to your browser with high performance ASCII and Pixel rendering.",
            formatter_class=argparse.RawTextHelpFormatter
        )

        # ── Source ──
        src = parser.add_argument_group('\033[33mSource\033[0m')
        src.add_argument(
            "video",
            nargs="?",
            default="video.mp4",
            help="Single video file to stream"
        )
        src.add_argument(
            "--playlist",
            metavar="FILE",
            default=None,
            help="Path to a playlist JSON file\n"
                "  Format: [{\"video\": \"a.mp4\", \"mode\": 5, \"vol\": 3}, ...]"
        )
        src.add_argument(
            "--folder",
            metavar="DIR",
            default=None,
            help="Path to a folder; plays all videos in filesystem order"
        )
        src.add_argument("--webcam", action="store_true", default=False, help="Use webcam instead of a video file")
        src.add_argument("--webcam-device", type=int, default=0, help="Webcam device index (default: 0)")
        src.add_argument("--webcam-fps", type=int, default=30, help="Target webcam FPS (default: 30)")
        src.add_argument("--no-mirror", action="store_true", default=False, help="Disable mirror (horizontal flip) in webcam mode")

        # ── Render ──
        render = parser.add_argument_group('\033[33mRender\033[0m')
        render.add_argument(
            "--mode",
            type=int, choices=[1, 2, 3, 4, 5, 6], default=1,
            help="Color quality: 1=B&W  2=64c  3=512c  4=32Kc  5=262Kc  6=16M Ultra"
        )
        render.add_argument(
            "--pixel",
            action="store_true", default=False,
            help="Pixel mode: replaces ASCII characters with colored blocks"
        )
        render.add_argument("--cols", type=int, default=None, help="Grid columns (default: 200 for text, 450 for pixel)")
        render.add_argument("--rows", type=int, default=0,   help="Grid rows    (default: auto from video aspect ratio)")

        # ── Playback ──
        playback = parser.add_argument_group('\033[33mPlayback\033[0m')
        playback.add_argument(
            "--vol",
            type=int, default=1,
            help="Volume 0-5  (0=muted, 1=normal, 5=double)"
        )
        playback.add_argument("--loop", action="store_true", default=False, help="Loop the queue infinitely")
        playback.add_argument(
            "--quality",
            choices=["lossless", "high", "balanced", "low"], default="lossless",
            help="Adaptive-codec colour fidelity (lossless = bit-exact; lower = "
                "smaller stream via lossy temporal delta). Chars always exact."
        )
        playback.add_argument(
            "--no-thumbnails",
            action="store_true", default=False,
            help="Turn off the hover thumbnails on the seek bar (skips building the "
                "preview sprite). The rest of the player still works."
        )


        # ── Server ──
        srv = parser.add_argument_group('\033[33mServer\033[0m')
        srv.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1; use 0.0.0.0 to expose on LAN)")
        srv.add_argument("--port", type=int, default=8000, help="Server port (default: 8000)")
        srv.add_argument("--debug", action="store_true", default=False, help="Enable bandwidth debug logging (RAW vs WIRE)")
        srv.add_argument("--cache-limit", type=int, default=10240, help="Cache limit in MB for downloaded videos (default: 10240 = 10GB)")

        args = parser.parse_args()

        # Automatically switch to a color mode if pixel mode is requested,
        # because the client requires mode > 1 to initialize the Canvas/Binary decoder.
        if args.pixel and args.mode == 1:
            args.mode = 6

        # Validate: --pixel does not support adaptive codec quality flags
        if args.pixel and args.quality != "lossless":
            print("[ERROR] --pixel mode sends raw data and does not support the adaptive codec. Remove the --quality flag.")
            exit(1)

        # Build the queue
        queue = build_queue(args)

        if not queue:
            print("[ERROR] No videos found. Check your --playlist / --folder / video argument.")
            exit(1)

        # Save state
        app.state.queue         = queue
        app.state.current_index = 0
        app.state.loop          = args.loop
        app.state.tolerance     = {"lossless": 0, "high": 4, "balanced": 8, "low": 16}[args.quality]

        app.state.debug         = args.debug
        app.state.thumbnails    = not args.no_thumbnails
        app.state.cache_limit   = args.cache_limit * 1024**2
        global_default_cols     = args.cols if args.cols is not None else (450 if args.pixel else 200)
        app.state.cols          = global_default_cols
        app.state.rows          = args.rows

        # ── High FPS Warning ──
        high_fps_videos = []
        for entry in queue:
            if entry.get("is_webcam", False):
                continue  # webcam: no fixed FPS to check
            if not isinstance(entry['video'], str):
                continue  # safety guard: non-string paths can't be URL-checked
            if ytdl.is_url(entry['video']):
                continue  # skip remote URLs; yt-dlp normalizes to 30 FPS
            cap = cv2.VideoCapture(entry['video'])
            if cap.isOpened():
                fps = cap.get(cv2.CAP_PROP_FPS)
                if fps > 35:  # Consider > 35 as high FPS
                    high_fps_videos.append((entry['video'], fps))
            cap.release()

        if high_fps_videos:
            print("\n\033[1;33m[WARNING] High FPS Source(s) Detected:\033[0m")
            for vid, fps in high_fps_videos:
                print(f"  - \033[36m{vid}\033[0m is \033[1;31m{fps:.1f} FPS\033[0m")
            print("\033[33mASCILINE is optimized for 24-30 FPS cinematic playback.")
            print("High FPS videos will automatically be decimated to ~30 FPS,")
            print("but performance may still drop depending on the system's CPU.")
            print("For optimal performance, we recommend using 30 FPS source videos.\033[0m\n")
            
            while True:
                choice = input("\033[1mDo you want to continue anyway? (y/n): \033[0m").strip().lower()
                if choice == 'y':
                    break
                elif choice == 'n':
                    print("Exiting...")
                    exit(0)

        # ── Warm-up Cache ──
        # Force the OS to load the first video into RAM cache before any client connects,
        # eliminating the initial multi-second startup lag (cold cache).
        if queue:
            first_vid = queue[0]["video"]
            is_webcam = queue[0].get("is_webcam", False)
            if not is_webcam and not ytdl.is_url(first_vid):
                try:
                    print(" \033[90m▶ Warming up cache for first video...\033[0m", end="", flush=True)
                    warm_decoder = VideoDecoder(first_vid, cols=2, rows=2)
                    warm_decoder.grab()
                    warm_decoder.release()
                    print(" \033[32mDONE\033[0m")
                except Exception as e:
                    print(f"\r\033[K \033[33m▶ Warmup failed (non-fatal): {e}\033[0m")
            elif ytdl.is_url(first_vid):
                print(" \033[90m▶ Warmup skipped: yt-dlp source (downloads on connect)\033[0m")

        # ── Startup Banner ──
        print(ASCII_LOGO)
        print(f"\033[1;37m{'═'*55}\033[0m")
        print(f" \033[32m▶\033[0m \033[1mQueue\033[0m     : {len(queue)} video(s)")
        print(f" \033[32m▶\033[0m \033[1mLoop\033[0m      : {'ON' if args.loop else 'OFF'}")
        res_str = f"{global_default_cols}x{args.rows}" if args.rows > 0 else f"{global_default_cols}x(auto)"
        print(f" \033[32m▶\033[0m \033[1mResolution\033[0m: {res_str}")
        print(f" \033[32m▶\033[0m \033[1mDefault\033[0m   : mode={args.mode} | pixel={'ON' if args.pixel else 'OFF'} | vol={args.vol}")
        print(f"\033[1;37m{'─'*55}\033[0m")
        MAX_DISPLAY = 10
        for i, entry in enumerate(queue[:MAX_DISPLAY], 1):
            px = ' \033[35m[PIXEL]\033[0m' if entry.get('pixel') else ''
            print(f"  {i:2}. \033[36m{entry['video']}\033[0m  (mode={entry['mode']}{px} vol={entry['vol']})")
        if len(queue) > MAX_DISPLAY:
            print(f"  \033[90m... and {len(queue) - MAX_DISPLAY} more\033[0m")
        print(f"\033[1;37m{'═'*55}\033[0m\n")
        print(f" \033[1;32m🚀 Server live →\033[0m \033[4;36mhttp://localhost:{args.port}\033[0m\n")

        # ── Run server in background thread, command loop in main thread ──
        server_thread = threading.Thread(
            target=uvicorn.run,
            args=(app,),
            kwargs={
                "host": args.host,
                "port": args.port,
                "log_level": "warning",
            },
            daemon=True
        )
        server_thread.start()
        command_loop()
except KeyboardInterrupt :
    print("\n \033[33m⏹  Shutting down ASCILINE...\033[0m\n")
