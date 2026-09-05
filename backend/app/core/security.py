"""
Security primitives shared by every module:
  - password hashing (argon2id)
  - JWT access/refresh token issuance & verification
  - AES-256 encryption for anything stored at rest that must never be
    plaintext (AI provider API keys, backup OAuth tokens)

Nothing outside this file should call jose/argon2/cryptography directly —
one place to audit, one place to rotate algorithms later without hunting
through every module.
"""

import base64
import hashlib
import os
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from jose import JWTError, jwt

from app.core.config import get_settings

settings = get_settings()
_hasher = PasswordHasher()  # argon2id, library defaults are current best-practice params


# ---- Passwords ----------------------------------------------------------


def hash_password(plain_password: str) -> str:
    return _hasher.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return _hasher.verify(hashed_password, plain_password)
    except VerifyMismatchError:
        return False


# ---- JWT tokens -----------------------------------------------------------

TokenType = Literal["access", "refresh"]


def create_token(
    subject: str, token_type: TokenType, extra_claims: dict[str, Any] | None = None
) -> str:
    expire_delta = (
        timedelta(minutes=settings.access_token_expire_minutes)
        if token_type == "access"
        else timedelta(days=settings.refresh_token_expire_days)
    )
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + expire_delta,
        **(extra_claims or {}),
    }
    encoded: str = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return encoded


def decode_token(token: str) -> dict[str, Any]:
    """Raises JWTError on invalid/expired token — caller handles the 401."""
    decoded: dict[str, Any] = jwt.decode(
        token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
    )
    return decoded


# ---- AES-256-GCM encryption for secrets at rest ---------------------------


def _get_aes_key() -> bytes:
    key = base64.b64decode(settings.encryption_key)
    if len(key) != 32:
        raise ValueError("encryption_key must decode to exactly 32 bytes (AES-256)")
    return key


def encrypt_secret(plaintext: str) -> str:
    """Returns base64(nonce || ciphertext), safe to store in a text column."""
    aesgcm = AESGCM(_get_aes_key())
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), associated_data=None)
    return base64.b64encode(nonce + ciphertext).decode()


def decrypt_secret(stored_value: str) -> str:
    aesgcm = AESGCM(_get_aes_key())
    raw = base64.b64decode(stored_value)
    nonce, ciphertext = raw[:12], raw[12:]
    return aesgcm.decrypt(nonce, ciphertext, associated_data=None).decode()


def encrypt_bytes(plaintext: bytes) -> bytes:
    """
    Raw-bytes variant for large binary payloads (backup dumps) --
    avoids the base64/str round-trip encrypt_secret forces, which
    would otherwise inflate a multi-megabyte backup by ~33% twice over.
    Returns nonce || ciphertext, no base64 encoding.
    """
    aesgcm = AESGCM(_get_aes_key())
    nonce = os.urandom(12)
    return nonce + aesgcm.encrypt(nonce, plaintext, associated_data=None)


def decrypt_bytes(stored_value: bytes) -> bytes:
    aesgcm = AESGCM(_get_aes_key())
    nonce, ciphertext = stored_value[:12], stored_value[12:]
    return aesgcm.decrypt(nonce, ciphertext, associated_data=None)


# The salt is randomly generated per encryption and stored alongside
# the ciphertext (see encrypt_bytes_with_passphrase) -- NOT fixed.
# A fixed, shared salt was used here previously; the problem with that
# wasn't restore portability (a random salt travels in the file just
# like the nonce always did, so a fresh device restoring with only the
# remembered passphrase is unaffected either way) -- it was that every
# installation of this software sharing one hardcoded salt means a
# single precomputed cracking table works against every customer's
# backup file, not just one. A random salt per backup removes that
# shared attack surface entirely; the passphrase's own strength is
# what protects any individual file either way.
_BACKUP_KDF_SALT_LENGTH = 16
_BACKUP_KDF_ITERATIONS = 390_000


def _derive_backup_key(passphrase: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", passphrase.encode(), salt, _BACKUP_KDF_ITERATIONS, dklen=32
    )


def encrypt_bytes_with_passphrase(plaintext: bytes, passphrase: str) -> bytes:
    """
    Used for backups specifically, instead of encrypt_bytes -- the key
    comes entirely from a passphrase the owner chose and remembers,
    never from anything stored on this specific machine. That's the
    one thing that makes restoring on a different, brand-new device
    possible: nothing about the old machine needs to be reachable,
    only the passphrase the person carries in their own memory.

    Returns salt || nonce || ciphertext. The salt travels with the
    file for the same reason the nonce always has -- restoring needs
    both, and neither is secret, so shipping them alongside the
    ciphertext costs nothing while letting each backup use its own
    unique salt (see _BACKUP_KDF_SALT_LENGTH above for why that
    matters).
    """
    salt = os.urandom(_BACKUP_KDF_SALT_LENGTH)
    aesgcm = AESGCM(_derive_backup_key(passphrase, salt))
    nonce = os.urandom(12)
    return salt + nonce + aesgcm.encrypt(nonce, plaintext, associated_data=None)


def decrypt_bytes_with_passphrase(stored_value: bytes, passphrase: str) -> bytes:
    """
    Raises exactly like decrypt_bytes on a wrong key -- the caller is
    expected to turn that into a clear "wrong passphrase or corrupted
    file" message, never a raw crash.
    """
    salt = stored_value[:_BACKUP_KDF_SALT_LENGTH]
    nonce = stored_value[_BACKUP_KDF_SALT_LENGTH : _BACKUP_KDF_SALT_LENGTH + 12]
    ciphertext = stored_value[_BACKUP_KDF_SALT_LENGTH + 12 :]
    aesgcm = AESGCM(_derive_backup_key(passphrase, salt))
    return aesgcm.decrypt(nonce, ciphertext, associated_data=None)


__all__ = [
    "hash_password",
    "verify_password",
    "create_token",
    "decode_token",
    "encrypt_secret",
    "decrypt_secret",
    "encrypt_bytes",
    "decrypt_bytes",
    "JWTError",
]
