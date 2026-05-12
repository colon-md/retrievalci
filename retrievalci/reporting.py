"""Static HTML reporting for RetrievalCI artifacts."""

from __future__ import annotations

import html
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from retrievalci.rag_eval.diagnostics import diagnose_report
from retrievalci.rag_eval.regression import RegressionCheck, compare_reports
from retrievalci.rag_eval.types import ComparisonReport, RunResult
from retrievalci.report_assets import REPORT_CSS, SORT_TABLES_JS


@dataclass(frozen=True)
class Cell:
    text: str
    sort_value: str | float | int | None = None
    class_name: str = ""


def load_rag_report(path: str | Path) -> ComparisonReport:
    return ComparisonReport.model_validate_json(Path(path).read_text(encoding="utf-8"))


def load_trace_metrics(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# Marker block syntax for `retrievalci report scorecard` injection. The
# generator finds these markers in a target Markdown file and rewrites
# everything between them. Outside the markers, the file is untouched.
SCORECARD_BEGIN_MARKER = "<!-- BEGIN retrievalci scorecard -->"
SCORECARD_END_MARKER = "<!-- END retrievalci scorecard -->"


# Map raw system identifiers to display labels used in the public scorecard.
# Keep keys aligned with the `name` property values returned by each system
# class. Unmapped identifiers render as-is.
_SCORECARD_DISPLAY_NAMES: dict[str, str] = {
    "bm25_lexical": "BM25 (lexical)",
    "dense_rag": "Dense (vector RAG)",
    "hybrid_rrf": "Hybrid (BM25+Dense RRF)",
    "dense_rerank": "Rerank (Dense+LLM)",
    "chunk_summary_rag": "Chunk-summary (Dense)",
    "claim_rag": "ClaimRAG",
    "wiki_pages": "Wiki pages (Karpathy)",
    "vertex_ai_rag": "Vertex AI RAG Engine",
    "bedrock_kb": "Bedrock KB (Cohere embed)",
    "openai_file_search": "OpenAI File Search",
    "azure_ai_search": "Azure AI Search (Gemini embed)",
}


def render_scorecard_markdown(
    report: ComparisonReport,
    *,
    hosted_placeholders: tuple[tuple[str, str], ...] = (),
) -> str:
    """Render a Markdown scorecard table from a ComparisonReport.

    The headline score is `100 * (0.7 * retrieval_source_recall + 0.3 *
    retrieval_source_precision)` per the plan; missing metrics render as
    "pending" rather than 0 so a half-finished benchmark doesn't get
    interpreted as zero-score.

    `hosted_placeholders` is a tuple of (system_name, status_text) for
    adapters that aren't shipped yet. They render as "pending" rows below
    the measured rows so the public scorecard tracks intent without
    fabricating numbers.
    """
    lines: list[str] = []
    lines.append("```text")
    lines.append("score = 100 * (0.7 * retrieval_source_recall + 0.3 * retrieval_source_precision)")
    lines.append("```")
    lines.append("")
    lines.append("| System | Score | Recall | Precision | p50 retrieve (ms) |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")

    for system in report.systems:
        display = _SCORECARD_DISPLAY_NAMES.get(system, system)
        m = report.by_system_metric.get(system, {})
        recall = m.get("retrieval_source_recall")
        precision = m.get("retrieval_source_precision")
        # Prefer retrieval-only latency for fair comparison between local
        # (retrieve + generate) and hosted (retrieve only) systems. Fall back
        # to end-to-end latency on older baselines.
        latency_p50 = m.get("retrieval_latency_ms_p50") or m.get("latency_ms_p50")
        if recall is None or precision is None:
            lines.append(f"| {display} | pending | pending | pending | pending |")
            continue
        score = 100.0 * (0.7 * recall + 0.3 * precision)
        latency_cell = f"{latency_p50:.1f}" if isinstance(latency_p50, (int, float)) else "pending"
        lines.append(
            f"| {display} | {score:.1f} | {recall * 100:.1f}% | {precision * 100:.1f}% "
            f"| {latency_cell} |"
        )

    # hosted_placeholders still accepts (name, status_text) tuples for caller
    # back-compat (CLI / Makefile both pass status text), but status text is
    # no longer rendered — the row's pending cells convey not-yet-measured.
    for system, _status in hosted_placeholders:
        lines.append(f"| {system} | pending | pending | pending | pending |")

    return "\n".join(lines) + "\n"


def inject_scorecard(
    target_path: Path,
    scorecard_markdown: str,
) -> bool:
    """Replace text between scorecard markers in a Markdown file.

    Returns True if the markers were found and the file was rewritten,
    False if either marker was missing (in which case the file is
    untouched and the caller should print the rendered scorecard to
    stdout instead).
    """
    if not target_path.is_file():
        raise FileNotFoundError(f"scorecard target file not found: {target_path}")
    original = target_path.read_text(encoding="utf-8")
    begin = original.find(SCORECARD_BEGIN_MARKER)
    end = original.find(SCORECARD_END_MARKER)
    if begin == -1 or end == -1 or end <= begin:
        return False
    head = original[: begin + len(SCORECARD_BEGIN_MARKER)]
    tail = original[end:]
    new_body = "\n\n" + scorecard_markdown.strip() + "\n\n"
    target_path.write_text(head + new_body + tail, encoding="utf-8")
    return True


def load_trace_per_turn(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_html_report(
    out: str | Path,
    *,
    title: str = "RetrievalCI Report",
    rag_report: ComparisonReport | None = None,
    baseline_rag_report: ComparisonReport | None = None,
    trace_metrics: dict[str, Any] | None = None,
    trace_per_turn: list[dict[str, Any]] | None = None,
    primary_metric: str = "retrieval_source_recall",
    regression_metric: str | None = None,
    max_drop: float = 0.02,
) -> None:
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        build_html_report(
            title=title,
            rag_report=rag_report,
            baseline_rag_report=baseline_rag_report,
            trace_metrics=trace_metrics,
            trace_per_turn=trace_per_turn,
            primary_metric=primary_metric,
            regression_metric=regression_metric,
            max_drop=max_drop,
        ),
        encoding="utf-8",
    )


def build_html_report(
    *,
    title: str = "RetrievalCI Report",
    rag_report: ComparisonReport | None = None,
    baseline_rag_report: ComparisonReport | None = None,
    trace_metrics: dict[str, Any] | None = None,
    trace_per_turn: list[dict[str, Any]] | None = None,
    primary_metric: str = "retrieval_source_recall",
    regression_metric: str | None = None,
    max_drop: float = 0.02,
) -> str:
    if rag_report is None and trace_metrics is None:
        msg = "at least one report input is required"
        raise ValueError(msg)

    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    sections: list[str] = []
    summary_items: list[Cell] = []

    if rag_report is not None:
        diag = diagnose_report(rag_report, primary_metric=primary_metric)
        summary_items.extend(
            [
                Cell("RAG systems", len(rag_report.systems)),
                Cell(str(len(rag_report.systems))),
                Cell("Questions", rag_report.n_questions),
                Cell(str(rag_report.n_questions)),
                Cell("RAG leader", diag.leader or "n/a"),
                Cell(diag.leader or "n/a"),
            ]
        )
        sections.append(_render_rag_section(rag_report, primary_metric=primary_metric))
        if baseline_rag_report is not None:
            sections.append(
                _render_rag_regression_section(
                    baseline_rag_report,
                    rag_report,
                    metric=regression_metric or primary_metric,
                    max_drop=max_drop,
                )
            )

    if trace_metrics is not None:
        best_policy = _best_trace_policy(trace_metrics)
        policy_count = len(trace_metrics.get("policies", {}))
        summary_items.extend(
            [
                Cell("Trace policies", policy_count),
                Cell(str(policy_count)),
                Cell("Best policy", best_policy or "n/a"),
                Cell(best_policy or "n/a"),
            ]
        )
        sections.append(_render_trace_section(trace_metrics, trace_per_turn or []))

    return _document(
        title=title,
        generated_at=generated_at,
        summary_html=_render_summary(summary_items),
        body="\n".join(sections),
    )


def _render_rag_section(report: ComparisonReport, *, primary_metric: str) -> str:
    diag = diagnose_report(report, primary_metric=primary_metric)
    findings = "".join(
        f"<li><span class=\"severity {finding.severity}\">{_h(finding.severity)}</span>"
        f"<code>{_h(finding.code)}</code> {_h(finding.message)}</li>"
        for finding in diag.findings[:6]
    )
    if not findings:
        findings = "<li>No diagnostic warnings for this primary metric.</li>"

    return f"""
<section class="band">
  <div class="section-head">
    <div>
      <p class="eyebrow">RAG architecture</p>
      <h2>What changed in the answer pipeline</h2>
    </div>
    {_status("Leader: " + (diag.leader or "n/a"), "neutral")}
  </div>
  <div class="diagnosis">
    <div>
      <h3>Diagnosis</h3>
      <dl class="facts">
        <div><dt>Primary metric</dt><dd>{_h(diag.primary_metric)}</dd></div>
        <div><dt>Bottleneck</dt><dd>{_h(diag.bottleneck)}</dd></div>
        <div><dt>Weakest tier</dt><dd>{_h(diag.weakest_tier or "n/a")}</dd></div>
      </dl>
    </div>
    <div>
      <h3>Recommended next experiment</h3>
      <p>{_h(diag.recommendation)}</p>
      <p class="muted">{_h(diag.next_experiment)}</p>
    </div>
  </div>
  <ul class="finding-list">{findings}</ul>
  <h3>System metrics</h3>
  {_render_rag_metric_table(report)}
  <h3>Failure examples</h3>
  {_render_rag_failure_table(report)}
</section>
"""


def _render_rag_regression_section(
    baseline: ComparisonReport,
    candidate: ComparisonReport,
    *,
    metric: str,
    max_drop: float,
) -> str:
    try:
        check = compare_reports(baseline, candidate, metric=metric, max_drop=max_drop)
    except ValueError as exc:
        return f"""
<section class="band">
  <div class="section-head">
    <div>
      <p class="eyebrow">Regression</p>
      <h2>Baseline comparison</h2>
    </div>
    {_status("Not comparable", "bad")}
  </div>
  <p>{_h(str(exc))}</p>
</section>
"""

    state = "good" if check.passed else "bad"
    status = "Passed" if check.passed else "Failed"
    failures = "".join(f"<li>{_h(failure.format())}</li>" for failure in check.failures)
    if not failures:
        failures = "<li>No selected system dropped beyond the configured threshold.</li>"

    return f"""
<section class="band">
  <div class="section-head">
    <div>
      <p class="eyebrow">Regression</p>
      <h2>Baseline comparison</h2>
    </div>
    {_status(status, state)}
  </div>
  <p class="muted">Metric: <code>{_h(metric)}</code>. Maximum allowed drop: {_fmt(max_drop)}.</p>
  {_render_rag_regression_table(baseline, candidate, check, metric)}
  <ul class="finding-list">{failures}</ul>
</section>
"""


def _render_trace_section(metrics: dict[str, Any], per_turn: list[dict[str, Any]]) -> str:
    best_policy = _best_trace_policy(metrics)
    return f"""
<section class="band">
  <div class="section-head">
    <div>
      <p class="eyebrow">Trace-state dynamics</p>
      <h2>Which state should retrieval receive</h2>
    </div>
    {_status("Best: " + (best_policy or "n/a"), "neutral")}
  </div>
  <h3>Policy metrics</h3>
  {_render_trace_metric_table(metrics)}
  <h3>State failure examples</h3>
  <div class="example-grid">
    {_render_trace_examples(per_turn, "zero_recall_at_k", "Zero recall")}
    {_render_trace_examples(per_turn, "stale_at_1", "Stale top hit")}
    {_render_trace_examples(per_turn, "false_lead_at_k", "False lead")}
  </div>
</section>
"""


def _render_summary(items: list[Cell]) -> str:
    pairs = list(zip(items[0::2], items[1::2], strict=False))
    if not pairs:
        return ""
    body = "".join(
        "<div class=\"summary-item\">"
        f"<span>{_h(label.text)}</span><strong>{_h(value.text)}</strong>"
        "</div>"
        for label, value in pairs
    )
    return f"<div class=\"summary-grid\">{body}</div>"


def _render_rag_metric_table(report: ComparisonReport) -> str:
    preferred = [
        "retrieval_source_recall",
        "retrieval_source_precision",
        "must_include_match",
        "answer_citation_recall",
        "answer_citation_precision",
        "faithfulness",
        "relevance",
        "refusal_rate",
        "latency_ms_p50",
        "tokens_used_total",
    ]
    present = {key for vals in report.by_system_metric.values() for key in vals}
    metrics = [m for m in preferred if m in present]
    metrics.extend(sorted(present - set(metrics)))

    rows: list[list[Cell]] = []
    for system in report.systems:
        values = report.by_system_metric.get(system, {})
        row = [Cell(system, system)]
        for metric in metrics:
            value = values.get(metric)
            row.append(Cell(_fmt(value), value, _metric_class(metric, value)))
        rows.append(row)
    return _table(["system", *[_metric_label(m) for m in metrics]], rows)


def _render_rag_regression_table(
    baseline: ComparisonReport,
    candidate: ComparisonReport,
    check: RegressionCheck,
    metric: str,
) -> str:
    rows: list[list[Cell]] = []
    for system in check.checked_systems:
        baseline_value = baseline.by_system_metric.get(system, {}).get(metric)
        candidate_value = candidate.by_system_metric.get(system, {}).get(metric)
        delta = (
            candidate_value - baseline_value
            if baseline_value is not None and candidate_value is not None
            else None
        )
        rows.append(
            [
                Cell(system, system),
                Cell(_fmt(baseline_value), baseline_value),
                Cell(_fmt(candidate_value), candidate_value),
                Cell(_fmt_delta(delta), delta, _delta_class(delta)),
            ]
        )
    return _table(["system", "baseline", "candidate", "delta"], rows)


def _render_rag_failure_table(report: ComparisonReport, *, limit: int = 12) -> str:
    rows: list[list[Cell]] = []
    for row in sorted(report.rows, key=_rag_risk_score, reverse=True)[:limit]:
        rows.append(
            [
                Cell(row.system, row.system),
                Cell(row.question_id, row.question_id),
                Cell(row.tier, row.tier),
                Cell(_rag_issue(row), _rag_risk_score(row)),
                Cell(_fmt(row.retrieval_source_recall), row.retrieval_source_recall),
                Cell(_fmt(row.answer_citation_recall), row.answer_citation_recall),
                Cell(_fmt(row.must_include_match), row.must_include_match),
                Cell(_join_sources(c.source_path for c in row.answer.citations)),
            ]
        )
    return _table(
        [
            "system",
            "question",
            "tier",
            "issue",
            "source recall",
            "citation recall",
            "term match",
            "sources",
        ],
        rows,
        empty="No per-question failures were found.",
    )


def _render_trace_metric_table(metrics: dict[str, Any]) -> str:
    policies = metrics.get("policies", {})
    ordered = sorted(
        policies.items(),
        key=lambda item: (
            -_safe_float(item[1].get("recall_at_5")),
            _safe_float(item[1].get("zero_recall_at_k")),
            item[0],
        ),
    )
    rows = [
        [
            Cell(policy, policy),
            Cell(_fmt(vals.get("n")), vals.get("n")),
            Cell(_fmt(vals.get("recall_at_5")), vals.get("recall_at_5"), "good"),
            Cell(_fmt(vals.get("zero_recall_at_k")), vals.get("zero_recall_at_k"), "bad"),
            Cell(_fmt(vals.get("drift_at_1")), vals.get("drift_at_1"), "bad"),
            Cell(_fmt(vals.get("stale_at_1")), vals.get("stale_at_1"), "bad"),
            Cell(_fmt(vals.get("false_lead_at_k")), vals.get("false_lead_at_k"), "bad"),
        ]
        for policy, vals in ordered
    ]
    return _table(
        ["policy", "n", "recall@5", "zero recall", "drift@1", "stale@1", "false lead"],
        rows,
        empty="No trace metrics were provided.",
    )


def _render_trace_examples(per_turn: list[dict[str, Any]], key: str, title: str) -> str:
    examples = [row for row in per_turn if row.get(key)][:5]
    if not examples:
        return f"""
<div class="example-block">
  <h4>{_h(title)}</h4>
  <p class="muted">No examples found.</p>
</div>
"""
    rows = [
        [
            Cell(str(row.get("policy", ""))),
            Cell(str(row.get("session_id", ""))),
            Cell(str(row.get("turn_id", ""))),
            Cell(_short(str(row.get("query_text", "")), 130)),
            Cell(_join_sources(row.get("gold_ids", []))),
            Cell(_join_sources(row.get("ranked_ids", [])[:3])),
        ]
        for row in examples
    ]
    return f"""
<div class="example-block">
  <h4>{_h(title)}</h4>
  {_table(["policy", "session", "turn", "query", "gold", "top retrieved"], rows)}
</div>
"""


def _rag_risk_score(row: RunResult) -> float:
    score = 0.0
    if row.refused:
        score += 100.0
    if row.retrieval_source_recall is not None:
        score += (1.0 - row.retrieval_source_recall) * 30.0
        if row.retrieval_source_recall == 0.0:
            score += 15.0
    if row.must_include_match is not None:
        score += (1.0 - row.must_include_match) * 20.0
    if row.answer_citation_recall is not None:
        score += (1.0 - row.answer_citation_recall) * 10.0
    return score


def _rag_issue(row: RunResult) -> str:
    if row.refused:
        reason = row.answer.refusal_reason or "refused"
        return f"refused: {reason}"
    if row.retrieval_source_recall == 0.0:
        return "zero source recall"
    if row.answer_citation_recall is not None and row.answer_citation_recall < 0.5:
        return "answer citation loss"
    if row.must_include_match is not None and row.must_include_match < 1.0:
        return "missing required terms"
    return "lower scoring row"


def _best_trace_policy(metrics: dict[str, Any]) -> str | None:
    policies = metrics.get("policies", {})
    if not policies:
        return None
    return max(policies.items(), key=lambda item: item[1].get("recall_at_5", float("-inf")))[0]


def _metric_class(metric: str, value: Any) -> str:
    number = _float_or_none(value)
    if number is None:
        return ""
    lower_is_better = metric in {
        "refusal_rate",
        "latency_ms_p50",
        "tokens_used_total",
        "zero_recall_at_k",
        "drift_at_1",
        "stale_at_1",
        "false_lead_at_k",
    }
    if lower_is_better:
        return "good" if number <= 0.05 else "bad" if number >= 0.25 else "warn"
    return "good" if number >= 0.8 else "bad" if number < 0.4 else "warn"


def _delta_class(delta: float | None) -> str:
    if delta is None:
        return ""
    if delta < 0:
        return "bad"
    if delta > 0:
        return "good"
    return "neutral"


def _table(headers: list[str], rows: list[list[Cell]], *, empty: str = "No rows.") -> str:
    if not rows:
        return f"<p class=\"empty\">{_h(empty)}</p>"
    head = "".join(f"<th><button type=\"button\">{_h(header)}</button></th>" for header in headers)
    body_rows = []
    for row in rows:
        cells = []
        for cell in row:
            sort_value = cell.text if cell.sort_value is None else cell.sort_value
            class_attr = f" class=\"{_h(cell.class_name)}\"" if cell.class_name else ""
            cells.append(
                f"<td{class_attr} data-sort-value=\"{_h(sort_value)}\">{_h(cell.text)}</td>"
            )
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        "<div class=\"table-wrap\"><table data-sortable=\"true\"><thead><tr>"
        + head
        + "</tr></thead><tbody>"
        + "".join(body_rows)
        + "</tbody></table></div>"
    )


