# RetrievalCI Architecture

RetrievalCI is a retrieval diagnostics product with two evaluation modes and a
canonical run registry.

The product thesis is that trace-state retrieval dynamics are the differentiated
surface. RAG architecture evaluation is useful, but the main product surface is
showing how the same retriever behaves when given query-only state, recorded
query state, compact state, bridge-answer state, or logged production IDs.

## Product Modes

| Mode | Command | Question Answered |
| --- | --- | --- |
| RAG architecture eval | `retrievalci rag run` | Which RAG architecture should I ship? |
| Trace-state eval | `retrievalci traces eval` | Which agent state should retrieval receive? |
| Product run artifact | `retrievalci runs create` | What should CI or a reviewer inspect from this run? |
| Project run | `retrievalci project run` | Can a team run the full workflow from one config? |

The modes are complementary. RAG eval compares systems such as dense RAG,
BM25, hybrid retrieval, reranking, ClaimRAG, chunk-summary RAG, and wiki-style
synthesis. Trace-state eval replays state-rendering policies over trace logs
and diagnoses zero-recall, drift, stale-state retrieval, and false-lead capture.
The run registry combines those outputs into a schema-versioned manifest plus a
small static report so teams do not have to chase scattered `/tmp` artifacts.

## Package Layout

```text
retrievalci/
  cli.py                    Product CLI dispatcher.
  project.py                Declarative project config to RunSpec mapping.
  rag_eval/                 RAG architecture evaluation.
    runner.py               Run orchestration and report writing.
    types.py                QAItem, SystemAnswer, RunResult, ComparisonReport.
    metrics.py              Retrieval, answer-citation, and bootstrap metrics.
    corpus.py               Document loading and paragraph chunking.
    systems/                Comparable RAG architectures.
    backends/               Mock and provider LLM/embedder backends.
    claims/                 Claim/proof-set substrate for ClaimRAG/WikiPages.
  trace_eval.py             Trace-state replay and policy diagnostics.
  trace_retrievers.py       BM25-compatible production retriever adapters.
  trace_adapters.py         Generic, Phoenix, OpenTelemetry, and RAG-report adapters.
  reporting.py              Static HTML report builder for human review.
  report_assets.py          Embedded CSS and JavaScript for static reports.
  runs/                     Lean local run registry.
    types.py                RunSpec, ArtifactPolicy, RunArtifact manifest.
    registry.py             Run-id allocation, manifest IO, run listing.
    execute.py              RAG + trace orchestration into one artifact.
```

## Data Flow

RAG architecture eval:

```text
corpus files + QAItem JSONL
  -> chunk corpus
  -> instantiate systems
  -> answer each question with each system
  -> compute retrieval/answer/judge metrics
  -> JSON + Markdown comparison report
  -> optional static HTML report
```

Trace-state eval:

```text
trace/span JSONL + corpus JSONL
  -> normalize trace rows
  -> render candidate retrieval-state policies
  -> replay retrieval with BM25 or recorded ids
  -> compute recall/drift/stale/false-lead metrics
  -> JSON + Markdown state-dynamics report
  -> optional static HTML report
```

Bridge:

```text
RAG ComparisonReport + QAItem JSONL
  -> trace rows with recorded retrieved ids and gold evidence
  -> trace-state eval for recorded retrieval diagnostics
```

Run registry:

```text
RunSpec
  -> reserve .retrievalci/runs/<run-id>
  -> run optional RAG architecture eval
  -> run optional trace-state eval
  -> optional baseline regression gate
  -> write manifest.json + report.html + compact machine artifacts
  -> record SHA-256 digests for explicit input files
  -> keep debug Markdown/per-turn rows only when requested
```

Project config:

```text
retrievalci.project.yaml
  -> optional RAG config
  -> optional trace source normalization
  -> optional production retriever settings
  -> RunSpec
  -> run registry artifact
```

## Artifact Policy

Default run output is intentionally lean:

```text
manifest.json
report.html
rag-report.json
trace-metrics.json
```

`--debug-artifacts` keeps Markdown reports and trace per-turn rows for
investigation. `--snapshot-inputs` copies run inputs for reproducibility, but it
is opt-in because traces and corpora may contain customer data.

Manifests include tool version, optional git SHA, and content digests for
explicit input files and normalized trace sources so baseline comparisons can
distinguish path reuse from content reuse. Normalized trace text from raw
observability exports is not persisted by default; keeping it requires
`--debug-artifacts`.

## Extension Points

- RAG systems live behind `rag_eval/systems/` and can be selected from config.
- LLM/embedder providers live behind `rag_eval/backends/`.
- Trace retrieval now accepts a `TraceRetriever` protocol, so production
  retrievers can be injected without changing the metric engine.
- `trace_retrievers.HTTPTraceRetriever` posts rendered state-policy queries to
  deployed retrieval services and records safe call metadata for debugging.
- Span/trace ingestion runs through `trace_adapters.py`. Phoenix/OpenTelemetry-
  style span exports are handled by the source-aware normalizer today.
