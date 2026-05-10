# RAG Diagnostics Design

Status: implemented in `retrievalci/rag_eval/diagnostics.py` and wired into
`retrievalci rag run` Markdown reports.

## Purpose

The next RetrievalCI power-up is a diagnosis layer for RAG architecture eval.

Today, `retrievalci rag run` produces reliable metrics and pairwise
comparisons, but the report still asks the reader to infer the decision. The
diagnostics layer should turn those metrics into a product answer:

> What is the bottleneck, which system should I try next, and what should I
> ship or block in CI?

## Product Promise

Given a `ComparisonReport`, RetrievalCI should produce a short recommendation:

> `hybrid_rag` is the strongest candidate by retrieval source recall. The
> current run is retrieval-limited: answer quality tracks source recall, and
> citation recall remains below 0.60. Ship hybrid retrieval before changing the
> answer prompt. Next experiment: add reranking or increase top-k, then re-run
> multi-hop questions.

The diagnosis must be deterministic, explainable, and conservative. It should
not pretend a small or underpowered eval proves more than it does.

## Scope

In scope for the first implementation:

- choose a leader by configurable primary metric;
- classify bottleneck type;
- identify weakest tier;
- surface cost/latency tradeoffs;
- call out underpowered comparisons;
- produce Markdown bullets and structured JSON fields.

Out of scope for the first implementation:

- HTML reports;
- LLM-authored recommendations;
- arbitrary user-defined rule language;
- retriever-specific tuning beyond simple next-experiment advice.

## Proposed Module

Add:

```text
retrievalci/rag_eval/diagnostics.py
```

The module should not call systems, providers, or the filesystem. It should
accept only an in-memory `ComparisonReport`.

## Proposed Types

```python
class DiagnosticFinding(BaseModel):
    severity: Literal["info", "warning", "critical"]
    code: str
    message: str
    evidence: dict[str, float | str | int | bool]


class DiagnosticReport(BaseModel):
    primary_metric: str
    leader: str | None
    bottleneck: Literal[
        "retrieval_limited",
        "generation_limited",
        "citation_limited",
        "refusal_limited",
        "latency_or_cost_limited",
        "inconclusive",
    ]
    weakest_tier: str | None
    recommendation: str
    next_experiment: str
    findings: list[DiagnosticFinding]
```

Keep this separate from `ComparisonReport` initially so the core report schema
does not churn. Later, `ComparisonReport` can grow an optional `diagnostics`
field.

## API

```python
def diagnose_report(
    report: ComparisonReport,
    *,
    primary_metric: str = "must_include_match",
    min_meaningful_delta: float = 0.03,
    min_questions_for_confidence: int = 20,
) -> DiagnosticReport:
    ...


def diagnostics_to_markdown(diag: DiagnosticReport) -> str:
    ...
```

`report_to_markdown()` should prepend or append:

```markdown
## Diagnosis

- Leader: `hybrid_rag` on `retrieval_source_recall` (0.780).
- Bottleneck: retrieval-limited.
- Weakest tier: `multi_hop`.
- Recommendation: ship hybrid retrieval before changing answer prompts.
- Next experiment: add reranking or increase top-k on multi-hop queries.
```

## Bottleneck Rules

The rules should be deliberately simple and auditable.

### Retrieval-Limited

Trigger when:

- best `retrieval_source_recall` is low, e.g. `< 0.70`; or
- `must_include_match` roughly tracks `retrieval_source_recall`; or
- answer metrics are weak but retrieved source recall is also weak.

Recommendation:

- try hybrid retrieval, reranking, better embedder, higher top-k, or corpus
  enrichment before answer prompt changes.

### Generation-Limited

Trigger when:

- best `retrieval_source_recall` is high, e.g. `>= 0.80`; and
- `must_include_match` is materially lower, e.g. gap `>= 0.20`; and
- refusal rate is not the dominant issue.

Recommendation:

- improve answer prompt, context formatting, citation instructions, synthesis,
  or judge-grounded answer training.

### Citation-Limited

Trigger when:

- `retrieval_source_recall` is acceptable; and
- `answer_citation_recall` or `answer_citation_precision` is low.

Recommendation:

- fix citation formatting/parsing, enforce `[doc:...]` citations, or add a
  post-answer citation verifier.

### Refusal-Limited

Trigger when:

- refusal rate is high, e.g. `> 0.30`; and
- non-refusing systems retrieve enough evidence.

Recommendation:

- tune refusal threshold or evidence sufficiency gate.

### Latency-Or-Cost-Limited

Trigger when:

- leader's quality gain is within `min_meaningful_delta`; and
- leader costs materially more, e.g. tokens or latency are `> 2x` the cheaper
  candidate.

