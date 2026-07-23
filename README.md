# Ad Ops Agent — başlangıç iskeleti

Herkese açık, Anthropic'in Claude Connectors Directory'sinde yayınlanacak, **tamamen ücretsiz**
bir Google Ads connector'ü. Her kullanıcı kendi Google Ads hesabını kendi OAuth izniyle bağlar;
Claude performansı analiz eder, öneriler kullanıcı onayından geçtikten sonra Google Ads'e
yazılır. Ödeme/abonelik altyapısı yoktur ve eklenmeyecektir.

## İlk adımlar
1. `AGENTS.md` dosyasını okuyun — tüm proje kuralları burada.
2. `CLAUDE.md` dosyasına bakın — Claude Code'a özel notlar.
3. `STARTER_PROMPT.md` içindeki metni VS Code'da Claude Code / Codex'e ilk mesaj olarak verin.
4. `docs/DOCUMENTATION.md` içindeki iş→belge matrisini izleyin.
5. Kod yazmadan önce sırasıyla `docs/SECURITY.md`, `docs/GOOGLE_API_ACCESS.md` (Basic/Standard
   Access, RMF uyumu) ve `docs/CONNECTOR_SUBMISSION.md` (Anthropic Connectors Directory
   başvuru gereksinimleri) okunmalı; `Taslak` veya bloklayıcı açık karara bağlı alanda kod yazılmamalı.
6. Güvenlik, tasarım, veri, API, MCP, test, operasyon ve hukuki belgeler kod için bağlayıcıdır;
   davranış değiştiğinde ilgili belge de aynı değişiklikte güncellenir.

## Yerel kurulum ve çalıştırma

Tüm komutlar **repo kök dizininden** (`AddObserver/`) çalıştırılmalıdır; `.env` dosyası ve
SQLite yolu (`backend/.data/local.db`) çalışma dizinine göre çözülür.

