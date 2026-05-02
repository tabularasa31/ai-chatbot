# Tenant settings JSON audit

Date: 2026-05-02

Scope: inventory every live backend read from `Tenant.settings` and classify the
stored keys as `keep`, `remove`, or `migrate`. This is an audit only; no runtime
code changes are part of this document.

## Search coverage

Commands used from the repository root:

```bash
rg -n "tenant\.settings|settings\.get\(|\[\"settings\"\]|isinstance\([^\n]*settings[^\n]*dict" backend
rg -n "\"redaction\"|\"support\"|\"retrieval\"|\"contradiction_adjudication\"|optional_entity_types|l2_email|escalation_language" backend tests -g'*.py'
rg -n "CONTRADICTION_ADJUDICATION|OPENAI|MODEL|RETRIEVAL|RERANK|BM25|VALIDATION|LOCALIZATION|WIDGET|GUARD|THRESHOLD" backend/core/config.py
```

The only live `Tenant.settings` key families read by backend code are:

- `redaction.optional_entity_types`
- `support.l2_email`
- `support.escalation_language`
- `retrieval.contradiction_adjudication.enabled`

`Tenant.settings` itself is defined as a generic JSON column at
`backend/models/tenant.py:34`.

## Inventory

| Key path | Read locations | Behavior | Global env duplicate | Per-tenant meaning | Category |
|---|---|---|---|---|---|
| `redaction.optional_entity_types` | `backend/privacy_config.py:17`, `backend/privacy_config.py:19`, consumed through `backend/tenants/service.py:155`, `backend/escalation/service.py:79`, `backend/chat/history_service.py:30`, `backend/scripts/backfill_pii_storage.py:16`, `backend/scripts/backfill_pii_storage.py:23` | Selects optional PII entity classes that the tenant wants redacted in chat persistence, escalation tickets, chat history previews, and the historical PII backfill script. Invalid entity names are filtered against `OPTIONAL_ENTITY_TYPES`. | No. Defaults are code constants in `backend/privacy_config.py:9` and `backend/chat/pii.py`, not env. | Yes. This is a tenant privacy policy / compliance knob. Different tenants can reasonably choose stricter or looser optional redaction. | `keep` now; consider `migrate` later if settings JSON is removed wholesale. |
| `support.l2_email` | `backend/support_config.py:35`, `backend/support_config.py:39`, consumed through `backend/tenants/service.py:179`, `backend/escalation/service.py:472` | Tenant owner can configure the Level 2 support inbox used for escalation notification emails. If absent, the notification falls back to the tenant owner email. | No. `EMAIL_FROM` is sender-side infrastructure, not the recipient. | Yes. It is customer/business configuration and naturally differs per tenant. | `keep` as product setting; `migrate` to a typed support settings table/columns if we want to retire generic JSON. |
| `support.escalation_language` | `backend/support_config.py:35`, `backend/support_config.py:40`, consumed through `backend/tenants/service.py:179`, `backend/chat/language_context.py:172`, `backend/escalation/service.py:806` | Tenant can choose the language for tenant-side escalation artifacts. Chat responses still follow the user's language; this value feeds escalation-side language context. | No global env duplicate. However, it overlaps with `TenantProfile.escalation_language` at `backend/models/tenant_profile.py:25`, and runtime falls back from settings to the profile field in `backend/chat/language_context.py:191` and `backend/escalation/service.py:814`. | Yes, but the current storage split is muddled: one logical setting has both a tenant-managed JSON location and an extracted/profile column fallback. | `migrate`. Pick one typed source of truth, preferably a support settings field/table, then remove the fallback ambiguity. |
| `retrieval.contradiction_adjudication.enabled` | `backend/search/service.py:681`, `backend/search/service.py:684`, `backend/search/service.py:689`, used by `_build_contradiction_adjudication_evidence` at `backend/search/service.py:749` | Temporary per-tenant opt-out for contradiction adjudication. Missing/malformed settings default to enabled; only explicit `false` disables it. Tests at `tests/test_search.py:2356` through `tests/test_search.py:2384` document that default-on behavior. | Yes. The global `CONTRADICTION_ADJUDICATION_ENABLED` gate is defined at `backend/core/config.py:145`. Related global knobs for model, max facts, preview chars, max tokens, and filter cap are at `backend/core/config.py:149` through `backend/core/config.py:167`. | Weak. This is an operational rollout gate, not a customer-owned business setting. Keeping it in tenant JSON creates hidden state that overrides global rollout intent. | `remove`. Delete the tenant-level override after contradiction adjudication is stable/default-on. |

