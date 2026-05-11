"""Run bench-v0 against Bedrock Knowledge Bases (Mode A) with hard cost cap.

Same safety pattern as the Vertex runner: context manager + atexit +
SIGINT/SIGTERM/SIGHUP all trigger teardown. The teardown is multi-service
(Bedrock KB + AOSS collection + AOSS policies + IAM role + S3 bucket)
because Bedrock doesn't own the vector store — we provisioned it.

OCU dominates cost; aim for short collection lifetime. Hard cap defaults
to $10. Cleanup subcommand lists stranded resources without deleting.

Usage:
  python scripts/run_bench_v0_bedrock.py run \\
    --questions examples/rag_eval/bench_v0/questions.jsonl \\
    --corpus-dir examples/rag_eval/bench_v0/corpus \\
    --output baselines/rag/bench_v0_bedrock.json \\
    --cap-usd 10

  python scripts/run_bench_v0_bedrock.py cleanup --yes
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
from retrievalci.rag_eval.systems.bedrock_kb import (
    BedrockKBSystem,
    load_bedrock_adapter_from_env,
)
from retrievalci.rag_eval.types import ComparisonReport, Tier


def _signal_handler(adapter: BedrockKBSystem):
    def handler(signum, _frame):
        print(f"\nrun_bench_v0_bedrock: signal {signum} → tearing down...")
        try:
            adapter.teardown()
        finally:
            sys.exit(128 + signum)
    return handler


def install_teardown_guards(adapter: BedrockKBSystem) -> None:
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
    adapter = load_bedrock_adapter_from_env(
        repo_root=repo_root, budget=budget, region=args.region
    )
    est = adapter.estimate_cost(len(questions))
    print(f"Pre-flight cost estimate: ${est:.4f} (cap: ${budget.cap_usd:.2f})")
    budget.preflight(estimate_usd=est, n_questions=len(questions))

    install_teardown_guards(adapter)

    rows = []
    try:
        with adapter:
            print("Provisioning Bedrock KB + AOSS collection + S3...")
            handle = adapter.index(corpus_dir_abs, corpus_hash)
            print(f"KB ready: {handle.provider_index_id}")
            for i, q in enumerate(questions, 1):
                try:
                    ans = adapter.answer(q.question)
                except BudgetExceededError as e:
                    print(f"Budget exceeded at question {i}/{len(questions)}: {e}")
                    raise
                rows.append(compute_row(adapter.name, q, ans))
                if i % 10 == 0 or i == len(questions):
                    print(
                        f"  [{i}/{len(questions)}] cost so far: "
                        f"${budget.actual_usd:.4f}, queries: {budget.actual_queries}"
                    )
    finally:
        pass

    print(f"\nRun complete. Total actual cost: ${budget.actual_usd:.4f}")

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
    """Enumerate possibly-stranded Bedrock + AOSS resources from prior runs.

    Lists by name-prefix match (`retrievalci-bench-v0-*`). With --yes, also
    deletes them.
    """
    repo_root = Path(args.repo_root).resolve()
    load_dotenv(repo_root / ".env")
    import os

    import boto3

    session = boto3.Session(
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name=args.region,
    )
    PREFIX = "retrievalci-bench-v0"
    bedrock_agent = session.client("bedrock-agent", region_name=args.region)
    aoss = session.client("opensearchserverless", region_name=args.region)
    iam = session.client("iam")
    s3 = session.client("s3", region_name=args.region)

    stranded = {
        "kbs": [],
        "collections": [],
        "iam_roles": [],
        "buckets": [],
    }
    for kb in bedrock_agent.list_knowledge_bases().get("knowledgeBaseSummaries", []):
        if kb["name"].startswith(PREFIX):
            stranded["kbs"].append(kb["knowledgeBaseId"])
    for c in aoss.list_collections().get("collectionSummaries", []):
        if c["name"].startswith(PREFIX[:32]):
            stranded["collections"].append(c["id"])
    for r in iam.list_roles().get("Roles", []):
        if r["RoleName"].startswith(PREFIX):
            stranded["iam_roles"].append(r["RoleName"])
    for b in s3.list_buckets().get("Buckets", []):
        if b["Name"].startswith(PREFIX.lower()):
            stranded["buckets"].append(b["Name"])

    print("Stranded resources:")
    for k, vs in stranded.items():
        print(f"  {k}: {len(vs)}")
        for v in vs:
            print(f"    - {v}")
    if not args.yes or not any(stranded.values()):
        if any(stranded.values()):
            print("\nRe-run with --yes to delete.")
        return 0

    # Best-effort cleanup. Order matters.
    for kb_id in stranded["kbs"]:
        try:
            bedrock_agent.delete_knowledge_base(knowledgeBaseId=kb_id)
            print(f"  deleted KB {kb_id}")
        except Exception as e:
            print(f"  FAILED KB {kb_id}: {e}")
    for cid in stranded["collections"]:
        try:
            aoss.delete_collection(id=cid)
            print(f"  deleted collection {cid}")
        except Exception as e:
            print(f"  FAILED collection {cid}: {e}")
    for role in stranded["iam_roles"]:
        try:
            for p in iam.list_role_policies(RoleName=role).get("PolicyNames", []):
                iam.delete_role_policy(RoleName=role, PolicyName=p)
            iam.delete_role(RoleName=role)
            print(f"  deleted role {role}")
        except Exception as e:
            print(f"  FAILED role {role}: {e}")
    for bucket in stranded["buckets"]:
        try:
            objs = s3.list_objects_v2(Bucket=bucket).get("Contents", [])
            if objs:
                s3.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": o["Key"]} for o in objs]})
            s3.delete_bucket(Bucket=bucket)
            print(f"  deleted bucket {bucket}")
        except Exception as e:
            print(f"  FAILED bucket {bucket}: {e}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bench-v0 against Bedrock KB.")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--region", default="us-east-1")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run")
    run_p.add_argument("--questions", type=Path, required=True)
    run_p.add_argument("--corpus-dir", type=Path, required=True)
    run_p.add_argument("--output", type=Path, required=True)
    run_p.add_argument("--cap-usd", type=float, default=DEFAULT_COST_CAP_USD)

    cleanup_p = sub.add_parser("cleanup")
    cleanup_p.add_argument("--yes", action="store_true")

    args = parser.parse_args(argv)
    if args.cmd == "run":
        return run(args)
    return cleanup(args)


if __name__ == "__main__":
    sys.exit(main())
