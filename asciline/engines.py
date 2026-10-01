"""Optional native backend with an explicit Python/Rust/auto selection contract."""
from dataclasses import dataclass
import argparse
import importlib
import importlib.machinery
import importlib.util
import json
import logging
import math
import os
from pathlib import Path
import sys

from ascii_video_player2 import VideoDecoder as PythonDecoder
from codec import encode_frame as python_encode

_ROOT = Path(__file__).resolve().parent.parent
_NATIVE_NAME = "_asciline_native"
_DLL_HANDLES = []


def _dll_directories():
    if os.name != "nt":
        return
    roots = [os.environ.get("ASCILINE_FFMPEG_DIR"), os.environ.get("FFMPEG_DIR")]
    config = _ROOT / "rust_core/runtime.json"
    if config.exists():
        roots.append(json.loads(config.read_text(encoding="utf-8")).get("ffmpeg_dir"))
    directories = []
    for value in roots:
        if value:
            path = Path(value)
            directories.extend((path, path / "bin"))
    directories.extend(Path(value) for value in os.environ.get("PATH", "").split(os.pathsep) if value)
    seen = set()
    for directory in directories:
        if directory.is_dir() and str(directory) not in seen and any(directory.glob("avcodec-*.dll")):
            _DLL_HANDLES.append(os.add_dll_directory(str(directory.resolve())))
            seen.add(str(directory))


def _load_native():
    if _NATIVE_NAME in sys.modules:
        module = sys.modules[_NATIVE_NAME]
    else:
        _dll_directories()
        suffix = ".dll" if os.name == "nt" else (".dylib" if sys.platform == "darwin" else ".so")
        prefix = "" if os.name == "nt" else "lib"
        binary = _ROOT / "rust_core/target/release" / f"{prefix}{_NATIVE_NAME}{suffix}"
        if binary.exists():
            config = _ROOT / "rust_core/runtime.json"
            if config.exists():
                built_for = json.loads(config.read_text(encoding="utf-8")).get("python_version")
                if built_for and built_for != list(sys.version_info[:2]):
                    raise ImportError(f"Native engine was built for Python {built_for}; rebuild with Python {list(sys.version_info[:2])}")
            loader = importlib.machinery.ExtensionFileLoader(_NATIVE_NAME, str(binary))
            spec = importlib.util.spec_from_file_location(_NATIVE_NAME, binary, loader=loader)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            sys.modules[_NATIVE_NAME] = module
        else:
            module = importlib.import_module(_NATIVE_NAME)
    if getattr(module, "API_VERSION", None) != 1:
        raise ImportError("Unsupported ASCILINE native API; rebuild rust_core with this checkout")
    return module


@dataclass(frozen=True)
class Engine:
    name: str
    native: object = None
    fallback_reason: str = None

    def decoder(self, path, cols, rows, **kwargs):
        # Device indices remain on OpenCV; the native decoder is a file engine.
        active = self if not isinstance(path, int) else Engine("python")
        implementation = active.native.VideoDecoder if active.native else PythonDecoder
        return Decoder(implementation(path, cols, rows, **kwargs), active)

    def encode_frame(self, frame, prev, frame_index, level=3, tolerance=0):
        encode = self.native.encode_frame if self.native else python_encode
        return encode(frame, prev, frame_index, level, tolerance)


class Decoder:
    """Common public API; native implementation owns timing and resource state."""
    def __init__(self, implementation, engine):
        self._implementation = implementation
        self.engine = engine

    def __getattr__(self, name):
        return getattr(self._implementation, name)

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._implementation)

    def resize(self, cols, rows):
        if self.engine.native:
            self._implementation.resize(cols, rows)
        else:
            self._implementation._size = (cols, rows)

    def set_skip_gray(self, skip):
        if self.engine.native:
            self._implementation.set_skip_gray(skip)
        else:
            self._implementation._skip_gray = skip

    def seek(self, seconds):
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("seek time must be finite and non-negative")
        return self._implementation.seek(seconds)

    def frame_index_after_seek(self, requested_seconds, fps):
        position = getattr(self._implementation, "position", None)
        return round(position * fps) if position is not None else int(requested_seconds * fps)

    def release(self):
        self._implementation.release()

    close = release

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.release()


def get_engine(selection="python"):
    if selection not in {"python", "rust", "auto"}:
        raise ValueError("engine must be python, rust or auto")
    if selection == "python":
        return Engine("python")
    try:
        return Engine("rust", _load_native())
    except (ImportError, OSError, ValueError) as exc:
        reason = f"{type(exc).__name__}: {exc}"
        if selection == "rust":
            raise RuntimeError(f"Rust engine could not load ({reason}). Run python rust_core/build.py with the same Python interpreter; on Windows supply --ffmpeg-dir.") from exc
        logging.getLogger(__name__).warning("Rust unavailable; using Python: %s", reason)
        return Engine("python", fallback_reason=reason)


def main():
    parser = argparse.ArgumentParser(description="Check the optional ASCILINE engine")
    parser.add_argument("--engine", choices=("python", "rust", "auto"), default="auto")
    args = parser.parse_args()
    engine = get_engine(args.engine)
    print(json.dumps({"engine": engine.name,
        "module": getattr(engine.native, "__file__", None),
        "version": getattr(engine.native, "__version__", None),
        "fallback_reason": engine.fallback_reason}, indent=2))


if __name__ == "__main__":
    main()
