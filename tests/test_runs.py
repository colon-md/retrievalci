from __future__ import annotations

import json
from pathlib import Path

import pytest
import retrievalci.runs.execute as runs_execute
import yaml
from retrievalci.runs import create_run, list_runs, load_manifest
from retrievalci.runs.types import RUN_MANIFEST_SCHEMA_VERSION, ArtifactPolicy, RunSpec
from retrievalci.trace_retrievers import RetrieverCall


def _write_project(root: Path) -> None:
    docs = root / "docs"
    docs.mkdir()
    (docs / "eval.md").write_text(
        "Payments service depends on postgres.\n\nAuth service depends on Redis.",
        encoding="utf-8",
    )
    (root / "questions.jsonl").write_text(
        json.dumps(
            {
                "id": "q01",
                "tier": "single_hop",
                "question": "What database does the payments service depend on?",
                "ground_truth_answer": "postgres",
                "ground_truth_citations": ["docs/eval.md"],
                "must_include_terms": ["postgres"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "rag.yaml").write_text(
        yaml.safe_dump(
            {
                "repo_root": ".",
                "questions": "questions.jsonl",
                "corpus": {"globs": ["docs/*.md"]},
                "systems": ["rag", "bm25"],
                "run": {"backend": "mock", "judge": "none", "max_chunks": 3},
                "reports": {"json": "ignored.json", "markdown": "ignored.md"},
                "diagnostics": {"primary_metric": "retrieval_source_recall"},
            }
        ),
        encoding="utf-8",
    )
    (root / "corpus.jsonl").write_text(
        json.dumps(
            {
                "doc_id": "docs/eval.md",
                "text": "Payments service depends on postgres.",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "trace.jsonl").write_text(
        json.dumps(
            {
                "session_id": "s",
                "turn_id": "1",
                "user_question": "What database does the payments service depend on?",
                "retrieval_query": "payments postgres database",
                "retrieved_doc_ids": ["docs/eval.md"],
                "gold_doc_ids": ["docs/eval.md"],
                "agent_state": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_create_run_writes_lean_default_artifacts(tmp_path) -> None:
    _write_project(tmp_path)
    registry = tmp_path / "runs"

    manifest = create_run(
        RunSpec(
            name="smoke",
            registry_dir=str(registry),
            repo_root=str(tmp_path),
            rag_config="rag.yaml",
            trace_input="trace.jsonl",
            trace_corpus="corpus.jsonl",
        )
    )

    run_dir = registry / manifest.run_id
    assert manifest.schema_version == RUN_MANIFEST_SCHEMA_VERSION
    assert manifest.status == "succeeded"
    assert set(manifest.artifacts) == {"rag_report_json", "trace_metrics_json", "report_html"}
    assert set(manifest.digests) == {"rag_config", "trace_input", "trace_corpus"}
    assert len(manifest.digests["rag_config"]) == 64
    assert (run_dir / "manifest.json").is_file()
    assert (run_dir / "rag-report.json").is_file()
    assert (run_dir / "trace-metrics.json").is_file()
    assert (run_dir / "report.html").is_file()
    assert not (run_dir / "rag-report.md").exists()
    assert not (run_dir / "trace-per-turn.jsonl").exists()
    assert load_manifest(run_dir).run_id == manifest.run_id
    assert list_runs(registry)[0].run_id == manifest.run_id


def test_create_run_debug_and_snapshot_artifacts_are_opt_in(tmp_path) -> None:
    _write_project(tmp_path)
    registry = tmp_path / "runs"

    manifest = create_run(
        RunSpec(
            name="debug",
            registry_dir=str(registry),
            repo_root=str(tmp_path),
            rag_config="rag.yaml",
            trace_input="trace.jsonl",
            trace_corpus="corpus.jsonl",
            artifact_policy=ArtifactPolicy(debug_artifacts=True, snapshot_inputs=True),
        )
    )

    run_dir = registry / manifest.run_id
    assert "rag_report_markdown" in manifest.artifacts
    assert "trace_per_turn_jsonl" in manifest.artifacts
    assert "trace_report_markdown" in manifest.artifacts
    assert "snapshot_rag_config" in manifest.artifacts
    assert "snapshot_trace_input" in manifest.artifacts
    assert "snapshot_trace_corpus" in manifest.artifacts
    assert (run_dir / "rag-report.md").is_file()
    assert (run_dir / "trace-per-turn.jsonl").is_file()
    assert (run_dir / "inputs" / "rag.yaml").is_file()


def test_create_run_rejects_baseline_without_rag_config(tmp_path) -> None:
    _write_project(tmp_path)

    with pytest.raises(ValueError, match="baseline_rag_report requires rag_config"):
        create_run(
            RunSpec(
                registry_dir=str(tmp_path / "runs"),
                repo_root=str(tmp_path),
                baseline_rag_report="rag-report.json",
                trace_input="trace.jsonl",
                trace_corpus="corpus.jsonl",
            )
        )


def test_create_run_can_use_http_trace_retriever_without_corpus(tmp_path, monkeypatch) -> None:
    _write_project(tmp_path)
    retriever_url = "http://user:secret@retriever.local/search?api_key=secret"

    class StubHTTPRetriever:
        def __init__(self, url: str, *, headers: dict[str, str], timeout_s: float) -> None:
            assert url == retriever_url
            assert headers == {"Authorization": "Bearer secret"}
            assert timeout_s == 3.0
            self.calls: list[RetrieverCall] = []

        def query(self, text: str, *, k: int):
            self.calls.append(
                RetrieverCall(
                    query=text,
                    k=k,
                    status_code=200,
                    latency_ms=1.0,
                    result_ids=("docs/eval.md",),
                )
            )
            return [("docs/eval.md", 1.0)]

    monkeypatch.setattr(runs_execute, "HTTPTraceRetriever", StubHTTPRetriever)
    registry = tmp_path / "runs"

    manifest = create_run(
        RunSpec(
            name="http",
            registry_dir=str(registry),
            repo_root=str(tmp_path),
            trace_input="trace.jsonl",
            trace_retriever_url=retriever_url,
            trace_retriever_headers=("Authorization: Bearer secret",),
            trace_retriever_timeout_s=3.0,
            trace_policies=("query_only",),
            artifact_policy=ArtifactPolicy(debug_artifacts=True),
        )
    )

    run_dir = registry / manifest.run_id
    assert manifest.status == "succeeded"
    assert manifest.summaries["trace_retriever"] == "http"
    assert manifest.summaries["trace_retriever_calls"] == 1
    assert manifest.options["trace_retriever"] == "http"
    assert manifest.options["trace_retriever_url"] == "http://retriever.local/search"
    assert "secret" not in json.dumps(manifest.options)
    assert "trace_corpus" not in manifest.inputs
    assert "trace_retriever_calls_jsonl" in manifest.artifacts
    calls = (run_dir / "trace-retriever-calls.jsonl").read_text(encoding="utf-8")
    assert "docs/eval.md" in calls
    assert "secret" not in calls


def test_create_run_records_rag_compare_configuration_error(tmp_path) -> None:
    _write_project(tmp_path)
    registry = tmp_path / "runs"
    baseline_manifest = create_run(
        RunSpec(
            name="baseline",
            registry_dir=str(registry),
            repo_root=str(tmp_path),
            rag_config="rag.yaml",
        )
    )
    baseline_path = registry / baseline_manifest.run_id / "rag-report.json"

    manifest = create_run(
        RunSpec(
            name="bad-metric",
            registry_dir=str(registry),
            repo_root=str(tmp_path),
            rag_config="rag.yaml",
            baseline_rag_report=str(baseline_path),
            regression_metric="not_a_metric",
        )
    )

    assert manifest.status == "failed"
    assert manifest.summaries["rag_regression_passed"] is False
    assert manifest.options["regression_metric"] == "not_a_metric"
    assert manifest.options["max_drop"] == 0.02
    assert "not_a_metric" in manifest.failures[0]


def test_create_run_can_normalize_trace_source_inside_run(tmp_path) -> None:
    _write_project(tmp_path)
    source_path = tmp_path / "otel.json"
    source_path.write_text(
        json.dumps(
            {
                "resourceSpans": [
                    {
                        "scopeSpans": [
                            {
                                "spans": [
                                    {
                                        "traceId": "trace",
                                        "spanId": "root",
                                        "name": "agent.run",
                                        "attributes": [
                                            {
                                                "key": "input.value",
                                                "value": {
                                                    "stringValue": (
                                                        "What database does payments use?"
                                                    )
                                                },
                                            },
                                            {
                                                "key": "retrievalci.gold_doc_ids",
                                                "value": {
                                                    "arrayValue": {
                                                        "values": [
                                                            {"stringValue": "docs/eval.md"}
                                                        ]
                                                    }
                                                },
                                            },
                                        ],
                                    },
                                    {
                                        "traceId": "trace",
                                        "spanId": "retriever",
                                        "name": "retriever",
                                        "attributes": [
                                            {
                                                "key": "openinference.span.kind",
                                                "value": {"stringValue": "RETRIEVER"},
                                            },
                                            {
                                                "key": "input.value",
                                                "value": {"stringValue": "payments database"},
                                            },
                                            {
                                                "key": (
                                                    "retrieval.documents.0.document.id"
                                                ),
                                                "value": {"stringValue": "docs/eval.md"},
                                            },
                                        ],
                                    },
                                ]
                            }
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    registry = tmp_path / "runs"
    manifest = create_run(
        RunSpec(
            name="normalized",
            registry_dir=str(registry),
            repo_root=str(tmp_path),
            trace_source="otel.json",
            trace_source_format="otel",
            trace_require_gold=True,
            trace_corpus="corpus.jsonl",
            trace_policies=("recorded",),
            trace_k=1,
        )
    )

    run_dir = registry / manifest.run_id
    assert manifest.status == "succeeded"
    assert manifest.inputs["trace_source"] == str(source_path)
    assert manifest.summaries["normalized_trace_rows"] == 1
    assert set(manifest.digests) == {
        "normalized_trace_input",
        "trace_corpus",
        "trace_source",
    }
    assert "normalized_trace_input_jsonl" not in manifest.artifacts
    assert not (run_dir / "trace-input.normalized.jsonl").exists()


def test_create_run_applies_trace_metric_gate(tmp_path) -> None:
    _write_project(tmp_path)
    registry = tmp_path / "runs"

    manifest = create_run(
        RunSpec(
            name="gate",
            registry_dir=str(registry),
            repo_root=str(tmp_path),
            trace_input="trace.jsonl",
            trace_corpus="corpus.jsonl",
            trace_policies=("query_only",),
            trace_gate_policy="query_only",
            trace_min_recall_at_5=1.1,
        )
    )

    assert manifest.status == "failed"
    assert manifest.summaries["trace_gate_policy"] == "query_only"
    assert manifest.summaries["trace_gate_passed"] is False
    assert manifest.failures == ["query_only.recall_at_5=1.000 violates >= 1.100"]
