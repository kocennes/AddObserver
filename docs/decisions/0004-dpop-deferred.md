# ADR-0004: DPoP (RFC 9449) şimdilik uygulanmaz — hem Google hem connector token düzleminde ertelendi

- Durum: Kabul edildi
- Tarih: 2026-07-18
- Sahip: Ürün sahibi onayıyla ajan (Claude Code)

## Bağlam

`docs/SECURITY.md` "DPoP, Google'ın 2026 önerisine uygun olarak tasarım aşamasında değerlendirilir"
diyordu ve bunu açık bir soru olarak bırakıyordu. Bu ADR, `todo.md` 2.5'in istediği güncel resmi
araştırmayı yapıp bu soruyu kapatır. İki ayrı token düzlemi ayrı ayrı değerlendirildi, çünkü
DPoP'un uygulanabilirliği ikisinde de farklı:

1. **Google upstream OAuth** — connector'ın backend'de tuttuğu Google refresh token'ı.
2. **Connector'ın kendi authorization server'ı** — Claude MCP client'a verdiği access/refresh token
   çifti (ADR-0002'nin elle yazdığı AS).

### Google tarafı — 2026-07-18'de doğrulanan bulgular

- Google'ın güncel [OAuth 2.0 best practices](https://developers.google.com/identity/protocols/oauth2/resources/best-practices)
  sayfası DPoP'u yalnız **refresh token'a bağlama** için destekliyor; verilen access token'lar
  standart Bearer olarak kalıyor (`token_type: Bearer`), DPoP-bound olmuyor.
- Aynı sayfa DPoP'u özellikle **public client'lar** (SPA, native/installed app) için öneriyor —
  bunlarda refresh token client tarafında (tarayıcı depolama, mobil cihaz) tutulduğu için
  exfiltration riski yüksek. Confidential/server-side web app senaryosu için bu belge ayrı bir
  zorunluluk koymuyor.
- Resmi Python kütüphaneleri (`google-auth`, `google-auth-oauthlib` — bu projede Google bacağı için
  kullanılan kütüphaneler, bkz. ADR-0002) DPoP proof (imzalı JWT) üretimini desteklemiyor; ne
  PyPI/GitHub changelog'larında ne de kütüphane GitHub issue geçmişinde bu özelliğe dair bir
  uygulama veya açık PR bulunamadı.
- `authlib`'in kendi DPoP talebi (GitHub issue #315, 2021-02-09 açıldı) 2026-07-18 itibarıyla hâlâ
  açık ve bağlı bir PR yok — yani ekosistemdeki en yaygın alternatif OAuth kütüphanesi de bunu
  sağlamıyor.

### Connector AS tarafı — 2026-07-18'de doğrulanan bulgular

- Güncel [MCP Authorization spesifikasyonu (2025-11-25)](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)
  DPoP'tan hiç bahsetmiyor; token hırsızlığına karşı önerdiği mekanizma **kısa ömürlü access
  token + public client'lar için zorunlu refresh rotation**. Bu proje her ikisini de zaten
  uyguluyor: `verify_access_token` audience-bound kısa ömürlü token doğrular, `db/oauth_store.py::rotate`
  her redeem'de yeni bir çift üretir ve **aynı `family_id`'nin tekrar kullanımını fail-closed
  `revoke_family` ile kapatır** (bkz. `docs/SECURITY.md` "Uçtan uca tehdit modeli" T2).
- MCP'nin genişletme mekanizması (`MCP Authorization Extensions` deposu) gelecekte bir DPoP
  eklentisi taşıyabilir, ama 2026-07-18 itibarıyla ne çekirdek spesifikasyonda ne extension
  listesinde somut, kararlı bir DPoP tanımı yok — bugün uygulanacak resmi bir sözleşme yok.

### Mimari bağlam

Bu projede Google refresh token'ı hiçbir zaman bir public client'a (tarayıcı, mobil, Claude) geçmez;
yalnız backend'in kendi vault'unda (`auth/vault.py`) şifreli tutulur ve MCP resource server Google
token'ını client'a iletmez (`docs/ARCHITECTURE.md`, `docs/SECURITY.md` "MCP ve model güvenliği").
DPoP'un asıl çözdüğü tehdit — public client tarafında tutulan bir refresh token'ın çalınıp başka bir
cihazdan replay edilmesi — bu mimaride yapısal olarak zaten yok; refresh token hiçbir zaman
network'ün client ucuna çıkmıyor.

## Seçenekler

- **Google tarafı için elle DPoP proof imzalama uygula** — Reddedildi. Resmi kütüphane desteği
  olmadan JWK üretimi, `ES256` imzalama, `htm`/`htu`/`iat`/`jti` claim'lerini doğru ürettiğimizi
  kanıtlayacak bir test yüzeyi kurmak gerekirdi; `todo.md`'nin "Desteklenmeyen veya yarım DPoP
  implementasyonu yazma" kısıtını ihlal eder ve bakım yükü, azalttığı riskle orantısız (refresh
  token zaten public client'a hiç çıkmıyor).
- **Connector AS için kendi DPoP doğrulamamızı elle yaz** — Reddedildi. MCP spesifikasyonu bunu
  istemiyor/tanımlamıyor; Claude MCP client'ının DPoP proof üretip üretmeyeceği bizim kontrolümüzde
  değil (istemci tarafı da desteklemeli). Tek taraflı, spesifikasyon dışı bir sözleşme icat etmek
  olur.
- **Şimdilik uygulama; kısa ömürlü token + refresh rotation/reuse-detection ile yetin, karar
  tarihli olarak ertele** — Kabul edildi. Mevcut kontroller (audience-bound kısa ömürlü access
  token, family-based reuse detection, encrypted server-side refresh token custody) DPoP'un
  hedeflediği tehdidi bu mimaride zaten kapatıyor; resmi kütüphane/spesifikasyon desteği
  oluştuğunda yeniden değerlendirilir.

## Karar

DPoP **şimdilik uygulanmaz**, ne Google upstream OAuth bacağında ne connector'ın kendi AS'inde.
Mevcut mitigasyonlar (kısa ömürlü audience-bound connector access token, refresh rotation +
family-based reuse detection, Google refresh token'ının yalnız backend vault'unda şifreli
tutulması ve hiçbir client'a geçmemesi) bu artışta yeterli kabul edilir.

Yeniden değerlendirme tetikleyicileri (herhangi biri gerçekleşirse bu ADR gözden geçirilir):

1. `google-auth`/`google-auth-oauthlib` resmi olarak DPoP proof üretimini destekler hale gelirse.
2. MCP spesifikasyonu (veya kararlı bir MCP Authorization Extension) DPoP'u tanımlar/zorunlu
   kılarsa.
3. Mimari, Google refresh token'ının bir public client tarafında tutulduğu bir modele
   (ör. doğrudan tarayıcıdan Google'a bağlanan bir mobil/SPA istemci) geçerse — bugünkü confidential
   server-side custody varsayımı geçersiz kalırsa.

## Sonuçlar

- `docs/SECURITY.md`'nin "DPoP, Google'ın 2026 önerisine uygun olarak tasarım aşamasında
  değerlendirilir" cümlesi bu ADR'a referansla netleştirilir; "Açık sorular" listesindeki "DPoP
  desteği ve uygulanabilirliği" maddesi kapanır.
- Kod değişikliği yoktur — bu bilinçli bir "yapma" kararıdır.
- `todo.md` 2.5 tamamlandı olarak işaretlenir; yeniden değerlendirme tetikleyicileri `todo.md`'ye
  Faz 14.3 (üç aylık politika gözden geçirmesi) kapsamına not düşülür.

## Kaynaklar

- [OAuth 2.0 best practices](https://developers.google.com/identity/protocols/oauth2/resources/best-practices) — Google'ın DPoP-bound refresh token / Bearer access token ayrımı, public client önerisi.
- [MCP Authorization 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization) — "Token Theft" bölümü: kısa ömürlü access token + public client refresh rotation önerisi, DPoP'a referans yok.
- [RFC 9449 — OAuth 2.0 DPoP](https://datatracker.ietf.org/doc/html/rfc9449) — birincil spesifikasyon.
- [authlib/authlib#315](https://github.com/authlib/authlib/issues/315) — DPoP destek talebi, 2026-07-18 itibarıyla açık, bağlı PR yok.
- `docs/decisions/0002-hand-rolled-oauth-as-cimd.md`, `docs/SECURITY.md`, `docs/ARCHITECTURE.md` (iç kaynaklar).
