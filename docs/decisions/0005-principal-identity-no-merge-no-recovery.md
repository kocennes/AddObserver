# ADR-0005: Principal kimliği yalnız Google `sub`'a bağlanır — hesap merge/recovery bypass'ı yoktur

- Durum: Kabul edildi
- Tarih: 2026-07-18
- Sahip: Ürün sahibi onayıyla ajan (Claude Code)

## Bağlam

`todo.md` 1.2, connector `principal_id` kökünün Google subject'e bağlanma biçimini, email değişimini,
yeniden bağlantıyı, kayıp hesap kurtarmayı, principal merge'i ve aynı Google hesabının farklı MCP
client'larıyla kullanımını tehdit modeliyle değerlendirmemi istedi. `docs/AUTH.md` ("Açık sorular")
ve `docs/ARCHITECTURE.md` ("Açık sorular") aynı soruyu ("Connector kullanıcı subject'i Google kimliği
mi, ayrı account mı olacak?" / "connector subject'in kalıcı kimlik kaynağı") açık bırakıyordu. Kod
incelemesi kararın büyük kısmının zaten uygulanmış olduğunu gösterdi; bu ADR mevcut davranışı resmi
karara bağlıyor ve geriye kalan tek gerçek soruyu (kayıp hesap kurtarma/merge) kapatıyor.

### Mevcut davranış (kod incelemesiyle doğrulandı)

- `auth/server.py::google_callback`, principal'ı `PrincipalRepository.get_or_create("https://accounts.google.com",
  google_result.google_subject)` ile kurar (`server.py:287-289`) — `google_subject` her zaman
  `google.oauth2.id_token.verify_oauth2_token`'ın DÖNÜŞ değerinden gelir (Faz 3.5, `test_google_oauth.py`),
  connector kendi ham `id_token`'ını decode etmez.
- `db/repository.py::PrincipalRepository.get_or_create` principal'ı yalnız `(issuer, subject)` çiftiyle
  anahtarlar; email, isim veya başka bir profil alanı hiç okunmaz/saklanmaz. Google'ın `sub` claim'i
  [OpenID Connect Core](https://openid.net/specs/openid-connect-core-1_0.html) gereği "Subject Identifier
  ... is never reassigned" ilkesine göre kalıcıdır ve e-posta değişse bile aynı kalır — yani email
  değişimi zaten bu tasarımda principal kimliğini etkilemez, ayrı bir kod yolu gerekmez.
  `test_db_repository.py::PrincipalRepositoryTests::test_get_or_create_is_idempotent` aynı
  `(issuer, subject)` ile art arda çağrının hep aynı principal'ı döndürdüğünü,
  `test_different_subjects_get_different_principals` farklı `subject`'lerin hiçbir zaman aynı principal'a
  düşmediğini kanıtlıyor.
- `PrincipalRepository`'de merge/reassign/transfer metodu yoktur (yalnız `get`/`get_or_create`); hiçbir
  HTTP/MCP/approval yüzeyi iki principal'ı birleştiremez veya bir principal'ın kaydını başka bir
  `(issuer, subject)`'e taşıyamaz.
- Aynı Google hesabı (aynı `sub`) farklı MCP client'larıyla (`client_id`) kullanıldığında zaten aynı
  principal'a düşer; her `client_id` kendi `oauth_client_grant`/token ailesini alır
  (`ClientGrantRepository`, Faz 3.6 "Güncelleme geçmişi") ama hepsi aynı `principal_id`'nin altındadır —
  bu, "aynı Google hesabının farklı MCP client'larıyla kullanımı" sorusunu ek koda gerek kalmadan
  zaten güvenli biçimde kapatıyor.

### Geriye kalan gerçek soru: kayıp hesap kurtarma ve principal merge

Kullanıcı Google hesabına erişimini kalıcı olarak kaybederse (parola/2FA kaybı, hesap askıya alındı,
hesap silindi) veya aynı kişi iki farklı Google hesabı (ör. kişisel + işyeri) kullandıysa, bugün bu iki
principal'ı birleştirecek veya "eski" principal'ın verisini "yeni" bir Google hesabına taşıyacak hiçbir
mekanizma yok. Bu, kasıtlı bir tasarım boşluğu değil, çünkü her olası merge/recovery mekanizması aynı
temel tehdidi taşıyor: **principal_id, izolasyonun TEK kökü** (`docs/ARCHITECTURE.md` "Connector user
subject (principal_id) izolasyon köküdür"); onu ikinci bir kanıt olmadan değiştirebilen/birleştirebilen
her yol, principal A'nın kimliğini principal B'ye devretmenin bir yoludur — yani cross-user account
takeover'ın kendisidir.

## Seçenekler

- **Merge/recovery yok (bu ADR'ın kabul ettiği seçenek)** — Google `sub` tek ve kalıcı kimlik kökü
  kalır; kayıp Google hesabı erişimi = connector verisine (proposal/audit/account geçmişi) kalıcı erişim
  kaybı, support dahil hiç kimse bypass edemez. Yeni kod gerekmez (zaten bu davranış uygulanıyor);
  cross-user takeover yüzeyi sıfırdır çünkü principal kimliğini değiştirebilecek hiçbir yol yoktur.
- **Support-onaylı manuel merge/relink** — Reddedildi. Bir support operatörünün "bu kullanıcı gerçekten
  bu principal'ın sahibi" diye karar vermesi, Google'ın kendi OAuth/2FA doğrulamasından daha zayıf bir
  kimlik kanıtına (destek bileti, e-posta, sosyal mühendislik yüzeyine açık) dayanır; `PRODUCT.md`
  "Support/security operator" rolü zaten yalnız gerekçeli/süreli **break-glass veri erişimi** için
  tanımlı, kimlik/hesap sahipliği kararı vermek için değil ("Claude öneri üretir; kimlik, yetki, hesap
  sahipliği veya insan onayı kararı vermez" ilkesiyle aynı gerekçeyle support'a da uygulanır). Ayrıca
  yeni bir rol, audit akışı ve runbook gerektirir — bugün hiçbir kullanıcı talebi/somut ihtiyaç yokken
  bu maliyeti ve saldırı yüzeyini eklemek orantısız.
- **Kullanıcı self-servis kurtarma (yedek e-posta/ikincil kimlik doğrulama)** — Reddedildi. Bu,
  Google'ın kendi güçlü OAuth+2FA doğrulamasının YANINA, bizim işlettiğimiz daha zayıf bir ikinci
  kimlik doğrulama yüzeyi (e-posta ele geçirme ile devralınabilir) eklemek anlamına gelir — tam olarak
  DPoP'un (ADR-0004) hedeflediği türden bir "public/daha zayıf kanaldan devralma" riskini, olmayan bir
  yerden icat eder. `docs/PRODUCT.md`'nin "Ücretsiz, herkese açık" kapsamı ve mevcut mimari (tek
  kimlik sağlayıcı: Google) bu ek yüzeyi gerektirmiyor.

## Karar

Principal kimliği kalıcı olarak `(issuer="https://accounts.google.com", subject=<Google sub>)` çiftine
bağlanır (zaten uygulanan davranış). **Hiçbir principal merge, hesap birleştirme veya support-mediated
kurtarma mekanizması yazılmaz.** Bir kullanıcı Google hesabına erişimini kaybederse, connector'daki
kaydı (proposal/audit/account geçmişi) `docs/LEGAL.md`'nin kabul edeceği retention/deletion süreci
dışında kalıcı olarak erişilemez kalır; bu, cross-user account takeover yüzeyini yapısal olarak sıfıra
indiren bilinçli bir tasarım tercihidir.

Yeniden değerlendirme tetikleyicileri (herhangi biri gerçekleşirse bu ADR gözden geçirilir):

1. Ürün, Google dışında ikinci bir kimlik sağlayıcı (ör. email/password, SSO) eklemeyi kabul ederse.
2. Somut, tekrarlayan bir kullanıcı talebi (kayıp hesap nedeniyle veri erişimi) birikip destek maliyeti
   ADR'ın "orantısız" değerlendirmesini geçersiz kılarsa — bu durumda önce Google'ın kendi hesap kurtarma
   akışının (kullanıcı kendi Google hesabına Google üzerinden yeniden erişim kazanması, ki bu zaten aynı
   `sub`'ı korur ve hiçbir yeni kodumuz gerekmeden principal'ı otomatik geri getirir) yeterli olup
   olmadığı değerlendirilir.
3. Google `sub`'ın kalıcılık garantisi (OIDC Core) resmi olarak değişirse.

## Sonuçlar

- `docs/AUTH.md` ("Upstream Google OAuth" + "Açık sorular") ve `docs/ARCHITECTURE.md` ("Açık sorular")
  bu ADR'a referansla güncellenir; "Connector kullanıcı subject'i Google kimliği mi, ayrı account mı
  olacak?" sorusu kapanır.
- Kod değişikliği yoktur — davranış zaten bu ADR'ın kabul ettiği modelle uyumlu. Mevcut
  `test_db_repository.py::PrincipalRepositoryTests` (idempotent get_or_create, farklı subject'ler için
  farklı principal) bu kararın regresyon kanıtıdır; yeni bir test eklemeye gerek yoktur.
- `todo.md` 1.2 tamamlandı olarak işaretlenir.

## Kaynaklar

- [OpenID Connect Core 1.0 — Subject Identifier](https://openid.net/specs/openid-connect-core-1_0.html#IDToken) —
  `sub`'ın "Locally unique and never reassigned" garantisi.
- `docs/PRODUCT.md` ("Roller ve sınır", "Değişmez kabul kriterleri" — cross-principal erişim yasağı).
- `docs/ARCHITECTURE.md` ("Connector user subject (principal_id) izolasyon köküdür").
- `docs/decisions/0004-dpop-deferred.md` (benzer "yapma" kararı biçimi ve gerekçelendirme üslubu).
- `backend/src/auth/server.py::google_callback`, `backend/src/db/repository.py::PrincipalRepository`,
  `backend/tests/test_db_repository.py::PrincipalRepositoryTests`,
  `backend/tests/test_google_oauth.py` (iç kaynaklar).
