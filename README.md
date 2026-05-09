# SearchTrace

SearchTrace is a local-first CI tool for retrieval systems. It helps teams
catch retrieval regressions before they ship by running two complementary
checks:

- **RAG architecture eval:** compare retrieval and answer systems against a
  fixed corpus and question set.
- **Trace-state eval:** replay agent trace logs with different retrieval-state
  policies to expose zero-recall, drift, stale evidence, and false-lead capture.

Every run can produce a versioned manifest, machine-readable metrics, and a
static HTML report that works as a CI artifact.

## Install

SearchTrace requires Python 3.12 or newer.

```bash
python -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
```

The smoke examples use mock/provider-free backends. Provider extras are only
needed when you want to run real LLM or embedding providers:

```bash
.venv/bin/python -m pip install -e '.[providers]'
```

Provider extras are bounded to SDK major versions that SearchTrace has adapter
coverage for. If a provider releases a new major SDK, update the backend adapter
and version bound together.

## Quick start

Run the full local check:

```bash
make check
```

Then run the bundled CI-style evaluation:

```bash
.venv/bin/searchtrace ci run --config examples/searchtrace.ci.yaml
```

The CI command writes a run directory under `.searchtrace/runs/`:

```text
.searchtrace/runs/<run-id>/
├── manifest.json
├── report.html
├── rag-report.json
└── trace-metrics.json
```

A RAG report includes a deterministic diagnosis section:

```markdown
## Diagnosis

- Leader: `rag` on `retrieval_source_recall`.
- Bottleneck: `retrieval_limited`.
- Weakest tier: `multi_hop`.
- Recommendation: Prioritize retrieval changes before answer-prompt changes.
- Next experiment: Try hybrid retrieval, reranking, better embeddings, or higher top-k.
```

The bundled example data lives in `examples/`:

- `examples/rag_eval/corpus/*.md`: small public support-desk corpus.
- `examples/rag_eval/questions.jsonl`: held-out RAG eval questions.
- `examples/third_party/`: compact WixQA and EnterpriseRAG-Bench fixtures
  converted to SearchTrace format.
- `examples/corpus.demo.jsonl`, `examples/traces.demo.jsonl`, and
  `examples/otel.spans.demo.json`: trace-state replay fixtures.

## CI workflow

This repo includes a GitHub Actions workflow:

```text
.github/workflows/searchtrace-ci.yml
```

It runs lint, tests, `searchtrace ci run --config examples/searchtrace.ci.yaml`,
and uploads `.searchtrace/runs` as the review artifact. See
[docs/CI.md](docs/CI.md) for baseline and artifact conventions.

## RAG architecture eval

Run a config-driven mock eval:

```bash
.venv/bin/searchtrace rag run --config examples/rag_eval/smoke.yaml
```

Run bundled third-party RAG examples:

```bash
.venv/bin/searchtrace rag run --config examples/third_party/wixqa/smoke.yaml
.venv/bin/searchtrace rag run --config examples/third_party/enterprise_rag_bench_github/smoke.yaml
```

Use `scripts/import_third_party_examples.py` to refresh or expand the local
WixQA and EnterpriseRAG-Bench fixtures under ignored `data/third_party/`.

Compare a candidate report against a baseline:

```bash
.venv/bin/searchtrace rag compare \
  --baseline baselines/rag/smoke.json \
  --candidate reports/pr.json \
  --metric retrieval_source_recall \
  --max-drop 0.02
```

`rag compare` exits `2` on regression, so it can be used directly in CI.

## Trace-state eval

Normalize a span export:

```bash
.venv/bin/searchtrace traces normalize \
  --source otel \
  --input examples/otel.spans.demo.json \
  --out /tmp/searchtrace-traces.demo.jsonl \
  --require-gold
```

Replay retrieval-state policies:

```bash
.venv/bin/searchtrace traces eval \
  --traces /tmp/searchtrace-traces.demo.jsonl \
  --corpus examples/corpus.demo.jsonl \
  --out /tmp/searchtrace-trace-report \
  --k 1 \
  --policies recorded,query_only,last_answer_x3,compact_state \
  --gate-policy last_answer_x3 \
  --min-recall-at-5 0.90
```

Use `--retriever-url` when you want SearchTrace to call a deployed retriever
instead of the local BM25 replay baseline.

## Project file

Run RAG eval, trace normalization, trace replay, gates, and report generation
from one YAML file:

```bash
.venv/bin/searchtrace project run --config examples/searchtrace.project.yaml
```

`searchtrace ci run --config ...` is an alias for the same project workflow.

## CLI aliases

The `searchtrace` command is the preferred interface. The package also exposes
stable script aliases for automation: `st-rag-eval`, `st-rag-compare`,
`st-report-build`, `st-runs-create`, `st-runs-list`, `st-project-run`,
`st-eval-traces`, and `st-normalize-traces`.

## Package map

```text
searchtrace/
  rag_eval/             RAG systems, metrics, diagnostics, regression checks
  trace_eval.py         Trace-state replay, metrics, gates, Markdown reports
  trace_retrievers.py   HTTP adapter for production retriever replay
  trace_adapters.py     Generic and OpenTelemetry/Phoenix span normalization
  reporting.py          Self-contained HTML report builder
  runs/                 Local run registry and schema-versioned manifests
  project.py            Project YAML to run-spec mapping
  cli.py                CLI dispatcher
```

## More docs

- [CONTRIBUTING.md](CONTRIBUTING.md): local setup, checks, and data-handling
  expectations for contributors.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): package map and data flow.
- [docs/CI.md](docs/CI.md): GitHub Actions, baselines, and artifacts.
- [docs/trace_eval/README.md](docs/trace_eval/README.md): trace-state eval reference.
- [docs/rag_eval/README.md](docs/rag_eval/README.md): RAG eval overview.
