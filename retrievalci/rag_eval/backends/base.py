"""Abstract LLM, embedding, and judge backends.

The eval harness depends only on these protocols. Real backends (Gemini, OpenAI)
plug in by implementing them. Mock backends produce deterministic outputs for
tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class GenerationRequest:
    prompt: str
    max_output_tokens: int = 1024
    temperature: float = 0.0


@dataclass(frozen=True)
class GenerationResponse:
    text: str
    tokens_used: int


@dataclass(frozen=True)
class JudgeScore:
    """Result of one judge call. Score on a 1-5 scale."""

    score: float
    rationale: str


class Embedder(Protocol):
    """Returns a fixed-dim vector for a piece of text."""

    @property
    def dim(self) -> int: ...

    def embed(self, text: str) -> list[float]: ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


class Generator(Protocol):
    """Returns LLM output for a prompt."""

    @property
    def model_id(self) -> str: ...

    def generate(self, req: GenerationRequest) -> GenerationResponse: ...


class Judge(Protocol):
    """LLM-as-judge backend. Scores an answer against ground truth + evidence.

    All scores are 1-5 (1 = bad, 5 = good). The runner aggregates means.
    """

    @property
    def model_id(self) -> str: ...

    def faithfulness(
        self, question: str, answer: str, evidence: str, ground_truth: str
    ) -> JudgeScore:
        """Does the answer make claims supported only by the evidence and ground truth?"""
        ...

    def relevance(self, question: str, answer: str) -> JudgeScore:
        """Does the answer address the question?"""
        ...
