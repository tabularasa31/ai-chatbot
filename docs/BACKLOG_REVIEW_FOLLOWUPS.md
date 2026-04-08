# Code Review Follow-ups Backlog

Living backlog for unresolved or intentionally deferred follow-up work that came out of code reviews.

Use this document when we want to continue from the latest review-driven hardening pass without re-reading the full PR history.

Last updated: 2026-04-08

---

## How to use this document

- Work items are ordered intentionally: top to bottom is the recommended execution order.
- Each item includes:
  - why it was deferred
  - where to start in the codebase
  - what a good small PR should contain
- When an item is finished:
  - mark it as `Done`
  - link the PR/commit
  - keep a short note about what changed

Status values:
- `Next` — good candidate for the next small PR
- `Later` — useful, but not urgent
- `Conditional` — do only if a real signal appears
- `Done` — finished; keep for history until the next cleanup pass

### Template for new items

Use this template when adding a new deferred review follow-up:

```md
### N. Short item title

- Status: `Next|Later|Conditional|Done`
- Priority: High|Medium|Low
- Source:
  - PR / review / incident / manual audit
- Why deferred:
  - short explanation
- Goal:
  - what the change should achieve
- Expected benefit:
  - concrete payoff
- Risk if postponed:
  - what can go wrong while it stays deferred
- Start here:
  - [/absolute/path/in/repo](/Users/tabularasa/Projects/ai-chatbot/path/to/file)
- Suggested scope for one PR:
  - flat bullet list of intended changes
- Definition of done:
  - flat bullet list of completion criteria
- Notes:
  - optional context, tradeoffs, or links
```

---

## Queue

### 1. FAQ batch embedding

- Status: `Next`
- Priority: High
- Why deferred:
  - the production-risk bugs from the review were fixed first
  - current FAQ ingestion is correct, but still does one embedding request per accepted candidate
  - this is optimization work, not a correctness blocker
- Goal:
  - batch FAQ question embeddings inside the FAQ insert flow so one extraction run can send many questions in one OpenAI embeddings request
- Expected benefit:
  - lower OpenAI request count
  - lower per-document FAQ extraction overhead
  - cleaner path toward future rate-limit/backpressure handling
- Start here:
  - [backend/tenant_knowledge/faq_service.py](/Users/tabularasa/Projects/ai-chatbot/backend/tenant_knowledge/faq_service.py)
  - [backend/tenant_knowledge/extract_tenant_knowledge.py](/Users/tabularasa/Projects/ai-chatbot/backend/tenant_knowledge/extract_tenant_knowledge.py)
  - [tests/test_tenant_knowledge_faq_service.py](/Users/tabularasa/Projects/ai-chatbot/tests/test_tenant_knowledge_faq_service.py)
- Suggested scope for one PR:
  - gather medium/high-confidence candidates first
  - normalize and drop empty candidates before embedding
  - call `embeddings.create(..., input=[...])` in batches
  - keep duplicate detection and savepoint isolation per candidate after vectors are available
  - add or update tests to assert batch call shape and low-confidence skip behavior
- Definition of done:
  - FAQ embedding requests are batched
  - existing duplicate/savepoint semantics stay unchanged
  - targeted FAQ tests pass

### 2. BM25 perf coverage and evidence collection

- Status: `Conditional`
- Priority: Medium
- Why deferred:
  - we already shipped the tenant corpus TTL cache
  - there is no current production signal that BM25 latency is a problem
  - synthetic perf tests can become noisy and expensive to maintain if added too early
- Goal:
  - collect stable evidence about BM25 latency and cache effectiveness before adding heavier performance-only test machinery
- Expected benefit:
  - better decision-making on whether more retrieval optimization is needed
  - avoids premature micro-benchmarking
- Start here:
  - [backend/gap_analyzer/repository.py](/Users/tabularasa/Projects/ai-chatbot/backend/gap_analyzer/repository.py)
  - [tests/test_gap_analyzer_phase5.py](/Users/tabularasa/Projects/ai-chatbot/tests/test_gap_analyzer_phase5.py)
  - [docs/qa/FI-115-query-variant-cost.md](/Users/tabularasa/Projects/ai-chatbot/docs/qa/FI-115-query-variant-cost.md)
- Suggested scope when the signal appears:
  - add lightweight timing instrumentation or counters for cache hit/miss visibility
  - add a bounded perf/regression harness only if latency or DB load justifies it
  - document expected tenant sizes and acceptable latency targets first
- Definition of done:
  - we have real evidence for cache hit rate and BM25 latency
  - any added perf test is deterministic enough for CI or explicitly marked non-CI

### 3. BM25 cold-cache stampede protection

- Status: `Later`
- Priority: Low
- Why deferred:
  - current behavior is acceptable for the present scale
  - the cache intentionally does not hold a lock during DB I/O
  - under concurrent cold-start requests, multiple workers can rebuild the same corpus in parallel
- Current behavior:
  - safe but redundant DB work can happen on the first miss for the same tenant/cache key
- Goal:
  - only if needed, prevent duplicate corpus rebuilds for the same key during a cold miss
- Start here:
  - [backend/gap_analyzer/repository.py](/Users/tabularasa/Projects/ai-chatbot/backend/gap_analyzer/repository.py)
- Suggested future options:
  - per-key in-flight registry
  - promise/future-style coalescing
  - small "double-check after load" coordination helper
- Definition of done:
  - concurrent cold-start misses for the same tenant/key do not all hit the DB
  - implementation stays simple and does not introduce lock contention or deadlocks

---

## Recently closed from PR #280

These were raised during review and are now finished:

- `Done` — FAQ savepoint counters and logging correlation
- `Done` — confidence filter before FAQ embedding work
- `Done` — named health-check constants and empty-document shared scoring path
- `Done` — BM25 empty-query fast path
- `Done` — BM25 tenant corpus TTL cache with tenant invalidation
- `Done` — FAQ confidence threshold single source of truth
- `Done` — embedding cache invalidation order fix
- `Done` — regression coverage for nested sections, empty-token BM25 query, BM25 cache reuse/invalidation, and FAQ confidence edge cases

Related PR:
- [PR #280](https://github.com/tabularasa31/ai-chatbot/pull/280)
