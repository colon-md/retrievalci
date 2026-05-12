# RAG Research Log — wiki+RAG ablation chain

Snapshot of the RAG architecture investigation that produced RetrievalCI's
current wiki+RAG variant. Eight rounds of pre-registered ablations on
Karpathy's "LLM Wiki" hypothesis across two corpora, captured at 2026-05-08.
The top-level [`README.md`](../../README.md) "Research findings (so far)"
section summarises the conclusions; this log contains the chronological arc,
costs, and decision points.

For the current public product workflow, start with the top-level `README.md`,
`docs/CI.md`, and `docs/trace_eval/README.md`.

## TL;DR

After 3 weeks of work and 4 rounds of pre-registered ablations, here's where the hypothesis stands:

- **Karpathy's "LLM reads pre-synthesized prose at query time" framing — falsified on K8s.** The LLM doesn't benefit from prose at answer time once retrieval lands the right pages.
- **The wiki architecture's actual win is at retrieval, not at generation.** Synthesis-derived prose makes embedding text materially richer, which improves retrieval. The page aggregation structure isn't load-bearing on its own.
- **Of the +0.30 retrieval lift on K8s, ~50% is attributable to term-density** (replicable for free with `entity name × 10` padding), **~50% to LLM-synthesized semantic content** (genuinely synthesis-derived).
- **A stronger embedder beats the wiki-synthesis architecture for free.** `bge-large-en` over the same wiki prose scores 0.900 vs MiniLM-L6-v2's 0.850 on K8s `must_include_match` — same content, different embedder, +0.05 lift at no API cost.
- **Multi-corpus expansion is justified** to test if these findings generalize beyond engineering documentation. Pre-registered V2 plan covers 3 corpora × 6 conditions × 5 seeds at expected $45, hard cap $150.

Estimated cumulative API spend so far: **~$15** across all ablations and pilots.

## What was being tested

Andrej Karpathy proposed the "LLM Wiki" pattern: pre-compute a synthesized wiki page per entity at ingest time, retrieve those pages at query time, and let the LLM read coherent pre-synthesized knowledge instead of raw retrieved chunks. The argument: amortize knowledge-summarization at write-time so query-time gets richer context.

RetrievalCI tests this against vanilla RAG and two intermediate systems (claim-RAG: extract triples, retrieve them; wiki-pages: aggregate triples into entity pages with LLM synthesis).

## Architecture built

| Module | Purpose |
|---|---|
| `retrievalci/rag_eval/claims/types.py` | Pydantic v2 substrate: `Claim`, `ProofSet`, `Evidence`. Frozen, content-hashed, ACL-aware. |
| `retrievalci/rag_eval/claims/builds.py` | `KnowledgeBuild` — versioned snapshots, on-disk persistence (`save_build` / `load_build` / `load_chain`). |
| `retrievalci/rag_eval/predicates/__init__.py` | Closed predicate vocabulary loader from `retrievalci/rag_eval/schemas/predicates.yml`. |
| `retrievalci/rag_eval/extraction/__init__.py` | Stopword + canonicalization + LLM-based subject_type inference. |
| `retrievalci/rag_eval/systems/rag.py` | Vanilla RAG: chunk → embed → top-k cosine → generate. |
| `retrievalci/rag_eval/systems/claim_rag.py` | ClaimRAG: extract triples per chunk → retrieve claims → generate. |
| `retrievalci/rag_eval/systems/wiki_pages.py` | WikiPagesSystem: aggregate claims into entity pages → LLM synthesis at ingest → page-level retrieval → generate. **Decoupled flags `embed_uses_prose` and `answer_uses_prose` for mechanism isolation.** |
| `retrievalci/rag_eval/systems/bm25.py` | In-house BM25 baseline. |
| `retrievalci/rag_eval/systems/rerank_rag.py` | RAG with cross-encoder reranking. |
| `retrievalci/rag_eval/systems/hybrid_rag.py` | BM25 + dense via reciprocal rank fusion. |
| `retrievalci/rag_eval/runner.py` | Eval harness. Backends: mock, gemini, claude, groq. Judges: same. Paired bootstrap CIs. |

149 tests passing at the time of this snapshot (current: 253), ruff clean.

## Findings — the chronological arc

### Round 1 (2026-05-06): First eval, internal docs corpus
Wiki+synthesis on the engine's own internal documentation scored worse than RAG. RAG=0.630, ClaimRAG=0.490, WikiPages=0.445 on `must_include_match`. **Wiki LOSES on its own corpus.**

### Round 2: Diagnostic — entity proliferation
Found 977 wiki pages from 328 chunks, with **75% singletons** (1 claim/page). Entity extraction was creating one-off entities like `"subject"` (84 claims — a meta-vocabulary leak from the extraction prompt).

