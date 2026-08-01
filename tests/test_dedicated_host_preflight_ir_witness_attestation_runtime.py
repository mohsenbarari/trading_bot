"""Root-file boundary tests for the local WA-IR Witness attester."""

from __future__ import annotations

from datetime import datetime, timezone
import base64
import hashlib
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import core.dedicated_host_preflight_ir_witness_attestation as attestation
import core.dedicated_host_preflight_ir_witness_attestation_runtime as runtime
from core.dedicated_host_preflight_receipt import PREFLIGHT_RECEIPT_SCHEMA, canonical_json_bytes
from scripts.dedicated_host_preflight_manifest import EXPECTED_HOSTS, READONLY_REQUEST_SCHEMA


NOW = datetime(2026, 7, 31, 14, 0, tzinfo=timezone.utc)
CAMPAIGN_ID = "preflight-wa-ir-runtime-20260731"
OPERATION_ID = "11111111-2222-4333-8444-555555555555"
RELEASE_SHA = "a" * 40
MANIFEST_SHA256 = "b" * 64


def _public(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def _key_id(public: bytes) -> str:
    return "ed25519-sha256:" + hashlib.sha256(public).hexdigest()


def _request(key_id: str) -> bytes:
    readonly = {
        "schema": READONLY_REQUEST_SCHEMA,
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "role": "webapp_ir",
        "manifest_sha256": MANIFEST_SHA256,
    }
    value = {
        "schema": attestation.WA_IR_WITNESS_ATTESTATION_REQUEST_SCHEMA,
        "version": 1,
        "purpose": attestation.WA_IR_WITNESS_ATTESTATION_REQUEST_PURPOSE,
        "readonly_request": readonly,
        "readonly_request_sha256": hashlib.sha256(canonical_json_bytes(readonly) + b"\n").hexdigest(),
        "attestation_id": "66666666-7777-4888-8999-aaaaaaaaaaaa",
        "nonce": "A" * 22,
        "maximum_validity_seconds": 120,
        "wa_ir_attestation_key_id": key_id,
    }
    return canonical_json_bytes(value) + b"\n"


def _receipt() -> bytes:
    binding = EXPECTED_HOSTS["webapp_ir"]
    value = {
        "schema": PREFLIGHT_RECEIPT_SCHEMA,
        "status": "observed",
        "observation_mode": "read-only",
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "role": "webapp_ir",
        "instance": {
            "provider": "arvan_ecc",
            "server_id": binding["instance_id"],
            "public_ipv4": binding["public_ip"],
        },
        "manifest_sha256": MANIFEST_SHA256,
        "observed_at": "2026-07-31T14:00:00Z",
        "observation": {
            "role_marker": "webapp_ir",
            "release": {"state": "present", "release_sha": RELEASE_SHA, "clean": True},
            "runtime": {
                "docker_state": "active",
                "container_count": 1,
                "matrix_process_count": 0,
                "current_link_present": True,
            },
            "staging_mount": {"present": False, "filesystem": None, "available_bytes": None, "options": []},
        },
    }
    return canonical_json_bytes(value) + b"\n"


def _key_record(signer: Ed25519PrivateKey, *, purpose: str) -> bytes:
    private = signer.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public = _public(signer)
    return canonical_json_bytes(
        {
            "schema": attestation.WA_IR_WITNESS_ATTESTATION_KEY_SCHEMA,
            "version": 1,
            "purpose": purpose,
            "algorithm": "ed25519",
            "private_key_base64": base64.b64encode(private).decode("ascii"),
            "public_key_sha256": hashlib.sha256(public).hexdigest(),
            "key_id": _key_id(public),
        }
    ) + b"\n"


class WaIrWitnessAttestationRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.request_path = self.root / "request.json"
        self.key_path = self.root / "key.json"
        self.signer = Ed25519PrivateKey.generate()
        self.request_path.write_bytes(_request(_key_id(_public(self.signer))))
        self.key_path.write_bytes(
            _key_record(
                self.signer,
                purpose=attestation.WA_IR_WITNESS_ATTESTATION_KEY_PURPOSE,
            )
        )
        self.request_path.chmod(0o600)
        self.key_path.chmod(0o400)
        self.paths = patch.multiple(
            runtime,
            FIXED_WA_IR_WITNESS_ATTESTATION_REQUEST_FILE=self.request_path,
            FIXED_WA_IR_WITNESS_ATTESTATION_KEY_FILE=self.key_path,
        )
        self.paths.start()
        self.addCleanup(self.paths.stop)
        self.addCleanup(self.temporary.cleanup)

    def config(self, **changes: object) -> runtime.RootOwnedWaIrWitnessAttestationRuntimeConfig:
        values: dict[str, object] = {"enabled": True}
        values.update(changes)
        return runtime.RootOwnedWaIrWitnessAttestationRuntimeConfig(**values)

    def test_root_owned_inputs_produce_a_verifiable_local_envelope(self) -> None:
        envelope = runtime.attest_root_owned_wa_ir_preflight_receipt(
            canonical_receipt=_receipt(),
            config=self.config(),
            now=NOW,
        )
        request = attestation.parse_wa_ir_witness_attestation_request(self.request_path.read_bytes())
        verified = attestation.verify_wa_ir_witness_attestation_envelope(
            canonical_envelope=envelope,
            expected_request=request,
            expected_wa_ir_public_key=_public(self.signer),
            now=NOW,
        )
        self.assertEqual(_receipt(), verified.canonical_receipt)

    def test_disabled_nonroot_insecure_key_and_wrong_purpose_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            runtime.DedicatedHostPreflightIrWitnessAttestationRuntimeError,
            "WA_IR_WITNESS_ATTESTATION_RUNTIME_DISABLED",
        ):
            runtime.attest_root_owned_wa_ir_preflight_receipt(
                canonical_receipt=_receipt(),
                config=self.config(enabled=False),
                now=NOW,
            )
        with patch.object(runtime.os, "geteuid", return_value=1000), self.assertRaisesRegex(
            runtime.DedicatedHostPreflightIrWitnessAttestationRuntimeError,
            "WA_IR_WITNESS_ATTESTATION_ROOT_RUNTIME_REQUIRED",
        ):
            runtime.attest_root_owned_wa_ir_preflight_receipt(
                canonical_receipt=_receipt(), config=self.config(), now=NOW
            )

        self.key_path.chmod(0o600)
        with self.assertRaisesRegex(
            runtime.DedicatedHostPreflightIrWitnessAttestationRuntimeError,
            "WA_IR_WITNESS_ATTESTATION_KEY_FILE_UNSAFE",
        ):
            runtime.attest_root_owned_wa_ir_preflight_receipt(
                canonical_receipt=_receipt(), config=self.config(), now=NOW
            )
        self.key_path.chmod(0o400)
        self.key_path.write_bytes(_key_record(self.signer, purpose="writer-witness-key"))
        self.key_path.chmod(0o400)
        with self.assertRaisesRegex(
            runtime.DedicatedHostPreflightIrWitnessAttestationRuntimeError,
            "WA_IR_WITNESS_ATTESTATION_KEY_FILE_INVALID",
        ):
            runtime.attest_root_owned_wa_ir_preflight_receipt(
                canonical_receipt=_receipt(), config=self.config(), now=NOW
            )
