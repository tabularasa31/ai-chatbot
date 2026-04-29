# Observability cheatsheet — Chat9

> Quick reference: what to look at and when.

---

## Two tools, two purposes

| Tool | When to use | What it gives you |
|------|-------------|-------------------|
| **Langfuse** | Debugging a *specific* chat turn or eval run | Full trace: retrieval chunks, prompt, generation, latency per span |
| **PostHog — Chat Health** | Tracking *trends* across all tenants | Latency p50/p95, escalation rate, reliability distribution, lang match, feedback ratio |

---

## Langfuse

### Entry points

- **Traces**: every chat turn (sampled per `TRACE_SAMPLE_RATE`; new tenants always traced for first N turns).
- **Dataset CHAT9-RU-20**: 20 regression cases for Chat9 features in Russian.
- **Dataset Runs**: each CI eval pass creates a Run; use the built-in **side-by-side** view to compare two runs.

### Scores per trace (eval runs only)

| Score | Type | Meaning |
|-------|------|---------|
| `pass` | numeric 1.0/0.0 | LLM judge approved the answer |
| `lang_match` | boolean | Response language == `expected_lang` |
| `escalation_match` | boolean | Behavior (answer/decline/escalate) matched `expected_behavior` |
| `hallucination` | boolean | Answer contained one of `must_not_include` patterns |
| `retrieval_recall_at_5` | boolean | At least one `expected_topics` keyword appeared in top-5 chunks |
| `mrr` | numeric 0–1 | Mean reciprocal rank of first relevant chunk |

### Running an eval (Langfuse runner)

```bash
# From ~/Projects/ai-chatbot-eval-local/scripts/
# Push / sync dataset only
python langfuse_runner.py --dataset-only

# Full eval run with 6 scores
python langfuse_runner.py \
    --bot-id $CHAT9_TEST_BOT_ID \
    --api-key $CHAT9_TEST_API_KEY \
    --judge-key $ANTHROPIC_API_KEY \
    --run-label "after-pr-531" \
    --workers 4
```

### Debugging a bad answer

1. Open the trace in Langfuse.
2. Check the **retrieval** span → `chunks_retrieved` and `scores` to see what the RAG pipeline surfaced.
3. Check the **generation** span → `prompt` to verify the system message and context window.
4. Compare `response_language` vs `query_language` in trace metadata.

---

## PostHog — Chat Health dashboard

**Dashboard**: https://eu.posthog.com/project/162137/dashboard/650852

**Filter**: `tenant_id` top-level filter + cohort split by `plan_tier`.  
**Default time range**: last 7 days.

### Tiles

| Tile | Source event | Key property |
|------|-------------|-------------|
| Latency p50/p95 over time | `chat_completed` | `latency_ms` |
| Token cost per tenant per day | `$ai_generation` | `$ai_total_cost_usd` |
| Escalation rate per week | `chat_escalated` / `chat_completed` | ratio |
| Escalation triggers breakdown | `chat_escalated` | `trigger` |
| Language match rate | `chat_completed` | `lang_match` (boolean) |
| Reliability cap_reason distribution | `chat_completed` | `cap_reason` |
| Thumbs-up/down ratio per week | `chat_feedback` | `feedback` (positive/negative) |

### Key event shapes

**`chat_completed`** — emitted on every chat turn:
```
tenant_id, bot_id, chat_id,
latency_ms, model,
lang_match (bool),
cap_reason (null | "not_relevant" | "low_confidence" | …),
reliability_score ("high" | "medium" | "low"),
decision_branch ("answer_with_citations" | "answer_from_faq" | "clarify" | "escalate" | …),
plan_tier ("free" | "pro" | "enterprise" | null)
```

**`chat_escalated`** — emitted when a ticket is created:
```
tenant_id, bot_id, chat_id,
trigger ("no_docs" | "low_confidence" | "answer_rejected" | "user_request"),
reason (full escalation reason string),
plan_tier, priority ("Critical" | "High" | "Medium" | null)
```

**`chat_feedback`** — emitted on thumbs-up / thumbs-down:
```
tenant_id, feedback ("positive" | "negative"),
decision_branch (optional), cap_reason (optional)
```

**`$ai_generation`** — PostHog LLM Observability, one per LLM call:
```
$ai_model, $ai_input_tokens, $ai_output_tokens,
$ai_total_cost_usd, $ai_latency, $ai_cached_tokens
```

### Common queries

**Escalation rate last 7 days:**
```
# In PostHog Insights
Events: chat_escalated | count
÷
Events: chat_completed | count
→ show as % trend
```

**p95 latency by plan tier:**
```
Events: chat_completed
Formula: percentile(latency_ms, 95)
Break down by: plan_tier
```

---

## What's out of scope (deliberate)

- **Alerts** (PagerDuty / Slack thresholds) — after 1–2 weeks of dashboard stabilization to set meaningful baselines.
- **A/B experiments** — covered in the entity-aware retrieval epic.
- **Sentry / error tracking** — separate zone.
