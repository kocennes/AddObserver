# Connector ve Google OAuth tasarımı

**Durum:** Kabul edildi  
**Son gözden geçirme:** 2026-07-18
**Sonraki gözden geçirme:** 2026-10-17

> Bu belge iki ayrı OAuth/oturum yüzeyini kapsar: (1) Claude'un connector'a bağlandığı OAuth
> 2.1 AS (aşağıdaki "Connector OAuth"), (2) insanın `/approvals` sayfasına giriş yaptığı, çok
> daha hafif bir Google girişi (aşağıdaki "Approval-UI web girişi"). İkisi ayrı token
> düzlemleridir ve birbirine karışmaz.

## Amaç

Claude'un connector'a OAuth 2.1 ile bağlanmasını ve her son kullanıcının Google Ads yetkisini güvenli biçimde
vermesini sağlayan iki token düzlemini, confused-deputy korumasını ve revoke yaşam döngüsünü belirlemek.

## Araştırma

- Anthropic [Authentication for connectors](https://claude.com/docs/connectors/building/authentication), `401`
  + `WWW-Authenticate resource_metadata`, exact resource URI, AS discovery, hosted callback ve directory'de
  herkes için tek paylaşılan OAuth application davranışını tarif eder.
- [MCP Authorization 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization),
  OAuth 2.1 draft, PKCE S256, RFC 9728 resource metadata, RFC 8414 AS metadata ve token audience ister.
- [MCP Security Best Practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices),
  upstream OAuth proxy'nin her client için açık consent uygulamaması halinde confused deputy oluşabileceğini ve
  token passthrough'ın yasak olduğunu belirtir.
- Google [OAuth best practices](https://developers.google.com/identity/protocols/oauth2/resources/best-practices),
  en dar scope, state, şifreli token, revoke/delete ve DPoP seçeneğini açıklar.
- Google Ads [credential güvenliği](https://developers.google.com/google-ads/api/docs/productionize/secure-credentials),
  `adwords` scope'unu restricted sayar ve production öncesi OAuth verification gerektirir. Google Ads
  [2SV gereksinimi](https://developers.google.com/google-ads/api/docs/oauth/security-requirements), yeni refresh
  token üretiminde iki adımlı doğrulama ve bunun için özel hata davranışını tanımlar.

## Karar

### Ayrı token düzlemleri

- **Connector token:** Connector AS tarafından Claude'a verilir; kısa ömürlü, audience/resource MCP endpoint,
  connector `principal_id` ve dar scope taşır. Google API'de kullanılamaz.
- **Google credential:** Google tarafından connector backend'e verilir; per-user secrets manager kaydıdır.
  Claude, MCP client, frontend veya connector access token içine asla konmaz.

### Connector OAuth

1. `/mcp` auth yoksa gerçek `401` + protected-resource metadata pointer döndürür.
2. Metadata exact MCP resource ve tek birincil AS issuer'ı listeler; AS discovery PKCE S256 desteğini yayınlar.
3. Directory shared client ve callback allowlist'i doğrulanır; authorization request PKCE, redirect URI,
   resource/audience, state ve kısa ömürlü transaction ile bağlanır.
4. Kullanıcı connector'ın Google verisine hangi amaçla erişeceğini ve write kapsamını açıkça görüp onaylar.
5. AS code'u tek kullanımlık verir; token endpoint PKCE verifier + client/redirect/resource binding doğrular.
6. Access token kısa ömürlüdür (`ACCESS_TOKEN_TTL_SECONDS = 600`); refresh rotation/reuse detection
   uygulanır (`REFRESH_TOKEN_TTL_SECONDS` = 30 gün, `db/oauth_store.py::TokenRepository.rotate`).
   Reuse tespiti artık gerçek eşzamanlılık altında da atomik: `rotate`'in eski hâli yalnız
   "önce oku, sonra yaz" sırası kullanıyordu (`SELECT` ile durumu kontrol edip ayrı, koşulsuz bir
   `UPDATE` ile `rotated` işaretliyordu) -- bu, aynı hâlâ-aktif refresh token'ı eşzamanlı iki
   çağrının rotate etmeye çalıştığı durumda (todo.md 3.4) her ikisinin de aktif durumu görüp
   ikisinin de başarıyla yeni bir çift üretebildiği gerçek bir TOCTOU açığıydı -- reuse tespiti
   atlanabiliyordu. `UPDATE ... WHERE token_hash = ? AND status = 'active'` koşullu ifadesine
   geçildi (`AuthorizationCodeRepository.claim`'in `WHERE consumed_at IS NULL` deseniyle aynı);
   artık iki eşzamanlı çağrıdan yalnız biri başarılı olur, diğeri reuse olarak TÜM aileyi iptal
   eder. Kanıt: `backend/tests/test_token_lifecycle.py::ConcurrentRefreshRotationTests` (iki
   bağımsız sqlite bağlantısı/thread ile gerçek race). Disconnect/revoke connector session
   ve Google credential'ı ayrı ayrı iptal eder; disconnect'in `TokenRepository.revoke_all_for_principal`'ı
   principal'ın bugüne kadar yetkilendirdiği HER `client_id`'nin token ailesini iptal ettiği
   (yalnız en son bağlanılan client'ınkini değil) `test_token_lifecycle.py::DisconnectRevokesAllClientsTests`
   ile kanıtlandı. `oauth_client_grant` tablosu (`ClientGrantRepository`) yalnız bir consent
   kaydıdır -- token isteme/yenileme akışının hiçbir noktasında scope'u bu tablodan okumaz, her
   zaman GEÇERLİ transaction'ın açıkça onaylanan scope'unu kullanır; bu yüzden daha önce geniş bir
   scope'a consent verilmiş olması, sonraki daha dar bir yetkilendirmeye o geniş scope'u sızdırmaz
   (`test_token_lifecycle.py::ScopeNarrowingTests`).

### Upstream Google OAuth

- Connector authorization/onboarding sırasında full browser redirect ve tek kullanımlık Google `state` ile
  `adwords` scope'u istenir. Claude OAuth `state` ile Google OAuth `state` aynı değer değildir ama server-side
  transaction ile aynı principal'a bağlanır.
- Principal kimliği kalıcı olarak Google'ın kendi `sub` claim'ine bağlanır
  (`PrincipalRepository.get_or_create("https://accounts.google.com", google_subject)`); email
  değişimi principal'ı etkilemez, hiçbir merge veya support-mediated hesap kurtarma yolu yoktur
  (`docs/decisions/0005-principal-identity-no-merge-no-recovery.md`).
- Google consent başarılı olsa bile connector kendi consent ekranını atlamaz. Google refresh token şifreli
  per-principal secret; access token bellekte kısa ömürlüdür.
- Google accessible customer ID listesi alınır ve kullanıcı seçimi principal ile bağlanır. `customer_id`
  hiçbir zaman tek başına yetki kanıtı değildir. **Uygulama durumu:** bu, henüz yazılmamış bir
  senkronizasyon adımıdır (`todo.md` 5.1, "Accessible accounts senkronizasyonunu tamamla" hâlâ
  açık); `db/repository.py::AdsAccountRepository.link_account` bugün yalnız repository katmanında
  var, hiçbir OAuth callback/production kod yolundan çağrılmıyor (yalnız test fixture'larında
  elle çağrılıyor). Faz 3.5'in Google OAuth exchange testleri (`test_google_oauth.py`) bu yüzden
  bu maddeyi kapsam dışı bıraktı -- henüz var olmayan bir davranış test edilemez.
- Consent öncesi kullanıcıya Google hesabında 2SV gerekebileceği anlatılır. `TWO_STEP_VERIFICATION_NOT_ENROLLED`
  alındığında token/secret loglanmadan kurulum durdurulur, 2SV etkinleştirme ve yeniden bağlama yönlendirmesi verilir.
  **Uygulama durumu (Faz 3.6):** bu hata OAuth exchange anında değil, ilk gerçek Google Ads API
  çağrısında (`authentication_error.TWO_STEP_VERIFICATION_NOT_ENROLLED`) ortaya çıkar; genel
  `authentication_error`/`authorization_error` sınıflandırması (`api/errors.py`) bunu zaten
  `ErrorClass.AUTH` yapıyordu, eksik olan şey o sınıflandırmanın credential'ı fiilen
  pasifleştirmesiydi -- bkz. `docs/ERROR_HANDLING.md` "Güncelleme geçmişi". Google'ın çoklu-scope
  onay ekranında kullanıcı yalnız `adwords`'ü reddedip diğer scope'ları kabul edebilir; bu durumda
  callback yine de başarılı bir `code` ile döner (`error=` dalına düşmez) -- `google_callback`
  artık `GoogleTokenResult.granted_scopes`'u kontrol edip `adwords` eksikse bunu `access_denied`
  olarak ele alıyor ve hiçbir vault/credential/consent kaydı oluşturmuyor
  (`backend/tests/test_auth_authorization_flow_http.py::ScopeDenialAtGoogleCallbackTests`).
- OAuth consent screen production durumu, verified domain'ler, tam redirect URI listesi ve istenen scope kodla
  eş tutulur. İsim/logo/redirect/homepage/privacy URL veya scope değişikliği re-verification kapısından geçer.
- DPoP şimdilik uygulanmaz (`docs/decisions/0004-dpop-deferred.md`): resmi Google Python stack'i
  (`google-auth`/`google-auth-oauthlib`) proof üretimini desteklemiyor ve Google refresh token'ı bu
  mimaride zaten yalnız backend vault'unda şifreli tutulup hiçbir client'a çıkmıyor; kısa ömürlü
  access token + refresh rotation/family reuse-detection bu artışta yeterli kabul edildi.

### Saldırı kontrolleri

- Exact redirect URI; yalnız RFC 8252 gerektiren loopback port wildcard davranışı. Open redirect yoktur.
- `create_app`, `APP_ENVIRONMENT != "local"` iken `PUBLIC_BASE_URL`'in `https://` ile başlamasını
  fail-closed zorunlu kılar (`backend/src/app.py`) -- AS'in `issuer`/`authorization_endpoint`/
  `token_endpoint` ve protected-resource `resource`/`authorization_servers` alanları doğrudan bu
  değerden kurulduğundan, yanlışlıkla `http://` ile üretime çıkmak OAuth 2.1/MCP Authorization'ın
  "tüm AS uç noktaları HTTPS" zorunluluğunu sessizce ihlal ederdi.
- Authorization code/token/log/cookie/referrer sızıntısı engellenir; auth sayfalarında sıkı CSP/referrer policy.
- Token audience, issuer, signature, expiry, scope ve subject her MCP isteğinde doğrulanır.
- Session fixation, login CSRF, mix-up, code interception, refresh replay ve account-linking CSRF test edilir.
- Directory shared client olduğu için kullanıcı/organizasyon izolasyonu OAuth client ID'ye değil doğrulanmış
  connector principal + Google credential ownership'e dayanır.
- **Account-linking CSRF savunması:** CIMD ile herkes kendi `client_id`/`redirect_uri`'sini
  kaydedebildiğinden, `GET /authorize`'ı çağıran taraf saldırgan olabilir ve `transaction_id`/
  consent onayı değerlerini zaten meşru biçimde bilir -- bu yüzden korumanın temeli "değeri
  bilmiyor musun" değil, "bu tarayıcı bu işlemin `GET /authorize` sayfasını gerçekten yükledi mi"dir.
  `AuthorizationTransaction.consent_csrf_hash`, `GET /authorize` render edilirken üretilen rastgele
  bir değerin hash'idir; ham değer yalnız `authorize_csrf` adlı `HttpOnly`+`SameSite=Strict`+(yerel
  ortam dışında) `Secure` bir cookie ile (path=`/authorize/consent`) verilir. `POST /authorize/consent`
  kararı işlemeden önce isteğin taşıdığı bu cookie'yi ilgili `transaction_id`'nin saklı hash'ine karşı
  `secrets.compare_digest` ile doğrular (`backend.src.auth.domain.verify_consent_csrf`); eksik/yanlış
  cookie `400` ile fail-closed reddedilir. Saldırganın kendi tarayıcısında oluşturduğu geçerli
  `transaction_id`+form değerleriyle kurbanın tarayıcısından tetiklediği CSRF POST'u, kurbanın
  tarayıcısı o `Set-Cookie`'yi hiç almadığı için (yalnız saldırganın tarayıcısına verildi) bu kontrolü
  geçemez.
- **Authorization transaction hardening (uçtan uca `/authorize` → `/authorize/consent` →
  `/google/callback` → `/token`):** state/PKCE/redirect_uri/resource binding'i,
  authorization code'un tek kullanımlıklığını (replay), çapraz istemci (confused-deputy)
  redemption'ı, açık redirect'i ve süre dolumunu gerçek ASGI uygulaması üzerinden
  `backend/tests/test_auth_authorization_flow_http.py` doğrular; eşzamanlı redeem
  atomikliği `backend/tests/test_oauth_store.py::ConcurrentAuthorizationCodeClaimTests`
  içinde iki bağımsız thread/connection ile test edilir. `state`/`code`/`redirect_uri`/
  `resource` hiçbir yerde loglanmaz (yapısal application logging henüz eklenmedi, Faz 9.1).
- **Girdi sınır değerleri (`GET /authorize`, `POST /authorize/consent`, `GET /google/callback`):**
  `client_id` en fazla 2048 karakter olabilir ve DNS/ağ çağrısından önce reddedilir
  (`backend/src/auth/cimd.py::MAX_CLIENT_ID_URL_LENGTH`); `state`/`scope` sırasıyla 512 karakterle
  sınırlıdır (`backend/src/auth/domain.py::MAX_STATE_LENGTH`/`MAX_SCOPE_LENGTH`); `code_challenge`
  ve `code_verifier` RFC 7636 s4.1'in 43-128 karakterlik base64url biçimine tam uyar, aksi halde
  `AS` sırasıyla `/authorize` ve `/token`'da fail-closed reddeder. `POST /authorize/consent`'in
  `transaction_id`'si ve `GET /google/callback`'in `state`'i, DB sorgusundan önce
  `backend.src.api.identifiers.validate_opaque_id` ile 1–128 karakterlik URL-safe bir kimliğe
  sınırlanır.

## Approval-UI web girişi

`docs/ARCHITECTURE.md`'nin write yolu, "human confirmation/approval"ın Claude'un tool-calling
döngüsü *dışında* gerçekleşmesini zorunlu kılar (bir MCP tool'u Claude'un kendi önerisini
onaylamasına izin verirdi, ki bu insan onayının anlamını ortadan kaldırır). Bunun için
`backend/src/auth/web_session.py` + `backend/src/auth/approvals_routes.py` ile ayrı, çok daha
hafif bir tarayıcı oturumu eklendi.

- **Kapsam farkı:** Bu giriş yalnız `openid`+`email` ister, `adwords` istemez -- amacı yalnız
  "bu tarayıcı principal X'e ait" demektir, Google Ads erişimini yeniden yetkilendirmez.
- **Aynı `redirect_uri`, ayrı `state` uzayı:** İkinci bir redirect URI kaydetmek yerine (bu,
  `GOOGLE_API_ACCESS.md`'nin belirttiği re-verification'ı tetikleyebilir) mevcut
  `/google/callback` iki tür `state`'i ayırt eder: önce `authorization_transaction` (Claude
  istemcisi akışı), bulunamazsa `web_login_state` (bu akış) aranır.
  - `web_login_state`: `/login`'in ürettiği, tek kullanımlık, 10 dakika TTL'li bir `state`
    (`backend/src/db/web_session_store.py::WebLoginStateRepository`, `authorization_code`
    single-use claim deseniyle aynı `UPDATE ... WHERE status = 'pending'` atomikliği).
- **Login asla principal yaratmaz veya credential'a dokunmaz:** `PrincipalRepository.get`
  (non-creating) kullanılır -- gerçek Google Ads connect akışının kullandığı
  `get_or_create`'den kasıtlı olarak farklı. Google exchange sonucu yalnız `google_subject`
  doğrulamak için kullanılır; `vault.store`, `OAuthCredentialRepository.upsert` veya
  `ClientGrantRepository.record_consent` hiçbir zaman çağrılmaz. Daha önce bağlı olmayan bir
  Google hesabıyla giriş denemesi `403` ile reddedilir ("önce Claude üzerinden bağlanın").
- **Oturum:** Başarılı girişte `backend/src/db/web_session_store.py::WebSessionRepository`
  30 dakikalık, HttpOnly + `SameSite=Strict` + (yerel ortam dışında) `Secure` bir `web_session`
  çerezi verir. Token değeri yalnız SHA-256 hash'i olarak saklanır (mevcut
  `authorization_code`/`access_token` deseniyle aynı).
- **CSRF:** Oturumla birlikte ayrı, bağımsız bir `csrf_token` üretilir (session token'dan
  tahmin edilemez). DB'de yalnız SHA-256 hash'i saklanır; raw değer tarayıcıya `web_csrf`
  adlı `SameSite=Strict` cookie ile verilir ve `/approvals` sayfası hash eşleşmesini
  doğruladıktan sonra bunu her formda gizli alan olarak taşır. `POST /approvals/{id}/decision`,
  `POST /disconnect` ve `POST /logout`, formdaki raw token'ı hashleyip `secrets.compare_digest`
  ile saklı hash'e karşı eşleştirir. Bu, `SECURITY.md`'nin "state-changing endpoint'ler
  CSRF koruması ... kullanır" kuralını bu yeni yüzey için karşılar.
  Public form girdisi hashlenmeden önce 1–128 karakterle sınırlanır; proposal kararı enum doğrulaması
  proposal repository sorgusundan önce yapılır.
- **Yetki sınırı:** `/approvals` yalnız oturumun `principal_id`'sine ait bekleyen önerileri
  listeler (`ProposalRepository.list_pending`); karar kaydı da yalnız
  `ProposalRepository.get(principal_id, proposal_id)` ile principal-kapsamlı okunan bir öneri
  üzerinde çalışır -- başka principal'ın `proposal_id`'si `404` döner, veri sızdırmaz.
- **Audit:** İnsan kararı, proposal durum güncellemesi, immutable `approval` satırı ve
  `approval.decided` audit_event'i aynı transaction içinde kaydedilir. Audit yazılamazsa
  karar da kaydedilmez; public HTTP çağrısında kabul edilen `X-Correlation-ID` audit kaydına
  aynen taşınır.

### Disconnect (uygulandı)

`docs/PRODUCT.md`'nin değişmez kabul kriteri -- "Kullanıcı disconnect ile gelecek erişimi
durdurabilir" -- `POST /disconnect` ile karşılanır (`backend/src/auth/approvals_routes.py`,
orkestrasyon `backend/src/auth/disconnect.py`). Aynı oturum + CSRF
kanıtını `/approvals/{id}/decision` ile paylaşır (ayrı bir yetki yüzeyi eklemez). Tek çağrıda:

- Principal'ın tüm connector `access_token`/`refresh_token` kayıtları iptal edilir
  (`TokenRepository.revoke_all_for_principal`) -- Claude bu connector oturumunu artık kullanamaz.
- Aktif Google credential `revoked` işaretlenir. Local SQLite adaptörü kasadaki sırrı aynı çağrıda
  yok eder. PostgreSQL production yolu credential snapshot'ını aynı transaction'da durable
  `credential_revocation_job` outbox'ına yazar; commit sonrası worker vault sırrını retry edilebilir
  biçimde yok eder. Route açık DB transaction içinde vault/network çağrısı yapmaz (ADR-0007).
- Bağlı her `ads_account` `disconnected` işaretlenir (satır silinmez -- geçmiş kayıtlar bozulmaz).
  Canlı HTTP/MCP read ve proposal yolları yalnız `active` hesap eşleşmelerini kabul eder; aynı
  `customer_id` daha sonra yeniden bağlanırsa mevcut satır tekrar `active` yapılır.
- Principal'ın **tüm** `web_session` satırları iptal edilir
  (`WebSessionRepository.revoke_all_for_principal`) -- yalnız isteği yapan çerez değil. Bunsuz,
  aynı principal'ın ikinci bir tarayıcıda/cihazda (veya eski, sızmış bir çerezle) açık bir oturumu
  disconnect'ten sonra da geçerli kalır, `/approvals`'ı listeleyip karar verebilirdi; bu,
  "disconnect ile gelecek erişimi durdurabilir" garantisini bozardı.
- Tek bir `principal.disconnected` audit_event yazılır; public HTTP çağrısında kabul edilen
  `X-Correlation-ID` varsa audit kaydı aynı correlation ID'yi taşır.

PostgreSQL yolunda connector token, credential/outbox, account, tüm browser session ve audit yazıları tek
principal-bound transaction'dadır. Audit sonucu credential varsa `revocation_queued`, yoksa `revoked` olur.
Idempotent: ikinci çağrı hata vermez, yalnız zaten iptal edilmiş olanı tekrar iptal etmeye çalışmaz.
"Account deletion talebi" ayrıca bir silme akışı GEREKTİRMEZ -- kasadaki sır kalıcı olarak yok
edildiği ve DB satırları zaten kimlik doğrulama için kullanılamaz hale geldiği için aynı işlem bu
kriteri de karşılar; audit_event append-only kaldığı için asla silinmez (`DATA_MODEL.md`).

## Açık sorular

- OAuth 2.1 authorization server ürünü/kütüphanesi: `docs/decisions/0001-backend-stack.md` ile
  Authlib olarak kapatıldı; DCR/CIMD desteğinin bu kütüphaneyle uygulanabilirliği açık kalır.
- Refresh token ömürleri/rotation grace ve Claude Code loopback desteği.
- Restricted-scope security assessment'ın server-side refresh token ve rapor verisi mimarimize uygulanma kapsamı.

## Güncelleme geçmişi

- 2026-07-22 — Connector `/google/callback` production PostgreSQL yoluna taşındı. Authorization
  transaction ilk kısa transaction'da okunup kapatılır; Google code exchange ve vault store DB
  transaction'ı dışında yürür. Doğrulanmış Google subject sonrasında ikinci transaction
  principal RLS context'ini bağlar ve credential metadata, client consent, authorization code ile
  `consented → completed` geçişini atomik kaydeder. Kalıcılaştırma rollback olursa yeni vault
  referansı best-effort revoke edilir ve provider/DB ayrıntısı OAuth cevabına taşınmaz.

- 2026-07-19 — Connector `/authorize` transaction oluşturma ile `/authorize/consent` okuma ve consent durum
  ilerletme işlemleri PostgreSQL production yolunda kısa unit-of-work transaction'ları kullanır. Consent
  okuma+CSRF doğrulama+`pending → consented` compare-and-set geçişi tek transaction'da atomiktir.

- 2026-07-19 — Approval browser session cookie'si için PostgreSQL exact-hash RLS bootstrap eklendi;
  `/approvals`, karar ve logout yolları bootstrap sonrası principal-scoped transaction kullanır.
- 2026-07-19 — Login-only state creation/claim PostgreSQL repository'ye taşındı; Google code exchange açık
  DB transaction dışında, session creation ise doğrulanmış Google subject sonrası principal RLS context'inde
  yürür.
- 2026-07-19 — PostgreSQL refresh-token replay tespiti artık family revoke state'ini commit ettikten sonra
  `invalid_grant` döner; beklenen `AuthError`'ın unit-of-work rollback'iyle revoke'u geri alması engellendi.

- 2026-07-18 — Faz 1.2: "Connector kullanıcı subject'i Google kimliği mi, ayrı account mı olacak?"
  sorusu `docs/decisions/0005-principal-identity-no-merge-no-recovery.md` ile kapatıldı: principal
  kalıcı olarak Google `sub`'a bağlanır (zaten uygulanan davranış, `server.py::google_callback`),
  hiçbir principal merge veya support-mediated hesap kurtarma mekanizması yazılmaz -- Google hesabına
  erişim kalıcı kaybedilirse connector kaydına erişim de kalıcı kaybedilir. Kod değişikliği yoktur;
  mevcut `test_db_repository.py::PrincipalRepositoryTests` bu kararın regresyon kanıtıdır.
- 2026-07-18 — Faz 3.6: İki gerçek kusur bulunup düzeltildi. (1) `docs/ERROR_HANDLING.md`'nin
  "Auth" satırı ("Credential pasifleştir, işleri durdur") kabul edilmişti ama hiçbir kod yolu
  bunu tetiklemiyordu -- bir Google Ads API çağrısı `TWO_STEP_VERIFICATION_NOT_ENROLLED`/
  `invalid_grant`/izin iptali gibi bir AUTH-class hatayla başarısız olduğunda credential DB'de
  aktif kalmaya devam ediyordu; her sonraki çağrı Google'a tekrar tekrar gidip aynı şekilde
  başarısız oluyordu. `mcp/credentials.py::deactivate_credential_on_auth_failure` +
  `mcp/tools.py::_fetch_report_page` eklendi; artık ilk AUTH hatası credential'ı pasifleştirir,
  sonraki her çağrı Google'a hiç ulaşmadan hızlıca reddedilir. (2) Google'ın çoklu-scope onay
  ekranında kullanıcının `adwords`'ü reddedip `openid`/`email`'i kabul etmesi hiç kontrol
  edilmiyordu -- callback başarılı bir `code` ile dönüyor, `exchange_code` başarılı oluyor ve
  işlevsiz (Ads erişimi olmayan) bir credential kalıcı hale getiriliyordu. `GoogleTokenResult`e
  `granted_scopes` eklendi (`auth/google_oauth.py`); `google_callback` artık `adwords` eksikse
  bunu `access_denied` olarak ele alıp hiçbir vault/credential/consent kaydı oluşturmuyor.
  Kanıt: `backend/tests/test_mcp_credentials.py::DeactivateCredentialOnAuthFailureTests`,
  `backend/tests/test_mcp_integration.py::test_auth_class_tool_failure_deactivates_the_credential`,
  `backend/tests/test_auth_authorization_flow_http.py::ScopeDenialAtGoogleCallbackTests`.
- 2026-07-18 — Faz 3.5: `backend/tests/test_google_oauth.py` eklendi (11 test) --
  `GoogleWebFlowOAuthClient`'in kendisi (gerçek `google_auth_oauthlib.flow.Flow` +
  `google.oauth2.credentials.Credentials` üretim yolu) önceden hiç doğrudan test edilmiyordu,
  yalnız test paketindeki `FakeGoogleOAuthClient` test double'ı kullanılıyordu. Yalnız gerçek ağ
  round-trip'i gerektiren iki nokta stub'landı (`Flow.fetch_token`, `verify_oauth2_token`); geri
  kalan tüm resmi kütüphane dönüşüm mantığı (`credentials_from_session`) gerçek çalıştı. Kanıtlanan
  davranışlar: `access_type=offline`+`prompt=consent`'in her authorization URL'de zorunlu
  istendiği (aksi halde dönen kullanıcılar için Google refresh_token vermez); redirect_uri'nin
  tam eşleştiği; `state`'in aynen geri döndüğü; restricted `adwords` scope'un varsayılan client'ta
  bulunduğu ama login-only client'ta (`_LOGIN_ONLY_SCOPES`) hiç bulunmadığı; `refresh_token`/
  `id_token` eksikse `verify_oauth2_token`'ın hiç çağrılmadan fail-closed reddedildiği; subject'in
  ham/doğrulanmamış bir decode'dan değil `verify_oauth2_token`'ın DÖNÜŞ değerinden geldiği (çağrının
  doğru `id_token`+`audience` ile yapıldığı doğrulanarak); `sub` claim'i eksikse reddedildiği; ve
  imza doğrulama hatasının (`ValueError`) yutulmadan çağırana aynen yayıldığı (server.py'deki genel
  `except Exception` zaten bunu güvenli bir redirect'e çeviriyor, ayrı bir güvenlik ağı gerekmiyor).
  Login-only akışın Ads credential'ı hiç oluşturmadığı zaten `test_approvals_http.py::
  test_login_never_creates_principal_or_touches_credential`/`test_login_does_not_rotate_existing_ads_credential`
  ile kapsanıyordu, tekrarlanmadı. "Accessible account linking" (Google accessible-customer
  senkronizasyonu) bu maddenin kapsamı dışında bırakıldı -- `AdsAccountRepository.link_account`
  bugün hiçbir production kod yolundan çağrılmıyor (`todo.md` 5.1 hâlâ açık); "Google OAuth" bölümüne
  bu durumu netleştiren bir not eklendi.
- 2026-07-18 — Faz 3.4: `db/oauth_store.py::TokenRepository.rotate`'te gerçek bir eşzamanlılık
  kusuru bulunup düzeltildi -- aynı hâlâ-aktif refresh token'ı eşzamanlı iki çağrı rotate etmeye
  çalıştığında (ör. bir istemcinin ağ hatası sonrası tekrar denemesi, veya çalınmış bir token'ın
  meşru istemciyle aynı anda kullanılması), her ikisi de "aktif" durumu görüp ikisi de başarıyla
  yeni bir token çifti üretebiliyordu; reuse-detection'ın "ikinci kullanım TÜM aileyi iptal eder"
  garantisi eşzamanlı çağrılar altında atlanabiliyordu. `UPDATE ... WHERE status = 'active'`
  koşullu ifadesiyle (authorization code claim'iyle aynı desen) atomik hale getirildi; artık iki
  eşzamanlı çağrıdan yalnız biri başarılı olur, diğeri reuse olarak TÜM aileyi (kazananın az önce
  aldığı yeni token dahil) iptal eder. Ayrıca `backend/tests/test_token_lifecycle.py` eklendi (5
  test): access token TTL'sinin gerçek bir HTTP isteğinde uygulandığı (önceden yalnız saf
  fonksiyon seviyesinde test ediliyordu), disconnect'in principal'ın TÜM client'larının token
  ailelerini iptal ettiği, ve `oauth_client_grant`'ın scope narrowing'i bozmadığı (daha önce geniş
  bir consent kaydı, sonraki dar bir yetkilendirmeye sızmıyor).
- 2026-07-18 — Faz 3.1: `create_app`'e `APP_ENVIRONMENT != "local"` iken `PUBLIC_BASE_URL`'in
  `https://` ile başlamasını zorunlu kılan fail-closed bir kontrol eklendi (`backend/src/app.py`);
  önceden bu değer hiç doğrulanmıyordu, yani üretimde yanlışlıkla `http://` bırakılırsa AS'in
  `issuer`/`authorization_endpoint`/`token_endpoint` ve protected-resource metadata'sı sessizce
  HTTPS-dışı URL'ler yayınlardı. RFC 9728 protected-resource ve RFC 8414 authorization-server
  metadata dokümanlarının içeriği (`resource`/`authorization_servers` tam eşleşmesi, PKCE S256
  zorunluluğu, desteklenen grant/response type'lar, CIMD desteği, `Cache-Control: no-store`)
  ilk kez doğrudan contract testiyle doğrulandı (`backend/tests/test_oauth_metadata_contract.py`,
  11 test); önceden yalnız `WWW-Authenticate` header'ının bu URL'e işaret ettiği test ediliyordu,
  dokümanların kendi gövdesi hiç okunmuyordu.
- 2026-07-18 — DPoP açık sorusu kapatıldı (`docs/decisions/0004-dpop-deferred.md`, Faz 2.5): resmi
  Google Python kütüphaneleri proof üretimini desteklemiyor ve refresh token bu mimaride zaten
  hiçbir client'a çıkmadığı için DPoP şimdilik uygulanmayacak; "Saldırı kontrolleri" bölümü
  güncellendi.
- 2026-07-18 — `auth/server.py::google_callback`'te gerçek bir kusur bulunup düzeltildi:
  kod `complete_transaction`'ı `issue_authorization_code`'dan ÖNCE çağırıyordu, ama
  `issue_authorization_code` işlemin hâlâ `CONSENTED` durumda olmasını şart koşuyor
  (`auth/domain.py`) -- `complete_transaction` durumu zaten `COMPLETED`'a çevirdiğinden
  bu sıra, Google onayından sonraki HER gerçek `/google/callback` isteğinin
  "Onay tamamlanmadan kod uretilemez." hatasıyla başarısız olmasına, yani connector'ın
  gerçek OAuth akışının hiçbir zaman tamamlanamamasına yol açıyordu. Bu kusur önceden
  yakalanmamıştı çünkü hiçbir HTTP testi `/authorize/consent`'in Google'a yönlendirme
  yapmasının ötesine (`/google/callback` ve `/token`'a kadar) gitmiyordu. Sıra
  düzeltildi (kod önce, tamamlama sonra) ve tüm zinciri gerçek ASGI uygulaması üzerinden
  egzersiz eden `backend/tests/test_auth_authorization_flow_http.py` eklendi.
- 2026-07-18 — Connector AS'ın `client_id`/`state`/`scope`/`code_challenge`/`code_verifier`
  girdilerine RFC 7636 uyumlu biçim ve sınır değer doğrulaması, `transaction_id`/`state`
  opaque kimliklerine ise DB sorgusundan önce URL-safe uzunluk doğrulaması eklendi (bkz. yukarı,
  "Saldırı kontrolleri" → "Girdi sınır değerleri").
- 2026-07-18 — `/authorize/consent` için account-linking CSRF savunması eklendi
  (`AuthorizationTransaction.consent_csrf_hash` + `authorize_csrf` SameSite=Strict cookie);
  önceden bu uç nokta hiçbir CSRF kontrolü yapmıyordu.
- 2026-07-18 — Approval form CSRF girdisi 128 karakterle sınırlandı ve karar enum'u proposal
  sorgusundan önce doğrulanacak şekilde fail-closed hale getirildi.
- 2026-07-18 — Disconnect sonrası hesap satırlarının canlı read/proposal erişiminden dışlandığı ve
  yeniden bağlantıda aynı satırın `active` yapıldığı netleştirildi.
- 2026-07-18 — Approval UI CSRF token'ının DB'de yalnız hash olarak tutulduğu; raw değerin
  `web_csrf` SameSite cookie ile form render için kullanıldığı netleştirildi.
- 2026-07-18 — `POST /disconnect`, isteği yapan çerezin yanı sıra principal'ın tüm `web_session`
  satırlarını iptal edecek şekilde düzeltildi (`WebSessionRepository.revoke_all_for_principal`) --
  önceden yalnız istek yapan çerez iptal ediliyordu, bu yüzden aynı principal'ın eşzamanlı ikinci
  bir tarayıcı oturumu disconnect sonrası da geçerli kalıp bekleyen önerileri görüp karar
  verebiliyordu.
- 2026-07-17 — İç uygulama session modeli public directory shared-client + upstream Google OAuth modeline çevrildi.
- 2026-07-17 — Ürün sahibi onayıyla Kabul edildi durumuna geçirildi; AS kütüphanesi ADR-0001 ile kapatıldı.
- 2026-07-17 — Restricted-scope verification, OAuth değişikliği sonrası re-verification ve Google Ads 2SV
  onboarding/hata davranışı eklendi.
- 2026-07-17 — `/login`/`/approvals` için ayrı, `openid`+`email`-only, credential'a dokunmayan
  approval-UI web girişi eklendi (`docs/ARCHITECTURE.md`'nin "Claude dışında insan onayı" açık
  sorusunu kapatır).
- 2026-07-17 — `POST /disconnect` uygulandı: connector token'ları, Google credential/vault sırrı
  ve bağlı hesaplar tek adımda iptal edilir; `docs/PRODUCT.md`'nin disconnect/deletion kabul
  kriterini kapatır.
- 2026-07-17 — Approval UI logout CSRF doğrulaması ve public HTTP güvenlik header'ları eklendi.
