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
