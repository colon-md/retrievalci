# Trace-State Eval

RetrievalCI has two complementary evaluation modes:

- `retrievalci rag run`: compare RAG architectures such as dense, BM25,
  hybrid, rerank, ClaimRAG, chunk-summary, and wiki/synthesis.
- `retrievalci traces eval`: replay retrieval-state policies over agent trace
  logs to identify drift, stale evidence, zero-recall, and false-lead capture.

Use trace-state eval when the question is:

> Which public agent state should be sent into retrieval?

Example:

```bash
retrievalci traces normalize \
  --source auto \
  --input examples/spans.demo.jsonl \
  --out /tmp/traces.retrievalci.jsonl \
  --require-gold

retrievalci traces eval \
  --traces /tmp/traces.retrievalci.jsonl \
  --corpus examples/corpus.demo.jsonl \
  --out /tmp/retrievalci-trace-report \
  --k 1 \
  --policies recorded,query_only,last_answer_x3,compact_state,public_trace \
  --gate-policy last_answer_x3 \
  --min-recall-at-5 0.90 \
  --max-stale-at-1 0.05
```

`recorded` replays the logged retrieval query through the same retriever as the
other policies. Use `production_baseline` when you want to score logged
`retrieved_doc_ids` directly.

For Phoenix/OpenTelemetry-style exports, `--source otel` flattens OTLP
`resourceSpans`/`scopeSpans` containers, typed attribute values, and indexed
OpenInference retrieval document fields:

```bash
retrievalci traces normalize \
  --source otel \
  --input examples/otel.spans.demo.json \
  --out /tmp/traces.retrievalci.jsonl \
  --require-gold
```

When a retriever span lacks the original user question, RetrievalCI uses
same-trace parent context such as `input.value`, `session.id`, and
`retrievalci.gold_doc_ids`.

Outputs:

- `metrics.json`: aggregate policy metrics.
- `per_turn.jsonl`: replay details for each trace turn and policy.
- `report.md`: Markdown report with recommendations and failure examples.

To evaluate the same policies against a deployed retriever, replace local BM25
with an HTTP retriever endpoint:

```bash
retrievalci traces eval \
  --traces /tmp/traces.retrievalci.jsonl \
  --out /tmp/retrievalci-trace-report \
  --k 5 \
  --policies query_only,last_answer_x3,compact_state \
  --retriever-url https://retriever.example.com/search \
  --retriever-header 'Authorization: Bearer ...'
```

The endpoint receives `{"query": text, "k": k}` and can return a list of ids,
or a JSON object with a `results`, `hits`, `matches`, `documents`, or `data`
list. Result objects may use `id`, `doc_id`, `chunk_id`, `document_id`,
`source_id`, or those same fields under `metadata`.

HTTP replay also writes `retriever-calls.jsonl` with safe call metadata: query
hash, query length, `k`, status code, latency, result ids, and any error.
Request headers, full query text, and full response bodies are not persisted.

To bridge an architecture report into trace rows:

```bash
retrievalci traces from-rag-report \
  --report-json /tmp/retrievalci-rag-smoke.json \
  --questions examples/rag_eval/questions.jsonl \
  --out /tmp/retrievalci-rag-as-traces.jsonl
```

Trace-state eval sits after or beside the RAG architecture pass. A team can
first learn which retriever performs best, then use trace-state eval to decide
whether full conversation history, compact state, or bridge-answer state should
be sent into that retriever.
