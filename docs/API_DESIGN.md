# API ve MCP tool tasarımı

**Durum:** Kabul edildi  
**Son gözden geçirme:** 2026-07-17  
**Sonraki gözden geçirme:** 2026-10-17

## Amaç

İç HTTP API ve MCP tool yüzeyinin kaynak modeli, sürümleme, doğrulama, hata ve yazma güvenliği
sözleşmesini belirlemek. Endpoint kataloğunun ayrıntısı `API_CONTRACTS.md` içindedir.

## Araştırma

- [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html) HTTP metot semantiğini ve idempotency'yi;
  [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457.html) ise `application/problem+json` hata modelini
  tanımlar ve problem detaylarında iç sistem bilgisinin sızdırılmamasını ister.
- Güncel MCP [Tools specification 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/tools),
  tool input/output için JSON Schema kullanımını ve annotations'ın güvenilmeyen metadata sayılmasını belirtir.
- MCP [Security Best Practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices),
  confused deputy, token passthrough, SSRF ve session hijacking risklerini tanımlar.
- Google Ads [mutate validation](https://developers.google.com/google-ads/api/docs/concepts/api-structure)
  çoğu mutate isteğinin `validate_only=true` ile yazmadan doğrulanabildiğini belirtir.

## Karar

### HTTP API

- JSON REST API `/api/v1` altında kaynak odaklıdır. OpenAPI sözleşmesi kodla birlikte üretilir ve CI'da
  breaking-change kontrolü yapılır.
- Principal request body'sinden seçilmez; connector token subject'inden türetilir. `customer_id` format + principal access
  doğrulamasından geçer.
- Request modelleri kapalıdır: bilinmeyen alanlar, yanlış enum/format, limit üstü string/list reddedilir.
- Para integer `amount_micros`, tarih RFC 3339 UTC, Ads customer ID tirelerden normalize edilmiş string olur.
- State-changing çağrılar CSRF koruması, `Idempotency-Key`, immutable proposal hash ve yetki gerektirir.
  Execution ham mutate body kabul etmez; yalnız onaylanmış proposal ID'sini uygular.
- Hatalar RFC 9457 `application/problem+json`: `type`, `title`, `status`, güvenli `detail`, opaque `instance`,
  `code`, `correlation_id`, gerekirse güvenli field errors. Stack trace ve başka kullanıcı varlık bilgisi yoktur.
- Her HTTP response `X-Correlation-ID` taşır. Client güvenli biçimde `X-Correlation-ID` verdiyse aynı değer
  korunur; geçersiz/çok uzun değerler yansıtılmaz ve yeni opaque ID üretilir. Problem response gövdesi aynı
  `correlation_id` değerini içerir.
- Liste endpoint'leri bounded cursor pagination kullanır; kullanıcı kontrollü sort/filter allowlist'tir.
- Public HTTP ingress request body üst sınırı 1 MiB'dir. Bu sınırı aşan `Content-Length` route/auth katmanına
  ulaşmadan `413 application/problem+json` ile reddedilir; başlıksız streamed body aynı sınırı downstream
  okuma sırasında geçerse yine `413` döner. Geçersiz `Content-Length` `400` döner.

### MCP tools

- Read, proposal preparation ve execution ayrı tool'lardır. Model için raw GAQL/raw mutate tool'u yoktur.
- Tool schema JSON Schema 2020-12, `additionalProperties: false`, açık min/max ve output schema taşır.
- Kimlik/principal tool argümanı değildir. Tool annotations yetkilendirme veya insan onayı sayılmaz.
- Execution tool yalnız proposal ID alır; backend onay, hash, freshness, ownership ve audit'i tekrar doğrular.
- Reklam metni ve URL güvenilmeyen veri olarak işaretlenir; içindeki talimat tool davranışını değiştirmez.
- Sonuçlar boyut/alan olarak minimize edilir; pagination ve timeout/cancellation desteklenir.

### Değişiklik yönetimi

- Additive alan minor değişiklik olabilir; alan kaldırma/semantik değişim yeni API/schema version gerektirir.
- Google Ads API ve proposal schema sürümü execution kaydına yazılır; eski proposal sessizce migrate edilmez.
- Her endpoint/tool için auth, principal isolation, schema, injection, timeout ve audit contract testleri zorunludur.

## Pagination sözleşmesi

`todo.md` 1.5 kararı: sürümleme mevcut path-based `/api/v1/...` biçiminde kalır (header-based
content negotiation'a geçilmez -- MCP/HTTP istemci ekosisteminde daha az yaygın, ek test/araç
yükü getirir). Sayfalama hiçbir zaman offset/sayısal index kullanmaz; keyset (`created_at`, `id`)
konumunu taşıyan, principal/customer/status bağlamına imzalı biçimde bağlı, opak bir cursor
kullanılır:

- Cursor `backend/src/api/pagination.py::encode_cursor`/`decode_cursor` ile üretilir/doğrulanır.
  İçerik JSON'dur (`principal_id`, `customer_id`, `status`, `after_created_at`, `after_id`,
  `issued_at`), HMAC-SHA256 ile imzalanır ve base64url ile taşınır -- ne ham offset ne düz metin
  pozisyon bilgisi client'a sızar.
