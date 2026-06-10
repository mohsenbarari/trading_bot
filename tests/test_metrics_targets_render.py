import unittest
from unittest.mock import patch

from scripts import render_metrics_targets


class MetricsTargetsRenderTests(unittest.TestCase):
    def test_manifest_marks_api_as_http_and_other_surfaces_as_logs_only(self):
        with patch.dict("os.environ", {"OBSERVABILITY_API_KEY": "secret-key", "TRADING_BOT_METRICS_BACKEND": "memory"}, clear=True):
            manifest = render_metrics_targets.build_manifest(
                server_mode="foreign",
                api_url="http://127.0.0.1:8000/metrics",
                compose_file="docker-compose.yml",
            )

        self.assertEqual(manifest["generated_for"], "foreign")
        self.assertEqual(manifest["surfaces"][0]["service"], "api")
        self.assertEqual(manifest["surfaces"][0]["collection_mode"], "http")
        self.assertEqual(manifest["surfaces"][0]["headers"][render_metrics_targets.OBSERVABILITY_HEADER], "[set in env]")
        self.assertEqual(manifest["surfaces"][1]["service"], "bot")
        self.assertEqual(manifest["surfaces"][1]["collection_mode"], "logs_only")
        self.assertEqual(manifest["surfaces"][2]["service"], "sync_worker")
        self.assertEqual(manifest["metrics_backend_policy"]["default_backend"], "memory")

    def test_manifest_marks_missing_api_key_explicitly(self):
        with patch.dict("os.environ", {}, clear=True):
            manifest = render_metrics_targets.build_manifest(
                server_mode="iran",
                api_url="http://127.0.0.1:8000/metrics",
                compose_file="docker-compose.iran.yml",
            )

        self.assertEqual(manifest["surfaces"][0]["headers"][render_metrics_targets.OBSERVABILITY_HEADER], "[missing]")
        self.assertEqual(manifest["generated_for"], "iran")


if __name__ == "__main__":
    unittest.main()
