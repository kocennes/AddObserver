# API ve Google Ads sözleşmeleri

**Durum:** Kabul edildi  
**Son gözden geçirme:** 2026-07-18

## Amaç

Public MCP'nin kullandığı iç HTTP kaynakları, Google Ads adapter sınırı ve proposal payload'ının test
edilebilir veri sözleşmelerini tanımlamak.

## Araştırma

- [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457.html) güvenli makine-okur HTTP problem modelini tanımlar.
- Google Ads [API structure](https://developers.google.com/google-ads/api/docs/concepts/api-structure)
  `validate_only`; [mutate best practices](https://developers.google.com/google-ads/api/docs/mutating/best-practices)
  operasyon/response davranışını açıklar.
- Anthropic [review criteria](https://claude.com/docs/connectors/building/review-criteria) catch-all read/write
  tool'ları reddeder ve amaç-özel tool bekler.

## Karar
**Sonraki gözden geçirme:** 2026-10-17

## Genel sözleşme

- Dış API sürümlüdür (`/api/v1`). Request/response katı şemalıdır; bilinmeyen mutate alanları reddedilir.
- Connector token subject'inden türetilen principal bağlamı bütün servis/repository çağrılarına açıkça aktarılır.
- Canlı read/proposal kaynakları yalnız `active` ads_account eşleşmelerini kullanır; `disconnected` satırlar
  geçmiş/audit için tutulur ama gelecekteki erişim kanıtı sayılmaz.
- State-changing istekler `Idempotency-Key`, correlation ID ve yetkili kullanıcı gerektirir.
- Zaman RFC 3339 UTC, para integer micros, ID'ler string olarak taşınır.
- Hatalar kararlı `code`, güvenli `message`, `correlation_id` ve alan detayları döndürür; stack trace dönmez.
  Her public HTTP response `X-Correlation-ID` taşır; güvenli client değeri korunur, geçersiz değer sanitize
  edilip yeni opaque ID ile değiştirilir.
- Public ingress request body sınırı 1 MiB'dir. `Content-Length` bu sınırı aşarsa handler çalışmadan
  `413 application/problem+json` ve `code=request_body_too_large` döner. Başlık olmadan gelen streamed body
  downstream okuma sırasında sınırı geçerse aynı `413` döner; geçersiz değer `400 invalid_content_length` olur.

## Önerilen HTTP kaynakları

| Metot/yol | Amaç | Yetki/koruma |
|---|---|---|
| `GET /api/v1/accounts` | Yetkili Ads hesapları | Principal-scoped, yalnız active hesaplar, uygulandı |
| `POST /api/v1/analyses` | Analiz başlat | Rate limit, idempotency |
| `GET /api/v1/proposals` | Önerileri listele | Principal + customer scope, opak cursor pagination, uygulandı |
| `GET /api/v1/proposals/{id}` | Değişiklik önizle | Ownership check, uygulandı |
| `POST /api/v1/proposals/{id}/decisions` | Onay/red | CSRF, role, immutable hash |
| `POST /api/v1/proposals/{id}/executions` | Onaylı değişikliği uygula | Revalidation, idempotency, audit |
| `GET /api/v1/audit-events` | Denetim izi | Auditor role, export audit |

Execution endpoint'i ham Google Ads mutate payload kabul etmez; yalnız önceden doğrulanmış proposal ID uygular.

## Google Ads istemci sınırı

- İç servis çağrısı doğrulanmış `principal_id` ve `customer_id` alır; MCP tool principal ID kabul etmez,
  resource server token subject'inden türetir ve active hesap eşlemesini doğrular.
- GAQL sorguları kodda tanımlı query object/allowlist ile kurulur. Tarih ve ID parametreleri doğrulanır.
- Mutate adapter yalnız `PRODUCT.md` içinde kabul edilmiş işlem türlerini destekler.
- `validate_only` mümkün olan işlemlerde onay öncesi kullanılır; başarı canlı uygulama sayılmaz.
- Retry yalnız belgelenmiş retryable hatalarda, jitter ve quota `retry_delay` ile yapılır. Mutate sonucu belirsizse
  önce Google durumu okunur; kör tekrar yapılmaz.
- Google `request_id`, hata kodu ve field path yakalanır; güvenli uygulama hatasına çevrilir.
- Güncel limitler için Google'ın [quota](https://developers.google.com/google-ads/api/docs/best-practices/quotas),
  [error handling](https://developers.google.com/google-ads/api/docs/get-started/handle-errors) ve
  [rate limit](https://developers.google.com/google-ads/api/docs/productionize/rate-limits) belgeleri izlenir.

## Öneri şeması — asgari alanlar

```json
{
  "schema_version": "1",
  "type": "campaign_budget_update",
  "customer_id": "1234567890",
  "resource_name": "customers/1234567890/campaignBudgets/42",
  "before": {"amount_micros": 4000000000},
  "after": {"amount_micros": 5000000000},
  "currency_code": "TRY",
  "reason": "Kaynak metriklere dayalı kısa gerekçe",
  "evidence_refs": ["metric-snapshot-id"],
  "risk": "medium"
}
```

Modelin gönderdiği `customer_id`, `resource_name`, `before` ve bütçe değeri backend tarafından yeniden doğrulanır.
`rationale` en fazla 2000 karakter olabilir ve kontrol karakteri içeremez; durum değişikliği önerilerinde
`current_status` yalnız Google Ads'in gerçek `CampaignStatus` değerlerinden (`ENABLED`/`PAUSED`/`REMOVED`)
biri olabilir; `campaign_id` en fazla 19 haneli (`int64`) sayısal bir kimlik olmalıdır.

## Uyum ve sürümleme

- Google Ads API sürümü ve resmi Python client sürümü lockfile'da sabitlenir.
- Destek sonu tarihleri üç aylık operasyonal kontrolde izlenir.
- Breaking sözleşme yeni API/schema version gerektirir; eski proposal yeni şemaya sessizce çevrilmez.

## Açık sorular

- İlk reporting alanları ve management allowlist'i Google RMF sınıflandırmasına bağlıdır.
- Public MCP dışında ayrı kullanıcı-facing HTTP API yayınlanıp yayınlanmayacağı.

## Güncelleme geçmişi

- 2026-07-18 — Faz 1.5: `GET /api/v1/proposals` opak, imzalı keyset cursor pagination'ı uyguladı
  (bkz. `docs/API_DESIGN.md` "Pagination sözleşmesi"); daha önce yalnız `limit` ile sınırlı, ilk
  sayfanın ötesine geçemeyen ad hoc davranışın yerini aldı. `next_cursor` yalnız daha fazla satır
  varsa döner; farklı principal/customer/status için üretilmiş veya süresi dolmuş bir cursor aynı
  genel `invalid_cursor` hatasıyla reddedilir.
- 2026-07-18 — Öneri şemasında `rationale`, `current_status` ve `campaign_id` için sınır değer/allowlist
  doğrulaması eklendi (bkz. yukarı, "Öneri şeması — asgari alanlar").
- 2026-07-18 — `proposal_id` girdileri bütün public yüzeylerde 1–128 karakterlik URL-safe opaque
  kimlik olarak sınırlandırıldı; geçersiz HTTP girdisi `invalid_proposal_id` problem cevabı üretir.

- 2026-07-18 — HTTP/MCP read ve proposal yolları `disconnected` hesap satırlarını erişimden dışlar;
  tekrar bağlantı aynı customer satırını `active` olarak canlandırır.
- 2026-07-17 — `GET /api/v1/accounts` principal-scoped HTTP endpoint'i uygulandı; connector bearer token
  audience/expiry/revocation doğrulamasını MCP ile ortak kullanır.
- 2026-07-17 — `GET /api/v1/proposals` ve `GET /api/v1/proposals/{id}` read-only endpoint'leri
  principal/customer izolasyonu ve problem+json hata şekliyle uygulandı.
- 2026-07-17 — 1 MiB public ingress request body sınırı, streamed-body uygulaması ve hata kodları eklendi.
- 2026-07-17 — `X-Correlation-ID` response header'ı ve problem response `correlation_id` sözleşmesi eklendi.
- 2026-07-17 — Principal-scoped public connector ve Google Ads sözleşmeleri tanımlandı.
- 2026-07-17 — Ürün sahibi onayıyla Kabul edildi durumuna geçirildi.
