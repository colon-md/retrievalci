"""OpenAI File Search adapter — third hosted Mode A target.

Uses OpenAI's Vector Stores API for retrieval (the cheaper retrieve-only
path than the Responses API). Lifecycle is much simpler than Bedrock:

  1. Upload each corpus file via /v1/files (purpose=assistants)
  2. Create a vector store via /v1/vector_stores
  3. Attach files to the store via /v1/vector_stores/{id}/file_batches
  4. Wait for processing complete
  5. For each question: POST /v1/vector_stores/{id}/search
  6. Teardown: delete vector store + delete files

Cost model:
  - Storage: $0.10/GB-day (bench-v0 corpus is ~1 MB → ~$0.0001/day)
  - Embedding ingestion: included in storage
  - Search: per-query (negligible)

API-key auth only. No IAM, no multi-service lifecycle.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from retrievalci.rag_eval.hosted import (
    IndexHandle,
    RunBudget,
    read_manifest,
    write_manifest,
)
from retrievalci.rag_eval.types import Citation, SystemAnswer

_API_HOST = "https://api.openai.com/v1"
_COST_STORAGE_DAY = 0.0001  # ~1MB bench-v0 corpus
_COST_PER_QUERY = 0.0025 / 1000  # search pricing


@dataclass
class OpenAIFileSearchConfig:
    name_prefix: str = "retrievalci-bench-v0"


def _api(
    method: str,
    path: str,
    api_key: str,
    payload: dict | None = None,
    timeout: float = 60.0,
) -> dict:
    """Minimal OpenAI REST helper with bearer auth + body decoding."""
    url = f"{_API_HOST}{path}"
    headers = {"Authorization": f"Bearer {api_key}"}
    body: bytes | None = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} → HTTP {e.code}: {err_body[:500]}") from None
    return json.loads(text) if text else {}


def _upload_file(api_key: str, source_path: Path) -> str:
    """POST /v1/files via multipart form. Returns the file_id."""
    boundary = "retrievalci_openai_boundary"
    file_bytes = source_path.read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="purpose"\r\n\r\n'
        f"assistants\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{source_path.name}"\r\n'
        f"Content-Type: text/markdown\r\n\r\n"
    ).encode() + file_bytes + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{_API_HOST}/files",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            response = json.loads(r.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"upload {source_path.name} → HTTP {e.code}: {err_body[:500]}") from None
    file_id = response.get("id")
    if not file_id:
        raise RuntimeError(f"Files API did not return id: {response}")
    return file_id


@dataclass
class _ProvisionedResources:
    file_ids: list[str] = field(default_factory=list)
    vector_store_id: str | None = None


class OpenAIFileSearchSystem:
    """Mode A retrieval via OpenAI's Vector Stores Search API."""

    name = "openai_file_search"

    def __init__(
        self,
        config: OpenAIFileSearchConfig,
        repo_root: Path,
        budget: RunBudget,
        api_key: str,
    ) -> None:
        self._config = config
        self._repo_root = repo_root
        self._budget = budget
        self._api_key = api_key
        self._resources = _ProvisionedResources()
        self._index: IndexHandle | None = None
        self._file_id_to_repo: dict[str, str] = {}

    # ---- HostedSystem protocol ----

    def index(self, corpus_dir: Path, corpus_version_hash: str) -> IndexHandle:
        short_hash = corpus_version_hash[:8]

        # 1. Upload each file individually (Files API has no bulk multipart).
        for src in sorted(corpus_dir.glob("*.md")):
            file_id = _upload_file(self._api_key, src)
            self._resources.file_ids.append(file_id)
            repo_relative = str(src.relative_to(self._repo_root))
            self._file_id_to_repo[file_id] = repo_relative
        print(f"  Uploaded {len(self._resources.file_ids)} files to /v1/files")

        # 2. Create the vector store.
        vs = _api(
            "POST", "/vector_stores", self._api_key,
            payload={
                "name": f"{self._config.name_prefix}-{short_hash}",
                "file_ids": self._resources.file_ids,
            },
        )
        self._resources.vector_store_id = vs["id"]
        print(f"  Created vector store: {self._resources.vector_store_id}")

        # 3. Wait for files to embed.
        self._wait_for_processing(self._resources.vector_store_id)

        # 4. Persist manifest.
        write_manifest(
            self._repo_root, self.name, corpus_version_hash, self._file_id_to_repo
        )
        self._index = IndexHandle(
            provider_index_id=self._resources.vector_store_id,
            corpus_version_hash=corpus_version_hash,
        )
        return self._index

    def chunk_manifest(self) -> dict[str, str]:
        if self._index is None:
            return {}
        return read_manifest(self._repo_root, self.name, self._index.corpus_version_hash)

    def estimate_cost(self, n_questions: int) -> float:
        return _COST_STORAGE_DAY + _COST_PER_QUERY * n_questions

    def answer(self, question: str) -> SystemAnswer:
        if self._resources.vector_store_id is None or self._index is None:
            raise RuntimeError("answer() called before index() — no vector store provisioned")
        manifest = self.chunk_manifest()
        t0 = time.perf_counter()
        result = _api(
            "POST",
            f"/vector_stores/{self._resources.vector_store_id}/search",
            self._api_key,
            payload={"query": question, "max_num_results": 5},
        )
        latency_ms = (time.perf_counter() - t0) * 1000
        self._budget.record(_COST_PER_QUERY)
        self._budget.record_query()

        citations: list[Citation] = []
        for hit in result.get("data", []):
            file_id = hit.get("file_id") or ""
            repo_path = manifest.get(file_id, file_id)
            # `content` is a list of {type, text} segments. Concatenate text.
            spans = [
                seg.get("text", "")
                for seg in (hit.get("content") or [])
                if seg.get("type") == "text"
            ]
            span = " ".join(spans)[:160] or None
            citations.append(Citation(source_path=repo_path, span=span))

        return SystemAnswer(
            answer="",
            citations=(),
            retrieved_sources=tuple(citations),
            latency_ms=latency_ms,
            retrieval_latency_ms=latency_ms,  # Mode A — retrieval is the whole call
            tokens_used=0,
            cost_usd=_COST_PER_QUERY,
            corpus_version_hash=self._index.corpus_version_hash,
            index_build_id=self._resources.vector_store_id,
            generator_model_id="openai-vector-store-search",
            meta={"openai_max_num_results": 5},
        )

    def teardown(self) -> None:
        """Delete the vector store + each uploaded file. Idempotent."""
        if self._resources.vector_store_id:
            self._safe(
                f"delete vector store {self._resources.vector_store_id}",
                lambda: _api(
                    "DELETE",
                    f"/vector_stores/{self._resources.vector_store_id}",
                    self._api_key,
                ),
            )
            self._resources.vector_store_id = None
        for file_id in list(self._resources.file_ids):
            self._safe(
                f"delete file {file_id}",
                lambda f=file_id: _api("DELETE", f"/files/{f}", self._api_key),
            )
        self._resources.file_ids = []

    def __enter__(self) -> OpenAIFileSearchSystem:
        return self

    def __exit__(self, *exc) -> None:
        self.teardown()

    # ---- internal ----

    def _safe(self, label: str, fn) -> None:
        try:
            fn()
            print(f"  teardown: {label} ✓")
        except Exception as e:
            print(f"  teardown: {label} ✗ {type(e).__name__}: {e}")

    def _wait_for_processing(self, vs_id: str, timeout_s: float = 600.0) -> None:
        """Poll the vector store until all files are processed.

        Status values: in_progress, completed, failed, cancelled.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            vs = _api("GET", f"/vector_stores/{vs_id}", self._api_key)
            counts = vs.get("file_counts", {})
            in_progress = counts.get("in_progress", 0)
            completed = counts.get("completed", 0)
            failed = counts.get("failed", 0)
            cancelled = counts.get("cancelled", 0)
            total = counts.get("total", 0)
            print(
                f"  vector store status: {vs.get('status')} "
                f"(completed={completed}/{total}, in_progress={in_progress}, "
                f"failed={failed}, cancelled={cancelled})"
            )
            if in_progress == 0 and total > 0:
                if failed > 0:
                    raise RuntimeError(f"vector store {vs_id}: {failed} files failed processing")
                print(f"  vector store ready: {completed}/{total} files embedded")
                return
            time.sleep(5)
        raise TimeoutError(f"vector store {vs_id} did not finish within {timeout_s}s")


def load_openai_adapter_from_env(repo_root: Path, budget: RunBudget) -> OpenAIFileSearchSystem:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not set; cannot construct OpenAI File Search adapter.")
    return OpenAIFileSearchSystem(
        config=OpenAIFileSearchConfig(),
        repo_root=repo_root,
        budget=budget,
        api_key=key,
    )
