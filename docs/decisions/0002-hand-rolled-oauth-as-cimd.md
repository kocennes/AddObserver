# ADR-0002: Bağlayıcı AS'i elle yazılır, Authlib düşürülür; istemci kimliği için CIMD

- Durum: Kabul edildi
- Tarih: 2026-07-17
- Sahip: Ürün sahibi onayıyla ajan (Claude Code)

## Bağlam

ADR-0001, connector'ın kendi OAuth 2.1 authorization server'ı (AS) için Authlib'i seçmişti.
`backend/src/auth/` fiilen tasarlanırken Anthropic'in [connector authentication](https://claude.com/docs/connectors/building/authentication)
ve [lazy authentication](https://claude.com/docs/connectors/building/lazy-authentication) belgeleri
(2026-07-17'de canlı olarak okundu) üç şeyi netleştirdi:

1. Anthropic, directory connector'ları için **DCR yerine CIMD (Client ID Metadata Document) veya
   `oauth_anthropic_creds` önerir** — DCR her yeni bağlantıda yeni bir OAuth client kaydettiği için
   `AUTH.md`'nin varsaydığı "tek paylaşılan client" modeliyle çelişir. CIMD kararlı, kendinden
   referanslı bir `client_id` URL'idir (Claude Code'unki sabit:
   `https://claude.ai/oauth/claude-code-client-metadata`) — bu, "paylaşılan client" varsayımıyla
   DCR'den çok daha iyi örtüşür.
2. **Claude Code loopback akışında her zaman CIMD kullanır**, asla DCR değil — yani Claude Code
   kullanıcıları çalışacaksa CIMD desteği opsiyonel değildir.
3. CIMD; per-client veritabanı veya `/register` uç noktası gerektirmez — AS, `/authorize` anında
   `client_id` URL'ini kendisi çeker, dokümanın kendine-referanslı olduğunu doğrular ve
   `redirect_uri`'yi doküman içindeki `redirect_uris` ile karşılaştırır.

Bu üç bulgu ışığında Authlib'in bu artıştaki katkısı düşük çıktı:

- Google tarafı zaten (ADR-0001'in kendi kararıyla) `google-auth-oauthlib` kullanıyor; Authlib hiç
  bu bacak için düşünülmemişti.
- AS tarafının ihtiyaç duyduğu tek kriptografik doğrulama — PKCE S256 — `hashlib.sha256` +
  `base64.urlsafe_b64encode` ile ~3 satırlık stdlib kodu; bir kütüphane burada ek güvenlik
  sağlamıyor.
- Projenin gerçekte ihtiyaç duyduğu istemci kimliklendirme mekanizması — CIMD — 2025 taslak bir
  spesifikasyon; Authlib'in hiçbir CIMD desteği yok.
- Authlib'in genel OAuth-provider katmanı (`AuthorizationServer` + grant hook'ları) Flask/Django
  tarzı request nesneleri etrafında tasarlanmış; FastAPI'ye uyarlamak, bu repodaki yerleşik desenin
  (bkz. `backend/src/approval/domain.py`: saf fonksiyon + dataclass + ince repository + kapsamlı
  unittest) zaten sağladığı şeyi elde etmek için gereksiz bir framework-adaptasyon yükü demek.

## Seçenekler

- **Authlib'i FastAPI'ye adapte ederek zorla kullanmaya devam et** — ADR-0001 kararına sadık kalır
  fakat CIMD'yi (asıl ihtiyaç) yine de elle yazmayı gerektirir; Authlib yalnız PKCE için kalır ve
  bu, framework uyum maliyetine değmez.
- **Authlib'i düşür, AS'i elle yaz** — `backend/src/auth/domain.py` (saf mantık) +
  `backend/src/auth/store.py` (sqlite repository) + FastAPI route'ları; CIMD `backend/src/auth/cimd.py`
  içinde ayrı, test edilebilir bir modül olur. Bu reponun var olan deseniyle birebir tutarlı.

## Karar

Authlib bağımlılığı **tamamen düşürülür**. Connector AS'i elle yazılır:

- `backend/src/auth/domain.py` — PKCE S256 doğrulama, redirect URI eşleştirme (RFC 8252 loopback
  istisnası dahil), CIMD doküman doğrulama, authorization transaction/code/token durum makinesi,
  refresh rotation + reuse detection. Stdlib-only, `backend/src/approval/domain.py` ile aynı desen.
- `backend/src/auth/cimd.py` — SSRF korumalı CIMD fetch (yalnız `https://`, private/loopback/
  link-local IP reddi, timeout, boyut sınırı).
- Google tarafı **`google-auth-oauthlib`** ile kalır (ADR-0001'in bu parçası değişmedi).
- İstemci kimliklendirme mekanizması olarak yalnız **CIMD** uygulanır; DCR (`/register`) ve
  `oauth_anthropic_creds` bu artışta kapsam dışıdır (bkz. ilgili plan/artış notu). Anthropic'in
  kendi önerisi zaten CIMD/`oauth_anthropic_creds`'i DCR'ye tercih etmek; CIMD hem hosted Claude
  hem Claude Code için tek başına yeterli ve per-client kayıt gerektirmiyor.

## Sonuçlar

- ADR-0001'in "Connector OAuth 2.1 AS: Authlib" kararı bu ADR ile **geçersiz kılındı**; ADR-0001
  güncellenmez (ADR'lar sessizce değiştirilmez), bu ADR'a referansla süpersede edilmiş sayılır.
  ADR-0001'in Google tarafı (`google-auth-oauthlib`), FastAPI ve PostgreSQL/SQLAlchemy/Alembic
  kararları geçerliliğini korur.
- `backend/pyproject.toml`'da Authlib bağımlılığı yoktur; `google-auth-oauthlib`, `google-auth`,
  `fastapi`, `uvicorn`, `httpx`, `python-multipart`, `cryptography` eklenir.
- DCR/`oauth_anthropic_creds` ihtiyacı ileride ortaya çıkarsa (ör. yüksek trafik, kurumsal sabit
  client talebi) ayrı bir ADR ile değerlendirilir; bu karar onları imkansız kılmaz, yalnız bu
  artışın kapsamına almaz.
- Geri alma: CIMD desteği yetersiz kalırsa (ör. Anthropic dışı bir MCP client DCR gerektirirse)
  `/register` uç noktası aynı `auth/store.py` deseni üzerine ayrı bir ADR ile eklenir.

## Kaynaklar

- [Authentication for connectors](https://claude.com/docs/connectors/building/authentication) — DCR/CIMD/`oauth_anthropic_creds` karşılaştırması, callback URL'leri, refresh rotation gereksinimi.
- [Lazy authentication for MCP servers](https://claude.com/docs/connectors/building/lazy-authentication) — 401/`WWW-Authenticate` kanonik şekli, CIMD doğrulama akışı, discovery metadata alanları.
- [MCP Authorization 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization) — CIMD spesifikasyonu, PKCE S256 zorunluluğu, RFC 8707 `resource` parametresi.
- `docs/decisions/0001-backend-stack.md`, `docs/AUTH.md`, `docs/SECURITY.md` (iç kaynaklar).
