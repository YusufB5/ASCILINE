# ASCILINE Master Architecture & Technical Specification

This document serves as the absolute architectural source of truth for the **ASCILINE** project. It details the core philosophy, system ecosystem, wire protocols, rendering pipelines, strict code governance, and compiler internals.

---

## 0. Core Philosophy & Manifest (Amacı ve Felsefesi)

> **"ASCILINE rejects traditional black-box video tags (`<video>`). In return, it is a representation engine designed to provide total typographic manipulation, algorithmic interactivity, and raw character control."**
>
> To casual users, ASCILINE may simply appear as a "fast ASCII video player". However, its true architectural purpose is far deeper:
> 1. **De-commoditizing Video:** Traditional video in the browser is an opaque texture trapped inside an untouchable `<video>` element. ASCILINE frees video into discrete, addressable text cells and data streams.
> 2. **Typographic & Algorithmic Manipulation:** Because every frame is decomposed into text characters and color matrices, developers can apply real-time font substitutions, text-selection layers, shader-like cellular effects, matrix glitching, and DOM-level interactions that are fundamentally impossible with standard MP4/WebM video players.
> 3. **The Pixel Mode Contrast:** Even in Pixel Mode (`--pixel`), ASCILINE bypasses media decoders in favor of direct HTML5 Canvas memory buffers (`putImageData`), treating the screen as an open canvas rather than a locked media stream.

---

## 1. System Ecosystem & Strict Governance

ASCILINE is a high-performance, real-time ASCII and Pixel video rendering engine. The ecosystem is divided into three execution environments:
1. **Live Streaming (Server-Client):** A FastAPI/WebSocket backend (stream_server.py) streaming encoded frames to a JS frontend.
2. **NPM SDK (sciline-player):** An encapsulated, zero-dependency library designed for modern web apps (React, Vue, Vite).
3. **Offline Static Player (static_player/ & compiler.py):** A pre-compiled, serverless .ascf file format player for zero-latency, offline playback.

> ⚠️ **STRICT GOLDEN MASTER GOVERNANCE RULE (DO NOT TOUCH):**
> * pp.js, codec.js, and index.html located in the root repository are the **Golden Master**.
> * The showcase website and standalone engine rely on this core.
> * **NO AI OR DEVELOPER MAY REFACTOR, MIGRATE, OR TOUCH THE GOLDEN MASTER TOWARDS THE SDK WITHOUT EXPLICIT USER APPROVAL.**
> * Migration or merging from Golden Master to SDK will only happen when the SDK is 100% perfected, verified, and explicitly authorized by the project owner.

---

## 2. Server Management & Rendering Modes (stream_server.py)

The Python backend extracts video (FFmpeg/OpenCV), performs real-time quantization, encodes payloads, and streams them over WebSocket.

### 2.1 Starting the Server
`ash
# ASCII Mode (Modes 1 to 6)
python stream_server.py <source> --mode <1-6> --host 0.0.0.0 --port 8000

# Pixel Mode (Dedicated Flag)
python stream_server.py <source> --pixel --host 0.0.0.0 --port 8000
`

### 2.2 Deep Dive into Rendering Modes

ASCILINE strictly separates **ASCII Modes (1 to 6)** from **Pixel Mode (--pixel)**:

#### 🅰️ ASCII Modes (--mode 1 to 6)
All ASCII modes render characters into an ASCII grid using font glyphs. The mode determines **color depth / quantization**:
* **Mode 1 (B&W / Monochrome String Stream):**
  * **Payload:** Plain UTF-8 Text WebSocket frame (<frame_index>\n<line_1>\n<line_2>...).
  * **Client Rendering:** Direct DOM <pre> or single-color Canvas illText(). Zero color memory overhead.
* **Modes 2 to 6 (Color Quantized Binary ASCII Streams):**
  * **Mode 2:** 64 colors (6-bit color quantization).
  * **Mode 3:** 512 colors (5-bit quantization).
  * **Mode 4:** 32,000 colors (3-bit quantization).
  * **Mode 5:** 262,000 colors (2-bit quantization).
  * **Mode 6:** 16 Million Colors (Full 24-bit True Color / Ultra).
  * **Cell Structure (Modes 2-6):** Packed as **4 bytes per cell: [ char_byte (uint8), R (uint8), G (uint8), B (uint8) ]**.
  * **Client Rendering:** Decoded via codec.js and rendered character-by-character using HTML5 Canvas 2D CPU ctx.fillText().

---

#### 🔲 Pixel Mode (--pixel)
Pixel mode is **NOT an ASCII mode**. It completely replaces character rendering with a direct hardware-accelerated video canvas:
* **Activation:** Triggered via the --pixel flag (automatically sets underlying mode to 6 True Color).
* **Payload:** Pure raw binary pixel stream.
* **Frame Structure:** [ 4 bytes frame_index BE uint32 ] + [ rows * cols * 3 bytes (BGR) ].
* **Client Rendering (HTML5 Canvas Optimization):** **NO illText() OR FONT GLYPHS ARE USED.**
  * The browser receives raw BGR bytes, converts them into an RGBA Uint8ClampedArray (TypedArray), and pushes them directly to the GPU via HTML5 Canvas **ctx.putImageData()**.
  * This bypasses all CPU font rendering overhead, achieving a rock-solid 60 FPS even at high resolutions.

