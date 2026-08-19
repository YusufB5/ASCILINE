"""
Bit-exactness fuzz harness for codec.py performance paths.

The encoder grew three optimizations (empty-DELTA short-circuit for identical
frames, cv2.absdiff accelerated differencing, lazy delta_shown construction).
All three must produce byte-identical wire output and identical decoder-visible
state compared to the original, straightforward algorithm. This file pins that
equivalence with a reference implementation of the original algorithm and a
randomized fuzz sweep over frame shapes, channel counts and tolerances.

    pytest test/test_codec_fastpath.py
"""
import os
import struct
import sys
import zlib

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import codec
from codec import encode_frame, TAG_RAW, TAG_ZLIB, TAG_DELTA, TAG_RLE_FULL, KEYFRAME_INTERVAL


def reference_encode_frame(frame, prev, frame_index, level=codec.DEFAULT_LEVEL, tolerance=0):
    """The original (pre-optimization) algorithm, verbatim. Ground truth."""
    raw = frame.tobytes()
    keyframe = prev is None or (frame_index % KEYFRAME_INTERVAL == 0)
    if keyframe or prev.shape != frame.shape:
        return codec._full_frame(raw, frame, frame_index, level), frame.copy()

    C = frame.shape[2]
    diff = np.abs(frame.astype(np.int16) - prev.astype(np.int16))
    if C == 4:
        char_changed = frame[:, :, 0] != prev[:, :, 0]
        if tolerance <= 0:
            color_changed = np.any(diff[:, :, 1:] != 0, axis=2)
        else:
            color_changed = np.any(diff[:, :, 1:] > tolerance, axis=2)
        changed = char_changed | color_changed
    else:
        changed = (np.any(diff != 0, axis=2) if tolerance <= 0
                   else np.any(diff > tolerance, axis=2))

    frac = float(changed.mean())
    ci = np.nonzero(changed.reshape(-1))[0].astype("<u4")

    delta_shown = prev.copy()
    delta_shown.reshape(-1, C)[ci] = frame.reshape(-1, C)[ci]

    candidates = []
    if frac < codec._DELTA_MAX_FRAC:
        vals = frame.reshape(-1, C)[ci]
        delta = zlib.compress(ci.tobytes() + vals.tobytes(), level)
        candidates.append((TAG_DELTA, delta, delta_shown))

    if frac >= codec._ZLIB_MIN_FRAC or not candidates:
        z_raw = zlib.compress(raw, level)
        rle_bytes = codec._rle_encode(frame)
        z_rle = zlib.compress(rle_bytes, level)
        if len(z_rle) < len(z_raw):
            candidates.append((TAG_RLE_FULL, z_rle, frame))
        else:
            candidates.append((TAG_ZLIB, z_raw, frame))

    tag, payload, shown = min(candidates, key=lambda c: len(c[1]))
    if len(raw) < len(payload):
        tag, payload, shown = TAG_RAW, raw, frame

    msg = struct.pack(">IB", frame_index, tag) + payload
    return msg, (shown.copy() if shown is frame else shown)


def _walk(encoder_fn, frames, tolerance):
    """Run a full frame sequence through an encoder, collecting wire bytes + state."""
    prev = None
    out = []
    for i, f in enumerate(frames):
        msg, prev = encoder_fn(f, prev, i, tolerance=tolerance)
        out.append(msg)
    return out, prev


