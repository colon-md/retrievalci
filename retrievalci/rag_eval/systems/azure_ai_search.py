"""Azure AI Search adapter — fourth hosted Mode A target.

Azure AI Search differs from Vertex / Bedrock / OpenAI File Search in one
important way: it doesn't embed documents at ingest time. We have to BYO
embeddings. The adapter uses our existing Gemini embedder to compute
vectors locally, then uploads (chunk_id, text, vector, source_path) to
the Azure index. Queries follow the same path: embed the question via
Gemini, then POST a vector search to the Azure index.

Lifecycle:
  1. Embed all bench-v0 chunks via Gemini
  2. Create an Azure index with a knn_vector field
  3. Bulk-upload documents
  4. For each question: embed → POST /indexes/{name}/docs/search
  5. Teardown: DELETE /indexes/{name}

Cost: Azure AI Search Free tier is $0. Gemini embedding API costs apply
but are covered by the existing GEMINI_API_KEY / Ultra subscription.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from retrievalci.rag_eval.corpus import chunk_corpus, load_documents
from retrievalci.rag_eval.hosted import (
    IndexHandle,
    RunBudget,
    read_manifest,
    write_manifest,
)
from retrievalci.rag_eval.types import Citation, SystemAnswer

# Azure AI Search REST API version. Stable as of late 2024.
_API_VERSION = "2024-07-01"

# Free tier has no per-query/per-doc cost, so this is effectively zero.
# The embedding cost is handled by the embedder backend (out-of-band).
_COST_PER_QUERY = 0.0


@dataclass
class AzureAISearchConfig:
    endpoint: str
    admin_key: str
    name_prefix: str = "retrievalci-bench-v0"


@dataclass
class _ProvisionedResources:
    index_name: str | None = None


class AzureAISearchSystem:
    """Mode A retrieval via Azure AI Search vector search."""

    name = "azure_ai_search"

    def __init__(
        self,
        config: AzureAISearchConfig,
        repo_root: Path,
        budget: RunBudget,
        embedder,
    ) -> None:
        self._config = config
        self._repo_root = repo_root
        self._budget = budget
        self._embedder = embedder
        self._resources = _ProvisionedResources()
        self._index: IndexHandle | None = None
        self._chunk_key_to_repo: dict[str, str] = {}

    # ---- HostedSystem protocol ----

    def index(self, corpus_dir: Path, corpus_version_hash: str) -> IndexHandle:
        short_hash = corpus_version_hash[:8]
        index_name = f"{self._config.name_prefix}-{short_hash}".lower()
        # Azure index names must be ≤128 chars, lowercase, alphanumeric + hyphens.
        self._resources.index_name = index_name

        # 1. Chunk the corpus exactly the way the local-system harness does,
        # so the comparison is apples-to-apples.
        docs = load_documents(self._repo_root, [str(corpus_dir.relative_to(self._repo_root)) + "/*.md"])
        chunks = chunk_corpus(docs)
        print(f"  Chunked corpus: {len(chunks)} chunks from {len(docs)} docs")

        # 2. Embed the chunks via Gemini. Probe one vector to discover dim.
        sample_vec = self._embedder.embed(chunks[0].text)
        dim = len(sample_vec)
        print(f"  Embedding dim: {dim}")

        # 3. Create the index with a vector field at the right dim.
        self._create_index(index_name, dim)

        # 4. Embed remaining chunks + bulk-upload (batched 100 docs per HTTP req).
        all_vectors = [sample_vec]
        for c in chunks[1:]:
            all_vectors.append(self._embedder.embed(c.text))
        print(f"  Embedded {len(all_vectors)} chunks via Gemini")

        # 5. Upload in batches of 100.
        for batch_start in range(0, len(chunks), 100):
            batch = chunks[batch_start : batch_start + 100]
            batch_vecs = all_vectors[batch_start : batch_start + 100]
            value = []
            for c, v in zip(batch, batch_vecs, strict=True):
                chunk_key = f"c-{c.source_path.replace('/', '_').replace('.', '_')}-{c.chunk_index}"
                self._chunk_key_to_repo[chunk_key] = c.source_path
                value.append({
                    "@search.action": "upload",
                    "id": chunk_key,
                    "text": c.text,
                    "source_path": c.source_path,
                    "embedding": v,
                })
            self._api_call(
                "POST",
                f"/indexes/{index_name}/docs/index?api-version={_API_VERSION}",
                payload={"value": value},
            )
        print(f"  Uploaded {len(self._chunk_key_to_repo)} chunks to Azure index")

        # 6. Wait briefly for indexing to settle.
        time.sleep(5)

        # 7. Persist manifest.
        write_manifest(
            self._repo_root, self.name, corpus_version_hash, self._chunk_key_to_repo
        )
        self._index = IndexHandle(
            provider_index_id=index_name,
            corpus_version_hash=corpus_version_hash,
        )
        return self._index

    def chunk_manifest(self) -> dict[str, str]:
        if self._index is None:
            return {}
        return read_manifest(self._repo_root, self.name, self._index.corpus_version_hash)

    def estimate_cost(self, n_questions: int) -> float:
        # Azure free tier is $0; only the Gemini embedding side has cost,
        # which is covered out-of-band.
        return _COST_PER_QUERY * n_questions

    def answer(self, question: str) -> SystemAnswer:
        if self._resources.index_name is None or self._index is None:
            raise RuntimeError("answer() called before index()")
        manifest = self.chunk_manifest()
        q_vec = self._embedder.embed(question)
        t0 = time.perf_counter()
        result = self._api_call(
            "POST",
            f"/indexes/{self._resources.index_name}/docs/search?api-version={_API_VERSION}",
            payload={
                "vectorQueries": [{
                    "kind": "vector",
                    "vector": q_vec,
                    "fields": "embedding",
                    "k": 5,
                }],
                "select": "id,source_path,text",
            },
        )
        latency_ms = (time.perf_counter() - t0) * 1000
        self._budget.record(_COST_PER_QUERY)
        self._budget.record_query()
        citations: list[Citation] = []
        for hit in result.get("value", []):
            # source_path is uploaded directly with each doc — primary path.
            # Fall back to manifest lookup on id for robustness.
            repo_path = hit.get("source_path") or manifest.get(hit.get("id"), hit.get("id"))
            text = hit.get("text") or ""
            citations.append(Citation(source_path=repo_path, span=text[:160] or None))
        return SystemAnswer(
            answer="",
            citations=(),
            retrieved_sources=tuple(citations),
            latency_ms=latency_ms,
            retrieval_latency_ms=latency_ms,  # Mode A — retrieval is the whole call
            tokens_used=0,
            cost_usd=_COST_PER_QUERY,
            corpus_version_hash=self._index.corpus_version_hash,
            index_build_id=self._resources.index_name,
            generator_model_id="azure-ai-search-vector",
            meta={"azure_top_k": 5},
        )

    def teardown(self) -> None:
        """Delete the index. Idempotent."""
        if self._resources.index_name:
            try:
                self._api_call(
                    "DELETE",
                    f"/indexes/{self._resources.index_name}?api-version={_API_VERSION}",
                )
                print(f"  teardown: delete index {self._resources.index_name} ✓")
            except Exception as e:
                print(f"  teardown: delete index {self._resources.index_name} ✗ {e}")
            self._resources.index_name = None

    def __enter__(self) -> AzureAISearchSystem:
        return self

    def __exit__(self, *exc) -> None:
        self.teardown()

    # ---- internal ----

    def _create_index(self, name: str, dim: int) -> None:
        schema = {
            "name": name,
            "fields": [
                {"name": "id", "type": "Edm.String", "key": True, "filterable": True},
                {"name": "text", "type": "Edm.String", "searchable": True},
                {"name": "source_path", "type": "Edm.String", "filterable": True, "facetable": True},
                {
                    "name": "embedding",
                    "type": "Collection(Edm.Single)",
                    "searchable": True,
                    "dimensions": dim,
                    "vectorSearchProfile": "vector-profile-hnsw",
                },
            ],
            "vectorSearch": {
                "algorithms": [{
                    "name": "hnsw-algo",
                    "kind": "hnsw",
                    "hnswParameters": {"m": 4, "efConstruction": 400, "efSearch": 500, "metric": "cosine"},
                }],
                "profiles": [{
                    "name": "vector-profile-hnsw",
                    "algorithm": "hnsw-algo",
                }],
            },
        }
        self._api_call(
            "PUT",
            f"/indexes/{name}?api-version={_API_VERSION}",
            payload=schema,
        )
        print(f"  Created Azure index: {name}")

    def _api_call(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = f"{self._config.endpoint}{path}"
        headers = {"api-key": self._config.admin_key}
        body: bytes | None = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                text = r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"{method} {path} → HTTP {e.code}: {err_body[:500]}"
            ) from None
        return json.loads(text) if text else {}


def load_azure_adapter_from_env(repo_root: Path, budget: RunBudget) -> AzureAISearchSystem:
    endpoint = os.environ.get("AZURE_SEARCH_ENDPOINT")
    key = os.environ.get("AZURE_SEARCH_ADMIN_KEY")
    if not endpoint or not key:
        raise RuntimeError(
            "AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_ADMIN_KEY must be set in .env."
        )
    from retrievalci.rag_eval.backends.gemini import GeminiEmbedder
    return AzureAISearchSystem(
        config=AzureAISearchConfig(endpoint=endpoint, admin_key=key),
        repo_root=repo_root,
        budget=budget,
        embedder=GeminiEmbedder(),
    )
