"""Extraction-quality remediation for wiki-style RAG systems.

Three lever fixes used by the RAG architecture eval:

1. **Stopword / meta-vocabulary blocklist** — drop claims whose subject is a
   linguistic-construct word leaking from the extraction prompt. The Tier-A
   diagnostic showed `subject` (84 claims), `system` (15), `claim` (13),
   `wiki` (9) at the top of the entity-page list. These are not entities.

2. **Subject canonicalization** — lowercase, strip leading articles, collapse
   punctuation. Collapses `Eval Harness` / `eval harness` / `Eval harness`
   into a single page.

3. **Subject_type inference** — replace the generic `subject_type="extracted"`
   label with a real type drawn from the closed entity-type list in
   `retrievalci/rag_eval/schemas/predicates.yml`. Done as a batched LLM pass
   over unique subjects. Falls back to `entity:concept` for unclassifiable
   subjects.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from retrievalci.rag_eval.backends.base import GenerationRequest, Generator

# Meta-vocabulary leakage from the extraction prompt itself, plus generic
# terms that never have entity-page value. Compared after `normalize_subject`.
STOPWORD_BLOCKLIST: frozenset[str] = frozenset(
    {
        # extraction-prompt leakage
        "subject",
        "predicate",
        "object",
        "triple",
        "fact",
        # corpus-meta leakage (the system describing itself)
        "system",
        "claim",
        "wiki",
        "page",
        "document",
        # generic / non-referential
        "design",
        "issue",
        "thing",
        "stuff",
        "user",
        "request",
        "response",
        "answer",
        "question",
        # common nouns the extractor treats as entities
        "data",
        "value",
        "result",
        "name",
        "id",
        "type",
    }
)

# Closed entity types from `retrievalci/rag_eval/schemas/predicates.yml` plus a
# fallback for concept-level entities that don't fit a structural type but are
# legitimate wiki content.
ENTITY_TYPES: tuple[str, ...] = (
    "entity:service",
    "entity:library",
    "entity:endpoint",
    "entity:database",
    "entity:event_type",
    "entity:team",
    "entity:rotation",
    "entity:document",
    "entity:auth_method",
    "entity:incident",
    "entity:concept",  # fallback for legitimate non-structural entities
)
_FALLBACK_TYPE = "entity:concept"


def normalize_subject(s: str) -> str:
    """Lowercase, strip leading article, collapse non-word chars + whitespace.

    Examples:
      'The Eval Harness' -> 'eval harness'
      'retrievalci/rag_eval/schemas/predicates.yml' -> 'retrievalci rag eval schemas predicates yml'
      'Right-to-Erasure Cascade' -> 'right to erasure cascade'
    """
    s = s.lower().strip()
    s = re.sub(r"^(the|a|an)\s+", "", s)
    s = re.sub(r"[^\w\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


_PR_PATTERN = re.compile(r"^pr\s*\d+$", re.IGNORECASE)
_DELIVERABLE_PATTERN = re.compile(r"^deliverable\s+[\d.]+$", re.IGNORECASE)
_TRACK_PATTERN = re.compile(r"^track\s+[a-z]$", re.IGNORECASE)
_VERSION_HEADER_PATTERN = re.compile(r"\sv\d+\.\d+\.\d+$", re.IGNORECASE)


def is_too_generic(subject: str) -> bool:
    """Heuristics for subjects that look like extraction noise rather than entities.

    Drops:
      - 1-2 char fragments
      - all-numeric subjects ('22', '0.1.0')
      - PR/issue numbering ('PR 1', 'pr 22')
      - 'Deliverable 12.4', 'Track A' style document headers
    Keeps:
      - longer concept entities even if they look administrative
        ('Predicate Vocabulary' is fine — it's the trailing version
         qualifier 'v0.1.0' that gets stripped in normalization)
    """
    raw = subject.strip()
    norm = normalize_subject(subject)
    if len(norm) <= 2:
        return True
    # Pattern matches operate on the raw subject so embedded `.` survives.
    if re.fullmatch(r"\d+(\.\d+)*", raw):
        return True
    if _PR_PATTERN.match(raw):
        return True
    if _DELIVERABLE_PATTERN.match(raw):
        return True
    if _TRACK_PATTERN.match(raw):
        return True
    return False


def should_drop_subject(subject: str) -> bool:
    """True if the subject should not be extracted as a wiki entity."""
    if not subject or not subject.strip():
        return True
    norm = normalize_subject(subject)
    if norm in STOPWORD_BLOCKLIST:
        return True
    return is_too_generic(subject)


def canonicalize_subject(subject: str) -> str:
    """Return the canonical-form subject string used for `(subject_type, subject)`
    bucketing. Strips trailing version qualifiers like ' v0.1.0'.

    Returns the canonical form; the original (display) subject is preserved
    elsewhere for citation rendering.
    """
    s = subject.strip()
    s = _VERSION_HEADER_PATTERN.sub("", s)
    return normalize_subject(s)


_TYPE_INFER_PROMPT = """\
Classify each subject into one of these entity types. Reply with one line per
subject in the order given, format `<index>. <type>`. If a subject doesn't fit
any structural type but is a real entity (a concept, pattern, policy), use
`entity:concept`. If a subject is meta-language or noise, also use
`entity:concept` (it'll be filtered downstream).

Types:
  entity:service       - a deployable software service (auth_service, payments_service)
  entity:library       - a library or package (numpy, tensorflow)
  entity:endpoint      - an API endpoint or URL pattern (/charge, /api/v1/users)
  entity:database      - a database, datastore, or storage system (postgres, redis, BigQuery)
  entity:event_type    - an event, message, or notification topic (events.bulk)
  entity:team          - a team, group, or organization (platform-team, SRE)
  entity:rotation      - an oncall rotation or schedule (payments-oncall)
  entity:document      - a document, runbook, file (predicates.yml, README.md)
  entity:auth_method   - an auth/authn/authz method (OAuth, mTLS, JWT)
  entity:incident      - an incident, outage, or post-mortem
  entity:concept       - architectural pattern, policy, or non-structural entity

Subjects:
{subjects}

Classifications:
"""


def infer_subject_types(
    subjects: list[str],
    generator: Generator,
    *,
    batch_size: int = 50,
) -> dict[str, str]:
    """Batched LLM classification of subjects → entity types.

    Returns `{subject: entity_type}` for every input. Subjects the LLM fails
    to classify get `entity:concept` as the fallback. Order-preserving:
    builds a dict keyed by the input subject string verbatim.
    """
    out: dict[str, str] = {s: _FALLBACK_TYPE for s in subjects}
    valid_types = set(ENTITY_TYPES)

    for batch_start in range(0, len(subjects), batch_size):
        batch = subjects[batch_start : batch_start + batch_size]
        listed = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(batch))
        prompt = _TYPE_INFER_PROMPT.format(subjects=listed)
        try:
            resp = generator.generate(GenerationRequest(prompt=prompt, max_output_tokens=1024))
        except Exception:
            continue  # leave fallback in out

        for line in resp.text.splitlines():
            line = line.strip()
            m = re.match(r"^(\d+)\s*[.):]\s*(entity:\w+)\s*$", line, re.IGNORECASE)
            if not m:
                continue
            idx = int(m.group(1)) - 1
            etype = m.group(2).lower()
            if 0 <= idx < len(batch) and etype in valid_types:
                out[batch[idx]] = etype

    return out


def filter_and_relabel_claims(
    claims: Iterable,
    type_map: dict[str, str],
    *,
    drop_stopwords: bool = True,
):
    """Apply stopword filter + subject_type relabeling to a sequence of Claims.

    Pure function — emits new Claim instances via Pydantic's model_copy without
    mutating inputs. Skips claims whose subject should be dropped.
    """
    out = []
    for c in claims:
        if drop_stopwords and should_drop_subject(c.subject):
            continue
        new_type = type_map.get(c.subject, c.subject_type)
        if new_type == c.subject_type:
            out.append(c)
        else:
            out.append(c.model_copy(update={"subject_type": new_type}))
    return out
