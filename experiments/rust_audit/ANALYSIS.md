# ASCILINE Rust entegrasyon analizi

Tarih: 30 Eylül 2026. Ana çalışma ağacı: `main`, `6571d9f`.

## Karar

Entegrasyonun başlangıcı, `feat/rust-core-integration` dalındaki paket yapısı
ve isteğe bağlı motor seçimi ile `RUST-ENTEGRATİON/ASCILINE-RUST` içindeki
çekirdek kaynaklar olmalı. Ana projenin güncel sunucu/istemci davranışına
küçük değişikliklerle taşınmalı. Eski sunucu ve istemciyi topluca kopyalamak
protokol, SDK ve yaşam döngüsü gerilemelerine yol açar.

İlk düzeltme doğru seek ve kare zamanı sözleşmesi. Ardından kaynak kapatma,
GIL sınırı, motor değişimi ve paketleme. Rust DCT'nin doğru çözülmesi ayrıca
doğrulanmadan canlı DCT ve performans optimizasyonuna geçilmemeli.

## Kaynaklar ve derleme durumu

| Kaynak | Mevcut içerik | Sonuç |
| --- | --- | --- |
| `RUST-ENTEGRATİON/ASCILINE-RUST` | Cargo manifesti, lib/ascii/codec/decoder kaynakları, eski sunucu ve istemci | Yeniden `cargo check --offline --locked` başarılı. Kaynak kontrolü; yeni release derlemesi/link/wheel testi yapılmadı. |
| `rust_core` | `Cargo.lock`, yalnızca `src/dct.rs`, önceden üretilmiş DLL | Manifest ve diğer kaynaklar eksik. Mevcut dizin kendi başına yeniden derlenebilir değil. |
| `feat/rust-core-integration`, `24c0fa1` | Manifest ve tam temel çekirdek, Python fallback, pixel için Rust seçimi | Entegrasyon geçmişi için yararlı. Bu dalın `lib.rs` dosyasında DCT yok. |
| Kurulu `ascii_core` | Python 3.11 native modül; DCT export'u da var | SHA-256, `rust_core/target/release/ascii_core.dll` ile aynı. |

Windows'ta doğrudan `import ascii_core` FFmpeg DLL'leri bulunamadığı için
başarısız oldu. Mevcut shared FFmpeg `bin` dizini, `os.add_dll_directory`
ile ve handle canlı tutularak eklendiğinde import başarılı.

Kaynak kontrolünde başlangıçta bindgen `errno.h` bulamadı. Kurulu Visual
Studio'nun `vcvars64.bat` ortamı ile shared FFmpeg kökü `FFMPEG_DIR` olarak
verildiğinde kontrol geçti. Yeni bağımlılık indirilmedi. Tam log:
`output/build-check.log`. Derleme ortamı subprocess ile sınırlı.

Önceden üretilmiş DLL'lerin mevcut kaynaklardan üretildiğini kanıtlamadık.
Binary testleri, açıkça yüklenen ve hash'i kaydedilen DLL'lere aittir.

## Çalıştırılan doğrulamalar

Fixture: FFmpeg `testsrc2`, MPEG-4, 640×360, 60 FPS, 3 saniye, GOP 120,
2 B-frame. Decode çıktısı 450×253 BGR. Fixture hash'i sonuç JSON'larında.
Seek sonrası gelen kare, aynı motorla baştan okunmuş karelerin SHA-256
listesinde bulunarak gerçek konumu saptandı. Python ve Rust'ın piksel
çıktılarının birbiriyle aynı olması bu karşılaştırmanın şartı değil.

### Seek: doğrulanmış senkron hatası

| İstenen zaman | Python ilk kare | Her iki mevcut Rust DLL'de ilk kare |
| --- | --- | --- |
| 0,35 s | 0,35 s (kare 21) | 0,00 s (kare 0) |
| 1,23 s | 1,233 s (kare 74) | 0,00 s (kare 0) |
| 2,35 s | 2,35 s (kare 141) | 2,00 s (kare 120) |

Rust `DecoderCore.seek`, demuxer seek ve decoder flush yapıyor; hedef
zamana kadar kare çözerek ilerlemiyor. Sunucu buna karşılık
`frame_index = int(target_sec * effective_fps)` atıyor. Örnekte gerçek
0,00 saniyenin görüntüsü 1,23 saniye olarak etiketleniyor. Bu, seek ve
zamana yapılan mod değişiminde A/V uyuşmazlığı üretmeye yeterli.

