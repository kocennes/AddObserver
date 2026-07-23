# AddObserver — uçtan uca proje backlog'u

Bu dosya AddObserver'ı mevcut prototipten güvenli, herkese açık, ücretsiz ve Anthropic Connector
Directory'de yayınlanmış bir Google Ads connector'üne götüren ana yürütme listesidir. Her checkbox
altındaki metin bağımsız bir Codex/Claude promptu olarak kullanılabilir.

## Kalan kapsam notu — 2026-07-19

- Genel ürün amacı baz alınarak proje yaklaşık `%35` tamamlanmış, kalan kapsam yaklaşık `%65` kabul
  edilmiştir. Bu oran checkbox sayısından türetilmiş kesin bir ilerleme metriği değildir; production
  veri katmanı, Google Ads reporting/write kapsamı, ürün yüzeyleri, operasyon, altyapı, hukuk/politika,
  dış doğrulamalar, Directory submission ve launch ağırlıkları birlikte değerlendirilmiştir.
- Aşağıdaki açık maddeler kalan `%65` için yürütme listesidir. Yeni bir iş yalnız kabul edilmiş bir
  belge gereksiniminden, doğrulanmış dış koşuldan veya uygulama sırasında bulunan somut bir eksikten
  doğarsa eklenir; varsayımsal özellik eklenmez.
- `LEGAL.md` ve `GOOGLE_API_ACCESS.md` hâlen `Taslak` olduğu için bunlara bağlı production işleri
  özellikle `BLOKE`/`... SONRASI` olarak açık bırakılmıştır. Hosting, işletmeci bilgisi, hukukçu,
  Google Compliance, Google OAuth verification ve Anthropic reviewer kararları uydurulmaz.

## Görev takip kuralı

- Göreve başlanmadıysa veya görev kısmen tamamlandıysa `- [ ]` olarak bırak.
- Görev bütün kabul kriterleriyle tamamlanıp doğrulandıysa aynı satırı `- [x]` olarak değiştir.
- `BLOKE`, dış karara bağlı veya yalnız taslağı hazırlanmış görevlere `x` koyma.
- Kod tamamlanmış olsa bile zorunlu test, belge, migration, güvenlik kontrolü veya dış kanıt eksikse
  görevi tamamlanmış sayma.
- Bir görev tamamlandığında mümkünse başlığın altına kısa bir `Tamamlanma kanıtı:` satırı ekle;
  test komutunu, ilgili commit/PR numarasını veya dış onay belgesini burada belirt.
- Her çalışma turunun sonunda bu dosyayı gözden geçir; o turda gerçekten tamamlanan görevlerin
  checkbox'larını `[x]` yap ve yeni ortaya çıkan zorunlu işleri uygun faza `[ ]` olarak ekle.

## Her görev için değişmez çalışma promptu

> Önce `AGENTS.md`, `docs/DOCUMENTATION.md` ve görevin belge matrisinde zorunlu kıldığı tüm belgeleri
> eksiksiz oku. Belgenin `Durum` alanı `Taslak` ise o karara bağlı production kodunu yazma; blokajı
> ve gerekli kararı raporla. Güncel/değişebilir teknik veya politika bilgilerini yalnız resmi birincil
> kaynaklardan doğrula. Mevcut kullanıcı değişikliklerini koru. Secret, gerçek token, gerçek müşteri
> verisi veya canlı Google Ads hesabı kullanma. Davranış değişikliğinde kod, test, sözleşme ve kabul
> kriterlerini aynı değişiklikte güncelle. Her public fonksiyona docstring ekle. Dosyaları yaklaşık
> 300 satır altında tut. Sonunda `python tools/check_docs.py`,
> `python -m unittest discover -s backend/tests -v` ve `git diff --check` çalıştır. Kullanıcı açıkça
> istemedikçe commit, push, PR, deploy, submission veya dış sisteme yazma işlemi yapma. Bir görev bütün
> kabul kriterleriyle bitip doğrulandıktan sonra durma; listedeki bir sonraki uygulanabilir, bloke olmayan
> göreve geç. Dış karar, kullanıcı onayı veya erişim bekleyen görevleri atlayıp sıradaki güvenli göreve devam et.

## Tamamlanma tanımı

Bir madde ancak aşağıdakilerin tamamı sağlandığında işaretlenir:

- Kabul edilmiş gereksinim ve tehditler test edilebilir kriterlere çevrildi.
- Kod, negatif testler, entegrasyon/contract testleri ve ilgili belgeler birlikte güncellendi.
- Principal/customer izolasyonu ve insan onayı sınırları korunuyor.
- Secret veya hassas veri log, hata, fixture, prompt ya da response içine sızmıyor.
- Dokümantasyon kapısı, test paketi ve diff kontrolü başarılı.
- Gerekli dış kanıt/inceleme gerçekten tamamlandı; yalnız “hazır” denmesi yeterli değil.

---

# Faz 0 — mevcut branch'i sağlamlaştır ve ana tabanı kur

---

# Faz 1 — ürün ve mimari kararlarını kapat

---

# Faz 2 — güvenlik temeli ve tehdit modeli

---

# Faz 3 — connector OAuth ve Google OAuth'u production seviyesine getir

---

# Faz 4 — veri katmanını production mimarisine taşı

