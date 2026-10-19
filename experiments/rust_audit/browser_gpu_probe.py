"""Browser-side probe: does the ASCII player keep its FPS with Chrome's GPU off?

Starts stream_server.py, launches Chrome (headed) with or without --disable-gpu,
calls the player's startStream() over the DevTools protocol and records the
player's own "FPS: x/y" status line for N seconds. Also records Chrome's
reported GPU feature status (SystemInfo.getInfo) so the run proves which
mode was really active.

    python experiments/rust_audit/browser_gpu_probe.py videos/x.mp4 --cols 200 --gpu off
    python experiments/rust_audit/browser_gpu_probe.py videos/x.mp4 --cols 200 --gpu on
"""
import argparse, asyncio, json, re, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
import websockets

ROOT = Path(__file__).resolve().parents[2]
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


async def main(a):
    srv = subprocess.Popen([sys.executable, str(ROOT / "stream_server.py"), a.video, "--cols", str(a.cols),
                            "--port", str(a.port), "--no-thumbnails", *a.extra], cwd=ROOT,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    prof = tempfile.mkdtemp()
    flags = [CHROME, f"--user-data-dir={prof}", "--remote-debugging-port=9333", "--no-first-run",
             "--autoplay-policy=no-user-gesture-required", "--new-window", f"http://localhost:{a.port}/"]
    if a.gpu == "off":
        flags.insert(1, "--disable-gpu")
    chrome = None
    try:
        for _ in range(60):
            time.sleep(0.5)
            try: http_json(f"http://localhost:{a.port}/health"); break
            except Exception: pass
        chrome = subprocess.Popen(flags)
        tab = None
        for _ in range(40):
            time.sleep(0.5)
            try:
                tab = next(t for t in http_json("http://localhost:9333/json") if t["type"] == "page" and f":{a.port}" in t["url"])
                break
            except Exception: pass
        s = {"id": 0}
        async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=None) as ws:
            bws = http_json("http://localhost:9333/json/version")["webSocketDebuggerUrl"]
            async with websockets.connect(bws, max_size=None) as b:
                info = await cdp(b, {"id": 0}, "SystemInfo.getInfo")
            g = info.get("gpu", {})
            feats = g.get("featureStatus", {})
            feats["gpu_device"] = (g.get("devices") or [{}])[0].get("deviceString")
            await asyncio.sleep(2)
            await cdp(ws, s, "Runtime.evaluate", expression="startStream()")
            fps = []
            t0 = time.time()
            while time.time() - t0 < a.seconds:
                await asyncio.sleep(1)
                r = await cdp(ws, s, "Runtime.evaluate", expression="document.getElementById('status').textContent")
                txt = r["result"].get("value", "")
                mm = re.match(r"FPS: (\d+)/(\d+)", txt)
                if mm: fps.append((int(mm[1]), int(mm[2])))
            vals = [f for f, _ in fps]
            print(json.dumps({
                "chrome_gpu": a.gpu,
                "chrome_feature_status": feats,
                "target_fps": fps[-1][1] if fps else None,
                "samples": len(vals), "fps_mean": round(sum(vals) / len(vals), 1) if vals else None,
                "fps_min": min(vals) if vals else None,
            }, indent=2))
    finally:
        if chrome: chrome.terminate()
        srv.terminate()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("video"); ap.add_argument("--cols", type=int, default=200)
    ap.add_argument("--port", type=int, default=8767); ap.add_argument("--seconds", type=int, default=25)
    ap.add_argument("--gpu", choices=("on", "off"), default="off")
    ap.add_argument("--extra", nargs=argparse.REMAINDER, default=[])
    asyncio.run(main(ap.parse_args()))
