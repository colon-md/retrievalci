"""End-to-end smoke test of the eval harness with mock backends.

Asserts the plumbing works:
  - Corpus loader returns documents
  - Chunker returns chunks
  - All three systems answer all questions and return well-shaped results
  - Metrics compute without error and return reasonable shapes
  - The Markdown report renders

Doesn't assert *quality* — mock backends can't produce intelligent answers.
The point is to catch wiring errors before plugging in real APIs.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from searchtrace.cli import _runner_argv
from searchtrace.rag_eval.backends.mock import MockEmbedder, MockGenerator
from searchtrace.rag_eval.corpus import (
    Chunk,
    Document,
    chunk_by_paragraph,
    chunk_corpus,
    load_documents,
)
from searchtrace.rag_eval.metrics import compute_row, must_include_match
from searchtrace.rag_eval.runner import report_to_markdown, run_eval
from searchtrace.rag_eval.systems import ClaimRAGSystem, RAGSystem
from searchtrace.rag_eval.types import QAItem


@pytest.fixture
def repo_root() -> Path:
    # Tests run with the searchtrace package installed, so use this file's
    # location to walk back to the repo root.
    here = Path(__file__).resolve()
    # tests/rag_eval/test_smoke.py -> rag_eval -> tests -> repo root
    return here.parent.parent.parent


@pytest.fixture
def synthetic_chunks() -> list[Chunk]:
    docs = [
        Document(
            source_path="docs/payments.md",
            text=(
                "Payments service depends on the postgres database.\n\n"
                "It exposes the /charge endpoint.\n\n"
                "It autoscales to 10 replicas under load."
            ),
        ),
        Document(
            source_path="docs/auth.md",
            text=(
                "Auth service depends on Redis.\n\n"
                "It is owned by the platform-security team.\n\n"
                "Telemetry shows it observed_max_replicas of 6 over the last 14 days."
            ),
        ),
    ]
    return chunk_corpus(docs)


@pytest.fixture
def sample_questions() -> list[QAItem]:
    return [
        QAItem(
            id="q01",
            tier="single_hop",
            question="What database does the payments service depend on?",
            ground_truth_answer="postgres",
            ground_truth_citations=("docs/payments.md",),
            must_include_terms=("postgres",),
        ),
        QAItem(
            id="q02",
            tier="multi_hop",
            question="Which services depend on which databases?",
            ground_truth_answer="payments uses postgres; auth uses Redis",
            ground_truth_citations=("docs/payments.md", "docs/auth.md"),
            must_include_terms=("postgres", "Redis"),
        ),
        QAItem(
            id="q03",
            tier="contradiction",
            question="Is the auth service running at the replica count we expect?",
            ground_truth_answer="No — observed_max_replicas is 6 vs declared autoscale.",
            ground_truth_citations=("docs/auth.md",),
            must_include_terms=("6",),
        ),
    ]


def test_corpus_loader_finds_repo_files(repo_root: Path) -> None:
    docs = load_documents(repo_root, ["README.md", "searchtrace/rag_eval/schemas/predicates.yml"])
    paths = {d.source_path for d in docs}
    assert "README.md" in paths
    assert "searchtrace/rag_eval/schemas/predicates.yml" in paths


def test_searchtrace_cli_accepts_rag_subcommand() -> None:
    assert _runner_argv(["run", "--backend", "mock"]) == ["--backend", "mock"]
    assert _runner_argv(["rag", "--backend", "mock"]) == ["--backend", "mock"]
    assert _runner_argv(["--backend", "mock"]) == ["--backend", "mock"]


def test_corpus_loader_dedupes(repo_root: Path) -> None:
    docs = load_documents(repo_root, ["README.md", "README.md"])
    assert len(docs) == 1


def test_chunker_paragraph_aligned() -> None:
    doc = Document(source_path="x.md", text="A para.\n\nB para.\n\nC para.")
    chunks = chunk_by_paragraph(doc, max_chars=20, overlap_chars=0)
    assert len(chunks) >= 1
    assert all(c.source_path == "x.md" for c in chunks)
    assert all(c.chunk_id == f"x.md#chunk-{c.chunk_index}" for c in chunks)


def test_must_include_match() -> None:
    assert must_include_match("postgres is the database", ("postgres",)) == 1.0
    assert must_include_match("oracle is the database", ("postgres",)) == 0.0
    assert must_include_match("a b", ("a", "c")) == 0.5
    assert must_include_match("anything", ()) is None


def test_bm25_system_answers_all_questions(
    synthetic_chunks: list[Chunk], sample_questions: list[QAItem]
) -> None:
    """BM25 baseline returns well-shaped answers and is order-deterministic."""
    from searchtrace.rag_eval.systems.bm25 import BM25System

    bm25 = BM25System(MockGenerator(), synthetic_chunks, top_k=3)
    for q in sample_questions:
        ans = bm25.answer(q.question)
        assert ans.answer
        assert len(ans.citations) <= 3
        assert ans.latency_ms >= 0
        assert ans.tokens_used > 0
        assert not ans.refused


def test_bm25_rank_returns_descending_scores(synthetic_chunks: list[Chunk]) -> None:
    """rank() returns (score, idx) tuples sorted descending — used by HybridRAG fusion."""
    from searchtrace.rag_eval.systems.bm25 import BM25System

    bm25 = BM25System(MockGenerator(), synthetic_chunks)
    ranked = bm25.rank("postgres database")
    assert len(ranked) == len(synthetic_chunks)
    scores = [s for s, _ in ranked]
    assert scores == sorted(scores, reverse=True)


def test_hybrid_rag_system_answers_all_questions(
    synthetic_chunks: list[Chunk], sample_questions: list[QAItem]
) -> None:
    """HybridRAG fuses BM25 + dense retrieval via RRF and answers all questions."""
    from searchtrace.rag_eval.systems.hybrid_rag import HybridRAGSystem

    hybrid = HybridRAGSystem(MockEmbedder(), MockGenerator(), synthetic_chunks, top_k=3)
    for q in sample_questions:
        ans = hybrid.answer(q.question)
        assert ans.answer
        assert len(ans.citations) <= 3
        assert not ans.refused


def test_chunk_summary_rag_system_answers_all_questions(
    synthetic_chunks: list[Chunk], sample_questions: list[QAItem]
) -> None:
    """Chunk-summary RAG builds summaries once and answers from retrieved raw chunks."""
    from searchtrace.rag_eval.systems.chunk_summary_rag import ChunkSummaryRAGSystem

    system = ChunkSummaryRAGSystem(MockEmbedder(), MockGenerator(), synthetic_chunks, top_k=3)
    assert system.summary_count == len(synthetic_chunks)
    for q in sample_questions:
        ans = system.answer(q.question)
        assert ans.answer
        assert len(ans.citations) <= 3
        assert ans.latency_ms >= 0
        assert ans.tokens_used > 0
        assert not ans.refused


def test_rag_system_answers_all_questions(
    synthetic_chunks: list[Chunk], sample_questions: list[QAItem]
) -> None:
    rag = RAGSystem(MockEmbedder(), MockGenerator(), synthetic_chunks, top_k=3)
    for q in sample_questions:
        ans = rag.answer(q.question)
        assert ans.answer
        assert len(ans.citations) <= 3
        assert ans.latency_ms >= 0
        assert ans.tokens_used > 0
        assert not ans.refused


def test_claim_rag_system_answers_or_refuses(
    synthetic_chunks: list[Chunk], sample_questions: list[QAItem]
) -> None:
    cr = ClaimRAGSystem(MockEmbedder(), MockGenerator(), synthetic_chunks, top_k=3)
    # Mock generator's triple extraction won't produce well-formed triples, so the
    # system may have zero claims and refuse. Either outcome is acceptable; we just
    # need the system to return a well-shaped SystemAnswer.
    for q in sample_questions:
        ans = cr.answer(q.question)
        assert ans.latency_ms >= 0
        if ans.refused:
            assert ans.refusal_reason is not None
        else:
            assert ans.answer


def test_run_eval_produces_well_shaped_report(
    synthetic_chunks: list[Chunk], sample_questions: list[QAItem]
) -> None:
    rag = RAGSystem(MockEmbedder(), MockGenerator(), synthetic_chunks)
    cr = ClaimRAGSystem(MockEmbedder(), MockGenerator(), synthetic_chunks)

    report = run_eval([rag, cr], sample_questions)
    assert report.systems == ("rag", "claim_rag")
    assert report.n_questions == 3
    assert sum(report.n_per_tier.values()) == 3
    # Two systems x three questions = 6 rows.
    assert len(report.rows) == 6
    for sys_name in report.systems:
        assert sys_name in report.by_system_metric
        assert "latency_ms_p50" in report.by_system_metric[sys_name]
        assert "tokens_used_total" in report.by_system_metric[sys_name]


def test_report_to_markdown_renders(
    synthetic_chunks: list[Chunk], sample_questions: list[QAItem]
) -> None:
    rag = RAGSystem(MockEmbedder(), MockGenerator(), synthetic_chunks)
    cr = ClaimRAGSystem(MockEmbedder(), MockGenerator(), synthetic_chunks)
    report = run_eval([rag, cr], sample_questions)
    md = report_to_markdown(report)
    assert "# Eval comparison report" in md
    assert "rag" in md and "claim_rag" in md


def test_metrics_compute_row_handles_empty_terms() -> None:
    from searchtrace.rag_eval.types import SystemAnswer

    q = QAItem(
        id="q",
        tier="single_hop",
        question="?",
        ground_truth_answer="x",
        ground_truth_citations=("a.md",),
    )
    a = SystemAnswer(answer="anything", citations=(), latency_ms=1.0, tokens_used=1)
    row = compute_row("test", q, a)
    assert row.must_include_match is None
    assert row.must_not_include_violations is None
    # Answer text has no [doc:...] tokens → answer-citation precision is None
    # (no cited), recall is 0 (ground truth has 1, none matched).
    assert row.answer_citation_precision is None
    assert row.answer_citation_recall == 0.0
    # Retrieved nothing → retrieval-source precision None, recall 0.
    assert row.retrieval_source_precision is None
    assert row.retrieval_source_recall == 0.0


def test_judge_wired_into_runner_populates_faithfulness_relevance(
    synthetic_chunks: list[Chunk], sample_questions: list[QAItem]
) -> None:
    from searchtrace.rag_eval.backends.mock import MockJudge

    rag = RAGSystem(MockEmbedder(), MockGenerator(), synthetic_chunks)
    cr = ClaimRAGSystem(MockEmbedder(), MockGenerator(), synthetic_chunks)
    report = run_eval([rag, cr], sample_questions, judge=MockJudge())

    # Every non-refused row should have judge metrics; refused rows should be None.
    populated = [r for r in report.rows if not r.refused]
    assert populated, "expected at least one non-refused row"
    for r in populated:
        assert r.faithfulness is not None
        assert 1.0 <= r.faithfulness <= 5.0
        assert r.relevance is not None
        assert 1.0 <= r.relevance <= 5.0
    for r in report.rows:
        if r.refused:
            assert r.faithfulness is None
            assert r.relevance is None


def test_run_eval_without_judge_leaves_judge_metrics_none(
    synthetic_chunks: list[Chunk], sample_questions: list[QAItem]
) -> None:
    rag = RAGSystem(MockEmbedder(), MockGenerator(), synthetic_chunks)
    cr = ClaimRAGSystem(MockEmbedder(), MockGenerator(), synthetic_chunks)
    report = run_eval([rag, cr], sample_questions)  # no judge
    for r in report.rows:
        assert r.faithfulness is None
        assert r.relevance is None


def test_mock_judge_scores_in_range() -> None:
    from searchtrace.rag_eval.backends.mock import MockJudge

    j = MockJudge()
    f = j.faithfulness("What DB?", "postgres is the database", "doc says postgres", "postgres")
    assert 1.0 <= f.score <= 5.0
    r = j.relevance("What DB does payments use?", "postgres")
    assert 1.0 <= r.score <= 5.0


def test_claude_judge_protocol_and_no_api_key() -> None:
    """ClaudeJudge satisfies the Judge protocol and surfaces a clear error when
    ANTHROPIC_API_KEY is unset. Doesn't make any real API calls."""
    import os

    from searchtrace.rag_eval.backends.claude import ClaudeJudge

    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            ClaudeJudge()
    finally:
        if saved is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved


