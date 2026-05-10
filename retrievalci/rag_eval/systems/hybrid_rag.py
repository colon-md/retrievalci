"""Hybrid sparse + dense retrieval via reciprocal rank fusion.

Run BM25 and dense retrieval independently, fuse the rankings via reciprocal
rank fusion (RRF):

    score(c) = sum_methods 1 / (k + rank_method(c))

Default k=60 (Cormack et al. 2009). Take top-k by fused score.

Stronger baseline than either BM25 or dense alone — combines lexical-match
and semantic-match retrieval signals. Common in production RAG systems.
"""

from __future__ import annotations

import time
from collections.abc import Iterable

from retrievalci.rag_eval.backends.base import Embedder, GenerationRequest, Generator
from retrievalci.rag_eval.corpus import Chunk
from retrievalci.rag_eval.systems.bm25 import BM25System
from retrievalci.rag_eval.types import Citation, SystemAnswer


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


_PROMPT_TEMPLATE = """\
You are answering questions about engineering documentation. Use ONLY the
retrieved context. Cite sources by [doc:path] inline. If the context doesn't
contain the answer, say so.

Question: {question}

Context:
{context}

Answer:"""


class HybridRAGSystem:
    """BM25 + dense retrieval with reciprocal-rank-fusion top-k."""

    def __init__(
        self,
        embedder: Embedder,
        generator: Generator,
        chunks: Iterable[Chunk],
        top_k: int = 5,
        retrieval_pool: int = 20,
        rrf_k: int = 60,
    ) -> None:
        self._embedder = embedder
        self._generator = generator
        self._chunks = list(chunks)
        self._top_k = top_k
        self._retrieval_pool = retrieval_pool
        self._rrf_k = rrf_k

        # Build dense index.
        self._index = self._embedder.embed_batch([c.text for c in self._chunks])
        # Build BM25 component (without its own generator — we don't run BM25's answer).
        self._bm25 = BM25System(generator=generator, chunks=self._chunks, top_k=retrieval_pool)

    @property
    def name(self) -> str:
        return "hybrid_rag"

    def _dense_rank(self, question: str) -> list[tuple[float, int]]:
        q_vec = self._embedder.embed(question)
        scored = [(_cosine(q_vec, v), i) for i, v in enumerate(self._index)]
        scored.sort(reverse=True)
        return scored

    def _fuse(
        self,
        dense_ranked: list[tuple[float, int]],
        sparse_ranked: list[tuple[float, int]],
    ) -> list[int]:
        """Reciprocal rank fusion. Returns chunk indices ordered by fused score, descending."""
        scores: dict[int, float] = {}
        # Take top retrieval_pool from each, fuse by 1/(k + rank).
        for rank, (_, idx) in enumerate(dense_ranked[: self._retrieval_pool]):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (self._rrf_k + rank + 1)
        for rank, (_, idx) in enumerate(sparse_ranked[: self._retrieval_pool]):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (self._rrf_k + rank + 1)
        fused = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [idx for idx, _ in fused]

    def answer(self, question: str) -> SystemAnswer:
        t0 = time.perf_counter()
        dense_ranked = self._dense_rank(question)
        sparse_ranked = self._bm25.rank(question)
        fused_idxs = self._fuse(dense_ranked, sparse_ranked)
        retrieved = [self._chunks[i] for i in fused_idxs[: self._top_k]]

        context = "\n\n".join(f"[doc:{c.chunk_id}]\n{c.text}" for c in retrieved)
        prompt = _PROMPT_TEMPLATE.format(question=question, context=context)
        resp = self._generator.generate(GenerationRequest(prompt=prompt))

        latency_ms = (time.perf_counter() - t0) * 1000.0
        citations = tuple(Citation(source_path=c.source_path, span=c.text[:160]) for c in retrieved)
        return SystemAnswer(
            answer=resp.text,
            citations=citations,
            latency_ms=latency_ms,
            tokens_used=resp.tokens_used,
        )
