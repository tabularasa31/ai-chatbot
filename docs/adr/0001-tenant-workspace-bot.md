# ADR 0001 — Tenant → Workspace → Bot

**Status:** Accepted (2026‑04‑21)
**Supersedes:** Historical single‑tier Client (=Tenant) model.

## Decision

The domain model has three explicit layers with non‑overlapping responsibilities:

| Layer | Responsibility | Keys |
|---|---|---|
| **Tenant** | *Ownership* — who pays, whose OpenAI key is used, tenant‑wide API key for server‑to‑server calls. | `id`, `api_key`, `openai_api_key`, (future) billing |
| **Workspace** | *Context* — knowledge scope and KYC signing secret. All content rows live under a workspace. | `id`, `tenant_id`, `kyc_secret_key`, `settings` |
| **Bot** | *Behavior* — per‑channel tuning (disclosure level, response detail, future prompt overrides). | `id`, `workspace_id`, `public_id`, `disclosure_config` |
| **`Bot.public_id`** | *Access* — the only externally visible identifier. Widget, eval, embed snippet all use it via `data-bot-id`. | — |

## Rules

1. **`public_id` lives on Bot only.** Tenant has no `public_id`. Widget and eval resolve by `Bot.public_id`.
2. **Content scopes by `workspace_id`.** Every content table (`documents`, `url_sources`, `chats`, `quick_answers`, `escalation_tickets`, `contact_sessions`, `gap_*`) has `workspace_id NOT NULL`. Tenant only appears on `workspaces`, `users`, and tenant‑owned config (`api_key`, `openai_api_key`).
3. **KYC signing secret lives on Workspace.** Rotating or compromising the secret affects one workspace, not the whole tenant.
4. **MVP cardinality:** exactly one Workspace per Tenant and one Bot per Workspace, both auto‑provisioned on email verification. Schema allows N at both levels; UI is single‑instance until we ship the selector.
5. **Bootstrap atomicity:** `bootstrap_tenant_for_user` creates Tenant + default Workspace + default Bot in one transaction. Users never see "workspace missing" or "bot missing" states.

## Why

- Previous single‑tier model conflated ownership with content scope — any multi‑project or multi‑brand story (one tenant, two isolated knowledge bases) required re‑keying every table.
- `public_id` was duplicated on Tenant and Bot, with eval silently resolving by Tenant while widget resolved by Bot. Same field name, different entities → recurring bugs.
- Putting KYC secrets on Tenant means rotating a secret invalidates tokens for every workspace the tenant ever launches. Moving it to Workspace keeps blast radius small.

## Consequences

- **Migration:** one hard cutover (no prod data). Drops `tenants.public_id`, `tenants.kyc_secret_*`, `bots.tenant_id`; adds `workspaces` table and `workspace_id` on all content tables.
- **Eval:** `eval_sessions.bot_id` now stores `Bot.public_id` (not Tenant's). `get_tenant_eligible_for_widget_chat` is removed; both widget and eval use `get_bot_for_widget_chat`.
- **Frontend:** embed snippet unchanged (`data-bot-id` already maps to `Bot.public_id`). Settings → Widget API moves to workspace scope; MVP hides the workspace selector.
- **Future:** enabling N workspaces per tenant or N bots per workspace is a pure UI change — no further schema migration.

## Non‑goals

- Billing design. Tenant is the billing boundary in principle, but fields are deferred until pricing is decided.
- Team collaboration. Users still have a single `tenant_id`; role/permission model is out of scope.
- Cross‑workspace analytics. Admin metrics remain tenant‑wide for now.
