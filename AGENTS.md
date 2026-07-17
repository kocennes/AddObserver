# AGENTS.md

Bu dosya, bu repoda çalışan tüm AI kodlama ajanları (Claude Code, Codex, Cursor vb.) için
kaynak talimat dosyasıdır. Araca özel notlar için `CLAUDE.md` dosyasına bakın.

## Proje özeti
Herkese açık, **Anthropic'in Claude Connectors Directory'sinde yayınlanacak**, tamamen
**ücretsiz** bir Google Ads connector'ü (Supermetrics'in Google Ads'e özel, daraltılmış bir
benzeri — ama ücretsiz). Her kullanıcı kendi Google Ads hesabını kendi OAuth izniyle bağlar;
Claude, bu veriyi analiz edip kampanya/bütçe/anahtar kelime önerileri üretir; kullanıcı
onaylamadan hiçbir değişiklik hesaba yazılmaz.

## Kapsam / kapsam dışı
- Bu bir **üçüncü taraf, herkese açık connector**dır — dışa kapalı bir iç araç DEĞİLDİR.
- Şu an için sadece **Google Ads**. Meta/TikTok/LinkedIn gibi diğer platformlar ayrı, ileride
  değerlendirilecek bir faz — bu dosyalara dahil değil.
- Üçüncü taraflara açık olduğu için Google'ın **Required Minimum Functionality (RMF)**
  kurallarına uyum ve muhtemelen **Standard Access** seviyesi ZORUNLU (Basic Access'in
  günlük 15.000 işlem limiti çok kullanıcılı bir connector için yetersiz kalır). Detay için
  `docs/GOOGLE_API_ACCESS.md`.
