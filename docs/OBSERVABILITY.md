# Loglama, gözlemlenebilirlik ve audit tasarımı

**Durum:** Kabul edildi  
**Son gözden geçirme:** 2026-07-17  
**Sonraki gözden geçirme:** 2026-10-17

## Amaç

Sistemin davranışını teşhis edilebilir kılarken secret/müşteri verisini sızdırmamak; operasyon telemetry'si
ile hukuki/işlemsel audit izini doğru biçimde ayırmak.

## Araştırma

- [OpenTelemetry Signals](https://opentelemetry.io/docs/concepts/signals/) traces, metrics, logs ve baggage
  sinyallerini ayırır. [Context propagation](https://opentelemetry.io/docs/concepts/context-propagation/)
  trace/log korelasyonu sağlar fakat dış servislere hassas baggage göndermeme konusunda uyarır.
- Temmuz 2026 güncellemeli [OpenTelemetry Metrics](https://opentelemetry.io/docs/concepts/signals/metrics/),
  yüksek kardinaliteli user ID/raw URL gibi attribute'ların sınırsız bellek maliyeti oluşturduğunu ve SDK
  cardinality limitlerini açıklar.
- [OpenTelemetry sensitive data](https://opentelemetry.io/docs/security/handling-sensitive-data/), data
  minimization ile collector attribute/filter/redaction processor'larını önerir.
- [OWASP Logging](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html), “when, where, who,
  what” alanlarını; access token, session ID, connection string ve encryption key'lerin loglanmamasını;
  güvenlik logu ile audit/transaction trail'in ayrılmasını belirtir.

## Karar

### Üç ayrı kayıt sınıfı

- **Uygulama logu:** yapılandırılmış JSON; debug/operasyon olayı, kısa retention.
- **Telemetry:** OpenTelemetry trace + metric; OTLP üzerinden collector, vendor-neutral export.
- **Audit:** append-only iş kaydı; ayrı erişim/retention/bütünlük, sampling yok.

Tümünde UTC, `service.name/version/environment`, correlation ID vardır. Trace ID loga eklenir. Audit ayrıca
actor/service/principal, customer, event type, proposal/approval/execution, before/after özeti/hash'i, outcome,
reason, Google request ID ve zaman taşır.

- Public HTTP sınırında her response `X-Correlation-ID` taşır. Gelen güvenli değerler korunur; geçersiz veya
  yüksek riskli değerler log/header'a yansıtılmadan yeni opaque ID ile değiştirilir. Problem response gövdesindeki
  `correlation_id`, response header'ıyla aynı değerdir.
- Baggage yalnız allowlist teknik değerler taşır; token, email, reklam metni, customer ID veya principal ID
  üçüncü taraf Google/Anthropic isteklerine propagate edilmez. Dış trace context sınırda sanitize edilir.
- Metrik label'larında user/principal, proposal, execution, raw customer ID, URL veya request ID yoktur. Kullanıcı bazlı
  teşhis gerekirse erişim kontrollü log/audit kullanılır; metric cardinality büyütülmez.
- HTTP body, prompt, Google payload varsayılan loglanmaz. Authorization/cookie/token/secret/redacted alanlar
  logger ve collector katmanında allowlist+redaction ile korunur.
- Audit write mutate ön koşuludur ve fail-closed'dur. Normal runtime rolü audit UPDATE/DELETE yapamaz;
  export/okuma da audit edilir. HTTP'den gelen approval decision ve disconnect gibi state-changing audit
  event'leri, güvenli client correlation ID kabul edildiyse response header'ıyla aynı correlation ID'yi
  taşır. Audit event'leri correlation ile Google sonucu uzlaştırır.
- Asgari metrikler: request rate/error/duration, queue depth/age, analysis latency/schema failure, approval age,
  execution outcome/unknown, Google error class/quota, credential invalid, audit failure ve principal mismatch.
- Alarm: onaysız mutate denemesi, cross-user mismatch, audit failure ve unknown execution anlık yüksek önem;
  quota/latency/error-rate pencere tabanlıdır. Alarmın runbook bağlantısı bulunur.
- Telemetry backend/retention seçilmeden üretime çıkılmaz; erişim least privilege ve export şifrelidir.

## Açık sorular

- OpenTelemetry collector ve log/metric/trace backend sağlayıcısı.
- Uygulama logu, trace ve immutable audit retention süreleri.
- Audit WORM/bütünlük mekanizması ve anahtar yönetimi.
- SLO hedefleri ve alarm eşikleri trafik gözleminden sonra kesinleştirilecek.

## Güncelleme geçmişi

- 2026-07-17 — OTel sinyalleri, düşük kardinalite, redaction ve ayrı fail-closed audit hattı tanımlandı.
- 2026-07-17 — Public HTTP `X-Correlation-ID` üretme/echo etme ve problem response korelasyonu eklendi.
