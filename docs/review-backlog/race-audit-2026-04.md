# Race Audit — 2026-04

## 1. Widget chat/session creation

- Location: `backend/routes/widget.py:137`
- Risk: low. `Chat(session_id=session_id)` uses a fresh UUID and does not coordinate with another mutable row keyed by the same value. A collision would surface as an insert failure, but practical collision probability is negligible.
- Mitigation: probabilistic uniqueness via UUID generation; no shared "check then insert" window on a human-derived key.
- Recommendation: `OK` — no action.

## 2. Client creation (`api_key`, one client per user)

- Location: `backend/clients/service.py:32`
- Risk: medium if unhandled, because concurrent creates for one `user_id` or a rare `api_key` collision would otherwise bubble as a 500 during onboarding.
- Mitigation: DB uniqueness on `clients.user_id` and `clients.api_key`, plus `IntegrityError` handling in `create_client()` at `backend/clients/service.py:58`, which converts the common race into HTTP 409 and lets `ensure_client_for_user()` re-read the winner row.
- Recommendation: `OK` — mitigation is present. Optional separate follow-up: add an explicit regression test for concurrent `ensure_client_for_user()`.

## 3. User registration (`email`)

- Location: `backend/auth/service.py:18`
- Risk: medium if unhandled, because two concurrent registrations for the same email would otherwise produce a 500 instead of a clean conflict response.
- Mitigation: pre-check plus DB unique index on `users.email`, with `IntegrityError` converted to HTTP 409 in `register_user()` at `backend/auth/service.py:38`.
- Recommendation: `OK` — mitigation is present. Optional separate follow-up: add a concurrency test if registration races become a recurring review topic.

## 4. FAQ insert/upsert flow

- Location: `backend/tenant_knowledge/faq_service.py:157`
- Risk: medium for candidate-level duplicate insertion and transaction blast radius. Without isolation, one duplicate or write conflict could roll back the whole extraction batch.
- Mitigation: candidate writes are wrapped in `with db.begin_nested()` and dedupe runs before insert; the pattern matches the same SAVEPOINT-style containment used by `start_user_session`, so one candidate failure does not poison the outer batch transaction.
- Recommendation: `OK` — mitigation is consistent with the preferred pattern. A dedicated concurrency test could be useful later, but no code change is needed in this PR.

## 5. URL source creation

- Location: `backend/documents/url_service.py:549`, `backend/documents/url_service.py:670`, `backend/models.py:330`
- Risk: medium. Duplicate-source prevention is currently only an application-level preflight query on `(client_id, normalized_domain)`. Two concurrent creates can both pass preflight and both commit, which can produce duplicate sources rather than a deterministic 409.
- Mitigation: none at the database level. `preflight_url_source()` checks for an existing row, but `UrlSource` has no corresponding unique constraint and `create_url_source()` does not catch `IntegrityError`.
- Recommendation: `нужен фикс в отдельном issue` — add a DB-enforced uniqueness rule for the intended key (`client_id + normalized_domain` or `client_id + normalized_url`, depending on product semantics) and handle the resulting conflict with the same savepoint / `IntegrityError` pattern.