def _status(text: str, state: str) -> str:
    return f"<span class=\"status {state}\">{_h(text)}</span>"


def _fmt(value: Any) -> str:
    number = _float_or_none(value)
    if number is None:
        if value is None:
            return "n/a"
        return str(value)
    if abs(number) >= 100:
        return f"{number:,.0f}"
    return f"{number:.3f}"


def _fmt_delta(value: float | None) -> str:
    if value is None or math.isnan(value):
        return "n/a"
    return f"{value:+.3f}"


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return number


def _safe_float(value: Any) -> float:
    number = _float_or_none(value)
    return number if number is not None else float("inf")


def _metric_label(value: str) -> str:
    return value.replace("_", " ")


def _join_sources(values: Iterable[Any]) -> str:
    shown = [str(value) for value in values if str(value)]
    if not shown:
        return "n/a"
    if len(shown) <= 3:
        return ", ".join(shown)
    return ", ".join(shown[:3]) + f", +{len(shown) - 3} more"


def _short(value: str, max_chars: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def _h(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _document(*, title: str, generated_at: str, summary_html: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_h(title)}</title>
  <style>
{REPORT_CSS}
  </style>
</head>
<body>
  <header>
    <div class="header-inner">
      <p class="eyebrow">RetrievalCI</p>
      <h1>{_h(title)}</h1>
      <p class="muted">
        Generated {generated_at}. Static artifact for RAG architecture and retrieval-state
        review.
      </p>
      {summary_html}
    </div>
  </header>
  <main>
    {body}
  </main>
  <script>
{SORT_TABLES_JS}
  </script>
</body>
</html>
"""
