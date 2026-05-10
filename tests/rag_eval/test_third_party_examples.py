"""Checks for bundled third-party RAG example fixtures."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from retrievalci.rag_eval.corpus import load_documents
from retrievalci.rag_eval.runner import load_questions, load_run_config


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def load_importer_module() -> ModuleType:
    script = repo_root() / "scripts" / "import_third_party_examples.py"
    spec = importlib.util.spec_from_file_location("import_third_party_examples", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bundled_third_party_examples_have_resolvable_citations() -> None:
    root = repo_root()
    for example in ("wixqa", "enterprise_rag_bench_github"):
        base = root / "examples" / "third_party" / example
        questions = load_questions(base / "questions.jsonl")
        config = load_run_config(base / "smoke.yaml")
        docs = load_documents(root, config["corpus"]["globs"])

        assert len(questions) == 20
        assert docs
        corpus_paths = {doc.source_path for doc in docs}
        citations = {
            citation
            for question in questions
            for citation in question.ground_truth_citations
        }
        assert citations
        assert citations <= corpus_paths


def test_bundled_third_party_examples_include_upstream_license_text() -> None:
    root = repo_root()
    wix_license = (root / "examples" / "third_party" / "wixqa" / "LICENSE").read_text(
        encoding="utf-8"
    )
    enterprise_license = (
        root / "examples" / "third_party" / "enterprise_rag_bench_github" / "LICENSE"
    ).read_text(encoding="utf-8")

    assert 'Cite "Wix.com AI Research"' in wix_license
    assert "Dataset engineered by the Wix AI Research team" in wix_license
    assert "MIT License" in wix_license
    assert "Copyright (c) 2026 DanswerAI, Inc." in enterprise_license
    assert "MIT License" in enterprise_license


def test_wixqa_writer_uses_repo_relative_citations(tmp_path: Path) -> None:
    importer = load_importer_module()
    out_dir = tmp_path / "repo" / "examples" / "third_party" / "wixqa"
    repo = tmp_path / "repo"
    qa_rows = [
        {
            "question": "How do I connect a domain?",
            "answer": "Use the Domains page.",
            "article_ids": ["article-1"],
        }
    ]
    corpus_rows = {
        "article-1": {
            "id": "article-1",
            "url": "https://support.example/article-1",
            "contents": "Connect a domain\n\nOpen the Domains page.",
        }
    }

    importer.write_wixqa_dataset(
        qa_rows=qa_rows,
        corpus_rows=corpus_rows,
        out_dir=out_dir,
        repo_root=repo,
        config_name="wixqa_expertwritten",
    )

    questions = load_questions(out_dir / "questions.jsonl")
    assert questions[0].ground_truth_citations == (
        "examples/third_party/wixqa/corpus/article-1.md",
    )
    assert (out_dir / "UPSTREAM.md").is_file()
    assert (out_dir / "LICENSE").is_file()


def test_enterprise_helpers_parse_release_paths() -> None:
    importer = load_importer_module()
    path = "github/dsid_abcd1234__review-policy-notes.txt"

    assert importer.asset_source_type("github_slice_0001.zip") == "github"
    assert importer.enterprise_doc_id_from_path(path) == "dsid_abcd1234"
    assert importer.title_from_enterprise_path(path, "") == "Review Policy Notes"
    assert importer.enterprise_tier("conflicting_info") == "contradiction"
    assert importer.enterprise_tier("basic") == "single_hop"
