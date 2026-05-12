"""Wiki-pages system — Karpathy's compounding-entity-page projection.

Aggregates `retrievalci.rag_eval.claims.Claim` rows into entity pages by `(subject_type,
subject)`, deduplicates values within each `(subject, predicate)` group, flags
contradictions when distinct objects assert the same predicate, and emits
cross-references when a value's `object` matches another page's `subject`.

The load-bearing Karpathy win is `synthesize_pages()`: one LLM call per entity
that turns the structured listing of facts into a coherent wiki-style prose
summary. This pays the synthesis cost ONCE at ingest, so 1000 queries read
the same pre-synthesized page. The prose is prefixed to the rendered Markdown;
the structured listing remains as the citation-bearing source of truth.

Pages render as Markdown; retrieval embeds the whole rendered page.

What this DOES NOT do (deferred):
  - Predicate canonicalization against `retrievalci/rag_eval/schemas/predicates.yml` —
    LLM-extracted predicate strings are used verbatim.
  - Append-only page history versioned to `KnowledgeBuild`.
  - Streaming aggregation (claim → existing page merge). Pages are re-projected
    from the full claim set on each system construction.
"""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from retrievalci.rag_eval.backends.base import Embedder, GenerationRequest, Generator
from retrievalci.rag_eval.claims import Claim
from retrievalci.rag_eval.predicates import PredicateVocabulary
from retrievalci.rag_eval.types import Citation, SystemAnswer

SynthesisMode = Literal["prose", "tag_list"]


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _doc_path_from_uri(uri: str) -> str:
    """Strip the `chunk://` prefix to get the chunk-id used in [doc:...] citations."""
    return uri.removeprefix("chunk://")


@dataclass(frozen=True)
class PredicateValue:
    """One observed value for a (subject, predicate) pair, plus its evidence.

    Re-asserted facts (same (object, object_type) from multiple chunks) collapse
    into one `PredicateValue` whose `evidence_uris` and `claim_ids` accumulate.
    """

    object: str | None
    object_type: str | None
    evidence_uris: tuple[str, ...]
    claim_ids: tuple[str, ...]


@dataclass(frozen=True)
class PredicateSection:
    """Grouping of values for one predicate on one subject.

    `is_contradicted` ↔ `len(values) > 1`. The render flags the section.
    """

    predicate: str
    values: tuple[PredicateValue, ...]
    is_contradicted: bool


@dataclass(frozen=True)
class CrossRef:
    """A backref from this page to another entity's page."""

    target_subject_type: str
    target_subject: str
    target_page_id: str


@dataclass(frozen=True)
class EntityPage:
    """All claims about one entity, aggregated from many sources.

    `synthesized_prose` is the LLM-written wiki summary populated by
    `synthesize_pages`. None until synthesis runs; structured listing alone
    when None.
    """

    subject_type: str
    subject: str
    page_id: str
    sections: tuple[PredicateSection, ...]
    cross_references: tuple[CrossRef, ...]
    contradiction_count: int
    synthesized_prose: str | None = None

    @property
    def name(self) -> str:
        return f"{self.subject_type}:{self.subject}"

    def _structured_lines(self) -> list[str]:
        lines: list[str] = []
        for section in self.sections:
            if section.is_contradicted:
                lines.append(
                    f"## ⚠ {section.predicate} "
                    f"(contradiction: {len(section.values)} values)"
                )
            else:
                lines.append(f"## {section.predicate}")
            for v in section.values:
                cites = " ".join(f"[doc:{_doc_path_from_uri(u)}]" for u in v.evidence_uris)
                if v.object is None:
                    lines.append(f"- (asserted) {cites}".rstrip())
                else:
                    lines.append(f"- {v.object} {cites}".rstrip())
            lines.append("")
        if self.cross_references:
            lines.append("## See also")
            for ref in self.cross_references:
                lines.append(f"- {ref.target_subject} ({ref.target_subject_type})")
            lines.append("")
        return lines

    def render_markdown(self, *, include_prose: bool = True) -> str:
        """Render the page Markdown.

        `include_prose=True` (default): prepend synthesized prose if present.
        `include_prose=False`: structured listing only — used by the mechanism-
        isolation ablation to test whether synthesis-derived prose affects
        retrieval quality vs. answer-context quality independently.
        """
        lines: list[str] = [f"# {self.subject} ({self.subject_type})", ""]
        if include_prose and self.synthesized_prose:
            lines.append(self.synthesized_prose.strip())
            lines.append("")
            lines.append("## Sources")
            lines.append("")
        lines.extend(self._structured_lines())
        return "\n".join(lines).rstrip() + "\n"


