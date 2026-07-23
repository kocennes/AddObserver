# Production veri envanteri ve veri akış haritası

**Durum:** Taslak — production sağlayıcıları, retention ve hukuk dayanakları onaylanmadan gerçek veri işlenemez  
**Son gözden geçirme:** 2026-07-22  
**Sonraki gözden geçirme:** 2026-10-22

## Amaç

Faz 11.2 için sistemde bulunabilecek veri kategorilerini, yaşam döngülerini ve açık karar kapılarını tek yerde
tutmak. Bu belge veri örneği içermez; alan adları yalnız şema ve kabul edilmiş mimariden türetilmiştir.

## Akış haritası

1. Kullanıcı/Claude → connector OAuth: `principal_id`, connector session ve grant metadata'sı.
2. Connector → Google OAuth: authorization code; connector kasası ← şifreli refresh token.
3. Connector → Google Ads: doğrulanmış `customer_id` ile istek; minimum rapor sonucu connector'a döner.
4. Connector → Claude/Anthropic: yalnız istenen tool sonucunun gerekli alanları; token/secret gönderilmez.
5. Kullanıcı → approval UI: proposal kararı; yalnız onaylı ve yeniden doğrulanmış değişiklik Google Ads'e gider.
6. Tüm güvenlik ve yazma adımları → ayrı, append-only audit; operasyon sinyalleri → redacted telemetry.
7. Hak talebi → kimlik doğrulama → export/düzeltme/silme kuyruğu → audit → yedek süresi sonunda purge.

## Veri envanteri

`Aday` hukuki dayanaklar hukukçu kararı değildir. Kesin dayanak ve süre gelene kadar production işleme kapalıdır.

| Kategori / alan grubu | Kaynak | Amaç | Dayanak adayı | Scope / sınıf | Saklama | Alıcı / processor | Bölge / aktarım | Silme ve hak akışı |
|---|---|---|---|---|---|---|---|---|
| Connector kimliği (`principal_id`, issuer/subject hash'i) | Connector AS | İzolasyon, oturum, hesap bağlama | Sözleşme / meşru menfaat adayı | principal / confidential | Hukuk kararı bekliyor | Production DB adayı bekliyor | Region/transfer bekliyor | Hesap silme; dar legal hold hariç purge/export |
| OAuth grant/session metadata (`client_id`, scope, timestamps, status; secret olmayan hash'ler) | Claude + connector | Yetkilendirme ve güvenlik | Sözleşme / güvenlik yükümlülüğü adayı | principal / restricted | Hukuk kararı bekliyor | DB | Bekliyor | Revoke/disconnect; auditli silme |
| Google credential (`refresh_token`) | Google OAuth | Kullanıcının istediği Ads erişimi | Açık yetkilendirme + sözleşme adayı | principal / secret | Disconnect/revoke'a kadar; kesin üst sınır bekliyor | Ayrı KMS/secrets provider adayı bekliyor | Bekliyor | Önce revoke, sonra vault delete; backup purge |
| Credential lifecycle (`credential_id`, key version, created/used/revoked) | Connector | Rotasyon, güvenlik, kanıt | Güvenlik yükümlülüğü adayı | principal / restricted | Hukuk kararı bekliyor | DB + secrets metadata | Bekliyor | Token değerinden ayrı değerlendirilir |
| Ads account mapping (`customer_id`, manager relation, permission metadata) | Google Ads | Yetki doğrulama ve routing | Sözleşme adayı | principal+customer / confidential | Bağlantı süresi + kesin süre bekliyor | DB, Google | Google ve host bölgeleri bekliyor | Disconnect/account deletion/export |
| İstenen Ads raporları (kampanya, bütçe, metrik, keyword alanları) | Google Ads | Kullanıcıya rapor/analiz | Sözleşme / kullanıcı talebi adayı | principal+customer / confidential | Varsayılan kalıcı değil; bounded memory/cache süresi bekliyor | Connector, kullanıcı Claude oturumu, Anthropic | Anthropic ve host şartları bekliyor | Cache expiry + kullanıcı talebi; raw snapshot tutulmaz |
| Proposal/approval/execution (önce/sonra değer, rationale, karar, actor, zaman) | Claude/connector/kullanıcı/Google | İnsan onayı ve değişiklik uygulama | Sözleşme + kanıt yükümlülüğü adayı | principal+customer / restricted | Hukuk/audit süresi bekliyor | DB, Google (yalnız onaylı mutate) | Bekliyor | Export; legal hold/audit istisnasıyla silme |
| Audit (`event_type`, actor, customer, correlation, outcome, request ID) | Connector | Güvenlik, hesap verebilirlik | Hukuki yükümlülük / meşru menfaat adayı | restricted; append-only | Kesin WORM/retention bekliyor | Ayrı audit store adayı bekliyor | Bekliyor | Normal support silemez; süre/hold sonunda kontrollü purge |
| Uygulama logu/metric/trace (redacted teknik metadata) | Connector/platform | Güvenlik ve güvenilirlik | Meşru menfaat adayı | internal/confidential | Kısa kesin süre bekliyor | Logging/security provider adayı bekliyor | Bekliyor | TTL purge; hak talebinde kimlikle ilişkilendirilebilen kayıt aranır |
| Support talebi ve kimlik doğrulama kanıtı | Kullanıcı/support kanalı | Destek ve hak talepleri | Sözleşme / hukuki yükümlülük adayı | confidential/restricted | Sağlayıcı ve hukuk kararı bekliyor | Support/email provider adayı bekliyor | Bekliyor | Ticket/export/silme; erişim break-glass ve auditli |
| Backup (DB/audit/vault metadata; raw token ancak şifreli provider backup'ında) | Production platform | Felaket kurtarma | Güvenlik yükümlülüğü adayı | Kaynağın sınıfını miras alır | RPO/RTO ve purge penceresi bekliyor | Hosting/DB/secrets provider adayı bekliyor | Bekliyor | Aktif sistemden erişim hemen kapanır; backup expiry sonunda purge |

## Minimizasyon ve yasaklar

- Ödeme/kart/faturalama verisi toplanmaz; ürün ücretsizdir.
- Access token kalıcılaştırılmaz; authorization header, cookie, refresh token, tam prompt ve gereksiz reklam içeriği loglanmaz.
- Raw Ads snapshot varsayılan olarak saklanmaz ve Google verisi reklam profili/model eğitimi için kullanılmaz.
- Support rolü principalsiz arama/export yapamaz; başka principal erişimi süreli break-glass + çift onay + audit gerektirir.

## Production kapıları

- İşletmeci, hedef ülkeler, minimum yaş ve hukuk kapsamı.
- Her satır için kesin hukuki dayanak, retention, legal-hold ve backup purge süresi.
- Kabul edilmiş hosting/DB/secrets/logging/support sağlayıcısı, region, DPA/SCC veya uygulanabilir KVKK aktarım mekanizması.
- Hukukçu onaylı privacy/terms, hak talepleri ve controller/processor rolü.

## Kaynaklar

- [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy)
- [Google OAuth verification requirements](https://support.google.com/cloud/answer/13464321)
- [GDPR tam metin](https://eur-lex.europa.eu/eli/reg/2016/679/oj)
- [KVKK aydınlatma yükümlülüğü](https://www.kvkk.gov.tr/Icerik/2033/Aydinlatma-Yukumlulugu-)

## Değişiklik geçmişi

- 2026-07-22 — Faz 11.2 üretim veri envanteri ve uçtan uca akış haritası oluşturuldu.
