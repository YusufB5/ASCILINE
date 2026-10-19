"""Server-side GPU probe (Windows): does the ASCILINE server touch the GPU?

Starts stream_server.py on a video, pulls the websocket stream with a headless
client (no browser, so no browser GPU), and samples the Windows
"GPU Engine" performance counters for the server process tree (all descendants).

Usage:
    python experiments/rust_audit/gpu_probe.py videos/costa_rica_60fps.mp4 --cols 200
    python experiments/rust_audit/gpu_probe.py video.mp4 --extra --pixel

Run once normally (GPU result) and once with --no-sampler (production rate).
Get-Counter sampling competes with the server for CPU and collapses the stream
(measured), so server_production from a sampler run is NOT a performance result.
Frames are unique '<index>\n<frame>' text messages; duplicates and control
messages are reported separately.
Scope: server process only, one machine, one clip. Browser rendering is NOT
measured here. A control check confirms the counter reports other processes.
"""
import argparse, asyncio, json, platform, subprocess, sys, time
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[2]

PS = r"""
$srv = %d
$all = Get-CimInstance Win32_Process | select ProcessId,ParentProcessId
$kids = @($srv); $queue = @($srv)
while ($queue.Count) { $cur = $queue[0]; $queue = $queue[1..($queue.Count)]
  $c2 = @($all | ? { $_.ParentProcessId -eq $cur } | %% ProcessId)
  $kids += $c2; $queue += $c2 }
$out = @()
1..%d | %% {
  $c = (Get-Counter '\GPU Engine(*)\Utilization Percentage' -ErrorAction SilentlyContinue).CounterSamples
  $pid_of = { [int]($args[0] -replace '^pid_(\d+)_.*','$1') }
  $mine = $c | ? { $kids -contains (& $pid_of $_.InstanceName) }
  $out += [pscustomobject]@{
    server_processes = @($kids).Count
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
    """Pull the stream and classify messages. Only text messages of the form
    '<index>, newline, <frame>' count as frames; repeated indices are duplicates and
    everything else (INIT/STATUS/SEEKED/errors) is protocol control. Binary
    messages (colour/pixel modes) are counted separately, unclassified."""
    seen, dup, control, binary, stamps = set(), 0, 0, 0, []
    async with websockets.connect(f"ws://localhost:{port}/ws", max_size=None) as ws:
        t = time.time()
        while time.time() - t < seconds:
            m = await asyncio.wait_for(ws.recv(), 15)
            if isinstance(m, bytes):
                binary += 1
                continue
            head, sep, _ = m.partition(chr(10))
            if sep and head.isdigit():
                if head in seen:
                    dup += 1
                else:
                    seen.add(head)
                    stamps.append(time.time() - t)
            else:
                control += 1
    return {"unique_frames": len(seen), "duplicates": dup, "control": control,
            "binary_unclassified": binary, "stamps": stamps, "seconds": time.time() - t}


def rate_stats(stamps, seconds):
    """Unique frames per 1 s bin, ignoring the first 2 s (startup)."""
    bins = [0] * int(seconds)
    for x in stamps:
        if int(x) < len(bins):
            bins[int(x)] += 1
    bins = bins[2:-1] or bins
    mean = sum(bins) / len(bins)
    sd = (sum((v - mean) ** 2 for v in bins) / len(bins)) ** 0.5
    return {"fps_mean": round(mean, 1), "fps_min": min(bins), "fps_stdev": round(sd, 2), "bins": len(bins)}


def tree_cpu_seconds(pid):
    """Total CPU seconds of a process and all its descendants (one cheap call)."""
    ps = (f"$all=Get-CimInstance Win32_Process|select ProcessId,ParentProcessId;$k=@({pid});$q=@({pid});"
          "while($q.Count){$c=$q[0];$q=$q[1..($q.Count)];$n=@($all|?{$_.ParentProcessId -eq $c}|%{$_.ProcessId});$k+=$n;$q+=$n};"
          "($k|%{(Get-Process -Id $_ -ErrorAction SilentlyContinue).CPU}|measure -Sum).Sum")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout.strip()
    return float(out.replace(",", ".")) if out else 0.0


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
        cpu0 = tree_cpu_seconds(server.pid)
        puller = asyncio.create_task(pull(args.port, args.seconds))
        await asyncio.sleep(2)
        samples = []
        if not args.no_sampler:
            samples_n = max(1, (args.seconds - 4) // 2)
            ps = subprocess.run(["powershell", "-NoProfile", "-Command", PS % (server.pid, samples_n, 2000)],
                                capture_output=True, text=True)
            samples = json.loads(ps.stdout or "[]")
            if isinstance(samples, dict):
                samples = [samples]
        pulled = await puller
        cpu1 = tree_cpu_seconds(server.pid)
    finally:
        server.terminate()
    secs = pulled["seconds"]
    result = {
        "platform": platform.platform(), "cmd": " ".join(cmd[1:]), "seconds": round(secs, 1),
        "sampler": not args.no_sampler,
        "frames": {k: pulled[k] for k in ("unique_frames", "duplicates", "control", "binary_unclassified")},
        "server_production": rate_stats(pulled["stamps"], secs),
        "server_cpu_cores_avg": round((cpu1 - cpu0) / secs, 2),
        "samples": len(samples),
        "server_processes_max": max((s["server_processes"] for s in samples), default=None),
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
    ap.add_argument("--no-sampler", action="store_true",
                    help="no GPU counters: only report the unperturbed stream rate (control run)")
    ap.add_argument("--extra", nargs=argparse.REMAINDER, default=[], help="extra stream_server.py flags")
    asyncio.run(run(ap.parse_args()))
