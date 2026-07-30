import base64
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = "source-adoption-20260730"
REVISION = "f2c7d8e9a0b1"
RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
VERSION_ID = "3/L4kqtJlcpXroDTDmJ+3DcJKZBjjfM7m1E7S="


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


issuer = _load_module(
    "manage_webapp_fi_source_signer_enrollment_test",
    ROOT / "scripts" / "manage_webapp_fi_source_signer_enrollment.py",
)
prepare = _load_module(
    "prepare_webapp_fi_source_adoption_for_signer_issuer_test",
    ROOT / "scripts" / "prepare_webapp_fi_source_adoption.py",
)
install = _load_module(
    "install_webapp_fi_source_adoption_for_signer_issuer_test",
    ROOT / "scripts" / "install_webapp_fi_source_adoption.py",
)
portable = _load_module(
    "verify_webapp_fi_source_provenance_for_signer_issuer_test",
    ROOT / "scripts" / "verify_webapp_fi_source_provenance.py",
)
bootstrap = _load_module(
    "bootstrap_webapp_fi_source_signer_for_signer_issuer_test",
    ROOT / "scripts" / "bootstrap_webapp_fi_source_signer.py",
)
fixtures = _load_module(
    "source_stage_fixture_helpers_for_signer_issuer_test",
    ROOT / "tests" / "source_stage_fixture_helpers.py",
)


def _private_file(path, payload):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    path.write_bytes(payload)
    os.chmod(path, 0o600)
    return path


def _canonical_private_json(path, value):
    return _private_file(path, install.canonical_json_bytes(value) + b"\n")


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _new_repo(path):
    path.mkdir(mode=0o700)
    _git(path, "init")
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "user.name", "Source signer issuer test")
    return path


def _commit(repo):
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fixture")
    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()


def _tree(repo, commit):
    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", commit + "^{tree}"], text=True).strip()


def _key_material():
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return key, raw, base64.b64encode(public).decode("ascii")


