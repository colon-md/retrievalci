"""RAG with cross-encoder reranking.

Two-stage retrieval: dense top-N (N=20) → cross-encoder rerank → final top-k=5.
The cross-encoder scores (query, chunk) pairs jointly, capturing semantic
match better than dual-encoder cosine alone.

Default cross-encoder: `cross-encoder/ms-marco-MiniLM-L-6-v2` from
sentence-transformers. ~22MB, runs on CPU. First call downloads weights.

Used as a "stronger RAG" baseline for Tier C. Pure-RAG comparison alone is
weak — production RAG always reranks. This puts wiki against a real RAG.
"""

from __future__ import annotations

import time
from collections.abc import Iterable

from retrievalci.rag_eval.backends.base import Embedder, GenerationRequest, Generator
from retrievalci.rag_eval.corpus import Chunk
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


class RerankRAGSystem:
    """Dense top-N → cross-encoder rerank → final top-k. Stronger RAG baseline."""

    def __init__(
        self,
        embedder: Embedder,
        generator: Generator,
        chunks: Iterable[Chunk],
        top_k: int = 5,
        rerank_pool: int = 20,
        cross_encoder_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
    ) -> None:
        self._embedder = embedder
        self._generator = generator
        self._chunks = list(chunks)
        self._top_k = top_k
        self._rerank_pool = rerank_pool
        self._cross_encoder_model = cross_encoder_model
        self._index = self._embedder.embed_batch([c.text for c in self._chunks])

        # Lazy-load cross-encoder so tests / mock backends don't require download.
        self._cross_encoder = None

    @property
    def name(self) -> str:
        return "rerank_rag"

    def _ensure_cross_encoder(self):
        if self._cross_encoder is not None:
            return self._cross_encoder
        try:
            from sentence_transformers import CrossEncoder  # type: ignore[import-not-found]
        except ImportError as e:  # pragma: no cover - env-dependent
            raise RuntimeError(
                "sentence-transformers required for rerank_rag. "
                "`uv pip install sentence-transformers`."
            ) from e
        self._cross_encoder = CrossEncoder(self._cross_encoder_model)
        return self._cross_encoder

    def answer(self, question: str) -> SystemAnswer:
        t0 = time.perf_counter()
        q_vec = self._embedder.embed(question)
        scored = [(_cosine(q_vec, v), i) for i, v in enumerate(self._index)]
        scored.sort(reverse=True)

        # Stage 1: dense top-N pool.
        pool_idxs = [i for _, i in scored[: self._rerank_pool]]
        pool_chunks = [self._chunks[i] for i in pool_idxs]

        # Stage 2: cross-encoder rerank.
        ce = self._ensure_cross_encoder()
        pairs = [[question, c.text] for c in pool_chunks]
        rerank_scores = ce.predict(pairs).tolist()
        reranked = sorted(zip(rerank_scores, pool_chunks, strict=True), reverse=True)
        retrieved = [c for _, c in reranked[: self._top_k]]

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
