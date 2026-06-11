import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import report_static_delivery as static_report


class StaticDeliveryReportTests(unittest.TestCase):
    def test_choose_representative_asset_prefers_js_asset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            assets = Path(tmpdir) / "assets"
            assets.mkdir()
            (assets / "app.css").write_text("body{}", encoding="utf-8")
            (assets / "app.js").write_text("console.log(1)", encoding="utf-8")

            self.assertEqual(static_report.choose_representative_asset(Path(tmpdir)), "assets/app.js")

    def test_evaluate_probe_requires_status_body_and_headers(self):
        response = {
            "status": 200,
            "headers": {
                "cache-control": "public, max-age=31536000, immutable",
                "x-static-delivery": "nginx",
            },
            "body_bytes": 12,
            "elapsed_ms": 3.4,
        }

        probe = static_report.evaluate_probe(
            name="asset",
            response=response,
            expected_status=200,
            required_headers={
                "cache-control": ("public", "immutable"),
                "x-static-delivery": ("nginx",),
            },
            require_body=True,
        )

        self.assertTrue(probe["ok"])
        self.assertEqual(probe["headers"]["x-static-delivery"], "nginx")

    def test_collect_report_checks_static_upload_and_media_contracts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            assets = Path(tmpdir) / "assets"
            assets.mkdir()
            (assets / "app.js").write_text("console.log(1)", encoding="utf-8")

            def fake_fetch(url: str, *, timeout: int = 15):
                if url.endswith("/assets/app.js"):
                    return {
                        "status": 200,
                        "headers": {
                            "cache-control": "public, max-age=31536000, immutable",
                            "x-static-delivery": "nginx",
                            "content-type": "application/javascript",
                        },
                        "body_bytes": 14,
                        "elapsed_ms": 2.0,
                    }
                if url.endswith("/sw.js"):
                    return {
                        "status": 200,
                        "headers": {
                            "cache-control": "no-cache, no-store, must-revalidate",
                            "service-worker-allowed": "/",
                            "x-static-delivery": "nginx",
                        },
                        "body_bytes": 9,
                        "elapsed_ms": 2.0,
                    }
                if url.endswith("/manifest.webmanifest"):
                    return {
                        "status": 200,
                        "headers": {
                            "cache-control": "no-cache, no-store, must-revalidate",
                            "x-static-delivery": "nginx",
                        },
                        "body_bytes": 9,
                        "elapsed_ms": 2.0,
                    }
                if "/assets/__p4_missing_asset__.css" in url:
                    return {"status": 404, "headers": {}, "body_bytes": 0, "elapsed_ms": 1.0}
                if "/uploads/__p4_static_probe__" in url:
                    return {"status": 404, "headers": {}, "body_bytes": 0, "elapsed_ms": 1.0}
                if "/api/chat/files/__p4_static_probe__" in url:
                    return {"status": 401, "headers": {}, "body_bytes": 20, "elapsed_ms": 1.0}
                raise AssertionError(url)

            with patch("scripts.report_static_delivery.fetch_url", side_effect=fake_fetch):
                report = static_report.collect_report(base_url="https://iran.example", dist_dir=Path(tmpdir))

        self.assertTrue(report["ok"])
        self.assertEqual(
            [probe["name"] for probe in report["probes"]],
            [
                "immutable_asset",
                "service_worker_no_cache",
                "manifest_no_cache",
                "missing_asset_404",
                "raw_uploads_not_public",
                "protected_chat_media_requires_token",
            ],
        )


if __name__ == "__main__":
    unittest.main()
