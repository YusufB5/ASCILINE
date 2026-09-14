"""
ascii_video_player.py
=====================
Modular, True Color (24-bit ANSI), zero-flicker ASCII video player.

  - VideoDecoder    : Produces (gray, color) frame pairs from video.
  - AsciiMapper     : Gray matrix -> ASCII character + ANSI True Color code -> String.
  - HalfBlockMapper : BGR matrix -> ▀ cells, top pixel as foreground, bottom as background.
  - TerminalRenderer: Main loop, FPS control, orientation detection, rendering.

Dependencies:
    pip install opencv-python numpy
"""

import sys
import time
import shutil
import numpy as np
import cv2
import os

# Enable ANSI color codes on PowerShell/CMD (Windows):
os.system("")


# ─────────────────────────────────────────────
#  MODULE 1 ─ VideoDecoder
# ─────────────────────────────────────────────
class VideoDecoder:
    """
    Opens the video file and yields (gray, bgr) pair for each frame.

    For color rendering, both gray (for character selection) and
    original BGR (for color sampling) matrices are needed.
    Both undergo the same resize operation -> size consistency guaranteed.
    """

    def __init__(self, path, cols: int, rows: int, skip_gray: bool = False,
                 mirror: bool = False, fallback_fps: float = 0) -> None:
        """
        :param path:         Video file path (str) or webcam device index (int).
        :param mirror:       If True, flip each frame horizontally (webcam selfie view).
        :param fallback_fps: FPS override when camera-reported FPS is unreliable.
        """
        self._is_webcam = isinstance(path, int)
        self._cap = cv2.VideoCapture(path)
        if not self._cap.isOpened():
            source = f"webcam {path}" if self._is_webcam else repr(path)
            raise FileNotFoundError(f"Could not open video source: {source}")

        # Webcam: minimize internal buffer for low latency (cross-OS)
        if self._is_webcam:
            self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        reported_fps = self._cap.get(cv2.CAP_PROP_FPS) or 0
        self.fps         : float = fallback_fps if (fallback_fps > 0) else (reported_fps or 24.0)
        self.frame_count : int   = 0 if self._is_webcam else int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.vid_w       : int   = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.vid_h       : int   = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self._size       : tuple = (cols, rows)
        self._skip_gray  : bool  = skip_gray
        self._mirror     : bool  = mirror

    def __iter__(self):
        return self

    def __next__(self) -> tuple[np.ndarray, np.ndarray]:
        """
        :return: (gray[H,W] uint8,  bgr[H,W,3] uint8)
                 gray is None when skip_gray=True (pixel mode optimization)
        """
        ok, frame = self._cap.read()
        if not ok:
            raise StopIteration

        if self._mirror:
            frame = cv2.flip(frame, 1)  # horizontal flip for selfie view

        small = cv2.resize(frame, self._size, interpolation=cv2.INTER_LINEAR)
        if self._skip_gray:
            return None, small
        gray  = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        return gray, small   # small = downscaled BGR frame

    def release(self):
        self._cap.release()

    def grab(self) -> bool:
        """Advance the video by one frame WITHOUT decoding (nearly free).
        Used by stream_server for FPS decimation of high-FPS sources."""
        return self._cap.grab()

    def seek(self, target_sec: float) -> bool:
        """Seeks the video capture to the specified target second."""
        if self._cap:
            return self._cap.set(cv2.CAP_PROP_POS_MSEC, target_sec * 1000)
        return False

    def __del__(self):
        self.release()


