import base64
import copy
import datetime as dt
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
        role = {
            "schema": install.SOURCE_ROLE_CONFIG_SCHEMA,
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "application": {"release_sha": self.release, "expected_alembic_revision": REVISION},
            "application_container": "fixture_app",
            "sync_worker_container": "fixture_sync",
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
        issued = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        not_after = issued + dt.timedelta(minutes=10)
        certificate_unsigned = {
            "schema": install.SIGNER_ENROLLMENT_CERTIFICATE_SCHEMA,
            "status": "issued",
            "certificate_id": "certificate-one",
            "operation_id": "enrollment-one",
            "issued_at": issued.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "not_before": issued.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "not_after": not_after.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "campaign_id": CAMPAIGN,
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "package_id": installed["package_id"],
            "application": installed["application"],
            "tooling": installed["tooling"],
            "canonical_release_tree_sha256": installed["canonical_release_tree_sha256"],
            "source_adoption_install_receipt_sha256": installed["receipt_sha256"],
            "delivery_envelope_sha256": installed["package"]["delivery_envelope_sha256"],
            "source_adoption_object": {
                "object_key": installed["package"]["object_key"],
                "version_id": installed["package"]["version_id"],
                "ciphertext_sha256": installed["package"]["ciphertext_sha256"],
                "ciphertext_bytes": installed["package"]["ciphertext_bytes"],
                "plaintext_sha256": installed["package"]["archive_sha256"],
                "plaintext_bytes": installed["package"]["archive_bytes"],
            },
            "fi_bootstrap_recipient": installed["package"]["fi_bootstrap_recipient"],
            "fi_ssh_host_public_key_sha256": install.sha256_file(ssh_public)[0],
            "source_signing_public_key_base64": self.fi_public,
            "source_signing_key_id": install._public_key_id(self.fi_public),
            "controller_public_key_base64": self.controller_public,
            "controller_key_id": install._public_key_id(self.controller_public),
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

    def _attest(self, installed, runtime, role_path, ssh_public, static_path, certificate, *, attestation_id):
        containers = self._container_records(runtime)
        image = {
            "image_id": IMAGE_ID,
            "image_reference": IMAGE_REFERENCE,
            "repo_tags": [IMAGE_REFERENCE],
            "repo_digests": [],
        }
        with patch.object(install, "_inspect_container", side_effect=lambda name: copy.deepcopy(containers[name])), patch.object(install, "_inspect_image", return_value=image):
            return install.attest_source_role(
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
                attestation_id=attestation_id,
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
        with patch.object(install, "_inspect_container", side_effect=lambda name: copy.deepcopy(containers[name])), patch.object(install, "_inspect_image", return_value=image):
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
                expected_canonical_release_tree_sha256=attestation["descriptor_claim"]["canonical_release_tree_sha256"],
                expected_app_image_id=IMAGE_ID,
                expected_app_image_reference=IMAGE_REFERENCE,
            )
        self.assertEqual(portable_verification["source_adoption_delivery_claim"]["version_id"], "version-0001")
        portable_result = portable.verify_source_role_attestation_payload(
            payload=Path(attestation["attestation_path"]).read_bytes(),
            pinned_source_signing_public_key_base64=self.fi_public,
            expected_campaign_id=CAMPAIGN,
            expected_application={"release_sha": self.release, "expected_alembic_revision": REVISION},
            expected_control_commit=self.control_commit,
            expected_canonical_release_tree_sha256=attestation["descriptor_claim"]["canonical_release_tree_sha256"],
            expected_app_image_id=IMAGE_ID,
            expected_app_image_reference=IMAGE_REFERENCE,
            verification_time=install.utc_now(),
        )
        self.assertEqual(portable_result["descriptor_claim"]["application_release_tree"], attestation["descriptor_claim"]["application_release_tree"])
        archive_payload = b"fixture exported image archive"
        export_runtime = {
            **portable_result["runtime_claim"],
            "active_application_image": portable_result["image_claim"]["active_application_image"],
        }
        image_unsigned = {
            "schema": install.IMAGE_EXPORT_RECEIPT_SCHEMA,
            "status": "exported",
            "exported_at": install.utc_now(),
            "export_id": "export-one",
            "campaign_id": CAMPAIGN,
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "application": {"release_sha": self.release, "expected_alembic_revision": REVISION},
            "application_release_tree": attestation["descriptor_claim"]["application_release_tree"],
            "tooling": {"control_commit": self.control_commit, "control_tree": self.control_commit},
            "canonical_release_tree_sha256": attestation["descriptor_claim"]["canonical_release_tree_sha256"],
            "source_role_attestation_sha256": attestation["attestation_sha256"],
            "observation_scope": {
                "point_in_time_only": True,
                "data_capture_performed": False,
                "schema_capture_performed": False,
                "promotion_ready": False,
                "later_snapshot_requires_separate_authorization": True,
            },
            "image": {
                "image_id": IMAGE_ID,
                "image_reference": IMAGE_REFERENCE,
                "archive_sha256": hashlib.sha256(archive_payload).hexdigest(),
                "archive_bytes": len(archive_payload),
                "docker_save": {
                    "command": ["docker", "save", "--output", "webapp-fi-active-app-image.tar", IMAGE_ID],
                    "docker_executable_sha256": hashlib.sha256(b"docker").hexdigest(),
                    "docker_executable_bytes": len(b"docker"),
                    "archive_semantics": "exact_bytes_only_unparsed",
                    "docker_load_invoked": False,
                    "loadability_claimed": False,
                },
            },
            "pre_export_runtime": export_runtime,
            "post_export_runtime": export_runtime,
            "exact_byte_export": {"archive_is_unparsed_exact_bytes": True, "docker_load_invoked": False, "loadability_claimed": False, "bind_mounted_runtime_revalidated_before_and_after": True},
            "archive_consumption": {"docker_load_prohibited": True, "fi_local_exact_byte_hash_before_age_encryption": True, "controller_read_back_exact_byte_hash_after_age_encryption": True, "raw_repo_tags_are_not_authorization": True},
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
            expected_application_release_tree=attestation["descriptor_claim"]["application_release_tree"],
            expected_canonical_release_tree_sha256=attestation["descriptor_claim"]["canonical_release_tree_sha256"],
            expected_attestation_sha256=attestation["attestation_sha256"],
            expected_app_image_id=IMAGE_ID,
            expected_app_image_reference=IMAGE_REFERENCE,
            verification_time=install.utc_now(),
        )
        self.assertEqual(image_result["image_claim"]["archive_sha256"], hashlib.sha256(archive_payload).hexdigest())

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

    def test_unexpected_sync_app_mount_blocks_without_data_capture(self):
        installed = self._install()
        runtime, role_path, ssh_public, static_path, certificate = self._runtime_and_config(installed)
        self._enroll(installed, role_path, ssh_public, certificate)
        containers = self._container_records(runtime)
        containers["fixture_sync"]["mounts"].append({"type": "bind", "source": str(runtime / "unreviewed"), "destination": "/app/unreviewed", "read_only": False})
        containers["fixture_sync"]["mounts"].sort(key=lambda item: (item["destination"], item["type"], str(item["source"])))
        with patch.object(install, "_inspect_container", side_effect=lambda name: copy.deepcopy(containers[name])):
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

    def test_exact_byte_export_plan_never_invokes_docker_and_requires_revalidation_inputs(self):
        installed = self._install()
        runtime, role_path, ssh_public, static_path, certificate = self._runtime_and_config(installed)
        self._enroll(installed, role_path, ssh_public, certificate)
        containers = self._container_records(runtime)
        image = {"image_id": IMAGE_ID, "image_reference": IMAGE_REFERENCE, "repo_tags": [IMAGE_REFERENCE], "repo_digests": []}
        with patch.object(install, "_inspect_container", side_effect=lambda name: copy.deepcopy(containers[name])), patch.object(install, "_inspect_image", return_value=image):
            attestation = install.attest_source_role(
                install_receipt=installed["receipt_path"], source_role_config=role_path, signer_enrollment_receipt=Path(installed["candidate"]) / "enrollments" / f"{CAMPAIGN}.json", signer_enrollment_certificate=certificate, ssh_host_public_key_file=ssh_public, runtime_source_root=runtime, static_assets_descriptor=static_path, pinned_controller_public_key_base64=self.controller_public, campaign_id=CAMPAIGN, expected_app_image_id=IMAGE_ID, expected_app_image_reference=IMAGE_REFERENCE, attestation_id="attestation-export-plan", apply=True,
            )
        parent = self.root / "exports"
        parent.mkdir(mode=0o700)
        with patch.object(install, "_inspect_container", side_effect=AssertionError("export plan touched Docker")):
            plan = install.export_actual_fi_image(
                attestation=Path(attestation["attestation_path"]),
                source_role_config=role_path,
                signer_enrollment_receipt=Path(installed["candidate"]) / "enrollments" / f"{CAMPAIGN}.json",
                signer_enrollment_certificate=certificate,
                ssh_host_public_key_file=ssh_public,
                runtime_source_root=runtime,
                static_assets_descriptor=static_path,
                pinned_controller_public_key_base64=self.controller_public,
                pinned_source_signing_public_key_base64=self.fi_public,
                expected_campaign_id=CAMPAIGN,
                expected_application={"release_sha": self.release, "expected_alembic_revision": REVISION},
                expected_control_commit=self.control_commit,
                expected_canonical_release_tree_sha256=attestation["descriptor_claim"]["canonical_release_tree_sha256"],
                expected_app_image_id=IMAGE_ID,
                expected_app_image_reference=IMAGE_REFERENCE,
                destination=parent / "fresh",
                export_id="export-one",
                apply=False,
            )
        self.assertTrue(plan["object_storage_export_required"]["create_only"])
        self.assertTrue(plan["revalidate_projection_static_containers_before_and_after_docker_save"])
        self.assertTrue(plan["exact_bytes_only_unparsed_archive"])
        self.assertFalse(plan["loadability_claimed"])


if __name__ == "__main__":
    unittest.main()
