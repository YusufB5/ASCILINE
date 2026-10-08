# ASCILINE optional Rust engine

File decoder, character mapper, adaptive RAW/ZLIB/DELTA/RLE encoder and
opt-in pixel DCT profile encoder.
The native module is `_asciline_native`; older `ascii_core` installations
are not used. The standard Python install and server default stay usable.

## Quick test on this checkout

From the repository root, after building:

```powershell
python -m asciline.engines --engine rust
python stream_server.py "C:\path\to\video.mp4" --engine rust --pixel --cols 450 --no-thumbnails
```

Open `http://localhost:8000`. Use a local 60 FPS source to test 60 FPS
playback. `--cols` controls output detail and cost. For a first comparison,
use 300–450 columns and then increase it.

Automatic grid sizing retains the existing pixel/cell, portrait and row
performance caps by default. Add `--no-resolution-limit` (alias: `--no-limit`)
to disable those caps. A 1280x720 source with `--pixel --cols 750` uses 471x265
by default, or 750x422 with the flag (about 57 MB/s of raw BGR at 60 FPS).
The flag also applies to ASCII mode, including mode switches; large ASCII
grids can slow browser character drawing. Explicit `--rows` keeps its previous
behavior. The native decoder still validates dimensions before allocating
memory, and the FPS limit is unchanged.

ASCII is also supported:

```powershell
python stream_server.py "C:\path\to\video.mp4" --engine rust --mode 6 --cols 160
```

The default ceiling is 30 FPS for ASCII with either engine, 60 FPS for Rust
pixel mode, and 30 FPS for Python pixel mode. Use `--fps 60` to opt into higher
ASCII FPS or `--fps 30` to lower pixel FPS. This option is independent of engine
selection and `--no-limit`; an explicit FPS ceiling persists across mode
switches. Without it, each mode uses its own default. Actual visible FPS
depends on source, CPU, network, browser drawing and screen refresh.
Sampling keeps evenly spaced source frames: 59.94 becomes 29.97 under a 30 FPS
ceiling, and 50 becomes 25. No extra frames are generated for lower-rate sources.
Rust server sessions disable WebSocket permessage-deflate: compressing the
raw pixel stream on the event loop can make delivery fall behind real time.
Adaptive ASCII compression still runs in the engine. Raw pixels use more
bandwidth (450x253 at 60 FPS is about 20.5 MB/s before protocol overhead).
Audio and scrub extraction use the native build's FFmpeg runtime, avoiding
an older unrelated executable on PATH. `ASCILINE_FFMPEG_DIR` takes precedence.
It does not create extra frames for a 24/30 FPS source. Current yt-dlp
normalization can produce 30 FPS downloads; use a local original file for
this comparison. Webcam device indices use the Python/OpenCV engine.
The live wire format still schedules frames by nominal FPS; use constant-rate
sources for the first A/V test. The decoder exposes actual PTS for later
transport work with variable-rate footage.

## Live pixel DCT

Rebuild the native module before enabling the new profile encoder. The root
demo negotiates `pixel_codec=dct-v1` with `sync=1`; `INIT` field 11 identifies
the selected pixel transport (`raw` or `dct`). Merely requesting adaptive ASCII
does not opt a client into DCT. The npm SDK continues receiving raw pixels.

```powershell
python stream_server.py "C:\path\to\video.mp4" --engine rust --pixel --pixel-codec dct --dct-quality 70 --cols 450 --fps 30 --no-thumbnails
```

The default remains raw. DCT dimensions round up to multiples of 16. Each
WebSocket owns a predictor; seek/reinit resets it and starts with a keyframe.
Packets use playback frame indices even after seeks and source-frame shedding.
The Python engine supports the same profile. `AsciiStreamServer` accepts
`pixel_codec="dct", dct_quality=70` alongside `engine`, `fps` and grid settings.
All DCT sessions disable transport deflate, since profile packets already use
zlib. Webcam mode remains raw.

Correct reconstruction has automated coverage, including the actual browser
codec source. Real browser FPS and A/V behavior still need a playback test.
The first Costa Rica probe at QF 70 / 464x256 averaged about 18 ms for source
decode plus native DCT, exceeding a 60 FPS budget before network overhead.
Use 30 FPS at that size initially, or try 320 columns for 60 FPS experiments.
See `experiments/rust_audit/DCT_INTEGRATION.md` for measurements and reproduction.

## Build

Use stable FFmpeg **8.1 shared development** libraries with the locked
`ffmpeg-next 8.1.0` dependency. Development/nightly headers may introduce enum
entries the wrapper cannot compile. A working older DLL does not prove that
a fresh dependency build against the currently installed headers will succeed.

