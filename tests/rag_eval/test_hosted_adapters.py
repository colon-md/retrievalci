"""Happy-path unit tests for the four hosted-RAG adapters.

These tests don't exercise the index() lifecycle (which hits live cloud
APIs); they pre-populate provisioned state and a manifest, then verify
the answer() path: budget charged, query counted, retrieved chunks
mapped back to repo-relative source paths via the manifest, and a
well-formed SystemAnswer returned.

Coverage rationale: prior to these tests, a response-shape change at any
of the four providers would silently break the bench-v0 scorecard
because no test imports VertexAIRAGSystem / BedrockKBSystem /
OpenAIFileSearchSystem / AzureAISearchSystem. These guard the parsing
path against silent regressions.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from retrievalci.rag_eval.hosted import IndexHandle, RunBudget, write_manifest


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def budget() -> RunBudget:
    return RunBudget(cap_usd=10.0, query_cap=10)


_CORPUS_HASH = "a" * 64


# --- OpenAI File Search ---------------------------------------------------


def test_openai_file_search_answer_maps_citations_and_charges_budget(
    repo_root: Path, budget: RunBudget
) -> None:
    from retrievalci.rag_eval.systems.openai_file_search import (
        OpenAIFileSearchConfig,
        OpenAIFileSearchSystem,
    )

    write_manifest(
        repo_root,
        "openai_file_search",
        _CORPUS_HASH,
        {"file-abc": "examples/corpus/a.md", "file-def": "examples/corpus/b.md"},
    )

    system = OpenAIFileSearchSystem(
        config=OpenAIFileSearchConfig(),
        repo_root=repo_root,
        budget=budget,
        api_key="sk-test",
    )
    system._resources.vector_store_id = "vs_test_123"
    system._index = IndexHandle(provider_index_id="vs_test_123", corpus_version_hash=_CORPUS_HASH)

    fake_response = {
        "data": [
            {"file_id": "file-abc", "content": [{"type": "text", "text": "alpha"}]},
            {"file_id": "file-unknown", "content": [{"type": "text", "text": "beta"}]},
        ]
    }
    with patch(
        "retrievalci.rag_eval.systems.openai_file_search._api",
        return_value=fake_response,
    ):
        result = system.answer("what?")

    assert budget.actual_queries == 1
    assert budget.actual_usd > 0
    assert len(result.retrieved_sources) == 2
    # Known file_id → manifest path; unknown file_id falls through to the raw id.
    assert result.retrieved_sources[0].source_path == "examples/corpus/a.md"
    assert result.retrieved_sources[1].source_path == "file-unknown"
    assert result.corpus_version_hash == _CORPUS_HASH


# --- Azure AI Search ------------------------------------------------------


def test_azure_ai_search_answer_uses_source_path_from_hit(
    repo_root: Path, budget: RunBudget
) -> None:
    from retrievalci.rag_eval.systems.azure_ai_search import (
        AzureAISearchConfig,
        AzureAISearchSystem,
    )

    write_manifest(
        repo_root,
        "azure_ai_search",
        _CORPUS_HASH,
        {"c-fallback-0": "examples/corpus/fallback.md"},
    )

    embedder = MagicMock()
    embedder.embed.return_value = [0.1] * 8  # dim doesn't matter for the test

    system = AzureAISearchSystem(
        config=AzureAISearchConfig(endpoint="https://test.search.windows.net", admin_key="k"),
        repo_root=repo_root,
        budget=budget,
        embedder=embedder,
    )
    system._resources.index_name = "test-index"
    system._index = IndexHandle(provider_index_id="test-index", corpus_version_hash=_CORPUS_HASH)

    fake_response = {
        "value": [
            {"id": "c-foo-0", "source_path": "examples/corpus/foo.md", "text": "alpha"},
            # No source_path → must fall back to manifest lookup on id.
            {"id": "c-fallback-0", "text": "beta"},
        ]
    }
    with patch.object(system, "_api_call", return_value=fake_response):
        result = system.answer("what?")

    assert budget.actual_queries == 1
    assert len(result.retrieved_sources) == 2
    assert result.retrieved_sources[0].source_path == "examples/corpus/foo.md"  # direct
    assert result.retrieved_sources[1].source_path == "examples/corpus/fallback.md"  # via manifest


# --- Vertex AI RAG Engine -------------------------------------------------


def test_vertex_ai_rag_answer_resolves_source_via_manifest(
    repo_root: Path, budget: RunBudget
) -> None:
    from retrievalci.rag_eval.systems.vertex_ai_rag import (
        VertexAIRAGConfig,
        VertexAIRAGSystem,
    )

    vertex_resource = (
        "projects/123/locations/us-west1/ragCorpora/456/ragFiles/789"
    )
    write_manifest(
        repo_root,
        "vertex_ai_rag",
        _CORPUS_HASH,
        {vertex_resource: "examples/corpus/found.md"},
    )

    system = VertexAIRAGSystem(
        config=VertexAIRAGConfig(project="123"),
        repo_root=repo_root,
        budget=budget,
        client_id="cid",
        client_secret="csecret",
        refresh_token="rt",
    )
    system._corpus_resource = "projects/123/locations/us-west1/ragCorpora/456"
    system._index = IndexHandle(
        provider_index_id=system._corpus_resource, corpus_version_hash=_CORPUS_HASH
    )

    fake_response = {
        "contexts": {
            "contexts": [
                {"sourceUri": vertex_resource, "text": "first chunk text"},
                {"sourceDisplayName": "unmapped.md", "text": "second chunk"},
            ]
        }
    }
    with (
        patch.object(system._tokens, "get", return_value="ya29.test"),
        patch(
            "retrievalci.rag_eval.systems.vertex_ai_rag._http_json",
            return_value=fake_response,
        ),
    ):
        result = system.answer("what?")

    assert budget.actual_queries == 1
    assert budget.actual_usd > 0
    assert len(result.retrieved_sources) == 2
    # First context: full Vertex resource → manifest hit → repo path.
    assert result.retrieved_sources[0].source_path == "examples/corpus/found.md"
    # Second context: only sourceDisplayName, no manifest entry → falls through.
    # resolve_source_path falls back to basename if no exact manifest hit.
    assert result.retrieved_sources[1].source_path in {"unmapped.md", "examples/corpus/unmapped.md"}


# --- Bedrock Knowledge Bases ----------------------------------------------


def test_bedrock_kb_answer_maps_s3_uri_via_manifest(
    repo_root: Path, budget: RunBudget
) -> None:
    from retrievalci.rag_eval.systems.bedrock_kb import (
        BedrockKBConfig,
        BedrockKBSystem,
    )

    s3_uri = "s3://bucket-abc/examples/corpus/known.md"
    write_manifest(
        repo_root,
        "bedrock_kb",
        _CORPUS_HASH,
        {s3_uri: "examples/corpus/known.md"},
    )

    # boto3.Session().client(...) calls happen in __init__. Mock the session so
    # each .client(name) returns a distinct MagicMock — the test only exercises
    # _bedrock_runtime.retrieve, so the other clients can stay inert.
    fake_session = MagicMock()
    fake_session.client.side_effect = lambda *args, **kwargs: MagicMock()

    system = BedrockKBSystem(
        config=BedrockKBConfig(region="us-east-1"),
        repo_root=repo_root,
        budget=budget,
        session=fake_session,
    )
    system._resources.knowledge_base_id = "kb-test-123"
    system._index = IndexHandle(provider_index_id="kb-test-123", corpus_version_hash=_CORPUS_HASH)

    fake_response = {
        "retrievalResults": [
            {
                "location": {"s3Location": {"uri": s3_uri}},
                "content": {"text": "alpha"},
            },
            {
                "location": {"s3Location": {"uri": "s3://bucket-abc/examples/corpus/unmapped.md"}},
                "content": {"text": "beta"},
            },
        ]
    }
    system._bedrock_runtime.retrieve = MagicMock(return_value=fake_response)

    result = system.answer("what?")

    assert budget.actual_queries == 1
    assert budget.actual_usd > 0
    assert len(result.retrieved_sources) == 2
    assert result.retrieved_sources[0].source_path == "examples/corpus/known.md"
    # Second hit: not in manifest → falls back to basename via resolve_source_path.
    assert "unmapped.md" in result.retrieved_sources[1].source_path
