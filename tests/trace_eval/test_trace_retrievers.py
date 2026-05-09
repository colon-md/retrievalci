from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

from searchtrace.trace_retrievers import (
    HTTPTraceRetriever,
    parse_http_headers,
    parse_retriever_response,
    write_retriever_calls,
)


def test_parse_http_headers_requires_name_value_pairs() -> None:
    assert parse_http_headers(["Authorization: Bearer token", "X-Team: eval"]) == {
        "Authorization": "Bearer token",
        "X-Team": "eval",
    }


def test_parse_retriever_response_accepts_common_shapes() -> None:
    assert parse_retriever_response({"results": [{"id": "doc_1", "score": 0.8}]}) == [
        ("doc_1", 0.8)
    ]
    assert parse_retriever_response({"hits": [{"metadata": {"doc_id": "doc_2"}}]}) == [
        ("doc_2", 0.0)
    ]
    assert parse_retriever_response(["doc_3"]) == [("doc_3", 0.0)]


def test_http_trace_retriever_posts_query_and_records_safe_metadata(tmp_path) -> None:
    handler = _handler_for({"results": [{"id": "doc_target", "score": 0.99}]})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/retrieve"
        retriever = HTTPTraceRetriever(
            url,
            headers={"Authorization": "Bearer secret-token"},
            timeout_s=2.0,
        )

        assert retriever.query("payments postgres", k=2) == [("doc_target", 0.99)]
        assert handler.requests[0]["json"] == {"query": "payments postgres", "k": 2}
        assert handler.requests[0]["authorization"] == "Bearer secret-token"
        assert retriever.calls[0].result_ids == ("doc_target",)

        calls_path = tmp_path / "retriever-calls.jsonl"
        write_retriever_calls(calls_path, retriever.calls)
        content = calls_path.read_text(encoding="utf-8")
        assert "doc_target" in content
        assert "payments postgres" not in content
        assert "query_sha256" in content
        assert "secret-token" not in content
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _handler_for(response: dict):
    class Handler(BaseHTTPRequestHandler):
        requests: ClassVar[list[dict]] = []

        def do_POST(self) -> None:
            length = int(self.headers.get("content-length", "0"))
            body = self.rfile.read(length)
            self.requests.append(
                {
                    "path": self.path,
                    "json": json.loads(body.decode("utf-8")),
                    "authorization": self.headers.get("authorization"),
                }
            )
            payload = json.dumps(response).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt: str, *args) -> None:
            return

    return Handler
