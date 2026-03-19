"""Symmetric encryption for sensitive values (e.g. OpenAI API keys)."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from backend.core.config import settings


def get_fernet() -> Fernet:
    """Build Fernet instance from settings.ENCRYPTION_KEY (base64 string)."""
    key = settings.encryption_key
    if not key:
        raise RuntimeError("ENCRYPTION_KEY is not set")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_value(value: str) -> str:
    """Encrypt a string value. Returns base64-encoded ciphertext."""
    f = get_fernet()
    return f.encrypt(value.encode()).decode()


def decrypt_value(value: str) -> str:
    """Decrypt a base64-encoded ciphertext. Returns plaintext string."""
    f = get_fernet()
    try:
        return f.decrypt(value.encode()).decode()
    except InvalidToken as e:
        raise RuntimeError("Failed to decrypt: invalid token") from e
    except (ValueError, UnicodeDecodeError) as e:
        raise RuntimeError(f"Failed to decrypt: {e}") from e
