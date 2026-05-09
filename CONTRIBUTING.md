# Contributing

SearchTrace is intended to stay local-first, provider-optional, and safe to run
on public examples. Keep changes small, testable, and tied to a concrete
diagnostic workflow.

## Setup

```bash
python -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
```

Install provider SDKs only when you need real LLM or embedding backends:

```bash
.venv/bin/python -m pip install -e '.[providers]'
```

## Checks

Run the standard local gate before opening a pull request:

```bash
make check
make smoke
```

For third-party example fixtures:

```bash
.venv/bin/searchtrace rag run --config examples/third_party/wixqa/smoke.yaml
.venv/bin/searchtrace rag run --config examples/third_party/enterprise_rag_bench_github/smoke.yaml
```

## Data And Secrets

Do not commit `.env`, credentials, customer data, local run outputs, or personal
planning notes. The repo ignores local-only paths such as `.env`,
`.searchtrace/`, `data/third_party/`, `goal.md`, `roadmap.md`, and local
RAG research archives under `docs/rag_eval/`.

Bundled third-party fixtures must keep their `UPSTREAM.md` attribution and
license notes. Large refreshed datasets should stay under ignored
`data/third_party/`.

## Style

- Prefer the existing package and CLI patterns over new abstractions.
- Keep provider SDKs optional; mock/provider-free examples should keep working
  with `.[dev]` only.
- Add focused tests for new metrics, schemas, CLI behavior, or example fixtures.
