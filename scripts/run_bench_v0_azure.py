"""Run bench-v0 against Azure AI Search (Mode A) with hard cost cap.

Same safety pattern as the other hosted runners. Free tier costs nothing,
but we keep the teardown discipline anyway so future paid-tier runs are
safe by default.
"""

from __future__ import annotations

import argparse
import atexit
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from retrievalci.rag_eval.corpus import (
    chunk_corpus,
    compute_corpus_version_hash,
    load_documents,
)
from retrievalci.rag_eval.hosted import (
    DEFAULT_COST_CAP_USD,
    BudgetExceededError,
    RunBudget,
)
from retrievalci.rag_eval.metrics import compute_row
from retrievalci.rag_eval.runner import _aggregate, load_dotenv, load_questions
from retrievalci.rag_eval.systems.azure_ai_search import (
    AzureAISearchSystem,
    load_azure_adapter_from_env,
)
from retrievalci.rag_eval.types import ComparisonReport, Tier


def _signal_handler(adapter: AzureAISearchSystem):
    def handler(signum, _frame):
        print(f"\nrun_bench_v0_azure: signal {signum} → tearing down...")
        try:
            adapter.teardown()
        finally:
            sys.exit(128 + signum)
    return handler


def install_teardown_guards(adapter: AzureAISearchSystem) -> None:
    atexit.register(adapter.teardown)
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _signal_handler(adapter))


def run(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    load_dotenv(repo_root / ".env")

    questions = load_questions(args.questions)
    print(f"Loaded {len(questions)} questions from {args.questions}")

    corpus_dir_abs = (
        args.corpus_dir if args.corpus_dir.is_absolute()
        else (repo_root / args.corpus_dir).resolve()
    )
    docs = load_documents(repo_root, [str(corpus_dir_abs.relative_to(repo_root)) + "/*.md"])
    chunks = chunk_corpus(docs)
    corpus_hash = compute_corpus_version_hash(chunks)
    print(f"Corpus: {len(docs)} docs, {len(chunks)} chunks, hash={corpus_hash[:16]}...")

    budget = RunBudget(cap_usd=args.cap_usd, query_cap=len(questions) + 5)
    adapter = load_azure_adapter_from_env(repo_root=repo_root, budget=budget)
    est = adapter.estimate_cost(len(questions))
    print(f"Pre-flight cost estimate: ${est:.4f} (cap: ${budget.cap_usd:.2f})")
    budget.preflight(estimate_usd=est, n_questions=len(questions))

    install_teardown_guards(adapter)

    rows = []
    try:
        with adapter:
            print("Creating Azure index + embedding corpus...")
            handle = adapter.index(corpus_dir_abs, corpus_hash)
            print(f"Index ready: {handle.provider_index_id}")
            for i, q in enumerate(questions, 1):
                try:
                    ans = adapter.answer(q.question)
                except BudgetExceededError as e:
                    print(f"Budget exceeded at question {i}/{len(questions)}: {e}")
                    raise
                rows.append(compute_row(adapter.name, q, ans))
                if i % 10 == 0 or i == len(questions):
                    print(f"  [{i}/{len(questions)}] queries: {budget.actual_queries}")
    finally:
        pass

    print(f"\nRun complete. Total actual cost: ${budget.actual_usd:.6f}")

    by_sys, by_sys_tier = _aggregate(rows)
    n_per_tier: dict[Tier, int] = {"single_hop": 0, "multi_hop": 0, "contradiction": 0}
    for q in questions:
        n_per_tier[q.tier] = n_per_tier.get(q.tier, 0) + 1
    report = ComparisonReport(
        systems=(adapter.name,),
        n_questions=len(questions),
        n_per_tier=n_per_tier,
        rows=rows,
        by_system_metric=by_sys,
        by_system_tier_metric=by_sys_tier,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print(f"Wrote report: {args.output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bench-v0 against Azure AI Search.")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run")
    run_p.add_argument("--questions", type=Path, required=True)
    run_p.add_argument("--corpus-dir", type=Path, required=True)
    run_p.add_argument("--output", type=Path, required=True)
    run_p.add_argument("--cap-usd", type=float, default=DEFAULT_COST_CAP_USD)
    args = parser.parse_args(argv)
    if args.cmd == "run":
        return run(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
