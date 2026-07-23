# Hukuki, gizlilik ve veri yaşam döngüsü kararları

**Durum:** Taslak — hukukçu incelemesi ve işletmeci bilgileri olmadan yayınlanamaz  
**Son gözden geçirme:** 2026-07-17  
**Sonraki gözden geçirme:** 2026-10-17

> Bu belge hukuki danışmanlık değildir. Uygulanacak ülke, işletmeci kimliği ve veri akışı hukukçu
> tarafından doğrulanmadan public lansman veya gerçek kullanıcı verisi işleme yapılmaz.

## Amaç

Ücretsiz ve herkese açık connector'ın hangi kullanıcı/Google Ads verisini hangi amaçla işlediğini,
kimlerle paylaştığını, ne kadar sakladığını, kullanıcı haklarını ve public politika gereksinimlerini belirlemek.

## Araştırma

- [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
  erişilen/kullanılan/saklanan/paylaşılan/silinen Google user data ile kullanıcı adına yapılan işlemlerin
  privacy policy'de açıkça belirtilmesini ve kullanımı açıklanan kullanıcı-facing amaçlarla sınırlar.
- Aynı Google User Data Policy, yeni/farklı veri kullanımında privacy açıklamasının güncellenmesini, kullanıcıya
  bildirim yapılmasını ve yeni kullanım başlamadan yeniden onay alınmasını; izinlerin gelecekteki olası özellikler
  için peşinen değil yalnız mevcut işlev için istenmesini şart koşar.
- Google [OAuth verification requirements](https://support.google.com/cloud/answer/13464321), privacy policy'nin
  doğrulanmış homepage domain'inde, homepage ve consent screen'de aynı URL ile bulunmasını; veri erişimi,
  kullanım, saklama, paylaşım ve Limited Use uyumunu açıklamasını ister.
- [Anthropic Software Directory Policy](https://support.claude.com/en/articles/13145358-anthropic-software-directory-policy),
  data collection/use/retention açıklayan erişilebilir privacy linki, doğrulanmış contact/support, minimum
  conversation data ve test hesabı gerektirir.
- KVKK [Aydınlatma Yükümlülüğü](https://www.kvkk.gov.tr/Icerik/2033/Aydinlatma-Yukumlulugu-), veri sorumlusu
  kimliği, amaç, alıcılar, yöntem/hukuki sebep ve ilgili kişi haklarının veri elde edilirken açıklanmasını ister.
- KVKK [silme/yok etme/anonimleştirme](https://www.kvkk.gov.tr/Icerik/2038/kisisel-verilerin-silinmesi-yok-edilmesi-veya-anonim-hale-getirilmesi),
  işleme sebebi bitince talep olmasa da verinin silinmesi/yok edilmesi/anonimleştirilmesini öngörür.
- AB kullanıcıları hedeflenirse [GDPR](https://eur-lex.europa.eu/eli/reg/2016/679/oj) controller identity,
  purpose/legal basis, recipient/transfer, retention ve data subject rights açıklamaları ile risk-temelli
  teknik/idari güvenlik gerektirir.
- [Google Ads API Terms](https://developers.google.com/google-ads/api/docs/api-policy/terms), API kullanımıyla
  kişisel veri işleniyorsa uygulanabilir Google Ads müşterisiyle GDPR uyumunu açıklayan yazılı sözleşme ve ilgili
  Google controller-controller/data-processing şartlarıyla tutarlılık öngörür. Bunun taraf rolleri ve Türkiye/
  AB uygulanabilirliği hukukçu tarafından müşteri onboarding'i öncesinde karara bağlanır.

## Karar

- Ürün ücretsizdir; ödeme, kart, abonelik veya faturalama verisi toplanmaz. Reklam/sponsorlu içerik sunulmaz.
- Google Ads verisi yalnız kullanıcının istediği reporting/analiz ve açıkça onayladığı hesap değişikliğini
  gerçekleştirmek için kullanılır. Satılmaz, reklam hedefleme profili oluşturmak veya model eğitmek için
  kullanılmaz.
- Claude/Anthropic'e yalnız tool çağrısını cevaplamak için gerekli minimum veri gönderilir. Veri akışında
  Google API → connector → kullanıcının Claude oturumu/Anthropic rolü açıkça privacy policy'de anlatılır;
  Anthropic bağımsız şart/politikalarına bağlantı verilir, onlar adına garanti verilmez.
- Saklanan minimum kategoriler: kullanıcı/connector subject'i, Google account eşlemesi, şifreli refresh token,
  izin metadata'sı, proposal/approval/execution ve güvenlik/audit. Raw performans snapshot'ı varsayılan kalıcı
  değildir; kısa cache/analiz retention kesinleşmeden gerçek veri alınmaz.
- Disconnect: Google token revoke, secret silme, planlı işleri durdurma. Account deletion: yasal zorunlu audit
  hariç kullanıcı ve Ads verisini belirlenen süre içinde silme; yedeklerde erişimi kapatıp retention sonunda yok etme.
- Kullanıcıya access/correction/deletion/objection/export ve consent/authorization withdrawal kanalı sağlanır.
  Talep kimliği güvenli biçimde doğrulanır ve işlem audit edilir.
- Subprocessor/veri aktarım envanteri (hosting, DB, secrets, telemetry, Anthropic, Google) ürün seçimiyle
  tamamlanır. Ülke/region ve transfer mekanizması hukukçu tarafından onaylanır.
- `PRIVACY_POLICY.md` ve `TERMS.md` kaynak taslaklardır; verified domain'de HTTPS olarak yayınlanan sürüm,
  yürürlük tarihi ve değişiklik geçmişiyle versionlanır. Maddi değişiklik kullanıcıya bildirilir.
- Yeni veri kategorisi, paylaşım, retention amacı veya Google scope'u yalnız privacy/data inventory güncellemesi,
  hukuk-güvenlik incelemesi, in-product zamanında bildirim ve gerekiyorsa yeniden kullanıcı onayı sonrasında açılır.

## Açık sorular

- Veri sorumlusu/işletmeci gerçek veya tüzel kişi, adres, ülke, support ve privacy contact.
- Hedef ülkeler, yaş sınırı, KVKK/GDPR/diğer hukuk kapsamı, VERBİS ve temsilci yükümlülüğü.
- Her veri kategorisinin kesin retention süresi ve hukuki dayanağı.
- Hosting/subprocessor listesi, region ve uluslararası aktarım mekanizmaları.
- Incident/breach bildirim prosedürü ve süreleri.
- Google Ads müşterisiyle gerekli controller/processor sözleşmesinin biçimi ve self-service kabul mekanizması.

Faz 11 karar girdileri ve hazırlık kanıtları `PRODUCTION_DATA_INVENTORY.md`, `SUBPROCESSORS.md`,
`LEGAL_REVIEW_PACKET.md`, `LEGAL_OPERATIONS_RUNBOOK.md` ve `GOOGLE_SUBMISSION_EVIDENCE.md` içinde tutulur.
Bu hazırlık belgeleri yukarıdaki açık soruları kapatmaz ve production işleme yetkisi vermez.

## Güncelleme geçmişi

- 2026-07-22 — Faz 11 veri envanteri, hukuk soru paketi, subprocessor kaydı, hak/ihlal runbook'u ve Google
  submission kanıt paketlerine çapraz bağlantı eklendi; dış kararlar açık tutuldu.

- 2026-07-17 — Google, Anthropic, KVKK ve GDPR kaynaklarıyla ilk veri/privaсy karar çerçevesi oluşturuldu.
- 2026-07-17 — Google Limited Use değişiklik/re-consent kapısı ve Google Ads kişisel veri sözleşmesi için
  hukuk incelemesi gereği eklendi.