- İmza anahtarı vault key'inden (`Settings.local_vault_key`) HKDF-benzeri sabit bir "info" etiketiyle
  türetilir (`hmac.new(vault_key, b"addobserver-api-pagination-cursor-v1", sha256)`); vault'un kendi
  Fernet anahtarı asla doğrudan başka bir amaç için yeniden kullanılmaz (anahtar ayrımı), ayrıca yeni
  bir zorunlu ortam değişkeni/secret provizyonu eklemez.
- Cursor 15 dakika (`CURSOR_TTL`) geçerlidir ve yalnız üretildiği principal/customer_id/status
  bağlamında geçerlidir; farklı bir principal, customer_id veya durumla tekrar kullanılırsa,
  imzası bozulmuşsa veya süresi dolmuşsa hepsi AYNI genel `invalid_cursor` hatasına düşer -- hangi
  kontrolün başarısız olduğu asla açığa çıkmaz (başka bir principal'ın verisinin var olup
  olmadığını sızdırmamak için, docs/SECURITY.md).
- `GET /api/v1/proposals` bu sözleşmeyi uygular: `next_cursor` yalnız gösterilecek daha fazla satır
  varsa response'a eklenir; `cursor` query parametresi verilirse bir önceki sayfanın konumundan
  devam eder. `backend/src/db/proposals.py::ProposalRepository.list_pending` `limit+1` satır çekip
  keyset `WHERE (created_at, id) > (?, ?)` ile devam eder -- asla `OFFSET` kullanmaz.
- Google Ads reporting tool'ları (`api/reporting.py`) zaten Google'ın kendi opak `page_token`'ını
  kullanıyor (offset değil); bu, aynı "asla offset yok" ilkesini kod değişikliği gerekmeden zaten
  karşılıyor -- yalnız bu belgeye çapraz referanslandı.

## Açık sorular

- HTTP framework (Python varsayımıyla aday FastAPI) ve OpenAPI breaking-change aracı.
- UI için aynı-origin cookie session mı, ayrı BFF mi kullanılacağı.
- Ayrı internal admin API ihtiyacı.
- İlk desteklenecek mutate allowlist'i.

## Güncelleme geçmişi

- 2026-07-18 — Faz 1.5: "Public MCP endpoint path/versioning" sorusu kapatıldı -- mevcut path-based
  `/api/v1/...` sürümleme korunur; yeni "Pagination sözleşmesi" bölümü opak, imzalı, principal/
  customer/status/expiry'e bağlı keyset cursor kararını ekliyor. `GET /api/v1/proposals` bu
  sözleşmeye göre uygulandı (`backend/src/api/pagination.py`, `backend/tests/test_api_pagination.py`,
  `backend/tests/test_api_http_routes.py`). MCP `list_proposals` tool'u aynı `has_more` sinyalini
  taşır ama HTTP'nin tam cursor sözleşmesini henüz uygulamaz -- bu `todo.md` 6.1'e (MCP tool
  sözleşme denetimi) bırakıldı.
- 2026-07-18 — Public opaque kimlik girdileri URL-safe karakterlerle ve 128 karakter üst sınırıyla
  sınırlandırıldı; HTTP, MCP ve approval form yolları aynı doğrulamayı kullanır.

- 2026-07-17 — Public HTTP ingress için 1 MiB request body sınırı ve güvenli problem response sözleşmesi eklendi.
- 2026-07-17 — Public HTTP responses için `X-Correlation-ID` üretme/echo etme ve problem response korelasyonu eklendi.
- 2026-07-17 — REST/RFC 9457 sözleşmesi, kapalı MCP şemaları ve proposal-only execution kararı tanımlandı.
