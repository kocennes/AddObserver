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

## Uygulanan instrumentation (Faz 9)

- `backend/src/observability/logging.py`, serbest payload kabul etmeyen sabit JSON şeması kullanır.
  Principal/customer kimlikleri süreç anahtarlı HMAC ile pseudonymous referansa çevrilir; kontrol karakteri,
  header/body, URL, token, cookie, prompt ve exception metni log API'sine alınmaz.
- `backend/src/observability/telemetry.py`, OpenTelemetry API/SDK 1.44 üzerinden exporter bağımsız tracer ve
  meter üretir. Exporter/collector yalnız deployment composition'ında seçilecektir. Boyutlar boundary,
  operation ve sonuç allowlist'iyle sınırlıdır; kullanıcı/customer/request kimliği metric attribute değildir.
- HTTP middleware request count/duration ve sonuç logunu üretir. Aynı telemetry nesnesi MCP, auth, Google,
  DB ve worker sınırlarının manuel instrumentation girişidir; baggage kullanılmaz.
- `JsonEventLogger.emit`'in isteğe bağlı `google_request_id` alanı (Faz 5.6), yalnız güvenli karakter
  kümesiyle eşleşen bir değer verildiğinde olaya eklenir, aksi halde tamamen atlanır (asla "unknown" gibi bir
  yer tutucuyla loglanmaz). `mcp/tools.py::_log_google_ads_failure`, gerçek bir Google Ads/transport
  `AdsApiError`'ı yakalayan iki sınırda (`_fetch_report_page`, `sync_accessible_accounts`) bunu
  `operation`/`reason_code`/principal-customer pseudonymous referansıyla aynı olaya yazar; `AdsApiError.message`
  (Google'ın serbest metni) bilinçli olarak şemaya eklenmez.

## Geçici SLI ve alarm başlangıç eşikleri

Kalıcı SLO taahhüdü değildir. İlk 30 günlük staging/public-beta ölçümü sonrasında, **2026-09-01** tarihinde
ADR review yapılır.

| Sinyal | Geçici pencere/eşik | Önem | Runbook |
|---|---|---|---|
| HTTP 5xx oranı | 5 dk içinde ≥%5 ve ≥20 istek | high | `OPERATIONS.md` → Genel servis bozulması |
| p95 HTTP gecikmesi | 15 dk boyunca ≥2 sn | medium | Genel servis bozulması |
| Auth failure | 5 dk içinde tabanın 3 katı ve ≥20 | high | Credential sızıntısı/auth kötüye kullanım |
| Principal mismatch | Tek olay | critical | Onaysız veya şüpheli mutate |
| Audit write failure | Tek olay | critical | Audit deposu kullanılamıyor |
| Unknown execution | Tek olay | critical | Onaysız veya şüpheli mutate |
| Google quota | 10 dk içinde ≥5 `RESOURCE_EXHAUSTED` | high | Google Ads quota/rate limit |
| Queue depth | 15 dk boyunca config kapasitesinin ≥%80'i | medium | Google Ads quota/rate limit |

Alert payload'ı secret/customer content içermez; yalnız service/environment, alarm adı, pencere, ölçüm,
correlation/trace referansı ve runbook bağlantısı taşır. Routing sağlayıcı ve gerçek on-call kimliği 9.7
kapısı kapanana kadar production'a bağlanmaz.

## Güncelleme geçmişi

- 2026-07-23 — Exporter-bağımsız `Telemetry` aynı composition root instance'ıyla HTTP ve MCP
  context'ine taşındı; Google reporting çağrıları `google.report_search`, PostgreSQL request unit
  of work işlemleri `db.request_transaction` düşük-cardinality operation'larıyla ölçüldü.

- 2026-07-22 — Faz 5.6 kapandı: `JsonEventLogger`'a `google_request_id` alanı eklendi ve
  `mcp/tools.py`'nin Google Ads hata sınırlarına bağlandı (bkz. `docs/ERROR_HANDLING.md`
  güncelleme geçmişi).
- 2026-07-22 — Faz 9 JSON logging/OpenTelemetry sözleşmesi, düşük kardinalite ve geçici SLI/alarm eşikleri eklendi.
- 2026-07-17 — OTel sinyalleri, düşük kardinalite, redaction ve ayrı fail-closed audit hattı tanımlandı.
- 2026-07-17 — Public HTTP `X-Correlation-ID` üretme/echo etme ve problem response korelasyonu eklendi.
