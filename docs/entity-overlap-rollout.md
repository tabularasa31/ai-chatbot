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

[**Entity-overlap retrieval channel**](https://eu.posthog.com/project/162137/dashboard/651556) — pre-built PostHog dashboard with seven tiles wired against `entity_overlap.channel_used`:

| Tile | What it answers |
|---|---|
| Channel uses — total (30d) | Did the channel run at all? Drops to ~0 when the kill switch is flipped. |
| Channel uses over time | Is volume steady? Step changes usually map to deploys or onboarding events. |
| Adoption — queries with extracted entities | What fraction of queries actually engage the channel (`had_query_entities=true`)? |
| NER + entity-search latency (median / p95) | Is p95 staying under the `NER_QUERY_TIMEOUT_SECONDS` budget? |
| Candidate count distribution | Is the chunk-side index returning useful matches, or mostly zeros? |
| Channel uses by tenant | Which tenants generate signal — useful when per-tenant rollout lands. |
| Avg query entities per turn | Has NER quality regressed (sudden drop)? |

The dashboard starts empty and fills in as soon as the demo deploy receives traffic.

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
