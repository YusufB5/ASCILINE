"""Main DCT is the reference: JS must reconstruct the encoder's exact lossy frame."""
import base64
import json
from pathlib import Path
import shutil
import struct
import subprocess
import zlib

import numpy as np
import pytest

from codec import ProfileEncoder

ROOT = Path(__file__).resolve().parent.parent


def profile_frames(width, height, count):
    # BGR primaries, secondaries, black/white and 2x2 chroma edges make swapped
    # channels or 4:2:0 indexing faults obvious; motion exercises predicted frames.
    colors = np.array([
        [0, 0, 255], [0, 255, 0], [255, 0, 0], [255, 0, 255],
        [0, 255, 255], [255, 255, 0], [0, 0, 0], [255, 255, 255],
    ], dtype=np.uint8)
    yy, xx = np.indices((height, width))
    chroma = colors[((yy // 2) + (xx // 2)) % len(colors)]
    texture = np.random.default_rng(721).integers(0, 256, (height, width, 3), dtype=np.uint8)
    for index in range(count):
        if index < len(colors):
            frame = np.broadcast_to(colors[index], (height, width, 3)).copy()
        elif index < 16:
            frame = chroma.copy()
        elif index < 32:
            frame = np.roll(texture, index - 16, axis=1)
        elif index < 48:
            frame = np.full((height, width, 3), 128, dtype=np.uint8)
        else:
            frame = np.roll(chroma, index - 48, axis=0)
        yield np.ascontiguousarray(frame)


@pytest.mark.parametrize("quality", [20, 70, 95])
@pytest.mark.parametrize("backend", ["python", "rust"])
def test_main_profile_matches_browser_after_keyframes_and_resets(tmp_path, quality, backend):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to execute the shipped JavaScript decoder")
    encoder_type = ProfileEncoder
    if backend == "rust":
        from asciline.engines import get_engine
        try:
            encoder_type = get_engine("rust").native.ProfileEncoder
        except RuntimeError as exc:
            pytest.skip(str(exc))
    sequences = []
    # Periodic keyframe at 48, seek to nonzero frame 300, then dimension change.
    for width, height, count, first_index in [(64, 48, 52, 0), (64, 48, 8, 300), (32, 64, 8, 90)]:
        encoder = encoder_type(width, height, quality)
        sequence = []
        for ordinal, frame in enumerate(profile_frames(width, height, count)):
            packet, shown = encoder.encode(frame)
            assert packet[4] == 4
            if ordinal % 48 == 0:
                assert zlib.decompress(packet[5:])[0] == 0
            # Live transport must carry its timeline index, independent of the
            # profile encoder's local frame counter (which restarts on seek).
            index = first_index + ordinal
            packet = struct.pack(">I", index) + packet[4:]
            sequence.append({
                "index": index,
                "packet": base64.b64encode(packet).decode("ascii"),
                "shown": base64.b64encode(shown).decode("ascii"),
            })
        sequences.append(sequence)
    fixture = tmp_path / "profile.json"
    fixture.write_text(json.dumps(sequences), encoding="utf-8")
    result = subprocess.run([node, str(ROOT / "test/check_profile_contract.cjs"), str(fixture)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["checked"] == 68


def test_native_profile_validation_and_reset():
    from asciline.engines import get_engine
    try:
        encoder_type = get_engine("rust").native.ProfileEncoder
    except RuntimeError as exc:
        pytest.skip(str(exc))
    for args in [(0, 16), (17, 16), (16, 16, 0), (16, 16, 101),
                 (16, 16, 70, float("nan")), (16, 16, 70, .75, -1),
                 (16, 16, 70, .75, 256, 10)]:
        with pytest.raises(ValueError):
            encoder_type(*args)
    encoder = encoder_type(16, 16)
    with pytest.raises(ValueError):
        encoder.encode(np.zeros((16, 32, 3), dtype=np.uint8))
    frame = np.full((16, 16, 3), 90, dtype=np.uint8)
    first = encoder.encode(frame)
    encoder.encode(frame)
    encoder.reset()
    assert encoder.encode(frame) == first
