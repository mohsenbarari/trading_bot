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
            self.assertIn("deploy/writer-witness/writer-witness-offsite-backup.sh", manifest)
            self.assertIn("deploy/writer-witness/writer-witness-s3-put.py", manifest)
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
        self.assertIn('backup_path" == "-"', restore)

    def test_offsite_s3_backup_keeps_decryption_identity_off_witness(self):
        sender = (ROOT / "deploy/writer-witness/writer-witness-offsite-backup.sh").read_text()
        uploader = (
            ROOT / "deploy/writer-witness/writer-witness-s3-put.py"
        ).read_text()
        configure = (
            ROOT / "scripts/configure_writer_witness_s3_backup.sh"
        ).read_text()
        service = (
            ROOT / "deploy/writer-witness/writer-witness-offsite-backup.service"
        ).read_text()
        timer = (
            ROOT / "deploy/writer-witness/writer-witness-offsite-backup.timer"
        ).read_text()
        self.assertIn("--recipients-file", sender)
        self.assertNotIn("--identity", sender)
        self.assertIn("writer-witness-s3-put", sender)
        self.assertIn("MAX_BACKUP_AGE_SECONDS", sender)
        self.assertIn('"PUT"', uploader)
        self.assertNotIn('connection.request("GET"', uploader)
        self.assertNotIn('connection.request("DELETE"', uploader)
        self.assertIn("private_decryption_key_present\":false", configure)
        self.assertNotIn("object-storage-admin.env", configure)
        self.assertIn("ProtectSystem=strict", service)
        self.assertIn("OnUnitInactiveSec=1h", timer)

    def test_object_storage_provisioning_uses_enforced_explicit_denies(self):
        provision = (
            ROOT / "scripts/provision_writer_witness_object_storage.py"
        ).read_text()
        self.assertIn('"Mode") != "COMPLIANCE"', provision)
        self.assertIn('xml_text(root, "Days") != "90"', provision)
        self.assertIn('"s3:ListBucket"', provision)
        self.assertIn('"s3:GetObject"', provision)
        self.assertIn('"s3:DeleteObject"', provision)
        self.assertNotIn('"NotAction"', provision)
        self.assertIn("verify_uploader_boundary", provision)
        self.assertIn("uploader unexpectedly listed", provision)
        self.assertIn("uploader unexpectedly wrote outside witness prefix", provision)
        self.assertIn("uploader unexpectedly read", provision)
        self.assertIn("uploader unexpectedly deleted", provision)

    def test_authenticated_smoke_is_read_only_and_redacts_client_secret(self):
        smoke = (ROOT / "scripts/smoke_writer_witness_client.py").read_text()
        self.assertIn('STATUS_PATH = "/v1/writer-witness/status"', smoke)
        self.assertIn('"GET"', smoke)
        self.assertNotIn('method="POST"', smoke)
        self.assertNotIn('"secret": secret', smoke)


if __name__ == "__main__":
    unittest.main()
