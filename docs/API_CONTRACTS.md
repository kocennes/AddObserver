# API ve Google Ads sözleşmeleri

**Durum:** Kabul edildi  
**Son gözden geçirme:** 2026-07-17

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
- State-changing istekler `Idempotency-Key`, correlation ID ve yetkili kullanıcı gerektirir.
- Zaman RFC 3339 UTC, para integer micros, ID'ler string olarak taşınır.
- Hatalar kararlı `code`, güvenli `message`, `correlation_id` ve alan detayları döndürür; stack trace dönmez.

## Önerilen HTTP kaynakları

| Metot/yol | Amaç | Yetki/koruma |
|---|---|---|
| `GET /api/v1/accounts` | Yetkili Ads hesapları | Principal-scoped |
| `POST /api/v1/analyses` | Analiz başlat | Rate limit, idempotency |
| `GET /api/v1/proposals` | Önerileri listele | Principal + customer scope |
| `GET /api/v1/proposals/{id}` | Değişiklik önizle | Ownership check |
| `POST /api/v1/proposals/{id}/decisions` | Onay/red | CSRF, role, immutable hash |
| `POST /api/v1/proposals/{id}/executions` | Onaylı değişikliği uygula | Revalidation, idempotency, audit |
| `GET /api/v1/audit-events` | Denetim izi | Auditor role, export audit |

Execution endpoint'i ham Google Ads mutate payload kabul etmez; yalnız önceden doğrulanmış proposal ID uygular.

## Google Ads istemci sınırı

- İç servis çağrısı doğrulanmış `principal_id` ve `customer_id` alır; MCP tool principal ID kabul etmez,
  resource server token subject'inden türetir ve hesap eşlemesini doğrular.
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

## Uyum ve sürümleme

- Google Ads API sürümü ve resmi Python client sürümü lockfile'da sabitlenir.
- Destek sonu tarihleri üç aylık operasyonal kontrolde izlenir.
- Breaking sözleşme yeni API/schema version gerektirir; eski proposal yeni şemaya sessizce çevrilmez.

## Açık sorular

- İlk reporting alanları ve management allowlist'i Google RMF sınıflandırmasına bağlıdır.
- Public MCP dışında ayrı kullanıcı-facing HTTP API yayınlanıp yayınlanmayacağı.

## Güncelleme geçmişi

- 2026-07-17 — Principal-scoped public connector ve Google Ads sözleşmeleri tanımlandı.
- 2026-07-17 — Ürün sahibi onayıyla Kabul edildi durumuna geçirildi.
