# ASCILINE architecture

This describes the source checkout. Commands are in the [live guide](docs/LIVE_STREAMING.md), [SDK guide](docs/SDK.md) and [Rust build guide](rust_core/README.md). Historical benchmark notes describe particular experiments, not current defaults.

## 0. Philosophy

ASCILINE turns moving images into programmable characters and color cells. Developers work with fonts, palettes, cells, selection layers and time-based effects. Pixel mode retains direct image-grid access through Canvas ImageData. Rust and DCT make these representations practical to produce and transport.

The purpose is creative and algorithmic control through this representation. Standard video can also be processed on Canvas; claims of distinctive control should identify the concrete advantage of characters/cells. Performance claims require source, grid, quality and runtime conditions. The live player draws without an HTML video element, while source media uses a server decoder, DCT reconstruction uses JavaScript and audio uses a browser media element. Canvas acceleration depends on the browser/platform; zero GPU is not a verified guarantee.

## 1. Components and governance

| Component | Source | Responsibility |
| --- | --- | --- |
| Server | `stream_server.py` | FastAPI/endpoints, source queue configuration, socket session and pacing |
| Engine adapter | `asciline/engines.py` | Python/Rust/auto and native loading |
| Native engine | `rust_core/src/` | File decode, mapping and encoding |
| Source queue | `asciline/source_queue.py` | Bounded worker owning decoder reads |
| Pixel profile | `asciline/pixel_profile.py` | Session predictor, alignment and live indices |
| Playback | `asciline/playback.py`, `asciline/commands.py` | FPS sampling and pending seek coalescing |
| Runtime/diagnostics | `asciline/media_runtime.py`, `asciline/perf_trace.py` | FFmpeg path and JSONL timing |
| Root client | `app.js`, `codec.js`, `index.html` | Standalone showcase session/renderer |
| SDK | `src/asciline-player.js`, `src/live-session.js` | Reusable player and live transport/timeline |
| Component | `src/ascf-element.js` | Packaged HTML wrapper |
| Static | `compiler.py`, `static_player/` | Compile, reader/player and browser Studio |

**Golden Master governance:** root `app.js`, `codec.js` and `index.html` are the standalone showcase reference. Refactoring or migrating this client into the SDK requires explicit owner approval. Protocol parity does not imply the root UI has been replaced by the SDK.

## 2. Live pipeline

```text
Source file
  -> Python/OpenCV or Rust/FFmpeg decode + resize + BGR conversion
  -> optional bounded source queue (Rust pixel files only)
  -> ASCII map/adaptive encode OR raw BGR OR pixel DCT
  -> session WebSocket packets
  -> ordered JavaScript reconstruction
  -> Canvas text or BGR-to-RGBA ImageData

Source audio -> /audio FFmpeg process -> audio element -> playback clock
```

Python is default; Rust optional; auto permits fallback. Webcam uses OpenCV. Source-file compression, ASCILINE encoding and browser reconstruction have different costs. Native source PTS is exposed, but live packets currently schedule by nominal effective FPS; constant-rate sources are the validated baseline.

Mode 1 sends text; 2–6 use character/RGB cells with quantized values. Pixel mode draws BGR through ImageData, without glyphs, and defaults to raw. ASCII ceiling is 30 FPS, Rust pixel 60, Python pixel 30. Explicit FPS samples without interpolation. Automatic dimensions are capped; DCT aligns upward to multiples of 16.

## 3. Handshake and packets

### Capabilities

Adaptive ASCII requests `codec=adaptive`; sync requests `sync=1`. Pixel DCT additionally requests `pixel_codec=dct-v1`, with server codec dct and active pixel file input. Otherwise the supported legacy/raw path is used. Current root client/local SDK advertise DCT; older published clients may stay raw.

### INIT

```text
INIT:<fps>:<mode>:<cols>:<rows>:<pixel>:<queue>:<duration>:<offset>:<webcam>[:<syncId>[:<pixelCodec>]]
```

Pixel/webcam are 0/1; offset is seconds. Sync ID is added for synchronized files. Profile-capable clients get raw/dct suffix. New INIT rebuilds dimensions and changes the client's timeline epoch.

| Representation | Packet |
| --- | --- |
| Text | `<frameIndex>\n<lines>` |
| Legacy ASCII color | BE uint32 index + character/RGB cells |
| Live raw pixels | BE uint32 index + BGR cells |
| Adaptive ASCII | BE uint32 index + uint8 tag + payload |
| DCT pixels | BE uint32 playback index + tag 4 + zlib profile |

