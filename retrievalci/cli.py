"""RetrievalCI command-line entrypoints."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _runner_argv(argv: list[str]) -> list[str]:
    """Normalize product subcommands to the current RAG eval runner args."""
    if argv and argv[0] in {"rag", "rag-eval"}:
        argv = argv[1:]
    if argv and argv[0] == "run":
        return argv[1:]
    return argv


def rag_eval() -> None:
    from retrievalci.rag_eval.runner import main as runner_main

    runner_main(_runner_argv(sys.argv[1:]))


def _eval_traces_main(argv: list[str]) -> int:
    from retrievalci.trace_eval import (
        DEFAULT_POLICIES,
        check_metric_gates,
        check_metric_regressions,
        evaluate_traces,
        load_corpus,
        load_metrics,
        load_traces,
        write_outputs,
    )
    from retrievalci.trace_retrievers import (
        HTTPTraceRetriever,
        parse_http_headers,
        write_retriever_calls,
    )

    parser = argparse.ArgumentParser(description="Evaluate retrieval-state policies on traces.")
    parser.add_argument("--traces", required=True)
    parser.add_argument("--corpus", default=None)
    parser.add_argument("--out", required=True)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument(
        "--retriever-url",
        default=None,
        help="HTTP POST retriever endpoint. Receives JSON: {'query': text, 'k': k}.",
    )
    parser.add_argument(
        "--retriever-header",
        action="append",
        default=None,
        help="HTTP header for --retriever-url, formatted as 'Name: value'. Repeatable.",
    )
    parser.add_argument("--retriever-timeout-s", type=float, default=10.0)
    parser.add_argument(
        "--policies",
        default=",".join(DEFAULT_POLICIES),
        help=(
            "Comma-separated policies. Built-ins: recorded, query_only, current_need, "
            "last_answer, last_answer_x3, compact_state, public_trace, "
            "production_baseline."
        ),
    )
    parser.add_argument("--gate-policy", default=None)
    parser.add_argument("--min-recall-at-5", type=float, default=None)
    parser.add_argument("--max-zero-recall-at-k", type=float, default=None)
    parser.add_argument("--max-stale-at-1", type=float, default=None)
    parser.add_argument("--max-false-lead-at-k", type=float, default=None)
    parser.add_argument("--compare-to", default=None, help="Prior metrics.json to compare against.")
    parser.add_argument(
        "--compare-policy",
        default=None,
        help="Policy to compare; defaults to --gate-policy when omitted.",
    )
    parser.add_argument("--max-recall-at-5-drop", type=float, default=None)
    parser.add_argument("--max-zero-recall-at-k-increase", type=float, default=None)
    parser.add_argument("--max-stale-at-1-increase", type=float, default=None)
    parser.add_argument("--max-false-lead-at-k-increase", type=float, default=None)
    args = parser.parse_args(argv)

    policies = tuple(p.strip() for p in args.policies.split(",") if p.strip())
    traces = load_traces(args.traces)
    corpus = load_corpus(args.corpus) if args.corpus else None
    retriever = None
    try:
        if args.retriever_url:
            retriever = HTTPTraceRetriever(
                args.retriever_url,
                headers=parse_http_headers(args.retriever_header),
                timeout_s=args.retriever_timeout_s,
            )
    except ValueError as exc:
        parser.error(str(exc))
    per_turn, metrics = evaluate_traces(
        traces,
        corpus,
        policies=policies,
        k=args.k,
        retriever=retriever,
    )
    write_outputs(per_turn, metrics, args.out)
    print(f"Wrote {args.out}/metrics.json, {args.out}/per_turn.jsonl, {args.out}/report.md")
    if retriever is not None:
        calls_path = Path(args.out) / "retriever-calls.jsonl"
        write_retriever_calls(calls_path, retriever.calls)
        print(f"Wrote {calls_path}")

    if args.gate_policy:
        failures = check_metric_gates(
            metrics,
            policy=args.gate_policy,
            min_recall_at_5=args.min_recall_at_5,
            max_zero_recall_at_k=args.max_zero_recall_at_k,
            max_stale_at_1=args.max_stale_at_1,
            max_false_lead_at_k=args.max_false_lead_at_k,
        )
        if failures:
            print("Metric gate failed:")
            for failure in failures:
                print(f"- {failure}")
            return 2

    if args.compare_to:
        compare_policy = args.compare_policy or args.gate_policy
        if not compare_policy:
            print("Metric regression check failed:")
            print("- --compare-policy is required when --compare-to is used without --gate-policy")
            return 2
        baseline_metrics = load_metrics(args.compare_to)
        failures = check_metric_regressions(
            metrics,
            baseline_metrics,
            policy=compare_policy,
            max_recall_at_5_drop=args.max_recall_at_5_drop,
            max_zero_recall_at_k_increase=args.max_zero_recall_at_k_increase,
            max_stale_at_1_increase=args.max_stale_at_1_increase,
            max_false_lead_at_k_increase=args.max_false_lead_at_k_increase,
        )
        if failures:
            print("Metric regression check failed:")
            for failure in failures:
                print(f"- {failure}")
            return 2
    return 0


def _normalize_traces_main(argv: list[str]) -> int:
    from retrievalci.trace_adapters import normalize_trace_jsonl

    parser = argparse.ArgumentParser(
        description="Normalize trace/span JSONL exports into the RetrievalCI trace schema."
    )
    parser.add_argument("--input", required=True, help="Input trace/span JSONL export.")
    parser.add_argument("--out", required=True, help="Output RetrievalCI trace JSONL.")
    parser.add_argument(
        "--source",
        choices=("auto", "generic", "otel", "opentelemetry", "phoenix"),
        default="auto",
        help="Input adapter. `auto` detects JSONL, Phoenix, and OpenTelemetry-style exports.",
    )
    parser.add_argument(
        "--allow-missing-question",
        action="store_true",
        help="Keep rows even if no question-like field is found.",
    )
    parser.add_argument(
        "--require-gold",
        action="store_true",
        help="Drop rows without gold_doc_ids/gold_chunk_ids labels.",
    )
    args = parser.parse_args(argv)

    written = normalize_trace_jsonl(
        args.input,
        args.out,
        require_question=not args.allow_missing_question,
        require_gold=args.require_gold,
        source=args.source,
    )
    print(f"Wrote {written} normalized trace rows to {args.out}")
    return 0


def _rag_report_traces_main(argv: list[str]) -> int:
    from retrievalci.trace_adapters import write_rag_report_traces

    parser = argparse.ArgumentParser(
        description="Convert a RetrievalCI RAG eval report into trace-eval JSONL."
    )
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--questions", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    written = write_rag_report_traces(args.report_json, args.questions, args.out)
    print(f"Wrote {written} trace rows to {args.out}")
    return 0


def _rag_compare_main(argv: list[str]) -> int:
    from retrievalci.rag_eval.regression import compare_reports, load_report

    parser = argparse.ArgumentParser(description="Compare RAG eval reports for regressions.")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--metric", required=True)
    parser.add_argument("--max-drop", type=float, required=True)
    parser.add_argument(
        "--system",
        action="append",
        default=None,
        help="System to check. Repeatable. Defaults to all common systems.",
    )
    args = parser.parse_args(argv)

    baseline = load_report(args.baseline)
    candidate = load_report(args.candidate)
    try:
        check = compare_reports(
            baseline,
            candidate,
            metric=args.metric,
            max_drop=args.max_drop,
            systems=tuple(args.system) if args.system else None,
        )
    except ValueError as exc:
        print("Metric regression check failed:")
        print(f"- {exc}")
        return 2
    if check.failures:
        print("Metric regression check failed:")
        for failure in check.failures:
            print(f"- {failure.format()}")
        return 2

    print(
        "Metric regression check passed: "
        f"{check.metric} across {', '.join(check.checked_systems)} "
        f"(max drop {args.max_drop:.3f})"
    )
    return 0


def _report_build_main(argv: list[str]) -> int:
    from retrievalci.reporting import (
        load_rag_report,
        load_trace_metrics,
        load_trace_per_turn,
        write_html_report,
    )

    parser = argparse.ArgumentParser(description="Build a static RetrievalCI HTML report.")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--title", default="RetrievalCI Report")
    parser.add_argument("--rag-report", type=Path, default=None)
    parser.add_argument("--baseline-rag-report", type=Path, default=None)
    parser.add_argument("--trace-metrics", type=Path, default=None)
    parser.add_argument("--trace-per-turn", type=Path, default=None)
    parser.add_argument("--primary-metric", default="retrieval_source_recall")
    parser.add_argument("--regression-metric", default=None)
    parser.add_argument("--max-drop", type=float, default=0.02)
    args = parser.parse_args(argv)

    if args.rag_report is None and args.trace_metrics is None:
        parser.error("at least one of --rag-report or --trace-metrics is required")

    rag_report = load_rag_report(args.rag_report) if args.rag_report else None
    baseline_rag_report = (
        load_rag_report(args.baseline_rag_report) if args.baseline_rag_report else None
    )
    trace_metrics = load_trace_metrics(args.trace_metrics) if args.trace_metrics else None
    trace_per_turn = load_trace_per_turn(args.trace_per_turn) if args.trace_per_turn else None

    write_html_report(
        args.out,
        title=args.title,
        rag_report=rag_report,
        baseline_rag_report=baseline_rag_report,
        trace_metrics=trace_metrics,
        trace_per_turn=trace_per_turn,
        primary_metric=args.primary_metric,
        regression_metric=args.regression_metric,
        max_drop=args.max_drop,
    )
    print(f"Wrote {args.out}")
    return 0


def _runs_create_main(argv: list[str]) -> int:
    from retrievalci.runs import create_run
    from retrievalci.runs.types import ArtifactPolicy, RunSpec

    parser = argparse.ArgumentParser(description="Create a lean RetrievalCI run artifact.")
    parser.add_argument("--name", default=None)
    parser.add_argument("--registry", default=".retrievalci/runs")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--rag-config", default=None)
    parser.add_argument("--baseline-rag-report", default=None)
    parser.add_argument("--primary-metric", default="retrieval_source_recall")
    parser.add_argument("--regression-metric", default=None)
    parser.add_argument("--max-drop", type=float, default=0.02)
    parser.add_argument("--trace-input", default=None)
    parser.add_argument("--trace-source", default=None)
    parser.add_argument(
        "--trace-source-format",
        choices=("auto", "generic", "otel", "opentelemetry", "phoenix"),
        default="auto",
    )
    parser.add_argument("--trace-require-gold", action="store_true")
    parser.add_argument("--trace-corpus", default=None)
    parser.add_argument("--trace-retriever-url", default=None)
    parser.add_argument(
        "--trace-retriever-header",
        action="append",
        default=None,
        help="HTTP header for --trace-retriever-url, formatted as 'Name: value'. Repeatable.",
    )
    parser.add_argument("--trace-retriever-timeout-s", type=float, default=10.0)
    parser.add_argument(
        "--trace-policies",
        default="recorded,query_only,last_answer_x3,compact_state,public_trace",
    )
    parser.add_argument("--trace-k", type=int, default=10)
    parser.add_argument("--trace-gate-policy", default=None)
    parser.add_argument("--trace-min-recall-at-5", type=float, default=None)
    parser.add_argument("--trace-max-zero-recall-at-k", type=float, default=None)
    parser.add_argument("--trace-max-stale-at-1", type=float, default=None)
    parser.add_argument("--trace-max-false-lead-at-k", type=float, default=None)
    parser.add_argument("--debug-artifacts", action="store_true")
    parser.add_argument("--snapshot-inputs", action="store_true")
    args = parser.parse_args(argv)

    if args.rag_config is None and args.trace_input is None and args.trace_source is None:
        parser.error("at least one of --rag-config, --trace-input, or --trace-source is required")

    policies = tuple(p.strip() for p in args.trace_policies.split(",") if p.strip())
    try:
        manifest = create_run(
            RunSpec(
                name=args.name,
                registry_dir=args.registry,
                repo_root=args.repo_root,
                rag_config=args.rag_config,
                baseline_rag_report=args.baseline_rag_report,
                primary_metric=args.primary_metric,
                regression_metric=args.regression_metric,
                max_drop=args.max_drop,
                trace_input=args.trace_input,
                trace_source=args.trace_source,
                trace_source_format=args.trace_source_format,
                trace_require_gold=args.trace_require_gold,
                trace_corpus=args.trace_corpus,
                trace_retriever_url=args.trace_retriever_url,
                trace_retriever_headers=tuple(args.trace_retriever_header or ()),
                trace_retriever_timeout_s=args.trace_retriever_timeout_s,
                trace_policies=policies,
                trace_k=args.trace_k,
                trace_gate_policy=args.trace_gate_policy,
                trace_min_recall_at_5=args.trace_min_recall_at_5,
                trace_max_zero_recall_at_k=args.trace_max_zero_recall_at_k,
                trace_max_stale_at_1=args.trace_max_stale_at_1,
                trace_max_false_lead_at_k=args.trace_max_false_lead_at_k,
                artifact_policy=ArtifactPolicy(
                    debug_artifacts=args.debug_artifacts,
                    snapshot_inputs=args.snapshot_inputs,
                ),
            )
        )
    except Exception as exc:
        print(f"RetrievalCI run failed: {exc}", file=sys.stderr)
        return 2
    registry = Path(args.registry)
    if not registry.is_absolute():
        registry = Path(args.repo_root).resolve() / registry
    run_dir = registry / manifest.run_id
    print(f"Wrote {run_dir}/manifest.json")
    print(f"Report: {run_dir / manifest.artifacts['report_html']}")
    if manifest.failures:
        print("Run completed with failures:")
        for failure in manifest.failures:
            print(f"- {failure}")
        return 2
    return 0


def _runs_list_main(argv: list[str]) -> int:
    from retrievalci.runs import list_runs

    parser = argparse.ArgumentParser(description="List local RetrievalCI runs.")
    parser.add_argument("--registry", default=".retrievalci/runs")
    args = parser.parse_args(argv)

    runs = list_runs(args.registry)
    if not runs:
        print("No RetrievalCI runs found.")
        return 0
    print("run_id\tstatus\tname\treport")
    for run in runs:
        report = run.artifacts.get("report_html", "")
        print(f"{run.run_id}\t{run.status}\t{run.name or ''}\t{report}")
    return 0


def _project_run_main(argv: list[str]) -> int:
    from retrievalci.project import load_project_config, project_config_to_run_spec
    from retrievalci.runs import create_run

    parser = argparse.ArgumentParser(description="Run RetrievalCI from a project YAML file.")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        config = load_project_config(args.config)
        spec = project_config_to_run_spec(config, config_path=args.config)
        manifest = create_run(spec)
    except Exception as exc:
        print(f"RetrievalCI project run failed: {exc}", file=sys.stderr)
        return 2

    registry = Path(spec.registry_dir)
    if not registry.is_absolute():
        registry = Path(spec.repo_root).resolve() / registry
    run_dir = registry / manifest.run_id
    print(f"Wrote {run_dir}/manifest.json")
    if manifest.artifacts.get("report_html"):
        print(f"Report: {run_dir / manifest.artifacts['report_html']}")
    if manifest.failures:
        print("Run completed with failures:")
        for failure in manifest.failures:
            print(f"- {failure}")
        return 2
    return 0


def eval_traces() -> None:
    raise SystemExit(_eval_traces_main(sys.argv[1:]))


def normalize_traces() -> None:
    raise SystemExit(_normalize_traces_main(sys.argv[1:]))


def rag_compare() -> None:
    raise SystemExit(_rag_compare_main(sys.argv[1:]))


def report_build() -> None:
    raise SystemExit(_report_build_main(sys.argv[1:]))


def runs_create() -> None:
    raise SystemExit(_runs_create_main(sys.argv[1:]))


def runs_list() -> None:
    raise SystemExit(_runs_list_main(sys.argv[1:]))


def project_run() -> None:
    raise SystemExit(_project_run_main(sys.argv[1:]))


def main() -> None:
    argv = sys.argv[1:]
    if argv[:2] == ["traces", "eval"]:
        raise SystemExit(_eval_traces_main(argv[2:]))
    if argv[:2] == ["traces", "normalize"]:
        raise SystemExit(_normalize_traces_main(argv[2:]))
    if argv[:2] == ["traces", "from-rag-report"]:
        raise SystemExit(_rag_report_traces_main(argv[2:]))
    if argv[:2] == ["rag", "compare"]:
        raise SystemExit(_rag_compare_main(argv[2:]))
    if argv[:2] == ["report", "build"]:
        raise SystemExit(_report_build_main(argv[2:]))
    if argv[:2] == ["runs", "create"]:
        raise SystemExit(_runs_create_main(argv[2:]))
    if argv[:2] == ["runs", "list"]:
        raise SystemExit(_runs_list_main(argv[2:]))
    if argv[:2] == ["project", "run"]:
        raise SystemExit(_project_run_main(argv[2:]))
    if argv[:2] == ["ci", "run"]:
        raise SystemExit(_project_run_main(argv[2:]))
    rag_eval()


if __name__ == "__main__":
    main()
