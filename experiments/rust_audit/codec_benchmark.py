"""Small reproducible rate/quality pilot, using identical decoded BGR inputs.

No live playback or source frame skipping. Software single-thread encoders,
max GOP 48, no lookahead/alt-ref for VP9/AV1. Metrics use decoded RGB, including
color conversion. FFmpeg pipeline timings are not native function timings.
"""
import argparse
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from asciline.engines import get_engine
from asciline.pixel_profile import align_profile_grid
from stream_server import calc_auto_dimensions

ENCODERS = {
    "h264-openh264": ["-c:v", "libopenh264", "-rc_mode", "bitrate", "-allow_skip_frames", "0"],
    "vp9-vpx-rt5": ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "5",
                    "-lag-in-frames", "0", "-auto-alt-ref", "0", "-drop-threshold", "0"],
    "av1-aom-rt8": ["-c:v", "libaom-av1", "-usage", "realtime", "-cpu-used", "8",
                    "-lag-in-frames", "0", "-auto-alt-ref", "0", "-drop-threshold", "0"],
}


def command(args, log=None):
    result = subprocess.run(list(map(str, args)), capture_output=True, text=True, timeout=240)
    if log:
        log.write_text(json.dumps(list(map(str, args))) + "\n" + result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(result.stderr[-5000:])
    return result


def stats(values):
    return {"mean": float(np.mean(values)), "p95": float(np.percentile(values, 95)), "max": float(np.max(values))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, default=ROOT / "videos/costa_rica_60fps.mp4")
    parser.add_argument("--starts", type=float, nargs="+", default=[5, 18, 30])
    parser.add_argument("--frames", type=int, default=180)
    parser.add_argument("--qualities", type=int, nargs="+", default=[50, 70, 90])
    parser.add_argument("--factors", type=float, nargs="+", default=[.5, 1, 2])
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "output/codec-benchmark")
    args = parser.parse_args()
    if args.frames < 1 or 70 not in args.qualities:
        parser.error("--frames must be positive and --qualities must include 70")
    args.out.mkdir(parents=True, exist_ok=True)
    config = json.loads((ROOT / "rust_core/runtime.json").read_text(encoding="utf-8"))
    bin_dir = Path(config["ffmpeg_dir"]) / "bin"
    ffmpeg, ffprobe = bin_dir / "ffmpeg.exe", bin_dir / "ffprobe.exe"
    engine = get_engine("rust")
    report = {"git_head": command(["git", "rev-parse", "HEAD"]).stdout.strip(),
              "ffmpeg_version": command([ffmpeg, "-version"]).stdout.splitlines()[0],
              "native_sha256": hashlib.sha256(Path(engine.native.__file__).read_bytes()).hexdigest(),
              "video": str(args.video.resolve()), "frames_per_scene": args.frames,
              "settings": ENCODERS, "threads": 1, "max_gop": 48, "results": []}

    def save():
        (args.out / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    for start in args.starts:
        scene = args.out / f"scene-{start:g}"
        scene.mkdir(exist_ok=True)
        reference = scene / "reference.bgr"
        with engine.decoder(str(args.video), 2, 2, skip_gray=True) as decoder:
            width, height = align_profile_grid(*calc_auto_dimensions(450, decoder.vid_w, decoder.vid_h, True))
            fps = Fraction(decoder.fps).limit_denominator(100000)
            decoder.resize(width, height)
            decoder.seek(start)
            with reference.open("wb") as out:
                for _ in range(args.frames):
                    _, frame = next(decoder)
                    out.write(frame.tobytes())
        report["grid"] = [width, height]
        report["fps"] = str(fps)
        duration = args.frames / float(fps)
        shape = (args.frames, height, width, 3)
        ref = np.memmap(reference, dtype=np.uint8, mode="r", shape=shape)
        raw_input = ["-f", "rawvideo", "-pixel_format", "bgr24", "-video_size", f"{width}x{height}",
                     "-framerate", str(fps)]
        base = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-filter_threads", "1", "-filter_complex_threads", "1"]

        def quality(decoded, stem):
            assert decoded.stat().st_size == reference.stat().st_size, "Frame count/geometry changed"
            dist = np.memmap(decoded, dtype=np.uint8, mode="r", shape=shape)
            mses = [float(np.mean((a.astype(np.float64) - b) ** 2)) for a, b in zip(ref, dist)]
            mean_mse = float(np.mean(mses))
            measured = {"rgb_psnr_db": 10 * math.log10(255 ** 2 / mean_mse), "rgb_mse": mean_mse}
            result = command([*base, *raw_input, "-i", reference, *raw_input, "-i", decoded,
                "-lavfi", "[0:v]format=gbrp,setpts=PTS-STARTPTS[r];[1:v]format=gbrp,setpts=PTS-STARTPTS[d];[d][r]ssim",
                "-frames:v", str(args.frames), "-f", "null", "-"], scene / f"{stem}-ssim.log")
            measured["rgb_ssim"] = float(re.findall(r"All:([0-9.]+)", result.stderr)[-1])
            # A fixed mid-window frame for a visual contact sheet after all runs.
            from PIL import Image
            Image.fromarray(np.array(dist[args.frames // 2, :, :, ::-1])).save(scene / f"{stem}.png")
            del dist
            return measured

        for q in args.qualities:
            label = f"asciline-q{q}"
            decoded = scene / "decoded.bgr"
            packet_dir = scene / label
            packet_dir.mkdir(exist_ok=True)
            encoder = engine.native.ProfileEncoder(width, height, q, .75, 256, 3)
            timings, hashes, sizes = [], [], []
            with decoded.open("wb") as out, (packet_dir / "packets.bin").open("wb") as packets:
                for frame in ref:
                    before = time.perf_counter()
                    packet, shown = encoder.encode(frame)
                    timings.append((time.perf_counter() - before) * 1000)
                    out.write(shown)
                    packets.write(struct.pack(">I", len(packet)))
                    packets.write(packet)
                    sizes.append(len(packet))
                    hashes.append(hashlib.sha256(shown).hexdigest())
            del frame  # Release the final memmap view before Windows file cleanup.
            (packet_dir / "meta.json").write_text(json.dumps({"frames": args.frames, "hashes": hashes}), encoding="utf-8")
            command([shutil.which("node"), Path(__file__).with_name("profile_probe.cjs"), packet_dir])
            node = json.loads((packet_dir / "node-report.json").read_text(encoding="utf-8"))
            row = {"start": start, "codec": "asciline", "label": label, "quality": q,
                   "payload_bytes": sum(sizes), "mbps": sum(sizes) * 8 / duration / 1e6,
                   "native_encode_ms": stats(timings), **node, **quality(decoded, label)}
            report["results"].append(row)
            save()
            decoded.unlink()
            print(f"{start:g}s {label}: {row['mbps']:.3f} Mbps PSNR={row['rgb_psnr_db']:.2f} SSIM={row['rgb_ssim']:.4f}", flush=True)

        anchor = next(row["mbps"] for row in report["results"] if row["start"] == start and row["label"] == "asciline-q70")
        for codec, options in ENCODERS.items():
            for factor in args.factors:
                label = f"{codec}-{factor:g}x"
                encoded, decoded = scene / f"{label}.mkv", scene / "decoded.bgr"
                bitrate = round(anchor * factor * 1e6)
                before = time.perf_counter()
                enc = command([*base, "-benchmark", *raw_input, "-i", reference,
                    "-an", "-frames:v", str(args.frames), "-pix_fmt", "yuv420p", *options,
                    "-threads", "1", "-g", "48", "-b:v", str(bitrate), encoded], scene / f"{label}-encode.log")
                enc_wall = time.perf_counter() - before
                data = json.loads(command([ffprobe, "-v", "error", "-select_streams", "v:0",
                    "-show_entries", "packet=size:stream=extradata_size", "-of", "json", encoded]).stdout)
                payload = sum(int(p["size"]) for p in data["packets"]) + sum(int(s.get("extradata_size", 0)) for s in data["streams"])
                dec = command([*base, "-benchmark", "-threads", "1", "-i", encoded, "-an",
                    "-pix_fmt", "bgr24", "-threads", "1", "-fps_mode", "passthrough", "-f", "rawvideo", decoded], scene / f"{label}-decode.log")
                row = {"start": start, "codec": codec, "label": label, "target_mbps": bitrate / 1e6,
                       "payload_bytes": payload, "file_bytes": encoded.stat().st_size, "mbps": payload * 8 / duration / 1e6,
                       "ffmpeg_encode_wall_ms_per_frame": enc_wall * 1000 / args.frames,
                       "ffmpeg_encode_rtime_ms_per_frame": float(re.findall(r"rtime=([0-9.]+)s", enc.stderr)[-1]) * 1000 / args.frames,
                       "ffmpeg_decode_rtime_ms_per_frame": float(re.findall(r"rtime=([0-9.]+)s", dec.stderr)[-1]) * 1000 / args.frames,
                       **quality(decoded, label)}
                report["results"].append(row)
                save()
                decoded.unlink()
                print(f"{start:g}s {label}: {row['mbps']:.3f} Mbps PSNR={row['rgb_psnr_db']:.2f} SSIM={row['rgb_ssim']:.4f}", flush=True)
        ref._mmap.close()
        del ref
        reference.unlink()
    print(f"Results: {args.out / 'results.json'}", flush=True)


if __name__ == "__main__":
    main()
