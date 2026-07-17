"""Principal-scoped persistence for the connector (DATA_MODEL.md / DATABASE.md).

DATABASE.md'nin bağlayıcı kararı üretimde PostgreSQL + SQLAlchemy 2 + Alembic + FORCE ROW
LEVEL SECURITY'dir (docs/decisions/0001-backend-stack.md). Bu paket yalnız ilk stdlib-only
iskelet artışı için sqlite3 üzerinde çalışır; principal izolasyonu burada yalnız uygulama/
repository katmanında zorunlu parametre olarak uygulanır, DB seviyesinde RLS YOKTUR.

Proposal/Approval/ExecutionReservation iş kuralları burada TEKRARLANMAZ — bu modüller yalnız
``backend.src.approval.domain``'in ürettiği immutable nesneleri saklar/okur; onay, hash ve
süre doğrulaması tek kaynak olarak orada kalır.
"""
