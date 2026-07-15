import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WriterWitnessDeploymentTests(unittest.TestCase):
    def test_release_builder_emits_minimal_importable_integrity_checked_payload(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-release-") as destination:
            subprocess.run(
                ["bash", str(ROOT / "scripts/build_writer_witness_release.sh"), destination],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            release = Path(destination)
            manifest = json.loads((release / "release-manifest.json").read_text())
            actual = {
                path.relative_to(release).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(release.rglob("*"))
                if path.is_file() and path.name != "release-manifest.json"
            }
            self.assertEqual(actual, manifest)
            self.assertIn("writer_witness_app.py", manifest)
            self.assertIn("deploy/writer-witness/001_initial.sql", manifest)
            self.assertIn("deploy/writer-witness/requirements.lock", manifest)
            self.assertNotIn(".env", "\n".join(manifest))
            self.assertFalse((release / "main.py").exists())
            imported = subprocess.run(
                [
                    "python3",
                    "-c",
                    "import writer_witness_app; print(writer_witness_app.app.title)",
                ],
                cwd=release,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(imported.stdout.strip(), "WebApp Writer Witness")

    def test_systemd_unit_is_single_worker_loopback_and_hardened(self):
        unit = (ROOT / "deploy/writer-witness/writer-witness.service").read_text()
        self.assertIn("User=writer-witness", unit)
        self.assertIn("--host 127.0.0.1 --port 8011 --workers 1", unit)
        self.assertIn("EnvironmentFile=/etc/trading-bot-witness/runtime.env", unit)
        self.assertIn("NoNewPrivileges=true", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertNotIn("0.0.0.0", unit)

    def test_nginx_exposes_only_fixed_private_control_paths(self):
        config = (ROOT / "deploy/writer-witness/nginx.conf.template").read_text()
        self.assertIn("allow __WEBAPP_FI_SOURCE_IP__;", config)
        self.assertIn("allow __WEBAPP_IR_SOURCE_IP__;", config)
        self.assertIn("allow __WITNESS_PUBLIC_IP__;", config)
        self.assertIn("deny all;", config)
        self.assertIn("location = /v1/writer-witness/status", config)
        self.assertIn("location = /v1/writer-witness/transitions", config)
        self.assertIn("client_max_body_size 16k;", config)
        self.assertNotIn("Access-Control-Allow-Origin", config)

    def test_provisioning_keeps_webapp_and_cdn_activation_out_of_scope(self):
        script = (ROOT / "scripts/provision_writer_witness_host.sh").read_text()
        self.assertIn("GRANT SELECT, UPDATE ON webapp_writer_witness_state", script)
        self.assertIn("GRANT SELECT, INSERT ON webapp_writer_witness_receipts", script)
        self.assertIn("ufw allow from \"$WEBAPP_FI_SOURCE_IP\"", script)
        self.assertIn("ufw allow from \"$WEBAPP_IR_SOURCE_IP\"", script)
        self.assertIn('"webapp_flags_changed":false', script)
        self.assertIn('"cdn_changed":false', script)
        self.assertNotIn("WRITER_WITNESS_REQUIRED=true", script)
        self.assertNotIn("arvan", script.lower())
        self.assertIn("PermitRootLogin prohibit-password", script)
        self.assertIn("PasswordAuthentication no", script)
        self.assertIn('WRITER_WITNESS_HARDEN_SSH:-false', script)
        self.assertIn("sha256sum --check SHA256SUMS", script)
        self.assertIn("--no-index --find-links", script)
        self.assertIn("basicConstraints=critical,CA:TRUE,pathlen:0", script)
        self.assertIn("keyUsage=critical,keyCertSign,cRLSign", script)
        self.assertIn("extendedKeyUsage=serverAuth", script)
        self.assertIn("WRITER_WITNESS_ROTATE_TLS:-false", script)
        self.assertIn("requirements.lock", script)

    def test_backup_and_restore_drill_do_not_require_runtime_credentials(self):
        backup = (ROOT / "deploy/writer-witness/writer-witness-backup.sh").read_text()
        restore = (ROOT / "deploy/writer-witness/writer-witness-restore-drill.sh").read_text()
        self.assertIn("runuser -u postgres -- pg_dump", backup)
        self.assertIn("sha256sum", backup)
        self.assertIn("runuser -u postgres -- pg_restore", restore)
        self.assertIn("writer_witness_restore_drill_", restore)
        self.assertIn("trap cleanup EXIT", restore)
        self.assertNotIn("WITNESS_DB_RUNTIME_PASSWORD", backup + restore)

    def test_authenticated_smoke_is_read_only_and_redacts_client_secret(self):
        smoke = (ROOT / "scripts/smoke_writer_witness_client.py").read_text()
        self.assertIn('STATUS_PATH = "/v1/writer-witness/status"', smoke)
        self.assertIn('"GET"', smoke)
        self.assertNotIn('method="POST"', smoke)
        self.assertNotIn('"secret": secret', smoke)


if __name__ == "__main__":
    unittest.main()
