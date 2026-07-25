"""
Application settings.

Everything environment-specific (DB creds, secret keys, token lifetimes)
lives here and is loaded from environment variables / .env — never
hardcoded, never committed. This is distinct from the business-facing
Configurable Business Panel (branding, currency, thresholds), which is
runtime data stored in the `business_config` table, not app config.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "Pharmacy ERP"
    environment: str = "development"  # development | staging | production
    api_v1_prefix: str = "/api/v1"

    database_url: str
    redis_url: str = "redis://localhost:6379/0"
    # "redis" (default) talks to a real Redis via redis_url, same as
    # always. "memory" swaps in an in-process fake with the same
    # interface (app/core/memory_redis.py) -- exclusively for the
    # bundled desktop .exe, which can't reasonably require a separate
    # Redis install. Nothing else should ever set this.
    redis_mode: str = "redis"

    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # AES key used to encrypt AI provider keys / OAuth tokens at rest.
    # Must be 32 bytes, base64-encoded. Rotate via a documented key-
    # rotation procedure, never by editing this value directly in prod.
    encryption_key: str

    cors_origins: list[str] = ["http://localhost:5173"]

    # Optional -- only needed if the Google Drive backup provider is used.
    # Blank by default so environments without backups configured don't
    # need to set these.
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""

    @property
    def local_backup_dir(self) -> Path:
        """
        A `backups/` folder next to the actual database file, wherever
        that happens to be -- %LOCALAPPDATA%\\PharmacyERP on the
        desktop app, or right next to dev.db during local development.
        Deriving it from database_url (rather than duplicating
        desktop_main.py's separate app-data-directory logic) means
        this works correctly regardless of platform or launch mode.
        """
        prefix = "sqlite+aiosqlite:///"
        if not self.database_url.startswith(prefix):
            # SQLite is this app's only supported database now; this
            # is just a safe fallback, not an expected real case.
            return Path("backups")
        db_path = Path(self.database_url[len(prefix) :]).resolve()
        return db_path.parent / "backups"


@lru_cache
def get_settings() -> Settings:
    return Settings()
