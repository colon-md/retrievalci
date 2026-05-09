"""Retriever adapters for trace-state replay."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class RetrieverCall:
    """Safe request/response metadata for one retriever call."""

    query: str
    k: int
    status_code: int | None
    latency_ms: float
    result_ids: tuple[str, ...]
    error: str | None = None

    def to_row(self) -> dict[str, Any]:
        return {
            "query_sha256": hashlib.sha256(self.query.encode("utf-8")).hexdigest(),
            "query_chars": len(self.query),
            "k": self.k,
            "status_code": self.status_code,
            "latency_ms": self.latency_ms,
            "result_ids": list(self.result_ids),
            "error": self.error,
        }


class HTTPTraceRetriever:
    """POST rendered trace-state queries to a production retriever endpoint.

    The endpoint receives `{"query": text, "k": k}` and may return either a
    list of ids/dicts or a dict containing `results`, `hits`, `matches`,
    `documents`, or `data`.
    """

    def __init__(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        if not url.startswith(("http://", "https://")):
            msg = "HTTP retriever URL must start with http:// or https://"
            raise ValueError(msg)
        self.url = url
        self.headers = dict(headers or {})
        self.timeout_s = timeout_s
        self.calls: list[RetrieverCall] = []

    def query(self, text: str, *, k: int) -> list[tuple[str, float]]:
        payload = json.dumps({"query": text, "k": k}).encode("utf-8")
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            **self.headers,
        }
        request = Request(self.url, data=payload, headers=headers, method="POST")
        started = time.perf_counter()
        status_code: int | None = None
        try:
            with urlopen(request, timeout=self.timeout_s) as response:
                status_code = int(response.status)
                data = json.loads(response.read().decode("utf-8"))
            results = parse_retriever_response(data)
            self._record(
                text,
                k,
                status_code=status_code,
                latency_ms=_elapsed_ms(started),
                result_ids=tuple(result_id for result_id, _ in results),
            )
            return results[:k]
        except HTTPError as exc:
            status_code = int(exc.code)
            message = f"HTTP {exc.code}: {exc.reason}"
            self._record_error(text, k, status_code, started, message)
            raise RuntimeError(f"HTTP retriever query failed: {message}") from exc
        except (OSError, TimeoutError, URLError, ValueError, json.JSONDecodeError) as exc:
            message = str(exc)
            self._record_error(text, k, status_code, started, message)
            raise RuntimeError(f"HTTP retriever query failed: {message}") from exc

    def _record(
        self,
        query: str,
        k: int,
        *,
        status_code: int | None,
        latency_ms: float,
        result_ids: tuple[str, ...],
        error: str | None = None,
    ) -> None:
        self.calls.append(
            RetrieverCall(
                query=query,
                k=k,
                status_code=status_code,
                latency_ms=latency_ms,
                result_ids=result_ids,
                error=error,
            )
        )

    def _record_error(
        self,
        query: str,
        k: int,
        status_code: int | None,
        started: float,
        error: str,
    ) -> None:
        self._record(
            query,
            k,
            status_code=status_code,
            latency_ms=_elapsed_ms(started),
            result_ids=(),
            error=error,
        )


def parse_http_headers(values: Iterable[str] | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    for value in values or ():
        if ":" not in value:
            msg = f"HTTP retriever header must be `Name: value`, got `{value}`"
            raise ValueError(msg)
        name, header_value = value.split(":", 1)
        name = name.strip()
        header_value = header_value.strip()
        if not name or not header_value:
            msg = f"HTTP retriever header must be `Name: value`, got `{value}`"
            raise ValueError(msg)
        headers[name] = header_value
    return headers


def parse_retriever_response(data: Any) -> list[tuple[str, float]]:
    items = _response_items(data)
    results: list[tuple[str, float]] = []
    for item in items:
        if isinstance(item, str):
            results.append((item, 0.0))
            continue
        if not isinstance(item, Mapping):
            continue
        result_id = _extract_result_id(item)
        if result_id is None:
            continue
        results.append((result_id, _extract_score(item)))
    return results


def write_retriever_calls(path: str | Path, calls: Sequence[RetrieverCall]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        for call in calls:
            f.write(json.dumps(call.to_row()) + "\n")


def _response_items(data: Any) -> Sequence[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, Mapping):
        for key in ("results", "hits", "matches", "documents", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    msg = "retriever response must be a list or contain a results-like list"
    raise ValueError(msg)


def _extract_result_id(item: Mapping[str, Any]) -> str | None:
    for key in ("chunk_id", "doc_id", "id", "document_id", "source_id"):
        value = item.get(key)
        if value:
            return str(value)
    metadata = item.get("metadata")
    if isinstance(metadata, Mapping):
        for key in ("chunk_id", "doc_id", "id", "document_id", "source_id"):
            value = metadata.get(key)
            if value:
                return str(value)
    document = item.get("document")
    if isinstance(document, Mapping):
        return _extract_result_id(document)
    return None


def _extract_score(item: Mapping[str, Any]) -> float:
    for key in ("score", "similarity", "_score", "relevance"):
        value = item.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 3)
