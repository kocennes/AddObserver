# Dokümantasyon rehberi

**Durum:** Kabul edildi  
**Son gözden geçirme:** 2026-07-17  
**Sonraki gözden geçirme:** 2026-10-17

Bu klasör, kodun ne yapacağını ve hangi sınırlar içinde yapılacağını belirleyen kaynak belgeleri
içerir. Kod ile belge çelişirse değişiklik durdurulur; karar netleştirilir ve önce ilgili belge
güncellenir.

## Amaç

Her iş türünü zorunlu araştırma/karar belgelerine bağlamak ve taslak kararlarla implementasyona başlanmasını
engellemek.

## Araştırma

- Google/Anthropic başvuruları güncel public belge ve kanıt gerektirdiği için kaynaklar üç ayda bir yenilenir.
- Mimari kararların gerekçe ve sonuçları `docs/decisions/` ADR şablonuyla kalıcılaştırılır.

## Karar

Aşağıdaki matris bağlayıcı dokümantasyon kapısıdır.

## İşe göre okunacak belgeler

| Yapılacak iş | Önce okunacak belgeler | Değişiklikte güncellenecek belge |
|---|---|---|
| Her türlü backend değişikliği | `SECURITY.md`, `ARCHITECTURE.md` | Mimari etkileniyorsa `ARCHITECTURE.md` |
| OAuth, session, token veya yetki | `AUTH.md`, `SECURITY.md`, `DATABASE.md` | Auth kararı ve gerekirse ADR |
| Yeni tablo, migration veya sorgu | `DATABASE.md`, `DATA_MODEL.md`, `SECURITY.md` | DB kararı + mantıksal model |
| HTTP endpoint veya MCP tool tasarımı | `API_DESIGN.md`, `API_CONTRACTS.md`, `MCP.md`, `SECURITY.md` | Tasarım + sözleşme/tool şeması |
| Google Ads okuma/yazma | `API_CONTRACTS.md`, `ERROR_HANDLING.md`, `RATE_LIMITS.md`, `SECURITY.md` | API sözleşmesi ve test planı |
| Google Ads access, permissible use, RMF veya OAuth verification | `GOOGLE_API_ACCESS.md`, `LEGAL.md`, `SECURITY.md` | Uyum tablosu/başvuru kanıtı |
| Directory submission veya public MCP auth | `CONNECTOR_SUBMISSION.md`, `AUTH.md`, `MCP.md`, `SECURITY.md` | Submission checklist/auth kararı |
| Privacy, terms, kullanıcı verisi veya deletion | `LEGAL.md`, `../PRIVACY_POLICY.md`, `../TERMS.md`, `SECURITY.md` | Hukuki karar + public metin |
| Production veri envanteri, subprocessor veya transfer | `LEGAL.md`, `PRODUCTION_DATA_INVENTORY.md`, `SUBPROCESSORS.md`, `SECURITY.md` | Envanter + subprocessor kaydı |
| Google developer-token/OAuth submission veya RMF kanıtı | `GOOGLE_API_ACCESS.md`, `GOOGLE_SUBMISSION_EVIDENCE.md`, `SECURITY.md` | Başvuru paketi + sonuç kanıtı |
| Kullanıcı hakkı, legal hold veya veri ihlali bildirimi | `LEGAL.md`, `LEGAL_OPERATIONS_RUNBOOK.md`, `OPERATIONS.md`, `SECURITY.md` | Hukuk kararı + runbook/tatbikat |
| Retry, queue veya hata mesajı | `ERROR_HANDLING.md`, `RATE_LIMITS.md` | Hata matrisi/limit kararı |
| Onay ekranı veya başka UI | `PRODUCT.md`, `DESIGN.md`, `SECURITY.md` | Akış/kabul kriteri değiştiyse ilgili belge |
| Bekleyen onay bildirimi (email/Slack/webhook) | `NOTIFICATIONS.md`, `LEGAL.md`, `SECURITY.md` | Bildirim kararı + kanal onay/consent akışı |
| Log, metric, trace veya audit | `OBSERVABILITY.md`, `SECURITY.md` | Telemetry/audit şeması |
| Test/CI | `TESTING.md`, `DEPLOYMENT.md` | Test matrisi/pipeline |
| Deploy veya altyapı | `DEPLOYMENT.md`, `OPERATIONS.md`, `SECURITY.md` | Altyapı kararı/runbook |
| İzleme veya olay müdahalesi | `OBSERVABILITY.md`, `OPERATIONS.md`, `SECURITY.md` | Alarm/runbook/SLO |
| Clone, fetch, pull, commit, push veya PR | `REPOSITORY.md`, `SECURITY.md` | Remote/branch politikası değiştiyse `REPOSITORY.md` |
| Büyük veya geri döndürmesi zor karar | Tüm ilgili belgeler | `docs/decisions/` altında ADR |

## Belge sahipliği ve güncellik

- Her belgede `Durum`, `Son gözden geçirme` ve `Sonraki gözden geçirme` alanları bulunur.
- Kaynağa dayalı değişebilir kurallar doğrudan bağlantıyla desteklenir.
- En az üç ayda bir Google Ads, OAuth, Anthropic/MCP ve bağımlılık sürümleri yeniden kontrol edilir.
- `Taslak` kararlar uygulama yetkisi vermez. `Kabul edildi` kararlar bağlayıcıdır.
- Belge içinde `TBD` kalabilir; fakat ilgili `TBD` kapanmadan ona bağımlı üretim kodu yazılmaz.
- Geçici belge sahibi repository CODEOWNER `@kocennes`'tir. Güvenlik/auth/legal/deployment belgeleri için
  review hedefi 5 iş günü, diğer bağlayıcı belgeler için 10 iş günüdür; bu 7/24 destek taahhüdü değildir.
- Belge sahibi üç aylık review tarihini ve kaynak güncelliğini takip eder. Hukuki belgeler gerçek hukukçu
  onayı olmadan `Kabul edildi` yapılamaz.

## Değişiklik kontrol listesi

1. İşe karşılık gelen belgeleri oku.
2. Gereksinim ve güvenlik kurallarını test edilebilir kabul kriterlerine çevir.
3. Mimari bir karar varsa ADR oluştur.
4. Kodu ve testleri birlikte değiştir.
5. Belge bağlantılarını, örnekleri ve tarihleri doğrula.
6. PR açıklamasında güncellenen belgeleri ve yapılan varsayımları belirt.

## Açık sorular

- İkinci belge reviewer'ı ve tek-sahip bus-factor azaltma planı.
- External linkler vendor/network flakiness nedeniyle PR'ı bloklamaz: metadata/status/date/internal-link/
  matrix/ADR/encoding her PR'da zorunludur; dış link taraması üç aylık review'da bounded timeout/concurrency
  ile çalıştırılır ve bulgusu triage edilir.

## Güncelleme geçmişi

- 2026-07-22 — Faz 7.5: yeni `NOTIFICATIONS.md` matrise eklendi (bkz. o belgenin "bugün gerekli
  değil" kararı).
- 2026-07-22 — CODEOWNER tabanlı geçici sahiplik/review hedefleri ve CI/internal-vs-external link kapısı eklendi.
- 2026-07-17 — Public connector için Google access, directory submission ve legal kapıları eklendi.
