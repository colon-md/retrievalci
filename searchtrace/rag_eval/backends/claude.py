"""Claude judge backend (gated on ANTHROPIC_API_KEY).

Uses the official `anthropic` Python SDK. Reuses the prompt templates from
gemini.py so faithfulness/relevance scores are comparable across judges.

Default model: claude-sonnet-4-6 — Pro-tier nuance for the judge task without
Opus pricing. Override via constructor for cost/quality experiments.

Free-tier and Tier 1 quotas vary; the SDK auto-retries 429 and 5xx with
exponential backoff (default 2 retries) — usually sufficient. We add a small
local throttle as belt-and-suspenders for tight free-tier scenarios.

This file is named `claude.py` rather than `anthropic.py` on purpose: a local
module named `anthropic` would shadow the SDK package import.
"""

from __future__ import annotations

import os
import re
import time

from searchtrace.rag_eval.backends.base import GenerationRequest, GenerationResponse, JudgeScore

# Reuse the prompt templates so judge scores are comparable across backends.
from searchtrace.rag_eval.backends.gemini import _FAITHFULNESS_PROMPT, _RELEVANCE_PROMPT

# Modest throttle to keep us under any reasonable per-minute limit. The SDK
# handles 429s automatically; this just smooths bursts.
_MIN_CLAUDE_INTERVAL_S = 0.2


def _supports_effort_param(model_id: str) -> bool:
    """`output_config.effort` is supported on Opus 4.5+ and Sonnet 4.6 only.
    Haiku 4.5 and Sonnet 4.5 reject it with 400."""
    return model_id.startswith("claude-opus-") or model_id == "claude-sonnet-4-6"


class ClaudeJudge:
    """LLM-as-judge backed by Claude Sonnet 4.6 (default)."""

    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        self._model_id = model
        self._timeout_s = float(os.environ.get("ANTHROPIC_TIMEOUT_S", "60"))
        self._client = self._make_client()
        self._last_call_at: float = 0.0

    @property
    def model_id(self) -> str:
        return self._model_id

    @staticmethod
    def _make_client():
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("Set ANTHROPIC_API_KEY to use the Claude judge backend.")
        try:
            import anthropic  # type: ignore[import-not-found]
        except ImportError as e:  # pragma: no cover - env-dependent
            raise RuntimeError("anthropic SDK is not installed. `uv pip install anthropic`.") from e
        timeout_s = float(os.environ.get("ANTHROPIC_TIMEOUT_S", "60"))
        return anthropic.Anthropic(api_key=api_key, timeout=timeout_s)

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call_at
        if elapsed < _MIN_CLAUDE_INTERVAL_S:
            time.sleep(_MIN_CLAUDE_INTERVAL_S - elapsed)
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
        # `effort: "low"` keeps token use modest on models that support it
        # (Opus, Sonnet 4.6); Haiku rejects the param so we omit it there.
        kwargs: dict = dict(
            model=self._model_id,
            max_tokens=128,
            timeout=self._timeout_s,
            messages=[{"role": "user", "content": GenerationRequest(prompt=prompt).prompt}],
        )
        if _supports_effort_param(self._model_id):
            kwargs["output_config"] = {"effort": "low"}
        response = self._client.messages.create(**kwargs)

        text = ""
        for block in response.content:
            if getattr(block, "type", None) == "text":
                text = block.text
                break

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


class ClaudeGenerator:
    """Claude as a Generator. Default `claude-sonnet-4-6` to match ClaudeJudge.

    Used for triple extraction (~chunks per call) and answer generation. The
    SDK auto-retries 429/5xx with exponential backoff; we add the same modest
    inter-call throttle as the judge for burst smoothing.
    """

    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        self._model_id = model
        self._timeout_s = float(os.environ.get("ANTHROPIC_TIMEOUT_S", "60"))
        self._client = ClaudeJudge._make_client()
        self._last_call_at: float = 0.0

    @property
    def model_id(self) -> str:
        return self._model_id

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call_at
        if elapsed < _MIN_CLAUDE_INTERVAL_S:
            time.sleep(_MIN_CLAUDE_INTERVAL_S - elapsed)
        self._last_call_at = time.monotonic()

    def generate(self, req: GenerationRequest) -> GenerationResponse:
        self._throttle()
        kwargs: dict = dict(
            model=self._model_id,
            max_tokens=req.max_output_tokens,
            timeout=self._timeout_s,
            messages=[{"role": "user", "content": req.prompt}],
        )
        if _supports_effort_param(self._model_id):
            kwargs["output_config"] = {"effort": "low"}
        response = self._client.messages.create(**kwargs)
        text = ""
        for block in response.content:
            if getattr(block, "type", None) == "text":
                text = block.text
                break
        usage = getattr(response, "usage", None)
        tokens = (
            int(getattr(usage, "input_tokens", 0)) + int(getattr(usage, "output_tokens", 0))
            if usage is not None
            else 0
        )
        return GenerationResponse(text=text, tokens_used=tokens)
