# RetrievalCI Hosted RAG Benchmark Plan

## Summary And Critique

The goal is to compare local RAG baselines, hosted/commercial RAG systems, and
Karpathy-style wiki systems on the same corpus, questions, and citation
contract. The original direction is right, but the benchmark needs tighter
fairness controls before any public comparison is credible.

Key critique:

- Start with 50 human-vetted questions, not 500-1,000. Scale after the harness
  proves it can produce comparable rows.
- Keep each corpus as a separate leaderboard. Do not average unrelated corpora
  into one global score.
- Separate retriever-only fairness from native-stack product quality.
- Use retriever-only mode for the public headline score.
- Require source-ID manifests for hosted systems, or hosted chunk IDs will not
  match `ground_truth_citations`.
- Use relative regression gates first; absolute quality floors need calibration.

## Evaluation Modes

### Mode A: Retriever-Only Fairness

Every system returns ranked source evidence. RetrievalCI then uses the same
answer generator and scoring path for all systems.

Use this mode for the README headline scorecard.

### Mode B: Native Stack

Each service uses its native answering path:

- Vertex AI RAG Engine generation and grounding.
- Bedrock `RetrieveAndGenerate`.
- OpenAI File Search with Responses API generation.

Report this separately as product behavior, not as the headline retrieval score.

## Schema And Interface Changes

Extend `QAItem` with backward-compatible defaults:

- `corpus_id: str | None`
- `facets: tuple[str, ...] = ()`
- `unanswerable: bool = False`
- `expected_abstain: bool = False`
- `distractor_citations: tuple[str, ...] = ()`
- `temporal_anchor: str | None = None`
- `authored_by: str | None = None`
- `verified_by: str | None = None`

Extend `SystemAnswer` with backward-compatible defaults:

- `retrieved_sources: tuple[Citation, ...] = ()`
- `answer_citations: tuple[Citation, ...] = ()`
- `cost_usd: float | None = None`
- `corpus_version_hash: str | None = None`
- `index_build_id: str | None = None`
- `generator_model_id: str | None = None`
- `meta: dict[str, object] = {}`

During transition, retrieval metrics should use `retrieved_sources` when it is
present and fall back to the existing `citations` field.

Add a hosted-system protocol on top of the current `System.answer(question)`
shape:

```python
class HostedSystem(System):
    def index(self, corpus_dir: Path, corpus_version_hash: str) -> IndexHandle: ...
    def chunk_manifest(self) -> dict[str, str]: ...
    def estimate_cost(self, n_questions: int) -> float: ...
```

The manifest maps service-level IDs or URIs back to repo-relative source paths:

```json
{
  "vertex://rag-corpus/.../chunk-123": "examples/rag_eval/corpus/security.md",
  "s3://bucket/docs/security.md#chunk-4": "examples/rag_eval/corpus/security.md"
}
```

Default matching policy: exact normalized source path after manifest mapping.
Do not use fuzzy matching in benchmark v1.

## Dataset Plan

### bench-v0

