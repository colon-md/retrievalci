"""Closed predicate vocabulary loader.

Parses `searchtrace/rag_eval/schemas/predicates.yml` into a `PredicateVocabulary` that maps
LLM-emitted predicate strings (canonical names or aliases) to their canonical
form. The wiki-pages projection uses this to collapse `is_deprecated` /
`marked_deprecated` / `EOL` into one section per entity.

Unknown predicates return None from `canonicalize` — the caller decides whether
to drop the claim, route to a proposal queue, or pass through verbatim. This
package is unopinionated about that policy.

Matching is case-insensitive on input; canonical names are returned in their
original snake_case form.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class PredicateDef:
    name: str
    arity: int
    aliases: tuple[str, ...]
    subject_type: str | None
    object_type: str | None
    transitive: bool


class PredicateVocabulary:
    """Closed vocabulary of allowed predicates + aliases.

    Build from a YAML file with `from_yaml_file`, or pass parsed defs directly.
    Lookups are O(1) via an internal alias→canonical index.
    """

    def __init__(self, predicates: list[PredicateDef]) -> None:
        self._defs: dict[str, PredicateDef] = {p.name: p for p in predicates}
        # Build alias index — lowercased aliases AND canonical names map to the
        # canonical name (in original case).
        self._alias_to_canonical: dict[str, str] = {}
        for p in predicates:
            self._alias_to_canonical[p.name.lower()] = p.name
            for alias in p.aliases:
                self._alias_to_canonical[alias.lower()] = p.name

    @classmethod
    def from_yaml_file(cls, path: Path) -> PredicateVocabulary:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw_predicates = data.get("predicates", {})
        defs: list[PredicateDef] = []
        for name, body in raw_predicates.items():
            defs.append(
                PredicateDef(
                    name=name,
                    arity=int(body.get("arity", 2)),
                    aliases=tuple(body.get("aliases", []) or ()),
                    subject_type=body.get("subject_type"),
                    object_type=body.get("object_type"),
                    transitive=bool(body.get("transitive", False)),
                )
            )
        return cls(defs)

    @property
    def predicate_names(self) -> tuple[str, ...]:
        return tuple(self._defs.keys())

    def get(self, name: str) -> PredicateDef | None:
        return self._defs.get(name)

    def is_known(self, predicate: str) -> bool:
        return predicate.lower() in self._alias_to_canonical

    def canonicalize(self, predicate: str) -> str | None:
        """Return the canonical name for `predicate` (an exact name or alias),
        or None if the input is unknown to this vocabulary."""
        return self._alias_to_canonical.get(predicate.lower())
