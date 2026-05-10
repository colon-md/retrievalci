"""Eval runner — runs every system on every question, aggregates, reports.

CLI: see `python -m retrievalci.rag_eval.runner --help`. Programmatic entry point is
`run_eval(systems, questions) -> ComparisonReport`.
"""

from __future__ import annotations

import argparse
import os
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast, get_args

import yaml

from retrievalci.rag_eval.backends.base import Judge
from retrievalci.rag_eval.diagnostics import diagnose_report, diagnostics_to_markdown
from retrievalci.rag_eval.metrics import _aligned_metric_values, compute_row, paired_bootstrap_ci
from retrievalci.rag_eval.systems.base import System
from retrievalci.rag_eval.types import ComparisonReport, PairwiseDelta, QAItem, RunResult, Tier

_DEFAULT_CORPUS_GLOBS = [
    "README.md",
    "docs/**/*.md",
    "retrievalci/rag_eval/schemas/*",
]
_TARGET_SYSTEMS = ("wiki_pages", "hybrid_rag", "chunk_summary_rag")
_SYSTEMS = (
    "rag",
    "claim_rag",
    "bm25",
    "hybrid_rag",
    "rerank_rag",
    "wiki_pages",
    "chunk_summary_rag",
)
_MISSING = object()


def load_dotenv(path: Path) -> int:
    """Load KEY=VALUE pairs from a .env file into os.environ. Returns count loaded.

    Existing env vars are not overwritten. Lines starting with # are ignored.
    Quotes around values are stripped. No interpolation. Tiny by design.
    """
    if not path.is_file():
        return 0
    loaded = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


def load_questions(path: Path) -> list[QAItem]:
    items: list[QAItem] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        items.append(QAItem.model_validate_json(line))
    return items


def load_run_config(path: Path) -> dict[str, Any]:
    """Load a YAML RAG run config.

    The config is intentionally a plain mapping so it stays easy to author by
    hand and easy for CI systems to template.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        msg = f"RAG run config must be a YAML mapping: {path}"
        raise ValueError(msg)
    return dict(raw)


def _repo_relative(repo_root: Path, path: Path | None) -> Path | None:
    """Resolve CLI paths relative to --repo-root, not the caller's cwd."""
    if path is None or path.is_absolute():
        return path
    return repo_root / path


def _config_lookup(config: Mapping[str, Any], paths: Sequence[Sequence[str]]) -> Any:
    for path in paths:
        value: Any = config
        for key in path:
            if not isinstance(value, Mapping) or key not in value:
                value = _MISSING
                break
            value = value[key]
        if value is not _MISSING:
            return value
    return _MISSING


def _arg_or_config(
    args: argparse.Namespace,
    config: Mapping[str, Any],
    attr: str,
    *config_paths: Sequence[str],
    default: Any = None,
) -> Any:
    arg_value = getattr(args, attr)
    if arg_value is not None:
        return arg_value
    value = _config_lookup(config, config_paths)
    if value is not _MISSING:
        return value
    return default


def _as_path(value: Any, field_name: str) -> Path | None:
    if value is None:
        return None
    if isinstance(value, Path):
        return value
    if isinstance(value, str):
        return Path(value)
    msg = f"{field_name} must be a path string"
    raise SystemExit(msg)


def _as_string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        result: list[str] = []
        for item in value:
            if not isinstance(item, str):
                msg = f"{field_name} must contain only strings"
                raise SystemExit(msg)
            item = item.strip()
            if item:
                result.append(item)
        return result
    msg = f"{field_name} must be a string or list of strings"
    raise SystemExit(msg)


def _required_path(value: Any, field_name: str, help_path: str) -> Path:
    path = _as_path(value, field_name)
    if path is None:
        msg = f"--{field_name.replace('_', '-')} is required (or set {help_path} in --config)"
        raise SystemExit(msg)
    return path


