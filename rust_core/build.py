"""Build the optional native engine in the workspace; no global install needed."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg-dir", type=Path, help="Shared FFmpeg root containing include/, lib/, bin/ (Windows)")
    parser.add_argument("--online", action="store_true", help="Allow Cargo to fetch missing dependencies")
    parser.add_argument("--test", action="store_true", help="Run native Rust unit tests in release mode")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    env = os.environ.copy()
    env["PYO3_PYTHON"] = sys.executable
    ffmpeg_dir = args.ffmpeg_dir or (Path(env["FFMPEG_DIR"]) if env.get("FFMPEG_DIR") else None)
    if ffmpeg_dir:
        ffmpeg_dir = ffmpeg_dir.resolve()
        for part in ("include", "lib", "bin"):
            if not (ffmpeg_dir / part).is_dir():
                parser.error(f"FFmpeg root is missing {part}/: {ffmpeg_dir}")
        env["FFMPEG_DIR"] = str(ffmpeg_dir)
        env["PATH"] = os.pathsep.join((str(ffmpeg_dir / "bin"), str(Path(sys.executable).parent), env.get("PATH", "")))
    if os.name == "nt" and ffmpeg_dir is None:
        parser.error("Pass --ffmpeg-dir or set FFMPEG_DIR to a shared FFmpeg development build")
    if not shutil.which("cargo"):
        parser.error("Cargo was not found; install the Rust toolchain first")
    target = root / "target"
    target.mkdir(exist_ok=True)
    offline = [] if args.online else ["--offline"]
    operation = "test" if args.test else "build"
    command = ["cargo", operation, "--release", "--locked", *offline, "--manifest-path", str(root / "Cargo.toml")]
    lock_command = ["cargo", "generate-lockfile", *offline, "--manifest-path", str(root / "Cargo.toml")]
    # Windows bindgen needs the MSVC/UCRT include environment as well as FFmpeg.
    if os.name == "nt":
        vswhere = Path(env.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Visual Studio/Installer/vswhere.exe"
        if not vswhere.exists():
            parser.error("Visual Studio C++ Build Tools (vswhere) not found")
        installation = subprocess.check_output([str(vswhere), "-latest", "-products", "*",
            "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
            "-property", "installationPath"], text=True).strip()
        vcvars = Path(installation) / "VC/Auxiliary/Build/vcvars64.bat"
        if not installation or not vcvars.exists():
            parser.error("Visual Studio x64 C++ Build Tools were not found")
        env["ASCILINE_VCVARS"] = str(vcvars)
        env["ASCILINE_MANIFEST"] = str(root / "Cargo.toml")
        env["ASCILINE_LOCK"] = str(root / "Cargo.lock")
        offline_arg = "" if args.online else "--offline"
        script = target / "build-native.cmd"
        script.write_bytes((
            '@echo off\r\ncall "%ASCILINE_VCVARS%" >nul\r\n'
            'if errorlevel 1 exit /b 1\r\n'
            f'if not exist "%ASCILINE_LOCK%" cargo generate-lockfile {offline_arg} --manifest-path "%ASCILINE_MANIFEST%"\r\n'
            'if errorlevel 1 exit /b 1\r\n'
            f'cargo {operation} --release --locked {offline_arg} --manifest-path "%ASCILINE_MANIFEST%"\r\n'
            'exit /b %errorlevel%\r\n').encode("ascii"))
        subprocess.run(["cmd.exe", "/d", "/c", str(script)], env=env, check=True)
    else:
        if not (root / "Cargo.lock").exists():
            subprocess.run(lock_command, env=env, check=True)
        subprocess.run(command, env=env, check=True)
    if args.test:
        print("\nNative unit tests passed.")
        return
    # Written only after a successful build. Loader retains DLL-directory handles.
    (root / "runtime.json").write_text(json.dumps({
        "python": sys.executable,
        "python_version": list(sys.version_info[:2]),
        "ffmpeg_dir": str(ffmpeg_dir) if ffmpeg_dir else None,
    }, indent=2), encoding="utf-8")
    print("\nNative engine built. Check: python -m asciline.engines --engine rust")


if __name__ == "__main__":
    main()
