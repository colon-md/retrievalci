# RAG Eval

SearchTrace's RAG architecture eval mode helps teams compare retrieval and
answering systems before changing production RAG behavior.

It is not a RAG framework. It is a diagnostic harness: bring a corpus, a fixed
question set, and one or more candidate systems; SearchTrace runs them side by
side and reports where failures come from.

## Questions It Answers

- Which architecture has the best retrieval-source recall?
- Did answer quality improve because retrieval improved, or because generation
  changed?
- Are citations missing even when the right sources were retrieved?
- Which tier is weakest: single-hop, multi-hop, or contradiction questions?
- Is the leading system worth its latency or token cost?

## Current Systems

SearchTrace can compare:

- dense chunk RAG;
- BM25;
- hybrid BM25 + dense retrieval;
- reranked RAG;
- ClaimRAG;
- chunk-summary RAG;
- wiki/entity-page synthesis.

The runner writes JSON and Markdown reports. Markdown reports include a
deterministic diagnosis section with the leading system, likely bottleneck,
weakest tier, recommendation, and next experiment.

For larger local studies using WixQA and EnterpriseRAG-Bench, see
`docs/rag_eval/RESEARCH_EXPANSION.md`.

## CI Shape

RAG eval becomes a CI gate through:

```bash
searchtrace rag run --config examples/rag_eval/smoke.yaml
searchtrace rag compare \
  --baseline baselines/rag/smoke.json \
  --candidate reports/pr.json \
  --metric retrieval_source_recall \
  --max-drop 0.02
```

The project-level command wraps this into one workflow:

```bash
searchtrace ci run --config examples/searchtrace.ci.yaml
```

The smoke config uses a small public support-desk fixture:

- `examples/rag_eval/corpus/*.md`
- `examples/rag_eval/questions.jsonl`

This public documentation set is limited to the current product workflow. Raw
research logs, pre-registrations, and generated experiment reports are excluded.
