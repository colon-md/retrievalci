"""Corpus loader for the eval harness.

Loads markdown / yaml / json / sql files from the workspace, chunks them by
paragraph (with overlap), and provides an API both RAG and the wiki extractor
can consume. The corpus path is a list of repo-relative globs; the loader
resolves them against a configurable repo root.
"""

from __future__ import annotations

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
