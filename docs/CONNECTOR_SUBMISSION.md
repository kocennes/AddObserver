# Anthropic Connectors Directory başvurusu

**Durum:** Kabul edildi — listeleme öncesi checklist kanıtları tamamlanmalı  
**Son gözden geçirme:** 2026-07-22
**Sonraki gözden geçirme:** 2026-10-22

## Amaç

Remote Google Ads MCP connector'ünü Anthropic Connectors Directory'ye göndermek için teknik, güvenlik,
ürün, test, dokümantasyon ve inceleme gereksinimlerini tek kontrol listesinde toplamak.

## Araştırma

- Anthropic [Submission requirements](https://claude.com/docs/connectors/building/submission), remote MCP
  server, OAuth 2.0, tool annotations, public docs/support/privacy, test account, connection bilgileri,
  tool listesi, use-case örnekleri ve launch readiness kanıtlarını ister.
- [Pre-submission checklist](https://claude.com/docs/connectors/building/review-criteria), read/write
  tool'larının ayrılmasını; her tool'da `title` ve uygun `readOnlyHint`/`destructiveHint`; 64 karakter altı
  ad; dar/açık açıklama; actionable hata; makul cevap boyutu ve MCP Inspector/custom connector testi ister.
- [Authentication for connectors](https://claude.com/docs/connectors/building/authentication), MCP endpoint'in
  `401` + `WWW-Authenticate: Bearer resource_metadata=...` döndürmesini, protected-resource metadata'daki
  `resource` değerinin MCP URL ile tam eşleşmesini ve authorization server discovery'yi tarif eder.
  Hosted Claude callback'i `https://claude.ai/api/mcp/auth_callback`'tir. Directory connector herkes için
  tek paylaşılan OAuth application kullanır.
- Güncel [MCP Authorization 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization),
  OAuth 2.1 draft, PKCE S256, RFC 9728 protected-resource metadata, token audience ve HTTPS gerektirir.
- [Anthropic Software Directory Policy](https://support.claude.com/en/articles/13145358-anthropic-software-directory-policy),
  minimum veri toplama, privacy/support, standard test account, en az üç örnek, bakım yükümlülüğü, doğru
  tool tanımları ve Streamable HTTP desteğini şart koşar.
- Anthropic'in güncel [submission requirements](https://claude.com/docs/connectors/building/submission) sayfası,
  connector güvenlik ve işlevinin yayın sonrasında sürdürülmesini, güvenlik sorunlarına hızlı yanıtı ve açıklama/
  dokümantasyonun doğru tutulmasını taahhüt olarak ister. `ui/open-link` kullanılırsa izin verilen HTTPS origin
  veya custom scheme'ler başvuruda ayrıca beyan edilir.

### 2026-07-22 tazeleme — beş kaynak yeniden doğrulandı

Faz 12.1 denetimi için beş birincil kaynak yeniden çekilip mevcut koda karşı satır satır karşılaştırıldı
(aşağıdaki "Teknik profil" ve "Submission paketi" bunun sonucudur):

- [Submission requirements](https://claude.com/docs/connectors/building/submission) artık submission'ın bir
  form değil, `claude.ai/admin-settings/directory/submissions/new` altındaki bir **portal** akışı olduğunu
  gösteriyor. Portal, gönderen tarafın bir **Team veya Enterprise organizasyonu** ve **Directory management**
  (veya daha geniş **Libraries**) izni olmasını zorunlu kılıyor — Individual/Pro hesaptan submission yapılamaz.
  Portal 10 adımdır (Introduction/Connection/Tools/Listing/Use cases/Company/Authentication/Data handling/
  Test & launch/Compliance/Review); "Compliance" adımında yedi zorunlu onay istenir: directory guidelines,
  first-party API usage, financial transactions yokluğu, AI media generation yokluğu, prompt injection
  savunması, conversation data toplanmadığı ve public documentation. Ayrıca [Directory Terms](https://support.claude.com/en/articles/13145338-anthropic-software-directory-terms)
  linki daha önce bu belgede yoktu, eklendi.
- [Pre-submission checklist](https://claude.com/docs/connectors/building/review-criteria) iki yeni netlik
  getirdi: (1) "Reference API docs in custom query tools" kuralı yalnız çağıranın serbestçe endpoint/query
  kurduğu tool'lar için geçerli — bizim sekiz tool'umuzun tamamı sabit, amaca özel çağrılar yapıyor
  (`backend/src/mcp/tools.py`/`proposals.py`), bu kural bize uygulanmıyor; (2) "Unsupported use cases" listesi
  (para/kripto transferi, AI ile görsel/video/ses üretimi) bizim kapsamımızda hiç yok.
- [Authentication for connectors](https://claude.com/docs/connectors/building/authentication) CIMD'nin Claude
  tarafından seçilmesi için authorization server metadata'sının **hem** `client_id_metadata_document_supported:
  true` **hem de** `token_endpoint_auth_methods_supported`'da `"none"` içermesi gerektiğini netleştirdi (ikisi
  eksikse Claude DCR'a düşer); `/token`'ın `application/x-www-form-urlencoded` kabul etmesi, refresh rotation'ın
  aynı cevapta yeni refresh token dönmesi, invalid refresh için `invalid_grant` (RFC 6749) dönmesi ve discovery/
  registration/token uç noktaları için 10 saniye, refresh için 30 saniye zaman aşımı sınırı olduğu netleşti.
  Anthropic'in MCP sunucusuna giden trafiği `160.79.104.0/21` aralığından çıkıyor (bkz. `docs/DEPLOYMENT.md`
  "Faz 10 uygulama durumu").
- [Anthropic Software Directory Policy](https://support.claude.com/en/articles/13145358-anthropic-software-directory-policy)
  yürürlük tarihi **2026-04-15** olarak teyit edildi; madde metinleri (minimum veri toplama, privacy/support,
  standart test hesabı, en az üç örnek, bakım yükümlülüğü, tool tanım doğruluğu, Streamable HTTP) değişmedi.
- Güncel [MCP Authorization 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)
  spesifikasyonu önceki incelemeden beri sürüm değiştirmedi; RFC 8707 `resource` parametresinin hem
  authorization hem token isteğinde zorunlu olduğu ve MCP sunucusunun audience'ı doğrulaması gerektiği teyit
  edildi (`backend/src/auth/domain.py::consume_authorization_code`/`AuthorizationTransaction.__init__` bunu
  zaten `invalid_target` ile uyguluyor).

## Karar

### Teknik profil

- Tek public endpoint: `https://<VERIFIED_DOMAIN>/mcp`, Streamable HTTP; SSE yalnız geçiş uyumluluğu için
  gerekirse eklenir, stdio directory ürünü değildir.
- Public TLS, 7/24 health, uygun timeout/rate limit ve Anthropic egress erişimi sağlanır. `Origin` header
  allowlist/doğrulaması ve DNS rebinding/SSRF koruması test edilir.
- `/.well-known/oauth-protected-resource` ve path-aware metadata yayınlanır. Unauthorized MCP çağrısı tool
  error/200 değil gerçek `401` + `WWW-Authenticate` döndürür.
- Ayrı OAuth authorization server, discovery metadata, PKCE S256, audience-bound kısa access token, rotation
  destekli refresh token ve revoke sağlar. Google refresh token MCP/Claude'a verilmez.
- Hosted callback ile Claude Code loopback callback'leri auth server policy'sinde güvenli ve standarda uygun
  kaydedilir. Consent ekranı redirect host, connector ve istenen yetkileri açık gösterir.

#### 2026-07-22 kod denetimi — kriter → kanıt eşlemesi (Faz 12.1)

Aşağıdaki tablo, yukarıdaki teknik profilin her satırını gerçek koddaki karşılığına ve regresyon testine bağlar.
Yalnız gerçek bir public domain/hosting gerektiren satırlar (MCP Inspector/Claude custom connector canlı testi,
Anthropic egress IP allowlist doğrulaması) `Bekliyor` kalır — bunlar kod değil dağıtım/domain kararına bağlıdır
(bkz. `todo.md` 12.3/12.8, `docs/DEPLOYMENT.md`).

| Kriter | Kod | Test | Durum |
|---|---|---|---|
| Streamable HTTP, tek `/mcp` endpoint | `backend/src/mcp/server.py::build_mcp_server` (`streamable_http_path`) | `test_mcp_integration.py` | Kanıtlandı |
| `401` + `WWW-Authenticate: Bearer resource_metadata=...` | `backend/src/mcp/auth_bridge.py::PrincipalAuthMiddleware._deny` | `test_mcp_integration.py::test_unauthenticated_request_gets_401_with_www_authenticate` | Kanıtlandı |
| RFC 9728 protected-resource metadata, `resource` MCP URL ile birebir | `auth/server.py::_protected_resource_metadata` | `test_oauth_metadata_contract.py::ProtectedResourceMetadataTests` | Kanıtlandı |
| RFC 8414 AS metadata, CIMD çift bayrağı (`client_id_metadata_document_supported`+`"none"`) | `auth/server.py::authorization_server_metadata` | `test_oauth_metadata_contract.py::AuthorizationServerMetadataTests::test_cimd_support_is_advertised` | Kanıtlandı |
| PKCE S256 zorunlu, `code_challenge_methods_supported` reklamı | `auth/domain.py::verify_pkce` | `test_oauth_metadata_contract.py::test_pkce_s256_is_advertised_as_required` | Kanıtlandı |
| RFC 8707 `resource` parametresi authorize+token'da zorunlu, audience uyuşmazlığı `invalid_target` | `auth/domain.py::AuthorizationTransaction.__init__`/`consume_authorization_code` | `test_auth_authorization_flow_http.py` | Kanıtlandı |
| `/token` `application/x-www-form-urlencoded` kabul eder | `auth/server.py::token` (`Form(...)`) | `test_auth_server_http.py::TokenContentTypeTests` | Kanıtlandı |
| Refresh rotation aynı cevapta yeni refresh token döner, geçersiz refresh `invalid_grant` | `db/oauth_store.py::TokenRepository.rotate` | `test_oauth_store.py` | Kanıtlandı |
| Claude Code loopback (`localhost`/`127.0.0.1`, port yok sayılır) | `auth/domain.py::redirect_uri_allowed` | `test_auth_domain.py` | Kanıtlandı |
| CIMD SSRF/DNS-rebinding koruması, yalnız `https://`, redirect yok, boyut sınırı | `auth/cimd.py::fetch_client_metadata` | `test_auth_cimd.py` | Kanıtlandı |
| Google refresh token MCP/Claude'a hiç verilmez | `mcp/credentials.py`/`auth/vault.py` (yalnız vault ref taşınır) | `test_secret_redaction.py`, `test_mcp_integration.py` | Kanıtlandı |
| MCP Inspector + Claude custom connector canlı testi | — | — | Bekliyor (canlı public domain/hosting gerekir, bkz. 12.3/12.8) |
| Anthropic egress IP `160.79.104.0/21` erişimi/WAF uyumu | — (deployment ADR'i açık, `docs/DEPLOYMENT.md`) | — | Bekliyor (sağlayıcı/topoloji kararına bağlı) |

### Tool politikası

- Read ve write kesin ayrıdır; catch-all `api_request`, raw GAQL veya raw mutate yoktur.
- Her tool: ≤64 karakter ad, insan-okur `title`, dar açıklama, kapalı input/output schema ve doğru annotation.
- Read-only: `readOnlyHint: true`. Directory v1/Faz 1'de Google Ads'te değişiklik yapan tool yoktur. Böyle
  bir tool Faz 8'de açılırsa `destructiveHint: true` taşır; kullanıcıya Claude confirmation ek olarak backend
  immutable approval kapısı uygulanır.
- Geçerli parametre başarı döndürür; invalid/unauthorized/quota hata mesajları actionable ve secret-free'dir.
- Tool cevapları page/limit/field allowlist ile token-frugal olur. Conversation history, Claude memory veya
  kullanıcı dosyaları istenmez/toplanmaz.
- Reklam verisi içindeki prompt injection talimat sayılmaz; tool description davranış manipülasyonu içermez.
- `ui/open-link` ilk fazda yoktur. Daha sonra eklenirse yalnız gerekli URI origin/scheme allowlist'i kullanılır,
  her hedef güvenlik incelemesinden geçer ve submission kaydı güncellenir.

### Submission paketi

| Kanıt | Kabul kriteri | Durum |
|---|---|---|
| Server URL + auth | Streamable HTTP, OAuth discovery, HTTPS | Kod+test kanıtlandı (yukarıdaki tablo); canlı domain'de yeniden doğrulama Bekliyor |
| Tool envanteri | Ad/title/schema/annotations/read-write matrisi | Kanıtlandı — `test_mcp_integration.py::test_registered_tools_have_closed_schemas_and_readonly_annotations` |
| Functional test | Her tool MCP Inspector + Claude custom connector | Bekliyor (canlı public domain gerekir) — yerel eşdeğeri (gerçek MCP `tools/list`+`tools/call` üzerinden) `test_mcp_integration.py`'de kanıtlı |
| Test account | Dolu Google Ads test hesabı, adım adım reviewer rehberi | Bekliyor — bkz. `todo.md` 12.3 |
| Public docs | Setup, kullanım, troubleshooting, en az 3 örnek prompt | Örnek prompt'lar hazır (bkz. "Submission görselleri ve demo materyali"); public setup/troubleshooting sayfası `todo.md` 12.4'e (hukuk sonrası) bağlı, Bekliyor |
| Privacy/support | Public URL, verified contact/security channel | Bekliyor — `LEGAL.md` Taslak, hukuk sonrası (`todo.md` 12.4) |
| Branding | İsim, logo, tagline, description, favicon | Bekliyor — `todo.md` 12.8 (dış karar) |
| Surface test | Claude.ai, Desktop, mobile, Code sonuçları | Bekliyor — canlı domain + Claude Code CIMD uyumluluk matrisi gerekir (`todo.md` 12.9) |
| Policy | Directory Terms/Policy ve Usage Policy onayı + 7 Compliance onayı (portal adımı) | Bekliyor — yalnız gerçek submission portalında verilebilir (`todo.md` 12.7) |
| Allowed link URIs | `ui/open-link` yok kanıtı veya dar URI allowlist'i | Kanıtlandı — kod tabanında `ui/open-link` hiç kullanılmıyor (`backend/src/mcp/` grep), `docs/MCP.md`/`CONNECTOR_SUBMISSION.md`'de "ilk fazda yok" olarak belgeli |
| Bakım ve güvenlik yanıtı | Sahip, security contact, patch/incident SLA ve kaldırma planı | Bekliyor — sahip/security contact `todo.md` 12.8'e bağlı |
| Submission organizasyonu | Team/Enterprise org + Directory management/Libraries izni | Bekliyor — Individual/Pro hesaptan submission portalı açılamaz (`todo.md` 12.7/12.8, dış karar) |

Başvuru yapılmadan checklist'in tamamı kanıt bağlantısı veya tarihli test sonucu taşır. Yayından sonra tool
eklemek aynı güvenlik/annotation/regression kontrolünü gerektirir; directory uyumu periyodik izlenir.
Yayın sonrası connector güvenli ve çalışır tutulur; açıklama/tool listesi gerçeği yansıtır, güvenlik bildirimi
izlenir ve kritik sorun düzeltilene kadar etkilenen tool veya connector kontrollü biçimde devre dışı bırakılır.

### Reviewer test ortamı ve test hesabı prosedürü (Faz 12.3)

Google'ın [test account best practices](https://developers.google.com/google-ads/api/docs/best-practices/test-accounts)
sayfası doğrulandı (2026-07-22): bir **test manager hesabı** üretim manager hesabından ayrı bir Google
Account ile Google Ads arayüzünden oluşturulur; altında açılan her client hesabı otomatik test hesabı olur;
test hesapları **onaylı developer token gerektirmez** (üretim manager hesabının token'ı test hesaplarına
karşı kullanılabilir), gerçek reklam yayınlamaz/faturalandırmaz ve üretim hesaplarıyla hiçbir şekilde
etkileşmez. Kritik kısıt: test hesaplarının **serving metrikleri her zaman boştur** (impressions/clicks/
cost/conversions hep sıfır/yok) — reviewer bunu bir connector kusuru sanmamalıdır. 1 yıl API/login
aktivitesi olmayan test hesabı kalıcı silinir.

Prosedür (uygulama, gerçek hesap açılana kadar bu belgede taslak/hazır durumda kalır — hesabın kendisi
ürün sahibinin Google Ads arayüzünden yapması gereken dış bir adımdır, ajan tarafından yapılamaz):

1. **Test hesap topolojisi:** Ürün sahibi mevcut üretim manager hesabından tamamen ayrı, yalnız bu amaca
   özgü bir Google Account ile bir test manager hesabı ve altında en az bir test client hesabı açar.
   Hiçbir gerçek müşteri/kişisel veri bu hesaba girilmez.
2. **Connector test principal'ı:** Reviewer, bu test Google Account'uyla connector'a OAuth ile bağlanır;
   bu principal connector DB'sinde gerçek kullanıcılardan tamamen izole bir `principal_id` alır (mevcut
   principal/customer izolasyon garantisi — `docs/AUTH.md`, `db/repository.py` — buraya da uygulanır,
   ek kod gerekmez).
3. **Reset prosedürü:** Her reviewer oturumu öncesi test principal'ın connector-taraflı durumu
   (`ads_account`, `proposal`, `oauth_credential`, `audit` satırları) sıfırlanır ve `sync_accessible_accounts`
   yeniden çalıştırılır; reset adımı `docs/OPERATIONS.md` "Yedekleme ve geri yükleme"/"Periyodik kontroller"
   ile aynı prensiptedir (gerçek müşteri verisi asla test/staging'e kopyalanmaz).
4. **Sample proposals:** Test client hesabının (gerçek olmayan) bir kampanya ID'siyle en az üç örnek
   `prepare_proposal` çağrısı önceden hazırlanır — biri `campaign_pause`, biri `campaign_enable`, biri
   `campaign_budget_update` — böylece reviewer `get_proposal`/`list_proposals` ve `/approvals` onay/red
   akışını gerçek Google Ads verisi beklemeden uçtan uca deneyebilir (bu tool zaten Google Ads'e hiç
   yazmadığından test hesabının serving-metrik kısıtından etkilenmez).
5. **Adım adım reviewer rehberi:** (a) connector'ı Claude'a ekle → OAuth consent ekranında test hesabıyla
   giriş yap; (b) `list_accessible_accounts`/`sync_accessible_accounts` çalıştır — bağlı test client
   hesabını gör; (c) `get_campaign_performance`/`get_ad_group_performance`/`get_keyword_performance` çalıştır
   — şema/pagination/annotation'ların doğru çalıştığını, satırların test hesabı kısıtı nedeniyle boş
   döndüğünü gözlemle; (d) `prepare_proposal` ile yeni bir taslak oluştur, `get_proposal`/`list_proposals`
   ile durumu doğrula; (e) `/approvals` sayfasını tarayıcıda aç, bir öneriyi onayla bir öneriyi reddet; (f)
   bağlantıyı kes (disconnect) ve token/credential'ın iptal edildiğini doğrula.
6. **Credential teslimi:** Test Google Account'un e-posta/parolası veya connector tarafı test bearer
   token'ı **hiçbir zaman** bu repoya, `todo.md`'ye veya Anthropic submission formunun serbest metin
   alanlarına yazılmaz. Anthropic'in submission portalı "Test & launch" adımı zaten bunu kimlik bilgisi
   *girilecek* ayrı bir alan olarak tasarlamıştır; buraya girilecek değerler yalnız portal doldurulurken,
   ürün sahibi tarafından, oturumda tutulur. Rotasyon/iptal prosedürünün tatbikatı `todo.md` 12.10'a bırakıldı.

Bu prosedür belge düzeyinde tamdır ve gerçek bir test hesabı açıldığında hiçbir kod değişikliği gerektirmez
(principal/customer izolasyonu zaten genel amaçlı). Gerçek test hesabının açılması, reviewer'a credential
teslimi ve "Test account" satırının `Kanıtlandı`ya geçmesi ürün sahibinin dış eylemine bağlıdır — bu yüzden
`todo.md` 12.3 bu adım tamamlanana kadar `[ ]` kalır.

### Submission görselleri ve demo materyali (Faz 12.5)

Anthropic'in submission sayfası carousel/screenshot zorunluluğunu yalnız MCP Apps'e bağladığı için (yukarıdaki
"Açık sorular" — çözüldü), bu connector için resmi bir screenshot *zorunluluğu* yok. Yine de reviewer'a ve
`docs/DOCUMENTATION.md`'nin public docs kapısına yardımcı olacak iki tür materyal hazırlandı:

- **`/approvals` ekran görüntüleri** (`docs/assets/connector-submission/`): connector'ın sahip olduğu tek
  browser yüzeyinin gerçek, çalışan halinin dört durumu — `approvals-pending.png` (üç örnek öneri: pause/
  enable/budget_update), `approvals-after-approve.png`, `approvals-after-reject.png`, `approvals-empty.png`.
  Gerçek bir Chromium (Playwright, `backend/tests/test_e2e_approvals_playwright.py` ile aynı fake-Google/
  in-memory-DB deseni) ile, tamamen sentetik veriyle üretildi: hesap `1112223333`, kampanya `5551001-3`,
  reviewer e-postası `reviewer-test@example.com` — hiçbiri gerçek bir Google Ads hesabına veya kişiye ait
  değil. Üretim script'i tek seferlik bir yardımcı araç olduğundan (kalıcı bir test veya uygulama modülü
  değil) repoya eklenmedi; ekran görüntülerinin kendisi kalıcı kanıt olarak `docs/assets/` altında tutulur
  ve gerektiğinde `test_e2e_approvals_playwright.py`'deki aynı desenle yeniden üretilebilir.
- **Claude tarafı akış için örnek prompt'lar** (screenshot değil, metin — Claude'un kendi sohbet arayüzü
  bizim render ettiğimiz bir yüzey olmadığından ve canlı bir deployment/domain olmadan gerçek bir Claude
  oturumu ekran görüntüsü alınamayacağından, bkz. `todo.md` 12.8/12.9): "Bağlı Google Ads hesaplarımı
  listele", "Son 30 gündeki kampanya performansımı getir ve düşük performanslı kampanyaları öner",
  "5551001 numaralı kampanyayı duraklatma önerisi hazırla ve nedenini açıkla". Bu üç örnek, Anthropic'in
  submission portalı "Use cases" adımının ve genel Directory Policy'nin istediği "en az üç örnek prompt/
  kullanım senaryosu" gereksinimini karşılar (bkz. `docs/PRODUCT.md` örnek kullanıcı senaryolarıyla tutarlı).

Gerçek bir Claude sohbet ekran görüntüsü veya video (canlı connector + gerçek Claude oturumu) yalnız bir
public domain deploy edildikten sonra alınabilir; bu adım `todo.md` 12.5'in tam kapanışını `todo.md`
12.8/13.x'teki hosting/domain kararına bağlı bırakır — mevcut dört `/approvals` ekran görüntüsü ve üç örnek
prompt, bugün üretilebilecek olan tüm demo materyalini kapsar.

### Faz 12.6 — iç denetim (bağımsız yeniden doğrulama, submission YAPILMADI)

Sekiz kategorinin her biri, yukarıdaki tablolardan ve ilgili belgelerden bağımsız olarak tek tek
yeniden doğrulandı. **Sonuç: paket submission'a hazır DEĞİL** — aşağıdaki dört blokajın hepsi gerçek dış
karar/eylem gerektiriyor, hiçbiri kod veya belge eksikliği değil.

| Kategori | Doğrulama | Verdict |
|---|---|---|
| Teknik | Faz 12.1 tablosu: her kriter kod+teste bağlı, kusur yok | ✅ Hazır (canlı domain'de yeniden doğrulama hariç) |
| Güvenlik | `docs/SECURITY.md` tehdit modeli, 2.1-2.5 + 12.1'deki OAuth/CIMD/SSRF denetimleri güncel | ✅ Hazır |
| Legal | `LEGAL.md`/`PRIVACY_POLICY.md`/`TERMS.md` üçü de `DRAFT — NOT FOR PUBLICATION` | ❌ **Blokaj** — hukukçu incelemesi (`todo.md` 12.4/11.3/11.4) |
| Support | Public support/security contact adresi yok (henüz doğrulanmış domain/marka yok) | ❌ **Blokaj** — `todo.md` 12.8 |
| Reviewer | Prosedür yazıldı (Faz 12.3); gerçek test hesabı/credential henüz yok | ❌ **Blokaj** — ürün sahibinin Google Ads'te hesap açması |
| Branding | İsim/logo/tagline/domain kesinleşmedi | ❌ **Blokaj** — `todo.md` 12.8 (dış karar) |
| Free pricing | Kod tabanında hiç ödeme/faturalama yolu yok (`backend/src` içinde billing modülü yok); `LEGAL.md`/`AGENTS.md` "tamamen ücretsiz" açıkça beyan ediyor | ✅ Hazır |
| Google policy | `docs/GOOGLE_API_ACCESS.md` hâlâ Taslak (RMF/Standard Access sınıflandırması bekliyor) — Anthropic submission'ını teknik olarak engellemez ama connector'ın launch sonrası tam işlevi buna bağlı | ⚠️ Ayrı bloke (Faz 11/`GOOGLE_SUBMISSION_EVIDENCE.md`), Anthropic Directory adımını doğrudan durdurmaz |

Bloklayıcı olmayan (✅) dört kategori gerçekten submission-hazır durumda ve gelecekteki bir denetimde
yeniden doğrulanmadan kabul edilebilir. Blokajlı (❌) dört kategori kapanmadan gerçek submission
(`todo.md` 12.7) başlatılmaz. Bu denetim submission formunu doldurmadı, hiçbir dış sisteme yazmadı;
yalnız mevcut kanıtı bağımsız olarak yeniden kontrol edip kullanıcıya özetledi.

## Açık sorular

- Verified domain, OAuth authorization server ürünü ve connector'ın public ürün adı (bkz. `todo.md` 12.8).
- Test hesabı credential'larının Anthropic'e güvenli teslim kanalı ve rotasyonu (bkz. `todo.md` 12.3/12.10).
- Submission'ı yapacak Anthropic/Claude organizasyonunun Team mi Enterprise mi olacağı ve Directory
  management/Libraries izninin kime verileceği (portal gereksinimi, `todo.md` 12.7/12.8'e bağlı dış karar).
- ~~MCP Apps UI gerekip gerekmediği~~ — **çözüldü (2026-07-22):** Faz 1.3 kararı (`docs/DESIGN.md`) yalnız
  minimal `/approvals` HTML yüzeyini kabul etti, MCP Apps UI eklenmedi; Anthropic'in resmi submission sayfası
  carousel screenshot gereksinimini yalnız "MCP Apps" (interaktif UI) submission'larına bağlıyor. Bu connector
  yalnız `tools` submit ettiği için screenshot/carousel zorunluluğu **uygulanmıyor**.

## Güncelleme geçmişi

- 2026-07-22 — Faz 12.5: `docs/assets/connector-submission/` altına gerçek Chromium ile üretilmiş dört
  `/approvals` ekran görüntüsü (pending/after-approve/after-reject/empty) ve üç örnek Claude prompt'u
  eklendi — tamamı sentetik veriyle (`test_e2e_approvals_playwright.py` ile aynı fake-Google deseni), gerçek
  müşteri/hesap/token verisi yok. Gerçek canlı Claude oturumu ekran görüntüsü/video, public domain deploy'u
  bekliyor (`todo.md` 12.8/13.x).
- 2026-07-22 — Faz 12.3: reviewer test ortamı prosedürü yazıldı (test manager/client hesap topolojisi,
  connector test principal izolasyonu, reset, en az üç örnek `prepare_proposal`, adım adım reviewer
  rehberi, credential teslim kısıtı). Google'ın test-accounts sayfası doğrulandı: test hesapları onaylı
  developer token istemez ama serving metrikleri her zaman boştur — reviewer rehberine bu kısıt açıkça
  eklendi ki reporting tool'larının boş satır dönmesi kusur sanılmasın. Gerçek hesabın açılması ürün
  sahibinin dış eylemi olduğundan `todo.md` 12.3 `[ ]` kaldı.
- 2026-07-22 — Faz 12.1 teknik pre-submission denetimi: beş birincil kaynak (submission/review-criteria/
  authentication/directory-policy/MCP authorization) yeniden çekildi, her kriter mevcut koddaki karşılığına ve
  regresyon testine bağlandı (yeni "Kriter → kanıt eşlemesi" tablosu). Gerçek gaplar: submission artık
  Team/Enterprise org + Directory management izni gerektiren bir portal akışı (form değil) — yeni satır ve
  açık soru eklendi; Anthropic egress IP aralığı (`160.79.104.0/21`) `docs/DEPLOYMENT.md`'ye eklendi. Kod
  tarafında hiçbir gerçek kusur bulunmadı; MCP Apps screenshot açık sorusu "uygulanmıyor" olarak kapatıldı.
- 2026-07-18 — Faz 1.1 kapsam kararı kapatıldı: Directory v1 submission paketi Google Ads live write
  kabiliyeti beyan etmez; local `prepare_proposal` Google Ads'e dokunmayan proposal hazırlama tool'u olarak
  belgelenir.
- 2026-07-17 — 2026 Anthropic submission, review, auth ve directory policy gereksinimleri kaynaklandırıldı.
- 2026-07-17 — Yayın sonrası bakım/güvenlik yanıtı ve `ui/open-link` allowed-URI beyan kapıları eklendi.
