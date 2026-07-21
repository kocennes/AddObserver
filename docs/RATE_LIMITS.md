# Rate limit ve kota yönetimi

**Durum:** Kabul edildi  
**Son gözden geçirme:** 2026-07-17  
**Sonraki gözden geçirme:** 2026-10-17

## Amaç

Google Ads ve public MCP limitlerini aşmadan kullanıcılar arasında adil, ölçülebilir ve kontrollü iş yürütmek;
bir kullanıcının ücretsiz ortak kapasiteyi tüketip diğerlerini aç bırakmasını engellemek.

## Araştırma

- Google Ads [Access Levels](https://developers.google.com/google-ads/api/docs/api-policy/access-levels),
  Basic Access için kayan 24 saatte 15.000 API operation sınırı; Standard için çoğu serviste sınırsız günlük
  operation fakat sistem rate limitlerinin devam ettiğini belirtir.
- 10 Temmuz 2026 güncellemeli [Rate Limits](https://developers.google.com/google-ads/api/docs/productionize/rate-limits),
  limitlerin client customer ID ve developer token düzeyinde token bucket ile ölçüldüğünü; concurrency sınırı,
  batching, client-side throttling ve queue önerildiğini açıklar.
- [API Limits and Quotas](https://developers.google.com/google-ads/api/docs/best-practices/quotas), mutate başına
  10.000 operation, 64 MB gRPC cevap sınırı ve servis özel limitlerini listeler. Başarısız `GoogleAdsFailure`
  istekleri de günlük kotayı tüketebilir.
- [QuotaErrorDetails](https://developers.google.com/google-ads/api/reference/rpc/v22/QuotaErrorDetails), sunucunun
  `rate_scope`, `rate_name` ve önerilen `retry_delay` alanlarını sağlayabileceğini belirtir.

## Karar

- Merkezi distributed limiter iki ana bucket tutar: developer token global ve `(principal_id, customer_id)`.
  Servis özel bucket gerektiğinde ayrıca eklenir. Kesin QPS sabit varsayılmaz; konfigürasyonla düşük başlar.
- Her principal için weighted fair queue ve concurrency üst sınırı vardır. Global kapasitenin tamamını tek kullanıcı
  tüketemez; interactive onaylı execution, planlı analizden önceliklidir fakat güvenlik/audit kapısını atlamaz.
- Günlük operation budget 15.000'in altında güvenlik payıyla planlanır. Usage estimate, gerçek hata/response
  ve kalan bütçe metriği izlenir; %70 uyarı, %85 planlı analiz azaltma, %95 yalnız kritik/onaylı işler için
  varsayılan eşiklerdir ve trafik verisiyle ayarlanır.
- Queue message principal, customer, operation tahmini, priority, attempt, not-before, idempotency ve correlation
  taşır; credential veya payload secret taşımaz. Dead-letter queue manuel incelemelidir.
- `RESOURCE_TEMPORARILY_EXHAUSTED` / `RESOURCE_EXHAUSTED` geldiğinde ilgili scope durdurulur; `retry_delay`
  ve full-jitter exponential backoff uygulanır. Diğer principal bucket'ları gereksiz durdurulmaz.
- Batching yalnız aynı principal/customer, uyumlu işlem ve bağımsız onay sınırında yapılır. Büyük GAQL seçimi
  alan/tarih/pagination ile küçültülür; response 64 MB sınırına yaklaşmaz.
- Backpressure UI'da “kuyrukta / tahmini başlama” olarak görünür. Limit aşımı başarı gibi gizlenmez.
- Public GA için Standard Access hedeflenir fakat bu rate limiting'i kaldırmaz; RMF/başvuru kapısı
  `GOOGLE_API_ACCESS.md` içindedir.

## Açık sorular

- Queue/distributed limiter teknolojisi (Redis, DB tabanlı veya yönetilen queue).
- İlk concurrency/QPS değerleri ve Anthropic workspace limitleri.
- Ürün ücretsiz olduğu için principal ağırlıklarının eşit/fair-use sınırları.
- Günlük budget rezervinin kritik execution için kesin yüzdesi.

## Güncelleme geçmişi

- 2026-07-22 — Reporting tool cevapları 100 satır/256 KiB ile sınırlandı. Stateless continuation'ın
  aynı provider sayfasını yeniden okuyabileceği ve her public çağrının `quota.google_requests=1`
  metadata'sıyla bu maliyeti görünür kılacağı sözleşmeye bağlandı.

- 2026-07-17 — Çift kapsamlı limiter, fair queue, operation budget eşikleri ve backpressure politikası tanımlandı.
