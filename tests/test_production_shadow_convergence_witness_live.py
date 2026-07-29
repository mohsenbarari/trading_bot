from __future__ import annotations

import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.writer_witness_contract import sign_witness_lease_proof
from scripts import production_shadow_convergence_witness_live as MODULE


IDENTITY = {
    "campaign_id": "22222222-2222-4222-8222-222222222222",
    "operation_id": "11111111-1111-4111-8111-111111111111",
    "release_sha": "a" * 40,
    "release_tree_sha": "b" * 40,
    "manifest_sha256": "c" * 64,
    "plan_sha256": "d" * 64,
    "approval_sha256": "e" * 64,
}
NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
JOURNAL_STARTED = NOW - timedelta(minutes=1)


def keypair() -> tuple[str, str]:
    private = Ed25519PrivateKey.generate()
    private_raw = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_raw = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return (
        base64.b64encode(private_raw).decode("ascii"),
        base64.b64encode(public_raw).decode("ascii"),
    )


def witness_input(
    *,
    expires_at: datetime = NOW + timedelta(seconds=120),
    observed_at: datetime = NOW,
) -> dict[str, object]:
    private, public = keypair()
    proof = sign_witness_lease_proof(
        holder_site="webapp_fi",
        writer_epoch=1,
        lease_id="lease-1",
        issued_at=NOW - timedelta(seconds=10),
        expires_at=expires_at,
        witness_transition_id="transition-1",
        private_key_base64=private,
    )
    document: dict[str, object] = {
        "schema": MODULE.INPUT_SCHEMA,
        "status": "observed",
        **IDENTITY,
        "journal_started_at": JOURNAL_STARTED.isoformat().replace("+00:00", "Z"),
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "witness_public_key": public,
        "witness_public_key_sha256": hashlib.sha256(public.encode("ascii")).hexdigest(),
        "signed_proof": proof,
        "signed_proof_sha256": MODULE._proof_sha256(proof),
        "witness_status_receipt_sha256": "f" * 64,
        "input_sha256": MODULE.ZERO_SHA256,
    }
    document["input_sha256"] = MODULE._input_digest(document)
    return document


class WitnessLiveContractTests(unittest.TestCase):
    def test_builds_gate_compatible_live_observation_from_signed_input(self) -> None:
        source = witness_input()
        observation = MODULE.build_observation(
            **IDENTITY,
            journal_started_at=JOURNAL_STARTED,
            witness_input=source,
            now=NOW,
        )
        self.assertEqual(
            MODULE.validate_observation(
                observation,
                identity=IDENTITY,
                journal_started_at=JOURNAL_STARTED,
                now=NOW,
            ),
            observation,
        )
        self.assertEqual(observation["schema"], MODULE.OBSERVATION_SCHEMA)
        self.assertEqual(observation["signed_proof"], source["signed_proof"])
        self.assertNotIn("private_key", json.dumps(observation, sort_keys=True))

    def test_input_payload_and_identity_journal_binding_are_canonical(self) -> None:
        source = witness_input()
        payload = MODULE._canonical_json(source) + b"\n"
        self.assertEqual(MODULE.parse_input_payload(payload), source)
        with self.assertRaises(MODULE.WitnessLiveContractError):
            MODULE.parse_input_payload(payload + b"\n")
        drift = copy.deepcopy(source)
        drift["approval_sha256"] = "1" * 64
        drift["input_sha256"] = MODULE._input_digest(drift)
        with self.assertRaises(MODULE.WitnessLiveContractError):
            MODULE.build_observation(
                **IDENTITY,
                journal_started_at=JOURNAL_STARTED,
                witness_input=drift,
                now=NOW,
            )
        after_journal = witness_input(observed_at=JOURNAL_STARTED - timedelta(seconds=1))
        with self.assertRaises(MODULE.WitnessLiveContractError):
            MODULE.build_observation(
                **IDENTITY,
                journal_started_at=JOURNAL_STARTED,
                witness_input=after_journal,
                now=NOW,
            )

    def test_rejects_signature_receipt_or_remaining_lease_drift(self) -> None:
        source = witness_input()
        tampered = copy.deepcopy(source)
        tampered["signed_proof"]["writer_epoch"] = 2  # type: ignore[index]
        tampered["signed_proof_sha256"] = MODULE._proof_sha256(tampered["signed_proof"])  # type: ignore[arg-type]
        tampered["input_sha256"] = MODULE._input_digest(tampered)
        with self.assertRaises(MODULE.WitnessLiveContractError):
            MODULE.build_observation(
                **IDENTITY,
                journal_started_at=JOURNAL_STARTED,
                witness_input=tampered,
                now=NOW,
            )
        near_expiry = witness_input(expires_at=NOW + timedelta(seconds=89))
        with self.assertRaises(MODULE.WitnessLiveContractError):
            MODULE.build_observation(
                **IDENTITY,
                journal_started_at=JOURNAL_STARTED,
                witness_input=near_expiry,
                now=NOW,
            )
        receipt_drift = copy.deepcopy(source)
        receipt_drift["witness_status_receipt_sha256"] = "1" * 64
        receipt_drift["input_sha256"] = MODULE._input_digest(receipt_drift)
        observation = MODULE.build_observation(
            **IDENTITY,
            journal_started_at=JOURNAL_STARTED,
            witness_input=receipt_drift,
            now=NOW,
        )
        observation["lease_live_readback_sha256"] = "2" * 64
        with self.assertRaises(MODULE.WitnessLiveContractError):
            MODULE.validate_observation(
                observation,
                identity=IDENTITY,
                journal_started_at=JOURNAL_STARTED,
                now=NOW,
            )

    def test_rejects_stale_or_future_input(self) -> None:
        for observed_at in (
            NOW - MODULE.MAX_INPUT_AGE - timedelta(seconds=1),
            NOW + MODULE.MAX_FUTURE_SKEW + timedelta(seconds=1),
        ):
            with self.subTest(observed_at=observed_at):
                with self.assertRaises(MODULE.WitnessLiveContractError):
                    MODULE.build_observation(
                        **IDENTITY,
                        journal_started_at=JOURNAL_STARTED,
                        witness_input=witness_input(observed_at=observed_at),
                        now=NOW,
                    )


if __name__ == "__main__":
    unittest.main()
