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

from retrievalci.rag_eval.backends.base import Embedder, GenerationRequest, Generator
from retrievalci.rag_eval.corpus import Chunk
from retrievalci.rag_eval.types import Citation, SystemAnswer


def _cosine(a: list[float], b: list[float]) -> float:
    # Both vectors are L2-normalized in our backend, so dot = cosine.
    return sum(x * y for x, y in zip(a, b, strict=True))


_PROMPT_TEMPLATE = """\
You are answering questions about engineering documentation. Use ONLY the
retrieved context. Cite sources by [doc:path] inline.

If the retrieved context does NOT contain enough information to answer
the question with specific facts, respond with exactly one line:
REFUSE: <one short sentence explaining what's missing>
Do not invent details, do not partial-answer, do not list related topics.

Otherwise, answer the question concisely with citations.

Question: {question}

Context:
{context}

Answer:"""


def _detect_refusal(answer_text: str) -> tuple[bool, str | None]:
    """Return (refused, reason) when the LLM emitted the REFUSE: protocol."""
    stripped = answer_text.lstrip()
    if stripped.startswith("REFUSE:"):
        reason = stripped[len("REFUSE:"):].splitlines()[0].strip() or None
        return True, reason
    return False, None


class DenseRAGSystem:
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
        return "dense_rag"

    def answer(self, question: str) -> SystemAnswer:
        t_retrieve_start = time.perf_counter()
        q_vec = self._embedder.embed(question)
        scored = [(_cosine(q_vec, v), i) for i, v in enumerate(self._index)]
        scored.sort(reverse=True)
        retrieved = [self._chunks[i] for _, i in scored[: self._top_k]]
        retrieval_latency_ms = (time.perf_counter() - t_retrieve_start) * 1000.0

        context = "\n\n".join(f"[doc:{c.chunk_id}]\n{c.text}" for c in retrieved)
        prompt = _PROMPT_TEMPLATE.format(question=question, context=context)
        resp = self._generator.generate(GenerationRequest(prompt=prompt))

        latency_ms = (time.perf_counter() - t_retrieve_start) * 1000.0
        citations = tuple(Citation(source_path=c.source_path, span=c.text[:160]) for c in retrieved)
        refused, reason = _detect_refusal(resp.text)
        return SystemAnswer(
            answer=resp.text,
            citations=citations,
            latency_ms=latency_ms,
            retrieval_latency_ms=retrieval_latency_ms,
            tokens_used=resp.tokens_used,
            refused=refused,
            refusal_reason=reason,
        )


# Back-compat alias — older imports of RAGSystem keep working.
RAGSystem = DenseRAGSystem

