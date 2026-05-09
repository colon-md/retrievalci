from __future__ import annotations

from searchtrace.rag_eval.diagnostics import diagnose_report, diagnostics_to_markdown
from searchtrace.rag_eval.types import ComparisonReport, PairwiseDelta


def _report(
    metrics,
    *,
    systems=("a", "b"),
    tiers=None,
    pairwise=None,
    n_questions=30,
) -> ComparisonReport:
    return ComparisonReport(
        systems=systems,
        n_questions=n_questions,
        n_per_tier={"single_hop": n_questions, "multi_hop": 0, "contradiction": 0},
        rows=[],
        by_system_metric=metrics,
        by_system_tier_metric=tiers or {},
        pairwise=pairwise or [],
    )


def test_diagnostics_selects_leader_by_primary_metric() -> None:
    report = _report(
        {
            "a": {"must_include_match": 0.50, "tokens_used_total": 10, "latency_ms_p50": 1},
            "b": {"must_include_match": 0.80, "tokens_used_total": 20, "latency_ms_p50": 2},
        }
    )

    diag = diagnose_report(report)

    assert diag.leader == "b"


def test_diagnostics_tie_breaks_by_lower_token_use() -> None:
    report = _report(
        {
            "a": {"must_include_match": 0.80, "tokens_used_total": 20, "latency_ms_p50": 1},
            "b": {"must_include_match": 0.80, "tokens_used_total": 10, "latency_ms_p50": 2},
        }
    )

    diag = diagnose_report(report)

    assert diag.leader == "b"


def test_diagnostics_treats_zero_token_use_as_cheapest() -> None:
    report = _report(
        {
            "a": {"must_include_match": 0.80, "tokens_used_total": 10, "latency_ms_p50": 1},
            "b": {"must_include_match": 0.80, "tokens_used_total": 0, "latency_ms_p50": 2},
        }
    )

    diag = diagnose_report(report)

    assert diag.leader == "b"


def test_diagnostics_tie_breaks_against_refusing_system() -> None:
    report = _report(
        {
            "a": {
                "retrieval_source_recall": 0.80,
                "refusal_rate": 0.0,
                "tokens_used_total": 10,
                "latency_ms_p50": 1,
            },
            "b": {
                "retrieval_source_recall": 0.80,
                "refusal_rate": 1.0,
                "tokens_used_total": 0,
                "latency_ms_p50": 0,
            },
        }
    )

    diag = diagnose_report(report, primary_metric="retrieval_source_recall")

    assert diag.leader == "a"


def test_diagnostics_classifies_retrieval_limited() -> None:
    report = _report(
        {
            "a": {
                "must_include_match": 0.40,
                "retrieval_source_recall": 0.50,
                "tokens_used_total": 10,
                "latency_ms_p50": 1,
            }
        },
        systems=("a",),
    )

    diag = diagnose_report(report)

    assert diag.bottleneck == "retrieval_limited"
    assert any(f.code == "LOW_RETRIEVAL_RECALL" for f in diag.findings)


def test_diagnostics_classifies_generation_limited() -> None:
    report = _report(
        {
            "a": {
                "must_include_match": 0.55,
                "retrieval_source_recall": 0.90,
                "answer_citation_recall": 0.90,
                "tokens_used_total": 10,
                "latency_ms_p50": 1,
            }
        },
        systems=("a",),
    )

    diag = diagnose_report(report)

    assert diag.bottleneck == "generation_limited"
    assert any(f.code == "GENERATION_GAP" for f in diag.findings)


def test_diagnostics_classifies_citation_limited() -> None:
    report = _report(
        {
            "a": {
                "must_include_match": 0.80,
                "retrieval_source_recall": 0.90,
                "answer_citation_recall": 0.30,
                "answer_citation_precision": 0.90,
                "tokens_used_total": 10,
                "latency_ms_p50": 1,
            }
        },
        systems=("a",),
    )

    diag = diagnose_report(report)

    assert diag.bottleneck == "citation_limited"
    assert any(f.code == "LOW_ANSWER_CITATION_QUALITY" for f in diag.findings)


def test_diagnostics_classifies_refusal_limited() -> None:
    report = _report(
        {
            "a": {
                "must_include_match": 0.80,
                "retrieval_source_recall": 0.90,
                "refusal_rate": 0.50,
                "tokens_used_total": 10,
                "latency_ms_p50": 1,
            }
        },
        systems=("a",),
    )

    diag = diagnose_report(report)

    assert diag.bottleneck == "refusal_limited"
    assert any(f.code == "HIGH_REFUSAL_RATE" for f in diag.findings)


def test_diagnostics_classifies_latency_or_cost_limited() -> None:
    report = _report(
        {
            "a": {"must_include_match": 0.82, "tokens_used_total": 300, "latency_ms_p50": 10},
            "b": {"must_include_match": 0.80, "tokens_used_total": 100, "latency_ms_p50": 9},
        }
    )

    diag = diagnose_report(report, min_meaningful_delta=0.03)

    assert diag.leader == "a"
    assert diag.bottleneck == "latency_or_cost_limited"
    assert any(f.code == "SMALL_GAIN_HIGH_TOKEN_COST" for f in diag.findings)


def test_diagnostics_is_inconclusive_when_primary_metric_missing() -> None:
    report = _report({"a": {"retrieval_source_recall": 0.80}}, systems=("a",))

    diag = diagnose_report(report, primary_metric="must_include_match")

    assert diag.leader is None
    assert diag.bottleneck == "inconclusive"
    assert diag.findings[0].code == "PRIMARY_METRIC_MISSING"


def test_diagnostics_selects_weakest_tier_for_leader() -> None:
    report = _report(
        {
            "a": {"must_include_match": 0.80, "tokens_used_total": 10, "latency_ms_p50": 1}
        },
        systems=("a",),
        tiers={
            "a": {
                "single_hop": {"must_include_match": 0.90},
                "multi_hop": {"must_include_match": 0.40},
                "contradiction": {"must_include_match": 0.70},
            }
        },
    )

    diag = diagnose_report(report)

    assert diag.weakest_tier == "multi_hop"


def test_diagnostics_uses_pairwise_confidence() -> None:
    report = _report(
        {
            "a": {"must_include_match": 0.85, "tokens_used_total": 10, "latency_ms_p50": 1},
            "b": {"must_include_match": 0.70, "tokens_used_total": 20, "latency_ms_p50": 2},
        },
        pairwise=[
            PairwiseDelta(
                metric="must_include_match",
                system_a="a",
                system_b="b",
                mean_a=0.85,
                mean_b=0.70,
                mean_diff=0.15,
                ci_low=0.05,
                ci_high=0.25,
                alpha=0.05,
                n=30,
                significant=True,
            )
        ],
    )

    diag = diagnose_report(report)

    assert any(f.code == "LEADER_CI_DIRECTIONAL" for f in diag.findings)


def test_diagnostics_markdown_contains_actionable_fields() -> None:
    report = _report(
        {
            "a": {
                "must_include_match": 0.40,
                "retrieval_source_recall": 0.50,
                "tokens_used_total": 10,
                "latency_ms_p50": 1,
            }
        },
        systems=("a",),
    )

    md = diagnostics_to_markdown(diagnose_report(report))

    assert "## Diagnosis" in md
    assert "Leader: `a`" in md
    assert "Bottleneck: `retrieval_limited`" in md
    assert "Recommendation:" in md
    assert "Next experiment:" in md
