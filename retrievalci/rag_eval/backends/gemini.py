"""Real Gemini backend (gated on GOOGLE_API_KEY).

Uses google-genai (the new SDK that replaces google-generativeai). Import is
lazy so the eval harness still works in mock-only environments without
google-genai installed.

Free-tier quotas (verified empirically; docs are out of date):
  - gemini-embedding-001:        100 RPM,  unknown RPD
  - gemini-2.5-flash:               5 RPM,        20 RPD  ← brutal
  - gemini-2.5-flash-lite:         10 RPM,     ~250 RPD
The generator default is gemini-2.5-flash-lite. We also retry on 429 using the
API's retryDelay hint, so transient quota nudges don't kill a long run.
On a paid plan, drop _MIN_GEN_INTERVAL_S to ~0.05 to run at full throughput.
"""

from __future__ import annotations

import os
import re
import time

from retrievalci.rag_eval.backends.base import GenerationRequest, GenerationResponse, JudgeScore

# 9 RPM = 6.7s/req, comfortably under the 10 RPM observed limit.
_MIN_GEN_INTERVAL_S = 6.7
# Embedder free-tier limit is 100 RPM. 0.7s/req = ~85 RPM, comfortable margin.
# Batched calls count as 1 request regardless of batch size, so this only
# bites when systems do many single-text embeds at query time.
_MIN_EMBED_INTERVAL_S = 0.7
_MAX_429_RETRIES = 3


class GeminiEmbedder:
    """text-embedding-004 by default; pass model= to override."""

    def __init__(self, model: str = "gemini-embedding-001") -> None:
        self._model = model
        self._client = self._make_client()
        self._last_call_at: float = 0.0
        # Single probe to learn the dimension; cheap.
        self._probe = self.embed("dim probe")
        self._dim = len(self._probe)

    @property
    def dim(self) -> int:
        return self._dim

    @staticmethod
    def _make_client(api_key: str | None = None):
        """Construct a google-genai client.

        If `api_key` is None, resolve from env vars in this order:
        GOOGLE_API_KEY, GEMINI_API_KEY. Callers that want a different key
        (e.g. a Pro-subscription key for the Judge) pass it in explicitly.
        """
        if api_key is None:
            api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("Set GOOGLE_API_KEY or GEMINI_API_KEY to use the Gemini backend.")
        try:
            from google import genai  # type: ignore[import-not-found]
        except ImportError as e:  # pragma: no cover - env-dependent
            raise RuntimeError(
                "google-genai is not installed. `uv pip install google-genai`."
            ) from e
        return genai.Client(api_key=api_key)

    def _throttle(self) -> None:
        interval = float(os.environ.get("GEMINI_EMBED_MIN_INTERVAL_S", _MIN_EMBED_INTERVAL_S))
        elapsed = time.monotonic() - self._last_call_at
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self._last_call_at = time.monotonic()

    def _call_embed_with_retry(self, contents: str | list[str]):
        from google.genai import errors  # type: ignore[import-not-found]

        for attempt in range(_MAX_429_RETRIES + 1):
            self._throttle()
            try:
                return self._client.models.embed_content(model=self._model, contents=contents)
            except errors.ClientError as e:
                if e.code != 429 or attempt == _MAX_429_RETRIES:
                    raise
                delay_match = re.search(r"retry in (\d+(?:\.\d+)?)s", str(e))
                wait = float(delay_match.group(1)) if delay_match else min(60.0, 2**attempt * 5.0)
                time.sleep(wait + 0.5)
            except errors.ServerError:
                if attempt == _MAX_429_RETRIES:
                    raise
                time.sleep(min(30.0, 5.0 + attempt * 5.0))
        raise RuntimeError("unreachable: embed retry loop exhausted without returning or raising")

    def embed(self, text: str) -> list[float]:
        from retrievalci.rag_eval.corpus import l2_normalize
        result = self._call_embed_with_retry(text)
        # Gemini's embed_content returns raw (un-normalized) vectors by
        # default. The local cosine-similarity code in retrievalci.rag_eval
        # .systems.* assumes vectors are L2-normalized so a plain dot product
        # equals cosine similarity. Normalize here so that assumption holds.
        return l2_normalize(list(result.embeddings[0].values))

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        from retrievalci.rag_eval.corpus import l2_normalize
        # The embedContent API caps batches at 100. Chunk the request.
        BATCH_LIMIT = 100
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH_LIMIT):
            batch = texts[i : i + BATCH_LIMIT]
            result = self._call_embed_with_retry(batch)
            for e in result.embeddings:
                out.append(l2_normalize(list(e.values)))
        return out


