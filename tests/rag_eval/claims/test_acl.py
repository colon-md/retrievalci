"""Tests for proof-set ACL intersection and residency precedence."""

from __future__ import annotations

import pytest
from retrievalci.rag_eval.claims.acl import (
    DEFAULT_REGION_PRECEDENCE,
    compute_proof_set_acl,
    most_stringent_region,
)


class TestComputeProofSetAcl:
    def test_intersection_of_three(self) -> None:
        result = compute_proof_set_acl(
            [
                {"a", "b", "c"},
                {"b", "c", "d"},
                {"b", "c", "e"},
            ]
        )
        assert result == frozenset({"b", "c"})

    def test_disjoint_sets_yield_empty(self) -> None:
        result = compute_proof_set_acl([{"a"}, {"b"}])
        assert result == frozenset()

    def test_single_source_returned_as_is(self) -> None:
        result = compute_proof_set_acl([{"a", "b"}])
        assert result == frozenset({"a", "b"})

    def test_empty_input_yields_empty(self) -> None:
        # A proof set with no sources cannot be satisfied → empty ACL is correct.
        result = compute_proof_set_acl([])
        assert result == frozenset()

    def test_returns_frozenset(self) -> None:
        result = compute_proof_set_acl([{"a"}])
        assert isinstance(result, frozenset)

    def test_short_circuits_on_empty_intersection(self) -> None:
        # Implementation detail surfaced: once intersection becomes empty, we stop.
        # We test the result is correct, not the loop early exit per se.
        result = compute_proof_set_acl([{"a"}, set(), {"a"}])
        assert result == frozenset()


class TestMostStringentRegion:
    def test_picks_more_stringent_of_two_listed(self) -> None:
        assert most_stringent_region(["public", "eu"]) == "eu"

    def test_picks_eu_over_us(self) -> None:
        assert most_stringent_region(["us", "eu"]) == "eu"

    def test_eu_wins_over_us_global_public(self) -> None:
        assert most_stringent_region(["public", "global", "us", "eu"]) == "eu"

    def test_all_public_returns_public(self) -> None:
        assert most_stringent_region(["public", "public"]) == "public"

    def test_single_region(self) -> None:
        assert most_stringent_region(["eu"]) == "eu"

    def test_empty_input_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            most_stringent_region([])

    def test_unlisted_region_beats_listed(self) -> None:
        # Unknown regions are treated as more conservative (more stringent) than known ones.
        # This biases toward safety — a region we haven't classified is treated as restricted.
        result = most_stringent_region(["public", "fedramp-high"])
        assert result == "fedramp-high"

    def test_unlisted_regions_break_ties_lexicographically(self) -> None:
        result = most_stringent_region(["zzz-region", "aaa-region"])
        assert result == "zzz-region"

    def test_custom_precedence(self) -> None:
        custom = ("low", "medium", "high")
        assert most_stringent_region(["low", "medium"], precedence=custom) == "medium"
        assert most_stringent_region(["medium", "high"], precedence=custom) == "high"

    def test_default_precedence_includes_expected_regions(self) -> None:
        for region in ("public", "global", "us", "eu"):
            assert region in DEFAULT_REGION_PRECEDENCE
