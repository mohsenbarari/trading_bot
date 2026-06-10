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

    def test_alert_contact_points_and_policies_are_env_driven_and_safe(self):
        contact_points = (ROOT / "observability/grafana/provisioning/alerting/contact-points.yml").read_text(
            encoding="utf-8"
        )
        policies = (ROOT / "observability/grafana/provisioning/alerting/notification-policies.yml").read_text(
            encoding="utf-8"
        )
        compose = (ROOT / "docker-compose.observability.yml").read_text(encoding="utf-8")

        for expected_receiver in (
            "Trading Bot Local Webhook",
            "Trading Bot Production Webhook",
            "Trading Bot Production Email",
        ):
            self.assertIn(expected_receiver, contact_points)

        self.assertIn("${GRAFANA_ALERT_WEBHOOK_URL}", contact_points)
        self.assertIn("${GRAFANA_ALERT_EMAIL_ADDRESSES}", contact_points)
        self.assertIn("${GRAFANA_ALERT_DEFAULT_RECEIVER}", policies)
        self.assertIn("${GRAFANA_ALERT_CRITICAL_RECEIVER}", policies)
        self.assertIn("${GRAFANA_ALERT_WARNING_RECEIVER}", policies)
        self.assertIn("request_id={{ index .CommonLabels \"request_id\" }}", contact_points)
        self.assertIn("event_id={{ index .CommonLabels \"event_id\" }}", contact_points)
        self.assertNotIn("default.message", contact_points)
        for expected_env in (
            "GF_SMTP_ENABLED",
            "GF_SMTP_HOST",
            "GF_SMTP_USER",
            "GF_SMTP_PASSWORD",
            "GRAFANA_ALERT_DEFAULT_RECEIVER",
            "GRAFANA_ALERT_WEBHOOK_URL",
            "GRAFANA_ALERT_EMAIL_ADDRESSES",
        ):
            self.assertIn(expected_env, compose)

    def test_production_release_script_enforces_observability_guards(self):
        script = (ROOT / "scripts/production_deploy_online.sh").read_text(encoding="utf-8")

        for expected in (
            "validate_observability_env_file",
            "TRUSTED_PROXY_CIDRS",
            "OBSERVABILITY_TELEGRAM_USER_HASH_SALT",
            "GRAFANA_ALERT_DEFAULT_RECEIVER",
            "Trading Bot Local Webhook",
            "install_sync_sampler_local",
            "install_sync_sampler_remote",
            "verify_sync_sampler_local",
            "verify_sync_sampler_remote",
            "trading-bot-sync-health-sampler.timer",
            "LOCAL_OS_CODENAME",
            "$LOCAL_DPKG_ARCH\" != \"$IRAN_DPKG_ARCH\" || \"$LOCAL_OS_CODENAME\" != \"$IRAN_OS_CODENAME",
            "apt identity differs",
            "npm ci --silent",
            "--source-env-file",
            "install_foreign_runtime_env",
            "Installed rendered foreign runtime env",
            "filter_hosts_file_for_managed_domains",
            "$i == foreign_domain || $i == iran_domain",
            "remote_docker_service_guard",
            "systemctl reset-failed docker.service docker.socket",
            "systemctl enable --now docker.socket",
            "check_local; install_sync_sampler_local; build_release; deploy_foreign; verify_sync_sampler_local",
            "check_local; bootstrap_iran; install_sync_sampler_remote; verify_sync_sampler_remote",
            "check_local; install_sync_sampler_remote; deploy_iran; verify_sync_sampler_remote",
            "ensure_runtime_env_file",
            "validate_observability_release_inputs",
        ):
            self.assertIn(expected, script)

    def test_production_release_script_enforces_modern_node_for_frontend_builds(self):
        script = (ROOT / "scripts/production_deploy_online.sh").read_text(encoding="utf-8")

        for expected in (
            "local_node_version_ok",
            "install_local_node_runtime",
            "DEPLOY_NODE_VERSION:-22.12.0",
            "node-v${node_version}-linux-${node_arch}.tar.xz",
            "Frontend build requires Node.js 20.19+ or 22.12+",
        ):
            self.assertIn(expected, script)

    def test_production_docs_and_examples_include_audit_anchor_and_runtime_env_requirements(self):
        hardening = (ROOT / "docs/OBSERVABILITY_PRODUCTION_HARDENING.md").read_text(encoding="utf-8")
        manifest_example = (ROOT / "deploy/production/online.env.example").read_text(encoding="utf-8")

        self.assertIn("## External Audit Integrity Anchor", hardening)
        self.assertIn("audit_durable", hardening)
        self.assertIn("TRUSTED_PROXY_CIDRS", manifest_example)
        self.assertIn("OBSERVABILITY_TELEGRAM_USER_HASH_SALT", manifest_example)

    def test_settings_accept_compose_observability_runtime_keys(self):
        config = (ROOT / "core/config.py").read_text(encoding="utf-8")

        self.assertIn('trading_bot_service: str = "app"', config)
        self.assertIn('trading_bot_metrics_backend: str = "memory"', config)


if __name__ == "__main__":
    unittest.main()
