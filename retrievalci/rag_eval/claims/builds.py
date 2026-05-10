"""Versioned, append-only KnowledgeBuild snapshots.

A `KnowledgeBuild` is a content-hashed snapshot of (claims, projected entity
pages) at a point in time. Builds form a chain: each build references its
parent and adds a delta of new claims.

The load-bearing invariant of `merge_claims_into_build` is that synthesis cost
scales with the size of the *delta*, not the cumulative claim count. Only
entity pages whose underlying claim set changed are re-projected and
re-synthesized; untouched pages keep their `synthesized_prose` as-is.
Cross-references are re-resolved across the full page set on every merge,
which is cheap (O(N) over pages, no LLM calls).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from retrievalci.rag_eval.backends.base import Generator
from retrievalci.rag_eval.claims.types import Claim
from retrievalci.rag_eval.predicates import PredicateVocabulary
from retrievalci.rag_eval.systems.wiki_pages import (
    CrossRef,
    EntityPage,
    PredicateSection,
    PredicateValue,
    project_pages,
    resolve_cross_references,
    synthesize_pages,
)


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _derive_build_id(parent_build_id: str | None, new_claim_ids: list[str]) -> str:
    """Build IDs are content-hashed: same parent + same new-claim set → same id.

    Stable across runs so two equivalent merges produce the same build_id.
    """
    parent = parent_build_id or "root"
    claims_payload = "|".join(sorted(new_claim_ids))
    return _sha256_hex(f"{parent}|{claims_payload}")


@dataclass(frozen=True)
class KnowledgeBuild:
    """One versioned snapshot of the claim graph + projected wiki pages.

    `claims` accumulates: every claim that has ever contributed to this build's
    chain is present here. `pages` is the projection at this build. The chain
    forms a parent-pointer tree via `parent_build_id`.
    """

    build_id: str
    parent_build_id: str | None
    built_at: datetime
    claims: tuple[Claim, ...]
    pages: tuple[EntityPage, ...]

    @property
    def claim_count(self) -> int:
        return len(self.claims)

    @property
    def page_count(self) -> int:
        return len(self.pages)


def merge_claims_into_build(
    prior_build: KnowledgeBuild | None,
    new_claims: list[Claim],
    generator: Generator,
    *,
    vocabulary: PredicateVocabulary | None = None,
) -> KnowledgeBuild:
    """Append-only merge — incremental synthesis only for modified entities.

    Without a prior build, projects + synthesizes everything as a root build.
    With a prior build:
      1. Filter out claims already present (idempotent merge).
      2. Identify entities touched by the truly-new claims.
      3. Re-project and re-synthesize ONLY those entities.
      4. Re-resolve cross-references across the full page set.
      5. Return a new build with the new build_id, the union of all claims,
         and the merged page list.
    """
    now = datetime.now(UTC)

    if prior_build is None:
        all_claims = list(new_claims)
        pages = project_pages(all_claims, vocabulary=vocabulary)
        if pages:
            pages = synthesize_pages(pages, generator)
        return KnowledgeBuild(
            build_id=_derive_build_id(None, [c.claim_id for c in all_claims]),
            parent_build_id=None,
            built_at=now,
            claims=tuple(all_claims),
            pages=tuple(pages),
        )

    prior_claim_ids = {c.claim_id for c in prior_build.claims}
    truly_new = [c for c in new_claims if c.claim_id not in prior_claim_ids]

    if not truly_new:
        return prior_build

    modified_entities: set[tuple[str, str]] = {
        (c.subject_type, c.subject) for c in truly_new
    }

    claims_by_id: dict[str, Claim] = {c.claim_id: c for c in prior_build.claims}
    for c in truly_new:
        claims_by_id[c.claim_id] = c
    all_claims = list(claims_by_id.values())

    re_synthesized: dict[tuple[str, str], EntityPage] = {}
    for entity in modified_entities:
        entity_claims = [
            c for c in all_claims if (c.subject_type, c.subject) == entity
        ]
        projected = project_pages(entity_claims, vocabulary=vocabulary)
        if not projected:
            continue
        synthesized = synthesize_pages(projected, generator)
        re_synthesized[entity] = synthesized[0]

    final_pages: list[EntityPage] = []
    for prior_page in prior_build.pages:
        key = (prior_page.subject_type, prior_page.subject)
        if key in re_synthesized:
            final_pages.append(re_synthesized.pop(key))
        else:
            final_pages.append(prior_page)
    final_pages.extend(re_synthesized.values())

    final_pages = resolve_cross_references(final_pages)

    return KnowledgeBuild(
        build_id=_derive_build_id(prior_build.build_id, [c.claim_id for c in truly_new]),
        parent_build_id=prior_build.build_id,
        built_at=now,
        claims=tuple(all_claims),
        pages=tuple(final_pages),
    )


# --- On-disk persistence ------------------------------------------------------
#
# Layout:
#   {dir}/HEAD                   Plain-text file containing the current build_id.
#   {dir}/{build_id}.json        One JSON file per build. Self-contained — claims
#                                and pages serialize fully so a build can be
#                                loaded without its parent (the chain is metadata
#                                only). Use load_chain() to walk parent_build_id
#                                pointers when full ancestry is needed.


def _serialize_value(v: PredicateValue) -> dict:
    return {
        "object": v.object,
        "object_type": v.object_type,
        "evidence_uris": list(v.evidence_uris),
        "claim_ids": list(v.claim_ids),
    }


def _serialize_section(s: PredicateSection) -> dict:
    return {
        "predicate": s.predicate,
        "values": [_serialize_value(v) for v in s.values],
        "is_contradicted": s.is_contradicted,
    }


def _serialize_page(p: EntityPage) -> dict:
    return {
        "subject_type": p.subject_type,
        "subject": p.subject,
        "page_id": p.page_id,
        "sections": [_serialize_section(s) for s in p.sections],
        "cross_references": [
            {
                "target_subject_type": r.target_subject_type,
                "target_subject": r.target_subject,
                "target_page_id": r.target_page_id,
            }
            for r in p.cross_references
        ],
        "contradiction_count": p.contradiction_count,
        "synthesized_prose": p.synthesized_prose,
    }


def _deserialize_value(d: dict) -> PredicateValue:
    return PredicateValue(
        object=d["object"],
        object_type=d["object_type"],
        evidence_uris=tuple(d["evidence_uris"]),
        claim_ids=tuple(d["claim_ids"]),
    )


def _deserialize_section(d: dict) -> PredicateSection:
    return PredicateSection(
        predicate=d["predicate"],
        values=tuple(_deserialize_value(v) for v in d["values"]),
        is_contradicted=d["is_contradicted"],
    )


def _deserialize_page(d: dict) -> EntityPage:
    return EntityPage(
        subject_type=d["subject_type"],
        subject=d["subject"],
        page_id=d["page_id"],
        sections=tuple(_deserialize_section(s) for s in d["sections"]),
        cross_references=tuple(CrossRef(**r) for r in d["cross_references"]),
        contradiction_count=d["contradiction_count"],
        synthesized_prose=d.get("synthesized_prose"),
    )


def save_build(build: KnowledgeBuild, dir: Path) -> Path:
    """Write the build to `{dir}/{build_id}.json` and update `{dir}/HEAD`.

    Returns the path of the written file. Creates `dir` if it doesn't exist.
    """
    dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "build_id": build.build_id,
        "parent_build_id": build.parent_build_id,
        "built_at": build.built_at.isoformat(),
        "claims": [c.model_dump(mode="json") for c in build.claims],
        "pages": [_serialize_page(p) for p in build.pages],
    }
    path = dir / f"{build.build_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (dir / "HEAD").write_text(build.build_id, encoding="utf-8")
    return path


def load_build(build_id: str, dir: Path) -> KnowledgeBuild:
    """Load a build from `{dir}/{build_id}.json`. Raises FileNotFoundError if missing."""
    payload = json.loads((dir / f"{build_id}.json").read_text(encoding="utf-8"))
    return KnowledgeBuild(
        build_id=payload["build_id"],
        parent_build_id=payload["parent_build_id"],
        built_at=datetime.fromisoformat(payload["built_at"]),
        claims=tuple(Claim.model_validate(c) for c in payload["claims"]),
        pages=tuple(_deserialize_page(p) for p in payload["pages"]),
    )


def load_head(dir: Path) -> KnowledgeBuild | None:
    """Load the build pointed to by `{dir}/HEAD`, or None if no HEAD exists."""
    head_path = dir / "HEAD"
    if not head_path.is_file():
        return None
    build_id = head_path.read_text(encoding="utf-8").strip()
    return load_build(build_id, dir)


def load_chain(build_id: str, dir: Path) -> list[KnowledgeBuild]:
    """Load `build_id` and walk every `parent_build_id` ancestor.

    Returns the chain oldest-first (root build at index 0, target at index -1).
    Raises FileNotFoundError if any ancestor is missing.
    """
    builds: list[KnowledgeBuild] = []
    current_id: str | None = build_id
    while current_id is not None:
        b = load_build(current_id, dir)
        builds.append(b)
        current_id = b.parent_build_id
    return list(reversed(builds))
