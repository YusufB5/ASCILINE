"""
ASCILINE - Real-time ASCII Video Rendering & Streaming Engine
==============================================================
Python SDK & Library API.
"""

import os
import sys

# Ensure root package modules are in sys.path
_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from ascii_video_player2 import VideoDecoder, AsciiMapper
from codec import encode_frame, DEFAULT_LEVEL

__version__ = "0.1.5"
__all__ = ["VideoDecoder", "AsciiMapper", "encode_frame", "AsciiStreamServer"]

class AsciiStreamServer:
    """
    Programmatic helper to launch an ASCILINE streaming server in 2 lines.
    
    Usage:
        from asciline import AsciiStreamServer
        
        server = AsciiStreamServer(source="video.mp4", port=8000)
        server.start()
    """
    def __init__(
        self,
        source="videos/test.mp4",
        host="127.0.0.1",
        port=8000,
        mode=2,
        pixel=False,
        loop=True,
        thumbnails=False,
        quality="lossless",
        cols=None,
        rows=0,
        engine="python",
        no_resolution_limit=False,
        fps=None,
        pixel_codec="raw",
        dct_quality=70,
    ):
        self.source = source
        self.host = host
        self.port = port
        self.mode = mode
        self.pixel = pixel
        self.loop = loop
        self.thumbnails = thumbnails
        self.quality = quality
        self.cols = cols
        self.rows = rows
        self.engine = engine
        self.no_resolution_limit = no_resolution_limit
        from .playback import resolve_fps_limit
        resolve_fps_limit(engine, pixel, fps)
        self.fps = fps
        from .pixel_profile import validate_profile_quality
        if pixel_codec not in {"raw", "dct"}:
            raise ValueError("pixel_codec must be raw or dct")
        self.pixel_codec = pixel_codec
        self.dct_quality = validate_profile_quality(dct_quality)

    def start(self):
        import uvicorn
        from stream_server import app, build_queue
        import argparse

        args = argparse.Namespace(
            video=self.source,
            webcam=False,
            webcam_device=0,
            webcam_fps=30.0,
            no_mirror=False,
            playlist=None,
            folder=None,
            mode=self.mode,
            pixel=self.pixel,
            vol=1.0,
            loop=self.loop,
            cols=self.cols,
            rows=self.rows,
            quality=self.quality,
            no_thumbnails=not self.thumbnails,
            debug=False,
            cache_limit=10240
        )
        
        queue = build_queue(args)
        app.state.queue = queue
        from asciline.engines import get_engine
        app.state.engine = get_engine(self.engine)
        app.state.current_index = 0
        app.state.loop = self.loop
        app.state.tolerance = {"lossless": 0, "high": 4, "balanced": 8, "low": 16}.get(self.quality, 0)
        app.state.debug = False
        app.state.thumbnails = self.thumbnails
        app.state.cache_limit = 10 * 1024**3
        app.state.cols = (self.cols if self.cols is not None else (450 if self.pixel else 200))
        app.state.rows = self.rows
        app.state.no_resolution_limit = self.no_resolution_limit
        app.state.fps = self.fps
        app.state.pixel_codec = self.pixel_codec
        app.state.dct_quality = self.dct_quality

        print(f"[ASCILINE] Starting server on http://{self.host}:{self.port} (source: {self.source})")
        uvicorn.run(app, host=self.host, port=self.port,
                    ws_per_message_deflate=app.state.engine.name != "rust" and self.pixel_codec == "raw")
