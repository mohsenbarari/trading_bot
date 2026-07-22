from pathlib import Path
import copy
import tempfile
import unittest

import yaml

from scripts.verify_three_site_staging_secret_boundaries import (
    SecretBoundaryError,
    verify_compose,
)


ROOT = Path(__file__).resolve().parents[1]


class ThreeSiteStagingSecretBoundaryTests(unittest.TestCase):
    def test_repository_compose_obeys_closed_secret_allowlist(self):
        result = verify_compose(ROOT / "deploy/staging/docker-compose.three-site.yml")
        self.assertEqual(result["status"], "verified")
        self.assertGreaterEqual(result["service_count"], 37)
        self.assertEqual(result["managed_network_count"], 11)

    def test_global_env_file_and_cross_role_secret_are_rejected(self):
        source = """
services:
  compromised:
    image: test
    env_file: all.env
    environment:
      BOT_TOKEN: ${BOT_TOKEN}
      DATABASE_URL: postgresql://x:${WEBAPP_FI_CONTROL_DB_PASSWORD}@db/x
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "compose.yml"
            path.write_text(source, encoding="utf-8")
            with self.assertRaises(SecretBoundaryError):
                verify_compose(path)

    def test_private_provider_and_api_jwt_secrets_cannot_reach_projection_worker(self):
        source = """
services:
  webapp_fi_dr_projection:
    image: test
    environment:
      JWT_SECRET_KEY: ${WEBAPP_JWT_SECRET_KEY}
      WEB_PUSH_VAPID_PRIVATE_KEY: ${WEB_PUSH_VAPID_PRIVATE_KEY}
      SMSIR_API_KEY: ${SMSIR_API_KEY}
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "compose.yml"
            path.write_text(source, encoding="utf-8")
            with self.assertRaises(SecretBoundaryError) as captured:
                verify_compose(path)
        message = str(captured.exception)
        self.assertIn("WEBAPP_JWT_SECRET_KEY", message)
        self.assertIn("WEB_PUSH_VAPID_PRIVATE_KEY", message)
        self.assertIn("SMSIR_API_KEY", message)

    def test_projection_credential_cannot_reach_receiver_or_delivery(self):
        source = """
services:
  webapp_fi_dr_receiver:
    image: test
    environment:
      DATABASE_URL: postgresql://x:${WEBAPP_FI_PROJECTION_DB_PASSWORD}@db/x
  bot_fi_dr_delivery:
    image: test
    environment:
      DATABASE_URL: postgresql://x:${BOT_FI_PROJECTION_DB_PASSWORD}@db/x
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "compose.yml"
            path.write_text(source, encoding="utf-8")
            with self.assertRaises(SecretBoundaryError) as captured:
                verify_compose(path)
        message = str(captured.exception)
        self.assertIn("webapp_fi_dr_receiver:forbidden:WEBAPP_FI_PROJECTION_DB_PASSWORD", message)
        self.assertIn("bot_fi_dr_delivery:forbidden:BOT_FI_PROJECTION_DB_PASSWORD", message)

    def test_shared_or_cross_role_control_network_is_rejected(self):
        source = """
services:
  compromised:
    image: test
    networks: [dr_control, writer_witness_egress]
networks:
  dr_control: {}
  writer_witness_egress: {}
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "compose.yml"
            path.write_text(source, encoding="utf-8")
            with self.assertRaises(SecretBoundaryError) as captured:
                verify_compose(path)
        message = str(captured.exception)
        self.assertIn("shared_dr_control_network_forbidden", message)
        self.assertIn("forbidden_network:writer_witness_egress", message)

    def test_public_api_cannot_receive_control_capability_even_with_inline_values(self):
        source = """
services:
  webapp_fi_api:
    image: test
    environment:
      DR_CONTROL_DATABASE_URL: postgresql://control:literal@db/app
      WRITER_WITNESS_CLIENT_SECRET: inline-secret-value
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "compose.yml"
            path.write_text(source, encoding="utf-8")
            with self.assertRaises(SecretBoundaryError) as captured:
                verify_compose(path)
        message = str(captured.exception)
        self.assertIn("writer_control_environment_forbidden:DR_CONTROL_DATABASE_URL", message)
        self.assertIn("writer_control_environment_forbidden:WRITER_WITNESS_CLIENT_SECRET", message)

    def test_writer_control_agent_cannot_expose_port_or_join_egress(self):
        source = """
services:
  webapp_fi_writer_control:
    image: test
    ports: ["8000:8000"]
    networks: [webapp_fi_egress]
networks:
  webapp_fi_egress: {}
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "compose.yml"
            path.write_text(source, encoding="utf-8")
            with self.assertRaises(SecretBoundaryError) as captured:
                verify_compose(path)
        message = str(captured.exception)
        self.assertIn("inbound_surface_forbidden", message)
        self.assertIn("public_egress_forbidden", message)

    def test_role_service_requires_one_exact_host_profile(self):
        source = """
services:
  bot_fi_api:
    image: test
    profiles: [webapp-fi]
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "compose.yml"
            path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(
                SecretBoundaryError, "role_profile_missing_or_mixed:bot-fi"
            ):
                verify_compose(path)

    def test_cross_host_clients_require_dedicated_egress_and_tls_is_inventory_bound(self):
        canonical = yaml.safe_load(
            (ROOT / "deploy/staging/docker-compose.three-site.yml").read_text()
        )
        without_egress = copy.deepcopy(canonical)
        without_egress["services"]["bot_fi_dr_delivery"]["networks"] = ["bot_fi"]
        unsafe_tls = copy.deepcopy(canonical)
        unsafe_tls["services"]["webapp_ir_dr_tls"]["ports"] = ["0.0.0.0:8443:443"]
        missing_peer_map = copy.deepcopy(canonical)
        missing_peer_map["services"]["webapp_fi_writer_control"].pop("extra_hosts")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "compose.yml"
            path.write_text(yaml.safe_dump(without_egress), encoding="utf-8")
            with self.assertRaisesRegex(
                SecretBoundaryError, "bot_fi_dr_delivery:missing_network:bot_fi_dr_egress"
            ):
                verify_compose(path)
            path.write_text(yaml.safe_dump(unsafe_tls), encoding="utf-8")
            with self.assertRaisesRegex(
                SecretBoundaryError, "fixed_inventory_bound_port_missing"
            ):
                verify_compose(path)
            path.write_text(yaml.safe_dump(missing_peer_map), encoding="utf-8")
            with self.assertRaisesRegex(
                SecretBoundaryError, "signed_peer_host_mapping_missing"
            ):
                verify_compose(path)


if __name__ == "__main__":
    unittest.main()
