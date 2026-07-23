# Test stratejisi

**Durum:** Kabul edildi  
**Son gözden geçirme:** 2026-07-22

## Amaç

Değişikliklerin iş kabul kriterlerini, public kullanıcı izolasyonunu, insan onayı kapısını ve dış servis
sözleşmelerini gerçek müşteri verisi kullanmadan kanıtlayan test yaklaşımını belirlemek.

## Araştırma

- Google Ads [test accounts](https://developers.google.com/google-ads/api/docs/best-practices/test-accounts),
  geliştirme sırasında üretim hesaplarını etkilemeden API çağrılarının denenmesini sağlar; ancak test
  hesaplarının reklam sunmadığı ve bazı özellikleri kapsamadığı dikkate alınmalıdır.
- [OWASP Web Security Testing Guide](https://owasp.org/www-project-web-security-testing-guide/), auth,
  authorization, session, input validation ve business logic için sistematik negatif test alanları sunar.
- [WCAG 2.2](https://www.w3.org/TR/WCAG22/) klavye, focus, reflow, hata önleme ve name/role/value gibi
  UI kabul kriterlerinin normatif kaynağıdır.
- MCP [Tools specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools), input/output
  schema ve tool error davranışının contract testlerinde doğrulanabilecek sınırlarını tanımlar.

## Karar

Testler risk tabanlı piramit ve aşağıdaki merge kapılarıyla uygulanır. Canlı Anthropic/Google testleri
deterministik CI kapısının parçası değildir; resmi client mock'ları ve ayrılmış Google test hesabı kullanılır.
**Sonraki gözden geçirme:** 2026-10-22

## Kalite kapısı

Merge için format/lint, type check, unit, integration, güvenlik negatif testleri ve secret/dependency
taraması başarılıdır. Testler gerçek müşteri credential'ı veya hesabı kullanmaz.

GitHub Actions required check adları: `lint-format`, `type-check`, `test-python-3.11`, `test-python-3.13`,
`docs`, `security`, `migrations`, `container`. Job'lar secret paylaşmaz/cache kullanmaz ve action'ları tam
commit SHA ile pinler. `uv.lock` değişmeden `uv sync --frozen` başarısız olur. Dependency güncellemesi haftalık
Dependabot veya ayrı bakım PR'ıdır; lock diff'i, test ve `pip-audit` birlikte review edilir. Critical/high açıkta
özellik geliştirme durur; en dar sürüm güncellemesi yapılır, istisna yalnız gerekçeli ve süreli olabilir.

Bağlayıcı belgelerin yaşam döngüsü metadata'sı, yerel Markdown bağlantıları, `DOCUMENTATION.md`
matrisindeki belge hedefleri, `docs/decisions/` ADR metadata'sı (`Durum`/`Tarih`/`Sahip`, kanonik
`Durum` değerleri), henüz kabul edilmemiş bir ADR'a referans veren belgeler, geçmiş `Sonraki gözden
geçirme` tarihleri ve Türkçe karakterlerin UTF-8→Latin-1 round-trip'inden kalan mojibake veya
çözümlenemeyen Unicode değiştirme karakteri kalıntıları `python tools/check_docs.py` ile doğrulanır;
her kural `backend/tests/test_check_docs.py` içinde birim testleriyle kapsanır.

Format/lint (Ruff), type checker (Pyright), test runner (pytest + pytest-cov, %80 coverage tabanı),
SAST (Bandit), secret scanner (detect-secrets) ve dependency scanner (pip-audit) seçimi ve gerekçesi
`docs/decisions/0003-dev-tooling.md`'de karara bağlandı; sürüm pinleri `backend/pyproject.toml` →
`[project.optional-dependencies].dev` grubunda, araç konfigürasyonları aynı dosyanın `[tool.*]`
bölümlerinde. Komutlar `README.md` → "Mevcut doğrulama komutları" ile tek kaynaktır.

Bu araçlar ilk kez eklenirken gerçek bulgular çıktı; hepsi ya düzeltildi ya da gerekçeli olarak
kapsam dışı bırakıldı:

- `pyright` iki gerçek hata buldu ve düzeltildi: `auth/server.py::_protected_resource_metadata`
  parametresindeki kullanılmayan `Settings` anotasyonu için eksik import eklendi;
  `api/retry.py`'nin `except Exception as exc` blokundaki kullanılmayan `exc` bağlaması kaldırıldı
  (F841, Ruff tarafından bulundu).
- `ruff check`, testlerdeki `sys.path.insert` sonrası modül-seviyesi importları (E402) ve
  `assertRaises(Exception)` (B017) desenlerini yapısal olarak kabul eden `per-file-ignores`
  aldı; FastAPI'nin `Depends(...)` varsayılan argüman deseni ve `RetryPolicy`
  (`@dataclass(frozen=True, slots=True)`) için B008 `extend-immutable-calls` allowlist'i eklendi
  (ikisi de gerçek bir mutable-default hatası değil). Ayrıca 6 gerçekten kullanılmayan import
  (`errors.py`, `test_auth_cimd.py`, `test_auth_server_http.py`, `test_oauth_store.py`) kaldırıldı.
- `bandit`, 12 bulgunun tamamını satır bazlı `# nosec BXXX` gerekçesiyle kapattı: GAQL sorgu
  string'i (B608 — SQL değil, alan/kaynak adları kod-only allowlist sabitleri), `proposal`
  repository'sindeki dinamik `WHERE` eki (B608 — değer `?` ile parametrize, yalnız iki sabit
  literalden biri eklenir), backoff jitter için `random.Random()` (B311 — kriptografik kullanım
  değil), `token=""` placeholder'ları (B106 — yalnız hash saklanır, ham değer hiç DB'ye yazılmaz)
  ve daha önceki bir kontrolün garanti ettiği `assert`'ler (B101). Her satırda gerekçe yorumu var;
  bkz. ilgili dosyalar.