Anchor on a public, MIT-licensed RAG benchmark — **EnterpriseRAG-Bench**
([Onyx, v1.0.0](https://github.com/onyx-dot-app/EnterpriseRAG-Bench)) —
rather than hand-authoring questions. ERB ships 500 questions across 10
upstream `question_type` categories with empirically-verified structure:
the `conflicting_info` slice cites exactly 2 docs per question (true
cross-source contradiction), `info_not_found` always cites 0 docs (true
unanswerable from corpus), and `project_related` / `completeness` reliably
cite 2-10 docs (real multi-document reasoning).

Built by `scripts/import_third_party_examples.py bench-v0` to
`examples/rag_eval/bench_v0/`. Stratified sample (deterministic by
question_id sort):

| Stratum | Count | ERB question_types |
| --- | --- | --- |
| single_hop | 25 | `basic`, `semantic` |
| multi_hop | 15 | `project_related`, `completeness` |
| contradiction | 5 | `conflicting_info` |
| unanswerable | 5 | `info_not_found` (`unanswerable=True` + `expected_abstain=True` set) |

Why adopt-not-author:

- ERB is MIT, so freely uploadable to Vertex / Bedrock / Azure / OpenAI for
  Mode A scoring.
- ERB has public leaderboard numbers for Vertex AI RAG Engine and OpenAI
  File Search (no Bedrock yet), enabling cross-vendor sanity-check of
  RetrievalCI's measured numbers.
- Avoids the unconscious author-bias risk of hand-authoring 50 questions
  against a corpus the local systems already index well.

Known limitations of the ERB anchor:

- **Multi-source restatement is modest.** Only a few corpus docs are cited
  by more than one question, so Karpathy-style wiki systems get limited
  opportunity to compound restated facts across sources. The wiki
  hypothesis is best tested against a separate corpus designed for
  restatement; bench-v0 will likely show wiki systems under-perform on
  this enterprise data.
- **Subset, not full ERB.** The 50-question stratified sample is not
  directly comparable to the full 500-question Onyx leaderboard scores
  for Vertex / OpenAI; bench-v0 numbers should be labelled
  "RetrievalCI-ERB-50-subset" rather than implying full ERB compatibility.
  Expanding to the full 500 lives in bench-v1 / bench-v2.
- **Distractor density is corpus-bounded.** Only the docs cited by the
  selected questions are imported (~80 docs). A real test of negative
  rejection wants thousands of distractor docs the system might wrongly
  retrieve; this is a known gap.

### bench-v1

Expand to 150 vetted questions after `bench-v0` produces stable local and Vertex
rows. Source: continue the stratified sampling from ERB
(use additional `project_related` / `completeness` for multi_hop; consider
`constrained` for partial multi-doc; sample the full 20 `conflicting_info` and
all 20 `info_not_found`). 

Use `bench-v1` for public hosted comparisons.

### bench-v2

Use the full 500-question EnterpriseRAG-Bench v1.0.0 release with the full
~512k-document corpus. This is the smallest set that lets RetrievalCI numbers
be cross-validated against the public Onyx ERB leaderboard for Vertex AI and
OpenAI File Search. Requires solving the corpus-distribution problem (the
full ERB corpus is too large to commit to the repo; lives under `data/`
gitignored, fetched on demand by the import script).

## Adapter Plan

Implement adapters in this order.

1. Vertex AI RAG Engine
   - Mode A: map `retrieveContexts` chunks to source paths.
   - Mode B: map `generateContent` grounding chunks to source paths.
   - Require corpus hash and completed ingestion before evaluation.

2. Amazon Bedrock Knowledge Bases
   - Mode A: map `Retrieve` results.
   - Mode B: map `RetrieveAndGenerate` references.
   - Record chunking strategy and vector store provider in `meta`.

3. Azure AI Search
   - Mode A first: keyword, vector, hybrid, and semantic retrieval as separate
     rows if needed.
   - Native mode only when paired with a fixed generator configuration.
   - Record SKU, region, semantic configuration, and vector settings.

4. OpenAI File Search
   - Mode A: use file-search results as retrieved sources.
   - Mode B: use generated answer plus file citation annotations.
   - Record vector store ID and OpenAI chunking caveat.

## Scoring

Headline retrieval score:

```text
score = 100 * (0.7 * retrieval_source_recall + 0.3 * retrieval_source_precision)
```

Report by corpus, system, and mode:

- retrieval recall and precision.
- MRR@k.
- binary nDCG@k.
- distractor rate.
- answer citation precision and recall.
- must-include match.
- must-not violations.
- abstention correctness.
- contradiction awareness.
- p50 and p95 latency.
- cost per question.

LLM judge metrics are secondary:

- Blind system labels before judging.
- Pin judge model, prompt, and version in the run manifest.
- Manually spot-check 10% of `bench-v1` before publishing claims.

## Gates

PR-blocking gates should be relative to the accepted baseline:

- `retrieval_source_recall` drop <= 0.02 absolute.
- `answer_citation_recall` drop <= 0.03 absolute.
- p95 latency regression <= 25%.
- cost per question regression <= 20%.

Full hosted benchmarks should not run on every PR. They should run manually or
on a schedule with a hard cost cap.

## MVP Sequence

1. Extend schemas and metrics fallback behavior. ✓ (shipped)
2. Add fake hosted adapter tests for manifest mapping and cost caps. ✓ (shipped)
3. Build `bench-v0` via `scripts/import_third_party_examples.py bench-v0`. ✓
   (50 questions, 81 corpus docs, stratified from EnterpriseRAG-Bench).
4. Re-baseline all local systems on bench-v0.
   - Mock-backend baseline ✓ at `baselines/rag/bench_v0.json` (all 7 systems).
   - Real Gemini baseline ⏳ at `baselines/rag/bench_v0_gemini.json` (5 light
     systems; claim_rag and wiki_pages skipped — index-time LLM extraction
     exceeds free-tier daily quota even at Ultra subscription).
5. Implement Vertex AI RAG Engine adapter. ⛔ Blocked on GCP service account.
6. Publish first hosted Mode A row. ⛔ Blocked on step 5.
7. Expand to `bench-v1` (150 questions, ERB sampling continued).
8. Add Bedrock, Azure, OpenAI File Search adapters. ⛔
   Mostly blocked on credentials.
9. Generate README scorecard from baseline JSON. ✓ (`retrievalci report
   scorecard --input ... --target README.md`; marker-based injection at
   `<!-- BEGIN/END retrievalci scorecard -->`).

Bonus shipped (not in original plan):
- `retrievalci rag rejudge` — re-score an existing report with a different
  judge backend (e.g. Claude judging Gemini-generated answers) without
  re-running the underlying generators. Preserves cross-model evaluation
  independence and avoids burning generator quota on judge-only refreshes.
- `abstention_correctness` metric — rewards systems that correctly refuse
  unanswerable questions (~50% on bench-v0 mock baseline for `wiki_pages`
  / `claim_rag`; 0% for retrieval-only systems that have no refusal logic).
- `make bench-v0`, `make bench-v0-rejudge`, `make bench-v0-scorecard`
  targets in the Makefile for one-command runs.
4. Re-baseline all local systems:
   - `bm25`
   - `hybrid_rag`
   - `rag`
   - `claim_rag`
   - `wiki_pages`
   - `rerank_rag`
   - `chunk_summary_rag`
5. Implement Vertex AI RAG Engine adapter.
6. Publish the first real hosted Mode A row only after Vertex runs on `bench-v0`.
7. Expand to `bench-v1` before adding broad commercial claims.
8. Add Bedrock, Azure, OpenAI File Search.
9. Generate README scorecard from benchmark JSON rather than hand-editing
   hosted scores.

## Known Limitations (Open Items)

- **Storage lifecycle is not budgeted.** `RunBudget` caps per-question spend
  and total query count but does not protect against hosted index storage
  costs that accrue per hour after the run completes. Until adapters
  implement a `teardown()` discipline and the harness enforces post-run
  index deletion, operators must manually delete provisioned indexes after
  each hosted run. Vertex (Spanner-hour), Bedrock (OpenSearch OCU-hour),
  Azure (search-unit-hour), and OpenAI (vector-store-byte-month) all bill
  for index existence, not just for queries.
- **Pricing is adapter-defined, not price-table-versioned.** Each adapter
  returns `estimate_cost(n_questions) -> float`. Audit-grade pricing should
  emit a structured usage breakdown (embedding tokens, retrieval calls,
  rerank calls, generation tokens, resource-hours) applied against a
  versioned price table the harness owns. Deferred to a follow-up.
- **Manifest provenance is minimal.** The current manifest carries adapter
  name, full corpus_version_hash (SHA-256), and the chunk-ID mapping.
  Provider region, adapter version, embedding model, reranker, and index
  expiry should be added before public hosted comparisons ship.

## Acceptance Criteria

- Existing smoke fixtures still load.
- `bench-v0` produces reproducible JSON and Markdown reports.
- Source manifests map hosted service IDs to `ground_truth_citations`.
- One hosted adapter produces a comparable Mode A row before public hosted
  scores are shown.
- README headline scorecard is generated from measured benchmark output.

## Assumptions

- The first controlled corpus is local and safe to upload to hosted RAG systems.
- Public README scores should prefer truth over breadth; pending rows are better
  than fabricated hosted results.
- Mode A is the default public comparison.
- Absolute quality thresholds remain advisory until `bench-v1` has enough
  historical runs to calibrate them.
