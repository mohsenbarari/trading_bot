import base64
import copy
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
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
PROVIDER_VERSION_ID = "3/L4kqtJlcpXroDTDmJ+3DcJKZBjjfM7m1E7S="


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
        self.external_certs = self.root / "external-certs"
        self.external_certs.mkdir(mode=0o700)
        self.control = _new_repo(self.root / "control")
        self.application = _new_repo(self.root / "application")
        for relative in prepare.SOURCE_PAYLOAD_FILES:
            target = self.control / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        self.control_commit = _commit(self.control)
        self.control_tree = subprocess.check_output(
            ["git", "-C", str(self.control), "rev-parse", self.control_commit + "^{tree}"],
            text=True,
        ).strip()
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
            "version_id": PROVIDER_VERSION_ID,
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
                records.append({"type": "bind", "source": str(self.external_certs), "destination": install.RUNTIME_EXTERNAL_NON_PAYLOAD_MOUNT_TARGET, "read_only": False})
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

    def _set_certificate_window(self, certificate, *, issued, not_before, not_after):
        value = json.loads(certificate.read_text(encoding="utf-8"))
        value["issued_at"] = issued.strftime("%Y-%m-%dT%H:%M:%SZ")
        value["not_before"] = not_before.strftime("%Y-%m-%dT%H:%M:%SZ")
        value["not_after"] = not_after.strftime("%Y-%m-%dT%H:%M:%SZ")
        unsigned = {key: item for key, item in value.items() if key != "controller_signature"}
        value["controller_signature"] = {
            "algorithm": "ed25519",
            "signature_base64": _signature(self.controller_key, install.SIGNER_ENROLLMENT_SIGNATURE_DOMAIN, unsigned),
        }
        _canonical_private_json(certificate, value)
        return {
            "issued_at": value["issued_at"],
            "not_before": value["not_before"],
            "not_after": value["not_after"],
        }

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

    def _export(self, installed, runtime, role_path, ssh_public, static_path, certificate, attestation, *, export_id):
        before = {
            **attestation["runtime_claim"],
            "active_application_image": attestation["image_claim"]["active_application_image"],
        }
        parent = self.root / ("exports-" + export_id)
        parent.mkdir(mode=0o700)

        def fake_export(*, archive, expected_image_id):
            archive.write_bytes(b"exact fixture docker-save bytes")
            os.chmod(archive, 0o600)
            return {
                "docker_save_archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "docker_save_archive_bytes": archive.stat().st_size,
                "docker_save": {
                    "command": ["docker", "save", "--output", archive.name, expected_image_id],
                    "docker_executable_sha256": hashlib.sha256(b"docker").hexdigest(),
                    "docker_executable_bytes": len(b"docker"),
                    "archive_semantics": "exact_bytes_only_unparsed",
                    "archive_layout": "not_inspected",
                    "manifest_semantics_attested": False,
                    "docker_load_invoked": False,
                    "loadability_claimed": False,
                },
            }

        required_free = 1024 * install.IMAGE_EXPORT_CAPACITY_MULTIPLIER + install.IMAGE_EXPORT_CAPACITY_MARGIN_BYTES
        with (
            patch.object(install, "_revalidate_export_runtime", side_effect=[before, before]),
            patch.object(install, "_inspect_image_storage_bytes", return_value=1024),
            patch.object(install.shutil, "disk_usage", return_value=SimpleNamespace(free=required_free)),
            patch.object(install, "_export_exact_docker_save_bytes", side_effect=fake_export),
        ):
            return install.export_actual_fi_image(
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
                expected_application=installed["application"],
                expected_control_commit=self.control_commit,
                expected_canonical_release_tree_sha256=attestation["descriptor_claim"]["canonical_release_tree_sha256"],
                expected_app_image_id=IMAGE_ID,
                expected_app_image_reference=IMAGE_REFERENCE,
                destination=parent / "candidate",
                export_id=export_id,
                apply=True,
            )

    def test_prepared_package_contains_only_attest_bootstrap_and_signed_envelope_binds_version(self):
        verified = self._verify_package()
        self.assertEqual(verified["campaign_id"], CAMPAIGN)
        self.assertEqual(verified["delivery_object"]["version_id"], PROVIDER_VERSION_ID)
        with tarfile.open(self.archive, "r:") as archive:
            self.assertEqual(
                {item.name for item in archive.getmembers()},
                set(prepare.PACKAGE_FILES),
            )
            self.assertNotIn("scripts/publish_webapp_fi_snapshot_standby.py", {item.name for item in archive.getmembers()})
            self.assertIn("scripts/prepare_webapp_fi_static_assets.py", {item.name for item in archive.getmembers()})
            static_preparer = archive.extractfile("scripts/prepare_webapp_fi_static_assets.py")
            self.assertIsNotNone(static_preparer)
            static_payload = static_preparer.read()
            self.assertNotIn(b"boto3", static_payload)
            self.assertNotIn(b"subprocess", static_payload)
            self.assertNotIn(b"docker ", static_payload.lower())
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
        self.assertEqual(portable_verification["source_adoption_delivery_claim"]["version_id"], PROVIDER_VERSION_ID)
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
        self.assertNotIn("controller_authorization_verified", portable_result)
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
            "source_signer_enrollment": portable_result["source_signer_enrollment_claim"],
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
                "docker_save_archive_sha256": hashlib.sha256(archive_payload).hexdigest(),
                "docker_save_archive_bytes": len(archive_payload),
                "docker_save": {
                    "command": ["docker", "save", "--output", "webapp-fi-active-app-image.tar", IMAGE_ID],
                    "docker_executable_sha256": hashlib.sha256(b"docker").hexdigest(),
                    "docker_executable_bytes": len(b"docker"),
                    "archive_semantics": "exact_bytes_only_unparsed",
                    "archive_layout": "not_inspected",
                    "manifest_semantics_attested": False,
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
        self.assertEqual(image_result["image_claim"]["docker_save_archive_sha256"], hashlib.sha256(archive_payload).hexdigest())
        self.assertEqual(image_result["source_signer_enrollment_claim"], portable_result["source_signer_enrollment_claim"])
        self.assertNotIn("controller_authorization_verified", image_result)
        invalid_image = copy.deepcopy(image_unsigned)
        invalid_image["image"]["image_reference"] = "registry.example/gold-trade/webapp:unexpected"
        invalid_image_receipt = {
            **invalid_image,
            "source_signature": {
                "algorithm": "ed25519",
                "signature_base64": _signature(self.fi_key, install.IMAGE_EXPORT_SIGNATURE_DOMAIN, invalid_image),
            },
        }
        with self.assertRaises(portable.SourceProvenanceVerificationError):
            portable.verify_image_export_receipt_payload(
                payload=portable.canonical_json_bytes(invalid_image_receipt) + b"\n",
                pinned_source_signing_public_key_base64=self.fi_public,
                expected_campaign_id=CAMPAIGN,
                expected_application=installed["application"],
                expected_control_commit=self.control_commit,
                expected_application_release_tree=attestation["descriptor_claim"]["application_release_tree"],
                expected_canonical_release_tree_sha256=attestation["descriptor_claim"]["canonical_release_tree_sha256"],
                expected_attestation_sha256=attestation["attestation_sha256"],
                expected_app_image_id=IMAGE_ID,
                expected_app_image_reference=IMAGE_REFERENCE,
                verification_time=install.utc_now(),
            )

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

    def test_enrollment_certificate_is_locally_single_use_after_interruption(self):
        installed = self._install()
        _, role_path, ssh_public, _, certificate = self._runtime_and_config(installed)
        role_config = install.load_source_role_config(role_path, expected_application=installed["application"])
        certificate_value = install._validate_signer_enrollment_certificate(
            certificate=certificate,
            pinned_controller_public_key_base64=self.controller_public,
            campaign_id=CAMPAIGN,
            installed=installed,
            role_config=role_config,
            ssh_host_public_key_file=ssh_public,
        )
        consumption = install._consume_enrollment_certificate(
            candidate=Path(installed["candidate"]),
            certificate_value=certificate_value,
            certificate_sha256=certificate_value["certificate_sha256"],
            campaign_id=CAMPAIGN,
        )
        self.assertTrue(Path(consumption["path"]).exists())
        with self.assertRaisesRegex(install.SourceAdoptionInstallError, "already consumed"):
            self._enroll(installed, role_path, ssh_public, certificate)

    def test_enrollment_certificate_rejects_expired_and_non_utc_timestamps(self):
        installed = self._install()
        _, role_path, ssh_public, _, certificate = self._runtime_and_config(installed)
        expired = json.loads(certificate.read_text(encoding="utf-8"))
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        expired["issued_at"] = (now - dt.timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        expired["not_before"] = (now - dt.timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        expired["not_after"] = (now - dt.timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        unsigned = {key: value for key, value in expired.items() if key != "controller_signature"}
        expired["controller_signature"] = {
            "algorithm": "ed25519",
            "signature_base64": _signature(self.controller_key, install.SIGNER_ENROLLMENT_SIGNATURE_DOMAIN, unsigned),
        }
        _canonical_private_json(certificate, expired)
        with self.assertRaisesRegex(install.SourceAdoptionInstallError, "not currently valid"):
            self._enroll(installed, role_path, ssh_public, certificate)

        non_utc = dict(expired)
        non_utc["issued_at"] = "2026-07-30T00:00:00+00:00"
        unsigned = {key: value for key, value in non_utc.items() if key != "controller_signature"}
        non_utc["controller_signature"] = {
            "algorithm": "ed25519",
            "signature_base64": _signature(self.controller_key, install.SIGNER_ENROLLMENT_SIGNATURE_DOMAIN, unsigned),
        }
        _canonical_private_json(certificate, non_utc)
        with self.assertRaises(install.SourceAdoptionInstallError):
            self._enroll(installed, role_path, ssh_public, certificate)

    def test_enrollment_apply_cannot_consume_certificate_before_not_before(self):
        installed = self._install()
        _, role_path, ssh_public, _, certificate = self._runtime_and_config(installed)
        future = json.loads(certificate.read_text(encoding="utf-8"))
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        future["issued_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        future["not_before"] = (now + dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        future["not_after"] = (now + dt.timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        unsigned = {key: value for key, value in future.items() if key != "controller_signature"}
        future["controller_signature"] = {
            "algorithm": "ed25519",
            "signature_base64": _signature(self.controller_key, install.SIGNER_ENROLLMENT_SIGNATURE_DOMAIN, unsigned),
        }
        _canonical_private_json(certificate, future)

        with self.assertRaisesRegex(install.SourceAdoptionInstallError, "outside signer enrollment validity window"):
            install.enroll_source_signer(
                install_receipt=installed["receipt_path"],
                source_role_config=role_path,
                certificate=certificate,
                ssh_host_public_key_file=ssh_public,
                pinned_controller_public_key_base64=self.controller_public,
                campaign_id=CAMPAIGN,
                apply=True,
                verification_time=future["not_before"],
            )

        self.assertFalse((Path(installed["candidate"]) / "enrollments" / f"{CAMPAIGN}.json").exists())
        self.assertFalse((Path(installed["candidate"]).parent / "certificate-consumptions" / "certificate-one.json").exists())
        self.assertFalse((Path(installed["candidate"]).parent / "certificate-consumptions").exists())

    def test_attestation_rechecks_not_before_at_final_signature_time(self):
        installed = self._install()
        runtime, role_path, ssh_public, static_path, certificate = self._runtime_and_config(installed)
        issued = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        not_before = issued + dt.timedelta(minutes=5)
        window = self._set_certificate_window(
            certificate,
            issued=issued,
            not_before=not_before,
            not_after=not_before + dt.timedelta(minutes=5),
        )

        with patch.object(install, "utc_now", side_effect=[window["not_before"], window["not_before"]]):
            install.enroll_source_signer(
                install_receipt=installed["receipt_path"],
                source_role_config=role_path,
                certificate=certificate,
                ssh_host_public_key_file=ssh_public,
                pinned_controller_public_key_base64=self.controller_public,
                campaign_id=CAMPAIGN,
                apply=True,
                verification_time=window["not_before"],
            )

        containers = self._container_records(runtime)
        image = {
            "image_id": IMAGE_ID,
            "image_reference": IMAGE_REFERENCE,
            "repo_tags": [IMAGE_REFERENCE],
            "repo_digests": [],
        }
        with (
            patch.object(install, "utc_now", side_effect=[window["not_before"], window["issued_at"]]),
            patch.object(install, "_inspect_container", side_effect=lambda name: copy.deepcopy(containers[name])),
            patch.object(install, "_inspect_image", return_value=image),
        ):
            with self.assertRaisesRegex(install.SourceAdoptionInstallError, "outside signer enrollment validity window"):
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
                    attestation_id="attestation-clock-regression",
                    apply=True,
                )

        self.assertFalse((Path(installed["candidate"]) / "attestations" / "attestation-clock-regression.json").exists())

    def test_export_rechecks_not_before_at_final_signature_time(self):
        installed = self._install()
        runtime, role_path, ssh_public, static_path, certificate = self._runtime_and_config(installed)
        issued = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        not_before = issued + dt.timedelta(minutes=5)
        window = self._set_certificate_window(
            certificate,
            issued=issued,
            not_before=not_before,
            not_after=not_before + dt.timedelta(minutes=5),
        )

        with patch.object(install, "utc_now", side_effect=[window["not_before"], window["not_before"]]):
            install.enroll_source_signer(
                install_receipt=installed["receipt_path"],
                source_role_config=role_path,
                certificate=certificate,
                ssh_host_public_key_file=ssh_public,
                pinned_controller_public_key_base64=self.controller_public,
                campaign_id=CAMPAIGN,
                apply=True,
                verification_time=window["not_before"],
            )

        containers = self._container_records(runtime)
        image = {
            "image_id": IMAGE_ID,
            "image_reference": IMAGE_REFERENCE,
            "repo_tags": [IMAGE_REFERENCE],
            "repo_digests": [],
        }
        with (
            patch.object(install, "utc_now", side_effect=[window["not_before"], window["not_before"]]),
            patch.object(install, "_inspect_container", side_effect=lambda name: copy.deepcopy(containers[name])),
            patch.object(install, "_inspect_image", return_value=image),
        ):
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
                attestation_id="attestation-export-clock-regression",
                apply=True,
            )

        before = {
            **attestation["runtime_claim"],
            "active_application_image": attestation["image_claim"]["active_application_image"],
        }
        parent = self.root / "exports-clock-regression"
        parent.mkdir(mode=0o700)

        def fake_export(*, archive, expected_image_id):
            archive.write_bytes(b"retained exact bytes after clock regression")
            os.chmod(archive, 0o600)
            return {
                "docker_save_archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "docker_save_archive_bytes": archive.stat().st_size,
                "docker_save": {
                    "command": ["docker", "save", "--output", archive.name, expected_image_id],
                    "docker_executable_sha256": hashlib.sha256(b"docker").hexdigest(),
                    "docker_executable_bytes": len(b"docker"),
                    "archive_semantics": "exact_bytes_only_unparsed",
                    "archive_layout": "not_inspected",
                    "manifest_semantics_attested": False,
                    "docker_load_invoked": False,
                    "loadability_claimed": False,
                },
            }

        required_free = 1024 * install.IMAGE_EXPORT_CAPACITY_MULTIPLIER + install.IMAGE_EXPORT_CAPACITY_MARGIN_BYTES
        with (
            patch.object(install, "utc_now", side_effect=[window["not_before"], window["not_before"], window["issued_at"]]),
            patch.object(install, "_revalidate_export_runtime", side_effect=[before, before]),
            patch.object(install, "_inspect_image_storage_bytes", return_value=1024),
            patch.object(install.shutil, "disk_usage", return_value=SimpleNamespace(free=required_free)),
            patch.object(install, "_export_exact_docker_save_bytes", side_effect=fake_export),
        ):
            with self.assertRaisesRegex(install.SourceAdoptionInstallError, "outside signer enrollment validity window"):
                install.export_actual_fi_image(
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
                    expected_application=installed["application"],
                    expected_control_commit=self.control_commit,
                    expected_canonical_release_tree_sha256=attestation["descriptor_claim"]["canonical_release_tree_sha256"],
                    expected_app_image_id=IMAGE_ID,
                    expected_app_image_reference=IMAGE_REFERENCE,
                    destination=parent / "candidate",
                    export_id="export-clock-regression",
                    apply=True,
                )

        self.assertTrue((parent / "candidate" / "webapp-fi-active-app-image.tar").exists())
        self.assertFalse((parent / "candidate" / "image-export-receipt.json").exists())

    def test_enrollment_certificate_rejects_controller_source_key_reuse(self):
        installed = self._install()
        _, role_path, ssh_public, _, certificate = self._runtime_and_config(installed)
        role = json.loads(role_path.read_text(encoding="utf-8"))
        role["source_signing_private_key_file"] = str(self.controller_private)
        _canonical_private_json(role_path, role)
        reused = json.loads(certificate.read_text(encoding="utf-8"))
        reused["source_signing_public_key_base64"] = self.controller_public
        reused["source_signing_key_id"] = install._public_key_id(self.controller_public)
        unsigned = {key: value for key, value in reused.items() if key != "controller_signature"}
        reused["controller_signature"] = {
            "algorithm": "ed25519",
            "signature_base64": _signature(self.controller_key, install.SIGNER_ENROLLMENT_SIGNATURE_DOMAIN, unsigned),
        }
        _canonical_private_json(certificate, reused)

        with self.assertRaisesRegex(install.SourceAdoptionInstallError, "distinct from the controller key"):
            self._enroll(installed, role_path, ssh_public, certificate)
        self.assertFalse((Path(installed["candidate"]) / "enrollments" / f"{CAMPAIGN}.json").exists())

    def test_enrollment_certificate_binds_exact_delivery_object_and_descriptor(self):
        installed = self._install()
        _, role_path, ssh_public, _, certificate = self._runtime_and_config(installed)
        altered = json.loads(certificate.read_text(encoding="utf-8"))
        altered["source_adoption_object"]["version_id"] = "different-version-id"
        unsigned = {key: value for key, value in altered.items() if key != "controller_signature"}
        altered["controller_signature"] = {
            "algorithm": "ed25519",
            "signature_base64": _signature(self.controller_key, install.SIGNER_ENROLLMENT_SIGNATURE_DOMAIN, unsigned),
        }
        _canonical_private_json(certificate, altered)
        with self.assertRaisesRegex(install.SourceAdoptionInstallError, "delivery target"):
            self._enroll(installed, role_path, ssh_public, certificate)

        unversioned = json.loads(certificate.read_text(encoding="utf-8"))
        unversioned["source_adoption_object"]["version_id"] = "null"
        unsigned = {key: value for key, value in unversioned.items() if key != "controller_signature"}
        unversioned["controller_signature"] = {
            "algorithm": "ed25519",
            "signature_base64": _signature(self.controller_key, install.SIGNER_ENROLLMENT_SIGNATURE_DOMAIN, unsigned),
        }
        _canonical_private_json(certificate, unversioned)
        with self.assertRaisesRegex(install.SourceAdoptionInstallError, "version ID"):
            self._enroll(installed, role_path, ssh_public, certificate)

    def test_portable_v2_rejects_stale_observation_and_incorrect_image_metadata(self):
        installed = self._install()
        runtime, role_path, ssh_public, static_path, certificate = self._runtime_and_config(installed)
        self._enroll(installed, role_path, ssh_public, certificate)
        attestation = self._attest(
            installed,
            runtime,
            role_path,
            ssh_public,
            static_path,
            certificate,
            attestation_id="attestation-v2-negatives",
        )
        original = json.loads(Path(attestation["attestation_path"]).read_text(encoding="utf-8"))

        stale = copy.deepcopy(original)
        stale["attested_at"] = "2020-01-01T00:00:00Z"
        unsigned = {key: value for key, value in stale.items() if key != "source_signature"}
        stale["source_signature"] = {
            "algorithm": "ed25519",
            "signature_base64": _signature(self.fi_key, install.ATTESTATION_SIGNATURE_DOMAIN, unsigned),
        }
        with self.assertRaisesRegex(portable.SourceProvenanceVerificationError, "stale"):
            portable.verify_source_role_attestation_payload(
                payload=portable.canonical_json_bytes(stale) + b"\n",
                pinned_source_signing_public_key_base64=self.fi_public,
                expected_campaign_id=CAMPAIGN,
                expected_application=installed["application"],
                expected_control_commit=self.control_commit,
                expected_canonical_release_tree_sha256=attestation["descriptor_claim"]["canonical_release_tree_sha256"],
                expected_app_image_id=IMAGE_ID,
                expected_app_image_reference=IMAGE_REFERENCE,
                verification_time=install.utc_now(),
                maximum_observation_age_seconds=60,
            )

        expired_enrollment = copy.deepcopy(original)
        attested_at = dt.datetime.strptime(original["attested_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        expired_enrollment["source_signer_enrollment"]["not_after"] = (attested_at - dt.timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        unsigned = {key: value for key, value in expired_enrollment.items() if key != "source_signature"}
        expired_enrollment["source_signature"] = {
            "algorithm": "ed25519",
            "signature_base64": _signature(self.fi_key, install.ATTESTATION_SIGNATURE_DOMAIN, unsigned),
        }
        with self.assertRaisesRegex(portable.SourceProvenanceVerificationError, "after source signer enrollment expiry"):
            portable.verify_source_role_attestation_payload(
                payload=portable.canonical_json_bytes(expired_enrollment) + b"\n",
                pinned_source_signing_public_key_base64=self.fi_public,
                expected_campaign_id=CAMPAIGN,
                expected_application=installed["application"],
                expected_control_commit=self.control_commit,
                expected_canonical_release_tree_sha256=attestation["descriptor_claim"]["canonical_release_tree_sha256"],
                expected_app_image_id=IMAGE_ID,
                expected_app_image_reference=IMAGE_REFERENCE,
                verification_time=install.utc_now(),
            )

        reused_controller_key = copy.deepcopy(original)
        reused_controller_key["source_signer_enrollment"]["controller_key_id"] = portable.public_key_id(self.fi_public)
        unsigned = {key: value for key, value in reused_controller_key.items() if key != "source_signature"}
        reused_controller_key["source_signature"] = {
            "algorithm": "ed25519",
            "signature_base64": _signature(self.fi_key, install.ATTESTATION_SIGNATURE_DOMAIN, unsigned),
        }
        with self.assertRaisesRegex(portable.SourceProvenanceVerificationError, "reuses the controller key"):
            portable.verify_source_role_attestation_payload(
                payload=portable.canonical_json_bytes(reused_controller_key) + b"\n",
                pinned_source_signing_public_key_base64=self.fi_public,
                expected_campaign_id=CAMPAIGN,
                expected_application=installed["application"],
                expected_control_commit=self.control_commit,
                expected_canonical_release_tree_sha256=attestation["descriptor_claim"]["canonical_release_tree_sha256"],
                expected_app_image_id=IMAGE_ID,
                expected_app_image_reference=IMAGE_REFERENCE,
                verification_time=install.utc_now(),
            )

        bad_image = copy.deepcopy(original)
        bad_image["active_application_image"]["image_reference"] = "registry.example/gold-trade/webapp:wrong"
        unsigned = {key: value for key, value in bad_image.items() if key != "source_signature"}
        bad_image["source_signature"] = {
            "algorithm": "ed25519",
            "signature_base64": _signature(self.fi_key, install.ATTESTATION_SIGNATURE_DOMAIN, unsigned),
        }
        with self.assertRaises(portable.SourceProvenanceVerificationError):
            portable.verify_source_role_attestation_payload(
                payload=portable.canonical_json_bytes(bad_image) + b"\n",
                pinned_source_signing_public_key_base64=self.fi_public,
                expected_campaign_id=CAMPAIGN,
                expected_application=installed["application"],
                expected_control_commit=self.control_commit,
                expected_canonical_release_tree_sha256=attestation["descriptor_claim"]["canonical_release_tree_sha256"],
                expected_app_image_id=IMAGE_ID,
                expected_app_image_reference=IMAGE_REFERENCE,
                verification_time=install.utc_now(),
            )

        with self.assertRaisesRegex(portable.SourceProvenanceVerificationError, "canonical descriptor"):
            portable.verify_source_role_attestation_payload(
                payload=portable.canonical_json_bytes(original) + b"\n",
                pinned_source_signing_public_key_base64=self.fi_public,
                expected_campaign_id=CAMPAIGN,
                expected_application=installed["application"],
                expected_control_commit=self.control_commit,
                expected_canonical_release_tree_sha256="0" * 64,
                expected_app_image_id=IMAGE_ID,
                expected_app_image_reference=IMAGE_REFERENCE,
                verification_time=install.utc_now(),
            )

    def test_composite_provenance_binds_controller_authority_to_isolated_image_artifacts(self):
        installed = self._install()
        runtime, role_path, ssh_public, static_path, certificate = self._runtime_and_config(installed)
        self._enroll(installed, role_path, ssh_public, certificate)
        attestation = self._attest(
            installed,
            runtime,
            role_path,
            ssh_public,
            static_path,
            certificate,
            attestation_id="attestation-composite",
        )
        exported = self._export(
            installed,
            runtime,
            role_path,
            ssh_public,
            static_path,
            certificate,
            attestation,
            export_id="export-composite",
        )
        source_role_payload = Path(attestation["attestation_path"]).read_bytes()
        image_export_payload = Path(exported["receipt_path"]).read_bytes()
        delivery_payload = self.envelope.read_bytes()
        certificate_payload = certificate.read_bytes()
        static_payload = static_path.read_bytes()
        image_bundle = b"controller-isolated-image-bundle"
        image_manifest = b"controller-isolated-image-manifest"
        source_image = exported["image_claim"]
        proof_sha256 = {
            "source_role_attestation": hashlib.sha256(source_role_payload).hexdigest(),
            "image_export_receipt": hashlib.sha256(image_export_payload).hexdigest(),
            "controller_delivery_envelope": hashlib.sha256(delivery_payload).hexdigest(),
            "signer_enrollment_certificate": hashlib.sha256(certificate_payload).hexdigest(),
            "static_assets_provenance": hashlib.sha256(static_payload).hexdigest(),
        }
        adoption_unsigned = {
            "schema": portable.IMAGE_ADOPTION_RECEIPT_SCHEMA,
            "status": "adopted",
            "adopted_at": install.utc_now(),
            "campaign_id": CAMPAIGN,
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "application": installed["application"],
            "tooling": {"control_commit": self.control_commit, "control_tree": self.control_tree},
            "canonical_release_tree_sha256": attestation["descriptor_claim"]["canonical_release_tree_sha256"],
            "proof_sha256": proof_sha256,
            "source_image": {
                "image_id": source_image["image_id"],
                "image_reference": source_image["image_reference"],
                "docker_save_archive_sha256": source_image["docker_save_archive_sha256"],
                "docker_save_archive_bytes": source_image["docker_save_archive_bytes"],
            },
            "source_image_object": {
                "object_key": "campaign/raw-image/fixture.age",
                "version_id": "raw-image-version-1",
                "ciphertext_sha256": hashlib.sha256(b"raw image ciphertext").hexdigest(),
                "ciphertext_bytes": len(b"raw image ciphertext"),
                "plaintext_sha256": source_image["docker_save_archive_sha256"],
                "plaintext_bytes": source_image["docker_save_archive_bytes"],
            },
            "source_image_transport": {
                "transport": "private_versioned_age_only",
                "create_only": True,
                "read_back_same_version_id": True,
                "provider_side_sse": False,
            },
            "controller_image_artifacts": {
                "image_bundle_sha256": hashlib.sha256(image_bundle).hexdigest(),
                "image_bundle_bytes": len(image_bundle),
                "image_manifest_sha256": hashlib.sha256(image_manifest).hexdigest(),
                "image_manifest_bytes": len(image_manifest),
                "image_set_sha256": hashlib.sha256(b"image-set").hexdigest(),
                "image_ids_sha256": hashlib.sha256(b"image-ids").hexdigest(),
                "app_image_id": IMAGE_ID,
                "app_image_archive_tag": portable.image_contract.canonical_archive_tag(
                    campaign_id=CAMPAIGN,
                    release_sha=installed["application"]["release_sha"],
                    image_id=IMAGE_ID,
                ),
            },
            "archive_contract": {
                "raw_source_archive_loadability_claimed": False,
                "raw_source_archive_semantics": "exact_bytes_only_unparsed",
                "controller_output_tags_isolated": True,
                "controller_docker_load_invoked": False,
            },
            "controller_public_key_base64": self.controller_public,
            "controller_key_id": portable.public_key_id(self.controller_public),
        }
        adoption = {
            **adoption_unsigned,
            "controller_signature": {
                "algorithm": "ed25519",
                "signature_base64": _signature(self.controller_key, portable.IMAGE_ADOPTION_SIGNATURE_DOMAIN, adoption_unsigned),
            },
        }
        adoption_payload = portable.canonical_json_bytes(adoption) + b"\n"
        def verify(
            *,
            source_role: bytes = source_role_payload,
            image_export: bytes = image_export_payload,
            controller_adoption: bytes = adoption_payload,
        ):
            return portable.verify_composite_webapp_fi_source_provenance(
                source_role_attestation_payload=source_role,
                image_export_receipt_payload=image_export,
                controller_delivery_envelope_payload=delivery_payload,
                signer_enrollment_certificate_payload=certificate_payload,
                static_assets_provenance_payload=static_payload,
                controller_image_adoption_receipt_payload=controller_adoption,
                pinned_source_signing_public_key_base64=self.fi_public,
                pinned_controller_public_key_base64=self.controller_public,
                expected_campaign_id=CAMPAIGN,
                expected_application=installed["application"],
                expected_control_commit=self.control_commit,
                expected_control_tree=self.control_tree,
                expected_canonical_release_tree_sha256=attestation["descriptor_claim"]["canonical_release_tree_sha256"],
                expected_app_image_id=IMAGE_ID,
                expected_app_image_reference=IMAGE_REFERENCE,
                expected_image_bundle_sha256=hashlib.sha256(image_bundle).hexdigest(),
                expected_image_bundle_bytes=len(image_bundle),
                expected_image_manifest_sha256=hashlib.sha256(image_manifest).hexdigest(),
                expected_image_manifest_bytes=len(image_manifest),
                verification_time=install.utc_now(),
            )

        verified = verify()
        self.assertEqual(proof_sha256, verified["authority"]["proof_sha256"])
        self.assertEqual(hashlib.sha256(adoption_payload).hexdigest(), verified["image_adoption"]["image_adoption_receipt_sha256"])

        altered = copy.deepcopy(adoption)
        altered["proof_sha256"]["static_assets_provenance"] = "0" * 64
        unsigned = {key: value for key, value in altered.items() if key != "controller_signature"}
        altered["controller_signature"] = {
            "algorithm": "ed25519",
            "signature_base64": _signature(self.controller_key, portable.IMAGE_ADOPTION_SIGNATURE_DOMAIN, unsigned),
        }
        with self.assertRaisesRegex(portable.SourceProvenanceVerificationError, "proof hashes"):
            verify(controller_adoption=portable.canonical_json_bytes(altered) + b"\n")

        wrong_package = json.loads(source_role_payload)
        wrong_package["package_id"] = "other-package"
        wrong_package_unsigned = {key: value for key, value in wrong_package.items() if key != "source_signature"}
        wrong_package["source_signature"] = {
            "algorithm": "ed25519",
            "signature_base64": _signature(self.fi_key, install.ATTESTATION_SIGNATURE_DOMAIN, wrong_package_unsigned),
        }
        with self.assertRaisesRegex(portable.SourceProvenanceVerificationError, "controller-authorized source installation"):
            verify(source_role=portable.canonical_json_bytes(wrong_package) + b"\n")

        wrong_install_receipt = json.loads(source_role_payload)
        wrong_install_receipt["source_adoption_install_receipt_sha256"] = "0" * 64
        wrong_install_unsigned = {key: value for key, value in wrong_install_receipt.items() if key != "source_signature"}
        wrong_install_receipt["source_signature"] = {
            "algorithm": "ed25519",
            "signature_base64": _signature(self.fi_key, install.ATTESTATION_SIGNATURE_DOMAIN, wrong_install_unsigned),
        }
        with self.assertRaisesRegex(portable.SourceProvenanceVerificationError, "controller-authorized source installation"):
            verify(source_role=portable.canonical_json_bytes(wrong_install_receipt) + b"\n")

        live_tag = copy.deepcopy(adoption)
        live_tag["controller_image_artifacts"]["app_image_archive_tag"] = IMAGE_REFERENCE
        live_tag_unsigned = {key: value for key, value in live_tag.items() if key != "controller_signature"}
        live_tag["controller_signature"] = {
            "algorithm": "ed25519",
            "signature_base64": _signature(self.controller_key, portable.IMAGE_ADOPTION_SIGNATURE_DOMAIN, live_tag_unsigned),
        }
        with self.assertRaisesRegex(portable.SourceProvenanceVerificationError, "application image is invalid"):
            verify(controller_adoption=portable.canonical_json_bytes(live_tag) + b"\n")

        inverted_export = json.loads(image_export_payload)
        attested_at = dt.datetime.strptime(attestation["attested_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
        inverted_export["exported_at"] = (attested_at - dt.timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        inverted_export_unsigned = {key: value for key, value in inverted_export.items() if key != "source_signature"}
        inverted_export["source_signature"] = {
            "algorithm": "ed25519",
            "signature_base64": _signature(self.fi_key, install.IMAGE_EXPORT_SIGNATURE_DOMAIN, inverted_export_unsigned),
        }
        inverted_export_payload = portable.canonical_json_bytes(inverted_export) + b"\n"
        inverted_adoption = copy.deepcopy(adoption)
        inverted_adoption["proof_sha256"]["image_export_receipt"] = hashlib.sha256(inverted_export_payload).hexdigest()
        inverted_adoption_unsigned = {key: value for key, value in inverted_adoption.items() if key != "controller_signature"}
        inverted_adoption["controller_signature"] = {
            "algorithm": "ed25519",
            "signature_base64": _signature(self.controller_key, portable.IMAGE_ADOPTION_SIGNATURE_DOMAIN, inverted_adoption_unsigned),
        }
        with self.assertRaisesRegex(portable.SourceProvenanceVerificationError, "outside the controller certificate window"):
            verify(
                image_export=inverted_export_payload,
                controller_adoption=portable.canonical_json_bytes(inverted_adoption) + b"\n",
            )

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

    def test_prepare_paths_reject_an_unsafe_root_owned_ancestor(self):
        unsafe_parent = self.root / "unsafe-parent"
        unsafe_parent.mkdir(mode=0o700)
        directory = unsafe_parent / "candidate"
        directory.mkdir(mode=0o700)
        private_file = unsafe_parent / "controller.raw"
        private_file.write_bytes(b"x" * 32)
        os.chmod(private_file, 0o600)
        os.chmod(unsafe_parent, 0o777)
        with self.assertRaises(prepare.SourceAdoptionPreparationError):
            prepare._require_root_directory(directory, field="unsafe test directory", private=True)
        with self.assertRaises(prepare.SourceAdoptionPreparationError):
            prepare._require_root_only_file(private_file, field="unsafe test file", maximum_bytes=32)

    def test_opaque_provider_version_ids_and_url_free_persistent_writers(self):
        self.assertEqual(
            prepare._require_version_id(PROVIDER_VERSION_ID, field="test VersionId"),
            PROVIDER_VERSION_ID,
        )
        self.assertEqual(
            install._require_version_id(PROVIDER_VERSION_ID, field="test VersionId"),
            PROVIDER_VERSION_ID,
        )
        self.assertEqual(
            portable._version_id(PROVIDER_VERSION_ID, field="test VersionId"),
            PROVIDER_VERSION_ID,
        )
        for invalid in ("", "null", "NULL", "version:one", "version?one", "version#one", "version one", "version\x1fone"):
            with self.subTest(version_id=repr(invalid)):
                with self.assertRaises(prepare.SourceAdoptionPreparationError):
                    prepare._require_version_id(invalid, field="test VersionId")
                with self.assertRaises(install.SourceAdoptionInstallError):
                    install._require_version_id(invalid, field="test VersionId")
                with self.assertRaises(portable.SourceProvenanceVerificationError):
                    portable._version_id(invalid, field="test VersionId")
        metadata = self.root / "metadata"
        metadata.mkdir(mode=0o700)
        with self.assertRaises(prepare.SourceAdoptionPreparationError):
            prepare._write_new_private_json(
                metadata / "prepare.json",
                {"control": "HTTPS://example.invalid/object"},
                field="test preparation metadata",
            )
        self.assertFalse((metadata / "prepare.json").exists())
        with self.assertRaises(install.SourceAdoptionInstallError):
            install._write_new_private_json(
                metadata / "install.json",
                {"control": "HTTPS://example.invalid/object"},
            )
        self.assertFalse((metadata / "install.json").exists())
        with self.assertRaises(portable.SourceProvenanceVerificationError):
            portable._parse(
                portable.canonical_json_bytes({"control": "HTTPS://example.invalid/object"}) + b"\n",
                field="test portable metadata",
            )

    def test_projection_root_mode_and_mount_bypasses_block_without_data_capture(self):
        installed = self._install()
        runtime, role_path, ssh_public, static_path, certificate = self._runtime_and_config(installed)
        self._enroll(installed, role_path, ssh_public, certificate)
        os.chmod(runtime / "api", 0o777)
        with self.assertRaises(install.SourceAdoptionInstallError):
            install.verify_canonical_runtime_projection(
                candidate=Path(installed["candidate"]),
                runtime_source_root=runtime,
                expected_application=installed["application"],
            )
        os.chmod(runtime / "api", 0o755)
        base_containers = self._container_records(runtime)
        bypasses = (
            ("fixture_app", "/app", "bind", str(self.root / "bypass-app")),
            ("fixture_app", "/", "bind", str(self.root / "bypass-root")),
            ("fixture_app", "/app/api/override", "bind", str(self.root / "bypass-api")),
            ("fixture_app", "/app/certs/private", "bind", str(self.root / "bypass-certs-child")),
            ("fixture_app", install.RUNTIME_EXTERNAL_NON_PAYLOAD_MOUNT_TARGET, "volume", "fixture-certs"),
            ("fixture_sync", install.RUNTIME_EXTERNAL_NON_PAYLOAD_MOUNT_TARGET, "bind", str(self.external_certs)),
        )
        for number, (container_name, destination, mount_type, source) in enumerate(bypasses, 1):
            with self.subTest(destination=destination, mount_type=mount_type):
                containers = copy.deepcopy(base_containers)
                containers[container_name]["mounts"].append(
                    {"type": mount_type, "source": source, "destination": destination, "read_only": False}
                )
                containers[container_name]["mounts"].sort(
                    key=lambda item: (item["destination"], item["type"], str(item["source"]))
                )
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
                            attestation_id=f"attestation-bypass-{number}",
                            apply=True,
                        )

    def test_portable_verifier_rejects_projection_bypass_and_certificate_overlap(self):
        installed = self._install()
        runtime, role_path, ssh_public, static_path, certificate = self._runtime_and_config(installed)
        self._enroll(installed, role_path, ssh_public, certificate)
        attestation = self._attest(
            installed,
            runtime,
            role_path,
            ssh_public,
            static_path,
            certificate,
            attestation_id="attestation-portable-bypass",
        )
        original = json.loads(Path(attestation["attestation_path"]).read_text(encoding="utf-8"))
        cases = (
            ("/app", str(self.root / "portable-bypass-app")),
            ("/", str(self.root / "portable-bypass-root")),
            ("/app/api/override", str(self.root / "portable-bypass-api")),
            ("/app/certs/override", str(self.root / "portable-bypass-certs")),
        )
        for destination, source in cases:
            with self.subTest(destination=destination):
                proof = copy.deepcopy(original)
                proof["containers"]["application"]["mounts"].append(
                    {"type": "bind", "source": source, "destination": destination, "read_only": False}
                )
                proof["containers"]["application"]["mounts"].sort(
                    key=lambda item: (item["destination"], item["type"], str(item["source"]))
                )
                unsigned = {key: value for key, value in proof.items() if key != "source_signature"}
                proof["source_signature"] = {
                    "algorithm": "ed25519",
                    "signature_base64": _signature(self.fi_key, install.ATTESTATION_SIGNATURE_DOMAIN, unsigned),
                }
                with self.assertRaises(portable.SourceProvenanceVerificationError):
                    portable.verify_source_role_attestation_payload(
                        payload=portable.canonical_json_bytes(proof) + b"\n",
                        pinned_source_signing_public_key_base64=self.fi_public,
                        expected_campaign_id=CAMPAIGN,
                        expected_application={"release_sha": self.release, "expected_alembic_revision": REVISION},
                        expected_control_commit=self.control_commit,
                        expected_canonical_release_tree_sha256=attestation["descriptor_claim"]["canonical_release_tree_sha256"],
                        expected_app_image_id=IMAGE_ID,
                        expected_app_image_reference=IMAGE_REFERENCE,
                        verification_time=install.utc_now(),
                    )
        proof = copy.deepcopy(original)
        for mount in proof["containers"]["application"]["mounts"]:
            if mount["destination"] == install.RUNTIME_EXTERNAL_NON_PAYLOAD_MOUNT_TARGET:
                mount["source"] = str(runtime)
        unsigned = {key: value for key, value in proof.items() if key != "source_signature"}
        proof["source_signature"] = {
            "algorithm": "ed25519",
            "signature_base64": _signature(self.fi_key, install.ATTESTATION_SIGNATURE_DOMAIN, unsigned),
        }
        with self.assertRaises(portable.SourceProvenanceVerificationError):
            portable.verify_source_role_attestation_payload(
                payload=portable.canonical_json_bytes(proof) + b"\n",
                pinned_source_signing_public_key_base64=self.fi_public,
                expected_campaign_id=CAMPAIGN,
                expected_application={"release_sha": self.release, "expected_alembic_revision": REVISION},
                expected_control_commit=self.control_commit,
                expected_canonical_release_tree_sha256=attestation["descriptor_claim"]["canonical_release_tree_sha256"],
                expected_app_image_id=IMAGE_ID,
                expected_app_image_reference=IMAGE_REFERENCE,
                verification_time=install.utc_now(),
            )

    def test_exact_byte_export_plan_never_invokes_docker_and_requires_revalidation_inputs(self):
        installed = self._install()
        runtime, role_path, ssh_public, static_path, certificate = self._runtime_and_config(installed)
        self._enroll(installed, role_path, ssh_public, certificate)
        attestation = self._attest(
            installed,
            runtime,
            role_path,
            ssh_public,
            static_path,
            certificate,
            attestation_id="attestation-export-plan",
        )
        parent = self.root / "exports"
        parent.mkdir(mode=0o700)
        with (
            patch.object(install, "_inspect_container", side_effect=AssertionError("export plan touched Docker")),
            patch.object(install, "_inspect_image_storage_bytes", side_effect=AssertionError("export plan inspected image storage")),
        ):
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
        self.assertEqual(
            {
                "before_destination_mkdir": True,
                "trusted_local_image_storage_size": True,
                "required_free_bytes_formula": "2x_inspected_image_storage_bytes_plus_1073741824",
            },
            plan["local_capacity_admission"],
        )
        self.assertFalse(plan["loadability_claimed"])

    def test_image_export_capacity_reserves_double_trusted_storage_plus_margin(self):
        parent = self.root / "exports-capacity"
        parent.mkdir(mode=0o700)
        destination = parent / "candidate"
        storage_bytes = 32 * 1024 * 1024
        required_free_bytes = storage_bytes * install.IMAGE_EXPORT_CAPACITY_MULTIPLIER + install.IMAGE_EXPORT_CAPACITY_MARGIN_BYTES

        with patch.object(install, "_inspect_image_storage_bytes", return_value=storage_bytes):
            admission = install._preflight_image_export_capacity(
                destination=destination,
                expected_image_id=IMAGE_ID,
                disk_usage=lambda _path: SimpleNamespace(free=required_free_bytes),
            )

        self.assertFalse(destination.exists())
        self.assertEqual(storage_bytes, admission["image_storage_bytes"])
        self.assertEqual(required_free_bytes, admission["required_free_bytes"])
        self.assertEqual(required_free_bytes, admission["available_free_bytes"])
        self.assertEqual(install.IMAGE_EXPORT_CAPACITY_MULTIPLIER, admission["reserve_multiplier"])
        self.assertEqual(install.IMAGE_EXPORT_CAPACITY_MARGIN_BYTES, admission["reserve_margin_bytes"])

    def test_image_export_capacity_rejects_invalid_trusted_storage_size_without_writing(self):
        parent = self.root / "exports-capacity-invalid"
        parent.mkdir(mode=0o700)
        destination = parent / "candidate"

        with patch.object(install, "_inspect_image_storage_bytes", return_value=0):
            with self.assertRaisesRegex(install.SourceAdoptionInstallError, "source image storage size is invalid"):
                install._preflight_image_export_capacity(
                    destination=destination,
                    expected_image_id=IMAGE_ID,
                    disk_usage=lambda _path: SimpleNamespace(free=10 * install.IMAGE_EXPORT_CAPACITY_MARGIN_BYTES),
                )

        self.assertFalse(destination.exists())

    def test_export_rejects_insufficient_capacity_before_candidate_mkdir_or_docker_save(self):
        installed = self._install()
        runtime, role_path, ssh_public, static_path, certificate = self._runtime_and_config(installed)
        self._enroll(installed, role_path, ssh_public, certificate)
        attestation = self._attest(
            installed,
            runtime,
            role_path,
            ssh_public,
            static_path,
            certificate,
            attestation_id="attestation-export-capacity",
        )
        before = {
            **attestation["runtime_claim"],
            "active_application_image": attestation["image_claim"]["active_application_image"],
        }
        parent = self.root / "exports-capacity-failure"
        parent.mkdir(mode=0o700)
        destination = parent / "candidate"

        with (
            patch.object(install, "_revalidate_export_runtime", return_value=before) as revalidate,
            patch.object(install, "_inspect_image_storage_bytes", return_value=1024),
            patch.object(install.shutil, "disk_usage", return_value=SimpleNamespace(free=0)),
            patch.object(install, "_create_directory", side_effect=AssertionError("capacity failure reached mkdir")),
            patch.object(install, "_export_exact_docker_save_bytes", side_effect=AssertionError("capacity failure reached docker save")),
        ):
            with self.assertRaisesRegex(install.SourceAdoptionInstallError, "insufficient free space for exact WebApp-FI image export"):
                install.export_actual_fi_image(
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
                    expected_application=installed["application"],
                    expected_control_commit=self.control_commit,
                    expected_canonical_release_tree_sha256=attestation["descriptor_claim"]["canonical_release_tree_sha256"],
                    expected_app_image_id=IMAGE_ID,
                    expected_app_image_reference=IMAGE_REFERENCE,
                    destination=destination,
                    export_id="export-capacity-failure",
                    apply=True,
                )

        self.assertEqual(1, revalidate.call_count)
        self.assertFalse(destination.exists())

    def test_export_rejects_live_runtime_destination_before_revalidation_mkdir_or_docker_save(self):
        installed = self._install()
        runtime, role_path, ssh_public, static_path, certificate = self._runtime_and_config(installed)
        self._enroll(installed, role_path, ssh_public, certificate)
        attestation = self._attest(
            installed,
            runtime,
            role_path,
            ssh_public,
            static_path,
            certificate,
            attestation_id="attestation-export-runtime-destination",
        )
        destination = runtime / "must-not-create-export"
        with (
            patch.object(install, "_revalidate_export_runtime", side_effect=AssertionError("runtime destination reached revalidation")),
            patch.object(install, "_create_directory", side_effect=AssertionError("runtime destination reached mkdir")),
            patch.object(install, "_export_exact_docker_save_bytes", side_effect=AssertionError("runtime destination reached docker save")),
        ):
            with self.assertRaisesRegex(install.SourceAdoptionInstallError, "outside the live source runtime"):
                install.export_actual_fi_image(
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
                    expected_application=installed["application"],
                    expected_control_commit=self.control_commit,
                    expected_canonical_release_tree_sha256=attestation["descriptor_claim"]["canonical_release_tree_sha256"],
                    expected_app_image_id=IMAGE_ID,
                    expected_app_image_reference=IMAGE_REFERENCE,
                    destination=destination,
                    export_id="export-runtime-destination",
                    apply=True,
                )
        self.assertFalse(destination.exists())

    def test_export_rejects_current_linked_runtime_alias_before_mkdir_or_docker_save(self):
        installed = self._install()
        runtime, role_path, ssh_public, static_path, certificate = self._runtime_and_config(installed)
        self._enroll(installed, role_path, ssh_public, certificate)
        attestation = self._attest(
            installed,
            runtime,
            role_path,
            ssh_public,
            static_path,
            certificate,
            attestation_id="attestation-export-runtime-alias",
        )
        current_alias = self.root / "current-linked-runtime"
        current_alias.mkdir(mode=0o700)
        destination = current_alias / "must-not-create-export"
        original_samestat = os.path.samestat
        alias_state = current_alias.stat()
        runtime_state = runtime.stat()

        def same_stat(left, right):
            if {left.st_ino, right.st_ino} == {alias_state.st_ino, runtime_state.st_ino} and {left.st_dev, right.st_dev} == {alias_state.st_dev, runtime_state.st_dev}:
                return True
            return original_samestat(left, right)

        with (
            patch.object(install.os.path, "samestat", side_effect=same_stat),
            patch.object(install, "_revalidate_export_runtime", side_effect=AssertionError("runtime alias reached revalidation")),
            patch.object(install, "_create_directory", side_effect=AssertionError("runtime alias reached mkdir")),
            patch.object(install, "_export_exact_docker_save_bytes", side_effect=AssertionError("runtime alias reached docker save")),
        ):
            with self.assertRaisesRegex(install.SourceAdoptionInstallError, "outside the live source runtime"):
                install.export_actual_fi_image(
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
                    expected_application=installed["application"],
                    expected_control_commit=self.control_commit,
                    expected_canonical_release_tree_sha256=attestation["descriptor_claim"]["canonical_release_tree_sha256"],
                    expected_app_image_id=IMAGE_ID,
                    expected_app_image_reference=IMAGE_REFERENCE,
                    destination=destination,
                    export_id="export-runtime-alias",
                    apply=True,
                )
        self.assertFalse(destination.exists())

    def test_export_rejects_bind_alias_of_runtime_child_before_revalidation_mkdir_or_docker_save(self):
        installed = self._install()
        runtime, role_path, ssh_public, static_path, certificate = self._runtime_and_config(installed)
        self._enroll(installed, role_path, ssh_public, certificate)
        attestation = self._attest(
            installed,
            runtime,
            role_path,
            ssh_public,
            static_path,
            certificate,
            attestation_id="attestation-export-runtime-child-alias",
        )
        runtime_child = runtime / "api"
        bind_alias = self.root / "runtime-child-bind-alias"
        bind_alias.mkdir(mode=0o700)
        destination_parent = bind_alias / "nested"
        destination_parent.mkdir(mode=0o700)
        destination = destination_parent / "must-not-create-export"
        runtime_device = (os.major(runtime.stat().st_dev), os.minor(runtime.stat().st_dev))
        mountinfo = (
            install._MountInfoRecord(
                mount_id=1,
                parent_id=0,
                device=runtime_device,
                root=PurePosixPath("/"),
                mount_point=Path("/"),
            ),
            install._MountInfoRecord(
                mount_id=2,
                parent_id=1,
                device=runtime_device,
                root=PurePosixPath(str(runtime_child)),
                mount_point=bind_alias,
            ),
        )
        with (
            patch.object(install, "_read_mountinfo_records", return_value=mountinfo),
            patch.object(install, "_revalidate_export_runtime", side_effect=AssertionError("runtime child alias reached revalidation")),
            patch.object(install, "_create_directory", side_effect=AssertionError("runtime child alias reached mkdir")),
            patch.object(install, "_export_exact_docker_save_bytes", side_effect=AssertionError("runtime child alias reached docker save")),
        ):
            with self.assertRaisesRegex(install.SourceAdoptionInstallError, "outside the live source runtime"):
                install.export_actual_fi_image(
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
                    expected_application=installed["application"],
                    expected_control_commit=self.control_commit,
                    expected_canonical_release_tree_sha256=attestation["descriptor_claim"]["canonical_release_tree_sha256"],
                    expected_app_image_id=IMAGE_ID,
                    expected_app_image_reference=IMAGE_REFERENCE,
                    destination=destination,
                    export_id="export-runtime-child-alias",
                    apply=True,
                )
        self.assertFalse(destination.exists())

    def test_export_rejects_source_only_enrollment_claim_drift_before_docker(self):
        installed = self._install()
        runtime, role_path, ssh_public, static_path, certificate = self._runtime_and_config(installed)
        self._enroll(installed, role_path, ssh_public, certificate)
        attestation = self._attest(
            installed,
            runtime,
            role_path,
            ssh_public,
            static_path,
            certificate,
            attestation_id="attestation-enrollment-drift",
        )
        attestation_path = Path(attestation["attestation_path"])
        altered = json.loads(attestation_path.read_text(encoding="utf-8"))
        altered["source_signer_enrollment"]["controller_key_id"] = "ed25519-sha256:" + "0" * 64
        unsigned = {key: value for key, value in altered.items() if key != "source_signature"}
        altered["source_signature"] = {
            "algorithm": "ed25519",
            "signature_base64": _signature(self.fi_key, install.ATTESTATION_SIGNATURE_DOMAIN, unsigned),
        }
        _canonical_private_json(attestation_path, altered)
        parent = self.root / "exports-enrollment-drift"
        parent.mkdir(mode=0o700)
        with patch.object(install, "_inspect_container", side_effect=AssertionError("enrollment mismatch touched Docker")):
            with self.assertRaisesRegex(install.SourceAdoptionInstallError, "signer enrollment differs"):
                install.export_actual_fi_image(
                    attestation=attestation_path,
                    source_role_config=role_path,
                    signer_enrollment_receipt=Path(installed["candidate"]) / "enrollments" / f"{CAMPAIGN}.json",
                    signer_enrollment_certificate=certificate,
                    ssh_host_public_key_file=ssh_public,
                    runtime_source_root=runtime,
                    static_assets_descriptor=static_path,
                    pinned_controller_public_key_base64=self.controller_public,
                    pinned_source_signing_public_key_base64=self.fi_public,
                    expected_campaign_id=CAMPAIGN,
                    expected_application=installed["application"],
                    expected_control_commit=self.control_commit,
                    expected_canonical_release_tree_sha256=attestation["descriptor_claim"]["canonical_release_tree_sha256"],
                    expected_app_image_id=IMAGE_ID,
                    expected_app_image_reference=IMAGE_REFERENCE,
                    destination=parent / "candidate",
                    export_id="export-enrollment-drift",
                    apply=False,
                )

    def test_exact_byte_export_invokes_only_docker_save_and_hashes_unparsed_bytes(self):
        executable = _private_file(self.root / "tools" / "docker", b"fixture trusted docker\n")
        os.chmod(executable, 0o700)
        destination = self.root / "exports" / "archive.tar"
        destination.parent.mkdir(mode=0o700)
        observed_commands = []

        def fake_run(command, **_kwargs):
            observed_commands.append(command)
            output = Path(command[command.index("--output") + 1])
            output.write_bytes(b"opaque docker save byte stream")
            os.chmod(output, 0o600)
            return subprocess.CompletedProcess(command, 0)

        with patch.object(install, "_require_trusted_executable", return_value=executable), patch.object(install.subprocess, "run", side_effect=fake_run):
            exported = install._export_exact_docker_save_bytes(archive=destination, expected_image_id=IMAGE_ID)
        self.assertEqual(observed_commands, [[str(executable), "save", "--output", str(destination), IMAGE_ID]])
        self.assertNotIn("load", observed_commands[0])
        self.assertEqual(exported["docker_save_archive_sha256"], hashlib.sha256(destination.read_bytes()).hexdigest())
        self.assertEqual(exported["docker_save"]["archive_semantics"], "exact_bytes_only_unparsed")
        self.assertEqual(exported["docker_save"]["archive_layout"], "not_inspected")
        self.assertFalse(exported["docker_save"]["manifest_semantics_attested"])
        self.assertFalse(exported["docker_save"]["loadability_claimed"])

    def test_export_revalidates_before_and_after_and_withholds_receipt_on_drift(self):
        installed = self._install()
        runtime, role_path, ssh_public, static_path, certificate = self._runtime_and_config(installed)
        self._enroll(installed, role_path, ssh_public, certificate)
        attestation = self._attest(
            installed,
            runtime,
            role_path,
            ssh_public,
            static_path,
            certificate,
            attestation_id="attestation-export-drift",
        )
        before = {
            **attestation["runtime_claim"],
            "active_application_image": attestation["image_claim"]["active_application_image"],
        }
        after = copy.deepcopy(before)
        after["active_application_image"]["repo_tags"] = [IMAGE_REFERENCE, "registry.example/gold-trade/webapp:changed"]
        parent = self.root / "exports-drift"
        parent.mkdir(mode=0o700)

        def fake_export(*, archive, expected_image_id):
            archive.write_bytes(b"retained untrusted exact bytes")
            os.chmod(archive, 0o600)
            return {
                "docker_save_archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "docker_save_archive_bytes": archive.stat().st_size,
                "docker_save": {
                    "command": ["docker", "save", "--output", archive.name, expected_image_id],
                    "docker_executable_sha256": hashlib.sha256(b"docker").hexdigest(),
                    "docker_executable_bytes": len(b"docker"),
                    "archive_semantics": "exact_bytes_only_unparsed",
                    "archive_layout": "not_inspected",
                    "manifest_semantics_attested": False,
                    "docker_load_invoked": False,
                    "loadability_claimed": False,
                },
            }

        with (
            patch.object(install, "_revalidate_export_runtime", side_effect=[before, after]) as revalidate,
            patch.object(install, "_inspect_image_storage_bytes", return_value=1024),
            patch.object(
                install.shutil,
                "disk_usage",
                return_value=SimpleNamespace(
                    free=1024 * install.IMAGE_EXPORT_CAPACITY_MULTIPLIER + install.IMAGE_EXPORT_CAPACITY_MARGIN_BYTES
                ),
            ),
            patch.object(install, "_export_exact_docker_save_bytes", side_effect=fake_export),
        ):
            with self.assertRaisesRegex(install.SourceAdoptionInstallError, "changed during exact-byte export"):
                install.export_actual_fi_image(
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
                    expected_application=installed["application"],
                    expected_control_commit=self.control_commit,
                    expected_canonical_release_tree_sha256=attestation["descriptor_claim"]["canonical_release_tree_sha256"],
                    expected_app_image_id=IMAGE_ID,
                    expected_app_image_reference=IMAGE_REFERENCE,
                    destination=parent / "candidate",
                    export_id="export-drift",
                    apply=True,
                )
        self.assertEqual(revalidate.call_count, 2)
        self.assertFalse((parent / "candidate" / "image-export-receipt.json").exists())


if __name__ == "__main__":
    unittest.main()
