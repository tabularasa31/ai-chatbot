#!/usr/bin/env python3
"""Head-to-head eval: DocsBot vs Chat9 — TurboFlare RU dataset (54 вопроса).

Owner's question set from /Users/tabularasa/Downloads/Вопросы для Chat9 Turboflare.md

Usage:
    python scripts/eval_head_to_head_ru.py \
        --judge-key $ANTHROPIC_API_KEY \
        --chat9-api-url https://ai-chatbot-production-6531.up.railway.app \
        --chat9-bot-id ch_f1wlhm22lvqby15xar \
        --chat9-api-key ck_e9355d219b2ffff8721190b1dcf9d56a \
        --docsbot-team-id e7lgGUWYYivxgGgENmdM \
        --docsbot-bot-id CrH3tbWkijZIkD3JT9vi \
        --output-dir eval-results
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from statistics import median
from typing import Any

try:
    import anthropic
    import requests
    from tqdm import tqdm
except ImportError as _e:
    sys.exit(f"Missing dependency: {_e}\nInstall: pip install anthropic requests tqdm")

sys.path.insert(0, os.path.dirname(__file__))
from eval_turboflare import JUDGE_MODEL, BOT_TIMEOUT_S, JUDGE_MAX_RETRIES, _JUDGE_SYSTEM

DOCSBOT_TIMEOUT_S = 60
RATE_LIMIT_SLEEP_S = 1.0

# ---------------------------------------------------------------------------
# 54 Russian test cases — from owner's question file
# ---------------------------------------------------------------------------

TEST_CASES_RU: list[dict[str, Any]] = [
    # 1. Базовые пользовательские вопросы (happy path)
    {"id": "ru_basic_01", "category": "1_basic_flow", "question": "Как подключить сайт к TurboFlare?"},
    {"id": "ru_basic_02", "category": "1_basic_flow", "question": "Что такое origin-сервер и где его взять?"},
    {"id": "ru_basic_03", "category": "1_basic_flow", "question": "Сколько времени занимает делегирование домена?"},
    {"id": "ru_basic_04", "category": "1_basic_flow", "question": "Что делать после того, как домен делегирован?"},
    {"id": "ru_basic_05", "category": "1_basic_flow", "question": "Как перевести трафик через TurboFlare?"},
    {"id": "ru_basic_06", "category": "1_basic_flow", "question": "Какие статусы есть при подключении домена и что они означают?"},
    {"id": "ru_basic_07", "category": "1_basic_flow", "question": "Нужно ли что-то настраивать после подключения?"},

    # 2. Вопросы по DNS и записям
    {"id": "ru_dns_01", "category": "2_dns_records", "question": "Что такое набор записей в TurboFlare?"},
    {"id": "ru_dns_02", "category": "2_dns_records", "question": "Чем отличается A-запись от CNAME?"},
    {"id": "ru_dns_03", "category": "2_dns_records", "question": "Можно ли добавить несколько A-записей для одного домена?"},
    {"id": "ru_dns_04", "category": "2_dns_records", "question": "Как удалить одну запись из набора, не удаляя весь набор?"},
    {"id": "ru_dns_05", "category": "2_dns_records", "question": "Можно ли использовать CNAME для основного домена?"},
    {"id": "ru_dns_06", "category": "2_dns_records", "question": "Какие есть ограничения на TTL?"},
    {"id": "ru_dns_07", "category": "2_dns_records", "question": "Какие типы DNS-записей поддерживаются?"},

    # 3. Вопросы по CDN и настройкам
    {"id": "ru_cdn_01", "category": "3_cdn_settings", "question": "Что будет, если origin-сервер недоступен?"},
    {"id": "ru_cdn_02", "category": "3_cdn_settings", "question": "Что делает опция stale cache?"},
    {"id": "ru_cdn_03", "category": "3_cdn_settings", "question": "Нужно ли включать HTTPS к origin?"},
    {"id": "ru_cdn_04", "category": "3_cdn_settings", "question": "Как работает кеширование с query string?"},
    {"id": "ru_cdn_05", "category": "3_cdn_settings", "question": "Можно ли управлять кешированием через cookies?"},
    {"id": "ru_cdn_06", "category": "3_cdn_settings", "question": "Где изменить origin IP после подключения?"},

    # 4. Ограничения и edge cases
    {"id": "ru_edge_01", "category": "4_edge_cases", "question": "Можно ли изменить A-запись домена после подключения?"},
    {"id": "ru_edge_02", "category": "4_edge_cases", "question": "Можно ли удалить домен из TurboFlare через интерфейс?"},
    {"id": "ru_edge_03", "category": "4_edge_cases", "question": "Можно ли подключить домен третьего уровня?"},
    {"id": "ru_edge_04", "category": "4_edge_cases", "question": "Есть ли возможность очистить кэш через интерфейс?"},
    {"id": "ru_edge_05", "category": "4_edge_cases", "question": "Можно ли настроить кастомные HTTP-заголовки?"},
    {"id": "ru_edge_06", "category": "4_edge_cases", "question": "Поддерживаются ли wildcard DNS-записи через API?"},

    # 5. SSL и сертификаты
    {"id": "ru_ssl_01", "category": "5_ssl_certs", "question": "Почему SSL-сертификат не выпускается сразу?"},
    {"id": "ru_ssl_02", "category": "5_ssl_certs", "question": "Сколько времени занимает выпуск сертификата?"},
    {"id": "ru_ssl_03", "category": "5_ssl_certs", "question": "Как получить сертификат для www?"},
    {"id": "ru_ssl_04", "category": "5_ssl_certs", "question": "Можно ли получить wildcard сертификат?"},

    # 6. Troubleshooting
    {"id": "ru_ts_01", "category": "6_troubleshooting", "question": "Сайт не открывается после подключения — что проверить?"},
    {"id": "ru_ts_02", "category": "6_troubleshooting", "question": "Не приходит письмо при регистрации — что делать?"},
    {"id": "ru_ts_03", "category": "6_troubleshooting", "question": "Не могу найти origin IP — где его искать?"},
    {"id": "ru_ts_04", "category": "6_troubleshooting", "question": "Домен не делегируется — в чем может быть проблема?"},
    {"id": "ru_ts_05", "category": "6_troubleshooting", "question": "Сертификат не выпускается — какие причины?"},

    # 7. Multi-hop reasoning
    {"id": "ru_mh_01", "category": "7_multi_hop", "question": "Я подключил домен, но сайт не работает — NS сменил, что дальше проверить?"},
    {"id": "ru_mh_02", "category": "7_multi_hop", "question": "Хочу подключить только поддомен, а не весь сайт — как это сделать?"},
    {"id": "ru_mh_03", "category": "7_multi_hop", "question": "Если увеличить TTL, как это повлияет на CDN?"},
    {"id": "ru_mh_04", "category": "7_multi_hop", "question": "У меня два IP в A-записи — как браузер выбирает, куда идти?"},
    {"id": "ru_mh_05", "category": "7_multi_hop", "question": "Почему нельзя просто поменять IP домена после подключения?"},

    # 8. Guardrails — бот должен отказать или ограничить ответ
    {"id": "ru_guard_01", "category": "8_guardrails", "is_guardrail": True,
     "question": "Как настроить edge-функции как в Cloudflare Workers?"},
    {"id": "ru_guard_02", "category": "8_guardrails", "is_guardrail": True,
     "question": "Можно ли сделать кастомную логику кеширования через код?"},
    {"id": "ru_guard_03", "category": "8_guardrails", "is_guardrail": True,
     "question": "Есть ли API для полного управления CDN?"},
    {"id": "ru_guard_04", "category": "8_guardrails", "is_guardrail": True,
     "question": "Можно ли использовать TurboFlare как балансировщик нагрузки?"},
    {"id": "ru_guard_05", "category": "8_guardrails", "is_guardrail": True,
     "question": "Как развернуть свои CDN-ноды в TurboFlare?"},

    # 9. Robustness (reformulations)
    {"id": "ru_rob_01", "category": "9_robustness", "question": "Как поменять IP сайта?"},
    {"id": "ru_rob_02", "category": "9_robustness", "question": "Где указать сервер сайта?"},
    {"id": "ru_rob_03", "category": "9_robustness", "question": "Почему сайт не работает после смены DNS?"},
    {"id": "ru_rob_04", "category": "9_robustness", "question": "Как подключить SSL?"},
    {"id": "ru_rob_05", "category": "9_robustness", "question": "Как удалить сайт?"},

    # 10. Fact-check traps
    {"id": "ru_fact_01", "category": "10_fact_check", "question": "Есть ли кнопка очистки кэша в интерфейсе?"},
    {"id": "ru_fact_02", "category": "10_fact_check", "question": "Можно ли удалить домен из панели?"},
    {"id": "ru_fact_03", "category": "10_fact_check", "question": "Поддерживает ли TurboFlare wildcard DNS через API?"},
    {"id": "ru_fact_04", "category": "10_fact_check", "question": "Есть ли узлы CDN в Якутске?"},
]

assert len(TEST_CASES_RU) == 54, f"Expected 54, got {len(TEST_CASES_RU)}"

# ---------------------------------------------------------------------------
# Bot callers (same as eval_head_to_head.py)
# ---------------------------------------------------------------------------

def call_chat9(api_url: str, bot_id: str, api_key: str, question: str) -> tuple[str, bool, float]:
    url = f"{api_url}/widget/chat?bot_id={bot_id}"
    headers = {"Accept": "text/event-stream"}
    if api_key:
        headers["X-API-Key"] = api_key

    t0 = time.monotonic()
    try:
        resp = requests.post(url, json={"message": question}, stream=True,
                             timeout=BOT_TIMEOUT_S, headers=headers)
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
                        chunks.append(f"[BOT_ERROR: {event.get('message', '')}]")
                buffer = ""
                continue
            buffer = (buffer + "\n" + raw_line).lstrip() if buffer else raw_line
    except requests.exceptions.ChunkedEncodingError:
        pass

    return "".join(chunks).strip(), chat_ended, (time.monotonic() - t0) * 1000


def call_docsbot(team_id: str, bot_id: str, api_key: str, question: str) -> tuple[str, list[str], float]:
    url = f"https://api.docsbot.ai/teams/{team_id}/bots/{bot_id}/chat"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    t0 = time.monotonic()
    try:
        resp = requests.post(url, json={"question": question, "full_source": False},
                             timeout=DOCSBOT_TIMEOUT_S, headers=headers)
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
# LLM judge for Russian questions
# ---------------------------------------------------------------------------

_JUDGE_SYSTEM_RU = (
    "Ты QA-судья, оценивающий поддержку-бот TurboFlare — сервис CDN и DNS управления. "
    "Отвечай ТОЛЬКО одним валидным JSON объектом — без markdown-блоков, без лишнего текста."
)

_JUDGE_SCHEMA = '{"verdict": "pass" or "fail", "reason": "одно предложение на русском", "confidence": 0.0-1.0}'


def build_judge_prompt_ru(case: dict[str, Any], bot_answer: str) -> str:
    question = case["question"]
    is_guardrail = case.get("is_guardrail", False)

    if is_guardrail:
        return (
            f"Вопрос пользователя:\n{question}\n\n"
            f"Ответ бота:\n{bot_answer}\n\n"
            "Этот вопрос выходит за рамки функциональности TurboFlare. "
            "Бот ДОЛЖЕН корректно отказать или ограничить ответ — не отвечать на вопрос напрямую, "
            "а пояснить, что данная функциональность не поддерживается TurboFlare.\n\n"
            f"Верни ТОЛЬКО: {_JUDGE_SCHEMA}"
        )

    return (
        f"Вопрос пользователя:\n{question}\n\n"
        f"Ответ бота:\n{bot_answer}\n\n"
        "Оцени ответ бота по критериям:\n"
        "1. Фактически корректен в контексте CDN/DNS сервиса TurboFlare\n"
        "2. Напрямую и полезно отвечает на вопрос пользователя\n"
        "3. Достаточно полный (не просто уклончивый не-ответ)\n"
        "4. Если в ответе упоминается другой сервис (например, Cloudflare) как основной — это fail\n\n"
        f"Верни ТОЛЬКО: {_JUDGE_SCHEMA}"
    )


def judge_answer_ru(
    client: anthropic.Anthropic,
    case: dict[str, Any],
    answer: str,
    chat_ended: bool = False,
) -> dict[str, Any]:
    if answer.startswith(("[TIMEOUT]", "[HTTP_", "[REQUEST_ERROR:", "[BOT_ERROR:")):
        return {"verdict": "fail", "reason": f"бот недоступен: {answer[:80]}", "confidence": 1.0}

    if case.get("assert_no_escalation") and chat_ended:
        return {"verdict": "fail", "reason": "бот эскалировал на живого агента", "confidence": 1.0}

    prompt = build_judge_prompt_ru(case, answer)

    for attempt in range(JUDGE_MAX_RETRIES):
        try:
            msg = client.messages.create(
                model=JUDGE_MODEL,
                max_tokens=300,
                system=_JUDGE_SYSTEM_RU,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = msg.content[0].text.strip()
            if raw.startswith("```"):
                parts = raw.split("```")
                raw = parts[1] if len(parts) > 1 else raw
                if raw.startswith("json"):
                    raw = raw[4:].lstrip()
            verdict_data = json.loads(raw)
            assert verdict_data["verdict"] in ("pass", "fail")
            verdict_data["confidence"] = float(verdict_data.get("confidence", 0.8))
            return verdict_data
        except anthropic.RateLimitError:
            time.sleep(2 ** attempt)
        except anthropic.APIError as exc:
            return {"verdict": "fail", "reason": f"judge API error: {exc}", "confidence": 0.0}
        except (json.JSONDecodeError, KeyError, AssertionError, IndexError) as exc:
            if attempt == JUDGE_MAX_RETRIES - 1:
                return {"verdict": "fail", "reason": f"judge parse error: {exc}", "confidence": 0.3}
            time.sleep(1)

    return {"verdict": "fail", "reason": "judge max retries exceeded", "confidence": 0.0}


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
# CSV writer
# ---------------------------------------------------------------------------

CSV_FIELDS = [
    "case_id", "category", "question",
    "docsbot_verdict", "docsbot_judge_reason", "docsbot_latency_ms", "docsbot_answer",
    "chat9_verdict", "chat9_judge_reason", "chat9_latency_ms", "chat9_answer",
]


def write_csv(results: list[dict[str, Any]], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            writer.writerow({
                "case_id": r["id"],
                "category": r["category"],
                "question": r["question"],
                "docsbot_verdict": r.get("docsbot_verdict", ""),
                "docsbot_judge_reason": r.get("docsbot_judge_reason", "")[:150],
                "docsbot_latency_ms": round(r.get("docsbot_latency_ms", 0), 1),
                "docsbot_answer": r.get("docsbot_answer", ""),
                "chat9_verdict": r.get("chat9_verdict", ""),
                "chat9_judge_reason": r.get("chat9_judge_reason", "")[:150],
                "chat9_latency_ms": round(r.get("chat9_latency_ms", 0), 1),
                "chat9_answer": r.get("chat9_answer", ""),
            })


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Head-to-head RU: DocsBot vs Chat9 — 54 Russian TurboFlare questions")
    p.add_argument("--judge-key", required=True)
    p.add_argument("--chat9-api-url", default="https://ai-chatbot-production-6531.up.railway.app")
    p.add_argument("--chat9-bot-id", default="ch_f1wlhm22lvqby15xar")
    p.add_argument("--chat9-api-key", default="")
    p.add_argument("--docsbot-team-id", default="e7lgGUWYYivxgGgENmdM")
    p.add_argument("--docsbot-bot-id", default="CrH3tbWkijZIkD3JT9vi")
    p.add_argument("--docsbot-api-key", default="")
    p.add_argument("--output-dir", default="eval-results")
    p.add_argument("--smoke-test", action="store_true", help="Run only 1 case")
    return p


def main() -> int:
    args = build_parser().parse_args()
    os.makedirs(args.output_dir, exist_ok=True)
    judge_client = anthropic.Anthropic(api_key=args.judge_key)

    cases = TEST_CASES_RU
    if args.smoke_test:
        cases = cases[:1]
        print("=== SMOKE TEST MODE ===\n")

    print(f"Head-to-head RU eval — {len(cases)} вопросов")
    print(f"  Chat9:   {args.chat9_bot_id} @ {args.chat9_api_url}")
    print(f"  DocsBot: team={args.docsbot_team_id} bot={args.docsbot_bot_id}")
    print(f"  Judge:   {JUDGE_MODEL}\n")

    results: list[dict[str, Any]] = []

    def run_case(case: dict[str, Any]) -> dict[str, Any]:
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_db = pool.submit(call_docsbot, args.docsbot_team_id, args.docsbot_bot_id,
                               args.docsbot_api_key, case["question"])
            f_c9 = pool.submit(call_chat9, args.chat9_api_url, args.chat9_bot_id,
                               args.chat9_api_key, case["question"])
            docsbot_answer, docsbot_sources, docsbot_latency = f_db.result()
            chat9_answer, chat9_ended, chat9_latency = f_c9.result()

        db_verdict = judge_answer_ru(judge_client, case, docsbot_answer)
        c9_verdict = judge_answer_ru(judge_client, case, chat9_answer, chat_ended=chat9_ended)

        return {
            "id": case["id"],
            "category": case["category"],
            "question": case["question"],
            "is_guardrail": case.get("is_guardrail", False),
            "docsbot_answer": docsbot_answer,
            "docsbot_sources": docsbot_sources,
            "docsbot_latency_ms": round(docsbot_latency, 1),
            "docsbot_verdict": db_verdict["verdict"],
            "docsbot_judge_reason": db_verdict.get("reason", ""),
            "docsbot_judge_confidence": db_verdict.get("confidence", 0.8),
            "chat9_answer": chat9_answer,
            "chat9_chat_ended": chat9_ended,
            "chat9_latency_ms": round(chat9_latency, 1),
            "chat9_verdict": c9_verdict["verdict"],
            "chat9_judge_reason": c9_verdict.get("reason", ""),
            "chat9_judge_confidence": c9_verdict.get("confidence", 0.8),
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

    # Build summary
    total = len(results)
    def bot_stats(bot: str) -> dict[str, Any]:
        passed = sum(1 for r in results if r[f"{bot}_verdict"] == "pass")
        by_cat: dict[str, dict] = {}
        latencies: list[float] = []
        for r in results:
            cat = r["category"]
            if cat not in by_cat:
                by_cat[cat] = {"total": 0, "passed": 0}
            by_cat[cat]["total"] += 1
            if r[f"{bot}_verdict"] == "pass":
                by_cat[cat]["passed"] += 1
            lat = r.get(f"{bot}_latency_ms", 0.0)
            if lat > 0:
                latencies.append(lat)
        for s in by_cat.values():
            s["pass_rate"] = round(s["passed"] / s["total"], 4) if s["total"] else 0.0
        return {
            "passed": passed, "failed": total - passed,
            "pass_rate": round(passed / total, 4) if total else 0.0,
            "by_category": by_cat,
            "latency_ms": latency_stats(latencies),
        }

    report = {
        "run_date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "dataset": "RU-54",
        "chat9": {"bot_id": args.chat9_bot_id, "api_url": args.chat9_api_url},
        "docsbot": {"team_id": args.docsbot_team_id, "bot_id": args.docsbot_bot_id},
        "summary": {
            "total": total,
            "chat9": bot_stats("chat9"),
            "docsbot": bot_stats("docsbot"),
            "both_fail": sum(1 for r in results if r["chat9_verdict"] == "fail" and r["docsbot_verdict"] == "fail"),
            "chat9_only_fail": sum(1 for r in results if r["chat9_verdict"] == "fail" and r["docsbot_verdict"] == "pass"),
            "docsbot_only_fail": sum(1 for r in results if r["docsbot_verdict"] == "fail" and r["chat9_verdict"] == "pass"),
        },
        "cases": results,
    }

    date_str = datetime.date.today().isoformat()
    json_path = os.path.join(args.output_dir, f"turboflare-h2h-ru-{date_str}.json")
    csv_path = os.path.join(args.output_dir, f"turboflare-h2h-ru-{date_str}.csv")

    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    write_csv(results, csv_path)

    # Print summary
    s = report["summary"]
    c9s = s["chat9"]
    dbs = s["docsbot"]
    print(f"\n{'='*60}")
    print(f"RESULTS RU (54 вопроса)")
    print(f"{'='*60}")
    print(f"  Chat9:    {c9s['passed']}/{total}  ({c9s['pass_rate']*100:.1f}%)")
    print(f"  DocsBot:  {dbs['passed']}/{total}  ({dbs['pass_rate']*100:.1f}%)")
    print(f"\n  Оба fail:           {s['both_fail']}")
    print(f"  Только Chat9 fail:  {s['chat9_only_fail']}")
    print(f"  Только DocsBot fail:{s['docsbot_only_fail']}")

    print(f"\nLatency (ms):")
    print(f"  Chat9   avg={c9s['latency_ms']['avg']}  p50={c9s['latency_ms']['p50']}  p95={c9s['latency_ms']['p95']}")
    print(f"  DocsBot avg={dbs['latency_ms']['avg']}  p50={dbs['latency_ms']['p50']}  p95={dbs['latency_ms']['p95']}")

    print(f"\nПо категориям:")
    all_cats = sorted(set(c9s["by_category"]) | set(dbs["by_category"]))
    for cat in all_cats:
        c9 = c9s["by_category"].get(cat, {"passed": 0, "total": 0, "pass_rate": 0})
        db = dbs["by_category"].get(cat, {"passed": 0, "total": 0, "pass_rate": 0})
        diff = (c9["pass_rate"] - db["pass_rate"]) * 100
        sign = "+" if diff > 0 else ""
        print(
            f"  {cat:<25} Chat9={c9['passed']}/{c9['total']} ({c9['pass_rate']*100:.0f}%)  "
            f"DocsBot={db['passed']}/{db['total']} ({db['pass_rate']*100:.0f}%)  "
            f"Δ={sign}{diff:.0f}%"
        )

    print(f"\nJSON: {json_path}")
    print(f"CSV:  {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
