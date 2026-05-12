"""Tests for the distillation-cost ablation primitives.

Covers:
  - extract_entity_terms: regex captures CamelCase / ACRONYM / kebab-snake
  - DenseRAGTermPadSystem: padding affects embed-time index but not the
    answer-time prompt context
  - synthesize_pages(synthesis_mode="tag_list"): tag-list prompt and tighter
    output-token cap are used; default still routes through the prose prompt
"""

from __future__ import annotations

from retrievalci.rag_eval.backends.base import GenerationRequest, GenerationResponse
from retrievalci.rag_eval.backends.mock import MockEmbedder
from retrievalci.rag_eval.corpus import Chunk
from retrievalci.rag_eval.systems.dense_rag_termpad import (
    DenseRAGTermPadSystem,
    extract_entity_terms,
)
from retrievalci.rag_eval.systems.wiki_pages import (
    EntityPage,
    PredicateSection,
    PredicateValue,
    synthesize_pages,
)


class _RecordingGenerator:
    """Records every prompt + max_output_tokens passed in. Returns a fixed reply."""

    model_id = "recording-gen"

    def __init__(self, reply: str = "ok") -> None:
        self._reply = reply
        self.calls: list[tuple[str, int]] = []

    def generate(self, req: GenerationRequest) -> GenerationResponse:
        self.calls.append((req.prompt, req.max_output_tokens))
        return GenerationResponse(text=self._reply, tokens_used=1)


def test_extract_entity_terms_covers_three_patterns() -> None:
    text = (
        "The Kubernetes Pod runs on the AOSS data plane. "
        "We use bge-large-en for embeddings and snake_case_id for keys."
    )
    terms = extract_entity_terms(text)
    # CamelCase
    assert "Kubernetes" in terms
    # ACRONYM (2+ chars)
    assert "AOSS" in terms
    # kebab-case and snake_case
    assert "bge-large-en" in terms
    assert "snake_case_id" in terms
    # Case-insensitive dedupe — same lower-form appears at most once.
    lowers = [t.lower() for t in terms]
    assert len(lowers) == len(set(lowers))


def test_extract_entity_terms_skips_plain_capitalized_words() -> None:
    """Single capitalized words at sentence start aren't entity-like enough to keep."""
    terms = extract_entity_terms("The runs. A simple sentence with no entities.")
    assert terms == []


def test_termpad_changes_embed_text_only() -> None:
    chunks = [
        Chunk(
            source_path="docs/k8s.md",
            chunk_index=0,
            text="The Kubernetes Pod runs on bge-large-en.",
        ),
        Chunk(
            source_path="docs/other.md",
            chunk_index=0,
            # All-lowercase fragment with no acronyms or kebab/snake — the
            # regex matches nothing, so _padded_text must return text unchanged.
            text="a fragment of lowercase prose with nothing to extract.",
        ),
    ]
    embedder = MockEmbedder()
    generator = _RecordingGenerator()
    system = DenseRAGTermPadSystem(embedder, generator, chunks, top_k=1, padding_factor=5)

    # First chunk has entity terms → padded text differs from raw.
    padded = system._padded_text(chunks[0].text)
    assert padded != chunks[0].text
    assert "Kubernetes Kubernetes" in padded  # repetition is observable
    assert "bge-large-en bge-large-en" in padded

    # Second chunk has no entities → padding is a no-op.
    assert system._padded_text(chunks[1].text) == chunks[1].text

    # Answer-time prompt must contain raw chunk text, NOT the padding tail.
    system.answer("what does the Pod run on?")
    assert len(generator.calls) == 1
    sent_prompt = generator.calls[0][0]
    assert "The Kubernetes Pod runs on bge-large-en." in sent_prompt
    # Padding repetition should NOT leak into the LLM context.
    assert "Kubernetes Kubernetes Kubernetes" not in sent_prompt


def _entity_page_one_fact() -> EntityPage:
    return EntityPage(
        subject_type="service",
        subject="payments",
        page_id="page-payments",
        sections=(
            PredicateSection(
                predicate="depends_on",
                values=(
                    PredicateValue(
                        object="postgres",
                        object_type="database",
                        evidence_uris=("chunk://docs/a.md#0",),
                        claim_ids=("c1",),
                    ),
                ),
                is_contradicted=False,
            ),
        ),
        cross_references=(),
        contradiction_count=0,
    )


def test_synthesize_pages_tag_list_uses_distinct_prompt_and_smaller_budget() -> None:
    page = _entity_page_one_fact()
    gen = _RecordingGenerator(reply="kube-pod\nstateful service\ndepends_on postgres")
    synthesize_pages([page], gen, synthesis_mode="tag_list")

    assert len(gen.calls) == 1
    prompt, max_tokens = gen.calls[0]
    # Tag-list prompt has its own signature phrasing.
    assert "List 15-25" in prompt
    assert "One term per line" in prompt
    # Prose-prompt phrasing must NOT appear in tag_list mode.
    assert "2-4 paragraph wiki-style summary" not in prompt
    # Tag-list mode uses the tighter output budget.
    assert max_tokens == 200


def test_synthesize_pages_defaults_to_prose() -> None:
    page = _entity_page_one_fact()
    gen = _RecordingGenerator()
    synthesize_pages([page], gen)  # default synthesis_mode

    assert len(gen.calls) == 1
    prompt, max_tokens = gen.calls[0]
    assert "2-4 paragraph wiki-style summary" in prompt
    assert max_tokens == 600