1. Python 3.11+ ile bir sanal ortam oluşturup etkinleştirin:
   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1        # POSIX: source .venv/bin/activate
   ```
2. Kilitli backend bağımlılıklarını kurun (`backend/uv.lock`):
   ```powershell
   uv sync --directory backend --frozen
   ```
3. `.env.example` dosyasını `.env` olarak kopyalayın ve `LOCAL_VAULT_KEY` için gerçek bir Fernet
   anahtarı üretin:
   ```powershell
   Copy-Item .env.example .env        # POSIX: cp .env.example .env
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
   Çıktıyı `.env` içindeki `LOCAL_VAULT_KEY=` satırına yapıştırın. Bu anahtar yalnız yerel
   `LocalEncryptedVault`'u (dev) şifreler; gerçek bir credential veya secret DEĞİLDİR ama yine de
   commit edilmemelidir (`.env` `.gitignore`'dadır).
4. Gerçek bir Google Ads hesabı bağlamadan uygulamayı ayağa kaldırmak (credential gerektirmeyen
   local smoke yolu) için `GOOGLE_ADS_CLIENT_ID`/`GOOGLE_ADS_CLIENT_SECRET`/
   `GOOGLE_ADS_DEVELOPER_TOKEN` alanlarına herhangi bir placeholder metin yazmak yeterlidir —
   uygulama başlangıcında bu değerler doğrulanmaz, yalnız gerçek Google OAuth/Ads çağrısı
   yapıldığında devreye girer. Gerçek Google Ads erişimi için `docs/AUTH.md` ve
   `docs/GOOGLE_API_ACCESS.md`'deki koşullar geçerlidir.
5. Uygulamayı başlatın:
   ```powershell
   python -m uvicorn backend.src.app:create_app --factory
   ```
   `--factory` bilinçlidir: düz bir `app = create_app()` içe aktarma anında gerçek bir sqlite
   bağlantısı/vault/Google OAuth istemcisi kurar (bkz. `backend/src/app.py` docstring'i).
6. Smoke kontrolü — ayrı bir terminalde:
   ```powershell
   curl http://localhost:8000/healthz
   curl http://localhost:8000/readyz
   ```
   İkisi de `{"status":"ok"}` döner; `backend/.data/local.db` otomatik oluşturulur (gitignore'da).

### Windows'ta Türkçe karakter çıktısı

`tools/check_docs.py` ve `unittest -v` çıktısı, Windows konsolunun varsayılan kod sayfası (ör.
`cp1254`) yüzünden Türkçe karakterleri bozuk (`Dok?mantasyon` gibi) gösterebilir. Bu yalnız
konsol görüntüleme sorunudur, dosya içeriği veya test sonucu etkilenmez; düzeltmek için
komuttan önce UTF-8 modunu zorlayın:

```powershell
$env:PYTHONUTF8 = "1"
```

## Mevcut doğrulama komutları

Kalite araçları (formatter/linter, type checker, test runner, SAST, secret/dependency taraması)
`docs/decisions/0003-dev-tooling.md`'de karara bağlandı; `backend[dev]` grubuyla kurulur:

```powershell
uv sync --directory backend --frozen --extra dev
```

```powershell
python tools/check_docs.py
python -m unittest discover -s backend/tests -v      # veya: pytest backend/tests --cov=src --cov-fail-under=80
ruff format --check backend
ruff check backend
pyright backend/src
Push-Location backend; python -m alembic -c alembic.ini upgrade head --sql; Pop-Location
bandit -c backend/pyproject.toml -r backend/src
pip-audit
```

Production PostgreSQL runtime helper'ları `DATABASE_URL` ister; yerel varsayılan akış hâlâ
SQLite prototiptir. `DATABASE_URL` parola içerebileceği için log/çıktı/dokümana gerçek değer
yazılmamalıdır.

Canlı PostgreSQL RLS entegrasyon testi yalnız disposable bir test veritabanı DSN'i açıkça verilirse
çalışır; aksi halde skip eder:

```powershell
$env:ADDOBSERVER_POSTGRES_TEST_DSN = "postgresql+psycopg://user:pass@localhost:5432/addobserver_test"
python -m unittest backend.tests.test_postgres_rls_integration -v
```

```powershell
# detect-secrets-hook denetimi baseline'ı değiştirmez, yalnız verilen dosyaları kontrol eder
$files = @(git ls-files) + @(git ls-files --others --exclude-standard) | Get-Unique
detect-secrets-hook --baseline .secrets.baseline @files
```

pytest, mevcut `unittest.TestCase` testlerini değiştirmeden aynen çalıştırır (yalnız daha zengin
çıktı ve coverage ekler); `unittest discover` hâlâ bağımsız bir doğrulama yoludur. `.secrets.baseline`
Windows'ta yeniden üretilirse (`detect-secrets scan --all-files > .secrets.baseline`) dosyadaki yol
ayırıcıları `\` yerine `/` olacak şekilde normalize edilmelidir (bkz. `docs/TESTING.md`) — aksi halde
Linux CI'da her satır "yeni secret" gibi görünür. Alembic komutu canlı DB'ye bağlanmadan PostgreSQL
DDL çıktısı üretir; gerçek migration çalıştırma production/staging runbook ve secret/config gerektirir.

Mimari, ürün, auth, veri modeli, API/MCP sözleşmesi ve tasarım kararları ürün sahibi tarafından
onaylanıp `Kabul edildi` durumuna geçirildi; `backend/` iskeleti bu kararlar üzerine küçük, test
edilebilir adımlarla inşa ediliyor (bkz. `docs/decisions/0001-backend-stack.md`). `LEGAL.md` ve
`GOOGLE_API_ACCESS.md` hukukçu incelemesi ve Google Compliance/RMF sınıflandırması gelene kadar
`Taslak` kalır; bunlara bağımlı alanlarda (ödeme dışı gerçek public lansman, management/write
tool'ları) implementasyon yapılmaz.

## Klasör yapısı
```
AGENTS.md              — tüm ajanlar için ana talimat dosyası
CLAUDE.md               — Claude Code'a özel kısa notlar
STARTER_PROMPT.md        — ajana verilecek ilk mesaj
PRIVACY_POLICY.md, TERMS.md — hukukçu/işletmeci bilgisi bekleyen public politika taslakları
docs/
  DOCUMENTATION.md         — işe göre zorunlu okuma ve güncelleme matrisi
  ARCHITECTURE.md          — sistem sınırları ve uçtan uca akış
  SECURITY.md              — güvenlik standardı ve kaynaklı kontrol kapıları
  GOOGLE_API_ACCESS.md      — Google Ads erişim seviyesi, RMF ve OAuth verification
  CONNECTOR_SUBMISSION.md    — Anthropic Connectors Directory başvuru hazırlığı
  LEGAL.md                    — gizlilik, veri yaşam döngüsü ve kullanım şartları kararları
  PRODUCT.md               — kapsam, roller, akışlar ve kabul kriterleri
  DESIGN.md                — UI/UX sistemi ve WCAG 2.2 AA
  DATA_MODEL.md            — varlıklar, izolasyon ve retention
  DATABASE.md              — PostgreSQL, RLS, transaction ve migration kararları
  AUTH.md                  — uygulama oturumu ve Google OAuth yaşam döngüsü
  API_DESIGN.md            — HTTP/MCP yüzeyinin tasarım standardı
  API_CONTRACTS.md         — HTTP ve Google Ads sözleşmeleri
  MCP.md                   — MCP tool ve model güvenliği
  ERROR_HANDLING.md        — hata taksonomisi, retry ve belirsiz mutate
  RATE_LIMITS.md           — kota bütçesi, fair queue ve throttling
  OBSERVABILITY.md         — log, metric, trace ve append-only audit
  TESTING.md               — test stratejisi ve kalite kapıları
  DEPLOYMENT.md            — CI/CD, immutable image ve ortam izolasyonu
  OPERATIONS.md            — deploy, izleme ve olay runbook'ları
  REPOSITORY.md            — GitHub remote ve Git çalışma düzeni
  decisions/               — mimari karar kayıtları (ADR)
backend/
  src/
    api/                    — Google Ads API istemcisi (customer_id parametreli, çoklu kullanıcı)
    mcp/                     — Claude'a bağlanan uzak (remote) MCP sunucusu, Streamable HTTP
    auth/                     — Her kullanıcı için OAuth 2.1 + PKCE akışı, token yönetimi
    approval/                  — insan onay iş akışı
    db/                         — kullanıcı/hesap eşlemesi, denetim kayıtları
  tests/
.env.example
.gitignore
```

## Belge önceliği

Bir işe başlamadan önce `docs/DOCUMENTATION.md` okunur. Örneğin bir onay ekranı tasarlanacaksa
`PRODUCT.md`, `DESIGN.md` ve `SECURITY.md`; yeni MCP tool'u yazılacaksa `MCP.md`,
`API_CONTRACTS.md`, `SECURITY.md` ve `CONNECTOR_SUBMISSION.md`; Google Ads erişimiyle ilgili
bir karar için `GOOGLE_API_ACCESS.md` uygulanır. Belge ile kod çelişirse önce karar netleştirilir
ve belge güncellenir.

> **Not:** Proje dışa kapalı ajans aracından public connector'a pivot etti. Temel mimari, ürün, auth,
> güvenlik ve kullanıcı izolasyonu belgeleri yeni modele çevrildi ve 2026-07-17'de ürün sahibi
> tarafından `Kabul edildi`. Her belgenin `Durum` alanı uygulanabilirlik kapısı olmaya devam eder.
> Hukuki metinler (`LEGAL.md`) ve Google Compliance/RMF sınıflandırması (`GOOGLE_API_ACCESS.md`)
> halen bloklayıcı, dışa bağımlı açık kararlardır.
