# Güvenlik standardı

**Durum:** Kabul edildi  
**Son gözden geçirme:** 2026-07-18
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
- DPoP (RFC 9449) şimdilik uygulanmaz — ne Google upstream refresh token'ında ne connector'ın kendi
  access/refresh token'ında. Gerekçe, araştırma ve yeniden değerlendirme tetikleyicileri
  `docs/decisions/0004-dpop-deferred.md` içindedir: Google'ın resmi Python kütüphaneleri
  (`google-auth`, `google-auth-oauthlib`) DPoP proof üretimini desteklemiyor, MCP Authorization
  spesifikasyonu (2025-11-25) DPoP'tan hiç bahsetmiyor ve bu mimaride Google refresh token'ı zaten
  hiçbir client'a çıkmadan yalnız backend vault'unda şifreli tutuluyor (DPoP'un hedeflediği "public
  client'ta tutulan token çalınır" tehdidi yapısal olarak yok). Mevcut kısa ömürlü audience-bound
  access token + refresh rotation/family reuse-detection (`db/oauth_store.py::rotate`/
  `revoke_family`) bu artışta yeterli kabul edildi.
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
  redirect sınırı ve özel ağ engeli uygulanır. DNS çözümlemesi doğrulama ve bağlantı arasında
  tekrarlanmaz (DNS-rebinding TOCTOU): bağlantı, doğrulanmış tam IP'ye pinlenir; orijinal hostname
  yalnız `Host` başlığı ve TLS SNI için korunur; yönlendirme (redirect) hiç takip edilmez; cevap
  gövdesi boyut sınırlıdır ve yalnız `application/json` Content-Type kabul edilir
  (`backend/src/auth/cimd.py`).
- Remote transport HTTPS kullanır; PKCE, kesin redirect URI doğrulaması, kısa token ömrü ve session
  binding zorunludur. Session ID kimlik doğrulama yerine geçmez ve kullanıcılar arasında paylaşılmaz.
- Tool sonucu minimum veridir; erişim ve yazma denemeleri audit edilir.

## Girdi, çıktı ve web güvenliği

- API sınırında tür, format, boyut, enum ve iş kuralı doğrulaması yapılır; GAQL parçaları kullanıcı/model
  metninden string birleştirme ile oluşturulmaz.
- Connector OAuth AS'ın public `/authorize`, `/authorize/consent`, `/google/callback` ve `/token`
  girdileri de aynı kurala tabidir: `client_id` DNS/ağ çağrısından önce uzunluk sınırıyla reddedilir
  (`backend/src/auth/cimd.py`), `state`/`scope` sınır değerlidir ve `code_challenge`/`code_verifier`
  RFC 7636'nın 43-128 karakterlik base64url biçimini doğrulanmadan hash karşılaştırmasına girmez
  (`backend/src/auth/domain.py`); `transaction_id`/`state` opaque kimlikleri DB sorgusundan önce
  URL-safe/uzunluk doğrulamasından geçer (docs/AUTH.md "Saldırı kontrolleri").
- State-changing endpoint'ler CSRF koruması, yeniden kimlik doğrulama gereken risk eşiği ve idempotency
  anahtarı kullanır. CORS açık allowlist'tir.
  - Uygulama: `/approvals` (docs/AUTH.md "Approval-UI web girişi") -- session cookie
    `HttpOnly`+`SameSite=Strict`+(yerel ortam dışında) `Secure`; her karar POST'u ayrıca
    session'dan bağımsız üretilmiş bir `csrf_token`'ı hashleyip saklı hash'e karşı
    `secrets.compare_digest` ile doğrular; `/logout` ve `/disconnect` aynı synchronizer token
    desenini kullanır. Session ve CSRF token değerleri yalnız SHA-256 hash'i olarak saklanır.
    `/logout` yalnız isteği yapan çerezi iptal eder (diğer cihazlardaki oturumlar etkilenmez);
    `/disconnect` ise principal'ın **tüm** `web_session` satırlarını iptal eder -- destructive/
    geri döndürülemez bir eylem olduğu için eşzamanlı hiçbir tarayıcı oturumu ayakta kalmaz.
- Public HTTP cevapları varsayılan olarak `Cache-Control: no-store`, `Referrer-Policy: no-referrer`,
  `X-Content-Type-Options: nosniff` ve form-only katı CSP (`default-src 'none'`, `script-src 'none'`,
  `form-action 'self'`, `frame-ancestors 'none'`) taşır; `environment` `local` dışındaysa ayrıca
  `Strict-Transport-Security: max-age=63072000; includeSubDomains` eklenir (`backend/src/app.py`).
  Bu karar `X-Forwarded-Proto` gibi proxy başlıklarına güvenmez -- DEPLOYMENT.md'nin proxy topolojisi
  ADR'i kabul edilene kadar yalnız yerel config (`APP_ENVIRONMENT`) kullanılır.
- `Host` başlığı `TrustedHostMiddleware` ile `PUBLIC_BASE_URL`'in hostname'ine (veya açıkça
  ayarlanmış `ALLOWED_HOSTS`'a) karşı doğrulanır; eşleşmeyen istek 400 ile reddedilir. CORS varsayılan
  olarak kapalıdır (`CORS_ALLOWED_ORIGINS` boşsa çapraz-origin erişim yoktur); yalnız açık, `*`
  olmayan bir allowlist eklenebilir ve `Access-Control-Allow-Credentials` hiçbir zaman gönderilmez.
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
- Secret tasiyan her nesne (`Settings`, `GoogleAdsCredentials`, `GoogleTokenResult`,
  `AuthorizationCode`/`AccessToken`/`RefreshToken`, `WebSession`, `WebSessionIssued`) ilgili
  alanlarda `dataclasses.field(repr=False)` tasir -- yapisal loglama henuz eklenmedi (Faz 9.1
  acik) ama bu nesneler neredeyse her istek yolundan gectigi icin, ileride eklenecek bir
  `logger.debug(...)`, bir f-string veya yakalanmamis bir exception'in traceback'indeki yerel
  degiskenin `repr()`/`str()`'ye dusmesi tek basina TUM secret'i sizdirir; bu savunma bunu
  onceden kapatir (kanit: `backend/tests/test_secret_redaction.py`).
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

## Uçtan uca tehdit modeli

Bu bölüm `ARCHITECTURE.md`'deki güven sınırlarını STRIDE benzeri bir yöntemle tehdit/azaltım/kanıt
üçlüsüne çevirir. Amaç, hangi tehditlerin bugün gerçek kodla kapatıldığını, hangilerinin henüz
uygulanmamış bir bileşene (queue, structured logging, execution) bağlı olduğu için "kapsam dışı/
henüz yok" sayıldığını ve hangilerinin bilinçli artık risk olarak açık kaldığını tek yerde
görünür kılmaktır. Yeni bir güven sınırı (queue, execution adapter, managed secrets/KMS, structured
logging) eklendiğinde bu tablo aynı değişiklikte güncellenir.

### Güven sınırları

| # | Bileşen | Güvenilir mi | Not |
|---|---|---|---|
| B1 | Claude MCP client | Hayır | Dışarıdan gelen herhangi bir MCP client; tool argümanı ve rationale/metin alanları untrusted girdi. |
| B2 | Public MCP resource server (`backend/src/mcp`) | Kısmen | Bizim kodumuz ama public internete açık; Google token'ı hiç tutmaz. |
| B3 | Connector Authorization Server (`backend/src/auth`, hand-rolled, ADR-0002) | Kısmen | Bizim kodumuz; state/PKCE/redirect_uri/resource binding'i taşır. |
| B4 | Google OAuth (upstream) | Hayır (dış taraf) | Yalnız Google'ın imzaladığı sonucu kabul ederiz; kendi `state`/PKCE'imizle bağlarız. |
| B5 | Approval tarayıcısı (`/login` + `/approvals`, insan) | Kısmen | `adwords` scope istemez, Google Ads'e yazmaz; yalnız yerel proposal karar kaydı. |
| B6 | DB (SQLite prototip; production Postgres — `todo.md` 4.x) | Evet (uygulama süreci) | Principal-scoped repository filtreleri birinci katman; production Alembic RLS migration'ı, transaction-local principal context helper'ı, PostgreSQL transaction helper'ı ve ilk SQLAlchemy repository dilimi (`principal`, `oauth_client_grant`, `ads_account`, `oauth_credential`) eklendi. `ADDOBSERVER_POSTGRES_TEST_DSN` ile çalışan canlı PostgreSQL izolasyon testi var, fakat bu ortamda DSN olmadığı ve kalan production repository/app yolları henüz SQLAlchemy'ye taşınmadığı için 4.3 açık. |
| B7 | Vault (yerel Fernet — `auth/vault.py`; production KMS — `todo.md` 10.6) | Evet (uygulama süreci) | Refresh token/secret yalnız burada düz metin; DB'de yalnız referans/hash. |
| B8 | Queue / async worker | Yok | Henüz uygulanmadı; ARCHITECTURE.md bileşen listesinde yer alsa da kod karşılığı yok — bu tehdit modelinde "N/A, eklenince genişletilir" olarak işaretli. |
| B9 | Observability (structured log/trace) | Yok | Faz 9.1 açık; bugün `backend/src`'de `logging`/`print` çağrısı yok (bkz. Faz 2.2 kanıtı), bu yüzden "log'a sızma" bugün gerçek bir yüzey değil. |
| B10 | Google Ads API (upstream) | Hayır (dış taraf) | Bugün hiç mutate çağrısı yok (Faz 8 tamamı bloke); yalnız reporting adapter'ı devrede. |

### Tehdit envanteri

| # | Tehdit (STRIDE) | Sınır | Azaltım | Kanıt | Artık risk |
|---|---|---|---|---|---|
| T1 | Google refresh token hırsızlığı (Bilgi ifşası) | B7↔B6 | Refresh token yalnız vault'ta düz metin tutulur; DB'de yalnız kasa referansı/hash. Secret taşıyan dataclass'lar `repr=False`. | `auth/vault.py`, `backend/tests/test_auth_vault.py`, `backend/tests/test_secret_redaction.py` | Üretim secrets manager/KMS sağlayıcısı henüz seçilmedi (`todo.md` 10.6) — yerel Fernet anahtarı tek başına üretim için yeterli değil. |
| T2 | Connector access/refresh token hırsızlığı veya replay (Bilgi ifşası, Kurcalama) | B1↔B2, B3↔B6 | HTTPS-only; access token kısa ömürlü ve audience-bound (`verify_access_token`); refresh rotation + reuse tespiti tüm `family_id`'yi fail-closed revoke eder. | `auth/deps.py::verify_access_token`, `db/oauth_store.py::rotate`/`revoke_family`, `backend/tests/test_oauth_store.py::ConcurrentAuthorizationCodeClaimTests` | Token TTL/rotation aralığının tam eşzamanlılık/negatif test matrisi hâlâ açık (`todo.md` 3.4). |
| T3 | Confused deputy — bir client'ın başka client'ın authorization code'unu veya kaynağını kendi adına kullanması (Kimlik sahteciliği) | B3↔B4 | Authorization code; `client_id`, `redirect_uri`, PKCE verifier ve `resource` ile bağlanır; uyuşmazlıkta `invalid_client`/`invalid_grant`. | `backend/tests/test_auth_authorization_flow_http.py` (cross-client redeem reddi, yanlış PKCE reddi) | Yok — üretim davranışını uçtan uca egzersiz eden HTTP testiyle kapatıldı (Faz 3.3). |
| T4 | SSRF — CIMD `client_id` URL'i saldırganın kontrolündeki bir host'a işaret eder (Yetki yükseltme) | B1→B3 | `https://` allowlist, DNS-rebinding TOCTOU pini (tek çözümleme, doğrulanmış IP'ye bağlanma), redirect=0, response-size streaming sınırı, `application/json` Content-Type zorunluluğu. | `auth/cimd.py`, `backend/tests/test_auth_cimd.py` (IPv4-mapped/NAT64/encoded-host/TOCTOU dahil) | Yok — bilinen bypass sınıfları ampirik olarak test edildi (Faz 3.2). |
| T5 | Prompt injection — reklam metni/keyword/rationale içine gömülü talimatın tool scope/customer/proposal tipini değiştirmesi (Kurcalama) | B1→B2 | Untrusted metin sabit alan-getter sözlüğünde tek, minimize alan olarak döner; `customer_id`/`campaign_id`/`proposal_type` ayrı doğrulanmış parametrelerdir, rationale metninden asla türetilmez. | `backend/tests/test_prompt_injection_safety.py` | Yok — hem adapter hem gerçek MCP Streamable HTTP protokolü üzerinden kanıtlandı (Faz 2.4). |
| T6 | IDOR / cross-principal veya cross-customer erişim (Bilgi ifşası, Yetki yükseltme) | B2↔B6 | Her repository metodu `principal_id` zorunlu parametresiyle filtreler; opaque ID'ler (`transaction_id`/`proposal_id`/vb.) DB sorgusundan önce biçim doğrulamasından geçer; cross-principal ve var-olmayan kaynak aynı hata şeklini döner. Production şemasında principal-scoped tablolar için `ENABLE` + `FORCE ROW LEVEL SECURITY` policy migration'ı vardır; `db/postgres.py::principal_transaction` transaction başında RLS principal context'ini set eder; `db/postgres_repository.py` ilk identity/account/credential repository diliminde commit etmeden aynı principal filtre contract'ını taşır. | `api/identifiers.py`, `backend/tests/test_api_identifiers.py`, `backend/tests/test_db_proposals.py`, `backend/tests/test_sqlalchemy_schema.py`, `backend/tests/test_postgres_runtime.py`, `backend/tests/test_postgres_repository.py`, `backend/tests/test_postgres_rls_integration.py` | RLS contract/runtime helper ve ilk SQLAlchemy repository contract testleri yerelde çalışır. Canlı PostgreSQL cross-principal CRUD/pool reuse/privileged-role testi `ADDOBSERVER_POSTGRES_TEST_DSN` gerektirir ve bu ortamda skip etmiştir; kalan production SQLAlchemy repository/app wiring hâlâ açık (`todo.md` 4.3). |
| T7 | CSRF — `/approvals` karar, `/disconnect`, `/logout`, `/authorize/consent` (Kurcalama) | B5→B3/B2 | Session'dan bağımsız üretilmiş synchronizer token, yalnız hash olarak saklanır, `secrets.compare_digest` ile doğrulanır; `SameSite=Strict` cookie. | `backend/tests/test_approvals_http.py`, `backend/tests/test_auth_server_http.py::AuthorizeConsentCsrfTests` | Yok. |
| T8 | Authorization code / state replay (Kurcalama) | B3↔B4 | Kod tek kullanımlık atomik `UPDATE ... WHERE consumed_at IS NULL` ile claim edilir; `state` kısa ömürlü ve callback'te tüketilir. | `backend/tests/test_auth_authorization_flow_http.py` (ikinci `/token` denemesi `invalid_grant`), `backend/tests/test_oauth_store.py::ConcurrentAuthorizationCodeClaimTests` (gerçek iki-thread race) | Yok. |
| T9 | Session fixation / çalıntı `web_session` çerezinin disconnect sonrası hâlâ geçerli kalması (Kimlik sahteciliği) | B5↔B6 | Her girişte taze `secrets.token_urlsafe` session+CSRF çifti (fixation yok); `disconnect_principal` principal'ın **tüm** `web_session` satırlarını iptal eder, yalnız isteği yapan çerezi değil. | `backend/tests/test_auth_web_session.py`, `backend/tests/test_auth_disconnect.py` | Riskli eylemler için re-auth/step-up eşiği henüz kararlaştırılmadı (`todo.md` 7.3, WRITE kapsamı sonrasına bloke). |
| T10 | Audit kurcalama / geçmiş olayın değiştirilmesi-silinmesi (Kurcalama, İnkar) | B2/B3/B5→B6 | `AuditRepository` yalnız `insert`/`list_for_principal` sağlar; update/delete metodu yoktur. Proposal/approval/audit tek transaction'da atomik yazılır; audit açılamazsa karar fail-closed kalır. | `db/proposals.py::AuditRepository`, `db/proposals.py::ApprovalRepository.save_decision_with_audit` | Üretim WORM/append-only depo sağlayıcısı henüz seçilmedi (`todo.md` 9.3) — bugünkü koruma yalnız "uygulama kodu update/delete sunmuyor" seviyesinde, DB dosyasına doğrudan erişimi ayrıca engellemez. |
| T11 | Bağımlılık/tedarik zinciri kompromisi (Kurcalama, Bilgi ifşası) | Repo → B2/B3 | SAST (Bandit), secret tarama (detect-secrets), dependency scan (pip-audit) araç seti ADR ile kabul edildi ve pin'lendi. | `docs/decisions/0003-dev-tooling.md`, `backend/pyproject.toml` | Reproducible lockfile (`uv.lock`) henüz üretilmedi (`todo.md` 10.1); CI'da otomatik gate henüz yok (`todo.md` 10.2) — bugün yalnız yerel/manuel çalıştırma. |
| T12 | Yetkisiz Google Ads mutate (Yetki yükseltme, İnkar) | B2→B10 | Bugün kod tabanında Google Ads'e giden hiçbir mutate çağrısı yok (Faz 8 tamamı bloke); `prepare_proposal` yalnız kendi DB'mize yazar. `test_prompt_injection_safety.py` bir proposal'ın otomatik onaylanamadığını (durumun `pending_approval` kaldığını) ayrıca kanıtlıyor. | `mcp/proposals.py`, `backend/tests/test_prompt_injection_safety.py` | Faz 8 açıldığında revalidation/reservation/idempotency/reconciliation kontrolleri bu tehdit modeline yeni satırlar olarak eklenmelidir; bugün "yok" denmesinin nedeni özellik eksikliği, tasarım kanıtı değil. |
| T13 | Kaynak tükenmesi / adil olmayan kullanım — bir principal'ın diğerlerini quota/latency açısından aç bırakması (Hizmet reddi) | B1→B2/B10 | Yok — rate limiting/fair-queue katmanı henüz uygulanmadı. | — | Açık artık risk (`todo.md` 6.7); Google Ads Basic Access sınırı (15.000 işlem/24 saat) tüm principal'lar için tek paylaşılan bütçe, şu an principal bazında bölünmüyor. |
| T14 | Log/trace/hata cevabına secret veya PII sızıntısı (Bilgi ifşası) | B2/B3→B9 | Yapısal logging henüz eklenmedi (bugün gerçek yüzey yok); secret taşıyan yedi dataclass önceden `repr=False` ile korunuyor; sınıflandırılamayan exception metni public hata mesajına hiç taşınmıyor. | `backend/tests/test_secret_redaction.py`, `backend/tests/test_api_errors.py::test_unrecognised_exception_text_never_reaches_the_public_message` | Faz 9.1 (yapısal logging) açıldığında redaction'ın gerçek log çıktısı üzerinden yeniden doğrulanması gerekir — bugünkü kanıt yalnız `repr()`/mesaj seviyesinde. |

### Açık sorular

- Üretim secrets manager ve KMS sağlayıcısı.
- Public kullanıcı ölçeğinde RLS/pool performansı ve gerekirse sharding sınırı.
- Audit retention süresi ve WORM sağlayıcısı.

## Güncelleme geçmişi

- 2026-07-19 — PostgreSQL reporting credential çözümlemesi metadata, vault ve provider aşamalarına ayrıldı;
  secret/vault ve Google Ads ağ çağrıları açık DB transaction içinde yürütülmez.
- 2026-07-19 — Refresh-token replay family revocation'ının PostgreSQL transaction'da rollback olması
  engellendi; replay cevabı yalnız revoke state commit edildikten sonra döner.

- 2026-07-19 — PostgreSQL `/token` RLS bootstrap'ı exact SHA-256 code hash'ine bağlı, transaction-local
  ve yalnız `SELECT` yetkili policy ile kapatıldı. Principal çözülür çözülmez hash context temizlenir ve
  normal principal RLS context kurulur; runtime rolüne `BYPASSRLS`, tablo sahipliği veya `SECURITY DEFINER`
  verilmez. PostgreSQL'in [RLS](https://www.postgresql.org/docs/current/ddl-rowsecurity.html) ve
  [CREATE POLICY](https://www.postgresql.org/docs/current/sql-createpolicy.html) kuralları temel alındı.
- 2026-07-19 — Tam ASGI PostgreSQL transaction/repository wiring'i tamamlanana kadar production startup
  fail-closed kapatıldı. `DATABASE_URL`'yi yok sayıp SQLite ile production açılması principal izolasyonu
  ihlali sayılır; local/test çalışma yolu korunur.
- 2026-07-19 — Authorization code ile aynı exact-hash/SELECT-only RLS bootstrap deseni access ve refresh
  token tablolarına genişletildi. Böylece bearer doğrulama ve refresh grant principal bilinmeden başlayabilir,
  fakat yalnız sunulan token'ın hash satırını görebilir; hash context hemen temizlenir ve yazmalar normal
  principal policy üzerinden yürür.

- 2026-07-18 — DPoP açık sorusu `docs/decisions/0004-dpop-deferred.md` ile kapatıldı (Faz 2.5):
  hem Google upstream refresh token hem connector'ın kendi access/refresh token'ı için DPoP
  şimdilik uygulanmayacak şekilde karar verildi — resmi Python kütüphaneleri (`google-auth`,
  `google-auth-oauthlib`, `authlib`) proof üretimini desteklemiyor, MCP Authorization
  spesifikasyonu (2025-11-25) DPoP'tan hiç bahsetmiyor ve Google refresh token'ı bu mimaride zaten
  hiçbir client'a çıkmıyor. Kod değişikliği yoktur; ADR yeniden değerlendirme tetikleyicileri
  taşır.
- 2026-07-18 — Uçtan uca tehdit modeli eklendi ("Uçtan uca tehdit modeli" bölümü, Faz 2.1):
  `ARCHITECTURE.md` güven sınırları (Claude client, public MCP, connector AS, Google OAuth,
  approval tarayıcısı, DB, vault, queue, observability, Google Ads API) STRIDE benzeri 14 tehdit
  satırına ve her biri için kod/test kanıtına bağlandı. Bugün kod karşılığı olmayan güven sınırları
  (queue, structured logging) "N/A, eklenince genişletilir" olarak; henüz uygulanmamış kontroller
  (rate limiting, RLS, üretim WORM audit deposu, execution/mutate) bilinçli artık risk olarak
  işaretlendi. Analiz sırasında kod değişikliği gerektiren yeni bir kusur bulunmadı.
- 2026-07-18 — Secret tasiyan yedi dataclass'a (`Settings`, `GoogleAdsCredentials`,
  `GoogleTokenResult`, `AuthorizationCode`, `AccessToken`, `RefreshToken`, `WebSession`,
  `WebSessionIssued`) `dataclasses.field(repr=False)` eklendi (Faz 2.2); onceden bu nesnelerin
  varsayilan `repr()`'i her alani (developer token, client secret, refresh/access/bearer token,
  vault key) duz metin yazdiriyordu -- yapisal loglama henuz eklenmedigi icin bugun bir HTTP/MCP
  yanitina sizmiyordu ama ileride eklenecek herhangi bir `logger.debug(...)`/f-string/yakalanmamis
  exception traceback'i tek basina tum secret'i sizdirirdi; kanit `backend/tests/test_secret_redaction.py`.
  `classify_transport_error`'in siniflandirilamayan-exception dalinin orijinal exception metnini
  hicbir zaman public mesaja tasimadigi da regresyon testiyle sabitlendi
  (`backend/tests/test_api_errors.py`).
- 2026-07-18 — Public HTTP yüzeyine `Strict-Transport-Security` (yalnız `local` dışı ortamda),
  `Host` başlığı allowlist doğrulaması (`TrustedHostMiddleware`) ve kapalı-varsayılan CORS allowlist
  (`CORSMiddleware`, `allow_credentials=False`) eklendi (`backend/src/app.py`,
  `backend/src/config.py`); önceden CORS hiç uygulanmıyordu (yalnız bu belgede "açık allowlist"
  olarak kararlaştırılmıştı ama kod karşılığı yoktu) ve `Host` başlığı doğrulanmıyordu.
- 2026-07-18 — Connector OAuth AS'ın `client_id`/`state`/`scope`/`code_challenge`/`code_verifier`/
  `transaction_id` girdilerine sınır değer ve RFC 7636 biçim doğrulaması eklendi (docs/AUTH.md
  "Saldırı kontrolleri" → "Girdi sınır değerleri"); önceden bu alanlar DB'ye veya ağ çağrısına
  ulaşmadan önce herhangi bir uzunluk/biçim kontrolünden geçmiyordu.
- 2026-07-18 — CIMD `client_id` fetch'i artık DNS'i yalnız bir kez çözer ve bağlantıyı o doğrulanmış
  IP'ye pinler (`backend/src/auth/cimd.py`); önceki halde SSRF kontrolü ve gerçek HTTP bağlantısı
  ayrı DNS çözümlemeleri kullandığından bir DNS-rebinding saldırganı doğrulamayı geçip bağlantıyı
  private bir adrese yönlendirebilirdi (TOCTOU).
- 2026-07-18 — Connector OAuth AS'ın `/authorize/consent` uç noktasına account-linking CSRF
  koruması eklendi (docs/AUTH.md "Saldırı kontrolleri"); önceki halde bu state-changing POST
  hiçbir CSRF doğrulaması yapmıyordu.
- 2026-07-18 — Approval UI CSRF token doğrulaması hash-at-rest davranışıyla netleştirildi.
- 2026-07-18 — `POST /disconnect` artık principal'ın tüm `web_session` satırlarını iptal ediyor
  (`WebSessionRepository.revoke_all_for_principal`, docs/AUTH.md "Disconnect"); önceki halde yalnız
  isteği yapan çerez iptal ediliyordu, bu yüzden aynı principal'ın eşzamanlı ikinci bir tarayıcı
  oturumu disconnect sonrasında da geçerli kalıp `/approvals`'ı listeleyip karar verebilirdi.
- 2026-07-18 — CIMD fetch'ine Content-Type doğrulaması eklendi (`backend/src/auth/cimd.py`):
  yalnızca `application/json` (parametreler hariç) dönen cevaplar kabul edilir; önceden bir CIMD
  host'unun `text/html` gibi başka bir Content-Type ile geçerli JSON baytı döndürmesi hâlâ kabul
  ediliyordu. IPv6 SSRF-guard davranışı (literal loopback/unique-local/link-local, IPv4-mapped
  ve NAT64 Well-Known-Prefix (`64:ff9b::/96`) adresler, encoded/alternate host metinleri, userinfo
  authority confusion) incelendi: mevcut `is_private`/`is_reserved` kontrolü bunların tamamını
  zaten kapsıyordu (`is_private` mapped adresler için gömülü IPv4'e delege eder; `64:ff9b::/96`
  uzun süredir `is_reserved` olan `::/8` bloğunun içinde kalır) — kod değişikliği gerekmedi, yalnız
  regresyon testi eksikti (`backend/tests/test_auth_cimd.py`).
- 2026-07-18 — Faz 4.3 ilk artış: production PostgreSQL şemasına principal-scoped RLS migration'ı
  ve transaction-local principal context helper'ı eklendi. Tehdit modeli RLS'i artık contract-test
  kapsamındaki ikinci savunma katmanı olarak işaretler; `db/postgres.py` helper'ı production
  `DATABASE_URL`'i PostgreSQL'e sınırlar ve principal transaction context'ini testle kanıtlar.
  `db/postgres_repository.py` ilk SQLAlchemy repository dilimiyle principal/client-grant/account/credential
  izolasyon contract'ını production metadata üzerinde taşımaya başladı. Canlı PostgreSQL izolasyon testi DSN'e bağlı olduğu ve
  kalan production repository/app wiring tamamlanmadığı için 4.3 artık riski açık kalır.
- 2026-07-17 — `/approvals` insan onay yüzeyi için CSRF token + cookie özniteliği kararı eklendi
  (docs/AUTH.md).
- 2026-07-17 — Public HTTP güvenlik header'ları ve `/logout` CSRF doğrulaması uygulama standardına eklendi.
- 2026-07-17 — 2026 Google OAuth/Ads, MCP ve OWASP kaynaklarıyla ilk güvenlik standardı oluşturuldu;
  belge zorunlu araştırma/karar formatına getirildi.
- 2026-07-17 — Restricted `adwords` scope, production verification/security assessment, developer-token
  koruması ve Google Ads 2SV hata akışı eklendi.
