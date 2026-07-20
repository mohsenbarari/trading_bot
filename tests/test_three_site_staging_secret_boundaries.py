from pathlib import Path
import tempfile
import unittest

from scripts.verify_three_site_staging_secret_boundaries import (
    SecretBoundaryError,
    verify_compose,
)


ROOT = Path(__file__).resolve().parents[1]


class ThreeSiteStagingSecretBoundaryTests(unittest.TestCase):
    def test_repository_compose_obeys_closed_secret_allowlist(self):
        result = verify_compose(ROOT / "deploy/staging/docker-compose.three-site.yml")
        self.assertEqual(result["status"], "verified")
        self.assertGreaterEqual(result["service_count"], 35)
        self.assertEqual(result["managed_network_count"], 6)

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

    def test_shared_or_cross_role_control_network_is_rejected(self):
        source = """
services:
  compromised:
    image: test
    networks: [dr_control, writer_witness]
networks:
  dr_control: {}
  writer_witness: {internal: true}
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "compose.yml"
            path.write_text(source, encoding="utf-8")
            with self.assertRaises(SecretBoundaryError) as captured:
                verify_compose(path)
        message = str(captured.exception)
        self.assertIn("shared_dr_control_network_forbidden", message)
        self.assertIn("forbidden_network:writer_witness", message)


if __name__ == "__main__":
    unittest.main()
