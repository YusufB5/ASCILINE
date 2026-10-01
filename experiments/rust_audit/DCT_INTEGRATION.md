# Rust + live DCT: reference and integration decision

Date: 2026-10-01

## Decision

Keep the main checkout's `codec.py::ProfileEncoder` and `codec.js` tag-4
decoder as the reference. Build the live session integration against this
contract. Do not merge the experimental branch's server/player wholesale.
The user observed large green/pink block corruption in that branch; it is
reference material, not a validated implementation.

The Rust DCT source is now compiled into `_asciline_native` as `ProfileEncoder`.
Its reconstructed reference planes match the JavaScript decoder in the tests
below. Raw pixel remains the default; DCT is an explicit pixel-only option
with client capability negotiation.

## Verified reference

`python -m pytest test/test_profile_contract.py -q` passes three cases, with
204 decoded frames total. The checker executes the root browser `codec.js`
directly, rather than relying on the separate CommonJS copy.

Coverage: QF 20/70/95, BGR primaries/secondaries, black/white, 2x2 chroma
boundaries, moving texture, static blocks, the periodic keyframe at frame 48,
decoder reset with nonzero timeline indices, and changed dimensions.
Every decoded byte matches the encoder's intended lossy reconstruction.
This proves encoder/decoder agreement for these cases, not lossless fidelity
to the original or real-browser playback performance.

The existing `experiments/profile_vectors.py` + `experiments/check_profile.cjs`
checks also pass 40 frames and concurrent independent decoders.

## Concrete differences found before integration

1. `rust_core/src/dct.rs::idct_8x8` divides signed integers with `/ 4096`.
   Main Python uses floor division and JavaScript uses `Math.floor`.
   Negative results differ: `-4097 / 4096` truncates to -1 in Rust but floors
   to -2 in Python/JS. This can make the encoder's reconstructed predictor
   differ from the decoder's and accumulate errors in predicted frames.
   Fixed with Euclidean division. This is not evidence that it caused the
   user's experimental-branch corruption.
2. The inactive Rust prototype also uses different source conversion rounding,
   coefficient rounding, motion search radius (2 versus 3), and motion-vector
   tie handling. These require separate correctness/compatibility checks;
   differing encoder decisions alone do not necessarily invalidate a packet.
3. In `ASCILINE-main-dct-yeni/stream_server.py`, the profile encoder returns
   a packet bearing its own local counter, and `produce(..., fi)` forwards
   that packet unchanged. Resetting the encoder on seek restarts the counter
   at zero; source-frame shedding also makes the local count diverge from
   the stream index. This is a concrete timing defect, not yet a reproduction
   of the reported chroma artifacts.

## Integration requirements

- Keep DCT math and tag-4 layout compatible with main, with a separately tested
  native implementation. Compare JavaScript output against the native
  encoder's reconstructed frame for every test frame, including long P chains.
- Store each client's predictor state in its own session. Reset it on seek,
  reconnect, dimensions/quality change and mode change. Start again with a
  keyframe and keep existing seek generation/ready handshakes.
- Use the playback timeline's frame index in the packet header; periodic
  keyframe scheduling may have a separate encoder-local counter.
- Require 16-aligned coded dimensions for YUV420/8x8 planes and communicate
  the actual dimensions consistently. Do not interpret tagged DCT bytes as
  raw BGR or infer DCT support merely from legacy adaptive ASCII support.
- Process predictive packets in order. If frames are skipped before encoding,
  keep the predictor tied to the last encoded/transmitted frame. Never discard
  an arbitrary encoded delta while keeping its descendants.
- Benchmark encode, transfer size, browser decode and browser drawing
  separately; include A/V drift and repeated seeking. Node correctness
  checks do not establish browser FPS. No 60 FPS claim for DCT yet.

## Implemented live path

`--pixel-codec dct --dct-quality 70` enables the profile. It is selected only
when a client requests adaptive transport, `sync=1`, and `pixel_codec=dct-v1`.
`INIT` field 11 names `dct` or `raw`; the root player selects a 3-byte profile
decoder for DCT and uses a fresh decoder on every seek/reinit. The npm SDK
does not announce the new capability and continues receiving raw pixels.

`asciline/pixel_profile.py` owns per-session encoding and replaces packet
indices with the playback timeline. Coded dimensions round up to multiples
of 16 and are announced in INIT. Seek/reinit starts a new predictor/keyframe.
Frame shedding advances the source without advancing the encoded predictor.
Python profile encoding remains available; both engine paths disable a
redundant WebSocket deflate layer when DCT is configured.

Native options and input shapes are checked before encoding. Inputs are copied
before releasing the GIL. Motion search uses radius 3 and zero-vector tie
preference; IDCT has an exact DC-only shortcut. SAD search can stop a candidate
once its accumulated error cannot beat the best and uses an edge-padded plane.
The resulting 120-packet Costa Rica stream was byte-for-byte identical before
and after these performance changes (SHA256
`93F20DBD375E7B67F131A3FB71F03274CE283C5A24CB0C47D33B1FE0DD54BC81`).