- `detect-secrets`, mevcut test fixture'larındaki kasıtlı sahte `client-secret`/`dev-token`
  değerlerini ve `docs/API_CONTRACTS.md`'deki örnek payload'ı `.secrets.baseline`'a kaydetti (repo
  kökünde). **Bilinen platform kısıtı:** `detect-secrets scan` Windows'ta baseline dosyasına `\`
  yol ayırıcısı yazar; bu, Linux CI'da her satırı "yeni secret" gibi gösterir. Baseline yeniden
  üretilirken her zaman `--all-files` kullanılmalı (bayraksız `scan --baseline` yalnız git-tracked
  dosyaları tarar, henüz commit edilmemiş yeni fixture'ları sessizce atlar) ve ardından `\` → `/`
  normalize edilmelidir. CI/lokal doğrulama komutu `detect-secrets scan` değil
  `detect-secrets-hook --baseline .secrets.baseline <dosyalar>`dır (baseline'ı değiştirmez, yalnız
  denetler); bu komutun gerçekten yeni bir sahte secret'i reddettiği doğrulandı.
- `pip-audit`, bu oturumun sandbox Python kurulumunda çalıştırılamadı (`ModuleNotFoundError: No
  module named 'venv'` — kurulum stdlib `venv`/`ensurepip` içermeyen, gömülü/minimal bir Python
  dağıtımı; standart bir CPython/CI ortamının parçası değil). Araç doğru pinlenip belgelendi;
  gerçek çalıştırma kanıtı `todo.md` 10.2 (CI pipeline) veya standart bir yerel Python kurulumunda
  sağlanmalı.

`ruff format --check`, `ruff check` ve `pyright backend/src` mevcut kodda temizdir (bkz. `todo.md`
1.6/1.7). PostgreSQL production şeması için Alembic offline SQL üretimi canlı DB'ye bağlanmadan
`backend/` dizininden `python -m alembic -c alembic.ini upgrade head --sql` ile doğrulanır.
Canlı RLS entegrasyon testleri yalnız `ADDOBSERVER_POSTGRES_TEST_DSN` disposable bir PostgreSQL test
veritabanına ayarlandığında çalışır; DSN yoksa `backend.tests.test_postgres_rls_integration` skip eder.
PostgreSQL runtime helper contract'ı canlı DB gerektirmeden `backend.tests.test_postgres_runtime` ile
doğrulanır: `DATABASE_URL` dialect kontrolü, DSN redaction ve principal transaction set/cleanup/rollback
sırası test edilir.
İlk SQLAlchemy repository contract'ı `backend.tests.test_postgres_repository` ile doğrulanır: principal
idempotency, connector client consent scoping/re-consent, account principal scoping, active/history ayrımı,
relink reactivation, credential active/revoke ownership, proposal payload/hash guard'ları, pending proposal
pagination/filtering, approval/audit principal scoping, execution idempotency/result ownership ve
repository'nin kendi transaction'ını commit etmemesi.

## Test piramidi

- **Unit:** şema, policy, para micros dönüşümü, state machine, retry sınıflandırma.
- **Integration:** DB isolation, repository filtreleri, approval→execution, audit atomicity.
- **Contract:** Google Ads ve MCP adapter request/response eşlemeleri; resmi client mock/fake.
- **Schema:** SQLAlchemy metadata ve Alembic revision sözleşmesi; PostgreSQL-only tipler ve composite
  ownership/idempotency constraint'leri canlı DB gerektirmeden derlenir. RLS migration'ı contract
  testleriyle görünür kalır; gerçek PostgreSQL üzerinde cross-principal ve pool reuse izolasyonu
  ayrı entegrasyon testleriyle kanıtlanmalıdır.
- **UI:** bileşen durumları, klavye, erişilebilir adlar, onay özeti ve error recovery.
- **E2E:** test hesabı veya tamamıyla fake servisle veri→öneri→onay→uygulama→audit. Browser E2E +
  otomatik erişilebilirlik aracı olarak **Playwright** (Python) + **axe-core** (`axe-core-python`
  paketiyle) seçildi (Faz 7.6): `backend/tests/test_e2e_approvals_playwright.py`, diğer tüm
  testlerin aksine `httpx.ASGITransport` yerine gerçek bir `uvicorn` soketine karşı gerçek bir
  Chromium başlatır; login→önizleme→axe-core taraması→klavye/skip-link→320px reflow→onayla→
  disconnect akışını tek bir testte kanıtlar. `playwright`/`axe-core-python` bu dosyayı import
  edemediğinde veya Chromium kurulu değilse (`python -m playwright install chromium`) test
  `unittest.SkipTest` ile temiz biçimde atlanır -- diğer testler bu ağır (~100MB) bağımlılığa hiç
  ihtiyaç duymaz. **Bilinen sınır:** bu dosya henüz `todo.md` 10.2'nin listelediği CI required
  check'lerine eklenmedi (`lint-format`/`type-check`/`test-python-3.11`/`test-python-3.13`/`docs`/
  `security`/`migrations`/`container`) -- bu, ayrı bir CI iş akışı değişikliği gerektirir ve Faz 7.6
  kapsamı yalnız "aracı seç ve staging-benzeri ortamda çalıştığını kanıtla"ydı; CI'a bağlama
  `todo.md`'ye ayrı bir takip maddesi olarak eklendi.
- **Faz 13.2 uçtan uca zincir:** gerçek bir staging ortamı henüz yok (`docs/OPERATIONS.md` "Faz 13.1"),
  bu yüzden `backend/tests/test_mcp_integration.py::test_full_chain_connect_accounts_reporting_proposal_approval_disconnect`
  yerel eşdeğerdir: connect→accounts→reporting→proposal (gerçek MCP Streamable HTTP istemcisi
  üzerinden) → browser approval → audit → disconnect zincirinin tamamını tek testte, her adımda
  ayırt edici bir `X-Correlation-Id` ile kanıtlar. Bu test aynı zamanda mounted `/mcp` ASGI
  uygulamasında `CorrelationIdMiddleware`'in gerçekten çalıştığını doğrulayan ilk testtir -- önceki
  correlation-id testlerinin hepsi yalnız FastAPI'nin kendi route'larını (auth/approvals) kapsıyordu.

## Zorunlu güvenlik vakaları

1. Onay olmadan execution çağrısı Google mutate üretmez.
2. Süresi dolmuş, reddedilmiş, hash'i değişmiş veya başka principal'a ait onay reddedilir.
   Approval persistence, proposal ile aynı principal kapsamında değilse repository ve DB
   bütünlük katmanlarında reddedilir.
   Execution reservation da proposal'nın principal, customer ve onaylanmış hash snapshot'ıyla
   eşleşmeden kaydedilemez.
3. `customer_id` değiştirerek cross-user erişim mümkün değildir.
   Çakışan bir `proposal_id`, başka principal veya customer kapsamındaki mevcut öneriyi
   güncellemek ya da taşımak için kullanılamaz.
   Başka principal'a ait bir `execution_id` ile sonuç durumu veya Google request ID güncellenemez.
