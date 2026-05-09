from __future__ import annotations

from searchtrace.cli import _report_build_main
from searchtrace.rag_eval.types import Citation, ComparisonReport, RunResult, SystemAnswer
from searchtrace.reporting import build_html_report


def _rag_report(value: float) -> ComparisonReport:
    row = RunResult(
        system="rag",
        question_id="q01",
        tier="single_hop",
        answer=SystemAnswer(
            answer="postgres [doc:docs/db.md]",
            citations=(Citation(source_path="docs/db.md", span="postgres"),),
            latency_ms=12.0,
            tokens_used=42,
        ),
        must_include_match=value,
        answer_citation_recall=value,
        retrieval_source_recall=value,
        retrieval_source_precision=1.0,
        answer_length_chars=24,
    )
    return ComparisonReport(
        systems=("rag",),
        n_questions=1,
        n_per_tier={"single_hop": 1, "multi_hop": 0, "contradiction": 0},
        rows=[row],
        by_system_metric={
            "rag": {
                "must_include_match": value,
                "answer_citation_recall": value,
                "retrieval_source_recall": value,
                "retrieval_source_precision": 1.0,
                "latency_ms_p50": 12.0,
                "tokens_used_total": 42.0,
                "refusal_rate": 0.0,
            }
        },
        by_system_tier_metric={
            "rag": {"single_hop": {"retrieval_source_recall": value}}
        },
        pairwise=[],
    )


def test_build_html_report_renders_rag_and_trace_sections() -> None:
    trace_metrics = {
        "policies": {
            "recorded": {
                "n": 1,
                "recall_at_5": 0.0,
                "recall_at_k": 0.0,
                "zero_recall_at_k": 1.0,
                "drift_at_1": 1.0,
                "stale_at_1": 1.0,
                "false_lead_at_k": 0.0,
            }
        }
    }
    html = build_html_report(
        title="Smoke Report",
        rag_report=_rag_report(0.5),
        trace_metrics=trace_metrics,
        trace_per_turn=[
            {
                "policy": "recorded",
                "session_id": "s",
                "turn_id": "1",
                "query_text": "where is postgres",
                "gold_ids": ["docs/db.md"],
                "ranked_ids": ["docs/other.md"],
                "zero_recall_at_k": True,
            }
        ],
    )

    assert "<title>Smoke Report</title>" in html
    assert "RAG architecture" in html
    assert "Trace-state dynamics" in html
    assert "Zero recall" in html
    assert "docs/db.md" in html


def test_build_html_report_marks_rag_regression_failure() -> None:
    html = build_html_report(
        rag_report=_rag_report(0.5),
        baseline_rag_report=_rag_report(0.8),
        primary_metric="retrieval_source_recall",
        max_drop=0.02,
    )

    assert "Baseline comparison" in html
    assert "Failed" in html
    assert "delta=-0.300" in html


def test_report_build_cli_writes_html(tmp_path) -> None:
    report_path = tmp_path / "rag.json"
    report_path.write_text(_rag_report(0.8).model_dump_json(), encoding="utf-8")
    out_path = tmp_path / "report.html"

    rc = _report_build_main(["--rag-report", str(report_path), "--out", str(out_path)])

    assert rc == 0
    assert out_path.is_file()
    assert "SearchTrace Report" in out_path.read_text(encoding="utf-8")
