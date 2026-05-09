"""Deterministic mock backends for tests.

Mock embedder: hash-based vectors, so identical text always produces identical
vectors and similar text produces somewhat-similar vectors via shared n-grams.

Mock generator: pattern-matches the prompt and emits a canned response. Not
intelligent — just enough to exercise the plumbing end-to-end without API keys.
"""

from __future__ import annotations

import hashlib
import re

from searchtrace.rag_eval.backends.base import GenerationRequest, GenerationResponse, JudgeScore


class MockEmbedder:
    def __init__(self, dim: int = 64) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str) -> list[float]:
        # n-gram bag, hashed into self._dim buckets, then unit-normalized.
        # Similar text → overlapping n-grams → cosine-similar vectors.
        norm = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
        tokens = [t for t in norm.split() if t]
        ngrams: list[str] = []
        ngrams.extend(tokens)
        for i in range(len(tokens) - 1):
            ngrams.append(f"{tokens[i]}_{tokens[i + 1]}")

        vec = [0.0] * self._dim
        for g in ngrams:
            h = int(hashlib.sha1(g.encode("utf-8")).hexdigest(), 16)
            vec[h % self._dim] += 1.0
        # L2-normalize so cosine similarity = dot product.
        norm_sq = sum(v * v for v in vec) or 1.0
        scale = norm_sq**-0.5
        return [v * scale for v in vec]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


class MockGenerator:
    """A stub generator that echoes prompt structure into a structured answer.

    For the eval harness, the mock generator extracts the literal substrings of
    the prompt that look like the question + retrieved context, and emits a
    deterministic synthesis. It is not an LLM — its purpose is to make smoke
    tests reproducible.
    """

    def __init__(self, model_id: str = "mock-generator-1") -> None:
        self._model_id = model_id

    @property
    def model_id(self) -> str:
        return self._model_id

    def generate(self, req: GenerationRequest) -> GenerationResponse:
        # Heuristic: pick the first ~3 sentence-like fragments from the prompt
        # context window. Treats the prompt as already-formatted retrieval results.
        # Real backends override this with model output.
        text = self._extract_synthesis(req.prompt)
        # Approximate token count: 1 token per 4 chars, common ratio for English.
        tokens = max(1, (len(req.prompt) + len(text)) // 4)
        return GenerationResponse(text=text, tokens_used=tokens)

    def _extract_synthesis(self, prompt: str) -> str:
        # Try to find the question line first.
        q_match = re.search(r"Question:\s*(.+)", prompt)
        question = q_match.group(1).strip() if q_match else ""

        # Pull the first 3 non-empty lines from each "[doc:...]" block we find,
        # which the systems all use to format their retrievals.
        cited_lines: list[str] = []
        for block_match in re.finditer(r"\[doc:[^\]]+\]([^\[]+)", prompt):
            block = block_match.group(1).strip()
            for line in block.splitlines():
                line = line.strip()
                if line:
                    cited_lines.append(line)
                    if len(cited_lines) >= 3:
                        break
            if len(cited_lines) >= 3:
                break

        if not cited_lines:
            return f"(mock answer to: {question})"
        bullets = "\n".join(f"- {ln}" for ln in cited_lines[:3])
        return f"(mock synthesis for: {question})\n{bullets}"


class MockJudge:
    """Deterministic scorer for tests. Uses simple substring overlap.

    Not a real judge — scoring real RAG quality requires an LLM. Use this in
    test fixtures so the harness can be exercised end-to-end without API keys.
    """

    @property
    def model_id(self) -> str:
        return "mock-judge-1"

    def faithfulness(
        self, question: str, answer: str, evidence: str, ground_truth: str
    ) -> JudgeScore:
        # Token-overlap heuristic: how much of the ground truth's content words
        # appear in the answer? Maps to a 1-5 score.
        gt_tokens = {t for t in re.findall(r"[a-z0-9]+", ground_truth.lower()) if len(t) > 3}
        if not gt_tokens:
            return JudgeScore(score=3.0, rationale="(mock: empty ground truth)")
        ans_lower = answer.lower()
        hits = sum(1 for t in gt_tokens if t in ans_lower)
        ratio = hits / len(gt_tokens)
        score = 1.0 + 4.0 * ratio  # 1.0 (no overlap) → 5.0 (full overlap)
        return JudgeScore(
            score=round(score, 2),
            rationale=f"(mock: {hits}/{len(gt_tokens)} ground-truth tokens present)",
        )

    def relevance(self, question: str, answer: str) -> JudgeScore:
        # Question-token overlap as a relevance proxy.
        q_tokens = {t for t in re.findall(r"[a-z0-9]+", question.lower()) if len(t) > 3}
        if not q_tokens or not answer:
            return JudgeScore(score=1.0, rationale="(mock: empty question or answer)")
        ans_lower = answer.lower()
        hits = sum(1 for t in q_tokens if t in ans_lower)
        ratio = hits / len(q_tokens)
        score = 1.0 + 4.0 * ratio
        return JudgeScore(
            score=round(score, 2),
            rationale=f"(mock: {hits}/{len(q_tokens)} question tokens present)",
        )
