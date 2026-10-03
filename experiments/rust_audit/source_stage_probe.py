"""Profile an isolated COPY of the source decoder, with unchanged output checks.

Normal/profile/normal passes decode and DCT-encode identical consecutive frames.
This is a serial stage benchmark, not browser playback or decode-ahead timing.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.rust_audit.stage_probe import load_binary, replace, summary

OUT = Path(__file__).parent / "output/source-stages"
STAGES = ("demux_read", "codec_receive_send", "scale_and_color", "pack_bgr_and_gray")


def instrument(source):
    source = replace(source, "    fallback_time: f64,", "    fallback_time: f64,\n    profile_ms: [f64; 4],")
    source = replace(source, "                fallback_time: 0.0,", "                fallback_time: 0.0,\n                profile_ms: [0.0; 4],")
    source = replace(source, "            match self.decoder.receive_frame(&mut frame) {", """            let tick = std::time::Instant::now();
            let result = self.decoder.receive_frame(&mut frame);
            self.profile_ms[1] += tick.elapsed().as_secs_f64() * 1000.0;
            match result {""")
    source = replace(source, "                match packet.read(&mut self.input) {", """                let tick = std::time::Instant::now();
                let result = packet.read(&mut self.input);
                self.profile_ms[0] += tick.elapsed().as_secs_f64() * 1000.0;
                match result {""")
    for call in ("self.decoder.send_packet(&packet)", "self.decoder.send_eof()"):
        source = replace(source, f"                        {call}?;", f"""                        let tick = std::time::Instant::now();
                        let result = {call};
                        self.profile_ms[1] += tick.elapsed().as_secs_f64() * 1000.0;
                        result?;""")
    source = replace(source, "    fn next_frame(&mut self) -> Result<Option<FrameOutput>, Error> {", """    fn next_frame(&mut self) -> Result<Option<FrameOutput>, Error> {
        self.profile_ms = [0.0; 4];""")
    source = replace(source, "        let source = (decoded.format(), decoded.width(), decoded.height());",
                     "        let tick = std::time::Instant::now();\n        let source = (decoded.format(), decoded.width(), decoded.height());")
    source = replace(source, "        let stride = scaled.stride(0);", """        self.profile_ms[2] = tick.elapsed().as_secs_f64() * 1000.0;
        let tick = std::time::Instant::now();
        let stride = scaled.stride(0);""")
    source = replace(source, "        Ok(Some((gray, bgr)))", "        self.profile_ms[3] = tick.elapsed().as_secs_f64() * 1000.0;\n        Ok(Some((gray, bgr)))")
    source = replace(source, "    fn grab(&self, py: Python<'_>) -> PyResult<bool> {", """    #[getter]
    fn profile_ms(&self, py: Python<'_>) -> PyResult<Vec<f64>> {
        self.with_core(py, |core| Ok(core.profile_ms.to_vec()))
    }

    fn grab(&self, py: Python<'_>) -> PyResult<bool> {""")
    return source


def nightly_compat(crate):
    """Explicit, isolated compatibility for the local FFmpeg June-2026 headers.

    Add lossless enum mappings, never wildcard/panic conversions. Do not patch
    the Cargo registry or production manifest. Stable FFmpeg builds do not need
    this option. The additional variants are not on this video's decoding path.
    """
    cargo_root = Path(os.environ.get("CARGO_HOME", str(Path.home() / ".cargo")))
    cached = sorted((cargo_root / "registry/src").glob("*/ffmpeg-next-8.1.0"))
    if len(cached) != 1:
        raise RuntimeError("Expected one cached ffmpeg-next 8.1.0 package for explicit nightly compatibility")
    vendor = crate / "vendor/ffmpeg-next"
    shutil.copytree(cached[0], vendor, dirs_exist_ok=True)
    mappings = [
        ("src/util/frame/side_data.rs", "Type", "AVFrameSideDataType", "AV_FRAME_DATA_",
         ["DYNAMIC_HDR_SMPTE_2094_APP5", "IAMF_MIX_GAIN_PARAM", "IAMF_DEMIXING_INFO_PARAM",
          "IAMF_RECON_GAIN_INFO_PARAM", "RAW_COLOR_PARAMS"]),
        ("src/codec/packet/side_data.rs", "Type", "AVPacketSideDataType", "AV_PKT_DATA_",
         ["DYNAMIC_HDR_SMPTE_2094_APP5", "HEVC_CONF"]),
        ("src/codec/id.rs", "Id", "AVCodecID", "AV_CODEC_ID_", ["WEBP_ANIM", "APPLE_APAC"]),
    ]
    for filename, enum, ffi, prefix, variants in mappings:
        path = vendor / filename
        source = path.read_text(encoding="utf-8")
        source = replace(source, f"pub enum {enum} {{", f"pub enum {enum} {{\n" +
                         "".join(f"    {v},\n" for v in variants))
        for before, after, arms in (
            (ffi, enum, "".join(f"            {prefix}{v} => {enum}::{v},\n" for v in variants)),
            (enum, ffi, "".join(f"            {enum}::{v} => {prefix}{v},\n" for v in variants)),
        ):
            offset = source.index(f"impl From<{before}> for {after}")
            position = source.index("match value {", offset) + len("match value {")
            source = source[:position] + "\n" + arms + source[position:]
        path.write_text(source, encoding="utf-8")
    manifest = crate / "Cargo.toml"
    manifest.write_text(manifest.read_text(encoding="utf-8") +
                        '\n[patch.crates-io]\nffmpeg-next = { path = "vendor/ffmpeg-next" }\n', encoding="utf-8")
    subprocess.run(["cargo", "generate-lockfile", "--offline", "--manifest-path", str(manifest)], check=True)


def prepare_build(compat=False):
    crate = OUT / "crate"
    (crate / "src").mkdir(parents=True, exist_ok=True)
    fingerprints = {}
    for path in (ROOT / "rust_core/src").glob("*.rs"):
        fingerprints[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        shutil.copy2(path, crate / "src" / path.name)
    for name in ("Cargo.toml", "Cargo.lock", "build.py"):
        shutil.copy2(ROOT / "rust_core" / name, crate / name)
    decoder = crate / "src/decoder.rs"
    decoder.write_text(instrument(decoder.read_text(encoding="utf-8")), encoding="utf-8")
    if compat:
        nightly_compat(crate)
    config = json.loads((ROOT / "rust_core/runtime.json").read_text(encoding="utf-8"))
    command = [sys.executable, str(crate / "build.py")]
    if config.get("ffmpeg_dir"):
        command += ["--ffmpeg-dir", config["ffmpeg_dir"]]
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str((crate / "target").resolve())
    subprocess.run(command, env=env, check=True)
    (OUT / "source-hashes.json").write_text(json.dumps(fingerprints, indent=2), encoding="utf-8")
    (OUT / "variant.json").write_text(json.dumps({"nightly_compat": compat}), encoding="utf-8")


def measure(args):
    if args.child == "profile":
        filename = "_asciline_native.dll" if os.name == "nt" else "lib_asciline_native" + (".dylib" if sys.platform == "darwin" else ".so")
        load_binary(OUT / "crate/target/release" / filename)
    from asciline.engines import get_engine
    engine = get_engine("rust")
    results = []
    for start in args.starts:
        with engine.decoder(args.video, args.cols, args.rows, skip_gray=True) as decoder:
            decoder.seek(start)
            encoder = engine.native.ProfileEncoder(args.cols, args.rows, 70, .75, 256, 3)
            samples = []
            digest = hashlib.sha256()
            for index in range(args.frames):
                tick = time.perf_counter()
                try:
                    _, bgr = next(decoder)
                except StopIteration:
                    break
                source_ms = (time.perf_counter() - tick) * 1000
                stages = list(decoder.profile_ms) if args.child == "profile" else None
                packet, shown = encoder.encode(bgr)
                digest.update(bgr.tobytes())
                digest.update(packet)
                digest.update(shown)
                sample = dict(ordinal=index, source_ms=source_ms)
                if stages:
                    sample.update(zip(STAGES, stages))
                    sample["boundary_and_unattributed"] = source_ms - sum(stages)
                samples.append(sample)
            if not samples:
                raise ValueError(f"No frames at {start}s")
            result = dict(start=start, grid=[args.cols, args.rows], frames=len(samples),
                          sha256=digest.hexdigest(), source_ms=summary([s["source_ms"] for s in samples]),
                          samples=samples)
            if args.child == "profile":
                result["stages_ms"] = {name: summary([s[name] for s in samples])
                                       for name in (*STAGES, "boundary_and_unattributed")}
                result["slowest"] = sorted(samples, key=lambda s: s["source_ms"], reverse=True)[:10]
            results.append(result)
            print(f"{args.child} {start}s: source={result['source_ms']}", flush=True)
    args.report.write_text(json.dumps(results, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--nightly-compat", action="store_true", help="Explicit isolated enum mappings for local FFmpeg June-2026 development headers")
    parser.add_argument("--video", default=str(ROOT / "videos/costa_rica_60fps.mp4"))
    parser.add_argument("--cols", type=int, default=464)
    parser.add_argument("--rows", type=int, default=256)
    parser.add_argument("--frames", type=int, default=180)
    parser.add_argument("--starts", type=float, nargs="+", default=[5, 18, 30])
    parser.add_argument("--child", choices=["normal", "profile"])
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.frames < 1 or args.cols < 16 or args.rows < 16 or args.cols % 16 or args.rows % 16:
        parser.error("frames must be positive and DCT grid dimensions must be positive multiples of 16")
    OUT.mkdir(parents=True, exist_ok=True)
    if args.child:
        if args.report is None:
            parser.error("--child requires --report")
        measure(args)
        return
    if args.build:
        prepare_build(args.nightly_compat)
    hashes_file = OUT / "source-hashes.json"
    if not hashes_file.exists():
        parser.error("Run with --build first")
    if json.loads((OUT / "variant.json").read_text(encoding="utf-8"))["nightly_compat"] != args.nightly_compat:
        parser.error("Compatibility option changed; rerun with --build")
    hashes = json.loads(hashes_file.read_text(encoding="utf-8"))
    if any(hashlib.sha256((ROOT / "rust_core/src" / name).read_bytes()).hexdigest() != value
           for name, value in hashes.items()):
        parser.error("Rust sources changed; rerun with --build")
    results = []
    for index, kind in enumerate(("normal", "profile", "normal")):
        report = OUT / f"{index}-{kind}.json"
        subprocess.run([sys.executable, __file__, "--child", kind, "--video", args.video,
                        "--cols", str(args.cols), "--rows", str(args.rows), "--frames", str(args.frames),
                        "--starts", *map(str, args.starts), "--report", str(report)], check=True)
        results.append(json.loads(report.read_text(encoding="utf-8")))
    for scene in range(len(args.starts)):
        if len({run[scene]["sha256"] for run in results}) != 1:
            raise RuntimeError("Source frames, DCT packets or reconstruction changed")
    (OUT / "comparison.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("PASS: normal/profile/normal source BGR + DCT packet + reconstruction hashes match.", flush=True)


if __name__ == "__main__":
    main()
