"""Profile a COPY of the current Rust encoder; never replace the live DLL.

Build/run: python experiments/rust_audit/stage_probe.py --build
The isolated copy gets coarse stage and per-block timers. Baseline/profile/
baseline passes expose instrumentation and machine-load effects. Packet and
reconstruction hashes must agree across all passes. Outputs are ignored.
"""
import argparse
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).parent / "output/stages"
STAGES = ["color_convert", "motion_prediction", "dct_quantize_skip",
          "idct_reconstruct", "pack_rle", "zlib", "shown_bgr"]


def replace(source, old, new, count=1):
    assert source.count(old) == count, f"Instrumentation anchor changed: {old!r}"
    return source.replace(old, new)


def instrument(source):
    source = replace(source, "    prev_cr: Vec<u8>,", "    prev_cr: Vec<u8>,\n    profile_ms: [f64; 7],")
    source = replace(source, "            prev_cr: Vec::new(),", "            prev_cr: Vec::new(),\n            profile_ms: [0.0; 7],")
    source = replace(source, "    fn reset(&mut self) {", "    #[getter]\n    fn profile_ms(&self) -> Vec<f64> { self.profile_ms.to_vec() }\n\n    fn reset(&mut self) {")
    source = replace(source, "        let (y, cb, cr) = bgr_to_yuv", "        self.profile_ms = [0.0; 7];\n        let stage = std::time::Instant::now();\n        let (y, cb, cr) = bgr_to_yuv")
    source = replace(source, "        let ftype = if", "        self.profile_ms[0] = stage.elapsed().as_secs_f64() * 1000.0;\n        let ftype = if")
    source = replace(source, "            self.skip_t,\n        );", "            self.skip_t,\n            &mut self.profile_ms,\n        );", 3)
    source = replace(source, "    skip_t: i32,\n) ->", "    skip_t: i32,\n    profile_ms: &mut [f64; 7],\n) ->")
    source = replace(source, "            let mut pred =", "            let stage = std::time::Instant::now();\n            let mut pred =")
    source = replace(source, "            // Residual", "            profile_ms[1] += stage.elapsed().as_secs_f64() * 1000.0;\n            let stage = std::time::Instant::now();\n            // Residual")
    source = replace(source, "            // Reconstruction", "            profile_ms[2] += stage.elapsed().as_secs_f64() * 1000.0;\n            let stage = std::time::Instant::now();\n            // Reconstruction")
    source = replace(source, "        }\n    }\n\n    // DC DPCM", "            profile_ms[3] += stage.elapsed().as_secs_f64() * 1000.0;\n        }\n    }\n\n    let stage = std::time::Instant::now();\n    // DC DPCM")
    source = replace(source, "    (payload, recon)", "    profile_ms[4] += stage.elapsed().as_secs_f64() * 1000.0;\n    (payload, recon)")
    source = replace(source, "        let mut e =", "        let stage = std::time::Instant::now();\n        let mut e =")
    source = replace(source, "        let mut msg =", "        self.profile_ms[5] = stage.elapsed().as_secs_f64() * 1000.0;\n        let mut msg =")
    source = replace(source, "        let bgr_rec =", "        let stage = std::time::Instant::now();\n        let bgr_rec =")
    source = replace(source, "        Ok((msg, bgr_rec))", "        self.profile_ms[6] = stage.elapsed().as_secs_f64() * 1000.0;\n        Ok((msg, bgr_rec))")
    return source


