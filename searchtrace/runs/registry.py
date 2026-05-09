"""Filesystem registry for SearchTrace run artifacts."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from searchtrace.runs.types import RunArtifact

_SLUG_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def utc_now_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def slugify(value: str | None) -> str:
    if not value:
        return ""
    slug = _SLUG_RE.sub("-", value.strip()).strip("-._").lower()
    return slug[:48]


def reserve_run_dir(registry_dir: str | Path, *, name: str | None = None) -> tuple[str, Path]:
    registry = Path(registry_dir)
    registry.mkdir(parents=True, exist_ok=True)
    stem = utc_now_id()
    suffix = slugify(name)
    base = f"{stem}-{suffix}" if suffix else stem
    run_id = base
    run_dir = registry / run_id
    counter = 2
    while run_dir.exists():
        run_id = f"{base}-{counter}"
        run_dir = registry / run_id
        counter += 1
    run_dir.mkdir(parents=True)
    return run_id, run_dir


def write_manifest(run_dir: str | Path, manifest: RunArtifact) -> None:
    path = Path(run_dir) / "manifest.json"
    path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")


def load_manifest(path: str | Path) -> RunArtifact:
    manifest_path = Path(path)
    if manifest_path.is_dir():
        manifest_path = manifest_path / "manifest.json"
    return RunArtifact.model_validate_json(manifest_path.read_text(encoding="utf-8"))


def list_runs(registry_dir: str | Path) -> list[RunArtifact]:
    registry = Path(registry_dir)
    if not registry.is_dir():
        return []
    manifests: list[RunArtifact] = []
    for path in sorted(registry.glob("*/manifest.json"), reverse=True):
        try:
            manifests.append(RunArtifact.model_validate_json(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, ValueError):
            continue
    return manifests


def relpath(path: str | Path, base: str | Path) -> str:
    return Path(path).resolve().relative_to(Path(base).resolve()).as_posix()
