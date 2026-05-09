# SearchTrace CI

SearchTrace can run as a local CI gate without a hosted service. The repository
workflow in `.github/workflows/searchtrace-ci.yml` does four things:

1. installs the package with dev extras;
2. runs `ruff` and `pytest`;
3. runs `searchtrace ci run --config examples/searchtrace.ci.yaml`;
4. uploads `.searchtrace/runs` as the review artifact.

The CI project file combines the two product checks:

- RAG architecture regression against `baselines/rag/smoke.json`;
- trace-state policy gating against `examples/otel.spans.demo.json`.

The bundled smoke data is deliberately small and public. RAG questions and
corpus files live under `examples/rag_eval/`; trace replay fixtures live under
`examples/`.

Provider SDKs are intentionally not installed in the default CI workflow. The
bundled examples use mock/provider-free backends, so the standard gate stays
fast and does not pull large optional dependencies. Install `.[providers]` only
in jobs that call real LLM or embedding providers. Provider extras are bounded
to the SDK major versions SearchTrace currently supports; update those bounds
only after validating the backend adapters against the new SDK APIs.

## Failure Rules

CI fails when any of these happen:

- lint or tests fail;
- the candidate RAG report drops below the baseline by more than `rag.max_drop`
  on `rag.regression_metric`;
- the configured trace policy violates a trace gate such as minimum Recall@5 or
  maximum zero-recall.

The run still writes a manifest and HTML report on failure. The GitHub workflow
uploads those artifacts so a reviewer can inspect concrete examples instead of
reading raw logs.

## Baseline Convention

Committed RAG baselines live under `baselines/rag/`. The current smoke baseline
is:

```text
baselines/rag/smoke.json
```

Update a baseline only after the new behavior is accepted. Regenerate the smoke
baseline with:

```bash
.venv/bin/searchtrace rag run \
  --config examples/rag_eval/smoke.yaml \
  --repo-root "$PWD" \
  --report-json baselines/rag/smoke.json \
  --report-md /tmp/searchtrace-rag-baseline.md \
  --primary-metric retrieval_source_recall
```

For a real team, replace the smoke config with a project-specific corpus,
question set, trace export, and accepted baseline report. Keep baselines small
enough to review in PRs; put large or sensitive artifacts in external storage
and point the project config at the downloaded path during CI.

## Artifact Policy

`.searchtrace/` is ignored locally. CI uploads `.searchtrace/runs` after each
run. The default CI example keeps debug artifacts disabled so uploaded artifacts
stay compact and avoid per-turn retrieved text.

> Warning: enable `artifacts.debug_artifacts: true` only when the CI environment
> is allowed to store rendered queries, retrieved IDs, snippets, and Markdown
> reports.

## Publishing

`.github/workflows/publish.yml` builds source and wheel distributions and
publishes them to PyPI through trusted publishing. Configure a PyPI project for
`searchtrace`, then map its trusted publisher to this repository and the `pypi`
GitHub environment before publishing a release.
