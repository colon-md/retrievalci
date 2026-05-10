"""Schema-versioned run registry types."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RUN_MANIFEST_SCHEMA_VERSION = "retrievalci.run_manifest.v1"

RunStatus = Literal["succeeded", "failed"]


class ArtifactPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    debug_artifacts: bool = False
    snapshot_inputs: bool = False


class RunSpec(BaseModel):
    """Inputs for creating one local RetrievalCI run artifact."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    registry_dir: str = ".retrievalci/runs"
    repo_root: str = "."

    rag_config: str | None = None
    baseline_rag_report: str | None = None
    primary_metric: str = "retrieval_source_recall"
    regression_metric: str | None = None
    max_drop: float = 0.02

    trace_input: str | None = None
    trace_source: str | None = None
    trace_source_format: str = "auto"
    trace_require_gold: bool = False
    trace_corpus: str | None = None
    trace_retriever_url: str | None = None
    trace_retriever_headers: tuple[str, ...] = ()
    trace_retriever_timeout_s: float = 10.0
    trace_policies: tuple[str, ...] = (
        "recorded",
        "query_only",
        "last_answer_x3",
        "compact_state",
        "public_trace",
    )
    trace_k: int = 10
    trace_gate_policy: str | None = None
    trace_min_recall_at_5: float | None = None
    trace_max_zero_recall_at_k: float | None = None
    trace_max_stale_at_1: float | None = None
    trace_max_false_lead_at_k: float | None = None

    artifact_policy: ArtifactPolicy = Field(default_factory=ArtifactPolicy)


class RunArtifact(BaseModel):
    """Manifest written to `.retrievalci/runs/<run-id>/manifest.json`."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = RUN_MANIFEST_SCHEMA_VERSION
    run_id: str
    created_at: str
    status: RunStatus
    name: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    inputs: dict[str, str] = Field(default_factory=dict)
    digests: dict[str, str] = Field(default_factory=dict)
    options: dict[str, str | int | float | bool | list[str] | None] = Field(default_factory=dict)
    summaries: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    failures: list[str] = Field(default_factory=list)
