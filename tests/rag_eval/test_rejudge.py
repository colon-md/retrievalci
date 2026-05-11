"""Tests for `rejudge_report` — judge an existing report without re-running generators.

This path lets a Claude judge grade Gemini-generated answers (or vice versa)
without re-running the underlying generators, which is critical when the
generator's daily quota is constrained.
"""

from __future__ import annotations

from retrievalci.rag_eval.backends.base import JudgeScore
from retrievalci.rag_eval.runner import rejudge_report
from retrievalci.rag_eval.types import (
    Citation,
    ComparisonReport,
    QAItem,
    RunResult,
    SystemAnswer,
)


class _FakeJudge:
    """Records every judge call and returns deterministic scores.

    Lets the test assert how many calls happened and with what arguments,
    without needing a real API key.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def faithfulness(
        self, question: str, answer: str, evidence: str, ground_truth: str
    ) -> JudgeScore:
        self.calls.append(
            {
                "method": "faithfulness",
                "question": question,
                "answer": answer,
                "evidence": evidence,
                "ground_truth": ground_truth,
            }
        )
        return JudgeScore(score=4.0, rationale="fake faithfulness")

    def relevance(self, question: str, answer: str) -> JudgeScore:
        self.calls.append({"method": "relevance", "question": question, "answer": answer})
        return JudgeScore(score=5.0, rationale="fake relevance")


def _make_report() -> tuple[ComparisonReport, list[QAItem]]:
    """Two questions, one system, with one row refused."""
    questions = [
        QAItem(
            id="q1",
            tier="single_hop",
            question="What is X?",
            ground_truth_answer="The answer is alpha.",
            ground_truth_citations=("docs/a.md",),
        ),
        QAItem(
            id="q2",
            tier="single_hop",
            question="What is Y?",
            ground_truth_answer="The answer is beta.",
            ground_truth_citations=("docs/b.md",),
            unanswerable=True,
        ),
    ]
    rows = [
        RunResult(
            system="bm25",
            question_id="q1",
            tier="single_hop",
            answer=SystemAnswer(
                answer="It is alpha.",
                citations=(Citation(source_path="docs/a.md", span="alpha excerpt"),),
                latency_ms=1.0,
                tokens_used=10,
            ),
            faithfulness=None,
            relevance=None,
        ),
        RunResult(
            system="bm25",
            question_id="q2",
            tier="single_hop",
            answer=SystemAnswer(
                answer="",
                citations=(),
                latency_ms=1.0,
                tokens_used=0,
                refused=True,
                refusal_reason="abstained",
            ),
            refused=True,
        ),
    ]
    report = ComparisonReport(
        systems=("bm25",),
        n_questions=2,
        n_per_tier={"single_hop": 2, "multi_hop": 0, "contradiction": 0},
        rows=rows,
    )
    return report, questions


def test_rejudge_populates_judge_metrics_on_answered_rows() -> None:
    report, questions = _make_report()
    judge = _FakeJudge()
    updated = rejudge_report(report, questions, judge)

    answered = next(r for r in updated.rows if r.question_id == "q1")
    assert answered.faithfulness == 4.0
    assert answered.relevance == 5.0


def test_rejudge_skips_refused_rows() -> None:
    """Refused rows should not be judged — matches live `run_eval` semantics."""
    report, questions = _make_report()
    judge = _FakeJudge()
    rejudge_report(report, questions, judge)

    # _FakeJudge records every call. Refused row would have triggered 2 more.
    method_calls = [c["method"] for c in judge.calls]
    assert method_calls.count("faithfulness") == 1
    assert method_calls.count("relevance") == 1


def test_rejudge_evidence_matches_run_eval_format() -> None:
    """Evidence string must be `\\n`.join(c.span or c.source_path)."""
    report, questions = _make_report()
    judge = _FakeJudge()
    rejudge_report(report, questions, judge)

    faith_call = next(c for c in judge.calls if c["method"] == "faithfulness")
    # The answered row has one citation with span "alpha excerpt".
    assert faith_call["evidence"] == "alpha excerpt"


def test_rejudge_falls_back_to_source_path_when_span_missing() -> None:
    questions = [
        QAItem(
            id="q",
            tier="single_hop",
            question="?",
            ground_truth_answer="x",
            ground_truth_citations=("docs/x.md",),
        )
    ]
    row = RunResult(
        system="bm25",
        question_id="q",
        tier="single_hop",
        answer=SystemAnswer(
            answer="x",
            citations=(Citation(source_path="docs/x.md", span=None),),
            latency_ms=1.0,
            tokens_used=5,
        ),
    )
    report = ComparisonReport(
        systems=("bm25",),
        n_questions=1,
        n_per_tier={"single_hop": 1, "multi_hop": 0, "contradiction": 0},
        rows=[row],
    )
    judge = _FakeJudge()
    rejudge_report(report, questions, judge)
    faith_call = next(c for c in judge.calls if c["method"] == "faithfulness")
    assert faith_call["evidence"] == "docs/x.md"


def test_rejudge_re_aggregates_by_system_metric() -> None:
    """After rejudge, `by_system_metric` must reflect the new judge scores."""
    report, questions = _make_report()
    judge = _FakeJudge()
    updated = rejudge_report(report, questions, judge)

    # Only one answered row → mean equals the single score (4.0 and 5.0).
    bm25_metrics = updated.by_system_metric["bm25"]
    assert bm25_metrics["faithfulness"] == 4.0
    assert bm25_metrics["relevance"] == 5.0


def test_rejudge_preserves_retrieval_metrics() -> None:
    """The original retrieval scores must survive — rejudge only touches judge fields."""
    report, questions = _make_report()
    # Pre-set retrieval metric on the answered row to verify it isn't blown away.
    rows = list(report.rows)
    rows[0] = rows[0].model_copy(
        update={"retrieval_source_recall": 1.0, "retrieval_source_precision": 0.5}
    )
    report = report.model_copy(update={"rows": rows})

    updated = rejudge_report(report, questions, _FakeJudge())
    answered = next(r for r in updated.rows if r.question_id == "q1")
    assert answered.retrieval_source_recall == 1.0
    assert answered.retrieval_source_precision == 0.5


def test_rejudge_warns_on_missing_question_id(capsys) -> None:
    """A row whose question_id isn't in the questions list is kept as-is with a warning."""
    report, questions = _make_report()
    # Drop one question so its row becomes a missing-qid case.
    rejudge_report(report, [questions[0]], _FakeJudge())
    out = capsys.readouterr().out
    assert "warning" in out
    assert "q2" in out