4. Yanlış principal credential'ı secrets manager'dan çözülemez.
5. Yeni campaign `PAUSED` değilse adapter isteği reddeder.
6. Audit başlangıç kaydı yazılamıyorsa mutate yapılmaz.
   Bu durumda execution `failed` olur; aynı idempotency anahtarıyla tekrar da provider mutate üretmez.
   Execution başlangıç ve sonuç audit olayları aynı `execution_id` ile ilişkilendirilir.
7. Prompt injection içeren reklam metni veya `rationale` gibi serbest metin alanları tool
   scope/argümanını, `customer_id`/`campaign_id`/`proposal_type`'ı ya da onay durumunu değiştirmez
   (`backend/tests/test_prompt_injection_safety.py`).
8. Duplicate idempotency key tek execution ve tek provider mutate üretir; aynı anahtar farklı
   principal, proposal veya payload kapsamıyla tekrar kullanılırsa fail-closed reddedilir.
9. Retryable/non-retryable Google Ads hataları doğru ayrılır; belirsiz mutate kör tekrarlanmaz.
   Adapter istisnası execution'ı `unknown` yapar ve `execution.completed` audit sonucu üretir;
   sonuç audit'i de yazılamazsa audit hatası asıl adapter hatasını exception chain içinde korur.
   Adapter non-terminal/geçersiz sonuç döndürürse de execution ve completion audit `unknown` olur.
10. Log capture içinde token, secret ve authorization header bulunmaz. Yapısal application
    logging henüz eklenmediği için (Faz 9.1 açık) bu bugün somut olarak iki şeyle kanıtlanır:
    secret taşıyan her nesnenin (`Settings`, `GoogleAdsCredentials`, `GoogleTokenResult`,
    `AuthorizationCode`/`AccessToken`/`RefreshToken`, `WebSession`, `WebSessionIssued`)
    `repr()`/`str()`'sinin raw secret'i asla yazdırmadığı (`backend/tests/test_secret_redaction.py`)
    ve sınıflandırılamayan bir transport hatasının orijinal metninin hiçbir zaman public
    `AdsApiError.message`'a taşınmadığı (`backend/tests/test_api_errors.py`).
11. MCP auth yokken `401` + doğru `WWW-Authenticate resource_metadata` döner; metadata resource exact match'tir.
12. PKCE, audience, issuer, redirect URI, refresh replay ve shared-client account-linking negatif testleri geçer.
13. Her tool annotation/title, ≤64 karakter ad ve read/write ayrımı review kriterlerine uyar.

## Mock politikası

- Her yeni Google Ads çağrısında başarılı sonuç, API hatası, timeout/rate limit ve ownership testi bulunur.
- Mock, resmi client'ın çağrı imzasına yakın tutulur; uydurma basitleştirilmiş API davranışı kullanılmaz.
- Test fixture'ları açıkça sahte ID ve metinler taşır; üretim dump'ı kullanılmaz.
- Model cevabı deterministik fixture/schema ile test edilir; canlı Anthropic çağrısı CI kalite kapısı değildir.

## Bilinen upstream test gürültüsü — MCP SDK ResourceWarning

`backend/tests/test_mcp_integration.py` çalışırken bazı testlerde `ResourceWarning: Unclosed
<MemoryObjectReceiveStream ...>` uyarıları görülür. Kök neden `PYTHONTRACEMALLOC=25` ile alınan
allocation trace'lerle doğrulandı: her iki sızıntı noktası da (`_handle_post_request`'in SSE dalı
ve `_handle_get_request`) tamamen kurulu `mcp` paketinin kendi kaynağındadır
(`site-packages/mcp/server/streamable_http.py`, sıfır tamponlu `SSEEvent` memory object stream
oluşturan `anyio.create_memory_object_stream` çağrıları). AddObserver'ın kendi ASGI middleware'leri (`backend/src/app.py`,
`backend/src/mcp/auth_bridge.py`) scope/receive/send'i olduğu gibi ileri taşır; hiçbir anyio stream
oluşturmaz — bu nedenle bizim lifecycle hatamız değildir.

