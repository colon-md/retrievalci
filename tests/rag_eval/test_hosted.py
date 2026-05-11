"""Fake hosted-RAG adapter tests for manifest mapping and cost caps.

Validates the enterprise-adoption guarantees of the hosted adapter scaffolding
without making real API calls. Every real adapter (Vertex, Bedrock, etc.)
plugs into the same mechanism, so passing this suite is a necessary precondition
for adding the first real hosted Mode A row to the public scorecard.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from retrievalci.rag_eval.corpus import Chunk, compute_corpus_version_hash
from retrievalci.rag_eval.hosted import (
    DEFAULT_COST_CAP_USD,
    DEFAULT_QUERY_CAP,
    BudgetExceededError,
    IndexHandle,
    ManifestMissingError,
    RunBudget,
    manifest_path,
    read_manifest,
    write_manifest,
)
from retrievalci.rag_eval.types import Citation, SystemAnswer


class FakeHostedSystem:
    """A HostedSystem stand-in that simulates a provider's chunk-ID scheme.

    Pretends to ingest a corpus, returns provider-internal chunk IDs of the
    form `fake://chunk-<n>`, and uses a chunk manifest to map those back to
    repo-relative source paths. Real adapters do exactly this shape against
    Vertex / Bedrock / etc.
    """

    name = "fake_hosted"

    def __init__(self, repo_root: Path, chunks: list[Chunk], cost_per_question: float) -> None:
        self.repo_root = repo_root
        self.chunks = chunks
        self.cost_per_question = cost_per_question
        self._index: IndexHandle | None = None

    def index(self, corpus_dir: Path, corpus_version_hash: str) -> IndexHandle:
        # Pretend each chunk got a provider-internal id; write the manifest.
        mapping = {
            f"fake://chunk-{i}": c.source_path
            for i, c in enumerate(self.chunks)
        }
        write_manifest(self.repo_root, self.name, corpus_version_hash, mapping)
        self._index = IndexHandle(
            provider_index_id="fake://corpus-1",
            corpus_version_hash=corpus_version_hash,
        )
        return self._index

    def chunk_manifest(self) -> dict[str, str]:
        if self._index is None:
            return {}
        return read_manifest(self.repo_root, self.name, self._index.corpus_version_hash)

    def estimate_cost(self, n_questions: int) -> float:
        return self.cost_per_question * n_questions

    def answer(self, question: str) -> SystemAnswer:
        # Pretend the hosted service returned provider-internal IDs; map them
        # to repo paths via the manifest before populating retrieved_sources.
        manifest = self.chunk_manifest()
        provider_id = "fake://chunk-0"
        repo_path = manifest[provider_id]
        return SystemAnswer(
            answer="fake answer",
            citations=(),
            retrieved_sources=(Citation(source_path=repo_path),),
            latency_ms=1.0,
            tokens_used=1,
            cost_usd=self.cost_per_question,
            corpus_version_hash=self._index.corpus_version_hash if self._index else None,
        )


@pytest.fixture
def synthetic_chunks() -> list[Chunk]:
    return [
        Chunk(source_path="docs/a.md", chunk_index=0, text="alpha"),
        Chunk(source_path="docs/a.md", chunk_index=1, text="beta"),
        Chunk(source_path="docs/b.md", chunk_index=0, text="gamma"),
    ]


def test_corpus_version_hash_is_deterministic(synthetic_chunks: list[Chunk]) -> None:
    h1 = compute_corpus_version_hash(synthetic_chunks)
    h2 = compute_corpus_version_hash(list(reversed(synthetic_chunks)))
    assert h1 == h2  # order-independent
    assert len(h1) == 64  # full SHA-256 hex digest


def test_short_corpus_version_hash_truncates_to_16() -> None:
    from retrievalci.rag_eval.corpus import short_corpus_version_hash

    full = "a" * 64
    assert short_corpus_version_hash(full) == "a" * 16


def test_short_corpus_version_hash_rejects_too_short_input() -> None:
    from retrievalci.rag_eval.corpus import short_corpus_version_hash

    with pytest.raises(ValueError):
        short_corpus_version_hash("abc")


def test_corpus_version_hash_changes_when_content_changes(
    synthetic_chunks: list[Chunk],
) -> None:
    h1 = compute_corpus_version_hash(synthetic_chunks)
    mutated = [*synthetic_chunks[:-1], Chunk(source_path="docs/b.md", chunk_index=0, text="DELTA")]
    h2 = compute_corpus_version_hash(mutated)
    assert h1 != h2


def test_manifest_round_trip(tmp_path: Path, synthetic_chunks: list[Chunk]) -> None:
    corpus_hash = compute_corpus_version_hash(synthetic_chunks)
    sys = FakeHostedSystem(tmp_path, synthetic_chunks, cost_per_question=0.01)
    handle = sys.index(corpus_dir=tmp_path, corpus_version_hash=corpus_hash)

    assert handle.corpus_version_hash == corpus_hash
    expected_path = manifest_path(tmp_path, "fake_hosted", corpus_hash)
    assert expected_path.exists()

    mapping = sys.chunk_manifest()
    assert mapping == {
        "fake://chunk-0": "docs/a.md",
        "fake://chunk-1": "docs/a.md",
        "fake://chunk-2": "docs/b.md",
    }


def test_manifest_missing_raises_fail_closed(tmp_path: Path) -> None:
    """If the manifest file isn't on disk, scoring must refuse to start."""
    with pytest.raises(ManifestMissingError) as exc:
        read_manifest(tmp_path, "fake_hosted", "deadbeef" * 8)  # 64 hex chars
    assert "not found" in str(exc.value)