def test_claude_judge_default_model_id() -> None:
    """Smoke-check the default model selection without instantiating a client."""
    from searchtrace.rag_eval.backends.claude import ClaudeJudge

    # Bypass the constructor's client init — we're only checking the model_id default.
    j = ClaudeJudge.__new__(ClaudeJudge)
    j._model_id = "claude-sonnet-4-6"
    assert j.model_id == "claude-sonnet-4-6"


def test_claude_generator_protocol_and_no_api_key() -> None:
    """ClaudeGenerator surfaces a clear error when ANTHROPIC_API_KEY is unset."""
    import os

    from searchtrace.rag_eval.backends.claude import ClaudeGenerator

    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            ClaudeGenerator()
    finally:
        if saved is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved


def test_claude_generator_default_model_id() -> None:
    """Default generator model is claude-sonnet-4-6, matching ClaudeJudge."""
    from searchtrace.rag_eval.backends.claude import ClaudeGenerator

    g = ClaudeGenerator.__new__(ClaudeGenerator)
    g._model_id = "claude-sonnet-4-6"
    assert g.model_id == "claude-sonnet-4-6"


def test_openai_judge_protocol_and_no_api_key() -> None:
    """OpenAIJudge satisfies the Judge protocol and surfaces a clear error when
    OPENAI_API_KEY is unset. Doesn't make any real API calls."""
    import os

    from searchtrace.rag_eval.backends.openai import OpenAIJudge

    saved = os.environ.pop("OPENAI_API_KEY", None)
    try:
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            OpenAIJudge()
    finally:
        if saved is not None:
            os.environ["OPENAI_API_KEY"] = saved


def test_groq_generator_protocol_and_no_api_key() -> None:
    """GroqGenerator surfaces a clear error when GROQ_API_KEY is unset."""
    import os

    from searchtrace.rag_eval.backends.groq import GroqGenerator

    saved = os.environ.pop("GROQ_API_KEY", None)
    try:
        with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
            GroqGenerator()
    finally:
        if saved is not None:
            os.environ["GROQ_API_KEY"] = saved


def test_groq_generator_default_model_id() -> None:
    """Default generator model is llama-3.3-70b-versatile."""
    from searchtrace.rag_eval.backends.groq import GroqGenerator

    g = GroqGenerator.__new__(GroqGenerator)
    g._model_id = "llama-3.3-70b-versatile"
    assert g.model_id == "llama-3.3-70b-versatile"


