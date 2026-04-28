#!/usr/bin/env python3
"""Head-to-head eval: DocsBot vs Chat9 — TurboFlare dataset (98 cases).

Reuses TEST_CASES and judge logic from eval_turboflare.py.

Usage:
    python scripts/eval_head_to_head.py \
        --judge-key $ANTHROPIC_API_KEY \
        --chat9-api-url https://ai-chatbot-production-6531.up.railway.app \
        --chat9-bot-id ch_f1wlhm22lvqby15xar \
        --chat9-api-key ck_e9355d219b2ffff8721190b1dcf9d56a \
        --docsbot-team-id e7lgGUWYYivxgGgENmdM \
        --docsbot-bot-id CrH3tbWkjZJkD3JT9vi \
        --docsbot-api-key 43b6e7e64c61a89f984c8e67b6806f65c10fb5bbc9c68762fccf12f6c203f297 \
        --output-dir eval-results

    # Smoke-test with 1 case:
    python scripts/eval_head_to_head.py ... --smoke-test
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from statistics import median
from typing import Any

try:
    import anthropic
    import requests
    from tqdm import tqdm
except ImportError as _e:
    sys.exit(
        f"Missing dependency: {_e}\n"
        "Install: pip install anthropic requests tqdm"
    )

# Import test cases and judge machinery from the existing eval script
sys.path.insert(0, os.path.dirname(__file__))
from eval_turboflare import (  # noqa: E402
    TEST_CASES,
    JUDGE_MODEL,
    BOT_TIMEOUT_S,
    JUDGE_MAX_RETRIES,
    _JUDGE_SYSTEM,
    _build_judge_prompt,
    judge_answer,
)

RATE_LIMIT_SLEEP_S = 1.0
DOCSBOT_TIMEOUT_S = 60

# ---------------------------------------------------------------------------
# Chat9 call (same SSE stream approach as eval_turboflare.py)
# ---------------------------------------------------------------------------

def call_chat9(api_url: str, bot_id: str, api_key: str, question: str) -> tuple[str, bool, float]:
    """POST to Chat9 /widget/chat SSE endpoint.

    Returns (answer_text, chat_ended, latency_ms).
    """
    url = f"{api_url}/widget/chat?bot_id={bot_id}"
    headers = {"Accept": "text/event-stream"}
    if api_key:
        headers["X-API-Key"] = api_key

    t0 = time.monotonic()
    try:
        resp = requests.post(
            url,
            json={"message": question},
            stream=True,
            timeout=BOT_TIMEOUT_S,
            headers=headers,
        )
    except requests.exceptions.Timeout:
        return "[TIMEOUT]", False, (time.monotonic() - t0) * 1000
    except requests.exceptions.RequestException as exc:
        return f"[REQUEST_ERROR: {exc}]", False, (time.monotonic() - t0) * 1000

    if resp.status_code != 200:
        try:
            detail = resp.json().get("detail", "")
        except Exception:
            detail = resp.text[:120]
        return f"[HTTP_{resp.status_code}: {detail}]", False, (time.monotonic() - t0) * 1000

    chunks: list[str] = []
    chat_ended = False
    buffer = ""

    try:
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line:
                if buffer.startswith("data:"):
                    data_str = buffer[len("data:"):].strip()
                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        buffer = ""
                        continue
                    etype = event.get("type")
                    if etype == "chunk":
                        chunks.append(event.get("text") or "")
                    elif etype == "done":
                        chat_ended = bool(event.get("chat_ended", False))
                        if not chunks and event.get("text"):
                            chunks.append(event["text"])
                    elif etype == "error":
                        msg = event.get("message", "unknown error")
                        chunks.append(f"[BOT_ERROR: {msg}]")
                buffer = ""
                continue
            buffer = (buffer + "\n" + raw_line).lstrip() if buffer else raw_line
    except requests.exceptions.ChunkedEncodingError:
        pass

    latency_ms = (time.monotonic() - t0) * 1000
    return "".join(chunks).strip(), chat_ended, latency_ms


# ---------------------------------------------------------------------------
# DocsBot call
# ---------------------------------------------------------------------------

def call_docsbot(
    team_id: str,
    bot_id: str,
    api_key: str,
    question: str,
) -> tuple[str, list[str], float]:
    """POST to DocsBot chat API.

    Returns (answer_text, sources, latency_ms).
    """
    url = f"https://api.docsbot.ai/teams/{team_id}/bots/{bot_id}/chat"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {"question": question, "full_source": False}

    t0 = time.monotonic()
    try:
        resp = requests.post(
            url,
            json=payload,
            timeout=DOCSBOT_TIMEOUT_S,
            headers=headers,
        )
    except requests.exceptions.Timeout:
        return "[TIMEOUT]", [], (time.monotonic() - t0) * 1000
    except requests.exceptions.RequestException as exc:
        return f"[REQUEST_ERROR: {exc}]", [], (time.monotonic() - t0) * 1000

    latency_ms = (time.monotonic() - t0) * 1000

    if resp.status_code != 200:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text[:120]
        return f"[HTTP_{resp.status_code}: {detail}]", [], latency_ms

    try:
        data = resp.json()
    except Exception as exc:
        return f"[JSON_PARSE_ERROR: {exc}]", [], latency_ms

    answer = data.get("answer") or data.get("response") or data.get("message") or ""
    if not answer:
        # Try other common response keys
        for key in ("text", "reply", "content", "output"):
            if data.get(key):
                answer = data[key]
                break

    if not answer:
        answer = f"[EMPTY_RESPONSE: {json.dumps(data)[:200]}]"

    sources_raw = data.get("sources") or []
    sources: list[str] = []
    for s in sources_raw:
        if isinstance(s, dict):
            sources.append(s.get("url") or s.get("title") or s.get("page") or str(s))
        else:
            sources.append(str(s))

    return str(answer).strip(), sources, latency_ms


# ---------------------------------------------------------------------------
# Judge wrapper for head-to-head (accepts chat_ended=False for docsbot)
# ---------------------------------------------------------------------------

def judge_h2h(
    client: anthropic.Anthropic,
    case: dict[str, Any],
    answer: str,
    chat_ended: bool = False,
) -> dict[str, Any]:
    return judge_answer(client, case, answer, chat_ended)


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_v = sorted(values)
    k = (len(sorted_v) - 1) * pct / 100.0
    lo, hi = int(k), min(int(k) + 1, len(sorted_v) - 1)
    return sorted_v[lo] + (sorted_v[hi] - sorted_v[lo]) * (k - lo)


def latency_stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"avg": 0.0, "p50": 0.0, "p95": 0.0}
    return {
        "avg": round(sum(values) / len(values), 1),
        "p50": round(median(values), 1),
        "p95": round(percentile(values, 95), 1),
    }


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

def build_report(
    results: list[dict[str, Any]],
    chat9_url: str,
    chat9_bot_id: str,
    docsbot_team_id: str,
    docsbot_bot_id: str,
) -> dict[str, Any]:
    total = len(results)

    def bot_stats(bot: str) -> dict[str, Any]:
        passed = sum(1 for r in results if r[f"{bot}_verdict"] == "pass")
        by_cat: dict[str, dict[str, Any]] = {}
        latencies: list[float] = []
        for r in results:
            cat = r["category"]
            if cat not in by_cat:
                by_cat[cat] = {"total": 0, "passed": 0}
            by_cat[cat]["total"] += 1
            if r[f"{bot}_verdict"] == "pass":
                by_cat[cat]["passed"] += 1
            lat = r.get(f"{bot}_latency_ms", 0.0)
            if isinstance(lat, (int, float)) and lat > 0:
                latencies.append(lat)
        for stats in by_cat.values():
            stats["pass_rate"] = round(stats["passed"] / stats["total"], 4) if stats["total"] else 0.0
        return {
            "passed": passed,
            "failed": total - passed,
            "pass_rate": round(passed / total, 4) if total else 0.0,
            "by_category": by_cat,
            "latency_ms": latency_stats(latencies),
        }

    both_fail = [r for r in results if r["chat9_verdict"] == "fail" and r["docsbot_verdict"] == "fail"]
    chat9_only_fail = [r for r in results if r["chat9_verdict"] == "fail" and r["docsbot_verdict"] == "pass"]
    docsbot_only_fail = [r for r in results if r["docsbot_verdict"] == "fail" and r["chat9_verdict"] == "pass"]

    return {
        "run_date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "chat9": {"bot_id": chat9_bot_id, "api_url": chat9_url},
        "docsbot": {"team_id": docsbot_team_id, "bot_id": docsbot_bot_id},
        "summary": {
            "total": total,
            "chat9": bot_stats("chat9"),
            "docsbot": bot_stats("docsbot"),
            "both_fail_count": len(both_fail),
            "chat9_only_fail_count": len(chat9_only_fail),
            "docsbot_only_fail_count": len(docsbot_only_fail),
        },
        "cases": results,
    }


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------

CSV_FIELDS = [
    "case_id", "category", "language", "question",
    "docsbot_verdict", "docsbot_error_category", "docsbot_latency_ms",
    "docsbot_answer",
    "chat9_verdict", "chat9_error_category", "chat9_latency_ms",
    "chat9_answer",
]


def write_csv(results: list[dict[str, Any]], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            row = {
                "case_id": r["id"],
                "category": r["category"],
                "language": r.get("language", ""),
                "question": r["question"],
                "docsbot_verdict": r.get("docsbot_verdict", ""),
                "docsbot_error_category": r.get("docsbot_judge_reason", "")[:120],
                "docsbot_latency_ms": round(r.get("docsbot_latency_ms", 0), 1),
                "docsbot_answer": r.get("docsbot_answer", ""),
                "chat9_verdict": r.get("chat9_verdict", ""),
                "chat9_error_category": r.get("chat9_judge_reason", "")[:120],
                "chat9_latency_ms": round(r.get("chat9_latency_ms", 0), 1),
                "chat9_answer": r.get("chat9_answer", ""),
            }
            writer.writerow(row)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Head-to-head eval: DocsBot vs Chat9 — TurboFlare 98 cases",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--judge-key", required=True, help="Anthropic API key for LLM judge")
    p.add_argument(
        "--chat9-api-url",
        default=os.environ.get("CHAT9_API_URL", "https://ai-chatbot-production-6531.up.railway.app"),
    )
    p.add_argument("--chat9-bot-id", default=os.environ.get("TURBOFLARE_BOT_ID", "ch_f1wlhm22lvqby15xar"))
    p.add_argument("--chat9-api-key", default=os.environ.get("CHAT9_API_KEY", ""))
    p.add_argument("--docsbot-team-id", default=os.environ.get("DOCSBOT_TEAM_ID", "e7lgGUWYYivxgGgENmdM"))
    p.add_argument("--docsbot-bot-id", default=os.environ.get("DOCSBOT_BOT_ID", "CrH3tbWkjZJkD3JT9vi"))
    p.add_argument("--docsbot-api-key", default=os.environ.get("DOCSBOT_API_KEY", ""))
    p.add_argument(
        "--output-dir",
        default="eval-results",
        help="Directory for JSON and CSV outputs",
    )
    p.add_argument(
        "--smoke-test",
        action="store_true",
        help="Run only 1 case (smoke test — verify both APIs work)",
    )
    p.add_argument(
        "--categories",
        nargs="+",
        metavar="CATEGORY",
        help="Run only these categories",
    )
    return p


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    judge_client = anthropic.Anthropic(api_key=args.judge_key)

    cases = TEST_CASES
    if args.categories:
        cat_set = set(args.categories)
        cases = [c for c in cases if c["category"] in cat_set]

    if args.smoke_test:
        cases = cases[:1]
        print("=== SMOKE TEST MODE (1 case) ===\n")

    print(f"Head-to-head eval — {len(cases)} cases")
    print(f"  Chat9:   {args.chat9_bot_id} @ {args.chat9_api_url}")
    print(f"  DocsBot: team={args.docsbot_team_id} bot={args.docsbot_bot_id}")
    print(f"  Judge:   {JUDGE_MODEL}\n")

    results: list[dict[str, Any]] = []

    def run_case(case: dict[str, Any]) -> dict[str, Any]:
        """Call both bots in parallel via ThreadPoolExecutor, then judge both."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_docsbot = pool.submit(
                call_docsbot,
                args.docsbot_team_id,
                args.docsbot_bot_id,
                args.docsbot_api_key,
                case["question"],
            )
            f_chat9 = pool.submit(
                call_chat9,
                args.chat9_api_url,
                args.chat9_bot_id,
                args.chat9_api_key,
                case["question"],
            )
            docsbot_answer, docsbot_sources, docsbot_latency = f_docsbot.result()
            chat9_answer, chat9_ended, chat9_latency = f_chat9.result()

        docsbot_verdict_data = judge_h2h(judge_client, case, docsbot_answer, chat_ended=False)
        chat9_verdict_data = judge_h2h(judge_client, case, chat9_answer, chat_ended=chat9_ended)

        return {
            "id": case["id"],
            "category": case["category"],
            "language": "EN",
            "question": case["question"],
            "expected_facts": case.get("expected_facts", []),
            "is_guardrail": case.get("is_guardrail", False),
            "assert_no_escalation": case.get("assert_no_escalation", False),
            "docsbot_answer": docsbot_answer,
            "docsbot_sources": docsbot_sources,
            "docsbot_latency_ms": round(docsbot_latency, 1),
            "docsbot_verdict": docsbot_verdict_data["verdict"],
            "docsbot_judge_reason": docsbot_verdict_data.get("reason", ""),
            "docsbot_judge_confidence": docsbot_verdict_data.get("confidence", 0.8),
            "chat9_answer": chat9_answer,
            "chat9_chat_ended": chat9_ended,
            "chat9_latency_ms": round(chat9_latency, 1),
            "chat9_verdict": chat9_verdict_data["verdict"],
            "chat9_judge_reason": chat9_verdict_data.get("reason", ""),
            "chat9_judge_confidence": chat9_verdict_data.get("confidence", 0.8),
        }

    for case in tqdm(cases, desc="Evaluating", unit="case", ncols=90):
        row = run_case(case)
        results.append(row)

        db_sym = "✓" if row["docsbot_verdict"] == "pass" else "✗"
        c9_sym = "✓" if row["chat9_verdict"] == "pass" else "✗"
        tqdm.write(
            f"  [{case['id']}] DocsBot:{db_sym} Chat9:{c9_sym}  "
            f"({row['docsbot_latency_ms']/1000:.1f}s / {row['chat9_latency_ms']/1000:.1f}s)"
        )
        time.sleep(RATE_LIMIT_SLEEP_S)

    # --- Build and save report ---
    report = build_report(
        results,
        args.chat9_api_url,
        args.chat9_bot_id,
        args.docsbot_team_id,
        args.docsbot_bot_id,
    )

    date_str = datetime.date.today().isoformat()
    json_path = os.path.join(args.output_dir, f"turboflare-head-to-head-{date_str}.json")
    csv_path = os.path.join(args.output_dir, f"turboflare-head-to-head-{date_str}.csv")

    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)

    write_csv(results, csv_path)

    # --- Print summary ---
    s = report["summary"]
    chat9_s = s["chat9"]
    docsbot_s = s["docsbot"]

    print(f"\n{'='*60}")
    print(f"RESULTS  ({s['total']} cases)")
    print(f"{'='*60}")
    print(f"  Chat9:    {chat9_s['passed']}/{s['total']}  ({chat9_s['pass_rate']*100:.1f}%)")
    print(f"  DocsBot:  {docsbot_s['passed']}/{s['total']}  ({docsbot_s['pass_rate']*100:.1f}%)")
    print(f"\n  Both fail:        {s['both_fail_count']}")
    print(f"  Chat9 only fail:  {s['chat9_only_fail_count']}")
    print(f"  DocsBot only fail:{s['docsbot_only_fail_count']}")

    print(f"\nLatency (ms):")
    print(f"  Chat9   avg={chat9_s['latency_ms']['avg']}  p50={chat9_s['latency_ms']['p50']}  p95={chat9_s['latency_ms']['p95']}")
    print(f"  DocsBot avg={docsbot_s['latency_ms']['avg']}  p50={docsbot_s['latency_ms']['p50']}  p95={docsbot_s['latency_ms']['p95']}")

    print(f"\nBy category:")
    all_cats = sorted(set(chat9_s["by_category"]) | set(docsbot_s["by_category"]))
    for cat in all_cats:
        c9 = chat9_s["by_category"].get(cat, {"passed": 0, "total": 0, "pass_rate": 0})
        db = docsbot_s["by_category"].get(cat, {"passed": 0, "total": 0, "pass_rate": 0})
        diff = (c9["pass_rate"] - db["pass_rate"]) * 100
        sign = "+" if diff > 0 else ""
        print(
            f"  {cat:<22} Chat9={c9['passed']}/{c9['total']} ({c9['pass_rate']*100:.0f}%)  "
            f"DocsBot={db['passed']}/{db['total']} ({db['pass_rate']*100:.0f}%)  "
            f"Δ={sign}{diff:.0f}%"
        )

    print(f"\nJSON:  {json_path}")
    print(f"CSV:   {csv_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
