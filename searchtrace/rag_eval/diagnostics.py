"""Deterministic diagnosis layer for RAG architecture reports."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from searchtrace.rag_eval.types import ComparisonReport

Severity = Literal["info", "warning", "critical"]
Bottleneck = Literal[
    "retrieval_limited",
    "generation_limited",
    "citation_limited",
    "refusal_limited",
    "latency_or_cost_limited",
    "inconclusive",
]

_TIER_ORDER = ("single_hop", "multi_hop", "contradiction")


class DiagnosticFinding(BaseModel):
    """One evidence-backed diagnostic claim."""

    model_config = ConfigDict(extra="forbid")

    severity: Severity
    code: str
    message: str
    evidence: dict[str, float | str | int | bool] = Field(default_factory=dict)


class DiagnosticReport(BaseModel):
    """Structured diagnosis for a `ComparisonReport`."""

    model_config = ConfigDict(extra="forbid")

    primary_metric: str
    leader: str | None
    bottleneck: Bottleneck
    weakest_tier: str | None
    recommendation: str
    next_experiment: str
    findings: list[DiagnosticFinding] = Field(default_factory=list)


def diagnose_report(
    report: ComparisonReport,
    *,
    primary_metric: str = "must_include_match",
    min_meaningful_delta: float = 0.03,
    min_questions_for_confidence: int = 20,
) -> DiagnosticReport:
    """Generate a deterministic product diagnosis from aggregate metrics."""

    findings: list[DiagnosticFinding] = []
    leader = _select_leader(report, primary_metric)
    if leader is None:
        return DiagnosticReport(
            primary_metric=primary_metric,
            leader=None,
            bottleneck="inconclusive",
            weakest_tier=None,
            recommendation=(
                f"No system reported `{primary_metric}`. Choose a metric present in the "
                "report or rerun with compatible questions."
            ),
            next_experiment="Rerun with a populated primary metric and at least two systems.",
            findings=[
                DiagnosticFinding(
                    severity="critical",
                    code="PRIMARY_METRIC_MISSING",
                    message=f"No system reported `{primary_metric}`.",
                    evidence={"primary_metric": primary_metric},
                )
            ],
        )

    if report.n_questions < min_questions_for_confidence:
        findings.append(
            DiagnosticFinding(
                severity="warning",
                code="UNDERPOWERED_EVAL",
                message=(
                    f"Only {report.n_questions} questions were evaluated; treat the "
                    "recommendation as directional."
                ),
                evidence={
                    "n_questions": report.n_questions,
                    "min_questions_for_confidence": min_questions_for_confidence,
                },
            )
        )

    ranked = _ranked_systems(report, primary_metric)
    runner_up = ranked[1] if len(ranked) > 1 else None
    if runner_up is not None:
        _append_delta_findings(
            report,
            findings,
            leader=leader,
            runner_up=runner_up,
            primary_metric=primary_metric,
            min_meaningful_delta=min_meaningful_delta,
        )
    elif report.n_questions < 5:
        findings.append(
            DiagnosticFinding(
                severity="warning",
                code="PAIRWISE_NOT_RUN",
                message="Pairwise bootstrap CIs require at least five questions.",
                evidence={"n_questions": report.n_questions},
            )
        )

    weakest_tier = _weakest_tier(report, leader, primary_metric)
    bottleneck = _classify_bottleneck(
        report,
        leader,
        runner_up=runner_up,
        primary_metric=primary_metric,
        min_meaningful_delta=min_meaningful_delta,
        findings=findings,
    )
    recommendation, next_experiment = _recommendation(
        report,
        leader,
        bottleneck,
        weakest_tier=weakest_tier,
    )
    return DiagnosticReport(
        primary_metric=primary_metric,
        leader=leader,
        bottleneck=bottleneck,
        weakest_tier=weakest_tier,
        recommendation=recommendation,
        next_experiment=next_experiment,
        findings=findings,
    )


def diagnostics_to_markdown(diag: DiagnosticReport) -> str:
    """Render a concise diagnosis section for the Markdown report."""

    leader = f"`{diag.leader}`" if diag.leader is not None else "none"
    weakest_tier = f"`{diag.weakest_tier}`" if diag.weakest_tier else "not available"
    lines = [
        "## Diagnosis",
        "",
        f"- Leader: {leader} on `{diag.primary_metric}`.",
        f"- Bottleneck: `{diag.bottleneck}`.",
        f"- Weakest tier: {weakest_tier}.",
        f"- Recommendation: {diag.recommendation}",
        f"- Next experiment: {diag.next_experiment}",
    ]
    if diag.findings:
        lines.extend(["", "### Diagnostic Findings", ""])
        for finding in diag.findings:
            lines.append(
                f"- `{finding.severity}` `{finding.code}`: {finding.message}"
            )
    return "\n".join(lines) + "\n"


def _select_leader(report: ComparisonReport, primary_metric: str) -> str | None:
    ranked = _ranked_systems(report, primary_metric)
    return ranked[0] if ranked else None


def _ranked_systems(report: ComparisonReport, primary_metric: str) -> list[str]:
    order = {system: i for i, system in enumerate(report.systems)}
    candidates = [
        system
        for system in report.systems
        if _metric(report, system, primary_metric) is not None
    ]
    return sorted(
        candidates,
        key=lambda system: (
            -float(_metric(report, system, primary_metric) or 0.0),
            _metric_or_default(report, system, "refusal_rate", 0.0),
            _metric_or_inf(report, system, "tokens_used_total"),
            _metric_or_inf(report, system, "latency_ms_p50"),
            order[system],
        ),
    )


def _metric_or_default(
    report: ComparisonReport,
    system: str,
    metric: str,
    default: float,
) -> float:
    value = _metric(report, system, metric)
    return value if value is not None else default


def _metric_or_inf(report: ComparisonReport, system: str, metric: str) -> float:
    value = _metric(report, system, metric)
    return value if value is not None else math.inf


def _metric(report: ComparisonReport, system: str, metric: str) -> float | None:
    value = report.by_system_metric.get(system, {}).get(metric)
    if value is None or math.isnan(value):
        return None
    return float(value)


def _append_delta_findings(
    report: ComparisonReport,
    findings: list[DiagnosticFinding],
    *,
    leader: str,
    runner_up: str,
    primary_metric: str,
    min_meaningful_delta: float,
) -> None:
    leader_value = _metric(report, leader, primary_metric)
    runner_value = _metric(report, runner_up, primary_metric)
    if leader_value is None or runner_value is None:
        return

    delta = leader_value - runner_value
    if delta < min_meaningful_delta:
        findings.append(
            DiagnosticFinding(
                severity="warning",
                code="SMALL_LEADER_DELTA",
                message=(
                    f"`{leader}` leads `{runner_up}` by {delta:.3f}, below the "
                    f"{min_meaningful_delta:.3f} meaningful-delta threshold."
                ),
                evidence={
                    "leader_value": leader_value,
                    "runner_up_value": runner_value,
                    "delta": delta,
                    "min_meaningful_delta": min_meaningful_delta,
                },
            )
        )

    pairwise = _pairwise_for(report, leader, runner_up, primary_metric)
    if pairwise is None:
        if report.n_questions >= 5:
            findings.append(
                DiagnosticFinding(
                    severity="warning",
                    code="PAIRWISE_UNAVAILABLE",
                    message=(
                        f"No paired bootstrap CI was available for `{leader}` vs "
                        f"`{runner_up}` on `{primary_metric}`."
                    ),
                    evidence={"n_questions": report.n_questions},
                )
            )
        return

    delta_for_leader, ci_low, ci_high = pairwise
    if ci_low <= 0.0 <= ci_high:
        findings.append(
            DiagnosticFinding(
                severity="warning",
                code="LEADER_CI_CROSSES_ZERO",
                message=(
                    f"`{leader}` has the top point estimate, but its paired CI vs "
                    f"`{runner_up}` crosses zero."
                ),
                evidence={
                    "mean_diff": delta_for_leader,
                    "ci_low": ci_low,
                    "ci_high": ci_high,
                },
            )
        )
    else:
        findings.append(
            DiagnosticFinding(
                severity="info",
                code="LEADER_CI_DIRECTIONAL",
                message=f"`{leader}` has a directional paired-CI edge over `{runner_up}`.",
                evidence={
                    "mean_diff": delta_for_leader,
                    "ci_low": ci_low,
                    "ci_high": ci_high,
                },
            )
        )


def _pairwise_for(
    report: ComparisonReport,
    leader: str,
    runner_up: str,
    metric: str,
) -> tuple[float, float, float] | None:
    for delta in report.pairwise:
        if delta.metric != metric:
            continue
        if delta.system_a == leader and delta.system_b == runner_up:
            return (delta.mean_diff, delta.ci_low, delta.ci_high)
        if delta.system_a == runner_up and delta.system_b == leader:
            return (-delta.mean_diff, -delta.ci_high, -delta.ci_low)
    return None


def _weakest_tier(
    report: ComparisonReport,
    leader: str,
    primary_metric: str,
) -> str | None:
    tiers = report.by_system_tier_metric.get(leader, {})
    if not tiers:
        return None
    metric = "must_include_match"
    if not any(metric in values for values in tiers.values()):
        metric = primary_metric
    scored = [
        (str(tier), float(values[metric]))
        for tier, values in tiers.items()
        if metric in values and not math.isnan(values[metric])
    ]
    if not scored:
        return None
    order = {tier: i for i, tier in enumerate(_TIER_ORDER)}
    scored.sort(key=lambda item: (item[1], order.get(item[0], len(order)), item[0]))
    return scored[0][0]


def _classify_bottleneck(
    report: ComparisonReport,
    leader: str,
    *,
    runner_up: str | None,
    primary_metric: str,
    min_meaningful_delta: float,
    findings: list[DiagnosticFinding],
) -> Bottleneck:
    if _is_latency_or_cost_limited(
        report,
        leader,
        runner_up=runner_up,
        primary_metric=primary_metric,
        min_meaningful_delta=min_meaningful_delta,
        findings=findings,
    ):
        return "latency_or_cost_limited"

    refusal_rate = _metric(report, leader, "refusal_rate")
    if refusal_rate is not None and refusal_rate > 0.30:
        findings.append(
            DiagnosticFinding(
                severity="warning",
                code="HIGH_REFUSAL_RATE",
                message=f"`{leader}` refuses {refusal_rate:.1%} of questions.",
                evidence={"refusal_rate": refusal_rate},
            )
        )
        return "refusal_limited"

    retrieval = _metric(report, leader, "retrieval_source_recall")
    must_include = _metric(report, leader, "must_include_match")
    answer_citation_recall = _metric(report, leader, "answer_citation_recall")
    answer_citation_precision = _metric(report, leader, "answer_citation_precision")

    if retrieval is not None and retrieval >= 0.70:
        if _low(answer_citation_recall, 0.60) or _low(answer_citation_precision, 0.60):
            findings.append(
                DiagnosticFinding(
                    severity="warning",
                    code="LOW_ANSWER_CITATION_QUALITY",
                    message="Retrieved sources are usable, but answer citations are weak.",
                    evidence={
                        "retrieval_source_recall": retrieval,
                        "answer_citation_recall": answer_citation_recall or 0.0,
                        "answer_citation_precision": answer_citation_precision or 0.0,
                    },
                )
            )
            return "citation_limited"

    if retrieval is not None and must_include is not None:
        gap = retrieval - must_include
        if retrieval >= 0.80 and gap >= 0.20:
            findings.append(
                DiagnosticFinding(
                    severity="warning",
                    code="GENERATION_GAP",
                    message="Retrieved sources are strong, but answer content lags.",
                    evidence={
                        "retrieval_source_recall": retrieval,
                        "must_include_match": must_include,
                        "gap": gap,
                    },
                )
            )
            return "generation_limited"

    if retrieval is not None and retrieval < 0.70:
        findings.append(
            DiagnosticFinding(
                severity="warning",
                code="LOW_RETRIEVAL_RECALL",
                message=f"Best retrieval_source_recall is {retrieval:.3f}, below 0.700.",
                evidence={"retrieval_source_recall": retrieval},
            )
        )
        return "retrieval_limited"

    return "inconclusive"


def _is_latency_or_cost_limited(
    report: ComparisonReport,
    leader: str,
    *,
    runner_up: str | None,
    primary_metric: str,
    min_meaningful_delta: float,
    findings: list[DiagnosticFinding],
) -> bool:
    if runner_up is None:
        return False
    leader_score = _metric(report, leader, primary_metric)
    runner_score = _metric(report, runner_up, primary_metric)
    if leader_score is None or runner_score is None:
        return False
    if leader_score - runner_score > min_meaningful_delta:
        return False

    leader_tokens = _metric(report, leader, "tokens_used_total")
    runner_tokens = _metric(report, runner_up, "tokens_used_total")
    if _more_than_2x(leader_tokens, runner_tokens):
        findings.append(
            DiagnosticFinding(
                severity="warning",
                code="SMALL_GAIN_HIGH_TOKEN_COST",
                message=f"`{leader}` is not meaningfully better but uses over 2x tokens.",
                evidence={
                    "leader_tokens": leader_tokens or 0.0,
                    "runner_up_tokens": runner_tokens or 0.0,
                },
            )
        )
        return True

    leader_latency = _metric(report, leader, "latency_ms_p50")
    runner_latency = _metric(report, runner_up, "latency_ms_p50")
    if _more_than_2x(leader_latency, runner_latency):
        findings.append(
            DiagnosticFinding(
                severity="warning",
                code="SMALL_GAIN_HIGH_LATENCY",
                message=f"`{leader}` is not meaningfully better but is over 2x slower.",
                evidence={
                    "leader_latency_ms_p50": leader_latency or 0.0,
                    "runner_up_latency_ms_p50": runner_latency or 0.0,
                },
            )
        )
        return True
    return False


def _low(value: float | None, threshold: float) -> bool:
    return value is not None and value < threshold


def _more_than_2x(lhs: float | None, rhs: float | None) -> bool:
    return lhs is not None and rhs is not None and rhs > 0.0 and lhs > 2.0 * rhs


def _recommendation(
    report: ComparisonReport,
    leader: str,
    bottleneck: Bottleneck,
    *,
    weakest_tier: str | None,
) -> tuple[str, str]:
    tier_phrase = f" on `{weakest_tier}` questions" if weakest_tier else ""
    if bottleneck == "retrieval_limited":
        return (
            f"Prioritize retrieval changes before answer-prompt changes; `{leader}` is "
            "the best current candidate but still misses needed sources.",
            f"Try hybrid retrieval, reranking, better embeddings, or higher top-k{tier_phrase}.",
        )
    if bottleneck == "generation_limited":
        return (
            f"Keep `{leader}` as the retrieval candidate and improve answer synthesis.",
            "Test stricter grounding prompts, context formatting, and answer verification.",
        )
    if bottleneck == "citation_limited":
        return (
            f"Keep `{leader}` retrieval, but fix answer citation behavior before shipping.",
            "Add citation-format enforcement or a post-answer citation verifier.",
        )
    if bottleneck == "refusal_limited":
        return (
            f"`{leader}` is refusing too often for this eval mix.",
            "Tune the evidence sufficiency/refusal gate and inspect refused answerable cases.",
        )
    if bottleneck == "latency_or_cost_limited":
        return (
            f"Do not ship `{leader}` on this eval alone; its quality gain is too small "
            "for the added cost or latency.",
            "Run a larger eval or prefer the cheaper runner-up until the gain is stable.",
        )
    if report.n_questions < 20:
        return (
            f"`{leader}` has the top point estimate, but the eval is underpowered.",
            f"Expand the eval set, especially{tier_phrase or ' weak and multi-hop tiers'}.",
        )
    return (
        f"`{leader}` has the top point estimate, but no single bottleneck dominates.",
        "Inspect per-question failures and add targeted stress cases.",
    )
