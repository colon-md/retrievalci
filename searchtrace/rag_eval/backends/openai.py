"""OpenAI judge backend (gated on OPENAI_API_KEY).

Uses the official `openai` Python SDK. Reuses the prompt templates from
gemini.py so faithfulness/relevance scores are comparable across judges.

Default model: gpt-5.4-mini — the newest mini-tier in the gpt-5 family at
the time of writing. Mini gives the nuance a judge needs without full-tier
pricing; nano is a tier below and can be brittle on rubric tasks.

Note on billing: an OpenAI key without active billing returns
`429 billing_not_active` on every chat completion call. The constructor
does not detect this — it only fires when the first judge call is made.

This file is named `openai.py` purposefully (not `gpt.py` or similar) to
match the SDK package name conceptually. The local module is reachable via
`searchtrace.rag_eval.backends.openai`; the SDK is imported lazily inside methods so
there's no shadowing.
"""

from __future__ import annotations

import os
import re
import time

from searchtrace.rag_eval.backends.base import JudgeScore

# Reuse the prompt templates so judge scores are comparable across backends.
from searchtrace.rag_eval.backends.gemini import _FAITHFULNESS_PROMPT, _RELEVANCE_PROMPT

# Mild throttle. OpenAI rate limits per-tier; the SDK auto-retries 429/5xx.
_MIN_OPENAI_INTERVAL_S = 0.2


class OpenAIJudge:
    """LLM-as-judge backed by an OpenAI mini-tier model (default: gpt-5.4-mini)."""

    def __init__(self, model: str = "gpt-5.4-mini") -> None:
        self._model_id = model
        self._client = self._make_client()
        self._last_call_at: float = 0.0

    @property
    def model_id(self) -> str:
        return self._model_id

    @staticmethod
    def _make_client():
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("Set OPENAI_API_KEY to use the OpenAI judge backend.")
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError as e:  # pragma: no cover - env-dependent
            raise RuntimeError("openai SDK is not installed. `uv pip install openai`.") from e
        return OpenAI(api_key=api_key)

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call_at
        if elapsed < _MIN_OPENAI_INTERVAL_S:
            time.sleep(_MIN_OPENAI_INTERVAL_S - elapsed)
        self._last_call_at = time.monotonic()

    def faithfulness(
        self, question: str, answer: str, evidence: str, ground_truth: str
    ) -> JudgeScore:
        prompt = _FAITHFULNESS_PROMPT.format(
            question=question, evidence=evidence, ground_truth=ground_truth, answer=answer
        )
        return self._score(prompt)

    def relevance(self, question: str, answer: str) -> JudgeScore:
        prompt = _RELEVANCE_PROMPT.format(question=question, answer=answer)
        return self._score(prompt)

    def _score(self, prompt: str) -> JudgeScore:
        self._throttle()
        response = self._client.chat.completions.create(
            model=self._model_id,
            max_completion_tokens=128,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.choices[0].message.content or ""

        score = 3.0
        rationale = "(unparsed)"
        for line in text.splitlines():
            if line.lower().startswith("score:"):
                m = re.search(r"\d+(?:\.\d+)?", line)
                if m:
                    score = max(1.0, min(5.0, float(m.group(0))))
            elif line.lower().startswith("rationale:"):
                rationale = line.split(":", 1)[1].strip()
        return JudgeScore(score=score, rationale=rationale)
