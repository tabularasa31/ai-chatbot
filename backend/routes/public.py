"""Public routes (no auth) for embed script."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

public_router = APIRouter(prefix="", tags=["public"])


@public_router.get("/embed.js")
async def get_embed_script():
    """
    Public script for embedding Chat9 widget.
    No authentication required.

    Usage: <script src="https://chat9.live/embed.js?botId=ch_xyz"></script>
    """
    script_path = Path(__file__).resolve().parent.parent / "static" / "embed.js"
    return FileResponse(
        path=script_path,
        media_type="application/javascript",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
