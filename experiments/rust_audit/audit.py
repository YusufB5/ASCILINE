"""Read-only engine audit; generates synthetic fixtures and JSON evidence locally.

Existing native binaries are loaded explicitly. Results do not establish that
those binaries were built from the currently visible sources.
"""
import argparse
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import codec
from ascii_video_player2 import VideoDecoder as PythonDecoder


def digest(data):
    return hashlib.sha256(data).hexdigest()


def decode_time(decoder_class, clip, native):
    decoder = decoder_class(str(clip), 450, 253, skip_gray=True)
    hashes, times = [], []
    while True:
        start = time.perf_counter()
        try:
            _, frame = next(decoder)
        except StopIteration:
            break
        times.append((time.perf_counter() - start) * 1000)
        hashes.append(digest(frame.tobytes()))
    result = {
        "fps_metadata": decoder.fps, "frames_metadata": decoder.frame_count,
        "frames_read": len(hashes), "decode_resize_ms_median": statistics.median(times),
        "decode_resize_ms_p95": float(np.percentile(times, 95)),
        "note": "Unpaced decode+resize only; excludes network, browser and audio.",
    }
    decoder.release()
    del decoder
    seeks = []
    for target in (0.35, 1.23, 2.35):
        decoder = decoder_class(str(clip), 450, 253, skip_gray=True)
        ok = decoder.seek(target)
        try:
            _, frame = next(decoder)
            frame_hash = digest(frame.tobytes())
            index = hashes.index(frame_hash) if frame_hash in hashes else None
        except StopIteration:
            index = None
        seeks.append({"requested_seconds": target, "seek_ok": ok,
                      "first_frame_sequential_index": index,
                      "first_frame_seconds": index / decoder.fps if index is not None else None})
        decoder.release()
        del decoder
    result["seeks"] = seeks
    if native:
        decoder = decoder_class(str(clip), 450, 253, skip_gray=True)
        decoder.release()
        try:
            next(decoder)
            result["next_after_release"] = "still yields a frame"
        except Exception as exc:
            result["next_after_release"] = type(exc).__name__
        del decoder
        decoder = decoder_class(str(clip), 450, 253, skip_gray=True)
        grabbed = 0
        while decoder.grab():
            grabbed += 1
        result["grab_frames_read"] = grabbed
        decoder.release()
    return result


def codec_checks(module, output):
    rng = np.random.default_rng(42)
    checks, vectors = [], []
    for channels in (3, 4):
        for tolerance in (0, 4, 16):
            py_prev = rust_prev = None
            frames = [np.full((16, 32, channels), 80, np.uint8)]
            for index in range(1, 50):
                frame = frames[-1].copy()
                if index % 10 == 5:
                    tile = rng.integers(0, 256, (16, 4, channels), dtype=np.uint8)
                    frame = np.tile(tile, (1, 8, 1))
                elif index % 10 == 0:
                    frame = rng.integers(0, 256, frame.shape, dtype=np.uint8)
                elif index % 3 == 0:
                    frame[2, 3] = rng.integers(0, 256, channels, dtype=np.uint8)
                elif index % 3 == 2:
                    color_start = 1 if channels == 4 else 0
                    frame[2, 3, color_start:] ^= np.uint8(1)
                frames.append(frame)
            wire_equal = shown_equal = 0
            tags = set()
            for index, frame in enumerate(frames):
                py_msg, py_prev = codec.encode_frame(frame, py_prev, index, 3, tolerance)
                rust_msg, rust_prev = module.encode_frame(frame, rust_prev, index, 3, tolerance)
                wire_equal += py_msg == rust_msg
                shown_equal += np.array_equal(py_prev, rust_prev)
                tags.add(rust_msg[4])
                vectors.append({"group": f"adaptive-{channels}-{tolerance}",
                                "channels": channels, "message": rust_msg.hex(),
                                "shown": rust_prev.tobytes().hex()})
            checks.append({"channels": channels, "tolerance": tolerance,
                           "wire_equal": wire_equal, "shown_equal": shown_equal,
                           "frames": len(frames), "tags": sorted(tags)})
    if hasattr(module, "RustProfileEncoder"):
        rust = module.RustProfileEncoder(32, 32, 70, 0.75, 256, 6)
        reference = codec.ProfileEncoder(32, 32, 70, 0.75, 256, 6)
        wire_equal = shown_equal = 0
        for index in range(50):
            frame = rng.integers(0, 256, (32, 32, 3), dtype=np.uint8)
            py_msg, py_shown = reference.encode(frame)
            rust_msg, rust_shown = rust.encode(frame)
            wire_equal += py_msg == rust_msg
            shown_equal += py_shown == rust_shown
            vectors.append({"group": "dct", "channels": 3,
                            "message": rust_msg.hex(), "shown": rust_shown.hex()})
            vectors.append({"group": "python-dct-control", "channels": 3,
                            "message": py_msg.hex(), "shown": py_shown.hex()})
        checks.append({"dct_frames": 50, "wire_equal": wire_equal,
                       "shown_equal": shown_equal})
    output.write_text(json.dumps(vectors), encoding="utf-8")
    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=("python", "installed", "rust-folder", "rust-core"), required=True)
    parser.add_argument("--ffmpeg-dir", type=Path, required=True)
    args = parser.parse_args()
    output = Path(__file__).parent / "output"
    output.mkdir(exist_ok=True)
    clip = output / "cfr60_gop120.mp4"
    if not clip.exists() or clip.stat().st_size == 0:
        subprocess.run([str(args.ffmpeg_dir / "ffmpeg.exe"), "-nostdin", "-y", "-v", "error",
                        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=60:duration=3",
                        "-c:v", "mpeg4", "-q:v", "3", "-g", "120",
                        "-bf", "2", "-pix_fmt", "yuv420p", str(clip)], check=True)
    handle = os.add_dll_directory(str(args.ffmpeg_dir))
    module = None
    result = {"engine": args.engine, "clip": str(clip), "clip_sha256": digest(clip.read_bytes())}
    if args.engine != "python":
        if args.engine == "installed":
            import ascii_core as module
            import ascii_core.ascii_core as native
            binary = Path(native.__file__)
        else:
            folder = ROOT / ("rust_core" if args.engine == "rust-core" else "RUST-ENTEGRATİON/ASCILINE-RUST")
            binary = folder / "target/release/ascii_core.dll"
            loader = importlib.machinery.ExtensionFileLoader("ascii_core", str(binary))
            spec = importlib.util.spec_from_file_location("ascii_core", binary, loader=loader)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        result.update(binary=str(binary), binary_sha256=digest(binary.read_bytes()),
                      exports=[name for name in dir(module) if not name.startswith("_")])
        result["codec"] = codec_checks(module, output / f"{args.engine}_vectors.json")
    result["decoder"] = decode_time(module.VideoDecoder if module else PythonDecoder, clip, module is not None)
    (output / f"{args.engine}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    handle.close()


if __name__ == "__main__":
    main()
