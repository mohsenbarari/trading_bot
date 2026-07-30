import base64
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = "static-packet-20260730"
REVISION = "f2c7d8e9a0b1"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


packet = _load_module(
    "webapp_fi_static_provenance_control_packet_test",
    ROOT / "scripts" / "webapp_fi_static_provenance_control_packet.py",
)
builder = _load_module(
    "build_webapp_fi_static_provenance_control_packet_test",
    ROOT / "scripts" / "build_webapp_fi_static_provenance_control_packet.py",
)


def _private_file(path, payload):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    path.write_bytes(payload)
    os.chmod(path, 0o600)
    return path


def _canonical_private_json(path, value):
    return _private_file(path, packet.canonical_json_bytes(value) + b"\n")


def _key_material():
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return key, base64.b64encode(public).decode("ascii")


def _noncanonical_base64_alias(value):
    """Return a strict-base64 spelling with identical decoded Ed25519 bytes."""

    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    if not isinstance(value, str) or not value.endswith("="):
        raise AssertionError("fixture public key must use one base64 padding byte")
    index = alphabet.index(value[-2])
    if index % 4:
        raise AssertionError("fixture public key has unexpected base64 pad bits")
    return value[:-2] + alphabet[index + 1] + "="


def _sign_with_fixture_key(key, unsigned, *, domain):
    signature = key.sign(domain + packet.canonical_json_bytes(unsigned))
    return {
        "algorithm": "ed25519",
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }


class StaticProvenanceControlPacketTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="static-provenance-packet-")
        self.root = Path(self.temporary.name)
        os.chmod(self.root, 0o700)
        self.controller_key, self.controller_public = _key_material()
        _, self.fi_public = _key_material()
        self.release = "a" * 40
        self.release_tree = "b" * 40
        self.control_commit = "c" * 40
        self.control_tree = "d" * 40
        self.binding = self._binding()
        self.binding_payload = packet.canonical_json_bytes(self.binding) + b"\n"
        self.certificate = self._certificate()
        self.role = self._role()
        self.static_provenance = self._static_provenance()
        self.exchange_policy = self._exchange_policy()

    def tearDown(self):
        self.temporary.cleanup()

    def _binding(self):
        unsigned = {
            "schema": packet.CAMPAIGN_BINDING_SCHEMA,
            "status": "bound",
            "campaign_id": CAMPAIGN,
            "application": {
                "release_sha": self.release,
                "release_tree": self.release_tree,
                "expected_alembic_revision": REVISION,
            },
            "tooling": {"control_commit": self.control_commit, "control_tree": self.control_tree},
        }
        return {**unsigned, "binding_sha256": packet.sha256_bytes(packet.canonical_json_bytes(unsigned))}

    def _certificate(self):
        source_object = {
            "object_key": "source-adoption/package-one.age",
            "version_id": "fixture-version-1",
            "ciphertext_sha256": "1" * 64,
            "ciphertext_bytes": 100,
            "plaintext_sha256": "2" * 64,
            "plaintext_bytes": 90,
        }
        unsigned = {
            "schema": packet.SIGNER_ENROLLMENT_CERTIFICATE_SCHEMA,
            "status": "issued",
            "certificate_id": "certificate-one",
            "operation_id": "operation-one",
            "issued_at": "2026-07-30T00:00:00Z",
            "not_before": "2026-07-30T00:00:00Z",
            "not_after": "2026-07-30T00:10:00Z",
            "campaign_id": CAMPAIGN,
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "package_id": "package-one",
            "application": {"release_sha": self.release, "expected_alembic_revision": REVISION},
            "tooling": {"control_commit": self.control_commit, "control_tree": self.control_tree},
            "canonical_release_tree_sha256": "3" * 64,
            "source_adoption_install_receipt_sha256": "4" * 64,
            "delivery_envelope_sha256": "5" * 64,
            "source_adoption_object": source_object,
            "fi_bootstrap_recipient": "age1" + "q" * 40,
            "fi_ssh_host_public_key_sha256": "6" * 64,
            "source_signing_public_key_base64": self.fi_public,
            "source_signing_key_id": packet.public_key_id(self.fi_public),
            "controller_public_key_base64": self.controller_public,
            "controller_key_id": packet.public_key_id(self.controller_public),
        }
        return {
            **unsigned,
            "controller_signature": _sign_with_fixture_key(
                self.controller_key,
                unsigned,
                domain=packet.SIGNER_ENROLLMENT_SIGNATURE_DOMAIN,
            ),
        }

    def _role(self):
        return {
            "schema": packet.SOURCE_ROLE_CONFIG_SCHEMA,
            "campaign_id": CAMPAIGN,
            "campaign_binding_sha256": self.binding["binding_sha256"],
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "application": {
                "release_sha": self.release,
                "release_tree": self.release_tree,
                "expected_alembic_revision": REVISION,
            },
            "tooling": {"control_commit": self.control_commit, "control_tree": self.control_tree},
            "application_container": "fixture_app",
            "sync_worker_container": "fixture_sync",
            "source_signing_private_key_file": packet.expected_source_signing_key_path(CAMPAIGN),
        }

    def _static_provenance(self):
        files = [{"path": "index.html", "sha256": "7" * 64, "bytes": 13}]
        unsigned = {
            "schema": packet.STATIC_ASSET_PROVENANCE_SCHEMA,
            "status": "verified",
            "campaign_id": CAMPAIGN,
            "application": {"release_sha": self.release, "expected_alembic_revision": REVISION},
            "source_kind": "deterministic_2c08_dist_manifest",
            "artifact": {
                "object_key": "static-assets/fixture.age",
                "version_id": "static-version-1",
                "ciphertext_sha256": "8" * 64,
                "ciphertext_bytes": 100,
                "plaintext_sha256": "9" * 64,
                "plaintext_bytes": 90,
            },
            "files": files,
            "files_sha256": packet.sha256_bytes(packet.canonical_json_bytes(files)),
            "controller_public_key_base64": self.controller_public,
        }
        return {
            **unsigned,
            "controller_signature": _sign_with_fixture_key(
                self.controller_key,
                unsigned,
                domain=packet.STATIC_ASSET_SIGNATURE_DOMAIN,
            ),
        }

    def _exchange_policy(self):
        return {
            "schema": packet.EXCHANGE_POLICY_SCHEMA,
            "endpoint": "https://s3.ir-thr-at1.arvanstorage.ir",
            "region": "ir-thr-at1",
            "bucket": "private-artifacts",
            "prefix": "campaigns/three-site",
            "age_binary": "/usr/bin/age",
            "workspace": "/srv/trading-bot-three-site-staging-data/webapp-fi-source-exchange",
            "controller_age_recipient": "age1" + "a" * 40,
            "webapp_fi_age_recipient": "age1" + "c" * 40,
            "webapp_ir_age_recipient": "age1" + "d" * 40,
            "maximum_plaintext_bytes": 1024 * 1024,
        }

    def _build(self, **changes):
        values = {
            "created_at": "2026-07-30T00:00:00Z",
            "campaign_binding_payload": self.binding_payload,
            "signer_enrollment_certificate_payload": packet.canonical_json_bytes(self.certificate) + b"\n",
            "source_role_config_payload": packet.canonical_json_bytes(self.role) + b"\n",
            "static_assets_provenance_payload": packet.canonical_json_bytes(self.static_provenance) + b"\n",
            "source_transport_policy_payload": packet.canonical_json_bytes(self.exchange_policy) + b"\n",
            "packet_id": "packet-one",
            "controller_signer": self.controller_key,
            "controller_public_key_base64": self.controller_public,
        }
        values.update(changes)
        return packet.build_control_packet_payload_with_signer(**values)

    def test_raw_key_packet_construction_api_is_not_exported(self):
        self.assertFalse(hasattr(packet, "build_control_packet_payload"))
        self.assertFalse(hasattr(packet, "controller_public_key_from_private"))
        self.assertFalse(hasattr(packet, "_sign"))
        self.assertNotIn(
            "controller_signing_private_key",
            inspect.signature(packet.build_control_packet_payload_with_signer).parameters,
        )

    def test_packet_is_canonical_url_free_and_binds_all_four_inputs(self):
        payload = self._build()
        value = json.loads(payload.decode("ascii"))
        self.assertEqual(payload, packet.canonical_json_bytes(value) + b"\n")
        self.assertNotIn(b"://", payload)
        self.assertNotIn(b"presigned", payload.lower())
        self.assertEqual(self.binding, value["campaign_binding"]["payload"])
        self.assertEqual(
            packet.sha256_bytes(self.binding_payload),
            value["campaign_binding"]["payload_sha256"],
        )
        self.assertEqual(
            packet.SOURCE_TRANSPORT_POLICY_SCHEMA,
            value["source_transport_policy"]["payload"]["schema"],
        )
        self.assertEqual(
            "s3.ir-thr-at1.arvanstorage.ir",
            value["source_transport_policy"]["payload"]["endpoint_host"],
        )
        verified = packet.verify_control_packet_payload(
            payload=payload,
            pinned_controller_public_key_base64=self.controller_public,
            expected_campaign_binding_identity=packet.binding_identity_from_payload(self.binding_payload),
        )
        self.assertEqual("packet-one", verified["packet_id"])
        self.assertEqual(self.binding_payload, verified["campaign_binding_payload"])
        self.assertEqual(packet.sha256_bytes(self.binding_payload), verified["campaign_binding_sha256"])
        self.assertEqual(packet.expected_source_signing_key_path(CAMPAIGN), json.loads(verified["source_role_config_payload"])["source_signing_private_key_file"])
        self.assertEqual(
            hashlib.sha256(verified["source_transport_policy_payload"]).hexdigest(),
            verified["source_transport_policy_sha256"],
        )

    def test_packet_rejects_wrong_campaign_key_path_and_signature_tampering(self):
        role = dict(self.role)
        role["source_signing_private_key_file"] = "/etc/trading-bot-three-site/campaigns/other/webapp-fi/source-signing-ed25519.raw"
        with self.assertRaisesRegex(packet.StaticProvenanceControlPacketError, "campaign-derived"):
            self._build(source_role_config_payload=packet.canonical_json_bytes(role) + b"\n")

        role = dict(self.role)
        role["tooling"] = {"control_commit": self.control_commit, "control_tree": "f" * 40}
        with self.assertRaisesRegex(packet.StaticProvenanceControlPacketError, "tooling binding"):
            self._build(source_role_config_payload=packet.canonical_json_bytes(role) + b"\n")

        payload = self._build()
        value = json.loads(payload.decode("ascii"))
        value["controller_signature"]["signature_base64"] = "A" * 88
        with self.assertRaisesRegex(packet.StaticProvenanceControlPacketError, "signature"):
            packet.verify_control_packet_payload(
                payload=packet.canonical_json_bytes(value) + b"\n",
                pinned_controller_public_key_base64=self.controller_public,
                expected_campaign_binding_identity=packet.binding_identity_from_payload(self.binding_payload),
            )

    def test_packet_rejects_a_url_embedded_in_signed_static_provenance(self):
        static_provenance = dict(self.static_provenance)
        files = [{"path": "https://example.invalid/capability", "sha256": "7" * 64, "bytes": 13}]
        static_provenance["files"] = files
        static_provenance["files_sha256"] = packet.sha256_bytes(packet.canonical_json_bytes(files))
        unsigned = {key: item for key, item in static_provenance.items() if key != "controller_signature"}
        static_provenance["controller_signature"] = _sign_with_fixture_key(
            self.controller_key,
            unsigned,
            domain=packet.STATIC_ASSET_SIGNATURE_DOMAIN,
        )
        with self.assertRaisesRegex(packet.StaticProvenanceControlPacketError, "forbidden transient URL"):
            self._build(static_assets_provenance_payload=packet.canonical_json_bytes(static_provenance) + b"\n")

    def test_packet_rejects_a_source_key_base64_alias_of_the_controller_key(self):
        certificate = dict(self.certificate)
        source_alias = _noncanonical_base64_alias(self.controller_public)
        certificate["source_signing_public_key_base64"] = source_alias
        certificate["source_signing_key_id"] = packet.public_key_id(source_alias)
        unsigned = {key: item for key, item in certificate.items() if key != "controller_signature"}
        certificate["controller_signature"] = _sign_with_fixture_key(
            self.controller_key,
            unsigned,
            domain=packet.SIGNER_ENROLLMENT_SIGNATURE_DOMAIN,
        )
        with self.assertRaisesRegex(packet.StaticProvenanceControlPacketError, "must be distinct"):
            self._build(signer_enrollment_certificate_payload=packet.canonical_json_bytes(certificate) + b"\n")

    def test_controller_builder_uses_fixed_campaign_derived_create_only_output(self):
        campaign_root = self.root / "controller-campaigns"
        source_phase = campaign_root / CAMPAIGN / builder.SOURCE_PHASE_DIRECTORY
        source_phase.mkdir(mode=0o700, parents=True)
        for path in (campaign_root, campaign_root / CAMPAIGN, source_phase):
            os.chmod(path, 0o700)
        _canonical_private_json(source_phase / builder.CAMPAIGN_BINDING_FILENAME, self.binding)
        output_root = self.root / "controller-output"
        output_root.mkdir(mode=0o700)
        certificate = _canonical_private_json(self.root / "inputs" / "certificate.json", self.certificate)
        role = _canonical_private_json(self.root / "inputs" / "role.json", self.role)
        static = _canonical_private_json(self.root / "inputs" / "static.json", self.static_provenance)
        policy = _canonical_private_json(self.root / "inputs" / "policy.json", self.exchange_policy)
        authority = SimpleNamespace(
            signer=self.controller_key,
            signing_key=SimpleNamespace(
                public_key_base64=self.controller_public,
                key_id=packet.public_key_id(self.controller_public),
                receipt_sha256=packet.sha256_bytes(b"fixture-controller-signing-receipt"),
            ),
            campaign_binding=SimpleNamespace(
                campaign_id=CAMPAIGN,
                application_release_sha=self.release,
                application_release_tree=self.release_tree,
                expected_alembic_revision=REVISION,
                control_commit=self.control_commit,
                control_tree=self.control_tree,
                binding_sha256=self.binding["binding_sha256"],
            ),
        )
        with (
            patch.object(builder, "CONTROLLER_CAMPAIGN_ROOT", campaign_root),
            patch.object(builder, "CONTROL_PACKET_ROOT", output_root),
            patch.object(
                builder,
                "_load_campaign_bound_controller_signer",
                return_value=authority,
            ),
        ):
            plan = builder.build_static_provenance_control_packet(
                campaign_id=CAMPAIGN,
                packet_id="packet-builder-one",
                signer_enrollment_certificate=certificate,
                source_role_config=role,
                static_assets_provenance=static,
                source_transport_policy=policy,
                apply=False,
                created_at="2026-07-30T00:00:00Z",
            )
            output = Path(plan["output_path"])
            self.assertFalse(output.exists())
            result = builder.build_static_provenance_control_packet(
                campaign_id=CAMPAIGN,
                packet_id="packet-builder-one",
                signer_enrollment_certificate=certificate,
                source_role_config=role,
                static_assets_provenance=static,
                source_transport_policy=policy,
                apply=True,
                created_at="2026-07-30T00:00:00Z",
            )
            self.assertEqual("sealed", result["status"])
            self.assertEqual(0o700, stat.S_IMODE(output.parent.stat().st_mode))
            self.assertEqual(0o600, stat.S_IMODE(output.stat().st_mode))
            with self.assertRaisesRegex(builder.StaticProvenanceControlPacketBuildError, "reuse or overwrite"):
                builder.build_static_provenance_control_packet(
                    campaign_id=CAMPAIGN,
                    packet_id="packet-builder-one",
                    signer_enrollment_certificate=certificate,
                    source_role_config=role,
                    static_assets_provenance=static,
                    source_transport_policy=policy,
                    apply=True,
                    created_at="2026-07-30T00:00:00Z",
                )
            wrong_binding_unsigned = {
                "schema": packet.CAMPAIGN_BINDING_SCHEMA,
                "status": "bound",
                "campaign_id": "other-campaign-20260730",
                "application": self.binding["application"],
                "tooling": self.binding["tooling"],
            }
            wrong_binding = {
                **wrong_binding_unsigned,
                "binding_sha256": packet.sha256_bytes(packet.canonical_json_bytes(wrong_binding_unsigned)),
            }
            _canonical_private_json(source_phase / builder.CAMPAIGN_BINDING_FILENAME, wrong_binding)
            with self.assertRaisesRegex(builder.StaticProvenanceControlPacketBuildError, "does not match"):
                builder.build_static_provenance_control_packet(
                    campaign_id=CAMPAIGN,
                    packet_id="packet-builder-other-binding",
                    signer_enrollment_certificate=certificate,
                    source_role_config=role,
                    static_assets_provenance=static,
                    source_transport_policy=policy,
                    apply=False,
                    created_at="2026-07-30T00:00:00Z",
                )
