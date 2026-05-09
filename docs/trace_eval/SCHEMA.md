# Trace Input Schema

`searchtrace traces eval` reads one JSON object per line. The schema is small
so teams can export it from existing logs without adopting a new runtime.

## Minimal Row

```json
{
  "session_id": "case-001",
  "turn_id": 3,
  "user_question": "Where are the headquarters of the place where the Widget inventor worked?",
  "retrieval_query": "Widget inventor worked headquarters",
  "retrieved_doc_ids": ["doc_prev"],
  "gold_doc_ids": ["doc_target"],
  "agent_state": {
    "previous_answers": ["Ada Lab"],
    "previous_doc_ids": ["doc_prev"],
    "false_lead_doc_ids": ["doc_false"],
    "public_trace": [
      "Asked who employed the Widget inventor. Answer found: Ada Lab."
    ]
  }
}
```

## Fields

| Field | Required | Meaning |
| --- | --- | --- |
| `session_id` | no | Conversation or task id. |
| `turn_id` | no | Retrieval step id. |
| `user_question` | yes | Original task or current user turn. |
| `retrieval_query` | no | Query production sent to retrieval. Used by `recorded`. |
| `retrieved_doc_ids` / `retrieved_chunk_ids` | no | Production retrieved ids. |
| `gold_doc_ids` / `gold_chunk_ids` | yes for metrics | Expected evidence ids. |
| `current_need` | no | Current information need for `current_need` policy. |
| `agent_state.previous_answers` | no | Public answers/facts found earlier. |
| `agent_state.known_facts` | no | Other public facts extracted by the agent. |
| `agent_state.excluded_leads` | no | Leads the agent has ruled out. |
| `agent_state.public_trace` | no | Public action/evidence trace. |
| `agent_state.previous_doc_ids` | no | Already-read docs for stale-state diagnostics. |
| `agent_state.false_lead_doc_ids` | no | Known wrong docs for false-lead diagnostics. |

`recorded` replays `retrieval_query` through the configured corpus or retriever
so its metrics are comparable with other state policies. To score logged
production IDs directly, include the `production_baseline` policy.

## Corpus Schema

The corpus is also JSONL. Each row can be either document-style:

```json
{"doc_id": "doc_target", "text": "Ada Lab headquarters are in Paris."}
```

or manifest-style:

```json
{
  "doc_id": "paper_001",
  "title": "Agentic Search",
  "abstract": "...",
  "body": "..."
}
```

Manifest-style rows are deterministically chunked into title/abstract and body
chunks before replay.

## HTTP Retriever Response Schema

When `--retriever-url` is used, SearchTrace sends this JSON body with `POST`:

```json
{"query": "rendered retrieval-state query", "k": 5}
```

The response can be a list:

```json
["doc_target", "doc_backup"]
```

or an object containing `results`, `hits`, `matches`, `documents`, or `data`:

```json
{
  "results": [
    {"id": "doc_target", "score": 0.91},
    {"metadata": {"doc_id": "doc_backup"}, "score": 0.42}
  ]
}
```

Recognized id fields are `chunk_id`, `doc_id`, `id`, `document_id`, and
`source_id`, either at the result top level or under `metadata`.

## Phoenix/OpenTelemetry Normalization

`searchtrace traces normalize --source otel` accepts JSON or JSONL exports
containing OpenTelemetry-style span objects or OTLP containers:

- `resourceSpans[].scopeSpans[].spans[]`
- `resourceSpans[].instrumentationLibrarySpans[].spans[]`
- `scopeSpans[].spans[]`
- `spans[]`

Typed attribute values such as `stringValue`, `arrayValue`, and `kvlistValue`
are decoded before mapping to the SearchTrace schema. SearchTrace treats spans
as retriever spans when `openinference.span.kind`, `span.kind`, or `otel.kind`
is `RETRIEVER`, or when the span name contains `retriev` or `search`.

Useful attributes:

| Attribute | Meaning |
| --- | --- |
| `input.value` | Original question on parent spans; retrieval query on retriever spans. |
| `session.id` | Session id. |
| `retrieval.documents` | JSON/list of retrieved documents. |
| `retrieval.documents.0.document.id` | Indexed OpenInference document id form. |
| `searchtrace.gold_doc_ids` | Gold evidence ids for evaluation. |
| `searchtrace.agent_state.previous_answers` | Prior public answers for state-policy replay. |
| `searchtrace.agent_state.previous_doc_ids` | Already-read documents for stale-state diagnostics. |
