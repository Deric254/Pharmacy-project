"""
Application settings.

Everything environment-specific (DB creds, secret keys, token lifetimes)
lives here and is loaded from environment variables / .env — never
hardcoded, never committed. This is distinct from the business-facing
Configurable Business Panel (branding, currency, thresholds), which is
runtime data stored in the `business_config` table, not app config.
"""

import base64
import binascii
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# HS256 needs a key at least as long as its 256-bit hash output (RFC 7518
# section 3.2); PyJWT warns about anything shorter.
MIN_JWT_SECRET_LENGTH = 32
_AES_KEY_BYTES = 32
# .env.example ships placeholders that start with this. Running with one
# would mean every install shares a publicly known signing key.
_PLACEHOLDER_PREFIX = "changeme"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "Pharmacy ERP"
    # A typo such as "prod" used to be accepted silently and quietly skip
    # every production-only behavior (docs off, Secure cookie), so the
    # allowed values are enforced instead.
    environment: Literal["development", "staging", "production"] = "development"
    api_v1_prefix: str = "/api/v1"

    database_url: str
    redis_url: str = "redis://localhost:6379/0"
    # "redis" (default) talks to a real Redis via redis_url, same as
    # always. "memory" swaps in an in-process fake with the same
    # interface (app/core/memory_redis.py) -- exclusively for the
    # bundled desktop .exe, which can't reasonably require a separate
    # Redis install. Nothing else should ever set this.
    redis_mode: Literal["redis", "memory"] = "redis"

    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # AES key used to encrypt AI provider keys / OAuth tokens at rest.
    # Must be 32 bytes, base64-encoded. Rotate via a documented key-
    # rotation procedure, never by editing this value directly in prod.
    encryption_key: str

    cors_origins: list[str] = ["http://localhost:5173"]

    @model_validator(mode="after")
    def _memory_redis_is_single_process_only(self) -> Self:
        # The in-memory stand-in lives inside one process. With several
        # workers each would get its own private cache, rate-limit counters
        # and pub/sub, so a login attempt limit or a cache invalidation would
        # silently apply to only one worker. WEB_CONCURRENCY is what uvicorn
        # and gunicorn read for their worker count.
        if self.redis_mode == "memory":
            try:
                workers = int(os.environ.get("WEB_CONCURRENCY", "1").strip() or "1")
            except ValueError:
                workers = 1
            if workers > 1:
                raise ValueError(
                    f"REDIS_MODE=memory only works with a single worker, but "
                    f"WEB_CONCURRENCY={workers}. Use REDIS_MODE=redis (a real Redis) "
                    "for multi-worker deployments."
                )
        return self

    @field_validator("jwt_secret_key")
    @classmethod
    def _jwt_secret_must_be_real(cls, value: str) -> str:
        if value.lower().startswith(_PLACEHOLDER_PREFIX):
            raise ValueError(
                "JWT_SECRET_KEY is still the .env.example placeholder. Generate one with: "
                'python -c "import secrets; print(secrets.token_hex(32))"'
            )
        if len(value) < MIN_JWT_SECRET_LENGTH:
            raise ValueError(
                f"JWT_SECRET_KEY must be at least {MIN_JWT_SECRET_LENGTH} characters. "
                'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
            )
        return value

    @field_validator("encryption_key")
    @classmethod
    def _encryption_key_must_be_a_valid_aes_key(cls, value: str) -> str:
        # Checked at startup so a bad key fails loudly now, not on the first
        # backup or AI-key save weeks later.
        how_to = (
            'Generate one with: python -c "import os,base64; '
            'print(base64.b64encode(os.urandom(32)).decode())"'
        )
        try:
            decoded = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"ENCRYPTION_KEY is not valid base64. {how_to}") from exc
        if len(decoded) != _AES_KEY_BYTES:
            raise ValueError(
                f"ENCRYPTION_KEY must decode to exactly {_AES_KEY_BYTES} bytes "
                f"(AES-256), got {len(decoded)}. {how_to}"
            )
        if not any(decoded):
            # 32 zero bytes is the most guessable key there is: anything
            # encrypted with it is effectively not encrypted at all.
            raise ValueError(f"ENCRYPTION_KEY is all zeros, which is not a real key. {how_to}")
        return value

    # Controls the `Secure` flag on the refresh-token cookie. Browsers
    # (Chromium/Electron included) silently REFUSE to store a cookie
    # marked Secure unless the page was loaded over HTTPS -- there is
    # no error, no exception, nothing in the network tab to notice.
    # The bundled desktop .exe sets ENVIRONMENT=production (correctly,
    # for logging/docs behavior) but always serves the app over plain
    # http://127.0.0.1:8000, never HTTPS. Tying the cookie's Secure
    # flag directly to environment == "production" therefore silently
    # dropped the refresh cookie on every single desktop install: the
    # first login of a session worked (access token lives in memory),
    # but any page reload, app restart, or access-token expiry had no
    # refresh cookie to redeem, so bootstrap()/refresh failed and the
    # app was stuck on the blank pre-render screen or bounced back to
    # a login that wouldn't take. None by default means "derive from
    # environment" for real HTTPS-fronted deployments; desktop_main.py
    # explicitly overrides this to false, since loopback-only HTTP has
    # no meaningful HTTPS threat model to protect against anyway.
    cookie_secure: bool | None = None

    @property
    def effective_cookie_secure(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.environment == "production"

    # Optional -- only needed if the Google Drive backup provider is used.
    # Blank by default so environments without backups configured don't
    # need to set these.
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""

    # Optional. A GitHub personal access token (repo scope not required --
    # this only reads public release metadata) used by
    # update_check_service.py when asking GitHub for the latest release.
    # Blank by default: the request still works unauthenticated, just
    # subject to GitHub's much lower 60-requests/hour-per-IP limit rather
    # than the ~5000/hour authenticated limit. That matters here
    # specifically because every desktop install behind the same
    # hospital/office NAT shares one public IP against that limit.
    github_update_token: str = ""

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

    @property
    def log_file_path(self) -> Path:
        """
        A real, persistent log file next to the database -- same
        convention as local_backup_dir. Without this, an unhandled
        error in the packaged desktop app (which has no visible
        console) was completely invisible: not to the person using
        it, and not to anyone trying to diagnose it afterward.
        """
        prefix = "sqlite+aiosqlite:///"
        if not self.database_url.startswith(prefix):
            return Path("logs") / "pharmacy-erp.log"
        db_path = Path(self.database_url[len(prefix) :]).resolve()
        return db_path.parent / "logs" / "pharmacy-erp.log"


@lru_cache
def get_settings() -> Settings:
    return Settings()