Recommendation:

- prefer the cheaper system or run a larger eval before adopting the expensive
  candidate.

### Inconclusive

Trigger when:

- `n_questions < min_questions_for_confidence`; or
- no primary metric exists across systems; or
- leader delta is below `min_meaningful_delta` and pairwise CI crosses zero.

Recommendation:

- expand eval set, especially weak tiers.

## Leader Selection

Leader selection should be deterministic:

1. sort systems by primary metric descending;
2. break ties by lower `refusal_rate`;
3. then lower `tokens_used_total`;
4. then lower `latency_ms_p50`;
5. then original `report.systems` order.

If no system has the primary metric, leader is `None` and bottleneck is
`inconclusive`.

## Weak Tier Selection

For the leader, inspect `report.by_system_tier_metric[leader]`.

Use `must_include_match` by default for tier weakness because all current evals
have it. If unavailable, use the primary metric. Return the tier with lowest
score.

Example:

```text
multi_hop: 0.42
single_hop: 0.77
contradiction: 0.50
```

Weakest tier: `multi_hop`.

## Pairwise Confidence

Use `report.pairwise` when available:

- If leader beats runner-up on primary metric and CI excludes zero, label the
  result as directional.
- If CI crosses zero, add a warning: underpowered or not statistically stable.
- If no pairwise data exists and `n_questions < 5`, add a warning explaining
  that pairwise bootstrap was not run.

Do not hide point estimates; just separate point-estimate leadership from
statistical confidence.

## JSON Shape

The JSON report should eventually include:

```json
{
  "diagnostics": {
    "primary_metric": "retrieval_source_recall",
    "leader": "hybrid_rag",
    "bottleneck": "retrieval_limited",
    "weakest_tier": "multi_hop",
    "recommendation": "Ship hybrid retrieval before changing answer prompts.",
    "next_experiment": "Add reranking on multi-hop questions.",
    "findings": [
      {
        "severity": "warning",
        "code": "LOW_RETRIEVAL_RECALL",
        "message": "Best retrieval_source_recall is below 0.70.",
        "evidence": {"retrieval_source_recall": 0.58}
      }
    ]
  }
}
```

For the first implementation, it is acceptable to write diagnostics only into
Markdown. The structured model should still exist so tests can assert behavior
without scraping Markdown.

## CLI Design

Add optional flags to `retrievalci rag run`:

```bash
retrievalci rag run \
  ... \
  --primary-metric retrieval_source_recall \
  --min-meaningful-delta 0.03 \
  --min-questions-for-confidence 20
```

Defaults:

- `primary_metric=must_include_match` to preserve current report semantics.
- `min_meaningful_delta=0.03`.
- `min_questions_for_confidence=20`.

## Report Placement

Put `## Diagnosis` immediately after the header and before aggregate metrics.
That makes the report useful in the first screen:

```markdown
# Eval comparison report

Systems: rag, claim_rag, hybrid_rag
Questions: 40 (...)

## Diagnosis

...

## Aggregate metrics by system
```

## Tests

Add `tests/rag_eval/test_diagnostics.py`.

Required cases:

1. leader selection by primary metric;
2. tie-break by lower token use;
3. retrieval-limited classification;
4. generation-limited classification;
5. citation-limited classification;
6. refusal-limited classification;
7. latency/cost-limited classification;
8. inconclusive when metric missing;
9. weakest tier selection;
10. Markdown contains leader, bottleneck, recommendation, and next experiment.

Keep tests synthetic. Build tiny `ComparisonReport` objects directly rather
than running full systems.

## Implementation Sequence

1. Add `retrievalci/rag_eval/diagnostics.py` with model + pure functions.
2. Add tests for the pure functions.
3. Update `report_to_markdown(report, diagnostics=None)` or call
   `diagnose_report()` internally with defaults.
4. Add runner CLI flags for primary metric and thresholds.
5. Write diagnostics into Markdown.
6. Optionally include diagnostics in JSON by extending `ComparisonReport`.

## Risks

- Heuristics can be overconfident. Mitigation: use conservative wording and
  emit `inconclusive` often on small evals.
- Metrics are sparse. Some systems/refusals produce `None` values. Mitigation:
  each rule must tolerate missing metrics.
- Primary metric choice is product-sensitive. Mitigation: keep it configurable.
- Current mock evals can produce odd metrics. Mitigation: tests should validate
  logic with synthetic reports, not rely on mock quality.

## Definition Of Done

- `retrievalci rag run` report includes `## Diagnosis`.
- Diagnostics are deterministic and unit-tested.
- Existing reports still render aggregate tables and pairwise comparisons.
- `make check` and `make smoke` pass.
- README includes one screenshot-like Markdown excerpt or command example.
