# RetrievalCI

> **Stage**: `bench-v0` early preview. Scorecard format and the four hosted adapters are stable; more corpora and per-tier breakdowns are pending. MIT licensed, 256 tests.

## Scorecard

<!-- BEGIN retrievalci scorecard -->

```text
score = 100 * (0.7 * retrieval_source_recall + 0.3 * retrieval_source_precision)
```

| System | Score | Recall | Precision | p50 retrieve (ms) |
| --- | ---: | ---: | ---: | ---: |
| Vertex AI RAG Engine | 82.6 | 94.5% | 54.7% | 358.4 |
| Bedrock KB (Cohere embed) | 82.5 | 87.9% | 70.1% | 408.1 |
| OpenAI File Search | 78.5 | 89.3% | 53.4% | 1358.3 |
| Azure AI Search (Gemini embed) | 84.0 | 90.9% | 67.8% | 408.4 |

<!-- END retrievalci scorecard -->

All four services index the same 81 docs, are asked the same 50 questions (25 single-hop, 15 multi-hop, 5 contradiction, 5 unanswerable, stratified from [EnterpriseRAG-Bench](https://github.com/onyx-dot-app/EnterpriseRAG-Bench) v1.0.0), and return chunks that we map back to repo-relative paths via a fail-closed manifest. Recall and precision are computed against the same `ground_truth_citations`.

The latency column is retrieve-only (no generation), cold-call medians from a single region. OpenAI File Search's 1358 ms is much higher than the other three (~400 ms); we haven't isolated whether that reflects server-side reranking on `/vector_stores/{id}/search`, larger retrieve traversal, or cold-tier infrastructure. Read the column as ordering, not as comparable magnitudes.

## What this is

The table above is RetrievalCI running four hosted RAG services against the same 81-doc enterprise corpus and scoring retrieved chunks against the same `ground_truth_citations`.

A free-CPU MiniLM local stack scores 45–50 on the same fixture. K8s ablations suggest the embedder is a large factor in that gap; running bench-v0 with a stronger local embedder is on the roadmap. See [research findings](#research-findings-so-far) below.

Two workflows share the same fixture and scoring code:

1. **Hosted-RAG comparison** — the workflow that produced the scorecard above.
2. **Local RAG architecture eval** — compare 8 local retrieval architectures on your own corpus, with deterministic diagnoses and CI regression gates. Produced the [research findings](#research-findings-so-far).

RetrievalCI doesn't run your production stack; it tells you whether retrieval changed relative to a baseline. See [Why this exists](#why-this-exists) for how it differs from RAGAS, Phoenix, TruLens, and LangSmith.

## Why this exists

Most RAG eval tooling grades generation: faithfulness, answer relevance, hallucination. RetrievalCI grades retrieval.

| Tool | Compares hosted RAG services? | Notes |
| --- | --- | --- |
| **RetrievalCI** | ✅ Vertex + Bedrock + Azure + OpenAI File Search on identical inputs | This repo |
| RAGAS | ❌ Generation-quality framework | Faithfulness / context precision metrics for your own pipeline |
| Phoenix / Arize | ❌ LLM observability | Runtime tracing, not pre-launch CI |
| TruLens | ❌ Custom feedback functions | BYO feedback definitions, no hosted comparison |
| LangSmith | ❌ Hosted experiment tracker | SaaS dependency, no vendor-neutral benchmark |
| Vendor blogs | ❌ Self-reported | Each vendor on their own benchmark, not directly comparable |

If you're picking between Vertex / Bedrock / Azure / OpenAI and want measured numbers on your own corpus before committing, run this.

## Research findings (so far)

Directional findings from K8s ablations (n=10, single corpus). Full numbers and caveats: [STATUS.md](docs/rag_eval/STATUS.md).

- **Wiki+RAG beats vanilla RAG by +0.20** on a multi-source corpus (K8s).
- **The win is at retrieval, not generation.** Putting the synthesized prose in the embedding text accounts for the gain; putting it in the answer prompt contributes ~0. Karpathy's framing pointed at the right feature for the wrong reason.
- **Half the gain is just term density.** Repeating extracted entity names ×10 in chunk text (no LLM) captures +0.15 of the +0.30 prose-embed gain. The other +0.15 is genuine synthesis-derived.
- **A stronger free embedder beats the architecture.** Same wiki prose, MiniLM-L6-v2 → bge-large-en: +0.05 at zero API cost.
- **Wiki+RAG loses on single-source corpora** where each fact appears once. The architecture assumes multi-source compounding.

Reproduce via `make ablation-distill`.

## Quick start

```bash
git clone https://github.com/colon-md/retrievalci.git
cd retrievalci
python -m venv .venv && .venv/bin/pip install -e '.[dev,providers,hosted-aws]'
make bench-v0-mock         # 0 cost, 0 credentials, validates the harness
```

Add `GOOGLE_OAUTH_*` / `AWS_*` / `OPENAI_API_KEY` / `AZURE_SEARCH_*` to `.env` (see [API keys](#api-keys)), then:

```bash
python scripts/run_bench_v0_vertex.py  run --questions ... --corpus-dir ... --output ...
python scripts/run_bench_v0_bedrock.py run ...
python scripts/run_bench_v0_openai.py  run ...
python scripts/run_bench_v0_azure.py   run ...
make bench-v0-scorecard    # regenerates the table in this README
```

Each adapter has a `cleanup` subcommand for stranded cloud resources. `RunBudget` caps cost at $20 and queries at 50 by default.

## What's in the box

- **4 hosted-RAG adapters** implementing the `HostedSystem` protocol with the same provision → ingest → query → teardown lifecycle: Vertex AI RAG Engine, Bedrock KB on OpenSearch Serverless, OpenAI Vector Stores, Azure AI Search.
- **bench-v0 fixture** under `examples/rag_eval/bench_v0/`: 50 questions, 81 corpus docs from EnterpriseRAG-Bench, MIT-licensed.
- **5 local retrieval baselines** (BM25, dense, hybrid-RRF, dense-rerank, chunk-summary) for CI regression checks against your own stack.
- **Scorecard generator** that reads `ComparisonReport` JSON and rewrites the table between `<!-- BEGIN/END retrievalci scorecard -->` markers.
- **Rejudge mode** (`retrievalci rag rejudge`) to re-score existing reports with a different judge without re-running the generators.

![Inputs, project CLI, checks, and run artifact pipeline](docs/assets/retrievalci-overview.png)

## Methodology + caveats

- **bench-v0 is synthetic enterprise data** from EnterpriseRAG-Bench v1.0.0 (fictional companies across GitHub PRs, Slack threads, Linear issues, Drive docs). Not public web content.
- **All four hosted services hallucinate on every unanswerable question in this sample** (`abstention_correctness=0.0` on the 5 `info_not_found` rows). Fixing this lives at the generator or threshold layer, not at the retriever.
- **Mode A only.** The current scorecard is retrieve-only. Mode B (native-stack grounded generation) is planned.
- **bench-v0 is 50 questions.** bench-v1 (150) and bench-v2 (full 500-question ERB) are in the plan doc. Treat the absolute scores as provisional.

Read the full methodology + rationale in [`docs/rag_eval/results/hosted-rag-benchmark-plan.md`](docs/rag_eval/results/hosted-rag-benchmark-plan.md).

## Install

RetrievalCI requires Python 3.12 or newer.

```bash
python -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
```

Smoke examples run on mock backends. Add `[providers]` to use real LLMs and embedders:

```bash
.venv/bin/python -m pip install -e '.[providers]'
```

## API keys

RetrievalCI runs end-to-end with no API keys using the mock backend. Keys are only needed for real LLM generation, real LLM judging, or hosted RAG adapters. Put them in a `.env` file at the repo root or export them in your shell (use `direnv` if you want auto-loading).

**Optional — enables real LLM generator or judge for local systems:**

| Backend | Variable | Used for |
| --- | --- | --- |
| Gemini (flash) | `GOOGLE_API_KEY` or `GEMINI_API_KEY` | Gemini generator (flash-lite) and embedder (public Gemini API, not Vertex AI) |
| Gemini Pro judge | `GEMINI_API_KEY_PRO` (optional override) | When `GeminiJudge` runs a Pro-family model, it prefers this key. Lets a Pro/Ultra subscription key serve judging while a separate free-tier key serves generator and embedder. Falls back to the standard chain if unset. |
| OpenAI | `OPENAI_API_KEY` | OpenAI judge today; OpenAI File Search adapter when shipped |
| Anthropic | `ANTHROPIC_API_KEY` | Claude generator and judge |
| Groq | `GROQ_API_KEY` | Groq generator and judge |

Each backend raises on missing key only when used. Defaults are pinned to free-tier-eligible Gemini models (`gemini-2.5-flash-lite` generator, `gemini-embedding-001` embedder, `gemini-2.5-pro` judge). The Pro judge's ~100 RPD free-tier quota may bind on a 50-question run; set `GEMINI_API_KEY_PRO` to a paid key or wait for the midnight Pacific reset.

**Required only for the hosted-RAG adapters:**

| Adapter | Required env vars |
| --- | --- |
| Vertex AI RAG Engine | `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN` |
| Bedrock Knowledge Bases | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` |
| Azure AI Search | `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_ADMIN_KEY`, `GEMINI_API_KEY` (the adapter brings its own embeddings) |
| OpenAI File Search | `OPENAI_API_KEY` |

Hosted-RAG runs are budget-capped at $20 / 50 queries by default. Override via `RunBudget(allow_overrun=True)` in `retrievalci/rag_eval/hosted.py`.

**Known limitation: `RunBudget` only caps per-query cost and query count.** Hosted services also charge for index storage (Vertex Spanner-hour, Bedrock OCU-hour, Azure search-unit-hour, OpenAI vector-store-byte-month) and one-time ingestion. Each adapter teardown deletes the provisioned index via context manager + `atexit` + SIGINT/SIGTERM/SIGHUP handlers, but if a teardown crashes you'll pay storage until you clean up. Each adapter ships a `cleanup` subcommand that enumerates and deletes stranded resources; see [docs/rag_eval/results/hosted-rag-benchmark-plan.md](docs/rag_eval/results/hosted-rag-benchmark-plan.md) for the full cost model.

## Local CI walkthrough

For the second workflow (local RAG architecture eval + regression gates), run the full local check:

```bash
make check
```

Then run the bundled CI-style evaluation:

```bash
.venv/bin/retrievalci ci run --config examples/retrievalci.ci.yaml
```

The CI command writes a run directory under `.retrievalci/runs/`:

```text
.retrievalci/runs/<run-id>/
├── manifest.json
├── report.html
├── rag-report.json
└── trace-metrics.json
```

A RAG report includes a deterministic diagnosis section:

```markdown
## Diagnosis

- Leader: `dense_rag` on `retrieval_source_recall`.
- Bottleneck: `retrieval_limited`.
- Weakest tier: `multi_hop`.
- Recommendation: Prioritize retrieval changes before answer-prompt changes.
- Next experiment: Try hybrid retrieval, reranking, better embeddings, or higher top-k.
```

The bundled example data lives in `examples/`:

- `examples/rag_eval/corpus/*.md`: small public support-desk corpus.
- `examples/rag_eval/questions.jsonl`: held-out RAG eval questions.
- `examples/rag_eval/bench_v0/`: 50-question hosted-RAG benchmark fixture
  stratified from EnterpriseRAG-Bench v1.0.0 (25 single_hop / 15 multi_hop
  / 5 contradiction / 5 unanswerable). See the
  [hosted-RAG benchmark plan](docs/rag_eval/results/hosted-rag-benchmark-plan.md).
- `examples/third_party/`: compact WixQA and EnterpriseRAG-Bench fixtures
  converted to RetrievalCI format.
- `examples/corpus.demo.jsonl`, `examples/traces.demo.jsonl`, and
  `examples/otel.spans.demo.json`: trace-state replay fixtures.

## CI workflow

This repo includes a GitHub Actions workflow:

```text
.github/workflows/retrievalci-ci.yml
```

It runs lint, tests, `retrievalci ci run --config examples/retrievalci.ci.yaml`,
and uploads `.retrievalci/runs` as the review artifact. See
[docs/CI.md](docs/CI.md) for baseline and artifact conventions.

## RAG architecture eval

Run a config-driven mock eval:

```bash
.venv/bin/retrievalci rag run --config examples/rag_eval/smoke.yaml
```

Run bundled third-party RAG examples:

```bash
.venv/bin/retrievalci rag run --config examples/third_party/wixqa/smoke.yaml
.venv/bin/retrievalci rag run --config examples/third_party/enterprise_rag_bench_github/smoke.yaml
```

Use `scripts/import_third_party_examples.py` to refresh or expand the local
WixQA and EnterpriseRAG-Bench fixtures under ignored `data/third_party/`.

Compare a candidate report against a baseline:

```bash
.venv/bin/retrievalci rag compare \
  --baseline baselines/rag/smoke.json \
  --candidate reports/pr.json \
  --metric retrieval_source_recall \
  --max-drop 0.02
```

`rag compare` exits `2` on regression, so it can be used directly in CI.

## Project file

Run RAG eval, trace normalization, trace replay, gates, and report generation
from one YAML file:

```bash
.venv/bin/retrievalci project run --config examples/retrievalci.project.yaml
```

`retrievalci ci run --config ...` is an alias for the same project workflow.

## CLI aliases

The `retrievalci` command is the preferred interface. The package also exposes
stable script aliases for automation: `rci-rag-eval`, `rci-rag-compare`,
`rci-report-build`, `rci-runs-create`, `rci-runs-list`, `rci-project-run`,
`rci-eval-traces`, and `rci-normalize-traces`.

## Package map

```text
retrievalci/
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
