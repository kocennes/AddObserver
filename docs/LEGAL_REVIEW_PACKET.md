# İşletmeci ve hukukçu karar paketi

**Durum:** Taslak — ürün sahibi ve hukukçu yanıtı bekleniyor  
**Son gözden geçirme:** 2026-07-22  
**Sonraki gözden geçirme:** 2026-10-22

## Kullanım

Bu form Faz 11.1, 11.3, 11.4 ve 11.11'in dış bağımlılıklarını toplar. Boş alanlar varsayılmaz; cevaplayan,
tarih ve dayanak kanıtı kaydedilir. Hukukçu onayı olmadan public metinlerin durumu değiştirilemez.

## Ürün sahibinden zorunlu bilgiler

| Karar | Yanıt | Kanıt / yetkili | Etkilediği çıktı |
|---|---|---|---|
| Gerçek/tüzel işletmeci unvanı ve kayıt bilgisi | Bekliyor | Bekliyor | Privacy, Terms, Google başvuruları |
| Tebligat/iş adresi ve ülke | Bekliyor | Bekliyor | Privacy, Terms |
| Privacy, support, security/incident e-postaları | Bekliyor | Bekliyor | Policies, OAuth, runbook |
| Hedeflenen ve açıkça hedeflenmeyen ülkeler | Bekliyor | Bekliyor | Hukuk kapsamı, region, transfer |
| Minimum yaş ve çocuk verisi yaklaşımı | Bekliyor | Bekliyor | Privacy/onboarding |
| Governing law, venue, uyuşmazlık/consumer yaklaşımı | Bekliyor | Bekliyor | Terms |
| Source-code/IP lisansı ve feedback yaklaşımı | Bekliyor | Bekliyor | Terms |
| Production domain ve marka adı | Bekliyor | Bekliyor | OAuth/Google/Anthropic |
| Hosting bütçesi, region, RPO/RTO ve yetkililer | Bekliyor | Bekliyor | ADR-0008, subprocessors |

## Hukukçu karar listesi

1. KVKK/GDPR ve diğer hedef ülke kurallarının kapsamı; controller/processor rolleri veri kategorisi bazında.
2. Her envanter satırının hukuki dayanağı, retention, legal hold, backup purge ve hak talebi SLA'sı.
3. VERBİS, temsilci, DPA, controller-controller veya veri işleme talimatı gereklilikleri.
4. Hosting/Anthropic/Google/support aktarımında ülke, onward transfer ve KVKK/GDPR safeguard seçimi.
5. Privacy Policy'nin tüm bölümleri ve Google Limited Use açıklamasının yeterliliği.
6. Terms için acceptable use, üçüncü taraf şartları, SLA/availability, termination/export, IP/license,
   disclaimer, liability cap, indemnity, mandatory consumer rights, governing law ve dispute metni.
7. İhlalde ülke/risk bazlı regulator, ilgili kişi, Google ve Anthropic bildirim tetikleyicileri/süreleri.
8. Self-service kabulün sözleşme kurulması için yeterliliği; checkbox metni, yeniden kabul ve kanıt alanları.
9. Google Ads müşterisiyle yazılı controller/processor anlaşması ve varsa standart DPA eki.

## Public belge tamamlama kapısı

`PRIVACY_POLICY.md` ve `TERMS.md` içindeki hiçbir `[TBD]` tahminle kapatılmaz. Tamamlama PR'ı; imzalı/izlenebilir
hukuk kararı, envanter sürümü, subprocessor sürümü, effective date, policy version ve production URL kanıtını
birlikte göstermelidir. Ürünün ücretsiz ve ödeme verisi toplamayan modeli değiştirilemez.

## Self-service kabul kayıt tasarımı (implementasyon yetkisi vermez)

Hukuk onayından sonra değerlendirilecek minimum kayıt: `principal_id`, `terms_version`, `privacy_version`, UTC
`accepted_at`, acceptance action, locale, kanıt hash'i, gerekli ise yeniden kabul nedeni ve önceki sürüm. IP/user-agent
yalnız hukuk ve minimizasyon kararı açıkça gerektirirse tutulur. Kabul; OAuth izni veya Ads mutate onayıyla birleştirilmez.

## Değişiklik geçmişi

- 2026-07-22 — Faz 11 dış kararları için işletmeci formu, hukuk soruları ve kabul kanıt tasarımı hazırlandı.
