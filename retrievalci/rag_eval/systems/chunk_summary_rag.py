"""Chunk-summary RAG baseline.

Condition S in Tier C V2: summarize each raw chunk once at index time, embed
the summary text, retrieve by summary similarity, and answer from the original
raw chunks. This tests whether cheap per-chunk write-time synthesis can capture
the retrieval lift of entity-page wiki synthesis without the page aggregation
machinery.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from hashlib import sha256
from pathlib import Path

from retrievalci.rag_eval.backends.base import Embedder, GenerationRequest, Generator
from retrievalci.rag_eval.corpus import Chunk
from retrievalci.rag_eval.types import Citation, SystemAnswer


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _summary_cache_key(
    model_id: str,
    chunk_id: str,
    chunk_text: str,
    max_output_tokens: int,
) -> str:
    payload = "\n".join(
        (
            "chunk-summary-v1",
            model_id,
            str(max_output_tokens),
            chunk_id,
            chunk_text,
        )
    )
    return sha256(payload.encode("utf-8")).hexdigest()


_SUMMARY_PROMPT = """\
Write a 3-5 sentence retrieval summary of this documentation chunk.
Use only the chunk. Preserve key entity names, API/resource names, mechanisms,
relationships, and constraints that a search query might mention. Do not add
facts that are not present.

Chunk:
{text}

Retrieval summary:"""


_QUERY_PROMPT = """\
You are answering questions about engineering documentation. Use ONLY the
retrieved context. Cite sources by [doc:path] inline. If the context doesn't
contain the answer, say so.

Question: {question}

Context:
{context}

Answer:"""


class ChunkSummaryRAGSystem:
    """Dense RAG over LLM-generated chunk summaries.

    Summaries are used only for retrieval. The answer prompt receives the raw
    source chunks, which keeps this condition focused on retrieval-side
    enrichment instead of giving the answerer synthesized prose to read.
    """

    def __init__(
        self,
        embedder: Embedder,
        generator: Generator,
        chunks: Iterable[Chunk],
        top_k: int = 5,
        summary_max_output_tokens: int = 256,
        summary_cache_dir: Path | None = None,
        progress_every: int = 0,
    ) -> None:
        self._embedder = embedder
        self._generator = generator
        self._chunks = list(chunks)
        self._top_k = top_k
        self._summary_max_output_tokens = summary_max_output_tokens
        self._summary_cache_dir = summary_cache_dir
        self._progress_every = progress_every
        if self._summary_cache_dir is not None:
            self._summary_cache_dir.mkdir(parents=True, exist_ok=True)

        self._summaries = []
        for idx, chunk in enumerate(self._chunks, start=1):
            self._summaries.append(self._summarize(chunk))
            if self._progress_every and idx % self._progress_every == 0:
                print(
                    f"chunk_summary_rag summarized {idx}/{len(self._chunks)} chunks",
                    flush=True,
                )
        self._index = self._embedder.embed_batch(self._summaries) if self._summaries else []

    @property
    def name(self) -> str:
        return "chunk_summary_rag"

    @property
    def summary_count(self) -> int:
        return len(self._summaries)

    def _summarize(self, chunk: Chunk) -> str:
        cache_path = self._cache_path(chunk)
        if cache_path is not None and cache_path.is_file():
            cached = cache_path.read_text(encoding="utf-8").strip()
            if cached:
                return cached

        prompt = _SUMMARY_PROMPT.format(text=chunk.text)
        resp = self._generator.generate(
            GenerationRequest(prompt=prompt, max_output_tokens=self._summary_max_output_tokens)
        )
        summary = resp.text.strip()
        text = summary if summary else chunk.text
        if cache_path is not None:
            cache_path.write_text(text, encoding="utf-8")
        return text

    def _cache_path(self, chunk: Chunk) -> Path | None:
        if self._summary_cache_dir is None:
            return None
        key = _summary_cache_key(
            self._generator.model_id,
            chunk.chunk_id,
            chunk.text,
            self._summary_max_output_tokens,
        )
        return self._summary_cache_dir / f"{key}.txt"

    def answer(self, question: str) -> SystemAnswer:
        t0 = time.perf_counter()
        if not self._chunks:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return SystemAnswer(
                answer="(no chunks loaded; system cannot answer)",
                citations=(),
                latency_ms=latency_ms,
                tokens_used=0,
                refused=True,
                refusal_reason="empty_chunk_index",
            )

        q_vec = self._embedder.embed(question)
        scored = [(_cosine(q_vec, v), i) for i, v in enumerate(self._index)]
        scored.sort(reverse=True)
        retrieved = [self._chunks[i] for _, i in scored[: self._top_k]]

        context = "\n\n".join(f"[doc:{c.chunk_id}]\n{c.text}" for c in retrieved)
        prompt = _QUERY_PROMPT.format(question=question, context=context)
        resp = self._generator.generate(GenerationRequest(prompt=prompt))

        latency_ms = (time.perf_counter() - t0) * 1000.0
        citations = tuple(Citation(source_path=c.source_path, span=c.text[:160]) for c in retrieved)
        return SystemAnswer(
            answer=resp.text,
            citations=citations,
            latency_ms=latency_ms,
            tokens_used=resp.tokens_used,
        )
