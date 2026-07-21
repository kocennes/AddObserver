"""Environment-based configuration for the dependency-free backend skeleton.

SECURITY.md geregi hicbir secret kod icine gomulmez; yalniz ortam degiskeninden okunur.
Uretimde ortam degiskenleri bir secrets manager'dan enjekte edilir (SECURITY.md -- "Secret
yonetimi"); bu modul yalniz *nereden* okunacagini belirler, secret'i kendisi uretmez/saklamaz.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """Minimal, bagimliliksiz .env yukleyici. Ortamda zaten tanimli degiskenlerin uzerine yazmaz."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip()


def _split_env_list(raw: str | None) -> tuple[str, ...]:
    """Parse a comma-separated env var into a trimmed, empty-entry-free tuple."""
    if not raw:
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    """Yerel gelistirme ayarlari.

    ``sqlite_db_path`` yalniz ilk stdlib-only iskelet icindir; DATABASE.md'nin baglayici
    karari uretimde PostgreSQL'dir (bkz. docs/decisions/0001-backend-stack.md).

    ``public_base_url``/``mcp_resource_path`` connector AS'in kendi issuer/resource
    kimligini kurar (RFC 8414/9728, ADR-0002). ``local_vault_key`` yalniz
    ``backend.src.auth.vault.LocalEncryptedVault`` icindir -- uretim secrets manager'i
    degildir (SECURITY.md, hala `TBD`). ``google_client_id``/``google_client_secret``
    Google'in `.env.example`'da zaten tanimli degiskenleridir; connector AS'in kendi
    OAuth client'iyla KARISTIRILMAZ (SECURITY.md -- token passthrough yasagi).
    ``google_ads_developer_token`` yalniz backend'de bulunur, hicbir MCP/Claude-facing
    cevaba veya loga girmez (SECURITY.md -- "Developer token yalniz backend'de bulunur").

    ``allowed_hosts`` `Host` basligi dogrulamasi icindir (SECURITY.md -- "Girdi, cikti ve web
    guvenligi"); belirtilmezse tek guvenli varsayilan ``public_base_url``'in kendi
    hostname'idir -- DEPLOYMENT.md'nin proxy topolojisi ADR'i kabul edilene kadar
    `X-Forwarded-Host` gibi proxy basliklarina guvenilmez. ``cors_allowed_origins`` bos ise
    (varsayilan) capraz-origin tarayici erisimi yoktur; SECURITY.md'nin "CORS acik
    allowlist'tir" karari geregi asla ``*`` kullanilmaz ve credential'li (cookie/Authorization
    tasiyan) capraz-origin istek desteklenmez.

    ``local_vault_key``/``google_client_secret``/``google_ads_developer_token``
    ``repr=False`` tasir: bu tek bir ``Settings`` nesnesi hemen hemen her istek
    yolundan (auth, MCP tool context, reporting adapter) gecer, bu yuzden onu
    tasiyan bir degiskenin yanlislikla ``repr()``/``str()``'ye (ornegin ileride
    eklenecek bir ``logger.debug(settings)`` veya bir exception/traceback'in
    yerel degiskeni) dusmesi TUM secret'lari tek seferde sizdirirdi
    (docs/SECURITY.md -- "Token, secret ... loglanmaz"; kanit:
    ``backend/tests/test_secret_redaction.py``).
    """

    sqlite_db_path: str
    environment: str
    public_base_url: str
    mcp_resource_path: str
    local_vault_key: str | None = field(repr=False)
    google_client_id: str
    google_client_secret: str = field(repr=False)
    google_ads_developer_token: str = field(repr=False)
    allowed_hosts: tuple[str, ...]
    cors_allowed_origins: tuple[str, ...]
    database_url: str = field(default="", repr=False)

    @property
    def mcp_resource_uri(self) -> str:
        return self.public_base_url.rstrip("/") + self.mcp_resource_path

    @property
    def google_redirect_uri(self) -> str:
        return self.public_base_url.rstrip("/") + "/google/callback"

    @classmethod
    def load(cls, dotenv_path: Path | None = None) -> Settings:
        _load_dotenv(dotenv_path or _REPO_ROOT / ".env")
        public_base_url = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000")
        default_host = urlsplit(public_base_url).hostname or "localhost"
        allowed_hosts = _split_env_list(os.environ.get("ALLOWED_HOSTS")) or (default_host,)
        cors_allowed_origins = _split_env_list(os.environ.get("CORS_ALLOWED_ORIGINS"))
        return cls(
            sqlite_db_path=os.environ.get("LOCAL_SQLITE_DB_PATH", "backend/.data/local.db"),
            environment=os.environ.get("APP_ENVIRONMENT", "local"),
            public_base_url=public_base_url,
            mcp_resource_path=os.environ.get("MCP_RESOURCE_PATH", "/mcp"),
            local_vault_key=os.environ.get("LOCAL_VAULT_KEY"),
            google_client_id=os.environ.get("GOOGLE_ADS_CLIENT_ID", ""),
            google_client_secret=os.environ.get("GOOGLE_ADS_CLIENT_SECRET", ""),
            google_ads_developer_token=os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", ""),
            allowed_hosts=allowed_hosts,
            cors_allowed_origins=cors_allowed_origins,
            database_url=os.environ.get("DATABASE_URL", ""),
        )