- Kurulu sürüm: `mcp==1.28.1` (PyPI, yayın tarihi 2026-06-26).
- Bu sızıntı sınıfı [modelcontextprotocol/python-sdk#1991](https://github.com/modelcontextprotocol/python-sdk/pull/1991)
  ile kısmen giderildi (2026-02-11'de merge edildi, `1.28.1` bu commit'i içerir); ancak o düzeltme
  yalnız *exception* yolunda `sse_stream_reader.aclose()` ekledi, normal tamamlanma yolunda
  koşulsuz bir `finally` kapatması yoktu.
- Koşulsuz `finally: await sse_stream_reader.aclose()` düzeltmesi hem `_handle_post_request`
  hem `_handle_get_request` için [modelcontextprotocol/python-sdk#2934](https://github.com/modelcontextprotocol/python-sdk/pull/2934)
  (commit `a5271423`, 2026-06-22) ile trunk'a girdi — ama bu commit `1.28.1` paketinde **yok**
  (yerel kurulu kaynak doğrulandı: `finally` bloğu eksik). Depo şu an paralel iki hat yayınlıyor
  (`1.28.x` bakım hattı ve `2.0.0aX`/`2.0.0bX` ön sürüm hattı); tam düzeltme henüz `1.28.x` bakım
  hattında stabil bir patch olarak yayınlanmadı.
- **Karar:** Transport kodu kopyalanmadı/vendor edilmedi; global `ResourceWarning` suppression
  eklenmedi (bu, bizim kodumuzdaki gerçek bir kaynak sızıntısını da gizler). Uyarılar test
  çıktısında bilinen, aksiyon gerektirmeyen gürültü olarak kabul edilir; CI bu uyarı yüzünden
  fail etmez. Minimum izlenecek sürüm: commit `a5271423`'ü içeren ilk stabil `mcp` yayını (bir
  sonraki `1.28.x` patch'i veya `2.0` GA). Aylık bağımlılık gözden geçirmesinde `pip show mcp`
  ile kurulu sürüm kontrol edilip bu commit'i içeriyorsa bu bölüm kaldırılır.

## Erişilebilirlik ve performans

- Otomatik axe benzeri tarama manuel klavye/screen reader kontrolünün yerine geçmez.
- Kritik onay akışı 320 CSS px, %200 zoom ve reduced motion ile test edilir.
- Quota ve concurrency davranışı kontrollü yük testinde doğrulanır; üretim Google hesabına yük testi yapılmaz.

## DAST ve mutation testing kapsamı (Faz 13.6)

### DAST

- **Araç:** [OWASP ZAP](https://www.zaphq.org/) (baseline + authenticated full scan) — mevcut
  `docs/SECURITY.md`/`docs/TESTING.md` araştırmasının zaten dayandığı OWASP Web Security Testing
  Guide ailesiyle aynı ekosistem, CI'da headless çalışabilir.
- **Kapsam:** unauthenticated yüzey (discovery/`.well-known`/`/healthz`/`/readyz`/OAuth `/authorize`,
  `/token` hata yolları) + authenticated yüzey (`/mcp` bearer, `/approvals` web session) ayrı taranır;
  `/disconnect` gibi destructive endpoint'ler yalnız ayrılmış test principal'ıyla ve az sayıda
  kontrollü istekle taranır (fuzzing'in gerçek kullanıcı verisini/oturumunu etkilemesi engellenir).
  Test verisi sınırı: yalnız Faz 12.3'teki ayrılmış test principal/hesabı; gerçek müşteri verisi asla
  tarama trafiğine girmez.
- **Bugünkü durum: BLOKE.** Gerçek bir public/staging dağıtımı yok (`docs/OPERATIONS.md` "Faz 13.1"),
  ZAP gibi bir DAST aracı yalnız gerçekten çalışan bir HTTP yüzeyine karşı anlamlıdır; production'a
  saldırı testi kesinlikle yapılmaz. Bugün var olan kanıt, OAuth redirect/SSRF/CSRF/CORS/injection/
  session/rate-limit kontrollerinin **kod seviyesinde** zaten test edildiğidir (`docs/SECURITY.md`
  "Tehdit envanteri" T2-T9, T13 satırları ve karşılık gelen testler) — bu, dinamik/black-box bir
  taramanın yerini tutmaz (gerçek HTTP/TLS katmanı, request smuggling, verbose hata sayfası gibi
  yalnız çalışan bir sunucuya karşı görülebilecek sınıfları kapsamaz) ama tamamlayıcıdır. Gerçek DAST
  koşusu `todo.md` 10.4/10.5/10.7 (hosting) tamamlanıp bir staging ortamı canlandığında yapılabilir.

### Mutation testing

- **Araç:** [`mutmut`](https://mutmut.readthedocs.io/) Windows'ta native çalışmıyor ("please use
  WSL", bu ortamda WSL kurulu değil) — bu proje geliştiricileri Windows'ta olabileceğinden
  [`cosmic-ray`](https://cosmic-ray.readthedocs.io/) seçildi; aynı iş için platform-bağımsız çalışıyor.
- **Değer kanıtı (2026-07-22, `src/approval/payload_schema.py` + `test_approval_payload_schema.py`
  üzerinde gerçek bir koşu):** başlangıç mutant hayatta kalma oranı **%65.49** (226 mutant, 148
  survived). İnceleme iki gerçek kategori ortaya çıkardı: (1) `ReplaceBinaryOperator_BitOr_*` (110
  mutant) tamamen **denk (equivalent) mutant** — cosmic-ray'in operatör seti `str | None` gibi PEP 604
  tip birleşimlerindeki `|`'yi aritmetik bit-or sanıp mutasyona uğratıyor; `from __future__ import
  annotations` sayesinde anotasyonlar hiç çalışma zamanında değerlendirilmediğinden bu mutasyonların
  davranışsal etkisi yok. (2) Geri kalan `NumberReplacer`/`ReplaceComparisonOperator` mutantları
  **gerçek test boşluklarıydı**: `PROPOSAL_SCHEMA_VERSION`/`MAX_CAMPAIGN_ID_DIGITS`/`MAX_EVIDENCE_REFS`/
  `MAX_EVIDENCE_REF_LENGTH` sabitleri hiçbir yerde kendi gerçek değerine (`1`/`19`/`20`/`128`) değil,
  yalnız kendilerine göre (`MAX_X + 1`) test ediliyordu — sabitin kendisi değişse bile testler geçmeye
  devam ederdi. Daha ciddisi: `_budget_change`'deki `current_budget_amount_micros < 0` sınırı hiç
  test edilmemiş (`current_budget_amount_micros=0` ne kabul ne red olarak denenmemiş) ve
  `evidence_refs` doğrulaması (`MAX_EVIDENCE_REFS`/`MAX_EVIDENCE_REF_LENGTH`/tekrar/boş-string
  reddi) bu dosyada **hiç** test edilmiyordu. Bu 9 test eklendi
  (`test_budget_update_accepts_zero_current_amount`, `test_budget_update_rejects_negative_current_amount`,
  dört `evidence_refs` testi, `test_invalid_risk_is_rejected`, `test_security_relevant_constants_are_pinned`)
  ve oran **%53.54**'e düştü (226 mutant, 121 survived) — kalan survivor'ların çoğu hâlâ BitOr denk
  mutantları; birkaçı `>`/`>=` tam sınır ve `!=`/`is not` (küçük tamsayılarda CPython int-cache nedeniyle
  fiilen denk) gibi düşük öncelikli kalan sınır durumlarıdır, bu turda daha fazla kovalanmadı.
- **Kapsam kararı:** mutation testing her PR'ı kapatan bir zorunlu kapı **olmaz** — her mutant tam bir
  test-dosyası yeniden çalıştırması gerektirdiğinden (bu küçük, saf-unit dosyada bile 226 mutant ~20
  saniye sürdü; büyük/DB-bağımlı dosyalarda çok daha yavaş olur) PR başına maliyeti orantısızdır.
  Bunun yerine: (1) `ReplaceBinaryOperator_BitOr_*` operatör ailesi bu kod tabanı için hariç tutulacak
  şekilde config'e eklenir (denk mutant gürültüsünü ortadan kaldırır); (2) yalnız en yüksek değerli,
  güvenlik-kritik, saf-mantık modüllerine uygulanır: `approval/payload_schema.py` (bu turda yapıldı),
  `auth/domain.py` (PKCE/authorization code/token state machine), `db/oauth_store.py::rotate`/
  `revoke_family` (refresh-token reuse tespiti); (3) periyodik/manuel bir denetim aracı olarak çalışır
  (aylık dependency review'ına eklenebilir), CI'ın her PR'da çalışan zorunlu kapısına değil. Bu karar
  `todo.md` 10.2 CI pipeline'ına ayrı bir iş akışı değişikliği gerektirmeden şimdilik belgeyle
  kayıtlıdır; gerçek CI entegrasyonu ayrı bir takip maddesidir.

## Erişilebilirlik ve performans

- Otomatik axe benzeri tarama manuel klavye/screen reader kontrolünün yerine geçmez.
- Kritik onay akışı 320 CSS px, %200 zoom ve reduced motion ile test edilir.
- Quota ve concurrency davranışı kontrollü yük testinde doğrulanır; üretim Google hesabına yük testi yapılmaz.

## Tamamlanma tanımı

Kod, test, ilgili belge, migration/rollback ve gözlemlenebilirlik birlikte teslim edilmedikçe iş tamamlanmış sayılmaz.

## Açık sorular

- Google Ads test hesabıyla çalışan opt-in contract testlerinin ortamı ve sıklığı.
- UI teknoloji seçimine göre accessibility/E2E araçları.
- ~~DAST ve mutation testing'in hangi fazda kalite kapısına alınacağı~~ — **çözüldü (2026-07-22):**
  bkz. "DAST ve mutation testing kapsamı (Faz 13.6)". DAST gerçek staging'e kadar bloke; mutation
  testing periyodik/manuel bir denetim aracı olarak (CI zorunlu kapısı değil) `payload_schema.py`/
  `auth/domain.py`/`db/oauth_store.py`'a uygulanacak şekilde kararlaştırıldı.

## Güncelleme geçmişi

- 2026-07-22 — Faz 13.6: DAST scope/araç kararı (OWASP ZAP, gerçek staging'e kadar bloke) ve mutation
  testing değerlendirmesi eklendi. `cosmic-ray` ile `approval/payload_schema.py` üzerinde gerçek bir koşu
  yapıldı (mutmut Windows'ta WSL istiyor, bu ortamda yok): başlangıç %65.49 survival, 110 mutant BitOr/
  PEP-604-tip-birleşimi denk mutant çıktı, kalanı gerçek boşluktu (self-referential sabit testleri,
  test edilmemiş `evidence_refs` doğrulaması, test edilmemiş bütçe-negatif sınırı). 9 yeni test eklendi,
  survival %53.54'e düştü. Kapsam kararı: CI zorunlu kapısı değil, periyodik/manuel denetim; hedef
  modüller `payload_schema.py` (yapıldı), `auth/domain.py`, `db/oauth_store.py`.
- 2026-07-22 — Faz 13.2: staging henüz yok, bu yüzden yerel bir uçtan uca zincir testi eklendi
  (`test_mcp_integration.py::test_full_chain_connect_accounts_reporting_proposal_approval_disconnect`).
  Bu, mounted `/mcp` ASGI uygulamasında `CorrelationIdMiddleware` davranışını doğrulayan ilk testtir --
  gerçek bir boşluk olarak bulundu (önceki correlation-id testlerinin tamamı yalnız FastAPI route'larını
  kapsıyordu). Gerçek staging'de test etme adımı hosting sağlayıcısı seçilmeden (`todo.md` 10.4/10.5/10.7)
  yapılamaz; `todo.md` 13.2 bu yüzden `[ ]` kaldı.
- 2026-07-22 — uv frozen lock, Python 3.11/3.13 CI matrisi, ayrı required check'ler ve statik delivery
  contract testleri eklendi; dependency remediation akışı belgelendi.
- 2026-07-22 — Faz 7.6: Browser E2E + otomatik erişilebilirlik aracı olarak Playwright + axe-core
  seçildi ve `backend/tests/test_e2e_approvals_playwright.py` eklendi (bkz. "Test piramidi" → E2E).
  `backend/pyproject.toml` dev extras'ına opsiyonel `playwright`/`axe-core-python` eklendi.

- 2026-07-22 — Faz 7.4: `GET /disconnect` onay ekranı için oturum gerektiren erişim ve etki
  özeti içeriği (bağlı hesap sayısı, credential silinme uyarısı, form action) doğrulayan
  testler eklendi (`backend/tests/test_approvals_http.py`).

- 2026-07-22 — Faz 7.1/7.2: `/approvals` tam önizleme (hesap, işlem, kaynak, mevcut/önerilen değer,
  gerekçe, kaynak metrikler, risk, son geçerlilik, "henüz uygulanmadı" durumu) ve WCAG 2.2 AA
  temel katmanı (`lang`, skip link, `main`/`nav` landmark, öneri başına `<h2>`+`aria-labelledby`,
  görünür focus, `prefers-reduced-motion`, light/dark kontrast) için regresyon testleri eklendi
  (`backend/tests/test_approvals_http.py`): tam bağlam görünürlüğü, hostile rationale escape'i,
  erişilebilir doküman yapısı.

- 2026-07-22 — Faz 9 için JSON log injection/redaction/pseudonymization, OpenTelemetry allowlist,
  pre-start/post-shutdown readiness, append-only audit migration ve beş secretsiz incident drill testi eklendi.

- 2026-07-19 — Connector authorization transaction repository seçimi için production PostgreSQL
  unit-of-work lifecycle ve hata rollback contract testleri eklendi (`test_postgres_authorize_routes.py`).
  Repository contract stale consent geçişini reddeder; opsiyonel canlı PostgreSQL suite iki connection/barrier
  ile eşzamanlı consent yarışında yalnız bir kazanan olduğunu doğrular.

- 2026-07-19 — Opsiyonel PostgreSQL suite'e iki gerçek pooled connection + thread barrier kullanan duplicate
  execution reservation yarışı eklendi. `ADDOBSERVER_POSTGRES_TEST_DSN` yoksa güvenli biçimde skip edilir.
- 2026-07-19 — Aynı canlı suite'e authorization-code tek tüketim, refresh-token replay sonrası aile revoke
  ve eşzamanlı approval kararında tek approval/audit yazımı yarış testleri eklendi.

- 2026-07-19 — PostgreSQL `refresh_token.family_id`, domain'in ürettiği URL-safe opaque token
  biçimiyle eşleşmesi için UUID yerine text yapıldı ve şema regresyon testi eklendi.
- 2026-07-19 — PostgreSQL sürücüsü production bağımlılığına taşındı; bozuk `DATABASE_URL` hata
  mesajının DSN credential'ını açığa çıkarmadığını doğrulayan runtime regresyon testi eklendi.

- 2026-07-18 — Faz 4.2: PostgreSQL production şeması için SQLAlchemy metadata ve Alembic başlangıç
  migration'ı eklendi. `backend/tests/test_sqlalchemy_schema.py`, tablo envanterini, Alembic head
  revision'ını, principal-scoped kolon zorunluluğunu, composite proposal/principal FK'leri,
  idempotency unique constraint'ini ve PostgreSQL DDL'de UUID/JSONB/timestamptz kullanımını doğrular.
  Alembic offline SQL üretimi kalite komutlarına eklendi.
- 2026-07-18 — Faz 4.3 ilk artış: `backend/tests/test_sqlalchemy_schema.py`, RLS revision'ının
  principal-scoped tablo envanterini, `ENABLE` + `FORCE ROW LEVEL SECURITY` policy contract'ını ve
  transaction-local principal context helper'ının UUID doğrulamasını kapsayacak şekilde genişletildi.
  `backend/tests/test_postgres_rls_integration.py`, `ADDOBSERVER_POSTGRES_TEST_DSN` verildiğinde canlı
  PostgreSQL üzerinde cross-principal CRUD, pool reuse ve test rolünün `BYPASSRLS`/superuser olmaması
  vakalarını; ayrıca iki pooled connection ile aynı revocation job'ı için `FOR UPDATE SKIP LOCKED`
  claim yarışında yalnız tek kazanan/attempt artışı/lease ilerlemesini çalıştırır. Bu ortamda DSN olmadığı
  için canlı test skip eder ve 4.3/4.4 tamamlanma kapısı
  açık kalır. `backend/tests/test_postgres_runtime.py`, `db/postgres.py` production helper'ının
  PostgreSQL-only `DATABASE_URL`, secret redaction ve principal transaction set/cleanup/rollback sırasını
  canlı DB gerektirmeden kanıtlar. `backend/tests/test_postgres_repository.py`, ilk SQLAlchemy repository
  diliminin (`principal`, `oauth_client_grant`, `ads_account`, `oauth_credential`, `proposal`,
  `approval`, `execution`, `audit_event`) SQLite prototip contract'ını commit etmeden taşıdığını
  kanıtlar.
- 2026-07-18 — Faz 3.6: İki gerçek kusur bulunup düzeltildi (bkz. `docs/AUTH.md` ve
  `docs/ERROR_HANDLING.md` "Güncelleme geçmişi" için tam detay). (1) AUTH-class bir Google Ads
  hatası (2SV, revoked/expired token, izin iptali) credential'ı hiç pasifleştirmiyordu --
  `mcp/credentials.py::deactivate_credential_on_auth_failure` eklendi ve `mcp/tools.py`'nin üç
  reporting tool'u ortak `_fetch_report_page` üzerinden buna bağlandı. Kanıt:
  `backend/tests/test_mcp_credentials.py::DeactivateCredentialOnAuthFailureTests` (birim) +
  `backend/tests/test_mcp_integration.py::test_auth_class_tool_failure_deactivates_the_credential`
  (gerçek MCP tool-call zinciri, ikinci çağrının Google'a hiç ulaşmadan reddedildiğini kanıtlıyor).
  (2) Google'ın çoklu-scope onay ekranında `adwords` reddedilip diğer scope'lar kabul edilirse
  callback yine de başarılı bir `code` ile dönüyordu ve işlevsiz bir credential kalıcı hale
  getiriliyordu -- `GoogleTokenResult.granted_scopes` eklendi, `google_callback` artık bunu
  `access_denied` olarak ele alıyor. Kanıt:
  `backend/tests/test_auth_authorization_flow_http.py::ScopeDenialAtGoogleCallbackTests` (3 test:
  kısmi red, tam onay, `scope` alanı hiç dönmeyince "granted == requested" varsayımı).
- 2026-07-18 — Faz 3.5: `backend/tests/test_google_oauth.py` eklendi (11 test) --
  `GoogleWebFlowOAuthClient` (`auth/google_oauth.py`) ilk kez doğrudan test edildi; önceden yalnız
  `FakeGoogleOAuthClient` test double'ı üzerinden dolaylı egzersiz ediliyordu. Yalnız gerçek ağ
  round-trip'i gerektiren iki nokta (`Flow.fetch_token`, `google.oauth2.id_token.verify_oauth2_token`)
  stub'landı; resmi `google-auth-oauthlib` kütüphanesinin `credentials_from_session` dönüşüm
  mantığı gerçek çalıştı. Kapsam: `access_type=offline`/`prompt=consent`, exact redirect_uri,
  `state` echo, restricted `adwords` scope (var/yok), eksik refresh/id token'ın fail-closed
  reddi, subject'in doğrulanmış claim'den geldiği, eksik `sub` reddi, imza doğrulama hatasının
  yutulmadan yayılması. `docs/AUTH.md` "Google OAuth" bölümüne "accessible account linking"in
  henüz uygulanmadığını (`todo.md` 5.1 açık) netleştiren bir not eklendi; bu maddenin dışında
  bırakıldı.
- 2026-07-18 — Faz 3.4: `backend/tests/test_token_lifecycle.py` eklendi (5 test) ve bu sırada
  `db/oauth_store.py::TokenRepository.rotate`'te bir eşzamanlılık kusuru bulunup düzeltildi
  (bkz. `docs/AUTH.md` "Connector OAuth" ve "Güncelleme geçmişi"): eski kod aynı hâlâ-aktif
  refresh token'ı eşzamanlı iki çağrının rotate etmesi durumunda ikisinin de başarılı olmasına
  izin veriyordu (reuse-detection atlanıyordu); `ConcurrentAuthorizationCodeClaimTests`
  (test_oauth_store.py, Faz 3.3) ile aynı iki-thread/iki-bağımsız-bağlantı deseniyle
  `ConcurrentRefreshRotationTests` bunu önce reprodüklüyor, düzeltmeden sonra iki eşzamanlı
  çağrıdan yalnız birinin başarılı olduğunu ve kaybedenin TÜM aileyi (kazananın yeni token'ı
  dahil) iptal ettiğini kanıtlıyor. Ayrıca: 600s access-token TTL'sinin ilk kez gerçek bir HTTP
  isteği üzerinden (önceden yalnız saf fonksiyon seviyesinde) uygulandığı; disconnect'in bir
  principal'ın bugüne kadar yetkilendirdiği HER `client_id`'nin token ailesini iptal ettiği
  (önceden yalnız tek-client senaryosu test ediliyordu); ve `oauth_client_grant`'ın daha önce
  kaydedilmiş geniş bir scope'un sonraki dar bir yetkilendirmeye sızmasına izin vermediği
  (scope narrowing) doğrulandı.
- 2026-07-18 — Faz 3.1: `backend/tests/test_oauth_metadata_contract.py` eklendi (11 test).
  RFC 9728 protected-resource metadata (`resource`/`authorization_servers` tam eşleşmesi,
  path-suffixed varyant, `Cache-Control: no-store`) ve RFC 8414 authorization-server metadata
  (issuer/endpoint'ler, PKCE S256 zorunluluğu, desteklenen grant/response type, CIMD desteği,
  `registration_endpoint`'in yokluğu) ilk kez doğrudan JSON gövdesi üzerinden test edildi;
  önceden yalnız 401 `WWW-Authenticate` header'ının metadata URL'ine işaret ettiği doğrulanıyordu.
  Ayrıca `create_app`'in yeni HTTPS-in-production fail-closed kontrolü (`docs/AUTH.md`) üç testle
  kapsandı: `local` dışı ortamda `http://` reddi, `local` ortamda `http://` kabulü, `local` dışı
  ortamda `https://` kabulü.
- 2026-07-18 — Zorunlu güvenlik vakası #12 (Faz 3.3, authorization transaction hardening)
  için uçtan uca `backend/tests/test_auth_authorization_flow_http.py` eklendi: gerçek ASGI
  uygulaması üzerinden tam `/authorize` → `/authorize/consent` → `/google/callback` →
  `/token` zincirinin state/PKCE/redirect_uri/resource binding'ini, authorization code'un
  tek kullanımlıklığını (replay), çapraz istemci (confused-deputy) redemption reddini,
  açık redirect reddini ve süre dolumu reddini kanıtlıyor. Bu test, `auth/server.py::
  google_callback`'in `complete_transaction`'ı `issue_authorization_code`'dan önce
  çağırdığı için Google onayından sonraki HER gerçek callback'i başarısız kılan gerçek bir
  kusuru buldu (bkz. `docs/AUTH.md` "Güncelleme geçmişi"); sıra düzeltildi. Eşzamanlı
  redeem atomikliği (`UPDATE ... WHERE consumed_at IS NULL`) `backend/tests/
  test_oauth_store.py::ConcurrentAuthorizationCodeClaimTests` içinde aynı dosya-tabanlı
  sqlite DB'sine iki bağımsız thread/connection ile gerçek bir race olarak test edildi.
- 2026-07-18 — CIMD SSRF guard'ı için Content-Type doğrulaması eklendi (yalnız `application/json`
  kabul edilir; `text/html` gibi geçerli-JSON-baytlı-ama-yanlış-Content-Type'lı cevaplar reddedilir)
  ve önceden testsiz olan IPv6 SSRF senaryoları için regresyon testleri eklendi
  (`backend/tests/test_auth_cimd.py`): literal `::1`/unique-local (`fdxx::/8`)/link-local (`fe80::/10`)
  adresler, IPv4-mapped (`::ffff:a.b.c.d`) loopback/link-local adresler, NAT64 Well-Known-Prefix
  (`64:ff9b::/96`) -- embettiği adres ne olursa olsun (loopback/link-local/public dahil) tamamen
  reddedildiği, çünkü bu önek uzun süredir `is_reserved` olan `::/8` bloğunun içinde kalıyor --
  encoded/alternate-form (hex/oktal/decimal/percent-encoded) host metinlerinin yalnız resolver'ın
  gerçekten döndürdüğü IP'ye göre değerlendirildiği ve userinfo authority-confusion URL'lerinin
  (`https://decoy@attacker/x`) `@`'den sonraki gerçek host'a çözüldüğü. Bu vakaların hiçbirinde kod
  değişikliği gerekmedi -- mevcut `is_private`/`is_reserved` kontrolü ve stdlib `urlsplit` zaten
  doğru davranıyordu; eksik olan yalnız regresyon testiydi (Faz 3.2).
- 2026-07-18 — Zorunlu güvenlik vakası #10 (log capture) için regresyon eklendi (Faz 2.2):
  `backend/tests/test_secret_redaction.py`, secret taşıyan yedi dataclass'ın (`Settings`,
  `GoogleAdsCredentials`, `GoogleTokenResult`, `AuthorizationCode`, `AccessToken`,
  `RefreshToken`, `WebSession`, `WebSessionIssued`) `repr()`/`str()`'sinin artık
  `dataclasses.field(repr=False)` sayesinde raw secret'i yazdırmadığını kanıtlıyor; henüz
  yapısal application logging olmadığı için gerçek risk "gelecekte eklenecek bir
  `logger.debug(...)`/f-string/yakalanmamış exception traceback'i bu nesnelerden birini tek
  seferde tüm secret'ıyla yazdırır" senaryosuydu. `backend/tests/test_api_errors.py`'a
  `classify_transport_error`'ın sınıflandırılamayan-exception dalının orijinal exception
  metnini asla public mesaja taşımadığını kanıtlayan bir regresyon eklendi.
- 2026-07-18 — Zorunlu güvenlik vakası #7 (prompt injection) için regresyon testleri eklendi
  (`backend/tests/test_prompt_injection_safety.py`): Google Ads'ten dönen `keyword_text`/
  `campaign_name` içine gömülü talimat metninin adapter ve gerçek MCP protokolü üzerinden
  değişmeden, yalnız kendi allowlist alanında opak veri olarak döndüğü; `prepare_proposal`'ın
  `rationale` alanına gömülü "başka customer_id/campaign_id/proposal_type kullan, onaysız uygula"
  talebinin, çağıranın gönderdiği doğrulanmış yapılandırılmış argümanları (ve `pending_approval`
  durumunu) hiç etkilemediği kanıtlandı. Kod değişikliği yok -- mevcut allowlist/ownership
  doğrulaması zaten yeterliydi, eksik olan yalnız bu regresyon testiydi.
- 2026-07-18 — Geliştirme kalite araçları (Faz 1.4) `docs/decisions/0003-dev-tooling.md` ile
  karara bağlandı: Ruff (format+lint), Pyright (type check, `basic` mod), pytest+pytest-cov
  (%80 coverage tabanı, mevcut `unittest.TestCase` testleri değişmeden çalışır), Bandit (SAST),
  detect-secrets (`.secrets.baseline`) ve pip-audit (dependency scan); sürümler
  `backend/pyproject.toml` → `[project.optional-dependencies].dev` ve `[tool.*]` bölümlerinde
  pinlendi/konfigüre edildi. Her araç gerçekten kuruldu ve repo üzerinde çalıştırılıp doğrulandı;
  bulunan iki gerçek hata (`auth/server.py` eksik `Settings` import'u, `api/retry.py` kullanılmayan
  `exc` bağlaması) ve 6 kullanılmayan import düzeltildi, 12 Bandit bulgusu satır bazlı gerekçeli
  `# nosec` ile kapatıldı. `pip-audit` bu oturumun sandbox Python kurulumunda `venv` stdlib modülü
  eksik olduğu için çalıştırılamadı (bkz. "Kalite kapısı"); pip-audit dışındaki tüm araçlar canlı
  çalıştırma kanıtıyla doğrulandı. Mevcut kodun henüz `ruff format`/`ruff check --fix` ve
  `pyright` tip anotasyonu borcu `todo.md` 1.6/1.7'ye ayrı takip maddeleri olarak eklendi.
- 2026-07-18 — `tools/check_docs.py` dokümantasyon kalite kapısı genişletildi: ADR metadata
  (`Durum`/`Tarih`/`Sahip` zorunluluğu ve kanonik `Durum` değerleri), henüz kabul edilmemiş bir
  ADR'a referans veren belgeleri reddeden taslak-bağımlılık kontrolü, geçmiş `Sonraki gözden
  geçirme` tarihlerini işaretleyen stale-review kontrolü ve UTF-8 round-trip mojibake/Türkçe
  karakter bozulması taraması eklendi; her kural `backend/tests/test_check_docs.py` ile
  regresyon kapısına alındı.
- 2026-07-18 — MCP SDK ResourceWarning'i (Faz 0.3) allocation trace ile incelendi; sızıntının tamamen
  kurulu `mcp==1.28.1` paketinin `streamable_http.py` kaynağında olduğu, uygulama kodunda
  olmadığı doğrulandı. Upstream issue/PR referansları, minimum izlenecek sürüm ve geçici izleme
  kararı "Bilinen upstream test gürültüsü" bölümüne eklendi; transport kodu kopyalanmadı, global
  warning suppression eklenmedi.
- 2026-07-18 — Connector OAuth AS'ın `client_id`/`state`/`scope`/`code_challenge`/`code_verifier`
  sınır değerleri ve `transaction_id`/`state` opaque kimlikleri için negatif regresyon testleri
  eklendi: undersized/oversized/non-base64url PKCE değerleri (`backend/tests/test_auth_domain.py`),
  DNS/ağ çağrısından önce reddedilen aşırı büyük `client_id` (`backend/tests/test_auth_cimd.py`),
  ve HTTP seviyesinde `/authorize`, `/authorize/consent`, `/google/callback` için aşırı büyük
  `client_id`/`transaction_id`/`state` (`backend/tests/test_auth_server_http.py`).
- 2026-07-18 — CIMD fetch için DNS-rebinding TOCTOU regresyon testleri eklendi: resolver'ın tam olarak
  bir kez çağrıldığı, isteğin hostname yerine doğrulanmış IP'ye pinlendiği (IPv4 ve IPv6), karışık
  public/private yanıtın reddedildiği ve boş resolver yanıtının reddedildiği doğrulandı
  (`backend/tests/test_auth_cimd.py`).
- 2026-07-18 — `prepare_proposal` payload doğrulaması için aşırı uzun/kontrol karakterli `rationale`,
  allowlist dışı `current_status` ve aşırı uzun `campaign_id` değerlerini reddeden birim regresyon
  testleri eklendi (`backend/tests/test_approval_payload_schema.py`).
- 2026-07-18 — Public `proposal_id` girdileri için boş, URL-safe olmayan ve aşırı uzun değerleri
  veri katmanına ulaşmadan reddeden HTTP, MCP, form ve birim regresyon testleri eklendi.
- 2026-07-18 — Approval formunda control-character içeren kararın proposal sorgusundan önce ve
  128 karakteri aşan CSRF girdisinin hash doğrulamasında reddedildiği negatif testlerle kanıtlandı.

- 2026-07-17 — Execution sonuç güncellemesinin principal filtresi ve cross-principal negatif testi eklendi.
- 2026-07-17 — Başlangıç audit hatasının execution'ı `failed` sonlandırdığı ve idempotent
  tekrarın provider'a gitmediği regresyon kapısına eklendi.
- 2026-07-17 — Execution→proposal ilişkisinde composite foreign key ile principal izolasyonu ve
  customer/hash snapshot doğrulaması eklendi.
- 2026-07-17 — Approval→proposal ilişkisinde uygulama kontrolü ve composite foreign key ile
  cross-principal bütünlük testi eklendi.
- 2026-07-17 — Çakışan proposal kimliğiyle cross-principal/cross-customer repository
  güncellemesini reddeden negatif testler eklendi.
- 2026-07-17 — Execution başlangıç/sonuç audit olaylarının aynı `execution_id` ile
  ilişkilendirilmesi test kapısına eklendi.
- 2026-07-17 — Belirsiz adapter istisnasında `unknown` completion audit kaydı ve audit yazma
  hatasında asıl provider hatasının exception chain ile korunması test kapısına eklendi.
- 2026-07-17 — Idempotency kapısı provider mutate tekilliğini ve principal/payload kapsam
  uyuşmazlığının fail-closed reddini kapsayacak şekilde genişletildi.
- 2026-07-17 — Audit başlangıç kaydı başarısızken provider mutate çağrısının yapılmadığı ve belirsiz
  adapter hatasının kör retry yerine `unknown` execution ürettiği uygulama sınırı testleri eklendi.
- 2026-07-17 — Approval domain için onaysız execution, cross-principal, değişmiş hash, expiry ve başarılı rezervasyon testleri eklendi.
- 2026-07-17 — ASGI lifespan shutdown'ın sqlite bağlantısını kapattığı lifecycle testi eklendi; HTTP/app testleri
  kapanış sonrası DB bağlantısına güvenmeyecek şekilde düzenlendi.
- 2026-07-17 — Public ingress body sınırı için `/mcp` üzerinde 413/400 ve başlıksız streamed-body negatif lifecycle testleri eklendi.
- 2026-07-17 — Public HTTP security header testi ve `/logout` CSRF pozitif/negatif testleri eklendi.
- 2026-07-17 — `X-Correlation-ID` üretme/echo etme, sanitize etme ve problem response `correlation_id` testi eklendi.
- 2026-07-17 — İnsan approval decision kaydının audit ile atomik yazıldığını ve audit hatasında rollback olduğunu doğrulayan testler eklendi.
- 2026-07-17 — `/disconnect` audit kaydının kabul edilen HTTP correlation ID ile yazıldığını doğrulayan test eklendi.
- 2026-07-17 — Bağımlılıksız ilk test runner'ı ve otomatik dokümantasyon kapısı tanımlandı.
- 2026-07-17 — Güvenlik negatif testleri, mock politikası ve WCAG kalite kapıları tanımlandı; zorunlu belge formatı eklendi.
