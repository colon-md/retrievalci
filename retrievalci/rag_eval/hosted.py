"""Hosted-RAG adapter scaffolding: manifests + cost caps + protocol.

This module is the shared mechanism every hosted adapter (Vertex AI RAG Engine,
Bedrock Knowledge Bases, Azure AI Search, OpenAI File Search) plugs into. It
exists so the hosted-RAG benchmark plan's enterprise guarantees are enforced
once, not re-implemented per provider:

  * Manifests are *written at index time, read at eval time, fail-closed* —
    a per-adapter manifest at examples/rag_eval/manifests/<adapter>/<hash>.json
    maps provider-internal chunk IDs back to repo-relative source paths. The
    directory is gitignored by default because manifest entries contain
    account-tied resource names (e.g. projects/<NUM>/.../ragFiles/<id> or
    OpenAI file-IDs scoped to the operator's API key). If the manifest is
    missing or its corpus hash doesn't match the chunks the harness is
    currently scoring against, evaluation refuses to start. The corpus hash
    makes runs deterministic: re-running on the same corpus produces an
    aligned mapping the harness can validate.

  * Run budgets are *hard pre-flight + in-flight tally + explicit override*,
    covering BOTH dollar cost AND total query count. estimate_cost() and
    n_questions are checked before any API call; if either exceeds its cap,
    evaluation refuses. A running tally is checked after each answer; if
    actual spend or query count crosses the cap mid-run, the loop aborts.
    Operators bypass intentionally with allow_overrun=True. Defaults are
    tight ($20 / 50 queries — sized to bench-v0); larger runs require
    explicit override.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from retrievalci.rag_eval.corpus import short_corpus_version_hash
from retrievalci.rag_eval.types import SystemAnswer


@dataclass(frozen=True)
class IndexHandle:
    """Reference to a hosted index that the adapter created at index time.

    The provider_index_id is the adapter-specific opaque ID (e.g. a Vertex
    RAG corpus resource name, a Bedrock knowledge base id, an OpenAI vector
    store id). The harness treats it as opaque; the adapter knows how to use
    it to issue retrieval queries later.
    """

    provider_index_id: str
    corpus_version_hash: str


class HostedSystem(Protocol):
    """A System backed by an external hosted RAG service.

    Adds three methods to the base System protocol so the harness can enforce
    reproducibility and cost guarantees uniformly across providers.
    """

    @property
    def name(self) -> str: ...

    def answer(self, question: str) -> SystemAnswer: ...

    def index(self, corpus_dir: Path, corpus_version_hash: str) -> IndexHandle:
        """Ingest the corpus into the hosted service and return an index handle.

        Implementations must ALSO write the chunk manifest (provider id →
        repo-relative source path) to disk via write_manifest(). The harness
        re-reads it at eval time to score Mode A retrieval correctly.
        """
        ...

    def chunk_manifest(self) -> dict[str, str]:
        """Mapping from provider-internal chunk/source IDs to repo paths.

        Adapter reads from its previously-written manifest file. Returning an
        empty dict means the adapter has not been indexed against the current
        corpus hash and Mode A scoring should fail-closed.
        """
        ...

    def estimate_cost(self, n_questions: int) -> float:
        """Pre-flight USD estimate for answering n_questions on this index.

        Conservative (over-estimate) is preferred — the cost cap is meant to
        catch obvious mistakes, not to be a precise forecast.
        """
        ...


def manifest_path(repo_root: Path, adapter_name: str, corpus_version_hash: str) -> Path:
    """Canonical on-disk location for a hosted adapter's chunk manifest.

    Filenames use the 16-char short hash (display form). The full SHA-256 is
    stored inside the manifest payload, so the persisted-comparison surface
    keeps full collision resistance even though the filename is truncated.
    """
    short = short_corpus_version_hash(corpus_version_hash)
    return (
        repo_root
        / "examples"
        / "rag_eval"
        / "manifests"
        / adapter_name
        / f"{short}.json"
    )


def write_manifest(
    repo_root: Path,
    adapter_name: str,
    corpus_version_hash: str,
    mapping: dict[str, str],
) -> Path:
    """Persist a chunk manifest. Called by adapters at index time.

    The on-disk format is a JSON document with the corpus hash embedded so a
    file copied to the wrong directory can't pretend to be for another corpus.
    """
    path = manifest_path(repo_root, adapter_name, corpus_version_hash)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "adapter": adapter_name,
        "corpus_version_hash": corpus_version_hash,
        "mapping": mapping,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


class ManifestMissingError(RuntimeError):
    """Raised when a hosted adapter's manifest is missing or stale.

    Fail-closed: rather than silently scoring against an unmapped index, the
    harness refuses to run so the operator notices and re-indexes.
    """


def read_manifest(
    repo_root: Path,
    adapter_name: str,
    corpus_version_hash: str,
) -> dict[str, str]:
    """Load a chunk manifest. Raises if missing or if the hash doesn't match."""
    path = manifest_path(repo_root, adapter_name, corpus_version_hash)
    if not path.exists():
        raise ManifestMissingError(
            f"manifest for adapter={adapter_name!r} corpus_version_hash="
            f"{corpus_version_hash!r} not found at {path}; re-index the hosted "
            "service against the current corpus to regenerate it"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    stored_hash = payload.get("corpus_version_hash")
    if stored_hash != corpus_version_hash:
        raise ManifestMissingError(
            f"manifest at {path} has corpus_version_hash={stored_hash!r} but "
            f"the harness is evaluating against {corpus_version_hash!r}; the "
            "hosted index is stale and must be rebuilt"
        )
    mapping = payload.get("mapping")
    if not isinstance(mapping, dict):
        raise ManifestMissingError(f"manifest at {path} is malformed: missing 'mapping' object")
    return {str(k): str(v) for k, v in mapping.items()}


def resolve_source_path(provider_source: str, manifest: dict[str, str]) -> str:
    """Map a provider-returned source identifier back to a repo-relative path.

    Tries direct manifest lookup first; falls back to basename match
    (Vertex, Bedrock, and OpenAI all return just display_name / filename
    in at least some response shapes); finally returns the raw input so
    citations can still show *something* readable rather than blanking.
    """
    if provider_source in manifest:
        return manifest[provider_source]
    basename = Path(provider_source).name
    for value in manifest.values():
        if Path(value).name == basename:
            return value
    return provider_source


class BudgetExceededError(RuntimeError):
    """Raised when a hosted run would exceed (or has exceeded) a budget cap.

    Covers both the dollar cap and the query-count cap. Callers can catch
    this single type to gracefully handle either kind of budget breach.
    """


DEFAULT_COST_CAP_USD = 20.0
DEFAULT_QUERY_CAP = 50


@dataclass
class RunBudget:
    """Hard pre-flight + in-flight caps on dollar spend and query count.

    Defaults are tight ($20, 50 queries — sized to bench-v0). Larger runs
    require explicit override at construction time. allow_overrun=True
    bypasses both caps for intentional runs.
    """

    cap_usd: float = DEFAULT_COST_CAP_USD
    query_cap: int = DEFAULT_QUERY_CAP
    allow_overrun: bool = False
    actual_usd: float = 0.0
    actual_queries: int = 0

    def preflight(self, estimate_usd: float, n_questions: int) -> None:
        """Check before any API call. Raises if over either cap and not overridden."""
        if self.allow_overrun:
            return
        if estimate_usd > self.cap_usd:
            raise BudgetExceededError(
                f"pre-flight: estimated cost ${estimate_usd:.4f} exceeds cap "
                f"${self.cap_usd:.4f}; rerun with allow_overrun=True or raise "
                "cap_usd if intentional"
            )
        if n_questions > self.query_cap:
            raise BudgetExceededError(
                f"pre-flight: {n_questions} questions exceeds query_cap "
                f"{self.query_cap}; rerun with allow_overrun=True or raise "
                "query_cap if intentional"
            )

    def record(self, usd: float) -> None:
        """Add an observed per-question cost. Aborts if cumulative > cap."""
        if usd < 0:
            raise ValueError(f"cost record must be non-negative, got {usd}")
        self.actual_usd += usd
        if self.allow_overrun:
            return
        if self.actual_usd > self.cap_usd:
            raise BudgetExceededError(
                f"in-flight: actual ${self.actual_usd:.4f} exceeded cap "
                f"${self.cap_usd:.4f}; aborting to limit further spend"
            )

    def record_query(self) -> None:
        """Increment the query tally after a question has been answered.

        Separate from record() so adapters can call it even when they don't
        have per-question cost data. Abort when the tally exceeds query_cap.
        """
        self.actual_queries += 1
        if self.allow_overrun:
            return
        if self.actual_queries > self.query_cap:
            raise BudgetExceededError(
                f"in-flight: actual {self.actual_queries} queries exceeded "
                f"query_cap {self.query_cap}; aborting to limit further spend"
            )
