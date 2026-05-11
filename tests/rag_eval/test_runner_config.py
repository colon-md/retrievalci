from __future__ import annotations

import sys

import yaml
from retrievalci.rag_eval.runner import main
from retrievalci.rag_eval.types import ComparisonReport, QAItem


def test_runner_accepts_yaml_config(tmp_path, monkeypatch) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "eval.md").write_text(
        "Payments service depends on postgres.\n\nAuth service depends on Redis.",
        encoding="utf-8",
    )
    question = QAItem(
        id="q01",
        tier="single_hop",
        question="What database does the payments service depend on?",
        ground_truth_answer="postgres",
        ground_truth_citations=("docs/eval.md",),
        must_include_terms=("postgres",),
    )
    (tmp_path / "questions.jsonl").write_text(question.model_dump_json() + "\n", encoding="utf-8")
    config_path = tmp_path / "run.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "repo_root": str(tmp_path),
                "questions": "questions.jsonl",
                "corpus": {"globs": ["docs/*.md"]},
                "systems": ["dense_rag", "bm25_lexical"],
                "run": {"backend": "mock", "judge": "none", "max_chunks": 3},
                "reports": {
                    "json": "out/report.json",
                    "markdown": "out/report.md",
                },
                "diagnostics": {"primary_metric": "retrieval_source_recall"},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(sys, "argv", ["retrievalci", "--config", str(config_path)])

    main()

    report_path = tmp_path / "out" / "report.json"
    report = ComparisonReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    assert report.systems == ("dense_rag", "bm25_lexical")
    assert report.n_questions == 1
    assert (tmp_path / "out" / "report.md").is_file()
