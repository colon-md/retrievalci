"""Groq backend (gated on GROQ_API_KEY).

Hosted OSS inference for the eval harness. Default model
`llama-3.3-70b-versatile` — a 70B-class model with prose, instruction-following,
and structured-output quality strong enough for all four eval call types
(extraction, synthesis, answer, judge).

Token rates as of May 2026: $0.59 / $0.79 per M input/output. At ~470 calls
across ~490K tokens per full eval, expected cost is ~$0.31/run. Latency is the
fastest in the comparison set — Groq's LPU inference runs ~500+ t/s, so the
full eval finishes in well under a minute even sequentially.

Reuses `_FAITHFULNESS_PROMPT` and `_RELEVANCE_PROMPT` from `gemini.py` so
judge scores are directly comparable to the Gemini and Claude judges.
"""

from __future__ import annotations

import os
import re
import time

from searchtrace.rag_eval.backends.base import GenerationRequest, GenerationResponse, JudgeScore

# Reuse the Gemini judge prompts for cross-judge score comparability.
from searchtrace.rag_eval.backends.gemini import _FAITHFULNESS_PROMPT, _RELEVANCE_PROMPT

_DEFAULT_MODEL = "llama-3.3-70b-versatile"
# 1K RPM / 300K TPM on Groq's developer plan = ~16 RPS ceiling. 0.05s
# inter-call gap stays comfortably under the cap.
_MIN_GROQ_INTERVAL_S = 0.05


class GroqGenerator:
    """Generator backed by Groq-hosted OSS models. Default Llama 3.3 70B."""

    def __init__(self, model: str = _DEFAULT_MODEL) -> None:
        self._model_id = model
        self._client = self._make_client()
        self._last_call_at: float = 0.0

    @property
    def model_id(self) -> str:
        return self._model_id

    @staticmethod
    def _make_client():
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("Set GROQ_API_KEY to use the Groq backend.")
        try:
            from groq import Groq  # type: ignore[import-not-found]
        except ImportError as e:  # pragma: no cover - env-dependent
            raise RuntimeError(
                "groq SDK is not installed. `uv pip install groq`."
            ) from e
        return Groq(api_key=api_key)

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call_at
        if elapsed < _MIN_GROQ_INTERVAL_S:
            time.sleep(_MIN_GROQ_INTERVAL_S - elapsed)
        self._last_call_at = time.monotonic()

    def generate(self, req: GenerationRequest) -> GenerationResponse:
        self._throttle()
        response = self._client.chat.completions.create(
            model=self._model_id,
            max_completion_tokens=req.max_output_tokens,
            temperature=req.temperature,
            messages=[{"role": "user", "content": req.prompt}],
        )
        text = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        tokens = (
            (usage.prompt_tokens or 0) + (usage.completion_tokens or 0)
            if usage is not None
            else 0
        )
        return GenerationResponse(text=text, tokens_used=tokens)


class GroqJudge:
    """LLM-as-judge backed by a Groq-hosted OSS model. Same prompt rubric as
    GeminiJudge / ClaudeJudge so 1-5 scores compare across judges."""

    def __init__(self, model: str = _DEFAULT_MODEL) -> None:
        self._gen = GroqGenerator(model=model)

    @property
    def model_id(self) -> str:
        return self._gen.model_id

    def faithfulness(
        self, question: str, answer: str, evidence: str, ground_truth: str
    ) -> JudgeScore:
        prompt = _FAITHFULNESS_PROMPT.format(
            question=question,
            evidence=evidence,
            ground_truth=ground_truth,
            answer=answer,
        )
        return self._score(prompt)

    def relevance(self, question: str, answer: str) -> JudgeScore:
        return self._score(_RELEVANCE_PROMPT.format(question=question, answer=answer))

    def _score(self, prompt: str) -> JudgeScore:
        resp = self._gen.generate(GenerationRequest(prompt=prompt, max_output_tokens=128))
        score = 3.0
        rationale = "(unparsed)"
        for line in resp.text.splitlines():
            if line.lower().startswith("score:"):
                m = re.search(r"\d+(?:\.\d+)?", line)
                if m:
                    score = max(1.0, min(5.0, float(m.group(0))))
            elif line.lower().startswith("rationale:"):
                rationale = line.split(":", 1)[1].strip()
        return JudgeScore(score=score, rationale=rationale)