class SourceSignerEnrollmentIssuerTests(unittest.TestCase):
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
        for relative in install.RUNTIME_CODE_PROJECTION_RELATIVES:
            target = self.application / relative
            if "." in relative:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("fixture " + relative + "\n", encoding="ascii")
            else:
                target.mkdir(parents=True, exist_ok=True)
                (target / "fixture.py").write_text("fixture " + relative + "\n", encoding="ascii")
        self.release = _commit(self.application)
        self.application_tree = _tree(self.application, self.release)
        self.control_tree = _tree(self.control, self.control_commit)
        self.transport_config, self.campaign_binding, self.initial_static_object_id = fixtures.make_initial_static_inputs(
            root=self.root,
            campaign_id=CAMPAIGN,
            application_repository=self.application,
            application_release_sha=self.release,
            expected_alembic_revision=REVISION,
            control_commit=self.control_commit,
            control_tree=self.control_tree,
        )
        self.controller_key, self.controller_raw, self.controller_public = _key_material()
        self.controller_private = _private_file(self.root / "keys" / "controller.raw", self.controller_raw)
        initial_authority = fixtures.campaign_bound_controller_signer(
            campaign_binding_path=self.campaign_binding,
            private_key_raw=self.controller_raw,
        )
        preparation_signing_loader = patch.object(
            prepare,
            "_load_campaign_bound_controller_signer",
            return_value=initial_authority,
        )
        preparation_signing_loader.start()
        self.addCleanup(preparation_signing_loader.stop)
        self.ssh_public = _private_file(
            self.root / "keys" / "ssh-host-ed25519.pub",
            b"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest fixture\n",
        )
        self.package_dir = self.root / "packages" / "package-one"
        (self.root / "packages").mkdir(mode=0o700)
        self.prepared = prepare.prepare_source_adoption_package(
            source_repository=self.control,
            application_source_repository=self.application,
            control_commit=self.control_commit,
            application_release_sha=self.release,
            expected_alembic_revision=REVISION,
            source_transport_config=self.transport_config,
            campaign_binding_path=self.campaign_binding,
            initial_static_object_id=self.initial_static_object_id,
            package_id="package-one",
            destination=self.package_dir,
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
            package_directory=self.package_dir,
            preparation_receipt=self.package_dir / prepare.PREPARATION_RECEIPT_NAME,
            expected_control_commit=self.control_commit,
            expected_application_release_sha=self.release,
            campaign_binding_path=self.campaign_binding,
            fi_bootstrap_recipient=RECIPIENT,
            object_key=self.delivery_object["object_key"],
            version_id=self.delivery_object["version_id"],
            ciphertext_sha256=self.delivery_object["ciphertext_sha256"],
            ciphertext_bytes=self.delivery_object["ciphertext_bytes"],
            plaintext_sha256=self.delivery_object["plaintext_sha256"],
            plaintext_bytes=self.delivery_object["plaintext_bytes"],
            destination=self.envelope,
            apply=True,
        )
        staging = self.root / "staging"
        staging.mkdir(mode=0o700)
        self.installed = install.install_source_adoption(
            archive=Path(self.prepared["archive_path"]),
            preparation_receipt=self.package_dir / prepare.PREPARATION_RECEIPT_NAME,
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
        # This is the non-secret control output that a real controller records
        # from its pinned FI SSH session.  The issuer must not use the FI path
        # embedded in the copied receipt as a local filesystem path.
        self.fi_install_control_receipt = _private_file(
            self.root / "controller-inputs" / "fi-source-adoption-install.json",
            Path(self.installed["receipt_path"]).read_bytes(),
        )
        self.campaign_binding = self._create_campaign_binding()
        self.controller_signing_authority = fixtures.campaign_bound_controller_signer(
            campaign_binding_path=self.campaign_binding,
            private_key_raw=self.controller_raw,
        )
        self.fi_campaign_root = self.root / "fi-campaigns"
        self.fi_campaign_root.mkdir(mode=0o700)
        (self.fi_campaign_root / CAMPAIGN).mkdir(mode=0o700)
        candidate_bootstrap = Path(self.installed["candidate"]) / bootstrap.THIS_SCRIPT_RELATIVE
        with (
            patch.object(bootstrap, "CAMPAIGN_ROOT", self.fi_campaign_root),
            patch.object(bootstrap, "__file__", str(candidate_bootstrap)),
        ):
            self.bootstrap_result = bootstrap.bootstrap_source_signer(
                install_receipt=Path(self.installed["receipt_path"]),
                ssh_host_public_key_file=self.ssh_public,
                apply=True,
            )
        self.fi_private = Path(self.bootstrap_result["source_signing_private_key_file"])
        self.fi_public = self.bootstrap_result["source_signing_public_key_base64"]
        signer_root = patch.object(
            install,
            "FI_SOURCE_SIGNER_CAMPAIGN_ROOT",
            PurePosixPath(str(self.fi_campaign_root)),
        )
        signer_root.start()
        self.addCleanup(signer_root.stop)
        self.bootstrap_signer_receipt = _private_file(
            self.root / "controller-inputs" / "fi-source-signer-bootstrap.json",
            Path(self.bootstrap_result["receipt_path"]).read_bytes(),
        )
        campaign_binding = json.loads(self.campaign_binding.read_text(encoding="utf-8"))
        self.role_path = _canonical_private_json(
            self.root / "keys" / "role.json",
            {
                "schema": install.CAMPAIGN_BOUND_SOURCE_ROLE_CONFIG_SCHEMA,
                "campaign_id": campaign_binding["campaign_id"],
                "campaign_binding_sha256": campaign_binding["binding_sha256"],
                "source_site": "webapp_fi",
                "destination_site": "webapp_ir",
                "application": campaign_binding["application"],
                "tooling": campaign_binding["tooling"],
                "application_container": "fixture_app",
                "sync_worker_container": "fixture_sync",
                "source_signing_private_key_file": str(self.fi_private),
            },
        )
        self.role_config = install.load_source_role_config(
            self.role_path,
            expected_application=self.installed["application"],
        )
        self.output_parent = self.root / "certificates"
        self.output_parent.mkdir(mode=0o700)
        self.issued = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        self.not_after = self.issued + dt.timedelta(minutes=10)

    def tearDown(self):
        self.temp.cleanup()

    def _create_campaign_binding(self):
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
                "release_tree": self.application_tree,
                "expected_alembic_revision": REVISION,
            },
            "tooling": {
                "control_commit": self.control_commit,
                "control_tree": self.control_tree,
            },
        }
        return _canonical_private_json(
            path,
            {**unsigned, "binding_sha256": hashlib.sha256(install.canonical_json_bytes(unsigned)).hexdigest()},
        )

    def _rewrite_bootstrap_signer_receipt(self, mutate):
        value = json.loads(self.bootstrap_signer_receipt.read_text(encoding="ascii"))
        mutate(value)
        unsigned = {key: item for key, item in value.items() if key != "receipt_sha256"}
        value["receipt_sha256"] = hashlib.sha256(install.canonical_json_bytes(unsigned)).hexdigest()
        _canonical_private_json(self.bootstrap_signer_receipt, value)

    def _rewrite_fi_install_control_receipt(self, mutate):
        value = json.loads(self.fi_install_control_receipt.read_text(encoding="ascii"))
        mutate(value)
        unsigned = {key: item for key, item in value.items() if key != "receipt_sha256"}
        value["receipt_sha256"] = hashlib.sha256(install.canonical_json_bytes(unsigned)).hexdigest()
        _canonical_private_json(self.fi_install_control_receipt, value)

    def _rewrite_campaign_binding(self, mutate):
        value = json.loads(self.campaign_binding.read_text(encoding="ascii"))
        mutate(value)
        unsigned = {key: item for key, item in value.items() if key != "binding_sha256"}
        value["binding_sha256"] = hashlib.sha256(install.canonical_json_bytes(unsigned)).hexdigest()
        _canonical_private_json(self.campaign_binding, value)

    @property
    def timestamp_args(self):
        return {
            "issued_at": self.issued.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "not_before": self.issued.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "not_after": self.not_after.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def _issue(self, output, **overrides):
        with (
            patch.object(issuer, "FI_SOURCE_SIGNER_CAMPAIGN_ROOT", self.fi_campaign_root),
            patch.object(
                issuer,
                "_load_campaign_bound_controller_signer",
                return_value=self.controller_signing_authority,
            ),
        ):
            return self._issue_with(issuer, output, **overrides)

    def _issue_with(self, module, output, **overrides):
        kwargs = {
            "package_directory": self.package_dir,
            "preparation_receipt": self.package_dir / prepare.PREPARATION_RECEIPT_NAME,
            "delivery_envelope": self.envelope,
            "campaign_binding": self.campaign_binding,
            "fi_install_control_receipt": self.fi_install_control_receipt,
            "bootstrap_signer_receipt": self.bootstrap_signer_receipt,
            "pinned_fi_ssh_host_public_key_file": self.ssh_public,
            "certificate_id": "certificate-one",
            "operation_id": "operation-one",
            **self.timestamp_args,
            "output": output,
            "apply": True,
        }
        kwargs.update(overrides)
        return module.issue_source_signer_enrollment_certificate(**kwargs)

    def _portable_delivery(self):
        return portable._controller_delivery_envelope(
            payload=self.envelope.read_bytes(),
            pinned_controller_public_key_base64=self.controller_public,
            expected_campaign_id=CAMPAIGN,
            expected_application=self.installed["application"],
            expected_tooling=self.installed["tooling"],
            expected_canonical_release_tree_sha256=self.installed["canonical_release_tree_sha256"],
        )

    def test_issues_root_only_certificate_accepted_by_both_existing_validators(self):
        output = self.output_parent / "certificate.json"
        result = self._issue(output)
        self.assertEqual(result["status"], "issued")
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        payload = output.read_bytes()
        value = json.loads(payload.decode("ascii"))
        self.assertEqual(payload, install.canonical_json_bytes(value) + b"\n")
        self.assertEqual(value["schema"], issuer.SIGNER_ENROLLMENT_CERTIFICATE_SCHEMA)
        self.assertEqual(value["source_adoption_install_receipt_sha256"], self.installed["receipt_sha256"])
        self.assertEqual(value["delivery_envelope_sha256"], self.installed["package"]["delivery_envelope_sha256"])
        self.assertEqual(value["source_adoption_object"]["version_id"], VERSION_ID)
        portable_certificate = portable._signer_enrollment_certificate(
            payload=payload,
            pinned_controller_public_key_base64=self.controller_public,
            expected_campaign_id=CAMPAIGN,
            expected_application=self.installed["application"],
            expected_tooling=self.installed["tooling"],
            expected_canonical_release_tree_sha256=self.installed["canonical_release_tree_sha256"],
            expected_delivery=self._portable_delivery(),
            expected_source_signing_public_key_base64=self.fi_public,
            verification_time=self.timestamp_args["not_before"],
        )
        self.assertEqual(portable_certificate["certificate_id"], "certificate-one")
        installed_certificate = install._validate_signer_enrollment_certificate(
            certificate=output,
            pinned_controller_public_key_base64=self.controller_public,
            campaign_id=CAMPAIGN,
            installed=self.installed,
            role_config=self.role_config,
            ssh_host_public_key_file=self.ssh_public,
            verification_time=self.timestamp_args["not_before"],
        )
        self.assertEqual(installed_certificate["source_signing_key_id"], install._public_key_id(self.fi_public))

    def test_rejects_campaign_not_bound_to_the_installed_receipt(self):
        self._rewrite_bootstrap_signer_receipt(
            lambda value: value.__setitem__("campaign_id", "wrong-campaign-20260730")
        )
        output = self.output_parent / "wrong-campaign.json"
        with self.assertRaisesRegex(issuer.SourceSignerEnrollmentIssuerError, "not bound"):
            self._issue(output)
        self.assertFalse(output.exists())

    def test_rejects_certificate_lifetime_over_the_existing_limit(self):
        output = self.output_parent / "long-lived.json"
        too_late = self.issued + dt.timedelta(
            seconds=issuer.MAX_ENROLLMENT_CERTIFICATE_LIFETIME_SECONDS + 1
        )
        with self.assertRaisesRegex(issuer.SourceSignerEnrollmentIssuerError, "lifetime"):
            self._issue(output, not_after=too_late.strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assertFalse(output.exists())

    def test_does_not_accept_a_caller_selected_controller_private_key(self):
        _, wrong_raw, _ = _key_material()
        wrong_key = _private_file(self.root / "keys" / "wrong-controller.raw", wrong_raw)
        output = self.output_parent / "wrong-controller.json"
        with self.assertRaises(TypeError):
            self._issue(output, controller_signing_private_key=wrong_key)
        self.assertFalse(output.exists())

    def test_rejects_controller_key_reused_as_source_signing_key(self):
        def mutate(value):
            value["source_signer"]["public_key_base64"] = self.controller_public
            value["source_signer"]["key_id"] = install._public_key_id(self.controller_public)

        self._rewrite_bootstrap_signer_receipt(mutate)
        output = self.output_parent / "reused-key.json"
        with self.assertRaisesRegex(issuer.SourceSignerEnrollmentIssuerError, "distinct"):
            self._issue(output)
        self.assertFalse(output.exists())

    def test_rejects_tampered_bootstrap_receipt_before_certificate_creation(self):
        value = json.loads(self.bootstrap_signer_receipt.read_text(encoding="ascii"))
        value["receipt_sha256"] = "0" * 64
        _canonical_private_json(self.bootstrap_signer_receipt, value)
        output = self.output_parent / "tampered-bootstrap-receipt.json"
        with self.assertRaisesRegex(issuer.SourceSignerEnrollmentIssuerError, "bootstrap receipt checksum"):
            self._issue(output)
        self.assertFalse(output.exists())

    def test_rejects_bootstrap_receipt_release_and_stale_install_mismatches(self):
        original = self.bootstrap_signer_receipt.read_bytes()
        with self.subTest(case="release"):
            self._rewrite_bootstrap_signer_receipt(
                lambda value: value["source_adoption"]["application"].__setitem__("release_sha", "0" * 40)
            )
            output = self.output_parent / "wrong-release.json"
            with self.assertRaisesRegex(issuer.SourceSignerEnrollmentIssuerError, "not bound"):
                self._issue(output)
            self.assertFalse(output.exists())

        _private_file(self.bootstrap_signer_receipt, original)
        with self.subTest(case="stale_install_receipt"):
            self._rewrite_bootstrap_signer_receipt(
                lambda value: value["source_adoption"].__setitem__("install_receipt_sha256", "0" * 64)
            )
            output = self.output_parent / "stale-install-receipt.json"
            with self.assertRaisesRegex(issuer.SourceSignerEnrollmentIssuerError, "not bound"):
                self._issue(output)
            self.assertFalse(output.exists())

    def test_rejects_campaign_binding_release_tree_drift(self):
        self._rewrite_campaign_binding(
            lambda value: value["application"].__setitem__("release_tree", "0" * 40)
        )
        output = self.output_parent / "wrong-binding-tree.json"
        with self.assertRaisesRegex(issuer.SourceSignerEnrollmentIssuerError, "release tree"):
            self._issue(output)
        self.assertFalse(output.exists())

    def test_rejects_bootstrap_key_path_and_ssh_digest_drift(self):
        original = self.bootstrap_signer_receipt.read_bytes()
        with self.subTest(case="key_path"):
            self._rewrite_bootstrap_signer_receipt(
                lambda value: value["source_signer"].__setitem__(
                    "private_key_file",
                    "/etc/trading-bot-three-site/campaigns/other/webapp-fi/source-signing-ed25519.raw",
                )
            )
            output = self.output_parent / "wrong-key-path.json"
            with self.assertRaisesRegex(issuer.SourceSignerEnrollmentIssuerError, "key path"):
                self._issue(output)
            self.assertFalse(output.exists())

        _private_file(self.bootstrap_signer_receipt, original)
        with self.subTest(case="ssh_digest"):
            self._rewrite_bootstrap_signer_receipt(
                lambda value: value.__setitem__("fi_ssh_host_public_key_sha256", "0" * 64)
            )
            output = self.output_parent / "wrong-ssh-digest.json"
            with self.assertRaisesRegex(issuer.SourceSignerEnrollmentIssuerError, "SSH digest"):
                self._issue(output)
            self.assertFalse(output.exists())

    def test_apply_rejects_ssh_pin_swap_before_signature(self):
        output = self.output_parent / "changed-ssh-pin.json"
        original = issuer._load_bootstrap_signer_receipt
        attempts = 0

        def replace_pin_after_initial_receipt(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            result = original(*args, **kwargs)
            if attempts == 1:
                _private_file(
                    self.ssh_public,
                    b"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIChanged controller pin\n",
                )
            return result

        with patch.object(
            issuer,
            "_load_bootstrap_signer_receipt",
            side_effect=replace_pin_after_initial_receipt,
        ):
            with self.assertRaisesRegex(issuer.SourceSignerEnrollmentIssuerError, "SSH digest"):
                self._issue(output)
        self.assertGreaterEqual(attempts, 2)
        self.assertFalse(output.exists())

    def test_opaque_fi_candidate_is_never_dereferenced_by_the_controller(self):
        """A controller consumes only the local receipt copy, not an FI path."""

        candidate = Path(self.installed["candidate"])
        candidate.rename(candidate.with_name(candidate.name + "-offline"))
        output = self.output_parent / "opaque-fi-candidate.json"
        result = self._issue(output)
        self.assertEqual("issued", result["status"])
        self.assertTrue(output.exists())

    def test_rejects_opaque_fi_receipt_that_drifts_from_local_package(self):
        self._rewrite_fi_install_control_receipt(
            lambda value: value.__setitem__(
                "candidate_directory",
                "/srv/trading-bot-three-site-staging-data/webapp-fi-source/installed-wrong-package",
            )
        )
        output = self.output_parent / "wrong-opaque-fi-install.json"
        with self.assertRaisesRegex(issuer.SourceSignerEnrollmentIssuerError, "not bound"):
            self._issue(output)
        self.assertFalse(output.exists())

    def test_apply_rejects_future_issued_at_without_creating_output(self):
        output = self.output_parent / "future-issued.json"
        future = self.issued + dt.timedelta(seconds=issuer.MAX_ISSUANCE_CLOCK_SKEW_SECONDS + 1)
        with patch.object(issuer, "_controller_utc_now", return_value=self.issued):
            with self.assertRaisesRegex(issuer.SourceSignerEnrollmentIssuerError, "issued_at.*clock-skew"):
                self._issue(
                    output,
                    issued_at=future.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    not_before=future.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    not_after=(future + dt.timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
        self.assertFalse(output.exists())

    def test_apply_rejects_not_before_outside_clock_skew_without_creating_output(self):
        output = self.output_parent / "future-not-before.json"
        begins = self.issued + dt.timedelta(seconds=issuer.MAX_ISSUANCE_CLOCK_SKEW_SECONDS + 1)
        with patch.object(issuer, "_controller_utc_now", return_value=self.issued):
            with self.assertRaisesRegex(issuer.SourceSignerEnrollmentIssuerError, "not_before.*clock-skew"):
                self._issue(
                    output,
                    not_before=begins.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    not_after=(begins + dt.timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
        self.assertFalse(output.exists())

    def test_apply_accepts_not_before_at_clock_skew_boundary(self):
        output = self.output_parent / "skew-boundary.json"
        begins = self.issued + dt.timedelta(seconds=issuer.MAX_ISSUANCE_CLOCK_SKEW_SECONDS)
        with patch.object(issuer, "_controller_utc_now", return_value=self.issued):
            result = self._issue(
                output,
                not_before=begins.strftime("%Y-%m-%dT%H:%M:%SZ"),
                not_after=(begins + dt.timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        self.assertEqual(result["status"], "issued")
        self.assertTrue(output.exists())

    def test_apply_rejects_currently_expired_certificate_without_creating_output(self):
        output = self.output_parent / "expired.json"
        starts = self.issued - dt.timedelta(seconds=30)
        with patch.object(issuer, "_controller_utc_now", return_value=self.issued):
            with self.assertRaisesRegex(issuer.SourceSignerEnrollmentIssuerError, "not_after.*current controller time"):
                self._issue(
                    output,
                    issued_at=starts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    not_before=starts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    not_after=self.issued.strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
        self.assertFalse(output.exists())

    def test_apply_revalidates_input_facts_immediately_before_signature(self):
        output = self.output_parent / "changed-input.json"
        original = issuer._load_delivery_envelope
        attempts = 0

        def mutate_after_initial_verification(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            result = original(*args, **kwargs)
            if attempts == 1:
                _private_file(self.envelope, b"{}\n")
            return result

        with patch.object(
            issuer,
            "_load_delivery_envelope",
            side_effect=mutate_after_initial_verification,
        ):
            with self.assertRaisesRegex(issuer.SourceSignerEnrollmentIssuerError, "delivery envelope"):
                self._issue(output)
        self.assertGreaterEqual(attempts, 2)
        self.assertFalse(output.exists())

    def test_refuses_to_overwrite_an_existing_certificate(self):
        output = self.output_parent / "certificate.json"
        self._issue(output)
        with self.assertRaisesRegex(issuer.SourceSignerEnrollmentIssuerError, "overwrite"):
            self._issue(output, certificate_id="certificate-two", operation_id="operation-two")

    def test_plan_does_not_create_a_certificate(self):
        output = self.output_parent / "planned.json"
        result = self._issue(output, apply=False)
        self.assertEqual(result["status"], "planned")
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
