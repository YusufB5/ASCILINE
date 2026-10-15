"""Compare saved release DLLs on identical video windows, without stage timers.

Runs baseline / optional intermediate / candidate / baseline sequentially.
Compares packet+reconstruction hashes and retains all per-window timings.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--intermediate", type=Path)
    parser.add_argument("--video", default="videos/costa_rica_60fps.mp4")
    parser.add_argument("--frames", type=int, default=180)
    parser.add_argument("--starts", type=float, nargs="+", default=[5, 18, 30])
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "output/optimization")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    runs = [("baseline", args.baseline)]
    if args.intermediate:
        runs.append(("intermediate", args.intermediate))
    runs += [("candidate", args.candidate), ("baseline_repeat", args.baseline)]
    results = {}
    for name, binary in runs:
        report = args.out / f"{name}.json"
        subprocess.run([sys.executable, str(Path(__file__).with_name("stage_probe.py")),
                        "--child", "baseline", "--binary", str(binary.resolve()),
                        "--video", args.video, "--frames", str(args.frames),
                        "--starts", *map(str, args.starts), "--report", str(report)], check=True)
        results[name] = {"binary": str(binary.resolve()),
                         "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                         "scenes": json.loads(report.read_text(encoding="utf-8"))}
    for scene in range(len(args.starts)):
        assert len({run["scenes"][scene]["sha256"] for run in results.values()}) == 1, "Wire or reconstruction changed"
        assert all(run["scenes"][scene]["frames"] == args.frames for run in results.values()), "Incomplete sample"
    (args.out / "comparison.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("PASS: all packet/reconstruction hashes match.")
    for scene, start in enumerate(args.starts):
        old = (results["baseline"]["scenes"][scene]["dct_ms"]["mean"] +
               results["baseline_repeat"]["scenes"][scene]["dct_ms"]["mean"]) / 2
        new = results["candidate"]["scenes"][scene]["dct_ms"]["mean"]
        print(f"start={start:g}s DCT {old:.3f} -> {new:.3f} ms ({old/new:.2f}x)")


if __name__ == "__main__":
    main()
