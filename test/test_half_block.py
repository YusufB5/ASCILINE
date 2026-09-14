"""
Tests for the terminal player's half-block renderer.

Each terminal cell shows two pixels: the upper half is the foreground colour
of a ▀ glyph, the lower half is its background colour. The frames here are
built by hand, so no video file or terminal is needed.

    python -m unittest discover -s test
    pytest test/
"""
import io
import os
import sys
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import numpy as np
import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ascii_video_player2 import HalfBlockMapper, TerminalRenderer

RESET = "\033[0m"


def fg(r, g, b):
    return f"\033[38;2;{r};{g};{b}m"


def bg(r, g, b):
    return f"\033[48;2;{r};{g};{b}m"


class HalfBlockMapperTest(unittest.TestCase):
    def test_two_pixel_rows_become_one_line(self):
        # Input is BGR: top pixel red, bottom pixel blue.
        frame = np.array([[[0, 0, 255]], [[255, 0, 0]]], dtype=np.uint8)
        out = HalfBlockMapper().convert(None, frame)
        self.assertEqual(out, RESET + fg(255, 0, 0) + bg(0, 0, 255) + "▀" + RESET)

    def test_line_count_is_half_the_pixel_rows(self):
        frame = np.zeros((6, 4, 3), dtype=np.uint8)
        out = HalfBlockMapper().convert(None, frame)
        self.assertEqual(out.count("\n"), 2)
        self.assertEqual(out.count("▀"), 12)

    def test_escape_is_emitted_only_when_that_half_changes(self):
        frame = np.zeros((2, 5, 3), dtype=np.uint8)
        frame[0, :] = (10, 20, 30)
        frame[1, :] = (40, 50, 60)
        frame[1, 3] = (0, 0, 0)
        out = HalfBlockMapper().convert(None, frame)
        self.assertEqual(out.count("\033[38;2;"), 1)
        self.assertEqual(out.count("\033[48;2;"), 3)

    def test_each_line_ends_with_reset(self):
        # The renderer prepends spaces for centring; they must not inherit
        # the previous line's background.
        frame = np.full((4, 2, 3), 200, dtype=np.uint8)
        out = HalfBlockMapper().convert(None, frame)
        for line in out.split("\n"):
            self.assertTrue(line.endswith(RESET), repr(line))

    def test_odd_row_count_fills_last_lower_half_with_black(self):
        frame = np.full((3, 1, 3), 100, dtype=np.uint8)
        out = HalfBlockMapper().convert(None, frame)
        self.assertIn(bg(0, 0, 0), out.split("\n")[-1])

    def test_quantize_bits_drop_low_colour_bits(self):
        frame = np.full((2, 1, 3), 255, dtype=np.uint8)
        out = HalfBlockMapper(quantize_bits=2).convert(None, frame)
        self.assertIn(fg(252, 252, 252), out)
        self.assertIn(bg(252, 252, 252), out)


def _make_video(path, frames=3, w=64, h=36, fps=10.0):
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), fps, (w, h))
    if not vw.isOpened():
        return False
    for i in range(frames):
        img = np.zeros((h, w, 3), np.uint8)
        img[:, : w // 2] = (40, 80, 120)
        img[:, w // 2 :] = (120 + i * 20, 80, 40)
        vw.write(img)
    vw.release()
    return os.path.exists(path) and os.path.getsize(path) > 0


class TerminalRendererHalfBlockTest(unittest.TestCase):
    TERM = os.terminal_size((120, 42))

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.video = os.path.join(self.tmp, "clip.avi")
        if not _make_video(self.video):
            self.skipTest("cv2.VideoWriter unavailable")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _renderer(self, **kwargs):
        with patch("ascii_video_player2.time.sleep"), \
             patch("ascii_video_player2.shutil.get_terminal_size", return_value=self.TERM), \
             redirect_stdout(io.StringIO()):
            return TerminalRenderer(self.video, **kwargs)

    def test_half_block_doubles_pixel_rows_for_the_same_cell_grid(self):
        plain = self._renderer(cols=100)
        half = self._renderer(cols=100, half_block=True)
        cols, rows = plain._decoder._size
        self.assertEqual(half._decoder._size, (cols, rows * 2))

    def test_play_writes_half_block_frames(self):
        renderer = self._renderer(cols=100, half_block=True)
        cols, pixel_rows = renderer._decoder._size
        out = io.StringIO()
        with patch("ascii_video_player2.time.sleep"), redirect_stdout(out):
            renderer.play()
        text = out.getvalue()
        self.assertEqual(text.count("\u2580"), 3 * cols * pixel_rows // 2)
        self.assertIn("\033[48;2;", text)


class HalfBlockCliTest(unittest.TestCase):
    PLAYER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "ascii_video_player2.py")

    def _run(self, *args):
        return subprocess.run([sys.executable, self.PLAYER, *args],
                              capture_output=True, text=True, encoding="utf-8")

    def test_help_lists_half_block(self):
        result = self._run("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--half-block", result.stdout)

    def test_half_block_rejects_palette(self):
        result = self._run("clip.mp4", "--half-block", "--palette", "#")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--palette has no effect with --half-block", result.stderr)


if __name__ == "__main__":
    unittest.main()