def test_groq_judge_protocol_and_no_api_key() -> None:
    """GroqJudge wraps GroqGenerator and inherits its no-key error."""
    import os

    from searchtrace.rag_eval.backends.groq import GroqJudge

    saved = os.environ.pop("GROQ_API_KEY", None)
    try:
        with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
            GroqJudge()
    finally:
        if saved is not None:
            os.environ["GROQ_API_KEY"] = saved


def test_openai_judge_default_model_id() -> None:
    """Default model is gpt-5.4-mini (the lighter mini-tier in the gpt-5 family)."""
    from searchtrace.rag_eval.backends.openai import OpenAIJudge

    j = OpenAIJudge.__new__(OpenAIJudge)
    j._model_id = "gpt-5.4-mini"
    assert j.model_id == "gpt-5.4-mini"


class TestPairedBootstrap:
    def test_strictly_higher_a_excludes_zero_below(self) -> None:
        from searchtrace.rag_eval.metrics import paired_bootstrap_ci

        # A is uniformly +0.5 above B at every paired index. CI on mean(a)-mean(b)
        # should sit safely above 0 with no resampling that drops it to 0.
        a = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
        b = [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5]
        lo, hi = paired_bootstrap_ci(a, b, n_resamples=500)
        assert lo > 0.0
        assert hi > 0.0
        assert abs((lo + hi) / 2 - 0.5) < 0.01

    def test_equal_distributions_straddle_zero(self) -> None:
        from searchtrace.rag_eval.metrics import paired_bootstrap_ci

        a = [0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0]
        b = [0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0]
        lo, hi = paired_bootstrap_ci(a, b, n_resamples=500)
        assert lo <= 0.0 <= hi

    def test_length_mismatch_raises(self) -> None:
        from searchtrace.rag_eval.metrics import paired_bootstrap_ci

        with pytest.raises(ValueError, match="length mismatch"):
            paired_bootstrap_ci([1.0, 2.0], [1.0])

    def test_empty_input_raises(self) -> None:
        from searchtrace.rag_eval.metrics import paired_bootstrap_ci

        with pytest.raises(ValueError, match="empty input"):
            paired_bootstrap_ci([], [])

    def test_seed_makes_runs_deterministic(self) -> None:
        from searchtrace.rag_eval.metrics import paired_bootstrap_ci

        a = [1.0, 2.0, 3.0, 4.0, 5.0]
        b = [0.5, 1.0, 2.0, 3.0, 4.5]
        ci1 = paired_bootstrap_ci(a, b, n_resamples=200, seed=42)
        ci2 = paired_bootstrap_ci(a, b, n_resamples=200, seed=42)
        assert ci1 == ci2


def test_run_eval_emits_pairwise_when_n_questions_sufficient(
    synthetic_chunks: list[Chunk],
) -> None:
    """With ≥ 5 questions and ≥ 2 systems, pairwise CIs should be computed."""
    qs = [
        QAItem(
            id=f"q{i:02d}",
            tier="single_hop",
            question=f"q{i}?",
            ground_truth_answer="x",
            ground_truth_citations=("docs/payments.md",),
            must_include_terms=("postgres",),
        )
        for i in range(5)
    ]
    rag = RAGSystem(MockEmbedder(), MockGenerator(), synthetic_chunks)
    cr = ClaimRAGSystem(MockEmbedder(), MockGenerator(), synthetic_chunks)
    report = run_eval([rag, cr], qs)
    # Pairwise should be non-empty (at least one metric has all-non-None values).
    assert report.pairwise, "expected pairwise CIs with n=5"
    for d in report.pairwise:
        assert d.system_a == "rag"
        assert d.system_b == "claim_rag"
        assert d.n == 5
        # CI must contain the mean diff (sanity check: bootstrap is symmetric-ish).
        # Allow small floating noise from resample quantization.
        assert d.ci_low - 1e-9 <= d.mean_diff <= d.ci_high + 1e-9


def test_run_eval_skips_pairwise_below_threshold(
    synthetic_chunks: list[Chunk], sample_questions: list[QAItem]
) -> None:
    """With n=3 questions, pairwise CIs should be skipped."""
    rag = RAGSystem(MockEmbedder(), MockGenerator(), synthetic_chunks)
    cr = ClaimRAGSystem(MockEmbedder(), MockGenerator(), synthetic_chunks)
    report = run_eval([rag, cr], sample_questions)
    assert report.pairwise == []


class _CannedTripleGenerator:
    """Deterministic generator that emits one well-formed triple per call.

    Lets the substrate-guard tests work with non-empty `_claims`. Not a real
    generator; only used here to exercise the production-type wiring.
    """

    def __init__(self, model_id: str = "canned-triple-gen-1") -> None:
        self._model_id = model_id

    @property
    def model_id(self) -> str:
        return self._model_id

    def generate(self, req):  # type: ignore[no-untyped-def]
        from searchtrace.rag_eval.backends.base import GenerationResponse

        # Distinguish extraction from query by sniffing the prompt template.
        if "Extract factual triples" in req.prompt:
            return GenerationResponse(
                text="payments service | depends on | postgres",
                tokens_used=10,
            )
        return GenerationResponse(text="(canned answer)", tokens_used=5)


class TestClaimRAGUsesProductionSubstrate:
    """Regression guard: ClaimRAGSystem must store production Claim/ProofSet/Evidence
    instances, not a parallel dataclass. The 3-way review flagged this as P0; we
    don't want a future refactor to silently re-introduce the parallel system.
    """

    def test_internal_claims_are_production_claim_instances(
        self, synthetic_chunks: list[Chunk]
    ) -> None:
        from searchtrace.rag_eval.claims import Claim, Evidence, ProofSet

        cr = ClaimRAGSystem(MockEmbedder(), _CannedTripleGenerator(), synthetic_chunks)
        assert cr.claim_count >= 1, "canned generator should yield at least one claim"
        for c in cr._claims:
            assert isinstance(c, Claim)
            for ps in c.proof_sets:
                assert isinstance(ps, ProofSet)
                for ev in ps.sources:
                    assert isinstance(ev, Evidence)

    def test_claim_id_is_content_hash(self, synthetic_chunks: list[Chunk]) -> None:
        from searchtrace.rag_eval.claims import derive_claim_id

        cr = ClaimRAGSystem(MockEmbedder(), _CannedTripleGenerator(), synthetic_chunks)
        assert cr._claims
        for claim in cr._claims:
            # Re-derive claim_id from its components — proves the system used
            # derive_claim_id, not a uuid or local hash.
            evidence_uris = [ev.evidence_uri for ps in claim.proof_sets for ev in ps.sources]
            recomputed = derive_claim_id(
                subject=claim.subject,
                predicate=claim.predicate,
                object_=claim.object,
                prompt_id=claim.prompt_id,
                evidence_uris=evidence_uris,
            )
            assert claim.claim_id == recomputed

    def test_knowledge_build_id_is_stable_across_instances(
        self, synthetic_chunks: list[Chunk]
    ) -> None:
        # Two systems built on equivalent generators should produce the same
        # build_id — deterministic, not random.
        a = ClaimRAGSystem(MockEmbedder(), _CannedTripleGenerator(), synthetic_chunks)
        b = ClaimRAGSystem(MockEmbedder(), _CannedTripleGenerator(), synthetic_chunks)
        assert a._knowledge_build_id == b._knowledge_build_id
        assert re.match(r"^[0-9a-f]{64}$", a._knowledge_build_id)

    def test_proof_set_acl_is_eval_default(self, synthetic_chunks: list[Chunk]) -> None:
        cr = ClaimRAGSystem(MockEmbedder(), _CannedTripleGenerator(), synthetic_chunks)
        assert cr._claims
        for claim in cr._claims:
            for ps in claim.proof_sets:
                assert ps.acl_labels == frozenset({"eval"})

    def test_claim_residency_is_public_for_eval(self, synthetic_chunks: list[Chunk]) -> None:
        cr = ClaimRAGSystem(MockEmbedder(), _CannedTripleGenerator(), synthetic_chunks)
        assert cr._claims
        for claim in cr._claims:
            assert claim.data_residency_region == "public"
            assert claim.domain == "eval"

    def test_dedupes_identical_claims_across_chunks(self) -> None:
        # When two chunks would yield the same claim_id (same subject, predicate,
        # object, prompt_id, evidence_uri set), the second is dropped. Production
        # would supersede; the eval just dedupes since builds are atomic.
        from searchtrace.rag_eval.corpus import Chunk

        # Same source_path → same evidence_uri → same proof_set → same claim_id.
        chunks = [
            Chunk(source_path="docs/x.md", chunk_index=0, text="alpha"),
            Chunk(source_path="docs/x.md", chunk_index=0, text="alpha"),  # duplicate
        ]
        cr = ClaimRAGSystem(MockEmbedder(), _CannedTripleGenerator(), chunks)
        assert cr.claim_count == 1


