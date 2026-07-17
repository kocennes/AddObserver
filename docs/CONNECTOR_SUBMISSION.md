# Anthropic Connectors Directory başvurusu

**Durum:** Kabul edildi — listeleme öncesi checklist kanıtları tamamlanmalı  
**Son gözden geçirme:** 2026-07-17  
**Sonraki gözden geçirme:** 2026-10-17

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

### Tool politikası

- Read ve write kesin ayrıdır; catch-all `api_request`, raw GAQL veya raw mutate yoktur.
- Her tool: ≤64 karakter ad, insan-okur `title`, dar açıklama, kapalı input/output schema ve doğru annotation.
- Read-only: `readOnlyHint: true`. Google Ads'te değişiklik yapan tool: `destructiveHint: true`; kullanıcıya
  Claude confirmation ek olarak backend immutable approval kapısı uygulanır.
- Geçerli parametre başarı döndürür; invalid/unauthorized/quota hata mesajları actionable ve secret-free'dir.
- Tool cevapları page/limit/field allowlist ile token-frugal olur. Conversation history, Claude memory veya
  kullanıcı dosyaları istenmez/toplanmaz.
- Reklam verisi içindeki prompt injection talimat sayılmaz; tool description davranış manipülasyonu içermez.
- `ui/open-link` ilk fazda yoktur. Daha sonra eklenirse yalnız gerekli URI origin/scheme allowlist'i kullanılır,
  her hedef güvenlik incelemesinden geçer ve submission kaydı güncellenir.

### Submission paketi

| Kanıt | Kabul kriteri | Durum |
|---|---|---|
| Server URL + auth | Streamable HTTP, OAuth discovery, HTTPS | Bekliyor |
| Tool envanteri | Ad/title/schema/annotations/read-write matrisi | Bekliyor |
| Functional test | Her tool MCP Inspector + Claude custom connector | Bekliyor |
| Test account | Dolu Google Ads test hesabı, adım adım reviewer rehberi | Bekliyor |
| Public docs | Setup, kullanım, troubleshooting, en az 3 örnek prompt | Bekliyor |
| Privacy/support | Public URL, verified contact/security channel | Bekliyor |
| Branding | İsim, logo, tagline, description, favicon | Bekliyor |
| Surface test | Claude.ai, Desktop, mobile, Code sonuçları | Bekliyor |
| Policy | Directory Terms/Policy ve Usage Policy onayı | Bekliyor |
| Allowed link URIs | `ui/open-link` yok kanıtı veya dar URI allowlist'i | Bekliyor |
| Bakım ve güvenlik yanıtı | Sahip, security contact, patch/incident SLA ve kaldırma planı | Bekliyor |

Başvuru yapılmadan checklist'in tamamı kanıt bağlantısı veya tarihli test sonucu taşır. Yayından sonra tool
eklemek aynı güvenlik/annotation/regression kontrolünü gerektirir; directory uyumu periyodik izlenir.
Yayın sonrası connector güvenli ve çalışır tutulur; açıklama/tool listesi gerçeği yansıtır, güvenlik bildirimi
izlenir ve kritik sorun düzeltilene kadar etkilenen tool veya connector kontrollü biçimde devre dışı bırakılır.

## Açık sorular

- Verified domain, OAuth authorization server ürünü ve connector'ın public ürün adı.
- Directory formunda yazma kabiliyeti için beklenen reviewer onay UX'i.
- Test hesabı credential'larının Anthropic'e güvenli teslim kanalı ve rotasyonu.
- MCP Apps UI gerekip gerekmediği; ilk faz yalnız tools ise screenshot şartı uygulanmayabilir.

## Güncelleme geçmişi

- 2026-07-17 — 2026 Anthropic submission, review, auth ve directory policy gereksinimleri kaynaklandırıldı.
- 2026-07-17 — Yayın sonrası bakım/güvenlik yanıtı ve `ui/open-link` allowed-URI beyan kapıları eklendi.
