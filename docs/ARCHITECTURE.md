# Mimari

**Durum:** Kabul edildi  
**Son gözden geçirme:** 2026-07-17  
**Sonraki gözden geçirme:** 2026-10-17

## Amaç

Public Claude connector, connector OAuth, Google OAuth, MCP tool, kullanıcı izolasyonu, Google Ads adapter ve
audit bileşenlerinin güven sınırlarını tanımlamak.

## Araştırma

- Güncel [MCP Authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization),
  protected resource ile authorization server'ı ayırır; PKCE, discovery, audience ve HTTPS ister.
- Anthropic [connector authentication](https://claude.com/docs/connectors/building/authentication), directory
  connector'ın tek paylaşılan OAuth application kullandığını; gerçek `401` + protected-resource metadata ve
  hosted callback gereksinimini açıklar.
- MCP [Security Best Practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices),
  third-party OAuth proxy'de confused deputy ve token passthrough risklerini tanımlar.

## Karar

```text
Claude MCP client
  │ Streamable HTTP + connector access token (aud=mcp resource)
  ▼
Public MCP resource server ──► policy/schema/rate limit ──► audit
  │                                      │
  │ user subject                         └─► Google Ads adapter
  ▼                                               │
Connector Authorization Server                    ▼
  │ PKCE + explicit consent                  Google Ads API
  │
  └─► Google OAuth (upstream authorization)
       └─ encrypted per-user refresh token in secrets manager
```

- MCP resource server Google token kabul etmez; yalnız kendi authorization server'ının audience-bound kısa
  token'ını doğrular. Google credential hiçbir zaman Claude'a/token response'a geçmez.
- Authorization transaction Claude client, redirect URI, PKCE challenge, connector user subject ve Google
  consent sonucunu server-side state ile bağlar. Her kullanıcı açık connector consent görür; Google'ın önceki
  consent cookie'si connector consent'i atlatmaz.
- Connector user subject (`principal_id`) izolasyon köküdür. Her credential/account/proposal/execution/cache/
  queue/audit kaydı principal kapsamlıdır. Kullanıcının seçtiği customer ID doğrulanmış Google accessible-
  customer listesiyle eşleştirilir.
- MCP tool'ları amaç-özel ve dar şemalıdır. Read ve destructive write ayrı; raw GAQL/mutate yoktur.
- Directory v1/Faz 1'de Google Ads'e gerçek write yolu yoktur. Mevcut `prepare_proposal` yolu yalnız
  proposal→preview→human confirmation/approval adımlarını yerel DB üzerinde kurar; freshness/hash/account
  revalidation→Google mutate→append-only audit execution akışı Faz 8'e kadar kapalıdır.
- "Human confirmation/approval" adımı Claude'un tool-calling döngüsü dışındadır: `/approvals`
  adında, kendi hafif Google girişiyle (`docs/AUTH.md` -- "Approval-UI web girişi") korunan ayrı
  bir tarayıcı yüzeyidir. Bu yüzey `adwords` scope'u istemez ve Google Ads'e hiç yazmaz; yalnız
  `prepare_proposal`'ın oluşturduğu öneriye insan kararını (`approve`/`reject`) kaydeder.
- Public ingress yalnız TLS MCP/OAuth/health/legal-doc uçlarıdır. DB/secrets/queue private'dır. Egress Google,
  gerekli Anthropic-facing callback/metadata ve allowlist telemetry hedefleriyle sınırlıdır.
- Uygulama kendi Anthropic API'sine reklam verisi göndermez; analiz Claude kullanıcısının connector tool
  sonuçları üzerinden gerçekleşir. Bu ayrım veri akışı ve maliyet modelini sadeleştirir.
- ASGI uygulama yaşam döngüsü MCP session manager'ı başlatır/durdurur ve shutdown sırasında yerel HTTP client ile
  sqlite bağlantısını kapatır; testler shutdown sonrası repository erişimine güvenmez.

## Bileşenler

- `auth`: OAuth 2.1 AS/resource metadata, upstream Google OAuth, connector token ve revoke.
- `mcp`: Streamable HTTP, tool schemas/annotations, user-bound policy ve minimal sonuç.
- `api`: principal+customer doğrulanmış Google Ads read adapter; mutate adapter'ı Faz 8'e kadar yoktur.
- `approval`: immutable proposal/hash ve kullanıcı onayı; Google Ads execution state machine'i Faz 8'e kadar yoktur.
- `db`: principal-scoped SQLite prototip repositories, PostgreSQL/Alembic production schema, RLS
  principal transaction helper'ı ve append-only audit.
- `operations`: quota/fair-use, incident, deployment ve reviewer test hesabı.

## Açık sorular

- Hosting/region, public domain ve Google/Anthropic egress/WAF gereksinimleri.

## Güncelleme geçmişi

- 2026-07-22 — Connector Google callback PostgreSQL composition'a iki kısa transaction olarak
  bağlandı: ilk transaction authorization state'i okur, ikinci transaction doğrulanmış principal
  context'inde credential/grant/code/completion kayıtlarını atomik yazar. Google ve vault egress'i
  iki transaction arasında gerçekleşir; rollback yeni vault referansını geri iptal etmeyi dener.

- 2026-07-19 — `/authorize` transaction oluşturma ve `/authorize/consent` transaction okuma/durum
  ilerletme işlemleri production composition'da kısa PostgreSQL unit-of-work sınırlarına taşındı. Consent
  okuma ve compare-and-set durum ilerletme aynı transaction içinde atomiktir.

- 2026-07-19 — PostgreSQL production composition için request-scoped unit-of-work sınırı eklendi:
  aynı istek içindeki repository'ler tek connection/transaction ve RLS context paylaşır; principal doğrulama
  sonrası bağlanabilir, OAuth code redemption exact-hash bootstrap kullanır. Mevcut route/MCP wiring'i
  tamamen taşınana kadar production başlangıcı fail-closed kalır.
- 2026-07-19 — Connector OAuth `/token` route'u unit-of-work sınırına bağlanan ilk gerçek ASGI yolu oldu.
  Code claim + token pair insert ve refresh rotation tek PostgreSQL transaction/RLS context içinde yürür;
  factory uygulama composition root'undan `AuthContext` içine enjekte edilir ve gerçek ASGI route testiyle
  doğrulanır; local SQLite davranışı regresyon yolu olarak korunur.
- 2026-07-19 — Bearer HTTP API hesap/proposal yolları access-token exact-hash bootstrap, token doğrulama ve
  principal-scoped repository sorgularını tek PostgreSQL request transaction'ında çalıştırır. SQLite aynı
  route sözleşmesinin local regresyon adaptörü olarak korunur.
- 2026-07-19 — MCP bearer middleware'i PostgreSQL exact-hash token bootstrap ve doğrulamasına bağlandı.
  Auth transaction'ı downstream tool'dan önce kapanır; böylece Google Ads ağ çağrısı açık DB transaction
  içinde tutulmaz. Tool repository işlemlerinin ayrı kısa transaction'lara taşınması sonraki artıştır.
- 2026-07-19 — Browser approval session'ları exact cookie-hash SELECT bootstrap policy'siyle principal'a
  bağlanır; approval listeleme, karar ve logout DB işlemleri tek kısa PostgreSQL unit-of-work içinde yürür.
  Dış ağ/secrets-manager etkileşimli login callback ayrıştırılmıştır; disconnect DB revocation ile
  durable vault outbox enqueue'yu tek transaction'da tamamlar ve vault çağrısını worker'a bırakır.
- 2026-07-19 — Login-only Google callback iki kısa PostgreSQL transaction'a ayrıldı: state atomik claim
  edildikten sonra transaction kapanır, Google code exchange DB dışında yürür, doğrulanmış subject için
  principal lookup/RLS bind/session create ikinci transaction'da tamamlanır.
- 2026-07-19 — Yalnız connector DB'sini kullanan MCP hesap/proposal tool'ları principal-bound kısa
  PostgreSQL transaction kullanır. Google reporting credential/vault çözümlemesi transaction dışına
  ayrılmıştır; disconnect de DB state geçişini durable vault-revocation outbox ile production yoluna bağlar.
- 2026-07-19 — Reporting credential çözümlemesi DB metadata transaction → transaction dışı vault read →
  transaction dışı Google Ads çağrısı olarak ayrıldı. AUTH provider hatası credential'ı ayrı kısa principal
  transaction'ında pasifleştirir.
- 2026-07-19 — Approval state transition yalnız pending satırı compare-and-set ile tüketir; execution
  reservation unique idempotency conflict'ini kontrollü biçimde kazanan satıra çözer ve payload snapshot'ını
  yeniden doğrular.

- 2026-07-18 — Faz 1.1 kapsam kararı kapatıldı: Directory v1/Faz 1'de Google Ads'e gerçek write/execution
  tool'u yoktur; `prepare_proposal` yalnız yerel DB'ye yazar ve execution/mutate mimarisi Faz 8 Google
  Compliance kapısına bağlı kalır.
- 2026-07-18 — Faz 4.3 ilk artış: production PostgreSQL RLS migration'ı ve
  `db/postgres.py::principal_transaction` helper'ı mimariye eklendi. İlk SQLAlchemy repository dilimi
  `principal`/`oauth_client_grant`/`ads_account`/`oauth_credential` için başladı. Mevcut ASGI composition
  root hâlâ SQLite prototip repositories kullanır; kalan production SQLAlchemy repository/app wiring ayrı
  artışta kapanır.
- 2026-07-18 — Faz 1.2: "Authorization server ürünü/kütüphanesi ve connector subject'in kalıcı
  kimlik kaynağı" sorusu kapatıldı. AS ürünü/kütüphanesi zaten `docs/decisions/0001-backend-stack.md`
  ile Authlib olarak kapanmıştı; connector subject'in kalıcı kimlik kaynağı şimdi
  `docs/decisions/0005-principal-identity-no-merge-no-recovery.md` ile kapandı (Google `sub`, kalıcı,
  merge/recovery bypass'ı yok).
- 2026-07-18 — Faz 1.3: "`/approvals`'ın ötesinde daha zengin bir dashboard gerekip gerekmediği"
  sorusu kapatıldı -- Faz 1'de yalnız minimal `/approvals` kalır, ayrı dashboard/MCP Apps UI
  eklenmez (bkz. `docs/DESIGN.md` "Güncelleme geçmişi", `docs/PRODUCT.md` "Açık sorular").
- 2026-07-17 — Mimari public Streamable HTTP connector, çift OAuth ve user-principal izolasyonuna çevrildi.
- 2026-07-17 — Ürün sahibi onayıyla Kabul edildi durumuna geçirildi.
- 2026-07-17 — "Write yolu"nun ilk adımı (`prepare_proposal`/`get_proposal` MCP tool'ları) uygulandı. Bu
  tool'lar yalnız kendi DB'mize yazar, Google Ads'e hiçbir mutate çağrısı yapmaz.
- 2026-07-17 — "Human confirmation/approval" adımına somut bir yüzey eklendi: `/login` +
  `/approvals` (docs/AUTH.md). "Claude dışında ayrı onboarding/account-management web UI
  kapsamı" açık sorusu kapatıldı; yerine daha zengin bir dashboard'un gerekip gerekmediği
  sorusu eklendi.
- 2026-07-17 — ASGI shutdown sırasında HTTP client ve sqlite bağlantısının deterministik kapatıldığı
  lifecycle sözleşmesi eklendi.

