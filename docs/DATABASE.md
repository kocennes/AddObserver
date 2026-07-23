# Veritabanı tasarımı

**Durum:** Kabul edildi  
**Son gözden geçirme:** 2026-07-17  
**Sonraki gözden geçirme:** 2026-10-17

## Amaç

`DATA_MODEL.md` içindeki varlıkları public kullanıcı izolasyonu, tutarlılık, migration ve audit gereksinimlerini
uygulayabilecek somut bir veritabanı yaklaşımına dönüştürmek.

## Araştırma

- PostgreSQL [Row Security Policies](https://www.postgresql.org/docs/current/ddl-rowsecurity.html), RLS
  açıkken uygun policy yoksa default-deny uygular. Ancak superuser, `BYPASSRLS` rolleri ve normalde tablo
  sahibi RLS'yi atlar; tablo sahibine `FORCE ROW LEVEL SECURITY` uygulanabilir.
- PostgreSQL [CREATE POLICY](https://www.postgresql.org/docs/current/sql-createpolicy.html), okunan satırlar
  için `USING`, eklenen/güncellenen satırlar için `WITH CHECK` koşullarını ayırır; birden çok permissive
  policy varsayılan olarak `OR` ile birleşebilir.
- [OWASP Multi-Tenant Security](https://cheatsheetseries.owasp.org/cheatsheets/Multi_Tenant_Security_Cheat_Sheet.html),
  her sorgu/cache/storage yolunda tenant bağlamı, veri katmanında ownership kontrolü ve DB seviyesinde RLS
  gibi defense-in-depth önerir.
- PostgreSQL [transaction isolation](https://www.postgresql.org/docs/current/transaction-iso.html) belgesi,
  varsayılan Read Committed seviyesinde iki ardışık SELECT'in farklı veri görebileceğini açıklar; onay
  tüketimi/idempotency gibi yarışlar constraint ve satır kilidiyle çözülmelidir.

## Karar

- DB: desteklenen güncel PostgreSQL major sürümü; production'da yönetilen, private network erişimli ve
  at-rest encryption açık. Kesin sağlayıcı deployment ADR'sinde seçilir.
- Faz 1 dahil ortak şema + zorunlu `principal_id` + RLS kullanılır. Kullanıcı başına DB/şema public connector
  için gereksiz operasyon yüküdür; ölçek/sharding ihtiyacı metriklerle yeniden değerlendirilir.
- Runtime DB rolü tablo sahibi, superuser veya `BYPASSRLS` olamaz. Migration owner rolü ayrıdır ve uygulama
  tarafından kullanılamaz. Principal-scoped tablolarda `ENABLE` + `FORCE ROW LEVEL SECURITY` uygulanır.
- Request transaction başında doğrulanmış principal UUID transaction-local setting/context olarak atanır.
  RLS policy hem `USING` hem `WITH CHECK` ile bu değeri zorunlu kılar. Repository metotları ayrıca açık
  `principal_id` alır; RLS uygulama filtresinin yerine geçmez.
- İlk RLS uygulaması `20260718_0002_enable_principal_rls` Alembic revision'ı ve
  `db/postgres_context.py` transaction-local context helper'ı ile eklendi. `db/postgres.py`,
  production `DATABASE_URL` değerinin PostgreSQL dialect'i olmasını zorlar, `pool_pre_ping` ile engine
  kurar ve her principal-scoped transaction başında RLS context'ini set edip çıkışta temizler. Faz
  4.3'ün tamamlanma kapısı için `ADDOBSERVER_POSTGRES_TEST_DSN` ile canlı PostgreSQL entegrasyon
  testinin çalıştırılması ve production repository/app yollarının bu helper'a bağlanması hâlâ gereklidir.
- İlk production repository dilimi `db/postgres_repository.py` ile `principal`, `oauth_client_grant`,
  `ads_account`, `oauth_credential`, `proposal`, `approval`, `execution` ve `audit_event` davranışlarını
  SQLAlchemy Core'a taşıdı. Bu adaptörler commit etmez; commit/rollback ve RLS context sınırı
  `db/postgres.py::principal_transaction` altında kalır.
- Composite foreign key/unique constraint kullanıcılar arası ilişkiyi DB seviyesinde reddeder. Para integer
  micros, zaman `timestamptz` UTC, dış ID UUID/ULID'dir.
- Proposal onayı ve execution kaydı tek transaction'da state/hash kontrolü, `SELECT ... FOR UPDATE` ve
  unique idempotency key ile rezerve edilir. Google ağı DB transaction'ı açıkken çağrılmaz; execution
  outbox/state machine ile `pending → applied|failed|unknown` ilerler.
- Credential revoke ve durable outbox enqueue aynı transaction'da, credential kimliği üzerinde idempotent
  ilerler. Worker principal-bound transaction'da due satırı `FOR UPDATE SKIP LOCKED` ile seçer, attempt
  sayısını artırıp `next_attempt_at` alanını lease olarak ileri taşır. Retry yalnız kısa güvenli hata kodu,
  completion ise `completed_at` yazar; tüm repository işlemleri RLS'ye ek olarak `principal_id` filtresi ister.
  Worker çekirdeği claim transaction'ını commit ettikten sonra vault'u çağırır; completion veya sanitize
  edilmiş `VAULT_UNAVAILABLE` retry sonucu ayrı ikinci kısa transaction'da yazılır. Scheduler/deploy
  tetikleyicisi altyapı kararına bağlıdır. Retry/completion update'i claim sırasında artırılan exact
  `attempts` generation'ını compare-and-set koşulu olarak kullanır; lease'i dolup daha yeni worker tarafından
  yeniden claim edilen iş, stale worker sonucu tarafından ezilemez.
- Audit normal CRUD tablolarından ayrı append-only tablo/rol kullanır. Runtime yalnız INSERT yapar;
  UPDATE/DELETE yetkisi yoktur. Uzun dönem WORM export sağlayıcısı açık karardır.
- Migration'lar ileri yönlü, numaralı ve review edilmiş olur. Expand→migrate/backfill→contract düzeni;
  destructive migration için yedek/restore testi ve ayrı onay gerekir.
- Connection pool her checkout/checkin'de principal context'ini temizler; context sızıntısı için özel test vardır.

## Açık sorular

- PostgreSQL sağlayıcısı ve desteklenen major sürüm.
- ORM/migration aracı: `docs/decisions/0001-backend-stack.md` ile SQLAlchemy 2 + Alembic olarak kapatıldı.
- SQLite prototipten PostgreSQL/Alembic başlangıç migration'ına geçiş planı
  `docs/decisions/0006-postgresql-migration-plan.md` ile kapatıldı.
- Audit retention, partitioning ve WORM export hedefi.
- RPO/RTO, PITR süresi ve region seçimi.

## Güncelleme geçmişi

- 2026-07-22 — `20260722_0007_append_only_audit` migration'ı `audit_event` satırlarında UPDATE/DELETE'i
  PostgreSQL trigger'ıyla tüm roller için reddeder. Uzun dönem WORM export/retention sağlayıcısı seçilmedi;
  ilgili açık karar korunur.

- 2026-07-19 — ADR-0007 ile disconnect DB–vault atomiklik boşluğu için credential revocation outbox kabul
  edildi; migration principal RLS, composite credential ownership ve credential başına tek iş constraint'i
  kurar. Repository atomik/idempotent enqueue ile principal-scoped lease/retry/completion davranışını ekler;
  canlı PostgreSQL claim yarışı, worker ve route wiring ayrı testli artış olarak kapalı kalır.

- 2026-07-19 — Connector authorization transaction durum geçişleri `pending → consented → completed`
  predecessor koşullu compare-and-set update kullanır; stale/ikinci ilerletme fail-closed reddedilir.
  Consent route'u okuma ve durum ilerletmeyi aynı kısa unit-of-work içinde yürütür.

- 2026-07-19 — Approval transition `pending_approval` koşuluyla compare-and-set hale getirildi; execution
  reservation idempotency yarışı conflict-safe INSERT + kazanan satır doğrulamasıyla kapatıldı. Canlı
  PostgreSQL iki-connection yarış kanıtı hâlâ zorunludur.
- 2026-07-19 — `web_session` için exact cookie-hash, SELECT-only bootstrap policy'si Alembic zincirine
  eklendi; hash context principal çözülür çözülmez temizlenir ve normal RLS context kurulur.

- 2026-07-17 — PostgreSQL, ortak şema + FORCE RLS, ayrık roller ve transactional outbox yaklaşımı seçildi.
- 2026-07-18 — ADR-0006 ile SQLite prototipin production kanıtı sayılmayacağı, SQLAlchemy metadata +
  Alembic başlangıç migration'ı, PostgreSQL entegrasyon testleri ve RLS/concurrency ayrımı kabul edildi.
- 2026-07-18 — Faz 4.3 ilk artış: principal-scoped tablolar için `ENABLE` + `FORCE ROW LEVEL SECURITY`
  migration'ı, transaction-local principal context helper'ı ve production PostgreSQL transaction helper'ı
  eklendi. İlk SQLAlchemy repository dilimi (`principal`, `oauth_client_grant`, `ads_account`,
  `oauth_credential`, `proposal`, `approval`, `execution`, `audit_event`) commit etmeyen adaptörlerle
  başladı; canlı PostgreSQL izolasyon testi DSN ile çalıştırılıp auth/token repository'leri ve production
  app wiring tamamlanana kadar 4.3 açık kalır.
- 2026-07-19 — Connector OAuth production dilimi `authorization_transaction` ve
  `authorization_code` repository'leriyle genişletildi. Transaction kimliği domain'in URL-safe opaque
  değerleriyle uyumlu `TEXT`, durum allowlist'i `pending/consented/completed` olarak düzeltildi;
  authorization code yalnız hash olarak saklanır ve koşullu update ile tek kez claim edilir.
- 2026-07-19 — `PostgresTokenRepository` access/refresh token hash saklama, atomik koşullu refresh
  rotation, replay halinde aile çapında revoke ve principal disconnect revoke davranışlarıyla eklendi.
  Repository DB transaction'ını commit etmez; timestamp dönüşleri domain'e daima UTC-aware verilir.
- 2026-07-19 — Approval-UI persistence dilimi `PostgresWebLoginStateRepository` ve
  `PostgresWebSessionRepository` ile eklendi: login state/session/CSRF değerleri yalnız hash olarak
  saklanır; state claim atomiktir; tekil ve principal-wide session revoke desteklenir.
- `/token` RLS bootstrap'ı `20260719_0003_authorization_code_bootstrap_rls` ile dar kapsamlı çözüldü:
  transaction-local SHA-256 code hash'iyle tam eşleşen tek `authorization_code` satırına yalnız `SELECT`
  izni veren ek policy principal'ı çözer; hash context hemen temizlenir, ardından normal principal context
  kurulup claim/update mevcut principal policy üzerinden yapılır. `db/postgres.py::authorization_code_transaction`
  bu sırayı tek transaction'da fail-closed uygular. Runtime rolüne `BYPASSRLS`, tablo sahipliği veya
  `SECURITY DEFINER` yetkisi verilmez. Canlı PostgreSQL entegrasyon testi production wiring öncesi kapıdır.
- ASGI route/MCP katmanında SQLite repository kurulumları tamamen PostgreSQL request transaction provider'a
  taşınana kadar `APP_ENVIRONMENT=production|prod` fail-closed başlangıç hatası verir. Böylece production
  config'de `DATABASE_URL` tanımlı olsa bile uygulamanın bunu sessizce yok sayıp yerel SQLite ile açılması
  engellenir; local/test prototip yolu etkilenmez.
- `db/postgres_uow.py`, production composition için request-scoped transaction sınırını ekledi. Bir unit of
  work içindeki tüm SQLAlchemy repository'leri aynı connection/transaction'ı paylaşır; doğrulanmış principal
  işlem sırasında bağlanabilir veya `/token` exact-hash bootstrap'ından türetilebilir. Başarıda context
  temizlenip tek commit, exception'da cleanup sorgusuna güvenmeden rollback uygulanır. Route/MCP çağrı
  noktalarının bu provider'a taşınması tamamlanana kadar production startup kapısı açık kalır.
- `20260719_0004_token_bootstrap_rls`, refresh grant ve bearer doğrulamasının principal bilinmeden başlayan
  RLS erişimini kapattı. Yalnız transaction-local SHA-256 token hash'iyle tam eşleşen access/refresh satırı
  `SELECT` edilebilir; principal çözüldükten sonra hash context temizlenip normal RLS context kurulur.
  Unit-of-work access ve refresh bootstrap metotlarını ayrı sunar; genel token tarama/yazma yetkisi vermez.
- Connector `/token` route'u production unit-of-work factory sağlandığında artık iki grant türünde de bu
  transaction sınırını kullanır: authorization code exact-hash bootstrap → atomik claim → access/refresh
  insert ve refresh exact-hash bootstrap → rotate aynı connection/transaction içindedir. Factory yoksa local
  SQLite test/geliştirme yolu korunur; diğer route/MCP yolları taşınana kadar production başlangıcı kapalıdır.
- 2026-07-22 — `auth/server.py::google_callback`'in Claude-client dalı (Google Ads refresh token'ını
  vault'a yazan ve `oauth_credential`/`oauth_client_grant`/`authorization_code`'a işleyen dal) production
  unit-of-work'e taşındı; bu, dual-path olmayan tek kalan SQLite-only yazma yoluydu (diğer tüm auth/API/MCP
  çağrı noktaları zaten dual-path'ti). `authorization_transaction` RLS'siz olduğundan ilk okuma principal
  bağlamadan yapılır; Google code exchange ve vault yazımı hiçbir açık DB transaction'ı içinde çalışmaz
  (ADR-0006) -- üç ayrı kısa transaction: (1) transaction oku, (2) principal `get_or_create`, (3) vault
  yazımından sonra `bind_principal` ile credential/grant/code yazımı ve transaction'ı `completed`'a
  taşıma. Yeni `backend/tests/test_postgres_google_callback_route.py`, bu sıralamayı, kısmi-scope
  reddinde vault'a hiç dokunulmadığını, tamamlanmış bir transaction'ın yeniden kullanılmasının yazma
  transaction'ını rollback ettiğini ve tam ASGI akışının (`/authorize` → `/authorize/consent` →
  `/google/callback`) sahte bir PostgreSQL backend'i üzerinden uçtan uca çalıştığını kanıtlıyor. Canlı
  PostgreSQL RLS izolasyon/concurrency kanıtı (`test_postgres_rls_integration.py`) bu makinede DSN
  olmadığı için hâlâ skip kalıyor; 4.3 bu yüzden açık kalmaya devam ediyor.
