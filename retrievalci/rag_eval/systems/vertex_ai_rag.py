"""Vertex AI RAG Engine adapter — first hosted Mode A target.

Implements the HostedSystem protocol against Google Vertex AI RAG Engine
(v1beta1) using OAuth 2.0 user-delegation auth (refresh token). The adapter
provisions a RAG corpus, uploads bench-v0 documents, runs retrieveContexts
queries, and tears the corpus down to stop Spanner-hour billing.

Auth: GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET /
GOOGLE_REFRESH_TOKEN in .env are exchanged for short-lived access tokens
(scope = cloud-platform). Project + location are passed to the constructor.

The lifecycle is explicit because Vertex RAG charges per Spanner-hour for
index storage — leaving an index alive after a run silently accrues cost
beyond the per-query budget cap. Use as a context manager so teardown()
runs even when an exception fires mid-run.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from retrievalci.rag_eval.hosted import (
    IndexHandle,
    RunBudget,
    read_manifest,
    write_manifest,
)
from retrievalci.rag_eval.types import Citation, SystemAnswer

_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"


def _aip_host(location: str) -> str:
    """Regional Vertex AI endpoint host for a given location."""
    return f"https://{location}-aiplatform.googleapis.com"

# Rough per-line cost estimates (USD) for pre-flight estimation. These are
# conservative — the real cost is provider-controlled. The post-run budget
# tally uses observed counts * these rates.
_COST_STORAGE_PER_HOUR = 0.07  # Spanner backing
_COST_INGEST_PER_DOC = 0.001  # generous over-estimate for small docs
_COST_RETRIEVE_PER_QUERY = 0.0001


class _OAuthTokenProvider:
    """Mints fresh access tokens from a refresh token. Caches until near expiry."""

    def __init__(self, client_id: str, client_secret: str, refresh_token: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._cached: tuple[str, float] | None = None  # (token, expires_at_monotonic)

    def get(self) -> str:
        now = time.monotonic()
        if self._cached and self._cached[1] - 60 > now:
            return self._cached[0]
        data = urllib.parse.urlencode({
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": self._refresh_token,
            "grant_type": "refresh_token",
        }).encode()
        req = urllib.request.Request(_OAUTH_TOKEN_URL, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            payload = json.loads(r.read())
        token = payload["access_token"]
        expires_in = float(payload.get("expires_in", 3600))
        self._cached = (token, now + expires_in)
        return token


def _http_json(
    method: str,
    url: str,
    access_token: str,
    payload: dict | None = None,
    timeout: float = 60.0,
) -> dict:
    """Minimal JSON-in / JSON-out HTTP helper with bearer auth."""
    headers = {"Authorization": f"Bearer {access_token}"}
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
        # Surface the actual API error message instead of bare HTTPError.
        raise RuntimeError(
            f"{method} {url} → HTTP {e.code}: {err_body[:600]}"
        ) from None
    return json.loads(text) if text else {}


@dataclass
class VertexAIRAGConfig:
    """Configuration for a Vertex AI RAG Engine adapter instance.

    Default location is us-west1 because us-central1 / us-east1 / us-east4
    Spanner-mode RAG Engine is allowlist-only for new projects (capacity
    cap). Serverless mode regions like us-west1 work without allowlist.
    """

    project: str
    location: str = "us-west1"
    display_name: str = "retrievalci-bench-v0"


class VertexAIRAGSystem:
    """Mode A retrieval against a Vertex AI RAG Engine corpus.

    Implements the HostedSystem protocol. The adapter is single-use per
    instance — index() provisions the corpus, answer() calls
    retrieveContexts, teardown() deletes the corpus. Use as a context
    manager so teardown runs unconditionally even on exceptions.
    """

    name = "vertex_ai_rag"

    def __init__(
        self,
        config: VertexAIRAGConfig,
        repo_root: Path,
        budget: RunBudget,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> None:
        self._config = config
        self._repo_root = repo_root
        self._budget = budget
        self._tokens = _OAuthTokenProvider(client_id, client_secret, refresh_token)
        self._index: IndexHandle | None = None
        self._corpus_resource: str | None = None  # full resource name
        self._rag_file_resources: dict[str, str] = {}  # rag_file_resource -> repo_path

    # --- HostedSystem protocol ---

    def index(self, corpus_dir: Path, corpus_version_hash: str) -> IndexHandle:
        """Provision a Vertex RAG corpus, upload each file, write the manifest."""
        access = self._tokens.get()
        host = _aip_host(self._config.location)
        parent = f"projects/{self._config.project}/locations/{self._config.location}"
        # 1. Create the corpus (long-running operation). Use a minimal payload
        # so Vertex picks region-appropriate backing-store / embedding defaults
        # — explicitly requesting Spanner mode trips an allowlist cap.
        create_url = f"{host}/v1beta1/{parent}/ragCorpora"
        create_payload = {
            "display_name": f"{self._config.display_name}-{corpus_version_hash[:8]}",
        }
        lro = _http_json("POST", create_url, access, create_payload)
        op_name = lro.get("name")
        if not op_name:
            raise RuntimeError(f"createRagCorpus did not return an operation: {lro}")
        corpus_resource = self._poll_lro(op_name, access).get("name")
        if not corpus_resource:
            raise RuntimeError("createRagCorpus completed without returning a resource name")
        self._corpus_resource = corpus_resource
        # 2. Upload each file under corpus_dir.
        for source_path in sorted(corpus_dir.glob("*.md")):
            rag_file_name = self._upload_file(corpus_resource, source_path, access)
            repo_relative = str(source_path.relative_to(self._repo_root))
            self._rag_file_resources[rag_file_name] = repo_relative
        # 3. Persist the chunk manifest (provider id -> repo-relative path).
        write_manifest(
            self._repo_root, self.name, corpus_version_hash, self._rag_file_resources
        )
        self._index = IndexHandle(
            provider_index_id=corpus_resource,
            corpus_version_hash=corpus_version_hash,
        )
        return self._index

    def chunk_manifest(self) -> dict[str, str]:
        if self._index is None:
            return {}
        return read_manifest(self._repo_root, self.name, self._index.corpus_version_hash)

    def estimate_cost(self, n_questions: int) -> float:
        """Conservative pre-flight USD estimate. Assumes 1-hour corpus lifetime."""
        return (
            _COST_STORAGE_PER_HOUR
            + _COST_INGEST_PER_DOC * len(self._rag_file_resources or {1: 1})
            + _COST_RETRIEVE_PER_QUERY * n_questions
        )

    def answer(self, question: str) -> SystemAnswer:
        """Run :retrieveContexts and produce a SystemAnswer for Mode A scoring."""
        if self._corpus_resource is None or self._index is None:
            raise RuntimeError("answer() called before index() — no corpus provisioned")
        manifest = self.chunk_manifest()
        access = self._tokens.get()
        host = _aip_host(self._config.location)
        url = (
            f"{host}/v1beta1/projects/{self._config.project}/locations/"
            f"{self._config.location}:retrieveContexts"
        )
        payload = {
            "vertex_rag_store": {
                "rag_resources": [{"rag_corpus": self._corpus_resource}],
            },
            "query": {"text": question, "similarity_top_k": 5},
        }
        t0 = time.perf_counter()
        result = _http_json("POST", url, access, payload, timeout=60)
        latency_ms = (time.perf_counter() - t0) * 1000
        # The budget records per-question retrieve cost; abort if cap is hit.
        self._budget.record(_COST_RETRIEVE_PER_QUERY)
        self._budget.record_query()
        from retrievalci.rag_eval.hosted import resolve_source_path
        contexts = result.get("contexts", {}).get("contexts", [])
        citations: list[Citation] = []
        for ctx in contexts:
            source = (
                ctx.get("sourceUri")
                or ctx.get("source_uri")
                or ctx.get("sourceDisplayName")
                or ctx.get("source_display_name")
                or ""
            )
            repo_path = resolve_source_path(source, manifest)
            text = ctx.get("text") or ""
            citations.append(Citation(source_path=repo_path, span=text[:160] or None))
        return SystemAnswer(
            answer="",  # Mode A: retrieval only, no generation
            citations=(),
            retrieved_sources=tuple(citations),
            latency_ms=latency_ms,
            retrieval_latency_ms=latency_ms,  # Mode A — retrieval is the whole call
            tokens_used=0,
            cost_usd=_COST_RETRIEVE_PER_QUERY,
            corpus_version_hash=self._index.corpus_version_hash,
            index_build_id=self._corpus_resource,
            generator_model_id="vertex-ai-rag-retrieve-only",
            meta={"vertex_top_k": 5},
        )

    def teardown(self) -> None:
        """Delete the provisioned RAG corpus to stop Spanner-hour billing.

        Idempotent — safe to call multiple times. Logs but does not re-raise on
        failure, so a teardown error doesn't mask an earlier exception.
        """
        if self._corpus_resource is None:
            return
        try:
            access = self._tokens.get()
            host = _aip_host(self._config.location)
            url = f"{host}/v1beta1/{self._corpus_resource}?force=true"
            req = urllib.request.Request(
                url, method="DELETE", headers={"Authorization": f"Bearer {access}"}
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                r.read()
            print(f"VertexAIRAGSystem.teardown: deleted {self._corpus_resource}")
        except Exception as e:
            print(
                f"VertexAIRAGSystem.teardown: WARNING failed to delete corpus "
                f"{self._corpus_resource}: {e}. Delete manually via gcloud or the "
                "GCP Console to stop Spanner billing."
            )
        finally:
            self._corpus_resource = None

    # --- internal ---

    def __enter__(self) -> VertexAIRAGSystem:
        return self

    def __exit__(self, *exc) -> None:
        self.teardown()

    def _upload_file(self, corpus_resource: str, source_path: Path, access: str) -> str:
        """Upload one file via the multipart :upload endpoint, return rag_file resource."""
        # The v1beta1 upload endpoint accepts a multipart request whose first
        # part is the rag_file metadata JSON and second part is the file body.
        host = _aip_host(self._config.location)
        url = f"{host}/upload/v1beta1/{corpus_resource}/ragFiles:upload"
        metadata = {
            "rag_file": {"display_name": source_path.name},
        }
        boundary = "retrievalci_vertex_boundary"
        file_bytes = source_path.read_bytes()
        body = (
            f"--{boundary}\r\n"
            f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(metadata)}\r\n"
            f"--{boundary}\r\n"
            f"Content-Type: text/plain\r\n\r\n"
        ).encode() + file_bytes + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {access}",
                "Content-Type": f"multipart/related; boundary={boundary}",
                "X-Goog-Upload-Protocol": "multipart",
            },
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            response = json.loads(r.read())
        # Vertex returns the response in camelCase (`ragFile`), and the SDK
        # accepts but doesn't echo snake_case. Try both, plus a top-level
        # name fallback for any LRO-wrapped variant.
        rag_file_resource = (
            response.get("ragFile", {}).get("name")
            or response.get("rag_file", {}).get("name")
            or response.get("name")
        )
        if not rag_file_resource:
            raise RuntimeError(f"ragFiles:upload did not return a resource: {response}")
        # Per-doc ingestion cost goes through the budget too.
        self._budget.record(_COST_INGEST_PER_DOC)
        return rag_file_resource

    def _poll_lro(self, op_name: str, access: str, interval_s: float = 3.0,
                  timeout_s: float = 600.0) -> dict:
        """Poll a long-running operation until done; return response payload."""
        deadline = time.monotonic() + timeout_s
        host = _aip_host(self._config.location)
        while time.monotonic() < deadline:
            url = f"{host}/v1beta1/{op_name}"
            req = urllib.request.Request(
                url, method="GET", headers={"Authorization": f"Bearer {access}"}
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                op = json.loads(r.read())
            if op.get("done"):
                if "error" in op:
                    raise RuntimeError(f"LRO failed: {op['error']}")
                return op.get("response") or op.get("metadata") or {}
            time.sleep(interval_s)
        raise TimeoutError(f"LRO {op_name} did not complete within {timeout_s}s")


def load_vertex_adapter_from_env(
    repo_root: Path,
    budget: RunBudget,
    project: str | None = None,
) -> VertexAIRAGSystem:
    """Construct a VertexAIRAGSystem from .env-style environment variables.

    Reads GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, and
    GOOGLE_REFRESH_TOKEN. The project defaults to the OAuth client's owning
    project number (parsed from GOOGLE_OAUTH_CLIENT_ID) if not supplied.
    """
    cid = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    refresh = os.environ.get("GOOGLE_REFRESH_TOKEN")
    missing = [
        k for k, v in {
            "GOOGLE_OAUTH_CLIENT_ID": cid,
            "GOOGLE_OAUTH_CLIENT_SECRET": secret,
            "GOOGLE_REFRESH_TOKEN": refresh,
        }.items() if not v
    ]
    if missing:
        raise RuntimeError(
            f"VertexAIRAGSystem requires {missing} in .env. Complete the OAuth "
            "flow first to obtain a refresh token."
        )
    if project is None:
        # Client IDs look like "<project_number>-<random>.apps.googleusercontent.com"
        project = cid.split("-", 1)[0]
    return VertexAIRAGSystem(
        config=VertexAIRAGConfig(project=project),
        repo_root=repo_root,
        budget=budget,
        client_id=cid,  # type: ignore[arg-type]
        client_secret=secret,  # type: ignore[arg-type]
        refresh_token=refresh,  # type: ignore[arg-type]
    )
