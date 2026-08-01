"""Adversarial tests for the literal central Witness-evidence SSH adapter.

All SSH activity is an in-memory runner.  No test opens a network connection
or contacts WA-IR/Witness; the only filesystem reads exercise temporary
root-owned known-hosts and identity files.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import dedicated_host_preflight_ir_witness_attestation as attestation
from core import dedicated_host_preflight_pinned_ssh_delivery as ssh
from core import dedicated_host_preflight_witness_evidence_pinned_ssh_delivery as delivery
from core.dedicated_host_preflight_controller import DELIVERY_CONTRACT_BY_ROLE, DedicatedHostTarget
from core.dedicated_host_preflight_receipt import PREFLIGHT_RECEIPT_SCHEMA, canonical_json_bytes
from scripts.dedicated_host_preflight_manifest import EXPECTED_HOSTS, READONLY_REQUEST_SCHEMA


CAMPAIGN_ID = "witness-evidence-ssh-20260731"
OPERATION_ID = "11111111-2222-4333-8444-555555555555"
RELEASE_SHA = "a" * 40
MANIFEST_SHA256 = "b" * 64
ATTESTATION_ID = "66666666-7777-4888-8999-aaaaaaaaaaaa"
NONCE = "A" * 22


def _public(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def _key_id(public_key: bytes) -> str:
    return "ed25519-sha256:" + hashlib.sha256(public_key).hexdigest()


def _request_payload(key_id: str) -> bytes:
    request = {
        "schema": READONLY_REQUEST_SCHEMA,
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "role": "webapp_ir",
        "manifest_sha256": MANIFEST_SHA256,
    }
    return canonical_json_bytes(
        {
            "schema": attestation.WA_IR_WITNESS_ATTESTATION_REQUEST_SCHEMA,
            "version": 1,
            "purpose": attestation.WA_IR_WITNESS_ATTESTATION_REQUEST_PURPOSE,
            "readonly_request": request,
            "readonly_request_sha256": hashlib.sha256(
                canonical_json_bytes(request) + b"\n"
            ).hexdigest(),
            "attestation_id": ATTESTATION_ID,
            "nonce": NONCE,
            "maximum_validity_seconds": 120,
            "wa_ir_attestation_key_id": key_id,
        }
    ) + b"\n"


def _receipt() -> bytes:
    expected = EXPECTED_HOSTS["webapp_ir"]
    return canonical_json_bytes(
        {
            "schema": PREFLIGHT_RECEIPT_SCHEMA,
            "status": "observed",
            "observation_mode": "read-only",
            "campaign_id": CAMPAIGN_ID,
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "role": "webapp_ir",
            "instance": {
                "provider": "arvan_ecc",
                "server_id": expected["instance_id"],
                "public_ipv4": expected["public_ip"],
            },
            "manifest_sha256": MANIFEST_SHA256,
            "observed_at": "2026-07-31T00:00:00Z",
            "observation": {
                "role_marker": "webapp_ir",
                "release": {"state": "present", "release_sha": RELEASE_SHA, "clean": True},
                "runtime": {
                    "docker_state": "active",
                    "container_count": 0,
                    "matrix_process_count": 0,
                    "current_link_present": False,
                },
                "staging_mount": {
                    "present": False,
                    "filesystem": None,
                    "available_bytes": None,
                    "options": [],
                },
            },
        }
    ) + b"\n"


def _host_key_blob(role: str) -> bytes:
    key_type = b"ssh-ed25519"
    public = hashlib.sha256(("witness-evidence-host:" + role).encode("ascii")).digest()
    return (
        len(key_type).to_bytes(4, "big")
        + key_type
        + len(public).to_bytes(4, "big")
        + public
    )


def _known_host_line(role: str) -> str:
    return " ".join(
        (
            EXPECTED_HOSTS[role]["public_ip"],
            "ssh-ed25519",
            base64.b64encode(_host_key_blob(role)).decode("ascii"),
        )
    )


def _target(role: str) -> DedicatedHostTarget:
    route, phase = DELIVERY_CONTRACT_BY_ROLE[role]
    expected = EXPECTED_HOSTS[role]
    return DedicatedHostTarget(
        role=role,
        instance_id=expected["instance_id"],
        public_ipv4=expected["public_ip"],
        region=expected["region"],
        host_key_sha256=hashlib.sha256(_host_key_blob(role)).hexdigest(),
        delivery_route=route,
        delivery_phase=phase,
    )


class _Runner:
    def __init__(self, evidence: bytes) -> None:
        self.evidence = evidence
        self.calls: list[delivery.PinnedSshWitnessEvidenceInvocation] = []

    async def run(
        self, *, invocation: delivery.PinnedSshWitnessEvidenceInvocation
    ) -> delivery.PinnedSshWitnessEvidenceRunnerResult:
        self.calls.append(invocation)
        return delivery.PinnedSshWitnessEvidenceRunnerResult(0, self.evidence)


@unittest.skipUnless(__import__("os").geteuid() == 0, "root-only adapter contract")
class WitnessEvidencePinnedSshDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="witness-evidence-ssh-")
        root = Path(self.temporary.name)
        root.chmod(0o700)
        self.security = root / "security"
        self.security.mkdir(mode=0o700)
        self.known_hosts = self.security / "known_hosts"
        self.known_hosts.write_text(
            "\n".join(_known_host_line(role) for role in ("bot_fi", "webapp_fi", "witness"))
            + "\n",
            encoding="ascii",
        )
        self.known_hosts.chmod(0o600)
        self.identity = self.security / "identity_ed25519"
        self.identity.write_bytes(b"test-private-identity")
        self.identity.chmod(0o600)
        self.ssh_binary = root / "ssh"
        self.ssh_binary.write_bytes(b"test-ssh")
        self.ssh_binary.chmod(0o755)
        self.wa_ir_signer = Ed25519PrivateKey.generate()
        self.witness_signer = Ed25519PrivateKey.generate()
        self.wa_ir_public = _public(self.wa_ir_signer)
        self.witness_public = _public(self.witness_signer)
        self.request = attestation.parse_wa_ir_witness_attestation_request(
            _request_payload(_key_id(self.wa_ir_public))
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _paths(self):
        return mock.patch.multiple(
            ssh,
            FIXED_PINNED_SSH_BINARY=self.ssh_binary,
            FIXED_DEDICATED_HOST_PREFLIGHT_KNOWN_HOSTS=self.known_hosts,
            FIXED_DEDICATED_HOST_PREFLIGHT_IDENTITY_FILE=self.identity,
        )

    def _evidence(self) -> bytes:
        now = datetime.now(timezone.utc)
        envelope = attestation.build_wa_ir_witness_attestation_envelope(
            request=self.request,
            canonical_receipt=_receipt(),
            signer=self.wa_ir_signer,
            issued_at=now,
        )
        verified = attestation.verify_wa_ir_witness_attestation_envelope(
            canonical_envelope=envelope,
            expected_request=self.request,
            expected_wa_ir_public_key=self.wa_ir_public,
            now=now,
        )
        return attestation.build_witness_preflight_evidence(
            wa_ir_attestation=verified,
            witness_signer=self.witness_signer,
            accepted_at=now,
        )

    def _adapter(self, runner: _Runner) -> delivery.PinnedSshWitnessEvidenceAgentDelivery:
        return delivery.PinnedSshWitnessEvidenceAgentDelivery(
            config=delivery.PinnedSshWitnessEvidenceDeliveryConfig(
                expected_request=self.request,
                expected_wa_ir_public_key=self.wa_ir_public,
                expected_witness_public_key=self.witness_public,
                enabled=True,
            ),
            witness_target=_target("witness"),
            runner=runner,
        )

    def test_literal_witness_command_returns_only_verified_inner_wa_ir_receipt(self) -> None:
        runner = _Runner(self._evidence())
        with self._paths():
            result = asyncio.run(
                self._adapter(runner).collect_readonly_receipt(
                    target=_target("webapp_ir"),
                    request_bytes=self.request.readonly_request_bytes,
                    request_sha256=self.request.readonly_request_sha256,
                    receipt_path="dedicated-host-preflight/webapp_ir/receipt.json",
                )
            )
        self.assertEqual(result["receipt_bytes"], _receipt())
        self.assertEqual(result["role"], "webapp_ir")
        self.assertEqual(result["delivery_route"], "witness-dual-signed-preflight-evidence")
        self.assertEqual(result["delivery_phase"], delivery.FIXED_WITNESS_EVIDENCE_REMOTE_COMMAND)
        self.assertEqual(len(runner.calls), 1)
        invocation = runner.calls[0]
        self.assertEqual(invocation.stdin_bytes, b"")
        self.assertIn(
            "preflight-witness-evidence@" + EXPECTED_HOSTS["witness"]["public_ip"],
            invocation.arguments,
        )
        self.assertEqual(invocation.arguments[-1], delivery.FIXED_WITNESS_EVIDENCE_REMOTE_COMMAND)
        self.assertNotIn("webapp_ir@", " ".join(invocation.arguments))

    def test_rejects_unpinned_request_and_tampered_evidence_without_fallback(self) -> None:
        runner = _Runner(self._evidence())
        with self._paths(), self.assertRaisesRegex(
            delivery.DedicatedHostPreflightWitnessEvidencePinnedSshDeliveryError,
            "PINNED_SSH_WITNESS_EVIDENCE_REQUEST_INVALID",
        ):
            asyncio.run(
                self._adapter(runner).collect_readonly_receipt(
                    target=_target("webapp_ir"),
                    request_bytes=self.request.readonly_request_bytes + b" ",
                    request_sha256=self.request.readonly_request_sha256,
                    receipt_path="dedicated-host-preflight/webapp_ir/receipt.json",
                )
            )
        self.assertEqual(runner.calls, [])

        altered = bytearray(self._evidence())
        altered[-2] = ord(" ")
        invalid_runner = _Runner(bytes(altered))
        with self._paths(), self.assertRaisesRegex(
            delivery.DedicatedHostPreflightWitnessEvidencePinnedSshDeliveryError,
            "PINNED_SSH_WITNESS_EVIDENCE_INVALID",
        ):
            asyncio.run(
                self._adapter(invalid_runner).collect_readonly_receipt(
                    target=_target("webapp_ir"),
                    request_bytes=self.request.readonly_request_bytes,
                    request_sha256=self.request.readonly_request_sha256,
                    receipt_path="dedicated-host-preflight/webapp_ir/receipt.json",
                )
            )
        self.assertEqual(len(invalid_runner.calls), 1)

    def test_default_off_and_non_witness_transport_target_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            delivery.DedicatedHostPreflightWitnessEvidencePinnedSshDeliveryError,
            "PINNED_SSH_WITNESS_EVIDENCE_DISABLED",
        ):
            asyncio.run(
                delivery.PinnedSshWitnessEvidenceAgentDelivery(
                    witness_target=_target("witness"),
                    runner=_Runner(self._evidence()),
                ).collect_readonly_receipt(
                    target=_target("webapp_ir"),
                    request_bytes=self.request.readonly_request_bytes,
                    request_sha256=self.request.readonly_request_sha256,
                    receipt_path="dedicated-host-preflight/webapp_ir/receipt.json",
                )
            )

        with self._paths(), self.assertRaisesRegex(
            delivery.DedicatedHostPreflightWitnessEvidencePinnedSshDeliveryError,
            "WITNESS_TARGET_INVALID",
        ):
            asyncio.run(
                delivery.PinnedSshWitnessEvidenceAgentDelivery(
                    config=delivery.PinnedSshWitnessEvidenceDeliveryConfig(
                        expected_request=self.request,
                        expected_wa_ir_public_key=self.wa_ir_public,
                        expected_witness_public_key=self.witness_public,
                        enabled=True,
                    ),
                    witness_target=_target("bot_fi"),
                    runner=_Runner(self._evidence()),
                ).collect_readonly_receipt(
                    target=_target("webapp_ir"),
                    request_bytes=self.request.readonly_request_bytes,
                    request_sha256=self.request.readonly_request_sha256,
                    receipt_path="dedicated-host-preflight/webapp_ir/receipt.json",
                )
            )