def _evidence_uris_for_claim(claim: Claim) -> tuple[str, ...]:
    """All evidence URIs across all proof sets, deduped, in encounter order."""
    seen: set[str] = set()
    out: list[str] = []
    for ps in claim.proof_sets:
        for ev in ps.sources:
            if ev.evidence_uri in seen:
                continue
            seen.add(ev.evidence_uri)
            out.append(ev.evidence_uri)
    return tuple(out)


def project_pages(
    claims: Iterable[Claim],
    *,
    vocabulary: PredicateVocabulary | None = None,
) -> list[EntityPage]:
    """Project a flat list of `Claim` rows into entity pages.

    Two-pass:
      1. Bucket claims by (subject_type, subject); within each bucket group by
         predicate; within each predicate dedupe values on (object, object_type).
         Cross-references stay empty.
      2. Walk every value across all pages — if `value.object` matches some
         other page's `subject`, record a `CrossRef` from this page to that one.

    If `vocabulary` is supplied, predicate strings are canonicalized before
    bucketing. `is_deprecated` / `marked_deprecated` / `EOL` collapse into a
    single canonical section. Unknown predicates pass through verbatim — this
    function does not gate on vocabulary membership.
    """
    by_entity: dict[tuple[str, str], list[Claim]] = defaultdict(list)
    for c in claims:
        by_entity[(c.subject_type, c.subject)].append(c)

    def _canon(predicate: str) -> str:
        if vocabulary is None:
            return predicate
        canonical = vocabulary.canonicalize(predicate)
        return canonical if canonical is not None else predicate

    pages_no_xref: list[EntityPage] = []
    for (subject_type, subject), entity_claims in sorted(by_entity.items()):
        by_pred: dict[str, list[Claim]] = defaultdict(list)
        for c in entity_claims:
            by_pred[_canon(c.predicate)].append(c)

        sections: list[PredicateSection] = []
        for pred, pred_claims in sorted(by_pred.items()):
            values_map: dict[tuple[str | None, str | None], PredicateValue] = {}
            for c in pred_claims:
                key = (c.object, c.object_type)
                claim_uris = _evidence_uris_for_claim(c)
                if key in values_map:
                    existing = values_map[key]
                    seen_uris = set(existing.evidence_uris)
                    merged_uris = list(existing.evidence_uris) + [
                        u for u in claim_uris if u not in seen_uris
                    ]
                    values_map[key] = PredicateValue(
                        object=c.object,
                        object_type=c.object_type,
                        evidence_uris=tuple(merged_uris),
                        claim_ids=(*existing.claim_ids, c.claim_id),
                    )
                else:
                    values_map[key] = PredicateValue(
                        object=c.object,
                        object_type=c.object_type,
                        evidence_uris=claim_uris,
                        claim_ids=(c.claim_id,),
                    )
            values_tuple = tuple(values_map.values())
            sections.append(
                PredicateSection(
                    predicate=pred,
                    values=values_tuple,
                    is_contradicted=len(values_tuple) > 1,
                )
            )

        sections_tuple = tuple(sections)
        pages_no_xref.append(
            EntityPage(
                subject_type=subject_type,
                subject=subject,
                page_id=_sha256_hex(f"{subject_type}|{subject}"),
                sections=sections_tuple,
                cross_references=(),
                contradiction_count=sum(1 for s in sections_tuple if s.is_contradicted),
            )
        )

    return resolve_cross_references(pages_no_xref)


def resolve_cross_references(pages: list[EntityPage]) -> list[EntityPage]:
    """Second-pass cross-reference resolver.

    Builds a {subject → page} index, then for every page collects unique
    `CrossRef` entries pointing to other pages whose `subject` appears as a
    value's `object`. Self-references and duplicates are filtered. Preserves
    each page's `synthesized_prose` — cross-ref updates do NOT trigger
    re-synthesis (the prose talks about the entity's facts, not its links).
    """
    subject_to_page: dict[str, EntityPage] = {p.subject: p for p in pages}
    out: list[EntityPage] = []
    for page in pages:
        seen_targets: set[str] = set()
        refs: list[CrossRef] = []
        for section in page.sections:
            for v in section.values:
                if v.object is None:
                    continue
                target = subject_to_page.get(v.object)
                if target is None or target.page_id == page.page_id:
                    continue
                if target.page_id in seen_targets:
                    continue
                seen_targets.add(target.page_id)
                refs.append(
                    CrossRef(
                        target_subject_type=target.subject_type,
                        target_subject=target.subject,
                        target_page_id=target.page_id,
                    )
                )
        out.append(
            EntityPage(
                subject_type=page.subject_type,
                subject=page.subject,
                page_id=page.page_id,
                sections=page.sections,
                cross_references=tuple(refs),
                contradiction_count=page.contradiction_count,
                synthesized_prose=page.synthesized_prose,
            )
        )
    return out