def _dedupe_preserve_order(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _resolve_system_names(
    args: argparse.Namespace,
    config: Mapping[str, Any],
    target_system: str,
) -> tuple[str, ...]:
    values = args.system
    if values is None:
        config_value = _config_lookup(config, (("systems",), ("run", "systems")))
        values = _as_string_list(config_value, "systems") if config_value is not _MISSING else None

    if values:
        systems = _dedupe_preserve_order(values)
    else:
        systems = ("rag", "claim_rag", target_system)

    invalid = sorted(set(systems) - set(_SYSTEMS))
    if invalid:
        msg = f"Unknown RAG eval system(s): {', '.join(invalid)}"
        raise SystemExit(msg)
    return systems


_PAIRWISE_METRICS = (
    "must_include_match",
    "answer_citation_precision",
    "answer_citation_recall",
    "retrieval_source_precision",
    "retrieval_source_recall",
    "faithfulness",
    "relevance",
)


def run_eval(
    systems: list[System],
    questions: list[QAItem],
    judge: Judge | None = None,
    *,
    bootstrap_resamples: int = 2000,
    bootstrap_alpha: float = 0.05,
) -> ComparisonReport:
    """Run every system on every question; aggregate results.

    If a judge is supplied, each row also gets faithfulness + relevance scores
    on a 1-5 scale. Without a judge those metrics stay None.

    Pairwise comparisons (paired bootstrap CI on mean differences) are added
    when there are ≥ 2 systems, ≥ 5 questions, and at least one metric where
    every (system, question) cell is non-None. With < 5 questions the CI
    bounds are too wide to be useful and the field stays empty.
    """
    rows: list[RunResult] = []
    for q in questions:
        for sys_ in systems:
            ans = sys_.answer(q.question)
            row = compute_row(sys_.name, q, ans)
            if judge is not None and not ans.refused:
                evidence = "\n".join(c.span or c.source_path for c in ans.citations)
                fa = judge.faithfulness(q.question, ans.answer, evidence, q.ground_truth_answer)
                re_ = judge.relevance(q.question, ans.answer)
                row = row.model_copy(update={"faithfulness": fa.score, "relevance": re_.score})
            rows.append(row)

    by_sys_metric, by_sys_tier_metric = _aggregate(rows)
    n_per_tier: dict[Tier, int] = defaultdict(int)
    for q in questions:
        n_per_tier[q.tier] += 1
    for t in get_args(Tier):
        n_per_tier.setdefault(cast(Tier, t), 0)

    pairwise: list[PairwiseDelta] = []
    if len(systems) >= 2 and len(questions) >= 5:
        question_ids = [q.id for q in questions]
        for metric in _PAIRWISE_METRICS:
            for i, sys_a in enumerate(systems):
                for sys_b in systems[i + 1 :]:
                    a_vals = _aligned_metric_values(rows, sys_a.name, metric, question_ids)
                    b_vals = _aligned_metric_values(rows, sys_b.name, metric, question_ids)
                    if a_vals is None or b_vals is None or not a_vals:
                        continue
                    mean_a = statistics.fmean(a_vals)
                    mean_b = statistics.fmean(b_vals)
                    ci_low, ci_high = paired_bootstrap_ci(
                        a_vals,
                        b_vals,
                        n_resamples=bootstrap_resamples,
                        alpha=bootstrap_alpha,
                    )
                    pairwise.append(
                        PairwiseDelta(
                            metric=metric,
                            system_a=sys_a.name,
                            system_b=sys_b.name,
                            mean_a=mean_a,
                            mean_b=mean_b,
                            mean_diff=mean_a - mean_b,
                            ci_low=ci_low,
                            ci_high=ci_high,
                            alpha=bootstrap_alpha,
                            n=len(a_vals),
                            significant=(ci_low > 0) or (ci_high < 0),
                        )
                    )

    return ComparisonReport(
        systems=tuple(s.name for s in systems),
        n_questions=len(questions),
        n_per_tier=dict(n_per_tier),
        rows=rows,
        by_system_metric=by_sys_metric,
        by_system_tier_metric=by_sys_tier_metric,
        pairwise=pairwise,
    )


def _mean_optional(values: list[float | None]) -> float | None:
    real = [v for v in values if v is not None]
    return statistics.fmean(real) if real else None


def _aggregate(
    rows: list[RunResult],
) -> tuple[dict[str, dict[str, float]], dict[str, dict[Tier, dict[str, float]]]]:
    by_sys: dict[str, dict[str, float]] = defaultdict(dict)
    by_sys_tier: dict[str, dict[Tier, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))

    metric_fields = (
        "must_include_match",
        "answer_citation_precision",
        "answer_citation_recall",
        "retrieval_source_precision",
        "retrieval_source_recall",
        "faithfulness",
        "relevance",
        "answer_length_chars",
    )

    sys_names = sorted({r.system for r in rows})
    tiers = sorted({r.tier for r in rows})

    for sys_name in sys_names:
        sys_rows = [r for r in rows if r.system == sys_name]
        for field in metric_fields:
            vals = [getattr(r, field) for r in sys_rows]
            mean = (
                _mean_optional(vals) if field != "answer_length_chars" else statistics.fmean(vals)
            )
            if mean is not None:
                by_sys[sys_name][field] = float(mean)
        # Latency + tokens.
        by_sys[sys_name]["latency_ms_p50"] = statistics.median(
            r.answer.latency_ms for r in sys_rows
        )
        by_sys[sys_name]["tokens_used_total"] = float(sum(r.answer.tokens_used for r in sys_rows))
        by_sys[sys_name]["refusal_rate"] = sum(1 for r in sys_rows if r.refused) / len(sys_rows)

        for tier in tiers:
            tier_rows = [r for r in sys_rows if r.tier == tier]
            if not tier_rows:
                continue
            for field in metric_fields:
                vals = [getattr(r, field) for r in tier_rows]
                mean = (
                    _mean_optional(vals)
                    if field != "answer_length_chars"
                    else statistics.fmean(vals)
                )
                if mean is not None:
                    by_sys_tier[sys_name][tier][field] = float(mean)

    return dict(by_sys), {k: dict(v) for k, v in by_sys_tier.items()}


def report_to_markdown(
    report: ComparisonReport,
    *,
    primary_metric: str = "must_include_match",
    min_meaningful_delta: float = 0.03,
    min_questions_for_confidence: int = 20,
) -> str:
    lines = ["# Eval comparison report", ""]
    lines.append(f"Systems: {', '.join(report.systems)}")
    lines.append(f"Questions: {report.n_questions} ({_n_per_tier_str(report.n_per_tier)})")
    lines.append("")
    diag = diagnose_report(
        report,
        primary_metric=primary_metric,
        min_meaningful_delta=min_meaningful_delta,
        min_questions_for_confidence=min_questions_for_confidence,
    )
    lines.append(diagnostics_to_markdown(diag).rstrip())
    lines.append("")
    lines.append("## Aggregate metrics by system")
    lines.append("")
    metric_keys = sorted(
        {k for sys_metrics in report.by_system_metric.values() for k in sys_metrics}
    )
    header = "| system | " + " | ".join(metric_keys) + " |"
    sep = "|" + "|".join(["---"] * (len(metric_keys) + 1)) + "|"
    lines.append(header)
    lines.append(sep)
    for sys_name in report.systems:
        cells = [sys_name]
        for k in metric_keys:
            v = report.by_system_metric.get(sys_name, {}).get(k)
            cells.append("—" if v is None else f"{v:.3f}")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("## Per-tier breakdown (must_include_match)")
    lines.append("")
    lines.append("| system | single_hop | multi_hop | contradiction |")
    lines.append("|---|---|---|---|")
    for sys_name in report.systems:
        cells = [sys_name]
        for tier in ("single_hop", "multi_hop", "contradiction"):
            v = (
                report.by_system_tier_metric.get(sys_name, {})
                .get(cast(Tier, tier), {})
                .get("must_include_match")
            )
            cells.append("—" if v is None else f"{v:.3f}")
        lines.append("| " + " | ".join(cells) + " |")

    if report.pairwise:
        confidence_pct = int((1 - report.pairwise[0].alpha) * 100)
        lines.append("")
        lines.append(f"## Pairwise comparisons ({confidence_pct}% bootstrap CI, mean(a) - mean(b))")
        lines.append("")
        lines.append("| metric | a vs b | mean(a) | mean(b) | mean diff | 95% CI | n | sig? |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for d in report.pairwise:
            lines.append(
                f"| {d.metric} | {d.system_a} vs {d.system_b} "
                f"| {d.mean_a:+.3f} | {d.mean_b:+.3f} | {d.mean_diff:+.3f} "
                f"| [{d.ci_low:+.3f}, {d.ci_high:+.3f}] | {d.n} "
                f"| {'**yes**' if d.significant else 'no'} |"
            )

    return "\n".join(lines) + "\n"


def _n_per_tier_str(n: dict[Tier, int]) -> str:
    return ", ".join(f"{tier}={count}" for tier, count in n.items())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=None, help="YAML run config.")
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument("--questions", type=Path, default=None)
    parser.add_argument("--report-json", type=Path, default=None)
    parser.add_argument("--report-md", type=Path, default=None)
    parser.add_argument(
        "--corpus-glob",
        action="append",
        default=None,
        help="Repeatable; defaults to repo docs and bundled schemas.",
    )
    parser.add_argument(
        "--system",
        action="append",
        choices=_SYSTEMS,
        default=None,
        help=(
            "Repeatable system list. Defaults to rag + claim_rag + --target-system. "
            "Config equivalent: systems: [rag, claim_rag, hybrid_rag]."
        ),
    )
    parser.add_argument(
        "--backend",
        choices=("mock", "gemini", "claude", "groq"),
        default=None,
        help=(
            "LLM/embedding backend. 'gemini' uses google-genai for both. "
            "'claude' pairs the local sentence-transformers embedder with "
            "the Anthropic SDK as the generator (ANTHROPIC_API_KEY required). "
            "'groq' pairs the local embedder with Groq-hosted Llama 3.3 70B "
            "(GROQ_API_KEY required) — fastest and cheapest of the real backends."
        ),
    )
    parser.add_argument(
        "--max-chunks",
        type=int,
        default=None,
        help=(
            "If set, truncate the corpus to the first N chunks after chunking. "
            "Useful for demo runs that fit a tight free-tier daily quota. "
            "The Gemini free tier caps at ~20 generate requests per day; "
            "--max-chunks=10 keeps a single full eval (extraction + queries) under 50 calls."
        ),
    )
    parser.add_argument(
        "--local-embedder-model",
        default=None,
        help=(
            "Sentence-transformers model used by local-embedding backends "
            "(claude/groq). Use BAAI/bge-large-en-v1.5 for the B' condition."
        ),
    )
    parser.add_argument(
        "--target-system",
        choices=_TARGET_SYSTEMS,
        default=None,
        help=(
            "Third system to compare against rag and claim_rag. "
            "Tier C V2 uses wiki_pages for W/T/D/B', hybrid_rag for H, "
            "and chunk_summary_rag for S."
        ),
    )
    parser.add_argument(
        "--chunk-summary-cache-dir",
        type=Path,
        default=None,
        help=(
            "Optional disk cache for chunk_summary_rag summaries. "
            "Use this for long S-condition runs so interrupted jobs can resume."
        ),
    )
    parser.add_argument(
        "--chunk-summary-progress-every",
        type=int,
        default=None,
        help="Print chunk_summary_rag indexing progress every N chunks.",
    )
    parser.add_argument(
        "--claim-cache-dir",
        type=Path,
        default=None,
        help=(
            "Optional disk cache for ClaimRAG extraction responses. Useful for "
            "long full-corpus pilot runs because all target systems include "
            "the claim_rag baseline."
        ),
    )
    parser.add_argument(
        "--claim-progress-every",
        type=int,
        default=None,
        help="Print ClaimRAG extraction progress every N chunks.",
    )
    parser.add_argument(
        "--judge",
        choices=("none", "mock", "gemini", "claude", "openai", "groq"),
        default=None,
        help=(
            "LLM-as-judge for faithfulness + relevance metrics. "
            "'mock' uses deterministic token-overlap (no API). "
            "'gemini' calls gemini-2.5-pro per (system, question) — adds 2 calls/row. "
            "'claude' calls claude-sonnet-4-6 — requires ANTHROPIC_API_KEY. "
            "'openai' calls gpt-5.4-mini — requires OPENAI_API_KEY with active billing. "
            "'groq' calls llama-3.3-70b-versatile — requires GROQ_API_KEY."
        ),
    )
    parser.add_argument(
        "--wiki-synthesize",
        choices=("on", "off"),
        default=None,
        help=(
            "Toggle the wiki synthesis pass. 'off' uses structured-listing pages "
            "without LLM-prose synthesis — for the synthesis-ablation experiment."
        ),
    )
    parser.add_argument(
        "--wiki-max-output-tokens",
        type=int,
        default=None,
        help=(
            "Cap WikiPagesSystem answer max_output_tokens. Set to ~200 to match "
            "RAG/ClaimRAG answer length budget for length-normalized comparison."
        ),
    )
    parser.add_argument(
        "--wiki-embed-uses-prose",
        choices=("on", "off"),
        default=None,
        help="Whether WikiPagesSystem embeds synthesized prose plus listing or listing only.",
    )
    parser.add_argument(
        "--wiki-answer-uses-prose",
        choices=("on", "off"),
        default=None,
        help="Whether WikiPagesSystem answer prompts include synthesized prose.",
    )
    parser.add_argument(
        "--primary-metric",
        default=None,
        help="Primary metric for report diagnosis.",
    )
    parser.add_argument(
        "--min-meaningful-delta",
        type=float,
        default=None,
        help="Minimum metric gap treated as a meaningful quality difference.",
    )
    parser.add_argument(
        "--min-questions-for-confidence",
        type=int,
        default=None,
        help="Question count below which diagnostics are marked directional.",
    )
    args = parser.parse_args(argv)
    config = load_run_config(args.config) if args.config is not None else {}

    repo_root = _required_path(
        _arg_or_config(args, config, "repo_root", ("repo_root",)),
        "repo_root",
        "repo_root",
    ).resolve()
    questions_path = _repo_relative(
        repo_root,
        _required_path(
            _arg_or_config(args, config, "questions", ("questions",)),
            "questions",
            "questions",
        ),
    )
    report_json_path = _repo_relative(
        repo_root,
        _required_path(
            _arg_or_config(args, config, "report_json", ("reports", "json"), ("report_json",)),
            "report_json",
            "reports.json",
        ),
    )
    report_md_path = _repo_relative(
        repo_root,
        _required_path(
            _arg_or_config(args, config, "report_md", ("reports", "markdown"), ("report_md",)),
            "report_md",
            "reports.markdown",
        ),
    )
    backend = _arg_or_config(
        args, config, "backend", ("run", "backend"), ("backend",), default="mock"
    )
    max_chunks = _arg_or_config(
        args, config, "max_chunks", ("run", "max_chunks"), ("max_chunks",), default=None
    )
    if max_chunks is not None:
        max_chunks = int(max_chunks)
    local_embedder_model = _arg_or_config(
        args,
        config,
        "local_embedder_model",
        ("run", "local_embedder_model"),
        ("local_embedder_model",),
        default="sentence-transformers/all-MiniLM-L6-v2",
    )
    target_system = _arg_or_config(
        args,
        config,
        "target_system",
        ("run", "target_system"),
        ("target_system",),
        default="wiki_pages",
    )
    target_system = str(target_system)
    systems = _resolve_system_names(args, config, target_system)
    claim_cache_dir = _repo_relative(
        repo_root,
        _as_path(
            _arg_or_config(
                args, config, "claim_cache_dir", ("caches", "claim"), ("claim_cache_dir",)
            ),
            "claim_cache_dir",
        ),
    )
    chunk_summary_cache_dir = _repo_relative(
        repo_root,
        _as_path(
            _arg_or_config(
                args,
                config,
                "chunk_summary_cache_dir",
                ("caches", "chunk_summary"),
                ("chunk_summary_cache_dir",),
            ),
            "chunk_summary_cache_dir",
        ),
    )
    claim_progress_every = int(
        _arg_or_config(
            args, config, "claim_progress_every", ("progress", "claim_every"), default=0
        )
    )
    chunk_summary_progress_every = int(
        _arg_or_config(
            args,
            config,
            "chunk_summary_progress_every",
            ("progress", "chunk_summary_every"),
            default=0,
        )
    )
    judge_name = _arg_or_config(args, config, "judge", ("run", "judge"), ("judge",), default="none")
    wiki_synthesize = _arg_or_config(
        args,
        config,
        "wiki_synthesize",
        ("wiki", "synthesize"),
        ("wiki_synthesize",),
        default="on",
    )
    wiki_max_output_tokens = _arg_or_config(
        args,
        config,
        "wiki_max_output_tokens",
        ("wiki", "max_output_tokens"),
        ("wiki_max_output_tokens",),
        default=None,
    )
    if wiki_max_output_tokens is not None:
        wiki_max_output_tokens = int(wiki_max_output_tokens)
    wiki_embed_uses_prose = _arg_or_config(
        args,
        config,
        "wiki_embed_uses_prose",
        ("wiki", "embed_uses_prose"),
        ("wiki_embed_uses_prose",),
        default="on",
    )
    wiki_answer_uses_prose = _arg_or_config(
        args,
        config,
        "wiki_answer_uses_prose",
        ("wiki", "answer_uses_prose"),
        ("wiki_answer_uses_prose",),
        default="on",
    )
    primary_metric = _arg_or_config(
        args,
        config,
        "primary_metric",
        ("diagnostics", "primary_metric"),
        ("primary_metric",),
        default="must_include_match",
    )
    min_meaningful_delta = float(
        _arg_or_config(
            args,
            config,
            "min_meaningful_delta",
            ("diagnostics", "min_meaningful_delta"),
            ("min_meaningful_delta",),
            default=0.03,
        )
    )
    min_questions_for_confidence = int(
        _arg_or_config(
            args,
            config,
            "min_questions_for_confidence",
            ("diagnostics", "min_questions_for_confidence"),
            ("min_questions_for_confidence",),
            default=20,
        )
    )
    config_globs = _config_lookup(
        config, (("corpus", "globs"), ("corpus_globs",), ("corpus_glob",))
    )
    globs = (
        args.corpus_glob
        or (
            _as_string_list(config_globs, "corpus.globs")
            if config_globs is not _MISSING
            else None
        )
        or _DEFAULT_CORPUS_GLOBS
    )
    assert questions_path is not None
    assert report_json_path is not None
    assert report_md_path is not None

    # Auto-load .env from the repo root if present. Real backends rely on
    # env vars (GOOGLE_API_KEY etc.) and a repo-local .env is the documented
    # storage location.
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")

    from retrievalci.rag_eval.backends.mock import MockEmbedder, MockGenerator
    from retrievalci.rag_eval.corpus import chunk_corpus, load_documents
    from retrievalci.rag_eval.systems import (
        ChunkSummaryRAGSystem,
        ClaimRAGSystem,
        HybridRAGSystem,
        RAGSystem,
        WikiPagesSystem,
    )

    if backend == "gemini":
        from retrievalci.rag_eval.backends.gemini import GeminiEmbedder, GeminiGenerator

        embedder, generator = GeminiEmbedder(), GeminiGenerator()
    elif backend == "claude":
        from retrievalci.rag_eval.backends.claude import ClaudeGenerator
        from retrievalci.rag_eval.backends.local import LocalEmbedder

        # Default to Haiku 4.5 — 3x cheaper than Sonnet at this eval volume.
        embedder = LocalEmbedder(model=local_embedder_model)
        generator = ClaudeGenerator(model="claude-haiku-4-5")
    elif backend == "groq":
        from retrievalci.rag_eval.backends.groq import GroqGenerator
        from retrievalci.rag_eval.backends.local import LocalEmbedder

        embedder = LocalEmbedder(model=local_embedder_model)
        generator = GroqGenerator()
    else:
        embedder, generator = MockEmbedder(), MockGenerator()

    judge = None
    if judge_name == "mock":
        from retrievalci.rag_eval.backends.mock import MockJudge

        judge = MockJudge()
    elif judge_name == "gemini":
        from retrievalci.rag_eval.backends.gemini import GeminiJudge

        judge = GeminiJudge()
    elif judge_name == "claude":
        from retrievalci.rag_eval.backends.claude import ClaudeJudge

        judge = ClaudeJudge(model="claude-haiku-4-5")
    elif judge_name == "openai":
        from retrievalci.rag_eval.backends.openai import OpenAIJudge

        judge = OpenAIJudge()
    elif judge_name == "groq":
        from retrievalci.rag_eval.backends.groq import GroqJudge

        judge = GroqJudge()

    docs = load_documents(repo_root, globs)
    chunks = chunk_corpus(docs)
    if max_chunks is not None and max_chunks < len(chunks):
        chunks = chunks[:max_chunks]

    built_systems: dict[str, System] = {}
    if "rag" in systems:
        built_systems["rag"] = RAGSystem(embedder, generator, chunks)

    claim_rag: ClaimRAGSystem | None = None
    if "claim_rag" in systems or "wiki_pages" in systems:
        claim_rag = ClaimRAGSystem(
            embedder,
            generator,
            chunks,
            extraction_cache_dir=claim_cache_dir,
            progress_every=claim_progress_every,
        )
        if "claim_rag" in systems:
            built_systems["claim_rag"] = claim_rag

    if "bm25" in systems:
        from retrievalci.rag_eval.systems import BM25System

        built_systems["bm25"] = BM25System(generator, chunks)

    if "hybrid_rag" in systems:
        built_systems["hybrid_rag"] = HybridRAGSystem(embedder, generator, chunks)

    if "rerank_rag" in systems:
        from retrievalci.rag_eval.systems import RerankRAGSystem

        built_systems["rerank_rag"] = RerankRAGSystem(embedder, generator, chunks)

    if "chunk_summary_rag" in systems:
        built_systems["chunk_summary_rag"] = ChunkSummaryRAGSystem(
            embedder,
            generator,
            chunks,
            summary_cache_dir=chunk_summary_cache_dir,
            progress_every=chunk_summary_progress_every,
        )

    relabeled_claim_count: int | None = None
    if "wiki_pages" in systems:
        assert claim_rag is not None
        # Apply the wiki-page cleanup levers before building the retrieval index:
        # drop prompt/meta-vocabulary subjects, infer subject types, and drop
        # singleton pages.
        from retrievalci.rag_eval.extraction import filter_and_relabel_claims, infer_subject_types
        from retrievalci.rag_eval.predicates import PredicateVocabulary

        vocab_path = repo_root / "retrievalci" / "rag_eval" / "schemas" / "predicates.yml"
        vocabulary = (
            PredicateVocabulary.from_yaml_file(vocab_path) if vocab_path.is_file() else None
        )

        # Step 1+2: filter stopwords + relabel subject_types from raw claim_rag claims.
        unique_subjects = sorted({c.subject for c in claim_rag._claims})
        type_map = infer_subject_types(unique_subjects, generator)
        relabeled_claims = filter_and_relabel_claims(claim_rag._claims, type_map)
        relabeled_claim_count = len(relabeled_claims)

        # Step 3: drop singletons from retrieval index (still kept in KnowledgeBuild).
        # Optional length-cap wrapper for wiki answer generation (length-confound
        # control for the K8s tightening study). Doesn't affect synthesis or
        # extraction generators.
        wiki_generator = generator
        if wiki_max_output_tokens is not None:
            cap = int(wiki_max_output_tokens)

            class _CappedGenerator:
                def __init__(self, inner, cap_tokens):
                    self._inner = inner
                    self._cap = cap_tokens

                @property
                def model_id(self):
                    return self._inner.model_id

                def generate(self, req):
                    from retrievalci.rag_eval.backends.base import GenerationRequest

                    capped_req = GenerationRequest(
                        prompt=req.prompt,
                        max_output_tokens=min(self._cap, req.max_output_tokens),
                        temperature=req.temperature,
                    )
                    return self._inner.generate(capped_req)

            wiki_generator = _CappedGenerator(generator, cap)

        built_systems["wiki_pages"] = WikiPagesSystem(
            embedder,
            wiki_generator,
            relabeled_claims,
            vocabulary=vocabulary,
            min_claims_per_indexed_page=2,
            synthesize=(wiki_synthesize == "on"),
            embed_uses_prose=(wiki_embed_uses_prose == "on"),
            answer_uses_prose=(wiki_answer_uses_prose == "on"),
        )

    questions = load_questions(questions_path)
    report = run_eval([built_systems[name] for name in systems], questions, judge=judge)

    report_json_path.parent.mkdir(parents=True, exist_ok=True)
    report_json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    report_md_path.write_text(
        report_to_markdown(
            report,
            primary_metric=primary_metric,
            min_meaningful_delta=min_meaningful_delta,
            min_questions_for_confidence=min_questions_for_confidence,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {report_json_path} and {report_md_path}")
    summary = f"Corpus: {len(docs)} docs, {len(chunks)} chunks"
    if claim_rag is not None:
        summary += f"; claim_rag extracted {claim_rag.claim_count} claims"
    if relabeled_claim_count is not None:
        summary += f"; after stopword filter + type-relabel: {relabeled_claim_count} claims"
    summary += f"; systems: {', '.join(report.systems)}"
    print(summary)


if __name__ == "__main__":
    main()
