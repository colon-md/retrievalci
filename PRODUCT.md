# Product Direction

## Users

RetrievalCI is for RAG engineers, AI platform teams, and evaluation owners who need to
understand retrieval failures before a change ships. They use it during local
experiments, pull request review, and CI gate analysis.

## Product Purpose

RetrievalCI is the practical CI layer for retrieval systems. It diagnoses whether
failures come from architecture choice, retrieval state, corpus shape, answer
synthesis, citations, or noisy memory. Success means a team can see what regressed,
inspect concrete failure examples, and choose the next experiment without reading raw
JSON by hand.

## Positioning

RetrievalCI should be positioned as a trace-state and retriever-regression tool,
not as a broad replacement for Ragas, TruLens, DeepEval, Phoenix, Langfuse, or
LangSmith. The strongest differentiated claim is:

> RetrievalCI tells a RAG team which agent state should be sent into retrieval,
> and whether a change increased zero-recall, drift, stale evidence, or
> false-lead capture against the team's own retriever.

RAG architecture comparison remains useful as a secondary local experiment mode.
The primary focus is trace ingestion, production retriever replay, regression
gates, and reviewable run artifacts.

## Ideal Customer

- AI platform teams that own retrieval quality for internal or customer-facing
  assistants.
- RAG engineers who already have observability traces but lack state-policy
  diagnostics.
- Evaluation owners who need PR checks and reproducible artifacts before a
  retrieval or agent change ships.

## Distribution Shape

Open-source core:

- local trace normalization;
- BM25 replay baseline;
- HTTP retriever replay;
- RAG architecture smoke comparisons;
- run manifests and static HTML reports.

Future hosted layer:

- hosted run history and baselines;
- GitHub pull-request checks;
- team dashboards and trend alerts;
- managed trace connectors for Phoenix/OpenTelemetry, Langfuse, LangSmith, and
  vendor-specific exports;
- secure retention, redaction, and access controls for customer traces.

## Brand Personality

Precise, calm, evidence-first.

## Anti-references

RetrievalCI should not feel like a marketing landing page, an ornamental analytics
dashboard, or an opaque judge-score scoreboard. It should avoid decorative chrome,
oversized hero metrics, vague health scores, and visuals that hide the actual
question, source, policy, or system that failed.

## Design Principles

- Findings before tables: lead with the decision a reviewer needs to make.
- Evidence stays inspectable: every conclusion should point back to systems, policies,
  questions, retrieved IDs, or metric deltas.
- CI-first, local-first: artifacts should work without a server, external assets, or
  proprietary storage.
- Separate failure modes: architecture failures, state failures, citation failures, and
  generation failures should not collapse into one generic quality score.
- Dense but calm: expert users need scan speed, stable structure, and restrained visual
  treatment.

## Accessibility & Inclusion

Reports should target WCAG AA contrast, keyboard-readable structure, and no color-only
status encoding. Motion should be minimal and nonessential. Tables should retain clear
text labels and sensible reading order when copied or viewed without styling.
