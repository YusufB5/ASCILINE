"""Capture real DCT traffic plus seek/pause events without altering packet order."""
import asyncio
import json
import struct
import time

import pytest
import websockets

from test_native_stream import live_server
from experiments.rust_audit.playback_report import read_trace, summarize


def recorded_sessions(trace):
    try:
        records, _ = read_trace(trace)
    except ValueError:
        return set()
    return {r["session"] for r in records if r["type"] == "connect"}


def wait_for_session(trace, previous_sessions):
    # The module-scoped fixture may serve several tests/connections. Never use
    # another session's disconnect as evidence that our records were flushed.
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            records, _ = read_trace(trace)
        except ValueError:
            records = []
        current = [r for r in records if r.get("session") and r["session"] not in previous_sessions]
        if any(r["type"] == "disconnect" for r in current):
            return current
        time.sleep(.05)
    pytest.fail("Recorder did not flush this disconnected session")


@pytest.mark.parametrize("live_server", [["--pixel", "--cols", "160", "--rows", "90",
    "--pixel-codec", "dct", "--fps", "60", "--perf-record", "{trace_dir}",
    "--decode-ahead", str(ahead)] for ahead in (0, 3)], indirect=True)
def test_recorded_dct_seek_and_client_epochs(live_server):
    port, _, clip = live_server
    trace = next((clip.parent / "trace").glob("*.jsonl"))
    previous_sessions = recorded_sessions(trace)

    async def run():
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?sync=1&pixel_codec=dct-v1") as ws:
            assert (await ws.recv()).endswith(":dct")
            for i in range(4):
                packet = await ws.recv()
                assert struct.unpack_from(">I", packet)[0] == i
                assert packet[4] == 4
            await ws.send(json.dumps({"type": "playback-ready", "requestId": 0, "time": 0}))
            for _ in range(5):
                assert isinstance(await ws.recv(), bytes)
            await ws.send(json.dumps({"type": "pause", "paused": True}))
            await ws.send(json.dumps({"type": "seek", "time": 1, "requestId": 5}))
            while (packet := await ws.recv()) != "SEEKED:5:1.000000000":
                assert isinstance(packet, bytes)
            await ws.send(json.dumps({"type": "pause", "paused": False}))
            for i in range(60, 64):
                assert struct.unpack_from(">I", await ws.recv())[0] == i
            await ws.send(json.dumps({"type": "playback-ready", "requestId": 5, "time": 1}))
            for epoch in (0, 5):
                await ws.send(json.dumps({"type": "buffer", "requestId": epoch, "depth": 0,
                    "decoded": 4, "rendered": 1, "decodeMs": 3, "renderMs": .5,
                    "lateDrops": 0, "decodeErrors": 0, "clock": 1, "displayTime": 1}))
            for _ in range(5):
                assert isinstance(await ws.recv(), bytes)

    asyncio.run(run_with_timeout(run))
    records = wait_for_session(trace, previous_sessions)
    segments = summarize(records)
    assert len(segments) == 2
    assert all(s["preroll_frames"] == 4 for s in segments)
    assert all(s["frames"] >= 5 for s in segments)
    assert segments[1]["client_samples"] == 1
    assert segments[1]["client_decode_ms"] == 3
    assert any(e["type"] == "seeked" for e in segments[1]["events"])
    for r in records:
        if r["type"] == "frame":
            assert r["source_ms"] >= 0 and r["encode_ms"] >= 0
            assert r["produce_ms"] >= r["source_ms"] + r["encode_ms"]
            assert r["source_work_ms"] >= 0
            assert 0 <= r["source_queue_depth"] <= 2


async def run_with_timeout(run):
    await asyncio.wait_for(run(), 15)


@pytest.mark.parametrize("live_server", [["--pixel", "--cols", "160", "--rows", "90",
    "--pixel-codec", "dct", "--fps", "60", "--perf-record", "{trace_dir}",
    "--decode-ahead", "3"]], indirect=True)
def test_seek_burst_reaches_latest_target_and_ignores_old_ready(live_server):
    port, _, clip = live_server
    trace = next((clip.parent / "trace").glob("*.jsonl"))
    previous_sessions = recorded_sessions(trace)

    async def run():
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?sync=1&pixel_codec=dct-v1") as ws:
            assert (await ws.recv()).endswith(":dct")
            for _ in range(4):
                await ws.recv()
            # All are pending or arrive during an in-progress seek. Only the
            # newest pending target matters; no pause/reinit barriers here.
            for request in range(1, 21):
                await ws.send(json.dumps({"type": "seek", "time": 2 if request == 20 else .05 * request,
                                          "requestId": request}))
            while (packet := await ws.recv()) != "SEEKED:20:2.000000000":
                assert isinstance(packet, bytes) or packet.startswith("SEEKED:")
            for i in range(120, 124):
                assert struct.unpack_from(">I", await ws.recv())[0] == i
            await ws.send(json.dumps({"type": "playback-ready", "requestId": 1, "time": .05}))
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(ws.recv(), .1)
            await ws.send(json.dumps({"type": "playback-ready", "requestId": 20, "time": 2}))
            assert struct.unpack_from(">I", await ws.recv())[0] == 124

    asyncio.run(run_with_timeout(run))
    records = wait_for_session(trace, previous_sessions)
    assert any(r["type"] == "seek_coalesced" and r["removed"] > 0 for r in records)
    seeks = [r for r in records if r["type"] == "seek"]
    assert len(seeks) < 20 and seeks[-1]["target"] == 2
