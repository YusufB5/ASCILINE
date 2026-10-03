"""Measure delivery lag on the real server, with/without WebSocket deflate."""
import argparse
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request

import websockets


async def probe(port, compression, seconds, dct=False):
    query = "codec=adaptive" + ("&sync=1&pixel_codec=dct-v1" if dct else "")
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws?{query}",
                                  compression=compression, max_size=4 * 1024**2) as ws:
        print(f"requested={compression} negotiated={ws.protocol.extensions}", flush=True)
        init = await ws.recv()
        print(init, flush=True)
        fps = float(init.split(":")[1])
        if dct:
            assert init.endswith(":dct"), "DCT was not negotiated"
            for _ in range(4):
                assert isinstance(await ws.recv(), bytes)
            await ws.send(json.dumps({"type": "playback-ready", "requestId": 0, "time": 0}))
        start = time.perf_counter()
        count = 0
        late = skipped = 0
        last_index = 3 if dct else -1
        lags = []
        max_lag = -float("inf")
        report = start
        async for data in ws:
            if not isinstance(data, bytes):
                continue
            now = time.perf_counter()
            index = struct.unpack_from(">I", data)[0]
            elapsed = now - start
            lag = elapsed - index / fps
            max_lag = max(max_lag, lag)
            lags.append(lag)
            late += lag > .1
            skipped += max(0, index - last_index - 1)
            last_index = index
            count += 1
            if now - report >= 2:
                print(f"compression={compression} frames={count} elapsed={elapsed:.3f}s "
                      f"PTS={index/fps:.3f}s lag={lag*1000:.1f}ms max={max_lag*1000:.1f}ms", flush=True)
                report = now
            if elapsed >= seconds:
                break
        elapsed = time.perf_counter() - start
        p95 = sorted(lags)[min(len(lags) - 1, int(len(lags) * .95))] if lags else 0
        print(f"delivered={count} elapsed={elapsed:.3f}s delivery_fps={count/elapsed:.2f} "
              f"late_over_100ms={late} source_skips={skipped} "
              f"p95_lag={p95*1000:.1f}ms max_lag={max_lag*1000:.1f}ms\n"
              "Delivery only: excludes browser decode, drawing and actual audio.", flush=True)


@contextmanager
def own_server(args):
    """Own and clean up an isolated CLI process; never touch an existing server."""
    if not args.serve_video:
        yield args.port
        return
    root = Path(__file__).resolve().parents[2]
    folder = root / "experiments/rust_audit/output"
    folder.mkdir(exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    suffix = f"-{args.label}" if args.label else ""
    log_path = folder / f"delivery-{args.cols}-{args.fps:g}{suffix}.log"
    with log_path.open("w", encoding="utf-8") as log:
        launch = [sys.executable, "-u", "stream_server.py"]
        if args.native_binary:
            # Load an explicitly selected saved DLL in this owned process only.
            # argv remains structured; no shell quoting or loader configuration edits.
            launch = [sys.executable, "-u", "-c",
                "import sys,runpy; from pathlib import Path; "
                "from experiments.rust_audit.stage_probe import load_binary; "
                "load_binary(Path(sys.argv.pop(1))); sys.argv[0]='stream_server.py'; "
                "runpy.run_path('stream_server.py',run_name='__main__')",
                str(args.native_binary.resolve())]
        process = subprocess.Popen([*launch, args.serve_video,
            "--engine", "rust", "--pixel", "--pixel-codec", "dct" if args.dct else "raw",
            "--cols", str(args.cols), "--fps", str(args.fps), "--debug",
            "--decode-ahead", str(args.decode_ahead),
            "--no-thumbnails", "--port", str(port),
            *(["--perf-record", str(args.perf_record)] if args.perf_record else [])], cwd=root,
            stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 30
            while True:
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError(f"Server failed to start; see {log_path}")
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=.5):
                        break
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(.05)
            yield port
        finally:
            try:
                process.communicate(input=b"/quit\n", timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            print(f"Server log: {log_path}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--no-deflate", action="store_true")
    parser.add_argument("--dct", action="store_true", help="Negotiate DCT and synchronized startup")
    parser.add_argument("--serve-video", help="Start an isolated server for this local video")
    parser.add_argument("--cols", type=int, default=450)
    parser.add_argument("--fps", type=float, default=60)
    parser.add_argument("--decode-ahead", type=int, choices=[0, 2, 3], default=0)
    parser.add_argument("--native-binary", type=Path, help="Use a saved DLL in the isolated server for A/B measurements")
    parser.add_argument("--label", help="Alphanumeric label for separate A/B log files")
    parser.add_argument("--perf-record", type=Path, help="Record per-frame diagnostics from the owned server")
    args = parser.parse_args()
    if args.label and not args.label.replace("-", "").isalnum():
        parser.error("--label must contain only letters, digits and hyphens")
    if args.native_binary and not args.serve_video:
        parser.error("--native-binary requires --serve-video")
    if args.perf_record and not args.serve_video:
        parser.error("--perf-record requires --serve-video")
    with own_server(args) as port:
        asyncio.run(probe(port, None if args.no_deflate else "deflate", args.seconds, args.dct))
