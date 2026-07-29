from __future__ import annotations

import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.writer_witness_contract import sign_witness_lease_proof
from scripts import production_shadow_convergence_witness_live as WITNESS
from scripts import production_shadow_convergence_witness_live_exporter as MODULE


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
    return (
        base64.b64encode(private.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
        )).decode("ascii"),
        base64.b64encode(private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )).decode("ascii"),
    )


def record_and_policy() -> tuple[dict[str, object], dict[str, str]]:
    private, public = keypair()
    policy = {
        "schema": MODULE.EXPORTER_POLICY_SCHEMA,
        "exporter_relative_path": "scripts/future_witness_live_exporter.py",
        "exporter_sha256": "9" * 64,
        "witness_public_key": public,
        "witness_public_key_sha256": hashlib.sha256(public.encode("ascii")).hexdigest(),
    }
    proof = sign_witness_lease_proof(
        holder_site="webapp_fi", writer_epoch=1, lease_id="lease-1",
        issued_at=NOW - timedelta(seconds=10), expires_at=NOW + timedelta(seconds=120),
        witness_transition_id="transition-1", private_key_base64=private,
    )
    result: dict[str, object] = {
        "schema": MODULE.EXPORTER_RECORD_SCHEMA, "status": "observed", **IDENTITY,
        "journal_started_at": JOURNAL_STARTED.isoformat().replace("+00:00", "Z"),
        "observed_at": NOW.isoformat().replace("+00:00", "Z"),
        "exporter_relative_path": policy["exporter_relative_path"],
        "exporter_sha256": policy["exporter_sha256"],
        "exporter_release_sha": IDENTITY["release_sha"],
        "exporter_release_tree_sha": IDENTITY["release_tree_sha"],
        "witness_public_key": public,
        "witness_public_key_sha256": policy["witness_public_key_sha256"],
        "signed_proof": proof,
        "signed_proof_sha256": WITNESS._proof_sha256(proof),
        "witness_status_receipt_sha256": "f" * 64,
        "exporter_record_sha256": "0" * 64,
    }
    result["exporter_record_sha256"] = MODULE._record_digest(result)
    return result, policy


class WitnessLiveExporterContractTests(unittest.TestCase):
    def test_reduces_only_exact_release_and_pinned_key_record(self) -> None:
        record, policy = record_and_policy()
        candidate = MODULE.reduce_exporter_record(
            record, identity=IDENTITY, journal_started_at=JOURNAL_STARTED,
            exporter_policy=policy, now=NOW,
        )
        self.assertEqual(candidate["schema"], WITNESS.INPUT_SCHEMA)
        self.assertEqual(candidate["witness_public_key"], policy["witness_public_key"])
        self.assertEqual(
            WITNESS.build_observation(**IDENTITY, journal_started_at=JOURNAL_STARTED,
                                      witness_input=candidate, now=NOW)["status"],
            "observed",
        )

    def test_rejects_self_supplied_key_exporter_or_release_drift(self) -> None:
        record, policy = record_and_policy()
        for key, value in (
            ("exporter_sha256", "1" * 64),
            ("exporter_release_sha", "b" * 40),
            ("witness_public_key", keypair()[1]),
        ):
            with self.subTest(key=key):
                drift = copy.deepcopy(record)
                drift[key] = value
                if key == "witness_public_key":
                    drift["witness_public_key_sha256"] = hashlib.sha256(value.encode("ascii")).hexdigest()
                drift["exporter_record_sha256"] = MODULE._record_digest(drift)
                with self.assertRaises(MODULE.WitnessLiveExporterError):
                    MODULE.reduce_exporter_record(
                        drift, identity=IDENTITY, journal_started_at=JOURNAL_STARTED,
                        exporter_policy=policy, now=NOW,
                    )

    def test_requires_canonical_payload_and_record_digest(self) -> None:
        record, policy = record_and_policy()
        payload = MODULE._canonical_json(record) + b"\n"
        self.assertEqual(MODULE.parse_exporter_record_payload(payload), record)
        with self.assertRaises(MODULE.WitnessLiveExporterError):
            MODULE.parse_exporter_record_payload(payload + b"\n")
        record["exporter_record_sha256"] = "1" * 64
        with self.assertRaises(MODULE.WitnessLiveExporterError):
            MODULE.reduce_exporter_record(
                record, identity=IDENTITY, journal_started_at=JOURNAL_STARTED,
                exporter_policy=policy, now=NOW,
            )


if __name__ == "__main__":
    unittest.main()
