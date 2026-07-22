from __future__ import annotations

import io
import base64
import json
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.wa_ir_object_storage_preflight_agent import (
    AgentError,
    SCHEMA,
    _safe_tar_members,
    install_role_materials,
    load_file_transfer_manifest,
    load_manifest,
    run_preflight,
    upload_evidence,
)


class WaIrObjectStoragePreflightAgentTests(unittest.TestCase):
    def _manifest(self, directory: Path) -> Path:
        payload = {
            "schema": SCHEMA,
            "role": "webapp-ir",
            "release_sha": "46b1d672548fdccb704c396262854c7149cbec82",
            "secure_materials_dir": "/root/secure-envs/trading-bot/three-site-staging-46b1d672",
            "release_bundle": {
                "url": "https://s3.ir-thr-at1.arvanstorage.ir/private/release",
                "sha256": "a" * 64,
                "bytes": 12,
            },
            "role_materials": {
                "url": "https://s3.ir-thr-at1.arvanstorage.ir/private/materials",
                "sha256": "b" * 64,
                "bytes": 12,
            },
            "preflight_output": "/root/secure-envs/trading-bot/three-site-staging-46b1d672/webapp-ir-fresh-preflight.json",
            "age_identity": "/root/secure-envs/trading-bot/wa-ir-object-storage-age-identity.txt",
        }
        path = directory / "manifest.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_manifest_is_strict_and_bound_to_wa_ir_paths(self):
        with tempfile.TemporaryDirectory() as raw:
            manifest = load_manifest(self._manifest(Path(raw)))
            self.assertEqual(manifest["role"], "webapp-ir")

            bad = json.loads(Path(self._manifest(Path(raw))).read_text())
            bad["preflight_output"] = "/tmp/webapp-ir-fresh-preflight.json"
            bad_path = Path(raw) / "bad.json"
            bad_path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaisesRegex(AgentError, "preflight_output"):
                load_manifest(bad_path)

            bad["preflight_output"] = (
                "/root/secure-envs/trading-bot/three-site-staging-46b1d672/"
                "webapp-ir-fresh-preflight.json"
            )
            bad["age_identity"] = (
                "/root/secure-envs/trading-bot/three-site-staging-46b1d672/other.key"
            )
            bad_path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaisesRegex(AgentError, "pinned WA-IR identity"):
                load_manifest(bad_path)

    def test_manifest_rejects_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "manifest.json"
            path.write_text('{"schema":"x","schema":"y"}', encoding="utf-8")
            with self.assertRaisesRegex(AgentError, "strict JSON"):
                load_manifest(path)

    def test_artifact_urls_are_pinned_to_arvan_object_storage(self):
        with tempfile.TemporaryDirectory() as raw:
            path = self._manifest(Path(raw))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["release_bundle"]["url"] = "https://example.com/private/release"
            path.write_text(json.dumps(payload), encoding="utf-8")
            manifest = load_manifest(path)
            from scripts.wa_ir_object_storage_preflight_agent import _artifact

            with self.assertRaisesRegex(AgentError, "approved Arvan"):
                _artifact(manifest["release_bundle"], label="release bundle")

    def test_role_materials_tar_rejects_path_traversal_and_links(self):
        with tempfile.TemporaryDirectory() as raw:
            traversal = Path(raw) / "bad.tar"
            with tarfile.open(traversal, "w") as archive:
                info = tarfile.TarInfo("../escape")
                data = b"unsafe"
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            with tarfile.open(traversal) as archive:
                with self.assertRaisesRegex(AgentError, "unsafe member"):
                    _safe_tar_members(archive)

            symlink = Path(raw) / "link.tar"
            with tarfile.open(symlink, "w") as archive:
                info = tarfile.TarInfo("roles/link")
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                archive.addfile(info)
            with tarfile.open(symlink) as archive:
                with self.assertRaisesRegex(AgentError, "unsafe member"):
                    _safe_tar_members(archive)

    def test_role_materials_install_runtime_secrets_with_exact_modes(self):
        from scripts.publish_wa_ir_object_storage_preflight import (
            REQUIRED_ROLE_MATERIALS,
            build_role_materials,
        )

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source"
            for relative, mode in REQUIRED_ROLE_MATERIALS:
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"material:{relative}\n", encoding="utf-8")
                path.chmod(mode)
            archive = root / "materials.tar"
            build_role_materials(source, archive)
            secure = root / "secure"
            runtime = root / "runtime-secrets"
            with patch(
                "scripts.wa_ir_object_storage_preflight_agent.RUNTIME_SECRET_ROOT",
                runtime,
            ):
                install_role_materials(archive, secure_dir=secure)

            self.assertEqual(
                stat.S_IMODE((runtime / "staging-dr-ca.crt").stat().st_mode),
                0o644,
            )
            self.assertEqual(
                stat.S_IMODE((runtime / "webapp-ir-dr.key").stat().st_mode),
                0o600,
            )
            self.assertEqual(
                stat.S_IMODE((runtime / "staging-dr-blob-s3.json").stat().st_mode),
                0o600,
            )

    def test_role_materials_reject_a_preexisting_symlink_child(self):
        from scripts.publish_wa_ir_object_storage_preflight import (
            REQUIRED_ROLE_MATERIALS,
            build_role_materials,
        )

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source"
            for relative, mode in REQUIRED_ROLE_MATERIALS:
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"material:{relative}\n", encoding="utf-8")
                path.chmod(mode)
            archive = root / "materials.tar"
            build_role_materials(source, archive)
            secure = root / "secure"
            secure.mkdir(mode=0o700)
            (secure / "roles").symlink_to(root)
            with self.assertRaisesRegex(AgentError, "child directory is unsafe"):
                install_role_materials(archive, secure_dir=secure)

    def test_file_transfer_is_encrypted_and_campaign_path_bound(self):
        payload = {
            "schema": "three-site-wa-ir-object-storage-file-v1",
            "role": "webapp-ir",
            "campaign_tag": "wwm_0123456789ab",
            "destination": "/run/writer-witness-matrix/wwm_0123456789ab/client.env",
            "mode": 0o600,
            "artifact": {
                "url": "https://s3.ir-thr-at1.arvanstorage.ir/private/client.age?sig=x",
                "sha256": "a" * 64,
                "bytes": 100,
                "encrypted": True,
                "ciphertext_sha256": "b" * 64,
                "ciphertext_bytes": 300,
            },
        }
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()
        self.assertEqual(load_file_transfer_manifest(encoded)["campaign_tag"], payload["campaign_tag"])
        payload["destination"] = "/root/.ssh/authorized_keys"
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()
        with self.assertRaisesRegex(AgentError, "destination"):
            load_file_transfer_manifest(encoded)

        payload["destination"] = "/run/writer-witness-matrix/wwm_0123456789ab/client.env"
        payload["artifact"]["encrypted"] = False
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()
        with self.assertRaisesRegex(AgentError, "must be age encrypted"):
            load_file_transfer_manifest(encoded)

    def test_preflight_command_is_fixed_to_webapp_ir_and_signed_materials(self):
        completed = Mock(
            returncode=0,
            stdout=json.dumps(
                {
                    "status": "verified",
                    "role": "webapp-ir",
                    "stage": "fresh-preflight",
                    "release_sha": "46b1d672548fdccb704c396262854c7149cbec82",
                }
            ),
            stderr="",
        )
        with patch(
            "scripts.wa_ir_object_storage_preflight_agent.subprocess.run",
            return_value=completed,
        ) as run:
            result = run_preflight(
                release_dir=Path("/srv/trading-bot-three-site/releases/46b"),
                secure_dir=Path("/root/secure-envs/trading-bot/three-site-staging-46b1d672"),
                output=Path("/root/secure-envs/trading-bot/three-site-staging-46b1d672/webapp-ir-fresh-preflight.json"),
            )
        command = run.call_args.args[0]
        self.assertIn("--role", command)
        self.assertIn("webapp-ir", command)
        self.assertIn("--stage", command)
        self.assertIn("fresh-preflight", command)
        self.assertIn("webapp-ir.compose.yml", " ".join(command))
        self.assertEqual(result["status"], "verified")

    def test_evidence_upload_uses_presigned_request_and_checks_status(self):
        with tempfile.TemporaryDirectory() as raw:
            evidence = Path(raw) / "evidence.json"
            evidence.write_text('{"status":"verified"}', encoding="utf-8")
            response = Mock()
            response.status = 200
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=None)
            response.read = Mock(return_value=b"")
            upload = {
                "url": "https://s3.ir-thr-at1.arvanstorage.ir/private/evidence",
                "method": "PUT",
                "headers": {"content-type": "application/json"},
                "expected_status": [200],
            }
            with patch(
                "scripts.wa_ir_object_storage_preflight_agent.urllib.request.urlopen",
                return_value=response,
            ) as urlopen:
                result = upload_evidence(upload, evidence)
            request = urlopen.call_args.args[0]
            self.assertEqual(request.get_method(), "PUT")
            self.assertEqual(result["status"], "uploaded")
            self.assertEqual(result["bytes"], evidence.stat().st_size)

    def test_evidence_upload_rejects_non_arvan_urls_and_auth_headers(self):
        with tempfile.TemporaryDirectory() as raw:
            evidence = Path(raw) / "evidence.json"
            evidence.write_text('{"status":"verified"}', encoding="utf-8")
            upload = {
                "url": "https://example.com/private/evidence",
                "method": "PUT",
                "headers": {},
                "expected_status": [200],
            }
            with self.assertRaisesRegex(AgentError, "approved Arvan"):
                upload_evidence(upload, evidence)

            upload["url"] = "https://s3.ir-thr-at1.arvanstorage.ir/private/evidence"
            upload["headers"] = {"Authorization": "must-not-be-persisted"}
            with self.assertRaisesRegex(AgentError, "forbidden credentials"):
                upload_evidence(upload, evidence)


if __name__ == "__main__":
    unittest.main()
