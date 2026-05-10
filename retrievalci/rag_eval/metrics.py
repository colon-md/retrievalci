"""Metric computation for one (question, system_answer) pair.

Two distinct families of metrics live here, both reported separately:

  * answer-citation metrics — what the GENERATED ANSWER actually cites,
    parsed from `[doc:...]` tokens in the answer text. This is what most
    RAG papers mean by "citation accuracy."

  * retrieval-source metrics — what the SYSTEM RETRIEVED before generation
    (i.e. the chunks/claims the system fed into the LLM). Useful as an
    upper bound on what the answer could have cited, but easily inflated:
    a system that returns 8 chunks gets credit if any one of them is in
    the ground-truth set, regardless of whether the answer actually used it.

The 3-way review (review-3way-synthesis.md) flagged the original
citation_precision/recall as the wrong-shape metric because it used
SystemAnswer.citations, which is populated with whatever the retriever
returned, not what the answer actually cited. Both metrics are now exposed
so the comparison is honest.

Faithfulness and relevance are NOT computed here — they need an LLM judge.
The Judge protocol (see backends/base.py) plugs in via runner.py.
"""

from __future__ import annotations

import random
import re
import statistics

from retrievalci.rag_eval.types import QAItem, RunResult, SystemAnswer

# Matches [doc:something] tokens the prompt asks the LLM to emit.
_CITED_DOC_RE = re.compile(r"\[doc:([^\]]+)\]")


def _normalize(s: str) -> str:
    return s.lower()


def must_include_match(answer_text: str, terms: tuple[str, ...]) -> float | None:
    if not terms:
        return None
    norm = _normalize(answer_text)
    hits = sum(1 for t in terms if _normalize(t) in norm)
    return hits / len(terms)


def must_not_include_violations(answer_text: str, terms: tuple[str, ...]) -> int | None:
    if not terms:
        return None
    norm = _normalize(answer_text)
    return sum(1 for t in terms if _normalize(t) in norm)


def parse_answer_citations(answer_text: str) -> set[str]:
    """Extract repo-relative source paths the answer text cited via [doc:...].

    The systems' prompts instruct the model to "cite sources by [doc:path]
    inline." This parser pulls those out. Each [doc:...] token may contain a
    chunk_id of the form `path/to/file#chunk-N`; we normalize to the file path.
    """
    out: set[str] = set()
    for match in _CITED_DOC_RE.finditer(answer_text):
        token = match.group(1).strip()
        # chunk_id format: path#chunk-N → drop the #chunk-N suffix to get the source file.
        path = token.split("#chunk-")[0]
        if path:
            out.add(path)
    return out


def precision_recall(cited: set[str], ground_truth: set[str]) -> tuple[float | None, float | None]:
    """Set-based precision / recall. None when the relevant denominator is empty."""
    if not cited and not ground_truth:
        return (None, None)
    intersect = cited & ground_truth
    precision = len(intersect) / len(cited) if cited else None
    recall = len(intersect) / len(ground_truth) if ground_truth else None
    return (precision, recall)


def paired_bootstrap_ci(
    a: list[float],
    b: list[float],
    n_resamples: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
) -> tuple[float, float]:
    """Paired bootstrap CI on mean(a) - mean(b).

    Both lists must be the same length and pre-aligned by question. Returns the
    (alpha/2, 1-alpha/2) quantile bounds of the resampled mean-difference
    distribution.

    Why paired: the same question runs through both systems, so per-question
    pairing reduces variance vs. unpaired bootstrap. We resample indices once
    per resample and compute mean(a[idx]) - mean(b[idx]).
    """
    if len(a) != len(b):
        raise ValueError(f"a/b length mismatch: {len(a)} vs {len(b)}")
    if not a:
        raise ValueError("cannot bootstrap on empty input")

    rng = random.Random(seed)
    n = len(a)
    diffs: list[float] = []
    for _ in range(n_resamples):
        idx = [rng.randrange(n) for _ in range(n)]
        diff = statistics.fmean(a[i] for i in idx) - statistics.fmean(b[i] for i in idx)
        diffs.append(diff)
    diffs.sort()
    lo_i = int(alpha / 2 * n_resamples)
    hi_i = int((1 - alpha / 2) * n_resamples) - 1
    return (diffs[lo_i], diffs[hi_i])


def _aligned_metric_values(
    rows: list[RunResult],
    system_name: str,
    metric: str,
    question_ids: list[str],
) -> list[float] | None:
    """Pull metric values for one system aligned to question_ids order.

    Returns None if any cell is None — a paired bootstrap can't span Nones
    without ad-hoc imputation, and silent imputation would mislead readers.
    """
    by_q = {r.question_id: r for r in rows if r.system == system_name}
    out: list[float] = []
    for qid in question_ids:
        r = by_q.get(qid)
        if r is None:
            return None
        v = getattr(r, metric, None)
        if v is None:
            return None
        out.append(float(v))
    return out


def compute_row(system_name: str, question: QAItem, answer: SystemAnswer) -> RunResult:
    truth = set(question.ground_truth_citations)

    # Answer-citation metrics: parse [doc:...] tokens out of the LLM's actual
    # answer text. Empty when the LLM didn't cite anything (or mock backends
    # that don't emit citations).
    answer_cited = parse_answer_citations(answer.answer)
    ans_p, ans_r = precision_recall(answer_cited, truth)

    # Retrieval-source metrics: the systems' returned `citations` field is
    # populated with whatever they retrieved before generation. This measures
    # the retriever, not the answerer.
    retrieved = {c.source_path for c in answer.citations}
    ret_p, ret_r = precision_recall(retrieved, truth)

    return RunResult(
        system=system_name,
        question_id=question.id,
        tier=question.tier,
        answer=answer,
        must_include_match=must_include_match(answer.answer, question.must_include_terms),
        must_not_include_violations=must_not_include_violations(
            answer.answer, question.must_not_include_terms
        ),
        answer_citation_precision=ans_p,
        answer_citation_recall=ans_r,
        retrieval_source_precision=ret_p,
        retrieval_source_recall=ret_r,
        answer_length_chars=len(answer.answer),
        refused=answer.refused,
    )