# ─────────────────────────────────────────────
#  MODULE 2 ─ AsciiMapper
# ─────────────────────────────────────────────
class AsciiMapper:
    """
    Converts Gray + BGR matrix into a string of ASCII characters
    colored with ANSI True Color codes.

    ── True Color ANSI Format ─────────────────────────────────────────────
      \033[38;2;R;G;Bm{character}\033[0m
      └─ foreground color ───────┘

    ── Color Quantization (Performance Optimization) ───────────────────────
      Instead of generating a separate escape code for every pixel, color values
      are downsampled to 6-bit (>> 2 << 2, 64 levels/channel).
      This allows consecutive pixels with the same color to share a single escape code
      -> reduces string size and stdout.write overhead.
      There is no visually perceptible loss of color (16M -> ~262K colors).

    ── RLE (Run-Length Encoding) ───────────────────────────────────────────
      The escape code is not repeated for consecutive characters of the same color;
      a new code is appended only when the color changes.
      This provides a 40-60% reduction in string size for a typical frame.
    """

    DEFAULT_PALETTE = list(
        " `.-':_,^=;><+!rc*/z?sLTv)J7(|Fi{C}fI31tlu[neoZ5Yxjya]2ESwqkP6h9d4VpOGbUAKXHm8RD#$Bg0MNWQ%&@"
    )

    # ANSI reset + carriage return
    _RESET = "\033[0m"

    def __init__(self, palette: list[str] | None = None, quantize_bits: int = 0) -> None:
        """
        :param palette:       Character list (None -> 93 level default)
        :param quantize_bits: Right bit shift amount for color quantization.
                              2 -> 64 levels/channel (fast),
                              0 -> full 8-bit (highest quality, default).
        """
        p = palette or self.DEFAULT_PALETTE
        self._n   = len(p)
        self._lut = np.array(p, dtype='U1')
        self._qb  = quantize_bits           # quantization bit shift amount

    def convert(self, gray: np.ndarray, bgr: np.ndarray) -> str:
        """
        For each pixel:
          1. Gray value -> ASCII character (intensity LUT)
          2. BGR color  -> ANSI True Color escape code (quantized + RLE)

        :param gray: shape=(H,W)   uint8 gray matrix
        :param bgr:  shape=(H,W,3) uint8 BGR color matrix
        :return: Colored ASCII string ready to be written directly to the terminal
        """
        H, W = gray.shape

        # ── Step 1: Pixel intensity -> character index ──────────────────
        indices = (gray.astype(np.uint16) * (self._n - 1)) // 255
        np.clip(indices, 0, self._n - 1, out=indices) # Defensive clip
        char_matrix = self._lut[indices]    # shape=(H,W), dtype='U1'

        # ── Step 2: Color quantization ────────────────────────────────────
        # BGR -> RGB order (ANSI code is in R,G,B order)
        rgb = bgr[:, :, ::-1]              # BGR -> RGB view, no copy

        if self._qb > 0:
            # Zero out the lower bits -> reduce color precision, increase speed
            qb = self._qb
            rgb = (rgb >> qb) << qb        # e.g., qb=2: 0b11111100 masking

        # ── Step 3: RLE and colored string construction ─────────────────────
        # Since RLE cannot be done with pure NumPy, this part uses a Python loop.
        # However, the escape code is only written when the color changes per row;
        # loop overhead is minimized for repeated colors.
        lines = []
        prev_r = prev_g = prev_b = -1      # previous color (first pixel is always different)

        for row_idx in range(H):
            row_chars  = char_matrix[row_idx]   # shape=(W,) char array
            row_colors = rgb[row_idx]            # shape=(W,3) uint8 array
            buf = []

            for col_idx in range(W):
                r, g, b = int(row_colors[col_idx, 0]), \
                           int(row_colors[col_idx, 1]), \
                           int(row_colors[col_idx, 2])

                # RLE: only add a new escape code if the color changes
                if r != prev_r or g != prev_g or b != prev_b:
                    buf.append(f"\033[38;2;{r};{g};{b}m")
                    prev_r, prev_g, prev_b = r, g, b

                buf.append(row_chars[col_idx])

            lines.append("".join(buf))

        return self._RESET + "\n".join(lines) + self._RESET


# ─────────────────────────────────────────────
#  MODULE 2b ─ HalfBlockMapper
# ─────────────────────────────────────────────
class HalfBlockMapper:
    """
    Packs two pixels into every terminal cell using the upper-half block ▀:
    the top pixel is the foreground colour, the bottom pixel the background.
    Doubles vertical resolution compared to AsciiMapper at the same grid size.

    Same convert() signature as AsciiMapper so TerminalRenderer can use either.
    The gray matrix is ignored; there is no character to pick.
    """

    _GLYPH = "\u2580"
    _RESET = "\033[0m"

    def __init__(self, quantize_bits: int = 0) -> None:
        self._qb = quantize_bits

    def convert(self, gray, bgr: np.ndarray) -> str:
        """
        :param bgr: shape=(H,W,3) uint8 BGR matrix. Odd H gets a black row appended.
        :return:    H/2 lines of coloured ▀ glyphs, each line ending in a reset.
        """
        if bgr.shape[0] % 2:
            bgr = np.concatenate([bgr, np.zeros_like(bgr[:1])])

        rgb = bgr[:, :, ::-1]
        if self._qb > 0:
            rgb = (rgb >> self._qb) << self._qb

        top    = rgb[0::2]
        bottom = rgb[1::2]
        lines  = []

        for row_idx in range(top.shape[0]):
            fg_row = top[row_idx]
            bg_row = bottom[row_idx]
            prev_fg = prev_bg = None
            buf = []

            for col_idx in range(fg_row.shape[0]):
                fg = (int(fg_row[col_idx, 0]), int(fg_row[col_idx, 1]), int(fg_row[col_idx, 2]))
                bg = (int(bg_row[col_idx, 0]), int(bg_row[col_idx, 1]), int(bg_row[col_idx, 2]))

                if fg != prev_fg:
                    buf.append(f"\033[38;2;{fg[0]};{fg[1]};{fg[2]}m")
                    prev_fg = fg
                if bg != prev_bg:
                    buf.append(f"\033[48;2;{bg[0]};{bg[1]};{bg[2]}m")
                    prev_bg = bg

                buf.append(self._GLYPH)

            lines.append("".join(buf))

        # Reset at every line end so centring padding on the next line is not
        # painted with the last background colour.
        return self._RESET + (self._RESET + "\n").join(lines) + self._RESET


