"""Run orchestration for the local SearchTrace registry."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
import subprocess
import tempfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from searchtrace.rag_eval.regression import compare_reports, load_report
from searchtrace.rag_eval.runner import main as run_rag_eval
from searchtrace.reporting import load_rag_report, write_html_report
from searchtrace.runs.registry import relpath, reserve_run_dir, utc_now_iso, write_manifest
from searchtrace.runs.types import RunArtifact, RunSpec
from searchtrace.trace_adapters import normalize_trace_jsonl
from searchtrace.trace_eval import (
    check_metric_gates,
    evaluate_traces,
    load_corpus,
    load_traces,
    render_markdown_report,
)
from searchtrace.trace_retrievers import (
    HTTPTraceRetriever,
    parse_http_headers,
    write_retriever_calls,
)


def create_run(spec: RunSpec) -> RunArtifact:
    """Create a lean, versioned local SearchTrace run artifact."""

    if spec.rag_config is None and spec.trace_input is None and spec.trace_source is None:
        msg = "at least one of rag_config, trace_input, or trace_source is required"
        raise ValueError(msg)
    if spec.baseline_rag_report is not None and spec.rag_config is None:
        msg = "baseline_rag_report requires rag_config"
        raise ValueError(msg)
    if (
        spec.trace_retriever_url is not None
        and spec.trace_input is None
        and spec.trace_source is None
    ):
        msg = "trace_retriever_url requires trace_input or trace_source"
        raise ValueError(msg)
    if spec.trace_retriever_headers and spec.trace_retriever_url is None:
        msg = "trace_retriever_headers requires trace_retriever_url"
        raise ValueError(msg)

    repo_root = Path(spec.repo_root).resolve()
    registry_dir = _resolve_path(spec.registry_dir, repo_root)
    run_id, run_dir = reserve_run_dir(registry_dir, name=spec.name)
    artifacts: dict[str, str] = {}
    inputs: dict[str, str] = {}
    digests: dict[str, str] = {}
    summaries: dict[str, str | int | float | bool | None] = {}
    failures: list[str] = []

    rag_report = None
    trace_metrics: dict | None = None
    trace_per_turn: list[dict] | None = None
    trace_retriever: HTTPTraceRetriever | None = None
    options = _manifest_options(spec)

    try:
        if spec.rag_config is not None:
            rag_config = _resolve_path(spec.rag_config, repo_root)
            inputs["rag_config"] = rag_config.as_posix()
            digests["rag_config"] = _sha256_file(rag_config)
            rag_json = run_dir / "rag-report.json"
            debug_md = run_dir / "rag-report.md"
            if spec.artifact_policy.debug_artifacts:
                rag_md = debug_md
            else:
                tmp_dir = tempfile.TemporaryDirectory()
                rag_md = Path(tmp_dir.name) / "rag-report.md"
            stdout = io.StringIO()
            try:
                with contextlib.redirect_stdout(stdout):
                    run_rag_eval(
                        [
                            "--config",
                            str(rag_config),
                            "--repo-root",
                            str(repo_root),
                            "--report-json",
                            str(rag_json),
                            "--report-md",
                            str(rag_md),
                            "--primary-metric",
                            spec.primary_metric,
                        ]
                    )
            except Exception as exc:
                tail = "\n".join(stdout.getvalue().splitlines()[-8:])
                if tail:
                    msg = f"{exc}; RAG runner output tail:\n{tail}"
                    raise RuntimeError(msg) from exc
                raise
            finally:
                if not spec.artifact_policy.debug_artifacts:
                    tmp_dir.cleanup()

            artifacts["rag_report_json"] = relpath(rag_json, run_dir)
            if spec.artifact_policy.debug_artifacts:
                artifacts["rag_report_markdown"] = relpath(debug_md, run_dir)
            rag_report = load_rag_report(rag_json)
            summaries["rag_systems"] = len(rag_report.systems)
            summaries["rag_questions"] = rag_report.n_questions

        if spec.trace_input is not None or spec.trace_source is not None:
            trace_tmp_dir = None
            if spec.trace_source is not None:
                trace_source = _resolve_path(spec.trace_source, repo_root)
                inputs["trace_source"] = trace_source.as_posix()
                digests["trace_source"] = _sha256_file(trace_source)
                if spec.artifact_policy.debug_artifacts:
                    trace_input = run_dir / "trace-input.normalized.jsonl"
                else:
                    trace_tmp_dir = tempfile.TemporaryDirectory()
                    trace_input = Path(trace_tmp_dir.name) / "trace-input.normalized.jsonl"
                written = normalize_trace_jsonl(
                    trace_source,
                    trace_input,
                    require_gold=spec.trace_require_gold,
                    source=spec.trace_source_format,
                )
                if spec.artifact_policy.debug_artifacts:
                    artifacts["normalized_trace_input_jsonl"] = relpath(trace_input, run_dir)
                digests["normalized_trace_input"] = _sha256_file(trace_input)
                summaries["normalized_trace_rows"] = written
            else:
                trace_input = _resolve_path(str(spec.trace_input), repo_root)
                inputs["trace_input"] = trace_input.as_posix()
                digests["trace_input"] = _sha256_file(trace_input)
            corpus = None
            if spec.trace_corpus is not None:
                trace_corpus = _resolve_path(spec.trace_corpus, repo_root)
                inputs["trace_corpus"] = trace_corpus.as_posix()
                digests["trace_corpus"] = _sha256_file(trace_corpus)
                corpus = load_corpus(trace_corpus)
            if spec.trace_retriever_url is not None:
                trace_retriever = HTTPTraceRetriever(
                    spec.trace_retriever_url,
                    headers=parse_http_headers(spec.trace_retriever_headers),
                    timeout_s=spec.trace_retriever_timeout_s,
                )
            try:
                traces = load_traces(trace_input)
                trace_per_turn, trace_metrics = evaluate_traces(
                    traces,
                    corpus,
                    policies=spec.trace_policies,
                    k=spec.trace_k,
                    retriever=trace_retriever,
                )
            finally:
                if trace_tmp_dir is not None:
                    trace_tmp_dir.cleanup()
            trace_metrics_path = run_dir / "trace-metrics.json"
            trace_metrics_path.write_text(json.dumps(trace_metrics, indent=2), encoding="utf-8")
            artifacts["trace_metrics_json"] = relpath(trace_metrics_path, run_dir)
            summaries["trace_policies"] = len(trace_metrics.get("policies", {}))
            if spec.trace_gate_policy:
                gate_failures = check_metric_gates(
                    trace_metrics,
                    policy=spec.trace_gate_policy,
                    min_recall_at_5=spec.trace_min_recall_at_5,
                    max_zero_recall_at_k=spec.trace_max_zero_recall_at_k,
                    max_stale_at_1=spec.trace_max_stale_at_1,
                    max_false_lead_at_k=spec.trace_max_false_lead_at_k,
                )
                summaries["trace_gate_policy"] = spec.trace_gate_policy
                summaries["trace_gate_passed"] = not gate_failures
                failures.extend(gate_failures)
            if trace_retriever is not None:
                summaries["trace_retriever"] = "http"
                summaries["trace_retriever_calls"] = len(trace_retriever.calls)
            if spec.artifact_policy.debug_artifacts:
                per_turn_path = run_dir / "trace-per-turn.jsonl"
                with per_turn_path.open("w", encoding="utf-8") as f:
                    for row in trace_per_turn:
                        f.write(json.dumps(row) + "\n")
                artifacts["trace_per_turn_jsonl"] = relpath(per_turn_path, run_dir)
                trace_md = run_dir / "trace-report.md"
                trace_md.write_text(
                    render_markdown_report(trace_metrics, per_turn=trace_per_turn),
                    encoding="utf-8",
                )
                artifacts["trace_report_markdown"] = relpath(trace_md, run_dir)
                if trace_retriever is not None:
                    calls_path = run_dir / "trace-retriever-calls.jsonl"
                    write_retriever_calls(calls_path, trace_retriever.calls)
                    artifacts["trace_retriever_calls_jsonl"] = relpath(calls_path, run_dir)

        if spec.baseline_rag_report is not None:
            baseline_path = _resolve_path(spec.baseline_rag_report, repo_root)
            inputs["baseline_rag_report"] = baseline_path.as_posix()
            digests["baseline_rag_report"] = _sha256_file(baseline_path)
            if rag_report is not None:
                baseline = load_report(baseline_path)
                try:
                    check = compare_reports(
                        baseline,
                        rag_report,
                        metric=spec.regression_metric or spec.primary_metric,
                        max_drop=spec.max_drop,
                    )
                except ValueError as exc:
                    summaries["rag_regression_passed"] = False
                    failures.append(str(exc))
                else:
                    summaries["rag_regression_passed"] = check.passed
                    failures.extend(failure.format() for failure in check.failures)

        if spec.artifact_policy.snapshot_inputs:
            _snapshot_inputs(run_dir, inputs, artifacts)

        report_html = run_dir / "report.html"
        baseline_report = (
            load_rag_report(_resolve_path(spec.baseline_rag_report, repo_root))
            if spec.baseline_rag_report
            else None
        )
        write_html_report(
            report_html,
            title=f"SearchTrace Run {run_id}",
            rag_report=rag_report,
            baseline_rag_report=baseline_report,
            trace_metrics=trace_metrics,
            trace_per_turn=trace_per_turn,
            primary_metric=spec.primary_metric,
            regression_metric=spec.regression_metric,
            max_drop=spec.max_drop,
        )
        artifacts["report_html"] = relpath(report_html, run_dir)

        manifest = RunArtifact(
            run_id=run_id,
            created_at=utc_now_iso(),
            status="failed" if failures else "succeeded",
            name=spec.name,
            artifacts=artifacts,
            inputs=inputs,
            digests=digests,
            options=options,
            summaries=summaries,
            failures=failures,
        )
        write_manifest(run_dir, manifest)
        return manifest
    except Exception as exc:
        manifest = RunArtifact(
            run_id=run_id,
            created_at=utc_now_iso(),
            status="failed",
            name=spec.name,
            artifacts=artifacts,
            inputs=inputs,
            digests=digests,
            options=options,
            summaries=summaries,
            failures=[str(exc)],
        )
        write_manifest(run_dir, manifest)
        raise


def _manifest_options(spec: RunSpec) -> dict[str, object]:
    return {
        "primary_metric": spec.primary_metric,
        "regression_metric": spec.regression_metric,
        "max_drop": spec.max_drop,
        "trace_policies": list(spec.trace_policies),
        "trace_k": spec.trace_k,
        "trace_source_format": spec.trace_source_format if spec.trace_source else None,
        "trace_require_gold": spec.trace_require_gold if spec.trace_source else None,
        "trace_gate_policy": spec.trace_gate_policy,
        "trace_min_recall_at_5": spec.trace_min_recall_at_5,
        "trace_max_zero_recall_at_k": spec.trace_max_zero_recall_at_k,
        "trace_max_stale_at_1": spec.trace_max_stale_at_1,
        "trace_max_false_lead_at_k": spec.trace_max_false_lead_at_k,
        "trace_retriever": "http" if spec.trace_retriever_url else None,
        "trace_retriever_url": _safe_retriever_url(spec.trace_retriever_url),
        "trace_retriever_timeout_s": spec.trace_retriever_timeout_s,
        "tool_version": _tool_version(),
        "git_sha": _git_sha(Path(spec.repo_root).resolve()),
        "debug_artifacts": spec.artifact_policy.debug_artifacts,
        "snapshot_inputs": spec.artifact_policy.snapshot_inputs,
    }


def _safe_retriever_url(url: str | None) -> str | None:
    if url is None:
        return None
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    host = parsed.hostname or ""
    netloc = host
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _resolve_path(value: str | Path, repo_root: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return repo_root / path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tool_version() -> str:
    try:
        return version("searchtrace")
    except PackageNotFoundError:
        return "0.0.0+unknown"


def _git_sha(repo_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None


def _snapshot_inputs(
    run_dir: Path,
    inputs: dict[str, str],
    artifacts: dict[str, str],
) -> None:
    snapshot_dir = run_dir / "inputs"
    snapshot_dir.mkdir(exist_ok=True)
    for key, path_text in inputs.items():
        source = Path(path_text)
        if not source.is_file():
            continue
        target = snapshot_dir / source.name
        if target.exists():
            target = snapshot_dir / f"{key}-{source.name}"
        shutil.copyfile(source, target)
        artifacts[f"snapshot_{key}"] = relpath(target, run_dir)