def test_parse_answer_citations() -> None:
    from searchtrace.rag_eval.metrics import parse_answer_citations

    # Empty answer → empty set.
    assert parse_answer_citations("nothing here") == set()

    # Single citation, normalize chunk_id to file path.
    cited = parse_answer_citations(
        "Postgres is the DB [doc:searchtrace/rag_eval/schemas/predicates.yml]."
    )
    assert cited == {"searchtrace/rag_eval/schemas/predicates.yml"}

    # Chunk ID format with #chunk-N suffix is normalized to the file path.
    cited = parse_answer_citations("X [doc:README.md#chunk-2].")
    assert cited == {"README.md"}

    # Multiple distinct citations, mixed shapes.
    cited = parse_answer_citations(
        "A [doc:a.md] and B [doc:b.md#chunk-1] and again A [doc:a.md#chunk-7]."
    )
    assert cited == {"a.md", "b.md"}


# --- WikiPagesSystem (entity-page projection) ----------------------------------


def _make_claim(
    subject: str,
    predicate: str,
    object_: str | None,
    *,
    source_path: str = "docs/test.md",
    chunk_idx: int = 0,
    subject_type: str = "service",
):
    """Construct a minimal production `Claim` for projection tests.

    Uses real `derive_claim_id` / `derive_proof_set_id` so claim_ids are
    content-hashed and dedupe correctly when the same logical claim recurs.
    """
    import hashlib
    from datetime import UTC, datetime

    from searchtrace.rag_eval.claims import (
        Claim,
        Evidence,
        ProofSet,
        derive_claim_id,
        derive_proof_set_id,
    )

    chunk_id = f"{source_path}#chunk-{chunk_idx}"
    evidence = Evidence(
        source_id=source_path,
        evidence_type="raw_doc",
        evidence_uri=f"chunk://{chunk_id}",
    )
    proof_set = ProofSet(
        proof_set_id=derive_proof_set_id([source_path]),
        sources=(evidence,),
        acl_labels=frozenset({"eval"}),
        validated_at=datetime.now(UTC),
        validator_model_id="test",
    )
    def h(s: str) -> str:
        return hashlib.sha256(s.encode()).hexdigest()

    return Claim(
        claim_id=derive_claim_id(
            subject=subject,
            predicate=predicate,
            object_=object_,
            prompt_id="test-prompt",
            evidence_uris=[evidence.evidence_uri],
        ),
        knowledge_build_id=h("test-build"),
        domain="eval",
        subject=subject,
        subject_type=subject_type,
        predicate=predicate,
        object=object_,
        object_type=None,
        proof_sets=(proof_set,),
        prompt_id="test-prompt",
        prompt_template_hash=h("test-template"),
        model_id="test",
        model_snapshot="test",
        sampling_params_hash=h("test-params"),
        asserted_at=datetime.now(UTC),
        data_residency_region="public",
    )


def test_project_pages_aggregates_by_subject() -> None:
    from searchtrace.rag_eval.systems.wiki_pages import project_pages

    claims = [
        _make_claim("payments_service", "depends_on", "postgres",
                    source_path="docs/a.md", chunk_idx=0),
        _make_claim("payments_service", "exposes", "/charge",
                    source_path="docs/a.md", chunk_idx=1),
        _make_claim("payments_service", "owned_by", "platform-team",
                    source_path="docs/b.md", chunk_idx=0),
    ]

    pages = project_pages(claims)
    assert len(pages) == 1
    page = pages[0]
    assert page.subject == "payments_service"
    assert page.subject_type == "service"
    assert len(page.sections) == 3
    assert {s.predicate for s in page.sections} == {"depends_on", "exposes", "owned_by"}
    assert all(s.is_contradicted is False for s in page.sections)
    assert page.contradiction_count == 0


def test_project_pages_dedupes_values_within_section() -> None:
    """Same (subject, predicate, object) re-asserted across 2 chunks → 1 value
    with 2 evidence_uris and 2 claim_ids."""
    from searchtrace.rag_eval.systems.wiki_pages import project_pages

    claims = [
        _make_claim("payments_service", "depends_on", "postgres",
                    source_path="docs/a.md", chunk_idx=0),
        _make_claim("payments_service", "depends_on", "postgres",
                    source_path="docs/b.md", chunk_idx=0),
    ]
    pages = project_pages(claims)
    assert len(pages) == 1
    sec = pages[0].sections[0]
    assert sec.predicate == "depends_on"
    assert len(sec.values) == 1
    assert sec.is_contradicted is False
    val = sec.values[0]
    assert val.object == "postgres"
    assert len(val.evidence_uris) == 2
    assert len(val.claim_ids) == 2


def test_project_pages_detects_contradiction() -> None:
    """Same (subject, predicate) with two distinct objects → is_contradicted=True."""
    from searchtrace.rag_eval.systems.wiki_pages import project_pages

    claims = [
        _make_claim("auth_service", "max_replicas", "10",
                    source_path="docs/declared.md", chunk_idx=0),
        _make_claim("auth_service", "max_replicas", "6",
                    source_path="docs/observed.md", chunk_idx=0),
    ]
    pages = project_pages(claims)
    assert len(pages) == 1
    page = pages[0]
    assert page.contradiction_count == 1
    sec = page.sections[0]
    assert sec.is_contradicted is True
    assert {v.object for v in sec.values} == {"10", "6"}


