"""Term-padded dense RAG — tests the term-density component of wiki retrieval lift.

At index time, each chunk's embedding text is the chunk plus a tail of regex-
extracted entity-like terms repeated `padding_factor` times. Retrieval uses
the padded embeddings; the answer-time prompt still receives the raw chunk
text, so the LLM never sees the padding.

The mechanism question this isolates: how much of the +0.30 wiki retrieval
lift on K8s comes from raw lexical/term density (replicable here, zero LLM
calls) versus genuine synthesis-derived semantic content (the residual). If
this system captures most of the lift, the case for paying LLM cost on
prose synthesis weakens for the term-density portion of the gain.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable

from retrievalci.rag_eval.backends.base import Embedder, GenerationRequest, Generator
from retrievalci.rag_eval.corpus import Chunk
from retrievalci.rag_eval.systems.rag import _PROMPT_TEMPLATE, _cosine, _detect_refusal
from retrievalci.rag_eval.types import Citation, SystemAnswer

# Patterns chosen for engineering / technical documentation: capitalized
# proper nouns ≥ 4 chars, all-caps acronyms ≥ 2 chars (with optional digits
# like "S3"), and kebab/snake_case identifiers. Pure regex — no model
# dependency, no LLM call. Some common capitalized words ("Some", "When")
# slip through, but they appear in nearly every chunk and so don't bias
# retrieval; the discriminating signal comes from rare entity-like terms.
_ENTITY_PATTERNS = (
    re.compile(r"\b[A-Z][A-Za-z0-9]{3,}\b"),               # Kubernetes, PostgreSQL, OpenAI
    re.compile(r"\b[A-Z]{2,}[0-9]*\b"),                    # ACRONYM: AOSS, SDK, K8 (no — needs 2 caps)
    re.compile(r"\b[a-z][a-z0-9]*(?:[-_][a-z0-9]+)+\b"),   # kebab/snake: bge-large-en, snake_case
)


def extract_entity_terms(text: str) -> list[str]:
    """Regex-extract entity-like terms. Case-insensitive dedupe, preserves first occurrence."""
    seen: set[str] = set()
    out: list[str] = []
    for pattern in _ENTITY_PATTERNS:
        for match in pattern.finditer(text):
            term = match.group(0)
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(term)
    return out


class DenseRAGTermPadSystem:
    def __init__(
        self,
        embedder: Embedder,
        generator: Generator,
        chunks: Iterable[Chunk],
        top_k: int = 5,
        padding_factor: int = 10,
    ) -> None:
        self._embedder = embedder
        self._generator = generator
        self._chunks = list(chunks)
        self._top_k = top_k
        self._padding_factor = padding_factor
        self._index = self._embedder.embed_batch(
            [self._padded_text(c.text) for c in self._chunks]
        )

    @property
    def name(self) -> str:
        return "dense_rag_termpad"

    def _padded_text(self, text: str) -> str:
        terms = extract_entity_terms(text)
        if not terms:
            return text
        padding = " ".join(t for t in terms for _ in range(self._padding_factor))
        return f"{text}\n\n{padding}"

    def answer(self, question: str) -> SystemAnswer:
        t_retrieve_start = time.perf_counter()
        q_vec = self._embedder.embed(question)
        scored = [(_cosine(q_vec, v), i) for i, v in enumerate(self._index)]
        scored.sort(reverse=True)
        retrieved = [self._chunks[i] for _, i in scored[: self._top_k]]
        retrieval_latency_ms = (time.perf_counter() - t_retrieve_start) * 1000.0

        # Padding is retrieval-only; the answer prompt sees raw chunk text.
        context = "\n\n".join(f"[doc:{c.chunk_id}]\n{c.text}" for c in retrieved)
        prompt = _PROMPT_TEMPLATE.format(question=question, context=context)
        resp = self._generator.generate(GenerationRequest(prompt=prompt))

        latency_ms = (time.perf_counter() - t_retrieve_start) * 1000.0
        citations = tuple(
            Citation(source_path=c.source_path, span=c.text[:160]) for c in retrieved
        )
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
