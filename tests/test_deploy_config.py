import tempfile
import unittest
from pathlib import Path

from scripts.deploy_config import resolve_deploy_settings


class DeployConfigTests(unittest.TestCase):
    def write_manifest(self, content: str) -> str:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        path = Path(tmpdir.name) / "online.env"
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_resolve_deploy_settings_prefers_manifest_values(self):
        manifest = self.write_manifest(
            "\n".join(
                [
                    "IRAN_HOST=203.0.113.50",
                    "IRAN_SSH_USER=deploy",
                    "IRAN_SSH_PORT=2202",
                    "IRAN_PROJECT_DIR=/srv/custom/current",
                    "IRAN_POSTGRES_SHARED_BUFFERS=8GB",
                    "REDIS_APPENDONLY=yes",
                ]
            )
        )

        resolved = resolve_deploy_settings(manifest_path=manifest, environ={})

        self.assertEqual(resolved["IRAN_HOST"], "203.0.113.50")
        self.assertEqual(resolved["IRAN_SSH_USER"], "deploy")
        self.assertEqual(resolved["IRAN_SSH_PORT"], "2202")
        self.assertEqual(resolved["IRAN_PROJECT_DIR"], "/srv/custom/current")
        self.assertEqual(resolved["IRAN_DIR"], "/srv/custom/current")
        self.assertEqual(resolved["IRAN_SSH_TARGET"], "deploy@203.0.113.50")
        self.assertEqual(resolved["IRAN_POSTGRES_SHARED_BUFFERS"], "8GB")
        self.assertEqual(resolved["REDIS_APPENDONLY"], "yes")

    def test_resolve_deploy_settings_prefers_environment_over_manifest(self):
        manifest = self.write_manifest(
            "\n".join(
                [
                    "IRAN_HOST=203.0.113.50",
                    "IRAN_SSH_USER=deploy",
                    "IRAN_PROJECT_DIR=/srv/custom/current",
                ]
            )
        )

        resolved = resolve_deploy_settings(
            manifest_path=manifest,
            environ={
                "IRAN_HOST": "198.51.100.70",
                "IRAN_SSH_USER": "root",
                "IRAN_PROJECT_DIR": "/srv/override/current",
            },
        )

        self.assertEqual(resolved["IRAN_HOST"], "198.51.100.70")
        self.assertEqual(resolved["IRAN_SSH_USER"], "root")
        self.assertEqual(resolved["IRAN_PROJECT_DIR"], "/srv/override/current")
        self.assertEqual(resolved["IRAN_SSH_TARGET"], "root@198.51.100.70")

    def test_resolve_deploy_settings_falls_back_to_defaults(self):
        resolved = resolve_deploy_settings(manifest_path="/nonexistent/online.env", environ={})

        self.assertEqual(resolved["IRAN_HOST"], "65.109.220.59")
        self.assertEqual(resolved["IRAN_SSH_USER"], "root")
        self.assertEqual(resolved["IRAN_SSH_PORT"], "37067")
        self.assertEqual(resolved["IRAN_PROJECT_DIR"], "/srv/trading-bot/current")
        self.assertEqual(resolved["IRAN_SSH_TARGET"], "root@65.109.220.59")


if __name__ == "__main__":
    unittest.main()
