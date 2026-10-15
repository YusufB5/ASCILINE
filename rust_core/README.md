# Optional Rust engine

Rust implements source-file decoding through FFmpeg, ASCII mapping, adaptive ASCII encoding and pixel DCT encoding. Python retains FastAPI, audio/thumbnail endpoints and playback sessions. Browser reconstruction stays JavaScript. See [architecture](../ARCHITECTURE.md) and [live usage](../docs/LIVE_STREAMING.md).

## Installation scope

`python -m pip install .` installs Python, not a Rust build. This checkout builds `_asciline_native` locally; Windows loader expects `rust_core/target/release/_asciline_native.dll` and records machine settings in `rust_core/runtime.json`.

Use the same Python interpreter for build and server. Windows was verified with Python 3.11 and native version 0.2.0 on 2026-10-08. Python/npm metadata remain 0.1.5. These source changes are not automatically present in a previously published package.

## Windows prerequisites

- Base Python dependencies: `python -m pip install .`.
- Rust toolchain with Cargo available.
- Visual Studio x64 C++ Build Tools, including `vswhere` and `vcvars64.bat`.
- Stable FFmpeg **8.1 shared development** package with `include/`, `lib/`, `bin/`; keep matching shared DLLs after compilation.

Executable-only FFmpeg can run audio extraction but cannot build the native decoder. Nightly headers can expose enum values not handled by locked `ffmpeg-next 8.1.0`. A previously working DLL does not prove a clean build with different headers works.

The validated BtbN package was `n8.1.3-14-g330caae0c1-20261003`, LGPL shared, from [BtbN releases](https://github.com/BtbN/FFmpeg-Builds/releases). Its archive SHA256 was `54f1f8cc5f6db333e8c34f19fb38dcc79d6427b38ecdd1a6dade1664be8018d5`. This identifies the tested archive; latest downloads change. Store local dependencies under ignored `rust_core/.deps/` or supply another root.

## Build and verify

From repository root, replace the example path with the extracted directory directly containing include/lib/bin:

```powershell
python rust_core/build.py --ffmpeg-dir "C:/dependencies/ffmpeg-shared"
python -m asciline.engines --engine rust
```

The script discovers MSVC, uses release optimization and Cargo.lock, and writes runtime settings only after success. It does not install globally or alter system PATH. Cargo is offline by default; on a machine without cached crates allow fetching:

```powershell
python rust_core/build.py --ffmpeg-dir "C:/dependencies/ffmpeg-shared" --online
```

The engine check should show `engine: rust`, native path, version and null fallback reason. Start a local video:

```powershell
python stream_server.py video.mp4 --engine rust --pixel --pixel-codec dct --cols 450 --fps 60 --no-thumbnails --decode-ahead 3
```

Open `http://localhost:8000/static/examples/sdk-live.html`. The local SDK negotiates sync/DCT; older clients without capabilities receive legacy/raw. `--pixel-codec raw` selects raw transport. [Live settings](../docs/LIVE_STREAMING.md) explain caps, FPS and thread overrides.

## Runtime discovery and troubleshooting

Loader first searches the local release binary, then an installed `_asciline_native`, and validates API version 1. `rust` fails explicitly; `auto` logs a reason and can fall back. Webcam devices keep Python/OpenCV.

Windows loader retains DLL-directory handles. `ASCILINE_FFMPEG_DIR`, `FFMPEG_DIR`, build configuration and compatible PATH directories participate in shared-library discovery. Audio executable selection prioritizes ASCILINE_FFMPEG_DIR, FFMPEG_DIR, then native runtime configuration when native playback is active, before PATH. An unrelated older PATH executable can fail on a file the Rust decoder handles.

| Symptom | Check/action |
| --- | --- |
| Native module not found | Build locally and confirm release DLL |
| Python version mismatch | Rebuild with server interpreter |
| DLL import failure | Matching FFmpeg runtime DLLs and paths |
| Missing include/lib/bin | Shared development package required |
| Wrapper enum errors | Validated stable headers rather than nightly |
| Build cannot replace DLL | Close servers/tests loading it and rebuild |
| Rust video works, audio fails | Recorded FFmpeg path and `/audio` diagnostics |
| DCT/thread capability unavailable | Rebuild current sources rather than older DLL |

Git contains source/lockfile, not local runtime. Fresh clone, branch changes or deleted build files may require rebuilding. Current Python packaging does not provision native wheels or FFmpeg development files. Current Docker runs Python and does not compile Rust.

## Linux and macOS

Install FFmpeg development libraries, pkg-config metadata, Python dependencies and Cargo, then run `python rust_core/build.py` (or `--online`). These script paths have not been validated in this Windows integration; native build coverage differs from Python platform support.

## Engine API and ownership

```python
from asciline.engines import get_engine
engine = get_engine("rust")
with engine.decoder("video.mp4", 450, 253, skip_gray=True) as decoder:
    decoder.seek(1.23)
    gray, bgr = next(decoder)
    print(decoder.position)  # source PTS relative to stream start
    decoder.resize(320, 180)
```

The adapter exposes seek, resize, next/grab, skip-gray and release. Native decoder calls are serialized. Decode/encode release the GIL; encoder inputs are copied to owned Rust buffers first. DCT uses the existing tag-4 format and reconstructed predictor, with integer inverse semantics matching JavaScript. Motion-search SAD uses SSE2 on x86_64 and scalar elsewhere; search/format decisions remain unchanged.

`decode_threads=None` keeps codec default, zero requests auto, 1–64 requests a fixed count. It applies to source decoding separately from DCT/queue. Actual workers are codec-specific. `AsciiStreamServer` supports engine selection but not this override or decode-ahead; use CLI for those.

## Tests

```powershell
python rust_core/build.py --test --ffmpeg-dir "C:/dependencies/ffmpeg-shared"
python -m pytest -q -rs test/test_sdk_profile.py test/test_engine_selection.py test/test_native_engine.py test/test_live_profile.py test/test_playback_recording.py test/test_native_stream.py
npm test
```

On 2026-10-08, the targeted Python command passed 66 tests with no skips after the Windows build. Coverage includes repeated seeks, B-frame drain, resize/mirror, reconstruction, actual server control and SDK connections. Rust's unit test compares SIMD SAD to scalar. Read pass/skip counts: missing native engine skips dependent tests. Node/synthetic tests complement browser observation, not all-device FPS or physical A/V skew.

[Measured performance and recording](../docs/PERFORMANCE.md).

## Files

| File | Responsibility |
| --- | --- |
| `build.py` | Release build and runtime metadata |
| `Cargo.toml`, `Cargo.lock` | Native dependencies |
| `src/lib.rs` | Python API/version/capabilities |
| `src/decoder.rs` | FFmpeg decode/drain, seek/PTS, resize and threads |
| `src/ascii.rs` | Character/color mapping |
| `src/codec.rs` | Adaptive ASCII encoding |
| `src/dct.rs` | DCT, prediction/reconstruction and motion search |
| `target/`, `.deps/`, `runtime.json` | Ignored machine-local files |
| `../asciline/engines.py` | Python/native selection, loader and adapter |