Tags 0/1/2/3 are RAW/ZLIB/DELTA/RLE_FULL. Tag 4 is the lossy pixel profile. Untagged live RAW pixels differ from adaptive packets; decode according to negotiated representation.

### DCT internals

BGR becomes full-range Y/Cb/Cr with 4:2:0 chroma. Planes use 8x8 blocks. Keyframes predict a constant; predictive frames use previous reconstructed planes. Luma searches integer motion within radius three; chroma uses its colocated previous block. Residuals are transformed and quantized by quality tables/dead-zone handling, zigzag/run-length coded with differential DC values. Skipped predictive blocks reuse reconstruction. Zlib compresses the payload; keyframes carry quality/dimensions.

Encoder reconstruction is the next predictor. Original source planes would diverge from decoder state. Python/Rust/JavaScript agreement is checked by packets and reconstructed BGR. Normal keyframe interval is 48 encoded frames. Live indices are rewritten to playback indices: encoder counters are not media clocks. Every socket owns its predictor, reset on seek/reinit.

## 4. Synchronization and cancellation

Sync sends four preroll frames and waits for matching playback-ready. Client waits for audio playing or uses a wall-clock fallback. Server pacing anchors to the client clock. Clean short EOF can start with fewer preroll frames.

```text
Client -> {type: seek, time: seconds, requestId: newId}
Server -> SEEKED:<requestId>:<actual seconds>
Server -> new keyframe/timeline packets
Client -> {type: playback-ready, requestId: newId, time: clock}
```

Seek stops/joins the source worker before repositioning, resets prediction/pacing/backlog, and starts a segment. Pending seeks are coalesced to latest target. This does not cancel a seek already executing inside FFmpeg; decoder access remains serialized.

LiveSession serializes decoding and discards older-epoch completions. Reinit creates a decoder after INIT. Resume re-seeks to align audio/frames. Dispose cancels startup/render/reports and socket handlers. Draw-late frames can be discarded after reconstruction; predictive packets still require ordered decoding. Source catch-up happens before ordered encoding.

Source queue bounds prepared/in-progress frames plus one consumer frame. It contains no predictors or encoded packets. Source work overlaps encode; consumer source timing becomes queue wait. FFmpeg threads control source decoding separately from queue capacity and DCT.

## 5. Static ASCF and validation

Current compiler output has an 18-byte big-endian header: ASC2 magic, float32 FPS, uint8 mode, uint8 pixel, uint16 columns/rows, uint32 total frames. Packets follow with BE uint32 length prefixes. Total frames are patched at byte offset 14. Legacy ASCF uses a shorter header. Audio is a paired file, not embedded; the header is binary, not JSON.

Static pixel packets use the compiler's tagged path, unlike untagged live RAW pixels. `--profile` enables pixel tag 4. The compiler still uses Python. Browser Studio encodes supported RAW/ZLIB/DELTA and can play other supported tags. Static playback needs no ASCILINE backend, but still has file/loading/buffer/render costs; zero latency and near-zero memory are not guarantees.

