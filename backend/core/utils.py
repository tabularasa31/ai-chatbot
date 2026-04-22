"""Core utility functions."""

import secrets
import string


def generate_public_id(prefix: str = "ch_") -> str:
    """
    Generate public client ID.
    Format: ch_<18-char alphanumeric>
    Example: ch_abc123xyz456789
    """
    chars = string.ascii_lowercase + string.digits
    random_part = "".join(secrets.choice(chars) for _ in range(18))
    return prefix + random_part


def generate_api_key() -> str:
    """
    Generate tenant API key.
    Format: ck_<32 hex chars>  (total 35 chars)
    The ck_ prefix makes keys visually distinct from OpenAI's sk_ keys in logs.
    """
    return "ck_" + secrets.token_hex(16)
