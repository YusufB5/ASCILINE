# ASCILINE documentation

This guide describes the source checkout with live Rust/Python, pixel DCT and synchronized SDK support. These changes have been tested locally; they are not an announcement of a new PyPI/npm release. Package metadata still says 0.1.5, while the optional native module reports 0.2.0.

## Start here

| Goal | Guide |
| --- | --- |
| Understand the idea and try the project | [Project README](../README.md) |
| Build the optional Rust engine | [Rust installation and runtime](../rust_core/README.md) |
| Choose ASCII/pixel, RAW/DCT and playback settings | [Live streaming](LIVE_STREAMING.md) |
| Embed the player and control its lifecycle | [JavaScript SDK](SDK.md) |
| Understand measured gains and tune a session | [Performance](PERFORMANCE.md) |
| Understand ownership, predictors and protocol | [Architecture](../ARCHITECTURE.md) |

## What this integration adds

Python/FastAPI retains the server, audio endpoints and playback session. An optional Rust engine supplies source-video decoding through FFmpeg, character mapping, adaptive ASCII encoding and pixel DCT encoding. A bounded source queue can overlap decoding with ordered encoding. Both the root demo and the local SDK negotiate synchronized live DCT transport.

ASCII represents images as characters and colors. Pixel mode exposes a BGR image grid drawn through Canvas ImageData. DCT is an optional lossy transport for that grid. The browser reconstructs it in JavaScript; choosing Rust on the server does not move the browser decoder into Rust.

The purpose is to make moving images available as programmable characters and cells. Rust makes this pipeline faster; DCT reduces its pixel payload. The goal is useful control and expression through this representation. The codec pilot measures trade-offs rather than establishing an advantage over standard video codecs.

## Scope of validation

On 2026-10-08, the restored Windows checkout built successfully with Python 3.11 and the local stable FFmpeg 8.1 development/runtime package. The targeted suite passed 66 tests with no skips: engine selection, native decoding/mapping, live DCT, playback recording, real WebSocket streaming and SDK integration. `npm test` passed the API smoke check, seven root-player timing tests and the SDK live-session scenarios. The native Rust SAD test passed as well.

The owner confirmed playback through the local SDK page after rebuilding. These results cover the tested environment and scenarios. They do not establish all-device 60 FPS, a platform-wide compatibility matrix or long-duration results. Long-video testing remains an owner-controlled final check.

## Local artifacts and distribution

Build products, FFmpeg dependencies, runtime paths, recordings and source videos are local files. A Git source commit does not supply a working Rust DLL on another machine. Build with the Python interpreter that will run the server and keep the matching FFmpeg shared runtime available afterwards.

The current Docker recipe runs the Python server and does not build Rust. The static compiler retains its Python path. The Python helper exposes fewer options than the CLI; consult the live guide before copying CLI tuning settings into application code.
