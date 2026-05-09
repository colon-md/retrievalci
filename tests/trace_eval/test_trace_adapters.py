from __future__ import annotations

import json

from searchtrace.trace_adapters import (
    normalize_trace_jsonl,
    normalize_trace_row,
    write_rag_report_traces,
)


def _write_jsonl(path, rows) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def test_normalize_trace_row_accepts_flat_span_attributes() -> None:
    row = {
        "trace_id": "trace-1",
        "span_id": "span-2",
        "attributes": {
            "input.value": "Where is Ada Lab headquartered?",
            "retrieval.query": "Ada Lab headquarters",
            "retrieval.documents": [
                {"document_id": "doc_target", "score": 0.9},
                {"document_id": "doc_prev", "score": 0.2},
            ],
            "agent_state.previous_answers": ["Ada Lab"],
            "agent_state.previous_doc_ids": ["doc_prev"],
        },
        "metadata": {"gold_doc_ids": ["doc_target"]},
    }

    normalized = normalize_trace_row(row)

    assert normalized == {
        "session_id": "trace-1",
        "turn_id": "span-2",
        "user_question": "Where is Ada Lab headquartered?",
        "retrieval_query": "Ada Lab headquarters",
        "retrieved_doc_ids": ["doc_target", "doc_prev"],
        "gold_doc_ids": ["doc_target"],
        "agent_state": {
            "previous_answers": ["Ada Lab"],
            "previous_doc_ids": ["doc_prev"],
        },
    }


def test_normalize_trace_jsonl_filters_missing_gold_when_required(tmp_path) -> None:
    input_path = tmp_path / "spans.jsonl"
    output_path = tmp_path / "traces.jsonl"
    _write_jsonl(
        input_path,
        [
            {
                "trace_id": "keep",
                "input": "Question?",
                "retrieved_doc_ids": ["doc_a"],
                "gold_doc_ids": ["doc_a"],
            },
            {
                "trace_id": "drop",
                "input": "Question?",
                "retrieved_doc_ids": ["doc_b"],
            },
        ],
    )

    written = normalize_trace_jsonl(input_path, output_path, require_gold=True)

    assert written == 1
    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["session_id"] == "keep"


def test_normalize_trace_row_decodes_json_encoded_retrieval_documents() -> None:
    row = {
        "trace_id": "trace-json-docs",
        "span_id": "retriever",
        "attributes": {
            "input.value": "Ada Lab headquarters",
            "retrieval.documents": json.dumps(
                [
                    {"document": {"id": "doc_target"}, "score": 0.9},
                    {"metadata": {"doc_id": "doc_prev"}, "score": 0.2},
                ]
            ),
        },
        "metadata": {"gold_doc_ids": ["doc_target"]},
    }

    normalized = normalize_trace_row(row)

    assert normalized["retrieved_doc_ids"] == ["doc_target", "doc_prev"]


def test_normalize_trace_jsonl_accepts_otlp_resource_spans_with_parent_context(tmp_path) -> None:
    input_path = tmp_path / "otel.json"
    output_path = tmp_path / "traces.jsonl"
    input_path.write_text(
        json.dumps(
            {
                "resourceSpans": [
                    {
                        "resource": {
                            "attributes": [
                                {
                                    "key": "service.name",
                                    "value": {"stringValue": "rag-api"},
                                }
                            ]
                        },
                        "scopeSpans": [
                            {
                                "spans": [
                                    {
                                        "traceId": "trace-otel",
                                        "spanId": "root",
                                        "name": "agent.run",
                                        "attributes": [
                                            {
                                                "key": "input.value",
                                                "value": {
                                                    "stringValue": (
                                                        "Where is Ada Lab headquartered?"
                                                    )
                                                },
                                            },
                                            {
                                                "key": "session.id",
                                                "value": {"stringValue": "case-123"},
                                            },
                                            {
                                                "key": "searchtrace.gold_doc_ids",
                                                "value": {
                                                    "arrayValue": {
                                                        "values": [
                                                            {"stringValue": "doc_target"}
                                                        ]
                                                    }
                                                },
                                            },
                                            {
                                                "key": (
                                                    "searchtrace.agent_state."
                                                    "previous_answers"
                                                ),
                                                "value": {
                                                    "arrayValue": {
                                                        "values": [
                                                            {"stringValue": "Ada Lab"}
                                                        ]
                                                    }
                                                },
                                            },
                                        ],
                                    },
                                    {
                                        "traceId": "trace-otel",
                                        "spanId": "retriever",
                                        "parentSpanId": "root",
                                        "name": "vector retriever",
                                        "attributes": [
                                            {
                                                "key": "openinference.span.kind",
                                                "value": {"stringValue": "RETRIEVER"},
                                            },
                                            {
                                                "key": "input.value",
                                                "value": {
                                                    "stringValue": "Ada Lab headquarters"
                                                },
                                            },
                                            {
                                                "key": (
                                                    "retrieval.documents.0."
                                                    "document.id"
                                                ),
                                                "value": {"stringValue": "doc_target"},
                                            },
                                            {
                                                "key": (
                                                    "retrieval.documents.1."
                                                    "document.id"
                                                ),
                                                "value": {"stringValue": "doc_prev"},
                                            },
                                        ],
                                    },
                                ]
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    written = normalize_trace_jsonl(input_path, output_path, source="otel", require_gold=True)

    assert written == 1
    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert rows == [
        {
            "session_id": "case-123",
            "turn_id": "retriever",
            "user_question": "Where is Ada Lab headquartered?",
            "retrieval_query": "Ada Lab headquarters",
            "retrieved_doc_ids": ["doc_target", "doc_prev"],
            "gold_doc_ids": ["doc_target"],
            "agent_state": {"previous_answers": ["Ada Lab"]},
        }
    ]


def test_write_rag_report_traces_exports_retrieved_and_gold_ids(tmp_path) -> None:
    report_path = tmp_path / "report.json"
    questions_path = tmp_path / "questions.jsonl"
    output_path = tmp_path / "traces.jsonl"
    report_path.write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "system": "hybrid_rag",
                        "question_id": "q1",
                        "answer": {
                            "answer": "Ada Lab is in Paris.",
                            "citations": [{"source_path": "doc_target"}],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    _write_jsonl(
        questions_path,
        [
            {
                "id": "q1",
                "question": "Where is Ada Lab?",
                "ground_truth_citations": ["doc_target"],
            }
        ],
    )

    written = write_rag_report_traces(report_path, questions_path, output_path)

    assert written == 1
    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["turn_id"] == "hybrid_rag"
    assert rows[0]["retrieved_doc_ids"] == ["doc_target"]
    assert rows[0]["gold_doc_ids"] == ["doc_target"]
