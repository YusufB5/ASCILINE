# JavaScript SDK integration

`AsciiPlayer` supports live WebSocket sessions and static ASCF files. This checkout adds synchronized live playback and pixel DCT negotiation. The player is [asciline-player.js](../src/asciline-player.js); live transport/timeline ownership is in [live-session.js](../src/live-session.js).

## Checkout versus published package

Use local source for the integration described here. `npm install asciline-player` or an unversioned CDN may load a previously published version. Checkout metadata 0.1.5 does not prove these changes are published. There is no Rust-specific JavaScript package: server engine selection is independent of SDK installation.

Use [examples/sdk-live.html](../examples/sdk-live.html), served by the backend at `/static/examples/sdk-live.html`. For a custom host, copy all `src/`, preserving module paths, and serve over HTTP(S). A bare `asciline-player` import needs a bundler or import map; the example below uses a concrete module URL.

## Minimal live player

```html
<div style="position:relative;aspect-ratio:16/9;background:#000">
  <canvas id="video"></canvas>
</div>
<button id="play">Play / pause</button>
<button id="audio">Enable audio</button>
<script type="module">
  import { AsciiPlayer } from '/static/src/asciline-player.js';
  const player = new AsciiPlayer('#video', {
    url: 'ws://localhost:8000/ws',
    selectionLayer: false, playOverlay: false, clickToPlayPause: false,
  });
  document.querySelector('#play').onclick = () => player.togglePlay();
  document.querySelector('#audio').onclick = () => player.unmute();
  player.on('init', info => console.log(info.pixelCodec, info.cols, info.rows));
  player.on('error', error => console.error(error));
  window.addEventListener('pagehide', () => player.destroy());
</script>
```

This assumes the page/modules are served by the local backend. For a separate frontend use your module URL and backend WebSocket address; HTTPS requires `wss://`. An omitted/`auto` URL uses page origin with `/ws`. The SDK preserves existing query parameters, adds adaptive capability if absent, requests `sync=1`, and advertises `pixel_codec=dct-v1` when its decoder supports tag 4. The server still selects RAW/DCT. Audio URL is derived from the resolved backend URL.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `url` | `null` | Live URL; omitted derives page origin |
| `src`, `audioSrc` | `null` | ASCF and paired audio URLs |
| `container` | canvas parent | Layout element or selector |
| `audio` | `true` | Create element; selector/element or `false` accepted |
| `muted` | `false` | Initially mute audio |
| `autoplay` | `false` | Attempt playback in constructor |
| `loop` | `false` | Static ASCF looping; live queues use server `--loop` |
| `selectionLayer` | `null` | Selection element; `true` creates one |
| `playOverlay` | `true` | Built-in play button |
| `muteButton` | `true` | Built-in mute toggle |
| `clickToPlayPause` | `true` | Toggle by canvas click |
| `keyboardShortcuts` | `true` | Built-in spacebar handling |
| `bufferSize` | `4` | Buffer-related sizing; sync preroll stays four frames |
| `filters` | default values | Contrast, gamma, brightness, sharpness, invert, palette |

Audio playback policy still applies. Autoplay may give silent video until a gesture enables audio. Muted audio can advance; zero volume is not pausing its clock. Live playback uses a wall clock with `audio:false`, webcam, or unavailable audio.

## Controls and events

```js
player.play();                // configured source
player.play('ws://localhost:8000/ws');
player.pause();
player.resume();
player.seek(10);               // live or static, seconds
player.skip(-10);
player.setPixelMode(true);     // live reinit
player.setRenderMode(6);       // live color-mode reinit
player.setFilters({ contrast: 1.2 });
player.setVolume(0.8);
player.mute();
player.unmute();
player.getMasterClock();       // seconds, selected audio/wall clock
player.getState();
player.destroy();
```

Live seeks clamp to known duration. Webcam does not seek/pause. A synchronized seek waits for the matching marker and new frames. Live resume re-seeks at the paused position, aligning audio range, clock and buffered frames. ASCII/pixel reinit receives a fresh INIT and rebuilds its pipeline.

`destroy()` stops render/report scheduling and disposes socket handlers and pending frames. Previous-timeline decode completions are discarded. `play()` can reconnect; a UI can instead create a fresh instance, as the supplied example does. Calling play while connecting/playing does not create a second connection.

| Event | Payload/use |
| --- | --- |
| `init` | Live: `fps`, `cols`, `rows`, `duration`, `pixelMode`, `renderMode`, `queueIdx`, `isWebcam`, `pixelCodec` |
| `fps` | `fps`, `targetFps`, `buffered` |
| `timeupdate` | Seconds |
| `statechange` | `IDLE`, `CONNECTING`, `PLAYING`, `PAUSED`, `ENDED`, `ERROR` |
| `buffering` | Connection/startup status |
| `seek` | Requested live target |
| `ended` | Playback completion |
| `error` | Error object/message |

Register with `on(name, handler)` and remove with `off(name, handler)`. Update seek UI from `timeupdate` when not dragging. Focusing a range input through Tab is not necessarily dragging; the sample active-element guard is a UI choice rather than a playback requirement.

## Timeline and predictor

`LiveSession` serializes async decoding. Seek, INIT, reinit and disposal change the epoch; old completions cannot populate the new buffer. Predictor resets create a fresh decoder. Discarding a late draw frame differs from discarding a predictive packet: packet reconstruction must remain ordered.

Sync sends four preroll frames and waits for client `playback-ready`. Start waits for audio playing, or falls back to wall clock. Matching SEEKED markers separate timelines. Short streams can start with fewer preroll frames on clean EOF. Old servers without sync INIT fields retain the legacy path; DCT requires capability and tag-4 support.

## Static ASCF and custom element

```js
import { AsciiPlayer } from './src/asciline-player.js';
const player = new AsciiPlayer('#video', {
  src: 'clip.ascf', audioSrc: 'clip.mp3', loop: true,
});
```

Static playback uses its existing reader path, independently of `LiveSession`. Rust is not needed at playback time. The compiler produces ASCF and paired audio; JavaScript reconstructs the representation.

```html
<script type="module" src="./src/ascf-element.js"></script>
<ascf-player src="clip.ascf" audio="clip.mp3" loop
  style="width:100%;aspect-ratio:16/9"></ascf-player>
```

Use `ws="ws://localhost:8000/ws"` instead of `src` for live. Attributes are `src`, `audio`, `ws`, `autoplay`, `loop`, `muted`. Events bubble with `ascf-` prefix such as `ascf-timeupdate`. Set source attributes before attaching: the current attribute handler does not implement runtime source replacement. Detach disposes the player.

The root showcase, packaged SDK and `demo/ascf-player.js` prototype are separate clients. The component documented here is `src/ascf-element.js`.

## Verify and package

```bash
npm test
npm pack --dry-run
python -m pytest -q test/test_sdk_profile.py
```

The npm files list includes `src`, covering `live-session.js`. Dry-run checks inclusion; it does not publish or prove a release shipped. Live tests exercise negotiation, decode order, seek isolation, pause/resume, reinit, destroy, replay, legacy and short EOF. The Python test sends actual Python/Rust DCT packets through the embedded codec and runs SDK against a real server. Rust coverage requires a built module; check skips. Node mocks/synthetic tests do not measure physical A/V skew or browser throughput; use the local example for observation.