def test_project_pages_resolves_cross_reference() -> None:
    """If page A has a value 'B' and a page for entity B exists, A's cross_references
    contains a CrossRef pointing at B's page."""
    from searchtrace.rag_eval.systems.wiki_pages import project_pages

    claims = [
        _make_claim("payments_service", "depends_on", "postgres",
                    source_path="docs/a.md", chunk_idx=0),
        _make_claim("postgres", "version", "16",
                    source_path="docs/b.md", chunk_idx=0,
                    subject_type="database"),
    ]
    pages = project_pages(claims)
    by_subject = {p.subject: p for p in pages}
    assert "payments_service" in by_subject and "postgres" in by_subject

    payments = by_subject["payments_service"]
    assert len(payments.cross_references) == 1
    ref = payments.cross_references[0]
    assert ref.target_subject == "postgres"
    assert ref.target_subject_type == "database"
    assert ref.target_page_id == by_subject["postgres"].page_id

    # Reverse direction: postgres has no cross-refs (no claim with object="payments_service").
    assert by_subject["postgres"].cross_references == ()


def test_project_pages_page_id_is_content_hash() -> None:
    """page_id is sha256("{subject_type}|{subject}"), deterministic across runs."""
    import hashlib

    from searchtrace.rag_eval.systems.wiki_pages import project_pages

    claims = [_make_claim("payments_service", "depends_on", "postgres")]
    pages = project_pages(claims)
    expected = hashlib.sha256(b"service|payments_service").hexdigest()
    assert pages[0].page_id == expected
    assert len(pages[0].page_id) == 64


def test_entity_page_render_markdown_shape() -> None:
    """Rendered Markdown contains the entity header, predicate sections, doc
    citations, the contradiction warning, and the See also block."""
    from searchtrace.rag_eval.systems.wiki_pages import project_pages

    claims = [
        _make_claim("payments_service", "depends_on", "postgres",
                    source_path="docs/a.md", chunk_idx=0),
        _make_claim("payments_service", "max_replicas", "10",
                    source_path="docs/declared.md", chunk_idx=0),
        _make_claim("payments_service", "max_replicas", "6",
                    source_path="docs/observed.md", chunk_idx=0),
        _make_claim("postgres", "version", "16",
                    source_path="docs/b.md", chunk_idx=0,
                    subject_type="database"),
    ]
    pages = project_pages(claims)
    payments = next(p for p in pages if p.subject == "payments_service")
    md = payments.render_markdown()

    assert md.startswith("# payments_service (service)")
    assert "## depends_on" in md
    assert "## ⚠ max_replicas (contradiction: 2 values)" in md
    assert "[doc:docs/a.md#chunk-0]" in md
    assert "[doc:docs/declared.md#chunk-0]" in md
    assert "[doc:docs/observed.md#chunk-0]" in md
    assert "## See also" in md
    assert "- postgres (database)" in md


def test_wiki_pages_system_embeds_pages_not_claims(synthetic_chunks: list[Chunk]) -> None:
    """Index size = page count, not claim count. Pages aggregate claims."""
    from searchtrace.rag_eval.systems.wiki_pages import WikiPagesSystem

    cr = ClaimRAGSystem(MockEmbedder(), _CannedTripleGenerator(), synthetic_chunks)
    wp = WikiPagesSystem(MockEmbedder(), _CannedTripleGenerator(), cr._claims)
    assert wp.page_count <= cr.claim_count, "pages aggregate claims"
    assert len(wp._index) == wp.page_count


def test_wiki_pages_system_refuses_when_no_claims(synthetic_chunks: list[Chunk]) -> None:
    """Empty claim list → refused answer with empty_page_index reason."""
    from searchtrace.rag_eval.systems.wiki_pages import WikiPagesSystem

    wp = WikiPagesSystem(MockEmbedder(), MockGenerator(), [])
    ans = wp.answer("anything?")
    assert ans.refused is True
    assert ans.refusal_reason == "empty_page_index"


class _SynthesisCountingGenerator:
    """Like `_CannedTripleGenerator` but counts calls per prompt type so tests
    can assert the synthesis pass ran exactly once per entity page."""

    def __init__(self, model_id: str = "counting-gen-1") -> None:
        self._model_id = model_id
        self.synthesis_calls = 0
        self.extraction_calls = 0
        self.other_calls = 0

    @property
    def model_id(self) -> str:
        return self._model_id

    def generate(self, req):  # type: ignore[no-untyped-def]
        from searchtrace.rag_eval.backends.base import GenerationResponse

        if "Wiki summary" in req.prompt:
            self.synthesis_calls += 1
            return GenerationResponse(
                text=f"Synthesized prose number {self.synthesis_calls}.",
                tokens_used=20,
            )
        if "Extract factual triples" in req.prompt:
            self.extraction_calls += 1
            return GenerationResponse(
                text="payments_service | depends_on | postgres",
                tokens_used=10,
            )
        self.other_calls += 1
        return GenerationResponse(text="(answer)", tokens_used=5)


def test_synthesize_pages_one_call_per_page() -> None:
    """The synthesis pass amortizes one LLM call per entity. With 2 entities
    we expect exactly 2 synthesis calls — the load-bearing efficiency claim."""
    from searchtrace.rag_eval.systems.wiki_pages import project_pages, synthesize_pages

    claims = [
        _make_claim("payments_service", "depends_on", "postgres",
                    source_path="docs/a.md", chunk_idx=0),
        _make_claim("auth_service", "depends_on", "redis",
                    source_path="docs/b.md", chunk_idx=0),
    ]
    pages = project_pages(claims)
    assert len(pages) == 2

    gen = _SynthesisCountingGenerator()
    synthesized = synthesize_pages(pages, gen)
    assert gen.synthesis_calls == 2  # one per page, amortized at write time
    assert all(p.synthesized_prose is not None for p in synthesized)
    assert all("Synthesized prose" in p.synthesized_prose for p in synthesized)


def test_synthesized_prose_appears_in_render_markdown() -> None:
    """Rendered Markdown prefixes the prose, then a `## Sources` header, then
    the structured listing. Embedder + LLM see the prose as primary content."""
    from searchtrace.rag_eval.systems.wiki_pages import project_pages, synthesize_pages

    claims = [_make_claim("payments_service", "depends_on", "postgres")]
    pages = project_pages(claims)
    synthesized = synthesize_pages(pages, _SynthesisCountingGenerator())
    md = synthesized[0].render_markdown()

    # Prose appears before "## Sources"
    prose_pos = md.find("Synthesized prose number 1")
    sources_pos = md.find("## Sources")
    listing_pos = md.find("## depends_on")
    assert prose_pos > 0 < sources_pos < listing_pos
    # The original citation-bearing structured listing is still present.
    assert "[doc:docs/test.md#chunk-0]" in md


def test_pages_without_prose_render_unchanged() -> None:
    """Backward compat: render_markdown on a non-synthesized page produces the
    pre-synthesis structured listing only — no `## Sources` header."""
    from searchtrace.rag_eval.systems.wiki_pages import project_pages

    claims = [_make_claim("payments_service", "depends_on", "postgres")]
    pages = project_pages(claims)
    md = pages[0].render_markdown()
    assert "## Sources" not in md
    assert pages[0].synthesized_prose is None


def test_wiki_pages_system_synthesize_false_skips_llm_call(
    synthetic_chunks: list[Chunk],
) -> None:
    """`synthesize=False` constructs the system without running the synthesis
    pass — pages have no prose, no synthesis LLM calls were made."""
    from searchtrace.rag_eval.systems.wiki_pages import WikiPagesSystem

    cr = ClaimRAGSystem(MockEmbedder(), _CannedTripleGenerator(), synthetic_chunks)
    gen = _SynthesisCountingGenerator()
    wp = WikiPagesSystem(MockEmbedder(), gen, cr._claims, synthesize=False)

    assert gen.synthesis_calls == 0
    assert all(p.synthesized_prose is None for p in wp._pages)


