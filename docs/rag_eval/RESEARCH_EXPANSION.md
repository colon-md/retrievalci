# Third-Party Research Expansion

RetrievalCI now has enough public third-party data plumbing to re-run the
Karpathy-style wiki/RAG study outside the original local corpora. Keep the
public repository small: bundled fixtures remain demos, while larger imports,
caches, and reports stay under ignored local paths.

## Research Question

The original finding was specific: wiki-style synthesis helped most when it
created richer retrieval text. The answer model did not appear to benefit much
from reading synthesized prose once retrieval had found the right sources.

The expanded question is:

> Does wiki/entity-page synthesis improve retrieval across support and
> enterprise RAG benchmarks, or was the win specific to dense engineering docs?

## Datasets

Use the importer to create larger local datasets from:

- WixQA expert-written, simulated, and synthetic QA configs.
- EnterpriseRAG-Bench GitHub-source slices across basic, semantic,
  conflicting-info, and intra-document-reasoning questions.

The public fixtures under `examples/third_party/` remain small smoke examples.
The expanded study writes to `data/third_party/`, which is ignored by git.

## Conditions

The generated study matrix covers:

- retrieval baselines: `rag`, `bm25`, `hybrid_rag`, `rerank_rag`;
- full wiki prose: `rag`, `claim_rag`, `wiki_pages`;
- wiki prose used for embeddings only;
- wiki prose used for answer context only;
- wiki listing-only control;
- full wiki prose with `BAAI/bge-large-en-v1.5`;
- per-chunk synthesis through `chunk_summary_rag`.

The key mechanism check is the same as the original study: if prose helps only
when `wiki.embed_uses_prose: on`, then the effect is retrieval enrichment. If
prose helps only when `wiki.answer_uses_prose: on`, then the effect is
answer-time synthesis.

## Generate A Local Study

```bash
python scripts/create_rag_research_study.py \
  --backend groq \
  --judge groq \
  --wixqa-limit 200 \
  --enterprise-limit 100
```

For a no-provider config smoke check, leave the defaults:

```bash
python scripts/create_rag_research_study.py
```

Then import the data and run the matrix:

```bash
bash data/rag_eval/studies/karpathy_third_party_expansion/import.sh
bash data/rag_eval/studies/karpathy_third_party_expansion/run.sh
```

Reports are written under `results/rag_eval/`, which is also ignored by git.
Curate only a final, reviewed summary for publication.

## Interpretation Rules

Treat a result as publishable only if:

- each compared condition uses the same dataset slice and question IDs;
- `retrieval_source_recall` and `must_include_match` move in the same direction
  or the disagreement is explained;
- pairwise confidence intervals do not contradict the headline claim;
- costs, backend, judge, embedder, and limits are reported;
- synthetic EnterpriseRAG-Bench content is clearly labeled as synthetic.

Treat early runs as directional. WixQA expert-written and simulated configs are
good for multi-document support questions; EnterpriseRAG-Bench is better for
enterprise-style ambiguity, contradiction, and source-type effects.