def test_manifest_hash_mismatch_raises_fail_closed(
    tmp_path: Path, synthetic_chunks: list[Chunk]
) -> None:
    """A manifest for a different corpus hash must not be silently accepted.

    Both the live and stale hashes are full SHA-256 digests; the manifest
    filename only encodes the short form, but the comparison happens on the
    full digest stored inside the file.
    """
    stale_hash = "stale" + "0" * 59  # 64 hex chars
    current_hash = "abcd" + "1" * 60  # 64 hex chars, different from stale
    path = manifest_path(tmp_path, "fake_hosted", stale_hash)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'{{"adapter": "fake_hosted", "corpus_version_hash": "{stale_hash}", '
        '"mapping": {"fake://x": "docs/a.md"}}',
        encoding="utf-8",
    )
    # Filename collision possible because manifest_path() truncates to 16 chars.
    # Read it back asking for `current_hash` — must fail-closed because the
    # internal hash doesn't match, even if the filename happens to collide.
    short_path = manifest_path(tmp_path, "fake_hosted", current_hash)
    short_path.parent.mkdir(parents=True, exist_ok=True)
    short_path.write_text(
        f'{{"adapter": "fake_hosted", "corpus_version_hash": "{stale_hash}", '
        '"mapping": {"fake://x": "docs/a.md"}}',
        encoding="utf-8",
    )
    with pytest.raises(ManifestMissingError) as exc:
        read_manifest(tmp_path, "fake_hosted", current_hash)
    assert "stale" in str(exc.value)


def test_manifest_malformed_raises(tmp_path: Path) -> None:
    corpus_hash = "abcd" * 16  # 64 hex chars
    path = manifest_path(tmp_path, "fake_hosted", corpus_hash)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'{{"adapter": "fake_hosted", "corpus_version_hash": "{corpus_hash}"}}',
        encoding="utf-8",
    )
    with pytest.raises(ManifestMissingError):
        read_manifest(tmp_path, "fake_hosted", corpus_hash)


