from __future__ import annotations

import json

import pytest
from searchtrace.trace_eval import (
    build_index,
    check_metric_gates,
    check_metric_regressions,
    evaluate_traces,
    load_corpus,
    load_traces,
    render_markdown_report,
    write_outputs,
)


def _write_jsonl(path, rows) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def test_trace_eval_detects_bridge_answer_gain(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.jsonl"
    traces_path = tmp_path / "traces.jsonl"
    _write_jsonl(
        corpus_path,
        [
            {
                "doc_id": "doc_target",
                "text": "Ada Lab headquarters are in Paris. Ada Lab location is Paris.",
            },
            {
                "doc_id": "doc_prev",
                "text": "The Widget inventor worked at the research institute.",
            },
            {
                "doc_id": "doc_false",
                "text": "Beta Corp headquarters are in Rome.",
            },
        ],
    )
    _write_jsonl(
        traces_path,
        [
            {
                "session_id": "s1",
                "turn_id": 2,
                "user_question": (
                    "Where are the headquarters of the place where the Widget inventor "
                    "worked?"
                ),
                "retrieval_query": "Widget inventor worked headquarters",
                "retrieved_doc_ids": ["doc_prev"],
                "gold_doc_ids": ["doc_target"],
                "agent_state": {
                    "previous_answers": ["Ada Lab"],
                    "previous_doc_ids": ["doc_prev"],
                    "false_lead_doc_ids": ["doc_false"],
                    "public_trace": ["Asked who invented Widget. Answer found: Ada Lab."],
                },
            }
        ],
    )

    traces = load_traces(traces_path)
    corpus = load_corpus(corpus_path)
    per_turn, metrics = evaluate_traces(
        traces,
        corpus,
        policies=("query_only", "last_answer_x3", "recorded"),
        k=1,
    )

    assert len(per_turn) == 3
    assert metrics["policies"]["query_only"]["recall_at_5"] == 0.0
    assert metrics["policies"]["recorded"]["stale_at_1"] == 1.0
    assert metrics["policies"]["last_answer_x3"]["recall_at_5"] == 1.0


def test_bm25_empty_query_returns_no_results() -> None:
    index = build_index([])
    index.fit(["doc_a"], ["Ada Lab headquarters"])

    assert index.query("???", k=1) == []


def test_bm25_ties_preserve_corpus_order() -> None:
    index = build_index([])
    index.fit(["doc_a", "doc_b"], ["same token", "same token"])

    assert [doc_id for doc_id, _ in index.query("same", k=2)] == ["doc_a", "doc_b"]


def test_write_outputs_creates_report(tmp_path) -> None:
    per_turn = [
        {
            "session_id": "s1",
            "turn_id": "2",
            "policy": "query_only",
            "query_text": "Where is the Widget institute headquartered?",
            "ranked_ids": ["doc_prev"],
            "gold_ids": ["doc_target"],
            "recall_at_5": 0.0,
            "recall_at_k": 0.0,
            "zero_recall_at_k": True,
            "drift_at_1": True,
            "stale_at_1": False,
            "false_lead_at_k": False,
        },
        {
            "session_id": "s1",
            "turn_id": "2",
            "policy": "last_answer_x3",
            "query_text": "Ada Lab Ada Lab Ada Lab Where is it headquartered?",
            "ranked_ids": ["doc_target"],
            "gold_ids": ["doc_target"],
            "recall_at_5": 1.0,
            "recall_at_k": 1.0,
            "zero_recall_at_k": False,
            "drift_at_1": False,
            "stale_at_1": False,
            "false_lead_at_k": False,
        },
    ]
    metrics = {
        "policies": {
            "query_only": {
                "n": 1,
                "recall_at_5": 0.0,
                "recall_at_k": 0.0,
                "zero_recall_at_k": 1.0,
                "drift_at_1": 1.0,
                "stale_at_1": 0.0,
                "false_lead_at_k": 0.0,
            },
            "last_answer_x3": {
                "n": 1,
                "recall_at_5": 1.0,
                "recall_at_k": 1.0,
                "zero_recall_at_k": 0.0,
                "drift_at_1": 0.0,
                "stale_at_1": 0.0,
                "false_lead_at_k": 0.0,
            },
        }
    }

    out_dir = tmp_path / "report"
    write_outputs(per_turn, metrics, out_dir)

    assert (out_dir / "metrics.json").exists()
    assert (out_dir / "per_turn.jsonl").exists()
    report = (out_dir / "report.md").read_text(encoding="utf-8")
    assert "SearchTrace Trace Evaluation" in report
    assert "Zero-Recall Examples" in report
    assert "doc_prev" in report
    assert "last_answer_x3" in render_markdown_report(metrics)


def test_evaluate_traces_accepts_external_retriever(tmp_path) -> None:
    class StubRetriever:
        def query(self, text: str, *, k: int):
            return [("doc_target", 1.0)]

    traces_path = tmp_path / "traces.jsonl"
    _write_jsonl(
        traces_path,
        [
            {
                "session_id": "s1",
                "turn_id": "1",
                "user_question": "Where is the target?",
                "retrieval_query": "target",
                "gold_doc_ids": ["doc_target"],
            }
        ],
    )
    traces = load_traces(traces_path)

    per_turn, metrics = evaluate_traces(
        traces,
        corpus=None,
        policies=("query_only",),
        k=1,
        retriever=StubRetriever(),
    )

    assert per_turn[0]["ranked_ids"] == ["doc_target"]
    assert metrics["policies"]["query_only"]["recall_at_5"] == 1.0


def test_evaluate_traces_rejects_replay_policies_without_backend(tmp_path) -> None:
    traces_path = tmp_path / "traces.jsonl"
    _write_jsonl(
        traces_path,
        [
            {
                "session_id": "s1",
                "turn_id": "1",
                "user_question": "Where is the target?",
                "retrieval_query": "target",
                "gold_doc_ids": ["doc_target"],
            }
        ],
    )

    with pytest.raises(ValueError, match="corpus or retriever is required"):
        evaluate_traces(load_traces(traces_path), corpus=None, policies=("query_only",), k=1)


def test_production_baseline_explicitly_scores_logged_retrieved_ids(tmp_path) -> None:
    traces_path = tmp_path / "traces.jsonl"
    _write_jsonl(
        traces_path,
        [
            {
                "session_id": "s1",
                "turn_id": "1",
                "user_question": "Where is the target?",
                "retrieval_query": "target",
                "retrieved_doc_ids": ["doc_target"],
                "gold_doc_ids": ["doc_target"],
            }
        ],
    )

    per_turn, metrics = evaluate_traces(
        load_traces(traces_path),
        corpus=None,
        policies=("production_baseline",),
        k=1,
    )

    assert per_turn[0]["ranked_ids"] == ["doc_target"]
    assert metrics["policies"]["production_baseline"]["recall_at_5"] == 1.0


def test_check_metric_gates_reports_policy_failures() -> None:
    metrics = {
        "policies": {
            "candidate": {
                "n": 10,
                "recall_at_5": 0.42,
                "recall_at_k": 0.50,
                "zero_recall_at_k": 0.20,
                "drift_at_1": 0.30,
                "stale_at_1": 0.11,
                "false_lead_at_k": 0.08,
            }
        }
    }

    failures = check_metric_gates(
        metrics,
        policy="candidate",
        min_recall_at_5=0.50,
        max_stale_at_1=0.10,
        max_false_lead_at_k=0.10,
    )

    assert failures == [
        "candidate.recall_at_5=0.420 violates >= 0.500",
        "candidate.stale_at_1=0.110 violates <= 0.100",
    ]


def test_check_metric_regressions_reports_drops_and_increases() -> None:
    baseline = {
        "policies": {
            "candidate": {
                "n": 10,
                "recall_at_5": 0.70,
                "zero_recall_at_k": 0.10,
                "stale_at_1": 0.05,
                "false_lead_at_k": 0.02,
            }
        }
    }
    current = {
        "policies": {
            "candidate": {
                "n": 10,
                "recall_at_5": 0.63,
                "zero_recall_at_k": 0.12,
                "stale_at_1": 0.09,
                "false_lead_at_k": 0.07,
            }
        }
    }

    failures = check_metric_regressions(
        current,
        baseline,
        policy="candidate",
        max_recall_at_5_drop=0.05,
        max_zero_recall_at_k_increase=0.05,
        max_stale_at_1_increase=0.03,
        max_false_lead_at_k_increase=0.03,
    )

    assert failures == [
        "candidate.recall_at_5 delta=-0.070 drops more than 0.050",
        "candidate.stale_at_1 delta=+0.040 increases more than 0.030",
        "candidate.false_lead_at_k delta=+0.050 increases more than 0.030",
    ]
