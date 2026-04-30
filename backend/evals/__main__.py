"""CLI: ``python -m backend.evals run --dataset chat9_basic --bot-id ch_...``.

Targets a running chat backend over HTTP via httpx. The bot must
already be set up (use ``scripts/seed_eval_bot.py`` for a local demo
bot, or pass ``--bot-id`` for one in your dev / staging deploy).

Subcommands:

- ``run`` — execute a dataset against a chat backend
- ``list`` — list discoverable datasets
- ``compare`` — diff two ``report.json`` files (used by GitHub Actions
  to post before/after summaries on pull requests)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import httpx

from backend.evals import compare as compare_module
from backend.evals.client import ChatClient
from backend.evals.dataset import load_dataset
from backend.evals.judge import DEFAULT_JUDGE_MODEL, AnthropicJudge
from backend.evals.langfuse_sink import upload_dataset, upload_run
from backend.evals.report import (
    RunReport,
    render_markdown,
    write_json,
    write_markdown,
)
from backend.evals.runner import RunnerConfig, run

DEFAULT_DATASET_DIR = Path("tests/eval/datasets")
DEFAULT_RESULTS_DIR = Path("eval-results")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m backend.evals")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run a golden dataset against a chat backend.")
    run_p.add_argument(
        "--dataset",
        required=True,
        help=(
            "Dataset name (file under tests/eval/datasets/<name>.yaml) "
            "or absolute path to a YAML file."
        ),
    )
    run_p.add_argument(
        "--bot-id",
        required=True,
        help="Bot public_id (same value as widget data-bot-id).",
    )
    run_p.add_argument(
        "--api-base",
        default="http://localhost:8000",
        help="Chat backend base URL (default: http://localhost:8000).",
    )
    run_p.add_argument(
        "--tag",
        default="local",
        help="Free-form run tag (e.g. 'pr-545-before', 'nightly-2026-04-30').",
    )
    run_p.add_argument(
        "--judge-model",
        default=DEFAULT_JUDGE_MODEL,
        help=f"Anthropic model for LLM-as-judge (default: {DEFAULT_JUDGE_MODEL}).",
    )
    run_p.add_argument(
        "--no-judge",
        action="store_true",
        help="Skip LLM-as-judge; deterministic metrics only.",
    )
    run_p.add_argument(
        "--out-dir",
        default=str(DEFAULT_RESULTS_DIR),
        help=f"Where to write report.json + report.md (default: {DEFAULT_RESULTS_DIR}).",
    )
    run_p.add_argument(
        "--langfuse",
        action="store_true",
        help=(
            "Mirror dataset items + per-case traces to Langfuse. "
            "Silently no-ops if LANGFUSE_HOST / LANGFUSE_PUBLIC_KEY / "
            "LANGFUSE_SECRET_KEY are not set."
        ),
    )
    run_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable INFO logging.",
    )

    list_p = sub.add_parser("list", help="List datasets discoverable in tests/eval/datasets/.")
    list_p.add_argument("--root", default=str(DEFAULT_DATASET_DIR))

    cmp_p = sub.add_parser(
        "compare",
        help="Diff two report.json files (before vs after) and print Markdown.",
    )
    cmp_p.add_argument("before", help="Path to baseline report.json")
    cmp_p.add_argument("after", help="Path to current report.json")
    cmp_p.add_argument(
        "--out",
        default=None,
        help="Optional output path for the Markdown diff. Defaults to stdout.",
    )
    cmp_p.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Exit 1 when any case regressed (was passing in 'before', failing in 'after').",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return _cmd_run(args)
    if args.command == "list":
        return _cmd_list(args)
    if args.command == "compare":
        return _cmd_compare(args)
    parser.error(f"unknown command: {args.command}")
    return 2


def _cmd_run(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    dataset_path = _resolve_dataset_path(args.dataset)
    dataset = load_dataset(dataset_path)

    judge: AnthropicJudge | None = None
    if not args.no_judge:
        judge = AnthropicJudge(model=args.judge_model)

    if args.langfuse:
        # Upload dataset items before the run so traces written below
        # can reference items by id.
        upload_dataset(dataset)

    with httpx.Client(base_url=args.api_base, timeout=120.0) as http:
        chat = ChatClient(bot_public_id=args.bot_id, http=http)
        config = RunnerConfig(dataset=dataset, tag=args.tag, chat=chat, judge=judge)
        report = run(config)

    out_dir = Path(args.out_dir) / args.tag
    json_path = write_json(report, out_dir / "report.json")
    md_path = write_markdown(report, out_dir / "report.md")

    if args.langfuse:
        upload_run(report)

    print(render_markdown(report))
    print(f"\nwrote {json_path}")
    print(f"wrote {md_path}")

    return 0 if _all_passed(report) else 1


def _cmd_list(args: argparse.Namespace) -> int:
    root = Path(args.root)
    if not root.is_dir():
        print(f"no such directory: {root}", file=sys.stderr)
        return 2
    files = sorted(root.glob("*.yaml"))
    if not files:
        print(f"no datasets in {root}")
        return 0
    for f in files:
        print(f.stem)
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    before = compare_module.load_report(args.before)
    after = compare_module.load_report(args.after)
    deltas = compare_module.diff(before, after)
    md = compare_module.render_markdown(before, after, deltas=deltas)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md, encoding="utf-8")
        print(f"wrote {out_path}")
    else:
        print(md)

    if args.fail_on_regression and any(d.regressed for d in deltas):
        return 1
    return 0


def _resolve_dataset_path(name_or_path: str) -> Path:
    p = Path(name_or_path)
    if p.is_file():
        return p
    candidate = DEFAULT_DATASET_DIR / f"{name_or_path}.yaml"
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(
        f"dataset not found: {name_or_path} (looked in cwd and {DEFAULT_DATASET_DIR})"
    )


def _all_passed(report: RunReport) -> bool:
    return report.passed_count == report.total


if __name__ == "__main__":
    raise SystemExit(main())
