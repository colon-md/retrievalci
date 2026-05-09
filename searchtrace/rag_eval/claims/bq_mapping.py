"""BigQuery row mapping for the normalized claim-graph schema.

A single Claim model maps to rows across four BigQuery tables:
  - claims                (1 row per claim)
  - claim_proof_sets      (N rows: one per proof set)
  - claim_evidence        (M rows: one per (proof_set, evidence) tuple)
  - claim_acl             (L rows: one per (proof_set, acl_label) tuple)

Use claim_to_rows() to flatten a Claim into the four lists.
Use rows_to_claim() to reconstruct a Claim from BigQuery query results.

DDL: see searchtrace/rag_eval/schemas/bigquery.sql.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from searchtrace.rag_eval.claims.types import Claim, Evidence, ProofSet


@dataclass(frozen=True)
class ClaimRows:
    """The fan-out of a single Claim across the four BigQuery tables."""

    claim: dict[str, Any]
    proof_sets: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    acls: list[dict[str, Any]]


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def claim_to_rows(claim: Claim) -> ClaimRows:
    """Flatten a Claim into the four BQ table row shapes.

    Row shapes match the column order in searchtrace/rag_eval/schemas/bigquery.sql.
    """
    claim_row: dict[str, Any] = {
        "claim_id": claim.claim_id,
        "knowledge_build_id": claim.knowledge_build_id,
        "subject": claim.subject,
        "subject_type": claim.subject_type,
        "predicate": claim.predicate,
        "object": claim.object,
        "object_type": claim.object_type,
        "prompt_id": claim.prompt_id,
        "prompt_template_hash": claim.prompt_template_hash,
        "model_id": claim.model_id,
        "model_snapshot": claim.model_snapshot,
        "sampling_params_hash": claim.sampling_params_hash,
        "asserted_at": _iso(claim.asserted_at),
        "retracted_at": _iso(claim.retracted_at) if claim.retracted_at else None,
        "superseded_by_claim_id": claim.superseded_by_claim_id,
        "ttl_days": claim.ttl_days,
        "data_residency_region": claim.data_residency_region,
        "domain": claim.domain,
    }

    proof_set_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    acl_rows: list[dict[str, Any]] = []

    for ps in claim.proof_sets:
        proof_set_rows.append(
            {
                "claim_id": claim.claim_id,
                "proof_set_id": ps.proof_set_id,
                "source_count": len(ps.sources),
                "validated_at": _iso(ps.validated_at),
                "validator_model_id": ps.validator_model_id,
            }
        )
        for ev in ps.sources:
            evidence_rows.append(
                {
                    "claim_id": claim.claim_id,
                    "proof_set_id": ps.proof_set_id,
                    "source_id": ev.source_id,
                    "evidence_type": ev.evidence_type,
                    "evidence_uri": ev.evidence_uri,
                    "span_start": ev.span_start,
                    "span_end": ev.span_end,
                    "source_version": ev.source_version,
                }
            )
        for label in sorted(ps.acl_labels):
            acl_rows.append(
                {
                    "claim_id": claim.claim_id,
                    "proof_set_id": ps.proof_set_id,
                    "acl_label": label,
                }
            )

    return ClaimRows(
        claim=claim_row, proof_sets=proof_set_rows, evidence=evidence_rows, acls=acl_rows
    )


def _parse_iso(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def rows_to_claim(
    claim_row: dict[str, Any],
    proof_set_rows: Iterable[dict[str, Any]],
    evidence_rows: Iterable[dict[str, Any]],
    acl_rows: Iterable[dict[str, Any]],
) -> Claim:
    """Reconstruct a Claim from the four-table fan-out.

    Filters every input list by `claim_row["claim_id"]` so a caller can pass
    raw query results that mix multiple claims; the returned Claim is built
    only from rows whose claim_id matches.
    """
    claim_id = claim_row["claim_id"]

    evidence_by_proof_set: dict[str, list[Evidence]] = {}
    for row in evidence_rows:
        if row["claim_id"] != claim_id:
            continue
        evidence_by_proof_set.setdefault(row["proof_set_id"], []).append(
            Evidence(
                source_id=row["source_id"],
                evidence_type=row["evidence_type"],
                evidence_uri=row["evidence_uri"],
                span_start=row.get("span_start"),
                span_end=row.get("span_end"),
                source_version=row.get("source_version"),
            )
        )

    acls_by_proof_set: dict[str, set[str]] = {}
    for row in acl_rows:
        if row["claim_id"] != claim_id:
            continue
        acls_by_proof_set.setdefault(row["proof_set_id"], set()).add(row["acl_label"])

    proof_sets: list[ProofSet] = []
    for ps_row in proof_set_rows:
        if ps_row["claim_id"] != claim_id:
            continue
        psid = ps_row["proof_set_id"]
        proof_sets.append(
            ProofSet(
                proof_set_id=psid,
                sources=tuple(evidence_by_proof_set.get(psid, [])),
                acl_labels=frozenset(acls_by_proof_set.get(psid, set())),
                validated_at=_parse_iso(ps_row["validated_at"]),
                validator_model_id=ps_row["validator_model_id"],
            )
        )

    return Claim(
        claim_id=claim_id,
        knowledge_build_id=claim_row["knowledge_build_id"],
        domain=claim_row["domain"],
        subject=claim_row["subject"],
        subject_type=claim_row["subject_type"],
        predicate=claim_row["predicate"],
        object=claim_row.get("object"),
        object_type=claim_row.get("object_type"),
        proof_sets=tuple(proof_sets),
        prompt_id=claim_row["prompt_id"],
        prompt_template_hash=claim_row["prompt_template_hash"],
        model_id=claim_row["model_id"],
        model_snapshot=claim_row["model_snapshot"],
        sampling_params_hash=claim_row["sampling_params_hash"],
        asserted_at=_parse_iso(claim_row["asserted_at"]),
        retracted_at=_parse_iso(claim_row["retracted_at"])
        if claim_row.get("retracted_at")
        else None,
        superseded_by_claim_id=claim_row.get("superseded_by_claim_id"),
        ttl_days=claim_row.get("ttl_days"),
        data_residency_region=claim_row["data_residency_region"],
    )
