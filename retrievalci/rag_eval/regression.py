"""Regression checks for RAG eval reports."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from retrievalci.rag_eval.types import ComparisonReport


@dataclass(frozen=True)
class RegressionFailure:
    system: str
    metric: str
    baseline: float | None
    candidate: float | None
    delta: float | None
    max_drop: float
    reason: str

    def format(self) -> str:
        if self.delta is None:
            return f"{self.system}.{self.metric}: {self.reason}"
        return (
            f"{self.system}.{self.metric} delta={self.delta:+.3f} "
            f"drops more than {self.max_drop:.3f} "
            f"(baseline={self.baseline:.3f}, candidate={self.candidate:.3f})"
        )


@dataclass(frozen=True)
class RegressionCheck:
    metric: str
    checked_systems: tuple[str, ...]
    failures: tuple[RegressionFailure, ...]

    @property
    def passed(self) -> bool:
        return not self.failures


def load_report(path: Path) -> ComparisonReport:
    return ComparisonReport.model_validate_json(path.read_text(encoding="utf-8"))


def compare_reports(
    baseline: ComparisonReport,
    candidate: ComparisonReport,
    *,
    metric: str,
    max_drop: float,
    systems: tuple[str, ...] | None = None,
) -> RegressionCheck:
    """Compare candidate aggregate metrics against a baseline report.

    A metric passes when every selected system has candidate >= baseline - max_drop.
    Missing systems or missing metrics are treated as failures because a CI gate
    should fail closed.
    """
    selected = systems or tuple(s for s in candidate.systems if s in baseline.systems)
    if not selected:
        msg = "reports have no common systems to compare"
        raise ValueError(msg)

    failures: list[RegressionFailure] = []
    for system in selected:
        baseline_metrics = baseline.by_system_metric.get(system)
        candidate_metrics = candidate.by_system_metric.get(system)
        if baseline_metrics is None:
            failures.append(
                RegressionFailure(
                    system=system,
                    metric=metric,
                    baseline=None,
                    candidate=None,
                    delta=None,
                    max_drop=max_drop,
                    reason="missing system in baseline report",
                )
            )
            continue
        if candidate_metrics is None:
            failures.append(
                RegressionFailure(
                    system=system,
                    metric=metric,
                    baseline=None,
                    candidate=None,
                    delta=None,
                    max_drop=max_drop,
                    reason="missing system in candidate report",
                )
            )
            continue

        baseline_value = baseline_metrics.get(metric)
        candidate_value = candidate_metrics.get(metric)
        if baseline_value is None:
            failures.append(
                RegressionFailure(
                    system=system,
                    metric=metric,
                    baseline=None,
                    candidate=candidate_value,
                    delta=None,
                    max_drop=max_drop,
                    reason="missing metric in baseline report",
                )
            )
            continue
        if candidate_value is None:
            failures.append(
                RegressionFailure(
                    system=system,
                    metric=metric,
                    baseline=baseline_value,
                    candidate=None,
                    delta=None,
                    max_drop=max_drop,
                    reason="missing metric in candidate report",
                )
            )
            continue

        delta = candidate_value - baseline_value
        if delta < -max_drop:
            failures.append(
                RegressionFailure(
                    system=system,
                    metric=metric,
                    baseline=baseline_value,
                    candidate=candidate_value,
                    delta=delta,
                    max_drop=max_drop,
                    reason="metric dropped beyond threshold",
                )
            )

    return RegressionCheck(
        metric=metric,
        checked_systems=tuple(selected),
        failures=tuple(failures),
    )
