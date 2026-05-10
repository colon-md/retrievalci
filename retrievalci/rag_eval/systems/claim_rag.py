"""Claim-RAG system — RAG with a triple-extraction pre-pass.

Wired through the production substrate in `retrievalci.rag_eval.claims` so the eval exercises
the actual `Claim`, `ProofSet`, and `Evidence` types, including:
  - content-hashed `claim_id` (via `retrievalci.rag_eval.claims.hashing.derive_claim_id`)
  - content-hashed `proof_set_id` (via `derive_proof_set_id`)
  - frozen `knowledge_build_id` per system instance
  - frozen ACL labels and residency region
  - 18-field Claim model (frozen, extra="forbid") with full provenance

This is **not** Karpathy's wiki pattern — it's RAG with cached triple
decomposition. The 3-way review at /tmp/review-3way-synthesis.md flagged the
original WikiSystem name as misleading. What this still does NOT implement
(deferred to a future wiki layer):
  - Compounding entity pages aggregating across many sources
  - Cross-references between claims
  - Contradiction detection / lint
  - Closed predicate vocabulary enforcement (retrievalci/rag_eval/schemas/predicates.yml)
  - Multi-source proof sets
  - Knowledge-build promotion / rollback (retrievalci.rag_eval.claims.builds)

If you need any of those, this is the wrong system to test.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from retrievalci.rag_eval.backends.base import Embedder, GenerationRequest, Generator
from retrievalci.rag_eval.claims import (
    Claim,
    Evidence,
    ProofSet,
    derive_claim_id,
    derive_proof_set_id,
)
from retrievalci.rag_eval.corpus import Chunk
from retrievalci.rag_eval.types import Citation, SystemAnswer

# Eval-specific defaults. The production system reads these from a build
# manifest + IAM policy; the eval pins them to single values per run because
# the experiment does not exercise multi-tenant ACLs or multi-region routing.
_EVAL_DOMAIN = "eval"
_EVAL_RESIDENCY = "public"
_EVAL_ACL_LABELS = frozenset({"eval"})
_EVAL_PROMPT_ID = "eval-claim-rag-extract-v1"


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


_EXTRACT_PROMPT = """\
Extract factual triples (subject, predicate, object) from the following text.
Each triple should capture one atomic fact. Return one triple per line in the
format `subject | predicate | object`. Maximum 5 triples. Be concise.

Text:
{text}

Triples:"""

_QUERY_PROMPT = """\
Answer the question using ONLY the retrieved claims. Each claim has the form
"subject predicate object" with a citation to the source it was derived from.
Cite sources by [doc:path] inline. If the claims don't contain the answer,
say so.

Question: {question}

Retrieved claims:
{context}

