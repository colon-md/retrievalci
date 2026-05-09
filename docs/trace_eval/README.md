# Trace-State Eval

SearchTrace has two complementary evaluation modes:

- `searchtrace rag run`: compare RAG architectures such as dense, BM25,
  hybrid, rerank, ClaimRAG, chunk-summary, and wiki/synthesis.
- `searchtrace traces eval`: replay retrieval-state policies over agent trace
  logs to identify drift, stale evidence, zero-recall, and false-lead capture.

Use trace-state eval when the question is:

> Which public agent state should be sent into retrieval?

Example:

```bash
searchtrace traces normalize \
  --source auto \
  --input examples/spans.demo.jsonl \
  --out /tmp/traces.searchtrace.jsonl \
  --require-gold

searchtrace traces eval \
  --traces /tmp/traces.searchtrace.jsonl \
  --corpus examples/corpus.demo.jsonl \
  --out /tmp/searchtrace-trace-report \
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
searchtrace traces normalize \
  --source otel \
  --input examples/otel.spans.demo.json \
  --out /tmp/traces.searchtrace.jsonl \
  --require-gold
```

When a retriever span lacks the original user question, SearchTrace uses
same-trace parent context such as `input.value`, `session.id`, and
`searchtrace.gold_doc_ids`.

Outputs:

- `metrics.json`: aggregate policy metrics.
- `per_turn.jsonl`: replay details for each trace turn and policy.
- `report.md`: Markdown report with recommendations and failure examples.

To evaluate the same policies against a deployed retriever, replace local BM25
with an HTTP retriever endpoint:

```bash
searchtrace traces eval \
  --traces /tmp/traces.searchtrace.jsonl \
  --out /tmp/searchtrace-trace-report \
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
searchtrace traces from-rag-report \
  --report-json /tmp/searchtrace-rag-smoke.json \
  --questions examples/rag_eval/questions.jsonl \
  --out /tmp/searchtrace-rag-as-traces.jsonl
```

Trace-state eval sits after or beside the RAG architecture pass. A team can
first learn which retriever performs best, then use trace-state eval to decide
whether full conversation history, compact state, or bridge-answer state should
be sent into that retriever.
