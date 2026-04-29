"""Multi-hop / brand-specific retrieval eval set.

Step 3 of the entity-aware retrieval epic (ClickUp 86exe5pjx). Used to
measure baseline recall@5 / MRR / precision@5 on the current hybrid
retriever (pgvector + BM25 + RRF) before the entity-overlap channel
lands. After Step 5 ships, the same harness reruns and the delta is the
prod-rollout signal.

Why a synthetic corpus?
- Real tenant FAQs are private; a fixed, shareable corpus is auditable
  and reproducible across runs.
- Lexical patterns (brand names, error codes, endpoints) are what we
  need to stress; a SaaS-flavored synthetic corpus exhibits exactly the
  shapes that hurt today's retriever (composite questions, rare names,
  short codes).

Categories (~30 queries total):
- ``multi_hop``: composite question requiring 2+ chunks to fully answer.
  Today's retriever often returns only one half; this is the headline
  category for the entity-overlap channel.
- ``brand_specific``: question name-drops a product / plan / integration.
  Dense embedding tends to dilute rare brand tokens.
- ``error_or_endpoint``: error codes, HTTP paths, parameter names —
  short rare tokens BM25 over-weights and dense smooths over.
- ``control_no_entities``: generic "how do I get started" style. The
  entity channel must NOT regress these.

Each ``Chunk`` has a stable ``chunk_id`` and a ``document_id``. Each
``Query`` carries ``gold_chunk_ids`` (set semantics) — for multi-hop,
all listed chunks are relevant and the harness measures whether the
top-k surfaces the union.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    document_id: str
    text: str
    # Ground-truth entities a well-functioning NER should extract from
    # ``text``. Used by the entity-overlap eval (Step 5+) to mock
    # ``extract_entities_from_passage`` deterministically — the harness
    # measures retriever quality given good NER, not NER quality. Empty
    # tuple is allowed and is the right answer for control / generic chunks.
    entities: tuple[str, ...] = ()


@dataclass(frozen=True)
class Query:
    query_id: str
    category: str  # multi_hop | brand_specific | error_or_endpoint | control_no_entities
    text: str
    gold_chunk_ids: tuple[str, ...]
    # Ground-truth entities for the query side. Mirrors ``Chunk.entities``.
    # Empty tuple means "no entities" — control_no_entities queries are
    # exactly this case and the retriever must not regress them.
    query_entities: tuple[str, ...] = ()


# ── Synthetic SaaS corpus ────────────────────────────────────────────────────
# Documents are SaaS-flavored: pricing, integrations, API errors, generic FAQs.
# Brand names use clearly fictional placeholders (Acme / Foo / Bar / Baz)
# matching the convention in backend/knowledge/prompts.py.

CHUNKS: tuple[Chunk, ...] = (
    # ── Pricing & plans ────────────────────────────────────────────────────
    Chunk(
        "pricing-pro-cost",
        "doc-pricing",
        "The Pro plan in Acme CRM costs $59 per month per seat when billed monthly. "
        "An annual subscription brings the price down to $49 per seat per month.",
        entities=("Pro plan", "Acme CRM"),
    ),
    Chunk(
        "pricing-pro-features",
        "doc-pricing",
        "The Pro plan includes integration with FooChat, BarMail, and BazDrive. "
        "Higher API rate limits and priority email support are also included.",
        entities=("Pro plan", "FooChat", "BarMail", "BazDrive"),
    ),
    Chunk(
        "pricing-enterprise",
        "doc-pricing",
        "The Enterprise plan adds SAML SSO, audit logs, a dedicated success manager, "
        "and a 99.95% uptime SLA. Pricing is custom — contact sales for a quote.",
        entities=("Enterprise plan", "SAML SSO"),
    ),
    Chunk(
        "pricing-free-tier",
        "doc-pricing",
        "The Free tier allows up to 100 contacts and 1 user. "
        "It is intended for evaluation and small personal projects.",
        entities=("Free tier",),
    ),
    # ── FooChat integration ───────────────────────────────────────────────
    Chunk(
        "integration-foochat-setup",
        "doc-foochat",
        "To connect FooChat, open Settings → Integrations and click Connect FooChat. "
        "You will be redirected to FooChat to authorize the OAuth scope chat:write.",
        entities=("FooChat", "OAuth"),
    ),
    Chunk(
        "integration-foochat-webhook",
        "doc-foochat",
        "FooChat sends events to our webhook at /v1/webhooks/foochat. "
        "Configure the signing secret in Settings → Integrations → FooChat → Security.",
        entities=("FooChat", "webhook"),
    ),
    # ── BarMail integration ───────────────────────────────────────────────
    Chunk(
        "integration-barmail-setup",
        "doc-barmail",
        "BarMail integration syncs your inbox into Acme CRM. "
        "Authorize via Settings → Integrations → BarMail. SMTP credentials are not required.",
        entities=("BarMail", "Acme CRM"),
    ),
    # ── BazDrive integration ──────────────────────────────────────────────
    Chunk(
        "integration-bazdrive-setup",
        "doc-bazdrive",
        "BazDrive lets you attach cloud files to contacts. "
        "After connecting, choose a default folder under Settings → Integrations → BazDrive.",
        entities=("BazDrive",),
    ),
    # ── API errors ────────────────────────────────────────────────────────
    Chunk(
        "api-error-401",
        "doc-errors",
        "A 401 Unauthorized response means the access token is missing, expired, or revoked. "
        "Refresh the token via POST /v1/auth/refresh.",
        entities=("401", "/v1/auth/refresh"),
    ),
    Chunk(
        "api-error-429",
        "doc-errors",
        "A 429 Too Many Requests response means you exceeded the rate limit "
        "of 100 requests per minute on the Pro plan. Back off and retry with exponential delay.",
        entities=("429", "Pro plan"),
    ),
    Chunk(
        "api-error-500",
        "doc-errors",
        "A 500 Internal Server Error is rare and indicates a server-side issue. "
        "Retry once; if it persists, contact support with the request ID from the response.",
        entities=("500",),
    ),
    # ── Auth flow ─────────────────────────────────────────────────────────
    Chunk(
        "auth-oauth-flow",
        "doc-auth",
        "To authenticate with the OAuth 2.0 flow, send a POST request to /v1/auth/token "
        "with client_id, client_secret, and grant_type=client_credentials.",
        entities=("OAuth 2.0", "/v1/auth/token", "grant_type", "client_id", "client_secret"),
    ),
    Chunk(
        "auth-token-refresh",
        "doc-auth",
        "Access tokens expire after 60 minutes. Use POST /v1/auth/refresh with the "
        "refresh_token to obtain a new access token without re-prompting the user.",
        entities=("/v1/auth/refresh", "refresh_token"),
    ),
    # ── Webhooks ──────────────────────────────────────────────────────────
    Chunk(
        "webhooks-overview",
        "doc-webhooks",
        "Webhooks let your app receive real-time events. Configure endpoint URLs under "
        "Settings → Webhooks. Each delivery is signed with HMAC-SHA256 using the signing secret.",
        entities=("webhook", "HMAC-SHA256"),
    ),
    # ── Onboarding / generic ──────────────────────────────────────────────
    # No entity tags — generic onboarding text. Tests that the entity
    # channel does not lift control queries via spurious matches.
    Chunk(
        "onboarding-getting-started",
        "doc-onboarding",
        "To get started, sign up at acme.example, verify your email, then invite teammates "
        "from the Team page. The setup wizard walks you through importing contacts.",
    ),
    Chunk(
        "onboarding-import-contacts",
        "doc-onboarding",
        "You can import contacts via CSV upload or by syncing from BarMail. "
        "CSV files must include at minimum a name column and an email column.",
        entities=("BarMail", "CSV"),
    ),
    # ── Account & password ────────────────────────────────────────────────
    Chunk(
        "account-reset-password",
        "doc-account",
        "If you forgot your password, use the Forgot password link on the sign-in page. "
        "We will email a reset link valid for 30 minutes.",
    ),
    Chunk(
        "account-2fa",
        "doc-account",
        "Two-factor authentication is available under Settings → Security. "
        "We support TOTP apps such as Authy and Google Authenticator.",
        entities=("two-factor authentication", "TOTP"),
    ),
    # ── SAML / SSO ────────────────────────────────────────────────────────
    Chunk(
        "sso-saml-overview",
        "doc-sso",
        "SAML SSO is available on the Enterprise plan only. "
        "Configure your identity provider with the metadata XML from Settings → SSO.",
        entities=("SAML SSO", "Enterprise plan"),
    ),
    # ── Data & exports ────────────────────────────────────────────────────
    Chunk(
        "data-export",
        "doc-data",
        "You can export contacts, deals, and activity logs as CSV files from the "
        "Data → Export page. Exports up to 50,000 rows complete within a few minutes.",
        entities=("CSV",),
    ),
    # ── Support & SLA ─────────────────────────────────────────────────────
    Chunk(
        "support-sla",
        "doc-support",
        "Pro customers get email support with a target first-response time of 24 hours. "
        "Enterprise customers also get a dedicated Slack channel and a 4-hour SLA.",
        entities=("Pro", "Enterprise", "Slack"),
    ),
    # ── Billing & invoices ────────────────────────────────────────────────
    Chunk(
        "billing-invoices",
        "doc-billing",
        "Invoices are emailed to the billing contact on the first of each month. "
        "You can also download past invoices from Settings → Billing → Invoices.",
    ),
    Chunk(
        "billing-payment-methods",
        "doc-billing",
        "We accept all major credit cards and ACH transfers for US-based customers. "
        "Wire transfers are available for annual Enterprise contracts.",
        entities=("ACH", "Enterprise"),
    ),
)


# ── Queries ──────────────────────────────────────────────────────────────────
# 30 queries across 4 categories. Gold labels are conservative — for multi_hop
# we list every chunk the answer needs, for the others we list 1-2 best chunks.

QUERIES: tuple[Query, ...] = (
    # ── multi_hop (8) ──────────────────────────────────────────────────────
    Query(
        "mh-pro-and-429",
        "multi_hop",
        "How much does the Pro plan cost and what does a 429 error mean?",
        ("pricing-pro-cost", "api-error-429"),
        query_entities=("Pro plan", "429"),
    ),
    Query(
        "mh-foochat-cost",
        "multi_hop",
        "How do I connect FooChat and is it included in the Pro plan?",
        ("integration-foochat-setup", "pricing-pro-features"),
        query_entities=("FooChat", "Pro plan"),
    ),
    Query(
        "mh-saml-enterprise-price",
        "multi_hop",
        "Is SAML SSO supported and which plan do I need?",
        ("sso-saml-overview", "pricing-enterprise"),
        query_entities=("SAML SSO", "Enterprise plan"),
    ),
    Query(
        "mh-401-refresh-flow",
        "multi_hop",
        "I am getting 401 errors — how do I refresh my access token?",
        ("api-error-401", "auth-token-refresh"),
        query_entities=("401",),
    ),
    Query(
        "mh-import-and-barmail",
        "multi_hop",
        "Can I import contacts from BarMail and what file format is supported?",
        ("integration-barmail-setup", "onboarding-import-contacts"),
        query_entities=("BarMail",),
    ),
    Query(
        "mh-export-and-data",
        "multi_hop",
        "How do I export my contacts to CSV and is there a row limit?",
        ("data-export",),
        query_entities=("CSV",),
    ),
    Query(
        "mh-sla-and-enterprise",
        "multi_hop",
        "What is the support SLA for Enterprise and is there a Slack channel?",
        ("support-sla", "pricing-enterprise"),
        query_entities=("Enterprise",),
    ),
    Query(
        "mh-2fa-and-password",
        "multi_hop",
        "How do I enable two-factor auth and reset my password?",
        ("account-2fa", "account-reset-password"),
        query_entities=("two-factor authentication",),
    ),
    # ── brand_specific (8) ─────────────────────────────────────────────────
    Query(
        "br-foochat-webhook",
        "brand_specific",
        "Where do I configure the FooChat webhook signing secret?",
        ("integration-foochat-webhook",),
        query_entities=("FooChat", "webhook"),
    ),
    Query(
        "br-bazdrive",
        "brand_specific",
        "How do I attach files from BazDrive to a contact?",
        ("integration-bazdrive-setup",),
        query_entities=("BazDrive",),
    ),
    Query(
        "br-barmail",
        "brand_specific",
        "How do I connect BarMail to Acme CRM?",
        ("integration-barmail-setup",),
        query_entities=("BarMail", "Acme CRM"),
    ),
    Query(
        "br-pro-plan-features",
        "brand_specific",
        "What integrations does the Pro plan include?",
        ("pricing-pro-features",),
        query_entities=("Pro plan",),
    ),
    Query(
        "br-acme-signup",
        "brand_specific",
        "How do I sign up for Acme CRM?",
        ("onboarding-getting-started",),
        query_entities=("Acme CRM",),
    ),
    Query(
        "br-enterprise-sla",
        "brand_specific",
        "What is the Enterprise plan uptime SLA?",
        ("pricing-enterprise",),
        query_entities=("Enterprise plan",),
    ),
    Query(
        "br-free-tier-limits",
        "brand_specific",
        "What are the limits of the Free tier?",
        ("pricing-free-tier",),
        query_entities=("Free tier",),
    ),
    Query(
        "br-saml-setup",
        "brand_specific",
        "How do I set up SAML SSO with my identity provider?",
        ("sso-saml-overview",),
        query_entities=("SAML SSO",),
    ),
    # ── error_or_endpoint (8) ──────────────────────────────────────────────
    Query(
        "err-401",
        "error_or_endpoint",
        "What does error 401 mean?",
        ("api-error-401",),
        query_entities=("401",),
    ),
    Query(
        "err-429",
        "error_or_endpoint",
        "What does HTTP 429 mean and what is the rate limit?",
        ("api-error-429",),
        query_entities=("429",),
    ),
    Query(
        "err-500",
        "error_or_endpoint",
        "I am getting 500 errors, what should I do?",
        ("api-error-500",),
        query_entities=("500",),
    ),
    Query(
        "err-auth-token-endpoint",
        "error_or_endpoint",
        "What does POST /v1/auth/token expect in its body?",
        ("auth-oauth-flow",),
        query_entities=("/v1/auth/token",),
    ),
    Query(
        "err-foochat-webhook-path",
        "error_or_endpoint",
        "What is the webhook path used by FooChat?",
        ("integration-foochat-webhook",),
        query_entities=("FooChat", "webhook"),
    ),
    Query(
        "err-token-expiry",
        "error_or_endpoint",
        "How long are access tokens valid?",
        ("auth-token-refresh",),
    ),
    Query(
        "err-hmac-signing",
        "error_or_endpoint",
        "How are webhook deliveries signed?",
        ("webhooks-overview",),
        query_entities=("webhook",),
    ),
    Query(
        "err-oauth-grant-type",
        "error_or_endpoint",
        "Which grant_type does the OAuth 2.0 token endpoint accept?",
        ("auth-oauth-flow",),
        query_entities=("OAuth 2.0", "grant_type"),
    ),
    # ── control_no_entities (6) ────────────────────────────────────────────
    Query(
        "ctl-getting-started",
        "control_no_entities",
        "How do I get started?",
        ("onboarding-getting-started",),
    ),
    Query(
        "ctl-forgot-password",
        "control_no_entities",
        "I forgot my password, what do I do?",
        ("account-reset-password",),
    ),
    Query(
        "ctl-invoices",
        "control_no_entities",
        "Where can I find my invoices?",
        ("billing-invoices",),
    ),
    Query(
        "ctl-payment-methods",
        "control_no_entities",
        "What payment methods do you accept?",
        ("billing-payment-methods",),
    ),
    Query(
        # Intentionally NO query_entities here. The control_no_entities
        # bucket is the regression guard for "empty NER falls back to
        # two-channel RRF cleanly" — assigning entities to even one
        # case contaminates that signal. Real NER would extract
        # "two-factor authentication" from this query, but for the eval
        # we model this case as the baseline-NER-empty path.
        "ctl-2fa",
        "control_no_entities",
        "How do I turn on two-factor authentication?",
        ("account-2fa",),
    ),
    Query(
        "ctl-export-data",
        "control_no_entities",
        "How can I download my data?",
        ("data-export",),
    ),
)


CATEGORIES: tuple[str, ...] = (
    "multi_hop",
    "brand_specific",
    "error_or_endpoint",
    "control_no_entities",
)


def queries_by_category() -> dict[str, list[Query]]:
    """Group queries by category for per-category metric reporting."""
    grouped: dict[str, list[Query]] = {c: [] for c in CATEGORIES}
    for q in QUERIES:
        grouped[q.category].append(q)
    return grouped


def chunk_by_id() -> dict[str, Chunk]:
    return {c.chunk_id: c for c in CHUNKS}


# Sanity: every gold chunk_id must exist in the corpus.
def _validate_dataset() -> None:
    ids = {c.chunk_id for c in CHUNKS}
    for q in QUERIES:
        for gid in q.gold_chunk_ids:
            if gid not in ids:
                raise AssertionError(
                    f"Query {q.query_id!r} references unknown chunk_id {gid!r}"
                )


_validate_dataset()
