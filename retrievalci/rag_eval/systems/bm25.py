"""BM25 sparse retrieval baseline.

In-house BM25 implementation — no rank_bm25 dependency. Standard parameters
k1=1.5, b=0.75. Tokenization: whitespace + lowercase + alphanumeric only.

Used as a first-class baseline in Tier C: any retrieval-grounded QA evaluation
without a sparse baseline is incomplete. BM25 over the same chunks RAG embeds
gives the apples-to-apples sparse comparison.
"""

from __future__ import annotations

import math
import re
import time
from collections import Counter
from collections.abc import Iterable

from retrievalci.rag_eval.backends.base import GenerationRequest, Generator
from retrievalci.rag_eval.corpus import Chunk
from retrievalci.rag_eval.types import Citation, SystemAnswer

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_PATTERN.findall(text)]


_PROMPT_TEMPLATE = """\
You are answering questions about engineering documentation. Use ONLY the
retrieved context. Cite sources by [doc:path] inline. If the context doesn't
contain the answer, say so.

Question: {question}

Context:
{context}

Answer:"""


class BM25System:
    """BM25 over chunks, generate from top-k. Sparse baseline for Tier C."""

    def __init__(
        self,
        generator: Generator,
        chunks: Iterable[Chunk],
        top_k: int = 5,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self._generator = generator
        self._chunks = list(chunks)
        self._top_k = top_k
        self._k1 = k1
        self._b = b

        # Pre-tokenize and pre-compute IDF + document length stats.
        self._doc_tokens: list[list[str]] = [_tokenize(c.text) for c in self._chunks]
        self._doc_lens = [len(d) for d in self._doc_tokens]
        self._avg_dl = sum(self._doc_lens) / max(1, len(self._doc_lens))

        # Document frequency per term.
        df: Counter[str] = Counter()
        for d in self._doc_tokens:
            for term in set(d):
                df[term] += 1
        N = max(1, len(self._doc_tokens))
        # BM25 IDF: log((N - df + 0.5) / (df + 0.5) + 1) — prevents negative values.
        self._idf: dict[str, float] = {
            term: math.log((N - n + 0.5) / (n + 0.5) + 1) for term, n in df.items()
        }
        # Per-document term frequencies.
        self._tf: list[Counter[str]] = [Counter(d) for d in self._doc_tokens]

    @property
    def name(self) -> str:
        return "bm25"

    def _score(self, query_tokens: list[str], doc_idx: int) -> float:
        tf = self._tf[doc_idx]
        dl = self._doc_lens[doc_idx]
        score = 0.0
        for q in query_tokens:
            if q not in tf:
                continue
            f = tf[q]
            idf = self._idf.get(q, 0.0)
            num = f * (self._k1 + 1)
            den = f + self._k1 * (1 - self._b + self._b * dl / self._avg_dl)
            score += idf * (num / den)
        return score

    def rank(self, query: str) -> list[tuple[float, int]]:
        """Return [(score, chunk_index), ...] sorted descending. Public for fusion."""
        q_tokens = _tokenize(query)
        scored = [(self._score(q_tokens, i), i) for i in range(len(self._chunks))]
        scored.sort(reverse=True)
        return scored

    def answer(self, question: str) -> SystemAnswer:
        t0 = time.perf_counter()
        scored = self.rank(question)
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
