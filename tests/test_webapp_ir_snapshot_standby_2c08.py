from __future__ import annotations

import re
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_COMPOSE = (
    ROOT / "deploy/production/docker-compose.webapp-ir-snapshot-standby-2c08.yml"
)
PROMOTED_COMPOSE = (
    ROOT / "deploy/production/docker-compose.webapp-ir-promoted-2c08.yml"
)
ENV_EXAMPLE = ROOT / "deploy/production/webapp-ir-snapshot-standby-2c08.env.example"
WRITER_CONFIG_EXAMPLE = ROOT / "deploy/production/production-writer-lease-agent.webapp-ir.json.example"
DARK_NGINX = ROOT / "deploy/production/nginx-webapp-ir-standby-dark-https.conf.template"


class WebappIrSnapshotStandby2c08Tests(unittest.TestCase):
    def test_snapshot_compose_is_database_only_and_network_dark(self) -> None:
        text = SNAPSHOT_COMPOSE.read_text(encoding="utf-8")
        self.assertIn("snapshot_db:", text)
        self.assertIn("network_mode: none", text)
        self.assertIn("pull_policy: never", text)
        self.assertIn("external: true", text)
        self.assertNotRegex(text, re.compile(r"(?m)^  (?:app|sync_worker|migration|redis):"))
        self.assertNotIn("ports:", text)
        self.assertNotIn("build:", text)

    def test_promotion_compose_is_explicit_and_never_starts_direct_sync(self) -> None:
        text = PROMOTED_COMPOSE.read_text(encoding="utf-8")
        self.assertEqual(text.count('profiles: ["promoted"]'), 3)
        self.assertIn('TRADING_BOT_DISABLE_DIRECT_SYNC_PUSH: "1"', text)
        self.assertIn('BACKGROUND_JOBS_ENABLED: "false"', text)
        self.assertIn("WA_IR_CANDIDATE_AUDIT_VOLUME", text)
        self.assertIn("candidate_audit_data:/app/audit_trail", text)
        self.assertIn("127.0.0.1:${WA_IR_APP_LOCAL_PORT:-18000}:8000", text)
        self.assertNotIn("sync_worker", text)
        self.assertNotIn("migration", text)
        self.assertNotIn("build:", text)
        self.assertEqual(text.count("pull_policy: never"), 3)
        self.assertIn("start_period: 5s", text)
        self.assertIn("interval: 3s", text)

    def test_release_and_schema_are_pinned_to_actual_production(self) -> None:
        text = ENV_EXAMPLE.read_text(encoding="utf-8")
        self.assertIn("RELEASE_SHA=2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5", text)
        self.assertIn("EXPECTED_ALEMBIC_REVISION=f2c7d8e9a0b1", text)
        self.assertIn("trading_bot_base:rollback-2c08da14-9ed63dd3e446", text)
        self.assertIn("WA_IR_STANDBY_DATA_ROOT=", text)
        self.assertIn("WA_IR_SNAPSHOT_MAX_AGE_SECONDS=30", text)
        self.assertIn("Object Storage", text)
        self.assertNotRegex(text, re.compile(r"(?:AWS_SECRET|POSTGRES_PASSWORD|JWT_SECRET_KEY)="))

    def test_ir_writer_config_pins_the_short_emergency_term_and_isolated_runtime(self) -> None:
        payload = json.loads(WRITER_CONFIG_EXAMPLE.read_text(encoding="utf-8"))
        self.assertEqual(payload["mode"], "writer")
        self.assertEqual(payload["site"], "webapp_ir")
        self.assertEqual(payload["runtime"]["services"], ["db", "redis", "app"])
        self.assertTrue(payload["runtime"]["compose_file"].endswith("docker-compose.webapp-ir-promoted-2c08.yml"))
        self.assertEqual(payload["witness"]["lease_duration_seconds"], 60)
        self.assertEqual(payload["witness"]["safety_margin_seconds"], 15)
        self.assertEqual(payload["witness"]["renew_interval_seconds"], 10)

    def test_dark_nginx_requires_local_tls_and_has_no_sync_or_upstream(self) -> None:
        text = DARK_NGINX.read_text(encoding="utf-8")
        self.assertIn("ssl_certificate __WA_IR_CERTIFICATE_PATH__", text)
        self.assertIn("ssl_certificate_key __WA_IR_CERTIFICATE_KEY_PATH__", text)
        self.assertIn("return 503", text)
        self.assertNotIn("acme-challenge", text)
        self.assertNotIn("proxy_pass", text)
        self.assertNotIn("/api/sync/receive", text)
        self.assertNotIn("__FOREIGN_PUBLIC_IP__", text)

    def test_transport_examples_require_age_at_the_correct_site(self) -> None:
        fi = (ROOT / "deploy/production/webapp-fi-snapshot-transport.json.example").read_text(encoding="utf-8")
        ir = (ROOT / "deploy/production/webapp-ir-snapshot-transport.json.example").read_text(encoding="utf-8")
        self.assertIn('"age_recipient"', fi)
        self.assertNotIn('"age_identity_file"', fi)
        self.assertIn('"age_identity_file"', ir)
        self.assertNotIn('"age_recipient"', ir)
        self.assertIn('"maximum_snapshot_age_seconds": 30', fi)
        self.assertIn('"maximum_snapshot_age_seconds": 30', ir)


if __name__ == "__main__":
    unittest.main()