### Round 3 (Tier A): Extraction-quality remediation
Pre-registered three sub-clauses (`docs/rag_eval/pre_registrations/PRE_REGISTRATION.md`, internal). Added stopword filter, subject canonicalization, type inference, drop-singletons threshold. Re-ran on the internal docs corpus.

**Result: 2 of 3 sub-clauses FAILED.** Wiki dropped further (0.445 → 0.295) because the singleton pages we dropped had been carrying answer-relevant content. The internal docs corpus didn't have the multi-source coverage the wiki hypothesis assumes. **Provisional falsification — domain-specific.**

### Round 4 (Tier A-Replicated): K8s corpus
Same code, different corpus (Kubernetes documentation, 174 docs, 2,375 chunks). Hand-authored 10 questions on K8s primitives.

**Result: ALL 3 sub-clauses PASSED.** Wiki=0.950 vs ClaimRAG=0.600 (+0.350, sig CI [+0.150, +0.600]). **Wiki wins on a corpus where compounding genuinely exists.**

The corpus difference is decisive: the internal docs describe each fact once; K8s docs describe the same entity (Pod, Service, Deployment) from multiple angles across many sources. Wiki architecture matches the latter, not the former.

### Round 5: Codex review of K8s result
Codex flagged a length confound — wiki's 1898-char answers are ~3x longer than ClaimRAG's 642-char answers, naturally hitting more must-include terms. Empirically reconstructed: at 642-char cap, wiki advantage drops from +0.350 to +0.150 (CI [-0.10, +0.40], not significant). **Half the K8s win is length-driven.**

### Round 6: Synthesis ablation (4-condition mechanism isolation)
Decoupled wiki rendering: `embed_uses_prose` × `answer_uses_prose`. Same extracted claims, same pages, only flags vary across 4 conditions:

| Condition | embed | answer | wiki score |
|---|---|---|---|
| A | prose | prose | 0.800 |
| B | prose | listing | 0.850 |
| C | listing | prose | 0.500 |
| D | listing | listing | 0.550 |

**Decomposition:** B−D = +0.30 (synthesis at retrieval), A−B = −0.05 (synthesis at answer = ~0). **Synthesis helps only at retrieval, not at answer time.**

### Round 7: Term-padding follow-up
Added entity name × 10 padding to listing-only embed text (no LLM synthesis). Wiki score = 0.700, vs 0.550 baseline (D). **Term-padding alone captures +0.15 of the +0.30 prose embedding gain.** Synthesis carries genuine semantic signal beyond term density (~+0.15 residual), but the mechanism is half-replicable for free.

### Round 8 (Tier C V2 K8s pilot): cost-benefit comparison
Pre-registered 6 conditions (`docs/rag_eval/pre_registrations/PRE_REGISTRATION_TIER_C_V2.md`, internal). All synthesize-once, embed-with-different-text. **5 of 6 completed before user paused the pilot mid-S-synthesis** at $0.64 spend:

| Condition | Embedding | Embedder | wiki score |
|---|---|---|---|
| **B'** | wiki-synthesis prose | bge-large-en | **0.900** ← leader |
| **W** | wiki-synthesis prose | MiniLM-L6-v2 | 0.850 |
| **H** | listing only | MiniLM (BM25+dense RRF) | 0.700 |
| **T** | listing + entity-name × 10 | MiniLM | 0.600 |
| **D** | listing only | MiniLM | 0.500 |
| **S** chunk-summary | (cancelled) | MiniLM | (unknown) |

**Per pre-registered Tier 1 criterion:** B' (or W) beats every cheaper alternative by ≥0.10. **Validated.** Multi-corpus expansion is justified.

**Notable secondary finding:** B' beats W (same prose, different embedder) by +0.05. A stronger free embedder is the cheapest possible upgrade — no API cost, just a 1.3GB local model.

## Current state of evidence

| Claim | Status |
|---|---|
| Karpathy's wiki idea works on dense single-author technical docs (internal docs) | **FALSE** — page aggregation can't compound when each fact appears once |
| Karpathy's wiki idea works on multi-source technical corpora (K8s) | **TRUE** — wiki+synthesis beats vanilla RAG by +0.20 |
| LLM benefits from synthesized prose at answer time | **FALSE** — answer-time prose contributes ~0; same effect with structured listing |
| Wiki's win is at retrieval (embedding-text enrichment) | **TRUE** — +0.30 from prose-embedded vs listing-embedded |
| 50% of the retrieval win is term-density (replicable for free) | **TRUE** — term-padding alone yields +0.15 |
| 50% is genuine synthesis-derived semantic content | **TRUE** — residual +0.15 not replicable by term-padding |
| A stronger embedder over the same prose is even better | **TRUE on K8s, n=10** — bge-large-en beats MiniLM by +0.05 at zero API cost |
| Wiki's win generalizes beyond K8s | **OPEN** — multi-corpus V2 is the test |
| Per-chunk synthesis matches per-entity synthesis | **OPEN** — S condition was cancelled |

