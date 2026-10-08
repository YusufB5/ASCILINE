# Source decoder concurrency — 2026-10-04

> Historical experiment report, recovered on 2026-10-08. Results below retain their original runtime/date. The isolated `decoder_threads_probe.py` mentioned here is not included in this checkout. For current supported CLI comparisons and recording, use [the performance guide](../../docs/PERFORMANCE.md); current SDK/build status is in [the documentation index](../../docs/README.md).

## Finding

The tested AV1 source opens with **libdav1d, thread_count=1** in the current
native decoder. Explicit automatic threading (`thread_count=0`) substantially
reduces the time the caller waits for a source frame in this experiment.
The source decoder has useful latency headroom; the earlier 83% source-codec
share did not mean that the decoder was already using the machine efficiently.

The measurements below used an isolated experiment. The subsequent opt-in
integration is documented at the end; server thread defaults and SDK remain
unchanged.

## Controlled source + DCT experiment

`decoder_threads_probe.py` builds a separate crate under ignored
`output/decoder-threads`. It changes only `AVCodecContext.thread_count` before
opening the decoder. FFmpeg's existing thread-type flags remain unchanged.
The diagnostic getter returns codec name, requested thread count, thread-type
flags and active FFmpeg thread type. For libdav1d, active type is zero because
the codec manages its own workers; this is not evidence of single-threaded
decoding. Auto's count of zero is a configuration value, not an actual worker
count. See the [FFmpeg libdav1d adapter](https://ffmpeg.org/doxygen/trunk/libdav1d_8c_source.html).

Input: Costa Rica AV1, 1280x720, 59.94 FPS. Output: 464x256, BGR, pixel DCT Q70.
Each pass opens and seeks fresh decoders at 5, 18 and 30 seconds, then consumes
180 consecutive frames from each. Decode and DCT API calls are sequential;
internal decoder workers may overlap with DCT work. There is no external
decode-ahead queue or realtime pacing in this part of the experiment.

Order: installed DLL, default, auto, 1, 2, 4, 8, then the reverse order back to
the installed DLL. All **7,560** frames have matching per-window concatenated
hashes of BGR bytes, DCT packets, reconstructed images and source timestamps.
No quality reduction, frame skipping or timestamp changes were used.

Pooled results from two passes of each setting (1,080 frames per row):

| Decoder setting | Source mean ms | Source p95 ms | Source max ms | DCT mean ms |
| --- | ---: | ---: | ---: | ---: |
| Installed DLL | 5.519 | 20.673 | 63.185 | 7.109 |
| Copied DLL, unchanged default | 5.491 | 21.061 | 65.140 | 7.002 |
| Explicit 1 | 5.472 | 19.837 | 65.804 | 6.972 |
| Explicit 2 | 1.968 | 6.332 | 38.914 | 7.460 |
| Explicit 4 | 2.336 | 6.011 | 14.104 | 7.585 |
| Explicit 8 | 2.405 | 4.433 | 10.024 | 7.579 |
| Automatic (0) | 1.938 | 3.841 | 7.852 | 6.728 |

Auto reduces measured source-call mean by about 65% and p95 by about 82%
relative to the copied default. These are wall-clock call times, including
scale/color/packing. They are **not** reductions in total CPU work or energy.
Hashing occurs outside the timers and also gives internal decoder workers time
to progress. Repeat ordering limits, but does not eliminate, machine-load and
thermal effects. More workers are not monotonically better in the fixed-count
results. Do not infer universal optimal settings from this one source/machine.

## Reproduction

### Live delivery results

Four sequential full-source runs, in the order below. Every run delivered
3,593 post-preroll frames plus four preroll frames, had zero source skips,
zero samples more than 100 ms late, and a complete trace with zero dropped
diagnostic records. Delivery rate stayed between 59.86 and 59.89 FPS.

| Setting | Source work mean / p95 ms | Queue wait max ms | DCT mean ms | Over-budget frames | Receiver lag p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| Default, before | 5.82 / 22.20 | 34.57 | 6.17 | 14 | 11.4 |
| 2 threads | 1.97 / 6.53 | 0.03 | 6.48 | 0 | 32.3 |
| Automatic | 2.41 / 4.65 | 0.03 | 7.30 | 4 | 8.8 |
| Default, after | 6.23 / 23.52 | 37.89 | 6.63 | 23 | 4.5 |

Over-budget means `produce_ms + send_ms > 16.68 ms`; it does not mean a visibly
dropped frame. Source work overlaps DCT encoding and must not be added to
production time. All 14 slow frames in the first default run were source-wait
dominated; 22 of 23 in the second were source-wait dominated. Auto's four slow
frames were encode dominated. Two threads removed budget overruns in this run,
but receiver lag was higher, so this is not a universal end-to-end latency win.
Receiver clock/startup offsets and normal run variability also affect those
figures. There is only one live run per candidate, bracketed by two defaults.

Conclusion: decoder concurrency removes the observed source stalls at this
grid in this workload, while the existing queue already sustained near-60 FPS.
Two threads are a promising bounded candidate; automatic selection has lower
source-tail time but higher DCT time in this live sample. Keep both selectable
for further comparison instead of declaring auto globally optimal. CPU/energy,
other source codecs, browser A/V behavior and long-duration operation remain
unmeasured here. The next integration should expose a decoder thread option
with validation, retain the existing default initially, and resolve the stable
FFmpeg build before updating the production DLL.

Raw live results: `output/decoder-threads/live-20261004T145600/`.

### Commands

```powershell
python experiments/rust_audit/decoder_threads_probe.py --build --nightly-compat
python experiments/rust_audit/decoder_threads_probe.py --nightly-compat --live
```

Omit `--nightly-compat` for supported stable headers. Its explicit isolated
dependency workaround and the still-open production fresh-build issue are
described in `SOURCE_STAGE_FINDINGS.md`. The experiment does not resolve that
release issue.

`output/decoder-threads/comparison.json` retains per-frame samples and hashes.
The live mode starts and cleans up its own servers sequentially on free ports,
using queue depth 3, 450 requested columns (464x256 coded), Q70 and 60 FPS.
It compares default / 2 / auto / default over the entire short source and
records server traces and receiver logs in a timestamped folder. This measures
WebSocket delivery, not browser rendering or actual audio synchronization.

## Opt-in integration after the experiment

`--decode-threads N` is now available on the CLI, with `None` (omitted) retaining
FFmpeg's default, zero selecting auto and 1–64 selecting a fixed requested count.
Python fallback, webcam and out-of-range values are rejected explicitly. Native
capability detection gives a rebuild message for older DLLs. Source decoder
name and configured count are logged and included in playback traces.

The fresh build succeeded without vendor modifications using stable BtbN
FFmpeg `n8.1.3-14-g330caae0c1-20261003` and the unchanged Cargo lockfile. The
nightly dependency workaround is not part of the installed production module.
Runtime libraries live under ignored `rust_core/.deps/ffmpeg-8.1/`; the previous
DLL and runtime configuration were retained in `.deps/pre-decode-threads/`.
See `rust_core/README.md` for the tested package checksum and build instructions.

Across three 180-frame AV1 windows, each of omitted / auto / two threads matched
the previous DLL's BGR, DCT packet and reconstruction hashes: 1,620 frames total.
The default native thread count remained 1 for this video. This comparison
checks correctness across the runtime change, not timing (a build ran during
part of reference capture). MPEG-4 B-frame drain and repeated seek correctness
also passed with 0/1/2/4 requested threads. Real-server DCT mode/reconnect/seek
tests cover two threads and auto with queue depth 3.

The earlier performance numbers were collected with the former nightly runtime;
they must not be relabeled as measurements of the stable runtime. Browser A/B
testing and long-duration validation are still needed for a default change.

## User browser comparison with the stable runtime

Recordings (2026-10-04 UTC):

- Two threads: `playback-threads2/playback-20261004T123436-26c6241b.jsonl`
- Default: `playback-default/playback-20261004T123610-d4978ee1.jsonl`
- Auto: `playback-threads-auto/playback-20261004T123741-9a14abe0.jsonl`

All are under `output/`, with complete footers, no diagnostic drops or sequence
warnings. Configurations agree: libdav1d, 464x256, Q70, 59.94 FPS, queue depth 3;
recorded counts are respectively 2, 1 and 0. User reported little visual
difference. Each run includes pause/resume and seeks, but targets differ.

### Whole recordings (different scene coverage)

| Metric | Default | Two threads | Auto |
| --- | ---: | ---: | ---: |
| Post-preroll frames | 2480 | 2020 | 2015 |
| Over-budget production + send | 14 (0.565%) | 1 (0.050%) | 1 (0.050%) |
| Source work mean ms | 6.195 | 2.773 | 2.594 |
| Source work p95 ms | 25.622 | 10.221 | 4.796 |
| Queue wait maximum ms | 42.608 | 0.067 | 0.056 |
| Encode mean ms | 6.541 | 7.831 | 7.531 |
| Server send-lag p95 ms | 8.783 | 9.267 | 9.650 |
| Current-epoch browser samples | 162 | 133 | 132 |

All server source-skip counts are zero. All sampled browser late-drop and decode
error counters are zero, pending decode depth is zero, and no sample is hidden.
Default's 14 budget overruns are source-wait dominated; the single overrun in
each parallel run is encode dominated. These are production budget overruns,
not observed dropped display frames. Browser counters are sampled every 250 ms
and cannot exclude every transient stall or prove physical A/V synchronization.

### Matched initial scene

To control scene coverage, compare segment 1, frame indices 4 through 566:
563 identical source timestamps in every recording, before the first seek.
Runs are still sequential, not repeated controlled CPU-load experiments.

| Metric (ms) | Default | Two threads | Auto |
| --- | ---: | ---: | ---: |
| Source work mean | 5.741 | 2.765 | 2.651 |
| Source work p95 | 22.652 | 9.246 | 5.073 |
| Encode mean | 6.661 | 7.650 | 7.345 |
| Production mean | 7.147 | 8.123 | 7.691 |
| Production maximum | 22.838 | 15.189 | 16.593 |
| Server send-lag p95 | 7.333 | 10.633 | 9.583 |

Conclusion: parallel decoding reduces source waiting and rare production
spikes; it does not reduce mean production time or show an end-to-end latency
win here. The queue already hides most source cost at this resolution, explaining
the similar visual experience. Encode time rises even in the matched scene;
CPU contention is a possible explanation, not proven without CPU measurements.
Auto has the shorter source tail in this sample, while both parallel choices
leave substantial frame-budget margin. Keep the override optional and preserve
the default. No further decoder micro-optimization is justified by this playback
test alone. Long-duration and SDK validation remain the next practical checks.