- Anthropic'in Connector Directory'sine kabul edilmek için ayrı bir uyum seti var (OAuth 2.1 +
  PKCE, Streamable HTTP transport, herkese açık gizlilik politikası, destek kanalı, reviewer'lar
  için test hesabı, doğru tool annotation'ları). Detay için `docs/CONNECTOR_SUBMISSION.md`.
- **Tamamen ücretsiz.** Kendi Claude hesabından bu connector'ı ekleyip kendi Google Ads
  hesabını bağlayan herkes, hiçbir ücret ödemeden kullanabilir. Ödeme/abonelik/faturalama
  altyapısı YOK ve eklenmeyecek.

## Klasör yapısı
- `backend/src/api/` — Google Ads API istemci sarmalayıcıları; her çağrı `customer_id` parametresi alır (çoklu kullanıcı desteği)
- `backend/src/mcp/` — Claude'a bağlanan **uzak (remote) MCP sunucusu**; Streamable HTTP transport, Claude'un çağırabileceği tool tanımları (veri çek, öneri hazırlat, değişiklik uygula) ve doğru tool annotation'ları (read-only / destructive hint) burada
- `backend/src/auth/` — Her son kullanıcı için OAuth 2.1 + PKCE akışı, token saklama/şifreleme, kullanıcılar arası erişim izolasyonu
- `backend/src/approval/` — İnsan onay iş akışı: öneri oluşturma, bildirim, onay/red kaydı
- `backend/src/db/` — Kullanıcı↔hesap eşlemesi, denetim (audit) kayıtları
- `backend/tests/` — Testler (gerçek kullanıcı hesabına karşı ASLA test çalıştırılmaz, mock/test hesabı kullanılır)
- `docs/` — Proje belgeleri. **Önce `docs/DOCUMENTATION.md`'yi oku** — hangi işe hangi
  belgenin önce okunması gerektiğini ve hangi belgenin güncellenmesi gerektiğini listeler.
  İçerir: `ARCHITECTURE.md` (sistem akışı/güven sınırları), `SECURITY.md` (zorunlu güvenlik
  standardı), `DATA_MODEL.md` (veri şeması), `API_CONTRACTS.md` (HTTP + Google Ads sözleşmesi),
  `MCP.md` (MCP tool sözleşmeleri), `PRODUCT.md` (ürün gereksinimleri/roller), `DESIGN.md`
  (UI tasarım sistemi), `TESTING.md` (test stratejisi), `OPERATIONS.md` (deploy/runbook),
  `REPOSITORY.md` (Git/remote kuralları), `decisions/` (ADR kayıtları),
  `GOOGLE_API_ACCESS.md` (Google Ads erişim seviyesi/RMF uyumu — mevcut, `Taslak`; Google
  Compliance sınıflandırması bekleniyor), `CONNECTOR_SUBMISSION.md` (Anthropic Connectors
  Directory başvuru gereksinimleri — mevcut, `Kabul edildi`), `LEGAL.md` (gizlilik politikası/
  kullanım şartları kararları — mevcut, `Taslak`; hukukçu incelemesi bekleniyor).
- `PRIVACY_POLICY.md`, `TERMS.md` — Herkese açık, yayınlanacak metinler (Anthropic ve Google,
  herkese açık bir gizlilik politikası URL'i zorunlu tutuyor). Taslak metinler mevcut; yayınlanmadan
  önce hukukçu incelemesi ve işletmeci bilgileri gerekir (bkz. `LEGAL.md`).
- Her belgede `Durum` alanı vardır: `Taslak` kararlar uygulama yetkisi vermez, yalnız
  `Kabul edildi` belgelere dayanarak kod yazılabilir (bkz. `DOCUMENTATION.md`).

> ✅ **Kapsam pivotu tamamlandı (2026-07-17):** `docs/` altındaki tüm belgeler "dışa kapalı, ajans
> içi" modelden **herkese açık, self-servis, çok kullanıcılı connector** modeline (izolasyon kökü
> `tenant_id` yerine connector `principal_id`) çevrildi ve ürün sahibi tarafından `Kabul edildi`.
> Yalnız `LEGAL.md` (hukukçu incelemesi) ve `GOOGLE_API_ACCESS.md` (Google Compliance/RMF
> sınıflandırması) dışa bağımlı nedenlerle `Taslak` kalır; bunlara bağımlı alanlarda kod yazılmaz.
> Teknoloji seçim kararları için `docs/decisions/0001-backend-stack.md`'ye bakın.

## Teknoloji yığını (varsayılan)
- Backend: Python 3.11+
- Google Ads erişimi: resmi `google-ads` Python kütüphanesi
- Claude entegrasyonu: resmi MCP Python SDK ile **uzak (remote) MCP sunucusu**, Streamable HTTP
  transport üzerinden — Anthropic'in dizin gereksinimleri bunu şart koşuyor (yerel/stdio değil)
- Auth: OAuth 2.1 + PKCE (S256), her kullanıcı kendi rızasıyla bağlanır; saf
  client-credentials (kullanıcı etkileşimsiz) akış desteklenmiyor
- Production'da `/.well-known/oauth-protected-resource` uç noktası barındırılmalı
- Secrets: yerelde `.env` (asla commit edilmez), üretimde bir secrets manager
- Barındırma: 7/24 erişilebilir, production-grade bir sunucu gerekir (reviewer'lar ve gerçek
  kullanıcılar canlı olarak bağlanacak)

Farklı bir yığın gerekiyorsa önce gerekçesini bu dosyaya yaz, sonra uygula.

## Kurulum / komutlar
- Bağımlılık kurulumu: yok — ilk iskelet (`backend/src/db`, `backend/src/config.py`) bilinçli
  olarak stdlib-only'dir (bkz. `docs/TESTING.md` → Kalite kapısı). Uygulama bağımlılıkları
  (`docs/decisions/0001-backend-stack.md`'de seçilen FastAPI/SQLAlchemy/Alembic/Authlib/google-ads/
  mcp) HTTP/DB/OAuth implementasyonuna geçilen artışta `backend/pyproject.toml` ile eklenecek.
- Yerel çalıştırma: `uvicorn backend.src.app:create_app --factory` (`.env`'de en az `GOOGLE_ADS_CLIENT_ID/SECRET`,
  `GOOGLE_ADS_DEVELOPER_TOKEN`, `LOCAL_VAULT_KEY` gerekir — bkz. `.env.example`). `--factory` bilinçli: düz bir
  modül seviyesi `app = create_app()` gerçek bir sqlite bağlantısı/vault/Google OAuth istemcisi kurma yan etkisini
  modülü *import etmeye* bağlardı, bu da testlerin `.env`'siz `create_app`'i import etmesini bozardı.
- Test çalıştırma: `python -m unittest discover -s backend/tests -v`
- Lint: `TODO` — formatter/linter/type checker seçimi `docs/TESTING.md`'nin açık sorusu; ilk iskelet
  hiçbir lint aracına bağımlı değildir.

## Çalışma akışı (uygulanacak mimari)
1. Backend, Google Ads API'den ilgili kullanıcının Google Ads hesabının performans verisini çeker (`backend/src/api/`).
2. Veri, Claude'a (Anthropic API/MCP) analiz için gönderilir; yapılandırılmış (JSON) öneri istenir.
3. Öneri `backend/src/approval/` üzerinden kullanıcının kendisine (insan onayı) sunulur.
4. Onaylanan değişiklik `backend/src/api/` üzerinden ilgili hesaba yazılır.
5. Her adım `backend/src/db/` içinde denetim (audit) kaydı olarak saklanır.

Detaylı akış için `docs/ARCHITECTURE.md` dosyasına bakın (yeni kapsama göre güncelleyin).

## Güvenlik — ZORUNLU İLK ADIM
Herhangi bir auth, backend veya API entegrasyon kodu yazmadan **önce**:

1. `docs/SECURITY.md` dosyasını oku; yeni (herkese açık, çok kullanıcılı) kapsama göre eksik
   veya tarihi geçmiş bölümleri güncelle.
2. Aşağıdaki konularda web'den **güncel** kaynak araştırması yap (bu proje 2026'da başlıyor,
   eski/genel eğitim verisine güvenme, aktif olarak arama yap):
   - OAuth2 access/refresh token'larının güvenli saklanması ve rotasyonu
   - Google Ads API'nin resmi güvenlik/kullanım politikaları (developer token kısıtları, rate limit'ler)
   - Çok kullanıcılı (multi-tenant) sistemlerde kullanıcı verisi/kimlik izolasyonu
   - MCP (Model Context Protocol) sunucuları için güvenlik en iyi uygulamaları — yetkilendirme,
     girdi doğrulama, prompt injection riskleri
   - Secrets/API key yönetimi (asla kod içine gömme, secrets manager, `.gitignore`)
   - Loglama ve denetim izi (audit trail) standartları
   - Google'ın hassas kapsamlar (sensitive/restricted scopes) için OAuth uygulama doğrulama
     (app verification) süreci — herkese açık, çok kullanıcılı bir uygulama olduğumuz için bu
     zorunlu olabilir
   - Her son kullanıcının token'ının diğer kullanıcılardan tamamen izole, şifrelenmiş şekilde
     saklanması (artık tek bir şirketin değil, bilinmeyen sayıda dış kullanıcının kimlik
     bilgilerini tutuyoruz — risk yüzeyi çok daha büyük)
