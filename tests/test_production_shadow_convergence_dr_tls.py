from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
import unittest

from scripts import production_shadow_convergence_dr_tls as MODULE


IDENTITY = {"campaign_id": "22222222-2222-4222-8222-222222222222", "operation_id": "11111111-1111-4111-8111-111111111111", "release_sha": "a" * 40, "release_tree_sha": "b" * 40, "manifest_sha256": "c" * 64, "plan_sha256": "d" * 64, "approval_sha256": "e" * 64}
NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


def proof(*, origin: str, destination: str, role: str, observed_at: datetime = NOW, protocol: str = "TLSv1.3", certificate: str = "1" * 64, handshake: str = "2" * 64, ca: str = "3" * 64) -> dict[str, object]:
    document: dict[str, object] = {"schema": MODULE.PROOF_SCHEMA, "status": "observed", **IDENTITY, "role": role, "origin_role": origin, "destination_role": destination, "observed_at": observed_at.isoformat().replace("+00:00", "Z"), "protocol": protocol, "status_code": 200, "certificate_sha256": certificate, "peer_handshake_sha256": handshake, "ca_bundle_sha256": ca, "proof_sha256": MODULE.ZERO_SHA256}
    document["proof_sha256"] = MODULE._proof_digest(document)
    return document


def proofs() -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for index, (origin, destination) in enumerate(MODULE.PAIRS, start=1):
        certificate, handshake, ca = str(index) * 64, str((index + 6) % 9 + 1) * 64, str((index + 1) % 9 + 1) * 64
        result.extend((proof(origin=origin, destination=destination, role=origin, certificate=certificate, handshake=handshake, ca=ca), proof(origin=origin, destination=destination, role=destination, certificate=certificate, handshake=handshake, ca=ca)))
    return result


class DrTlsContractTests(unittest.TestCase):
    def test_reduces_twelve_two_sided_proofs_to_six_gate_compatible_peers(self) -> None:
        observation = MODULE.build_observation(**IDENTITY, peer_proofs=proofs(), now=NOW)
        self.assertEqual(MODULE.validate_observation(observation, identity=IDENTITY, now=NOW), observation)
        self.assertEqual(len(observation["peers"]), 6)
        self.assertEqual({(item["origin_role"], item["destination_role"]) for item in observation["peers"]}, set(MODULE.PAIRS))
        self.assertNotIn("endpoint", json.dumps(observation, sort_keys=True))

    def test_proof_payload_identity_and_digest_are_strict(self) -> None:
        source = proof(origin="bot_fi", destination="webapp_fi", role="bot_fi")
        payload = MODULE._canonical_json(source) + b"\n"
        self.assertEqual(MODULE.parse_proof_payload(payload), source)
        with self.assertRaises(MODULE.DrTlsContractError):
            MODULE.parse_proof_payload(payload + b" ")
        source["plan_sha256"] = "9" * 64
        source["proof_sha256"] = MODULE._proof_digest(source)
        with self.assertRaises(MODULE.DrTlsContractError):
            MODULE.validate_proof(source, identity=IDENTITY, origin_role="bot_fi", destination_role="webapp_fi", role="bot_fi", now=NOW)

    def test_rejects_unredacted_or_unsuccessful_proof(self) -> None:
        unredacted = proof(origin="bot_fi", destination="webapp_fi", role="bot_fi")
        unredacted["endpoint"] = "https://internal.example.invalid/healthz"
        unredacted["proof_sha256"] = MODULE._proof_digest(unredacted)
        with self.assertRaises(MODULE.DrTlsContractError):
            MODULE.validate_proof(
                unredacted,
                identity=IDENTITY,
                origin_role="bot_fi",
                destination_role="webapp_fi",
                role="bot_fi",
                now=NOW,
            )
        unsuccessful = proof(origin="bot_fi", destination="webapp_fi", role="bot_fi")
        unsuccessful["status_code"] = 503
        unsuccessful["proof_sha256"] = MODULE._proof_digest(unsuccessful)
        with self.assertRaises(MODULE.DrTlsContractError):
            MODULE.validate_proof(
                unsuccessful,
                identity=IDENTITY,
                origin_role="bot_fi",
                destination_role="webapp_fi",
                role="bot_fi",
                now=NOW,
            )

    def test_rejects_missing_two_sided_or_tls_drift(self) -> None:
        missing = proofs()[:-1]
        with self.assertRaises(MODULE.DrTlsContractError):
            MODULE.build_observation(**IDENTITY, peer_proofs=missing, now=NOW)
        altered = proofs()
        altered[1]["protocol"] = "TLSv1.1"
        altered[1]["proof_sha256"] = MODULE._proof_digest(altered[1])
        with self.assertRaises(MODULE.DrTlsContractError):
            MODULE.build_observation(**IDENTITY, peer_proofs=altered, now=NOW)
        mismatch = proofs()
        mismatch[1]["peer_handshake_sha256"] = "9" * 64
        mismatch[1]["proof_sha256"] = MODULE._proof_digest(mismatch[1])
        with self.assertRaises(MODULE.DrTlsContractError):
            MODULE.build_observation(**IDENTITY, peer_proofs=mismatch, now=NOW)

    def test_rejects_stale_skewed_or_peer_set_digest_drift(self) -> None:
        stale = proofs()
        for item in stale:
            item["observed_at"] = (NOW - MODULE.MAX_PROOF_AGE - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
            item["proof_sha256"] = MODULE._proof_digest(item)
        with self.assertRaises(MODULE.DrTlsContractError):
            MODULE.build_observation(**IDENTITY, peer_proofs=stale, now=NOW)
        skewed = proofs()
        skewed[-1]["observed_at"] = (NOW + MODULE.MAX_PROOF_SKEW + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        skewed[-1]["proof_sha256"] = MODULE._proof_digest(skewed[-1])
        with self.assertRaises(MODULE.DrTlsContractError):
            MODULE.build_observation(
                **IDENTITY,
                peer_proofs=skewed,
                now=NOW + MODULE.MAX_PROOF_SKEW + timedelta(minutes=1),
            )
        observation = MODULE.build_observation(**IDENTITY, peer_proofs=proofs(), now=NOW)
        altered = copy.deepcopy(observation)
        altered["peer_set_sha256"] = "f" * 64
        with self.assertRaises(MODULE.DrTlsContractError):
            MODULE.validate_observation(altered, identity=IDENTITY, now=NOW)


if __name__ == "__main__":
    unittest.main()
