# Operasyon ve runbook

**Durum:** Kabul edildi  
**Son gözden geçirme:** 2026-07-22

## Amaç

Public connector'ın 7/24 işletimi, incident response, backup/restore ve Google/Anthropic uyum kontrollerini
tanımlamak.

## Araştırma

- Google Ads [rate limits](https://developers.google.com/google-ads/api/docs/productionize/rate-limits) queue,
  throttling ve scope bazlı toparlanma önerir.
- Anthropic [submission requirements](https://claude.com/docs/connectors/building/submission) server bakım,
  support, test account ve launch readiness kanıtları ister.

## Karar
**Sonraki gözden geçirme:** 2026-10-22

## Ortamlar

- `local`: `.env`, yalnız fake/mock servisler veya açıkça ayrılmış test hesabı.
- `test/staging`: üretimden ayrı cloud project, OAuth client, developer token erişimi, DB ve secret.
- `production`: yönetilen secrets manager/KMS, least-privilege servis kimliği, onaylı deployment.
- Üretim verisi alt ortamlara kopyalanmaz.

## Deployment kapısı

- Test/güvenlik taramaları başarılı.
- Migration ileri/geri alma planı denenmiş.
- Secret ve config referansları doğrulanmış; değerler çıktılanmamış.
- Google Ads API sürümü, quota bütçesi ve permissible use kontrol edilmiş.
- Dashboard/alert ve runbook değişiklikle birlikte hazır.
- Yazma özelliği feature flag/kill switch ile bağımsız kapatılabilir.

## Gözlemlenebilirlik

- Health/readiness: `/healthz` yalnız process liveness için `{"status":"ok"}` döndürür; `/readyz` DB erişimini
  doğrular ve bağımlılık kullanılamıyorsa secret/stack trace sızdırmadan `503 {"status":"unavailable"}` verir.
- Metrikler: analiz başarı/gecikme, pending approval yaşı, execution sonucu, Google hata sınıfı,
  quota/rate limit, queue depth, stale proposal ve audit yazma hatası.
- Trace/log correlation ID taşır; principal/customer değerleri erişim kontrollü metadata olur.
- Alarm: onaysız mutate denemesi, principal/account mismatch, audit failure, credential invalidation,
  tekrarlayan `RESOURCE_EXHAUSTED`, execution belirsizliği.
- SLO değerleri trafik gözlendikten sonra ADR ile belirlenir (`TBD`).

## Olay runbook'ları

### Genel servis bozulması

1. 5xx ve latency sinyallerini correlation/trace ile doğrula; response body veya credential toplama.
2. Son deployment/config değişikliğini belirle, gerekiyorsa trafiği sağlıklı sürüme yönlendir.
3. DB readiness, pool ve MCP session manager durumunu ayrı değerlendir; Google kesintisini connector
   readiness'ini global kapatmak için kullanma.
4. Etki kapsamını ve kullanıcı iletişimini incident kaydına geçir; iyileşmeyi aynı SLI ile doğrula.

### Onaysız veya şüpheli mutate

1. Global write kill switch'i kapat.
2. Credential ve ilgili servis kimliğini revoke/rotate et.
3. Audit + Google request ID ile kapsamı belirle; kayıtları değiştirme.
4. Google Ads mevcut durumunu salt okunur sorguyla doğrula.
5. Yetkili insan kararıyla geri al; yeni değişiklik de ayrıca audit edilir.
6. Kök neden, etki ve tekrar önleme aksiyonlarını kaydet.

### Credential sızıntısı

1. Secret/token'ı derhal revoke et; sonra rotate et.
2. Write yolunu ve planlı işleri durdur.
3. Secret erişim logları ile kod/CI/log sızıntısını tara.
4. Yeni credential'ı en dar yetkiyle dağıt ve eski referansı sil.
5. Etkilenen kullanıcıları ve gerekli paydaşları olay/hukuk politikasına göre bilgilendir.

### Google Ads quota/rate limit

1. Hata detayındaki `retry_delay` değerine uy; tüketiciyi kuyruğa geri al.
2. Principal/customer ve developer-token bazlı trafiği azalt; jitter'lı backoff uygula.
3. Kör mutate tekrarı yapma; mevcut durumu kontrol et.
4. Kalan 24 saatlik quota ve birikmiş iş etkisini görünür kıl.

### Audit deposu kullanılamıyor

1. Tüm write işlemlerini fail-closed durdur.
2. Read-only analiz devam edebiliyorsa bunu UI'da açıkça belirt.
3. Depoyu onar, bütünlüğü doğrula; kayıp audit'i tahminle üretme.
4. Write açılmadan önce kontrollü smoke test yap.

## Mock olay tatbikatı — 2026-07-22

`backend/src/operations/drills.py` ile gerçek secret/ağ/üretim hesabı kullanmadan beş senaryo çalıştırıldı:

| Senaryo | Aşamalar | Simüle süre | Sonuç |
|---|---|---:|---|
| Credential leak | detect, contain/revoke, evidence, communication, recovery, postmortem | 30 dk | geçti |
| Unauthorized mutate şüphesi | detect, kill switch, evidence, communication, recovery, postmortem | 30 dk | geçti |
| Audit outage | detect, write fail-closed, evidence, communication, recovery, postmortem | 30 dk | geçti |
| Google quota | detect, throttle/no blind retry, evidence, communication, recovery, postmortem | 30 dk | geçti |
| DB restore | detect, isolate, evidence, communication, restore verification, postmortem | 30 dk | geçti |

Tatbikat bulguları: genel servis bozulması runbook'u eksikti ve eklendi; Google dış kesintisinin `/readyz`'i
global kapatmaması netleştirildi; audit outage sırasında read-only yolun açık kalması korundu. Gerçek revoke,
restore, iletişim ve status-page entegrasyonu production sağlayıcısı/operasyon sahibi olmadan çalıştırılmadı.

## Operasyonel sahiplik kapısı

Production alert routing ve incident SLA henüz etkin değildir. Repo içinde gerçek kişi/kurum, nöbet kapasitesi,
support/security adresi veya status page bulunmadığından bunlar uydurulamaz. Minimum kabul kanıtı: primary ve
secondary on-call sahibi, izlenen support/security kanalı, escalation yetkilisi, saat dilimi/kapsama saati ve
gerçek kapasiteyle onaylanmış severity yanıt hedefleri. Bu kanıt gelene kadar “7/24 insan yanıtı” taahhüdü yoktur;
servis production/directory lansman kapısından geçemez.

## Yedekleme ve geri yükleme

- DB, audit ve secrets metadata için RPO/RTO üretim öncesi belirlenir (`TBD`).
- Şifreli yedek, ayrı erişim alanı ve düzenli restore tatbikatı zorunludur.
- Secret değeri yedekten dönüyorsa rotation ve erişim etkisi ayrıca test edilir.
- En az üç ayda bir restore ve write kill-switch tatbikatı yapılır.

## Periyodik kontroller

- Aylık: dependency/secret erişimi, başarısız auth, quota trendleri.
- Üç aylık: Google Ads API sürüm/limit/politika, Google OAuth, MCP güvenlik belgesi, restore ve rotation.
- Altı aylık: rol/üyelik ve kullanılmayan OAuth client/credential temizliği.

## Execution reconciliation bütçesi

Faz 8 execution açıldığında `unknown` sonuçlar 5 dakikada bir salt okunur provider durumu ile uzlaştırılır.
15 dakika çözülemeyen kayıt manual-review alarmı üretir; operatör hedef SLA'sı 4 saattir. Provider sonucu
belirsizken mutate yeniden gönderilmez.

## Faz 13.1 — production readiness review

Aşağıdaki 12 kategori, mevcut belgelerden bağımsız olarak tek tek yeniden doğrulandı (2026-07-22). **Sonuç:
production launch için hazır DEĞİL.** Sekiz kategori bloklu; hiçbiri varsayım/tahminle kapatılmadı, her
satırın kanıtı somut bir belge/kod/test referansına bağlıdır.

| # | Kategori | Kanıt | Verdict |
|---|---|---|---|
| 1 | Security threat model | `docs/SECURITY.md` "Uçtan uca tehdit modeli" — 14 tehditten 9'u kapalı; açık kalanlar üretim secrets/KMS sağlayıcısı (T1), RLS'in tüm production repository/app yollarına tam sarılması (T6, `todo.md` 4.3), fair-queue/rate limiting (T13, `todo.md` 6.7), WORM audit deposu (T10, `todo.md` 9.3) | ❌ Bloklu — dört artık risk sağlayıcı/altyapı kararı bekliyor |
| 2 | Google access/OAuth verification | `docs/GOOGLE_API_ACCESS.md` (Taslak), `docs/GOOGLE_SUBMISSION_EVIDENCE.md` 11.7-11.9 — developer token/OAuth verification/RMF paketleri hazırlandı ama hiçbiri Google'a gönderilmedi | ❌ Bloklu — Google Compliance sınıflandırması ve gönderim onayı yok |
| 3 | Legal | `LEGAL.md`, `PRIVACY_POLICY.md`, `TERMS.md` üçü de `DRAFT — NOT FOR PUBLICATION` | ❌ Bloklu — hukukçu incelemesi (`todo.md` 11.3/11.4/12.4) |
| 4 | Dependency scan | Bandit/detect-secrets/pip-audit ADR ile pinlendi, `backend/uv.lock` üretildi, `.github/workflows/ci.yml` bunları ayrı job'larda zorunlu kılıyor (`todo.md` 10.1/10.2, `[x]`) | ✅ Config/kod hazır — gerçek GitHub Actions çalıştırması bu repo push edilmeden kanıtlanamaz |
| 5 | Penetration/DAST | Henüz hiç çalıştırılmadı; kapsam/araç seçimi bu turda `todo.md` 13.6'da ele alınıyor | ❌ Bloklu — bkz. 13.6 |
| 6 | Restore/rotation tatbikatı | Beş senaryolu **mock** tatbikat yapıldı (`docs/OPERATIONS.md` "Mock olay tatbikatı", `backend/src/operations/drills.py`) — gerçek revoke/restore/status-page entegrasyonu yok | ⚠️ Kısmen — yalnız simülasyon, gerçek sağlayıcı yok |
| 7 | SLO/alarms | Metrik/alarm tanımları var ama SLO eşik değerleri `TBD` (gerçek trafik gözlenene kadar, `todo.md` 14.5) | ❌ Bloklu — henüz gerçek trafik yok |
| 8 | Runbooks | Beş runbook yazılı ve tatbik edildi (genel bozulma, onaysız mutate, credential sızıntısı, quota/rate limit, audit outage) | ✅ Hazır |
| 9 | Support | "Operasyonel sahiplik kapısı": gerçek on-call/support/security iletişim kanalı yok | ❌ Bloklu |
| 10 | Capacity | Google Ads Basic Access 15.000 işlem/24 saat tüm principal'lar için tek paylaşılan bütçe; principal bazlı adil bölüşüm henüz yok (T13) | ❌ Bloklu — Standard Access + fair-queue gerekir |
| 11 | Quota | Gerçek quota bütçesi Google'ın access-level kararına bağlı (`docs/GOOGLE_API_ACCESS.md` Taslak) | ❌ Bloklu — kategori 2 ile aynı kök neden |
| 12 | Directory approval | Faz 12.6 iç denetimi: 8 kategoriden 4'ü bloklu (legal, support, reviewer test hesabı, branding) — bkz. `docs/CONNECTOR_SUBMISSION.md` | ❌ Bloklu |

**Toplam:** 2/12 tam hazır (runbooks, dependency-scan config), 1/12 kısmen (restore/rotation — yalnız mock),
9/12 bloklu. Hiçbir bloklayıcı bu turda kapatılamaz çünkü hepsi ya gerçek dış onay (Google, hukukçu,
Anthropic) ya da henüz seçilmemiş bir sağlayıcı/kaynak (hosting, KMS, on-call) gerektiriyor. `todo.md`
13.4/13.5 (production deploy / kontrollü açılış) bu blokajlar kapanmadan **önerilmez**.

## Açık sorular

- On-call sahibi, support/security iletişim adresi ve incident SLA.
- Production sağlayıcısı, RPO/RTO ve public status page.

## Güncelleme geçmişi

- 2026-07-22 — Faz 13.1: 12 kategorilik production readiness review eklendi (security threat model,
  Google access/OAuth verification, legal, dependency scan, DAST, restore/rotation, SLO/alarms, runbooks,
  support, capacity, quota, Directory approval). Sonuç: launch için hazır değil, 9/12 kategori bloklu.
  Hiçbir blokaj bu turda kapatılamadı (hepsi dış onay veya seçilmemiş sağlayıcıya bağlı); `todo.md`
  13.4/13.5 bu yüzden önerilmedi.
- 2026-07-22 — Hukukçu onayı sonrası uygulanacak kullanıcı hakkı ve veri ihlali karar/tatbikat akışı için
  `LEGAL_OPERATIONS_RUNBOOK.md` bağlandı; varsayımsal bildirim süresi eklenmedi.

- 2026-07-22 — Faz 9 readiness sözleşmesi, genel bozulma runbook'u, beş secretsiz mock tatbikat sonucu ve
  gerçek sahiplik/SLA kanıt kapısı eklendi.
- 2026-07-17 — Public connector submission/availability ve principal izolasyon operasyonları eklendi.
- 2026-07-17 — Ürün sahibi onayıyla Kabul edildi durumuna geçirildi.
- 2026-07-17 — `/healthz` liveness ve `/readyz` DB readiness endpoint sözleşmesi eklendi.
