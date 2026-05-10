"""Tests for BigQuery row mapping (Claim → 4-table fan-out and back)."""

from __future__ import annotations

from datetime import UTC, datetime

from retrievalci.rag_eval.claims import (
    Claim,
    ClaimRows,
    Evidence,
    ProofSet,
    claim_to_rows,
    derive_proof_set_id,
    rows_to_claim,
)


class TestClaimToRows:
    def test_returns_claim_rows_dataclass(self, sample_claim: Claim) -> None:
        rows = claim_to_rows(sample_claim)
        assert isinstance(rows, ClaimRows)

    def test_one_claim_row(self, sample_claim: Claim) -> None:
        rows = claim_to_rows(sample_claim)
        assert isinstance(rows.claim, dict)
        assert rows.claim["claim_id"] == sample_claim.claim_id
        assert rows.claim["subject"] == "payments-api"
        assert rows.claim["domain"] == "payments"

    def test_proof_set_rows_count_matches(self, sample_claim: Claim) -> None:
        rows = claim_to_rows(sample_claim)
        assert len(rows.proof_sets) == len(sample_claim.proof_sets)

    def test_evidence_rows_carry_claim_and_proof_set_id(self, sample_claim: Claim) -> None:
        rows = claim_to_rows(sample_claim)
        for ev_row in rows.evidence:
            assert ev_row["claim_id"] == sample_claim.claim_id
            assert ev_row["proof_set_id"] in {ps["proof_set_id"] for ps in rows.proof_sets}

    def test_acl_rows_one_per_label(self, sample_claim: Claim) -> None:
        rows = claim_to_rows(sample_claim)
        expected_count = sum(len(ps.acl_labels) for ps in sample_claim.proof_sets)
        assert len(rows.acls) == expected_count

    def test_acl_rows_sorted_for_determinism(self, sample_claim: Claim) -> None:
        rows = claim_to_rows(sample_claim)
        per_proof_set: dict[str, list[str]] = {}
        for r in rows.acls:
            per_proof_set.setdefault(r["proof_set_id"], []).append(r["acl_label"])
        for labels in per_proof_set.values():
            assert labels == sorted(labels)

    def test_timestamps_are_iso(self, sample_claim: Claim) -> None:
        rows = claim_to_rows(sample_claim)
        # asserted_at should round-trip via fromisoformat
        datetime.fromisoformat(rows.claim["asserted_at"])

    def test_retracted_at_none_serializes_as_none(self, sample_claim: Claim) -> None:
        rows = claim_to_rows(sample_claim)
        assert rows.claim["retracted_at"] is None

    def test_object_none_preserved_in_row(self, sample_claim: Claim) -> None:
        c = Claim(
            **{
                **sample_claim.model_dump(),
                "predicate": "is_deprecated",
                "object": None,
                "object_type": None,
            }
        )
        rows = claim_to_rows(c)
        assert rows.claim["object"] is None
        assert rows.claim["object_type"] is None


class TestRowsToClaim:
    def test_roundtrip(self, sample_claim: Claim) -> None:
        rows = claim_to_rows(sample_claim)
        restored = rows_to_claim(
            claim_row=rows.claim,
            proof_set_rows=rows.proof_sets,
            evidence_rows=rows.evidence,
            acl_rows=rows.acls,
        )
        assert restored == sample_claim

    def test_roundtrip_with_retraction(self, sample_claim: Claim, fixed_now: datetime) -> None:
        retracted = Claim(**{**sample_claim.model_dump(), "retracted_at": fixed_now})
        rows = claim_to_rows(retracted)
        restored = rows_to_claim(
            claim_row=rows.claim,
            proof_set_rows=rows.proof_sets,
            evidence_rows=rows.evidence,
            acl_rows=rows.acls,
        )
        assert restored == retracted

    def test_roundtrip_with_two_proof_sets(self, sample_claim: Claim, fixed_now: datetime) -> None:
        # Build a claim with two distinct proof sets — the visibility scenario from the design.
        ev_a = Evidence(
            source_id="src:public-adr", evidence_type="raw_doc", evidence_uri="gs://b/adr.md"
        )
        ev_b = Evidence(
            source_id="src:restricted-postmortem",
            evidence_type="raw_doc",
            evidence_uri="gs://b/pm.md",
        )
        ps_a = ProofSet(
            proof_set_id=derive_proof_set_id([ev_a.source_id]),
            sources=(ev_a,),
            acl_labels=frozenset({"tier:public"}),
            validated_at=fixed_now,
            validator_model_id="m",
        )
        ps_b = ProofSet(
            proof_set_id=derive_proof_set_id([ev_b.source_id]),
            sources=(ev_b,),
            acl_labels=frozenset({"tier:restricted"}),
            validated_at=fixed_now,
            validator_model_id="m",
        )
        multi = Claim(**{**sample_claim.model_dump(), "proof_sets": (ps_a, ps_b)})
        rows = claim_to_rows(multi)
        assert len(rows.proof_sets) == 2
        assert len(rows.evidence) == 2
        assert len(rows.acls) == 2
        restored = rows_to_claim(rows.claim, rows.proof_sets, rows.evidence, rows.acls)
        assert restored == multi

    def test_filters_to_correct_claim_when_other_rows_present(self, sample_claim: Claim) -> None:
        # Confirm that rows for a different claim_id are ignored — supports batched query results.
        rows = claim_to_rows(sample_claim)
        unrelated_claim_id = "f" * 64
        unrelated_evidence_row = {**rows.evidence[0], "claim_id": unrelated_claim_id}
        unrelated_acl_row = {**rows.acls[0], "claim_id": unrelated_claim_id}
        restored = rows_to_claim(
            claim_row=rows.claim,
            proof_set_rows=rows.proof_sets,
            evidence_rows=[*rows.evidence, unrelated_evidence_row],
            acl_rows=[*rows.acls, unrelated_acl_row],
        )
        assert restored == sample_claim

    def test_iso_timestamp_parsing_with_offset(self, sample_claim: Claim) -> None:
        rows = claim_to_rows(sample_claim)
        # Replace asserted_at with an explicit offset to confirm parsing accepts it.
        rows.claim["asserted_at"] = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC).isoformat()
        restored = rows_to_claim(rows.claim, rows.proof_sets, rows.evidence, rows.acls)
        assert restored.asserted_at == datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)
