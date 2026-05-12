# RAG eval

RetrievalCI's RAG eval mode supports two workflows that share the same fixture format, scoring code, and report shape:

## 1. Hosted-RAG comparison (Mode A scorecard)

Index your corpus through each hosted service, query it on a fixed question set, score the returned chunks against the same `ground_truth_citations`. This is what the top-level [`README.md`](../../README.md) headline scorecard shows.

Adapters implement the `HostedSystem` protocol (`retrievalci/rag_eval/hosted.py`):
- Vertex AI RAG Engine — `retrievalci/rag_eval/systems/vertex_ai_rag.py`
- Bedrock Knowledge Bases — `retrievalci/rag_eval/systems/bedrock_kb.py`
- Azure AI Search — `retrievalci/rag_eval/systems/azure_ai_search.py`
- OpenAI File Search — `retrievalci/rag_eval/systems/openai_file_search.py`

Each adapter handles provision → ingest → query → teardown under a shared `RunBudget` cost cap and a fail-closed manifest that maps provider-internal chunk IDs back to repo-relative source paths.

Methodology, design tradeoffs, and open items: [`results/hosted-rag-benchmark-plan.md`](results/hosted-rag-benchmark-plan.md).

## 2. Local RAG architecture eval

Compare local retrieval architectures on your own corpus. Useful for CI regression checks against a baseline retrieval stack you already run.

Three categories of local systems live under `retrievalci/rag_eval/systems/`:

| Category | Systems | Use for |
|---|---|---|
| **Retrieval baselines** | `dense_rag`, `bm25_lexical`, `hybrid_rrf`, `dense_rerank`, `dense_rag_termpad`, `chunk_summary_rag` | CI regression checks; ablation against your production retriever |
| **Research systems** (wiki+RAG variant) | `claim_rag`, `wiki_pages` | Testing the hypothesis that LLM-distilled entity pages improve retrieval. See top-level README "Research findings" |
| **Ablation** | `wiki_pages` with `synthesis_mode={prose,tag_list}` + `embed_uses_prose × answer_uses_prose` flags | Decomposing where the wiki retrieval lift actually comes from. Runnable via `make ablation-distill` |

The runner writes JSON and Markdown reports. Markdown reports include a deterministic diagnosis section (leading system, likely bottleneck, weakest tier, recommended next experiment).

## CI gate

Both workflows expose a regression-gate command:

```bash
retrievalci rag run --config examples/rag_eval/smoke.yaml
retrievalci rag compare \
  --baseline baselines/rag/smoke.json \
  --candidate reports/pr.json \
  --metric retrieval_source_recall \
  --max-drop 0.02
```

`rag compare` exits `2` on regression so it can run directly in CI.

## Reading order

- New here? Start with the top-level [`README.md`](../../README.md).
- Choosing between hosted services? [`results/hosted-rag-benchmark-plan.md`](results/hosted-rag-benchmark-plan.md).
- Curious about the wiki+RAG research? [`STATUS.md`](STATUS.md).
- Building your own local-eval suite? [`RESEARCH_EXPANSION.md`](RESEARCH_EXPANSION.md).

Raw research logs, pre-registrations, and generated experiment reports under `pre_registrations/` and `results/EVAL_RESULTS_*.md` are gitignored; the bench-v0 fixture and the methodology / status docs ship.
