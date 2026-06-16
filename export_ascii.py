"""
export_ascii.py — Export ASCILINE video frames to static files
==============================================================
Outputs files from a video in two modes:

  ASCII mode (default):
    1. <output>.txt      — Plain ASCII text, one frame per section
    2. <output>.ascjson  — JSON with char+RGB per cell

  PIXEL mode (--pixel):
    1. <output>.txt      — Full-block █ with ANSI 24-bit color codes
    2. <output>.ascjson  — Compact JSON, RGB only (3 bytes/cell, 16M colors)
                           Structure: {"meta":{cols,rows,fps,mode:"pixel"},
                                       "frames":[[r,g,b,...]...]}

Usage:
    python export_ascii.py myvideo.mp4
    python export_ascii.py myvideo.mp4 --pixel --cols 320 --max-fps 15
    python export_ascii.py myvideo.mp4 --pixel --no-color   # txt only

Dependencies: opencv-python numpy
Place this file next to ascii_video_player2.py
"""

import sys
import os
import json
import argparse
import numpy as np

# ── resolve ASCILINE root ─────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

try:
    from ascii_video_player2 import VideoDecoder, AsciiMapper
except ImportError:
    print("ERROR: ascii_video_player2.py not found. Place export_ascii.py in the ASCILINE folder.")
    sys.exit(1)

# ── palette (mirrors ASCILINE default) ───────────────────────────────────────
PALETTE = list(" `.-':_,^=;><+!rc*/z?sLTv)J7(|Fi{C}fI31tlu[neoZ5Yxjya]2ESwqkP6h9d4VpOGbUAKXHm8RD#$Bg0MNWQ%&@")
FRAME_SEP = "─" * 80

# ── ANSI 24-bit color helpers ────────────────────────────────────────────────
ANSI_FG   = "\033[38;2;{};{};{}m"
ANSI_BG   = "\033[48;2;{};{};{}m"
ANSI_RESET = "\033[0m"
FULL_BLOCK = "\u2588"  # █


def calc_rows(cols, vid_w, vid_h):
    """Match ASCILINE's auto-row formula (chars are ~2× taller than wide)."""
    ratio = vid_w / max(vid_h, 1)
    return max(1, round(cols / ratio / 2))