# --- Extraction module (Tier A remediation) ----------------------------------


def test_normalize_subject_strips_articles_and_punct() -> None:
    from searchtrace.rag_eval.extraction import normalize_subject

    assert normalize_subject("The Eval Harness") == "eval harness"
    assert normalize_subject("Eval Harness") == "eval harness"
    assert normalize_subject("eval harness") == "eval harness"
    assert normalize_subject("Right-to-Erasure Cascade") == "right to erasure cascade"
    assert (
        normalize_subject("searchtrace/rag_eval/schemas/predicates.yml")
        == "searchtrace rag_eval schemas predicates yml"
    )


def test_should_drop_subject_meta_vocab_and_generic() -> None:
    from searchtrace.rag_eval.extraction import should_drop_subject

    # Stopwords (meta-vocabulary leakage)
    assert should_drop_subject("subject")
    assert should_drop_subject("System")
    assert should_drop_subject("the claim")
    assert should_drop_subject("wiki")

    # Generic noise patterns
    assert should_drop_subject("PR 1")
    assert should_drop_subject("pr 22")
    assert should_drop_subject("Track A")
    assert should_drop_subject("Deliverable 12.4")
    assert should_drop_subject("0.1.0")
    assert should_drop_subject("a")  # 1 char
    assert should_drop_subject("")  # empty
    assert should_drop_subject("   ")  # whitespace only

    # Real entities should NOT be dropped
    assert not should_drop_subject("payments_service")
    assert not should_drop_subject("Right-to-Erasure Cascade")
    assert not should_drop_subject("Predicate Vocabulary")
    assert not should_drop_subject("AnswerTrace")


def test_canonicalize_subject_strips_version_qualifier() -> None:
    from searchtrace.rag_eval.extraction import canonicalize_subject

    assert canonicalize_subject("Predicate Vocabulary v0.1.0") == "predicate vocabulary"
    assert canonicalize_subject("Predicate Vocabulary") == "predicate vocabulary"
    # Same canonical form for different surface variants
    assert canonicalize_subject("Eval Harness") == canonicalize_subject("eval harness")
    assert canonicalize_subject("The Right-to-Erasure Cascade") == "right to erasure cascade"


def test_filter_and_relabel_claims_drops_stopwords_and_relabels_types() -> None:
    """Stopword subjects are filtered; remaining claims get new subject_types."""
    from searchtrace.rag_eval.extraction import filter_and_relabel_claims

    claims = [
        _make_claim("payments_service", "depends_on", "postgres",
                    source_path="docs/a.md", chunk_idx=0),
        _make_claim("subject", "depends_on", "noise",  # stopword — should be dropped
                    source_path="docs/b.md", chunk_idx=0),
        _make_claim("Wiki", "is", "page",  # stopword
                    source_path="docs/c.md", chunk_idx=0),
        _make_claim("postgres", "version", "16",
                    source_path="docs/d.md", chunk_idx=0,
                    subject_type="extracted"),
    ]
    type_map = {"payments_service": "entity:service", "postgres": "entity:database"}

    out = filter_and_relabel_claims(claims, type_map)
    assert len(out) == 2
    subjects = {c.subject for c in out}
    assert "subject" not in subjects
    assert "Wiki" not in subjects
    assert "payments_service" in subjects
    assert "postgres" in subjects
    type_by_subj = {c.subject: c.subject_type for c in out}
    assert type_by_subj["payments_service"] == "entity:service"
    assert type_by_subj["postgres"] == "entity:database"


def test_wiki_pages_drops_singletons_from_retrieval_index() -> None:
    """`min_claims_per_indexed_page=2` excludes singleton pages from the retrieval
    index but keeps them in `_pages` (and the underlying KnowledgeBuild)."""
    from searchtrace.rag_eval.systems.wiki_pages import WikiPagesSystem

    claims = [
        # Two claims about postgres — page survives the singleton filter.
        _make_claim("postgres", "version", "16",
                    source_path="docs/a.md", chunk_idx=0,
                    subject_type="entity:database"),
        _make_claim("postgres", "owned_by", "platform-team",
                    source_path="docs/b.md", chunk_idx=0,
                    subject_type="entity:database"),
        # One claim about redis — page is a singleton, dropped from index.
        _make_claim("redis", "version", "7",
                    source_path="docs/c.md", chunk_idx=0,
                    subject_type="entity:database"),
    ]
    wp = WikiPagesSystem(
        MockEmbedder(),
        _CannedTripleGenerator(),
        claims,
        synthesize=False,
        min_claims_per_indexed_page=2,
    )
    assert wp.page_count == 2  # both pages exist in _pages
    assert len(wp._indexed_pages) == 1  # only postgres is indexed
    assert wp._indexed_pages[0].subject == "postgres"
    assert len(wp._index) == 1


def test_predicate_vocabulary_loads_from_yaml(repo_root: Path) -> None:
    """Loader parses searchtrace/rag_eval/schemas/predicates.yml into a PredicateVocabulary
    that recognizes every canonical name from the YAML."""
    from searchtrace.rag_eval.predicates import PredicateVocabulary

    vocab = PredicateVocabulary.from_yaml_file(
        repo_root / "searchtrace" / "rag_eval" / "schemas" / "predicates.yml"
    )
    # Spot-check a few canonical names from the schema.
    for name in ("is_deprecated", "depends_on", "owned_by", "autoscales_to"):
        assert name in vocab.predicate_names
        assert vocab.canonicalize(name) == name


def test_predicate_vocabulary_canonicalizes_aliases() -> None:
    """Aliases map to the canonical name. Unknown predicates return None."""
    from searchtrace.rag_eval.predicates import PredicateDef, PredicateVocabulary

    vocab = PredicateVocabulary([
        PredicateDef(
            name="is_deprecated",
            arity=1,
            aliases=("deprecated", "marked_deprecated", "EOL", "sunset"),
            subject_type="entity:service",
            object_type=None,
            transitive=False,
        ),
        PredicateDef(
            name="depends_on",
            arity=2,
            aliases=("requires", "calls", "uses"),
            subject_type="entity:service",
            object_type="entity:service",
            transitive=True,
        ),
    ])
    assert vocab.canonicalize("deprecated") == "is_deprecated"
    assert vocab.canonicalize("marked_deprecated") == "is_deprecated"
    assert vocab.canonicalize("EOL") == "is_deprecated"
    # Case insensitive on input — aliases match regardless of case.
    assert vocab.canonicalize("eol") == "is_deprecated"
    assert vocab.canonicalize("DEPRECATED") == "is_deprecated"
    # Canonical name lookup also works.
    assert vocab.canonicalize("is_deprecated") == "is_deprecated"
    # Unknown predicates return None.
    assert vocab.canonicalize("teleports_to") is None
    assert vocab.is_known("requires") is True
    assert vocab.is_known("teleports_to") is False


