.PHONY: install install-dev install-providers test lint check smoke smoke-rag smoke-rag-config smoke-rag-compare smoke-report smoke-runs smoke-project smoke-traces clean

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
	$(BIN)/python -m ruff check searchtrace tests scripts

check: lint test

smoke: smoke-rag smoke-rag-config smoke-rag-compare smoke-traces smoke-report smoke-runs smoke-project

smoke-rag:
	$(BIN)/searchtrace rag run \
	  --repo-root $(CURDIR) \
	  --questions examples/rag_eval/questions.jsonl \
	  --corpus-glob 'examples/rag_eval/corpus/*.md' \
	  --backend mock \
	  --judge mock \
	  --system rag \
	  --system bm25 \
	  --system hybrid_rag \
	  --max-chunks 20 \
	  --primary-metric retrieval_source_recall \
	  --report-json /tmp/searchtrace-rag-smoke.json \
	  --report-md /tmp/searchtrace-rag-smoke.md

smoke-rag-config:
	$(BIN)/searchtrace rag run \
	  --config examples/rag_eval/smoke.yaml \
	  --repo-root $(CURDIR)

smoke-rag-compare: smoke-rag
	$(BIN)/searchtrace rag compare \
	  --baseline /tmp/searchtrace-rag-smoke.json \
	  --candidate /tmp/searchtrace-rag-smoke.json \
	  --metric retrieval_source_recall \
	  --max-drop 0

smoke-report: smoke-rag smoke-traces
	$(BIN)/searchtrace report build \
	  --rag-report /tmp/searchtrace-rag-smoke.json \
	  --baseline-rag-report /tmp/searchtrace-rag-smoke.json \
	  --trace-metrics /tmp/searchtrace-trace-report/metrics.json \
	  --trace-per-turn /tmp/searchtrace-trace-report/per_turn.jsonl \
	  --out /tmp/searchtrace-report.html

smoke-runs:
	rm -rf /tmp/searchtrace-runs-smoke
	$(BIN)/searchtrace runs create \
	  --registry /tmp/searchtrace-runs-smoke \
	  --repo-root $(CURDIR) \
	  --name smoke \
	  --rag-config examples/rag_eval/smoke.yaml \
	  --trace-input examples/traces.demo.jsonl \
	  --trace-corpus examples/corpus.demo.jsonl \
	  --trace-k 1
	$(BIN)/searchtrace runs list --registry /tmp/searchtrace-runs-smoke

smoke-project:
	rm -rf /tmp/searchtrace-project-smoke
	$(BIN)/searchtrace project run --config examples/searchtrace.project.yaml
	$(BIN)/searchtrace runs list --registry /tmp/searchtrace-project-smoke

smoke-traces:
	$(BIN)/searchtrace traces normalize \
	  --input examples/spans.demo.jsonl \
	  --out /tmp/searchtrace-traces.demo.jsonl \
	  --require-gold
	$(BIN)/searchtrace traces normalize \
	  --source otel \
	  --input examples/otel.spans.demo.json \
	  --out /tmp/searchtrace-otel-traces.demo.jsonl \
	  --require-gold
	$(BIN)/searchtrace traces eval \
	  --traces /tmp/searchtrace-traces.demo.jsonl \
	  --corpus examples/corpus.demo.jsonl \
	  --out /tmp/searchtrace-trace-report \
	  --k 1 \
	  --policies recorded,query_only,last_answer_x3,compact_state,public_trace \
	  --gate-policy last_answer_x3 \
	  --min-recall-at-5 0.90 \
	  --max-stale-at-1 0.05

clean:
	find searchtrace tests scripts -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .mypy_cache
