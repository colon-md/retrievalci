"""Shared fixtures for the SearchTrace test suite."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from searchtrace.rag_eval.claims import (
    Claim,
    Evidence,
    ProofSet,
    derive_claim_id,
    derive_proof_set_id,
)

_FAKE_HASH = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def sample_evidence() -> Evidence:
    return Evidence(
        source_id="src:gs://searchtrace-demo-payments/runbook-v3.pdf",
        evidence_type="raw_doc",
        evidence_uri="gs://searchtrace-demo-payments/runbook-v3.pdf#span=120,180",
        span_start=120,
        span_end=180,
        source_version="v3",
    )


@pytest.fixture
def sample_proof_set(sample_evidence: Evidence, fixed_now: datetime) -> ProofSet:
    psid = derive_proof_set_id([sample_evidence.source_id])
    return ProofSet(
        proof_set_id=psid,
        sources=(sample_evidence,),
        acl_labels=frozenset({"group:eng@example.com", "tier:internal"}),
        validated_at=fixed_now,
        validator_model_id="gemini-2.5-pro-001",
    )


@pytest.fixture
def sample_claim(sample_proof_set: ProofSet, fixed_now: datetime) -> Claim:
    cid = derive_claim_id(
        subject="payments-api",
        predicate="autoscales_to",
        object_="10",
        prompt_id="extract-claims-v3",
        evidence_uris=["gs://searchtrace-demo-payments/runbook-v3.pdf#span=120,180"],
    )
    return Claim(
        claim_id=cid,
        knowledge_build_id=_FAKE_HASH,
        domain="payments",
        subject="payments-api",
        subject_type="entity:service",
        predicate="autoscales_to",
        object="10",
        object_type="scalar:int",
        proof_sets=(sample_proof_set,),
        prompt_id="extract-claims-v3",
        prompt_template_hash=_FAKE_HASH,
        model_id="gemini-2.5-flash-001",
        model_snapshot="gemini-2.5-flash-001@2026-04-15",
        sampling_params_hash=_FAKE_HASH,
        asserted_at=fixed_now,
        data_residency_region="us",
    )
