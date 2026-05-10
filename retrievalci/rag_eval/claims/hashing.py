"""Content-hash derivation for claim_id, proof_set_id, trace_id, and supporting hashes.

Every hash is sha256, lowercase hex, length 64. Hashes are deterministic functions of their
inputs — given the same inputs they always return the same value, including across processes
and platforms.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Iterable
from datetime import datetime

_HASH_SEP = b"|"


def _sha256_hex(parts: Iterable[bytes]) -> str:
    h = hashlib.sha256()
    for i, part in enumerate(parts):
        if i > 0:
            h.update(_HASH_SEP)
        h.update(part)
    return h.hexdigest()


def derive_claim_id(
    subject: str,
    predicate: str,
    object_: str | None,
    prompt_id: str,
    evidence_uris: Iterable[str],
) -> str:
    """Compute the content-hash claim_id.

    claim_id = sha256(subject | predicate | object | prompt_id | sorted_evidence_uris)
    Order-invariant in evidence_uris (sorted internally). object=None is treated as empty string.
    """
    sorted_uris = "\n".join(sorted(evidence_uris))
    parts = [
        subject.encode("utf-8"),
        predicate.encode("utf-8"),
        (object_ or "").encode("utf-8"),
        prompt_id.encode("utf-8"),
        sorted_uris.encode("utf-8"),
    ]
    return _sha256_hex(parts)


def derive_proof_set_id(source_ids: Iterable[str]) -> str:
    """Compute proof_set_id = sha256(sorted source_ids)."""
    sorted_ids = "\n".join(sorted(source_ids))
    return _sha256_hex([sorted_ids.encode("utf-8")])


def derive_trace_id(query_hash: str, principal_hash: str, ts: datetime) -> str:
    """Compute trace_id = sha256(query_hash | principal_hash | ts.isoformat())."""
    parts = [
        query_hash.encode("utf-8"),
        principal_hash.encode("utf-8"),
        ts.isoformat().encode("utf-8"),
    ]
    return _sha256_hex(parts)


def hash_user_principal(principal: str, salt: bytes) -> str:
    """One-way hash of a user identity. The salt is project-wide, stored in Secret Manager.

    Reverse mapping (hash → principal) lives in a separate audit-only table requiring
    explicit privilege escalation to read.
    """
    if not salt:
        raise ValueError("salt must be non-empty")
    return _sha256_hex([salt, principal.encode("utf-8")])


def hash_query(query: str) -> str:
    """Stable hash of the user's verbatim query string. NFC-normalized."""
    normalized = unicodedata.normalize("NFC", query)
    return _sha256_hex([normalized.encode("utf-8")])


def hash_acl_labels(labels: Iterable[str]) -> str:
    """Order-invariant hash of an ACL label set."""
    sorted_labels = "\n".join(sorted(set(labels)))
    return _sha256_hex([sorted_labels.encode("utf-8")])


def hash_str(s: str) -> str:
    """SHA-256 hex digest of a single UTF-8 string."""
    return _sha256_hex([s.encode("utf-8")])
