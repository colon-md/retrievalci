# RetrievalCI

**Put a number on RAG quality before it reaches users.**

RetrievalCI is a local-first CI tool for retrieval systems. It runs the same
question set against dense RAG, BM25, hybrid retrieval, hosted RAG services, or
wiki-style knowledge systems, then turns retrieval quality into a scorecard,
CI gate, and static review artifact.

## RAG scorecard

Can your RAG beat the baseline? The table below is **generated** from a
measured benchmark JSON via `retrievalci report scorecard` — do not edit by
hand. Regenerate after each baseline refresh:

```bash
BENCH_BASELINE=baselines/rag/bench_v0_gemini.json \
BENCH_LABEL="bench-v0 / Gemini real backend (judge: mock)" \
  make bench-v0-scorecard
```

Three baselines live in `baselines/rag/`:

- `bench_v0.json` — mock backend, all 7 local systems including
  `claim_rag` and `wiki_pages` (which refuse 100% under mock).
- `bench_v0_gemini.json` — real Gemini-2.5-flash-lite generator + Gemini
  embedder, mock judge; 5 systems (claim_rag and wiki_pages excluded due
  to index-time LLM extraction cost).
- `bench_v0_gemini_claude.json` — same answers as `bench_v0_gemini`,
  rejudged with Claude Haiku 4.5 for faithfulness/relevance. Built by
  `make bench-v0-rejudge` once an `ANTHROPIC_API_KEY` is present.

<!-- BEGIN retrievalci scorecard -->

_Generated from `bench-v0 / local (MiniLM+Claude) + 4 hosted RAG services` — do not edit by hand._

```text
score = 100 * (0.7 * retrieval_source_recall + 0.3 * retrieval_source_precision)
```

| System | Score | Recall | Precision | p50 latency (ms) | Status |
| --- | ---: | ---: | ---: | ---: | --- |
| BM25 (lexical) | 45.7 | 47.7% | 41.0% | 2998.0 | Measured |
| Dense (vector RAG) | 45.6 | 48.5% | 38.8% | 3059.2 | Measured |
| Hybrid (BM25+Dense RRF) | 46.2 | 47.7% | 42.9% | 3323.6 | Measured |
| Rerank (Dense+LLM) | 46.3 | 48.2% | 41.7% | 3000.9 | Measured |
| Chunk-summary (Dense) | 43.2 | 45.8% | 37.1% | 3200.1 | Measured |
| Vertex AI RAG Engine | 82.6 | 94.5% | 54.7% | 358.4 | Measured |
| Bedrock KB (Cohere embed) | 82.5 | 87.9% | 70.1% | 408.1 | Measured |
| OpenAI File Search | 78.5 | 89.3% | 53.4% | 1358.3 | Measured |
| Azure AI Search (Gemini embed) | 84.0 | 90.9% | 67.8% | 408.4 | Measured |
| OmegaWiki /ask | pending | pending | pending | pending | Needs adapter |

<!-- END retrievalci scorecard -->

The bench-v0 fixture (50 questions stratified from EnterpriseRAG-Bench v1.0.0)
exercises retrieval against 81 enterprise-corpus docs across single-hop,
multi-hop, contradiction, and unanswerable facets.

**Reading the scorecard**: the ~35-point gap between local and hosted is
dominated by **embedder size**, not architecture. The local rows use
`sentence-transformers/all-MiniLM-L6-v2` (384-dim, runs free on CPU); the
hosted services use larger embedders (Gemini 3072-dim, Cohere 1024-dim,
OpenAI 1536-dim+). Swap the local embedder for a comparable-size model and
the gap narrows substantially. Architecture differences between local
systems (BM25 / dense / hybrid / rerank / chunk-summary) are second-order
at this corpus size — they all cluster within ~3 points.

**On unanswerable handling**: under real Gemini, none of the systems
(local or hosted) refuse on the 5 unanswerable questions — they all
hallucinate retrievals. `abstention_correctness=0.0` is visible in the
per-system Markdown reports next to the scorecard. This is the next
prompt-engineering / retrieval-thresholding target.

