# Post-merge regression eval runbook

When a backend PR claims to improve a measurable metric (latency, accuracy, cost), run this checklist after merge to confirm the win and surface regressions early.

Last validated: PR [#522](https://github.com/tabularasa31/ai-chatbot/pull/522) — parallel embed + relevance guard. ClickUp [86exdt3ft](https://app.clickup.com/t/86exdt3ft). **Default dataset is now CHAT9-RU-20** (Chat9 internal docs, 20 cases). RU-54 / EN-98 are deprecated — TurboFlare became a real client.

---

## When to use

Use this for PRs where:

- A specific metric delta is claimed in the PR body (e.g. "−100–300 ms p50", "+5 pp pass rate", "−30% token cost").
- The change touches the chat hot path (`backend/chat/handlers/`, `backend/search/`, `backend/guards/`).
- The change does not show up in unit tests (latency, prompt quality, retrieval ranking).

Skip for: typo fixes, refactors with no behaviour delta, doc-only changes, frontend-only changes.

---

## Prerequisites

| Item | Where to find it |
|------|------------------|
| Before-merge baseline | Run an eval **before** opening the PR. Save artifacts (json + csv) to `~/Downloads/<dataset>-<date>.{json,csv}`. |
| Eval scripts | `~/Projects/ai-chatbot-eval-local/scripts/` — kept **outside the repo** (contains tenant API keys in usage examples). Never commit these to the public repo. |
| Chat9 docs eval script | `~/Projects/ai-chatbot-eval-local/scripts/eval_chat9_docs_ru.py` — **primary eval script** for CHAT9-RU-20 (Chat9 internal test bot). |
| Baseline runs | `~/Projects/ai-chatbot-eval-local/baselines/` — historical before-merge runs, also local-only. |
| Anthropic key (judge) | `~/.zshrc` → `ANTHROPIC_API_KEY` |
| Chat9 test bot API key | `CHAT9_TEST_API_KEY` — widget API key (`ck_…`) for the Chat9 internal test tenant. |
| Chat9 test bot ID | `CHAT9_TEST_BOT_ID` — `public_id` of the Chat9 internal test bot. |

If no baseline exists for the dataset you want to use: run the eval against the pre-PR commit first (or use the most recent prod run from the eval-results archive).

---

## Procedure

### 1. Wait for prod deploy

Railway auto-deploys on merge to `main` via `Procfile` release step. Confirm:

```bash
git fetch origin main
git log --oneline origin/main -3   # confirm merge commit on top
curl -s https://api.getchat9.live/health
# {"status":"ok"} — deploy is up
```

If you need to be sure the new code is live, hit any endpoint that surfaces a behaviour the PR changed (e.g. `/widget/chat` and check Langfuse trace for the new span).

### 2. Run the eval — Chat9 only, sequential

Use the `run_eval.sh` wrapper. It calls `eval_chat9_docs_ru.py` sequentially and ingests the result into the local SQLite store in one step:

```bash
cd ~/Projects/ai-chatbot-eval-local
export ANTHROPIC_API_KEY="..."       # from ~/.zshrc
export CHAT9_TEST_API_KEY="..."      # widget API key for Chat9 internal test bot
export CHAT9_TEST_BOT_ID="..."       # public_id of Chat9 internal test bot
./run_eval.sh after-PR-<NUM> --pr <NUM>
# defaults to --dataset CHAT9-RU-20
```

**Why sequential** (`--workers 1`): running parallel cases changes server contention and invalidates the latency comparison.

Expected runtime: ~4 min for CHAT9-RU-20 (20 cases). Run it in the background; don't burn cache polling for completion.

For the **before-merge baseline**: run the same wrapper *before* opening the PR, with `--label before-PR-<NUM>`. The post-merge run then has something to compare against.

### 3. Compare metrics

```bash
# Standard diff: writes ClickUp-ready markdown to stdout.
scripts/eval_cli.py diff --before before-PR-<NUM> --after after-PR-<NUM> --markdown > /tmp/report.md

# Quick text view in terminal:
scripts/eval_cli.py diff --before before-PR-<NUM> --after after-PR-<NUM>

# Sanity-check trend across recent runs:
scripts/eval_cli.py trend --dataset CHAT9-RU-20 --metric latency_p50

# When a "new fail" looks suspicious, check if the case has been flaky:
scripts/eval_cli.py case-history ru_on_01 --dataset CHAT9-RU-20
```

Note: on a 20-case set, a 1-case swing = 5% — the noise floor is higher than RU-54. Apply Rule 2 conservatively.

The `diff --markdown` output drops directly into a ClickUp comment with the same shape as [86exdt3ft](https://app.clickup.com/t/86exdt3ft) (latency table → category breakdown → verdict shifts → top-10 wins / top-5 regressions).

### 4. Interpret — three rules

**Rule 1: Latency wins are credible if monotonic across the distribution.** A real win shows up as negative delta on avg, p50, p75, p95, max. If only avg moves but p95 doesn't, suspect a few outliers, not a systemic improvement.

**Rule 2: Pass-rate shifts within ±5% on a 50–100 case set are judge variance, not regressions.** Without a fixed seed and N≥3 runs, a 4-case swing on a 54-case set is inside the noise floor of LLM-as-judge evals (~5–10% per Anthropic's own internal calibration).

**Rule 3: Attribute fails to the merge only if the failure mode is mechanically explained by the change.** Read the PR diff, then read the failed answer + judge reason. If the failure mode (e.g. "bot returned wrong language", "bot escalated when it shouldn't") is something the changed code path could produce, treat as real. If it's "judge dinged the bot for mentioning Cloudflare in passing" and the diff doesn't touch generation prompts, treat as judge variance.

If unclear, escalate to multi-run: rerun the failing cases ×3 and compute pass-rate per case. Cases that pass ≥2 out of 3 are noise; cases that fail all 3 are real.

### 5. Post the report to ClickUp

Use the template in [Appendix A](#appendix-a--report-template). Attach JSON + CSV artifacts. Update task status: `In Progress` → `In Review` → `Done` after owner sign-off.

---

## Appendix A — Report template

Paste this into the ClickUp comment, fill in the blanks. Russian, because that's how the team reads these reports.

````markdown
## Результат прогона after-merge

<!-- workers=1 sequential to match before-run conditions -->
Sequential run (workers=1).

**Артефакты:**
- Скрипт: `<path to eval script>`
- After: `<path to after-merge json/csv>` (sequential, <duration>)
- Before (reference): `<path to before-merge json/csv>`

## Latency — целевой эффект merge

| metric |  before |  after |  delta |    % |
|--------|--------:|-------:|-------:|-----:|
| avg    | <X> | <X> | **<±X>** | **<±X%>** |
| p50    | <X> | <X> | **<±X>** | **<±X%>** |
| p75    | <X> | <X> | <±X>     | <±X%> |
| p95    | <X> | <X> | <±X>     | <±X%> |
| max    | <X> | <X> | <±X>     | <±X%> |

<!-- One sentence: did p50 hit the PR's claimed delta? Is the win monotonic? -->

## Pass rate

|       | before | after | Δ |
|-------|-------:|------:|--:|
| Total | <a/N>  | <b/N> | <±k> |

**Стабильные fail (<k> кейсов)** — те же провалы и до, и после: `<list>`. Известные проблемы KB, не связаны с merge.

**Только before fail (<k>):** `<list>` → теперь pass.

**Только after fail (<k> — кандидаты в регрессии):** `<list>`.

### Атрибуция кандидатов в регрессии:

<!-- For each "Only after fail" case, decide: real regression or judge variance? -->
<!-- Use Rule 3 from the runbook. Cite specific PR-touched code paths. -->

1. `<case_id>` — <real | variance>: <one-sentence reason>
2. ...

## Acceptance — итог

- [x] Прогнан <dataset> на проде после деплоя `<commit>`.
- [<x|>] <metric> hit / missed target (target: <X>; actual: <Y>).
- [<x|>] Pass rate: <delta> within / outside noise floor.
- [x] Артефакты сохранены.
- [x] Дельты выписаны выше.

## Рекомендация

<!-- One of: -->
<!--   "Merge выполнил задачу — <metric> win получен. Можно закрыть в Done." -->
<!--   "Merge показал ожидаемый <metric> win, но обнаружена реальная регрессия в <X>. Открываю follow-up таску." -->
<!--   "Результаты в шумовом коридоре — нужен multi-run для подтверждения. Открываю follow-up." -->
````

---

## Appendix B — Known limitations

- **No fixed seed.** Chat completions and the judge are stochastic. ±5% pass-rate variance is normal. To eliminate, set `temperature=0` in both the bot and the judge for eval runs — requires a feature flag we don't currently have.
- **Small dataset.** CHAT9-RU-20 has 20 cases; 1 case swing = 5% pass-rate shift. For high-confidence latency/quality claims, expand to CHAT9-RU-50 first (see Appendix C).
- **RU-54 / EN-98 deprecated.** TurboFlare became a real client — running evals there risks touching live user data. These datasets remain in the store for historical diffs but should not be used for new runs.
- **Judge model drift.** `claude-haiku-4-5-20251001` is pinned in the script. If you upgrade the judge, baselines become invalid — re-run before-merge baseline.
- **Cold start spikes.** First case after Railway idle eats ~25–30s vs ~13s steady. Discard the first case from latency stats if you suspect cold start, or run a warmup call before the eval.
- **No statistical test.** This runbook reports raw deltas, not significance. For high-stakes decisions, run N≥3 before and N≥3 after, then run Welch's t-test on the per-case latencies.

---

## Appendix C — Common eval datasets

| Dataset     | File | Size | Language | Corpus | Status |
|-------------|------|-----:|----------|--------|--------|
| CHAT9-RU-20 | `~/Projects/ai-chatbot-eval-local/scripts/eval_chat9_docs_ru.py` → `TEST_CASES_CHAT9_RU` | 20 | RU | Chat9 internal docs (`frontend/content/docs/*.mdx`) | **Active — default** |
| CHAT9-RU-50 | _planned expansion of CHAT9-RU-20_ | 50 | RU | Same corpus, 6 categories, ~15/10/10/8/5/2 | Pending |
| RU-54       | `~/Projects/ai-chatbot-eval-local/scripts/eval_head_to_head_ru.py` → `TEST_CASES_RU` | 54 | RU | TurboFlare | **Deprecated** — TurboFlare is now a real client |
| EN-98       | `~/Projects/ai-chatbot-eval-local/scripts/eval_turboflare.py` → `TEST_CASES`         | 98 | EN | TurboFlare | **Deprecated** — same reason |

To add a new dataset: copy `eval_chat9_docs_ru.py`, replace `TEST_CASES_CHAT9_RU` and `DATASET_NAME`, update the judge system prompt if the tenant domain differs.