- [ ] **4.3 PostgreSQL RLS izolasyonunu uygula**

  Prompt: Principal-scoped tablolarda `ENABLE` + `FORCE ROW LEVEL SECURITY`, `USING` ve `WITH CHECK`
  politikalarını uygula. Her transaction başında güvenli principal context set et; pool reuse sırasında
  context sızıntısını engelle. Uygulama filtrelerini koru. Cross-principal SELECT/INSERT/UPDATE/DELETE,
  pool reuse ve privileged-role negatif entegrasyon testleri yaz.

  Kısmi ilerleme: `backend/alembic/versions/20260718_0002_enable_principal_rls.py`, ADR-0006'daki
  principal-scoped tablolar için `ENABLE ROW LEVEL SECURITY`, `FORCE ROW LEVEL SECURITY` ve aynı
  `app.current_principal_id` transaction-local setting'ine bağlı `USING`/`WITH CHECK` policy'lerini
  ekledi. `backend/src/db/postgres_context.py`, principal UUID'sini doğrulayıp `set_config(..., true)`
  ile transaction-local context atayan/temizleyen helper'ları ekledi. `backend/tests/test_sqlalchemy_schema.py`
  RLS revision contract'ını ve helper davranışını kapsayacak şekilde genişledi.
  `backend/src/db/postgres.py`, production `DATABASE_URL` değerinin PostgreSQL dialect'i olmasını zorlayan
  engine factory'yi ve her transaction'da RLS principal context'ini set/cleanup eden
  `principal_transaction` helper'ını ekledi; `backend/tests/test_postgres_runtime.py` bu helper'ın
  DSN redaction, PostgreSQL-only URL, commit ve rollback sırasını canlı DB gerektirmeden kanıtlıyor.
  `backend/src/db/postgres_repository.py`, ilk production repository dilimi olarak `principal`,
  `oauth_client_grant`, `ads_account`, `oauth_credential`, `proposal`, `approval`, `execution` ve `audit_event`
  adaptörlerini SQLAlchemy Core'a taşıdı; `backend/tests/test_postgres_repository.py` idempotent
  principal/account linking, client consent scoping/re-consent, cross-principal read yokluğu,
  active/history ayrımı, relink reactivation, credential active/revoke ownership, proposal
  payload/hash guard'ları, pending proposal pagination/filtering, approval/audit principal scoping,
  execution idempotency/snapshot/result ownership ve repository'nin kendi transaction'ını commit etmediğini
  doğruluyor. Bu artış ayrıca production
  `approval.decision` constraint'ini domain enum değerleriyle (`approve`/`reject`) hizaladı ve
  `backend/tests/test_sqlalchemy_schema.py` içine regression ekledi.
  `backend/tests/test_postgres_rls_integration.py`, `ADDOBSERVER_POSTGRES_TEST_DSN` verildiğinde
  disposable PostgreSQL schema'sı üzerinde cross-principal SELECT/INSERT/UPDATE/DELETE, pool reuse ve
  `BYPASSRLS`/superuser test rolü negatif vakalarını çalıştırır. Madde hâlâ açık: bu ortamda canlı
  PostgreSQL DSN'i olmadığı için entegrasyon testi skip etti; auth/token repository'leri ve ASGI composition
  root henüz production SQLAlchemy helper'a bağlanmadı. Production şema denetiminde domain'in URL-safe
  opaque değer ürettiği connector refresh-token `family_id` alanının yanlışlıkla UUID tanımlandığı bulundu;
  metadata ve başlangıç migration'ı `TEXT` ile hizalandı ve şema regresyon testi eklendi.
  Connector OAuth geçişinin ilk parçasında `PostgresAuthorizationTransactionRepository` ve
  `PostgresAuthorizationCodeRepository` eklendi: repository'ler commit etmez, authorization code'u
  yalnız SHA-256 hash olarak saklar ve `consumed_at IS NULL` koşullu update ile tek kullanımlı claim'i
  atomik uygular. Aynı denetimde `authorization_transaction.id` ve child FK'sinin domain'in opaque
  kimliğiyle çelişen UUID tipi `TEXT` yapıldı; DB status constraint'i domain enum'uyla
  `pending/consented/completed` olarak hizalandı. Regresyon testleri opaque ID round-trip, durum geçişi,
  hash-only saklama, replay tespiti ve bilinmeyen kodun fail-closed davranışını kapsıyor.
  `PostgresTokenRepository` de access/refresh token'ları yalnız hash olarak saklayacak, koşullu
  `status = active` update ile atomik rotate edecek, replay'de tüm family'yi ve disconnect'te yalnız hedef
  principal'ın tüm access/refresh token'larını revoke edecek şekilde eklendi. SQLite'ın timezone bilgisini
  düşüren test dönüşleri repository sınırında UTC-aware normalize edildi.
  `PostgresWebLoginStateRepository` ve `PostgresWebSessionRepository` login state/session/CSRF değerlerini
  yalnız hash saklama, atomik state claim, unknown-safe lookup, tekil revoke ve principal-wide revoke
  davranışlarıyla eklendi. Wiring denetiminde `/token`'ın code hash'inden principal'ı öğrenmeden RLS context
  kuramayacağı, `authorization_code` policy'sinin ise context olmadan satırı göstermediği bootstrap problemi
  bulundu. `20260719_0003_authorization_code_bootstrap_rls` yalnız SELECT için, transaction-local SHA-256
  code hash'iyle tam eşleşen tek satırı görünür yapan policy ekledi; principal çözüldüğünde hash context
  temizlenip normal principal context kuruluyor. `authorization_code_transaction` bu sırayı atomik ve
  fail-closed uygular; `BYPASSRLS`, tablo sahipliği veya `SECURITY DEFINER` verilmez. Migration zinciri,
  exact-hash policy sözleşmesi, context cleanup, unknown-code rollback ve opsiyonel canlı PostgreSQL bootstrap
  testi eklendi. ASGI taramasında auth/API/MCP yollarında yaklaşık 30 doğrudan SQLite repository kurulumu
  bulundu; kısmi geçiş RLS transaction sınırını parçalayacağından tek tek production'a açılmadı. Bunun yerine
  `APP_ENVIRONMENT=production|prod`, tüm yollar PostgreSQL request transaction provider'a taşınana kadar
  SQLite fallback'e düşmeden fail-closed başlangıç hatası verir; DSN/secret hata metnine girmez. Tam ASGI
  wiring için `PostgresUnitOfWorkFactory`/`PostgresRequestUnitOfWork` temeli eklendi: bir isteğin tüm
  repository'leri aynı connection/transaction'ı paylaşır, principal sonradan bağlanabilir veya exact-hash
  authorization-code bootstrap ile türetilebilir, başarı tek commit/context cleanup ve hata rollback üretir.
  Lifecycle/connection-sharing/fail-closed testleri eklendi. Route/MCP çağrı noktalarının provider'a taşınması
  sırasında refresh-token grant ve bearer access-token doğrulamasının da principal'ı ancak token satırından
  öğrenebildiği ikinci bootstrap boşluğu bulundu. `20260719_0004_token_bootstrap_rls`, access/refresh tablolarına
  yalnız exact SHA-256 token hash'i için SELECT policy ekledi; unit-of-work ayrı access/refresh bootstrap
  metotlarıyla principal context kurup hash context'i temizler. Migration ve opsiyonel canlı PostgreSQL testleri
  genişletildi. İlk gerçek ASGI dilimi olarak `/token`, production factory verildiğinde code bootstrap/claim/
  token insert ile refresh bootstrap/rotation'ı tek unit-of-work içinde çalıştıracak şekilde bağlandı; iki grant
  için route contract testleri ve mevcut SQLite uçtan uca OAuth regresyonları geçti. Kalan auth/API/MCP çağrı
  noktalarının provider'a taşınması ve canlı PostgreSQL kanıtı tamamlanana kadar madde açıktır.
  `create_app`, unit-of-work factory'sini composition root'tan `AuthContext` içine taşıyacak biçimde
  genişletildi; gerçek ASGI `/token` isteğinin authorization-code bootstrap, atomik claim ve token
  insert'lerini aynı injected work üzerinden yürüttüğü route testiyle doğrulandı.
  Bearer-korumalı `GET /api/v1/accounts`, `GET /api/v1/proposals` ve
  `GET /api/v1/proposals/{proposal_id}` yolları da access-token exact-hash bootstrap, token doğrulama ve
  principal-scoped sorguyu aynı request unit-of-work içinde çalıştıracak şekilde taşındı; gerçek ASGI
  accounts testi transaction sınırını doğruluyor. Approval/browser ve MCP yolları henüz taşınmadığı için
  production kapısı ve madde açık kalır.
  MCP bearer middleware'i de PostgreSQL access-token bootstrap/doğrulama yoluna bağlandı. Bu kısa auth
  transaction'ı downstream tool çalışmadan önce kapanır; böylece ADR-0006'nın Google Ads ağ çağrısını açık
  DB transaction içinde çalıştırmama kuralı korunur. MCP tool repository'lerinin ağ çağrısından ayrılmış
  transaction'lara taşınması ve browser/approval yolları hâlâ açıktır.
  Browser approval session'ı için `20260719_0005_web_session_bootstrap_rls` eklendi: yalnız exact SHA-256
  cookie hash'ine uyan `web_session` satırı SELECT ile görünür, ardından principal context kurulur.
  `/approvals`, proposal decision ve `/logout` session bootstrap ile DB işlemlerini aynı kısa unit-of-work
  içinde yürütür. Canlı PostgreSQL fixture'ında policy'lerin tablolar yaratılmadan önce kurulması hatası da
  düzeltildi. Google/secrets-manager etkileşimli login callback ve disconnect akışları ayrıştırılmadan
  taşınmayacağı için madde açık kalır.
  Login-only `/login` state creation PostgreSQL repository'ye taşındı. Callback state claim transaction'ı
  Google code exchange'inden önce kapanır; doğrulanmış Google subject sonrasında ikinci kısa transaction
  principal'ı bulur, RLS context'i bağlar ve browser session'ı oluşturur. Test, dış Google exchange'in iki
  DB transaction arasında gerçekleştiğini kanıtlar. Disconnect ve MCP tool repository işlemleri açıktır.
  MCP'nin yalnız connector DB'sine dokunan `list_accessible_accounts`, `prepare_proposal`, `get_proposal`
  ve `list_proposals` yolları principal-bound kısa PostgreSQL transaction'lara taşındı. Google reporting
  credential metadata çözümlemesi de kısa principal transaction'ına taşındı; transaction kapandıktan sonra
  vault okunur ve Google Ads çağrılır. AUTH-class provider hatasında credential pasifleştirme ayrı kısa
  transaction'da yapılır. Böylece reporting boyunca açık DB transaction tutulmaz.

  `/authorize` transaction oluşturma ile `/authorize/consent` transaction okuma/durum ilerletme yolları da
  kısa PostgreSQL unit-of-work transaction'larına taşındı; lifecycle ve hata rollback contract testleri
  eklendi. Vault yazan connector Google callback ve durable revoke gerektiren disconnect, 4.4'teki kalıcı
  state/outbox kararı tamamlanmadan production PostgreSQL yoluna açılmaz.
  Authorization consent okuma+durum ilerletme tek unit-of-work içine alındı; PostgreSQL repository
  `pending → consented → completed` geçişlerini predecessor koşullu compare-and-set ile uygular. Stale
  contract testi ve iki-connection canlı PostgreSQL consent yarışı testi eklendi; canlı kanıt DSN olmadığı
  için skip kalır.

  `auth/server.py::google_callback`'in Claude-client dalı (Google refresh token'ını vault'a yazan ve
  `oauth_credential`/`oauth_client_grant`/`authorization_code`'a işleyen dal) production unit-of-work'e
  taşındı -- bu, önceki bir denetimde bulunan, hâlâ dual-path olmayan tek kalan SQLite-only yazma yoluydu
  (auth/API/MCP altındaki diğer tüm çağrı noktaları, `context.postgres_uow_factory is None` kontrolüyle
  zaten dual-path'ti; bu denetim `context.conn` kullanan her satır tek tek grep'lenerek doğrulandı).
  `authorization_transaction` tablosu RLS'siz olduğundan (`20260718_0002_enable_principal_rls`'nin
  `RLS_TABLES` listesinde yok, çünkü onay öncesi hiçbir principal'a henüz bağlı değil) ilk okuma principal
  bağlamadan yapılabiliyor; ADR-0006'nın "açık DB transaction içinde ağ çağrısı yok" kuralı gereği Google
  code exchange ve vault yazımı hiçbir transaction içinde çalışmaz. Yeni `_postgres_google_callback`
  fonksiyonu üç ayrı kısa transaction kullanıyor: (1) `authorization_transaction` oku (yoksa `/approvals`
  login fallback'ine düşer, davranış değişmedi), (2) principal `get_or_create`, sonra transaction kapanır
  ve vault'a yazılır, (3) `bind_principal` sonrası credential upsert + client grant + authorization code
  + transaction'ı `completed`'a taşıma tek transaction'da. Yeni
  `backend/tests/test_postgres_google_callback_route.py` (7 test): başarı yolunun tam olarak bu sırayı
  izlediğini (transaction-read work google exchange'den önce kapanır, principal-create work vault
  yazımından önce kapanır), kısmi-scope reddinde (`adwords` eksik) ikinci/üçüncü work'ün hiç istenmediğini
  ve vault'a dokunulmadığını, bilinmeyen `state`'in login fallback'ine düştüğünü ve fallback'in kendi
  login-state-claim work'ünü kullandığını, tamamlanmış (`COMPLETED`) bir transaction'ın yeniden
  kullanılmasının `issue_authorization_code`'da `AuthError` fırlatıp yazma work'ünü (zaten yapılmış
  credential/grant yazımları dahil) rollback ettiğini, ve tam ASGI akışının (`/authorize` →
  `/authorize/consent` → `/google/callback`) sahte, request'ler arasında durumu paylaşan bir PostgreSQL
  backend'i üzerinden uçtan uca çalışıp kimlik bilgisini/rızayı/kodu kalıcılaştırdığını kanıtlıyor.
  Doğrulama: `python -m unittest discover -s backend/tests` (482 test, OK; önceki 477'den +5),
  `pytest backend/tests --cov=src --cov-fail-under=80` (coverage %92.55), `pyright backend/src`
  (0 hata), `ruff check .`/`ruff format --check .` (temiz), `bandit -c backend/pyproject.toml -r
  backend/src` (0 bulgu), `python tools/check_docs.py` (21 belge doğrulandı), `git diff --check`
  (temiz). `docs/DATABASE.md` güncellendi. Canlı PostgreSQL RLS izolasyon/concurrency kanıtı
  (`test_postgres_rls_integration.py`) bu makinede `ADDOBSERVER_POSTGRES_TEST_DSN` olmadığı için hâlâ
  skip kalıyor -- madde bu yüzden hâlâ açık. Commit/push yapılmadı.

- [ ] **4.4 Repository transaction ve concurrency güvenliğini tamamla**

  Prompt: Authorization code claim, refresh rotation, proposal decision, execution reservation,
  idempotency ve audit atomicity yollarını concurrent transaction testleriyle doğrula. Lost update,
  double approval, duplicate execution ve cross-principal collision'ı DB constraint/locking ile
  fail-closed engelle. SQLite'a özel davranışı production varsayımı yapma.

  Ek zorunlu iş: disconnect için durable vault-revocation state/outbox kararı ve concurrency testi ekle.
  Mevcut sırada DB credential metadata revoke edildikten sonra vault silme başarısız olursa sonraki retry
  vault referansını bulamaz; vault-first yaklaşımı ise eşzamanlı relink sırasında yanlış credential'ı revoke
  edebilir. Kalıcı state-machine olmadan production PostgreSQL wiring yapılmaz.

  ADR-0007 kabul edildi ve `20260719_0006_credential_revocation_outbox` migration'ı eklendi.
  `credential_revocation_job`, principal/credential composite ownership, FORCE RLS, credential başına tek iş,
  attempt/next-attempt ve güvenli error-code alanlarını taşır; secret değeri DB'ye girmez.
  `PostgresCredentialRevocationRepository`, credential metadata revoke + outbox enqueue işlemini aynı
  transaction'da ve credential kimliği üzerinde idempotent uygular. Principal-scoped due-job claim,
  `FOR UPDATE SKIP LOCKED`, attempt artırımı ve `next_attempt_at` lease'iyle eşzamanlı worker'ları ayırır;
  retry yalnız kısa/güvenli error code kabul eder, completion idempotenttir. SQLite repository contract
  testleri ownership, duplicate enqueue, lease, retry ve completion davranışını doğrular. Gerçek
  `test_postgres_rls_integration.py` iki gerçek pooled connection ve thread barrier ile aynı revocation
  job'ını yarıştırır; yalnız tek claim kazananı, tek attempt artışı ve ileri taşınan lease'i doğrular.
  Bu makinede DSN yok ve Docker daemon çalışmıyor; dolayısıyla test hazır olsa da canlı kanıt skip kalır.
  `auth/revocation_worker.py`, claim transaction'ını
  vault çağrısından önce kapatır; başarı completion'ını veya provider metnini sızdırmayan
  `VAULT_UNAVAILABLE` retry sonucunu ikinci kısa transaction'da kalıcılaştırır. Başarı/hata/no-work
  contract testleri transaction sırasını kanıtlar. Retry/completion exact claimed-attempt generation'ını
  compare-and-set koşulu yapar; lease expiry sonrası yeniden claim edilen işi stale worker'ın ezemediği
  repository regresyonuyla doğrulanır. Scheduler/deployment tetikleyicisi Faz 10'a bağlı kalır.
  Production
  `POST /disconnect` artık exact session-hash bootstrap sonrası connector token, credential revoke+outbox,
  account, tüm web session ve audit yazılarını tek principal-bound unit-of-work içinde commit eder; route
  vault'a dokunmaz. ASGI contract testi atomik repository kapsamını, correlation/audit sonucunu ve vault
  çağrısı olmadığını doğrular.

  Kısmi ilerleme: approval state transition artık yalnız `pending_approval` satırını koşullu update eder;
  ikinci/eşzamanlı karar proposal+approval+audit yazamaz. Execution reservation, yarışa açık
  SELECT→INSERT yerine PostgreSQL/SQLite conflict-safe INSERT kullanır; aynı idempotency key eşzamanlı
  geldiğinde unique violation/500 yerine kazanan satırı okuyup payload eşitliğini doğrular. SQLite contract
  testleri geçti; gerçek iki-connection PostgreSQL yarış testleri DSN ile çalıştırılmak üzere hazırdır.
  `test_postgres_rls_integration.py` gerçek iki ayrı pooled connection ve thread barrier ile duplicate
  execution reservation yarışını çalıştıracak şekilde genişletildi; tek satır, tek `created=True` kazananı
  ve her iki çağrının aynı execution ID'ye çözülmesini doğrular. Bu makinede Docker CLI bulunmasına rağmen
  daemon çalışmıyor ve `ADDOBSERVER_POSTGRES_TEST_DSN` tanımlı değil; test bu nedenle henüz canlı kanıt
  üretmeden skip kalır. Aynı canlı suite authorization-code claim için tek tüketici, refresh rotation replay
  için tek rotation kazananı + tüm ailenin revoke edilmesi ve çift approval kararı için tek approval/audit
  yazılması yarışlarını da iki ayrı pooled connection ile doğrular.
  Refresh replay yolunda kritik rollback kusuru düzeltildi: repository aileyi revoke edip `AuthError`
  yükseltiyor, route hatayı unit-of-work dışına kaçırdığı için güvenlik revoke'u rollback oluyordu. Route
  artık beklenen replay hatasını transaction içinde yakalar, family revoke'u commit eder ve sonra güvenli
  OAuth `invalid_grant` cevabı döner; beklenmeyen hatalar rollback olmaya devam eder.

- [ ] **4.5 Veri retention ve purge altyapısını hazırla — HUKUK KARARINDAN SONRA**

  Prompt: Yalnız `LEGAL.md` kabul edilip kategori bazlı retention süreleri belirlendikten sonra
  analysis, performance cache, app log, session, revoked token metadata ve audit için lifecycle job'ları
  ekle. Audit'i yasal zorunluluk dışında silme. Purge, legal hold, backup expiry ve deletion request
  davranışını auditli ve idempotent test et.

- [ ] **4.6 Backup/restore ve schema rollback tatbikatını otomatikleştir — SAĞLAYICI SONRASI**

  Prompt: Hosting/DB sağlayıcısı seçildikten sonra şifreli backup, ayrı erişim alanı, restore doğrulaması,
  migration rollback ve veri bütünlüğü kontrollerini runbook + otomasyon olarak ekle. RPO/RTO kabul
  edilmeden sahte hedef yazma. En az bir belgelenmiş restore tatbikatı kanıtı üret.

---

# Faz 5 — Google Ads reporting connector'ünü tamamla

- [x] **5.6 Google Ads hata sınıflandırmasını tamamla**

  Prompt: Auth, permission, not found/stale, validation, quota/rate limit, transient transport,
  2SV ve unknown hataları resmi error type'larıyla test et. Google request ID'yi audit/telemetry'ye
  ekle fakat secret/payload sızdırma. Retryable olmayan hatayı tekrar deneme; server retry delay'i
  alt sınır olarak kullan.

  Tamamlanma kanıtı: `api/errors.py::classify_google_ads_exception`/`classify_transport_error` ve
  `api/retry.py::execute_with_retry` karar tablosunun altı sınıfını (validation, auth/permission,
  rate/quota, transient, sync/stale; internal invariant koddan ayrı) zaten uyguluyordu.
  `backend/tests/test_api_errors.py`'a üç eksik regresyon testi eklendi: `authorization_error`
  (permission) alanının `authentication_error`'dan ayrı fakat aynı AUTH sınıfına düştüğü,
  ERROR_HANDLING.md'nin özellikle andığı `TWO_STEP_VERIFICATION_NOT_ENROLLED` (2SV) değerinin
  değer-özel bir dal gerekmeden alan-adı eşlemesiyle AUTH'a düştüğü ve boş/`unknown` failure
  gövdesinin `errors[0]`'da çökmeden `TRANSIENT`/`empty_failure` olarak güvenli sınıflandığı
  gerçek Google Ads v24 proto hata tipleriyle kanıtlandı; sınıflandırıcı kodu değişmedi.
  Retryable olmayan sınıfların tekrar denenmediği (`test_non_retryable_class_raises_immediately_without_sleeping`)
  ve Google'ın `retry_delay`'inin backoff'a üst sınır değil alt sınır olarak uygulandığı
  (`test_googles_retry_delay_is_applied_as_a_floor_not_a_ceiling`) `test_api_retry.py`'de zaten
  kanıtlıydı. Kalan tek eksik -- Google `request_id`'nin gerçek bir audit/telemetry kaydına
  yazılması (`todo.md` 9.1 tamamlanana kadar bloke) -- 9.1'in yapılandırılmış JSON logging'i
  eklemesiyle kapatıldı: `observability/logging.py::JsonEventLogger.emit`'e yalnız güvenli
  karakter kümesiyle eşleşirse eklenen (aksi halde tamamen atlanan) bir `google_request_id`
  alanı eklendi; `mcp/tools.py::_log_google_ads_failure`, gerçek bir Google Ads `AdsApiError`
  yakalayan iki sınıra (`_fetch_report_page`, `sync_accessible_accounts`) bağlandı ve her olayı
  `operation`/`reason_code`/principal-customer pseudonymous referansı/`google_request_id` ile
  tek bir JSON olayı olarak yazar; `AdsApiError.message` (Google'ın serbest metni) bilinçli olarak
  şemaya eklenmedi. Bizim kendi ürettiğimiz (Google'a hiç ulaşmayan) `AdsApiError`'lar
  (`request_id=None`) bu logu tetiklemez. Yeni testler:
  `backend/tests/test_observability.py::test_google_request_id_is_carried_when_present_and_safe`/
  `test_google_request_id_is_omitted_when_absent_or_unsafe`,
  `backend/tests/test_mcp_integration.py::test_google_ads_failure_logs_the_google_request_id`
  (gerçek MCP tool-call zinciri + gerçek `GoogleAdsException` üzerinden uçtan uca). Doğrulama:
  `python -m unittest discover -s backend/tests` (553 test, OK), `pyright backend/src` (0 hata),
  `ruff check .`/`ruff format --check .` (temiz), `bandit -c backend/pyproject.toml -r backend/src`
  (0 bulgu), `python tools/check_docs.py` (27 belge doğrulandı), `git diff --check` (yalnız CRLF
  normalizasyon uyarıları). `docs/ERROR_HANDLING.md` ve `docs/OBSERVABILITY.md` "Güncelleme
  geçmişi" güncellendi. Commit/push yapılmadı.

- [ ] **5.7 Opt-in Google Ads contract test ortamını kur — TEST HESABI/DIŞ ERİŞİM SONRASI**

  Prompt: `TESTING.md` açık sorusunu kapat; yalnız ayrılmış ve gerçek müşteri verisi içermeyen bir Google
  Ads test hesabında çalışan opt-in contract test suite'i, gerekli environment/secret sözleşmesi, güvenli
  skip davranışı, çalışma sıklığı ve veri reset prosedürünü tanımla. Normal unit/CI koşusunda canlı çağrı
  yapma. Test hesabı ve developer token sağlanmadan sahte başarı kanıtı üretme. Dayanak:
  `TESTING.md`, `API_CONTRACTS.md`, `SECURITY.md`, `GOOGLE_API_ACCESS.md`.

---

# Faz 6 — MCP ve HTTP ürün yüzeylerini tamamla

---

# Faz 7 — insan onayı ve approval UI

- [ ] **7.3 Yüksek etkili işlem için ikinci onay tasarla — WRITE KAPSAMI SONRASI**

  Prompt: Account budget ve toplu disable/delete gibi yüksek etkili işlemlerin sınıflandırmasını,
  ikinci approver/step-up auth gereksinimini ve expiry/hash binding'ini tasarla. Ürün/Google access
  kararı kabul edilmeden implementasyon yapma. Kabul sonrası aynı kişinin iki onayıyla bypass,
  replay ve stale state negatif testlerini ekle.

  Durum kontrolü (2026-07-22): `docs/GOOGLE_API_ACCESS.md` hâlâ `Taslak` (Google Ads Compliance
  sınıflandırması bekleniyor) ve başlığın kendisi `WRITE KAPSAMI SONRASI` olarak işaretli --
  `todo.md` 1.1/8.1 write/execution kapısı açılmadan bu görev bilinçli olarak atlanmıştır,
  bloke değildir/çözülmemiştir. İmplementasyon yapılmadı.

---

# Faz 8 — Google Ads write/execution (tamamı BLOKE: Google Compliance kararı gerekli)

- [ ] **8.1 Google Compliance sınıflandırmasını al ve write kapısını aç**

  Prompt: Google Ads Compliance'a ürünün reporting + dar proposal + insan onaylı management profilini
  eksiksiz sun. Special-purpose/full-service sınıflandırmasını, uygulanabilir RMF listesini, Basic/Standard
  erişim beklentisini ve write izinlerini yazılı kanıtla. `GOOGLE_API_ACCESS.md` ancak gerçek yanıtla
  güncellenip kabul edilsin. Bu tamamlanmadan aşağıdaki 8.x production kodlarını yazma.

- [ ] **8.2 Execution domain state machine'ini tamamla — BLOKE**

  Prompt: Onaylı proposal → revalidation → reservation → audit start → provider mutate → completion
  audit akışını uygula. Approved hash, expiry, principal/customer, active credential/account ve current
  Google state tekrar doğrulansın. Stale/rejected/expired/unapproved proposal mutate üretmesin.

- [ ] **8.3 Campaign pause/enable adapter'ını uygula — BLOKE**

  Prompt: Resmi Google Ads client ve yalnız allowlist operation ile campaign status mutate adapter'ı
  ekle. Resource name'i trusted IDs'den üret. Validate-only desteğini ve request ID'yi kullan. Success,
  permission, stale, partial failure, timeout ve unknown-result mock testleri ekle.

- [ ] **8.4 Campaign budget update adapter'ını uygula — BLOKE**

  Prompt: Budget resource ownership, shared budget etkisi, currency/micros sınırı, min/max business
  validation ve fresh current value kontrolüyle dar budget update uygula. Float kullanma. Onay snapshot'ı
  değişmişse stale yap. High-impact threshold kararı varsa ikinci onayı zorunlu kıl.

- [ ] **8.5 Yeni campaign oluşturmayı uygula — AYRI ÜRÜN/RMF KARARIYLA BLOKE**

  Prompt: Yalnız ürün ve RMF kapsamı açıkça kabul ederse campaign create tasarla. Her yeni campaign
  zorunlu `PAUSED` doğsun. Budget, bidding, targeting, ad group/ad minimumları ve RMF UI gereksinimlerini
  eksiksiz karşılamadan endpoint/tool açma. Arbitrary mutate veya raw protobuf kabul etme.

- [ ] **8.6 Idempotency ve unknown mutate reconciliation uygula — BLOKE**

  Prompt: Idempotency key'i principal + proposal + payload hash ile bağla. Aynı key farklı scope/payload
  ile fail-closed reddedilsin. Provider timeout/exception sonucunda kör retry yapma; execution `unknown`
  olsun ve read-after-write/reconciliation job ile gerçek durum belirlensin. Tek provider mutate ve
  audit zincirini concurrent testlerle kanıtla.

- [ ] **8.7 Execution HTTP/MCP yüzeyini aç — BLOKE**

  Prompt: Backend execution güvenliği tamamlanınca, insan onayını bypass etmeyen en dar apply yüzeyini
  tasarla. Destructive annotation, confirmation UX, idempotency, audit ve safe error contract ekle.
  Claude'un tek başına proposal oluşturup onaylayıp uygulayabildiği bir zincir yaratma.

---

# Faz 9 — observability, audit ve operasyon

- [ ] **9.2 OpenTelemetry trace ve metric katmanını ekle — SAĞLAYICI BAĞIMSIZ**

  Prompt: HTTP/MCP/auth/Google adapter/DB sınırlarına minimum OpenTelemetry instrumentation tasarla.
  Trace baggage'e secret veya customer content koyma. Latency, request count, error class, quota,
  queue depth, approval age, execution outcome ve auth failure metriklerini düşük-cardinality etiketlerle
  ekle. Exporter seçimini deployment sağlayıcısından ayır.

  Kısmi: OTel 1.44 exporter-bağımsız instrument envanteri ile HTTP, Google reporting ve PostgreSQL
  request-transaction span/metric wiring'i tamamlandı. MCP tool ve auth alt-sınırları henüz ayrı
  operation olarak bağlanmadığı için madde açık.

- [ ] **9.3 Append-only audit deposunu production seviyesine getir — SAĞLAYICI KARARI GEREKİR**

  Prompt: Normal app rolünün geçmiş audit'i update/delete edemediği append-only/WORM yaklaşımını seç.
  Event integrity, actor/principal/customer/proposal/approval/execution/correlation/request ID alanlarını
  doğrula. Audit başlangıcı yazılamıyorsa mutate fail-closed olsun. Retention ve WORM sağlayıcısını ADR
  kabul edilmeden bağlama.

  Kısmi: `20260722_0007` PostgreSQL trigger'ı audit UPDATE/DELETE'i reddeder ve testlidir. WORM/retention
  sağlayıcısı ve bütünlük anahtarı ADR'siz seçilemediği için madde açık.

- [ ] **9.7 Operasyonel sahiplik, destek ve incident SLA'larını kabul ettir**

  Prompt: On-call sahibi, support sahibi/kanalı, security contact, incident severity sınıfları, ilk yanıt
  ve kullanıcı bildirim hedefleri, escalation zinciri, bakım iletişimi ve status page ihtiyacını gerçek
  işletmeci kapasitesiyle belirle. Sahibi olmayan 7/24 taahhüt yazma. Runbook ve alert routing'i bu
  kararlara bağla. Dayanak: `OPERATIONS.md`, `OBSERVABILITY.md`, `PRODUCT.md`, `LEGAL.md`.

  Bloklayıcı: gerçek on-call/support/security sahibi, izlenen kanal ve nöbet kapasitesi repoda yok;
  `LEGAL.md` iletişim alanları Taslak/TBD. Uydurma kişi veya 7/24 SLA yazılmadı; minimum kanıt ve
  production/directory fail-closed kapısı `OPERATIONS.md`'ye eklendi.

---

# Faz 10 — build, CI/CD ve production altyapısı

- [ ] **10.3 Container ve supply-chain güvenliğini kur**

  Prompt: Minimal, non-root, read-only filesystem'e uygun immutable container oluştur. Multi-stage build,
  pinned base digest, SBOM, vulnerability scan, provenance/signing ve healthcheck ekle. Build sırasında
  secret bake etme. Runtime writable path ve CA/timezone gereksinimlerini test et.

- [ ] **10.4 Hosting/network sağlayıcısı ADR'sini kabul ettir**

  Prompt: 7/24 public HTTPS, managed PostgreSQL, secret manager/KMS, WAF/rate limiting, logs/metrics,
  backup, region/data residency, maliyet ve ücretsiz ürün sürdürülebilirliği açısından sağlayıcıları
  karşılaştır. Tek sağlayıcıyı ADR ile kabul ettirmeden vendor-specific production kodu yazma.

- [ ] **10.5 Infrastructure as Code ekle — SAĞLAYICI SONRASI**

  Prompt: Kabul edilen sağlayıcıda dev/staging/prod izolasyonu, private DB, service identity, least
  privilege IAM, secret references, TLS, domain, network egress, autoscaling sınırı, backup ve monitoring'i
  IaC ile kur. Plan/validation testleri ekle. Production apply yapma; kullanıcıdan açık onay al.

- [ ] **10.6 Production secrets manager/KMS adapter'ını uygula — SAĞLAYICI SONRASI**

  Prompt: Yerel vault interface'ini koruyarak managed secret store/KMS adapter'ı, principal-bound secret
  references, envelope encryption, key versioning, rotation ve permanent revoke ekle. App DB'de secret
  değeri tutma. Cross-principal read, revoked secret, key rotation ve provider outage testleri ekle.

- [ ] **10.7 Deployment ve rollback pipeline'ını kur**

  Prompt: Immutable artifact promotion, migration precheck, canary/rolling deploy, readiness gate,
  automated rollback ve manual approval içeren staging→production pipeline tasarla. Production deploy
  yetkisini kullanıcı açık onayı olmadan kullanma. Rollback'in irreversible migration/write etkisini
  runbook'ta açıkla.

- [ ] **10.8 GitHub repository yönetim kararlarını uygula — KULLANICI ONAYIYLA**

  Prompt: Repo visibility, team roles, branch protection, required review/check sayısı, merge yöntemi,
  push protection, Dependabot/update policy ve CODEOWNERS kararlarını kullanıcıyla netleştir. GitHub
  ayarlarını değiştirmeden önce açık onay al. `REPOSITORY.md` ile gerçek remote ayarını eşleştir.

- [ ] **10.10 Browser E2E/a11y suite'ini CI required check'lerine bağla**

  Prompt: Faz 7.6'da eklenen `backend/tests/test_e2e_approvals_playwright.py` (Playwright +
  axe-core) hâlâ yalnız yerel/opsiyonel çalışıyor; `.github/workflows/ci.yml`'e Chromium indirmeyi
  (`python -m playwright install chromium --with-deps`) ve bu dosyayı çalıştıran ayrı bir job ekle,
  `docs/TESTING.md`'nin required check listesine işle. Mevcut `lint-format`/`type-check`/
  `test-python-3.11`/`test-python-3.13`/`docs`/`security`/`migrations`/`container` job'larının süresini/
  önbelleğini bozma; bu job'un başarısızlığının diğer job'ları bloke etmemesi gerekip gerekmediğine
  (flaky/yavaş bir tarayıcı testi olarak required mi yoksa yalnız bilgilendirici mi) karar ver.
  Dayanak: `docs/TESTING.md`, `todo.md` 10.2.

---

# Faz 11 — hukuk, gizlilik ve Google politika uyumu (dış bağımlılıklar)

- [ ] **11.1 İşletmeci ve hukuk kapsamını belirle — BLOKE**

  Prompt: Ürün sahibinden legal entity/unvan, adres, privacy contact, support contact, hedef ülkeler,
  minimum yaş, governing law ve dispute yaklaşımı bilgilerini al. Hukukçuya açık sorular listesi hazırla.
  Bu alanları varsayma veya sahte bilgiyle public metne doldurma.

- [ ] **11.3 Privacy Policy'yi hukukçu incelemesiyle tamamla — BLOKE**

  Prompt: `PRIVACY_POLICY.md` içindeki bütün TBD'leri yalnız işletmeci bilgisi, production veri envanteri,
  subprocessor listesi, retention schedule, transfer safeguards ve hukukçu kararıyla doldur. Google Limited
  Use ve Anthropic directory gereksinimleriyle çapraz kontrol et. Hukukçu onayı olmadan “Kabul edildi” yapma.

- [ ] **11.4 Terms of Service'i hukukçu incelemesiyle tamamla — BLOKE**

  Prompt: `TERMS.md` içindeki provider, acceptable use, third-party terms, availability, termination,
  IP/license, disclaimer/liability, governing law ve consumer notice TBD'lerini hukukçu kararıyla kapat.
  Ürünün tamamen ücretsiz olduğunu ve ödeme bilgisi toplamadığını koru. Hukukçu onayı olmadan yayınlama.

- [ ] **11.5 Kullanıcı hakları ve deletion/export operasyonunu kur — HUKUK SONRASI**

  Prompt: Kabul edilen legal karara göre access/export/correction/deletion/objection talepleri için
  güvenli request channel, identity verification, SLA, legal hold, audit ve backup deletion süreci kur.
  Support görevlisinin başka principal verisine sınırsız erişmesini engelle. Her işlem auditli ve testli olsun.

- [ ] **11.6 Subprocessor ve uluslararası transfer kayıtlarını yayınla — SAĞLAYICI SONRASI**

  Prompt: Gerçek hosting, DB, logging, email/support ve security provider'larını amaç/ülke/safeguard ile
  listele. DPA/SCC ve değişiklik bildirim yükümlülüklerini hukukçuya doğrulat. Kullanılmayan hayali provider
  ekleme.

- [ ] **11.7 Google Ads developer token erişim başvurusunu tamamla — DIŞ EYLEM**

  Prompt: Developer token başvurusunda ürün modeli, kullanıcı akışı, free pricing, reporting/write kapsamı,
  OAuth, güvenlik, RMF ve reviewer erişimini doğru beyan eden kanıt paketi hazırla. Başvuruyu göndermeden
  önce kullanıcıdan açık onay al. Basic/Standard sonucu ve koşulları `GOOGLE_API_ACCESS.md` içine kanıtla.

- [ ] **11.8 Google OAuth app verification paketini tamamla — DIŞ EYLEM**

  Prompt: Restricted `adwords` scope için verified domain, homepage, privacy policy, terms, support,
  consent screen, scope justification, demo video ve test talimatlarını hazırla. Gerekli bağımsız security
  assessment kapsamını Google'dan doğrula. Submission öncesi kullanıcı onayı al; sonucu belgeye işle.

- [ ] **11.9 Google RMF uygunluk matrisini kapat — DIŞ KARAR**

  Prompt: Google'ın verdiği ürün sınıflandırmasına göre creation, management ve reporting RMF maddelerini
  endpoint/tool/UI/test kanıtlarıyla tek tek eşleştir. Uygulanmayan zorunlu satır varken Standard Access
  hazır deme. “Not applicable” satırlarını yazılı Google dayanağı olmadan kapatma.

- [ ] **11.10 Veri ihlali bildirim ve kullanıcı iletişimi sürecini hukukçuya onaylat — BLOKE**

  Prompt: İhlal tespiti, delil koruma, kapsam belirleme, regulator/Google/Anthropic/kullanıcı bildirim
  sahipleri, ülkeye göre süreler, mesaj onayı ve kayıt saklama akışını hukukçu kararıyla netleştir.
  Varsayımsal yasal süre yazma; kabul edilen süreçle `SECURITY.md` ve `OPERATIONS.md` runbook'larını
  eşleştirip masaüstü tatbikat yap. Dayanak: `LEGAL.md`, `SECURITY.md`, `OPERATIONS.md`.

- [ ] **11.11 Controller/processor rolü ve self-service sözleşme kabulünü kapat — BLOKE**

  Prompt: Google Ads verisi, connector telemetry'si ve support verisi için işletmecinin controller/
  processor rolünü hukukçuya belirlet; gerekiyorsa DPA ve veri işleme talimatlarını hazırla. Terms/Privacy
  sürümünü, kabul timestamp'ini, yeniden kabul koşulunu ve kanıt kaydını tasarla. Hukuk kararı olmadan
  checkbox/consent metni veya production kabul kaydı ekleme. Dayanak: `LEGAL.md`, `DATA_MODEL.md`,
  `PRIVACY_POLICY.md`, `TERMS.md`.

---

# Faz 12 — Anthropic Connector Directory hazırlığı

- [ ] **12.3 Reviewer test ortamı ve test hesabı hazırla**

  Prompt: Gerçek müşteri verisi içermeyen ayrılmış Google Ads test hesabı, connector test principal'ı,
  reset prosedürü, sample proposals ve adım adım reviewer instructions hazırla. Credential'ı repo/dokümana
  koyma; güvenli ayrı kanaldan sağlama prosedürünü belgeye ekle.

- [ ] **12.4 Public website/legal/support URL'lerini yayınla — HUKUK SONRASI**

  Prompt: Homepage, privacy policy, terms, support ve gerekirse deletion instructions sayfalarını kabul
  edilmiş metinlerle public HTTPS altında yayınla. Link, mobile/a11y, cache, contact ve availability kontrolü
  yap. Taslak hukuk metnini public final olarak işaretleme.

- [ ] **12.7 Anthropic Directory submission yap — AÇIK KULLANICI ONAYIYLA**

  Prompt: Kullanıcı son paketi açıkça onayladıktan sonra submission formunu doğrulanmış bilgilerle gönder.
  Gönderilen cevapların ve tarihlerin kaydını tut; secret/test credential kopyalama. Reviewer sorularını
  issue/checklist olarak takip et ve gerekli değişikliklerde normal kod+test+belge kapısını uygula.

- [ ] **12.8 Public ürün kimliği, marka, domain ve destek bilgilerini kesinleştir — DIŞ KARAR**

  Prompt: Public ürün adı, doğrulanmış domain, homepage, operator adı ve support/privacy/security contact
  adreslerini ürün sahibi ve hukuk kararıyla kesinleştir. Google/Anthropic marka politikalarına uygunluğu
  doğrula; repo package adı ile public marka farklıysa mapping'i belgele. Placeholder veya sahip olunmayan
  domain yayınlama. Dayanak: `PRODUCT.md`, `CONNECTOR_SUBMISSION.md`, `GOOGLE_API_ACCESS.md`, `LEGAL.md`.

- [ ] **12.9 Claude istemci OAuth uyumluluk matrisini tamamla**

  Prompt: Claude.ai, Claude Desktop ve desteklenecekse Claude Code için CIMD/DCR davranışı, redirect URI,
  hosted callback veya loopback, refresh-token lifetime/rotation grace ve disconnect/re-link akışlarını
  gerçek istemci contract testleriyle doğrula. Authlib authorization-server yaklaşımının desteklenen
  client metadata akışına yeterli olup olmadığını kanıtla; değilse ADR aç. Dayanak: `AUTH.md`,
  `CONNECTOR_SUBMISSION.md`, `MCP.md`, `SECURITY.md`.

- [ ] **12.10 Reviewer credential teslim ve rotasyon prosedürünü doğrula**

  Prompt: Test principal/account credential'larının hangi güvenli Anthropic kanalından, kim tarafından,
  hangi süreyle verileceğini; submission öncesi reset, reviewer erişimi sırasında izleme ve inceleme sonrası
  revoke/rotate adımlarını yazılı prosedür ve tatbikatla doğrula. Credential'ı repo, issue, log veya demo
  materyaline koyma. Dayanak: `CONNECTOR_SUBMISSION.md`, `SECURITY.md`, `OPERATIONS.md`.

---

# Faz 13 — production launch

- [ ] **13.2 Staging uçtan uca testini tamamla**

  Prompt: Ayrılmış test hesabıyla connect → accounts → reporting → proposal → browser approval → varsa
  approved execution → audit → disconnect akışını staging'de test et. Production credential/müşteri verisi
  kullanma. Correlation ID ile bütün zinciri doğrula ve bulunan kusurları release öncesi kapat.

- [ ] **13.3 Güvenlik değerlendirmesi/pentest bulgularını kapat**

  Prompt: Google'ın veya bağımsız değerlendiricinin required assessment/pentest bulgularını severity ve
  exploitability ile triage et. Her fix için regresyon testi ve belge güncellemesi yap. Risk acceptance
  gerekiyorsa ürün sahibi + güvenlik/hukuk onayı olmadan kapatma.

- [ ] **13.4 Production deploy yap — AÇIK KULLANICI ONAYIYLA**

  Prompt: Onaylanan immutable artifact'ı migration backup/precheck, canary, readiness, smoke test ve alarm
  gözlemiyle production'a çıkar. Kullanıcı açıkça deploy istemeden apply yapma. Hata eşiğinde runbook'a göre
  rollback et; veri/mutate durumunu ayrıca reconcile et.

- [ ] **13.5 Kontrollü public açılış yap**

  Prompt: İlk kullanıcı sayısını/quota bütçesini sınırlı tut, onboarding ve support kanalını izle. Auth,
  quota, latency, error, approval age, disconnect ve policy metriklerini gözle. Unauthorized mutate/audit
  failure durumunda write kill switch'i çalıştır. Kullanıcı verisini debug amacıyla ham loglama.

- [ ] **13.6 DAST ve ileri güvenlik test kapısını çalıştır**

  Prompt: Public staging yüzeyinde authenticated/unauthenticated DAST kapsamını, destructive endpoint
  güvenliğini ve test verisi sınırını belirle; uygun araçla OAuth redirect, SSRF, CSRF, CORS, injection,
  session ve rate-limit kontrollerini çalıştır. Mutation testing'in kritik auth/approval/execution
  modüllerine sağlayacağı değeri ölçüp uygulanacak kapsamı belgeye bağla. Production'a saldırı testi yapma.
  Dayanak: `TESTING.md`, `SECURITY.md`, `OPERATIONS.md`.

---

# Faz 14 — lansman sonrası sürekli işler

- [ ] **14.1 Haftalık güvenlik ve operasyon kontrolü kur**

  Prompt: Auth anomaly, cross-principal denial, secret scan, dependency alerts, audit integrity, quota,
  backups, failed jobs ve support incidents için haftalık checklist/automation oluştur. Gerçek olayları
  ticket/runbook ile takip et. Hassas veriyi raporlara koyma.

- [ ] **14.2 Aylık dependency ve platform güncellemesi yap**

  Prompt: Python, FastAPI, MCP SDK, Google Ads API/client, Authlib/Google auth, crypto ve container base
  sürümlerini resmi release/security notlarından kontrol et. Staging contract/regression testleri olmadan
  production yükseltme yapma. Breaking API sunset tarihlerini takvime işle.

- [ ] **14.3 Üç aylık politika/dokümantasyon gözden geçirmesi yap**

  Prompt: Google Ads access/RMF/OAuth/security, Anthropic Connector Directory/MCP, OWASP ve legal/subprocessor
  kaynaklarını yeniden doğrula. Tüm `Sonraki gözden geçirme` tarihlerini ve kaynak linklerini güncelle.
  Politika değişikliği kodu etkiliyorsa normal ADR/kod/test sürecini başlat.

- [ ] **14.4 Quota ve kapasite planını gerçek trafikle güncelle**

  Prompt: Aggregate ve principal/customer bazlı operasyon tüketimi, response size, concurrency, cache hit,
  latency ve queue gözlemlerini anonim/aggregate biçimde analiz et. Rate limit, fair queue ve Standard Access
  kapasite varsayımlarını güncelle. Tek kullanıcının diğerlerini etkilemediğini load testle doğrula.

- [ ] **14.5 SLO ve alert eşiklerini gerçek trafikle kabul et**

  Prompt: En az yeterli gözlem penceresi sonrası availability, latency, auth success, reporting success,
  approval latency ve execution correctness SLI'larından gerçekçi SLO öner. Error budget ve escalation
  politikasını ADR ile kabul ettir. Vanity metric veya müşteri içeriği kullanma.

- [ ] **14.6 Retention/purge ve restore tatbikatlarını periyodik çalıştır**

  Prompt: Kabul edilen takvimde purge sonuçlarını, legal hold istisnalarını, backup expiry ve restore
  bütünlüğünü test et. Credential rotation/revoke tatbikatı yap. Gerçek müşteri kaydını test amacıyla silme;
  ayrılmış fixture/test tenant kullan.

- [ ] **14.7 Kullanıcı geri bildirimi ve ürün yol haritasını güncelle**

  Prompt: Support talepleri ve aggregate kullanım sinyallerinden reporting alanları, approval UX ve yeni
  Google Ads operation ihtiyaçlarını çıkar. Meta/TikTok/LinkedIn'i bu repo kapsamına kendiliğinden ekleme.
  Yeni büyük özellik için önce PRODUCT/DESIGN/API/MCP/LEGAL/Google RMF etkisi ve ADR oluştur.

- [ ] **14.8 Olay sonrası postmortem sürecini uygula**

  Prompt: Her güvenlik, availability, quota, data isolation, audit veya unauthorized mutate olayında
  blameless timeline, detection gap, root cause, impact, containment ve kalıcı aksiyonları kaydet. Secret
  veya müşteri içeriğini postmortem'e koyma. Aksiyonlara sahip/tarih/test kanıtı ata ve runbook'u güncelle.

---

# Faz 15 — OpenAI/ChatGPT istemci desteği (gelecek faz)

> Amaç: Mevcut tek public `/mcp` sunucusunu ve Google Ads backend'ini koruyarak aynı connector'ün hem Claude
> hem ChatGPT tarafından kullanılmasını sağlamak. İkinci backend veya ikinci MCP sunucusu kurulmaz. Bu faz,
> Claude launch görevlerini bloklamaz ve OpenAI yayın/onay işlemleri kullanıcı açıkça istemeden yapılmaz.

- [ ] **15.1 OpenAI/ChatGPT güncel MCP gereksinimlerini araştır ve kapsamı kabul et**

  Prompt: Yalnız resmi OpenAI kaynaklarından ChatGPT Apps/connector, remote MCP, Streamable HTTP, OAuth,
  tool annotation, approval, privacy, review ve yayın gereksinimlerini doğrula. Claude gereksinimleriyle ortak
  çekirdeği ve istemciye özel farkları tabloya dök. Büyük mimari fark çıkarsa koddan önce ADR oluştur.

- [ ] **15.2 Tek sunucu/çoklu MCP istemcisi mimarisini belgele**

  Prompt: Claude ve ChatGPT'nin aynı HTTPS `/mcp`, tool catalog, Google Ads adapter, approval sistemi ve veri
  katmanını kullanacağını; istemci kimliği ile connector `principal_id` izolasyonunun nasıl ayrılacağını
  `ARCHITECTURE.md`, `AUTH.md`, `MCP.md` ve `PRODUCT.md` içinde tanımla. Bir kullanıcının platformlar arası
  hesabını otomatik birleştirme; açık ve güvenli account-linking kararı olmadan kimlik merge etme.

- [ ] **15.3 ChatGPT OAuth client ve callback politikasını uygula**

  Prompt: OpenAI'nin doğrulanmış callback/client registration gereksinimlerini connector authorization server'a
  dar allowlist olarak ekle. PKCE S256, exact redirect URI, resource audience, state, consent, refresh rotation,
  revoke ve confused-deputy kontrollerini koru. Claude callback davranışını bozma; istemciler arası code/token
  redemption negatif testleri ekle.

- [ ] **15.4 ChatGPT için MCP tool uyumluluğunu doğrula**

  Prompt: Mevcut `tools/list` ve `tools/call` şemalarını ChatGPT remote MCP istemcisiyle doğrula. Tool adları,
  JSON Schema, output schema, read-only/destructive annotation, hata cevapları, pagination ve response size
  davranışını iki istemcide ortak tut. Platforma özel tool kopyaları oluşturma; zorunlu farkı adapter katmanında
  ve contract testleriyle sınırla.

- [ ] **15.5 Yazma ve insan onayı davranışını ChatGPT üzerinde doğrula**

  Prompt: ChatGPT'nin kendi tool approval UX'i olsa bile bunu backend insan onayının yerine kabul etme.
  `prepare proposal → browser approval/reject → execute` zincirini test hesabıyla uçtan uca doğrula. Onaysız,
  expired, changed-hash, cross-principal ve replay senaryolarında Google mutate çağrısının yapılmadığını test et.

- [ ] **15.6 OpenAI veri akışı ve hukuki metin etkisini kapat — HUKUK SONRASI**

  Prompt: ChatGPT/OpenAI'ye giden minimum tool verisini production envanteri ve subprocessor kaydına ekle;
  OpenAI'nin rolü, region/transfer, retention ve şartlarını gerçek sözleşmelerle doğrulat. `PRIVACY_POLICY.md`,
  `TERMS.md` ve consent açıklamalarını hukukçu onayı olmadan yayımlama veya `Kabul edildi` yapma.

- [ ] **15.7 ChatGPT güvenlik ve prompt-injection testlerini tamamla**

  Prompt: Google Ads içeriğinin ChatGPT tarafında talimat gibi yorumlanmasının authorization, customer scope,
  tool seçimi veya approval durumunu değiştiremediğini doğrula. Token passthrough, SSRF, oversized input/output,
  cross-client principal karışması, session fixation ve secret/log sızıntısı negatif testlerini çalıştır.

- [ ] **15.8 ChatGPT staging canlı bağlantı testini yap**

  Prompt: Public staging domain ve ayrılmış Google Ads test hesabıyla connect → account discovery → reporting →
  proposal → browser approval/reject → audit → disconnect akışını ChatGPT'de çalıştır. Gerçek müşteri hesabı veya
  production secret kullanma. Aynı staging MCP endpoint'inin Claude regresyon testini de eşzamanlı çalıştır.

- [ ] **15.9 OpenAI/ChatGPT yayın ve reviewer paketini hazırla**

  Prompt: Güncel OpenAI gereksinimlerine göre app/connector adı, açıklaması, tool envanteri, privacy/support URL,
  OAuth bilgileri, test hesabı, reviewer talimatları, örnek prompt'lar ve gerekli görselleri hazırla. Claude
  Directory paketinden ortak kanıtları yeniden kullan; OpenAI'ye özgü beyanları ayrı versionla.

- [ ] **15.10 OpenAI/ChatGPT submission ve kontrollü açılışı yap — AÇIK KULLANICI ONAYIYLA**

  Prompt: Tüm teknik, güvenlik, hukuki ve staging kapıları kapanınca gönderilecek tam paketi kullanıcıya göster
  ve açık onay al. Onay olmadan submission/deploy yapma. Submission ID, tarih, reviewer yazışması, karar ve
  koşulları belgeye işle; kabul sonrası sınırlı kullanıcıyla quota/error/auth/approval sinyallerini izle.

---

# Faz 16 — production PostgreSQL geçişini kapat

- [ ] **16.1 Bütün runtime repository yollarını PostgreSQL unit-of-work'e taşı**

  Prompt: OAuth, account, reporting metadata, proposal, approval, execution, session, disconnect ve audit
  yollarında production composition'ın SQLite repository'ye düşmediğini doğrula. Her request'i kısa transaction,
  açık principal context ve dış ağ çağrılarından ayrılmış unit-of-work ile çalıştır; contract testleri ekle.

- [ ] **16.2 RLS izolasyonunu bütün tablolarda ve sorgularda kanıtla**

  Prompt: Principal'a bağlı her tablo için RLS enable/force policy, composite foreign key ve filtersiz sorgu
  reddini doğrula. Disposable PostgreSQL üzerinde cross-principal read/write/update/delete negatif testlerini
  çalıştır; migration owner/bypass rolünün uygulama runtime'ında kullanılamadığını göster.

- [ ] **16.3 Migration upgrade, rollback ve veri doğrulama kapısını tamamla**

  Prompt: Boş DB ve bir önceki sürümden upgrade senaryolarını çalıştır; destructive DDL ve lock süresini incele.
  Rollback mümkün değilse forward-fix prosedürü, backup precheck ve veri bütünlüğü sorgularını runbook'a ekle.

---

# Faz 17 — retention, deletion ve yedek yaşam döngüsü

- [ ] **17.1 Hukukçu onaylı retention schedule'ı şemaya dönüştür — HUKUK SONRASI**

  Prompt: Her veri kategorisinin retention, legal hold, tombstone ve purge kuralını production envanterinden
  makinece uygulanabilir policy'ye çevir. Süresi kararlaştırılmamış kategori için sonsuz saklama varsayma.

- [ ] **17.2 Principal export ve deletion worker'ını uygula — HUKUK SONRASI**

  Prompt: Kimlik doğrulanmış talebi principal kapsamlı export/delete job'una çevir; secret, başka principal ve
  internal güvenlik metadata'sını export etme. Revoke, aktif veri purge, legal-hold istisnası ve audit sonucunu
  idempotent worker/testlerle doğrula.

- [ ] **17.3 Backup expiry ve restore sonrası deletion tutarlılığını test et — SAĞLAYICI SONRASI**

  Prompt: Silinen principal'ın yedekten geri dönüp aktif hale gelmesini engelleyen suppression/tombstone akışını
  kur. Backup retention sonunda fiziksel purge kanıtını ve restore tatbikatını gerçek seçilmiş sağlayıcıda çalıştır.

---

# Faz 18 — hosting, network ve public domain kararı

- [ ] **18.1 Hosting/region/bütçe ADR'sini ürün sahibine kabul ettir — DIŞ KARAR**

  Prompt: GCP/AWS/Azure adaylarını hedef ülkeler, veri yerleşimi, aylık bütçe, RPO/RTO, operasyon yükü ve gerekli
  servislerle karşılaştır. Ürün sahibi ve hukuk onayı olmadan ADR-0008'i kabul edilmiş yapma.

- [ ] **18.2 Public domain, DNS, TLS ve marka sahipliğini kesinleştir — DIŞ KARAR**

  Prompt: Ürün adı, doğrulanabilir domain sahibi, DNS yöneticisi, TLS yenilemesi, homepage/legal/support URL'leri
  ve OAuth redirect host'unu kaydet. Geçici veya sahipliği doğrulanmamış domain'i production kimliği yapma.

- [ ] **18.3 WAF, egress ve private network topolojisini tasarla**

  Prompt: Public ingress'i yalnız HTTPS MCP/OAuth/health/legal uçlarıyla sınırla; DB/KMS private kalsın. Google,
  Anthropic/OpenAI callback ve telemetry egress'ini allowlist et; proxy trust, real client IP ve SSRF sınırını ADR'ye bağla.

---

# Faz 19 — Infrastructure as Code ve ortam izolasyonu

- [ ] **19.1 Dev/staging/production altyapısını IaC ile oluştur — SAĞLAYICI SONRASI**

  Prompt: Ayrı project/account, network, compute, PostgreSQL, KMS, secrets, artifact registry, WAF ve telemetry
  kaynaklarını versionlanmış IaC ile tanımla. Production apply kullanıcı onayı olmadan çalışmasın.

- [ ] **19.2 Least-privilege servis kimliklerini ve deploy rollerini uygula**

  Prompt: Runtime, migration, deploy, audit writer, revocation worker ve insan break-glass rollerini ayır.
  Wildcard admin yetkilerini kaldır; erişim süresi, approval ve audit kanıtını test et.

- [ ] **19.3 IaC plan, drift ve policy-as-code kapılarını CI'a ekle**

  Prompt: PR'da salt okunur plan, secret scan, public exposure ve encryption policy kontrolleri çalıştır.
  Drift tespitini alarm üretir hale getir; otomatik production apply veya destroy yapma.

---

# Faz 20 — production secrets ve credential güvenliği

- [ ] **20.1 Managed KMS/secrets adapter'ını uygula — SAĞLAYICI SONRASI**

  Prompt: Local vault interface'ini managed KMS/secrets provider'a bağla; DB'de yalnız opaque reference/key version
  tut. Principal+credential binding, envelope encryption, least privilege ve secret redaction testlerini koru.

- [ ] **20.2 Secret rotasyon ve emergency revoke runbook'unu canlı ortamda doğrula**

  Prompt: OAuth client secret, developer token, DB credential, signing key ve vault key için ayrı owner/tetikleyici/
  rollback tanımla. Ayrılmış staging secret'larıyla rotasyon yap; gerçek değeri log/test çıktısına koyma.

- [ ] **20.3 Credential revocation outbox worker'ını production seviyesine getir**

  Prompt: Disconnect/revoke outbox'ını retry, dead-letter, idempotency, alert ve reconciliation ile tamamla.
  Vault/Google geçici hatasında DB state'in yeniden erişime açılmadığını ve cross-principal işlem olmadığını test et.

---

# Faz 21 — quota, fair-use ve kapasite koruması

- [ ] **21.1 Principal/customer/developer-token kota bütçelerini uygula**

  Prompt: Google access level sonucuna göre kayan pencere budget'larını aggregate ve principal/customer düzeyinde
  uygula. Tek kullanıcının ortak developer token kotasını tüketmesini engelle; retry-after ve actionable hata döndür.

- [ ] **21.2 Fair queue ve kontrollü concurrency katmanını kur**

  Prompt: Reporting işlerini principal namespace'li adil kuyruğa al; global/customer/service concurrency sınırlarını
  uygula. Starvation, noisy-neighbor, cancellation ve worker crash senaryolarını deterministic test et.

- [ ] **21.3 Load ve soak testleriyle kapasite zarfını ölç**

  Prompt: Sentetik/test verisiyle MCP concurrency, pagination, DB pool, queue latency ve Google mock rate-limit
  davranışını ölç. Sonuçları capacity planına yaz; gerçek müşteri verisi veya production Ads mutate kullanma.

---

# Faz 22 — production observability ve immutable audit

- [ ] **22.1 OpenTelemetry trace/metric export'unu seçilmiş backend'e bağla**

  Prompt: Allowlist metric ve span şemalarını export et; principal/customer/secret gibi yüksek cardinality veya
  hassas değerleri label yapma. Sampling'in audit veya hata kanıtını düşürmediğini doğrula.

- [ ] **22.2 Append-only/WORM audit deposunu production'a al**

  Prompt: Audit writer ile reader/admin rollerini ayır; update/delete'i teknik olarak engelle. Hash chain veya
  eşdeğer bütünlük kontrolü, zaman senkronizasyonu, export ve retention politikasını saldırı testleriyle doğrula.

- [ ] **22.3 Alarm, dashboard ve correlation zincirini staging'de doğrula**

  Prompt: Auth anomaly, cross-principal denial, audit failure, quota, latency, revocation backlog ve unauthorized
  mutate sinyalleri için alarm üret. Bir test request'ini HTTP→MCP→DB→Google mock→audit boyunca correlation ile izle.

---

# Faz 23 — işletmeci, hukuk ve public politikalar

- [ ] **23.1 İşletmeci ve hedef hukuk kapsamını ürün sahibinden al — DIŞ KARAR**

  Prompt: Unvan, adres, ülke, privacy/support/security contact, hedef ülkeler, minimum yaş, governing law ve
  uyuşmazlık yaklaşımını kanıtlı biçimde topla. Sahte veya varsayımsal bilgi yazma.

- [ ] **23.2 Privacy Policy ve Terms'ü hukukçuya onaylat — HUKUKÇU GEREKLİ**

  Prompt: Veri envanteri, subprocessors, retention, transfer, controller/processor rolleri ve ücretsiz ürün
  modelini public metinlere işle. Hukukçu adı/tarih/sürüm kanıtı olmadan DRAFT durumunu kaldırma.

- [ ] **23.3 Kullanıcı hakları, DPA ve breach karar matrisini kabul ettir — HUKUKÇU GEREKLİ**

  Prompt: Access/export/correction/deletion/objection SLA'ları, identity verification, legal hold, DPA ve ülke
  bazlı bildirim sahip/sürelerini karara bağla. Kabul edilen kararları runbook ve test kabul kriterlerine dönüştür.

---

# Faz 24 — Google developer token ve OAuth verification

- [ ] **24.1 Developer-token access/permissible-use başvurusunu gönder — AÇIK KULLANICI ONAYIYLA**

  Prompt: Ürün akışı, ücretsiz model, reporting/proposal/write kapsamı, OAuth, güvenlik ve reviewer kanıt paketini
  kullanıcıya göster. Onay sonrası yetkili API Center hesabından gönder; submission ID ve tam beyan sürümünü kaydet.

- [ ] **24.2 Google OAuth app verification'ı tamamla — DIŞ EYLEM**

  Prompt: Verified domain, consent screen, homepage, privacy/terms/support, scope justification, demo video ve
  test talimatlarını production bilgileriyle gönder. Google yazışmalarını ve re-verification koşullarını belgele.

- [ ] **24.3 Restricted-scope security assessment sonucunu kapat — DIŞ DEĞERLENDİRME**

  Prompt: Google'ın istediği CASA/assessor kapsamını doğrula; bulguları severity ve regression testleriyle kapat.
  Letter of Validation veya uygulanmaz yazılı teyidi olmadan verification tamamlandı deme.

---

# Faz 25 — Google RMF ve güvenli write kapsamı

- [ ] **25.1 Google'ın yazılı RMF sınıflandırmasını kanıt matrisine işle — DIŞ KARAR**

  Prompt: Reporting, management, creation ve recommendation satırlarını Google cevabıyla eşleştir. Yazılı dayanak
  olmadan zorunlu işlevi N/A yapma veya Standard Access hazır iddiasında bulunma.

- [ ] **25.2 Pause/enable ve budget update execution adapter'larını uygula**

  Prompt: Yalnız onaylı allowlist operasyonlarını freshness/hash/account revalidation, idempotency ve immutable
  audit ile Google Ads test hesabına bağla. Onaysız veya ambiguous retry'da mutate çağrısı yapma.

- [ ] **25.3 Campaign creation kapsamını ayrı ürün kararıyla değerlendir**

  Prompt: RMF, UI, validation, budget ve legal etkisini ADR/PRODUCT kararıyla belirle. Açılırsa yeni campaign'i
  daima `PAUSED` oluştur; karar yoksa tool/schema/endpoint ekleme.

---

# Faz 26 — staging ortamı ve uçtan uca doğrulama

- [ ] **26.1 Production-benzeri staging deploy'u yap — AÇIK KULLANICI ONAYIYLA**

  Prompt: Ayrı cloud project, DB, KMS, OAuth client, developer token ve sentetik test verisi kullan. Migration,
  readiness, TLS, metadata ve rollback smoke kontrollerini çalıştır; production credential kullanma.

- [ ] **26.2 Claude üzerinden tam read/proposal/disconnect akışını test et**

  Prompt: Connect→Google OAuth→accounts→reporting→proposal→browser approve/reject→audit→disconnect zincirini
  public staging URL'de çalıştır. Correlation ID ve principal isolation kanıtını kaydet.

- [ ] **26.3 Onaylı write ve failure/recovery senaryolarını staging'de test et — GOOGLE KARARI SONRASI**

  Prompt: Ayrılmış Ads test hesabında pause/enable/budget işlemlerini test et. Timeout, unknown result, stale
  approval, audit outage ve revoke sırasında fail-closed/reconciliation davranışını doğrula.

---

# Faz 27 — güvenlik değerlendirmesi ve release hardening

- [ ] **27.1 Authenticated/unauthenticated DAST çalıştır**

  Prompt: Staging üzerinde OAuth redirect, SSRF, CSRF, CORS, injection, session, rate-limit ve security header
  testleri yap. Destructive endpoint'leri yalnız sentetik/test hesabında sınırla; production'a saldırı testi yapma.

- [ ] **27.2 Bağımsız pentest ve threat-model açıklarını kapat**

  Prompt: Bulguları severity/exploitability ile triage et; her düzeltmeye regression testi ve belge kanıtı ekle.
  High/critical bulgu açıkken launch önerme; risk acceptance için ürün sahibi+güvenlik+hukuk onayı iste.

- [ ] **27.3 Supply-chain ve release artifact doğrulamasını tamamla**

  Prompt: Locked dependency, SBOM, vulnerability scan, image signature/provenance, non-root runtime ve digest-pinned
  deploy'u CI'da doğrula. Aynı test edilen immutable artifact dışında production release yapma.

---

# Faz 28 — Claude Directory submission ve yayın

- [ ] **28.1 Public website, support ve reviewer hesabını hazırla**

  Prompt: Verified domain'de setup/troubleshooting/privacy/terms/support/security sayfalarını yayınla. Dolu fakat
  sentetik Google Ads test hesabı, reset prosedürü ve credential teslim kanalını doğrula.

- [ ] **28.2 Claude yüzeylerinde connector uyumluluğunu doğrula**

  Prompt: Claude.ai, Desktop, mobile ve Code üzerinde auth, tools/list, read, proposal, approval ve disconnect
  davranışını test et. Platform farklarını belgeleyip server contract'ını parçalamadan düzelt.

- [ ] **28.3 Anthropic Directory submission yap — AÇIK KULLANICI ONAYIYLA**

  Prompt: Portalda gönderilecek isim, açıklama, URL, OAuth, tool, reviewer, policy ve bakım beyanlarını kullanıcıya
  göster. Açık onay sonrası gönder; submission ID, yazışma, karar ve koşulları versionlanmış kanıt olarak sakla.

---

# Faz 29 — kontrollü production launch

- [ ] **29.1 Final production readiness ve go/no-go toplantısını tamamla**

  Prompt: Security, legal, Google, Anthropic, DB, backup, observability, support, capacity ve rollback kanıtlarını
  imzalı checklist'te doğrula. Tek bloklayıcı açıkken go kararı verme.

- [ ] **29.2 Canary production deploy yap — AÇIK KULLANICI ONAYIYLA**

  Prompt: Backup/precheck/migration/canary/readiness/smoke adımlarıyla immutable artifact'ı deploy et. Hata eşiğinde
  rollback ve mutate reconciliation çalıştır; deploy sonrası secret veya müşteri verisi loglama.

- [ ] **29.3 Sınırlı kullanıcıyla public açılışı izle**

  Prompt: Kullanıcı/quota bütçesini sınırlı tut; auth, error, latency, approval age, revoke, audit ve support
  sinyallerini izle. Unauthorized mutate veya isolation ihlalinde kill switch ve incident runbook'u çalıştır.

---

# Faz 30 — ChatGPT ortak MCP yayını ve çoklu istemci operasyonu

- [ ] **30.1 ChatGPT OAuth ve remote MCP uyumluluğunu production contract'ına ekle**

  Prompt: Resmi OpenAI gereksinimlerini yeniden doğrula; aynı `/mcp` sunucusunda istemciye özel callback/client
  allowlist'ini uygula. Claude ile cross-client token/code/principal karışmasını negatif testlerle engelle.

- [ ] **30.2 ChatGPT staging, hukuk ve reviewer kapılarını tamamla**

  Prompt: ChatGPT read/proposal/approval/disconnect akışını staging'de test et; OpenAI veri akışı/subprocessor/
  privacy etkisini hukukçuya onaylat ve reviewer materyalini gerçek yayın gereksinimine göre hazırla.

- [ ] **30.3 ChatGPT submission ve çoklu istemci canary açılışı yap — AÇIK KULLANICI ONAYIYLA**

  Prompt: Gönderim paketini kullanıcıya gösterip açık onay al; karar/koşulları kaydet. Açılışta Claude ve ChatGPT
  trafiğini ayrı client sinyalleriyle ama aynı principal güvenlik standardıyla izle; ikinci backend kurma.

---

# Faz 31 — abuse, fraud ve kötüye kullanım koruması

- [ ] **31.1 Abuse tehdit modelini ve fair-use politikasını kabul et**

  Prompt: Bot hesapları, credential stuffing, OAuth consent abuse, quota exhaustion, proposal spam, scraping ve
  coordinated multi-account kullanımını modelle. Ücretsiz ürün modelini koruyan, ayrımcı olmayan ve privacy ile
  uyumlu fair-use limitlerini PRODUCT/SECURITY/RATE_LIMITS belgelerinde kabul ettir.

- [ ] **31.2 Risk tabanlı throttling ve otomatik koruma kontrollerini uygula**

  Prompt: IP'yi kalıcı kimlik olarak kullanmadan principal/client/network sinyallerini minimum veriyle değerlendir.
  Kademeli rate limit, temporary cooldown, CAPTCHA/step-up gereksinimi ve write kill switch uygula; fail-open yapma.

- [ ] **31.3 Abuse appeal ve yanlış pozitif operasyonunu kur**

  Prompt: Askıya alma neden kodu, kullanıcı bildirimi, güvenli itiraz kanalı, support yetki sınırı ve audit kaydını
  tanımla. Başka principal verisini support'a açmadan restore/deny kararını test et ve ölçülebilir SLA belirle.

---

# Faz 32 — disaster recovery ve bölgesel dayanıklılık

- [ ] **32.1 Kabul edilmiş RPO/RTO için disaster recovery mimarisini tamamla**

  Prompt: DB, audit, KMS metadata, queue ve config için backup/replication/failover kapsamını seçilmiş provider'da
  tasarla. Google token secret'larını gereksiz region'lara kopyalama; veri yerleşimi ve hukuk kararına uy.

- [ ] **32.2 Tam ortam kaybı tatbikatı yap**

  Prompt: Staging project/region kaybını simüle ederek IaC restore, secret erişimi, DB point-in-time recovery,
  DNS/TLS yönlendirme, queue reconciliation ve readiness adımlarını çalıştır. Ölçülen RPO/RTO'yu kaydet.

- [ ] **32.3 Failback ve split-brain korumasını doğrula**

  Prompt: Eski region geri geldiğinde çift worker/mutate, stale approval, duplicate audit ve token rotation yarışını
  engelle. Tek aktif writer/lease/fencing mekanizmasını test et; failback kullanıcı onayı ve runbook ile yürüsün.

---

# Faz 33 — erişilebilirlik, lokalizasyon ve kullanıcı deneyimi kalitesi

- [ ] **33.1 Approval ve public web yüzeylerinde WCAG 2.2 AA denetimi yap**

  Prompt: Keyboard-only, focus order, screen reader labels, contrast, error association, zoom/reflow ve reduced
  motion kontrollerini otomatik ve manuel test et. Kritik erişilebilirlik kusuru açıkken reviewer paketi kapatma.

- [ ] **33.2 Türkçe/İngilizce lokalizasyon altyapısını tamamla**

  Prompt: UI, OAuth consent yardımcı metni, hata mesajı, support ve public dokümanlarda locale negotiation/fallback
  tanımla. Security reason code'larını çevrilebilir kullanıcı metninden ayır; loglara serbest kullanıcı metni koyma.

- [ ] **33.3 Tarih, para birimi ve timezone doğruluğunu test et**

  Prompt: Google Ads account timezone/currency ile kullanıcının locale/timezone'unu açıkça ayır. Rapor aralığı,
  budget preview, UTC audit ve DST sınırlarını test et; float ile para hesabı yapma.

---

# Faz 34 — performans, maliyet ve sürdürülebilir ücretsiz hizmet

- [ ] **34.1 Uçtan uca performans bütçelerini ve SLI hedeflerini ölç**

  Prompt: Auth, accounts, reporting, proposal, approval ve disconnect için p50/p95/p99 latency, payload size,
  DB query count ve Google operation maliyetini staging/load testlerinden çıkar. Tahmini değil ölçülmüş baseline yaz.

- [ ] **34.2 Güvenli cache ve sorgu optimizasyonunu uygula**

  Prompt: Yalnız kabul edilmiş kısa TTL ile principal+customer+query namespace'li cache kullan. Cache poisoning,
  cross-principal hit, stale approval ve deletion sonrası veri kalması testlerini ekle; raw Ads snapshot'ı kalıcılaştırma.

- [ ] **34.3 Ücretsiz hizmet için maliyet guardrail'lerini kur**

  Prompt: Compute, DB, logging, egress, KMS ve support maliyetlerini aggregate takip et; bütçe alarmı ve kontrollü
  degradation tanımla. Maliyet baskısında güvenlik/audit/onay kontrollerini kapatma veya ödeme altyapısı ekleme.

---

# Faz 35 — sürekli uyum, yeniden doğrulama ve ürün yönetişimi

- [ ] **35.1 Google, Anthropic ve OpenAI yeniden doğrulama takvimini kur**

  Prompt: OAuth/security assessment, developer-token/RMF, Directory/App review, domain/contact ve policy review
  tarihlerine owner/uyarı/kanıt ata. Süresi geçen onayda etkilenen özelliği fail-closed sınırlayan runbook yaz.

- [ ] **35.2 Tool ve veri kullanımı değişiklik yönetimini otomatik kapıya bağla**

  Prompt: Yeni tool/scope/veri kategorisi/subprocessor/retention değişikliğinde SECURITY, LEGAL, API/MCP sözleşmesi,
  kullanıcı bildirimi ve gerekiyorsa re-consent/re-submission kontrolünü CI/release checklist'inde zorunlu yap.

- [ ] **35.3 Yıllık ürün ve risk değerlendirmesi gerçekleştir**

  Prompt: Kullanım, incidents, abuse, support, quota, accessibility, privacy, subprocessors ve platform politika
  değişikliklerini yıllık olarak değerlendir. Büyük kapsam değişikliğini ADR/ürün sahibi/hukuk onayı olmadan açma;
  ücretsiz Google Ads-only ürün sınırını yeniden doğrula.

---

# Kesinlikle kapsam dışı

- Ödeme, abonelik, faturalama veya kredi kartı altyapısı ekleme.
- Meta, TikTok, LinkedIn veya başka reklam platformlarını bu fazlara dahil etme.
- Raw GAQL veya arbitrary Google Ads mutate tool'u açma.
- Claude/model onayını insan onayı yerine kabul etme.
- Gerçek müşteri credential'ı veya hesabıyla CI/test çalıştırma.
- Google token'ını MCP client'a/Claude'a iletme.
- Taslak legal/Google access kararlarına dayanarak production özelliği açma.
- Kullanıcı istemeden commit, push, PR, deploy, GitHub ayarı veya dış submission yapma.