Çözüm sözleşmesi: hedefin önceki keyframe'ine seek, decoder flush,
hedef PTS'ye kadar decode/discard, ilk gösterilecek karenin gerçek zamanını
taşıma. PTS ve başlangıç zamanı ofsetleri desteklenmeli; yalnızca sayısal
kare indeksi VFR veya başlangıcı sıfır olmayan kaynaklar için yeterli değil.

### Normal adaptive codec: olumlu

Her DLL için 300 kare; 3/4 kanal, tolerans 0/4/16, aynı kare, küçük renk
değişimi, seyrek değişim, tekrarlanan örüntü ve rastgele kareler. 0 ve 48
keyframe sınırları; RAW/ZLIB/DELTA/RLE_FULL etiketlerinin tamamı kullanıldı.

- Python ve Rust paketleri: **300/300 aynı**.
- Python ve Rust yeniden oluşturulan durum: **300/300 aynı**.
- Ana projenin `codec.cjs` çözücüsüyle görüntü: **300/300 aynı**.

Bu, test girdileri için uyumluluk kanıtıdır; tüm olası girdiler için ispat
veya codec performans ölçümü değildir.

### Rust DCT: doğrulanmış encoder/decoder uyuşmazlığı

`RustProfileEncoder`: 32×32 BGR, QF 70, DZ 0,75, SKIP_T 256, level 6;
50 deterministik rastgele kare (keyframe 0 ve 48 dahil).

- Python DCT ile paket ve yeniden oluşturulan görüntü: **0/50 aynı**.
- Rust encoder'ın yeniden oluşturduğu görüntü ile mevcut JS decoder:
  **50/50 kare farklı**, toplam **139.665 byte farklı**.
- Kontrol olarak aynı girdilerde Python DCT → aynı JS decoder:
  **50/50 kare aynı**.

Farklı encoder'ların farklı paket üretmesi tek başına hata değildir.
Kritik hata, Rust encoder'ın gelecekte referans alacağı görüntünün
istemcinin gerçekten çözdüğü görüntüden farklı olmasıdır. Bu fark
tahminli karelerde birikmeye uygun bir durumdur.

Kaynak incelemesinde somut aritmetik farkı: Rust IDCT'de
`(sum + 2048) / 4096`, Python'da `// 4096`, JS'de `Math.floor` kullanılıyor.
Negatif tam sayılarda Rust bölmesi sıfıra doğru, diğerleri aşağıya doğru
yuvarlıyor. Örneğin pay -1 olduğunda sonuç Rust'ta 0, mevcut çözücüde -1.
Koşulsuz truncating division mevcut decoder sözleşmesiyle uyuşmuyor.

Başka farklar da var: katsayı/renk/subsample yuvarlaması, hareket araması
yarıçapı (Rust 2, Python 3). Arama yarıçapı farkı tek başına protokol
uyumsuzluğu değildir; encoder seçenekleri farklı paketler üretebilir.
IDCT farkının düzeltilmesi bütün hataların çözüldüğünü göstermeyeceği için
aynı vektör deneyi her değişiklikten sonra korunmalı.

### Decoder yaşam döngüsü ve EOF

- Her iki Rust DLL ve Python, normal `next()` ile 180/180 kare verdi.
- Her iki Rust DLL'de `release()` sonrası `next()` hâlâ kare verdi.
  Kaynakta `release()` no-op; kaynaklar nesne düşene kadar tutuluyor.
- Yalnızca Rust `grab()` ile ilerlenince 179/180 kareye ulaşıldı. Kaynakta
  `grab()` demux EOF'ta decoder'a EOF gönderip son tampon kareleri
  boşaltmıyor. Backpressure ve FPS düşürme yolunun EOF davranışı ayrılıyor.

Kaynakta decode/encode işlemleri GIL'i bırakmıyor. `run_in_executor`
tek başına Rust hesaplarını GIL'den ayırmıyor. Olası event-loop gecikmesi
ayrıca ölçülmeli; bu incelemede tarayıcı donması olarak doğrulanmadı.

### Hız ölçümünün sınırı

Sentetik klipte tek kare decode+resize medyanı Python'da yaklaşık 0,79 ms,
Rust DLL'lerinde yaklaşık 0,63–0,67 ms. Bunlar kısa, ısınmış yerel deneyler;
network, ses, WebSocket paketleme, pacing ve tarayıcı çizimini içermiyor.
Uçtan uca 60 FPS veya gerçek video iş yükünde hızlanma iddiası kurulamaz.

## Eski sunucu/istemciyi taşımayı engelleyen bulgular

1. Rust klasöründeki sunucunun ilk INIT mesajında p[8] sabit `4`.
   Eski app.js bunu `PARTICLE_CHUNK_SIZE` olarak okuyor; güncel main/SDK
   bunu başlangıç zamanı olarak yorumluyor. Başlangıçta yanlış ses ofseti
   ve kare atlaması riski. İlk INIT ofseti gerçek zamanla, başlangıçta 0
   olarak gönderilmeli.
