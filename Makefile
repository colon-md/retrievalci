.PHONY: install install-dev install-providers test lint check smoke smoke-rag smoke-rag-config smoke-rag-compare smoke-report smoke-runs smoke-project smoke-traces bench-v0 bench-v0-mock bench-v0-rejudge bench-v0-scorecard clean

PYTHON ?= python
VENV ?= .venv
BIN := $(VENV)/bin

install:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/python -m pip install --upgrade pip
	$(BIN)/python -m pip install -e .

install-dev:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/python -m pip install --upgrade pip
	$(BIN)/python -m pip install -e '.[dev]'

install-providers: install-dev
	$(BIN)/python -m pip install -e '.[providers]'

test:
	$(BIN)/python -m pytest -q

lint:
	$(BIN)/python -m ruff check retrievalci tests scripts

check: lint test

smoke: smoke-rag smoke-rag-config smoke-rag-compare smoke-traces smoke-report smoke-runs smoke-project

smoke-rag:
	$(BIN)/retrievalci rag run \
	  --repo-root $(CURDIR) \
	  --questions examples/rag_eval/questions.jsonl \
	  --corpus-glob 'examples/rag_eval/corpus/*.md' \
	  --backend mock \
	  --judge mock \
	  --system dense_rag \
	  --system bm25_lexical \
	  --system hybrid_rrf \
	  --max-chunks 20 \
	  --primary-metric retrieval_source_recall \
	  --report-json /tmp/retrievalci-rag-smoke.json \
	  --report-md /tmp/retrievalci-rag-smoke.md

smoke-rag-config:
	$(BIN)/retrievalci rag run \
	  --config examples/rag_eval/smoke.yaml \
	  --repo-root $(CURDIR)

smoke-rag-compare: smoke-rag
	$(BIN)/retrievalci rag compare \
	  --baseline /tmp/retrievalci-rag-smoke.json \
	  --candidate /tmp/retrievalci-rag-smoke.json \
	  --metric retrieval_source_recall \
	  --max-drop 0

smoke-report: smoke-rag smoke-traces
	$(BIN)/retrievalci report build \
	  --rag-report /tmp/retrievalci-rag-smoke.json \
	  --baseline-rag-report /tmp/retrievalci-rag-smoke.json \
	  --trace-metrics /tmp/retrievalci-trace-report/metrics.json \
	  --trace-per-turn /tmp/retrievalci-trace-report/per_turn.jsonl \
	  --out /tmp/retrievalci-report.html

smoke-runs:
	rm -rf /tmp/retrievalci-runs-smoke
	$(BIN)/retrievalci runs create \
	  --registry /tmp/retrievalci-runs-smoke \
	  --repo-root $(CURDIR) \
	  --name smoke \
	  --rag-config examples/rag_eval/smoke.yaml \
	  --trace-input examples/traces.demo.jsonl \
	  --trace-corpus examples/corpus.demo.jsonl \
	  --trace-k 1
	$(BIN)/retrievalci runs list --registry /tmp/retrievalci-runs-smoke

smoke-project:
	rm -rf /tmp/retrievalci-project-smoke
	$(BIN)/retrievalci project run --config examples/retrievalci.project.yaml
	$(BIN)/retrievalci runs list --registry /tmp/retrievalci-project-smoke

smoke-traces:
	$(BIN)/retrievalci traces normalize \
	  --input examples/spans.demo.jsonl \
	  --out /tmp/retrievalci-traces.demo.jsonl \
	  --require-gold
	$(BIN)/retrievalci traces normalize \
	  --source otel \
	  --input examples/otel.spans.demo.json \
	  --out /tmp/retrievalci-otel-traces.demo.jsonl \
	  --require-gold
	$(BIN)/retrievalci traces eval \
	  --traces /tmp/retrievalci-traces.demo.jsonl \
	  --corpus examples/corpus.demo.jsonl \
	  --out /tmp/retrievalci-trace-report \
	  --k 1 \
	  --policies recorded,query_only,last_answer_x3,compact_state,public_trace \
	  --gate-policy last_answer_x3 \
	  --min-recall-at-5 0.90 \
	  --max-stale-at-1 0.05

# bench-v0: the 50-question ERB-derived hosted-RAG benchmark fixture.
# bench-v0-mock         → all 7 local systems, mock backend (no API keys).
# bench-v0              → 5 light systems, real Gemini backend, mock judge.
#                         Requires GEMINI_API_KEY (or GOOGLE_API_KEY) in .env.
# bench-v0-rejudge      → re-score the real-backend baseline with Claude judge.
#                         Requires ANTHROPIC_API_KEY in .env.
# bench-v0-scorecard    → render the README scorecard from a baseline JSON.
bench-v0-mock:
	$(BIN)/retrievalci rag run \
	  --config examples/rag_eval/bench_v0/baseline.yaml \
	  --repo-root $(CURDIR)

bench-v0:
	$(BIN)/retrievalci rag run \
	  --config examples/rag_eval/bench_v0/baseline_gemini.yaml \
	  --repo-root $(CURDIR)

bench-v0-rejudge:
	$(BIN)/retrievalci rag rejudge \
	  --input baselines/rag/bench_v0_gemini.json \
	  --questions examples/rag_eval/bench_v0/questions.jsonl \
	  --judge claude \
	  --output-json baselines/rag/bench_v0_gemini_claude.json \
	  --output-md baselines/rag/bench_v0_gemini_claude.md

bench-v0-scorecard:
	$(BIN)/retrievalci report scorecard \
	  --input $(or $(BENCH_BASELINE),baselines/rag/bench_v0.json) \
	  --target README.md \
	  --label "$(or $(BENCH_LABEL),bench-v0)" \
	  --hosted-placeholder "Google Vertex AI RAG Engine:Needs adapter" \
	  --hosted-placeholder "Amazon Bedrock Knowledge Bases:Needs adapter" \
	  --hosted-placeholder "Azure AI Search:Needs adapter" \
	  --hosted-placeholder "OpenAI File Search:Needs adapter"

# Distillation-cost ablation. Three runs share corpus + questions + embedder,
# differ only in how the wiki_pages system enriches its embedding text:
#   - ablation-bge      : free baseline (bge-large embedder, no LLM enrichment)
#   - ablation-prose    : prose synthesis  (default — ~600 output tokens/page)
#   - ablation-tag-list : tag-list synthesis (~200 output tokens/page; ~5x cheaper)
# Compare the three JSON reports by retrieval_source_recall / precision to
# decompose the wiki retrieval lift into free vs paid components.
ablation-distill:
	$(BIN)/retrievalci rag run \
	  --config examples/rag_eval/bench_v0/ablation_distill_cost.yaml \
	  --repo-root $(CURDIR) \
	  --report-json baselines/rag/ablation_bge_baseline.json \
	  --report-md   baselines/rag/ablation_bge_baseline.md
	$(BIN)/retrievalci rag run \
	  --config examples/rag_eval/bench_v0/ablation_distill_cost.yaml \
	  --repo-root $(CURDIR) \
	  --wiki-synthesis-mode prose \
	  --report-json baselines/rag/ablation_prose.json \
	  --report-md   baselines/rag/ablation_prose.md
	$(BIN)/retrievalci rag run \
	  --config examples/rag_eval/bench_v0/ablation_distill_cost.yaml \
	  --repo-root $(CURDIR) \
	  --wiki-synthesis-mode tag_list \
	  --report-json baselines/rag/ablation_tag_list.json \
	  --report-md   baselines/rag/ablation_tag_list.md

clean:
	find retrievalci tests scripts -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .mypy_cache
