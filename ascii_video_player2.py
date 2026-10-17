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


def _force_utf8_output() -> None:
    """
    Windows consoles come up on a legacy code page (cp1252, cp1254, ...) that
    has no room for the \u2580 glyph half-block mode is built on, so --help and
    every rendered frame would die with UnicodeEncodeError. Switch any stream
    that cannot carry the glyph to UTF-8; errors="replace" leaves a stubborn
    console printing "?" instead of raising. A pipe opened under an ASCII
    locale hits the same wall, which is why this is not gated on win32.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            "\u2580".encode(getattr(stream, "encoding", None) or "ascii")
        except (LookupError, UnicodeEncodeError):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError):
                pass


_force_utf8_output()


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

    # "0".."255", so the escape codes for a whole frame are assembled by NumPy
    # instead of one Python f-string per cell. A 160x40 cell grid is 6400 cells
    # per frame, far too many to format one at a time at 30 fps.
    _DEC = np.array([str(v) for v in range(256)], dtype="U3")

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

        fg = self._escapes(top,    "\033[38;2;")
        bg = self._escapes(bottom, "\033[48;2;")

        # RLE: blank the escape wherever that half repeats the previous cell's
        # colour. Column 0 stays False, so every line re-states both colours and
        # a run never carries across a line break.
        repeat = np.zeros(top.shape[:2], dtype=bool)
        repeat[:, 1:] = np.all(top[:, 1:] == top[:, :-1], axis=2)
        fg[repeat] = ""
        repeat[:, 1:] = np.all(bottom[:, 1:] == bottom[:, :-1], axis=2)
        bg[repeat] = ""

        cells = np.char.add(np.char.add(fg, bg), self._GLYPH)

        # Reset at every line end so centring padding on the next line is not
        # painted with the last background colour.
        return self._RESET + "\n".join(
            "".join(row) + self._RESET for row in cells.tolist()
        )

    def convert_diff(self, bgr: np.ndarray, pad_x: int = 0, pad_y: int = 0,
                     tolerance: int = 0) -> str:
        """
        Differential frame: redraw only the cells that visibly changed.

        A cell is redrawn when its top or bottom colour moved by more than
        ``tolerance`` (per channel, 0-255) from what is *currently on screen*.
        The reference is the displayed colour, not the previous source frame,
        so slow drifts still accumulate and eventually update.

        Every span is positioned with an absolute cursor move (CUP), so no row
        depends on the previous one and nothing can shear left/right.
        The first call (or a size change) draws the whole frame.

        :param pad_x: columns of centring offset.
        :param pad_y: rows of centring offset.
        :return:      escape string ready to write ('' if nothing changed).
        """
        if bgr.shape[0] % 2:
            bgr = np.concatenate([bgr, np.zeros_like(bgr[:1])])
        rgb = bgr[:, :, ::-1]
        if self._qb > 0:
            rgb = (rgb >> self._qb) << self._qb

        top, bottom = rgb[0::2], rgb[1::2]
        shown = getattr(self, "_shown", None)

        if shown is None or shown[0].shape != top.shape:
            self._shown = (top.copy(), bottom.copy())
            changed = np.ones(top.shape[:2], dtype=bool)
        else:
            st, sb = shown
            dt = np.abs(top.astype(np.int16) - st).max(axis=2)
            db = np.abs(bottom.astype(np.int16) - sb).max(axis=2)
            changed = (dt > tolerance) | (db > tolerance)
            if not changed.any():
                return ""
            # Update only what we are redrawing; the rest keeps its old colour.
            st[changed] = top[changed]
            sb[changed] = bottom[changed]

        st, sb = self._shown
        fg = self._escapes(st, "\033[38;2;")
        bg = self._escapes(sb, "\033[48;2;")
        rep_fg = np.zeros(st.shape[:2], dtype=bool)
        rep_bg = np.zeros(st.shape[:2], dtype=bool)
        rep_fg[:, 1:] = np.all(st[:, 1:] == st[:, :-1], axis=2)
        rep_bg[:, 1:] = np.all(sb[:, 1:] == sb[:, :-1], axis=2)

        out = []
        for r in np.flatnonzero(changed.any(axis=1)).tolist():
            cols = np.flatnonzero(changed[r])
            a, b = int(cols[0]), int(cols[-1]) + 1
            f = np.where(rep_fg[r, a:b], "", fg[r, a:b])
            g = np.where(rep_bg[r, a:b], "", bg[r, a:b])
            f[0], g[0] = fg[r, a], bg[r, a]      # a span restates both colours
            out.append(f"\033[{pad_y + r + 1};{pad_x + a + 1}H")
            out.append("".join(np.char.add(np.char.add(f, g), self._GLYPH).tolist()))
        out.append(self._RESET)
        return "".join(out)

    @classmethod
    def _escapes(cls, px: np.ndarray, lead: str) -> np.ndarray:
        """
        True Color escape code for every pixel of an (H,W,3) RGB block.

        :param lead: "\033[38;2;" for the foreground, "\033[48;2;" for the background.
        :return:     shape=(H,W) unicode array of codes like "\033[38;2;12;34;56m".
        """
        dec = cls._DEC
        add = np.char.add
        return add(add(add(add(add(lead, dec[px[:, :, 0]]), ";"),
                           dec[px[:, :, 1]]), ";"),
                   add(dec[px[:, :, 2]], "m"))


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
    _SYNC_BEGIN    = "\033[?2026h"  # synchronized output: begin atomic frame
    _SYNC_END      = "\033[?2026l"  # synchronized output: present frame
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
        tolerance    : int = 6,
    ) -> None:
        """
        :param path:          Path to video file or webcam index
        :param palette:       Custom character palette (None -> 93 levels)
        :param quantize_bits: Color quantization (0=full quality, 2=fast)
        :param cols:          Fixed columns. If 0, auto-fit to terminal.
        :param fallback_fps:  Fallback FPS if source FPS is unknown.
        :param mirror:        If True, flip each frame horizontally (for webcams).
        :param half_block:    If True, render two pixels per cell with ▀ (palette is ignored).
        :param tolerance:     Half-block only: redraw a cell only when a colour channel
                              moved by more than this (0 = every change).
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
        self._diff          = half_block
        self._tolerance     = max(0, tolerance)
        self._fps           = src_fps
        self._frame_t       = 1.0 / self._fps

    def play(self) -> None:
        """Main playback loop."""
        stdout = sys.stdout

        stdout.write(self._DISABLE_WRAP + self._HIDE_CURSOR + self._BLACK_BG)
        if self._diff:
            stdout.write(self._CLEAR_SCREEN)  # diff frames never repaint the margins
        stdout.flush()

        # Wall-clock schedule: frame N is due at start + N * frame_t. When
        # decode + mapping + the terminal write cannot keep up (maximised
        # window), frames are dropped to stay in real time instead of the
        # video sliding into slow motion.
        can_drop = not self._decoder._is_webcam
        start    = time.perf_counter()
        index    = 0

        try:
            for gray_frame, bgr_frame in self._decoder:
                if self._diff:
                    # Only changed cells, absolutely positioned (no shearing).
                    ascii_frame = self._mapper.convert_diff(
                        bgr_frame, len(self._pad_x), self._pad_y, self._tolerance)
                    prefix = ""
                else:
                    ascii_frame = self._mapper.convert(gray_frame, bgr_frame)

                    # Apply padding for centering
                    if self._pad_x:
                        ascii_frame = self._pad_x + ascii_frame.replace('\n', '\n' + self._pad_x)
                    if self._pad_y > 0:
                        ascii_frame = ('\n' * self._pad_y) + ascii_frame
                    prefix = self._CURSOR_HOME

                # Synchronized output (DEC 2026): the terminal presents the
                # frame atomically, so a slow redraw no longer shows torn,
                # vertically stretched bands. Unsupported terminals ignore it.
                if ascii_frame:
                    stdout.write(self._SYNC_BEGIN + prefix + ascii_frame
                                 + self._SYNC_END)
                    stdout.flush()

                index += 1
                due  = start + index * self._frame_t
                wait = due - time.perf_counter()
                if wait > 0:
                    time.sleep(wait)
                elif can_drop:
                    # Behind schedule: skip as many frames as we are late.
                    late = int(-wait / self._frame_t)
                    for _ in range(late):
                        if not self._decoder.grab():
                            break
                        index += 1

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
    parser.add_argument("--tolerance", type=int, default=6,
        help="Half-block: redraw only cells whose colour changed by more than this "
             "(0-255, default: 6; 0 = redraw every change, lossless)")
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
    if not 0 <= args.tolerance <= 255:
        parser.error("--tolerance must be between 0 and 255")
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
            tolerance     = args.tolerance,
        )
        renderer.play()
    except FileNotFoundError as e:
        print(f"\n[Error] {e}")
        sys.exit(1)