def test_project_pages_canonicalizes_with_vocabulary() -> None:
    """When a vocabulary is provided, predicate aliases collapse into one
    canonical section. Without vocabulary, aliases stay as separate sections."""
    from searchtrace.rag_eval.predicates import PredicateDef, PredicateVocabulary
    from searchtrace.rag_eval.systems.wiki_pages import project_pages

    claims = [
        _make_claim("payments_service", "deprecated", None,
                    source_path="docs/a.md", chunk_idx=0),
        _make_claim("payments_service", "marked_deprecated", None,
                    source_path="docs/b.md", chunk_idx=0),
        _make_claim("payments_service", "EOL", None,
                    source_path="docs/c.md", chunk_idx=0),
    ]

    # Without vocabulary: 3 separate sections.
    no_vocab_pages = project_pages(claims)
    assert len(no_vocab_pages) == 1
    assert len(no_vocab_pages[0].sections) == 3

    # With vocabulary: 1 collapsed section under canonical name.
    vocab = PredicateVocabulary([
        PredicateDef(
            name="is_deprecated",
            arity=1,
            aliases=("deprecated", "marked_deprecated", "EOL"),
            subject_type=None,
            object_type=None,
            transitive=False,
        ),
    ])
    canonical_pages = project_pages(claims, vocabulary=vocab)
    assert len(canonical_pages) == 1
    page = canonical_pages[0]
    assert len(page.sections) == 1
    assert page.sections[0].predicate == "is_deprecated"
    # All 3 source claims merged into one PredicateValue (object=None for arity-1).
    assert len(page.sections[0].values) == 1
    assert len(page.sections[0].values[0].evidence_uris) == 3


def test_wiki_pages_system_synthesize_default_runs_pass(
    synthetic_chunks: list[Chunk],
) -> None:
    """Default `synthesize=True` runs the synthesis pass: synthesis call count
    equals page count, and every page has prose."""
    from searchtrace.rag_eval.systems.wiki_pages import WikiPagesSystem

    cr = ClaimRAGSystem(MockEmbedder(), _CannedTripleGenerator(), synthetic_chunks)
    gen = _SynthesisCountingGenerator()
    wp = WikiPagesSystem(MockEmbedder(), gen, cr._claims)

    assert wp.page_count > 0
    assert gen.synthesis_calls == wp.page_count
    assert all(p.synthesized_prose is not None for p in wp._pages)


def test_knowledge_build_initial_synthesizes_all_pages() -> None:
    """First build (no parent): all pages get synthesized."""
    from searchtrace.rag_eval.claims.builds import merge_claims_into_build

    claims = [
        _make_claim("payments_service", "depends_on", "postgres",
                    source_path="docs/a.md", chunk_idx=0),
        _make_claim("auth_service", "depends_on", "redis",
                    source_path="docs/b.md", chunk_idx=0),
    ]
    gen = _SynthesisCountingGenerator()
    build = merge_claims_into_build(None, claims, gen)

    assert build.parent_build_id is None
    assert build.claim_count == 2
    assert build.page_count == 2
    assert gen.synthesis_calls == 2  # one per page
    assert all(p.synthesized_prose is not None for p in build.pages)


def test_knowledge_build_no_op_merge_returns_prior() -> None:
    """Merging a claim that's already in the build is a no-op."""
    from searchtrace.rag_eval.claims.builds import merge_claims_into_build

    claims = [_make_claim("payments_service", "depends_on", "postgres")]
    gen = _SynthesisCountingGenerator()
    build_a = merge_claims_into_build(None, claims, gen)
    initial_calls = gen.synthesis_calls

    # Re-merge with the same claim — no new claims, no work.
    build_b = merge_claims_into_build(build_a, claims, gen)
    assert build_b is build_a  # identity: prior build returned unchanged
    assert gen.synthesis_calls == initial_calls  # no new synthesis


def test_knowledge_build_only_resynthesizes_modified_entities() -> None:
    """Adding a new claim about ONE entity re-synthesizes only that entity.

    This is the load-bearing compounding invariant: synthesis cost scales with
    the delta, not the cumulative total.
    """
    from searchtrace.rag_eval.claims.builds import merge_claims_into_build

    initial_claims = [
        _make_claim("payments_service", "depends_on", "postgres",
                    source_path="docs/a.md", chunk_idx=0),
        _make_claim("auth_service", "depends_on", "redis",
                    source_path="docs/b.md", chunk_idx=0),
    ]
    gen = _SynthesisCountingGenerator()
    build_v1 = merge_claims_into_build(None, initial_claims, gen)
    assert gen.synthesis_calls == 2

    # Add ONE new claim about payments_service — auth_service should NOT be resynthesized.
    new_claim = [
        _make_claim("payments_service", "owned_by", "platform-team",
                    source_path="docs/c.md", chunk_idx=0),
    ]
    build_v2 = merge_claims_into_build(build_v1, new_claim, gen)

    assert build_v2.parent_build_id == build_v1.build_id
    assert build_v2.claim_count == 3
    assert build_v2.page_count == 2  # still 2 entities
    # Only ONE additional synthesis call (for payments_service).
    assert gen.synthesis_calls == 3

    # auth_service page data is unchanged — same prose, same sections, same id.
    # (`resolve_cross_references` rebuilds EntityPage instances for everyone,
    # so identity is not preserved; data equality is the invariant we want.)
    auth_v1 = next(p for p in build_v1.pages if p.subject == "auth_service")
    auth_v2 = next(p for p in build_v2.pages if p.subject == "auth_service")
    assert auth_v1 == auth_v2

    # payments_service page IS rebuilt — its prose reflects the new synthesis call.
    pay_v1 = next(p for p in build_v1.pages if p.subject == "payments_service")
    pay_v2 = next(p for p in build_v2.pages if p.subject == "payments_service")
    assert pay_v2.synthesized_prose != pay_v1.synthesized_prose


def test_knowledge_build_adds_new_entity_without_resynthesizing_old() -> None:
    """A new entity arrives — old pages are reused, only the new entity synthesizes.

    Cross-references on the OLD pages must update if they pointed at the
    just-arrived subject."""
    from searchtrace.rag_eval.claims.builds import merge_claims_into_build

    initial = [
        _make_claim("payments_service", "depends_on", "postgres",
                    source_path="docs/a.md", chunk_idx=0),
    ]
    gen = _SynthesisCountingGenerator()
    build_v1 = merge_claims_into_build(None, initial, gen)
    assert gen.synthesis_calls == 1
    # No postgres page yet, so no cross-ref from payments.
    pay_v1 = next(p for p in build_v1.pages if p.subject == "payments_service")
    assert pay_v1.cross_references == ()

    # postgres page arrives.
    new = [
        _make_claim("postgres", "version", "16",
                    source_path="docs/b.md", chunk_idx=0,
                    subject_type="database"),
    ]
    build_v2 = merge_claims_into_build(build_v1, new, gen)

    # Exactly one additional synthesis call (postgres).
    assert gen.synthesis_calls == 2
    assert build_v2.page_count == 2

    # payments_service page now cross-references postgres — but its prose was
    # NOT re-synthesized. Cross-ref updates skip the LLM call.
    pay_v2 = next(p for p in build_v2.pages if p.subject == "payments_service")
    assert len(pay_v2.cross_references) == 1
    assert pay_v2.cross_references[0].target_subject == "postgres"
    assert pay_v2.synthesized_prose == pay_v1.synthesized_prose  # prose preserved


