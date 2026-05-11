"""Schemas for the eval harness.

A run produces one RunResult per (system, question) pair. Metrics are computed over
the set of RunResults; the report compares systems on the same questions.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Tier = Literal["single_hop", "multi_hop", "contradiction"]


class QAItem(BaseModel):
    """One held-out QA pair with ground truth."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    tier: Tier
    question: str
    ground_truth_answer: str
    ground_truth_citations: tuple[str, ...]
    must_include_terms: tuple[str, ...] = ()
    must_not_include_terms: tuple[str, ...] = ()
    notes: str = ""

    # Hosted-RAG benchmark facets. Optional and orthogonal to `tier` so we can
    # mark a question as e.g. multi_hop AND unanswerable without inventing a
    # fourth tier value. `expected_abstain` distinguishes "this should refuse"
    # from "this is genuinely unanswerable" (the former is a policy assertion,
    # the latter is a corpus fact).
    corpus_id: str | None = None
    facets: tuple[str, ...] = ()
    unanswerable: bool = False
    expected_abstain: bool = False
    distractor_citations: tuple[str, ...] = ()
    temporal_anchor: str | None = None
    authored_by: str | None = None
    verified_by: str | None = None


class Citation(BaseModel):
    """A claim made by a system answer, traced back to source(s)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_path: str
    span: str | None = None  # short excerpt; None if span not tracked


class SystemAnswer(BaseModel):
    """One system's response to one question."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    answer: str
    citations: tuple[Citation, ...]
    latency_ms: float = Field(ge=0.0)
    tokens_used: int = Field(ge=0)
    refused: bool = False
    refusal_reason: str | None = None

    # Hosted-RAG benchmark fields. `retrieved_sources` and `answer_citations`
    # let hosted adapters report the retriever and answerer separately even
    # when the existing `citations` field would conflate them. Metrics prefer
    # `retrieved_sources` when set, otherwise fall back to `citations`.
    retrieved_sources: tuple[Citation, ...] = ()
    answer_citations: tuple[Citation, ...] = ()
    cost_usd: float | None = None
    corpus_version_hash: str | None = None
    index_build_id: str | None = None
    generator_model_id: str | None = None
    meta: dict[str, object] = Field(default_factory=dict)

    # Retrieval-only latency, exclusive of LLM generation. For Mode A hosted
    # systems (vertex_ai_rag, bedrock_kb, openai_file_search, azure_ai_search)
    # this equals `latency_ms` because they're retrieve-only. For local
    # systems that combine retrieval + generation, this records just the
    # retrieval step so the scorecard can compare retrieval latency apples-
    # to-apples across both kinds of systems.
    retrieval_latency_ms: float | None = None


class RunResult(BaseModel):
    """Outcome of running one system on one question, plus computed metrics."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    system: str
    question_id: str
    tier: Tier
    answer: SystemAnswer

    # Metrics. None means "not computable for this row" (e.g. precision is
    # undefined when no terms were cited).
    must_include_match: float | None = None
    must_not_include_violations: int | None = None

    # Parsed from [doc:...] tokens in the answer text — measures the answerer.
    answer_citation_precision: float | None = None
    answer_citation_recall: float | None = None

    # Computed from SystemAnswer.citations (i.e. what the retriever returned) —
    # measures the retriever, not the answerer. Useful as an upper bound on
    # what the answer COULD have cited.
    retrieval_source_precision: float | None = None
    retrieval_source_recall: float | None = None

    # LLM-judge metrics. None unless a Judge was wired into runner.run_eval().
    faithfulness: float | None = None  # 1-5 scale
    relevance: float | None = None  # 1-5 scale

    # Abstention correctness: 1.0 when the system's refusal matches the
    # question's expected_abstain flag, 0.0 when it disagrees, None when the
    # question has no abstention expectation. Without this metric, a system
    # that correctly refuses an unanswerable question is penalized by
    # must_include_match=0 and looks worse than a system that hallucinates.
    abstention_correctness: float | None = None

    answer_length_chars: int = 0
    refused: bool = False


class PairwiseDelta(BaseModel):
    """Difference between two systems on a single metric, with a bootstrap CI.

    `mean_diff` is mean(system_a) - mean(system_b). Positive = a is higher.
    The CI is a paired bootstrap over per-question metric values: each resample
    draws indices with replacement and recomputes the difference. CI bounds are
    the (alpha/2, 1-alpha/2) quantiles of the resampled differences.

    `significant` is True iff the CI excludes 0 — i.e., we can claim a directional
    difference at the chosen alpha. Sample sizes < ~30 typically produce wide
    CIs that fail this test; that's not the bootstrap's fault, that's signal
    that the experiment is underpowered.
    """

    model_config = ConfigDict(extra="forbid")

    metric: str
    system_a: str
    system_b: str
    mean_a: float
    mean_b: float
    mean_diff: float
    ci_low: float
    ci_high: float
    alpha: float
    n: int
    significant: bool


class ComparisonReport(BaseModel):
    """The aggregated multi-system result. Written to disk as JSON + Markdown."""

    model_config = ConfigDict(extra="forbid")

    systems: tuple[str, ...]
    n_questions: int
    n_per_tier: dict[Tier, int]
    rows: list[RunResult]

    # Per-system aggregates.
    by_system_metric: dict[str, dict[str, float]] = Field(default_factory=dict)
    by_system_tier_metric: dict[str, dict[Tier, dict[str, float]]] = Field(default_factory=dict)

    # Pairwise comparisons with bootstrap CIs. Empty when comparison was not
    # requested (e.g. n_questions < 5).
    pairwise: list[PairwiseDelta] = Field(default_factory=list)
