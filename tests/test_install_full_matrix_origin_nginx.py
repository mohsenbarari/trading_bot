from pathlib import Path
import os
import tempfile
import unittest

from scripts.install_full_matrix_origin_nginx import FullMatrixNginxError, render


class FullMatrixOriginNginxTests(unittest.TestCase):
    def _inputs(self, root: Path) -> dict:
        frontend = root / "dist"
        frontend.mkdir()
        cert = root / "fullchain.pem"
        key = root / "privkey.pem"
        auth = root / "htpasswd"
        cert.write_text("certificate")
        key.write_text("private")
        auth.write_text("operator:hash")
        os.chmod(key, 0o600)
        os.chmod(auth, 0o600)
        return {
            "host": "app.gold-trading.ir",
            "port": 8212,
            "frontend_root": frontend,
            "certificate": cert,
            "certificate_key": key,
            "basic_auth_file": auth,
        }

    def test_render_is_secretless_and_production_isolated(self):
        with tempfile.TemporaryDirectory() as raw:
            rendered = render(**self._inputs(Path(raw)))
        self.assertIn("server_name app.gold-trading.ir;", rendered)
        self.assertIn("127.0.0.1:8212", rendered)
        self.assertIn("location = /api/auth/dev-login", rendered)
        self.assertNotIn("X-DEV-API-KEY", rendered)
        self.assertNotIn("coin.gold-trade.ir", rendered)
        self.assertNotRegex(rendered, r"__[A-Z0-9_]+__")

    def test_rejects_unapproved_host_and_port(self):
        with tempfile.TemporaryDirectory() as raw:
            values = self._inputs(Path(raw))
            values["host"] = "coin.gold-trade.ir"
            with self.assertRaises(FullMatrixNginxError):
                render(**values)
            values["host"] = "app.gold-trading.ir"
            values["port"] = 8000
            with self.assertRaises(FullMatrixNginxError):
                render(**values)


if __name__ == "__main__":
    unittest.main()