# ─────────────────────────────────────────────
#  MODULE 3 ─ TerminalRenderer
# ─────────────────────────────────────────────
class TerminalRenderer:
    """
    Manages the flow: VideoDecoder -> AsciiMapper -> stdout.

    Additional features (colored version):
      - Sets terminal background to black initially (\033[40m)
        -> colored characters appear more prominent.
      - Resets color with \033[0m at the end of each frame
        -> prevents affecting subsequent terminal commands.
    """

    _CURSOR_HOME   = "\033[H"
    _HIDE_CURSOR   = "\033[?25l"
    _SHOW_CURSOR   = "\033[?25h"
    _DISABLE_WRAP  = "\033[?7l"    # prevent line wrapping
    _ENABLE_WRAP   = "\033[?7h"    # restore line wrapping
    _BLACK_BG      = "\033[40m"    # black background — for contrast
    _RESET_ALL     = "\033[0m"
    _CLEAR_SCREEN  = "\033[2J"

    CHAR_RATIO = 0.45              # terminal character aspect ratio correction

    def __init__(
        self,
        path,
        palette      : list[str] | None = None,
        quantize_bits: int = 0,
        cols         : int = 0,
        fallback_fps : float = 0,
        mirror       : bool = False,
        half_block   : bool = False,
    ) -> None:
        """
        :param path:          Path to video file or webcam index
        :param palette:       Custom character palette (None -> 93 levels)
        :param quantize_bits: Color quantization (0=full quality, 2=fast)
        :param cols:          Fixed columns. If 0, auto-fit to terminal.
        :param fallback_fps:  Fallback FPS if source FPS is unknown.
        :param mirror:        If True, flip each frame horizontally (for webcams).
        :param half_block:    If True, render two pixels per cell with ▀ (palette is ignored).
        """
        # ── Video metadata ────────────────────────────────────────────
        # Initialize decoder once with dummy dimensions to get source resolution
        self._decoder = VideoDecoder(path, 2, 2, skip_gray=half_block,
                                     mirror=mirror, fallback_fps=fallback_fps)
        vid_w, vid_h = self._decoder.vid_w, self._decoder.vid_h
        src_fps      = self._decoder.fps

        # ── Terminal dimensions ────────────────────────────────────────────
        term    = shutil.get_terminal_size(fallback=(220, 50))
        t_cols  = term.columns
        t_lines = term.lines - 2

        # ── Orientation detection & aspect-ratio-preserving resizing ─────────────
        orientation = "portrait" if vid_h > vid_w else "landscape"
        aspect      = vid_h / vid_w

        if cols > 0:
            # User provided a fixed column width
            rows = max(1, int(cols * aspect * self.CHAR_RATIO))
        else:
            # Auto-fit to terminal size (with a safe maximum to prevent lag/wrapping)
            safe_cols = min(t_cols, 160)  # Windows terminal often struggles above 160 cols
            
            if orientation == "landscape":
                cols = safe_cols
                rows = max(1, int(cols * aspect * self.CHAR_RATIO))
                if rows > t_lines:
                    rows = t_lines
                    cols = max(1, int(rows / (aspect * self.CHAR_RATIO)))
            else:
                rows = t_lines
                cols = max(1, int(rows / (aspect * self.CHAR_RATIO)))
                if cols > safe_cols:
                    cols = safe_cols
                    rows = max(1, int(cols * aspect * self.CHAR_RATIO))

        # cols/rows above are terminal cells. Half-block mode fits the same
        # cell grid but samples two pixel rows per cell.
        pixel_rows = rows * 2 if half_block else rows

        # ── Calculate Center Padding ──────────────────────────────────────────────
        self._pad_y = max(0, (t_lines - rows) // 2)
        self._pad_x = " " * max(0, (t_cols - cols) // 2)

        # ── Info screen ──────────────────────────────────────────────────
        if half_block:
            grid_info = f"{cols}x{rows} cells, {cols}x{pixel_rows} pixels (half-block)"
        else:
            grid_info = f"{cols}x{rows} characters"

        print(self._CLEAR_SCREEN)
        print(
            f"\033[1m[ASCII Player — True Color]\033[0m\n"
            f"  Orientation : {orientation.upper()}\n"
            f"  Video       : {vid_w}x{vid_h}\n"
            f"  ASCII       : {grid_info}\n"
            f"  FPS         : {src_fps:.1f}\n"
            f"  Quantization: {2**(8-quantize_bits)} levels/channel\n"
            f"  Exit        : Ctrl+C\n"
        )
        time.sleep(2.0)

        self._decoder._size = (cols, pixel_rows)  # update target size after calculation
        self._mapper        = HalfBlockMapper(quantize_bits) if half_block \
                              else AsciiMapper(palette, quantize_bits)
        self._fps           = src_fps
        self._frame_t       = 1.0 / self._fps

    def play(self) -> None:
        """Main playback loop."""
        stdout = sys.stdout

        stdout.write(self._DISABLE_WRAP + self._HIDE_CURSOR + self._BLACK_BG)
        stdout.flush()

        try:
            for gray_frame, bgr_frame in self._decoder:
                t0 = time.perf_counter()

                ascii_frame = self._mapper.convert(gray_frame, bgr_frame)
                
                # Apply padding for centering
                if self._pad_x:
                    ascii_frame = self._pad_x + ascii_frame.replace('\n', '\n' + self._pad_x)
                if self._pad_y > 0:
                    ascii_frame = ('\n' * self._pad_y) + ascii_frame

                stdout.write(self._CURSOR_HOME + ascii_frame)
                stdout.flush()

                wait = self._frame_t - (time.perf_counter() - t0)
                if wait > 0:
                    time.sleep(wait)

        except KeyboardInterrupt:
            pass

        finally:
            stdout.write(self._ENABLE_WRAP + self._SHOW_CURSOR + self._RESET_ALL + "\n")
            stdout.flush()
            self._decoder.release()


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="True Color ANSI ASCII video player — zero flicker"
    )
    parser.add_argument("video", nargs="?", default=None,
        help="Path to video file (MP4, AVI, MKV ...)")
    parser.add_argument("--palette", default=None,
        help="Custom character palette, space-separated")
    parser.add_argument("-q", "--quality", type=int, choices=[0, 1, 2, 3], default=0,
        help="Color quality: 0=max quality, 3=max speed (default: 0)")
    parser.add_argument("-c", "--cols", type=int, default=0,
        help="Fixed grid width. If 0, auto-fits to terminal (default: 0)")
    parser.add_argument("--half-block", action="store_true", default=False,
        help="Render two pixels per cell with \u2580 (doubles vertical resolution)")
    parser.add_argument("--webcam", action="store_true", default=False,
        help="Use webcam instead of a video file")
    parser.add_argument("--webcam-device", type=int, default=0,
        help="Webcam device index (default: 0)")
    parser.add_argument("--webcam-fps", type=int, default=30,
        help="Target webcam FPS (default: 30)")
    parser.add_argument("--no-mirror", action="store_true", default=False,
        help="Disable mirror (horizontal flip) in webcam mode")
    args = parser.parse_args()

    if not args.webcam and args.video is None:
        parser.error("a video file is required (or use --webcam)")
    if args.half_block and args.palette:
        parser.error("--palette has no effect with --half-block")

    custom_palette = args.palette.split() if args.palette else None

    # Determine video source: webcam device index or file path
    video_source = args.webcam_device if args.webcam else args.video
    mirror = args.webcam and not args.no_mirror

    try:
        renderer = TerminalRenderer(
            path          = video_source,
            palette       = custom_palette,
            quantize_bits = args.quality,
            cols          = args.cols,
            fallback_fps  = args.webcam_fps if args.webcam else 0,
            mirror        = mirror,
            half_block    = args.half_block,
        )
        renderer.play()
    except FileNotFoundError as e:
        print(f"\n[Error] {e}")
        sys.exit(1)
