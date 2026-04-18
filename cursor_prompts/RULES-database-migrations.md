# RULES: Database Migrations

⚠️ **THESE RULES APPLY TO ALL CURSOR SESSIONS. NO EXCEPTIONS.**

---

## ❌ NEVER DO THIS

```bash
alembic downgrade <anything>   # FORBIDDEN
alembic downgrade -1           # FORBIDDEN
alembic downgrade base         # FORBIDDEN
```

**Never run `alembic downgrade` against any database — local or production.**

Why: downgrade drops real columns and data. `is_admin` was lost this way in March 2026.

---

## ✅ ALLOWED COMMANDS

```bash
alembic upgrade head           # apply all pending migrations
alembic current                # check current revision
alembic history                # view migration chain
alembic check                  # verify model/migration sync
```

---

## CREATING NEW MIGRATIONS

1. **Create the migration file manually** — do NOT use `alembic revision --autogenerate` against production DB
2. Set correct `down_revision` — it must point to the current head (`alembic heads` before you start)
3. **Normal migrations:** implement `upgrade()` with the actual schema change. `downgrade()` must be a **fail-loud stub** that raises `NotImplementedError` — we never run downgrade, so this guarantees an accidental `alembic downgrade` errors out instead of silently moving `alembic_version` backward while the schema stays put:
   ```python
   def downgrade() -> None:
       # Intentional fail-loud: downgrade is never executed (see project CLAUDE.md).
       raise NotImplementedError("downgrade is not supported for this migration")
   ```
4. Test by reading the migration file and running **`alembic upgrade head`** on a throwaway/local DB — not by downgrade/upgrade cycles
5. **Repair / idempotent migrations** (schema drift, “column already exists” on some envs): in `upgrade()`, use `inspect()` or SQL `IF NOT EXISTS` so applying twice is safe. `downgrade()` follows the same fail-loud stub pattern as normal migrations

```bash
# Correct flow:
alembic revision -m "add_my_column"    # create empty file
# Edit the file manually
alembic upgrade head                   # apply it
```

---

## IF MIGRATION FAILS

1. Fix the migration file
2. Run `alembic upgrade head` again
3. If you get "column already exists" — the column is already there, skip or use `IF NOT EXISTS`
4. **DO NOT run downgrade to "clean up"**

---

## PRODUCTION RULE

Railway runs `alembic upgrade head` automatically on every deploy (via Procfile `release` step).
You never need to run migrations manually against production. Ever.