3. Bulduğun her kuralı **kaynak linkiyle birlikte** `docs/SECURITY.md`'ye yaz.
4. Yazacağın her backend kodunun bu dosyadaki kurallara uyduğunu doğrula; uymuyorsa önce
   dosyayı güncelle, sonra kodu yaz.

## Dokümantasyon kapıları — ZORUNLU

Her işten önce `docs/DOCUMENTATION.md` içindeki matrise göre ilgili belgeleri oku. Bu projenin
kapsam pivotu nedeniyle `docs/DOCUMENTATION.md`'nin tablosuna şu satırlar eklenmeli (henüz
eklenmediyse önce bunu yap):

- Google Ads erişim seviyesi & RMF uyumu → `docs/GOOGLE_API_ACCESS.md`
- Anthropic Connector Directory'ye yayın → `docs/CONNECTOR_SUBMISSION.md`
- Hukuki (gizlilik politikası, kullanım şartları) → `docs/LEGAL.md`

- UI, frontend veya ekran tasarımından önce `docs/PRODUCT.md` + `docs/DESIGN.md` okunur.
- Veritabanı/migration öncesi `docs/DATA_MODEL.md` + `docs/SECURITY.md` okunur.
- Google Ads entegrasyonu öncesi `docs/API_CONTRACTS.md` + `docs/SECURITY.md` +
  `docs/GOOGLE_API_ACCESS.md` okunur.
