import unittest

from fastapi.responses import FileResponse, JSONResponse, Response

import main


@unittest.skipUnless(hasattr(main, "serve_frontend"), "serve_frontend only exists when mini_app_dist is present")
class MainFrontendServingTests(unittest.IsolatedAsyncioTestCase):
    async def test_serve_frontend_returns_404_for_unhandled_api_paths(self):
        result = await main.serve_frontend("api/missing")

        self.assertIsInstance(result, JSONResponse)
        self.assertEqual(result.status_code, 404)

    async def test_serve_frontend_serves_existing_static_file_and_index_fallback(self):
        static_result = await main.serve_frontend("index.html")
        fallback_result = await main.serve_frontend("chat/thread/123")

        self.assertIsInstance(static_result, FileResponse)
        self.assertTrue(str(static_result.path).endswith("mini_app_dist/index.html"))
        self.assertIsInstance(fallback_result, FileResponse)
        self.assertTrue(str(fallback_result.path).endswith("mini_app_dist/index.html"))

    async def test_serve_frontend_returns_reload_script_for_stale_js_chunk(self):
        result = await main.serve_frontend("assets/old-chunk.js")

        self.assertIsInstance(result, Response)
        self.assertEqual(result.media_type, "application/javascript")
        self.assertIn("window.location.reload(true)", result.body.decode())


if __name__ == "__main__":
    unittest.main()