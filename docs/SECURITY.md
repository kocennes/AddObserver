# Güvenlik standardı

**Durum:** Kabul edildi  
**Son gözden geçirme:** 2026-07-17  
**Sonraki gözden geçirme:** 2026-10-17

## Amaç

Bu belge bütün backend, UI, MCP, veri ve operasyon değişiklikleri için zorunlu güvenlik
tabanını ve değişmez kontrol kapılarını belirler. Sistem internete açık ve bilinmeyen sayıda dış kullanıcının
uzun ömürlü Google credential'larını tuttuğu için hiçbir ağ, client veya kullanıcı güvenilir varsayılmaz.

## Araştırma

- Google'ın güncel [OAuth güvenlik önerileri](https://developers.google.com/identity/protocols/oauth2/resources/best-practices)
  token şifreleme, secret manager, revoke, `state` ve DPoP kontrollerinin temelidir.
- Google Ads [access/policy](https://developers.google.com/google-ads/api/docs/api-policy/access-levels),
  [quota](https://developers.google.com/google-ads/api/docs/best-practices/quotas) ve
  [rate limit](https://developers.google.com/google-ads/api/docs/productionize/rate-limits) belgeleri
  developer token, permissible use ve trafik kontrollerinin kaynağıdır.
- [MCP Security Best Practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices)
  confused deputy, token passthrough, SSRF ve session saldırılarını; OWASP'nin
  [Multi-Tenant](https://cheatsheetseries.owasp.org/cheatsheets/Multi_Tenant_Security_Cheat_Sheet.html),
  [Secrets](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html) ve
  [Logging](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html) rehberleri izolasyon,
  credential ve audit kontrollerini açıklar.
- Google Ads [credential güvenliği](https://developers.google.com/google-ads/api/docs/productionize/secure-credentials),
  `adwords` scope'unun restricted olduğunu, production öncesi verification'ı, developer token'ın secret
  manager'da tutulmasını ve çok kullanıcılı uygulamada user token izolasyonunu açıkça ister.
- Google Ads [2-step verification gereksinimi](https://developers.google.com/google-ads/api/docs/oauth/security-requirements),
  21 Nisan 2026'dan başlayarak user-authentication akışında yeni refresh token üretimi için 2SV zorunluluğunu
  ve `TWO_STEP_VERIFICATION_NOT_ENROLLED` hata durumunu tanımlar.

## Karar

Aşağıdaki kurallar kabul edilmiş güvenlik standardıdır. Ayrıntılı uygulama kararları `AUTH.md`,
`DATABASE.md`, `API_DESIGN.md`, `ERROR_HANDLING.md`, `RATE_LIMITS.md`, `OBSERVABILITY.md` ve
`DEPLOYMENT.md` içinde yer alır.

## Değişmez güvenlik ilkeleri

- Canlı Google Ads hesabına insan onayı olmadan hiçbir mutate isteği gönderilmez.
- Yeni kampanyalar `PAUSED` oluşturulur.
- Okuma ve yazma dahil her işlem doğrulanmış connector `principal_id` ve `customer_id` bağlamına sahip olur.
- Secret, token, authorization code ve müşteri reklam içeriği loglanmaz veya modele gereksiz yere verilmez.
- Her yazma; aktör/principal, onaylayan, hesap, önceki/sonraki değer, zaman, korelasyon kimliği ve
  Google `request_id` ile değiştirilemez audit olayına dönüşür.
- Model çıktısı güvenilmeyen girdidir. Model hiçbir zaman doğrudan API yetkisi veya credential almaz.
- Üretimde fail-open davranışı yoktur: principal, hesap sahipliği, onay veya audit doğrulanamazsa yazma reddedilir.

## OAuth 2.0 ve token yaşam döngüsü

Google'ın [OAuth web server akışı](https://developers.google.com/identity/protocols/oauth2/web-server)
ve [OAuth güvenlik önerileri](https://developers.google.com/identity/protocols/oauth2/resources/best-practices)
uygulanır.

- Authorization Code akışı, tam özellikli sistem tarayıcısı, kesin eşleşen HTTPS redirect URI ve
  tahmin edilemez tek kullanımlık `state` kullanır. `state` kısa ömürlüdür ve callback'te tüketilir.
- Yalnız gereken scope istenir; çevrimdışı işler gerekiyorsa `access_type=offline` kullanılır.
- Refresh token üretimde secrets manager/KMS destekli bir kasada, uygulama verisinden ayrı ve
  şifreli saklanır. Veritabanında yalnız kasa referansı bulunur.
- Token kaydı `principal_id`, Google Ads hesap kapsamı, oluşturma/son kullanım zamanı, durum ve key version
  ile bağlanır. Bir kullanıcının token'ı başka principal için çözülemez.
- Access token kalıcılaştırılmaz; mümkün olduğunca bellekte kısa süre tutulur. Token ve OAuth client
  secret hiçbir log, exception, trace, prompt veya frontend cevabına girmez.
- Revocation/`invalid_grant` durumunda credential pasifleştirilir, planlı işler durur ve yeniden
  yetkilendirme istenir. İşten ayrılma ve müşteri kapatma sürecinde revoke + kalıcı silme yapılır.
- Google OAuth onboarding 2SV gereksinimini önceden açıklar. `TWO_STEP_VERIFICATION_NOT_ENROLLED` güvenli ve
  actionable bir yeniden-yetkilendirme hatasına çevrilir; başarısız çağrı döngüsü oluşturulmaz.
- DPoP, Google'ın 2026 önerisine uygun olarak tasarım aşamasında değerlendirilir. Kullanılan resmi
  Python kitaplıkları ve çalışma ortamı güvenli uygulamayı desteklemiyorsa karar ADR ile belgelenir.
- OAuth client ve secret erişimleri en az yetkiyle sınırlandırılır; kullanılmayan client'lar silinir.

## Secret yönetimi

[OWASP Secrets Management](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)
rehberi tabandır.

- Yerelde yalnız `.env`; üretimde yönetilen secrets manager kullanılır. `.env` commit edilmez.
- Secret değerleri kodda, fixture'da, ekran görüntüsünde, dokümanda veya CI değişken çıktısında yer almaz.
- Geliştirme/test/üretim secret'ları ve şifreleme anahtarları ayrıdır.
- Erişim uygulama servis kimliğine verilir; insan erişimi süreli, gerekçeli ve auditli olur.
- Rotasyon runbook'u her secret türü için sahip, tetikleyici, süre ve geri alma adımı tanımlar.
- Sızıntıda önce revoke/rotate, sonra etki analizi ve geçmiş temizliği yapılır; yalnız Git geçmişini
  silmek credential'ı güvenli hale getirmez.
- CI ve pre-commit secret taraması zorunlu kalite kapısıdır.

## Kullanıcı ve Google Ads hesap izolasyonu

[OWASP Multi-Tenant Security](https://cheatsheetseries.owasp.org/cheatsheets/Multi_Tenant_Security_Cheat_Sheet.html)
önerileri savunma katmanı olarak uygulanır.

- `principal_id` tool/request argümanından alınmaz; doğrulanmış connector access token subject'inden türetilir.
- `customer_id`, principal'ın Google credential'ıyla erişebildiği doğrulanmış hesap eşlemesine karşı kontrol edilir.
- Repository sorguları principal filtresini zorunlu parametre yapar; filtersiz genel sorgu public API olamaz.
- Cache key, queue message, blob path, idempotency key ve audit kaydı principal ile namespace edilir.
- Credential çözümleme `principal_id + credential_id` ile yapılır ve hesap erişimi ikinci kez doğrulanır.
- Support erişimi süreli break-glass onayı gerektirir ve ayrı audit olayı üretir.
- İzolasyon testleri IDOR, değiştirilmiş customer ID, yanlış token ve cache/queue karışması vakalarını kapsar.

## Google Ads API güvenliği ve politika

- Developer token yalnız backend'de bulunur; Google'ın [access level ve permissible use](https://developers.google.com/google-ads/api/docs/api-policy/access-levels)
  kurallarına uygun özelliklerde kullanılır.
- Developer token repository, CI çıktısı, frontend, MCP sonucu veya kullanıcıya ait config içinde bulunmaz;
  üretim secrets manager'ında ayrı erişim politikasıyla tutulur. Sızıntıda API Center'dan derhal reset edilir.
- Basic Access için güncel sınır 15.000 işlem/kayan 24 saattir; public GA için Standard Access hedeflenir.
  Tüm seviyelerde sistem rate limitleri ayrıca geçerlidir; bütçe principal/customer ve developer token
  düzeyinde izlenir. RMF ve permissible use kararları `GOOGLE_API_ACCESS.md` kapısından geçer.
- [API quota sınırları](https://developers.google.com/google-ads/api/docs/best-practices/quotas) ve
  [rate limit rehberi](https://developers.google.com/google-ads/api/docs/productionize/rate-limits)
  uyarınca kontrollü concurrency, kuyruk, jitter'lı exponential backoff ve sunucunun `retry_delay`
  değeri kullanılır. Mutate otomatik tekrarları idempotency/sonuç doğrulaması olmadan yapılmaz.
- Testler yalnız Google Ads test hesabı veya mock ile çalışır. Gerçek müşteri hesabı CI'da erişilebilir olmaz.
- İstek hatalarında Google'ın önerdiği `request_id` kaydedilir; credential ve hassas payload kaydedilmez.

## İnsan onayı ve yazma güvenliği

Öneri ile uygulama iki ayrı yetkidir.

1. Model yapılandırılmış bir öneri üretir; allowlist şeması dışında alan reddedilir.
2. Backend mevcut değeri tekrar okur, sınırları ve principal↔Google account erişimini doğrular.
3. Kullanıcı değişiklik önizlemesinde hesap, işlem, eski/yeni değer, etki ve risk uyarısını görür.
4. Onay kaydı immutable `proposal_hash`, kapsam, onaylayan ve sona erme zamanı taşır.
5. Uygulama anında hash ve mevcut durum yeniden doğrulanır. Değer değişmişse eski onay geçersizdir.
6. Yüksek etkili işlemler (hesap bütçesi, toplu silme/devre dışı bırakma) ikinci onay gerektirir.
7. Her yazma öncesi audit kaydı açılır; sonuç başarı/başarısızlık olarak tamamlanır.

## MCP ve model güvenliği

[MCP Security Best Practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices)
ve güncel [MCP Authorization](https://modelcontextprotocol.io/specification/2025-03-26/basic/authorization)
gereksinimleri uygulanır.

- MCP sunucusu upstream Google token'ını MCP client'a iletmez; token passthrough yasaktır.
- Her tool en dar scope'a, katı JSON Schema'ya, uzunluk/sayı allowlist'lerine ve timeout'a sahiptir.
- Tool açıklaması veya model talimatı yetkilendirme değildir. Yetki backend policy katmanında doğrulanır.
- Reklam metni, arama terimi, URL ve üçüncü taraf veri prompt injection taşıyabilir; bunlar talimat değil
  veri olarak etiketlenir ve tool çağırma/yazma yetkisini değiştiremez.
- Modelin ürettiği URL fetch edilmez. Gerekirse SSRF korumalı allowlist, DNS/IP yeniden doğrulama,
  redirect sınırı ve özel ağ engeli uygulanır.
- Remote transport HTTPS kullanır; PKCE, kesin redirect URI doğrulaması, kısa token ömrü ve session
  binding zorunludur. Session ID kimlik doğrulama yerine geçmez ve kullanıcılar arasında paylaşılmaz.
- Tool sonucu minimum veridir; erişim ve yazma denemeleri audit edilir.

## Girdi, çıktı ve web güvenliği

- API sınırında tür, format, boyut, enum ve iş kuralı doğrulaması yapılır; GAQL parçaları kullanıcı/model
  metninden string birleştirme ile oluşturulmaz.
- State-changing endpoint'ler CSRF koruması, yeniden kimlik doğrulama gereken risk eşiği ve idempotency
  anahtarı kullanır. CORS açık allowlist'tir.
  - Uygulama: `/approvals` (docs/AUTH.md "Approval-UI web girişi") -- session cookie
    `HttpOnly`+`SameSite=Strict`+(yerel ortam dışında) `Secure`; her karar POST'u ayrıca
    session'dan bağımsız üretilmiş bir `csrf_token`'ı `secrets.compare_digest` ile doğrular;
    `/logout` ve `/disconnect` aynı synchronizer token desenini kullanır. Token değerleri
    yalnız SHA-256 hash'i olarak saklanır.
- Public HTTP cevapları varsayılan olarak `Cache-Control: no-store`, `Referrer-Policy: no-referrer`,
  `X-Content-Type-Options: nosniff` ve form-only katı CSP (`default-src 'none'`, `script-src 'none'`,
  `form-action 'self'`, `frame-ancestors 'none'`) taşır.
- Hata cevapları secret, SQL, stack trace veya başka kullanıcı/hesap varlığını açığa çıkarmaz.
- Bağımlılıklar kilitlenir, düzenli taranır ve desteklenen sürümlerde tutulur.

## Audit, loglama ve veri koruma

[OWASP Logging](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html) taban alınır.

- Uygulama logu ile audit kaydı ayrıdır. Audit append-only/WORM benzeri korumaya ve bütünlük kontrolüne
  sahip olmalıdır; normal uygulama rolü geçmiş audit olayını güncelleyemez.
- Zorunlu alanlar: UTC zaman, event type, actor/service/principal, customer, proposal/approval, correlation,
  outcome, reason code ve varsa Google request ID.
- İnsan onay/red kararı state-changing write sayılır; proposal status, `approval` satırı ve
  `approval.decided` audit_event'i aynı transaction içinde yazılır. Audit yazılamazsa karar
  fail-closed kalır.
- Token, secret, authorization header, cookie, tam prompt, gereksiz PII ve ödeme verisi loglanmaz.
- Saatler senkronize edilir; erişim, export ve retention işlemleri audit edilir.
- Retention süresi hukuk/iş ihtiyacıyla üretim öncesi kararlaştırılır (`TBD`); süresiz saklama varsayılmaz.

## Olay müdahalesi ve kalite kapıları

- Credential sızıntısı, kullanıcılar arası erişim denemesi, onaysız mutate ve audit yazamama güvenlik olayıdır.
- Böyle bir olayda yazma yolu otomatik kapanır, nöbetçi bilgilendirilir ve `OPERATIONS.md` runbook'u izlenir.
- Merge için secret scan, SAST/dependency scan, principal izolasyon testleri, onaysız yazma testi ve audit alan
  testleri başarılı olmalıdır.
- Üretim öncesi tehdit modeli ve en az bir geri yükleme/credential rotation tatbikatı tamamlanır.
- Restricted-scope OAuth verification ve Google'ın gerekli gördüğü bağımsız security assessment tamamlanmadan
  gerçek dış kullanıcı verisiyle production açılmaz; gerekiyorsa değerlendirme her yıl yenilenir.

## Açık sorular

- Üretim secrets manager ve KMS sağlayıcısı.
- Public kullanıcı ölçeğinde RLS/pool performansı ve gerekirse sharding sınırı.
- Audit retention süresi ve WORM sağlayıcısı.
- DPoP desteği ve uygulanabilirliği.

## Güncelleme geçmişi

- 2026-07-17 — `/approvals` insan onay yüzeyi için CSRF token + cookie özniteliği kararı eklendi
  (docs/AUTH.md).
- 2026-07-17 — Public HTTP güvenlik header'ları ve `/logout` CSRF doğrulaması uygulama standardına eklendi.
- 2026-07-17 — 2026 Google OAuth/Ads, MCP ve OWASP kaynaklarıyla ilk güvenlik standardı oluşturuldu;
  belge zorunlu araştırma/karar formatına getirildi.
- 2026-07-17 — Restricted `adwords` scope, production verification/security assessment, developer-token
  koruması ve Google Ads 2SV hata akışı eklendi.
