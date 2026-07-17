# Ürün gereksinimleri

**Durum:** Kabul edildi  
**Son gözden geçirme:** 2026-07-17  
**Sonraki gözden geçirme:** 2026-10-17

## Amaç

Her Google Ads kullanıcısının kendi hesabını Claude'a güvenli biçimde bağlayıp performans verisini
sorgulayabildiği, öneri alabildiği ve yalnız kendi açık onayıyla sınırlı değişiklik uygulayabildiği ücretsiz,
herkese açık bir Connectors Directory ürünü sağlamak.

## Araştırma

- Anthropic [Connectors overview](https://claude.com/docs/connectors/overview), remote MCP connector'ların
  Claude web, desktop, mobile ve Code yüzeylerinde araç/veri sağladığını açıklar.
- Anthropic [review criteria](https://claude.com/docs/connectors/building/review-criteria), dar read/write tool,
  doğru annotation, makul cevap boyutu, public docs ve dolu test hesabı bekler.
- Google Ads [RMF](https://developers.google.com/google-ads/api/docs/api-policy/rmf), üçüncü taraf reporting/full-
  service tool'ların sunduğu hiyerarşi ve işleve göre minimum özellikler taşımasını gerektirebilir.

## Karar

### Faz 1 — directory-ready reporting connector

- Public remote MCP + OAuth ile self-service Google Ads bağlantısı ve disconnect/delete.
- Kullanıcının erişebildiği customer account'ları listeleme/seçme.
- Account/campaign/ad group/keyword performansını dar, sayfalı ve tarih aralıklı okuma tool'ları.
- Kaynak metriklere dayalı yapılandırılmış öneri hazırlama; önerinin AI çıktısı olduğunun açık olması.
- En az üç belgelenmiş örnek prompt, actionable hata ve quota/backpressure görünürlüğü.
- Ücret, abonelik, ödeme ve reklam/sponsorlu içerik yoktur.

### Faz 1.1 — sınırlı yazma

Google Compliance/RMF sınıflandırması ve Anthropic review UX'i doğrulandıktan sonra yalnız allowlist işlemler:
campaign pause/enable ve bütçe güncelleme. Her biri ayrı destructive MCP tool, Claude confirmation ve backend
immutable approval/freshness kapısından geçer. Yeni campaign oluşturma sonraki fazdır ve her zaman `PAUSED` olur.

### Roller ve sınır

- **Son kullanıcı:** Kendi connector oturumu ve Google yetkisiyle erişebildiği hesapları okur/onaylar.
- **Support/security operator:** Kullanıcı Google verisini varsayılan göremez; gerekçeli, süreli break-glass ayrı
  onay/audit ister.
- **Anthropic reviewer:** Ayrılmış, sentetik verili Google Ads test hesabıyla tüm tool'ları test eder.
- Claude öneri üretir; kimlik, yetki, hesap sahipliği veya insan onayı kararı vermez.

### Değişmez kabul kriterleri

- Bir connector principal'ı başka kullanıcının Google credential/account/proposal/audit kaydına erişemez.
- Onay yoksa Google Ads mutate çağrısı sıfırdır; onay payload değişirse yeni onay gerekir.
- Kullanıcı her tool sonucunda account/customer ve tarih aralığını anlayabilir; stale veri belirtilir.
- Kullanıcı disconnect ile gelecek erişimi durdurabilir, account deletion talebi başlatabilir.
- Google verisi satılmaz, reklam profili/model eğitimi için kullanılmaz; minimum veri döndürülür/saklanır.
- Ürün public documentation, privacy, terms ve support URL'leri olmadan directory'ye gönderilmez.

## Açık sorular

- Public ürün adı/domain/support ve işletmeci kimliği.
- Faz 1'in reporting-only kalıp kalmayacağı; write'ın RMF sınıflandırmasından sonra açılması önerilir.
- Ücretsiz hizmetin abuse/kota için kullanıcı limiti ve fair-use politikası.
- Kullanıcıya ayrı web dashboard gerekip gerekmediği veya tüm deneyimin Claude içinde kalması.

## Güncelleme geçmişi

- 2026-07-17 — İç ajans aracı kapsamı public, self-service, ücretsiz directory connector olarak değiştirildi.
- 2026-07-17 — Ürün sahibi onayıyla Kabul edildi durumuna geçirildi.

