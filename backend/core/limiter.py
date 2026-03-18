"""Rate limiter for FastAPI using slowapi."""

import uuid

from slowapi import Limiter
from slowapi.util import get_remote_address

from backend.core.config import settings


def _key_func(request):
    """Use unique key in test mode to avoid rate limiting tests."""
    if settings.environment == "test":
        return str(uuid.uuid4())
    return get_remote_address(request)


limiter = Limiter(key_func=_key_func)
