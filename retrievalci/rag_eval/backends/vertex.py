"""Vertex AI text embedding backend (API-key auth).

The public Gemini API (`generativelanguage.googleapis.com`) has a
hard 1000-RPD free-tier cap on `gemini-embedding-001` that's annoying for
benchmark runs. Vertex AI exposes a separate embedding surface
(`publishers/google/models/text-embedding-005:predict`) on
`aiplatform.googleapis.com` with a different quota pool. The API key
that already works for Vertex generation (stored as VERTEX_API_KEY in
.env) also authenticates this endpoint.

Returns 768-dim vectors. L2-normalized after the response because our
local cosine-similarity code assumes unit-length vectors (the same
assumption the `GeminiEmbedder` fix enforces).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request


class VertexEmbedder:
    """text-embedding-005 via the publishers/google/models/<m>:predict path."""

    def __init__(self, model: str = "text-embedding-005") -> None:
        self._model = model
        self._api_key = os.environ.get("VERTEX_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not self._api_key:
            raise RuntimeError("VERTEX_API_KEY (or GOOGLE_API_KEY) must be set to use VertexEmbedder")
        self._last_call_at: float = 0.0
        # Probe to learn dim — cheap one-shot call (must happen AFTER
        # _last_call_at init since _throttle() reads it).
        self._probe = self.embed("dim probe")
        self._dim = len(self._probe)

    @property
    def dim(self) -> int:
        return self._dim

    def _throttle(self, interval_s: float = 0.1) -> None:
        elapsed = time.monotonic() - self._last_call_at
        if elapsed < interval_s:
            time.sleep(interval_s - elapsed)
        self._last_call_at = time.monotonic()

    def _call(self, instances: list[dict]) -> list[list[float]]:
        from retrievalci.rag_eval.corpus import l2_normalize
        url = (
            f"https://aiplatform.googleapis.com/v1/publishers/google/models/"
            f"{self._model}:predict?key={urllib.parse.quote(self._api_key)}"
        )
        payload = {"instances": instances}
        body = json.dumps(payload).encode()
        last_err = None
        for attempt in range(4):
            self._throttle()
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    response = json.loads(r.read())
                preds = response.get("predictions", [])
                return [
                    l2_normalize(list(p.get("embeddings", {}).get("values", [])))
                    for p in preds
                ]
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code != 429 or attempt == 3:
                    err_body = e.read().decode("utf-8", errors="replace")[:400]
                    raise RuntimeError(
                        f"Vertex embedding → HTTP {e.code}: {err_body}"
                    ) from None
                time.sleep(min(60.0, 2**attempt * 5.0))
        raise RuntimeError(f"Vertex embedding retries exhausted: {last_err}")

    def embed(self, text: str) -> list[float]:
        return self._call([{"content": text}])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        # Vertex `:predict` accepts up to 250 instances per request, but
        # docs recommend smaller batches for embedding throughput. Use 25.
        BATCH = 25
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH):
            batch = texts[i : i + BATCH]
            out.extend(self._call([{"content": t} for t in batch]))
        return out
