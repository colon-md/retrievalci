"""Trace export adapters for product-facing SearchTrace workflows."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from searchtrace.trace_eval import load_jsonl

_INDEXED_DOCUMENT_RE = re.compile(
    r"^retrieval\.documents\.(?P<index>\d+)\.(?:document\.)?(?P<field>.+)$"
)


def _get_path(row: dict[str, Any], path: str) -> Any:
    if path in row:
        return row[path]
    current: Any = row
    parts = path.split(".")
    for i, part in enumerate(parts):
        if not isinstance(current, dict) or part not in current:
            if isinstance(current, dict):
                return current.get(".".join(parts[i:]))
            return None
        current = current[part]
    return current


def _first_value(row: dict[str, Any], paths: tuple[str, ...]) -> Any:
    for path in paths:
        value = _get_path(row, path)
        if value not in (None, "", []):
            return value
    return None


def _jsonish(value: str) -> Any:
    text = value.strip()
    if not text or text[0] not in "[{":
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        decoded = _jsonish(value)
        if decoded is not value:
            return _as_list(decoded)
        return [value] if value else []
    if isinstance(value, dict):
        identifier = _first_value(
            value,
            (
                "doc_id",
                "document_id",
                "chunk_id",
                "id",
                "source_id",
                "metadata.doc_id",
                "metadata.document_id",
                "metadata.chunk_id",
                "metadata.id",
                "metadata.source_id",
                "document.id",
                "document.doc_id",
                "document.document_id",
            ),
        )
        return [str(identifier)] if identifier else []
    if isinstance(value, list | tuple):
        output: list[str] = []
        for item in value:
            output.extend(_as_list(item))
        return output
    return [str(value)]


def _agent_state(row: dict[str, Any]) -> dict[str, Any]:
    existing = row.get("agent_state") if isinstance(row.get("agent_state"), dict) else {}
    state = dict(existing)
    mappings = {
        "previous_answers": (
            "previous_answers",
            "attributes.agent_state.previous_answers",
            "attributes.state.previous_answers",
            "attributes.searchtrace.agent_state.previous_answers",
            "metadata.previous_answers",
        ),
        "previous_doc_ids": (
            "previous_doc_ids",
            "attributes.agent_state.previous_doc_ids",
            "attributes.state.previous_doc_ids",
            "attributes.searchtrace.agent_state.previous_doc_ids",
            "metadata.previous_doc_ids",
        ),
        "false_lead_doc_ids": (
            "false_lead_doc_ids",
            "attributes.agent_state.false_lead_doc_ids",
            "attributes.state.false_lead_doc_ids",
            "attributes.searchtrace.agent_state.false_lead_doc_ids",
            "metadata.false_lead_doc_ids",
        ),
        "public_trace": (
            "public_trace",
            "attributes.agent_state.public_trace",
            "attributes.state.public_trace",
            "attributes.searchtrace.agent_state.public_trace",
            "metadata.public_trace",
        ),
    }
    for key, paths in mappings.items():
        if key in state:
            continue
        value = _first_value(row, paths)
        if value not in (None, "", []):
            state[key] = _as_list(value)
    return state


def normalize_trace_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a generic trace/span export into the SearchTrace trace schema.

    This accepts broad JSON shapes because Langfuse, Phoenix/OpenTelemetry, and
    custom span exports differ in field names. It does not invent gold labels;
    those should come from eval data, incident labels, clicks, or review.
    """

    session_id = _first_value(
        row,
        (
            "session_id",
            "conversation_id",
            "trace_id",
            "attributes.session.id",
            "attributes.session_id",
            "metadata.session_id",
        ),
    )
    turn_id = _first_value(
        row,
        (
            "turn_id",
            "step_id",
            "span_id",
            "id",
            "attributes.turn_id",
            "attributes.step_id",
        ),
    )
    user_question = _first_value(
        row,
        (
            "user_question",
            "question",
            "input.question",
            "input",
            "attributes.user_question",
            "attributes.question",
            "attributes.input.value",
            "metadata.user_question",
        ),
    )
    retrieval_query = _first_value(
        row,
        (
            "retrieval_query",
            "query",
            "input.query",
            "attributes.retrieval.query",
            "attributes.query",
            "attributes.input.value",
        ),
    )
    current_need = _first_value(
        row,
        (
            "current_need",
            "attributes.current_need",
            "attributes.agent_state.current_need",
            "metadata.current_need",
        ),
    )
    retrieved_ids = _first_value(
        row,
        (
            "retrieved_doc_ids",
            "retrieved_chunk_ids",
            "retrieved_ids",
            "documents",
            "output.documents",
            "attributes.retrieval.documents",
            "attributes.retrieval.documents_json",
            "attributes.retrieval.documents",
            "attributes.retrieved_documents",
            "attributes.retrieved_doc_ids",
        ),
    )
    if retrieved_ids is None:
        retrieved_ids = _indexed_retrieval_documents(row.get("attributes"))
    gold_ids = _first_value(
        row,
        (
            "gold_doc_ids",
            "gold_chunk_ids",
            "gold_ids",
            "labels.gold_doc_ids",
            "metadata.gold_doc_ids",
            "attributes.searchtrace.gold_doc_ids",
            "attributes.gold_doc_ids",
        ),
    )

    normalized: dict[str, Any] = {
        "session_id": str(session_id or ""),
        "turn_id": str(turn_id or ""),
        "user_question": str(user_question or ""),
        "retrieval_query": str(retrieval_query or user_question or ""),
        "retrieved_doc_ids": _as_list(retrieved_ids),
        "gold_doc_ids": _as_list(gold_ids),
        "agent_state": _agent_state(row),
    }
    if current_need:
        normalized["current_need"] = str(current_need)
    return normalized