**Why no Tavily row**: [Tavily](https://tavily.com) is a web-search-as-a-service
API for grounding LLMs against the public web. It has no
upload-your-corpus endpoint, so a bench-v0 run would score ~0% recall on
synthetic enterprise documents Tavily's web crawler has never seen. It's
the wrong category for this benchmark, not the wrong product.

See [`examples/rag_eval/bench_v0/`](examples/rag_eval/bench_v0/) and
[`docs/rag_eval/results/hosted-rag-benchmark-plan.md`](docs/rag_eval/results/hosted-rag-benchmark-plan.md).

Hosted and wiki-style systems get scored the same way once they run against the
same corpus, question IDs, and citation contract. OmegaWiki can be evaluated
alongside Vertex AI RAG Engine as a concrete implementation of the
[Karpathy LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
idea: export its wiki/graph pages or adapt `/ask` outputs so RetrievalCI can
compare retrieved sources with `ground_truth_citations`.

Commercial targets worth wiring in next:

| Target | What to map into RetrievalCI |
| --- | --- |
| [Vertex AI RAG Engine](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/rag-engine/rag-overview) | `retrieveContexts` chunks or `generateContent` grounding chunks |
| [Amazon Bedrock Knowledge Bases](https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base.html) | `Retrieve` or `RetrieveAndGenerate` source results |
| [Azure AI Search](https://azure.microsoft.com/en-us/products/ai-services/ai-search/) | search, vector, hybrid, or semantic result documents |
| [OpenAI File Search](https://developers.openai.com/api/docs/guides/tools-file-search) | `file_search_call.results` and file citations |
| [OmegaWiki](https://github.com/skyllwt/OmegaWiki) | maintained wiki pages, graph entities, and answer citations |

Amazon Q Business is also RAG-based, but it is closer to a managed enterprise
assistant than a raw retriever; score it at the application boundary if you can
export cited sources. [Databricks Mosaic AI Vector Search](https://learn.microsoft.com/en-us/azure/databricks/vector-search/vector-search),
[Pinecone](https://www.pinecone.io/solutions/rag/), and
[Weaviate](https://docs.weaviate.io/weaviate/starter-guides/generative) are
commercial retrieval layers for RAG stacks and can be scored through the same
citation adapter pattern.

RetrievalCI runs two complementary checks:

- **RAG architecture eval:** compare retrieval and answer systems against a
  fixed corpus and question set.
- **Trace-state eval:** replay agent trace logs with different retrieval-state
  policies to expose zero-recall, drift, stale evidence, and false-lead capture.

Every run can produce a versioned manifest, machine-readable metrics, and a
static HTML report that works as a CI artifact.

![Inputs, project CLI, checks, and run artifact pipeline](docs/assets/retrievalci-overview.png)

## Install

RetrievalCI requires Python 3.12 or newer.

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

Provider extras are bounded to SDK major versions that RetrievalCI has adapter
coverage for. If a provider releases a new major SDK, update the backend adapter
and version bound together.

## API keys

RetrievalCI runs end-to-end with **no API keys** using the bundled mock backend
(`make smoke-rag`, `make smoke-rag-config`). Keys are only needed once you want
real LLM generation, real LLM judging, or hosted RAG service adapters.

Place keys in a `.env` file at the repo root, or export them in your shell.
RetrievalCI does not bundle a `.env` loader; use your shell or `direnv`.

**Required: none.** Smoke tests, BM25 baselines, and the mock generator/judge
pipeline all work without provider credentials.

**Optional — enables a real LLM generator or judge for local systems:**

| Backend | Variable | Used for |
| --- | --- | --- |
| Gemini (flash) | `GOOGLE_API_KEY` or `GEMINI_API_KEY` | Gemini generator (flash-lite) and embedder (public Gemini API, not Vertex AI) |
| Gemini Pro judge | `GEMINI_API_KEY_PRO` (optional override) | When `GeminiJudge` runs a Pro-family model, it prefers this key. Lets a Pro/Ultra subscription key serve judging while a separate free-tier key serves generator and embedder. Falls back to the standard chain if unset. |
| OpenAI | `OPENAI_API_KEY` | OpenAI judge today; OpenAI File Search adapter when shipped |
| Anthropic | `ANTHROPIC_API_KEY` | Claude generator and judge |
| Groq | `GROQ_API_KEY` | Groq generator and judge |

Each backend raises at construction time if its key is missing, so unused
backends never demand a key.

**Cost safety**: defaults are pinned to free-tier-eligible Gemini models
(`gemini-2.5-flash-lite` generator, `gemini-embedding-001` embedder). The
`GeminiJudge` default is `gemini-2.5-pro`, which has a tighter free-tier
daily quota (~100 RPD) — judging a 50-question benchmark may need a
Pro/Ultra subscription key on `GEMINI_API_KEY_PRO`, or a wait for the
midnight Pacific quota reset. The pinned defaults are guarded by
`test_gemini_defaults_match_free_tier_policy` so a future contributor
cannot silently regress them.

**Required only for specific hosted-RAG adapters (not yet shipped):**

These adapters are described in
[docs/rag_eval/results/hosted-rag-benchmark-plan.md](docs/rag_eval/results/hosted-rag-benchmark-plan.md).
None of them are implemented yet; the credentials below are what each adapter
will require when added.

| Adapter | Credential | Notes |
| --- | --- | --- |
| Google Vertex AI RAG Engine | GCP service account JSON (`GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json`) or Application Default Credentials from `gcloud auth application-default login` | The service account needs `roles/aiplatform.user` plus read access to the corpus bucket. A `GEMINI_API_KEY` alone is **not** sufficient — Vertex AI is a separate authentication surface. |
| Amazon Bedrock Knowledge Bases | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` (or an IAM role on the host machine) | IAM principal needs `bedrock:Retrieve` and `bedrock:RetrieveAndGenerate`. |
| Azure AI Search | `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_ADMIN_KEY` | Admin key for index management at adapter `index()` time; query key works for `answer()` only. |
| OpenAI File Search | `OPENAI_API_KEY` | Same key as the OpenAI judge above. |
| [OmegaWiki](https://github.com/skyllwt/OmegaWiki) `/ask` | Depends on deployment | OmegaWiki is self-hosted; supply the `/ask` endpoint URL and any auth header per your OmegaWiki instance. |

Hosted-RAG runs are gated by a tight default budget cap (currently $20 and 50
questions per run). Larger runs require explicit operator override; see
`retrievalci/rag_eval/hosted.py` (`RunBudget`).

**Known limitation: `RunBudget` protects against runaway *query volume* only.**
Hosted RAG services (Vertex AI RAG Engine, Bedrock Knowledge Bases, Azure AI
Search, OpenAI File Search) bill for several cost lines that fall *outside*
the per-question cap:

- **Index storage** (Spanner-hour for Vertex, OCU-hour for Bedrock with
  OpenSearch Serverless, search-unit-hour for Azure, vector-store-byte-month
  for OpenAI) accrues for as long as the provisioned index exists, regardless
  of whether queries are issued. A 50-query run can finish cleanly under the
  $20 cap and still bill more than that in storage if the index is not torn
  down.
- **One-time ingestion / embedding cost** when the corpus is first uploaded.
- **Generation cost** in Mode B (native-stack) evaluation.

Until adapter-level `teardown()` discipline and a storage-aware budget are
implemented, operators of hosted adapters must manually delete provisioned
indexes after each run. The plan tracks this as an open item; see
[docs/rag_eval/results/hosted-rag-benchmark-plan.md](docs/rag_eval/results/hosted-rag-benchmark-plan.md).

## Quick start

Run the full local check:

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

- Leader: `rag` on `retrieval_source_recall`.
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

The two checks below share the same project file and run artifact.

![Project file branching into RAG eval and trace-state eval, then merging into one run artifact](docs/assets/retrievalci-evaluation-modes.png)

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

## Trace-state eval

Trace-state policies control what each replayed retrieval call can see, such as
the recorded prompt, only the current query, or compacted recent answer state.

![Agent trace, state policy, retriever, and trace metrics flow](docs/assets/retrievalci-trace-state-eval.png)

Normalize a span export:

```bash
.venv/bin/retrievalci traces normalize \
  --source otel \
  --input examples/otel.spans.demo.json \
  --out /tmp/retrievalci-traces.demo.jsonl \
  --require-gold
```

Replay retrieval-state policies:

```bash
.venv/bin/retrievalci traces eval \
  --traces /tmp/retrievalci-traces.demo.jsonl \
  --corpus examples/corpus.demo.jsonl \
  --out /tmp/retrievalci-trace-report \
  --k 1 \
  --policies recorded,query_only,last_answer_x3,compact_state \
  --gate-policy last_answer_x3 \
  --min-recall-at-5 0.90
```

Use `--retriever-url` when you want RetrievalCI to call a deployed retriever
instead of the local BM25 replay baseline.

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