def _make_seq(rng, rows, cols, channels, nframes, motion):
    """Pseudo-realistic sequence: mostly-static frames with moving noise blocks."""
    base = rng.integers(0, 256, (rows, cols, channels), dtype=np.uint8)
    frames = []
    for i in range(nframes):
        f = base.copy()
        if rng.random() < motion:
            bh = max(1, rows // 4)
            bw = max(1, cols // 4)
            y = rng.integers(0, max(1, rows - bh + 1))
            x = rng.integers(0, max(1, cols - bw + 1))
            f[y:y + bh, x:x + bw] = rng.integers(0, 256, (bh, bw, channels), dtype=np.uint8)
        frames.append(f)
        base = f
    return frames


@pytest.mark.parametrize("channels", [3, 4])
@pytest.mark.parametrize("tolerance", [0, 4, 8, 16])
def test_fuzz_wire_output_matches_reference(channels, tolerance):
    rng = np.random.default_rng(0xA5C11)
        # walk long enough to cross several keyframes (interval 48)
    frames = _make_seq(rng, rows=12, cols=17, channels=channels, nframes=120, motion=0.9)
    got_msgs, got_state = _walk(encode_frame, frames, tolerance)
    ref_msgs, ref_state = _walk(reference_encode_frame, frames, tolerance)
    assert got_msgs == ref_msgs
    assert np.array_equal(got_state, ref_state)


@pytest.mark.parametrize("channels", [3, 4])
@pytest.mark.parametrize("tolerance", [0, 8])
def test_identical_frames_empty_delta(channels, tolerance):
    rng = np.random.default_rng(7)
        # non-keyframe index → delta territory, and a frame == prev exactly
    frame = rng.integers(0, 256, (9, 13, channels), dtype=np.uint8)
    _, prev = encode_frame(frame, None, 1, tolerance=tolerance)  # keyframe seeds state
    msg, shown = encode_frame(frame, prev, 2, tolerance=tolerance)
    idx, tag = struct.unpack(">IB", msg[:5])
    assert (idx, tag) == (2, TAG_DELTA)
    assert zlib.decompress(msg[5:]) == b""          # empty delta payload
    assert np.array_equal(shown, frame)             # decoder-visible state == truth


def test_absdiff_fallback_matches_cv2():
    """The NumPy fallback and the cv2 fast path must agree bit-for-bit."""
    rng = np.random.default_rng(99)
    a = rng.integers(0, 256, (40, 60, 4), dtype=np.uint8)
    b = rng.integers(0, 256, (40, 60, 4), dtype=np.uint8)
    via_numpy = np.abs(a.astype(np.int16) - b.astype(np.int16))
    if codec._cv2 is None:
        pytest.skip("cv2 unavailable; nothing to compare")
    via_cv2 = codec._cv2.absdiff(a, b)
    assert via_cv2.dtype == np.uint8
    assert np.array_equal(via_cv2.astype(np.int16), via_numpy)


def test_full_frame_guard_tiny_frames():
    """Degenerate tiny frames must still fall back to RAW when compression grows data."""
    frame = np.zeros((1, 1, 3), dtype=np.uint8)
    msg, shown = encode_frame(frame, frame.copy(), 1)  # non-keyframe, identical → empty delta
    idx, tag = struct.unpack(">IB", msg[:5])
    # raw = 3 bytes < zlib(empty) = 8 bytes → guard swaps to RAW, like the reference
    assert tag == TAG_RAW
    ref_msg, ref_shown = reference_encode_frame(frame, frame.copy(), 1)
    assert msg == ref_msg


def test_all_tags_emitted_across_content():
    """Sanity: the tag race still explores RAW/ZLIB/DELTA/RLE across content types."""
    rng = np.random.default_rng(3)
    seen = set()

    # Small localized change on a random background → DELTA wins.
    prev = None
    base = rng.integers(0, 256, (24, 24, 4), dtype=np.uint8)
    for i in range(1, 6):
        f = base.copy()
        f[2:5, 2:5] = rng.integers(0, 256, (3, 3, 4), dtype=np.uint8)
        msg, prev = encode_frame(f, prev, i)
        seen.add(msg[4])
        base = f

    # Full-frame change of smooth (compressible) content → ZLIB wins.
    x = np.linspace(0, 1, 24, dtype=np.float32)
    grad = np.stack([np.outer(x, x)] * 4, axis=2)
    prev = None
    for i in range(1, 6):
        f = np.clip(grad * (128 + 10 * i), 0, 255).astype(np.uint8).copy()
        msg, prev = encode_frame(f, prev, 100 + i)
        seen.add(msg[4])

    # Incompressible full-frame noise → RAW fallback via the size guard.
    prev = None
    for i in range(1, 4):
        f = rng.integers(0, 256, (24, 24, 4), dtype=np.uint8)
        msg, prev = encode_frame(f, prev, 200 + i)
        seen.add(msg[4])

    # Large flat colour regions → RLE wins.
    flat = np.zeros((32, 32, 4), dtype=np.uint8)
    flat[:, :16] = (65, 10, 20, 30)
    for i in range(1, 4):
        msg, _ = encode_frame(flat, None, 300 + i)
        seen.add(msg[4])

    assert {TAG_RAW, TAG_ZLIB, TAG_DELTA, TAG_RLE_FULL} <= seen
