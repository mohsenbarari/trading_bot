from pathlib import Path
import os
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from scripts.rotate_staging_nginx_dev_key import rotate


class RotateStagingNginxDevKeyTests(unittest.TestCase):
    def test_missing_env_disables_all_staging_copies_without_printing_key(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for name in ("active", "backup"):
                path = root / name
                path.write_text(
                    "server_name staging.gold-trade.ir;\n"
                    "  proxy_set_header X-DEV-API-KEY \"exposed\";\n"
                )
                os.chmod(path, 0o600)
            with patch(
                "scripts.rotate_staging_nginx_dev_key.subprocess.run",
                return_value=SimpleNamespace(returncode=0),
            ):
                result = rotate(nginx_root=root, env_paths=[], apply=True)
            self.assertTrue(result["dev_login_disabled"])
            self.assertEqual(result["nginx_files"], 2)
            for path in root.iterdir():
                value = path.read_text()
                self.assertNotIn("exposed", value)
                self.assertNotIn("X-DEV-API-KEY", value)
                self.assertIn("dev-login disabled", value)

    def test_env_and_nginx_receive_same_fresh_value(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            env = root / "runtime.env"
            env.write_text("DEV_API_KEY=old\n")
            config = root / "active"
            config.write_text(
                "server_name staging.gold-trade.ir;\n"
                "proxy_set_header X-DEV-API-KEY \"old\";\n"
            )
            os.chmod(env, 0o600)
            os.chmod(config, 0o600)
            with (
                patch(
                    "scripts.rotate_staging_nginx_dev_key.secrets.token_urlsafe",
                    return_value="fresh",
                ),
                patch(
                    "scripts.rotate_staging_nginx_dev_key.subprocess.run",
                    return_value=SimpleNamespace(returncode=0),
                ),
            ):
                result = rotate(nginx_root=root, env_paths=[env], apply=True)
            self.assertFalse(result["dev_login_disabled"])
            self.assertIn("DEV_API_KEY=fresh", env.read_text())
            self.assertIn('X-DEV-API-KEY "fresh"', config.read_text())


if __name__ == "__main__":
    unittest.main()
