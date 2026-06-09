import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ObservabilityConfigTests(unittest.TestCase):
    def test_promtail_keeps_original_json_line_for_loki_queries(self):
        config = (ROOT / "observability/promtail/promtail-config.yml").read_text(encoding="utf-8")

        self.assertIn("- docker: {}", config)
        self.assertIn("- json:", config)
        self.assertIn("- labels:", config)
        self.assertNotRegex(config, r"(?m)^\s*-\s*output:\s*\n\s*source:\s*message\s*$")

    def test_grafana_dashboards_are_valid_json_and_use_loki_json_queries(self):
        dashboard_dir = ROOT / "observability/grafana/dashboards"
        dashboards = sorted(dashboard_dir.glob("*.json"))
        self.assertTrue(dashboards)

        combined_queries: list[str] = []
        for path in dashboards:
            payload = json.loads(path.read_text(encoding="utf-8"))
            for panel in payload.get("panels", []):
                for target in panel.get("targets", []):
                    expr = target.get("expr")
                    if expr:
                        combined_queries.append(expr)

        rendered = "\n".join(combined_queries)
        self.assertIn("| json", rendered)
        for expected_field in ("event", "status_code", "duration_ms", "path", "job_name"):
            self.assertIn(expected_field, rendered)

    def test_alert_rules_keep_json_field_assumptions_visible(self):
        rules = (ROOT / "observability/grafana/provisioning/alerting/rules.yml").read_text(encoding="utf-8")

        self.assertIn("| json", rules)
        for expected_field in (
            "status_code",
            "path",
            "server_mode",
            "unsynced_change_log_count",
            "oldest_unsynced_age_seconds",
            "sync_retry_queue_length",
        ):
            self.assertIn(expected_field, rules)

        promtail = (ROOT / "observability/promtail/promtail-config.yml").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"(?m)^\s*source:\s*message\s*$", promtail))

    def test_public_nginx_configs_block_metrics_endpoint(self):
        paths = [
            ROOT / "scripts/setup_foreign_nginx.sh",
            ROOT / "deploy/production/nginx-iran-online.conf.template",
        ]

        for path in paths:
            with self.subTest(path=str(path.relative_to(ROOT))):
                content = path.read_text(encoding="utf-8")
                self.assertRegex(content, r"location\s+=\s+/metrics\s*\{")
                metrics_block = re.search(r"location\s+=\s+/metrics\s*\{(?P<body>.*?)\n\s*\}", content, re.S)
                self.assertIsNotNone(metrics_block)
                self.assertIn("deny all", metrics_block.group("body"))
                self.assertIn("return 404", metrics_block.group("body"))


if __name__ == "__main__":
    unittest.main()
