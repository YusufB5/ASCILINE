"""Server-side GPU probe (Windows): does the ASCILINE server touch the GPU?

Starts stream_server.py on a video, pulls the websocket stream with a headless
client (no browser, so no browser GPU), and samples the Windows
"GPU Engine" performance counters for the server process tree.

Usage:
    python experiments/rust_audit/gpu_probe.py videos/costa_rica_60fps.mp4 --cols 200
    python experiments/rust_audit/gpu_probe.py video.mp4 --extra --pixel

Note: Get-Counter sampling competes with the server for CPU (measured: 20 -> 6
msg/s on one machine), so the message count here is NOT a frame-rate result.
Scope: server process only, one machine, one clip. Browser rendering is NOT
measured here. A control check confirms the counter reports other processes.
"""
import argparse, asyncio, json, platform, subprocess, sys, time
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[2]

PS = r"""
$srv = %d
$kids = @($srv) + (Get-CimInstance Win32_Process -Filter "ParentProcessId=$srv" | %% ProcessId)
$out = @()
1..%d | %% {
  $c = (Get-Counter '\GPU Engine(*)\Utilization Percentage' -ErrorAction SilentlyContinue).CounterSamples
  $pid_of = { [int]($args[0] -replace '^pid_(\d+)_.*','$1') }
  $mine = $c | ? { $kids -contains (& $pid_of $_.InstanceName) }
  $out += [pscustomobject]@{
    server_gpu = [double](($mine | measure CookedValue -Sum).Sum)
    server_engine_instances = @($mine).Count
    system_gpu = [double](($c | measure CookedValue -Sum).Sum)
    pids_with_gpu_engine = @($c | %% { & $pid_of $_.InstanceName } | sort -Unique).Count
  }
  Start-Sleep -Milliseconds %d
}
$out | ConvertTo-Json -Compress
"""


async def pull(port, seconds):
    n = 0
    async with websockets.connect(f"ws://localhost:{port}/ws", max_size=None) as ws:
        t = time.time()
        while time.time() - t < seconds:
            await asyncio.wait_for(ws.recv(), 15)
            n += 1
    return n, time.time() - t


async def run(args):
    cmd = [sys.executable, str(ROOT / "stream_server.py"), args.video, "--cols", str(args.cols),
           "--port", str(args.port), "--no-thumbnails", *args.extra]
    server = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              stdin=subprocess.DEVNULL)
    try:
        for _ in range(60):
            await asyncio.sleep(0.5)
            try:
                await asyncio.wait_for(pull(args.port, 0.2), 5)
                break
            except Exception:
                continue
        puller = asyncio.create_task(pull(args.port, args.seconds))
        await asyncio.sleep(2)
        samples_n = max(1, (args.seconds - 4) // 2)
        ps = subprocess.run(["powershell", "-NoProfile", "-Command", PS % (server.pid, samples_n, 2000)],
                            capture_output=True, text=True)
        samples = json.loads(ps.stdout or "[]")
        if isinstance(samples, dict):
            samples = [samples]
        msgs, secs = await puller
    finally:
        server.terminate()
    result = {
        "platform": platform.platform(), "cmd": " ".join(cmd[1:]), "seconds": round(secs, 1),
        "messages": msgs, "samples": len(samples),
        "server_gpu_max_pct": max((s["server_gpu"] for s in samples), default=None),
        "server_gpu_engine_instances_max": max((s["server_engine_instances"] for s in samples), default=None),
        "system_gpu_max_pct": max((s["system_gpu"] for s in samples), default=None),
        "control_pids_with_gpu_engine": max((s["pids_with_gpu_engine"] for s in samples), default=None),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    if platform.system() != "Windows":
        sys.exit("gpu_probe.py uses Windows GPU Engine counters")
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--cols", type=int, default=200)
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--seconds", type=int, default=30)
    ap.add_argument("--extra", nargs=argparse.REMAINDER, default=[], help="extra stream_server.py flags")
    asyncio.run(run(ap.parse_args()))
