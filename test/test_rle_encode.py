"""Byte equivalence between the uint8 and generic RLE paths."""
import os
import struct
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import codec


def _generic_frame(frame):
    storage = np.empty((*frame.shape[:2], frame.shape[2] * 2), dtype=np.uint8)
    view = storage[:, :, ::2]
    view[:] = frame
    # The reshape must preserve the strides to exercise the generic path.
    assert not view.reshape(-1, frame.shape[2]).flags.c_contiguous
    return view


@pytest.mark.parametrize("channels", [1, 2, 3, 4, 5, 8])
@pytest.mark.parametrize("lengths", [
    (65535,), (65536,), (131070,), (1, 65535, 65536, 131070, 2),
])
def test_rle_long_runs_match_generic(channels, lengths):
    values = np.arange(len(lengths) * channels, dtype=np.uint8).reshape(-1, channels)
    frame = np.repeat(values, lengths, axis=0).reshape(1, -1, channels)
    assert frame.flags.c_contiguous

    expected = bytearray()
    for length, value in zip(lengths, values):
        while length:
            count = min(length, 65535)
            expected += struct.pack("<H", count) + value.tobytes()
            length -= count

    assert codec._rle_encode(frame) == codec._rle_encode(_generic_frame(frame)) == bytes(expected)


@pytest.mark.parametrize("channels", [1, 2, 3, 4, 5, 8])
@pytest.mark.parametrize("layout", ["contiguous", "fortran", "reversed", "sliced"])
def test_rle_layouts_match_generic(channels, layout):
    rng = np.random.default_rng(82)
    frame = rng.integers(0, 256, (3, 5, channels), dtype=np.uint8)
    frame[:, 2] = frame[:, 1]
    if layout == "fortran":
        frame = np.asfortranarray(frame)
    elif layout == "reversed":
        frame = frame[::-1]
    elif layout == "sliced":
        frame = _generic_frame(frame)

    assert frame.reshape(-1, channels).flags.c_contiguous == (layout != "sliced")
    assert codec._rle_encode(frame) == codec._rle_encode(_generic_frame(frame))
