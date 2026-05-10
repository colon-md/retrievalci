"""Trace-level evaluation for RetrievalCI state-dynamics workflows.

This module evaluates retrieval-state policies on real or synthetic agent
traces. It intentionally has no heavyweight dependency: the built-in replay
backend is a small BM25 implementation over JSONL corpus rows.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

DEFAULT_POLICIES = (
    "recorded",
    "query_only",
    "last_answer_x3",
    "compact_state",
    "public_trace",
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


@dataclass(frozen=True)
class CorpusRecord:
    chunk_id: str
    doc_id: str
    text: str


@dataclass(frozen=True)
class TraceRecord:
    session_id: str
    turn_id: str
    user_question: str
    retrieval_query: str
    current_need: str
    previous_answers: tuple[str, ...]
    known_facts: tuple[str, ...]
    excluded_leads: tuple[str, ...]
    public_trace: tuple[str, ...]
    retrieved_ids: tuple[str, ...]
    gold_ids: tuple[str, ...]
    previous_doc_ids: tuple[str, ...]
    false_lead_doc_ids: tuple[str, ...]
    raw: dict[str, Any]


class TraceRetriever(Protocol):
    """Retriever adapter for trace replay.

    Production users can pass an adapter that calls their real retriever,
    vector store, or search API. The built-in BM25 index implements this
    protocol and remains the portable default.
    """

    def query(self, text: str, *, k: int) -> list[tuple[str, float]]: ...


class BM25Index:
    """Tiny BM25 index for portable offline replay."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._ids: list[str] = []
        self._doc_tfs: list[Counter[str]] = []
        self._doc_lens: list[int] = []
        self._idf: dict[str, float] = {}
        self._avgdl = 0.0

    def fit(self, ids: list[str], texts: list[str]) -> None:
        self._ids = ids
        tokenized = [_tokens(t) for t in texts]
        self._doc_tfs = [Counter(toks) for toks in tokenized]
        self._doc_lens = [len(toks) for toks in tokenized]
        self._avgdl = sum(self._doc_lens) / len(self._doc_lens) if self._doc_lens else 0.0

        df: Counter[str] = Counter()
        for toks in tokenized:
            df.update(set(toks))
        n_docs = len(tokenized)
        self._idf = {
            term: math.log(1.0 + (n_docs - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def query(self, text: str, *, k: int) -> list[tuple[str, float]]:
        query_terms = _tokens(text)
        if not query_terms:
            return []
        scored = [(self._score(query_terms, i), i) for i in range(len(self._ids))]
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [(self._ids[i], score) for score, i in scored[:k]]

    def _score(self, query_terms: list[str], doc_i: int) -> float:
        tf = self._doc_tfs[doc_i]
        dl = self._doc_lens[doc_i] or 1
        denom_norm = 1.0 - self._b + self._b * (dl / (self._avgdl or 1.0))
        score = 0.0
        for term in query_terms:
            freq = tf.get(term, 0)
            if not freq:
                continue
            numerator = freq * (self._k1 + 1.0)
            denominator = freq + self._k1 * denom_norm
            score += self._idf.get(term, 0.0) * numerator / denominator
        return score


def _tokens(text: str) -> list[str]:
    return [m.group(0).lower() for m in _TOKEN_RE.finditer(text)]


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_metrics(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, Iterable):
        return tuple(str(v) for v in value if str(v))
    return (str(value),)


def parse_trace(row: dict[str, Any]) -> TraceRecord:
    state = row.get("agent_state") or {}
    previous_answers = _as_tuple(
        state.get("previous_answers")
        or state.get("answers")
        or row.get("previous_answers")
    )
    known_facts = _as_tuple(state.get("known_facts") or state.get("known_evidence"))
    excluded_leads = _as_tuple(state.get("excluded_leads"))
    public_trace = _as_tuple(state.get("public_trace") or row.get("public_trace"))
    retrieved_ids = _as_tuple(
        row.get("retrieved_chunk_ids")
        or row.get("retrieved_doc_ids")
        or row.get("retrieved_ids")
    )
    gold_ids = _as_tuple(
        row.get("gold_chunk_ids") or row.get("gold_doc_ids") or row.get("gold_ids")
    )
    previous_doc_ids = _as_tuple(
        state.get("previous_doc_ids") or row.get("previous_doc_ids")
    )
    false_lead_doc_ids = _as_tuple(
        state.get("false_lead_doc_ids") or row.get("false_lead_doc_ids")
    )
    return TraceRecord(
        session_id=str(row.get("session_id", "")),
        turn_id=str(row.get("turn_id", row.get("step_id", ""))),
        user_question=str(row.get("user_question", row.get("question", ""))),
        retrieval_query=str(row.get("retrieval_query", "")),
        current_need=str(row.get("current_need", state.get("current_need", ""))),
        previous_answers=previous_answers,
        known_facts=known_facts,
        excluded_leads=excluded_leads,
        public_trace=public_trace,
        retrieved_ids=retrieved_ids,
        gold_ids=gold_ids,
        previous_doc_ids=previous_doc_ids,
        false_lead_doc_ids=false_lead_doc_ids,
        raw=row,
    )


def load_traces(path: str | Path) -> list[TraceRecord]:
    return [parse_trace(row) for row in load_jsonl(path)]


def load_corpus(path: str | Path) -> list[CorpusRecord]:
    records: list[CorpusRecord] = []
    for row in load_jsonl(path):
        doc_id = str(row.get("doc_id") or row.get("id") or row.get("chunk_id"))
        if not doc_id:
            raise ValueError(f"corpus row missing doc_id/id/chunk_id: {row}")
        if "text" in row:
            chunk_id = str(row.get("chunk_id") or doc_id)
            records.append(CorpusRecord(chunk_id=chunk_id, doc_id=doc_id, text=str(row["text"])))
            continue
        if "title" in row or "abstract" in row or "body" in row:
            records.extend(
                _chunk_manifest_row(
                    doc_id=doc_id,
                    title=str(row.get("title", "")),
                    abstract=str(row.get("abstract", "")),
                    body=str(row.get("body", "")),
                )
            )
            continue
        raise ValueError(f"corpus row missing text/title/abstract/body: {row}")
    return records


def _chunk_manifest_row(
    *,
    doc_id: str,
    title: str,
    abstract: str,
    body: str,
    chunk_words: int = 350,
    overlap_words: int = 80,
) -> list[CorpusRecord]:
    records = [
        CorpusRecord(
            chunk_id=f"{doc_id}::0000",
            doc_id=doc_id,
            text="\n\n".join(part for part in (title, abstract) if part),
        )
    ]
    words = body.split()
    if not words:
        return [r for r in records if r.text.strip()]
    step = max(1, chunk_words - overlap_words)
    for i, start in enumerate(range(0, len(words), step), start=1):
        chunk = " ".join(words[start : start + chunk_words])
        if chunk:
            records.append(CorpusRecord(f"{doc_id}::{i:04d}", doc_id, chunk))
    return records


def build_index(records: list[CorpusRecord]) -> BM25Index:
    index = BM25Index()
    index.fit([r.chunk_id for r in records], [r.text for r in records])
    return index


def id_to_doc_id(identifier: str) -> str:
    if "::" in identifier:
        return identifier.split("::", 1)[0]
    if "#chunk-" in identifier:
        return identifier.split("#chunk-", 1)[0]
    return identifier


def _cap_words(text: str, max_words: int) -> str:
    words = text.split()
    return " ".join(words[:max_words])


def render_policy(trace: TraceRecord, policy: str) -> str:
    if policy == "recorded":
        return trace.retrieval_query or trace.user_question
    if policy == "production_baseline":
        return trace.retrieval_query or trace.user_question
    if policy == "query_only":
        return trace.user_question
    if policy == "current_need":
        return trace.current_need or trace.user_question
    if policy == "last_answer":
        last = trace.previous_answers[-1] if trace.previous_answers else ""
        return f"{last} {trace.user_question}".strip()
    if policy == "last_answer_x3":
        last = trace.previous_answers[-1] if trace.previous_answers else ""
        return f"{last} {last} {last} {trace.user_question}".strip()
    if policy == "compact_state":
        parts = [f"Question: {trace.user_question}"]
        if trace.current_need:
            parts.append(f"Need: {trace.current_need}")
        evidence = trace.known_facts + trace.previous_answers
        if evidence:
            parts.append("Known: " + " | ".join(evidence))
        if trace.excluded_leads:
            parts.append("Exclude: " + " | ".join(trace.excluded_leads))
        return _cap_words("\n".join(parts), 120)
    if policy == "public_trace":
        parts = [trace.user_question, *trace.public_trace]
        return _cap_words("\n".join(p for p in parts if p), 500)
    raise ValueError(f"unknown policy: {policy}")


def _recall_at_k(ranked: tuple[str, ...], gold: tuple[str, ...], k: int) -> float:
    if not gold:
        return math.nan
    ranked_docs = {id_to_doc_id(r) for r in ranked[:k]} | set(ranked[:k])
    gold_docs = {id_to_doc_id(g) for g in gold} | set(gold)
    return len(ranked_docs & gold_docs) / len(gold_docs)


def _any_doc_overlap(ranked: tuple[str, ...], docs: tuple[str, ...], k: int) -> bool:
    if not docs:
        return False
    ranked_docs = {id_to_doc_id(r) for r in ranked[:k]} | set(ranked[:k])
    target_docs = {id_to_doc_id(d) for d in docs} | set(docs)
    return bool(ranked_docs & target_docs)


def evaluate_traces(
    traces: list[TraceRecord],
    corpus: list[CorpusRecord] | None,
    *,
    policies: tuple[str, ...] = DEFAULT_POLICIES,
    k: int = 10,
    retriever: TraceRetriever | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    index = retriever or (build_index(corpus) if corpus else None)
    replay_policies = tuple(policy for policy in policies if policy != "production_baseline")
    if index is None and replay_policies:
        joined = ", ".join(replay_policies)
        msg = f"corpus or retriever is required for replay policies: {joined}"
        raise ValueError(msg)
    per_turn: list[dict[str, Any]] = []

    for trace in traces:
        for policy in policies:
            query_text = render_policy(trace, policy)
            if policy == "production_baseline":
                if not trace.retrieved_ids:
                    msg = (
                        "production_baseline policy requires retrieved_doc_ids "
                        f"for session={trace.session_id} turn={trace.turn_id}"
                    )
                    raise ValueError(msg)
                ranked = trace.retrieved_ids[:k]
            else:
                ranked = tuple(cid for cid, _ in index.query(query_text, k=k))

            recall5 = _recall_at_k(ranked, trace.gold_ids, min(5, k))
            recallk = _recall_at_k(ranked, trace.gold_ids, k)
            top1 = ranked[0] if ranked else ""
            top1_is_gold = _any_doc_overlap((top1,), trace.gold_ids, 1)
            stale_at_1 = _any_doc_overlap((top1,), trace.previous_doc_ids, 1)
            stale_at_1 = stale_at_1 and not top1_is_gold
            false_lead_at_k = _any_doc_overlap(ranked, trace.false_lead_doc_ids, k)
            drift_at_1 = bool(trace.gold_ids and top1 and not top1_is_gold)
            per_turn.append(
                {
                    "session_id": trace.session_id,
                    "turn_id": trace.turn_id,
                    "policy": policy,
                    "query_text": query_text,
                    "ranked_ids": list(ranked),
                    "gold_ids": list(trace.gold_ids),
                    "recall_at_5": recall5,
                    "recall_at_k": recallk,
                    "zero_recall_at_k": bool(trace.gold_ids and recallk == 0.0),
                    "drift_at_1": drift_at_1,
                    "stale_at_1": stale_at_1,
                    "false_lead_at_k": false_lead_at_k,
                }
            )

    return per_turn, summarize_per_turn(per_turn)


def _mean(values: list[float]) -> float:
    clean = [v for v in values if not math.isnan(v)]
    return float(sum(clean) / len(clean)) if clean else math.nan


def summarize_per_turn(per_turn: list[dict[str, Any]]) -> dict[str, Any]:
    by_policy: dict[str, list[dict[str, Any]]] = {}
    for row in per_turn:
        by_policy.setdefault(row["policy"], []).append(row)

    metrics: dict[str, Any] = {"policies": {}}
    for policy, rows in sorted(by_policy.items()):
        metrics["policies"][policy] = {
            "n": len(rows),
            "recall_at_5": _mean([float(r["recall_at_5"]) for r in rows]),
            "recall_at_k": _mean([float(r["recall_at_k"]) for r in rows]),
            "zero_recall_at_k": _mean([float(r["zero_recall_at_k"]) for r in rows]),
            "drift_at_1": _mean([float(r["drift_at_1"]) for r in rows]),
            "stale_at_1": _mean([float(r["stale_at_1"]) for r in rows]),
            "false_lead_at_k": _mean([float(r["false_lead_at_k"]) for r in rows]),
        }
    if "recorded" in metrics["policies"]:
        recorded = metrics["policies"]["recorded"]["recall_at_5"]
        metrics["policy_deltas_vs_recorded_recall_at_5"] = {
            policy: vals["recall_at_5"] - recorded
            for policy, vals in metrics["policies"].items()
            if not math.isnan(vals["recall_at_5"]) and not math.isnan(recorded)
        }
    if "query_only" in metrics["policies"]:
        query_only = metrics["policies"]["query_only"]["recall_at_5"]
        metrics["policy_deltas_vs_query_only_recall_at_5"] = {
            policy: vals["recall_at_5"] - query_only
            for policy, vals in metrics["policies"].items()
            if not math.isnan(vals["recall_at_5"]) and not math.isnan(query_only)
        }
    return metrics


def check_metric_gates(
    metrics: dict[str, Any],
    *,
    policy: str,
    min_recall_at_5: float | None = None,
    max_zero_recall_at_k: float | None = None,
    max_stale_at_1: float | None = None,
    max_false_lead_at_k: float | None = None,
) -> list[str]:
    policies = metrics.get("policies", {})
    if policy not in policies:
        return [f"policy `{policy}` was not evaluated"]
    vals = policies[policy]
    failures: list[str] = []
    checks = (
        ("recall_at_5", min_recall_at_5, ">="),
        ("zero_recall_at_k", max_zero_recall_at_k, "<="),
        ("stale_at_1", max_stale_at_1, "<="),
        ("false_lead_at_k", max_false_lead_at_k, "<="),
    )
    for metric, threshold, direction in checks:
        if threshold is None:
            continue
        value = float(vals[metric])
        failed = value < threshold if direction == ">=" else value > threshold
        if failed:
            failures.append(
                f"{policy}.{metric}={value:.3f} violates {direction} {threshold:.3f}"
            )
    return failures


def check_metric_regressions(
    metrics: dict[str, Any],
    baseline_metrics: dict[str, Any],
    *,
    policy: str,
    max_recall_at_5_drop: float | None = None,
    max_zero_recall_at_k_increase: float | None = None,
    max_stale_at_1_increase: float | None = None,
    max_false_lead_at_k_increase: float | None = None,
) -> list[str]:
    current_policies = metrics.get("policies", {})
    baseline_policies = baseline_metrics.get("policies", {})
    if policy not in current_policies:
        return [f"policy `{policy}` was not evaluated"]
    if policy not in baseline_policies:
        return [f"policy `{policy}` is missing from baseline metrics"]

    current = current_policies[policy]
    baseline = baseline_policies[policy]
    failures: list[str] = []

    if max_recall_at_5_drop is not None:
        delta = float(current["recall_at_5"]) - float(baseline["recall_at_5"])
        if delta < -max_recall_at_5_drop:
            failures.append(
                f"{policy}.recall_at_5 delta={delta:.3f} drops more than "
                f"{max_recall_at_5_drop:.3f}"
            )

    increase_checks = (
        ("zero_recall_at_k", max_zero_recall_at_k_increase),
        ("stale_at_1", max_stale_at_1_increase),
        ("false_lead_at_k", max_false_lead_at_k_increase),
    )
    for metric, max_increase in increase_checks:
        if max_increase is None:
            continue
        delta = float(current[metric]) - float(baseline[metric])
        if delta > max_increase:
            failures.append(
                f"{policy}.{metric} delta=+{delta:.3f} increases more than "
                f"{max_increase:.3f}"
            )

    return failures


def write_outputs(
    per_turn: list[dict[str, Any]],
    metrics: dict[str, Any],
    out_dir: str | Path,
) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "per_turn.jsonl").open("w", encoding="utf-8") as f:
        for row in per_turn:
            f.write(json.dumps(row) + "\n")
    with (out / "metrics.json").open("w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)
    with (out / "report.md").open("w", encoding="utf-8") as f:
        f.write(render_markdown_report(metrics, per_turn=per_turn))


def _short_text(value: Any, *, max_chars: int = 120) -> str:
    text = " ".join(str(value).split())
    text = text.replace("|", "\\|")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _short_ids(ids: Any, *, max_items: int = 3) -> str:
    if not ids:
        return ""
    values = [str(v) for v in ids]
    shown = values[:max_items]
    if len(values) > max_items:
        shown.append(f"+{len(values) - max_items} more")
    return ", ".join(shown).replace("|", "\\|")


def _failure_examples(
    per_turn: list[dict[str, Any]] | None,
    key: str,
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    if not per_turn:
        return []
    examples: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in per_turn:
        if not row.get(key):
            continue
        fingerprint = (
            str(row.get("policy", "")),
            str(row.get("session_id", "")),
            str(row.get("turn_id", "")),
        )
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        examples.append(row)
        if len(examples) >= limit:
            break
    return examples


def _append_examples(
    lines: list[str],
    title: str,
    examples: list[dict[str, Any]],
) -> None:
    if not examples:
        return
    lines.extend(
        [
            "",
            f"## {title}",
            "",
            "| Policy | Session | Turn | Query | Gold | Top retrieved |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in examples:
        lines.append(
            "| {policy} | {session} | {turn} | {query} | {gold} | {ranked} |".format(
                policy=_short_text(row.get("policy", "")),
                session=_short_text(row.get("session_id", "")),
                turn=_short_text(row.get("turn_id", "")),
                query=_short_text(row.get("query_text", ""), max_chars=140),
                gold=_short_ids(row.get("gold_ids", ())),
                ranked=_short_ids(row.get("ranked_ids", ())),
            )
        )


def render_markdown_report(
    metrics: dict[str, Any],
    *,
    per_turn: list[dict[str, Any]] | None = None,
) -> str:
    lines = [
        "# RetrievalCI Trace Evaluation",
        "",
        "| Policy | n | Recall@5 | Zero-recall | Drift@1 | Stale@1 | False-lead |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for policy, vals in metrics.get("policies", {}).items():
        lines.append(
            "| {policy} | {n} | {r5:.3f} | {zero:.3f} | {drift:.3f} "
            "| {stale:.3f} | {false:.3f} |".format(
                policy=policy,
                n=vals["n"],
                r5=vals["recall_at_5"],
                zero=vals["zero_recall_at_k"],
                drift=vals["drift_at_1"],
                stale=vals["stale_at_1"],
                false=vals["false_lead_at_k"],
            )
        )
    lines.extend(["", "## Recommendations", ""])
    policies = metrics.get("policies", {})
    if policies:
        best = max(policies.items(), key=lambda item: item[1]["recall_at_5"])[0]
        lines.append(f"- Highest Recall@5 policy: `{best}`.")
    if "public_trace" in policies and "query_only" in policies:
        if policies["public_trace"]["recall_at_5"] < policies["query_only"]["recall_at_5"]:
            lines.append(
                "- `public_trace` underperforms `query_only`; full trace is retrieval noise here."
            )
    if "last_answer_x3" in policies and "query_only" in policies:
        if policies["last_answer_x3"]["recall_at_5"] > policies["query_only"]["recall_at_5"]:
            lines.append(
                "- `last_answer_x3` improves recall; bridge state is useful when reliable."
            )
    if "recorded" in policies and policies["recorded"]["stale_at_1"] > 0:
        lines.append(
            "- `recorded` has stale top-1 hits; inspect whether production search is "
            "re-reading old evidence."
        )
    max_false_lead = max(
        (vals["false_lead_at_k"] for vals in policies.values()),
        default=0.0,
    )
    if max_false_lead > 0:
        lines.append(
            "- At least one policy retrieves known false leads; add noisy-state stress "
            "tests before shipping."
        )
    _append_examples(lines, "Zero-Recall Examples", _failure_examples(per_turn, "zero_recall_at_k"))
    _append_examples(lines, "Stale-State Examples", _failure_examples(per_turn, "stale_at_1"))
    _append_examples(lines, "False-Lead Examples", _failure_examples(per_turn, "false_lead_at_k"))
    return "\n".join(lines) + "\n"