Answer:"""


def _claim_text(claim: Claim) -> str:
    """Embedding text: 'subject predicate object' (object empty for arity-1)."""
    obj = claim.object or ""
    return f"{claim.subject} {claim.predicate} {obj}".strip()


def _evidence_uri_for_chunk(chunk: Chunk) -> str:
    return f"chunk://{chunk.chunk_id}"


class ClaimRAGSystem:
    def __init__(
        self,
        embedder: Embedder,
        generator: Generator,
        chunks: Iterable[Chunk],
        top_k: int = 8,
        extraction_cache_dir: Path | None = None,
        progress_every: int = 0,
    ) -> None:
        self._embedder = embedder
        self._generator = generator
        self._top_k = top_k
        self._extraction_cache_dir = extraction_cache_dir
        self._progress_every = progress_every
        if self._extraction_cache_dir is not None:
            self._extraction_cache_dir.mkdir(parents=True, exist_ok=True)

        # Frozen build context for this system instance. In production these
        # come from the active knowledge build's manifest. For the eval, we
        # derive deterministically from the generator + extraction prompt so
        # two runs with the same backend produce the same build_id.
        self._prompt_template_hash = _sha256_hex(_EXTRACT_PROMPT)
        self._sampling_params_hash = _sha256_hex("temperature=0.0|max_output_tokens=1024")
        self._model_id = generator.model_id
        self._model_snapshot = generator.model_id  # eval treats model_id == snapshot
        self._knowledge_build_id = _sha256_hex(
            f"{self._model_id}|{self._prompt_template_hash}|{_EVAL_PROMPT_ID}"
        )
        self._validator_model_id = generator.model_id
        self._asserted_at = datetime.now(UTC)

        self._claims: list[Claim] = self._extract(list(chunks))
        self._index = (
            self._embedder.embed_batch([_claim_text(c) for c in self._claims])
            if self._claims
            else []
        )

    @property
    def name(self) -> str:
        return "claim_rag"

    @property
    def claim_count(self) -> int:
        return len(self._claims)

    def _build_claim(self, subject: str, predicate: str, object_: str, chunk: Chunk) -> Claim:
        evidence = Evidence(
            source_id=chunk.source_path,
            evidence_type="raw_doc",
            evidence_uri=_evidence_uri_for_chunk(chunk),
            source_version=None,
        )
        proof_set = ProofSet(
            proof_set_id=derive_proof_set_id([evidence.source_id]),
            sources=(evidence,),
            acl_labels=_EVAL_ACL_LABELS,
            validated_at=self._asserted_at,
            validator_model_id=self._validator_model_id,
        )
        claim_id = derive_claim_id(
            subject=subject,
            predicate=predicate,
            object_=object_,
            prompt_id=_EVAL_PROMPT_ID,
            evidence_uris=[evidence.evidence_uri],
        )
        return Claim(
            claim_id=claim_id,
            knowledge_build_id=self._knowledge_build_id,
            domain=_EVAL_DOMAIN,
            subject=subject,
            subject_type="extracted",  # eval has no type inference
            predicate=predicate,
            object=object_ or None,
            object_type=None,
            proof_sets=(proof_set,),
            prompt_id=_EVAL_PROMPT_ID,
            prompt_template_hash=self._prompt_template_hash,
            model_id=self._model_id,
            model_snapshot=self._model_snapshot,
            sampling_params_hash=self._sampling_params_hash,
            asserted_at=self._asserted_at,
            data_residency_region=_EVAL_RESIDENCY,
        )

    def _extract(self, chunks: list[Chunk]) -> list[Claim]:
        out: list[Claim] = []
        seen_ids: set[str] = set()  # dedupe by claim_id (deterministic content hash)
        for idx, chunk in enumerate(chunks, start=1):
            prompt = _EXTRACT_PROMPT.format(text=chunk.text)
            cache_path = self._extraction_cache_path(chunk)
            if cache_path is not None and cache_path.is_file():
                raw_triples = cache_path.read_text(encoding="utf-8")
            else:
                resp = self._generator.generate(GenerationRequest(prompt=prompt))
                raw_triples = resp.text
                if cache_path is not None:
                    cache_path.write_text(raw_triples, encoding="utf-8")

            for subj, pred, obj in self._parse_triples(raw_triples):
                claim = self._build_claim(subj, pred, obj, chunk)
                if claim.claim_id in seen_ids:
                    continue
                seen_ids.add(claim.claim_id)
                out.append(claim)
            if self._progress_every and idx % self._progress_every == 0:
                print(f"claim_rag extracted {idx}/{len(chunks)} chunks", flush=True)
        return out

    def _extraction_cache_path(self, chunk: Chunk) -> Path | None:
        if self._extraction_cache_dir is None:
            return None
        payload = "\n".join(
            (
                "claim-rag-extract-v1",
                self._model_id,
                self._prompt_template_hash,
                chunk.chunk_id,
                chunk.text,
            )
        )
        key = _sha256_hex(payload)
        return self._extraction_cache_dir / f"{key}.txt"

    @staticmethod
    def _parse_triples(text: str) -> list[tuple[str, str, str]]:
        triples: list[tuple[str, str, str]] = []
        for raw in text.splitlines():
            # Strip leading dashes/bullets the mock generator emits.
            line = re.sub(r"^[-*•\s]+", "", raw).strip()
            if "|" not in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3 and all(parts[:3]):
                triples.append((parts[0], parts[1], parts[2]))
        return triples

    @staticmethod
    def _claim_source_path(claim: Claim) -> str:
        return claim.proof_sets[0].sources[0].source_id

    @staticmethod
    def _claim_chunk_id(claim: Claim) -> str:
        # evidence_uri is `chunk://path#chunk-N` — strip the prefix to get the chunk_id.
        uri = claim.proof_sets[0].sources[0].evidence_uri
        return uri.removeprefix("chunk://")

    def answer(self, question: str) -> SystemAnswer:
        t0 = time.perf_counter()
        if not self._claims:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return SystemAnswer(
                answer="(no claims extracted from corpus; system cannot answer)",
                citations=(),
                latency_ms=latency_ms,
                tokens_used=0,
                refused=True,
                refusal_reason="empty_claim_index",
            )

        q_vec = self._embedder.embed(question)

        def cosine(v: list[float]) -> float:
            return sum(x * y for x, y in zip(q_vec, v, strict=True))

        scored = sorted(((cosine(v), i) for i, v in enumerate(self._index)), reverse=True)
        retrieved = [self._claims[i] for _, i in scored[: self._top_k]]

        context = "\n".join(f"[doc:{self._claim_chunk_id(c)}] {_claim_text(c)}" for c in retrieved)
        prompt = _QUERY_PROMPT.format(question=question, context=context)
        resp = self._generator.generate(GenerationRequest(prompt=prompt))

        latency_ms = (time.perf_counter() - t0) * 1000.0
        # Dedupe citations by source_path while preserving retrieval order.
        seen: set[str] = set()
        cits: list[Citation] = []
        for c in retrieved:
            path = self._claim_source_path(c)
            if path in seen:
                continue
            seen.add(path)
            cits.append(Citation(source_path=path, span=_claim_text(c)[:240]))

        return SystemAnswer(
            answer=resp.text,
            citations=tuple(cits),
            latency_ms=latency_ms,
            tokens_used=resp.tokens_used,
        )
