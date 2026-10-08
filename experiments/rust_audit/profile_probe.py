"""Measure native tag-4 encoding and export packets for JS/browser verification."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from asciline.engines import get_engine
from asciline.pixel_profile import align_profile_grid
from stream_server import calc_auto_dimensions


def stats(values):
    return {"mean": round(float(np.mean(values)), 3),
            "p95": round(float(np.percentile(values, 95)), 3),
            "max": round(float(np.max(values)), 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video")
    parser.add_argument("--cols", type=int, default=450)
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--quality", type=int, default=70)
    parser.add_argument("--start", type=float, default=5)
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "output/profile")
    args = parser.parse_args()
    engine = get_engine("rust")
    args.out.mkdir(parents=True, exist_ok=True)
    dec_ms, enc_ms, hashes = [], [], []
    total = 0
    with engine.decoder(args.video, 2, 2, skip_gray=True) as decoder:
        cols, rows = align_profile_grid(*calc_auto_dimensions(args.cols, decoder.vid_w, decoder.vid_h, True))
        decoder.resize(cols, rows)
        decoder.seek(args.start)
        encoder = engine.native.ProfileEncoder(cols, rows, args.quality, 0.75, 256, 3)
        with (args.out / "packets.bin").open("wb") as stream:
            for _ in range(args.frames):
                before = time.perf_counter()
                try:
                    _, bgr = next(decoder)
                except StopIteration:
                    break
                decoded = time.perf_counter()
                packet, shown = encoder.encode(bgr)
                finished = time.perf_counter()
                dec_ms.append((decoded - before) * 1000)
                enc_ms.append((finished - decoded) * 1000)
                hashes.append(hashlib.sha256(shown).hexdigest())
                stream.write(struct.pack(">I", len(packet)))
                stream.write(packet)
                total += len(packet)
        count = len(hashes)
        raw = count * (cols * rows * 3 + 4)
        report = {"cols": cols, "rows": rows, "frames": count, "quality": args.quality,
                  "source_fps": decoder.fps, "decode_source_ms": stats(dec_ms),
                  "encode_dct_ms": stats(enc_ms), "combined_ms": stats(np.array(dec_ms) + enc_ms),
                  "raw_bytes": raw, "dct_bytes": total,
                  "raw_to_dct_ratio": round(raw / total, 2),
                  "dct_MB_s_at_source_fps": round(total / count * decoder.fps / 1_000_000, 3)}
    (args.out / "meta.json").write_text(json.dumps({**report, "hashes": hashes}), encoding="utf-8")
    (args.out / "codec.js").write_text((ROOT / "codec.js").read_text(encoding="utf-8"), encoding="utf-8")
    (args.out / "index.html").write_text(Path(__file__).with_name("profile_bench.html").read_text(encoding="utf-8"), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
