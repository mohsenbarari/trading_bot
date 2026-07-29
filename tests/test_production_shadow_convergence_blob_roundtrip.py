from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
import unittest

from scripts import production_shadow_convergence_blob_roundtrip as MODULE


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


def proof(
    *,
    source: str,
    target: str,
    role: str,
    observed_at: datetime = NOW,
    object_set: str = "1" * 64,
    keyring: str = "2" * 64,
    versioned_readback: str = "3" * 64,
    count: int = 2,
    samples: int = 1,
) -> dict[str, object]:
    document: dict[str, object] = {
        "schema": MODULE.ROLE_PROOF_SCHEMA,
        "status": "observed",
        **IDENTITY,
        "role": role,
        "scope": MODULE.SCOPE,
        "source_site": source,
        "target_site": target,
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "object_storage_private": True,
        "object_storage_versioned": True,
        "local_object_set_sha256": object_set,
        "local_object_count": count,
        "local_keyring_sha256": keyring,
        "versioned_readback_set_sha256": versioned_readback,
        "readback_sample_count": samples,
        "missing_object_count": 0,
        "corrupt_object_count": 0,
        "proof_sha256": MODULE.ZERO_SHA256,
    }
    document["proof_sha256"] = MODULE._proof_digest(document)
    return document


def proofs() -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for source, target in MODULE.PAIRS:
        object_set = "1" * 64 if source == "webapp_fi" else "4" * 64
        keyring = "2" * 64 if source == "webapp_fi" else "5" * 64
        versioned = "3" * 64 if source == "webapp_fi" else "6" * 64
        result.extend(
            (
                proof(
                    source=source,
                    target=target,
                    role=source,
                    object_set=object_set,
                    keyring=keyring,
                    versioned_readback=versioned,
                ),
                proof(
                    source=source,
                    target=target,
                    role=target,
                    object_set=object_set,
                    keyring=keyring,
                    versioned_readback=versioned,
                ),
            )
        )
    return result


class BlobRoundtripContractTests(unittest.TestCase):
    def test_builds_gate_compatible_redacted_observation_from_four_proofs(self) -> None:
        observation = MODULE.build_observation(**IDENTITY, role_proofs=proofs(), now=NOW)
        self.assertEqual(MODULE.validate_observation(observation, identity=IDENTITY, now=NOW), observation)
        self.assertEqual(observation["schema"], MODULE.OBSERVATION_SCHEMA)
        self.assertEqual(observation["status"], "observed")
        self.assertEqual(len(observation["scopes"]), 2)
        serialized = json.dumps(observation, sort_keys=True)
        self.assertNotIn("version_id", serialized)
        self.assertNotIn("object_key", serialized)

    def test_canonical_payload_and_identity_binding_are_strict(self) -> None:
        document = proof(source="webapp_fi", target="webapp_ir", role="webapp_fi")
        payload = MODULE._canonical_json(document) + b"\n"
        self.assertEqual(MODULE.parse_role_proof_payload(payload), document)
        with self.assertRaises(MODULE.BlobRoundtripContractError):
            MODULE.parse_role_proof_payload(payload + b" ")
        drift = copy.deepcopy(document)
        drift["approval_sha256"] = "f" * 64
        drift["proof_sha256"] = MODULE._proof_digest(drift)
        with self.assertRaises(MODULE.BlobRoundtripContractError):
            MODULE.validate_role_proof(
                drift,
                identity=IDENTITY,
                source_site="webapp_fi",
                target_site="webapp_ir",
                role="webapp_fi",
                now=NOW,
            )

    def test_rejects_coverage_keyring_or_exact_version_readback_drift(self) -> None:
        cases: list[list[dict[str, object]]] = []
        missing = proofs()[:-1]
        cases.append(missing)
        keyring = proofs()
        keyring[1]["local_keyring_sha256"] = "9" * 64
        keyring[1]["proof_sha256"] = MODULE._proof_digest(keyring[1])
        cases.append(keyring)
        versioned = proofs()
        versioned[1]["versioned_readback_set_sha256"] = "9" * 64
        versioned[1]["proof_sha256"] = MODULE._proof_digest(versioned[1])
        cases.append(versioned)
        for role_proofs in cases:
            with self.subTest(role_proofs=len(role_proofs)):
                with self.assertRaises(MODULE.BlobRoundtripContractError):
                    MODULE.build_observation(**IDENTITY, role_proofs=role_proofs, now=NOW)

    def test_rejects_stale_or_skewed_timestamps_and_observation_digest_drift(self) -> None:
        stale = proofs()
        for item in stale:
            item["observed_at"] = (NOW - MODULE.MAX_PROOF_AGE - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
            item["proof_sha256"] = MODULE._proof_digest(item)
        with self.assertRaises(MODULE.BlobRoundtripContractError):
            MODULE.build_observation(**IDENTITY, role_proofs=stale, now=NOW)
        skewed = proofs()
        skewed[-1]["observed_at"] = (NOW + MODULE.MAX_PROOF_SKEW + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        skewed[-1]["proof_sha256"] = MODULE._proof_digest(skewed[-1])
        with self.assertRaises(MODULE.BlobRoundtripContractError):
            MODULE.build_observation(**IDENTITY, role_proofs=skewed, now=NOW)
        observation = MODULE.build_observation(**IDENTITY, role_proofs=proofs(), now=NOW)
        observation["blob_state_sha256"] = "f" * 64
        with self.assertRaises(MODULE.BlobRoundtripContractError):
            MODULE.validate_observation(observation, identity=IDENTITY, now=NOW)


if __name__ == "__main__":
    unittest.main()
