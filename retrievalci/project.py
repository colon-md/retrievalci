"""Project-level RetrievalCI configuration."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from retrievalci.runs.types import ArtifactPolicy, RunSpec


class RAGProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: str | None = None
    baseline_report: str | None = None
    primary_metric: str | None = None
    regression_metric: str | None = None
    max_drop: float | None = None


class TraceSourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str
    format: str = "auto"
    require_gold: bool = False


class TraceRetrieverConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    headers: tuple[str, ...] = ()
    timeout_s: float = 10.0


class TraceGateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: str
    min_recall_at_5: float | None = None
    max_zero_recall_at_k: float | None = None
    max_stale_at_1: float | None = None
    max_false_lead_at_k: float | None = None


class TraceProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str | None = None
    source: TraceSourceConfig | None = None
    corpus: str | None = None
    policies: tuple[str, ...] | None = None
    k: int = 10
    retriever: TraceRetrieverConfig | None = None
    gate: TraceGateConfig | None = None

    @model_validator(mode="after")
    def _check_input(self) -> TraceProjectConfig:
        if self.input and self.source:
            msg = "trace.input and trace.source are mutually exclusive"
            raise ValueError(msg)
        if not self.input and not self.source:
            msg = "trace.input or trace.source is required"
            raise ValueError(msg)
        return self


class ArtifactProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    debug_artifacts: bool = False
    snapshot_inputs: bool = False


class RetrievalCIProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    registry: str = ".retrievalci/runs"
    repo_root: str = "."
    rag: RAGProjectConfig | None = None
    trace: TraceProjectConfig | None = None
    artifacts: ArtifactProjectConfig = Field(default_factory=ArtifactProjectConfig)

    @model_validator(mode="after")
    def _check_modes(self) -> RetrievalCIProjectConfig:
        if self.rag is None and self.trace is None:
            msg = "at least one of rag or trace is required"
            raise ValueError(msg)
        return self


def load_project_config(path: str | Path) -> RetrievalCIProjectConfig:
    config_path = Path(path)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        msg = "RetrievalCI project config must be a YAML object"
        raise ValueError(msg)
    return RetrievalCIProjectConfig.model_validate(data)


def project_config_to_run_spec(
    config: RetrievalCIProjectConfig,
    *,
    config_path: str | Path,
) -> RunSpec:
    config_dir = Path(config_path).resolve().parent
    repo_root = _resolve_config_path(config.repo_root, config_dir)
    rag = config.rag
    trace = config.trace
    retriever = trace.retriever if trace else None
    source = trace.source if trace else None
    gate = trace.gate if trace else None
    return RunSpec(
        name=config.name,
        registry_dir=config.registry,
        repo_root=repo_root.as_posix(),
        rag_config=rag.config if rag else None,
        baseline_rag_report=rag.baseline_report if rag else None,
        primary_metric=(
            rag.primary_metric if rag and rag.primary_metric else "retrieval_source_recall"
        ),
        regression_metric=rag.regression_metric if rag else None,
        max_drop=rag.max_drop if rag and rag.max_drop is not None else 0.02,
        trace_input=trace.input if trace else None,
        trace_source=source.input if source else None,
        trace_source_format=source.format if source else "auto",
        trace_require_gold=source.require_gold if source else False,
        trace_corpus=trace.corpus if trace else None,
        trace_policies=trace.policies
        if trace and trace.policies
        else (
            "recorded",
            "query_only",
            "last_answer_x3",
            "compact_state",
            "public_trace",
        ),
        trace_k=trace.k if trace else 10,
        trace_gate_policy=gate.policy if gate else None,
        trace_min_recall_at_5=gate.min_recall_at_5 if gate else None,
        trace_max_zero_recall_at_k=gate.max_zero_recall_at_k if gate else None,
        trace_max_stale_at_1=gate.max_stale_at_1 if gate else None,
        trace_max_false_lead_at_k=gate.max_false_lead_at_k if gate else None,
        trace_retriever_url=retriever.url if retriever else None,
        trace_retriever_headers=retriever.headers if retriever else (),
        trace_retriever_timeout_s=retriever.timeout_s if retriever else 10.0,
        artifact_policy=ArtifactPolicy(
            debug_artifacts=config.artifacts.debug_artifacts,
            snapshot_inputs=config.artifacts.snapshot_inputs,
        ),
    )


def _resolve_config_path(path: str, base_dir: Path) -> Path:
    value = Path(path)
    if value.is_absolute():
        return value
    return (base_dir / value).resolve()