_SYNTHESIS_PROMPT = """\
Write a 2-4 paragraph wiki-style summary of the entity below using ONLY the
facts listed. Surface any contradictions explicitly when sources disagree.
Do not invent facts not in the listing. Be concise and authoritative.

Entity: {subject_type}: {subject}

Facts:
{facts}

Wiki summary:"""

# Optimised for embedder fuel, not human prose. Empirically, prior ablations
# (STATUS.md round 6) showed the LLM doesn't read synthesized prose at answer
# time; the win is entirely in the embedding text. Tag-list mode trades the
# 2-4 paragraph form for ~15-25 terms — same retrieval-time mechanism at
# roughly one-fifth the output token cost per page.
_TAG_LIST_PROMPT = """\
List 15-25 distinct terms, phrases, aliases, and related concepts that a
search query about this entity might use. Include identifier variants,
synonyms, and key relationships drawn from the facts. One term per line,
no numbering, no prose, no explanation.

Entity: {subject_type}: {subject}

Facts:
{facts}

Terms:"""


_PROMPT_BY_MODE: dict[SynthesisMode, str] = {
    "prose": _SYNTHESIS_PROMPT,
    "tag_list": _TAG_LIST_PROMPT,
}
_MAX_TOKENS_BY_MODE: dict[SynthesisMode, int] = {
    "prose": 600,
    "tag_list": 200,
}


def synthesize_pages(
    pages: list[EntityPage],
    generator: Generator,
    *,
    synthesis_mode: SynthesisMode = "prose",
) -> list[EntityPage]:
    """One LLM call per page → fill `synthesized_prose`.

    `synthesis_mode="prose"` (default, preserves prior behavior) writes a
    paragraph-form wiki summary. `synthesis_mode="tag_list"` writes a short
    list of search terms — optimised for embedding-text enrichment without
    the paragraph-length cost.

    Pages with no sections (which `project_pages` shouldn't produce, but guard
    anyway) are returned unchanged.
    """
    prompt_template = _PROMPT_BY_MODE[synthesis_mode]
    max_tokens = _MAX_TOKENS_BY_MODE[synthesis_mode]
    out: list[EntityPage] = []
    for page in pages:
        if not page.sections:
            out.append(page)
            continue
        facts = "\n".join(page._structured_lines()).rstrip()
        prompt = prompt_template.format(
            subject_type=page.subject_type,
            subject=page.subject,
            facts=facts,
        )
        resp = generator.generate(
            GenerationRequest(prompt=prompt, max_output_tokens=max_tokens)
        )
        prose = resp.text.strip()
        out.append(
            EntityPage(
                subject_type=page.subject_type,
                subject=page.subject,
                page_id=page.page_id,
                sections=page.sections,
                cross_references=page.cross_references,
                contradiction_count=page.contradiction_count,
                synthesized_prose=prose if prose else None,
            )
        )
    return out


_QUERY_PROMPT = """\
Answer the question using ONLY the retrieved entity pages. Each page contains a
synthesized wiki summary plus the source facts. When pages flag a contradiction
(⚠), surface it explicitly in your answer. Cite source documents by [doc:path]
inline. If the pages don't contain the answer, say so.

Question: {question}

Retrieved pages:
{context}

Answer:"""


def _page_claim_count(page: EntityPage) -> int:
    """Total number of distinct underlying claims in a page (sum across sections)."""
    return sum(len(v.claim_ids) for s in page.sections for v in s.values)