def export(video_path: str, output_stem: str, cols: int, rows: int | None,
           no_color: bool, max_fps: float, progress: bool, pixel_mode: bool):

    # ── Open once to get dimensions ───────────────────────────────────────
    probe = VideoDecoder(video_path, cols, rows or 1)
    vid_w, vid_h = probe.vid_w, probe.vid_h
    probe.release()

    if rows is None:
        rows = calc_rows(cols, vid_w, vid_h)

    decoder = VideoDecoder(video_path, cols, rows)
    fps = min(decoder.fps, max_fps)

    skip_n = max(1, round(decoder.fps / fps)) if decoder.fps > max_fps else 1
    effective_fps = decoder.fps / skip_n

    mode_label = "PIXEL" if pixel_mode else "ASCII"

    # ── ASCII mode setup ──────────────────────────────────────────────────
    if not pixel_mode:
        mapper = AsciiMapper(PALETTE)
        n = mapper._n
        lut = mapper._lut
        char_byte_lut = np.array([ord(c) for c in lut], dtype=np.uint8)

    txt_path  = output_stem + ".txt"
    json_path = output_stem + ".ascjson"

    print(f"Video : {video_path}")
    print(f"Grid  : {cols}×{rows}  FPS: {effective_fps:.1f}  Mode: {mode_label}")
    print(f"Output: {txt_path}")
    if not no_color:
        print(f"        {json_path}")

    txt_frames  = []
    json_frames = []
    frame_idx   = 0

    try:
        while True:
            # FPS decimation
            for _ in range(skip_n - 1):
                if not decoder.grab():
                    decoder.release()
                    break

            try:
                gray, bgr = next(decoder)
            except StopIteration:
                break

            if pixel_mode:
                # ═══════════════════════════════════════════════════════════
                #  PIXEL MODE — solid color blocks, 16M color fidelity
                # ═══════════════════════════════════════════════════════════
                rgb = bgr[:, :, ::-1].copy()  # BGR → RGB, shape (rows, cols, 3)

                # ── .txt with ANSI 24-bit color ───────────────────────────
                lines = []
                for r in range(rows):
                    parts = []
                    for c in range(cols):
                        pr, pg, pb = int(rgb[r, c, 0]), int(rgb[r, c, 1]), int(rgb[r, c, 2])
                        parts.append(f"{ANSI_FG.format(pr, pg, pb)}{FULL_BLOCK}")
                    parts.append(ANSI_RESET)
                    lines.append(''.join(parts))
                txt_frames.append(f"FRAME {frame_idx}\n" + '\n'.join(lines))

                # ── .ascjson — RGB only, 3 values per cell ────────────────
                if not no_color:
                    json_frames.append(rgb.flatten().tolist())

            else:
                # ═══════════════════════════════════════════════════════════
                #  ASCII MODE — character density + color (original)
                # ═══════════════════════════════════════════════════════════
                indices = np.floor_divide(gray, max(1, 256 // n))
                np.clip(indices, 0, n - 1, out=indices)

                char_matrix = lut[indices]
                lines = [''.join(row) for row in char_matrix]
                txt_frames.append(f"FRAME {frame_idx}\n" + '\n'.join(lines))

                if not no_color:
                    char_codes = char_byte_lut[indices]
                    rgb = bgr[:, :, ::-1].copy()
                    frame_buf = np.empty((rows, cols, 4), dtype=np.uint8)
                    frame_buf[:, :, 0] = char_codes
                    frame_buf[:, :, 1:] = rgb
                    json_frames.append(frame_buf.flatten().tolist())

            frame_idx += 1
            if progress and frame_idx % 50 == 0:
                total = decoder.frame_count
                pct = min(100, round(frame_idx * skip_n / total * 100))
                print(f"  … {frame_idx} frames ({pct}%)", end='\r')

    except KeyboardInterrupt:
        print("\nInterrupted — saving partial export…")
    finally:
        decoder.release()

    # ── Write .txt ────────────────────────────────────────────────────────────
    header = (
        f"ASCILINE {mode_label} Export\n"
        f"cols={cols} rows={rows} fps={effective_fps:.3f} "
        f"frames={frame_idx} mode={mode_label.lower()}\n"
        f"{FRAME_SEP}\n"
    )
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(header)
        f.write(f"\n{FRAME_SEP}\n".join(txt_frames))
    print(f"\n✓ Saved {frame_idx} frames → {txt_path}  ({os.path.getsize(txt_path)//1024} KB)")

    # ── Write .ascjson ───────────────────────────────────────────────────────
    if not no_color:
        meta = {
            "cols": cols,
            "rows": rows,
            "fps": round(effective_fps, 3),
            "mode": "pixel" if pixel_mode else "ascii",
        }
        with open(json_path, "w") as f:
            json.dump({"meta": meta, "frames": json_frames}, f, separators=(',', ':'))
        print(f"✓ Saved {frame_idx} frames → {json_path}  ({os.path.getsize(json_path)//1024} KB)")


# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Export ASCILINE video to .txt and .ascjson",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Mode comparison:
  ASCII  (default)  — Character density + color, artistic look
  PIXEL  (--pixel)  — Solid █ blocks + 16M color RGB, ultra-high fidelity
        """)
    ap.add_argument("video", help="Path to video file")
    ap.add_argument("-o", "--output", default=None,
                    help="Output file stem (default: same as video name)")
    ap.add_argument("--cols",    type=int,   default=240, help="ASCII columns (default 240, pixel 320)")
    ap.add_argument("--rows",    type=int,   default=None, help="ASCII rows (auto if omitted)")
    ap.add_argument("--max-fps", type=float, default=12,  help="Cap output FPS (default 12)")
    ap.add_argument("--pixel",   action="store_true",
                    help="PIXEL mode: solid color blocks, 16M colors, ultra fidelity")
    ap.add_argument("--no-color", action="store_true",   help="Skip .ascjson, txt only")
    ap.add_argument("--quiet",   action="store_true",    help="Suppress progress output")
    args = ap.parse_args()

    # Pixel mode defaults to higher resolution
    cols = args.cols
    if args.pixel and cols == 240:
        cols = 320  # pixel mode benefits from more columns

    stem = args.output or os.path.splitext(os.path.basename(args.video))[0]
    export(
        video_path  = args.video,
        output_stem = stem,
        cols        = cols,
        rows        = args.rows,
        no_color    = args.no_color,
        max_fps     = args.max_fps,
        progress    = not args.quiet,
        pixel_mode  = args.pixel,
    )


if __name__ == "__main__":
    main()