2. Rust klasöründeki codec.js yalnızca 0/1/2 destekliyor; Rust encoder
   RLE_FULL=3 üretiyor. Kendi istemcisinde bile normal renkli akış uyumsuz.
3. Eski app.js stateful decode işlemlerini sıralamadan çağırıyor. INIT ve
   seek sonrasında eski decode işlerini ayıran güncel yaşam döngüsü eksik.
4. Webcam yolunda opsiyonel import edilen fakat klasörde bulunmayan
   `webcam_engine` doğrudan çağrılıyor.
5. Klasör sunucusunda Python fallback yok; temel modül yüklenmezse sunucu
   başlamıyor. Dalda fallback var ama import hatasının nedeni kayboluyor.
6. Dalın Windows araması kişisel FFmpeg yolları içeriyor ve
   `os.add_dll_directory` handle'larını saklamıyor. Dağıtılabilir loader ve
   paketleme, kişisel makine yollarından bağımsız olmalı.
7. Dalda pixel→ASCII reinit için başlangıçta pixel iken oluşturulmayan
   `mapper` kullanılabiliyor. Mod değişimi motor seçimi, buffer ve filtre
   durumunu birlikte yeniden kurmalı.
8. Dalın `encode_frame` alias'ı moddan bağımsız Rust seçiyor; açıklamalar
   ASCII'nin Python encoder'da kalacağını söylüyor. Motor seçimi decoder,
   mapper ve encoder için ayrı ve açık olmalı.
9. Ham pixel yolu Rust codec'i çağırmıyor: native codec'in varlığı ham
   pixel yayınını sıkıştırmıyor. Rust DCT'nin sonradan eklenmesi sunucu ve
   istemci codec anlaşmasını ayrıca gerektiriyor.

## Önerilen sonraki uygulama

1. Tam temel çekirdeği tek `rust_core` paketi altında toplamak; mevcut DCT
   kaynağını korumak, henüz varsayılan yola bağlamamak. Temel paket kaynakları
   dalda mevcut. Yeni derlemenin gerçekten bu kaynaklardan üretildiğini
   doğrulamak.
2. Decoder'a doğru seek/PTS, gerçek close/release ve ortak EOF draining
   eklemek. Bu audit'teki sapmaları anlamlı regresyon testlerine çevirmek.
3. Native hesaplarda güvenli GIL bırakma sınırı; kullanılan tamponların
   sahipliği ve decoder erişiminin tek sıra halinde korunması.
4. `python / rust / auto` seçimi ile motor adaptörü; explicit Rust seçimi
   başarısızsa açıklayıcı hata, auto'da nedeni raporlayarak Python fallback.
5. Güncel sunucu/SDK üzerinde ilk kare, seek, pause/resume, mod değişimi,
   sessiz oynatma, uzun oynatma ve bağlantı kapanışı ölçümleri. FPS 60'ın
   üstünde/altında olan kaynaklar ve webcam ayrıca kapsanmalı.
6. Rust DCT/JS yeniden oluşturma eşitliğini sağlamak; sonra opt-in canlı
   DCT ve tarayıcı decode/çizim ölçümüne geçmek.

## Yeniden çalıştırma

`audit.py` Python veya açıkça seçilen DLL için ayrı süreçte çalıştırılır.
Shared FFmpeg `bin` yolu `--ffmpeg-dir` ile verilir. `python`, `rust-folder`,
`rust-core`, `installed` seçenekleri vardır. Her koşu `output/` altında
JSON kanıtı üretir. `check_decode.cjs`, bu vektörleri ana projenin mevcut
JS çözücüsüyle karşılaştırır; optional ikinci dosya argümanına rapor yazar.

Örnek (proje kökünden; yolu yerel FFmpeg kurulumuna göre ayarla):

```powershell
python experiments/rust_audit/audit.py --engine rust-core --ffmpeg-dir 'C:\path\to\shared-ffmpeg\bin'
node experiments/rust_audit/check_decode.cjs experiments/rust_audit/output/rust-core_vectors.json experiments/rust_audit/output/rust-core_js.json
```

Scriptler teşhis amaçlı rapor üretir; görüntü farkı bulunması sürecin exit
kodunu otomatik başarısız yapmaz. Gerçek tarayıcı profili, sesli uçtan uca
oynatım, uzun klip, VFR ve yeni wheel kurulumu bu analizde yapılmadı.
Üretim kaynakları değiştirilmedi; yalnızca audit scriptleri ve bu belge eklendi.
