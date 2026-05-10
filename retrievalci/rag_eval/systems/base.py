"""System protocol — every comparable system implements this surface."""

from __future__ import annotations

from typing import Protocol

from retrievalci.rag_eval.types import SystemAnswer


class System(Protocol):
    """A queryable system under evaluation.

    The corpus is provided at index time (constructor or .index()); the harness
    only calls .answer() during the evaluation loop.
    """

    @property
    def name(self) -> str: ...

    def answer(self, question: str) -> SystemAnswer: ...
