"""
Tests for the terminal player's half-block renderer.

Each terminal cell shows two pixels: the upper half is the foreground colour
of a ▀ glyph, the lower half is its background colour. The frames here are
built by hand, so no video file or terminal is needed.

    python -m unittest discover -s test
    pytest test/
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ascii_video_player2 import HalfBlockMapper

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


if __name__ == "__main__":
    unittest.main()
