import tempfile
import unittest
from pathlib import Path

from scripts.render_release_artifacts import (
    derive_release_values,
    healthcheck_bundle,
    release_values_bundle,
    render_hosts_block,
    render_nginx_config,
    write_artifacts,
)


class RenderReleaseArtifactsTests(unittest.TestCase):
    def write_file(self, name: str, content: str) -> str:
        if not hasattr(self, "_tmpdir"):
            self._tmpdir = tempfile.TemporaryDirectory()
            self.addCleanup(self._tmpdir.cleanup)
        path = Path(self._tmpdir.name) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(path)

    def manifest_content(self, extra: str = "") -> str:
        base = "\n".join(
            [
                "FOREIGN_PUBLIC_IP=65.109.216.187",
                "FOREIGN_PUBLIC_DOMAIN=coin.362514.ir",
                "IRAN_PUBLIC_IP=87.107.3.22",
                "IRAN_PUBLIC_DOMAIN=coin.gold-trade.ir",
                "IRAN_APP_DOMAIN=coin.gold-trade.ir",
                "IRAN_PROJECT_DIR=/srv/trading-bot/current",
                "IRAN_CERTBOT_EMAIL=ops@example.ir",
            ]
        )
        return f"{base}\n{extra}" if extra else base

    def test_derives_defaults_and_renders_release_artifacts(self):
        manifest = self.write_file("online.env", self.manifest_content())
        template = self.write_file("nginx.template", "server_name __SERVER_NAME__;\nroot __APP_ROOT__;\n")

        values = derive_release_values(manifest)

        self.assertEqual(values["IRAN_SERVER_URL"], "https://coin.gold-trade.ir")
        self.assertEqual(values["IRAN_HEALTHCHECK_URL"], "https://coin.gold-trade.ir/api/config")
        self.assertEqual(values["NGINX_APP_ROOT"], "/srv/trading-bot/current/mini_app_dist")

        hosts = render_hosts_block(values)
        self.assertIn("65.109.216.187 coin.362514.ir", hosts)
        self.assertIn("87.107.3.22 coin.gold-trade.ir", hosts)

        nginx = render_nginx_config(values, template)
        self.assertIn("server_name coin.gold-trade.ir;", nginx)
        self.assertIn("root /srv/trading-bot/current/mini_app_dist;", nginx)

        health = healthcheck_bundle(values)
        release_values = release_values_bundle(values)
        self.assertEqual(health["iran_local_api_url"], "http://127.0.0.1:8000/api/config")
        self.assertEqual(release_values["certbot_domain"], "coin.gold-trade.ir")

    def test_writes_all_artifact_files(self):
        manifest = self.write_file("online.env", self.manifest_content())
        template = self.write_file("nginx.template", "server_name __SERVER_NAME__;\n")
        output_dir = str(Path(self._tmpdir.name) / "artifacts")

        values = derive_release_values(manifest)
        paths = write_artifacts(values, template, output_dir)

        self.assertEqual(set(paths), {"hosts", "nginx", "health", "values"})
        for path in paths.values():
            self.assertTrue(Path(path).exists())
        self.assertIn("coin.gold-trade.ir", Path(paths["health"]).read_text(encoding="utf-8"))

    def test_rejects_contradictory_url_and_domain(self):
        manifest = self.write_file(
            "bad.env",
            self.manifest_content("IRAN_SERVER_URL=https://wrong.example\nIRAN_SERVER_DOMAIN=coin.gold-trade.ir"),
        )

        with self.assertRaisesRegex(ValueError, "IRAN_SERVER_URL host"):
            derive_release_values(manifest)

    def test_rejects_identical_foreign_and_iran_ips(self):
        manifest = self.write_file(
            "bad-ip.env",
            self.manifest_content("IRAN_PUBLIC_IP=65.109.216.187"),
        )

        with self.assertRaisesRegex(ValueError, "must not be identical"):
            derive_release_values(manifest)


if __name__ == "__main__":
    unittest.main()