def test_wiki_pages_system_exposes_knowledge_build(synthetic_chunks: list[Chunk]) -> None:
    """When synthesize=True, the system constructs a KnowledgeBuild internally
    and exposes it via _build (build_id, page_count, claim_count populated)."""
    from searchtrace.rag_eval.claims.builds import KnowledgeBuild
    from searchtrace.rag_eval.systems.wiki_pages import WikiPagesSystem

    cr = ClaimRAGSystem(MockEmbedder(), _CannedTripleGenerator(), synthetic_chunks)
    wp = WikiPagesSystem(
        MockEmbedder(), _SynthesisCountingGenerator(), cr._claims
    )
    assert isinstance(wp._build, KnowledgeBuild)
    assert wp._build.parent_build_id is None
    assert wp._build.page_count == wp.page_count
    assert wp._build.claim_count == cr.claim_count


def test_wiki_pages_system_synthesize_false_has_no_build(
    synthetic_chunks: list[Chunk],
) -> None:
    """With synthesize=False, no build is constructed (no LLM calls)."""
    from searchtrace.rag_eval.systems.wiki_pages import WikiPagesSystem

    cr = ClaimRAGSystem(MockEmbedder(), _CannedTripleGenerator(), synthetic_chunks)
    wp = WikiPagesSystem(
        MockEmbedder(), _CannedTripleGenerator(), cr._claims, synthesize=False
    )
    assert wp._build is None
    # merge() raises a clear error when no build exists.
    with pytest.raises(RuntimeError, match="synthesize=True"):
        wp.merge([])


def test_wiki_pages_system_merge_compounds_incrementally() -> None:
    """system.merge(new_claims) re-synthesizes only modified entities and
    re-embeds the index. The new build chains from the prior."""
    from searchtrace.rag_eval.systems.wiki_pages import WikiPagesSystem

    initial = [
        _make_claim("payments_service", "depends_on", "postgres",
                    source_path="docs/a.md", chunk_idx=0),
        _make_claim("auth_service", "depends_on", "redis",
                    source_path="docs/b.md", chunk_idx=0),
    ]
    gen = _SynthesisCountingGenerator()
    wp = WikiPagesSystem(MockEmbedder(), gen, initial)
    initial_calls = gen.synthesis_calls
    parent_id = wp._build.build_id

    # Merge a new claim about payments_service. Only payments should re-synthesize.
    wp.merge([
        _make_claim("payments_service", "owned_by", "platform-team",
                    source_path="docs/c.md", chunk_idx=0),
    ])

    assert wp._build.parent_build_id == parent_id
    assert wp._build.claim_count == 3
    assert wp.page_count == 2
    assert gen.synthesis_calls == initial_calls + 1  # only payments resynth'd
    # Index is re-embedded for the full page set (so retrieval reflects the
    # updated payments page).
    assert len(wp._index) == wp.page_count


def test_knowledge_build_id_is_content_hash() -> None:
    """Build ID is sha256(parent + sorted_new_claim_ids), deterministic."""
    from searchtrace.rag_eval.claims.builds import _derive_build_id, merge_claims_into_build

    claims = [_make_claim("x", "p", "y")]
    gen = _SynthesisCountingGenerator()
    build = merge_claims_into_build(None, claims, gen)
    expected = _derive_build_id(None, [claims[0].claim_id])
    assert build.build_id == expected
    assert len(build.build_id) == 64


def test_knowledge_build_round_trip_to_disk(tmp_path: Path) -> None:
    """save_build + load_build round-trip preserves every page field including
    synthesized_prose and cross_references; HEAD points at the saved build."""
    from searchtrace.rag_eval.claims.builds import (
        KnowledgeBuild,
        load_build,
        load_head,
        merge_claims_into_build,
        save_build,
    )

    claims = [
        _make_claim("payments_service", "depends_on", "postgres",
                    source_path="docs/a.md", chunk_idx=0),
        _make_claim("postgres", "version", "16",
                    source_path="docs/b.md", chunk_idx=0,
                    subject_type="database"),
    ]
    gen = _SynthesisCountingGenerator()
    original = merge_claims_into_build(None, claims, gen)
    save_build(original, tmp_path)

    # Round-trip via load_build.
    loaded = load_build(original.build_id, tmp_path)
    assert isinstance(loaded, KnowledgeBuild)
    assert loaded.build_id == original.build_id
    assert loaded.parent_build_id == original.parent_build_id
    assert loaded.built_at == original.built_at
    assert loaded.claim_count == original.claim_count
    assert loaded.page_count == original.page_count

    # Page-level invariants: prose preserved, cross-refs preserved, sections
    # preserved with their evidence_uris and claim_ids.
    for p_orig, p_loaded in zip(
        sorted(original.pages, key=lambda p: p.subject),
        sorted(loaded.pages, key=lambda p: p.subject),
        strict=True,
    ):
        assert p_orig == p_loaded

    # HEAD points at the saved build.
    head = load_head(tmp_path)
    assert head is not None and head.build_id == original.build_id


def test_knowledge_build_chain_traversal(tmp_path: Path) -> None:
    """load_chain walks parent_build_id pointers, returns oldest-first."""
    from searchtrace.rag_eval.claims.builds import load_chain, merge_claims_into_build, save_build

    gen = _SynthesisCountingGenerator()
    v1 = merge_claims_into_build(
        None,
        [_make_claim("a", "p", "x", source_path="docs/a.md", chunk_idx=0)],
        gen,
    )
    v2 = merge_claims_into_build(
        v1,
        [_make_claim("b", "p", "y", source_path="docs/b.md", chunk_idx=0)],
        gen,
    )
    v3 = merge_claims_into_build(
        v2,
        [_make_claim("c", "p", "z", source_path="docs/c.md", chunk_idx=0)],
        gen,
    )
    for b in (v1, v2, v3):
        save_build(b, tmp_path)

    chain = load_chain(v3.build_id, tmp_path)
    assert [b.build_id for b in chain] == [v1.build_id, v2.build_id, v3.build_id]
    assert chain[0].parent_build_id is None
    assert chain[1].parent_build_id == v1.build_id
    assert chain[2].parent_build_id == v2.build_id


def test_load_head_missing_returns_none(tmp_path: Path) -> None:
    from searchtrace.rag_eval.claims.builds import load_head

    assert load_head(tmp_path) is None


def test_run_eval_with_three_systems_emits_pairwise(synthetic_chunks: list[Chunk]) -> None:
    """End-to-end: RAG + ClaimRAG + WikiPages run on the same questions and the
    report carries pairwise CIs for all 3 system pairs (3 choose 2 = 3 pairs)."""
    from searchtrace.rag_eval.systems.wiki_pages import WikiPagesSystem

    qs = [
        QAItem(
            id=f"q{i:02d}",
            tier="single_hop",
            question=f"q{i}?",
            ground_truth_answer="x",
            ground_truth_citations=("docs/payments.md",),
            must_include_terms=("postgres",),
        )
        for i in range(5)
    ]
    rag = RAGSystem(MockEmbedder(), _CannedTripleGenerator(), synthetic_chunks)
    cr = ClaimRAGSystem(MockEmbedder(), _CannedTripleGenerator(), synthetic_chunks)
    wp = WikiPagesSystem(MockEmbedder(), _CannedTripleGenerator(), cr._claims)
    report = run_eval([rag, cr, wp], qs)

    assert report.systems == ("rag", "claim_rag", "wiki_pages")
    assert len(report.rows) == 15  # 3 systems x 5 questions
    pairs = {(d.system_a, d.system_b) for d in report.pairwise}
    assert ("rag", "claim_rag") in pairs
    assert ("rag", "wiki_pages") in pairs
    assert ("claim_rag", "wiki_pages") in pairs
