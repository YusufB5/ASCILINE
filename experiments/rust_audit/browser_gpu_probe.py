"""Browser-side probe: does the ASCII player keep its FPS with Chrome's GPU off?

Starts stream_server.py, launches Chrome (headed) with or without --disable-gpu,
calls the player's startStream() over the DevTools protocol and records the
player's own "FPS: x/y" status line for N seconds. Also records Chrome's
reported GPU feature status (SystemInfo.getInfo) so the run proves which
mode was really active.

    python experiments/rust_audit/browser_gpu_probe.py videos/x.mp4 --cols 200 --gpu off
    python experiments/rust_audit/browser_gpu_probe.py videos/x.mp4 --cols 200 --gpu on
"""
import argparse, asyncio, json, platform, re, statistics, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
import websockets

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gpu_probe import PS, tree_cpu_seconds  # same folder
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def http_json(url):
    return json.loads(urllib.request.urlopen(url, timeout=3).read())


async def cdp(ws, state, method, **params):
    state["id"] += 1
    await ws.send(json.dumps({"id": state["id"], "method": method, "params": params}))
    while True:
        m = json.loads(await ws.recv())
        if m.get("id") == state["id"]:
            return m.get("result", m)


async def run_once(a, server_port):
    """One Chrome session. CPU is the Chrome process tree's CPU seconds over the
    playback window (two cheap reads, no sampler). With --chrome-gpu-engine the
    Windows GPU Engine counters are sampled for the Chrome tree instead; that
    perturbs playback, so FPS from such a run is not a performance result."""
    prof = tempfile.mkdtemp()
    flags = [CHROME, f"--user-data-dir={prof}", f"--remote-debugging-port={a.dport}", "--no-first-run",
             "--autoplay-policy=no-user-gesture-required", "--new-window", f"http://localhost:{server_port}/"]
    if a.gpu == "off":
        flags.insert(1, "--disable-gpu")
    chrome = subprocess.Popen(flags)
    try:
        tab = None
        for _ in range(40):
            time.sleep(0.5)
            try:
                tab = next(t for t in http_json(f"http://localhost:{a.dport}/json") if t["type"] == "page" and f":{server_port}" in t["url"])
                break
            except Exception: pass
        s = {"id": 0}
        async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=None) as ws:
            ver = http_json(f"http://localhost:{a.dport}/json/version")
            async with websockets.connect(ver["webSocketDebuggerUrl"], max_size=None) as b:
                info = await cdp(b, {"id": 0}, "SystemInfo.getInfo")
            g = info.get("gpu", {})
            feats = g.get("featureStatus", {})
            feats["gpu_device"] = (g.get("devices") or [{}])[0].get("deviceString")
            await asyncio.sleep(2)
            await cdp(ws, s, "Runtime.evaluate", expression="startStream()")
            await asyncio.sleep(3)  # let playback settle
            cpu0 = tree_cpu_seconds(chrome.pid)
            t0 = time.time()
            gpu_task = None
            if a.chrome_gpu_engine:
                n = max(1, a.seconds // 2)
                gpu_task = asyncio.create_task(asyncio.to_thread(
                    subprocess.run, ["powershell", "-NoProfile", "-Command", PS % (chrome.pid, n, 2000)],
                    capture_output=True, text=True))
            fps = []
            while time.time() - t0 < a.seconds:
                await asyncio.sleep(1)
                r = await cdp(ws, s, "Runtime.evaluate", expression="document.getElementById('status').textContent")
                mm = re.match(r"FPS: (\d+)/(\d+)", r["result"].get("value", ""))
                if mm: fps.append((int(mm[1]), int(mm[2])))
            secs = time.time() - t0
            cpu1 = tree_cpu_seconds(chrome.pid)
            gpu = None
            if gpu_task:
                out = (await gpu_task).stdout or "[]"
                rows = json.loads(out); rows = [rows] if isinstance(rows, dict) else rows
                gpu = {"chrome_gpu_max_pct": max((x["server_gpu"] for x in rows), default=None),
                       "chrome_gpu_mean_pct": round(statistics.mean(x["server_gpu"] for x in rows), 2) if rows else None,
                       "chrome_processes": max((x["server_processes"] for x in rows), default=None)}
        vals = [f for f, _ in fps]
        return {"chrome_version": ver.get("Browser"), "chrome_feature_status": feats,
                "target_fps": fps[-1][1] if fps else None, "fps_samples": vals,
                "chrome_cpu_cores_avg": round((cpu1 - cpu0) / secs, 2), "chrome_gpu_engine": gpu}
    finally:
        chrome.terminate()
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(chrome.pid)], capture_output=True)


async def main(a):
    srv = subprocess.Popen([sys.executable, str(ROOT / "stream_server.py"), a.video, "--cols", str(a.cols),
                            "--port", str(a.port), "--no-thumbnails", *a.extra], cwd=ROOT,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    runs = []
    try:
        for _ in range(60):
            time.sleep(0.5)
            try: http_json(f"http://localhost:{a.port}/health"); break
            except Exception: pass
        for _ in range(a.repeat):
            runs.append(await run_once(a, a.port))
    finally:
        srv.terminate()
    means = [statistics.mean(r["fps_samples"]) for r in runs if r["fps_samples"]]
    allv = [v for r in runs for v in r["fps_samples"]]
    cpus = [r["chrome_cpu_cores_avg"] for r in runs]
    print(json.dumps({
        "platform": platform.platform(), "chrome_gpu": a.gpu, "cols": a.cols, "seconds_per_run": a.seconds,
        "runs": len(runs), "fps_source": "player status line (browser render rate, not server production rate)",
        "chrome_version": runs[0]["chrome_version"], "chrome_feature_status": runs[0]["chrome_feature_status"],
        "target_fps": runs[0]["target_fps"],
        "fps_run_means": [round(m, 1) for m in means], "fps_mean": round(statistics.mean(allv), 1) if allv else None,
        "fps_min": min(allv) if allv else None, "fps_stdev": round(statistics.pstdev(allv), 2) if allv else None,
        "chrome_cpu_cores_avg_per_run": cpus, "chrome_gpu_engine": [r["chrome_gpu_engine"] for r in runs],
        "gpu_sampler_active": a.chrome_gpu_engine,
    }, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("video"); ap.add_argument("--cols", type=int, default=200)
    ap.add_argument("--port", type=int, default=8767); ap.add_argument("--seconds", type=int, default=25)
    ap.add_argument("--debug-port", dest="dport", type=int, default=9333)
    ap.add_argument("--gpu", choices=("on", "off"), default="off")
    ap.add_argument("--repeat", type=int, default=1, help="number of fresh Chrome sessions")
    ap.add_argument("--chrome-gpu-engine", action="store_true",
                    help="sample GPU Engine counters for the Chrome tree (perturbs playback; run separately)")
    ap.add_argument("--extra", nargs=argparse.REMAINDER, default=[])
    asyncio.run(main(ap.parse_args()))
