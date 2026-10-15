# Live Rust/DCT profiling — 2026-10-01

## Conclusion

At 464x256/Q70 the main costs are source video decoding/resizing and native
DCT encoding, which currently execute serially. Inside the encoder, motion
search/prediction is the largest measured stage, followed by BGR-to-YUV420
conversion. The actual DCT transform and zlib are smaller costs. Prior user
logs showed client decode averages around 2–4ms and no reported decode queue.
There is no evidence here to prioritize a browser decoder rewrite.

## Method and limits

`stage_probe.py` copies the current Rust sources into an ignored workspace
directory and adds stage timers to the copy. It builds a separate DLL; neither
the production Rust source nor its DLL is replaced. Each experiment executes
normal / instrumented / normal passes, with 180 consecutive frames at each of
5, 18 and 30 seconds of `videos/costa_rica_60fps.mp4`. There are 540 frames per
pass, starting a fresh encoder in each window. Output packet and reconstructed
BGR hashes agree for every window across all passes.

These are wall-clock timings on this machine, with scheduling/concurrent-load
effects. Per-block timers also add overhead: the original instrumented encoder
averaged 12.14–14.52ms versus 10.38–14.38ms across the normal passes. Stage values
identify where time goes; they are not a promise of the exact savings attainable
in production. Source timing includes resizing/output conversion. Short windows
with fresh encoders do not reproduce the full live predictor history or skips.

## Original encoder stage means (ms per frame)

| Stage | Start 5s | Start 18s | Start 30s |
| --- | ---: | ---: | ---: |
| BGR → YUV420 | 3.544 | 3.160 | 3.403 |
| Motion search and prediction | 6.046 | 6.124 | 4.862 |
| Residual, DCT, quantization, skip decision | 1.535 | 1.652 | 1.509 |
| IDCT/reconstruction and zigzag collection | 0.563 | 1.033 | 0.412 |
| RLE and plane packing | 0.143 | 0.280 | 0.096 |
| zlib | 0.212 | 0.651 | 0.189 |
| Reconstructed BGR output | 0.633 | 0.565 | 0.600 |
| Unattributed, including allocations, copies and timer overhead | 1.383 | 1.051 | 1.065 |

Normal source decode/resize means were 4.95–6.02ms (5s window), 6.33–6.71ms
(18s), and 3.80–4.04ms (30s). Source p95 was materially higher than its mean;
timing spikes also matter for a 16.68ms frame budget.

## Real WebSocket cross-check

The server's `[PERF]` line now splits production into
`source/encode/handoff/send` average milliseconds:

- `source`: source sampling/grab, decode and output resizing/conversion.
- `encode`: filters, frame construction and encoding (DCT in this test).
- `handoff`: the remaining executor dispatch/return time, including scheduling.
- `send`: awaited WebSocket send time; this is not network round-trip latency.

In a separate 23-second isolated CLI run at 450 columns/60 target FPS, heavy
windows measured source 5.8–8.1ms, encoding 11.9–13.9ms, handoff 0.2–0.3ms and
send about 0.1ms. The probe received 1,206 post-preroll packets in 23.021s
(52.39 FPS), p95 delivery lag 41.9ms, maximum 84.9ms. It did not run browser
decoding/Canvas/audio and does not characterize Internet performance.

## Initial isolated optimization candidate

In `bgr_to_yuv` and `sub_2x2`, two values already clamped to 0..255 use floating
point `trunc` before being represented as f64. Converting through u8 performs
the same truncation in this range. Only the isolated copy was changed:

```rust
y[i] = (y_val as u8) as f64;
out[y * hw + x] = (val as u8) as f64;
```

With the same stage instrumentation, color-conversion means became
1.853/1.991/1.375ms for the three windows, down from 3.544/3.160/3.403ms. All 540
encoded packets and reconstructed frames matched the normal encoder by hash.
This is a promising roughly 1–2ms stage saving in these samples; timer overhead
and machine variation mean a production build still needs a fresh comparison.
At that stage it was isolated and did not establish 60 FPS. It has since been
integrated alongside the motion-search change described below.

## Implementation order

1. Apply and validate the exact color-conversion simplification, then consider
   avoiding full-resolution temporary chroma planes while preserving arithmetic
   order. Low-risk first gain; run Python/native/JS contract tests and live seeks.
2. Optimize motion search SAD (sum of absolute pixel differences). Currently
   candidate loops repeatedly convert integral f64 block values to i32 and branch
   after each pixel. Precompute integer blocks and evaluate SIMD/row-wise sums.
   Preserve candidate order, zero-motion tie preference, edge extension and exact
   predictor selection. Do not reduce the search radius or quality to claim an
   equivalent speedup.
3. Offer a packet-only native encode path for live streaming. Keep reconstructed
   YUV predictor planes, but avoid generating/copying the BGR `shown` output that
   `PixelProfile.encode` immediately discards. Measured BGR work is about 0.6ms.