class WikiPagesSystem:
    def __init__(
        self,
        embedder: Embedder,
        generator: Generator,
        claims: Iterable[Claim],
        top_k: int = 4,
        *,
        synthesize: bool = True,
        vocabulary: PredicateVocabulary | None = None,
        min_claims_per_indexed_page: int = 1,
        embed_uses_prose: bool = True,
        answer_uses_prose: bool = True,
        synthesis_mode: SynthesisMode = "prose",
    ) -> None:
        """`min_claims_per_indexed_page=2` drops singleton pages from the
        retrieval index (per `eval/PRE_REGISTRATION.md` Tier A clause). Pages
        below the threshold remain in `self._build` (and `self._pages`) for
        completeness — they're just not embedded for top-k retrieval.

        `embed_uses_prose` / `answer_uses_prose`: independently control whether
        synthesized prose appears in the embedding text vs. the answer-time
        prompt. Default both True (the standard wiki pipeline). Set False on
        either to run the mechanism-isolation ablation that decouples synthesis
        contribution to retrieval-quality vs. answer-context-quality.
        Requires `synthesize=True` to have any effect.

        `synthesis_mode="tag_list"` replaces the paragraph-form summary with
        a 15-25-term list optimised for embedding-text enrichment. Same
        retrieval-time mechanism, ~5x cheaper per page in output tokens.
        """
        self._embedder = embedder
        self._generator = generator
        self._top_k = top_k
        self._vocabulary = vocabulary
        self._min_claims_per_indexed_page = min_claims_per_indexed_page
        self._embed_uses_prose = embed_uses_prose
        self._answer_uses_prose = answer_uses_prose
        self._synthesis_mode: SynthesisMode = synthesis_mode
        # The synthesis path goes through KnowledgeBuild so future calls to
        # `self.merge(new_claims)` get incremental compounding for free. The
        # synthesize=False path stays cheap (no LLM, no build) — useful in
        # tests + mock-backed runs.
        if synthesize:
            from retrievalci.rag_eval.claims.builds import merge_claims_into_build

            self._build = merge_claims_into_build(
                None,
                list(claims),
                generator,
                vocabulary=vocabulary,
                synthesis_mode=synthesis_mode,
            )
            self._pages = list(self._build.pages)
        else:
            self._build = None
            self._pages = project_pages(claims, vocabulary=vocabulary)
        # Index only pages that meet the minimum-claim threshold. The full page
        # list stays in `self._pages` for inspection / KnowledgeBuild fidelity.
        self._indexed_pages = [
            p for p in self._pages if _page_claim_count(p) >= self._min_claims_per_indexed_page
        ]
        self._index = (
            self._embedder.embed_batch(
                [
                    p.render_markdown(include_prose=self._embed_uses_prose)
                    for p in self._indexed_pages
                ]
            )
            if self._indexed_pages
            else []
        )

    def merge(self, new_claims: list[Claim]) -> None:
        """Incrementally fold new claims into the existing pages.

        Re-synthesizes only modified entities, then re-embeds the full page
        index. Requires `synthesize=True` at construction (the build is None
        otherwise).
        """
        if self._build is None:
            raise RuntimeError(
                "WikiPagesSystem.merge requires synthesize=True at construction"
            )
        from retrievalci.rag_eval.claims.builds import merge_claims_into_build

        self._build = merge_claims_into_build(
            self._build,
            new_claims,
            self._generator,
            vocabulary=self._vocabulary,
            synthesis_mode=self._synthesis_mode,
        )
        self._pages = list(self._build.pages)
        self._indexed_pages = [
            p for p in self._pages if _page_claim_count(p) >= self._min_claims_per_indexed_page
        ]
        self._index = (
            self._embedder.embed_batch(
                [
                    p.render_markdown(include_prose=self._embed_uses_prose)
                    for p in self._indexed_pages
                ]
            )
            if self._indexed_pages
            else []
        )

    @property
    def name(self) -> str:
        return "wiki_pages"

    @property
    def page_count(self) -> int:
        return len(self._pages)

    def answer(self, question: str) -> SystemAnswer:
        t0 = time.perf_counter()
        if not self._indexed_pages:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return SystemAnswer(
                answer="(no entity pages projected from corpus; system cannot answer)",
                citations=(),
                latency_ms=latency_ms,
                tokens_used=0,
                refused=True,
                refusal_reason="empty_page_index",
            )

        q_vec = self._embedder.embed(question)

        def cosine(v: list[float]) -> float:
            return sum(x * y for x, y in zip(q_vec, v, strict=True))

        scored = sorted(((cosine(v), i) for i, v in enumerate(self._index)), reverse=True)
        retrieved = [self._indexed_pages[i] for _, i in scored[: self._top_k]]

        context = "\n\n".join(
            p.render_markdown(include_prose=self._answer_uses_prose) for p in retrieved
        )
        prompt = _QUERY_PROMPT.format(question=question, context=context)
        resp = self._generator.generate(GenerationRequest(prompt=prompt))

        latency_ms = (time.perf_counter() - t0) * 1000.0

        seen_paths: set[str] = set()
        cits: list[Citation] = []
        for page in retrieved:
            for section in page.sections:
                for v in section.values:
                    for uri in v.evidence_uris:
                        chunk_id = _doc_path_from_uri(uri)
                        source_path = chunk_id.split("#", 1)[0]
                        if source_path in seen_paths:
                            continue
                        seen_paths.add(source_path)
                        cits.append(
                            Citation(
                                source_path=source_path,
                                span=f"{page.name}: {section.predicate}"[:240],
                            )
                        )
        return SystemAnswer(
            answer=resp.text,
            citations=tuple(cits),
            latency_ms=latency_ms,
            tokens_used=resp.tokens_used,
        )
