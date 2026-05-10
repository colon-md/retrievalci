"""Tests for the Pydantic models in claims.types."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from retrievalci.rag_eval.claims import Claim, Evidence, ProofSet, derive_proof_set_id


class TestEvidence:
    def test_minimal_construction(self) -> None:
        ev = Evidence(
            source_id="src:1",
            evidence_type="raw_doc",
            evidence_uri="gs://bucket/file",
        )
        assert ev.source_id == "src:1"
        assert ev.span_start is None

    def test_span_consistency(self) -> None:
        with pytest.raises(ValidationError, match="span_end requires span_start"):
            Evidence(
                source_id="s",
                evidence_type="raw_doc",
                evidence_uri="u",
                span_end=10,
            )

    def test_span_end_before_start_rejected(self) -> None:
        with pytest.raises(ValidationError, match=">= span_start"):
            Evidence(
                source_id="s",
                evidence_type="raw_doc",
                evidence_uri="u",
                span_start=20,
                span_end=10,
            )

    def test_empty_source_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Evidence(source_id="", evidence_type="raw_doc", evidence_uri="u")

    def test_immutable(self) -> None:
        ev = Evidence(source_id="s", evidence_type="raw_doc", evidence_uri="u")
        with pytest.raises(ValidationError):
            ev.source_id = "other"  # type: ignore[misc]

    def test_evidence_type_enum(self) -> None:
        for t in ("raw_doc", "materialized_event", "human_attestation"):
            ev = Evidence(source_id="s", evidence_type=t, evidence_uri="u")  # type: ignore[arg-type]
            assert ev.evidence_type == t
        with pytest.raises(ValidationError):
            Evidence(source_id="s", evidence_type="bogus", evidence_uri="u")  # type: ignore[arg-type]


class TestProofSet:
    def test_minimal_construction(self, fixed_now: datetime, sample_evidence: Evidence) -> None:
        psid = derive_proof_set_id([sample_evidence.source_id])
        ps = ProofSet(
            proof_set_id=psid,
            sources=(sample_evidence,),
            acl_labels=frozenset({"group:a"}),
            validated_at=fixed_now,
            validator_model_id="m",
        )
        assert len(ps.sources) == 1

    def test_zero_sources_rejected(self, fixed_now: datetime) -> None:
        with pytest.raises(ValidationError, match="at least one source"):
            ProofSet(
                proof_set_id="0" * 64,
                sources=(),
                acl_labels=frozenset(),
                validated_at=fixed_now,
                validator_model_id="m",
            )

    def test_proof_set_id_must_be_64_hex(
        self, fixed_now: datetime, sample_evidence: Evidence
    ) -> None:
        with pytest.raises(ValidationError):
            ProofSet(
                proof_set_id="not-hex",
                sources=(sample_evidence,),
                acl_labels=frozenset(),
                validated_at=fixed_now,
                validator_model_id="m",
            )

    def test_immutable(self, sample_proof_set: ProofSet) -> None:
        with pytest.raises(ValidationError):
            sample_proof_set.acl_labels = frozenset()  # type: ignore[misc]


class TestClaim:
    def test_minimal_construction(self, sample_claim: Claim) -> None:
        assert sample_claim.subject == "payments-api"
        assert sample_claim.predicate == "autoscales_to"
        assert sample_claim.retracted_at is None
        assert len(sample_claim.proof_sets) == 1

    def test_zero_proof_sets_rejected(self, sample_claim: Claim) -> None:
        with pytest.raises(ValidationError, match="at least one proof set"):
            Claim(**{**sample_claim.model_dump(), "proof_sets": ()})

    def test_retracted_after_asserted_required(
        self, sample_claim: Claim, fixed_now: datetime
    ) -> None:
        with pytest.raises(ValidationError, match=">= asserted_at"):
            Claim(
                **{
                    **sample_claim.model_dump(),
                    "retracted_at": fixed_now - timedelta(days=1),
                }
            )

    def test_retracted_equal_to_asserted_allowed(
        self, sample_claim: Claim, fixed_now: datetime
    ) -> None:
        # Atomic create-and-retract is permitted (test-data scenario).
        c = Claim(**{**sample_claim.model_dump(), "retracted_at": fixed_now})
        assert c.retracted_at == fixed_now

    def test_arity_1_predicate_object_none(self, sample_claim: Claim) -> None:
        c = Claim(
            **{
                **sample_claim.model_dump(),
                "predicate": "is_deprecated",
                "object": None,
                "object_type": None,
            }
        )
        assert c.object is None

    def test_ttl_must_be_positive(self, sample_claim: Claim) -> None:
        with pytest.raises(ValidationError):
            Claim(**{**sample_claim.model_dump(), "ttl_days": 0})

    def test_immutable(self, sample_claim: Claim) -> None:
        with pytest.raises(ValidationError):
            sample_claim.subject = "other"  # type: ignore[misc]

    def test_serialization_roundtrip(self, sample_claim: Claim) -> None:
        as_json = sample_claim.model_dump_json()
        restored = Claim.model_validate_json(as_json)
        assert restored == sample_claim

    def test_extra_fields_rejected(self, sample_claim: Claim) -> None:
        with pytest.raises(ValidationError):
            Claim(**{**sample_claim.model_dump(), "bogus_field": 1})

    def test_claim_id_must_be_hex(self, sample_claim: Claim) -> None:
        with pytest.raises(ValidationError):
            Claim(**{**sample_claim.model_dump(), "claim_id": "not-hex"})

    def test_naive_timestamps_accepted(self, sample_claim: Claim) -> None:
        # We accept timezone-naive datetimes but recommend UTC. Document behavior.
        naive = datetime(2026, 5, 4, 12, 0, 0)
        c = Claim(**{**sample_claim.model_dump(), "asserted_at": naive})
        assert c.asserted_at == naive

    def test_offset_timestamps_accepted(self, sample_claim: Claim) -> None:
        utc_ts = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)
        c = Claim(**{**sample_claim.model_dump(), "asserted_at": utc_ts})
        assert c.asserted_at == utc_ts
