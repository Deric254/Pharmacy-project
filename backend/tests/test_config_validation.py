"""
Settings are validated when the app starts, so a mis-set secret, a typo in
ENVIRONMENT or REDIS_MODE, or a malformed encryption key fails immediately
with a message saying how to fix it -- instead of silently weakening
security or surfacing weeks later on the first backup.

Settings(...) is built directly with keyword arguments (which outrank the
process environment) and _env_file=None, so nothing ambient leaks in.
"""

import base64
import os

import pytest
from pydantic import ValidationError

from app.core.config import MIN_JWT_SECRET_LENGTH, Settings

GOOD_JWT = "k" * MIN_JWT_SECRET_LENGTH
GOOD_AES = base64.b64encode(os.urandom(32)).decode()


def _settings(**overrides) -> Settings:
    values = {
        "database_url": "sqlite+aiosqlite:///:memory:",
        "jwt_secret_key": GOOD_JWT,
        "encryption_key": GOOD_AES,
        **overrides,
    }
    return Settings(_env_file=None, **values)


def test_valid_configuration_is_accepted(monkeypatch):
    # Defaults only: a REDIS_MODE / ENVIRONMENT left in the shell must not leak in.
    monkeypatch.delenv("REDIS_MODE", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    settings = _settings()
    assert settings.environment == "development"
    assert settings.redis_mode == "redis"


@pytest.mark.parametrize("environment", ["development", "staging", "production"])
def test_every_documented_environment_is_accepted(environment):
    assert _settings(environment=environment).environment == environment


@pytest.mark.parametrize("environment", ["prod", "Production", "test", ""])
def test_a_mistyped_environment_is_rejected(environment):
    with pytest.raises(ValidationError, match="environment"):
        _settings(environment=environment)


@pytest.mark.parametrize("mode", ["Memory", "inmemory", ""])
def test_a_mistyped_redis_mode_is_rejected(mode):
    with pytest.raises(ValidationError, match="redis_mode"):
        _settings(redis_mode=mode)


def test_memory_redis_mode_is_still_accepted_for_the_desktop_app():
    assert _settings(redis_mode="memory").redis_mode == "memory"


def test_a_jwt_secret_one_character_too_short_is_rejected():
    with pytest.raises(ValidationError, match="at least 32 characters"):
        _settings(jwt_secret_key="k" * (MIN_JWT_SECRET_LENGTH - 1))


def test_the_minimum_length_jwt_secret_is_accepted():
    assert _settings(jwt_secret_key="k" * MIN_JWT_SECRET_LENGTH)


@pytest.mark.parametrize(
    "placeholder",
    [
        "changeme-generate-a-real-secret",
        "CHANGEME-" + "x" * 40,  # long enough, still a placeholder; case-insensitive
    ],
)
def test_the_env_example_placeholder_is_rejected_even_when_long_enough(placeholder):
    with pytest.raises(ValidationError, match="placeholder"):
        _settings(jwt_secret_key=placeholder)


def test_the_error_tells_you_how_to_generate_a_secret():
    with pytest.raises(ValidationError, match="secrets.token_hex"):
        _settings(jwt_secret_key="short")


def test_an_encryption_key_that_is_not_base64_is_rejected():
    with pytest.raises(ValidationError, match="not valid base64"):
        _settings(encryption_key="this is not base64 !!!")


@pytest.mark.parametrize("raw_length", [16, 24, 31, 33, 64])
def test_an_encryption_key_of_the_wrong_length_is_rejected(raw_length):
    key = base64.b64encode(os.urandom(raw_length)).decode()
    with pytest.raises(ValidationError, match="exactly 32 bytes"):
        _settings(encryption_key=key)


def test_the_encryption_key_placeholder_from_env_example_is_rejected():
    with pytest.raises(ValidationError, match="ENCRYPTION_KEY"):
        _settings(encryption_key="changeme-generate-a-real-32-byte-base64-key")


def test_memory_redis_with_several_workers_is_rejected(monkeypatch):
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    with pytest.raises(ValidationError, match="single worker"):
        _settings(redis_mode="memory")


def test_real_redis_is_fine_with_several_workers(monkeypatch):
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    assert _settings(redis_mode="redis").redis_mode == "redis"


@pytest.mark.parametrize("value", ["1", " 1 ", "", "not-a-number"])
def test_memory_redis_is_fine_with_one_worker_or_an_unreadable_setting(monkeypatch, value):
    monkeypatch.setenv("WEB_CONCURRENCY", value)
    assert _settings(redis_mode="memory").redis_mode == "memory"


def test_an_all_zero_encryption_key_is_rejected():
    with pytest.raises(ValidationError, match="all zeros"):
        _settings(encryption_key=base64.b64encode(bytes(32)).decode())


def test_the_values_the_readme_and_test_suite_use_are_valid():
    """The documented dev setup must itself pass startup validation."""
    import re
    from pathlib import Path

    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text()
    jwt = re.search(r'export JWT_SECRET_KEY="([^"]+)"', readme)
    key = re.search(r'export ENCRYPTION_KEY="([^"]+)"', readme)
    assert jwt and key
    assert _settings(jwt_secret_key=jwt.group(1), encryption_key=key.group(1))
