# Performance, measurements and tuning

ASCILINE provides a custom representation pipeline: source video to programmable characters/pixels, ordered encoding, WebSocket delivery, JavaScript reconstruction and Canvas drawing. Rust and live DCT make this usable near 60 FPS at the tested small pixel grid. They serve the representation goal rather than a requirement to replace standard codecs.

## Measured changes

| Change | Evidence | Interpretation |
| --- | --- | --- |
| Rust DCT conversion + SIMD motion search | Three identical 180-frame windows: mean 10.78–13.91 ms became 5.41–7.60 ms | About 1.83–1.99x faster encode; matched packet/reconstruction hashes |
| Full-video delivery after optimization | Mean 52.37 to 58.88 FPS; source-index skips 449 to 61 | Server/receiver improvement on a 60-second clip, not browser render FPS |
| Source-stage profiling | Codec receive/send about 83% of source-call time; scale/conversion about 15% | Dominant source cost was decoding; 83% is neither a speedup nor total-pipeline share |
| Bounded decode-ahead | Owner's first segments: 320/1845 over budget without queue, 19/2742 with depth 3 | Fewer production overruns in separate runs with differing scene coverage |
| Decoder threads | Matched 563-frame scene: production mean 7.147 ms default, 8.123 ms two threads, 7.691 ms auto | Source work faster, encode slower; no demonstrated average end-to-end latency win |

Historical methods/results:

- [DCT optimization](../experiments/rust_audit/PERFORMANCE_FINDINGS.md).
- [Source stages](../experiments/rust_audit/SOURCE_STAGE_FINDINGS.md).
- [Decoder concurrency](../experiments/rust_audit/DECODER_THREAD_FINDINGS.md).
- [Standard-codec pilot](../experiments/rust_audit/CODEC_BENCHMARK.md).

These measurements have differing dates, runtimes and methods; do not pool them into a headline speedup. Owner decode-ahead counts above are reported terminal summaries, not controlled identical-scene repetitions. Over-budget production need not mean a visible dropped frame.

At 60 FPS the budget is about 16.67 ms. Without queue, source decode/resize and encoding are sequential. With queue, source work overlaps encoding; consumer queue wait and encode form the critical path. Adding worker source time to production would double-count overlap.

## Bandwidth and codec comparison

RAW live pixels send three BGR bytes per cell plus a four-byte frame index. At 450x253 and 60 FPS pixel payload is about 20.5 MB/s (164 Mbit/s). At coded 464x256 the same raw reference is about 21.4 MB/s at 60 FPS. This describes representation bytes, not MP4 bitrate.

The three-window software pilot measured ASCILINE Q70 at 1.915 Mbit/s aggregate payload, RGB PSNR 35.37 dB and RGB SSIM 0.9356. All encoders received the same 464x256 predecoded BGR frames at 60000/1001 FPS: 540 unique frames. Audio and network overhead were excluded. Raw-to-DCT savings describe lossy coding of that source at that grid, not universal bitrate or equal-quality efficiency.

The chosen VP9/AV1 realtime presets produced higher PSNR/SSIM at roughly half the payload in this pilot. H.264 used OpenH264, not x264. Hardware playback, energy and broad content were not measured. Use this evidence to quantify representation costs; it does not establish a standard-codec advantage for ASCILINE.

## Record what you watch

```bash
python stream_server.py video.mp4 --engine rust --pixel --pixel-codec dct --cols 450 --fps 60 --no-thumbnails --decode-ahead 3 --perf-record experiments/rust_audit/output/playback
```

Open root player or SDK example, watch, then stop with Ctrl+C. Recording measures that session; it starts no second decoder. Each connection has a session ID; seek/reinit/source transitions produce segments. Empty segments may reflect interrupted startup/control activity.

```bash
python experiments/rust_audit/playback_report.py experiments/rust_audit/output/playback
```

A directory selects its latest recording by modification time. For comparison use exact JSONL files. The reporter writes sibling Markdown and summary JSON. Output is ignored; interrupted/running captures are marked incomplete. The [recording reference](../experiments/rust_audit/PLAYBACK_RECORDING.md) explains fields and reports.

| Metric | Meaning |
| --- | --- |
| `source_ms` | Serial decode/resize, or consumer queue wait with decode-ahead |
| `source_work_ms` | Actual source work; overlaps encode with queue |
| `encode_ms` | Frame processing/encoding/packet preparation, not only DCT transform |
| `handoff_ms` | Remaining executor/scheduling time |
| `send_ms` | Awaited send duration, not network RTT |
| `produce_ms` | Consumer source + encode + handoff |
| `send_lag_ms` | Server playback clock minus sent frame time |
| Client decode/draw averages | Since current pipeline reset, reported periodically |
| Client late drops | Draw-late reconstructed frames, distinct from decode error |
| Display lag | Client master clock minus last drawn timestamp |

Over-budget means production + send exceeds the effective frame interval. p95 is a nearest-rank percentile, not an average. Client reports are sampled about every 250 ms. `BUF: 0` alone neither proves a stall nor a browser limit. Clock/display lag is not physical A/V skew.

## Evidence-led tuning

1. Hold source, grid, quality, FPS, browser and foreground tab fixed.
2. Source spikes: compare queue 0/2/3; verify predictor/seek correctness and bounded memory.
3. With queue effective: compare omitted/2/auto threads; less source wait can coincide with slower encode.
4. Encode bottleneck: vary columns, quality or FPS individually. Describe reduced detail as a trade-off, not same-quality speedup.
5. Client bottleneck: inspect decode/draw/drops. Server threads cannot accelerate JavaScript reconstruction or ASCII font drawing.
6. Compare exact recordings and reverse run order when differences are small.

Defaults remain Python, RAW, decode-ahead off and codec-default threading. The verified example opts into Rust/DCT/queue explicitly. Each socket owns its processing; one-client results do not establish multi-client capacity. Long-video validation remains an owner-controlled final check.
