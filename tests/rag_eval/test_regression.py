from __future__ import annotations

import pytest
from retrievalci.rag_eval.regression import compare_reports
from retrievalci.rag_eval.types import ComparisonReport


def _report(metrics: dict[str, dict[str, float]]) -> ComparisonReport:
    return ComparisonReport(
        systems=tuple(metrics),
        n_questions=10,
        n_per_tier={"single_hop": 10, "multi_hop": 0, "contradiction": 0},
        rows=[],
        by_system_metric=metrics,
        by_system_tier_metric={},
        pairwise=[],
    )


def test_compare_reports_passes_within_allowed_drop() -> None:
    baseline = _report({"rag": {"retrieval_source_recall": 0.80}})
    candidate = _report({"rag": {"retrieval_source_recall": 0.79}})

    check = compare_reports(
        baseline,
        candidate,
        metric="retrieval_source_recall",
        max_drop=0.02,
    )

    assert check.passed
    assert check.checked_systems == ("rag",)


def test_compare_reports_fails_when_metric_drops_too_far() -> None:
    baseline = _report({"rag": {"retrieval_source_recall": 0.80}})
    candidate = _report({"rag": {"retrieval_source_recall": 0.70}})

    check = compare_reports(
        baseline,
        candidate,
        metric="retrieval_source_recall",
        max_drop=0.02,
    )

    assert not check.passed
    assert check.failures[0].system == "rag"
    assert check.failures[0].delta == pytest.approx(-0.10)
    assert "drops more than" in check.failures[0].format()


def test_compare_reports_fails_closed_on_missing_metric() -> None:
    baseline = _report({"rag": {"retrieval_source_recall": 0.80}})
    candidate = _report({"rag": {}})

    check = compare_reports(
        baseline,
        candidate,
        metric="retrieval_source_recall",
        max_drop=0.02,
    )

    assert not check.passed
    assert check.failures[0].reason == "missing metric in candidate report"


def test_compare_reports_checks_requested_systems_even_when_missing() -> None:
    baseline = _report({"rag": {"retrieval_source_recall": 0.80}})
    candidate = _report({"claim_rag": {"retrieval_source_recall": 0.90}})

    check = compare_reports(
        baseline,
        candidate,
        metric="retrieval_source_recall",
        max_drop=0.02,
        systems=("rag",),
    )

    assert not check.passed
    assert check.failures[0].reason == "missing system in candidate report"
