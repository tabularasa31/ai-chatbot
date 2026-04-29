# Entity-overlap retrieval channel — rollout runbook

Step 6 of the entity-aware retrieval epic ([ClickUp 86exe5pjx](https://app.clickup.com/t/86exe5pjx)). This doc covers the operational side: how to turn the channel on/off, what telemetry to watch, and how to extend to a per-tenant rollout when the first pilot client lands.

## What it is

A third RRF channel in the hybrid retriever (`backend/search/service.py`). On every chat turn, an LLM extracts named entities from the user's question; the retriever then surfaces FAQ chunks whose pre-indexed entity list overlaps with the query's. The result list is fused with the existing dense (pgvector) and BM25 channels via RRF.

End-state per the epic: lifts retrieval recall on multi-hop / brand-specific / error-code queries without regressing generic ones.

## Current state

- **Globally enabled by default** since merge of [#TBD] (Step 6).
- **No per-tenant override** — gated by a single global flag.
- **No live clients yet** — feature runs in demo / preview deploys; production data measurement happens at the first pilot.

## Turning it on / off

### Global kill switch

Single env var, both for Railway and local:

```bash
ENTITY_OVERLAP_ENABLED=false   # disable everywhere — no deploy needed
ENTITY_OVERLAP_ENABLED=true    # default; explicit set for clarity
```

When `false`, `_run_candidate_stage` skips the entity NER and entity-overlap search entirely; RRF degrades to today's two-channel formula at zero added cost. No code change, no migration.

### Other tunables

| env var | default | purpose |
|---|---|---|
| `NER_MODEL` | `gpt-4.1-mini` | LLM used for entity extraction (both query and chunk side) |
| `NER_MAX_COMPLETION_TOKENS` | `300` | Token cap on NER output — entities are short, no need for more |
| `NER_QUERY_TIMEOUT_SECONDS` | `2.0` | Hard wall-clock budget for the per-request NER call. Slow NER → return `[]` and fall through to two-channel RRF. |

## What to watch in production

### Dashboard

[**Entity-overlap retrieval channel**](https://eu.posthog.com/project/162137/dashboard/651556) — pre-built PostHog dashboard with seven tiles wired against the `entity_overlap.channel_used` event. Starts empty and fills in as soon as a deploy starts receiving traffic.

Each tile below: **what it shows**, **what "healthy" looks like**, **what to do when it drifts**.

---

#### 1. Channel uses — total (30d)
*Bold number, last 30 days.*

**Shows:** total count of `entity_overlap.channel_used` events, i.e. how many chat turns ran through the entity channel in the window.

**Healthy:** roughly equal to the chat-turn volume in the same period (every successful chat retrieval should fire one event when the flag is on). A non-trivial number means the channel is actually executing.

**Drift signals:**
- **Number = 0** → either no traffic, the kill switch was flipped (`ENTITY_OVERLAP_ENABLED=false`), or the deploy hasn't picked up the flag yet. Cross-check with chat-turn volume on the main observability dashboard.
- **Number ≪ chat turns** → most chats are skipping the channel. Most likely cause: `api_key` was None at retrieval time (no per-tenant OpenAI key). Check `client.openai_api_key` decryption errors in logs.

---

#### 2. Channel uses over time
*Daily line chart, last 30 days.*

**Shows:** day-over-day volume of channel invocations.

**Healthy:** smooth curve that tracks chat-turn volume. Weekday/weekend patterns inherit from chat traffic.

**Drift signals:**
- **Cliff edges** → a deploy. Cross-reference with the deploy log; if the deploy is unrelated to entity-overlap, dig into recent commits to `backend/search/service.py` or `backend/knowledge/`.
- **Sudden jumps** → usually a new tenant onboarded with a large FAQ. Check the per-tenant tile to confirm.
- **Slow downward trend** → tenants disabling the feature one by one (only relevant once per-tenant override lands). Or tenants with expired OpenAI keys.

---

#### 3. Adoption — queries with extracted entities
*Stacked bar by `had_query_entities`, daily.*

**Shows:** how often NER on the user query actually returns at least one entity. False = NER returned `[]` and the entity channel contributes zero votes that turn (RRF reduces to dense + BM25). True = NER produced something and the channel is contributing.

**Healthy:** True share around **60–80%**. Real-world FAQ queries often have at least one product/code/endpoint name to anchor on. The control category in our eval is exactly the True ≈ 0% case — generic "how do I get started" queries — and we expect those to be a minority in production.

**Drift signals:**
- **True < 30% sustained** → NER is missing entities the dataset says exist. Check:
  1. NER prompt is intact (`backend/knowledge/prompts.py` not edited recently?)
  2. Model setting (`NER_MODEL`) wasn't switched to something weaker
  3. Query distribution itself shifted toward generic queries (which is real-world signal, not a bug — but should match your hypothesis)
- **True near 100%** → almost certainly NER hallucinating entities. Spot-check via Langfuse traces (see "Langfuse trace span" below).

---

#### 4. NER + entity-search latency (median / p95)
*Two-series line chart, daily, in milliseconds.*

**Shows:** wall-clock for `extract_entities_from_query` + `entity_overlap_search` combined per chat turn.

**Healthy:**
- median ≈ **400–800 ms** (one `gpt-4.1-mini` call + one indexed PG query)
- p95 ≈ **1200–1700 ms** (well under the 2000 ms `NER_QUERY_TIMEOUT_SECONDS` budget)

**Drift signals:**
- **p95 ≥ NER_QUERY_TIMEOUT_SECONDS (2000 ms) repeatedly** → the wall-clock guard is firing. Each timeout = one chat turn that paid the latency budget for nothing (returned `[]`). Two fixes:
  - Bump `NER_QUERY_TIMEOUT_SECONDS` if you can afford the p95 chat latency increase
  - Drop to a smaller NER model (or eventually a local model) — see runbook tunables
- **median climbs steadily** → OpenAI degradation or cold-cache effects on a large tenant. Check OpenAI status, then the GIN index health (`SELECT pg_size_pretty(pg_relation_size('ix_embeddings_entities_gin'))`).
- **median spikes on deploy** → cold caches; should self-resolve in <10 minutes.

---

#### 5. Candidate count distribution
*Stacked bar by `candidate_count` value, daily.*

**Shows:** for queries where NER produced entities, how many chunks the entity index returned. Bucketed by candidate_count (0, 1, 2, 3, 4, 5+).

**Healthy:** mode around **2–5 candidates**. The per-channel cap is `ENTITY_SEARCH_CANDIDATE_LIMIT = 1000` — we should never see all queries piling at the cap.

**Drift signals:**
- **High share of `candidate_count = 0`** → query NER extracted entities, but none of them appear in any chunk's entity list. Three causes, in order of likelihood:
  1. **Surface-form mismatch** — query NER says `"Pro"`, chunks have `"Pro plan"`. Either side could be normalized; today we don't normalize on either. If this is dominant, consider lowercase / canonicalization on both sides.
  2. **Chunk-side NER missed entities at indexing time** — re-index the affected tenant's documents. The entity column gets rebuilt on every re-embed.
  3. **Tenant has a very small / very generic FAQ** — entity channel just isn't useful for them. Per-tenant disable when that lands.
- **Always near 1000** → very popular entities in the query. Check the cap is still right; consider a smaller `ENTITY_SEARCH_CANDIDATE_LIMIT` if RRF below it doesn't actually consume that many.

---

#### 6. Channel uses by tenant
*Table, last 30 days, top 25 tenants.*

**Shows:** per-tenant volume of channel uses. Most useful once per-tenant override exists (currently the channel is global, so this is mostly an FYI distribution).

**Healthy:** distribution mirrors chat-turn volume per tenant (a tenant with 10× more chats should have ~10× more entity-overlap events).

**Drift signals:**
- **One tenant has 0 events but non-zero chat traffic** → that tenant's `client.openai_api_key` is missing or undecryptable. Check `decrypt_value` errors in logs filtered by tenant id.
- **Hot-tenant outlier with disproportionate volume** → could be a runaway loop or a chat-bomb. Cross-check chat-turn volume; if entity uses ≫ chat turns, retrieval is being called multiple times per turn (likely a regression in the chat handler).
- **Once per-tenant override lands:** use this tile to verify rollout — pilot tenant's count should jump when their flag flips on.

---

#### 7. Avg query entities per turn
*Line chart of `avg(query_entity_count)`, daily.*

**Shows:** mean entities NER pulls from a user question. Our multi-hop eval averages ~1.5 entities/query across the 30-case dataset; production is usually a touch lower because real users ask more open-ended questions.

**Healthy:** **0.8–1.8**.

**Drift signals:**
- **Sudden drop to ~0** → almost certainly the NER prompt or model regressed. Cross-check:
  1. Recent commits to `backend/knowledge/prompts.py`
  2. Recent commits / env-var changes to `NER_MODEL`
  3. Spot-check 2–3 chat traces in Langfuse — does the `entity-overlap-search` span's `query_entities` field have the entities you'd expect?
- **Climb above ~3.0** → NER is hallucinating broad terms as entities. Tightening the prompt's "Skip generic words" instruction or a one-shot update is the fix. Don't tune to specific tenants; this is a model-level concern.

### PostHog event

`entity_overlap.channel_used` fires once per chat turn that runs through the channel. Properties:

| field | meaning |
|---|---|
| `query_entity_count` | how many entities NER extracted from the user's question (0 = NER returned nothing) |
| `had_query_entities` | bool shortcut for `query_entity_count > 0` |
| `candidate_count` | how many chunks the entity index returned (0 = no overlap with any indexed chunk) |
| `duration_ms` | wall-clock for `extract_entities_from_query` + `entity_overlap_search` combined |

**Useful aggregations:**
- `count(had_query_entities=true) / count(*)` — what fraction of queries actually engage the channel. If consistently low (e.g. <30%), NER is missing entities the dataset says exist → revisit prompt / model.
- `p95(duration_ms)` — should stay under the `NER_QUERY_TIMEOUT_SECONDS` budget. If p95 hits the timeout often, either bump the budget or move to a smaller / faster NER model.
- `count(had_query_entities=true AND candidate_count=0)` — entity extracted but nothing in the index matches it. Happens if the chunk-side NER missed it at indexing time. High rate → re-index with a better NER prompt.

### Langfuse trace span

`entity-overlap-search` span attaches to every chat retrieval when the flag is on. Inputs/outputs include the query, the extracted entities, the resulting chunk ranking, and `duration_ms`. Use it for per-conversation forensics (the PostHog event is for population-level aggregation; the trace is for "why did this one query behave that way?").

### Multi-hop eval harness

`make multi-hop-eval` runs the static 30-case dataset through both flag-off (`test_multi_hop_baseline`) and flag-on (`test_multi_hop_with_entity_overlap_channel`) paths and prints recall@5 / MRR / precision@5 side by side. Use this as the regression gate when changing anything in the entity pipeline.

Today's numbers (synthetic embeddings, deterministic):

```
                          baseline (OFF)   entity ON
overall recall@5             0.933          0.933
overall MRR                  0.904          0.926
brand_specific MRR           0.917          1.000
error_or_endpoint recall     0.875          0.875
control_no_entities recall   0.833          0.833
```

The `>= baseline` floors per category are pinned in the test — any future change that drops them fails CI.

## Rolling back if something goes wrong

1. Set `ENTITY_OVERLAP_ENABLED=false` in Railway env vars. Effect: instant, no deploy needed.
2. Restart the backend service so the new env value is picked up by the running workers.
3. Verify in PostHog that `entity_overlap.channel_used` event volume drops to zero.
4. Open an issue with: PostHog dashboard link, Langfuse trace IDs of failing queries, the change that introduced the regression.

The chat hot path has graceful fallback at every layer — even with the flag on and NER hard-failing, retrieval still serves results from dense + BM25. So a "rollback" is rarely time-critical, but the kill switch exists for when it is.

## Future: per-tenant rollout

When the first pilot client lands and we want to flip the channel **only for them** before a wider rollout, follow the `_tenant_contradiction_adjudication_enabled` precedent in `backend/search/service.py` (~line 667):

1. Add `_tenant_entity_overlap_enabled(tenant: Tenant | None) -> bool` reading `tenant.settings["retrieval"]["entity_overlap"]["enabled"]`.
2. Gate the `if settings.entity_overlap_enabled` block in `_run_candidate_stage` with an additional `and _tenant_entity_overlap_enabled(tenant)` check. This requires loading the `Tenant` row in `_run_candidate_stage` (currently only `tenant_id` is threaded through).
3. Decide opt-in vs opt-out semantics:
   - **Opt-in** (mirrors `contradiction_adjudication`): default tenant flag = False. Channel only runs for explicitly-enrolled tenants. Use this if there's any concern about NER cost or quality on production tenants we haven't profiled.
   - **Opt-out**: default tenant flag = True. Channel runs everywhere unless a specific tenant turned it off. Use this if the multi-hop eval delta is large enough that running by default is the safer choice.

The current global default `True` is effectively opt-out at the global level. The per-tenant layer will refine that when added.

A 30-line PR following the contradiction precedent — kept out of this rollout because we don't yet have a pilot tenant to flip it for, and "build infra before there's a use case" loses to "build infra when the case appears."
