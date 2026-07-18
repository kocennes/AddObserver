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
- Write yolu proposal→preview→human confirmation/approval→freshness/hash/account revalidation→Google mutate→
  append-only audit akışıdır. Audit açılamazsa write fail-closed olur.
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
- `api`: principal+customer doğrulanmış Google Ads read/write adapter.
- `approval`: immutable proposal/hash, kullanıcı onayı ve execution state machine.
- `db`: principal-scoped repositories, RLS ve append-only audit.
- `operations`: quota/fair-use, incident, deployment ve reviewer test hesabı.

## Açık sorular

- Hosting/region, public domain ve Google/Anthropic egress/WAF gereksinimleri.
- Faz 1'de write tool bulunup bulunmayacağı.

## Güncelleme geçmişi

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
  tool'lar yalnız kendi DB'mize yazar, Google Ads'e hiçbir mutate çağrısı yapmaz; bu yüzden "Faz 1'de write
  tool bulunup bulunmayacağı" açık sorusu hâlâ Google Ads'i gerçekten değiştiren execution tool'u için geçerli.
- 2026-07-17 — "Human confirmation/approval" adımına somut bir yüzey eklendi: `/login` +
  `/approvals` (docs/AUTH.md). "Claude dışında ayrı onboarding/account-management web UI
  kapsamı" açık sorusu kapatıldı; yerine daha zengin bir dashboard'un gerekip gerekmediği
  sorusu eklendi.
- 2026-07-17 — ASGI shutdown sırasında HTTP client ve sqlite bağlantısının deterministik kapatıldığı
  lifecycle sözleşmesi eklendi.

