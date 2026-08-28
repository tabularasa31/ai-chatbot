#!/usr/bin/env python
"""Write frontend/openapi.public.json from the application code.

The public API schema is a property of this repository, not of whatever happens
to be deployed. The frontend used to fetch it from the live API at build time,
which was wrong twice over. A build could be validated against a schema from a
different commit than the one being built. And on 2026-08-28 a backend outage
failed the frontend job on every branch, which turned the check suite red --
Railway refuses to deploy on a red suite, so the deploy it refused was the one
that would have brought the API back.

Run from the repository root; ``frontend/scripts/generate-openapi.mjs`` calls
it as the frontend's prebuild step.

    python scripts/generate-openapi.py
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
OUTPUT = REPO_ROOT / "frontend" / "openapi.public.json"

# Importing the app builds Settings, which requires a populated environment.
# None of it is contacted -- the schema is derived from the route table alone --
# so inert placeholders are enough, and they keep the script runnable with no
# setup on a machine that has never configured the backend.
PLACEHOLDER_ENV = {
    "DATABASE_URL": "sqlite:///:memory:?check_same_thread=False",
    "JWT_SECRET": "openapi-generation-placeholder-secret-key!",
    "ENVIRONMENT": "test",
    "ENCRYPTION_KEY": "7b4_zUZivxPZWzIkXbVf3dpQX9Ab22HB51H9Qcrjya8=",
    "OPENAI_API_KEY": "sk-openapi-generation-placeholder",
    "FRONTEND_URL": "http://localhost:3000",
    "BREVO_API_KEY": "openapi-generation-placeholder",
    "EMAIL_FROM": "openapi-generation@example.com",
}

# The documented surface. Everything else in the app -- auth, admin, billing,
# tenant management -- is deliberately absent from the published reference.
PUBLIC_TAGS = {"widget", "documents", "chat", "escalations", "gap-analyzer", "knowledge"}
PUBLIC_PATHS = {"/health"}
OPERATIONS = ("get", "post", "put", "patch", "delete", "head", "options", "trace")

FALLBACK_SERVER = "https://api.getchat9.live"


def build_public_spec() -> dict:
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
        spec["servers"] = [{"url": FALLBACK_SERVER}]
    return spec


def main() -> int:
    for key, value in PLACEHOLDER_ENV.items():
        os.environ.setdefault(key, value)
    sys.path.insert(0, str(REPO_ROOT))

    spec = build_public_spec()
    if not spec["paths"]:
        # A tag rename upstream would silently empty the reference. Better to
        # fail the build than to publish an API reference with no API in it.
        print(
            "no public paths matched -- check PUBLIC_TAGS against the route tags",
            file=sys.stderr,
        )
        return 1

    OUTPUT.write_text(json.dumps(spec, indent=2) + "\n")
    print(f"[openapi] wrote {OUTPUT.relative_to(REPO_ROOT)} ({len(spec['paths'])} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