def prepare_build(color_casts=False):
    crate = OUT / "crate"
    (crate / "src").mkdir(parents=True, exist_ok=True)
    fingerprints = {}
    for path in (ROOT / "rust_core/src").glob("*.rs"):
        fingerprints[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        shutil.copy2(path, crate / "src" / path.name)
    for filename in ("Cargo.toml", "Cargo.lock", "build.py"):
        shutil.copy2(ROOT / "rust_core" / filename, crate / filename)
    source = (crate / "src/dct.rs").read_text(encoding="utf-8")
    if color_casts:
        # Both values have already been clamped to 0..255. Integer conversion
        # truncates exactly, avoiding scalar floating-point trunc calls.
        if "y[i] = y_val.trunc() as f64;" in source:
            source = replace(source, "y[i] = y_val.trunc() as f64;", "y[i] = (y_val as u8) as f64;")
            source = replace(source, "out[y * hw + x] = val.trunc();", "out[y * hw + x] = (val as u8) as f64;")
        else:
            assert "y[i] = (y_val as u8) as f64;" in source, "Color conversion changed"
    (crate / "src/dct.rs").write_text(instrument(source), encoding="utf-8")
    (OUT / "source-hashes.json").write_text(json.dumps(fingerprints, indent=2), encoding="utf-8")
    config = json.loads((ROOT / "rust_core/runtime.json").read_text(encoding="utf-8"))
    command = [sys.executable, str(crate / "build.py")]
    if config.get("ffmpeg_dir"):
        command += ["--ffmpeg-dir", config["ffmpeg_dir"]]
    env = os.environ.copy()
    # Even an externally configured Cargo target must not overwrite the live DLL.
    env["CARGO_TARGET_DIR"] = str((crate / "target").resolve())
    subprocess.run(command, env=env, check=True)
    (OUT / "built-variant.json").write_text(json.dumps({"color_casts": color_casts}), encoding="utf-8")


def summary(values):
    return {"mean": round(float(np.mean(values)), 4),
            "p95": round(float(np.percentile(values, 95)), 4),
            "max": round(float(np.max(values)), 4)}


def load_binary(binary):
    from asciline.engines import _dll_directories
    _dll_directories()
    loader = importlib.machinery.ExtensionFileLoader("_asciline_native", str(binary))
    spec = importlib.util.spec_from_file_location("_asciline_native", binary, loader=loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules["_asciline_native"] = module


def measure(args):
    from asciline.engines import get_engine
    if args.binary:
        load_binary(args.binary.resolve())
    elif args.child == "profile":
        filename = "_asciline_native.dll" if os.name == "nt" else "lib_asciline_native" + (".dylib" if sys.platform == "darwin" else ".so")
        binary = OUT / "crate/target/release" / filename
        load_binary(binary)
    from asciline.pixel_profile import align_profile_grid
    from stream_server import calc_auto_dimensions
    engine = get_engine("rust")
    results = []
    for start in args.starts:
        with engine.decoder(args.video, 2, 2, skip_gray=True) as decoder:
            w, h = align_profile_grid(*calc_auto_dimensions(args.cols, decoder.vid_w, decoder.vid_h, True))
            decoder.resize(w, h)
            decoder.seek(start)
            encoder = engine.native.ProfileEncoder(w, h, 70, .75, 256, 3)
            dec_ms, enc_ms, stages = [], [], []
            digest = hashlib.sha256()
            packets = 0
            for _ in range(args.frames):
                before = time.perf_counter()
                try:
                    _, bgr = next(decoder)
                except StopIteration:
                    break
                decoded = time.perf_counter()
                packet, shown = encoder.encode(bgr)
                encoded = time.perf_counter()
                dec_ms.append((decoded - before) * 1000)
                enc_ms.append((encoded - decoded) * 1000)
                if args.child == "profile":
                    stages.append(encoder.profile_ms)
                digest.update(packet)
                digest.update(shown)
                packets += len(packet)
            result = {"start": start, "grid": [w, h], "frames": len(enc_ms),
                      "source_ms": summary(dec_ms), "dct_ms": summary(enc_ms),
                      "combined_ms": summary(np.array(dec_ms) + enc_ms),
                      "bytes": packets, "sha256": digest.hexdigest()}
            if stages:
                result["stages_ms"] = {name: summary(np.array(stages)[:, i]) for i, name in enumerate(STAGES)}
                result["unattributed_ms"] = summary(np.array(enc_ms) - np.sum(stages, axis=1))
            results.append(result)
            print(f"{args.child} start={start}: decode={result['source_ms']['mean']:.2f} "
                  f"DCT={result['dct_ms']['mean']:.2f} ms", flush=True)
    args.report.write_text(json.dumps(results, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--color-casts", action="store_true", help="Try exact truncating integer casts in the isolated color conversion")
    parser.add_argument("--video", default=str(ROOT / "videos/costa_rica_60fps.mp4"))
    parser.add_argument("--cols", type=int, default=450)
    parser.add_argument("--frames", type=int, default=180)
    parser.add_argument("--starts", type=float, nargs="+", default=[5, 18, 30])
    parser.add_argument("--child", choices=["baseline", "profile"])
    parser.add_argument("--report", type=Path)
    parser.add_argument("--binary", type=Path, help="Explicit DLL for an isolated --child measurement")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.child:
        measure(args)
        return
    if args.build:
        prepare_build(args.color_casts)
    else:
        metadata = OUT / "built-variant.json"
        if not metadata.exists() or json.loads(metadata.read_text(encoding="utf-8"))["color_casts"] != args.color_casts:
            parser.error("Run with --build to create the requested isolated measurement variant")
    results = []
    for ordinal, kind in enumerate(("baseline", "profile", "baseline")):
        report = OUT / f"{ordinal}-{kind}.json"
        subprocess.run([sys.executable, __file__, "--child", kind, "--video", args.video,
                        "--cols", str(args.cols), "--frames", str(args.frames),
                        "--starts", *map(str, args.starts), "--report", str(report)], check=True)
        results.append(json.loads(report.read_text(encoding="utf-8")))
    for scene in range(len(args.starts)):
        assert len({run[scene]["sha256"] for run in results}) == 1, "Instrumentation changed output!"
    print("PASS: baseline/profile/baseline packet and reconstructed-image hashes match.", flush=True)
    filename = "comparison-color-casts.json" if args.color_casts else "comparison.json"
    (OUT / filename).write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
