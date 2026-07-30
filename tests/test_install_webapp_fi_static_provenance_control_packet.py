import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
        raise
    return module


reader = _load_module(
    "install_webapp_fi_static_provenance_control_packet_test",
    ROOT / "scripts" / "install_webapp_fi_static_provenance_control_packet.py",
)
packet = _load_module(
    "webapp_fi_static_provenance_control_packet_for_reader_test",
    ROOT / "scripts" / "webapp_fi_static_provenance_control_packet.py",
)
transport = _load_module(
    "webapp_fi_source_transport_contract_for_reader_test",
    ROOT / "scripts" / "webapp_fi_source_transport_contract.py",
)
source_adoption_fixture_module = _load_module(
    "webapp_fi_source_adoption_fixture_for_static_provenance_reader_test",
    ROOT / "tests" / "test_webapp_fi_source_adoption.py",
)


def _private_file(path, payload):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    path.write_bytes(payload)
    os.chmod(path, 0o600)
    return path


def _canonical_private_json(path, value):
    return _private_file(path, packet.canonical_json_bytes(value) + b"\n")


class StaticProvenanceControlPacketReaderTests(unittest.TestCase):
    """Exercise a real installed candidate without Docker, SSH, or Object Storage."""

    def setUp(self):
        self.fixture = source_adoption_fixture_module.WebAppFiSourceAdoptionTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.installed = self.fixture._install()
        _, original_role, _, static_path, certificate = self.fixture._runtime_and_config(self.installed)
        self.campaign = source_adoption_fixture_module.CAMPAIGN
        self.campaign_root = self.fixture.root / "campaigns"
        self.campaign_root.mkdir(mode=0o700)
        self.campaign_directory = self.campaign_root / self.campaign
        self.campaign_directory.mkdir(mode=0o700)
        self.binding = self._binding()
        # This is controller input used to seal the packet, not an FI-local
        # binding.  The latter must not exist before the verified packet is
        # consumed.
        self.controller_binding_path = _canonical_private_json(
            self.fixture.root / "controller-inputs" / "campaign-binding.json",
            self.binding,
        )

        role = json.loads(original_role.read_text(encoding="ascii"))
        role["schema"] = packet.SOURCE_ROLE_CONFIG_SCHEMA
        role["campaign_id"] = self.campaign
        role["campaign_binding_sha256"] = self.binding["binding_sha256"]
        role["application"] = dict(self.binding["application"])
        role["tooling"] = dict(self.binding["tooling"])
        role["source_signing_private_key_file"] = packet.expected_source_signing_key_path(self.campaign)
        self.role_path = _canonical_private_json(self.fixture.root / "controller-inputs" / "role.json", role)
        self.certificate_path = certificate
        self.static_path = static_path
        self.workspace = self.fixture.root / "exchange-workspace"
        self.workspace.mkdir(mode=0o700)
        self.policy = self._policy()
        self.packet_payload = self._seal_packet(
            created_at="2026-07-30T00:00:00Z",
            campaign_binding_payload=self.controller_binding_path.read_bytes(),
            signer_enrollment_certificate_payload=self.certificate_path.read_bytes(),
            source_role_config_payload=self.role_path.read_bytes(),
            static_assets_provenance_payload=self.static_path.read_bytes(),
            source_transport_policy_payload=packet.canonical_json_bytes(self.policy) + b"\n",
            packet_id="packet-reader-one",
        )
        self.received_directory = self.workspace / "received-one"
        self.received_directory.mkdir(mode=0o700)
        self._write_exchange_receive_receipt()

    def _binding(self):
        application_tree = subprocess.check_output(
            ["git", "-C", str(self.fixture.application), "rev-parse", self.fixture.release + "^{tree}"],
            text=True,
        ).strip()
        unsigned = {
            "schema": packet.CAMPAIGN_BINDING_SCHEMA,
            "status": "bound",
            "campaign_id": self.campaign,
            "application": {
                "release_sha": self.fixture.release,
                "release_tree": application_tree,
                "expected_alembic_revision": source_adoption_fixture_module.REVISION,
            },
            "tooling": {
                "control_commit": self.fixture.control_commit,
                "control_tree": self.fixture.control_tree,
            },
        }
        return {**unsigned, "binding_sha256": packet.sha256_bytes(packet.canonical_json_bytes(unsigned))}

    def _seal_packet(self, **values):
        return packet.build_control_packet_payload_with_signer(
            **values,
            controller_signer=self.fixture.controller_key,
            controller_public_key_base64=self.fixture.controller_public,
        )

    def _policy(self):
        return {
            "schema": packet.EXCHANGE_POLICY_SCHEMA,
            "endpoint": "https://s3.ir-thr-at1.arvanstorage.ir",
            "region": "ir-thr-at1",
            "bucket": "private-artifacts",
            "prefix": "campaigns/three-site",
            "age_binary": "/usr/bin/age",
            "workspace": str(self.workspace),
            "controller_age_recipient": "age1" + "a" * 40,
            "webapp_fi_age_recipient": "age1" + "c" * 40,
            "webapp_ir_age_recipient": "age1" + "d" * 40,
            "maximum_plaintext_bytes": 1024 * 1024,
        }

    def _object_key(self):
        config = transport.validate_policy(
            transport.SourceTransportPolicy(
                endpoint=self.policy["endpoint"],
                region=self.policy["region"],
                bucket=self.policy["bucket"],
                prefix=self.policy["prefix"],
                age_binary=self.policy["age_binary"],
                workspace=Path(self.policy["workspace"]),
                controller_age_recipient=self.policy["controller_age_recipient"],
                webapp_fi_age_recipient=self.policy["webapp_fi_age_recipient"],
                webapp_ir_age_recipient=self.policy["webapp_ir_age_recipient"],
                maximum_plaintext_bytes=self.policy["maximum_plaintext_bytes"],
            )
        )
        request = transport.SourceObjectRequest(
            campaign_id=self.campaign,
            release_sha=self.fixture.release,
            control_commit=self.fixture.control_commit,
            control_tree=self.fixture.control_tree,
            source_site="controller",
            destination_site="webapp_fi",
            object_kind=transport.STATIC_PROVENANCE_OBJECT_KIND,
            object_id="packet-reader-one",
            mode=transport.SINGLE_MODE,
            recipients=(self.policy["webapp_fi_age_recipient"],),
        )
        return transport.source_object_key(config, request)

    def _write_exchange_receive_receipt(self):
        _private_file(self.received_directory / reader.RECEIVED_PACKET_NAME, self.packet_payload)
        descriptor = {
            "object_key": self._object_key(),
            "version_id": "received-version-1",
            "ciphertext_sha256": hashlib.sha256(b"fixture ciphertext").hexdigest(),
            "ciphertext_bytes": len(b"fixture ciphertext"),
            "plaintext_sha256": packet.sha256_bytes(self.packet_payload),
            "plaintext_bytes": len(self.packet_payload),
        }
        request = {
            "campaign_id": self.campaign,
            "release_sha": self.fixture.release,
            "control_commit": self.fixture.control_commit,
            "control_tree": self.fixture.control_tree,
            "source_site": "controller",
            "destination_site": "webapp_fi",
            "object_kind": "static-provenance",
            "object_id": "packet-reader-one",
            "recipient_mode": "single",
            "recipients": [self.policy["webapp_fi_age_recipient"]],
        }
        unsigned = {
            "schema": packet.EXCHANGE_RECEIVE_RECEIPT_SCHEMA,
            "status": "received",
            "request": request,
            "object": descriptor,
            "controller_publish_receipt_sha256": "e" * 64,
            "plaintext": {
                "name": reader.RECEIVED_PACKET_NAME,
                "sha256": packet.sha256_bytes(self.packet_payload),
                "bytes": len(self.packet_payload),
            },
            "transport": {
                "private_bucket": True,
                "provider_side_sse": False,
                "version_bound_get": True,
            },
        }
        _canonical_private_json(
            self.received_directory / reader.EXCHANGE_RECEIPT_NAME,
            {**unsigned, "receive_receipt_sha256": packet.sha256_bytes(packet.canonical_json_bytes(unsigned))},
        )

    def _run(self, *, apply):
        candidate_script = Path(self.installed["candidate"]) / reader.THIS_SCRIPT_RELATIVE
        with (
            patch.object(reader, "CAMPAIGN_ROOT", self.campaign_root),
            patch.object(reader, "__file__", str(candidate_script)),
        ):
            return reader.install_static_provenance_control_packet(
                install_receipt=Path(self.installed["receipt_path"]),
                received_directory=self.received_directory,
                apply=apply,
            )

    def test_plan_then_apply_installs_fixed_verified_candidate_material(self):
        plan = self._run(apply=False)
        output = Path(plan["output_directory"])
        binding_path = self.campaign_directory / reader.SOURCE_PHASE_DIRECTORY / reader.CAMPAIGN_BINDING_FILENAME
        self.assertEqual("planned", plan["status"])
        self.assertFalse(output.exists())
        self.assertEqual(str(binding_path), plan["campaign_binding_path"])
        self.assertFalse(binding_path.exists())

        result = self._run(apply=True)
        self.assertEqual("installed", result["status"])
        self.assertEqual(0o700, stat.S_IMODE(output.stat().st_mode))
        expected_names = {
            reader.CONTROL_PACKET_FILENAME,
            reader.SIGNER_ENROLLMENT_CERTIFICATE_FILENAME,
            reader.SOURCE_ROLE_CONFIG_FILENAME,
            reader.STATIC_ASSETS_PROVENANCE_FILENAME,
            reader.SOURCE_TRANSPORT_POLICY_FILENAME,
            reader.READ_RECEIPT_FILENAME,
        }
        self.assertEqual(expected_names, {item.name for item in output.iterdir()})
        for name in expected_names:
            self.assertEqual(0o600, stat.S_IMODE((output / name).stat().st_mode))
        self.assertEqual(self.controller_binding_path.read_bytes(), binding_path.read_bytes())
        self.assertEqual(0o700, stat.S_IMODE(binding_path.parent.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(binding_path.stat().st_mode))
        self.assertEqual(self.certificate_path.read_bytes(), (output / reader.SIGNER_ENROLLMENT_CERTIFICATE_FILENAME).read_bytes())
        self.assertEqual(self.role_path.read_bytes(), (output / reader.SOURCE_ROLE_CONFIG_FILENAME).read_bytes())
        policy = json.loads((output / reader.SOURCE_TRANSPORT_POLICY_FILENAME).read_text(encoding="ascii"))
        self.assertEqual(packet.SOURCE_TRANSPORT_POLICY_SCHEMA, policy["schema"])
        self.assertNotIn("endpoint", policy)
        self.assertEqual("s3.ir-thr-at1.arvanstorage.ir", policy["endpoint_host"])
        self.assertNotIn(b"://", (output / reader.CONTROL_PACKET_FILENAME).read_bytes())
        self.assertEqual(
            self.installed["candidate"],
            source_adoption_fixture_module.install.verify_installed_source_adoption(Path(self.installed["receipt_path"]))["candidate"],
        )
        with self.assertRaisesRegex(reader.StaticProvenanceControlPacketInstallError, "reuse or overwrite"):
            self._run(apply=True)

    def test_tampered_exchange_receipt_is_rejected_before_candidate_output(self):
        receipt_path = self.received_directory / reader.EXCHANGE_RECEIPT_NAME
        value = json.loads(receipt_path.read_text(encoding="ascii"))
        value["object"]["object_key"] = "wrong/object.age"
        unsigned = {key: item for key, item in value.items() if key != "receive_receipt_sha256"}
        value["receive_receipt_sha256"] = packet.sha256_bytes(packet.canonical_json_bytes(unsigned))
        _canonical_private_json(receipt_path, value)
        with self.assertRaisesRegex(reader.StaticProvenanceControlPacketInstallError, "receipt"):
            self._run(apply=False)
        output = Path(self.installed["candidate"]) / reader.CONTROLLER_STATIC_PROVENANCE_DIRECTORY / "packet-reader-one"
        self.assertFalse(output.exists())

    def test_null_exchange_version_id_is_rejected_before_candidate_output(self):
        receipt_path = self.received_directory / reader.EXCHANGE_RECEIPT_NAME
        value = json.loads(receipt_path.read_text(encoding="ascii"))
        value["object"]["version_id"] = "null"
        unsigned = {key: item for key, item in value.items() if key != "receive_receipt_sha256"}
        value["receive_receipt_sha256"] = packet.sha256_bytes(packet.canonical_json_bytes(unsigned))
        _canonical_private_json(receipt_path, value)
        with self.assertRaisesRegex(reader.StaticProvenanceControlPacketInstallError, "receipt"):
            self._run(apply=False)
        output = Path(self.installed["candidate"]) / reader.CONTROLLER_STATIC_PROVENANCE_DIRECTORY / "packet-reader-one"
        self.assertFalse(output.exists())

    def test_preexisting_fi_binding_is_rejected_without_reuse(self):
        source_phase = self.campaign_directory / reader.SOURCE_PHASE_DIRECTORY
        source_phase.mkdir(mode=0o700)
        binding_path = _canonical_private_json(source_phase / reader.CAMPAIGN_BINDING_FILENAME, self.binding)
        with self.assertRaisesRegex(reader.StaticProvenanceControlPacketInstallError, "reuse or overwrite existing campaign binding"):
            self._run(apply=False)
        output = Path(self.installed["candidate"]) / reader.CONTROLLER_STATIC_PROVENANCE_DIRECTORY / "packet-reader-one"
        self.assertFalse(output.exists())
        self.assertEqual(self.binding, json.loads(binding_path.read_text(encoding="ascii")))

    def test_packet_binding_release_tree_drift_is_rejected_before_output(self):
        unsigned = {
            "schema": packet.CAMPAIGN_BINDING_SCHEMA,
            "status": "bound",
            "campaign_id": self.campaign,
            "application": {
                **self.binding["application"],
                "release_tree": "f" * 40,
            },
            "tooling": self.binding["tooling"],
        }
        wrong_binding = {
            **unsigned,
            "binding_sha256": packet.sha256_bytes(packet.canonical_json_bytes(unsigned)),
        }
        wrong_binding_path = _canonical_private_json(
            self.fixture.root / "controller-inputs" / "wrong-release-tree-binding.json",
            wrong_binding,
        )
        wrong_role = json.loads(self.role_path.read_text(encoding="ascii"))
        wrong_role["campaign_binding_sha256"] = wrong_binding["binding_sha256"]
        wrong_role["application"] = dict(wrong_binding["application"])
        wrong_role["tooling"] = dict(wrong_binding["tooling"])
        wrong_role_path = _canonical_private_json(
            self.fixture.root / "controller-inputs" / "wrong-release-tree-role.json",
            wrong_role,
        )
        self.packet_payload = self._seal_packet(
            created_at="2026-07-30T00:00:00Z",
            campaign_binding_payload=wrong_binding_path.read_bytes(),
            signer_enrollment_certificate_payload=self.certificate_path.read_bytes(),
            source_role_config_payload=wrong_role_path.read_bytes(),
            static_assets_provenance_payload=self.static_path.read_bytes(),
            source_transport_policy_payload=packet.canonical_json_bytes(self.policy) + b"\n",
            packet_id="packet-reader-one",
        )
        for name in (reader.RECEIVED_PACKET_NAME, reader.EXCHANGE_RECEIPT_NAME):
            (self.received_directory / name).unlink()
        self._write_exchange_receive_receipt()
        with self.assertRaisesRegex(reader.StaticProvenanceControlPacketInstallError, "release tree does not match"):
            self._run(apply=False)
        output = Path(self.installed["candidate"]) / reader.CONTROLLER_STATIC_PROVENANCE_DIRECTORY / "packet-reader-one"
        self.assertFalse(output.exists())

    def test_certificate_for_a_different_candidate_is_rejected_before_output(self):
        certificate = json.loads(self.certificate_path.read_text(encoding="ascii"))
        certificate["package_id"] = "other-package"
        unsigned = {key: item for key, item in certificate.items() if key != "controller_signature"}
        certificate["controller_signature"] = {
            "algorithm": "ed25519",
            "signature_base64": source_adoption_fixture_module._signature(
                self.fixture.controller_key,
                packet.SIGNER_ENROLLMENT_SIGNATURE_DOMAIN,
                unsigned,
            ),
        }
        certificate_path = _canonical_private_json(
            self.fixture.root / "controller-inputs" / "wrong-candidate-certificate.json",
            certificate,
        )
        self.packet_payload = self._seal_packet(
            created_at="2026-07-30T00:00:00Z",
            campaign_binding_payload=self.controller_binding_path.read_bytes(),
            signer_enrollment_certificate_payload=certificate_path.read_bytes(),
            source_role_config_payload=self.role_path.read_bytes(),
            static_assets_provenance_payload=self.static_path.read_bytes(),
            source_transport_policy_payload=packet.canonical_json_bytes(self.policy) + b"\n",
            packet_id="packet-reader-one",
        )
        for name in (reader.RECEIVED_PACKET_NAME, reader.EXCHANGE_RECEIPT_NAME):
            (self.received_directory / name).unlink()
        self._write_exchange_receive_receipt()
        with self.assertRaisesRegex(reader.StaticProvenanceControlPacketInstallError, "not bound to this installed"):
            self._run(apply=False)
        output = Path(self.installed["candidate"]) / reader.CONTROLLER_STATIC_PROVENANCE_DIRECTORY / "packet-reader-one"
        self.assertFalse(output.exists())