4. If source decode spikes still prevent the target, evaluate a small bounded
   decode-ahead queue. Overlapping source decode with ordered DCT encoding could
   reduce their summed critical path, but requires seek/pause/reset epoch tests,
   cancellation, bounded memory and last-transmitted-predictor correctness.

Reproduce:

```powershell
python experiments/rust_audit/stage_probe.py --build
python experiments/rust_audit/stage_probe.py --build --color-casts
python experiments/rust_audit/live_probe.py --serve-video videos/costa_rica_60fps.mp4 --dct --cols 450 --fps 60 --seconds 23
```

The ignored `output/stages/comparison.json` and `comparison-color-casts.json`
contain full mean/p95/max data and hashes. Rebuild before switching candidates;
the isolated crate is reused. Live logs are under `output/delivery-450-60.log`.

## Integrated color conversion and motion search

Both improvements are now in `rust_core/src/dct.rs` and the local release DLL:

- Use exact integer truncation for clamped color-conversion values.
- Convert each motion-search block to bytes once. On x86_64, SSE2 SAD computes
  eight absolute byte differences together; other architectures use scalar rows.
  Bound checks cover the complete last row before unaligned 8-byte loads.
- Stop losing candidates at row boundaries instead of per pixel. Since all
  differences are nonnegative, this preserves the winning candidate. Search
  radius, candidate traversal, strict comparison and zero-motion tie preference
  remain identical. No quality setting or frame resolution was reduced.

Release DLL comparison, with **no added stage timers**, used the same three
180-frame windows. Old / color-only / final / old passes ran sequentially.
Old values below average the two old runs. All 540 packet+reconstruction hashes
matched across the four variants/runs.

| Window starts at | Old DCT mean | Color-only mean | Final DCT mean | DCT speedup |
| --- | ---: | ---: | ---: | ---: |
| 5s | 11.887ms | 10.459ms | 6.387ms | 1.86x |
| 18s | 13.909ms | 12.166ms | 7.600ms | 1.83x |
| 30s | 10.777ms | 8.803ms | 5.409ms | 1.99x |

Final source-decode + encode means were 11.29/13.90/9.23ms. Their p95 values
remain above a frame budget in some windows because source decode is bursty.
Encoder speedup does not by itself certify browser playback FPS.

Validation: 44 Python tests covering native operations, Python/native/JS DCT
agreement, actual CLI/WebSocket startup, pause, seek, reconnect, mode changes and
producer catch-up; one Rust test compares SIMD to scalar across 1,152 combinations
of offsets/strides/values/cutoffs plus zero/maximum cases; seven root-player
timing tests and the SDK import smoke check passed. The original Python/JS DCT
algorithms and the wire protocol were unchanged.

Reproduce release comparisons with saved DLLs:

```powershell
python experiments/rust_audit/compare_native.py --baseline experiments/rust_audit/output/optimization/baseline/_asciline_native.dll --intermediate experiments/rust_audit/output/optimization/color/_asciline_native.dll --candidate rust_core/target/release/_asciline_native.dll
```

`output/optimization/comparison.json` retains binary hashes and all per-scene
timings/hashes. Saved binaries are local ignored artifacts. `stage_probe.py`
also accepts `--child baseline --binary <path> --report <path>` for a single
uninstrumented measurement. The earlier `--color-casts` experiment is now
idempotent because the casts are part of the normal source.

### Full-video live delivery comparison

The old saved DLL and new installed DLL were then run sequentially through the
same CLI/WebSocket probe, at 450 columns (coded 464x256), quality 70 and nominal
59.94 source FPS. Both runs reached the end of the 60-second clip.

| Metric | Before | After |
| --- | ---: | ---: |
| Post-preroll delivered frames | 3,144 | 3,532 |
| Measurement duration | 60.038s | 59.990s |
| Mean delivered FPS | 52.37 | 58.88 |
| Observed source-index skips | 449 | 61 |
| p95 delivery lag | 54.0ms | 26.2ms |
| Maximum delivery lag | 93.1ms | 94.4ms |
| Packets arriving over 100ms late | 0 | 0 |

This shows improved server throughput and usual latency, with occasional source
decode/scheduling spikes still present. It is a local delivery test without
browser decode/draw/audio. Different source-frame skips also mean live predictor
histories differ; exact-output verification uses the identical-input offline
windows above, not a byte comparison of these two different live frame sequences.

```powershell
python experiments/rust_audit/live_probe.py --serve-video videos/costa_rica_60fps.mp4 --dct --cols 450 --fps 60 --seconds 65 --native-binary experiments/rust_audit/output/optimization/baseline/_asciline_native.dll --label before
python experiments/rust_audit/live_probe.py --serve-video videos/costa_rica_60fps.mp4 --dct --cols 450 --fps 60 --seconds 65 --label after
```

The probe cleans up only the process it started. Separate logs are retained in
`output/delivery-450-60-before.log` and `output/delivery-450-60-after.log`.