## The architectural lesson

Karpathy's framing pointed at the right architectural feature (synthesis at write-time) for the wrong reason (LLM needs prose at query time). The actual mechanism: synthesis incidentally produces denser embedding text, which improves retrieval. The LLM doesn't read the prose; the embedder does.

This recasts "LLM Wiki" as a **retrieval-time enrichment trick** rather than a knowledge-amortization architecture. Cheaper alternatives (term-padding, better embedder, hybrid sparse+dense) capture portions of the gain. The remaining wiki advantage (+0.15 residual after controlling for term density) is genuine synthesis-derived semantic value, but only ~50% of the headline number.

## Decision point

Three paths from here:

### Path 1 — Multi-corpus V2 (rigorous validation)
Run the 6-condition pilot on 2 additional corpora (target: PostgreSQL docs + Helm/OpenTelemetry/SRE-equivalent) × 5 seeds = ~75 evals total. Tests whether the K8s findings generalize. Expected ~$45, hard cap $150. ~1-2 weeks calendar.

If it generalizes: defensible architectural finding suitable for documentation/publication.
If it doesn't: K8s was domain-specific lucky win; still publishable as a negative-but-clean result.

### Path 2 — Ship B' as the operational architecture
Skip multi-corpus validation. Commit to "wiki-synthesis + bge-large-en embedder" as the default. Accept the K8s result as sufficient evidence for engineering decisions, and ship.

This is the right path if the goal is operational deployment, not generalizable research.

### Path 3 — Re-run S only, then decide
~$3 to fill the gap on per-entity vs per-chunk synthesis granularity. Informs whether wiki's entity-aggregation structure adds value or whether per-chunk LLM summaries match it. Useful but not decisive.

## Recommended path

**Path 1 (multi-corpus V2)** is the right rigorous next step. Three reasons:

1. The K8s result itself was a single-corpus replication of a single-corpus failure. We don't know if the K8s win generalizes.
2. Tier C V2 is pre-registered (`docs/rag_eval/pre_registrations/PRE_REGISTRATION_TIER_C_V2.md`, internal) — the methodology discipline is in place.
3. ~$45 to confirm or refute is cheap relative to the ~$15 already spent investigating.

**Open operational questions** for multi-corpus V2:
- Third corpus selection: PostgreSQL docs (would need SGML→markdown conversion) or alternatives (Helm www, OpenTelemetry docs, AWS CLI docs)
- QA generation strategy: cross-family LLM (GPT-5-mini if billing active, else Groq Llama) or hand-curated subset
- Cross-family judge: Llama via Groq requires billing add (currently free-tier only)

## Caveats and limits

- **n=10 per corpus** is structurally underpowered. Codex's prior power analysis: n=20 has 25-36% power to detect a 0.10 effect. Multi-corpus + multi-seed at V2 (~330 question-runs per condition) tightens this materially.
- **Single judge (Haiku)** across all evals. Cross-family judge calibration drift is unmeasured.
- **Hand-authored questions** by the system designer. Selection bias toward retrieval-heavy questions targeting well-known entities. Tier C V2 mandates ≥50% human-curated to mitigate.
- **No production deployment data** — these are research findings, not usage statistics.

## Repo state at time of snapshot

- 149 tests passing, ruff clean (current: 253)
- 2 pre-registrations signed: `docs/rag_eval/pre_registrations/PRE_REGISTRATION_K8S.md` (validated), `docs/rag_eval/pre_registrations/PRE_REGISTRATION_TIER_C_V2.md` (revised post-Codex review, pilot validates Tier 1) — both kept internal
- 1 voided: `docs/rag_eval/pre_registrations/PRE_REGISTRATION_TIER_C.md` (wrong scope; superseded by V2) — internal
- All eval reports persisted under `results/rag_eval/reports/report-*.{json,md}` — gitignored
- Cumulative cost across all ablations: ~$15

## Next concrete actions if Path 1 chosen

1. Acquire 2 more corpora (research alternatives; Terraform repo restructured, Postgres needs SGML conversion)
2. Generate per-corpus QA sets (cross-family LLM + hand-validation)
3. Build multi-corpus runner script (extends `pilot_v2_k8s.py` to loop corpora × seeds)
4. Run V2 main study at expected $45
5. Publish findings document