def normalize_observability_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize Phoenix/OpenTelemetry-style spans into SearchTrace rows."""

    spans = [_flatten_span(span) for row in rows for span in _iter_span_rows(row)]
    if not spans:
        return []

    contexts: dict[str, dict[str, Any]] = {}
    for span in spans:
        trace_id = str(span.get("trace_id", ""))
        if not trace_id:
            continue
        context = contexts.setdefault(trace_id, {})
        _capture_context(context, span)

    retriever_spans = [span for span in spans if _is_retriever_span(span)]
    source_spans = retriever_spans or spans
    normalized: list[dict[str, Any]] = []
    for span in source_spans:
        trace_id = str(span.get("trace_id", ""))
        enriched = _merge_trace_context(span, contexts.get(trace_id, {}))
        normalized.append(normalize_trace_row(enriched))
    return normalized


def normalize_trace_jsonl(
    input_path: str | Path,
    output_path: str | Path,
    *,
    require_question: bool = True,
    require_gold: bool = False,
    source: str = "auto",
) -> int:
    rows = _load_export_rows(input_path)
    normalized_rows = (
        normalize_observability_rows(rows)
        if _use_observability_adapter(rows, source)
        else [normalize_trace_row(row) for row in rows]
    )
    written = 0
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for normalized in normalized_rows:
            if require_question and not normalized["user_question"]:
                continue
            if require_gold and not normalized["gold_doc_ids"]:
                continue
            f.write(json.dumps(normalized) + "\n")
            written += 1
    return written


def _load_export_rows(input_path: str | Path) -> list[dict[str, Any]]:
    path = Path(input_path)
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if not stripped:
        return []
    if stripped[0] in "[{":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return load_jsonl(path)
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        if isinstance(data, dict):
            return [data]
    return load_jsonl(path)


def _use_observability_adapter(rows: list[dict[str, Any]], source: str) -> bool:
    normalized_source = source.lower()
    if normalized_source in {"otel", "opentelemetry", "phoenix"}:
        return True
    if normalized_source != "auto":
        return False
    return any(_looks_like_observability_export(row) for row in rows)


def _looks_like_observability_export(row: dict[str, Any]) -> bool:
    if any(key in row for key in ("resourceSpans", "scopeSpans", "instrumentationLibrarySpans")):
        return True
    if any(key in row for key in ("traceId", "spanId", "parentSpanId")):
        return True
    attributes = row.get("attributes")
    if isinstance(attributes, list):
        return True
    if isinstance(attributes, dict):
        kind = str(
            attributes.get("openinference.span.kind")
            or attributes.get("span.kind")
            or attributes.get("otel.kind")
            or ""
        ).lower()
        return kind == "retriever"
    return False


def _iter_span_rows(row: dict[str, Any]) -> list[dict[str, Any]]:
    if "resourceSpans" in row:
        return [
            _with_resource_attributes(span, resource.get("resource", {}))
            for resource in row.get("resourceSpans", [])
            if isinstance(resource, dict)
            for scope in (
                resource.get("scopeSpans")
                or resource.get("instrumentationLibrarySpans")
                or []
            )
            if isinstance(scope, dict)
            for span in scope.get("spans", [])
            if isinstance(span, dict)
        ]
    if "scopeSpans" in row or "instrumentationLibrarySpans" in row:
        return [
            span
            for scope in row.get("scopeSpans") or row.get("instrumentationLibrarySpans") or []
            if isinstance(scope, dict)
            for span in scope.get("spans", [])
            if isinstance(span, dict)
        ]
    spans = row.get("spans")
    if isinstance(spans, list):
        return [span for span in spans if isinstance(span, dict)]
    return [row]


def _with_resource_attributes(span: dict[str, Any], resource: dict[str, Any]) -> dict[str, Any]:
    attributes = dict(_otel_attributes(resource.get("attributes")))
    attributes.update(_otel_attributes(span.get("attributes")))
    enriched = dict(span)
    enriched["attributes"] = attributes
    return enriched


def _flatten_span(span: dict[str, Any]) -> dict[str, Any]:
    flattened = dict(span)
    flattened["trace_id"] = str(
        span.get("trace_id")
        or span.get("traceId")
        or _get_path(span, "context.trace_id")
        or _get_path(span, "context.traceId")
        or ""
    )
    flattened["span_id"] = str(
        span.get("span_id")
        or span.get("spanId")
        or _get_path(span, "context.span_id")
        or _get_path(span, "context.spanId")
        or span.get("id")
        or ""
    )
    flattened["parent_span_id"] = str(
        span.get("parent_span_id") or span.get("parentSpanId") or span.get("parent_id") or ""
    )
    flattened["name"] = str(span.get("name") or span.get("span_name") or "")
    attributes = span.get("attributes")
    flattened["attributes"] = (
        _otel_attributes(attributes) if isinstance(attributes, list) else dict(attributes or {})
    )
    return flattened


def _otel_attributes(attributes: Any) -> dict[str, Any]:
    if isinstance(attributes, dict):
        return attributes
    output: dict[str, Any] = {}
    for item in attributes or []:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if not key:
            continue
        output[str(key)] = _otel_value(item.get("value"))
    return output


def _otel_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key in ("stringValue", "intValue", "doubleValue", "boolValue", "bytesValue"):
        if key in value:
            return value[key]
    if "arrayValue" in value:
        values = (
            value["arrayValue"].get("values", [])
            if isinstance(value["arrayValue"], dict)
            else []
        )
        return [_otel_value(v) for v in values]
    if "kvlistValue" in value:
        values = (
            value["kvlistValue"].get("values", [])
            if isinstance(value["kvlistValue"], dict)
            else []
        )
        return {
            str(v.get("key")): _otel_value(v.get("value"))
            for v in values
            if isinstance(v, dict)
        }
    return value


def _is_retriever_span(span: dict[str, Any]) -> bool:
    attributes = span.get("attributes") if isinstance(span.get("attributes"), dict) else {}
    kind = str(
        attributes.get("openinference.span.kind")
        or attributes.get("span.kind")
        or attributes.get("otel.kind")
        or ""
    ).lower()
    if kind == "retriever":
        return True
    name = str(span.get("name") or "").lower()
    return "retriev" in name or "search" in name


def _capture_context(context: dict[str, Any], span: dict[str, Any]) -> None:
    candidates = {
        "session_id": _first_value(
            span,
            (
                "session_id",
                "attributes.session.id",
                "attributes.session_id",
                "attributes.conversation.id",
                "attributes.thread.id",
            ),
        ),
        "user_question": _first_value(
            span,
            (
                "user_question",
                "question",
                "attributes.user_question",
                "attributes.question",
                "attributes.input.value",
                "input.value",
                "input",
            ),
        ),
        "gold_doc_ids": _first_value(
            span,
            (
                "gold_doc_ids",
                "attributes.searchtrace.gold_doc_ids",
                "attributes.gold_doc_ids",
                "metadata.gold_doc_ids",
            ),
        ),
        "current_need": _first_value(
            span,
            (
                "current_need",
                "attributes.current_need",
                "attributes.searchtrace.current_need",
            ),
        ),
    }
    if _is_retriever_span(span):
        candidates["user_question"] = None
    for key, value in candidates.items():
        if key not in context and value not in (None, "", []):
            context[key] = value
    state = _agent_state(span)
    if state:
        existing = context.setdefault("agent_state", {})
        if isinstance(existing, dict):
            for key, value in state.items():
                existing.setdefault(key, value)


def _merge_trace_context(span: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(span)
    for key in ("session_id", "user_question", "gold_doc_ids", "current_need"):
        if context.get(key) not in (None, "", []) and _first_value(enriched, (key,)) in (
            None,
            "",
            [],
        ):
            enriched[key] = context[key]
    if context.get("agent_state") and "agent_state" not in enriched:
        enriched["agent_state"] = context["agent_state"]
    return enriched


def _indexed_retrieval_documents(attributes: Any) -> list[dict[str, Any]]:
    if not isinstance(attributes, dict):
        return []
    docs: dict[int, dict[str, Any]] = {}
    for key, value in attributes.items():
        match = _INDEXED_DOCUMENT_RE.match(str(key))
        if not match:
            continue
        index = int(match.group("index"))
        field = match.group("field")
        doc = docs.setdefault(index, {})
        _assign_dotted(doc, field, value)
    return [docs[index] for index in sorted(docs)]


def _assign_dotted(target: dict[str, Any], path: str, value: Any) -> None:
    current = target
    parts = path.split(".")
    for part in parts[:-1]:
        nested = current.setdefault(part, {})
        if not isinstance(nested, dict):
            nested = {}
            current[part] = nested
        current = nested
    current[parts[-1]] = value


def rag_report_to_trace_rows(
    report_json_path: str | Path,
    questions_path: str | Path,
) -> list[dict[str, Any]]:
    """Convert RAG architecture eval rows into trace-eval rows.

    This is a one-way bridge from `searchtrace rag run` to
    `searchtrace traces eval`. The RAG report does not contain alternative
    agent state, so the output is best used for recorded/query-only retrieval
    diagnostics and as a starting point for incident enrichment.
    """

    with Path(report_json_path).open(encoding="utf-8") as f:
        report = json.load(f)
    questions = {str(q["id"]): q for q in load_jsonl(questions_path)}

    trace_rows: list[dict[str, Any]] = []
    for row in report.get("rows", []):
        qid = str(row.get("question_id", ""))
        question = questions.get(qid)
        if question is None:
            continue
        answer = row.get("answer") or {}
        citations = answer.get("citations") or []
        retrieved = [
            str(c.get("source_path"))
            for c in citations
            if isinstance(c, dict) and c.get("source_path")
        ]
        system = str(row.get("system", ""))
        trace_rows.append(
            {
                "session_id": qid,
                "turn_id": system,
                "user_question": str(question.get("question", "")),
                "retrieval_query": str(question.get("question", "")),
                "retrieved_doc_ids": retrieved,
                "gold_doc_ids": list(question.get("ground_truth_citations") or []),
                "agent_state": {
                    "public_trace": [
                        f"System `{system}` answered: {str(answer.get('answer', ''))[:300]}"
                    ]
                },
            }
        )
    return trace_rows


def write_rag_report_traces(
    report_json_path: str | Path,
    questions_path: str | Path,
    output_path: str | Path,
) -> int:
    rows = rag_report_to_trace_rows(report_json_path, questions_path)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return len(rows)
