import base64
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = "source-signer-20260730"
REVISION = "f2c7d8e9a0b1"
RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
VERSION_ID = "3/L4kqtJlcpXroDTDmJ+3DcJKZBjjfM7m1E7S="


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bootstrap = _load_module(
    "bootstrap_webapp_fi_source_signer_test",
    ROOT / "scripts" / "bootstrap_webapp_fi_source_signer.py",
)
prepare = _load_module(
    "prepare_webapp_fi_source_adoption_for_source_signer_bootstrap_test",
    ROOT / "scripts" / "prepare_webapp_fi_source_adoption.py",
)
install = _load_module(
    "install_webapp_fi_source_adoption_for_source_signer_bootstrap_test",
    ROOT / "scripts" / "install_webapp_fi_source_adoption.py",
)
issuer = _load_module(
    "manage_webapp_fi_source_signer_enrollment_for_source_signer_bootstrap_test",
    ROOT / "scripts" / "manage_webapp_fi_source_signer_enrollment.py",
)


def _private_file(path, payload, *, mode=0o600):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    path.write_bytes(payload)
    os.chmod(path, mode)
    return path


def _canonical_private_json(path, value):
    return _private_file(path, install.canonical_json_bytes(value) + b"\n")


def _git(repository, *arguments):
    subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _new_repository(path):
    path.mkdir(mode=0o700)
    _git(path, "init")
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "user.name", "FI source signer bootstrap test")
    return path


def _commit(repository):
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "fixture")
    return subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()


def _key_material():
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return key, raw, base64.b64encode(public).decode("ascii")


class WebAppFiSourceSignerBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        os.chmod(self.root, 0o700)
        self.control = _new_repository(self.root / "control")
        self.application = _new_repository(self.root / "application")
        for relative in prepare.SOURCE_PAYLOAD_FILES:
            target = self.control / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        self.control_commit = _commit(self.control)
        for relative in install.RUNTIME_CODE_PROJECTION_RELATIVES:
            target = self.application / relative
            if "." in relative:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("fixture " + relative + "\n", encoding="ascii")
            else:
                target.mkdir(parents=True, exist_ok=True)
                (target / "fixture.py").write_text("fixture " + relative + "\n", encoding="ascii")
        self.release = _commit(self.application)
        _, controller_raw, self.controller_public = _key_material()
        self.controller_private = _private_file(self.root / "keys" / "controller.raw", controller_raw)
        self.package_directory = self.root / "packages" / "package-one"
        (self.root / "packages").mkdir(mode=0o700)
        self.prepared = prepare.prepare_source_adoption_package(
            source_repository=self.control,
            application_source_repository=self.application,
            control_commit=self.control_commit,
            application_release_sha=self.release,
            expected_alembic_revision=REVISION,
            package_id="package-one",
            destination=self.package_directory,
            apply=True,
        )
        self.delivery_object = {
            "object_key": "source-adoption/package-one.age",
            "version_id": VERSION_ID,
            "ciphertext_sha256": hashlib.sha256(b"fixture ciphertext").hexdigest(),
            "ciphertext_bytes": len(b"fixture ciphertext"),
            "plaintext_sha256": self.prepared["archive_sha256"],
            "plaintext_bytes": self.prepared["archive_bytes"],
        }
        delivery_unsigned = {
            "schema": install.DELIVERY_RECEIPT_SCHEMA,
            "status": "received",
            "source_site": install.PACKAGE_SOURCE_SITE,
            "destination_site": install.PACKAGE_DESTINATION_SITE,
            "control_commit": self.control_commit,
            "package_id": "package-one",
            "object": self.delivery_object,
            "archive": {
                "sha256": self.prepared["archive_sha256"],
                "bytes": self.prepared["archive_bytes"],
            },
        }
        self.delivery_receipt = _canonical_private_json(
            self.root / "inbox" / "delivery.json",
            {
                **delivery_unsigned,
                "receipt_sha256": install.sha256_bytes(install.canonical_json_bytes(delivery_unsigned)),
            },
        )
        self.envelope = self.root / "inbox" / "delivery-envelope.json"
        prepare.sign_delivery_envelope(
            package_directory=self.package_directory,
            preparation_receipt=self.package_directory / prepare.PREPARATION_RECEIPT_NAME,
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
        staging = self.root / "staging"
        staging.mkdir(mode=0o700)
        self.installed = install.install_source_adoption(
            archive=Path(self.prepared["archive_path"]),
            preparation_receipt=self.package_directory / prepare.PREPARATION_RECEIPT_NAME,
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
        self.campaign_root = self.root / "campaigns"
        self.campaign_root.mkdir(mode=0o700)
        self.campaign_directory = self.campaign_root / CAMPAIGN
        self.campaign_directory.mkdir(mode=0o700)
        self.ssh_public = _private_file(
            self.root / "keys" / "ssh-host-ed25519.pub",
            b"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest fixture\n",
        )
        self.legacy_key = _private_file(self.root / "legacy" / "webapp-fi-source-ed25519.raw", b"legacy key")

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, *, apply):
        candidate_script = Path(self.installed["candidate"]) / bootstrap.THIS_SCRIPT_RELATIVE
        with (
            patch.object(bootstrap, "CAMPAIGN_ROOT", self.campaign_root),
            patch.object(bootstrap, "__file__", str(candidate_script)),
        ):
            return bootstrap.bootstrap_source_signer(
                install_receipt=Path(self.installed["receipt_path"]),
                ssh_host_public_key_file=self.ssh_public,
                apply=apply,
            )

    def _paths(self):
        fi_directory = self.campaign_directory / bootstrap.FI_SOURCE_SIGNER_DIRECTORY
        return fi_directory, fi_directory / bootstrap.FI_SOURCE_SIGNER_KEY_NAME, fi_directory / bootstrap.FI_SOURCE_SIGNER_RECEIPT_NAME

    def _campaign_binding(self):
        application_tree = subprocess.check_output(
            ["git", "-C", str(self.application), "rev-parse", self.release + "^{tree}"],
            text=True,
        ).strip()
        control_tree = subprocess.check_output(
            ["git", "-C", str(self.control), "rev-parse", self.control_commit + "^{tree}"],
            text=True,
        ).strip()
        path = (
            self.root
            / "controller-campaigns"
            / CAMPAIGN
            / "webapp-fi-source"
            / "campaign-binding.json"
        )
        unsigned = {
            "schema": "gold-trade-webapp-fi-source-campaign-binding-v1",
            "status": "bound",
            "campaign_id": CAMPAIGN,
            "application": {
                "release_sha": self.release,
                "release_tree": application_tree,
                "expected_alembic_revision": REVISION,
            },
            "tooling": {"control_commit": self.control_commit, "control_tree": control_tree},
        }
        return _canonical_private_json(
            path,
            {**unsigned, "binding_sha256": hashlib.sha256(install.canonical_json_bytes(unsigned)).hexdigest()},
        )

    def test_plan_is_non_mutating_then_apply_creates_bound_key_and_nonsecret_receipt(self):
        fi_directory, key_path, receipt_path = self._paths()
        plan = self._run(apply=False)
        self.assertEqual("planned", plan["status"])
        self.assertEqual(str(key_path), plan["source_signing_private_key_file"])
        self.assertEqual(str(receipt_path), plan["receipt_path"])
        self.assertFalse(fi_directory.exists())
        self.assertFalse(key_path.exists())
        self.assertFalse(receipt_path.exists())

        result = self._run(apply=True)
        self.assertEqual("created", result["status"])
        self.assertTrue(result["private_key_created"])
        self.assertEqual(CAMPAIGN, result["campaign_id"])
        self.assertEqual(str(key_path), result["source_signing_private_key_file"])
        self.assertEqual(str(receipt_path), result["receipt_path"])
        self.assertEqual(hashlib.sha256(self.ssh_public.read_bytes()).hexdigest(), result["fi_ssh_host_public_key_sha256"])
        self.assertEqual(self.installed["receipt_sha256"], result["source_adoption_install_receipt_sha256"])
        self.assertEqual(0o700, stat.S_IMODE(fi_directory.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(key_path.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(receipt_path.stat().st_mode))

        raw = key_path.read_bytes()
        self.assertEqual(32, len(raw))
        public = Ed25519PrivateKey.from_private_bytes(raw).public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        self.assertEqual(base64.b64encode(public).decode("ascii"), result["source_signing_public_key_base64"])
        self.assertEqual(install._public_key_id(result["source_signing_public_key_base64"]), result["source_signing_key_id"])

        receipt_payload = receipt_path.read_bytes()
        receipt = json.loads(receipt_payload)
        self.assertEqual(bootstrap.SOURCE_SIGNER_BOOTSTRAP_RECEIPT_SCHEMA, receipt["schema"])
        self.assertEqual(CAMPAIGN, receipt["campaign_id"])
        self.assertEqual(self.installed["receipt_sha256"], receipt["source_adoption"]["install_receipt_sha256"])
        self.assertEqual(result["source_signing_public_key_base64"], receipt["source_signer"]["public_key_base64"])
        self.assertEqual(result["source_signing_key_id"], receipt["source_signer"]["key_id"])
        self.assertEqual(receipt, result["source_signing_receipt"])
        unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        self.assertEqual(bootstrap.sha256_bytes(bootstrap.canonical_json_bytes(unsigned)), receipt["receipt_sha256"])
        self.assertNotIn(raw, receipt_payload)
        self.assertNotIn(base64.b64encode(raw), receipt_payload)
        self.assertNotIn(raw, bootstrap.canonical_json_bytes(result))
        self.assertNotIn(base64.b64encode(raw), bootstrap.canonical_json_bytes(result))
        self.assertEqual(b"legacy key", self.legacy_key.read_bytes())

    def test_existing_key_or_receipt_is_retained_and_never_reused(self):
        _, key_path, receipt_path = self._paths()
        self._run(apply=True)
        original_key = key_path.read_bytes()
        original_receipt = receipt_path.read_bytes()
        with self.assertRaisesRegex(bootstrap.SourceSignerBootstrapError, "reuse or overwrite"):
            self._run(apply=True)
        self.assertEqual(original_key, key_path.read_bytes())
        self.assertEqual(original_receipt, receipt_path.read_bytes())

    def test_created_public_material_is_accepted_by_the_controller_enrollment_issuer(self):
        result = self._run(apply=True)
        issued = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        output = self.root / "issuer" / "certificate.json"
        output.parent.mkdir(mode=0o700)
        bootstrap_receipt = _private_file(
            self.root / "controller-inputs" / "fi-source-signer-bootstrap.json",
            Path(result["receipt_path"]).read_bytes(),
        )
        fi_install_control_receipt = _private_file(
            self.root / "controller-inputs" / "fi-source-adoption-install.json",
            Path(self.installed["receipt_path"]).read_bytes(),
        )
        with patch.object(issuer, "FI_SOURCE_SIGNER_CAMPAIGN_ROOT", self.campaign_root):
            certificate = issuer.issue_source_signer_enrollment_certificate(
                package_directory=self.package_directory,
                preparation_receipt=self.package_directory / prepare.PREPARATION_RECEIPT_NAME,
                delivery_envelope=self.envelope,
                campaign_binding=self._campaign_binding(),
                fi_install_control_receipt=fi_install_control_receipt,
                bootstrap_signer_receipt=bootstrap_receipt,
                pinned_fi_ssh_host_public_key_file=self.ssh_public,
                certificate_id="source-signer-bootstrap-certificate",
                operation_id="source-signer-bootstrap-operation",
                issued_at=issued.strftime("%Y-%m-%dT%H:%M:%SZ"),
                not_before=issued.strftime("%Y-%m-%dT%H:%M:%SZ"),
                not_after=(issued + dt.timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                controller_signing_private_key=self.controller_private,
                output=output,
                apply=True,
            )
        self.assertEqual("issued", certificate["status"])
        self.assertEqual(result["source_signing_key_id"], certificate["source_signing_key_id"])
        self.assertTrue(output.is_file())
        self.assertEqual(0o600, stat.S_IMODE(output.stat().st_mode))

    def test_tampered_installed_helper_is_rejected_before_any_key_is_created(self):
        _, key_path, receipt_path = self._paths()
        candidate_script = Path(self.installed["candidate"]) / bootstrap.THIS_SCRIPT_RELATIVE
        candidate_script.write_bytes(candidate_script.read_bytes() + b"\n# tampered\n")
        os.chmod(candidate_script, 0o600)
        with self.assertRaisesRegex(bootstrap.SourceSignerBootstrapError, "bootstrap hash changed"):
            self._run(apply=True)
        self.assertFalse(key_path.exists())
        self.assertFalse(receipt_path.exists())

    def test_unsafe_ssh_public_key_is_rejected_before_any_key_is_created(self):
        _, key_path, receipt_path = self._paths()
        os.chmod(self.ssh_public, 0o666)
        with self.assertRaisesRegex(bootstrap.SourceSignerBootstrapError, "SSH host public key is unsafe"):
            self._run(apply=True)
        self.assertFalse(key_path.exists())
        self.assertFalse(receipt_path.exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
