"""SQL DDL for the local, dependency-free skeleton database.

DATABASE.md'nin bağlayıcı kararı üretimde PostgreSQL + FORCE ROW LEVEL SECURITY'dir. Bu şema
yalnız ilk stdlib-only iskelet artışı için sqlite3 üzerinde çalışır; principal izolasyonu
burada yalnız uygulama/repository katmanında zorunlu parametre olarak uygulanır, DB
seviyesinde RLS YOKTUR. Gerçek Postgres/Alembic migration'ı docs/decisions/0001-backend-stack.md
ile kaydedilen sonraki bir artıştır.

``proposal``/``approval``/``execution`` sütunları kasıtlı olarak ``backend.src.approval.domain``
dataclass alanlarıyla birebir eşleşir (`type`/`risk` gibi ek alanlar payload JSON'u içindedir).

``authorization_transaction``/``authorization_code``/``access_token``/``refresh_token`` aynı
şekilde ``backend.src.auth.domain`` dataclass alanlarıyla eşleşir (ADR-0002 -- elle yazılan
connector OAuth 2.1 AS). Kod/token değerleri asla ham saklanmaz; yalnız SHA-256 hash'i
(`backend.src.auth.domain.hash_token`) tutulur. ``vault_secret`` yalnız yerel/dev
``LocalEncryptedVault`` içindir (bkz. backend/src/auth/vault.py) -- SECURITY.md'nin ongordugu
uretim secrets manager'in yerine GECMEZ.

``web_login_state``/``web_session``, ``backend.src.auth.web_session`` dataclass'larıyla
eşleşir -- Claude'un connector OAuth AS'ından tamamen ayrı, yalnız insan onayı sayfasının
(`/login`, `/approvals`) kendi oturumu içindir. Token değerleri burada da asla ham
saklanmaz.
"""

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS principal (
        id TEXT PRIMARY KEY,
        issuer TEXT NOT NULL,
        subject TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (issuer, subject)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ads_account (
        id TEXT PRIMARY KEY,
        principal_id TEXT NOT NULL REFERENCES principal(id),
        customer_id TEXT NOT NULL,
        login_customer_id TEXT,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (principal_id, customer_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS oauth_credential (
        id TEXT PRIMARY KEY,
        principal_id TEXT NOT NULL REFERENCES principal(id),
        vault_ref TEXT NOT NULL,
        status TEXT NOT NULL,
        key_version INTEGER NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS proposal (
        id TEXT PRIMARY KEY,
        principal_id TEXT NOT NULL REFERENCES principal(id),
        customer_id TEXT NOT NULL,
        payload TEXT NOT NULL,
        proposal_hash TEXT NOT NULL,
        status TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (id, principal_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS approval (
        id TEXT PRIMARY KEY,
        proposal_id TEXT NOT NULL,
        principal_id TEXT NOT NULL REFERENCES principal(id),
        approver_id TEXT NOT NULL,
        decision TEXT NOT NULL,
        proposal_hash TEXT NOT NULL,
        decided_at TEXT NOT NULL,
        FOREIGN KEY (proposal_id, principal_id) REFERENCES proposal(id, principal_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS execution (
        id TEXT PRIMARY KEY,
        proposal_id TEXT NOT NULL,
        principal_id TEXT NOT NULL REFERENCES principal(id),
        idempotency_key TEXT NOT NULL UNIQUE,
        before TEXT NOT NULL,
        after TEXT NOT NULL,
        google_request_id TEXT,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (proposal_id, principal_id) REFERENCES proposal(id, principal_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_event (
        event_id TEXT PRIMARY KEY,
        occurred_at TEXT NOT NULL,
        actor TEXT NOT NULL,
        principal_id TEXT,
        customer_id TEXT,
        event_type TEXT NOT NULL,
        proposal_id TEXT,
        approval_id TEXT,
        execution_id TEXT,
        outcome TEXT NOT NULL,
        reason_code TEXT,
        correlation_id TEXT NOT NULL,
        google_request_id TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS oauth_client_grant (
        id TEXT PRIMARY KEY,
        principal_id TEXT NOT NULL REFERENCES principal(id),
        client_id TEXT NOT NULL,
        scope TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (principal_id, client_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS authorization_transaction (
        id TEXT PRIMARY KEY,
        client_id TEXT NOT NULL,
        redirect_uri TEXT NOT NULL,
        code_challenge TEXT NOT NULL,
        code_challenge_method TEXT NOT NULL,
        resource TEXT NOT NULL,
        scope TEXT NOT NULL,
        client_state TEXT NOT NULL,
        status TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS authorization_code (
        code_hash TEXT PRIMARY KEY,
        transaction_id TEXT NOT NULL REFERENCES authorization_transaction(id),
        principal_id TEXT NOT NULL REFERENCES principal(id),
        client_id TEXT NOT NULL,
        redirect_uri TEXT NOT NULL,
        code_challenge TEXT NOT NULL,
        code_challenge_method TEXT NOT NULL,
        resource TEXT NOT NULL,
        scope TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        consumed_at TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS access_token (
        token_hash TEXT PRIMARY KEY,
        principal_id TEXT NOT NULL REFERENCES principal(id),
        client_id TEXT NOT NULL,
        resource TEXT NOT NULL,
        scope TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        revoked_at TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS refresh_token (
        token_hash TEXT PRIMARY KEY,
        family_id TEXT NOT NULL,
        principal_id TEXT NOT NULL REFERENCES principal(id),
        client_id TEXT NOT NULL,
        resource TEXT NOT NULL,
        scope TEXT NOT NULL,
        status TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        rotated_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS vault_secret (
        vault_ref TEXT PRIMARY KEY,
        ciphertext BLOB NOT NULL,
        created_at TEXT NOT NULL,
        revoked_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS web_login_state (
        state_hash TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS web_session (
        token_hash TEXT PRIMARY KEY,
        principal_id TEXT NOT NULL REFERENCES principal(id),
        csrf_token TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        revoked_at TEXT,
        created_at TEXT NOT NULL
    )
    """,
]
