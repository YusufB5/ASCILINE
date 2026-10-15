"""Turn codec_benchmark.py output into a report and native-size frame sheets."""
import argparse
import json
import math
from pathlib import Path
import statistics
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from asciline.engines import get_engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path(__file__).parent / "output/codec-benchmark/results.json")
    parser.add_argument("--report", type=Path, default=Path(__file__).parent / "CODEC_BENCHMARK.md")
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8"))
    rows = data["results"]
    starts = sorted({row["start"] for row in rows})
    labels = ["asciline-q70", "h264-openh264-0.5x", "vp9-vpx-rt5-0.5x", "av1-aom-rt8-0.5x"]
    names = {"asciline-q70": "ASCILINE Q70", "h264-openh264-0.5x": "H.264 / OpenH264",
             "vp9-vpx-rt5-0.5x": "VP9 / libvpx RT5", "av1-aom-rt8-0.5x": "AV1 / libaom RT8"}
    aggregates = []
    for label in labels:
        group = [row for row in rows if row["label"] == label]
        assert len(group) == len(starts)
        aggregates.append({"label": label, "mbps": statistics.mean(x["mbps"] for x in group),
                           "psnr": 10 * math.log10(255 ** 2 / statistics.mean(x["rgb_mse"] for x in group)),
                           "ssim": statistics.mean(x["rgb_ssim"] for x in group)})
    table = "\n".join(f"| {names[x['label']]} | {x['mbps']:.3f} | {x['psnr']:.2f} | {x['ssim']:.4f} |" for x in aggregates)
    text = f"""# ASCILINE vs software video encoders: first pilot

Date: 2026-10-01. Source checkout: `{data['git_head']}`.
Native DLL SHA256: `{data['native_sha256']}`.
FFmpeg: `{data['ffmpeg_version']}`.

## Finding

In these three short natural-video windows, the selected VP9 and AV1 real-time
settings give higher RGB PSNR and RGB SSIM than ASCILINE Q70 at roughly half the
aggregate video payload rate. H.264/OpenH264 at that target is mixed by scene;
it is not x264 and must not be presented as a best-H.264 result. This pilot does
not establish a compression-efficiency advantage for ASCILINE over standard
codecs. The independently demonstrated achievement remains its custom
Rust/WebSocket/JavaScript/Canvas pipeline operating near 60 FPS at this grid.

## Aggregate: ASCILINE Q70 vs half-rate targets

Actual measured payload rates, **not requested encoder bitrates**:

| Encoder | Actual Mbit/s | RGB PSNR dB (higher better) | RGB SSIM (higher better) |
| --- | ---: | ---: | ---: |
{table}

Standard-codec targets were half the ASCILINE Q70 rate **for each scene**.
Actual output can undershoot or overshoot; see the full sweep below. Rates
aggregate equal-duration windows. Aggregate PSNR is computed from pooled pixel
MSE, not the arithmetic average of dB scores. SSIM averages equal frame counts.
No interpolation or equal-quality bitrate claim is made from these few points.

## Method

- One local AV1 source video, Costa Rica, three 180-frame windows starting at
  5s, 18s and 30s (540 unique input frames; about nine seconds total).
- Every encoder receives the **same predecoded BGR frames**, 464x256 at
  60000/1001 FPS. No source decoding or live frame dropping in encoding tests.
- ASCILINE Q50/Q70/Q90, dead-zone .75, skip threshold 256, zlib level 3;
  every 48th encoded frame is a keyframe. Fresh encoder for each window.
- Standards: OpenH264 bitrate mode without frame skipping; libvpx-vp9 realtime
  cpu-used 5; libaom-av1 realtime cpu-used 8. Software, one encoder thread,
  maximum GOP 48; VP9/AV1 lag-in-frames 0 and auto-alt-ref 0. YUV420P output.
  No GPU encoding, offline slow preset or multi-pass rate control.
- Standard-codec target rates: .5x, 1x and 2x that scene's measured ASCILINE Q70
  payload rate. Codec configuration and complete commands are saved in JSON/logs.
- Native ASCILINE reconstructed BGR is verified frame-for-frame by the shipped
  root `codec.js` running in Node (all 1,620 encoded frames across three qualities).
  Standard bitstreams are decoded by FFmpeg back to BGR. Decoded byte counts must
  match the reference exactly. RGB PSNR includes all channels and color-conversion
  error. FFmpeg SSIM uses planar RGB (`gbrp`) at the same native resolution.
- Payload size: ASCILINE wire packets including their headers (excluding the
  benchmark file's 4-byte length wrappers); standards sum video packet sizes
  plus codec extradata, excluding Matroska muxing overhead. Audio and WebSocket/
  TCP/IP overhead are excluded on both sides.

## Limits and interpretation

This is a reproducible **pilot**, not a broad codec ranking: one already-lossy
source, small resolution, short clips, few operating points and one machine.
The decoded source is a common reference; its earlier AV1 compression cannot
be measured or undone. Each implementation and preset matters. PSNR/SSIM are
objective reconstruction metrics, not a complete perceptual-quality verdict.

VMAF was deliberately not scored on these tiny native frames. Its default
viewing assumptions need appropriate common display scaling; low-resolution
native scores can look misleadingly high. See the
[Netflix VMAF FAQ](https://github.com/Netflix/vmaf/blob/master/resource/doc/faq.md).
Metric filters are documented in the
[FFmpeg filter manual](https://ffmpeg.org/ffmpeg-filters.html#ssim).

Timing fields are diagnostic, not directly equivalent measurements: ASCILINE
records individual native encode calls; FFmpeg `-benchmark` rtime includes its
raw-file input, conversion, encoding and output pipeline. Node JS decode timing
is not browser/Canvas playback and should not replace the user's browser logs.
The sweep does not measure network latency, seek recovery, browser hardware
decoding, energy use or comparable peak memory. Those need a separate playback
benchmark. No source-dependent adaptive bitrate tuning is claimed for ASCILINE.

## Full rate/quality sweep

| Start | Encoder / setting | Actual Mbit/s | RGB PSNR dB | RGB SSIM |
| ---: | --- | ---: | ---: | ---: |
"""
    text += "\n".join(f"| {x['start']:g}s | {x['label']} | {x['mbps']:.3f} | {x['rgb_psnr_db']:.2f} | {x['rgb_ssim']:.4f} |" for x in rows)
    text += "\n\n## Visual samples\n\nNative-size mid-window frames; a single still does not show temporal artifacts.\nThese sheets use the same half-rate comparison as the aggregate table.\nGenerated files are local ignored artifacts and can be regenerated.\n\n"
    engine = get_engine("rust")
    width, height = data["grid"]
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 18)
    title_font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 24)
    for start in starts:
        scene = args.input.parent / f"scene-{start:g}"
        with engine.decoder(data["video"], width, height, skip_gray=True) as decoder:
            decoder.seek(start)
            for _ in range(data["frames_per_scene"] // 2 + 1):
                _, bgr = next(decoder)
        reference = Image.fromarray(np.ascontiguousarray(bgr[:, :, ::-1]))
        reference.save(scene / "reference.png")
        cell_w, cell_h = width + 24, height + 82
        sheet = Image.new("RGB", (cell_w * 3, cell_h * 2 + 72), "#10161e")
        draw = ImageDraw.Draw(sheet)
        draw.text((12, 12), f"ASCILINE codec pilot | window {start:g}s | 464 x 256 | mid-window frame", font=title_font, fill="white")
        draw.text((12, 44), "Standards target half of ASCILINE Q70 bitrate. Labels show actual rates; no scaling of image tiles.", font=font, fill="#a9b6c6")
        panels = [("Reference BGR", reference, None)]
        for label in labels:
            row = next(x for x in rows if x["start"] == start and x["label"] == label)
            panels.append((names[label], Image.open(scene / f"{label}.png"), row))
        for i, (name, picture, row) in enumerate(panels):
            x, y = (i % 3) * cell_w + 12, (i // 3) * cell_h + 80
            draw.text((x, y), name, font=font, fill="white")
            if row:
                draw.text((x, y + 25), f"{row['mbps']:.3f} Mbps | {row['rgb_psnr_db']:.2f} dB | SSIM {row['rgb_ssim']:.4f}", font=font, fill="#a9b6c6")
            sheet.paste(picture, (x, y + 51))
        path = args.input.parent / f"comparison-{start:g}s.png"
        sheet.save(path)
        text += f"- [Window starting at {start:g}s](output/codec-benchmark/{path.name})\n"
    text += """
## Reproduce

```powershell
python experiments/rust_audit/codec_benchmark.py
python experiments/rust_audit/summarize_codec_benchmark.py
```

`output/codec-benchmark/results.json` contains all measurements and software
identifiers. Per-run logs preserve commands and FFmpeg diagnostics, standard
bitstreams remain available, and ASCILINE packets retain reconstruction hashes
and Node verification results. Large temporary BGR files are removed after each
scene. Benchmark scripts/report are separate from the safety checkpoint; the
player, native codec and server implementation were not changed for this pilot.
"""
    args.report.write_text(text, encoding="utf-8")
    print(table)
    print(args.report)


if __name__ == "__main__":
    main()
