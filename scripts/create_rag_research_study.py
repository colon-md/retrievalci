#!/usr/bin/env python3
"""Create local RAG research study configs from third-party fixtures.

The generated study lives under ignored local data paths by default. It does not
download data or call model providers; it writes import commands and RetrievalCI
run configs so a study can be prepared, reviewed, and executed deliberately.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    out_dir: Path
    import_command: tuple[str, ...]
    note: str


@dataclass(frozen=True)
class ConditionSpec:
    name: str
    systems: tuple[str, ...]
    note: str
    wiki: dict[str, str] | None = None
    local_embedder_model: str | None = None


WIXQA_CONFIGS = (
    "wixqa_expertwritten",
    "wixqa_simulated",
    "wixqa_synthetic",
)
ENTERPRISE_PRESETS = (
    ("enterprise_github_basic", "basic"),
    ("enterprise_github_semantic", "semantic"),
    ("enterprise_github_conflicting_info", "conflicting_info"),
    ("enterprise_github_intra_document_reasoning", "intra_document_reasoning"),
)
CONDITIONS = (
    ConditionSpec(
        name="retrieval_baselines",
        systems=("rag", "bm25", "hybrid_rag", "rerank_rag"),
        note="Dense, sparse, hybrid, and reranked RAG retrieval baselines.",
    ),
    ConditionSpec(
        name="wiki_full_prose",
        systems=("rag", "claim_rag", "wiki_pages"),
        note="Wiki pages synthesize prose; prose is used for embedding and answer context.",
        wiki={"synthesize": "on", "embed_uses_prose": "on", "answer_uses_prose": "on"},
    ),
    ConditionSpec(
        name="wiki_embed_prose_answer_listing",
        systems=("rag", "claim_rag", "wiki_pages"),
        note=(
            "Mechanism isolation: synthesized prose enriches embeddings, "
            "but answers see listings."
        ),
        wiki={"synthesize": "on", "embed_uses_prose": "on", "answer_uses_prose": "off"},
    ),
    ConditionSpec(
        name="wiki_embed_listing_answer_prose",
        systems=("rag", "claim_rag", "wiki_pages"),
        note="Mechanism isolation: embeddings see listings, but answers see synthesized prose.",
        wiki={"synthesize": "on", "embed_uses_prose": "off", "answer_uses_prose": "on"},
    ),
    ConditionSpec(
        name="wiki_listing_only",
        systems=("rag", "claim_rag", "wiki_pages"),
        note="Mechanism isolation: structured entity listings only.",
        wiki={"synthesize": "on", "embed_uses_prose": "off", "answer_uses_prose": "off"},
    ),
    ConditionSpec(
        name="wiki_bge_large",
        systems=("rag", "claim_rag", "wiki_pages"),
        note="Same full-prose wiki condition with the bge-large local embedder.",
        wiki={"synthesize": "on", "embed_uses_prose": "on", "answer_uses_prose": "on"},
        local_embedder_model="BAAI/bge-large-en-v1.5",
    ),
    ConditionSpec(
        name="chunk_summary",
        systems=("rag", "claim_rag", "chunk_summary_rag"),
        note="Tests whether per-chunk synthesis competes with entity-page synthesis.",
    ),
)


def build_dataset_specs(
    *,
    repo_root: Path,
    wixqa_limit: int,
    enterprise_limit: int,
    enterprise_release_tag: str,
) -> list[DatasetSpec]:
    specs: list[DatasetSpec] = []
    for config_name in WIXQA_CONFIGS:
        out_dir = Path("data") / "third_party" / config_name
        specs.append(
            DatasetSpec(
                name=config_name,
                out_dir=out_dir,
                import_command=(
                    "python",
                    "scripts/import_third_party_examples.py",
                    "--repo-root",
                    ".",
                    "wixqa",
                    "--config-name",
                    config_name,
                    "--limit",
                    str(wixqa_limit),
                    "--out",
                    str(out_dir),
                ),
                note="WixQA support QA config.",
            )
        )

    for dataset_name, question_type in ENTERPRISE_PRESETS:
        out_dir = Path("data") / "third_party" / dataset_name
        specs.append(
            DatasetSpec(
                name=dataset_name,
                out_dir=out_dir,
                import_command=(
                    "python",
                    "scripts/import_third_party_examples.py",
                    "--repo-root",
                    ".",
                    "enterprise-rag-bench",
                    "--source-type",
                    "github",
                    "--question-type",
                    question_type,
                    "--limit",
                    str(enterprise_limit),
                    "--release-tag",
                    enterprise_release_tag,
                    "--out",
                    str(out_dir),
                ),
                note=f"EnterpriseRAG-Bench GitHub source, {question_type} questions.",
            )
        )
    return specs


def build_config(
    *,
    study_name: str,
    dataset: DatasetSpec,
    condition: ConditionSpec,
    backend: str,
    judge: str,
    max_chunks: int | None,
    claim_progress_every: int,
    chunk_summary_progress_every: int,
    default_local_embedder_model: str,
) -> dict[str, Any]:
    run: dict[str, Any] = {
        "backend": backend,
        "judge": judge,
        "local_embedder_model": condition.local_embedder_model or default_local_embedder_model,
    }
    if max_chunks is not None:
        run["max_chunks"] = max_chunks

    claim_cache = Path(".retrievalci") / "cache" / "rag_eval" / study_name / dataset.name / "claims"
    summary_cache = (
        Path(".retrievalci")
        / "cache"
        / "rag_eval"
        / study_name
        / dataset.name
        / "chunk_summaries"
    )
    config: dict[str, Any] = {
        "repo_root": ".",
        "questions": str(dataset.out_dir / "questions.jsonl"),
        "corpus": {"globs": [str(dataset.out_dir / "corpus" / "*.md")]},
        "systems": list(condition.systems),
        "run": run,
        "caches": {
            "claim": str(claim_cache),
            "chunk_summary": str(summary_cache),
        },
        "progress": {
            "claim_every": claim_progress_every,
            "chunk_summary_every": chunk_summary_progress_every,
        },
        "reports": {
            "json": str(
                Path("results") / "rag_eval" / study_name / dataset.name / f"{condition.name}.json"
            ),
            "markdown": str(
                Path("results") / "rag_eval" / study_name / dataset.name / f"{condition.name}.md"
            ),
        },
        "diagnostics": {
            "primary_metric": "retrieval_source_recall",
            "min_meaningful_delta": 0.03,
            "min_questions_for_confidence": 20,
        },
    }
    if condition.wiki is not None:
        config["wiki"] = condition.wiki
    return config


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)


def write_study(
    *,
    repo_root: Path,
    out_dir: Path,
    study_name: str,
    backend: str,
    judge: str,
    max_chunks: int | None,
    wixqa_limit: int,
    enterprise_limit: int,
    enterprise_release_tag: str,
    claim_progress_every: int,
    chunk_summary_progress_every: int,
    default_local_embedder_model: str,
) -> list[Path]:
    out_dir = out_dir.resolve()
    config_dir = out_dir / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)

    datasets = build_dataset_specs(
        repo_root=repo_root,
        wixqa_limit=wixqa_limit,
        enterprise_limit=enterprise_limit,
        enterprise_release_tag=enterprise_release_tag,
    )
    written_configs: list[Path] = []
    for dataset in datasets:
        for condition in CONDITIONS:
            config = build_config(
                study_name=study_name,
                dataset=dataset,
                condition=condition,
                backend=backend,
                judge=judge,
                max_chunks=max_chunks,
                claim_progress_every=claim_progress_every,
                chunk_summary_progress_every=chunk_summary_progress_every,
                default_local_embedder_model=default_local_embedder_model,
            )
            path = config_dir / f"{dataset.name}__{condition.name}.yaml"
            path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
            written_configs.append(path)

    manifest = {
        "study_name": study_name,
        "backend": backend,
        "judge": judge,
        "max_chunks": max_chunks,
        "datasets": [
            {
                "name": dataset.name,
                "out_dir": str(dataset.out_dir),
                "note": dataset.note,
                "import_command": list(dataset.import_command),
            }
            for dataset in datasets
        ],
        "conditions": [
            {
                "name": condition.name,
                "systems": list(condition.systems),
                "note": condition.note,
                "wiki": condition.wiki,
                "local_embedder_model": condition.local_embedder_model,
            }
            for condition in CONDITIONS
        ],
        "configs": [str(path.relative_to(out_dir)) for path in written_configs],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    rel_to_repo = os.path.relpath(repo_root, out_dir)
    import_lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        'SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"',
        f'REPO_ROOT="$(cd -- "$SCRIPT_DIR/{rel_to_repo}" && pwd)"',
        'cd "$REPO_ROOT"',
        "",
    ]
    import_lines.extend(shlex.join(dataset.import_command) for dataset in datasets)
    write_executable(out_dir / "import.sh", "\n".join(import_lines) + "\n")

    run_lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        'SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"',
        f'REPO_ROOT="$(cd -- "$SCRIPT_DIR/{rel_to_repo}" && pwd)"',
        'cd "$REPO_ROOT"',
        'RETRIEVALCI="${RETRIEVALCI:-.venv/bin/retrievalci}"',
        'if [[ ! -x "$RETRIEVALCI" ]]; then',
        '  RETRIEVALCI="${RETRIEVALCI:-retrievalci}"',
        "fi",
        'for config in "$SCRIPT_DIR"/configs/*.yaml; do',
        '  echo "==> $config"',
        '  "$RETRIEVALCI" rag run --config "$config"',
        "done",
        "",
    ]
    write_executable(out_dir / "run.sh", "\n".join(run_lines))

    readme = f"""\
