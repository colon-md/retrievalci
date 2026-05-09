from __future__ import annotations

import yaml
from searchtrace.project import load_project_config, project_config_to_run_spec


def test_project_config_maps_to_run_spec(tmp_path) -> None:
    config_path = tmp_path / "searchtrace.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "name": "ci",
                "registry": ".searchtrace/runs",
                "repo_root": ".",
                "rag": {
                    "config": "examples/rag_eval/smoke.yaml",
                    "primary_metric": "retrieval_source_recall",
                },
                "trace": {
                    "source": {
                        "input": "examples/otel.spans.demo.json",
                        "format": "otel",
                        "require_gold": True,
                    },
                    "corpus": "examples/corpus.demo.jsonl",
                    "policies": ["recorded", "query_only"],
                    "k": 3,
                    "retriever": {
                        "url": "https://retriever.example.com/search",
                        "headers": ["Authorization: Bearer token"],
                        "timeout_s": 2.5,
                    },
                    "gate": {
                        "policy": "query_only",
                        "min_recall_at_5": 0.8,
                        "max_stale_at_1": 0.05,
                    },
                },
                "artifacts": {
                    "debug_artifacts": True,
                    "snapshot_inputs": False,
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_project_config(config_path)
    spec = project_config_to_run_spec(config, config_path=config_path)

    assert spec.name == "ci"
    assert spec.repo_root == str(tmp_path.resolve())
    assert spec.rag_config == "examples/rag_eval/smoke.yaml"
    assert spec.trace_source == "examples/otel.spans.demo.json"
    assert spec.trace_source_format == "otel"
    assert spec.trace_require_gold is True
    assert spec.trace_policies == ("recorded", "query_only")
    assert spec.trace_k == 3
    assert spec.trace_retriever_url == "https://retriever.example.com/search"
    assert spec.trace_retriever_headers == ("Authorization: Bearer token",)
    assert spec.trace_retriever_timeout_s == 2.5
    assert spec.trace_gate_policy == "query_only"
    assert spec.trace_min_recall_at_5 == 0.8
    assert spec.trace_max_stale_at_1 == 0.05
    assert spec.artifact_policy.debug_artifacts is True
