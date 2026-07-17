# Operasyon ve runbook

**Durum:** Kabul edildi  
**Son gözden geçirme:** 2026-07-17

## Amaç

Public connector'ın 7/24 işletimi, incident response, backup/restore ve Google/Anthropic uyum kontrollerini
tanımlamak.

## Araştırma

- Google Ads [rate limits](https://developers.google.com/google-ads/api/docs/productionize/rate-limits) queue,
  throttling ve scope bazlı toparlanma önerir.
- Anthropic [submission requirements](https://claude.com/docs/connectors/building/submission) server bakım,
  support, test account ve launch readiness kanıtları ister.

## Karar
**Sonraki gözden geçirme:** 2026-10-17

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

- Metrikler: analiz başarı/gecikme, pending approval yaşı, execution sonucu, Google hata sınıfı,
  quota/rate limit, queue depth, stale proposal ve audit yazma hatası.
- Trace/log correlation ID taşır; principal/customer değerleri erişim kontrollü metadata olur.
- Alarm: onaysız mutate denemesi, principal/account mismatch, audit failure, credential invalidation,
  tekrarlayan `RESOURCE_EXHAUSTED`, execution belirsizliği.
- SLO değerleri trafik gözlendikten sonra ADR ile belirlenir (`TBD`).

## Olay runbook'ları

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

## Yedekleme ve geri yükleme

- DB, audit ve secrets metadata için RPO/RTO üretim öncesi belirlenir (`TBD`).
- Şifreli yedek, ayrı erişim alanı ve düzenli restore tatbikatı zorunludur.
- Secret değeri yedekten dönüyorsa rotation ve erişim etkisi ayrıca test edilir.
- En az üç ayda bir restore ve write kill-switch tatbikatı yapılır.

## Periyodik kontroller

- Aylık: dependency/secret erişimi, başarısız auth, quota trendleri.
- Üç aylık: Google Ads API sürüm/limit/politika, Google OAuth, MCP güvenlik belgesi, restore ve rotation.
- Altı aylık: rol/üyelik ve kullanılmayan OAuth client/credential temizliği.

## Açık sorular

- On-call sahibi, support/security iletişim adresi ve incident SLA.
- Production sağlayıcısı, RPO/RTO ve public status page.

## Güncelleme geçmişi

- 2026-07-17 — Public connector submission/availability ve principal izolasyon operasyonları eklendi.
- 2026-07-17 — Ürün sahibi onayıyla Kabul edildi durumuna geçirildi.
