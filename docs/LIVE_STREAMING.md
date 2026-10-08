# Live streaming

Run commands from the repository root. Use a local source file first; URLs additionally need the yt-dlp extra and may be normalized to a lower frame rate. A 60 FPS ceiling cannot create frames absent from the source.

## Python quick start

```bash
python -m pip install .
python stream_server.py video.mp4 --mode 6 --cols 160
```

Open `http://localhost:8000/`. Python is the default engine. FFmpeg is needed for audio extraction and thumbnails. Video uses Canvas; audio uses an HTML audio element and browser playback policy. Click Play or Enable audio when needed.

## Rust pixel + DCT

Build Rust using the [native installation guide](../rust_core/README.md), then use the configuration verified in the owner's playback test:

```bash
python stream_server.py video.mp4 --engine rust --pixel --pixel-codec dct --cols 450 --fps 60 --no-thumbnails --decode-ahead 3
```

Open `http://localhost:8000/` or `http://localhost:8000/static/examples/sdk-live.html`. The latter imports the local SDK. This is a tested starting configuration, not a new default. Engine, effective FPS and coded grid are printed on connection.

A 1280x720 source with 450 requested columns has a 450x253 base pixel grid. DCT aligns it to 464x256. This aligned grid is actually encoded/drawn, not cropped back to 450 afterwards. To compare raw pixels, change only the codec to `--pixel-codec raw`.

Raw pixels avoid DCT reconstruction but send more data: at 450x253 and 60 FPS, BGR payload alone is about 20.5 MB/s (decimal), excluding packet/transport overhead. DCT introduces lossy reconstruction and work on server and client for a smaller content-dependent payload.

## Engine, mode and codec

| Choice | Values | Responsibility |
| --- | --- | --- |
| Engine | `python`, `rust`, `auto` | Source decoder and server processing |
| Representation | `--mode 1..6`, `--pixel` | Characters/colors or BGR pixels |
| Pixel transport | `--pixel-codec raw`, `dct` | Full pixel bytes or tag-4 prediction/DCT |

`rust` fails explicitly if unavailable. `auto` allows Python fallback with a reason in the log. Webcam device input uses Python/OpenCV even when the file engine is Rust. The SDK receives the negotiated representation and codec; it needs no engine option.

| ASCII mode | Nominal colors | Representation |
| --- | --- | --- |
| 1 | Monochrome | Frame index and newline-separated text |
| 2 | 64 | Character byte + RGB bytes per cell |
| 3 | 512 | Character byte + RGB bytes per cell |
| 4 | 32,768 | Character byte + RGB bytes per cell |
| 5 | 262,144 | Character byte + RGB bytes per cell |
| 6 | 16,777,216 | Character byte + RGB bytes per cell |

Modes 2–6 quantize values but still store four bytes per cell before compression. ASCII uses Canvas text drawing. Pixel mode converts BGR to RGBA and calls `putImageData`; it draws no glyphs. DCT applies only to pixel mode.

## Defaults and flags

| Option | Default | Effect |
| --- | --- | --- |
| `--engine` | `python` | Explicit engine or fallback with `auto` |
| `--mode` | `1` | ASCII color mode; pixel promotes mode 1 to mode 6 |
| `--cols` | ASCII 200; pixel 450 | Requested grid width |
| `--rows` | `0` (automatic) | Height from source and representation |
| `--fps` | ASCII 30; Python pixel 30; Rust pixel 60 | FPS ceiling, not interpolation |
| `--pixel-codec` | `raw` | DCT requires pixel and capable client |
| `--dct-quality` | `70` | Integer 1–100; lower is smaller/lossier |
| `--quality` | `lossless` | Adaptive ASCII color tolerance |
| `--decode-ahead` | `0` | Source queue: `0`, `2`, `3` |
| `--decode-threads` | omitted | Codec default; `0` auto, `1..64` requested |
| `--no-resolution-limit` / `--no-limit` | off | Disable automatic grid performance caps |
| `--no-thumbnails` | off | Disable root-player hover previews |
| `--debug` | off | Bandwidth, production and client timing logs |
| `--perf-record DIR` | off | Session events and timing JSONL |
| `--host`, `--port` | `127.0.0.1`, `8000` | Bind address and port |

Automatic rows retain 125,000-pixel/12,000-ASCII-cell caps plus row/portrait rules. Requested columns may be reduced: the 1280x720 pixel example with `--cols 750` becomes 471x265, or 750x422 with `--no-resolution-limit`, before DCT alignment. Explicit rows retain supplied dimensions. The override preserves FPS/native allocation checks. Alignment can add cells beyond the base-grid cap.

Sampling keeps every Nth source frame with `N = ceil(source_fps / fps_limit)`, at least one. A 59.94 source becomes 29.97 under a 30 ceiling; 50 becomes 25. Lower-rate sources are not interpolated. Mode switches recalculate the default FPS; explicit `--fps` persists. Large ASCII grids can be browser text-drawing limited.

`--quality high|balanced|low` controls ASCII temporal color tolerance and is rejected with pixel playback. Use `--dct-quality` for DCT. Even quality 100 is lossy, including chroma subsampling.

## Capability and predictor

DCT requires server codec `dct`, active pixel file playback, and client query `codec=adaptive&sync=1&pixel_codec=dct-v1`. The local SDK and root demo advertise this. Older clients receive legacy/raw. Inspect SDK `init.pixelCodec` and the server `[DCT]` line to know the active transport.

Each session owns a predictor, reset on seek/reinit with a fresh keyframe. Encoding stays ordered. Source decoding, ASCILINE encoding and browser decoding are separate; Rust accelerates the first two and client reconstruction stays JavaScript. Rust/DCT CLI sessions disable WebSocket permessage-deflate; DCT packets already contain zlib compression.

## Source queue and decoder threads

`--decode-ahead 3` creates one source worker only for Rust pixel files, RAW or DCT. It holds at most three prepared/in-progress frames plus the frame being encoded. Three 464x256 BGR payloads are about 1.02 MiB, excluding decoder/array overhead.

The worker owns source reads. Pause fills only the bounded queue. Seek, resize and release stop/join it before repositioning and starting a new queue. It stores no encoded packets or predictors. It overlaps work rather than making source decode cheaper.

`--decode-threads 2` requests two FFmpeg source-decoder threads; `0` requests automatic selection. Actual workers are codec-specific. Omission preserves the default. Python/webcam inputs reject the override. This is independent of queue capacity and DCT encoding; more workers may compete with encoding. Compare timings before choosing.

## Python application API

```python
from asciline import AsciiStreamServer
server = AsciiStreamServer(
    source="video.mp4", engine="rust", pixel=True,
    pixel_codec="dct", dct_quality=70, cols=450, fps=60,
    loop=False, thumbnails=False,
)
server.start()
```

The helper supports engine, representation, grid, FPS, codec, DCT quality and resolution override. Its constructor does not expose `decode_ahead`, `decode_threads` or `perf_record`; use CLI for these. Its defaults differ from CLI: mode 2, loop on, thumbnails off. Do not assume identical source-queue policy.

## Troubleshooting

For a freeze, correlate server delivery, decode backlog, rendered count and clock progress. `BUF: 0` alone can be healthy just-in-time delivery. Record the real session using the [performance guide](PERFORMANCE.md).

For black SDK output, serve modules over HTTP and copy all `src/`, including `live-session.js`. If DCT is unexpectedly raw, inspect capability and INIT. If Rust video works but audio fails, check its recorded FFmpeg runtime and the `/audio` diagnostics. Keep tabs foreground when measuring visible FPS.
