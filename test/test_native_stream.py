"""Exercise the real CLI server with Rust, using a synthetic 60 FPS clip."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest
import websockets
import numpy as np

from asciline.engines import get_engine
from ascii_video_player2 import AsciiMapper

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def live_server(tmp_path_factory, request):
    render_args = getattr(request, "param", ["--pixel", "--cols", "160", "--rows", "90"])
    selection = "rust"
    if "--engine" in render_args:
        selection = render_args[render_args.index("--engine") + 1]
    try:
        engine = get_engine(selection)
    except RuntimeError as exc:
        pytest.skip(str(exc))
    config = ROOT / "rust_core/runtime.json"
    ffmpeg_dir = os.environ.get("FFMPEG_DIR")
    if not ffmpeg_dir and config.exists():
        ffmpeg_dir = json.loads(config.read_text(encoding="utf-8")).get("ffmpeg_dir")
    ffmpeg = str(Path(ffmpeg_dir) / "bin/ffmpeg.exe") if os.name == "nt" and ffmpeg_dir else shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg unavailable")
    folder = tmp_path_factory.mktemp("native_server")
    clip = folder / "clip.mp4"
    subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=320x180:rate=60:duration=3", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=44100:duration=3", "-c:v", "mpeg4",
                    "-q:v", "3", "-g", "120", "-bf", "2", "-c:a", "aac", str(clip)], check=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    log = (folder / "server.log").open("w", encoding="utf-8")
    render_args = [str(folder / "trace") if arg == "{trace_dir}" else arg for arg in render_args]
    process = subprocess.Popen([sys.executable, "-u", "stream_server.py", str(clip),
        "--engine", "rust", *render_args,
        "--vol", "1", "--no-thumbnails", "--port", str(port)], cwd=ROOT,
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                log.flush()
                pytest.fail((folder / "server.log").read_text(encoding="utf-8"))
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as response:
                    if response.status == 200:
                        break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.05)
        else:
            pytest.fail("Server startup timed out")
        yield port, engine, clip
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()


def test_native_audio_endpoint_delivers_mp3(live_server):
    port, _, _ = live_server
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/audio?v=0", timeout=5) as response:
        assert response.status == 200
        assert response.headers.get_content_type() == "audio/mpeg"
        # The former PATH executable returned HTTP 200 with an empty body.
        assert len(response.read(4096)) == 4096


@pytest.mark.parametrize("live_server,pixel_grid,ascii_grid", [
    (["--pixel", "--cols", "750"], (471, 265), (207, 58)),
    (["--pixel", "--cols", "750", "--no-resolution-limit"], (750, 422), (750, 211)),
    (["--pixel", "--cols", "750", "--no-limit"], (750, 422), (750, 211)),
], indirect=["live_server"])
def test_resolution_caps_and_override_survive_mode_switch(live_server, pixel_grid, ascii_grid):
    port, _, _ = live_server

    async def run():
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?sync=1", max_size=2**22) as ws:
            init = await asyncio.wait_for(ws.recv(), 5)
            assert tuple(map(int, init.split(":")[3:5])) == pixel_grid
            assert float(init.split(":")[1]) == 60
            first = await asyncio.wait_for(ws.recv(), 5)
            assert len(first) == 4 + pixel_grid[0] * pixel_grid[1] * 3
            await ws.send(json.dumps({"type": "reinit", "pixel": False, "time": 0}))
            while True:
                message = await asyncio.wait_for(ws.recv(), 5)
                if isinstance(message, str) and message.startswith("INIT:"):
                    break
            assert message.split(":")[5] == "0"
            assert float(message.split(":")[1]) == 30
            assert tuple(map(int, message.split(":")[3:5])) == ascii_grid
            assert isinstance(await asyncio.wait_for(ws.recv(), 5), bytes)

    asyncio.run(run())


def test_pixel_stream_seek_pause_and_reinit(live_server):
    port, engine, clip = live_server
    with engine.decoder(str(clip), 160, 90, skip_gray=True) as reference:
        reference.seek(1.23)
        _, expected = next(reference)

    async def run():
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws") as ws:
            # A browser offers permessage-deflate by default. Rust must reject
            # that extra compression layer even when the client offers it.
            assert not ws.protocol.extensions
            init = await asyncio.wait_for(ws.recv(), 3)
            parts = init.split(":")
            assert parts[0] == "INIT" and float(parts[1]) == 60
            assert parts[8] == "0"
            first = await asyncio.wait_for(ws.recv(), 3)
            assert struct.unpack_from(">I", first)[0] == 0
            assert len(first) == 4 + 160 * 90 * 3
            await ws.send(json.dumps({"type": "pause", "paused": True}))
            await asyncio.sleep(0.1)
            while True:
                try:
                    await asyncio.wait_for(ws.recv(), 0.04)
                except asyncio.TimeoutError:
                    break
            await ws.send(json.dumps({"type": "seek", "time": 1.23}))
            await ws.send(json.dumps({"type": "pause", "paused": False}))
            sought = await asyncio.wait_for(ws.recv(), 3)
            assert struct.unpack_from(">I", sought)[0] == 74
            assert sought[4:] == expected.tobytes()
            await ws.send(json.dumps({"type": "reinit", "pixel": False, "time": 0.35}))
            while True:
                message = await asyncio.wait_for(ws.recv(), 3)
                if isinstance(message, str) and message.startswith("INIT:"):
                    break
            assert message.split(":")[5] == "0"
            assert float(message.split(":")[1]) == 30
            colored = await asyncio.wait_for(ws.recv(), 3)
            assert struct.unpack_from(">I", colored)[0] == round(0.35 * 30)
            assert colored[4] in (0, 1, 2, 3)
            # Disconnect while paused must wake the server receive loop and close
            # the decoder even though no further send would detect the disconnect.
            await ws.send(json.dumps({"type": "pause", "paused": True}))
    asyncio.run(run())


def test_60fps_complete_stream(live_server):
    port, _, _ = live_server
    async def run():
        indices = []
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws") as ws:
            assert (await ws.recv()).startswith("INIT:60.0:")
            start = time.monotonic()
            try:
                while True:
                    message = await asyncio.wait_for(ws.recv(), 5)
                    if isinstance(message, bytes):
                        indices.append(struct.unpack_from(">I", message)[0])
            except websockets.exceptions.ConnectionClosedOK:
                elapsed = time.monotonic() - start
        assert indices == list(range(180))
        assert 2.5 <= elapsed <= 5.0
    asyncio.run(run())


@pytest.mark.parametrize("live_server,expected_fps", [
    (["--mode", "6", "--cols", "160", "--rows", "90", "--no-limit"], 30),
    (["--mode", "6", "--cols", "160", "--rows", "90", "--fps", "60"], 60),
    (["--mode", "6", "--cols", "160", "--rows", "90", "--fps", "15"], 15),
], indirect=["live_server"])
def test_ascii_default_and_explicit_fps_keep_duration(live_server, expected_fps):
    port, engine, clip = live_server
    lut = np.array([ord(c) for c in AsciiMapper()._lut], dtype=np.uint8)
    with engine.decoder(str(clip), 160, 90) as reference:
        gray, bgr = next(reference)
        first_frame = engine.native.build_frame_buf(gray, bgr.reshape(-1), lut, 0).tobytes()

    async def run():
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?codec=raw") as ws:
            init = await asyncio.wait_for(ws.recv(), 5)
            assert float(init.split(":")[1]) == expected_fps
            assert init.split(":")[5] == "0"
            started = time.monotonic()
            indices = []
            try:
                while True:
                    message = await asyncio.wait_for(ws.recv(), 5)
                    if not indices:
                        assert message[4:] == first_frame
                    indices.append(struct.unpack_from(">I", message)[0])
            except websockets.exceptions.ConnectionClosedOK:
                assert indices == list(range(3 * expected_fps))
                assert 2.5 <= time.monotonic() - started <= 7

    asyncio.run(run())


@pytest.mark.parametrize("live_server,ascii_fps", [
    (["--pixel", "--cols", "160", "--rows", "90"], 30),
    (["--pixel", "--cols", "160", "--rows", "90", "--fps", "60"], 60),
], indirect=["live_server"])
def test_rate_changes_keep_seek_and_paused_mode_switch_working(live_server, ascii_fps):
    port, engine, clip = live_server
    with engine.decoder(str(clip), 160, 90, skip_gray=True) as reference:
        reference.seek(0.5)
        _, expected_pixel = next(reference)

    async def next_init(ws):
        while True:
            message = await asyncio.wait_for(ws.recv(), 5)
            if isinstance(message, str) and message.startswith("INIT:"):
                return message.split(":")

    async def frames(ws, start):
        packets = [await asyncio.wait_for(ws.recv(), 5) for _ in range(4)]
        assert [struct.unpack_from(">I", p)[0] for p in packets] == list(range(start, start + 4))
        return packets

    async def run():
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?sync=1&codec=raw") as ws:
            assert float((await next_init(ws))[1]) == 60
            await frames(ws, 0)
            await ws.send(json.dumps({"type": "reinit", "pixel": False, "time": 1}))
            init = await next_init(ws)
            assert float(init[1]) == ascii_fps and float(init[8]) == 1
            await frames(ws, ascii_fps)
            await ws.send(json.dumps({"type": "seek", "time": 1.5, "requestId": 10}))
            marker = await asyncio.wait_for(ws.recv(), 5)
            assert marker == "SEEKED:10:1.500000000"
            await frames(ws, int(1.5 * ascii_fps))
            await ws.send(json.dumps({"type": "pause", "paused": True}))
            await ws.send(json.dumps({"type": "reinit", "pixel": True, "time": 0.5}))
            init = await next_init(ws)
            assert float(init[1]) == 60 and float(init[8]) == 0.5
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(ws.recv(), 0.1)
            await ws.send(json.dumps({"type": "pause", "paused": False}))
            packets = await frames(ws, 30)
            assert packets[0][4:] == expected_pixel.tobytes()
            await ws.send(json.dumps({"type": "playback-ready", "requestId": int(init[10]), "time": 0.5}))
            assert struct.unpack_from(">I", await asyncio.wait_for(ws.recv(), 5))[0] == 34

    asyncio.run(run())


def test_synced_startup_pause_and_repeated_seeks(live_server):
    port, _, _ = live_server

    async def receive_frame(ws):
        message = await asyncio.wait_for(ws.recv(), 3)
        assert isinstance(message, bytes)
        return struct.unpack_from(">I", message)[0]

    async def marker(ws, request_id):
        while True:
            message = await asyncio.wait_for(ws.recv(), 3)
            if isinstance(message, str) and message.startswith(f"SEEKED:{request_id}:"):
                return float(message.split(":")[2])

    async def ready(ws, request_id, clock):
        await ws.send(json.dumps({"type": "playback-ready", "requestId": request_id, "time": clock}))

    async def run():
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?sync=1") as ws:
            init = await ws.recv()
            assert init.split(":")[10] == "0"
            assert [await receive_frame(ws) for _ in range(4)] == [0, 1, 2, 3]
            # Slow audio startup cannot advance the source and discard frame 0.
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(ws.recv(), 0.7)
            await ready(ws, 0, 0)
            assert await receive_frame(ws) == 4
            await ws.send(json.dumps({"type": "seek", "time": 1.5, "requestId": 1}))
            assert await marker(ws, 1) == pytest.approx(1.5)
            assert [await receive_frame(ws) for _ in range(4)] == [90, 91, 92, 93]
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(ws.recv(), 0.4)
            await ready(ws, 1, 1.5)
            assert await receive_frame(ws) == 94
            await ws.send(json.dumps({"type": "pause", "paused": True}))
            await ws.send(json.dumps({"type": "seek", "time": 0.35, "requestId": 2}))
            assert await marker(ws, 2) == pytest.approx(0.35)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(ws.recv(), 0.1)
            await ws.send(json.dumps({"type": "pause", "paused": False}))
            assert [await receive_frame(ws) for _ in range(4)] == [21, 22, 23, 24]
            await ready(ws, 2, 0.35)
            assert await receive_frame(ws) == 25
            # Two quick jumps: an old audio-ready message must not release the
            # newest timeline, and the final marker precedes its first keyframe.
            await ws.send(json.dumps({"type": "seek", "time": 0.2, "requestId": 3}))
            await ws.send(json.dumps({"type": "seek", "time": 2.0, "requestId": 4}))
            assert await marker(ws, 4) == pytest.approx(2.0)
            assert [await receive_frame(ws) for _ in range(4)] == [120, 121, 122, 123]
            await ready(ws, 3, 0.2)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(ws.recv(), 0.1)
            await ready(ws, 4, 2.0)
            assert await receive_frame(ws) == 124
            await ws.send(json.dumps({"type": "pause", "paused": True}))

    asyncio.run(run())
