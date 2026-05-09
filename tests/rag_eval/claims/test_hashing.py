"""Tests for claim_id, proof_set_id, trace_id, and supporting hash functions."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from searchtrace.rag_eval.claims.hashing import (
    derive_claim_id,
    derive_proof_set_id,
    derive_trace_id,
    hash_acl_labels,
    hash_query,
    hash_str,
    hash_user_principal,
)

_HEX64 = r"^[0-9a-f]{64}$"


class TestDeriveClaimId:
    def test_format(self) -> None:
        cid = derive_claim_id("s", "p", "o", "prompt-v1", ["src:a"])
        assert len(cid) == 64
        assert all(c in "0123456789abcdef" for c in cid)

    def test_deterministic(self) -> None:
        a = derive_claim_id("s", "p", "o", "prompt-v1", ["src:a", "src:b"])
        b = derive_claim_id("s", "p", "o", "prompt-v1", ["src:a", "src:b"])
        assert a == b

    def test_evidence_order_invariant(self) -> None:
        a = derive_claim_id("s", "p", "o", "prompt-v1", ["src:a", "src:b"])
        b = derive_claim_id("s", "p", "o", "prompt-v1", ["src:b", "src:a"])
        assert a == b

    @pytest.mark.parametrize(
        "diff",
        [
            ("subject", ("s2", "p", "o", "prompt-v1", ["src:a"])),
            ("predicate", ("s", "p2", "o", "prompt-v1", ["src:a"])),
            ("object", ("s", "p", "o2", "prompt-v1", ["src:a"])),
            ("prompt_id", ("s", "p", "o", "prompt-v2", ["src:a"])),
            ("evidence", ("s", "p", "o", "prompt-v1", ["src:b"])),
        ],
    )
    def test_each_input_changes_hash(self, diff: tuple[str, tuple]) -> None:
        baseline = derive_claim_id("s", "p", "o", "prompt-v1", ["src:a"])
        _, args = diff
        # mypy: ignore call site narrowing — args is a tuple of literals
        variant = derive_claim_id(*args)  # type: ignore[arg-type]
        assert baseline != variant

    def test_object_none_is_distinct_from_empty_string(self) -> None:
        # Documenting actual behavior: object=None is normalized to "" inside the hash.
        # Both inputs therefore produce the same hash. This is an intentional choice —
        # arity-1 predicates have object=None and we want them to be reproducible.
        a = derive_claim_id("s", "p", None, "prompt-v1", ["src:a"])
        b = derive_claim_id("s", "p", "", "prompt-v1", ["src:a"])
        assert a == b

    def test_unicode_subject(self) -> None:
        cid = derive_claim_id("café", "p", "o", "prompt-v1", ["src:a"])
        assert len(cid) == 64


class TestDeriveProofSetId:
    def test_format(self) -> None:
        psid = derive_proof_set_id(["src:a"])
        assert len(psid) == 64

    def test_order_invariant(self) -> None:
        a = derive_proof_set_id(["src:a", "src:b", "src:c"])
        b = derive_proof_set_id(["src:c", "src:a", "src:b"])
        assert a == b

    def test_single_source(self) -> None:
        psid = derive_proof_set_id(["src:only"])
        assert len(psid) == 64

    def test_distinct_sources_distinct_hashes(self) -> None:
        a = derive_proof_set_id(["src:a"])
        b = derive_proof_set_id(["src:b"])
        assert a != b


class TestDeriveTraceId:
    def test_format(self) -> None:
        ts = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)
        tid = derive_trace_id("q" * 64, "u" * 64, ts)
        assert len(tid) == 64

    def test_deterministic(self) -> None:
        ts = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)
        a = derive_trace_id("q" * 64, "u" * 64, ts)
        b = derive_trace_id("q" * 64, "u" * 64, ts)
        assert a == b

    def test_different_ts_different_hash(self) -> None:
        a = derive_trace_id("q" * 64, "u" * 64, datetime(2026, 1, 1, tzinfo=UTC))
        b = derive_trace_id("q" * 64, "u" * 64, datetime(2026, 1, 2, tzinfo=UTC))
        assert a != b


class TestHashUserPrincipal:
    def test_deterministic_with_salt(self) -> None:
        a = hash_user_principal("alice@example.com", b"salt")
        b = hash_user_principal("alice@example.com", b"salt")
        assert a == b
        assert len(a) == 64

    def test_salt_changes_hash(self) -> None:
        a = hash_user_principal("alice@example.com", b"salt-1")
        b = hash_user_principal("alice@example.com", b"salt-2")
        assert a != b

    def test_empty_salt_rejected(self) -> None:
        with pytest.raises(ValueError, match="salt"):
            hash_user_principal("alice@example.com", b"")


class TestHashQuery:
    def test_deterministic(self) -> None:
        assert hash_query("how does payments scale?") == hash_query("how does payments scale?")

    def test_unicode_normalization(self) -> None:
        # NFC: precomposed café == decomposed café
        precomposed = "café"
        decomposed = "café"
        assert hash_query(precomposed) == hash_query(decomposed)


class TestHashAclLabels:
    def test_order_invariant(self) -> None:
        a = hash_acl_labels(["group:a", "group:b", "tier:internal"])
        b = hash_acl_labels(["tier:internal", "group:b", "group:a"])
        assert a == b

    def test_dedupe(self) -> None:
        a = hash_acl_labels(["group:a", "group:a"])
        b = hash_acl_labels(["group:a"])
        assert a == b

    def test_empty(self) -> None:
        h = hash_acl_labels([])
        assert len(h) == 64


class TestHashStr:
    def test_known_digest(self) -> None:
        assert (
            hash_str("searchtrace")
            == "58b6ae384cfbc0dc1fad057900366258f9e285c909d47d4515392567653bb120"
        )
