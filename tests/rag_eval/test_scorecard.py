"""Tests for the README scorecard generator (plan MVP step 9)."""

from __future__ import annotations

from pathlib import Path

import pytest
from retrievalci.rag_eval.types import ComparisonReport
from retrievalci.reporting import (
    SCORECARD_BEGIN_MARKER,
    SCORECARD_END_MARKER,
    inject_scorecard,
    load_rag_report,
    render_scorecard_markdown,
)


def _report_with_metrics(
    metrics_by_system: dict[str, dict[str, float | None]],
) -> ComparisonReport:
    # ComparisonReport.by_system_metric is dict[str, dict[str, float]] — None
    # is not a valid value; "missing" must be represented by omitting the key.
    cleaned = {
        sys: {k: v for k, v in metrics.items() if v is not None}
        for sys, metrics in metrics_by_system.items()
    }
    return ComparisonReport(
        systems=tuple(metrics_by_system),
        n_questions=50,
        n_per_tier={"single_hop": 30, "multi_hop": 15, "contradiction": 5},
        rows=[],
        by_system_metric=cleaned,
    )


def test_render_scorecard_computes_headline_formula() -> None:
    """score = 100 * (0.7 * recall + 0.3 * precision). Pinning the formula."""
    report = _report_with_metrics(
        {
            "bm25": {
                "retrieval_source_recall": 0.50,
                "retrieval_source_precision": 0.40,
                "latency_ms_p50": 5.0,
            }
        }
    )
    md = render_scorecard_markdown(report)
    # 100 * (0.7 * 0.50 + 0.3 * 0.40) = 47.0
    assert "| bm25 | 47.0 | 50.0% | 40.0% | 5.0 |" in md


def test_render_scorecard_marks_missing_metrics_as_pending() -> None:
    """Systems with no retrieval signal (e.g. 100% refusal) render as pending,
    not as zero — fabricating zero would be a misleading scorecard claim."""
    report = _report_with_metrics(
        {
            "claim_rag": {
                "retrieval_source_recall": None,
                "retrieval_source_precision": None,
            }
        }
    )
    md = render_scorecard_markdown(report)
    assert "pending" in md
    # Identifier "claim_rag" renders via display map as "ClaimRAG".
    assert "ClaimRAG | pending" in md


def test_render_scorecard_includes_hosted_placeholders() -> None:
    """Hosted adapters that aren't measured yet must appear as pending rows
    so the public scorecard tracks intent without fabricating numbers."""
    report = _report_with_metrics(
        {"bm25": {"retrieval_source_recall": 0.5, "retrieval_source_precision": 0.4}}
    )
    md = render_scorecard_markdown(
        report,
        hosted_placeholders=(
            ("Vertex AI", "Needs adapter"),
            ("Future /ask adapter", "Blocked on credentials"),
        ),
    )
    # The placeholder status text is accepted for API back-compat but is no
    # longer rendered. Each placeholder shows up as a pending row.
    assert "| Vertex AI | pending | pending | pending | pending |" in md
    assert "| Future /ask adapter | pending | pending | pending | pending |" in md


def test_inject_scorecard_rewrites_between_markers(tmp_path: Path) -> None:
    target = tmp_path / "README.md"
    target.write_text(
        f"# Project\n\n## RAG scorecard\n\n{SCORECARD_BEGIN_MARKER}\n"
        f"old content\n{SCORECARD_END_MARKER}\n\n## Next section\n",
        encoding="utf-8",
    )
    ok = inject_scorecard(target, "NEW CONTENT\n")
    assert ok is True
    final = target.read_text(encoding="utf-8")
    assert "old content" not in final
    assert "NEW CONTENT" in final
    assert "## Next section" in final  # outside-markers content preserved
    # Markers themselves must survive injection so a second inject can find them.
    assert SCORECARD_BEGIN_MARKER in final
    assert SCORECARD_END_MARKER in final


def test_inject_scorecard_returns_false_when_markers_missing(tmp_path: Path) -> None:
    target = tmp_path / "README.md"
    target.write_text("# Project\n\nNo markers here.\n", encoding="utf-8")
    ok = inject_scorecard(target, "anything")
    assert ok is False
    # File is untouched on a marker-miss — caller decides what to do.
    assert "anything" not in target.read_text(encoding="utf-8")


def test_inject_scorecard_raises_when_target_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        inject_scorecard(tmp_path / "does-not-exist.md", "x")


def test_inject_scorecard_handles_end_before_begin(tmp_path: Path) -> None:
    """Defensive: a malformed file with END before BEGIN must not corrupt."""
    target = tmp_path / "README.md"
    target.write_text(
        f"{SCORECARD_END_MARKER}\nstuff\n{SCORECARD_BEGIN_MARKER}\n",
        encoding="utf-8",
    )
    ok = inject_scorecard(target, "NEW")
    assert ok is False
    assert target.read_text(encoding="utf-8").startswith(SCORECARD_END_MARKER)


def test_load_rag_report_roundtrip(tmp_path: Path) -> None:
    """Sanity: the loader accepts the standard ComparisonReport JSON shape."""
    report = _report_with_metrics(
        {"bm25": {"retrieval_source_recall": 0.5, "retrieval_source_precision": 0.4}}
    )
    p = tmp_path / "r.json"
    p.write_text(report.model_dump_json(), encoding="utf-8")
    loaded = load_rag_report(p)
    assert loaded.systems == ("bm25",)
    assert loaded.by_system_metric["bm25"]["retrieval_source_recall"] == 0.5
