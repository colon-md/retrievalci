"""Run bench-v0 against Vertex AI RAG Engine (Mode A) with hard cost cap.

Guarantees one-time cost: the Vertex RAG corpus is provisioned, queried,
and TORN DOWN in the same script. Three layered teardown defenses against
stranded indexes:

  1. Context manager `__exit__` on normal scope exit (success or exception).
  2. `atexit.register(teardown)` for interpreter shutdown paths.
  3. SIGINT / SIGTERM / SIGHUP handlers that invoke teardown before exiting.

If the host kernel SIGKILLs the process, none of the above can run — use
`--cleanup` mode to enumerate and delete any stranded corpora before they
accrue more Spanner-hours.

Cost-cap: defaults to $10 (`--cap-usd 10`). Pre-flight refuses to start if
estimated cost exceeds the cap; in-flight aborts if actual records exceed it.

Usage:
  python scripts/run_bench_v0_vertex.py run \\
    --questions examples/rag_eval/bench_v0/questions.jsonl \\
    --corpus-dir examples/rag_eval/bench_v0/corpus \\
    --output baselines/rag/bench_v0_vertex.json \\
    --cap-usd 10

  python scripts/run_bench_v0_vertex.py cleanup
"""

from __future__ import annotations

import argparse
import atexit
import json
import signal
import sys
import urllib.request
from pathlib import Path

# Ensure the package is importable when this script runs from the repo root.
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
from retrievalci.rag_eval.systems.vertex_ai_rag import (
    VertexAIRAGSystem,
    load_vertex_adapter_from_env,
)
from retrievalci.rag_eval.types import ComparisonReport, Tier


def _signal_handler(adapter: VertexAIRAGSystem):
    def handler(signum, _frame):
        print(f"\nrun_bench_v0_vertex: received signal {signum}, tearing down corpus...")
        try:
            adapter.teardown()
        finally:
            sys.exit(128 + signum)
    return handler


def install_teardown_guards(adapter: VertexAIRAGSystem) -> None:
    """Wire atexit + signal handlers so teardown runs even on abnormal exit."""
    atexit.register(adapter.teardown)
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _signal_handler(adapter))


def run(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    load_dotenv(repo_root / ".env")

    questions = load_questions(args.questions)
    print(f"Loaded {len(questions)} questions from {args.questions}")

    # Resolve corpus_dir relative to repo_root so the relative_to() call works
    # whether the user passed an absolute or relative path.
    corpus_dir_abs = (
        args.corpus_dir if args.corpus_dir.is_absolute()
        else (repo_root / args.corpus_dir).resolve()
    )
    corpus_glob = str(corpus_dir_abs.relative_to(repo_root)) + "/*.md"
    docs = load_documents(repo_root, [corpus_glob])
    chunks = chunk_corpus(docs)
    corpus_hash = compute_corpus_version_hash(chunks)
    print(f"Corpus: {len(docs)} docs, {len(chunks)} chunks, hash={corpus_hash[:16]}...")

    budget = RunBudget(cap_usd=args.cap_usd, query_cap=len(questions) + 5)
    adapter = load_vertex_adapter_from_env(
        repo_root=repo_root, budget=budget, project=args.project
    )

    # Pre-flight cost check.
    est = adapter.estimate_cost(len(questions))
    print(f"Pre-flight cost estimate: ${est:.4f} (cap: ${budget.cap_usd:.2f})")
    budget.preflight(estimate_usd=est, n_questions=len(questions))

    install_teardown_guards(adapter)

    rows = []
    try:
        with adapter:  # context manager ensures teardown on exit
            print(f"Provisioning Vertex RAG corpus + uploading {len(docs)} files...")
            handle = adapter.index(corpus_dir_abs, corpus_hash)
            print(f"Corpus provisioned: {handle.provider_index_id}")

            for i, q in enumerate(questions, 1):
                try:
                    ans = adapter.answer(q.question)
                except BudgetExceededError as e:
                    print(f"Budget exceeded at question {i}/{len(questions)}: {e}")
                    raise
                row = compute_row(adapter.name, q, ans)
                rows.append(row)
                if i % 10 == 0 or i == len(questions):
                    print(
                        f"  [{i}/{len(questions)}] cost so far: "
                        f"${budget.actual_usd:.4f}, queries: {budget.actual_queries}"
                    )
    finally:
        # context manager already triggered teardown; this is the belt half.
        pass

    print(f"\nRun complete. Total actual cost: ${budget.actual_usd:.4f}")

    # Aggregate and write report.
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


def cleanup(args: argparse.Namespace) -> int:
    """List and delete any Vertex RAG corpora left over from prior runs.

    For when a previous run was killed before teardown could complete and
    you want to make sure no Spanner-hours are still being billed.
    """
    repo_root = Path(args.repo_root).resolve()
    load_dotenv(repo_root / ".env")
    adapter = load_vertex_adapter_from_env(
        repo_root=repo_root,
        budget=RunBudget(allow_overrun=True),  # cleanup shouldn't be cap-gated
        project=args.project,
    )
    access = adapter._tokens.get()
    loc = adapter._config.location
    parent = f"projects/{adapter._config.project}/locations/{loc}"
    url = f"https://{loc}-aiplatform.googleapis.com/v1beta1/{parent}/ragCorpora"
    req = urllib.request.Request(url, method="GET", headers={"Authorization": f"Bearer {access}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        listing = json.loads(r.read())
    corpora = listing.get("ragCorpora") or []
    print(f"Found {len(corpora)} existing RAG corpora in {parent}:")
    for c in corpora:
        print(f"  {c.get('name')} display_name={c.get('displayName')!r}")
    if not corpora:
        return 0
    if not args.yes:
        print(f"\nRe-run with --yes to DELETE all {len(corpora)} corpora.")
        return 0
    deleted = 0
    for c in corpora:
        name = c.get("name")
        if not name:
            continue
        del_url = f"https://{loc}-aiplatform.googleapis.com/v1beta1/{name}?force=true"
        try:
            req = urllib.request.Request(
                del_url, method="DELETE", headers={"Authorization": f"Bearer {access}"}
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                r.read()
            print(f"  Deleted: {name}")
            deleted += 1
        except Exception as e:
            print(f"  FAILED to delete {name}: {e}")
    print(f"Cleanup complete: {deleted}/{len(corpora)} deleted.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bench-v0 against Vertex AI RAG Engine.")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--project", default=None, help="GCP project (default: OAuth client owner).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="Run bench-v0 against Vertex RAG Engine.")
    run_p.add_argument("--questions", type=Path, required=True)
    run_p.add_argument("--corpus-dir", type=Path, required=True)
    run_p.add_argument("--output", type=Path, required=True)
    run_p.add_argument("--cap-usd", type=float, default=DEFAULT_COST_CAP_USD)

    cleanup_p = sub.add_parser("cleanup", help="List or delete stranded RAG corpora.")
    cleanup_p.add_argument("--yes", action="store_true", help="Actually delete (otherwise dry-run).")

    args = parser.parse_args(argv)
    if args.cmd == "run":
        return run(args)
    if args.cmd == "cleanup":
        return cleanup(args)
    parser.error(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