---

## 3. The Wire Protocol & Handshake

### 3.1 The INIT Handshake Frame
Immediately upon WebSocket connection, the server sends a text control frame before any video frames:
`	ext
INIT:<effective_fps>:<render_mode>:<cols>:<rows>:<pixel_mode>:<queue_index>:<duration>:<0>:<is_webcam>
`
* **Example:** INIT:30.0:6:240:135:1:0:45.120:0:0 (Here pixel_mode=1 indicates Pixel Mode is active).
* **Purpose:** The client immediately:
  1. Sizes the <canvas> buffer dimensions (cols x ows).
  2. Sets up audio synchronization (/audio endpoint or embedded duration).
  3. Instantiates the correct decoder pipeline:
     * Mode 1: Plain text string splitter.
     * Modes 2-6: Adaptive ASCII binary decoder (makeDecoder()).
     * Pixel Mode: Direct ImageData BGR-to-RGBA frame buffer.

### 3.2 Binary Frame Envelope (Modes 2-6 & Adaptive Codec)
When using the adaptive codec (?codec=adaptive), binary ASCII frames follow this envelope:
`	ext
[ 4 bytes (UInt32 Big Endian) Frame Index ] + [ 1 byte Codec Tag ] + [ Payload ]
`

---

## 4. Audio & Video Synchronization (pp.js vs. SDK)

Synchronization between video frames and audio is the most critical component of ASCILINE.

### 4.1 The Golden Master Engine (pp.js)
* **Master Clock:** pp.js uses udioEl.currentTime as the absolute source of truth (getMasterClock()).
* **Render Loop:** Uses equestAnimationFrame to poll the rameBuffer. It displays the frame whose timestamp most closely matches the Master Clock.
* **Fallback:** If unmuted audio is blocked by the browser, pp.js seamlessly falls back to a wall-clock implementation (performance.now()).

### 4.2 The NPM SDK (src/asciline-player.js)
The SDK adapts the Golden Master logic into a portable, class-based architecture (AsciiPlayer).
* **State Isolation:** The SDK strictly decouples playback state (PLAYING, PAUSED) from the audio element's volume state.
* **Clock Continuity:** When resuming a paused video, the SDK forces udioEl.play() even if the player is muted. This ensures the master clock ticks forward, preventing the render loop from freezing.

---

## 5. The Compiler & Static Player

ASCILINE is not just a live streaming engine; it can compile videos into a proprietary offline format.

### 5.1 The Compiler (compiler.py)
Pre-processes a video and compiles it into an **ASCILINE Compiled File (.ascf)**.
* Runs the same encoding pipeline as stream_server.py but writes the binary output to disk instead of a WebSocket.
* **File Structure of .ascf:**
  1. A JSON Header (Metadata, dimensions, fps) length-prefixed.
  2. The embedded Audio Track (base64 or raw binary).
  3. The sequentially packed Binary Video Frames, each prefixed with a **4-byte length header** (so the static player can chunk the ArrayBuffer linearly, unlike WebSockets which natively chunk frames).

### 5.2 Codec Tags & Streaming vs. Compiler Differences
ASCILINE uses 1-byte tags to identify frame payload encodings:
* TAG_RAW (0): Uncompressed frame payload.
* TAG_ZLIB (1): Zlib compressed payload (High compression, moderate CPU).
* TAG_DELTA (2): Delta encoded (Transmits only changed pixels/characters since the last frame).
* TAG_RLE_FULL (3): Run-Length Encoded.
* TAG_PROFILE (4): **Lossy DCT (Discrete Cosine Transform) Profile.**

> **CRITICAL ARCHITECTURAL DISTINCTION:**
> * **Live Streaming (stream_server.py):** ASCII uses **Tags 0, 1, 2, 3**. Pixel defaults to raw BGR; `--pixel-codec dct` enables **Tag 4** only for clients negotiating `pixel_codec=dct-v1` and `sync=1`. Each session owns its predictor, starts with a keyframe after seek/reinit, and uses playback indices in packet headers. Python and Rust encoders share the existing profile format. DCT encode/decode cost must be measured separately from raw-pixel performance.
> * **Offline Compiler (compiler.py):** Continues supporting the opt-in **Tag 4 (TAG_PROFILE)** pixel profile for `.ascf` files.

### 5.3 The Static Player (static_player/)
A specialized HTML/JS client designed to load and play .ascf files locally.
* **Zero Latency:** Since the file is entirely in memory (ArrayBuffer), there is no network jitter.
* **Self-Contained:** It reads the .ascf binary, mounts the audio, strips the 4-byte length prefixes to emulate WebSocket packet boundaries, and uses the exact same codec.js decoding logic (inflate) to render frames.

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
