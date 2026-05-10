"""ACL computation helpers for claims and proof sets."""

from __future__ import annotations

from collections.abc import Iterable

# Residency precedence for the MVP (GDPR + SOC2 scope, payments domain).
# Higher index = more stringent. Add regions here when a new compliance regime
# (HIPAA, FedRAMP, etc.) enters scope; do not add speculatively.
DEFAULT_REGION_PRECEDENCE: tuple[str, ...] = ("public", "global", "us", "eu")


def compute_proof_set_acl(source_acls: Iterable[Iterable[str]]) -> frozenset[str]:
    """Intersection of source ACLs.

    Returns frozenset (hashable, immutable). Empty input → empty result.
    """
    materialized = [frozenset(s) for s in source_acls]
    if not materialized:
        return frozenset()
    result = materialized[0]
    for next_set in materialized[1:]:
        result = result & next_set
        if not result:
            break
    return result


def most_stringent_region(
    regions: Iterable[str],
    precedence: tuple[str, ...] = DEFAULT_REGION_PRECEDENCE,
) -> str:
    """Pick the region with the highest precedence index (= most stringent).

    Regions absent from the precedence list compare conservatively: any unlisted region
    is considered more stringent than any listed region, and unlisted regions tie-break
    lexicographically (deterministic).

    Raises ValueError on empty input — there is no sensible "most stringent" of nothing.
    """
    materialized = list(regions)
    if not materialized:
        raise ValueError("regions must be non-empty")

    listed_index = {r: i for i, r in enumerate(precedence)}

    def rank(region: str) -> tuple[int, str]:
        if region in listed_index:
            return (0, str(listed_index[region]).zfill(8))
        return (1, region)

    return max(materialized, key=rank)