See [validation](docs/README.md#scope-of-validation) and [measurements](docs/PERFORMANCE.md). Windows native build, automated controls/protocol and owner playback are verified. Native Linux/macOS, broad device/codec coverage and multi-client capacity need separate measurements. LLM directions remain research ideas, not shipped semantic understanding.

---

## 6. Agent Comments

### GPT Astra

*Tarih: 15 Eylül 2026. Bu bölüm, ARCHITECTURE.md ve README.md üzerinden geliştirdiğim proje yorumudur. Mimari kuralların yerine geçmez; öneriler ve gelecek ihtimalleri, uygulanmış özellikler olarak okunmamalıdır.*

#### ASCILINE'ı nasıl anlıyorum?

Benim gözümde ASCILINE'ın merkezinde şu soru var: **Bir hareketli görüntüyü, yazılımın tek tek dokunabildiği karakterlere ve renk hücrelerine dönüştürürsek onunla neler yapabiliriz?** ASCII estetiği bu sorunun görünen yüzü. Asıl değer, görüntünün temsilini geliştiricinin kontrolüne açmakta: bir hücrenin karakteri, rengi, konumu ve zaman içindeki değişimi üzerinde işlem yapabilmek.

Bu yüzden projeyi bir **hareketli görüntü temsil motoru** olarak okuyorum. Kaynaktan gelen görüntü bir ızgaraya dönüşüyor; kodlama, taşıma ve çizim katmanları bu temsili farklı ortamlara ulaştırıyor. Canlı yayın, SDK ve `.ascf` oynatımı, aynı fikrin farklı kullanım biçimleri. Pixel Mode da karakter estetiğinin ötesinde, görüntü verisine doğrudan erişim fikrini sürdürüyor.

#### Felsefesi ve amaçlanan değer

- **Görüntüyü programlanabilir bir malzeme yapmak.** Font, palet ve hücre davranışı üzerinde kontrol; görsel sanat, deneysel arayüzler ve etkileşimli anlatımlar için ortak bir temel sunabilir. Bence etkileşimin hazır olarak sunulduğu yerlerle, geliştiricinin bu veri üzerinden kurabileceği etkileşimler belgelerde açıkça ayrılmalı.
- **Temsilin ayrıntısını ihtiyaca göre seçmek.** Izgara boyutu, renk derinliği ve kodlama tercihleri; görünüm, işlem yükü ve aktarım maliyeti arasında bilinçli seçim yapmayı sağlıyor. Başarıyı değerlendirirken hangi ayrıntının korunduğunu ve karşılığında hangi maliyetin doğduğunu birlikte ölçmek gerekiyor.
- **Aynı fikri farklı ortamlarda kullanılabilir kılmak.** Terminal, tarayıcı, uygulama içine gömülen SDK ve statik dosya oynatımı, projenin taşınabilirlik hedefini somutlaştırıyor. Bu yolların ortak veri sözleşmeleriyle anlaşılabilir kalması, bana göre mimarinin uzun vadeli değerlerinden biri.
- **Deney yaparken güvenilir bir temel korumak.** Golden Master kuralını, çalışan kullanıcı deneyimini koruyan bir sınır olarak anlıyorum. Yeni motor, codec veya SDK çalışmaları bu temelin yanında gelişip doğrulanmalı; aktarım kararları belgedeki yetkilendirme kuralına uymalı.

#### ChatGPT ve diğer ajanlar açısından anlamı

Bir ajan açısından ilgi çekici ihtimal, görüntünün konum ve zaman bilgisi taşıyan hücreler üzerinden incelenebilmesi. Örneğin belirli bir bölgede hangi hücrelerin değiştiğini çıkarmak, ileride görsel olayları inceleyen bir deneyin girdisi olabilir. README'deki LLM yönünü bu kapsamda bir araştırma fikri olarak görüyorum; mevcut bir ChatGPT entegrasyonu olarak değil.

Ancak **karakterlerden oluşmak, kendiliğinden anlamsal olarak anlaşılır olmak demek değildir**. Bir ASCII kare nesne adlarını, olayları veya neden-sonuç ilişkilerini hazır olarak vermez. Ayrıca sıkıştırılmış ikili aktarımın küçük olması, modele metin olarak sunulduğunda az token gerektireceğini göstermez. Böyle bir yön geliştirilecekse aynı görevde doğruluk, token tüketimi, gecikme ve kaybolan görsel ayrıntı ölçülmeli. İlk somut adım, az sayıda kareyle dar kapsamlı bir değişim tespiti deneyi olabilir.

#### Benim gelişim pusulam

Bence ASCILINE'ın ilerlemesini yönlendirecek temel soru şu olmalı: **Bu değişiklik, görüntü üzerinde hangi yeni kontrolü sağlıyor ve bu kontrolün maliyeti ne?** Standart video araçları da Canvas üzerinden işlenebilir; projenin değerini anlatırken hücre ve karakter temsilinin sağladığı somut kolaylıkları göstermek daha açıklayıcı olur. Benzer şekilde hız ve bant genişliği iddiaları; içerik, çözünürlük, donanım ve kalite koşullarıyla birlikte sunulmalı.

Benim anladığım vizyonun özeti: **ASCILINE, hareketli görüntüyü karakterler, renkler ve zaman üzerinden yeniden kurulabilen; geliştiricinin müdahale edebildiği bir ifade ortamına dönüştürmeyi amaçlıyor.** Bu fikrin gücü, estetik tercih ile mühendislik kontrolünü aynı temsil üzerinde buluşturmasında.
