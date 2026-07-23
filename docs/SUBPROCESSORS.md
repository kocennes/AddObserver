# Subprocessor ve uluslararası aktarım kaydı

**Durum:** Taslak — production sağlayıcıları ve hukuk safeguard kararı bekleniyor  
**Son gözden geçirme:** 2026-07-22  
**Sonraki gözden geçirme:** 2026-10-22

## Karar

Hayali sağlayıcı yayımlanmaz. Aşağıdaki kayıt, yalnız fiilen kullanılan dış hizmetleri ve henüz seçilmemiş
production kategorilerini ayırır.

| Sağlayıcı / kategori | Rol ve amaç | Veri | Ülke/region | DPA / transfer safeguard | Durum |
|---|---|---|---|---|---|
| Google LLC / Google Ads ve OAuth | Bağımsız hizmet; yetkilendirme ve Ads işlemleri | OAuth/Ads verisi | Kullanıcı ve Google altyapısına bağlı | Google şartları + hukuk rol kararı bekliyor | Kullanılan upstream; production hukuk incelemesi açık |
| Anthropic / Claude | Kullanıcının seçtiği Claude deneyimine minimum tool sonucu | İstenen minimum Ads/tool sonucu | Kullanıcının planı ve Anthropic altyapısına bağlı | Anthropic şartları + hukuk rol/transfer kararı bekliyor | Zorunlu ürün entegrasyonu; kayıtlar bekliyor |
| Hosting/compute | Public MCP uygulaması | Request metadata, geçici işleme | Seçilmedi | DPA/aktarım bekliyor | ADR-0008 önerildi; sağlayıcı değildir |
| Managed PostgreSQL | Uygulama ve audit metadata | Envanterdeki kalıcı DB alanları | Seçilmedi | DPA/aktarım bekliyor | Seçilmedi |
| KMS/secrets | Şifreli refresh token ve key metadata | Secret/restricted | Seçilmedi | DPA/aktarım bekliyor | Seçilmedi |
| Logging/security | Redacted log/metric/alert | Teknik metadata | Seçilmedi | DPA/aktarım bekliyor | Seçilmedi |
| Email/support | Support ve hak talepleri | İletişim/ticket/kimlik kanıtı | Seçilmedi | DPA/aktarım bekliyor | Seçilmedi |

## Yayın ve değişiklik prosedürü

Production sağlayıcısı ancak sözleşme sahibi, hizmet amacı, veri kategorisi, region, alt işleyen listesi,
retention/deletion, breach bildirimi, DPA ve uygulanabilir SCC/KVKK mekanizması hukukçu tarafından doğrulandıktan
sonra tabloya adıyla girer. Maddi değişiklik önceden tanımlanmış bildirim süresine ve gerekiyorsa itiraz/termination
hakkına tabidir; bu süre hukukçu kararı beklemektedir. Güncel sürüm public privacy sayfasından linklenir.

## Kaynaklar

- [KVKK veri sorumlusundan veri işleyene standart sözleşme](https://www.kvkk.gov.tr/Icerik/7931/Kisisel-Verilerin-Yurt-Disina-Aktarilmasinda-Kullanilacak-Standart-Sozlesme-2-Veri-Sorumlusundan-Veri-Isleyene-)
- [GDPR, özellikle Madde 28 ve Bölüm V](https://eur-lex.europa.eu/eli/reg/2016/679/oj)

## Değişiklik geçmişi

- 2026-07-22 — Faz 11.6 gerçek/kategorik subprocessor kaydı ve yayın kapısı oluşturuldu.