# {study_name}

Local RetrievalCI RAG research matrix generated by
`scripts/create_rag_research_study.py`.

This directory is intentionally ignored by git. Large third-party data, provider
outputs, and report artifacts should stay local unless a specific result is
curated for publication.

## Prepare Data

```bash
bash {out_dir.relative_to(repo_root)}/import.sh
```

## Run One Config

```bash
.venv/bin/retrievalci rag run --config {written_configs[0].relative_to(repo_root)}
```

## Run Full Matrix

```bash
bash {out_dir.relative_to(repo_root)}/run.sh
```

Reports are written under `results/rag_eval/{study_name}/`.
"""
    (out_dir / "README.md").write_text(readme, encoding="utf-8")
    return written_configs


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--study-name", default="karpathy_third_party_expansion")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/rag_eval/studies/karpathy_third_party_expansion"),
    )
    parser.add_argument(
        "--backend",
        choices=("mock", "gemini", "claude", "groq"),
        default="mock",
        help="Use mock for config smoke checks; use claude/groq/gemini for real studies.",
    )
    parser.add_argument(
        "--judge",
        choices=("none", "mock", "gemini", "claude", "openai", "groq"),
        default="none",
    )
    parser.add_argument("--max-chunks", type=int, default=None)
    parser.add_argument("--wixqa-limit", type=int, default=200)
    parser.add_argument("--enterprise-limit", type=int, default=100)
    parser.add_argument("--enterprise-release-tag", default="v1.0.0")
    parser.add_argument("--claim-progress-every", type=int, default=50)
    parser.add_argument("--chunk-summary-progress-every", type=int, default=50)
    parser.add_argument(
        "--default-local-embedder-model",
        default="sentence-transformers/all-MiniLM-L6-v2",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()
    written = write_study(
        repo_root=repo_root,
        out_dir=(repo_root / args.out if not args.out.is_absolute() else args.out),
        study_name=args.study_name,
        backend=args.backend,
        judge=args.judge,
        max_chunks=args.max_chunks,
        wixqa_limit=args.wixqa_limit,
        enterprise_limit=args.enterprise_limit,
        enterprise_release_tag=args.enterprise_release_tag,
        claim_progress_every=args.claim_progress_every,
        chunk_summary_progress_every=args.chunk_summary_progress_every,
        default_local_embedder_model=args.default_local_embedder_model,
    )
    print(f"Wrote {len(written)} configs under {written[0].parent}")
    print("Next: run the generated import.sh, then run selected configs or run.sh.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
