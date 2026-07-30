import base64
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare = _load_module("prepare_webapp_fi_source_adoption_test", ROOT / "scripts" / "prepare_webapp_fi_source_adoption.py")
install = _load_module("install_webapp_fi_source_adoption_test", ROOT / "scripts" / "install_webapp_fi_source_adoption.py")
portable = _load_module("verify_webapp_fi_source_provenance_test", ROOT / "scripts" / "verify_webapp_fi_source_provenance.py")


CAMPAIGN = "source-adoption-20260730"
REVISION = "f2c7d8e9a0b1"
RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
IMAGE_ID = "sha256:" + "b" * 64
IMAGE_REFERENCE = "registry.example/gold-trade/webapp:2c08"


def _private_file(path, payload):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    path.write_bytes(payload)
    os.chmod(path, 0o600)
    return path


def _canonical_private_json(path, value):
    return _private_file(path, install.canonical_json_bytes(value) + b"\n")


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _new_repo(path):
    path.mkdir(mode=0o700)
    _git(path, "init")
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "user.name", "Source Adoption Test")
    return path


def _commit(repo):
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fixture")
    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()


def _key_material():
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return key, raw, base64.b64encode(public).decode("ascii")


def _signature(key, domain, value):
    return base64.b64encode(key.sign(domain + install.canonical_json_bytes(value))).decode("ascii")


class WebAppFiSourceAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        os.chmod(self.root, 0o700)
        self.control = _new_repo(self.root / "control")
        self.application = _new_repo(self.root / "application")
        for relative in prepare.SOURCE_PAYLOAD_FILES:
            target = self.control / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        self.control_commit = _commit(self.control)
        self.application_files = {}
        for relative in install.RUNTIME_CODE_PROJECTION_RELATIVES:
            if "." in relative:
                content = ("fixture " + relative + "\n").encode("ascii")
                self.application_files[relative] = content
            else:
                content = ("fixture " + relative + "\n").encode("ascii")
                self.application_files[relative + "/fixture.py"] = content
        for relative, content in self.application_files.items():
            target = self.application / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        self.release = _commit(self.application)
        self.controller_key, controller_raw, self.controller_public = _key_material()
        self.controller_private = _private_file(self.root / "keys" / "controller.raw", controller_raw)
        self.fi_key, fi_raw, self.fi_public = _key_material()
        self.fi_private = _private_file(self.root / "keys" / "fi.raw", fi_raw)
        self.package_dir = self.root / "packages" / "package-one"
        (self.root / "packages").mkdir(mode=0o700)
        self.prepared = prepare.prepare_source_adoption_package(
            source_repository=self.control,
            application_source_repository=self.application,
            control_commit=self.control_commit,
            application_release_sha=self.release,
            expected_alembic_revision=REVISION,
            package_id="package-one",
            destination=self.package_dir,
            apply=True,
        )
        self.archive = Path(self.prepared["archive_path"])
        self.preparation_receipt = self.package_dir / prepare.PREPARATION_RECEIPT_NAME
        self.delivery_object = {
            "object_key": "source-adoption/package-one.age",
            "version_id": "version-0001",
            "ciphertext_sha256": hashlib.sha256(b"ciphertext").hexdigest(),
            "ciphertext_bytes": len(b"ciphertext"),
            "plaintext_sha256": self.prepared["archive_sha256"],
            "plaintext_bytes": self.prepared["archive_bytes"],
        }
        unsigned_delivery = {
            "schema": install.DELIVERY_RECEIPT_SCHEMA,
            "status": "received",
            "source_site": install.PACKAGE_SOURCE_SITE,
            "destination_site": install.PACKAGE_DESTINATION_SITE,
            "control_commit": self.control_commit,
            "package_id": "package-one",
            "object": self.delivery_object,
            "archive": {"sha256": self.prepared["archive_sha256"], "bytes": self.prepared["archive_bytes"]},
        }
        delivery = {**unsigned_delivery, "receipt_sha256": install.sha256_bytes(install.canonical_json_bytes(unsigned_delivery))}
        self.delivery_receipt = _canonical_private_json(self.root / "inbox" / "delivery.json", delivery)
        self.envelope = self.root / "inbox" / "delivery-envelope.json"
        prepare.sign_delivery_envelope(
            package_directory=self.package_dir,
            preparation_receipt=self.preparation_receipt,
            expected_control_commit=self.control_commit,
            expected_application_release_sha=self.release,
            campaign_id=CAMPAIGN,
            fi_bootstrap_recipient=RECIPIENT,
            object_key=self.delivery_object["object_key"],
            version_id=self.delivery_object["version_id"],
            ciphertext_sha256=self.delivery_object["ciphertext_sha256"],
            ciphertext_bytes=self.delivery_object["ciphertext_bytes"],
            plaintext_sha256=self.delivery_object["plaintext_sha256"],
            plaintext_bytes=self.delivery_object["plaintext_bytes"],
            controller_signing_private_key=self.controller_private,
            destination=self.envelope,
            apply=True,
        )

    def tearDown(self):
        self.temp.cleanup()

    def _verify_package(self):
        return install.verify_package_inputs(
            archive=self.archive,
            preparation_receipt=self.preparation_receipt,
            delivery_receipt=self.delivery_receipt,
            delivery_envelope=self.envelope,
            pinned_controller_public_key_base64=self.controller_public,
            expected_campaign_id=CAMPAIGN,
            expected_fi_bootstrap_recipient=RECIPIENT,
            expected_control_commit=self.control_commit,
            expected_application_release_sha=self.release,
        )

    def _install(self):
        staging_root = self.root / "staging"
        staging_root.mkdir(mode=0o700)
        return install.install_source_adoption(
            archive=self.archive,
            preparation_receipt=self.preparation_receipt,
            delivery_receipt=self.delivery_receipt,
            delivery_envelope=self.envelope,
            pinned_controller_public_key_base64=self.controller_public,
            expected_campaign_id=CAMPAIGN,
            expected_fi_bootstrap_recipient=RECIPIENT,
            staging_root=staging_root,
            expected_control_commit=self.control_commit,
            expected_application_release_sha=self.release,
            apply=True,
        )

    def _runtime_and_config(self, installed):
        runtime = self.root / "runtime"
        runtime.mkdir(mode=0o700)
        for relative, content in self.application_files.items():
            target = runtime / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        static = runtime / install.RUNTIME_STATIC_ASSET_RELATIVE
        static.mkdir()
        index = static / "index.html"
        index.write_bytes(b"fixture static asset\n")
        ssh_public = _private_file(self.root / "keys" / "ssh-host-ed25519.pub", b"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest fixture\n")
        reader = _private_file(self.root / "keys" / "reader.env", b"CAPTURE_DB_USER=reader\nCAPTURE_DB_PASSWORD=fixture-password\n")
        role = {
            "schema": install.SOURCE_ROLE_CONFIG_SCHEMA,
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "application": {"release_sha": self.release, "expected_alembic_revision": REVISION},
            "database_container": "fixture_db",
            "application_container": "fixture_app",
            "sync_worker_container": "fixture_sync",
            "read_only_capture_env_file": str(reader),
            "source_signing_private_key_file": str(self.fi_private),
        }
        role_path = _canonical_private_json(self.root / "keys" / "role.json", role)
        static_files = [{"path": "index.html", "sha256": install.sha256_bytes(index.read_bytes()), "bytes": index.stat().st_size}]
        static_unsigned = {
            "schema": install.STATIC_ASSET_PROOF_SCHEMA,
            "status": "verified",
            "campaign_id": CAMPAIGN,
            "application": {"release_sha": self.release, "expected_alembic_revision": REVISION},
            "source_kind": "deterministic_2c08_dist_manifest",
            "artifact": {
                "object_key": "static-assets/fixture.age",
                "version_id": "static-version-1",
                "ciphertext_sha256": hashlib.sha256(b"static ciphertext").hexdigest(),
                "ciphertext_bytes": len(b"static ciphertext"),
                "plaintext_sha256": hashlib.sha256(index.read_bytes()).hexdigest(),
                "plaintext_bytes": index.stat().st_size,
            },
            "files": static_files,
            "files_sha256": install.sha256_bytes(install.canonical_json_bytes(static_files)),
            "controller_public_key_base64": self.controller_public,
        }
        static_proof = {**static_unsigned, "controller_signature": {"algorithm": "ed25519", "signature_base64": _signature(self.controller_key, install.STATIC_ASSET_SIGNATURE_DOMAIN, static_unsigned)}}
        static_path = _canonical_private_json(self.root / "keys" / "static-proof.json", static_proof)
        certificate_unsigned = {
            "schema": install.SIGNER_ENROLLMENT_CERTIFICATE_SCHEMA,
            "status": "issued",
            "campaign_id": CAMPAIGN,
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "package_id": installed["package_id"],
            "application": installed["application"],
            "fi_ssh_host_public_key_sha256": install.sha256_file(ssh_public)[0],
            "source_signing_public_key_base64": self.fi_public,
            "controller_public_key_base64": self.controller_public,
        }
        certificate = {**certificate_unsigned, "controller_signature": {"algorithm": "ed25519", "signature_base64": _signature(self.controller_key, install.SIGNER_ENROLLMENT_SIGNATURE_DOMAIN, certificate_unsigned)}}
        certificate_path = _canonical_private_json(self.root / "keys" / "certificate.json", certificate)
        return runtime, role_path, ssh_public, static_path, certificate_path

    def _container_records(self, runtime):
        def mounts(include_static):
            records = [
                {"type": "bind", "source": str(runtime / relative), "destination": "/app/" + relative, "read_only": False}
                for relative in install.RUNTIME_CODE_PROJECTION_RELATIVES
            ]
            if include_static:
                records.append({"type": "bind", "source": str(runtime / install.RUNTIME_STATIC_ASSET_RELATIVE), "destination": "/app/" + install.RUNTIME_STATIC_ASSET_RELATIVE, "read_only": False})
                records.append({"type": "volume", "source": "fixture_uploads", "destination": "/app/uploads", "read_only": False})
                records.append({"type": "volume", "source": "fixture_audit", "destination": "/app/audit_trail", "read_only": False})
            return sorted(records, key=lambda item: (item["destination"], item["type"], str(item["source"])))

        return {
            "fixture_app": {"name": "fixture_app", "container_id": "a" * 64, "image_id": IMAGE_ID, "image_reference": IMAGE_REFERENCE, "mounts": mounts(True)},
            "fixture_sync": {"name": "fixture_sync", "container_id": "c" * 64, "image_id": IMAGE_ID, "image_reference": IMAGE_REFERENCE, "mounts": mounts(False)},
            "fixture_db": {"name": "fixture_db", "container_id": "d" * 64, "image_id": "sha256:" + "e" * 64, "image_reference": "postgres:16", "mounts": []},
        }

    def _enroll(self, installed, role_path, ssh_public, certificate):
        return install.enroll_source_signer(
            install_receipt=installed["receipt_path"],
            source_role_config=role_path,
            certificate=certificate,
            ssh_host_public_key_file=ssh_public,
            pinned_controller_public_key_base64=self.controller_public,
            campaign_id=CAMPAIGN,
            apply=True,
        )

    def test_prepared_package_contains_only_attest_bootstrap_and_signed_envelope_binds_version(self):
        verified = self._verify_package()
        self.assertEqual(verified["campaign_id"], CAMPAIGN)
        self.assertEqual(verified["delivery_object"]["version_id"], "version-0001")
        with tarfile.open(self.archive, "r:") as archive:
            self.assertEqual(
                {item.name for item in archive.getmembers()},
                set(prepare.PACKAGE_FILES),
            )
            self.assertNotIn("scripts/publish_webapp_fi_snapshot_standby.py", {item.name for item in archive.getmembers()})
        altered = dict(self.delivery_object)
        altered["version_id"] = "version-0002"
        unsigned = {
            "schema": install.DELIVERY_RECEIPT_SCHEMA,
            "status": "received",
            "source_site": install.PACKAGE_SOURCE_SITE,
            "destination_site": install.PACKAGE_DESTINATION_SITE,
            "control_commit": self.control_commit,
            "package_id": "package-one",
            "object": altered,
            "archive": {"sha256": self.prepared["archive_sha256"], "bytes": self.prepared["archive_bytes"]},
        }
        altered_receipt = _canonical_private_json(self.root / "inbox" / "delivery-altered.json", {**unsigned, "receipt_sha256": install.sha256_bytes(install.canonical_json_bytes(unsigned))})
        with self.assertRaises(install.SourceAdoptionInstallError):
            install.verify_package_inputs(
                archive=self.archive,
                preparation_receipt=self.preparation_receipt,
                delivery_receipt=altered_receipt,
                delivery_envelope=self.envelope,
                pinned_controller_public_key_base64=self.controller_public,
                expected_campaign_id=CAMPAIGN,
                expected_fi_bootstrap_recipient=RECIPIENT,
                expected_control_commit=self.control_commit,
                expected_application_release_sha=self.release,
            )

    def test_invalid_controller_envelope_blocks_before_install_candidate(self):
        value = json.loads(self.envelope.read_text(encoding="utf-8"))
        value["object"]["version_id"] = "tampered"
        _canonical_private_json(self.envelope, value)
        staging = self.root / "staging"
        staging.mkdir(mode=0o700)
        with self.assertRaises(install.SourceAdoptionInstallError):
            install.install_source_adoption(
                archive=self.archive,
                preparation_receipt=self.preparation_receipt,
                delivery_receipt=self.delivery_receipt,
                delivery_envelope=self.envelope,
                pinned_controller_public_key_base64=self.controller_public,
                expected_campaign_id=CAMPAIGN,
                expected_fi_bootstrap_recipient=RECIPIENT,
                staging_root=staging,
                expected_control_commit=self.control_commit,
                expected_application_release_sha=self.release,
                apply=True,
            )
        self.assertEqual(list(staging.iterdir()), [])

    def test_sensitive_package_verification_has_an_explicit_root_gate(self):
        with patch.object(install.os, "geteuid", return_value=1000):
            with self.assertRaises(install.SourceAdoptionInstallError):
                self._verify_package()

    def test_preparation_and_controller_envelope_default_to_no_write_plan(self):
        package_destination = self.root / "packages" / "planned-package"
        package_plan = prepare.prepare_source_adoption_package(
            source_repository=self.control,
            application_source_repository=self.application,
            control_commit=self.control_commit,
            application_release_sha=self.release,
            expected_alembic_revision=REVISION,
            package_id="planned-package",
            destination=package_destination,
            apply=False,
        )
        self.assertEqual(package_plan["status"], "planned")
        self.assertFalse(package_destination.exists())
        envelope_destination = self.root / "inbox" / "planned-envelope.json"
        envelope_plan = prepare.sign_delivery_envelope(
            package_directory=self.package_dir,
            preparation_receipt=self.preparation_receipt,
            expected_control_commit=self.control_commit,
            expected_application_release_sha=self.release,
            campaign_id=CAMPAIGN,
            fi_bootstrap_recipient=RECIPIENT,
            object_key=self.delivery_object["object_key"],
            version_id=self.delivery_object["version_id"],
            ciphertext_sha256=self.delivery_object["ciphertext_sha256"],
            ciphertext_bytes=self.delivery_object["ciphertext_bytes"],
            plaintext_sha256=self.delivery_object["plaintext_sha256"],
            plaintext_bytes=self.delivery_object["plaintext_bytes"],
            controller_signing_private_key=self.controller_private,
            destination=envelope_destination,
            apply=False,
        )
        self.assertEqual(envelope_plan["status"], "planned")
        self.assertFalse(envelope_destination.exists())

    def test_enrolled_attestation_is_portable_and_covers_app_sync_static_projection(self):
        installed = self._install()
        runtime, role_path, ssh_public, static_path, certificate = self._runtime_and_config(installed)
        enrollment = self._enroll(installed, role_path, ssh_public, certificate)
        containers = self._container_records(runtime)
        image = {"image_id": IMAGE_ID, "image_reference": IMAGE_REFERENCE, "repo_tags": [IMAGE_REFERENCE], "repo_digests": []}
        with patch.object(install, "_inspect_container", side_effect=lambda name: copy.deepcopy(containers[name])), patch.object(install, "_inspect_image", return_value=image), patch.object(install, "_read_only_schema_observation", return_value={"observed_alembic_revision": REVISION, "capture_role_verified_read_only": True}):
            attestation = install.attest_source_role(
                install_receipt=installed["receipt_path"],
                source_role_config=role_path,
                signer_enrollment_receipt=Path(installed["candidate"]) / "enrollments" / f"{CAMPAIGN}.json",
                signer_enrollment_certificate=certificate,
                ssh_host_public_key_file=ssh_public,
                runtime_source_root=runtime,
                static_assets_descriptor=static_path,
                pinned_controller_public_key_base64=self.controller_public,
                campaign_id=CAMPAIGN,
                expected_app_image_id=IMAGE_ID,
                expected_app_image_reference=IMAGE_REFERENCE,
                attestation_id="attestation-one",
                apply=True,
            )
        self.assertEqual(enrollment["status"], "verified")
        self.assertEqual(attestation["status"], "verified")
        self.assertEqual(attestation["source_signing_key_id"], "ed25519-sha256:" + hashlib.sha256(base64.b64decode(self.fi_public)).hexdigest())
        with patch.object(install, "_inspect_container", side_effect=AssertionError("portable verifier touched Docker")), patch.object(install, "_inspect_image", side_effect=AssertionError("portable verifier touched Docker")):
            portable_verification = install.verify_source_role_attestation(
                attestation=Path(attestation["attestation_path"]),
                pinned_source_signing_public_key_base64=self.fi_public,
                expected_campaign_id=CAMPAIGN,
                expected_application={"release_sha": self.release, "expected_alembic_revision": REVISION},
                expected_control_commit=self.control_commit,
                expected_app_image_id=IMAGE_ID,
                expected_app_image_reference=IMAGE_REFERENCE,
            )
        self.assertEqual(portable_verification["source_adoption_delivery"]["version_id"], "version-0001")
        portable_result = portable.verify_source_role_attestation_payload(
            payload=Path(attestation["attestation_path"]).read_bytes(),
            pinned_source_signing_public_key_base64=self.fi_public,
            expected_campaign_id=CAMPAIGN,
            expected_application={"release_sha": self.release, "expected_alembic_revision": REVISION},
            expected_control_commit=self.control_commit,
            expected_app_image_id=IMAGE_ID,
            expected_app_image_reference=IMAGE_REFERENCE,
        )
        self.assertEqual(portable_result["application_release_tree"], attestation["application_release_tree"])
        archive_payload = b"fixture exported image archive"
        image_unsigned = {
            "schema": install.IMAGE_EXPORT_RECEIPT_SCHEMA,
            "status": "exported",
            "exported_at": "2026-07-30T00:00:00Z",
            "export_id": "export-one",
            "campaign_id": CAMPAIGN,
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "application": {"release_sha": self.release, "expected_alembic_revision": REVISION},
            "application_release_tree": attestation["application_release_tree"],
            "tooling": {"control_commit": self.control_commit, "control_tree": self.control_commit},
            "canonical_release_tree_sha256": attestation["canonical_release_tree_sha256"],
            "source_role_attestation_sha256": attestation["attestation_sha256"],
            "image": {
                "image_id": IMAGE_ID,
                "image_reference": IMAGE_REFERENCE,
                "archive_sha256": hashlib.sha256(archive_payload).hexdigest(),
                "archive_bytes": len(archive_payload),
                "docker_manifest_sha256": hashlib.sha256(b"manifest").hexdigest(),
                "docker_config_sha256": hashlib.sha256(b"config").hexdigest(),
                "layer_count": 1,
                "repo_tags": [IMAGE_REFERENCE],
            },
            "pre_export_runtime": {"application": containers["fixture_app"], "sync_worker": containers["fixture_sync"], "active_image": image},
            "post_export_runtime": {"application": containers["fixture_app"], "sync_worker": containers["fixture_sync"], "active_image": image},
            "image_archive_does_not_prove_bind_mounted_runtime": True,
            "archive_consumption": {"docker_load_prohibited": True, "fi_local_archive_verification_before_age_encryption": True, "controller_read_back_verification_after_age_encryption": True, "raw_repo_tags_are_not_authorization": True},
            "object_storage_export_required": {"transport": "private_versioned_age_only", "create_only": True, "read_back_same_version_id": True, "direct_webapp_fi_to_webapp_ir_transfer": False},
            "source_signing_public_key_base64": self.fi_public,
            "source_signing_key_id": portable.public_key_id(self.fi_public),
        }
        image_receipt = {**image_unsigned, "source_signature": {"algorithm": "ed25519", "signature_base64": _signature(self.fi_key, install.IMAGE_EXPORT_SIGNATURE_DOMAIN, image_unsigned)}}
        image_result = portable.verify_image_export_receipt_payload(
            payload=portable.canonical_json_bytes(image_receipt) + b"\n",
            pinned_source_signing_public_key_base64=self.fi_public,
            expected_campaign_id=CAMPAIGN,
            expected_application={"release_sha": self.release, "expected_alembic_revision": REVISION},
            expected_control_commit=self.control_commit,
            expected_application_release_tree=attestation["application_release_tree"],
            expected_attestation_sha256=attestation["attestation_sha256"],
            expected_app_image_id=IMAGE_ID,
            expected_app_image_reference=IMAGE_REFERENCE,
        )
        self.assertEqual(image_result["image"]["archive_sha256"], hashlib.sha256(archive_payload).hexdigest())

    def test_static_asset_drift_blocks_attestation_without_docker(self):
        installed = self._install()
        runtime, role_path, ssh_public, static_path, certificate = self._runtime_and_config(installed)
        self._enroll(installed, role_path, ssh_public, certificate)
        (runtime / install.RUNTIME_STATIC_ASSET_RELATIVE / "index.html").write_bytes(b"changed after controller manifest\n")
        with patch.object(install, "_inspect_container", side_effect=AssertionError("static proof should fail before Docker")):
            with self.assertRaises(install.SourceAdoptionInstallError):
                install.attest_source_role(
                    install_receipt=installed["receipt_path"],
                    source_role_config=role_path,
                    signer_enrollment_receipt=Path(installed["candidate"]) / "enrollments" / f"{CAMPAIGN}.json",
                    signer_enrollment_certificate=certificate,
                    ssh_host_public_key_file=ssh_public,
                    runtime_source_root=runtime,
                    static_assets_descriptor=static_path,
                    pinned_controller_public_key_base64=self.controller_public,
                    campaign_id=CAMPAIGN,
                    expected_app_image_id=IMAGE_ID,
                    expected_app_image_reference=IMAGE_REFERENCE,
                    attestation_id="attestation-static-drift",
                    apply=False,
                )

    def test_signer_enrollment_rejects_certificate_for_different_ssh_host(self):
        installed = self._install()
        _, role_path, ssh_public, _, certificate = self._runtime_and_config(installed)
        ssh_public.write_bytes(b"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDifferent fixture\n")
        os.chmod(ssh_public, 0o600)
        with self.assertRaises(install.SourceAdoptionInstallError):
            self._enroll(installed, role_path, ssh_public, certificate)

    def test_unexpected_sync_app_mount_blocks_before_schema_query(self):
        installed = self._install()
        runtime, role_path, ssh_public, static_path, certificate = self._runtime_and_config(installed)
        self._enroll(installed, role_path, ssh_public, certificate)
        containers = self._container_records(runtime)
        containers["fixture_sync"]["mounts"].append({"type": "bind", "source": str(runtime / "unreviewed"), "destination": "/app/unreviewed", "read_only": False})
        containers["fixture_sync"]["mounts"].sort(key=lambda item: (item["destination"], item["type"], str(item["source"])))
        with patch.object(install, "_inspect_container", side_effect=lambda name: copy.deepcopy(containers[name])), patch.object(install, "_read_only_schema_observation", side_effect=AssertionError("mount validation should fail before DB query")):
            with self.assertRaises(install.SourceAdoptionInstallError):
                install.attest_source_role(
                    install_receipt=installed["receipt_path"],
                    source_role_config=role_path,
                    signer_enrollment_receipt=Path(installed["candidate"]) / "enrollments" / f"{CAMPAIGN}.json",
                    signer_enrollment_certificate=certificate,
                    ssh_host_public_key_file=ssh_public,
                    runtime_source_root=runtime,
                    static_assets_descriptor=static_path,
                    pinned_controller_public_key_base64=self.controller_public,
                    campaign_id=CAMPAIGN,
                    expected_app_image_id=IMAGE_ID,
                    expected_app_image_reference=IMAGE_REFERENCE,
                    attestation_id="attestation-unreviewed-mount",
                    apply=True,
                )

    def test_docker_archive_contract_binds_image_config_and_export_plan_never_inspects_docker(self):
        config = b'{"architecture":"amd64"}'
        image_id = "sha256:" + hashlib.sha256(config).hexdigest()
        archive = self.root / "archive.tar"
        with tarfile.open(archive, "w:") as output:
            manifest = json.dumps([{"Config": image_id.removeprefix("sha256:") + ".json", "RepoTags": [IMAGE_REFERENCE], "Layers": ["layer.tar"]}], separators=(",", ":")).encode("utf-8")
            for name, payload in (("manifest.json", manifest), (image_id.removeprefix("sha256:") + ".json", config), ("layer.tar", b"layer")):
                item = tarfile.TarInfo(name)
                item.size = len(payload)
                output.addfile(item, io.BytesIO(payload))
        os.chmod(archive, 0o600)
        info = install._verify_docker_save_archive(archive=archive, expected_image_id=image_id)
        self.assertEqual(info["docker_config_sha256"], image_id.removeprefix("sha256:"))
        installed = self._install()
        runtime, role_path, ssh_public, static_path, certificate = self._runtime_and_config(installed)
        self._enroll(installed, role_path, ssh_public, certificate)
        containers = self._container_records(runtime)
        image = {"image_id": IMAGE_ID, "image_reference": IMAGE_REFERENCE, "repo_tags": [IMAGE_REFERENCE], "repo_digests": []}
        with patch.object(install, "_inspect_container", side_effect=lambda name: copy.deepcopy(containers[name])), patch.object(install, "_inspect_image", return_value=image), patch.object(install, "_read_only_schema_observation", return_value={"observed_alembic_revision": REVISION, "capture_role_verified_read_only": True}):
            attestation = install.attest_source_role(
                install_receipt=installed["receipt_path"], source_role_config=role_path, signer_enrollment_receipt=Path(installed["candidate"]) / "enrollments" / f"{CAMPAIGN}.json", signer_enrollment_certificate=certificate, ssh_host_public_key_file=ssh_public, runtime_source_root=runtime, static_assets_descriptor=static_path, pinned_controller_public_key_base64=self.controller_public, campaign_id=CAMPAIGN, expected_app_image_id=IMAGE_ID, expected_app_image_reference=IMAGE_REFERENCE, attestation_id="attestation-export-plan", apply=True,
            )
        parent = self.root / "exports"
        parent.mkdir(mode=0o700)
        with patch.object(install, "_inspect_container", side_effect=AssertionError("export plan touched Docker")):
            plan = install.export_actual_fi_image(
                attestation=Path(attestation["attestation_path"]), source_role_config=role_path, pinned_source_signing_public_key_base64=self.fi_public, expected_campaign_id=CAMPAIGN, expected_application={"release_sha": self.release, "expected_alembic_revision": REVISION}, expected_control_commit=self.control_commit, expected_app_image_id=IMAGE_ID, expected_app_image_reference=IMAGE_REFERENCE, destination=parent / "fresh", export_id="export-one", apply=False,
            )
        self.assertTrue(plan["object_storage_export_required"]["create_only"])


if __name__ == "__main__":
    unittest.main()