Reference checks now cover both native and Python encoders: 408 frames across
three qualities. Real WebSocket checks compare packets with reference
reconstructions and run those packets through root `codec.js`: periodic
keyframes, forward/backward/paused seek, mode switches, reconnection, and
source-frame shedding. Legacy/raw clients and the Python fallback have checks.

## First Costa Rica measurements

Source: local `videos/costa_rica_60fps.mp4`, 59.94 FPS, 120 frames from 5 seconds,
QF 70, zlib level 3. These short, content-specific samples are not quality
scores, whole-video averages or end-to-end browser playback measurements.

| Coded grid | Native DCT mean / p95 | Source decode + DCT mean / p95 | Node decode mean / p95 | DCT bytes / raw bytes |
| --- | --- | --- | --- | --- |
| 464x256 (`--cols 450`) | 12.65 / 13.91 ms | 18.24 / 33.27 ms | 6.94 / 11.71 ms | 261,507 / 42,762,720 |
| 320x192 (`--cols 320`) | 6.02 / 6.33 ms | 11.08 / 24.99 ms | 3.53 / 6.67 ms | 125,837 / 22,118,880 |

At 464x256 the server average already exceeds the 16.68 ms source-frame
budget: begin with `--fps 30`. At 320x192 the average fits, but p95 exceeds
the budget, so 60 FPS remains a playback experiment. Lossy profile traffic
in these samples was about 0.131 and 0.063 MB/s at source FPS respectively,
before transport overhead; the large compression ratios depend on the scene
and on discarding image detail.

The browser benchmark could not be opened: the user's saved browser permission
blocks localhost:8001. Node measurements do not establish Canvas speed or A/V
sync. The benchmark page is saved for manual testing; no alternate browser
access was attempted.

Reproduce (from repository root):

```powershell
python -m pytest test/test_profile_contract.py test/test_live_profile.py -q
python experiments/rust_audit/profile_probe.py videos/costa_rica_60fps.mp4 --cols 450
node experiments/rust_audit/profile_probe.cjs
```

The probe writes ignored packets/metadata and a manual browser benchmark to
`experiments/rust_audit/output/profile/`.

## Recovering from a slow live producer (2026-10-01)

Follow-up stage profiling and an isolated exact-output optimization experiment
are recorded in [PERFORMANCE_FINDINGS.md](PERFORMANCE_FINDINGS.md).

The 450-column / 60-FPS user test froze while audio and the time counter kept
advancing. The server only skipped frames for a reported decoder backlog.
If source decode + encode exceeded the frame budget, it kept producing old
timestamps even with a healthy client queue. The root player discarded every
frame more than 100ms behind audio; an empty queue did not trigger recovery.

The server now checks its synchronized playback clock before producing each
frame. When the next source timestamp is over 25ms late, it advances the source
without encoding, until it catches up. The last transmitted DCT predictor is
preserved. Pause and startup/seek preroll are excluded. This prevents accumulating
producer delay; it does not make encoding faster or guarantee 60 rendered FPS.

A regression starts a real WebSocket stream with its clock ahead by 1.5 seconds
and an empty decode queue. It fails on the old code. With catch-up enabled, both
30 and 60 FPS recover within the player's 100ms window. Packets across the gap
match independent native encodes and root `codec.js` reconstructions, including
the 30-FPS source sampling path. Player tests also cover timing reports, late
frame removal, recovery without moving audio, and statistics reset on seek.

`--debug` now includes `[PERF]`: delivered FPS, mean/max source-decode + process +
encode time (including executor wait), send lag, source skips due to clock or
client backlog, and pending client decodes. Root-player telemetry adds epoch
average `decodeMs` and `renderMs`, latest decoded-frame `lagMs`, and cumulative
`lateDrops` / `decodeErrors`. Canvas timing measures the JS draw call, not GPU
presentation. Bandwidth rates now divide bytes by the actual reporting interval.

An isolated, real-video WebSocket probe at 464x256/Q70/59.94 target FPS completed
the entire clip: 3,045 post-preroll packets over 60.043 seconds (50.71 delivered
FPS), 548 observed skipped source indices, p95 delivery lag 50.6ms, maximum
106.1ms. Only 2 received packets were already over 100ms late. These are local
server delivery measurements with no client decoding, Canvas, or actual audio;
browser performance and other concurrent machine load can change the result.

The same delivery probe at 320x192 over the first 30.012 seconds received 1,780
post-preroll packets (59.31 FPS), with 16 observed source skips, p95 lag 18.7ms,
maximum 50.5ms, and no packets over 100ms late. The runs were sequential. This
supports a server throughput limit at the larger grid, independently of browser
cost; it is not a browser 60-FPS certification.

Reproduce without taking over an existing server (the probe owns and cleans up
an isolated CLI process; logs go under the ignored `output/` directory):

```powershell
python experiments/rust_audit/live_probe.py --serve-video videos/costa_rica_60fps.mp4 --dct --cols 450 --fps 60 --seconds 65
```
