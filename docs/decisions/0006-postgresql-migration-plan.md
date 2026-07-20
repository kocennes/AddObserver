# ADR-0006: PostgreSQL migration planı ve test stratejisi

- Durum: Kabul edildi
- Tarih: 2026-07-18
- Sahip: Ürün sahibi onayıyla ajan (Codex)

## Bağlam

Mevcut kod tabanı bilinçli olarak `sqlite3` tabanlı, hızlı ve stdlib-only bir prototip şema kullanıyor
(`backend/src/db/schema.py`). Bu şema public connector'ın davranışını test etmek için yeterli olsa da
`docs/DATABASE.md` üretim kararını PostgreSQL + SQLAlchemy 2 + Alembic + FORCE RLS olarak veriyor.

SQLite prototipteki gerçek tablo envanteri `DATA_MODEL.md` çekirdek varlıklarıyla büyük ölçüde eşleşiyor:
`principal`, `oauth_client_grant`, `ads_account`, `oauth_credential`, connector OAuth transaction/code/token,
`web_login_state`, `web_session`, `proposal`, `approval`, `execution`, `audit_event` ve yerel geliştirmeye
özel `vault_secret`. Bilinçli farklar şunlar:

- SQLite şeması DB-level RLS, rol ayrımı, `timestamptz`, JSONB, composite FK derinliği, outbox locking ve
  partial/deferrable constraint gibi üretim kontrollerini uygulamaz.
- `analysis_run` henüz kodda yoktur; `todo.md` 6.4 ürün kararı olmadan başlangıç migration'ına eklenmez.
- `vault_secret` yalnız yerel `LocalEncryptedVault` içindir; üretim secrets manager/KMS seçimi 10.6'ya
  bağlıdır ve PostgreSQL production şemasına secret ciphertext kolonu olarak taşınmaz.
- Retention süreleri ve WORM audit hedefi legal/observability kararlarına bağlıdır; başlangıç migration'ı
  purge politikasını uygulamaz.

Bu karar, 4.2'de kalıcı model/migration yazmaya başlamadan önce geçiş sırasını ve test sınırını kabul eder.

## Seçenekler

**Seçenek A: SQLite prototipi üretim davranışı gibi genişletmek.**
Hızlıdır, fakat RLS/rol/transaction-local context gibi asıl güvenlik kontrollerini saklar. Public connector için
yanlış güven hissi üretir.

**Seçenek B: Hemen yalnız PostgreSQL'e geçip SQLite testlerini kaldırmak.**
Üretime daha yakındır, fakat hızlı unit test döngüsünü ve dependency-free local smoke yolunu kaybettirir.
Çoğu domain/repository testi gerçek PostgreSQL gerektirmeden çalışabilir.

**Seçenek C: Çift katmanlı geçiş.**
Domain ve repository davranış testleri SQLite üzerinde hızlı kalır; production-only kontroller PostgreSQL
entegrasyon testleriyle ayrı egzersiz edilir. SQLAlchemy 2 metadata tek kaynak olur, Alembic migration'ları
bu metadata'dan üretilir ama her migration elle review edilir.

## Karar

Seçenek C uygulanır.

1. SQLAlchemy 2 declarative/core metadata production şemasının tek kaynağı olur. Mevcut stdlib SQLite şeması
   kısa vadede local hızlı testleri destekler, fakat production davranışının kanıtı sayılmaz.
2. Alembic başlangıç migration'ı `principal`, `oauth_client_grant`, `ads_account`, `oauth_credential`,
   `authorization_transaction`, `authorization_code`, `access_token`, `refresh_token`, `web_login_state`,
   `web_session`, `proposal`, `approval`, `execution` ve `audit_event` tablolarını oluşturur. `analysis_run`
   ürün kararı gelene kadar, `vault_secret` ise production secrets manager kararı gelene kadar dahil edilmez.
3. PostgreSQL tipleri ve constraint'leri üretim sözleşmesini taşır: UUID dış kimlikler, UTC `timestamptz`,
   `jsonb` proposal/execution payloadları, integer micros, named constraints, principal-scoped composite FK'ler
   ve idempotency unique constraint'leri.
