"""Native DCT over real WebSockets: negotiation, predictor lifecycle and wire truth."""
import asyncio
import base64
import json
import shutil
import struct
import subprocess

import pytest
import websockets

from test_native_stream import live_server, ROOT
from asciline.engines import get_engine
from codec import ProfileEncoder

DCT_ARGS = ["--pixel", "--cols", "160", "--rows", "90", "--pixel-codec", "dct"]


@pytest.mark.parametrize("live_server", [[*DCT_ARGS, "--fps", str(fps), "--decode-ahead", str(ahead)]
                                       for fps in (30, 60) for ahead in (0, 3)], indirect=True)
def test_late_producer_catches_clock_without_breaking_dct(live_server, tmp_path):
    """A healthy decoder queue must not hide an overdue server timeline."""
    port, engine, clip = live_server
    sequence = []

    async def run():
        url = f"ws://127.0.0.1:{port}/ws?codec=adaptive&sync=1&pixel_codec=dct-v1"
        async with websockets.connect(url) as ws:
            parts = (await asyncio.wait_for(ws.recv(), 5)).split(":")
            fps = float(parts[1])
            encoder = engine.native.ProfileEncoder(160, 96, 70, .75, 256, 3)
            with engine.decoder(str(clip), 160, 96, skip_gray=True) as reference:
                async def check():
                    packet = await asyncio.wait_for(ws.recv(), 5)
                    index = struct.unpack_from(">I", packet)[0]
                    reference.seek(index / fps)
                    _, frame = next(reference)
                    expected, shown = encoder.encode(frame)
                    assert packet == struct.pack(">I", index) + expected[4:]
                    sequence.append({"index": index, "packet": base64.b64encode(packet).decode(),
                                     "shown": base64.b64encode(shown).decode()})
                    return index

                for index in range(4):
                    assert await check() == index
                # Simulate a producer that is 1.4 seconds behind running audio.
                # No browser backlog: the old policy would send frame 4 forever late.
                await ws.send(json.dumps({"type": "buffer", "depth": 0}))
                clock = 1.5
                started = asyncio.get_running_loop().time()
                await ws.send(json.dumps({"type": "playback-ready", "requestId": 0, "time": clock}))
                for _ in range(12):
                    index = await check()
                    elapsed = asyncio.get_running_loop().time() - started
                    assert clock + elapsed - index / fps < .1, "Late frames would all be discarded by the player"

    asyncio.run(run())
    fixture = tmp_path / "catchup-profile.json"
    fixture.write_text(json.dumps([sequence]), encoding="utf-8")
    result = subprocess.run([shutil.which("node"), str(ROOT / "test/check_profile_contract.cjs"), str(fixture)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("live_server", [DCT_ARGS, [*DCT_ARGS, "--decode-ahead", "3"],
    [*DCT_ARGS, "--decode-ahead", "3", "--decode-threads", "2"],
    [*DCT_ARGS, "--decode-ahead", "3", "--decode-threads", "0"]], indirect=True)
def test_live_profile_seek_modes_reconnect_and_browser_truth(live_server, tmp_path):
    port, engine, clip = live_server
    sequences = []

    async def init(ws):
        while True:
            packet = await asyncio.wait_for(ws.recv(), 5)
            if isinstance(packet, str) and packet.startswith("INIT:"):
                return packet.split(":")

    async def marker(ws, request_id):
        while True:
            packet = await asyncio.wait_for(ws.recv(), 5)
            if isinstance(packet, str) and packet.startswith(f"SEEKED:{request_id}:"):
                return float(packet.split(":")[2])

    async def run():
        url = f"ws://127.0.0.1:{port}/ws?codec=adaptive&sync=1&pixel_codec=dct-v1"
        with engine.decoder(str(clip), 160, 96, skip_gray=True) as reference:
            async def collect(ws, encoder, first_index, count, sequence):
                for ordinal in range(count):
                    packet = await asyncio.wait_for(ws.recv(), 5)
                    index = struct.unpack_from(">I", packet)[0]
                    if first_index is not None:
                        assert index == first_index + ordinal
                    reference.seek(index / 60)
                    _, bgr = next(reference)
                    expected, shown = encoder.encode(bgr)
                    assert packet == struct.pack(">I", index) + expected[4:]
                    sequence.append({"index": index, "packet": base64.b64encode(packet).decode(),
                                     "shown": base64.b64encode(shown).decode()})

            def fresh():
                sequence = []
                sequences.append(sequence)
                return engine.native.ProfileEncoder(160, 96, 70, 0.75, 256, 3), sequence

            async with websockets.connect(url) as ws:
                parts = await init(ws)
                assert parts[3:6] == ["160", "96", "1"] and parts[11] == "dct"
                encoder, sequence = fresh()
                await collect(ws, encoder, 0, 4, sequence)
                await ws.send(json.dumps({"type": "playback-ready", "requestId": 0, "time": 0}))
                # Cross the periodic keyframe boundary at encoded frame 48.
                await collect(ws, encoder, 4, 46, sequence)
                await ws.send(json.dumps({"type": "seek", "time": 1.5, "requestId": 10}))
                assert await marker(ws, 10) == 1.5
                encoder, sequence = fresh()
                await collect(ws, encoder, 90, 4, sequence)
                # Paused seek sends the marker but holds frames until resume.
                await ws.send(json.dumps({"type": "pause", "paused": True}))
                await ws.send(json.dumps({"type": "seek", "time": 0.5, "requestId": 11}))
                assert await marker(ws, 11) == 0.5
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(ws.recv(), 0.1)
                await ws.send(json.dumps({"type": "pause", "paused": False}))
                encoder, sequence = fresh()
                await collect(ws, encoder, 30, 4, sequence)
                await ws.send(json.dumps({"type": "reinit", "pixel": False, "time": 1}))
                parts = await init(ws)
                assert parts[1] == "30.0" and parts[5] == "0" and parts[11] == "raw"
                for _ in range(4):
                    assert (await asyncio.wait_for(ws.recv(), 5))[4] in (0, 1, 2, 3)
                await ws.send(json.dumps({"type": "reinit", "pixel": True, "time": 2}))
                parts = await init(ws)
                assert parts[1] == "60.0" and parts[11] == "dct"
                encoder, sequence = fresh()
                await collect(ws, encoder, 120, 4, sequence)

            async with websockets.connect(url) as ws:
                await init(ws)
                encoder, sequence = fresh()
                await collect(ws, encoder, 0, 4, sequence)
                # Shedding source frames must preserve the last sent predictor.
                await ws.send(json.dumps({"type": "buffer", "depth": 100}))
                await ws.send(json.dumps({"type": "buffer", "depth": 100}))
                await ws.send(json.dumps({"type": "playback-ready", "requestId": 0, "time": 0}))
                await collect(ws, encoder, None, 1, sequence)
                assert sequence[-1]["index"] > 4

    asyncio.run(run())
    fixture = tmp_path / "live-profile.json"
    fixture.write_text(json.dumps(sequences), encoding="utf-8")
    node = shutil.which("node")
    assert node, "Node is required to validate the shipped browser codec"
    result = subprocess.run([node, str(ROOT / "test/check_profile_contract.cjs"), str(fixture)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("live_server", [DCT_ARGS], indirect=True)
@pytest.mark.parametrize("query", ["", "?codec=adaptive&sync=1", "?pixel_codec=dct-v1",
                                   "?codec=raw&sync=1&pixel_codec=dct-v1"])
def test_unnegotiated_clients_receive_raw_pixels(live_server, query):
    port, _, _ = live_server

    async def run():
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws{query}") as ws:
            init = await asyncio.wait_for(ws.recv(), 5)
            assert init.split(":")[3:5] == ["160", "90"]
            assert not init.endswith(":dct")
            assert len(await asyncio.wait_for(ws.recv(), 5)) == 4 + 160 * 90 * 3

    asyncio.run(run())


@pytest.mark.parametrize("live_server", [[*DCT_ARGS, "--engine", "python"]], indirect=True)
def test_python_profile_uses_same_protocol_without_double_compression(live_server):
    port, _, clip = live_server
    with get_engine("python").decoder(str(clip), 160, 96, skip_gray=True) as reference:
        _, frame = next(reference)
    expected, _ = ProfileEncoder(160, 96, 70, .75, 256, 3).encode(frame)

    async def run():
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?sync=1&pixel_codec=dct-v1") as ws:
            assert not ws.protocol.extensions
            init = await asyncio.wait_for(ws.recv(), 5)
            assert init.startswith("INIT:30.0:") and init.endswith(":dct")
            assert await asyncio.wait_for(ws.recv(), 5) == expected

    asyncio.run(run())
