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
- Composite foreign key/unique constraint kullanıcılar arası ilişkiyi DB seviyesinde reddeder. Para integer
  micros, zaman `timestamptz` UTC, dış ID UUID/ULID'dir.
- Proposal onayı ve execution kaydı tek transaction'da state/hash kontrolü, `SELECT ... FOR UPDATE` ve
  unique idempotency key ile rezerve edilir. Google ağı DB transaction'ı açıkken çağrılmaz; execution
  outbox/state machine ile `pending → applied|failed|unknown` ilerler.
- Audit normal CRUD tablolarından ayrı append-only tablo/rol kullanır. Runtime yalnız INSERT yapar;
  UPDATE/DELETE yetkisi yoktur. Uzun dönem WORM export sağlayıcısı açık karardır.
- Migration'lar ileri yönlü, numaralı ve review edilmiş olur. Expand→migrate/backfill→contract düzeni;
  destructive migration için yedek/restore testi ve ayrı onay gerekir.
- Connection pool her checkout/checkin'de principal context'ini temizler; context sızıntısı için özel test vardır.

## Açık sorular

- PostgreSQL sağlayıcısı ve desteklenen major sürüm.
- ORM/migration aracı: `docs/decisions/0001-backend-stack.md` ile SQLAlchemy 2 + Alembic olarak kapatıldı.
- Audit retention, partitioning ve WORM export hedefi.
- RPO/RTO, PITR süresi ve region seçimi.

## Güncelleme geçmişi

- 2026-07-17 — PostgreSQL, ortak şema + FORCE RLS, ayrık roller ve transactional outbox yaklaşımı seçildi.
