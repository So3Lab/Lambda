"""Tests for web_fetch tool."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from SimpleLLMFunc.runtime.primitives import PrimitiveCallContext

from lambda_coding_agent.builtin.workspace import build_workspace_pack
from lambda_coding_agent.tools.webfetch import web_fetch


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/html":
            body = b"""
            <html><head><title>Ignored</title><script>bad()</script></head>
            <body><h1>Hello Docs</h1><p>Example &amp; text.</p></body></html>
            """
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/text":
            body = b"plain response"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/large":
            body = b"x" * 100
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/binary":
            body = b"\x00\x01\x02"
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = b"missing"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@pytest.fixture()
def server_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


class TestWebFetch:
    async def test_fetch_html_extracts_text(self, server_url):
        result = await web_fetch(f"{server_url}/html")
        assert result["success"] is True
        assert result["status_code"] == 200
        assert "Hello Docs" in result["text"]
        assert "Example & text." in result["text"]
        assert "bad()" not in result["text"]

    async def test_rejects_non_http_url(self):
        result = await web_fetch("file:///etc/passwd")
        assert result["success"] is False
        assert "http" in result["error"].lower()

    async def test_http_error_returns_status(self, server_url):
        result = await web_fetch(f"{server_url}/missing")
        assert result["success"] is False
        assert result["status_code"] == 404
        assert "HTTP error 404" in result["error"]

    async def test_rejects_binary_content_type(self, server_url):
        result = await web_fetch(f"{server_url}/binary")
        assert result["success"] is False
        assert "Unsupported content type" in result["error"]

    async def test_truncates_text(self, server_url):
        result = await web_fetch(f"{server_url}/large", max_chars=10)
        assert result["success"] is True
        assert result["truncated"] is True
        assert result["text"].startswith("x" * 10)

    def test_workspace_pack_registers_web_fetch(self, server_url, tmp_path):
        pack = build_workspace_pack(str(tmp_path))
        entry = next(e for e in pack.primitives if e.name == "workspace.web_fetch")
        ctx = PrimitiveCallContext(
            primitive_name="workspace.web_fetch",
            call_id="test",
            execution_id="test",
            backend=pack.backend,
        )
        result = entry.handler(ctx, f"{server_url}/text")
        assert "success: True" in result
        assert "saved_path: .lambda/webfetch/" in result
        assert "--- preview ---" in result
        assert "plain response" in result

    def test_workspace_web_fetch_saves_full_content_and_returns_preview(self, server_url, tmp_path):
        pack = build_workspace_pack(str(tmp_path))
        entry = next(e for e in pack.primitives if e.name == "workspace.web_fetch")
        ctx = PrimitiveCallContext(
            primitive_name="workspace.web_fetch",
            call_id="test",
            execution_id="test",
            backend=pack.backend,
        )
        result = entry.handler(ctx, f"{server_url}/large", max_chars=10, file="fetches/large.txt")
        assert "success: True" in result
        assert "saved_path: fetches/large.txt" in result
        assert "preview_truncated: True" in result
        assert "--- preview ---" in result
        assert (tmp_path / "fetches" / "large.txt").read_text() == "x" * 100
