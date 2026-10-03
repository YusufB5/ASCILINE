# Source decoder stages — 2026-10-03

## Finding

The measured bursts are primarily inside source codec receive/send calls.
Resizing and source-format-to-BGR conversion together cost less than 1 ms on
average at the tested grid. Packing the scaled rows into a contiguous BGR buffer
is smaller still. Changing the resize algorithm is not the first optimization
supported by these measurements.

These are source-video decoding costs, separate from ASCILINE DCT encoding and
browser-side DCT decoding. The source queue previously reduced consumer waiting
by overlapping source work with encoding; this experiment locates that work.

## Method

`source_stage_probe.py` copies the Rust crate to ignored `output/source-stages`,
instruments only the copy's decoder, and builds a separate DLL. Production Rust
sources, manifest, lockfile, runtime configuration and DLL are not changed.

Normal / instrumented / normal passes use the same Costa Rica source, coded grid
464x256, pixel mode (no grayscale generation), DCT quality 70, and three windows
of 180 consecutive frames starting at 5, 18 and 30 seconds. Each window opens and
seeks a fresh decoder and creates a fresh DCT encoder. The first retained seek
frame excludes the work already done by seek. There is no realtime pacing,
decode-ahead queue, frame skipping or browser in this stage experiment.

All 540 source BGR frames, DCT packets and reconstructed images matched by
concatenated SHA-256 per window across all three passes. Timers are wall-clock
measurements, including scheduling and any internal FFmpeg worker waits; they
are not isolated CPU utilization measurements.

### Mean milliseconds per output frame

| Stage | Window 5s | Window 18s | Window 30s |
| --- | ---: | ---: | ---: |
| Demux / packet reads | 0.0324 | 0.0373 | 0.0310 |
| Source codec receive/send | 4.2489 | 6.0276 | 3.1318 |
| Scale + color conversion | 0.7935 | 0.8263 | 0.7476 |
| Contiguous BGR packing | 0.0922 | 0.0922 | 0.0685 |
| Boundary and unattributed work | 0.0261 | 0.0277 | 0.0238 |
| Entire source call | 5.1931 | 7.0111 | 4.0027 |

Across equally sized windows, codec calls account for approximately 83% of
measured source-call time, scale/conversion 15%, and packing 1.6%.

The scale timer includes scaler initialization when necessary, scaled-frame
allocation, and `sws_scale` via `Scaler::run`. Scaling and color conversion are
fused in that call, so this does not claim separate costs for those operations.
Packing includes allocation, row copies and optional mirror/grayscale work
(both optional transformations are disabled here). Boundary remainder includes
destruction, mutex/GIL transitions, Python array wrapping and timer overhead;
it must not be presented as a pure Python-copy measurement.

### Tails

| Stage p95 (ms) | Window 5s | Window 18s | Window 30s |
| --- | ---: | ---: | ---: |
| Source codec receive/send | 16.1352 | 30.2125 | 10.3073 |
| Scale + color conversion | 0.9860 | 1.1485 | 0.8933 |
| Entire source call | 17.0898 | 31.2428 | 11.1089 |

For example, a 59.27 ms source call in the 18s window spent 56.33 ms in codec
calls, 2.61 ms in scale/conversion, 0.21 ms packing and 0.05 ms in demux reads.
This directly attributes a slow sample instead of inferring the cause solely
from a window average.

Normal source means before/after profiling were 5.5426/5.2025, 7.4126/6.5614,
and 3.9968/3.9950 ms. Variation between runs limits precise overhead estimates.
No general speedup, all-device result or browser FPS follows from this profile.

## Next experiment

Inspect the source decoder actually selected by FFmpeg and its threading
configuration; test a small, bounded set of decode concurrency settings. Keep
the source queue, output grid, color conversion and DCT quality fixed. Verify
identical source frames and DCT output, then compare full live delivery. More
threads may contend with DCT encoding or increase internal buffering; accept
only measured improvements. The current data does not justify changing image
quality or switching the resize algorithm as the primary optimization.

## Reproduction and fresh-build compatibility

For a supported stable FFmpeg development installation:

```powershell
python experiments/rust_audit/source_stage_probe.py --build
```

The local development snapshot `ffmpeg-N-124953-gd30dead35e` exposes nine newer
enum entries not handled by cached `ffmpeg-next 8.1.0`. A clean dependency build
failed in frame-side-data, packet-side-data and codec-ID conversions, despite
the existing production DLL working. This is a reproducible-build issue to
resolve before release, independent of playback profiling.

For this specific snapshot the probe provides an **explicit isolated** option:

```powershell
python experiments/rust_audit/source_stage_probe.py --build --nightly-compat
```

It copies ffmpeg-next into the probe's own vendor directory and adds the nine
enum variants and both conversion directions. No wildcard/panic fallback is
introduced; no registry files or production dependencies are patched. The
additional mappings do not change the tested video's decode operations, and
the output hash comparison checks agreement with the installed normal DLL.
This is a profiling workaround, not a production compatibility solution.
The option is specific to those headers; do not use it for older stable headers.

After building, repeat measurements without compilation:

```powershell
python experiments/rust_audit/source_stage_probe.py --nightly-compat
```

Per-frame samples, summaries, source hashes and the compatibility option are
retained under `output/source-stages/`. Check `comparison.json` and
`1-profile.json` for full precision and the slowest samples.