def test_hosted_adapter_round_trip_metric_credits_correct_source(
    tmp_path: Path, synthetic_chunks: list[Chunk]
) -> None:
    """End-to-end: provider-internal ID resolves to a repo path, and the
    retrieval metric credits the ground-truth source through the manifest."""
    from retrievalci.rag_eval.metrics import compute_row
    from retrievalci.rag_eval.types import QAItem

    corpus_hash = compute_corpus_version_hash(synthetic_chunks)
    sys = FakeHostedSystem(tmp_path, synthetic_chunks, cost_per_question=0.01)
    sys.index(corpus_dir=tmp_path, corpus_version_hash=corpus_hash)
    q = QAItem(
        id="q1",
        tier="single_hop",
        question="?",
        ground_truth_answer="x",
        ground_truth_citations=("docs/a.md",),
    )
    ans = sys.answer(q.question)
    row = compute_row(sys.name, q, ans)
    assert row.retrieval_source_recall == 1.0


def test_run_budget_defaults_are_tight() -> None:
    """Construction with no args must apply the documented tight defaults."""
    b = RunBudget()
    assert b.cap_usd == DEFAULT_COST_CAP_USD == 20.0
    assert b.query_cap == DEFAULT_QUERY_CAP == 50


def test_run_budget_preflight_under_caps_allows() -> None:
    b = RunBudget(cap_usd=5.0, query_cap=10)
    b.preflight(estimate_usd=2.0, n_questions=5)  # no raise


def test_run_budget_preflight_over_cost_cap_refuses() -> None:
    b = RunBudget(cap_usd=5.0, query_cap=10)
    with pytest.raises(BudgetExceededError) as exc:
        b.preflight(estimate_usd=10.0, n_questions=5)
    assert "pre-flight" in str(exc.value)
    assert "cap_usd" in str(exc.value)


def test_run_budget_preflight_over_query_cap_refuses() -> None:
    b = RunBudget(cap_usd=100.0, query_cap=10)
    with pytest.raises(BudgetExceededError) as exc:
        b.preflight(estimate_usd=1.0, n_questions=50)
    assert "pre-flight" in str(exc.value)
    assert "query_cap" in str(exc.value)


def test_run_budget_default_refuses_bench_v1_size() -> None:
    """Default $20 / 50q caps must refuse a bench-v1 (150q) run without override.

    Pinning this is the whole point of "tight defaults" — bigger runs must
    force the operator to explicitly opt in, not happen by accident.
    """
    b = RunBudget()
    with pytest.raises(BudgetExceededError):
        b.preflight(estimate_usd=1.0, n_questions=150)


def test_run_budget_preflight_explicit_override() -> None:
    """allow_overrun lets enterprises bypass intentionally — but only when set."""
    b = RunBudget(cap_usd=5.0, query_cap=10, allow_overrun=True)
    b.preflight(estimate_usd=10.0, n_questions=500)  # no raise


def test_run_budget_in_flight_aborts_on_cost_cap() -> None:
    """Once cumulative cost exceeds cap_usd, record() must raise."""
    b = RunBudget(cap_usd=1.0, query_cap=1000)
    b.record(usd=0.50)
    with pytest.raises(BudgetExceededError):
        b.record(usd=0.60)


def test_run_budget_in_flight_aborts_on_query_cap() -> None:
    """record_query() must abort when actual queries cross query_cap."""
    b = RunBudget(cap_usd=1000.0, query_cap=3)
    b.record_query()
    b.record_query()
    b.record_query()
    with pytest.raises(BudgetExceededError) as exc:
        b.record_query()  # 4th call -> over cap
    assert "in-flight" in str(exc.value)
    assert "query_cap" in str(exc.value)


def test_run_budget_allow_overrun_disables_in_flight_checks() -> None:
    b = RunBudget(cap_usd=1.0, query_cap=1, allow_overrun=True)
    b.record(usd=10.0)  # no raise
    for _ in range(10):
        b.record_query()  # no raise


def test_run_budget_rejects_negative_cost_record() -> None:
    b = RunBudget()
    with pytest.raises(ValueError):
        b.record(usd=-0.01)
