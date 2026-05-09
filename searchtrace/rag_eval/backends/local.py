"""Local sentence-transformers embedder.

Free, deterministic, no API quota. First call downloads the model weights
(~80MB for `all-MiniLM-L6-v2`); subsequent calls are CPU-only inference.
Embeddings are L2-normalized so the existing cosine code (just dot product)
stays correct.

Quality note: `all-MiniLM-L6-v2` scores roughly 70% of OpenAI/Gemini
embeddings on retrieval benchmarks. For this eval that's fine — all three
systems share the same embedder, so any embedding-quality penalty is shared.
"""

from __future__ import annotations


class LocalEmbedder:
    """sentence-transformers embedder. Default `all-MiniLM-L6-v2` (384-dim)."""

    def __init__(self, model: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
        except ImportError as e:  # pragma: no cover - env-dependent
            raise RuntimeError(
                "sentence-transformers not installed. "
                "`uv pip install sentence-transformers`."
            ) from e
        self._model = SentenceTransformer(model)
        self._dim = int(self._model.get_sentence_embedding_dimension())

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str) -> list[float]:
        vec = self._model.encode(text, normalize_embeddings=True, show_progress_bar=False)
        return vec.tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        mat = self._model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False, batch_size=32
        )
        return [v.tolist() for v in mat]