4. Principal-scoped tablolarda `principal_id` zorunludur; `audit_event.principal_id` yalnız sistem/global olaylar
   için nullable kalabilir. 4.3'te bu tablolara `ENABLE ROW LEVEL SECURITY` + `FORCE ROW LEVEL SECURITY` ve
   hem `USING` hem `WITH CHECK` policy'leri eklenir.
5. Runtime DB rolü migration owner, superuser, tablo sahibi veya `BYPASSRLS` olamaz. Migration owner rolü yalnız
   migration çalıştırır; uygulama rolü normal runtime için kullanılır.
6. Request transaction'ı açılır açılmaz doğrulanmış principal transaction-local setting olarak atanır. Pool reuse
   sırasında context temizliği zorunludur; SQLAlchemy pool reset/transaction rollback davranışına ek olarak
   explicit context-cleanup testleri yazılır.
   Connector `/token` code redemption bunun zorunlu bootstrap istisnasıdır: principal henüz bilinmediği için
   yalnız sunulan authorization code'un SHA-256 hash'i transaction-local setting'e yazılır; SELECT-only RLS
   policy yalnız tam eşleşen tek satırı görünür kılar. Principal bu satırdan öğrenildikten sonra hash setting'i
   temizlenir ve normal principal context kurulur. Runtime rolüne `BYPASSRLS` veya tablo sahipliği verilmez.
   Aynı exact-hash, SELECT-only bootstrap deseni refresh-token grant ve bearer access-token doğrulaması için
   `access_token`/`refresh_token` tablolarında uygulanır; token hash context principal çözülür çözülmez temizlenir.
7. Google Ads network çağrısı açık DB transaction içinde yapılmaz. Execution yolu ileride outbox/state machine
   olarak `pending -> applied|failed|unknown` ilerler; provider sonucu belirsizse kör retry yapılmaz.
8. Alembic akışı expand -> backfill/migrate -> contract olarak yürür. Destructive migration ayrı onay, backup ve
   restore kanıtı olmadan yazılmaz.

## Sonuçlar

- 4.2 artık SQLAlchemy modelleri ve başlangıç Alembic migration'ı yazabilir; RLS policy uygulaması 4.3'te ayrı
  ve PostgreSQL entegrasyon testleriyle yapılır.
- SQLite testleri hızlı regression paketi olarak kalır, fakat DB-level izolasyon veya transaction semantics
  kanıtı olarak kullanılmaz.
- PostgreSQL entegrasyon testleri 4.3/4.4'te en az şu vakaları kapsar: cross-principal SELECT/INSERT/UPDATE
  reddi, transaction-local principal context sızıntısı, runtime rolün migration/table-owner yetkisi olmaması,
  authorization code claim race, refresh rotation race, double approval ve duplicate execution reservation.
- `analysis_run`, retention/purge, WORM audit export, production KMS/secrets manager ve deployment provider
  kararları kendi backlog maddeleri tamamlanmadan başlangıç migration'ına gömülmez.
- Geri alma stratejisi: başlangıç migration'ı yeni production şema kurulumudur; canlı veri contract migration'ı
  başlamadan önce SQLite prototip şeması local test amacıyla korunabilir. Canlı veri taşınacak aşama ayrı bir
  migration/runbook ve restore tatbikatı gerektirir.

## Kaynaklar

- [PostgreSQL Row Security Policies](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)
- [PostgreSQL CREATE POLICY](https://www.postgresql.org/docs/current/sql-createpolicy.html)
- [PostgreSQL CREATE FUNCTION güvenlik notları](https://www.postgresql.org/docs/current/sql-createfunction.html)
- [SQLAlchemy 2 documentation](https://docs.sqlalchemy.org/)
- [SQLAlchemy connection pooling](https://docs.sqlalchemy.org/en/latest/core/pooling.html)
- [Alembic autogenerate](https://alembic.sqlalchemy.org/en/latest/autogenerate.html)
- [Alembic naming conventions](https://alembic.sqlalchemy.org/en/latest/naming.html)
- `docs/DATABASE.md`, `docs/DATA_MODEL.md`, `docs/SECURITY.md`
