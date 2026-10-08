# Real-source Rust playback freeze, 30 September 2026

Source: `videos/costa_rica_60fps.mp4`, AV1 1280x720, 60000/1001 FPS,
Opus audio, about 60 seconds. Output: raw BGR 450x253.

The native decoder processed the first 600 frames in 3.20 seconds
(187 FPS average). Decoder speed alone did not explain the freeze.

With Uvicorn's default WebSocket permessage-deflate negotiation, a real
WebSocket client measured 1.60 seconds of delivery lag at wall time 28.18s
(frame PTS 26.58s), growing to 1.77 seconds at wall time 40.21s. The browser
discards frames more than 100ms behind its clock. Once that gap accumulates,
new frames are discarded on arrival and the last rendered picture freezes.

With the client declining permessage-deflate, the same 42-second run stayed
within 53.1ms maximum delivery lag. The Rust CLI and programmatic server now
disable this extra transport compression even when a browser offers it.
Adaptive ASCII codec compression remains enabled. Raw pixels at this grid
and frame rate require about 20.5 MB/s; this is a local throughput fix.
The patched server was also measured through the full 60-second source with
a client offering deflate: maximum delivery lag was 71.3ms and did not
accumulate. The handshake regression confirms no deflate is negotiated.

The audio endpoint also returned HTTP 200 with zero bytes. PATH resolved to
an FFmpeg build dated August 2013; the native engine used the configured
FFmpeg 8 development runtime. Audio and scrub extraction now resolve that
same runtime. After the fix, the real-source audio endpoint returned the
first 4096 MP3 bytes in 88ms.

Regression checks cover native runtime selection over PATH, invalid explicit
runtime errors, actual MP3 delivery, and rejecting transport deflate offered
by a real WebSocket client. The Python suite passed 59 tests, with 3 existing
FFmpeg-dependent skips. The SDK import smoke check passed.

Reproduce delivery measurement while the server is running:

```powershell
python experiments/rust_audit/live_probe.py --port 8000 --seconds 60
```

Browser verification of the patched server is still required: the browser
permission request for the temporary test server on port 8001 was declined.
Restart the user's server on port 8000 and refresh the player to test the fix.

## Startup and repeated seek follow-up

After the transport fix, the user reported low startup FPS and freezing after
a second seek. On the same AV1 source, seeks to 10, 20, 30 and 40 seconds took
approximately 0.26–0.70 seconds. The player started the new audio immediately
on the seek click, so its clock was already hundreds of milliseconds ahead
when the native decoder delivered the correct target frame.

The root player now opts into coordinated startup (`sync=1`): the server
prepares four frames, waits for `playback-ready`, and aligns its pacing to
the actual client clock. A seek includes an ID, and a FIFO `SEEKED` marker
identifies the new timeline before its first keyframe. The player drops
pre-marker frames and ignores stale decode/audio completions. Pause/resume
reuses the same gate. Only one requestAnimationFrame chain may be active;
the player waits for actual audio playback rather than metadata readiness.
Unavailable audio selects a stable wall clock instead.

Real WebSocket regression tests exercise delayed startup, paused seeks,
forward/backward jumps and a stale ready message after two quick seeks.
`test/test_live_player_timing.cjs` executes the shipped root app in a Node VM
with a controlled media clock; this covers delayed media/decode completions,
render-loop cancellation, no-audio fallback and recovering source FPS after
multiple seeks. These simulations do not replace real browser A/V testing.

## Intermittent seek freeze investigation

The user confirmed startup/buffering had recovered, but some seeks stayed
frozen indefinitely. Comparing the same AV1 file directly:

| Target | Python/OpenCV seek | Rust seek |
| --- | --- | --- |
| 10s | 2282ms | 623ms |
| 3s | 2716ms | 719ms |
| 20s | 1014ms | 345ms |

The first read after each seek took 5–8ms in Python and under 1ms in Rust.
Python delegates seek to `CAP_PROP_POS_MSEC`; Rust seeks backwards to a
keyframe, flushes the decoder and retains the first frame at/after the
requested PTS. Native seek speed did not explain an indefinite freeze.

The live server's forward/backward seeks also returned the matching marker,
four correct preroll frame indices, and continued after the ready message.
`client_seek_probe.cjs` couples the shipped root app to that real server,
using a simulated audio clock. Six forward/backward seeks and a paused seek
resumed successfully. This checks the client/server contract, not real DOM
media-event ordering.

A new regression test exposed a client hole: the reused audio element's
`playing` handler checked the seek epoch, but not the resource responsible
for that event. An old queued media event could start the newest listener
with the old `currentTime`, anchoring server pacing to the wrong time.
Playback now requires matching `currentSrc`, sufficient readiness and an
unpaused element. Each audio URL also includes the seek epoch. The test
failed before this change and passed after it. The real browser root cause
has not been directly confirmed; browser inspection timed out.

The status now reports Seeking / Preparing playback / Starting audio, so a
remaining stall can be located without relying on a stale FPS display.
