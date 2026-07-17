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

## Açık sorular

- HTTP framework (Python varsayımıyla aday FastAPI) ve OpenAPI breaking-change aracı.
- UI için aynı-origin cookie session mı, ayrı BFF mi kullanılacağı.
- Public MCP endpoint path/versioning ve ayrı internal admin API ihtiyacı.
- İlk desteklenecek mutate allowlist'i.

## Güncelleme geçmişi

- 2026-07-17 — Public HTTP ingress için 1 MiB request body sınırı ve güvenli problem response sözleşmesi eklendi.
- 2026-07-17 — Public HTTP responses için `X-Correlation-ID` üretme/echo etme ve problem response korelasyonu eklendi.
- 2026-07-17 — REST/RFC 9457 sözleşmesi, kapalı MCP şemaları ve proposal-only execution kararı tanımlandı.
