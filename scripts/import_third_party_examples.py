#!/usr/bin/env python3
"""Import third-party RAG benchmarks into SearchTrace example format."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import textwrap
import zipfile
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from urllib.request import Request, urlopen

WIXQA_BASE_URL = "https://huggingface.co/datasets/Wix/WixQA/resolve/main"
WIXQA_CARD_URL = "https://huggingface.co/datasets/Wix/WixQA"
ENTERPRISE_REPO_API = "https://api.github.com/repos/onyx-dot-app/EnterpriseRAG-Bench"
ENTERPRISE_QUESTIONS_URL = (
    "https://raw.githubusercontent.com/onyx-dot-app/EnterpriseRAG-Bench/main/questions.jsonl"
)
ENTERPRISE_CARD_URL = "https://huggingface.co/datasets/onyx-dot-app/EnterpriseRAG-Bench"
USER_AGENT = "searchtrace-third-party-importer"
MIT_LICENSE_TEXT = """\
MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
ENTERPRISE_LICENSE_TEXT = f"""\
MIT License

Copyright (c) 2026 DanswerAI, Inc.

{MIT_LICENSE_TEXT.removeprefix("MIT License\n\n")}"""
WIXQA_LICENSE_NOTICE = f"""\
WixQA Upstream License Notice

Source: {WIXQA_CARD_URL}
Upstream dataset card license metadata: mit

Exact upstream licensing text from the WixQA dataset card:

Released under the MIT License. Cite "Wix.com AI Research" when using the data.

Exact upstream authorship text from the WixQA dataset card:

Dataset engineered by the Wix AI Research team. External annotators are
acknowledged in the paper.

The upstream dataset card did not include a standalone LICENSE file or formal
copyright line in the bundled source at the time this fixture was imported.
The standard MIT License text is included below so redistributed fixture copies
carry the license terms.

{MIT_LICENSE_TEXT}"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import third-party RAG benchmarks into SearchTrace format."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Repo root used to write repo-relative citations and config paths.",
    )
    sub = parser.add_subparsers(dest="dataset", required=True)

    wix = sub.add_parser("wixqa", help="Import WixQA from Hugging Face.")
    wix.add_argument("--out", type=Path, default=Path("data/third_party/wixqa"))
    wix.add_argument(
        "--config-name",
        choices=("wixqa_expertwritten", "wixqa_simulated", "wixqa_synthetic"),
        default="wixqa_expertwritten",
    )
    wix.add_argument("--limit", type=int, default=20, help="Maximum QA rows to import.")

    ent = sub.add_parser(
        "enterprise-rag-bench",
        help="Import EnterpriseRAG-Bench from GitHub release assets.",
    )
    ent.add_argument("--out", type=Path, default=Path("data/third_party/enterprise_rag_bench"))
    ent.add_argument("--limit", type=int, default=20, help="Maximum question rows to import.")
    ent.add_argument(
        "--source-type",
        action="append",
        default=None,
        help=(
            "Restrict to one source type such as github, confluence, jira, slack, "
            "gmail, linear, google_drive, hubspot, or fireflies. Repeatable."
        ),
    )
    ent.add_argument(
        "--question-type",
        action="append",
        default=None,
        help="Restrict to an upstream question_type such as basic or conflicting_info.",
    )
    ent.add_argument(
        "--release-tag",
        default="v1.0.0",
        help="GitHub release tag containing document slices.",
    )
    ent.add_argument(
        "--max-slices",
        type=int,
        default=None,
        help="Optional cap on downloaded document slices for testing importer behavior.",
    )

    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()

    if args.dataset == "wixqa":
        return import_wixqa(
            out_dir=args.out,
            repo_root=repo_root,
            config_name=args.config_name,
            limit=args.limit,
        )
    if args.dataset == "enterprise-rag-bench":
        return import_enterprise_rag_bench(
            out_dir=args.out,
            repo_root=repo_root,
            limit=args.limit,
            source_types=tuple(args.source_type or ()),
            question_types=tuple(args.question_type or ()),
            release_tag=args.release_tag,
            max_slices=args.max_slices,
        )
    raise AssertionError(args.dataset)


def import_wixqa(
    *,
    out_dir: Path,
    repo_root: Path,
    config_name: str,
    limit: int,
) -> int:
    qa_url = f"{WIXQA_BASE_URL}/{config_name}/test.jsonl"
    corpus_url = f"{WIXQA_BASE_URL}/wix_kb_corpus/wix_kb_corpus.jsonl"
    qa_rows = take(iter_jsonl_url(qa_url), limit)
    wanted_article_ids = {
        str(article_id)
        for row in qa_rows
        for article_id in row.get("article_ids", [])
        if str(article_id)
    }
    corpus_rows: dict[str, dict] = {}
    for row in iter_jsonl_url(corpus_url):
        article_id = str(row.get("id") or "")
        if article_id in wanted_article_ids:
            corpus_rows[article_id] = row
            if set(corpus_rows) == wanted_article_ids:
                break
    missing = sorted(wanted_article_ids - set(corpus_rows))
    if missing:
        raise SystemExit(f"WixQA corpus missing {len(missing)} article id(s): {missing[:5]}")

    write_wixqa_dataset(
        qa_rows=qa_rows,
        corpus_rows=corpus_rows,
        out_dir=out_dir,
        repo_root=repo_root,
        config_name=config_name,
    )
    print(f"Wrote WixQA SearchTrace fixture: {out_dir.resolve()}")
    return 0


def import_enterprise_rag_bench(
    *,
    out_dir: Path,
    repo_root: Path,
    limit: int,
    source_types: tuple[str, ...],
    question_types: tuple[str, ...],
    release_tag: str,
    max_slices: int | None,
) -> int:
    selected_source_types = {normalize_key(v) for v in source_types}
    selected_question_types = {normalize_key(v) for v in question_types}
    questions: list[dict] = []
    for row in iter_jsonl_url(ENTERPRISE_QUESTIONS_URL):
        row_source_types = {normalize_key(v) for v in row.get("source_types", [])}
        row_question_type = normalize_key(str(row.get("question_type") or ""))
        if selected_source_types and not (selected_source_types & row_source_types):
            continue
        if selected_question_types and row_question_type not in selected_question_types:
            continue
        questions.append(row)
        if len(questions) >= limit:
            break

    wanted_doc_ids = {
        str(doc_id)
        for row in questions
        for doc_id in row.get("expected_doc_ids", [])
        if str(doc_id)
    }
    wanted_source_types = selected_source_types or {
        normalize_key(source_type)
        for row in questions
        for source_type in row.get("source_types", [])
        if str(source_type)
    }
    assets = enterprise_release_assets(release_tag)
    docs = download_enterprise_docs(
        assets=assets,
        wanted_doc_ids=wanted_doc_ids,
        wanted_source_types=wanted_source_types,
        max_slices=max_slices,
    )
    missing = sorted(wanted_doc_ids - set(docs))
    if missing:
        raise SystemExit(
            f"EnterpriseRAG-Bench import found {len(docs)}/{len(wanted_doc_ids)} docs; "
            f"missing examples: {missing[:5]}"
        )

    write_enterprise_dataset(
        questions=questions,
        docs=docs,
        out_dir=out_dir,
        repo_root=repo_root,
        release_tag=release_tag,
        source_types=tuple(sorted(wanted_source_types)),
    )
    print(f"Wrote EnterpriseRAG-Bench SearchTrace fixture: {out_dir.resolve()}")
    return 0


def write_wixqa_dataset(
    *,
    qa_rows: Sequence[dict],
    corpus_rows: dict[str, dict],
    out_dir: Path,
    repo_root: Path,
    config_name: str,
) -> None:
    out_dir = out_dir.resolve()
    corpus_dir = out_dir / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    doc_paths: dict[str, Path] = {}
    for article_id, row in sorted(corpus_rows.items()):
        path = corpus_dir / f"{safe_filename(article_id)}.md"
        doc_paths[article_id] = path
        title = first_line(str(row.get("contents") or "")) or f"Wix article {article_id}"
        body = str(row.get("contents") or "").strip()
        url = str(row.get("url") or "")
        path.write_text(
            frontmatter(title=title, fields={"Upstream URL": url, "Article ID": article_id})
            + body
            + "\n",
            encoding="utf-8",
        )

    question_rows: list[dict] = []
    for i, row in enumerate(qa_rows, start=1):
        article_ids = [str(v) for v in row.get("article_ids", []) if str(v) in doc_paths]
        citations = [repo_relative(doc_paths[article_id], repo_root) for article_id in article_ids]
        question_rows.append(
            {
                "id": f"wixqa-{i:04d}",
                "tier": "multi_hop" if len(article_ids) > 1 else "single_hop",
                "question": str(row.get("question") or ""),
                "ground_truth_answer": str(row.get("answer") or ""),
                "ground_truth_citations": citations,
                "notes": f"WixQA {config_name}; upstream article_ids={article_ids}",
            }
        )

    write_jsonl(out_dir / "questions.jsonl", question_rows)
    write_smoke_config(out_dir=out_dir, repo_root=repo_root, report_stem=f"wixqa-{config_name}")
    (out_dir / "LICENSE").write_text(WIXQA_LICENSE_NOTICE, encoding="utf-8")
    (out_dir / "UPSTREAM.md").write_text(
        textwrap.dedent(
            f"""\
            # WixQA Import

            Source: {WIXQA_CARD_URL}
            Config: `{config_name}`
            License: MIT

            Third-party question and corpus content remains copyright its
            upstream authors and is redistributed under the upstream MIT
            license.

            Exact upstream licensing text from the WixQA dataset card:

            > Released under the MIT License. Cite "Wix.com AI Research" when using the data.

            Exact upstream authorship text from the WixQA dataset card:

            > Dataset engineered by the Wix AI Research team. External annotators are
            > acknowledged in the paper.

            See `LICENSE` for the upstream notice and MIT license text carried
            with this fixture.

            SearchTrace conversion:

            - Imported rows: {len(question_rows)}
            - Imported corpus documents: {len(doc_paths)}
            - Corpus files were generated from Wix Help-Center article text.
            - Question citations point to generated local Markdown files.
            """
        ),
        encoding="utf-8",
    )


def write_enterprise_dataset(
    *,
    questions: Sequence[dict],
    docs: dict[str, dict[str, str]],
    out_dir: Path,
    repo_root: Path,
    release_tag: str,
    source_types: tuple[str, ...],
) -> None:
    out_dir = out_dir.resolve()
    corpus_dir = out_dir / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    doc_paths: dict[str, Path] = {}
    for doc_id, doc in sorted(docs.items()):
        source_type = safe_filename(doc.get("source_type", "unknown"))
        path = corpus_dir / f"{source_type}__{safe_filename(doc_id)}.md"
        doc_paths[doc_id] = path
        title = doc.get("title") or first_line(doc.get("content", "")) or doc_id
        path.write_text(
            frontmatter(
                title=title,
                fields={
                    "Source type": doc.get("source_type", ""),
                    "Document ID": doc_id,
                    "Release": release_tag,
                    "Benchmark": "EnterpriseRAG-Bench synthetic upstream data",
                },
            )
            + doc.get("content", "").strip()
            + "\n",
            encoding="utf-8",
        )

    question_rows: list[dict] = []
    for row in questions:
        expected_ids = [str(v) for v in row.get("expected_doc_ids", []) if str(v) in doc_paths]
        citations = [repo_relative(doc_paths[doc_id], repo_root) for doc_id in expected_ids]
        question_rows.append(
            {
                "id": str(row.get("question_id") or f"enterprise-{len(question_rows) + 1:04d}"),
                "tier": enterprise_tier(str(row.get("question_type") or "")),
                "question": str(row.get("question") or ""),
                "ground_truth_answer": str(row.get("gold_answer") or ""),
                "ground_truth_citations": citations,
                "must_include_terms": [str(v) for v in row.get("answer_facts", [])[:3]],
                "notes": (
                    "EnterpriseRAG-Bench "
                    f"{row.get('question_type')}; upstream_doc_ids={expected_ids}"
                ),
            }
        )

    write_jsonl(out_dir / "questions.jsonl", question_rows)
    write_smoke_config(out_dir=out_dir, repo_root=repo_root, report_stem="enterprise-rag-bench")
    (out_dir / "LICENSE").write_text(ENTERPRISE_LICENSE_TEXT, encoding="utf-8")
    (out_dir / "UPSTREAM.md").write_text(
        textwrap.dedent(
            f"""\
            # EnterpriseRAG-Bench Import

            Source: {ENTERPRISE_CARD_URL}
            GitHub: https://github.com/onyx-dot-app/EnterpriseRAG-Bench
            Release: `{release_tag}`
            License: MIT

            Third-party question and corpus content remains copyright its
            upstream authors and is redistributed under the upstream MIT
            license.

            Exact upstream copyright/license line from the
            EnterpriseRAG-Bench repository:

            > MIT License Copyright (c) 2026 DanswerAI, Inc.

            Attribution: EnterpriseRAG-Bench by Onyx. See `LICENSE` for the
            upstream MIT license text carried with this fixture.

            Note: EnterpriseRAG-Bench is synthetic benchmark content.
            Internal-sounding issue IDs, rollout notes, or engineering documents
            in this fixture are upstream test data, not SearchTrace or customer
            data.

            SearchTrace conversion:

            - Imported rows: {len(question_rows)}
            - Imported corpus documents: {len(doc_paths)}
            - Source types: {", ".join(source_types)}
            - Document slice text files were converted to Markdown.
            - Question citations point to generated local Markdown files.
            """
        ),
        encoding="utf-8",
    )


def download_enterprise_docs(
    *,
    assets: Sequence[dict],
    wanted_doc_ids: set[str],
    wanted_source_types: set[str],
    max_slices: int | None,
) -> dict[str, dict[str, str]]:
    if not wanted_doc_ids:
        return {}
    docs: dict[str, dict[str, str]] = {}
    slices = [
        asset
        for asset in assets
        if asset.get("name", "").endswith(".zip")
        and asset_source_type(asset.get("name", "")) in wanted_source_types
    ]
    if max_slices is not None:
        slices = slices[:max_slices]
    with tempfile.TemporaryDirectory(prefix="searchtrace-enterprise-rag-bench-") as tmp:
        tmp_dir = Path(tmp)
        for asset in slices:
            remaining = wanted_doc_ids - set(docs)
            if not remaining:
                break
            name = str(asset["name"])
            url = str(asset["browser_download_url"])
            zip_path = tmp_dir / name
            print(f"Downloading {name} ({asset.get('size', 0)} bytes)...", file=sys.stderr)
            download_file(url, zip_path)
            with zipfile.ZipFile(zip_path) as archive:
                for info in archive.infolist():
                    doc_id = enterprise_doc_id_from_path(info.filename)
                    if not doc_id or doc_id not in remaining:
                        continue
                    source_type = info.filename.split("/", 1)[0]
                    content = archive.read(info).decode("utf-8", errors="replace")
                    docs[doc_id] = {
                        "source_type": source_type,
                        "title": title_from_enterprise_path(info.filename, content),
                        "content": content,
                    }
    return docs


def enterprise_release_assets(release_tag: str) -> list[dict]:
    url = f"{ENTERPRISE_REPO_API}/releases/tags/{release_tag}"
    with urlopen(request(url), timeout=60) as response:
        data = json.load(response)
    assets = data.get("assets")
    if not isinstance(assets, list):
        raise SystemExit(f"EnterpriseRAG-Bench release has no assets: {release_tag}")
    return assets


def iter_jsonl_url(url: str) -> Iterator[dict]:
    with urlopen(request(url), timeout=120) as response:
        for raw in response:
            line = raw.decode("utf-8").strip()
            if line:
                yield json.loads(line)


def request(url: str) -> Request:
    return Request(url, headers={"User-Agent": USER_AGENT})


def download_file(url: str, path: Path) -> None:
    with urlopen(request(url), timeout=300) as response, path.open("wb") as out:
        shutil.copyfileobj(response, out)


def take(rows: Iterable[dict], limit: int) -> list[dict]:
    if limit <= 0:
        raise SystemExit("--limit must be greater than zero")
    out: list[dict] = []
    for row in rows:
        out.append(row)
        if len(out) >= limit:
            break
    return out


def write_jsonl(path: Path, rows: Sequence[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_smoke_config(*, out_dir: Path, repo_root: Path, report_stem: str) -> None:
    questions_path = repo_relative(out_dir / "questions.jsonl", repo_root)
    corpus_glob = repo_relative(out_dir / "corpus" / "*.md", repo_root)
    config = textwrap.dedent(
        f"""\
        repo_root: .
        questions: {questions_path}

        corpus:
          globs:
            - {corpus_glob}

        systems:
          - rag
          - bm25
          - hybrid_rag

        run:
          backend: mock
          judge: mock
          max_chunks: 500

        reports:
          json: /tmp/searchtrace-{report_stem}.json
          markdown: /tmp/searchtrace-{report_stem}.md

        diagnostics:
          primary_metric: retrieval_source_recall
          min_meaningful_delta: 0.03
          min_questions_for_confidence: 20
        """
    )
    (out_dir / "smoke.yaml").write_text(config, encoding="utf-8")


def frontmatter(*, title: str, fields: dict[str, str]) -> str:
    lines = [f"# {title.strip()}", ""]
    for key, value in fields.items():
        if value:
            lines.append(f"{key}: {value}")
    lines.append("")
    return "\n".join(lines)


def repo_relative(path: Path, repo_root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def safe_filename(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return safe or "item"


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip().strip("#").strip()
        if line:
            return line[:120]
    return ""


def enterprise_tier(question_type: str) -> str:
    normalized = normalize_key(question_type)
    if "conflict" in normalized or "not_found" in normalized:
        return "contradiction"
    if normalized in {"basic", "semantic", "misc", "miscellaneous"}:
        return "single_hop"
    return "multi_hop"


def asset_source_type(asset_name: str) -> str:
    match = re.match(r"(?P<source>.+)_slice_\d+\.zip$", asset_name)
    return normalize_key(match.group("source")) if match else ""


def enterprise_doc_id_from_path(path: str) -> str:
    name = Path(path).name
    match = re.match(r"(?P<doc_id>dsid_[A-Za-z0-9]+)__", name)
    return match.group("doc_id") if match else ""


def title_from_enterprise_path(path: str, content: str) -> str:
    name = Path(path).name
    match = re.match(r"dsid_[A-Za-z0-9]+__(?P<title>.+)\.txt$", name)
    if match:
        return match.group("title").replace("-", " ").strip().title()
    return first_line(content)


if __name__ == "__main__":
    raise SystemExit(main())
