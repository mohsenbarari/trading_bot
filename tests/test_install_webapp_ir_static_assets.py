from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_webapp_ir_static_assets.py"
SPEC = importlib.util.spec_from_file_location("install_webapp_ir_static_assets_test", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


CAMPAIGN = "campaign-12345678"
RELEASE = "a" * 40
REVISION = "f2c7d8e9a0b1"
BUNDLE_ID = "bundle-20260730"


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def private_file(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


def deterministic_archive(path: Path, entries: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w", format=tarfile.USTAR_FORMAT) as archive:
        for name in sorted(entries):
            payload = entries[name]
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o644
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            archive.addfile(info, io.BytesIO(payload))
    path.chmod(0o600)
    return path


@unittest.skipUnless(os.geteuid() == 0, "static extraction is root-only")
class WebappIrStaticAssetInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="wa-ir-static-install-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.inputs = self.root / "inputs"
        self.receipts = self.root / "receipts"
        self.static_parent = self.root / "static-releases"
        self.inputs.mkdir(mode=0o700)
        self.receipts.mkdir(mode=0o700)
        self.static_parent.mkdir(mode=0o755)
        self.application_release_root = self.root / "application-releases" / RELEASE
        self.application = {"release_sha": RELEASE, "expected_alembic_revision": REVISION}
        self.entries = {
            "assets/app.js": b"console.log('fixture');\n",
            "index.html": b"<!doctype html><title>fixture</title>\n",
        }
        self.archive = deterministic_archive(self.inputs / "mini-app-dist.tar", self.entries)
        self.archive_sha256 = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.archive_bytes = self.archive.stat().st_size
        self.controller = Ed25519PrivateKey.generate()
        self.controller_public = base64.b64encode(
            self.controller.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        ).decode("ascii")
        self.static_object = {
            "object_key": "campaigns/fixture/static.age",
            "version_id": "fixture-static-version-1",
            "ciphertext_sha256": hashlib.sha256(b"ciphertext").hexdigest(),
            "ciphertext_bytes": len(b"ciphertext"),
            "plaintext_sha256": self.archive_sha256,
            "plaintext_bytes": self.archive_bytes,
        }
        self.proof = self.make_static_proof()
        self.receive_receipt = private_file(
            self.inputs / "static-receive.json",
            canonical(
                {
                    "schema": MODULE.STATIC_RECEIVE_RECEIPT_SCHEMA,
                    "status": "read_back",
                    "campaign_id": CAMPAIGN,
                    "source_site": "webapp_fi",
                    "destination_site": "webapp_ir",
                    "object": self.static_object,
                    "transport": MODULE.STATIC_TRANSPORT,
                    "age_decryption": MODULE.STATIC_AGE_DECRYPTION,
                }
            ),
        )
        self.bootstrap = SimpleNamespace(
            webapp_fi_controller_authorization_public_key=base64.b64decode(self.controller_public),
        )
        self.stage = SimpleNamespace(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            release_sha=RELEASE,
            bundle_id=BUNDLE_ID,
            receipt_sha256="b" * 64,
        )
        self.staged_provenance = SimpleNamespace(
            application=SimpleNamespace(release_root=self.application_release_root),
            webapp_fi_source_provenance={
                "campaign_id": CAMPAIGN,
                "application": self.application,
                "proofs": {"static_assets_provenance": self.proof},
            }
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_static_proof(self, *, entries: dict[str, bytes] | None = None) -> dict:
        entries = entries if entries is not None else self.entries
        files = [
            {
                "path": path,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
            for path, payload in sorted(entries.items())
        ]
        unsigned = {
            "schema": MODULE.portable.STATIC_ASSET_PROVENANCE_SCHEMA,
            "status": "verified",
            "campaign_id": CAMPAIGN,
            "application": self.application,
            "source_kind": "deterministic_2c08_dist_manifest",
            "artifact": dict(self.static_object),
            "files": files,
            "files_sha256": hashlib.sha256(MODULE.canonical_json_bytes(files)).hexdigest(),
            "controller_public_key_base64": self.controller_public,
        }
        signature = self.controller.sign(
            MODULE.portable.STATIC_ASSET_SIGNATURE_DOMAIN + MODULE.canonical_json_bytes(unsigned)
        )
        return {
            **unsigned,
            "controller_signature": {"algorithm": "ed25519", "signature_base64": base64.b64encode(signature).decode("ascii")},
        }

    def stage_mocks(self) -> tuple[object, object]:
        return (
            mock.patch.object(MODULE.provenance, "load_bootstrap_receive_receipt", return_value=self.bootstrap),
            mock.patch.object(
                MODULE.provenance,
                "verify_staged_provenance",
                return_value=(self.stage, self.staged_provenance),
            ),
        )

    def expected_stage(self) -> dict[str, str]:
        return {
            "source_site": self.stage.source_site,
            "destination_site": self.stage.destination_site,
            "release_sha": self.stage.release_sha,
            "bundle_id": self.stage.bundle_id,
            "receipt_sha256": self.stage.receipt_sha256,
        }

    def install(self, *, receipt_name: str = "static-install.json") -> dict:
        first, second = self.stage_mocks()
        with first, second:
            return MODULE.install_verified_static_assets(
                stage_receipt_path=self.inputs / "stage-receipt.json",
                bootstrap_receipt_path=self.inputs / "bootstrap-receipt.json",
                static_archive=self.archive,
                static_receive_receipt=self.receive_receipt,
                static_release_parent=self.static_parent,
                receipt_path=self.receipts / receipt_name,
                now=datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
            )

    def test_verify_is_local_only_and_install_creates_a_separate_immutable_static_root(self) -> None:
        first, second = self.stage_mocks()
        with first, second:
            plan = MODULE.verify_static_install_inputs(
                stage_receipt_path=self.inputs / "stage-receipt.json",
                bootstrap_receipt_path=self.inputs / "bootstrap-receipt.json",
                static_archive=self.archive,
                static_receive_receipt=self.receive_receipt,
                static_release_parent=self.static_parent,
            )
        self.assertFalse(Path(plan["static_root"]).exists())
        self.assertFalse(plan["object_storage_action"])
        self.assertFalse(plan["age_action"])
        self.assertFalse(plan["ssh_action"])
        self.assertFalse(plan["docker_action"])
        self.assertFalse(plan["service_changed"])

        result = self.install()
        static_root = Path(result["static_root"])
        self.assertEqual("installed", result["status"])
        self.assertNotIn("mini_app_dist", static_root.parts)
        self.assertEqual(b"<!doctype html><title>fixture</title>\n", (static_root / "index.html").read_bytes())
        self.assertEqual(b"console.log('fixture');\n", (static_root / "assets" / "app.js").read_bytes())
        self.assertEqual(0o755, mode(static_root))
        self.assertEqual(0o755, mode(static_root / "assets"))
        self.assertEqual(0o644, mode(static_root / "index.html"))
        self.assertEqual(0o600, mode(self.receipts / "static-install.json"))

        verified = MODULE.verify_installed_static_assets(
            receipt_path=self.receipts / "static-install.json",
            expected_application_release_sha=RELEASE,
            pinned_controller_public_key_base64=self.controller_public,
            expected_stage=self.expected_stage(),
        )
        self.assertEqual("verified", verified["status"])
        self.assertEqual(str(static_root), verified["static_root"])
        self.assertEqual(2, verified["file_count"])

    def test_rejects_wrong_version_readback_before_creating_a_static_root(self) -> None:
        value = json.loads(self.receive_receipt.read_text(encoding="ascii"))
        value["object"]["version_id"] = "wrong-version"
        private_file(self.receive_receipt, canonical(value))
        first, second = self.stage_mocks()
        with first, second:
            with self.assertRaisesRegex(MODULE.StaticAssetInstallError, "does not match the signed static object"):
                MODULE.verify_static_install_inputs(
                    stage_receipt_path=self.inputs / "stage-receipt.json",
                    bootstrap_receipt_path=self.inputs / "bootstrap-receipt.json",
                    static_archive=self.archive,
                    static_receive_receipt=self.receive_receipt,
                    static_release_parent=self.static_parent,
                )
        self.assertEqual([], list(self.static_parent.iterdir()))

    def test_rejects_archive_member_not_in_signed_file_manifest_and_preserves_candidate(self) -> None:
        signed_entries = dict(self.entries)
        self.entries["unexpected.txt"] = b"unexpected"
        deterministic_archive(self.archive, self.entries)
        self.archive_sha256 = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.archive_bytes = self.archive.stat().st_size
        self.static_object["plaintext_sha256"] = self.archive_sha256
        self.static_object["plaintext_bytes"] = self.archive_bytes
        self.proof = self.make_static_proof(entries=signed_entries)
        self.staged_provenance.webapp_fi_source_provenance["proofs"]["static_assets_provenance"] = self.proof
        value = json.loads(self.receive_receipt.read_text(encoding="ascii"))
        value["object"] = self.static_object
        private_file(self.receive_receipt, canonical(value))

        with self.assertRaisesRegex(MODULE.StaticAssetInstallError, "contains files absent"):
            self.install(receipt_name="unexpected.json")

        campaign = self.static_parent / CAMPAIGN
        candidates = list((campaign / RELEASE).glob(".incoming-static-*"))
        self.assertEqual(1, len(candidates))
        self.assertEqual(0o700, mode(candidates[0]))
        self.assertFalse((self.receipts / "unexpected.json").exists())

    def test_failed_candidate_does_not_block_a_new_verified_retry(self) -> None:
        signed_entries = dict(self.entries)
        invalid_entries = {**signed_entries, "unexpected.txt": b"unexpected"}
        deterministic_archive(self.archive, invalid_entries)
        self.archive_sha256 = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.archive_bytes = self.archive.stat().st_size
        self.static_object["plaintext_sha256"] = self.archive_sha256
        self.static_object["plaintext_bytes"] = self.archive_bytes
        self.proof = self.make_static_proof(entries=signed_entries)
        self.staged_provenance.webapp_fi_source_provenance["proofs"]["static_assets_provenance"] = self.proof
        receive = json.loads(self.receive_receipt.read_text(encoding="ascii"))
        receive["object"] = self.static_object
        private_file(self.receive_receipt, canonical(receive))
        with self.assertRaisesRegex(MODULE.StaticAssetInstallError, "contains files absent"):
            self.install(receipt_name="failed.json")

        retry_entries = {
            "assets/app.js": b"console.log('retry');\n",
            "index.html": b"<!doctype html><title>retry</title>\n",
        }
        deterministic_archive(self.archive, retry_entries)
        self.archive_sha256 = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.archive_bytes = self.archive.stat().st_size
        self.static_object["version_id"] = "fixture-static-version-2"
        self.static_object["plaintext_sha256"] = self.archive_sha256
        self.static_object["plaintext_bytes"] = self.archive_bytes
        self.proof = self.make_static_proof(entries=retry_entries)
        self.staged_provenance.webapp_fi_source_provenance["proofs"]["static_assets_provenance"] = self.proof
        receive = json.loads(self.receive_receipt.read_text(encoding="ascii"))
        receive["object"] = self.static_object
        private_file(self.receive_receipt, canonical(receive))

        result = self.install(receipt_name="retry.json")
        self.assertEqual("installed", result["status"])
        candidates = list((self.static_parent / CAMPAIGN / RELEASE).glob(".incoming-static-*"))
        self.assertEqual(1, len(candidates))
        self.assertTrue((self.receipts / "retry.json").is_file())

    def test_rejects_a_signed_static_proof_without_index_before_creating_a_root(self) -> None:
        entries = {"assets/app.js": b"console.log('no-index');\n"}
        deterministic_archive(self.archive, entries)
        self.archive_sha256 = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.archive_bytes = self.archive.stat().st_size
        self.static_object["plaintext_sha256"] = self.archive_sha256
        self.static_object["plaintext_bytes"] = self.archive_bytes
        self.proof = self.make_static_proof(entries=entries)
        self.staged_provenance.webapp_fi_source_provenance["proofs"]["static_assets_provenance"] = self.proof
        receive = json.loads(self.receive_receipt.read_text(encoding="ascii"))
        receive["object"] = self.static_object
        private_file(self.receive_receipt, canonical(receive))

        with self.assertRaisesRegex(MODULE.StaticAssetInstallError, "must include index.html"):
            self.install(receipt_name="no-index.json")
        self.assertEqual([], list(self.static_parent.iterdir()))

    def test_rejects_a_static_parent_inside_the_staged_git_application_release(self) -> None:
        nested_parent = self.application_release_root / "static-assets"
        nested_parent.mkdir(parents=True, mode=0o755)
        nested_parent.chmod(0o755)
        self.application_release_root.chmod(0o755)
        self.application_release_root.parent.chmod(0o755)
        first, second = self.stage_mocks()
        with first, second:
            with self.assertRaisesRegex(MODULE.StaticAssetInstallError, "outside the staged Git application release"):
                MODULE.verify_static_install_inputs(
                    stage_receipt_path=self.inputs / "stage-receipt.json",
                    bootstrap_receipt_path=self.inputs / "bootstrap-receipt.json",
                    static_archive=self.archive,
                    static_receive_receipt=self.receive_receipt,
                    static_release_parent=nested_parent,
                )
        self.assertFalse((nested_parent / CAMPAIGN).exists())

    def test_rejects_a_static_parent_below_a_nonsticky_writable_ancestor(self) -> None:
        unsafe = self.root / "unsafe"
        unsafe.mkdir(mode=0o777)
        unsafe.chmod(0o777)
        nested_parent = unsafe / "static-releases"
        nested_parent.mkdir(mode=0o755)
        nested_parent.chmod(0o755)
        first, second = self.stage_mocks()
        with first, second:
            with self.assertRaisesRegex(MODULE.StaticAssetInstallError, "unsafe ancestor"):
                MODULE.verify_static_install_inputs(
                    stage_receipt_path=self.inputs / "stage-receipt.json",
                    bootstrap_receipt_path=self.inputs / "bootstrap-receipt.json",
                    static_archive=self.archive,
                    static_receive_receipt=self.receive_receipt,
                    static_release_parent=nested_parent,
                )

    def test_recovers_an_exact_root_when_only_receipt_creation_failed(self) -> None:
        original_create_only = MODULE._create_only_json

        def fail_only_receipt(path, value, *, field):
            if field == "static install receipt":
                raise MODULE.StaticAssetInstallError("simulated receipt failure")
            return original_create_only(path, value, field=field)

        with mock.patch.object(MODULE, "_create_only_json", side_effect=fail_only_receipt):
            with self.assertRaisesRegex(MODULE.StaticAssetInstallError, "simulated receipt failure"):
                self.install()
        static_root = self.static_parent / CAMPAIGN / RELEASE / self.proof["files_sha256"]
        self.assertTrue(static_root.is_dir())
        self.assertFalse((self.receipts / "static-install.json").exists())

        result = self.install()
        self.assertEqual("recovered", result["status"])
        self.assertTrue((self.receipts / "static-install.json").is_file())

    def test_existing_static_root_without_its_create_only_marker_cannot_recover(self) -> None:
        result = self.install()
        receipt_path = Path(result["receipt_path"])
        receipt = json.loads(receipt_path.read_text(encoding="ascii"))
        marker = Path(receipt["install_marker"]["path"])
        receipt_path.unlink()
        marker.unlink()

        with self.assertRaisesRegex(MODULE.StaticAssetInstallError, "has no exact staged-object install marker"):
            self.install()

    def test_installed_verifier_rejects_a_static_receipt_for_another_stage(self) -> None:
        self.install()
        stale_stage = self.expected_stage()
        stale_stage["receipt_sha256"] = "c" * 64
        with self.assertRaisesRegex(MODULE.StaticAssetInstallError, "does not match the installed release provenance"):
            MODULE.verify_installed_static_assets(
                receipt_path=self.receipts / "static-install.json",
                expected_application_release_sha=RELEASE,
                pinned_controller_public_key_base64=self.controller_public,
                expected_stage=stale_stage,
            )

    def test_rejects_mutated_installed_static_file(self) -> None:
        result = self.install()
        static_root = Path(result["static_root"])
        (static_root / "index.html").write_text("mutated\n", encoding="ascii")
        (static_root / "index.html").chmod(0o644)

        with self.assertRaisesRegex(MODULE.StaticAssetInstallError, "does not match the signed proof"):
            MODULE.verify_installed_static_assets(
                receipt_path=self.receipts / "static-install.json",
                expected_application_release_sha=RELEASE,
                pinned_controller_public_key_base64=self.controller_public,
                expected_stage=self.expected_stage(),
            )

    def test_installed_verifier_rejects_a_listener_bound_to_another_application_release(self) -> None:
        self.install()
        with self.assertRaisesRegex(MODULE.StaticAssetInstallError, "does not match the listener release"):
            MODULE.verify_installed_static_assets(
                receipt_path=self.receipts / "static-install.json",
                expected_application_release_sha="c" * 40,
                pinned_controller_public_key_base64=self.controller_public,
                expected_stage=self.expected_stage(),
            )

    def test_implementation_has_no_transport_or_runtime_client(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in ("import boto3", "import subprocess", "import urllib", "import requests", "ssh ", "scp "):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
