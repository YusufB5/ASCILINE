"""Resolve the FFmpeg executable without changing the machine's PATH."""
import json
import os
from pathlib import Path
import shutil

_RUNTIME_CONFIG = Path(__file__).resolve().parent.parent / "rust_core/runtime.json"


def ffmpeg_executable(*, native=False):
    """Use an explicit runtime, or the native build's runtime, before PATH.

    An unrelated, older FFmpeg on PATH may not understand files that the
    native decoder can play. Audio extraction must use the matching runtime.
    """
    root = os.environ.get("ASCILINE_FFMPEG_DIR") or os.environ.get("FFMPEG_DIR")
    if not root and native and _RUNTIME_CONFIG.exists():
        root = json.loads(_RUNTIME_CONFIG.read_text(encoding="utf-8")).get("ffmpeg_dir")
    if root:
        filename = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        directory = Path(root)
        for candidate in (directory / "bin" / filename, directory / filename):
            if candidate.is_file():
                return str(candidate.resolve())
        raise FileNotFoundError(f"FFmpeg executable not found in configured runtime: {root}")
    executable = shutil.which("ffmpeg")
    if not executable:
        raise FileNotFoundError("FFmpeg executable not found; set ASCILINE_FFMPEG_DIR")
    return executable
