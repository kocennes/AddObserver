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
6. Access token kısa ömürlüdür; refresh rotation/reuse detection uygulanır. Disconnect/revoke connector session
   ve Google credential'ı ayrı ayrı iptal eder.

### Upstream Google OAuth

- Connector authorization/onboarding sırasında full browser redirect ve tek kullanımlık Google `state` ile
  `adwords` scope'u istenir. Claude OAuth `state` ile Google OAuth `state` aynı değer değildir ama server-side
  transaction ile aynı principal'a bağlanır.
- Google consent başarılı olsa bile connector kendi consent ekranını atlamaz. Google refresh token şifreli
  per-principal secret; access token bellekte kısa ömürlüdür.
- Google accessible customer ID listesi alınır ve kullanıcı seçimi principal ile bağlanır. `customer_id`
  hiçbir zaman tek başına yetki kanıtı değildir.
- Consent öncesi kullanıcıya Google hesabında 2SV gerekebileceği anlatılır. `TWO_STEP_VERIFICATION_NOT_ENROLLED`
  alındığında token/secret loglanmadan kurulum durdurulur, 2SV etkinleştirme ve yeniden bağlama yönlendirmesi verilir.
- OAuth consent screen production durumu, verified domain'ler, tam redirect URI listesi ve istenen scope kodla
  eş tutulur. İsim/logo/redirect/homepage/privacy URL veya scope değişikliği re-verification kapısından geçer.
- DPoP desteği resmi Google client/AS stack ile güvenli doğrulanır; destek yoksa ADR/risk acceptance gerekir.

### Saldırı kontrolleri

- Exact redirect URI; yalnız RFC 8252 gerektiren loopback port wildcard davranışı. Open redirect yoktur.
- Authorization code/token/log/cookie/referrer sızıntısı engellenir; auth sayfalarında sıkı CSP/referrer policy.
- Token audience, issuer, signature, expiry, scope ve subject her MCP isteğinde doğrulanır.
- Session fixation, login CSRF, mix-up, code interception, refresh replay ve account-linking CSRF test edilir.
- Directory shared client olduğu için kullanıcı/organizasyon izolasyonu OAuth client ID'ye değil doğrulanmış
  connector principal + Google credential ownership'e dayanır.

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
  tahmin edilemez); `/approvals` sayfası bunu her formda gizli alan olarak taşır,
  `POST /approvals/{id}/decision`, `POST /disconnect` ve `POST /logout`
  `secrets.compare_digest` ile eşleştirir. Bu, `SECURITY.md`'nin "state-changing endpoint'ler
  CSRF koruması ... kullanır" kuralını bu yeni yüzey için karşılar.
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
orkestrasyon `backend/src/auth/disconnect.py::disconnect_principal`). Aynı oturum + CSRF
kanıtını `/approvals/{id}/decision` ile paylaşır (ayrı bir yetki yüzeyi eklemez). Tek çağrıda:

- Principal'ın tüm connector `access_token`/`refresh_token` kayıtları iptal edilir
  (`TokenRepository.revoke_all_for_principal`) -- Claude bu connector oturumunu artık kullanamaz.
- Aktif Google credential `revoked` işaretlenir ve kasadaki sır kalıcı olarak yok edilir
  (`OAuthCredentialRepository.revoke_active` + `VaultClient.revoke`).
- Bağlı her `ads_account` `disconnected` işaretlenir (satır silinmez -- geçmiş kayıtlar bozulmaz).
  Canlı HTTP/MCP read ve proposal yolları yalnız `active` hesap eşleşmelerini kabul eder; aynı
  `customer_id` daha sonra yeniden bağlanırsa mevcut satır tekrar `active` yapılır.
- Tek bir `principal.disconnected` audit_event yazılır; public HTTP çağrısında kabul edilen
  `X-Correlation-ID` varsa audit kaydı aynı correlation ID'yi taşır.

Idempotent: ikinci çağrı hata vermez, yalnız zaten iptal edilmiş olanı tekrar iptal etmeye çalışmaz.
"Account deletion talebi" ayrıca bir silme akışı GEREKTİRMEZ -- kasadaki sır kalıcı olarak yok
edildiği ve DB satırları zaten kimlik doğrulama için kullanılamaz hale geldiği için aynı işlem bu
kriteri de karşılar; audit_event append-only kaldığı için asla silinmez (`DATA_MODEL.md`).

## Açık sorular

- OAuth 2.1 authorization server ürünü/kütüphanesi: `docs/decisions/0001-backend-stack.md` ile
  Authlib olarak kapatıldı; DCR/CIMD desteğinin bu kütüphaneyle uygulanabilirliği açık kalır.
- Connector kullanıcı subject'i Google kimliği mi, ayrı account mı olacak?
- Refresh token ömürleri/rotation grace ve Claude Code loopback desteği.
- Google DPoP desteğinin Python production stack'indeki uygulanabilirliği.
- Restricted-scope security assessment'ın server-side refresh token ve rapor verisi mimarimize uygulanma kapsamı.

## Güncelleme geçmişi

- 2026-07-18 — Disconnect sonrası hesap satırlarının canlı read/proposal erişiminden dışlandığı ve
  yeniden bağlantıda aynı satırın `active` yapıldığı netleştirildi.
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