## Non-live or misleading settings

- `settings.language` appears in `tests/test_models.py:32` as fixture data, but no
  backend path reads `Tenant.settings["language"]` or `Tenant.settings.get("language")`.
  Runtime language behavior uses chat/user context plus support escalation language,
  not this key.
- BYO OpenAI key is not stored in `Tenant.settings`; it is the typed
  `Tenant.openai_api_key` column at `backend/models/tenant.py:29`.
- KYC secret state is not stored in `Tenant.settings`; it uses typed tenant columns
  at `backend/models/tenant.py:30` through `backend/models/tenant.py:33`.

## Recommendations

### Keep

Keep `redaction.optional_entity_types` and `support.l2_email` behavior. They are
real tenant-owned settings and do not duplicate env configuration.

If the goal is to reduce JSON usage, migrate them to typed storage instead of
removing the product behavior:

- `redaction.optional_entity_types`: a typed JSON/list column or a small
  `tenant_privacy_settings` table is enough.
- `support.l2_email`: a nullable string column in a `tenant_support_settings`
  table, or a typed nullable column if we keep the model simple.

### Migrate

Migrate `support.escalation_language` to one source of truth.

Current issue: settings JSON has the tenant-managed value, while
`TenantProfile.escalation_language` is another typed location. Runtime checks the
JSON value first, then falls back to the profile column. That makes ownership
unclear: is this a support-team preference, or an extracted tenant-profile fact?

Recommended target:

1. Keep the tenant-managed setting as the authoritative product surface.
2. Move it out of `Tenant.settings` into typed support settings storage.
3. Stop using `TenantProfile.escalation_language` as a runtime fallback for the
   support setting. If profile extraction still needs a language hint, rename it
   to something clearly extracted/non-authoritative or keep it out of runtime
   escalation selection.

### Remove

Remove `retrieval.contradiction_adjudication.enabled` from tenant JSON.

It duplicates the global `CONTRADICTION_ADJUDICATION_ENABLED` rollout gate and
has no durable customer-facing semantics. It also violates the owner's control
goal: a hidden per-tenant JSON opt-out can make production behavior differ from
the global config without being visible in normal env/config review.

## Cleanup order

Recommended as a series of small PRs, not one large PR:

1. Remove contradiction adjudication tenant override.
   - Delete `_tenant_contradiction_adjudication_enabled`.
   - Remove the tenant-level skip branch from contradiction adjudication.
   - Delete tests that preserve the JSON override contract.
   - Keep global env knobs as the only rollout controls.
2. Normalize escalation language ownership.
   - Decide the typed destination (`tenant_support_settings` or a typed column).
   - Backfill from `settings.support.escalation_language`.
   - Update reads/writes to the typed source.
   - Remove the `TenantProfile.escalation_language` runtime fallback or rename
     the profile field if it remains useful as extracted metadata.
3. Optionally migrate the remaining real tenant settings out of JSON.
   - Move `support.l2_email`.
   - Move `redaction.optional_entity_types`.
   - Leave `Tenant.settings` empty only after migrations and API reads no longer
     depend on it.

Do not combine all three steps unless the team explicitly wants a breaking
settings-storage cleanup. The contradiction override removal is independent and
low blast radius; escalation language needs product ownership clarity; privacy
redaction should move last because it touches persistence, history display, and
backfill behavior.
