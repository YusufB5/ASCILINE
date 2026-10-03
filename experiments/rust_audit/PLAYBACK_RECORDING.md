# Recording playback while watching

This measures the real server session used by your browser. It does not start a
second decoder or synthetic playback client alongside your viewing session.
Recording is opt-in and does not require rebuilding the Rust library.

## 1. Capture a baseline

From the repository root:

```powershell
python stream_server.py costa_rica_60fps.mp4 --engine rust --pixel --pixel-codec dct --cols 450 --fps 60 --no-thumbnails --perf-record experiments/rust_audit/output/playback
```

Open the root player at `http://localhost:8000/` and refresh it to load the updated
JavaScript. Watch one uninterrupted pass with the browser in the foreground.
Use the same source, resolution, quality, FPS and browser for later comparisons.
The console prints `[RECORD]` with the unique output filename. Every connection
has its own session ID; seek, mode change and playlist transition start separate
segments. Pause/resume and playback-ready events are recorded too.

Then, in a separate recording, try several forward/backward seeks and pause/resume.
If you notice a hitch, note the approximate video time; the report's Video PTS
column and raw events can locate it. The recorder itself cannot identify which
hitches you perceived. Ten-minute endurance testing can follow later.

Stop with **Ctrl+C** or `/quit` to drain the recording and write its footer.
Closing the terminal or killing the process may lose the last buffered records.
The writer flushes about every half second during playback.

## 2. Produce the report

This selects the most recently modified recording in the directory:

```powershell
python experiments/rust_audit/playback_report.py experiments/rust_audit/output/playback
```

It writes sibling `.report.md` and `.summary.json` files. You can also pass an
exact `.jsonl` path, including while the server is running. A running/interrupted
capture is explicitly marked incomplete. Output under `output/` is Git-ignored.

The report contains per-segment mean/p95/p99/max stage timings, over-budget
frames, source skips, delivery lag, the ten slowest frames with video timestamps,
and playback events. p95 means 95% of measured values are at or below that value
(nearest-rank percentile). No interpolation or averages of percentiles are used.

### What each measurement means

- **source_ms:** consumer source work/wait. Without decode-ahead this is sampling,
  decode, resizing and conversion. With decode-ahead this is waiting for a queued
  source frame, not the worker's decode duration.
- **source_work_ms:** actual sampling/decode/resize duration in either path. It
  overlaps encoding when decode-ahead is on, so do not add it to `produce_ms`.
  Neither field isolates FFmpeg decode CPU time. Older recordings omit this field.
- **encode_ms:** frame processing and encoding. In pixel DCT this includes
  `PixelProfile.encode`, native DCT/prediction/compression/reconstruction and
  Python packet preparation; it is not only the mathematical DCT transform.
- **handoff_ms:** time remaining in the executor round trip after source and
  encoding. Includes scheduling delay, not a pure thread-switch measurement.
- **send_ms:** time awaiting WebSocket send, not network round-trip latency.
- **produce_ms:** source + encode + handoff. The report counts frames where
  produce + send exceeds `1000 / effective_fps` as over budget.
- **send_lag_ms:** server playback clock minus sent frame timestamp. Negative
  means ahead. Startup/resume preroll is excluded from these statistics.
- **send_gap_ms:** consecutive sends within active playback ranges. Pause,
  seek and playback-ready boundaries break the sequence. Not browser FPS.
- **client:** real browser reports every 250 ms. Decode/draw times are averages
  since its last frame-pipeline reset, not per-frame samples. Reports include
  decoded/rendered counts, ready-frame buffer, pending-decode depth, late drops,
  errors, master clock, last displayed timestamp and hidden-tab status. Reports
  from an old synchronization epoch are excluded from the summary.

An empty render buffer alone is not proof of a stall. Correlate it with increasing
display lag, delivery gaps and late drops. Display lag uses the player's selected
master clock (audio or fallback wall clock), not a physical A/V sync measurement.
Raw JSONL retains the individual browser reports and source-skip timings.

## 3. Decide whether to try decode-ahead, then compare

First inspect the slow frames, not just averages. If source decode/resize spikes
coincide with lag/skips while encoding has headroom, a bounded 2–3-frame source
queue is worth an isolated experiment. If encode dominates, a source queue alone
will not remove that work. If scheduling/send dominates, investigate that path.
The largest stage is a clue, not causal proof. Wall-clock timers also include
preemption by other processes.

Once an implementation candidate exists, capture the same uninterrupted scene
again, then compare the exact files:

```powershell
python experiments/rust_audit/playback_report.py PATH_TO_BASELINE.jsonl PATH_TO_CANDIDATE.jsonl
```

This prints per-segment p95 values side by side and regenerates both full reports.
It deliberately does not claim a speedup for different scenes/settings. Repeat
in reversed order if the difference is small relative to run-to-run variation.
Accept a queue only if lag/skips improve without stale frames after seek,
unbounded memory, or extra sustained latency. Correct DCT predictor order must
remain intact. Recording alone does not enable the decode-ahead queue.

### Trying the bounded source queue

The experimental queue is opt-in with `--decode-ahead 3` (also supports `2`,
or `0` for the original serial path). It applies only to Rust pixel file playback,
including RAW and DCT. Python, ASCII and webcam decoding keep their existing path.
No Rust library rebuild is needed.

```powershell
python stream_server.py costa_rica_60fps.mp4 --engine rust --pixel --pixel-codec dct --cols 450 --fps 60 --no-thumbnails --decode-ahead 3 --perf-record experiments/rust_audit/output/playback-ahead3
```