Validated on Windows with BtbN `n8.1.3-14-g330caae0c1-20261003`, LGPL shared,
downloaded from the [BtbN releases](https://github.com/BtbN/FFmpeg-Builds/releases).
The tested archive SHA256 is
`54f1f8cc5f6db333e8c34f19fb38dcc79d6427b38ecdd1a6dade1664be8018d5`.
The upstream `latest` asset changes; this checksum identifies the tested
artifact, not every later download. Store local dependencies under ignored
`rust_core/.deps/` or supply your own directory. No vendor patches are needed
for the validated stable build.

Requirements: base ASCILINE Python dependencies, Cargo, and FFmpeg headers
and libraries. On Windows, use a **shared development** FFmpeg build with
`bin/`, `include/`, `lib/`, and Visual Studio x64 C++ Build Tools. A normal
standalone FFmpeg executable alone is insufficient for native compilation.

```powershell
python rust_core/build.py --ffmpeg-dir "C:\path\to\shared-ffmpeg"
```

The script builds an optimized release module locally, discovers the MSVC
environment, keeps Cargo.lock fixed, and records local runtime/ABI settings
in ignored `runtime.json`. It does not install into global Python or change
the system PATH. By default Cargo is offline; on a new machine with no cached
dependencies use `--online` to permit dependency downloads.

On Linux/macOS install FFmpeg development libraries and their pkg-config
metadata, then run `python rust_core/build.py` (add `--online` for an empty
Cargo cache). Those platforms have not been exercised in this checkout.

Rebuild with the same Python major/minor version that runs the server.
On Windows, the loader retains DLL search handles. `ASCILINE_FFMPEG_DIR`
or `FFMPEG_DIR` can override the FFmpeg runtime directory.

## Integration API

### Source decoder concurrency

`stream_server.py --engine rust --decode-threads N` controls source-video
decoder concurrency, independently of the DCT encoder and decode-ahead queue:

- Omit the flag to preserve the codec's existing default (no thread override).
- `--decode-threads 2` requests two source-decoder threads.
- `--decode-threads 0` lets the decoder choose automatically.
- Accepted range: 0–64. Python fallback and webcam input reject this option.

The Python native adapter accepts `decode_threads=None` or an integer in the
same range. The option requires a rebuilt DLL; older DLLs produce an explicit
rebuild error when the override is requested. Server logs and performance
records include decoder name and configured thread count. A zero value denotes
automatic selection, not zero actual workers. Codec implementations may adjust
the requested count.

Keep `--decode-ahead 3` fixed when comparing these settings. Increased source
concurrency can compete with DCT encoding; do not assume automatic or larger
counts always improve end-to-end playback. See
`experiments/rust_audit/DECODER_THREAD_FINDINGS.md` for measurements.

```python
from asciline.engines import get_engine

engine = get_engine("rust")  # explicit failure if unavailable
# get_engine("auto"): fallback to Python, with reason in the log
# get_engine("python"): no native loading

with engine.decoder("video.mp4", 450, 253, skip_gray=True) as decoder:
    decoder.seek(1.23)
    gray, bgr = next(decoder)
    print(decoder.position)  # actual decoded PTS, relative to stream start
    decoder.resize(320, 180)
    decoder.set_skip_gray(False)
```

The Python adapter contains engine choice and compatibility behavior.
The server uses public `resize`, `set_skip_gray`, `seek`, `grab`, `release`
methods. Native decoder access is serialized; decode and encode work release
the Python GIL. Encoder/mapper inputs are copied to owned Rust buffers
before the GIL is released, preventing concurrent NumPy mutation races.

`AsciiStreamServer(..., engine="rust")` also selects the native engine.
Its optional `fps=60` argument overrides the same FPS policy; omitting it uses
the mode defaults above. `no_resolution_limit=True` only changes grid sizing.
The transport and the current web player retain the existing protocol.

The root live player additionally requests `sync=1` on its WebSocket. This
optional extension sends four preroll frames, waits for `playback-ready`,
and anchors server pacing to the client's actual clock. Seek requests carry
a `requestId`; `SEEKED:<requestId>:<actual seconds>` separates stale frames
from the new timeline. Audio starts only after the matching marker and new
frames arrive. Clients without `sync=1` retain the existing behavior and wire
frames. This extension has not been ported to the npm SDK.

## Tests and manual checks

```powershell
python rust_core/build.py --test --ffmpeg-dir C:\path\to\ffmpeg-shared
python -m pytest test/test_native_engine.py test/test_native_stream.py test/test_engine_selection.py -q
python -m pytest test/ -q
```

Native tests skip if the engine is not built; check the reported pass/skip
counts. Tests generate short local videos. They cover long-GOP seek,
nonzero stream start, B-frame draining, mixed next/grab, resize, mirror,
close, validation, mapping, codec keyframes/tolerance, GIL progress, and
the real CLI/WebSocket path at 60 FPS with seek/pause/reinit.

For the user playback test:

1. Play a local 60 FPS video with sound for several minutes.
2. Seek forward and backward, pause/resume, and switch ASCII/pixel modes.
3. Repeat muted and with a 24/30 FPS video.
4. Compare `--engine python` and `--engine rust` at the same dimensions.

Automated WebSocket tests establish delivered frames and timing; a real
browser/audio session is needed to assess visible smoothness and A/V sync.

## Layout

```text
rust_core/
  Cargo.toml, Cargo.lock   reproducible dependency definition
  build.py                local release build
  src/lib.rs              versioned Python API
  src/decoder.rs          FFmpeg receive/feed/drain, PTS and seek
  src/ascii.rs            character and color mapping
  src/codec.rs            existing adaptive wire format
  target/                 ignored build artifacts
  runtime.json            ignored machine-specific runtime configuration
asciline/engines.py        Python/Rust adapter and module loader
```

`src/dct.rs` is now compiled and exports `ProfileEncoder`. Its inverse transform
uses floor division to match JavaScript, validates shape/options, owns inputs
before releasing the GIL, and maintains its own reconstructed reference planes.

Color conversion uses exact integer truncation of already clamped samples.
Motion-search SAD uses SSE2 on x86_64, with a scalar implementation on other
architectures. It preserves search radius, candidate order, zero-motion tie
preference and the encoded format. Native tests cover unaligned loads, extreme
pixel values and bounded SAD cutoffs; Python/JS contract tests validate the
complete encoder. Measurements are in
[`PERFORMANCE_FINDINGS.md`](../experiments/rust_audit/PERFORMANCE_FINDINGS.md).
