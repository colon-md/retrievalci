"""Checks for local RAG research study generation."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from retrievalci.rag_eval.runner import load_run_config


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def load_study_module() -> ModuleType:
    script = repo_root() / "scripts" / "create_rag_research_study.py"
    spec = importlib.util.spec_from_file_location("create_rag_research_study", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_research_study_generator_writes_configs_and_scripts(tmp_path: Path) -> None:
    module = load_study_module()
    repo = tmp_path / "repo"
    out = repo / "data" / "rag_eval" / "studies" / "study"
    repo.mkdir()

    written = module.write_study(
        repo_root=repo,
        out_dir=out,
        study_name="study",
        backend="mock",
        judge="none",
        max_chunks=25,
        wixqa_limit=20,
        enterprise_limit=10,
        enterprise_release_tag="v1.0.0",
        claim_progress_every=5,
        chunk_summary_progress_every=7,
        default_local_embedder_model="sentence-transformers/all-MiniLM-L6-v2",
    )

    assert len(written) == len(module.WIXQA_CONFIGS + module.ENTERPRISE_PRESETS) * len(
        module.CONDITIONS
    )
    assert (out / "import.sh").is_file()
    assert (out / "run.sh").is_file()
    assert (out / "manifest.json").is_file()

    bge_config = load_run_config(out / "configs" / "wixqa_expertwritten__wiki_bge_large.yaml")
    assert bge_config["run"]["local_embedder_model"] == "BAAI/bge-large-en-v1.5"
    assert bge_config["wiki"] == {
        "synthesize": "on",
        "embed_uses_prose": "on",
        "answer_uses_prose": "on",
    }

    listing_config = load_run_config(
        out / "configs" / "wixqa_expertwritten__wiki_listing_only.yaml"
    )
    assert listing_config["systems"] == ["rag", "claim_rag", "wiki_pages"]
    assert listing_config["wiki"]["embed_uses_prose"] == "off"
    assert listing_config["wiki"]["answer_uses_prose"] == "off"
    assert listing_config["reports"]["json"] == (
        "results/rag_eval/study/wixqa_expertwritten/wiki_listing_only.json"
    )
