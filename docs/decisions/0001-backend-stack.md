# ADR-0001: Backend teknoloji yığını ve connector OAuth 2.1 AS kütüphanesi

- Durum: Kabul edildi
- Tarih: 2026-07-17
- Sahip: Ürün sahibi onayıyla ajan (Claude Code)

## Bağlam

`AGENTS.md` Python 3.11+, resmi `google-ads` kütüphanesi ve resmi MCP Python SDK ile Streamable
HTTP transport üzerinden remote MCP sunucusunu zorunlu kılıyor. Ancak üç somut kütüphane/ürün
seçimi `docs/AUTH.md` ve `docs/DATABASE.md` içinde "Açık soru" olarak bırakılmıştı:

1. Connector'ın kendi OAuth 2.1 authorization server'ını (protected-resource + AS metadata, PKCE
   S256, audience-bound token) hangi kütüphaneyle kuracağı.
2. HTTP yüzeyi için framework (API_DESIGN.md zaten "aday: FastAPI" diyordu).
3. DB/ORM/migration aracı (DATABASE.md zaten "aday: SQLAlchemy 2 + Alembic" diyordu).

`docs/DOCUMENTATION.md` kuralı gereği bu TBD'ler kapanmadan onlara bağımlı üretim kodu
yazılamıyordu. `docs/TESTING.md`in "Kalite kapısı" bölümü ayrıca ilk backend iskeletinin **harici
bağımlılık gerektirmeyen** olmasını, uygulama bağımlılıklarının (pytest dahil) ayrı ve sonraki bir
adımda seçilip kilitleneceğini şart koşuyor — bu ADR kütüphane seçimini kapatır, fakat kurulumu
HTTP/DB/OAuth implementasyonuna asıl geçilen artışa erteler.

## Seçenekler

**Connector OAuth 2.1 AS:**
- *Authlib* — Python'da OAuth2/OIDC client+server için düşük seviyeli, spec-uyumlu primitive'ler
  sunan olgun bir kütüphane; RFC 8414 (AS metadata) ve RFC 7636 (PKCE) desteği resmi dokümante
  edilmiş durumda ([RFC8414 — Authlib docs](https://docs.authlib.org/en/latest/specs/rfc8414.html),
  [Authorization Server — Authlib docs](https://docs.authlib.org/en/latest/flask/1/authorization-server.html)).
  FastAPI ile aynı süreçte, ayrı bir IdP servisi kurmadan entegre edilebilir.
- *Ağır bir IdP (Ory Hydra, Keycloak vb.)* — RFC 9728/8414/PKCE'yi kutudan çıkar çıkmaz destekler
  fakat ayrı bir servis, operasyon yükü ve deploy karmaşıklığı getirir; tek-connector MVP için
  aşırı mühendislik.
- *Sıfırdan el yazımı AS* — tam kontrol fakat token/PKCE/replay güvenliğini yeniden icat etme
  riski; SECURITY.md'nin "değişmez ilkeleri" ile uyumsuz risk yüzeyi.

**HTTP framework:** FastAPI (zaten API_DESIGN.md adayı) vs Flask/Starlette çıplak — FastAPI Pydantic
tabanlı şema doğrulamasıyla API_CONTRACTS.md'nin "kapalı şema" kuralına doğrudan uyuyor.

**DB/ORM:** SQLAlchemy 2 + Alembic (zaten DATABASE.md adayı) vs ham SQL — SQLAlchemy 2, DATABASE.md
kararındaki `FORCE ROW LEVEL SECURITY` + transaction-local principal context deseniyle iyi
entegre olan `Core`/`ORM` katmanı sağlıyor.

## Karar

- **Web/HTTP:** FastAPI + Uvicorn.
- **DB/ORM/migration:** PostgreSQL + SQLAlchemy 2 + Alembic.
- **MCP:** resmi `mcp` Python SDK, Streamable HTTP transport.
- **Connector OAuth 2.1 AS:** Authlib. Bilinen sınır: Authlib'in RFC 9728 (protected-resource
  metadata) desteği bu ADR tarihi itibarıyla henüz tamamlanmamış, açık bir geliştirme konusu
  ([authlib/authlib#752](https://github.com/authlib/authlib/issues/752)). RFC 9728 uç noktası
  (`/.well-known/oauth-protected-resource`) statik/az mantıklı bir JSON metadata cevabı olduğundan
  Authlib'in RFC 8414 AS metadata + PKCE + grant/token akışının üzerine elle, spec'e birebir uyumlu
  şekilde eklenecek; bu, `AUTH.md`'nin "Connector OAuth" kararındaki adım 1-2 ile tutarlıdır.
- **Google tarafı:** resmi `google-ads` kütüphanesinin kendi OAuth credential taşıyıcısı kullanılır;
  Authlib yalnız connector'ın kendi AS'i için kullanılır, Google token'ı hiçbir zaman connector
  AS'inden geçmez (SECURITY.md — token passthrough yasağı).
- **Sıralama:** Bu bağımlılıklar `backend/pyproject.toml`'a, ilgili modül (HTTP/DB/OAuth) fiilen
  yazılırken eklenir. İlk artış (`backend/src/db`) stdlib-only kalır (`sqlite3` + `dataclasses`);
  bu ADR yalnız "hangi kütüphane" sorusunu kapatır, kurulum zamanlamasını değiştirmez.

## Sonuçlar

- `docs/AUTH.md` ve `docs/DATABASE.md`'deki ilgili açık sorular bu ADR'a referansla kapatıldı.
- Authlib'in RFC 9728 boşluğu nedeniyle protected-resource metadata endpoint'i elle yazılacak ve
  ayrı bir contract testiyle (MCP.md "Tool ekleme kontrolü" / TESTING.md madde 11) doğrulanacak.
- Alembic migration'ları `expand → migrate/backfill → contract` sırasını izleyecek
  (DATABASE.md kararı); bu ADR ORM seçimini kapattığı için migration script'leri artık yazılabilir.
- Geri alma: bu seçimler MVP sonrası trafik/ölçek verisiyle yeniden değerlendirilebilir; değişiklik
  yeni bir ADR gerektirir (ADR'lar geçersiz kılınabilir, sessizce değiştirilmez).

## Kaynaklar

- [RFC 8414 — Authlib docs](https://docs.authlib.org/en/latest/specs/rfc8414.html)
- [Authorization Server — Authlib docs](https://docs.authlib.org/en/latest/flask/1/authorization-server.html)
- [authlib/authlib — RFC 9728 issue #752](https://github.com/authlib/authlib/issues/752)
- [RFC 9728 — OAuth 2.0 Protected Resource Metadata](https://www.rfc-editor.org/info/rfc9728/)
- `docs/AUTH.md`, `docs/DATABASE.md`, `docs/API_DESIGN.md`, `docs/TESTING.md` (iç kaynaklar).
