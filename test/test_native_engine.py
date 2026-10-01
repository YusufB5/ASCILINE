"""Native regression tests. Requires a workspace build and an FFmpeg executable."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading

import numpy as np
import pytest

from asciline.engines import get_engine
import codec

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def engine():
    try:
        return get_engine("rust")
    except RuntimeError as exc:
        pytest.skip(str(exc))


@pytest.fixture(scope="module")
def clip(tmp_path_factory, engine):
    config = ROOT / "rust_core/runtime.json"
    ffmpeg_dir = os.environ.get("FFMPEG_DIR")
    if not ffmpeg_dir and config.exists():
        ffmpeg_dir = json.loads(config.read_text(encoding="utf-8")).get("ffmpeg_dir")
    ffmpeg = str(Path(ffmpeg_dir) / "bin/ffmpeg.exe") if os.name == "nt" and ffmpeg_dir else shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg executable not available")
    path = tmp_path_factory.mktemp("native") / "long_gop.mp4"
    subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=320x180:rate=60:duration=3", "-c:v", "mpeg4",
                    "-q:v", "3", "-g", "120", "-bf", "2", str(path)], check=True)
    return path


def fingerprint(frame):
    return hashlib.sha256(frame.tobytes()).digest()


def test_accurate_seek_and_seek_after_eof(engine, clip):
    with engine.decoder(str(clip), 160, 90, skip_gray=True) as decoder:
        reference = [fingerprint(bgr) for _, bgr in decoder]
        assert len(reference) == 180
        for seconds, expected in [(0.35, 21), (1.23, 74), (2.35, 141), (0, 0)]:
            assert decoder.seek(seconds)
            assert decoder.frame_index_after_seek(seconds, 60) == expected
            _, bgr = next(decoder)
            assert fingerprint(bgr) == reference[expected]
            assert decoder.position == pytest.approx(expected / 60)


def test_grab_drains_b_frames_and_mixed_read(engine, clip):
    with engine.decoder(str(clip), 160, 90, skip_gray=True) as decoder:
        assert sum(1 for _ in iter(decoder.grab, False)) == 180
        assert not decoder.grab()
        with pytest.raises(StopIteration):
            next(decoder)
        assert decoder.seek(0)
        count = 0
        for index in range(180):
            if index % 2:
                assert decoder.grab()
            else:
                next(decoder)
            count += 1
        assert count == 180
        assert not decoder.grab()


def test_close_is_real_and_idempotent(engine, clip):
    decoder = engine.decoder(str(clip), 16, 16)
    decoder.release()
    decoder.release()
    for operation in (lambda: next(decoder), decoder.grab, lambda: decoder.seek(0),
                      lambda: decoder.resize(32, 32)):
        with pytest.raises(RuntimeError, match="closed"):
            operation()


def test_resize_gray_and_mirror(engine, clip):
    with engine.decoder(str(clip), 160, 90, skip_gray=True) as normal:
        _, expected = next(normal)
    with engine.decoder(str(clip), 160, 90, mirror=True) as decoder:
        gray, mirrored = next(decoder)
        np.testing.assert_array_equal(mirrored, expected[:, ::-1])
        assert gray.shape == (90, 160)
        assert decoder.seek(0.35)
        decoder.resize(40, 20)
        decoder.set_skip_gray(True)
        gray, bgr = next(decoder)
        assert gray is None and bgr.shape == (20, 40, 3)
        assert decoder.position == pytest.approx(0.35)


def test_nonzero_stream_start_is_normalized(engine, clip, tmp_path):
    config = json.loads((ROOT / "rust_core/runtime.json").read_text(encoding="utf-8"))
    ffmpeg = str(Path(config["ffmpeg_dir"]) / "bin/ffmpeg.exe") if os.name == "nt" else shutil.which("ffmpeg")
    shifted = tmp_path / "shifted.mp4"
    subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-itsoffset", "5", "-i", str(clip),
                    "-c", "copy", str(shifted)], check=True)
    with engine.decoder(str(shifted), 160, 90, skip_gray=True) as decoder:
        _, first = next(decoder)
        assert decoder.position == pytest.approx(0)
        reference = [fingerprint(first)] + [fingerprint(bgr) for _, bgr in decoder]
        assert len(reference) == 180
        assert decoder.seek(1.23)
        _, sought = next(decoder)
        assert decoder.position == pytest.approx(74 / 60)
        assert fingerprint(sought) == reference[74]


@pytest.mark.parametrize("seconds", [-1, float("nan"), float("inf")])
def test_invalid_seek(engine, clip, seconds):
    with engine.decoder(str(clip), 16, 16) as decoder:
        with pytest.raises(ValueError):
            decoder.seek(seconds)


def test_native_validation(engine, clip):
    native = engine.native
    with pytest.raises(ValueError):
        native.VideoDecoder(str(clip), 0, 16)
    gray = np.zeros((2, 3), np.uint8)
    with pytest.raises(ValueError):
        native.build_text_frame(gray, "")
    with pytest.raises(ValueError):
        native.build_frame_buf(gray, np.zeros(3, np.uint8), np.array([32], np.uint8), 0)
    with pytest.raises(ValueError):
        native.build_frame_buf(gray, np.zeros(18, np.uint8), np.array([32], np.uint8), 8)
    with pytest.raises(ValueError):
        native.encode_frame(np.zeros((2, 3, 2), np.uint8))
    with pytest.raises(ValueError):
        native.encode_frame(np.zeros((2, 3, 4), np.uint8), level=10)


@pytest.mark.parametrize("channels", [3, 4])
@pytest.mark.parametrize("tolerance", [0, 4, 16])
def test_codec_matches_python_across_keyframes(engine, channels, tolerance):
    rng = np.random.default_rng(123)
    frame = np.full((16, 32, channels), 80, np.uint8)
    py_prev = native_prev = None
    tags = set()
    for index in range(100):
        frame = frame.copy()
        if index % 10 == 5:
            frame = np.tile(rng.integers(0, 256, (16, 4, channels), dtype=np.uint8), (1, 8, 1))
        elif index % 10 == 0 and index:
            frame = rng.integers(0, 256, frame.shape, dtype=np.uint8)
        elif index % 3 == 0:
            frame[2, 3] = rng.integers(0, 256, channels, dtype=np.uint8)
        elif index % 3 == 2:
            frame[2, 3, 1 if channels == 4 else 0:] ^= np.uint8(1)
        py_msg, py_prev = codec.encode_frame(frame, py_prev, index, 3, tolerance)
        native_msg, native_prev = engine.encode_frame(frame, native_prev, index, 3, tolerance)
        assert native_msg == py_msg
        np.testing.assert_array_equal(native_prev, py_prev)
        tags.add(native_msg[4])
    assert tags == {0, 1, 2, 3}


def test_character_mapping(engine):
    gray = np.arange(256, dtype=np.uint8).reshape(16, 16)
    rng = np.random.default_rng(99)
    bgr = rng.integers(0, 256, (16, 16, 3), dtype=np.uint8)
    palette = " .:+#@"
    indices = gray.astype(np.uint16) * (len(palette) - 1) // 255
    expected_text = "\n".join("".join(palette[index] for index in row) for row in indices)
    assert engine.native.build_text_frame(gray, palette) == expected_text
    for qb in (0, 2, 3, 5, 6):
        lut = np.frombuffer(palette.encode("ascii"), dtype=np.uint8)
        result = engine.native.build_frame_buf(gray, bgr.reshape(-1), lut, qb)
        np.testing.assert_array_equal(result[:, :, 0], lut[indices])
        np.testing.assert_array_equal(result[:, :, 1:], (bgr[:, :, ::-1] >> qb) << qb)


def test_native_encode_releases_gil(engine):
    frame = np.random.default_rng(5).integers(0, 256, (768, 1024, 4), dtype=np.uint8)
    entered, done = threading.Event(), threading.Event()
    failures = []
    def encode():
        entered.set()
        try:
            engine.encode_frame(frame, None, 0, 9, 0)
        except Exception as exc:
            failures.append(exc)
        finally:
            done.set()
    worker = threading.Thread(target=encode)
    worker.start()
    entered.wait()
    ticks = 0
    while not done.wait(0.001):
        ticks += 1
    worker.join()
    assert not failures
    assert ticks >= 2, "Python threads could not progress during native compression"