- MCP tool değişikliğinden önce `docs/MCP.md` + `docs/SECURITY.md` +
  `docs/CONNECTOR_SUBMISSION.md` okunur.
- Test/CI değişikliğinden önce `docs/TESTING.md`; deploy/izleme için `docs/OPERATIONS.md` okunur.
- Clone/fetch/pull/commit/push/PR öncesi `docs/REPOSITORY.md` okunur ve remote doğrulanır.
- Kullanıcı verisi işlenmeden önce `docs/LEGAL.md` + `PRIVACY_POLICY.md` okunur.
- Büyük, geri döndürmesi zor veya modüller arası karar `docs/decisions/` altında ADR olmadan uygulanmaz.
- Kod davranışı değişirse aynı değişiklikte ilgili belge ve kabul kriterleri de güncellenir.

### Asgari kurallar (araştırma sonucu ne olursa olsun en baştan geçerli)
- Hiçbir secret / API key / token kod içine veya repoya commit edilmez.
- Her yazma işlemi (create/update/pause/bütçe değişikliği) loglanır: kim, ne zaman, hangi
  hesap, ne değişti, kim onayladı.
- İnsan onayı olmadan hiçbir değişiklik canlı hesaba yazılmaz.
- Yeni oluşturulan kampanyalar varsayılan olarak **duraklatılmış** başlar.
- Bir kullanıcının kimlik bilgisi/token'ı başka bir kullanıcının hesabına erişim için asla
  kullanılamaz — hesaplar arası tam izolasyon.
- Üretim secrets'ı `.env` dosyasında tutulmaz; bir secrets manager kullanılır.
- Ürün tamamen ücretsizdir — ödeme/faturalama altyapısı oluşturulmaz. `docs/LEGAL.md` içinde
  "ücretsiz, ödeme bilgisi toplanmaz" açıkça belirtilir.

## Yeni bir tasarım alanı ortaya çıkarsa
`docs/DOCUMENTATION.md`'deki tabloda karşılığı olmayan yeni bir tasarım kararı gerekiyorsa
(ör. önbellekleme, bildirim sistemi), aynı desenle yeni bir `docs/<ALAN>.md` dosyası açılır
(Durum/Son gözden geçirme/Sonraki gözden geçirme başlığı, kaynak linkli araştırma, Karar,
Açık sorular, Değişiklik geçmişi bölümleriyle — bkz. mevcut belgelerin formatı) ve
`docs/DOCUMENTATION.md`'nin tablosuna eklenir. İlgili belge yoksa veya "Karar" bölümü
boş/`Taslak` ise o alanla ilgili implementasyon koduna başlanmaz.

## Kod stili
- Her fonksiyon/modül tek sorumluluk taşır; dosyalar ~300 satırı geçerse bölünür.
- Her public fonksiyon/tool tanımı docstring içerir.
- Testsiz kod merge edilmez.

## Test
- Her yeni Google Ads API çağrısı için mock'lanmış bir test yazılır.
- Onay akışı için "onaylanmadan yazma olmaz" senaryosunu doğrulayan özel bir test bulunur.

## Commit / PR kuralları
- Her commit tek bir mantıksal değişiklik içerir.
- Güvenlikle ilgili her değişiklik commit mesajında `[security]` etiketiyle işaretlenir.

## Ajan davranış kuralları
- Belirsiz bir gereksinimle karşılaşırsan mantıklı bir varsayım yap, bunu PR/commit
  açıklamasında belirt, sonra devam et.
- Mimariyi değiştiren her karar `docs/ARCHITECTURE.md`'ye yansıtılır.
- Gerçek müşteri API kimlik bilgileriyle asla test yapılmaz; sahte/mock veriyle çalışılır.
- Büyük bir değişiklik yapmadan önce kısa bir plan paylaş.
