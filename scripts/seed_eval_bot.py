"""Seed a demo bot for the chat9 eval pipeline.

Creates (idempotently) a user + tenant + bot wired to your OpenAI key,
ingests the fixture docs from ``tests/eval/datasets/_fixtures/docs/``,
generates embeddings, and prints the bot's ``public_id`` so you can
plug it into the runner:

    DATABASE_URL=postgresql://… OPENAI_API_KEY=sk-… \
        python scripts/seed_eval_bot.py
    # … prints EVAL_BOT_PUBLIC_ID=ch_xxxxxxxxxxxxxxxx

    python -m backend.evals run \\
        --dataset chat9_basic \\
        --bot-id ch_xxxxxxxxxxxxxxxx \\
        --api-base http://localhost:8000

This talks to the DB directly (no HTTP, no email verification) so it is
intended for **local dev** only. Costs a few cents in OpenAI embedding
charges per fresh seed.
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from pathlib import Path

# Set safe defaults so importing the backend doesn't fail when an env
# var is missing — required values still need to be set by the caller.
import os

os.environ.setdefault("ENVIRONMENT", "development")

from sqlalchemy.orm import Session  # noqa: E402

from backend.core.crypto import encrypt_value  # noqa: E402
from backend.core.db import SessionLocal  # noqa: E402
from backend.documents.service import upload_document  # noqa: E402
from backend.embeddings.service import run_embeddings_background  # noqa: E402
from backend.models import Bot, Tenant, User  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_EMAIL = "eval-seed@chat9.local"
DEFAULT_TENANT_NAME = "chat9-eval-demo"
DEFAULT_BOT_NAME = "chat9-eval-bot"
FIXTURE_DIR = Path("tests/eval/datasets/_fixtures/docs")

FILE_TYPE_BY_SUFFIX = {
    ".md": "markdown",
    ".mdx": "markdown",
    ".txt": "plaintext",
    ".pdf": "pdf",
    ".json": "swagger",
    ".yaml": "swagger",
    ".yml": "swagger",
    ".docx": "docx",
    ".doc": "doc",
}


def _ensure_user_tenant_bot(
    db: Session,
    *,
    email: str,
    tenant_name: str,
    bot_name: str,
    openai_api_key: str,
) -> tuple[User, Tenant, Bot]:
    """Create or fetch an idempotent (user, tenant, bot) triple."""

    user = db.query(User).filter(User.email == email).first()
    if user is None:
        user = User(
            id=uuid.uuid4(),
            email=email,
            password="seed-no-login",
            is_verified=True,
            verification_token=None,
            verification_expires_at=None,
        )
        db.add(user)
        db.flush()

    tenant = (
        db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
        if user.tenant_id
        else None
    )
    if tenant is None:
        tenant = Tenant(name=tenant_name, is_active=True)
        db.add(tenant)
        db.flush()
        user.tenant_id = tenant.id

    tenant.openai_api_key = encrypt_value(openai_api_key)

    bot = (
        db.query(Bot)
        .filter(Bot.tenant_id == tenant.id, Bot.name == bot_name)
        .first()
    )
    if bot is None:
        bot = Bot(tenant_id=tenant.id, name=bot_name)
        db.add(bot)
        db.flush()

    db.commit()
    db.refresh(bot)
    db.refresh(tenant)
    return user, tenant, bot


def _ingest_fixtures(
    db: Session, *, tenant: Tenant, fixture_dir: Path, openai_api_key: str
) -> list[uuid.UUID]:
    """Upload + embed every fixture file. Skips files already present
    on the tenant (by filename) so re-running is a no-op."""

    if not fixture_dir.is_dir():
        raise SystemExit(f"fixture dir not found: {fixture_dir}")

    from backend.models import Document

    existing = {
        d.filename
        for d in db.query(Document).filter(Document.tenant_id == tenant.id).all()
    }

    indexed: list[uuid.UUID] = []
    for path in sorted(fixture_dir.iterdir()):
        if path.is_dir() or path.name.startswith("."):
            continue
        if path.name in existing:
            print(f"  · {path.name}: already present, skipping")
            continue
        file_type = FILE_TYPE_BY_SUFFIX.get(path.suffix.lower())
        if file_type is None:
            print(f"  · {path.name}: unsupported suffix, skipping")
            continue
        content = path.read_bytes()
        doc = upload_document(
            tenant_id=tenant.id,
            filename=path.name,
            content=content,
            file_type=file_type,
            db=db,
        )
        print(f"  + {path.name}: uploaded as document {doc.id}")
        run_embeddings_background(doc.id, openai_api_key)
        indexed.append(doc.id)

    return indexed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the chat9 demo eval bot.")
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    parser.add_argument("--tenant-name", default=DEFAULT_TENANT_NAME)
    parser.add_argument("--bot-name", default=DEFAULT_BOT_NAME)
    parser.add_argument(
        "--fixture-dir",
        default=str(FIXTURE_DIR),
        help="Directory of seed documents (default: %(default)s).",
    )
    args = parser.parse_args(argv)

    openai_api_key = os.environ.get("OPENAI_API_KEY")
    if not openai_api_key or openai_api_key == "sk-test":
        print(
            "OPENAI_API_KEY must be set to a real key in the environment.",
            file=sys.stderr,
        )
        return 2
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL must be set in the environment.", file=sys.stderr)
        return 2

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    db = SessionLocal()
    try:
        user, tenant, bot = _ensure_user_tenant_bot(
            db,
            email=args.email,
            tenant_name=args.tenant_name,
            bot_name=args.bot_name,
            openai_api_key=openai_api_key,
        )
        print(f"User:   {user.email} (id={user.id})")
        print(f"Tenant: {tenant.name} (public_id={tenant.public_id})")
        print(f"Bot:    {bot.name} (public_id={bot.public_id})")
        print(f"Ingesting fixtures from {args.fixture_dir} …")
        _ingest_fixtures(
            db, tenant=tenant, fixture_dir=Path(args.fixture_dir), openai_api_key=openai_api_key
        )
    finally:
        db.close()

    print(f"\nEVAL_BOT_PUBLIC_ID={bot.public_id}")
    print(
        "Embedding runs in a background thread; allow ~30s before kicking off"
        " the eval runner. Verify status via Dashboard → Knowledge or "
        "`SELECT id, filename, status FROM documents;`."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