class GeminiGenerator:
    """gemini-2.5-flash-lite by default. Throttled to stay under free-tier 30 RPM."""

    def __init__(self, model: str = "gemini-2.5-flash-lite", api_key: str | None = None) -> None:
        self._model_id = model
        self._client = GeminiEmbedder._make_client(api_key=api_key)
        self._last_call_at: float = 0.0

    @property
    def model_id(self) -> str:
        return self._model_id

    def _throttle(self) -> None:
        # GEMINI_MIN_INTERVAL_S env var overrides the free-tier-safe default.
        # On Tier 1+ (1000 RPM), set GEMINI_MIN_INTERVAL_S=0.05 to skip throttling.
        interval = float(os.environ.get("GEMINI_MIN_INTERVAL_S", _MIN_GEN_INTERVAL_S))
        elapsed = time.monotonic() - self._last_call_at
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self._last_call_at = time.monotonic()

    def generate(self, req: GenerationRequest) -> GenerationResponse:
        from google.genai import errors, types  # type: ignore[import-not-found]

        # For flash-tier models, disable thinking — the eval wants
        # deterministic, cheap, fast output without 2.5-Flash's reasoning
        # trace eating the output budget. For Pro-tier models, thinking is
        # required by the API (setting thinking_budget=0 raises
        # INVALID_ARGUMENT "This model only works in thinking mode"), so
        # we omit the override and let Pro reason at its default budget.
        config_kwargs: dict = {
            "max_output_tokens": req.max_output_tokens,
            "temperature": req.temperature,
        }
        if "pro" not in self._model_id.lower():
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        config = types.GenerateContentConfig(**config_kwargs)

        for attempt in range(_MAX_429_RETRIES + 1):
            self._throttle()
            try:
                result = self._client.models.generate_content(
                    model=self._model_id,
                    contents=req.prompt,
                    config=config,
                )
                break
            except errors.ClientError as e:
                if e.code != 429 or attempt == _MAX_429_RETRIES:
                    raise
                # Honor the API's retryDelay hint when present. When the API
                # returns 429 without a hint (which happens for daily-quota
                # exhaustion), wait long enough for a per-minute window to
                # clear: 65s + jitter. This won't help if the daily cap is hit
                # — the next call will just 429 again — but it does recover
                # from generic burst limits the SDK doesn't disclose.
                delay_match = re.search(r"retry in (\d+(?:\.\d+)?)s", str(e))
                if delay_match:
                    wait = float(delay_match.group(1))
                else:
                    wait = 65.0 + attempt * 5.0
                time.sleep(wait + 0.5)
            except errors.ServerError:
                # 5xx — transient. Linear backoff, max 30s.
                if attempt == _MAX_429_RETRIES:
                    raise
                time.sleep(min(30.0, 5.0 + attempt * 5.0))

        text = result.text or ""
        usage = getattr(result, "usage_metadata", None)
        tokens = (usage.total_token_count if usage else 0) or 0
        return GenerationResponse(text=text, tokens_used=tokens)


_FAITHFULNESS_PROMPT = """\
You are an evaluator grading the faithfulness of an answer to retrieved evidence.

Question: {question}

Evidence the system used:
{evidence}

Reference (ground-truth) answer:
{ground_truth}

Candidate answer:
{answer}

Score 1 (entirely fabricated/contradicts evidence) to 5 (every claim is grounded in
the evidence and consistent with the reference). Output exactly two lines:
SCORE: <number>
RATIONALE: <one short sentence>
"""

_RELEVANCE_PROMPT = """\
You are an evaluator grading whether an answer addresses the question.

Question: {question}
Candidate answer: {answer}

Score 1 (off-topic) to 5 (directly answers the question). Output exactly two lines:
SCORE: <number>
RATIONALE: <one short sentence>
"""


class GeminiJudge:
    """Real Gemini judge — uses gemini-2.5-pro by default for nuance.

    Reuses the Generator path (with throttle + retry) for API calls. Pro
    judging has the tightest free-tier daily quota of the three Gemini calls
    in a run (~100 RPD vs flash's 250), so a 50-question x 2-call-per-row
    judge phase comes close to the daily cap and may need to wait for the
    midnight Pacific reset to retry. Cost safety against silent billing is a
    PROJECT-LEVEL concern (no billing account attached → 429 hard block on
    cap), not a model-default concern.

    Key resolution: when running a Pro-family model, the Judge prefers
    `GEMINI_API_KEY_PRO` if set — this lets a Pro/Ultra subscription key
    serve judging while a separate free-tier key serves generator and
    embedder. Falls back to `GOOGLE_API_KEY` / `GEMINI_API_KEY` if the
    Pro-specific var is unset.
    """

    def __init__(self, model: str = "gemini-2.5-pro", api_key: str | None = None) -> None:
        # Prefer the Pro-subscription key for Pro models unless the caller
        # passes one in explicitly.
        if api_key is None and "pro" in model.lower():
            api_key = os.environ.get("GEMINI_API_KEY_PRO")
        # Compose with GeminiGenerator so we get throttle + retry for free.
        self._gen = GeminiGenerator(model=model, api_key=api_key)

    @property
    def model_id(self) -> str:
        return self._gen.model_id

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
        # Pro-tier models can't disable thinking (the API rejects
        # thinking_budget=0 with INVALID_ARGUMENT), and their thinking tokens
        # share the max_output_tokens budget with visible output. A 128-token
        # cap leaves no room for visible content after thinking, returning
        # empty text. 4096 is enough headroom for typical judge reasoning
        # plus the two-line SCORE/RATIONALE output.
        is_pro = "pro" in self._gen.model_id.lower()
        budget = 4096 if is_pro else 128
        resp = self._gen.generate(GenerationRequest(prompt=prompt, max_output_tokens=budget))
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
