# Third-Party RAG Examples

This directory includes compact, ready-to-run SearchTrace fixtures from
third-party RAG evaluation datasets. They are bundled so users can try realistic
public examples without first downloading or converting upstream data.

Bundled subsets:

- `wixqa/`: 20 expert-written WixQA questions plus their cited Wix help
  articles.
- `enterprise_rag_bench_github/`: 20 GitHub-source EnterpriseRAG-Bench
  questions plus their expected documents.

The source datasets are:

- WixQA: MIT licensed enterprise support QA benchmark by Wix AI Research.
- EnterpriseRAG-Bench: MIT licensed synthetic enterprise RAG benchmark by Onyx.

Third-party question and corpus content remains copyright its upstream authors
and is redistributed under the upstream MIT licenses. Keep each fixture's
`UPSTREAM.md` and `LICENSE` files with any redistribution.

EnterpriseRAG-Bench is synthetic benchmark content. Internal-sounding issue
IDs, rollout notes, or engineering documents in that fixture are upstream test
data, not SearchTrace or customer data.

Each fixture includes:

- `corpus/*.md`: SearchTrace corpus documents.
- `questions.jsonl`: SearchTrace QA items with source citations.
- `smoke.yaml`: a provider-free mock config for `searchtrace rag run`.
- `UPSTREAM.md`: source URL, exact upstream notice text, and import notes.
- `LICENSE`: upstream license/copyright notice carried with the fixture.

Run the bundled examples:

```bash
.venv/bin/searchtrace rag run --config examples/third_party/wixqa/smoke.yaml

.venv/bin/searchtrace rag run \
  --config examples/third_party/enterprise_rag_bench_github/smoke.yaml
```

Use the importer when you want to refresh or scale the local fixtures. The
default output path is ignored by git:

```bash
python scripts/import_third_party_examples.py wixqa \
  --limit 20 \
  --out data/third_party/wixqa
```

EnterpriseRAG-Bench is much larger. Start with one source type before trying
the full benchmark:

```bash
python scripts/import_third_party_examples.py enterprise-rag-bench \
  --source-type github \
  --limit 20 \
  --out data/third_party/enterprise_rag_bench_github
```