One worker owns decoder reads. At most three source frames are queued/in progress,
in addition to the frame being encoded. At 464x256 BGR, three prepared frames hold
about 1.02 MiB of pixel payload (excluding decoder internals/array overhead).
Pause lets the queue fill to its limit. Seek and mode changes stop and join the
worker, discard old prepared frames, reposition/reconfigure the decoder, then
create a fresh queue. Disconnect joins the worker before releasing the decoder.
Source-frame drops consume queue entries without encoding them; DCT prediction
continues from the last transmitted packet.

Pending seek requests now coalesce within a run of seek/telemetry commands.
Pause, mode changes, filters and disconnect remain ordering barriers. A seek
already executing in FFmpeg is not cancelled. `seek_coalesced` trace events show
how many pending targets were superseded. This command handling improvement is
independent of the decode-ahead flag and does not make individual source seeks faster.

Generate the latest viewing report with:

```powershell
python experiments/rust_audit/playback_report.py experiments/rust_audit/output/playback-ahead3
```

## Recording overhead and limits

Per-frame records are queued in memory; JSON serialization and file writes run
on a separate thread. The queue is bounded to 8,192 records. If storage cannot
keep up, diagnostic records are dropped instead of blocking playback, and the
footer reports that loss. Sequence gaps and missing footers produce warnings.
A write error stops recording with a warning, leaving playback running.
Recording still consumes some CPU and memory; compare short captures with and
without it before attributing small differences to playback changes.

Only timing/configuration metadata is saved, not image/audio payloads. The source
basename and platform/Python version are included. Browser draw/audio must be
measured with an actual viewing session; `live_probe.py` is delivery-only.

Developer smoke test (starts and closes only its own server):

```powershell
python experiments/rust_audit/live_probe.py --serve-video videos/costa_rica_60fps.mp4 --dct --cols 450 --fps 60 --seconds 23 --label trace-on --perf-record experiments/rust_audit/output/playback-validation
```

## Initial validation — 2026-10-03

Four sequential 23-second delivery-only runs at 464x256/Q70/59.94 FPS:

| Order | Recording | Delivered FPS | Source skips | Delivery lag p95 |
| --- | --- | ---: | ---: | ---: |
| 1 | on | 57.59 | 51 | 30.3 ms |
| 2 | off | 59.12 | 17 | 20.3 ms |
| 3 | off | 58.88 | 20 | 26.9 ms |
| 4 | on | 59.13 | 15 | 27.6 ms |

The first run had a 113 ms source-stage spike; the repeat's source maximum was
59 ms. This variation does not establish zero recording overhead or a causal
regression. In both recorded runs, source decode/resize was the largest stage
on almost all over-budget frames (247/265 and 244/245). In the repeat, source
p95 was 27.28 ms versus encode p95 7.78 ms; means were 6.06 and 6.54 ms.
That supports investigating source bursts before further DCT arithmetic work.
It does not prove a 2–3-frame queue can hide every burst or preserve latency.

Both recordings closed with zero dropped diagnostic records. These probes did
not run browser decoding/Canvas/audio. Actual viewing capture is still needed
to correlate server spikes with browser symptoms. Logs and reports are retained
under the ignored `output/` directory.

## Decode-ahead comparison — 2026-10-03

One full-video sequential A/B pair, same Costa Rica source, 464x256/Q70/59.94 FPS,
recording enabled on both. These are local WebSocket delivery measurements,
excluding browser decode/Canvas/audio. Seek coalescing does not affect this
uninterrupted probe. No source resolution or quality settings were reduced.

| Metric | Serial (`0`) | Three source frames (`3`) |
| --- | ---: | ---: |
| Post-preroll frames delivered | 3,554 | 3,593 |
| Duration | 59.998 s | 60.001 s |
| Mean delivered FPS | 59.24 | 59.88 |
| Source skips | 39 | 0 |
| Receiver delivery lag p95 | 31.3 ms | 10.8 ms |
| Receiver delivery lag maximum | 103.4 ms | 21.5 ms |
| Consumer source wait/work p95 | 24.48 ms | 0.02 ms |
| Production + send over-budget frames | 631 | 28 |

The queued worker's actual source work still had a 27.27 ms p95: work has been
overlapped, not made free. Encoding p95 was 8.19 ms serial and 9.20 ms queued,
consistent with possible resource contention; total consumer production p95
in the queued run was 9.99 ms. The flag stays off by default pending actual
browser testing and longer runs. A single pair does not establish a universal
speedup or guarantee every stall is eliminated.

Reproduce separately, without other benchmark/test processes competing for CPU:

```powershell
python experiments/rust_audit/live_probe.py --serve-video videos/costa_rica_60fps.mp4 --dct --cols 450 --fps 60 --seconds 65 --decode-ahead 0 --label ahead0 --perf-record experiments/rust_audit/output/decode-ahead
python experiments/rust_audit/live_probe.py --serve-video videos/costa_rica_60fps.mp4 --dct --cols 450 --fps 60 --seconds 65 --decode-ahead 3 --label ahead3 --perf-record experiments/rust_audit/output/decode-ahead
```

Validation covers bounded preparation during pause, join-before-seek/release,
worker errors/EOF, 30/60 FPS source sampling/catch-up, DCT packet and browser
decoder truth, repeated/paused seeks, mode changes, reconnect and a 20-request
seek burst ending at the correct frame. Old playback-ready epochs cannot release
the newest preroll. The recorder tests isolate sessions when reusing a server.
