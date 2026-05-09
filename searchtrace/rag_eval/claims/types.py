"""Pydantic models for the claim graph.

These types are the canonical in-memory representation. BigQuery I/O is in bq_mapping.py.

Design notes:
- All models are frozen (immutable) — once a claim is constructed, it cannot be mutated.
  Append-only semantics are enforced at this layer too, not just at the storage seam.
- Predicate validation is intentionally NOT enforced here. The Claim model accepts any
  string for `predicate`. Canonicalization against predicates.yml happens in the predicates
  package; the claims package is unopinionated about vocabulary.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

EvidenceType = Literal["raw_doc", "materialized_event", "human_attestation"]


class Evidence(BaseModel):
    """One source contribution to a proof set."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    evidence_type: EvidenceType
    evidence_uri: str
    span_start: int | None = None
    span_end: int | None = None
    source_version: str | None = None

    @field_validator("source_id", "evidence_uri")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("must be non-empty")
        return v

    @field_validator("span_end")
    @classmethod
    def _span_consistency(cls, v: int | None, info) -> int | None:
        # Pydantic v2 passes other fields via info.data; span_start may be unset.
        if v is None:
            return v
        start = info.data.get("span_start")
        if start is None:
            raise ValueError("span_end requires span_start")
        if v < start:
            raise ValueError("span_end must be >= span_start")
        return v


class ProofSet(BaseModel):
    """A minimum sufficient evidence set for a claim.

    Visibility = ∃ proof set fully visible to user (i.e., user_acl ⊇ acl_labels).
    A claim has 1+ proof sets; each carries its own evidence and ACL.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    proof_set_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    sources: tuple[Evidence, ...]
    acl_labels: frozenset[str]
    validated_at: datetime
    validator_model_id: str

    @field_validator("sources")
    @classmethod
    def _at_least_one_source(cls, v: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
        if not v:
            raise ValueError("proof set must have at least one source")
        return v


class Claim(BaseModel):
    """An atomic factual statement: (subject, predicate, object) with provenance.

    A claim has 1+ proof sets and is visible iff at least one proof set is fully visible.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    knowledge_build_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    domain: str

    subject: str
    subject_type: str
    predicate: str
    object: str | None = None
    object_type: str | None = None

    proof_sets: tuple[ProofSet, ...]

    prompt_id: str
    prompt_template_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_id: str
    model_snapshot: str
    sampling_params_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    asserted_at: datetime
    retracted_at: datetime | None = None
    superseded_by_claim_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    ttl_days: int | None = Field(default=None, ge=1)

    data_residency_region: str

    @field_validator("proof_sets")
    @classmethod
    def _at_least_one_proof_set(cls, v: tuple[ProofSet, ...]) -> tuple[ProofSet, ...]:
        if not v:
            raise ValueError("claim must have at least one proof set")
        return v

    @field_validator("retracted_at")
    @classmethod
    def _retraction_after_assertion(cls, v: datetime | None, info) -> datetime | None:
        if v is None:
            return v
        asserted = info.data.get("asserted_at")
        if asserted is not None and v < asserted:
            raise ValueError("retracted_at must be >= asserted_at")
        return v
