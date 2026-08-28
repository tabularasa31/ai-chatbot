#!/usr/bin/env python
"""Write frontend/openapi.public.json from the application code.

The public API schema is a property of this repository, not of whatever happens
to be deployed. This regenerates the committed copy that
``frontend/scripts/fetch-openapi.mjs`` falls back to when the live API is
unreachable, so a backend outage cannot stop the frontend from building.

Run it from the repository root after changing a public route:

    DATABASE_URL=sqlite:// JWT_SECRET=x PYTHONPATH=. python scripts/generate-openapi.py

The filtering below mirrors ``fetch-openapi.mjs`` exactly; the two must agree,
or a build served from the fallback would expose a different surface from one
served by the fetch.
"""

from __future__ import annotations

import json
import pathlib

PUBLIC_TAGS = {"widget", "documents", "chat", "escalations", "gap-analyzer", "knowledge"}
PUBLIC_PATHS = {"/health"}
OPERATIONS = ("get", "post", "put", "patch", "delete", "head", "options", "trace")
OUTPUT = pathlib.Path("frontend/openapi.public.json")


def main() -> None:
    from backend.main import app

    spec = app.openapi()

    kept: dict[str, dict] = {}
    for path, item in (spec.get("paths") or {}).items():
        if path in PUBLIC_PATHS:
            kept[path] = item
            continue
        for method in OPERATIONS:
            tags = (item.get(method) or {}).get("tags") or []
            if PUBLIC_TAGS.intersection(tags):
                kept[path] = item
                break

    spec["paths"] = kept
    spec["tags"] = [t for t in (spec.get("tags") or []) if t.get("name") in PUBLIC_TAGS]
    if not spec.get("servers"):
        spec["servers"] = [{"url": "https://api.getchat9.live"}]

    OUTPUT.write_text(json.dumps(spec, indent=2) + "\n")
    print(f"wrote {OUTPUT} ({len(kept)} paths)")


if __name__ == "__main__":
    main()
