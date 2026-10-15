"""Decode real Python and Rust DCT sequences through the SDK's embedded codec."""
import base64
import json
from pathlib import Path
import subprocess

import numpy as np
import pytest
from asciline.engines import get_engine
from codec import ProfileEncoder
from test_native_stream import live_server

@pytest.mark.parametrize('selection', ['python', 'rust'])
def test_sdk_profile_packets(selection, tmp_path):
    try:
        engine = get_engine(selection)
    except RuntimeError as exc:
        if selection == 'rust':
            pytest.skip(str(exc))
        raise
    encoder_type = engine.native.ProfileEncoder if engine.native else ProfileEncoder
    cases = []
    rng = np.random.default_rng(71)
    for cols, rows in [(32, 16), (16, 32)]:
        encoder = encoder_type(cols, rows, 70, .75, 256, 3)
        frame = rng.integers(0, 256, (rows, cols, 3), dtype=np.uint8)
        frames = []
        for i in range(12):
            if i % 3:
                frame = np.roll(frame, 1, axis=1)
            packet, shown = encoder.encode(frame)
            frames.append(dict(packet=base64.b64encode(packet).decode(),
                               expected=base64.b64encode(shown).decode()))
        cases.append(dict(frames=frames))
    fixture = tmp_path / 'sdk-profile.json'
    fixture.write_text(json.dumps(cases))
    subprocess.run(['node', 'test/test_sdk_live.js', str(fixture)], check=True,
                   cwd=Path(__file__).resolve().parents[1], timeout=20)


@pytest.mark.parametrize('live_server', [
    ['--pixel', '--cols', '160', '--rows', '90', '--pixel-codec', 'dct',
     '--decode-ahead', '3', '--decode-threads', '2'],
    ['--pixel', '--cols', '160', '--rows', '90', '--pixel-codec', 'dct', '--engine', 'python'],
], indirect=True)
def test_sdk_real_websocket(live_server):
    port, _, _ = live_server
    subprocess.run(['node', 'test/test_sdk_live.js', f'ws://127.0.0.1:{port}/ws'],
                   check=True, cwd=Path(__file__).resolve().parents[1], timeout=30)
