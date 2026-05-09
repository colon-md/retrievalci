"""Pure-RAG baseline.

Embed all chunks at index time. At query time: embed the question, return
top-k chunks by cosine similarity, format them into a prompt, send to the
generator, return the answer with the retrieved chunks as citations.

Strawman the wiki must beat. Intentionally simple — a real production RAG
would add reranking, query rewriting, hybrid sparse+dense retrieval, etc.
"""

from __future__ import annotations

import time
from collections.abc import Iterable

from searchtrace.rag_eval.backends.base import Embedder, GenerationRequest, Generator
from searchtrace.rag_eval.corpus import Chunk
from searchtrace.rag_eval.types import Citation, SystemAnswer


def _cosine(a: list[float], b: list[float]) -> float:
    # Both vectors are L2-normalized in our backend, so dot = cosine.
    return sum(x * y for x, y in zip(a, b, strict=True))


_PROMPT_TEMPLATE = """\
You are answering questions about engineering documentation. Use ONLY the
retrieved context. Cite sources by [doc:path] inline. If the context doesn't
contain the answer, say so.

Question: {question}

Context:
{context}

Answer:"""


class RAGSystem:
    def __init__(
        self,
        embedder: Embedder,
        generator: Generator,
        chunks: Iterable[Chunk],
        top_k: int = 5,
    ) -> None:
        self._embedder = embedder
        self._generator = generator
        self._chunks = list(chunks)
        self._top_k = top_k
        self._index = self._embedder.embed_batch([c.text for c in self._chunks])

    @property
    def name(self) -> str:
        return "rag"

    def answer(self, question: str) -> SystemAnswer:
        t0 = time.perf_counter()
        q_vec = self._embedder.embed(question)
        scored = [(_cosine(q_vec, v), i) for i, v in enumerate(self._index)]
        scored.sort(reverse=True)
        retrieved = [self._chunks[i] for _, i in scored[: self._top_k]]

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
