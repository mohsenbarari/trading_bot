import unittest

from fastapi.responses import FileResponse, JSONResponse, Response

import main


@unittest.skipUnless(hasattr(main, "serve_frontend"), "serve_frontend only exists when mini_app_dist is present")
class MainFrontendServingTests(unittest.IsolatedAsyncioTestCase):
    async def test_serve_frontend_returns_404_for_unhandled_api_paths(self):
        result = await main.serve_frontend("api/missing")

        self.assertIsInstance(result, JSONResponse)
        self.assertEqual(result.status_code, 404)

    async def test_serve_frontend_returns_404_for_blocked_docs_probe_paths(self):
        for path in ("openapi.json", "docs", "redoc", "docs/index.html"):
            with self.subTest(path=path):
                result = await main.serve_frontend(path)

                self.assertIsInstance(result, JSONResponse)
                self.assertEqual(result.status_code, 404)

    async def test_serve_frontend_serves_existing_static_file_and_index_fallback(self):
        static_result = await main.serve_frontend("index.html")
        fallback_result = await main.serve_frontend("chat/thread/123")

        self.assertIsInstance(static_result, FileResponse)
        self.assertTrue(str(static_result.path).endswith("mini_app_dist/index.html"))
        self.assertEqual(static_result.headers["referrer-policy"], "no-referrer")
        self.assertIsInstance(fallback_result, FileResponse)
        self.assertTrue(str(fallback_result.path).endswith("mini_app_dist/index.html"))
        self.assertEqual(fallback_result.headers["referrer-policy"], "no-referrer")

    async def test_root_document_uses_the_same_no_referrer_boundary(self):
        result = await main.root()

        self.assertIsInstance(result, FileResponse)
        self.assertEqual(result.headers["referrer-policy"], "no-referrer")

    async def test_serve_frontend_returns_non_executable_410_for_stale_js_chunk(self):
        result = await main.serve_frontend("assets/old-chunk.js")

        self.assertIsInstance(result, Response)
        self.assertEqual(result.status_code, 410)
        self.assertEqual(result.headers["cache-control"], "no-store, no-cache, must-revalidate")
        self.assertNotIn("window.location.reload", result.body.decode())


class MainRoutingPolicyTests(unittest.TestCase):
    def test_registration_context_isolation_uses_only_exact_cookie_endpoints(self):
        for path in (
            "/api/auth/registration-context",
            "/api/auth/registration-context/exchange",
            "/api/auth/registration-context/otp/request",
            "/api/auth/registration-context/otp/verify",
            "/api/auth/registration-context/complete",
            "/api/auth/registration-context/clear",
        ):
            with self.subTest(path=path):
                self.assertTrue(main._is_production_test_isolation_public_path(path))

        self.assertFalse(
            main._is_production_test_isolation_public_path(
                "/api/auth/registration-context/opaque-handle"
            )
        )
        self.assertFalse(
            main._is_production_test_isolation_public_path("/api/auth/pending-registration")
        )


if __name__ == "__main__":
    unittest.main()
