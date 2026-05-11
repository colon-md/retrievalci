"""Corpus loader for the eval harness.

Loads markdown / yaml / json / sql files from the workspace, chunks them by
paragraph (with overlap), and provides an API both RAG and the wiki extractor
can consume. The corpus path is a list of repo-relative globs; the loader
resolves them against a configurable repo root.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Document:
    """One source document, with provenance preserved."""

    source_path: str  # repo-relative
    text: str


@dataclass(frozen=True)
class Chunk:
    """A retrievable unit. RAG indexes these; wiki extracts claims from these."""

    source_path: str
    chunk_index: int  # 0-based within the document
    text: str

    @property
    def chunk_id(self) -> str:
        return f"{self.source_path}#chunk-{self.chunk_index}"


def load_documents(repo_root: Path, globs: Iterable[str]) -> list[Document]:
    """Load every file matching any glob, repo-relative."""
    docs: list[Document] = []
    seen: set[Path] = set()
    for pat in globs:
        for path in sorted(repo_root.glob(pat)):
            if not path.is_file() or path in seen:
                continue
            seen.add(path)
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            rel = path.relative_to(repo_root)
            docs.append(Document(source_path=str(rel), text=text))
    return docs


def chunk_by_paragraph(
    doc: Document, max_chars: int = 1200, overlap_chars: int = 120
) -> list[Chunk]:
    """Split a document into paragraph-aligned chunks with light overlap.

    Uses blank-line paragraph boundaries. Greedy fill: pack paragraphs into
    chunks up to max_chars; oversized paragraphs are kept whole (we don't break
    sentences, since that hurts retrieval quality more than it helps).
    """
    paragraphs = [p.strip() for p in doc.text.split("\n\n") if p.strip()]
    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_len = 0

    def flush() -> None:
        nonlocal buf, buf_len
        if buf:
            chunks.append(
                Chunk(source_path=doc.source_path, chunk_index=len(chunks), text="\n\n".join(buf))
            )
            buf = []
            buf_len = 0

    for para in paragraphs:
        if buf and buf_len + len(para) + 2 > max_chars:
            flush()
            # Light overlap: prepend the tail of the previous chunk's last paragraph.
            if chunks and overlap_chars > 0:
                tail = chunks[-1].text[-overlap_chars:]
                buf.append(tail)
                buf_len += len(tail)
        buf.append(para)
        buf_len += len(para) + 2

    flush()
    return chunks


def chunk_corpus(docs: Iterable[Document]) -> list[Chunk]:
    out: list[Chunk] = []
    for d in docs:
        out.extend(chunk_by_paragraph(d))
    return out


def l2_normalize(v: list[float]) -> list[float]:
    """Scale a vector to unit length. Used by embedder backends so cosine
    similarity downstream reduces to a plain dot product."""
    import math
    n = math.sqrt(sum(x * x for x in v))
    if n == 0.0:
        return v
    return [x / n for x in v]


def compute_corpus_version_hash(chunks: Iterable[Chunk]) -> str:
    """Deterministic SHA-256 over the chunked corpus.

    Returns the full 64-char hex digest. Identifies what a hosted RAG service
    was *supposed to* index. The hash is keyed on chunk content + position
    (not file mtime) so a clean checkout rehashes to the same value
    regardless of when the files were written.

    For filename / UI rendering, use short_corpus_version_hash() — but
    persisted manifests and IndexHandles must store the full digest so
    audit can detect 64-bit collisions that the truncated form can't.
    """
    h = hashlib.sha256()
    for c in sorted(chunks, key=lambda x: (x.source_path, x.chunk_index)):
        h.update(c.source_path.encode("utf-8"))
        h.update(b"\x00")
        h.update(str(c.chunk_index).encode("ascii"))
        h.update(b"\x00")
        h.update(c.text.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def short_corpus_version_hash(full_hash: str) -> str:
    """Truncate a corpus version hash for filenames and UI rendering only.

    Never use this form as a comparison key or persisted identifier — it
    has only ~64 bits of collision resistance vs. 256 bits in the full form.
    """
    if len(full_hash) < 16:
        raise ValueError(f"hash too short to truncate: {full_hash!r}")
    return full_hash[:16]
