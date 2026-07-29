from __future__ import annotations

import copy
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import production_shadow_convergence_dr_tls as TLS
from scripts import production_shadow_convergence_dr_tls_reference as MODULE


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


def observation() -> dict[str, object]:
    peers = [
        {
            "origin_role": origin,
            "destination_role": destination,
            "protocol": "TLSv1.3",
            "status_code": 200,
            "certificate_sha256": str(index) * 64,
            "peer_handshake_sha256": str(index + 1) * 64,
            "ca_bundle_sha256": str(index + 2) * 64,
        }
        for index, (origin, destination) in enumerate(TLS.PAIRS, start=1)
    ]
    document: dict[str, object] = {
        "schema": TLS.OBSERVATION_SCHEMA,
        "status": "observed",
        **IDENTITY,
        "observed_at": NOW.isoformat().replace("+00:00", "Z"),
        "peers": peers,
        "peer_set_sha256": TLS._sha256(peers),
    }
    return document


class DrTlsObservationReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "evidence"
        self.root.mkdir(mode=0o700)
        self.root.chmod(0o700)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_installs_only_canonical_root_only_redacted_observation_and_reads_it_back(self) -> None:
        reference, outcome = MODULE.install_observation(
            observation(), evidence_root=self.root, identity=IDENTITY, now=NOW
        )
        expected = self.root / "convergence-gate" / "observation-inputs" / "incoming" / "pure-observations"
        self.assertEqual(outcome, "created")
        self.assertEqual(reference.path.parent, expected)
        self.assertEqual(reference.path.name, f"dr_tls.{reference.sha256}.json")
        self.assertEqual(reference.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(reference.path.stat().st_uid, 0)
        self.assertEqual(
            MODULE.validate_reference(
                MODULE.reference_document(reference),
                evidence_root=self.root,
                identity=IDENTITY,
                now=NOW,
            ),
            observation(),
        )
        self.assertNotIn("endpoint", reference.path.read_text(encoding="ascii"))

    def test_exact_collision_is_reused_but_drift_fails_closed(self) -> None:
        document = observation()
        reference, _outcome = MODULE.install_observation(
            document, evidence_root=self.root, identity=IDENTITY, now=NOW
        )
        duplicate, outcome = MODULE.install_observation(
            document, evidence_root=self.root, identity=IDENTITY, now=NOW
        )
        self.assertEqual((duplicate, outcome), (reference, "reused"))
        reference.path.chmod(0o600)
        reference.path.write_bytes(b'{"not":"the-original"}\n')
        with self.assertRaisesRegex(
            MODULE.DrTlsObservationReferenceError,
            "cannot be published safely|differs and will not be replaced",
        ):
            MODULE.install_observation(
                document, evidence_root=self.root, identity=IDENTITY, now=NOW
            )

    def test_rejects_path_digest_and_observation_drift(self) -> None:
        reference, _outcome = MODULE.install_observation(
            observation(), evidence_root=self.root, identity=IDENTITY, now=NOW
        )
        bad_path = MODULE.reference_document(reference)
        bad_path["path"] = str(self.root / "somewhere-else.json")
        with self.assertRaises(MODULE.DrTlsObservationReferenceError):
            MODULE.validate_reference(
                bad_path, evidence_root=self.root, identity=IDENTITY, now=NOW
            )
        bad_digest = MODULE.reference_document(reference)
        bad_digest["sha256"] = "f" * 64
        with self.assertRaises(MODULE.DrTlsObservationReferenceError):
            MODULE.validate_reference(
                bad_digest, evidence_root=self.root, identity=IDENTITY, now=NOW
            )
        tampered = copy.deepcopy(observation())
        tampered["peers"][0]["status_code"] = 503
        with self.assertRaises(MODULE.DrTlsObservationReferenceError):
            MODULE.install_observation(
                tampered, evidence_root=self.root, identity=IDENTITY, now=NOW
            )

    def test_requires_root_and_rejects_nonprivate_evidence_root(self) -> None:
        with mock.patch.object(os, "geteuid", return_value=1000):
            with self.assertRaises(MODULE.DrTlsObservationReferenceError):
                MODULE.install_observation(
                    observation(), evidence_root=self.root, identity=IDENTITY, now=NOW
                )
        self.root.chmod(0o755)
        with self.assertRaises(MODULE.DrTlsObservationReferenceError):
            MODULE.install_observation(
                observation(), evidence_root=self.root, identity=IDENTITY, now=NOW
            )

    def test_readback_rejects_a_nonprivate_canonical_directory(self) -> None:
        reference, _outcome = MODULE.install_observation(
            observation(), evidence_root=self.root, identity=IDENTITY, now=NOW
        )
        reference.path.parent.chmod(0o750)
        with self.assertRaises(MODULE.DrTlsObservationReferenceError):
            MODULE.validate_reference(
                MODULE.reference_document(reference),
                evidence_root=self.root,
                identity=IDENTITY,
                now=NOW,
            )


if __name__ == "__main__":
    unittest.main()
