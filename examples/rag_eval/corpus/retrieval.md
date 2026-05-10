# Retrieval Quality Notes

The support retriever uses hybrid search: BM25 candidates are fused with dense
embedding candidates using reciprocal rank fusion. The production top-k is 8,
while the CI smoke fixture uses a smaller top-k to keep reports compact.

Prompt bundle changes must pass a RetrievalCI CI run before deployment. The
release manager compares the candidate RAG report to the accepted baseline on
retrieval_source_recall and blocks the change when the drop is greater than
0.02.

The approved rollback order is prompt bundle first, reranker second, embedding
model last.

The retrieval canary compares the candidate prompt bundle against the accepted
baseline on 20 held-out support questions. A run is marked directional instead
of confident when it has fewer than 20 questions.

The trace replay job evaluates recorded, query-only, last-answer, compact-state,
and public-trace policies. Reviewers look for stale references, false leads, and
zero-recall turns before approving a state-policy change.
