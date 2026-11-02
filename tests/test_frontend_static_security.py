"""Hermetic static-response tests; never import runtime config or the live app."""

import ast
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote

from fastapi import FastAPI

from core.frontend_static import frontend_static_response


class FrontendStaticSecurityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="frontend-static-test-")
        self.addCleanup(self.temp_dir.cleanup)
        self.fixture_root = Path(self.temp_dir.name)
        self.build = self.fixture_root / "mini_app_dist"
        self.build.mkdir()
        (self.build / "assets").mkdir()
        (self.build / "index.html").write_text("<html>SPA fixture</html>")
        (self.build / "assets" / "app.js").write_text("console.log('safe fixture')")
        self.outside = self.fixture_root / "outside.txt"
        self.outside.write_text("outside-fixture-must-never-be-served")

        async def endpoint(full_path: str):
            return frontend_static_response(self.build, full_path)

        self.app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
        self.app.add_api_route("/{full_path:path}", endpoint, methods=["GET"])

    async def request(self, raw_path: str):
        """Use server-decoded ASGI scopes, without client dot-path normalization."""
        messages = []
        request_received = False
        response_complete = asyncio.Event()

        async def receive():
            nonlocal request_received
            if not request_received:
                request_received = True
                return {"type": "http.request", "body": b"", "more_body": False}
            # Starlette listens for disconnects while sending files on ASGI2.3.
            # Repeating an immediately-ready body forever would starve that
            # concurrent sender; disconnect only once the complete body arrived.
            await response_complete.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                response_complete.set()

        await asyncio.wait_for(self.app(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": unquote(raw_path),
                "raw_path": raw_path.encode("utf-8"),
                "root_path": "",
                "query_string": b"",
                "headers": [],
                "client": ("127.0.0.1", 12345),
                "server": ("fixture.invalid", 80),
            },
            receive,
            send,
        ), timeout=5)
        start = next(message for message in messages if message["type"] == "http.response.start")
        body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
        headers = {name.decode(): value.decode() for name, value in start["headers"]}
        return start["status"], headers, body

    async def test_asgi_rejects_plain_and_encoded_traversal_without_reading_outside(self):
        for path in (
            "/../outside.txt",
            "/%2e%2e/outside.txt",
            "/assets/%2E%2e/%2e%2e/outside.txt",
            "/assets%2f..%2f..%2foutside.txt",
            "/assets/%2e/app.js",
            "/%2f" + str(self.outside).lstrip("/"),
            "/%5c..%5coutside.txt",
            "/C:/outside.txt",
            "/C:outside.txt",
            "/assets/app.js%00.txt",
        ):
            with self.subTest(path=path):
                status, _, body = await self.request(path)
                self.assertEqual(status, 404)
                self.assertNotIn(self.outside.read_bytes(), body)

    async def test_existing_file_and_spa_fallback_preserve_no_referrer(self):
        for path, expected in (
            ("/assets/app.js", b"console.log('safe fixture')"),
            ("/index.html", b"<html>SPA fixture</html>"),
            ("/", b"<html>SPA fixture</html>"),
            ("/chat/thread/123", b"<html>SPA fixture</html>"),
            ("/missing-image.png", b"<html>SPA fixture</html>"),
        ):
            with self.subTest(path=path):
                status, headers, body = await self.request(path)
                self.assertEqual(status, 200)
                self.assertEqual(headers["referrer-policy"], "no-referrer")
                self.assertEqual(body, expected)

    async def test_url_decoding_happens_only_at_the_server_boundary(self):
        (self.build / "assets" / "نام فایل.png").write_bytes(b"image fixture")
        status, _, body = await self.request("/assets/%D9%86%D8%A7%D9%85%20%D9%81%D8%A7%DB%8C%D9%84.png")
        self.assertEqual((status, body), (200, b"image fixture"))
        status, _, body = await self.request("/%252e%252e/outside.txt")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"<html>SPA fixture</html>")

    async def test_api_and_documentation_probes_never_receive_spa_document(self):
        for path in ("/api/missing", "/openapi.json", "/docs", "/docs/index.html", "/redoc", "/redoc/index.html"):
            with self.subTest(path=path):
                status, _, body = await self.request(path)
                self.assertEqual(status, 404)
                self.assertEqual(body, b'{"detail":"Not Found"}')

    async def test_stale_javascript_returns_empty_non_executable_uncacheable_410(self):
        status, headers, body = await self.request("/assets/old-chunk.js")
        self.assertEqual(status, 410)
        self.assertEqual(headers["cache-control"], "no-store, no-cache, must-revalidate")
        self.assertEqual(body, b"")

    async def test_symlink_file_and_directory_escapes_return_404(self):
        (self.build / "assets" / "escape.js").symlink_to(self.outside)
        (self.build / "escape-dir").symlink_to(self.fixture_root, target_is_directory=True)
        (self.build / "assets" / "missing-escape.js").symlink_to(self.fixture_root / "missing.txt")
        for path in ("/assets/escape.js", "/escape-dir/outside.txt", "/assets/missing-escape.js"):
            with self.subTest(path=path):
                status, _, body = await self.request(path)
                self.assertEqual(status, 404)
                self.assertNotIn(self.outside.read_bytes(), body)

    async def test_internal_symlink_remains_usable(self):
        (self.build / "assets" / "alias.js").symlink_to(self.build / "assets" / "app.js")
        status, _, body = await self.request("/assets/alias.js")
        self.assertEqual((status, body), (200, b"console.log('safe fixture')"))

    async def test_spa_and_root_fallback_cannot_follow_index_symlink_outside_build(self):
        (self.build / "index.html").unlink()
        (self.build / "index.html").symlink_to(self.outside)
        for path in ("/", "/chat/thread/123", "/index.html"):
            with self.subTest(path=path):
                status, _, body = await self.request(path)
                self.assertEqual(status, 404)
                self.assertNotIn(self.outside.read_bytes(), body)

    async def test_symlink_loop_and_filesystem_errors_fail_closed(self):
        (self.build / "loop").symlink_to(self.build / "loop")
        status, _, _ = await self.request("/loop")
        self.assertEqual(status, 404)
        with patch("core.frontend_static.Path.resolve", side_effect=PermissionError):
            self.assertEqual(frontend_static_response(self.build, "index.html").status_code, 404)

    def test_absolute_backslash_dot_and_nul_paths_are_rejected_before_filesystem_access(self):
        for path in (str(self.outside), "../outside.txt", "./index.html", r"assets\app.js", "\\server\\share", "C:/outside", "index.html\x00"):
            with self.subTest(path=path), patch("core.frontend_static.Path.resolve", side_effect=AssertionError("must reject before resolving")):
                self.assertEqual(frontend_static_response(self.build, path).status_code, 404)

    def test_main_catchall_and_root_delegate_to_the_guarded_policy(self):
        # Verify wiring without importing main, settings, credentials or services.
        source = (Path(__file__).resolve().parents[1] / "main.py").read_text()
        tree = ast.parse(source)
        functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)}
        for name, expected_path in (("serve_frontend", "full_path"), ("root", "")):
            calls = [node for node in ast.walk(functions[name]) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "frontend_static_response"]
            self.assertEqual(len(calls), 1)
            self.assertIsInstance(calls[0].args[0], ast.Name)
            self.assertEqual(calls[0].args[0].id, "static_dir")
            if name == "serve_frontend":
                self.assertEqual(calls[0].args[1].id, expected_path)
            else:
                self.assertEqual(calls[0].args[1].value, expected_path)


if __name__ == "__main__":
    unittest.main()
