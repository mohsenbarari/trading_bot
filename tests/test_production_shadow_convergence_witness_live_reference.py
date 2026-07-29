from __future__ import annotations

import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.writer_witness_contract import sign_witness_lease_proof
from scripts import production_shadow_convergence_witness_live as WITNESS
from scripts import production_shadow_convergence_witness_live_reference as MODULE


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


def observation() -> dict[str, object]:
    private = Ed25519PrivateKey.generate()
    private_text = base64.b64encode(
        private.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
    ).decode("ascii")
    public = base64.b64encode(
        private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    proof = sign_witness_lease_proof(
        holder_site="webapp_fi",
        writer_epoch=1,
        lease_id="lease-1",
        issued_at=NOW - timedelta(seconds=10),
        expires_at=NOW + timedelta(seconds=120),
        witness_transition_id="transition-1",
        private_key_base64=private_text,
    )
    document: dict[str, object] = {
        "schema": WITNESS.OBSERVATION_SCHEMA,
        "status": "observed",
        **IDENTITY,
        "observed_at": NOW.isoformat().replace("+00:00", "Z"),
        "witness_public_key": public,
        "witness_public_key_sha256": hashlib.sha256(public.encode("ascii")).hexdigest(),
        "signed_proof": proof,
        "signed_proof_sha256": WITNESS._proof_sha256(proof),
        "witness_status_receipt_sha256": "f" * 64,
        "lease_live_readback_sha256": WITNESS._lease_readback_digest(
            proof_sha256=WITNESS._proof_sha256(proof),
            receipt_sha256="f" * 64,
            observed_at=NOW.isoformat().replace("+00:00", "Z"),
        ),
    }
    return document


class WitnessLiveObservationReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "evidence"
        self.root.mkdir(mode=0o700)
        self.root.chmod(0o700)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_installs_only_canonical_root_only_observation_and_reads_back(self) -> None:
        document = observation()
        reference, outcome = MODULE.install_observation(
            document,
            evidence_root=self.root,
            identity=IDENTITY,
            journal_started_at=JOURNAL_STARTED,
            now=NOW,
        )
        self.assertEqual(outcome, "created")
        self.assertEqual(
            reference.path.parent,
            self.root / "convergence-gate" / "observation-inputs" / "incoming" / "pure-observations",
        )
        self.assertEqual(reference.path.name, f"witness_live.{reference.sha256}.json")
        self.assertEqual(reference.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            MODULE.validate_reference(
                MODULE.reference_document(reference),
                evidence_root=self.root,
                identity=IDENTITY,
                journal_started_at=JOURNAL_STARTED,
                now=NOW,
            ),
            document,
        )
        self.assertNotIn("private_key", reference.path.read_text(encoding="ascii"))

    def test_exact_collision_reuses_but_drift_is_not_replaced(self) -> None:
        document = observation()
        reference, _outcome = MODULE.install_observation(
            document, evidence_root=self.root, identity=IDENTITY,
            journal_started_at=JOURNAL_STARTED, now=NOW,
        )
        duplicate, outcome = MODULE.install_observation(
            document, evidence_root=self.root, identity=IDENTITY,
            journal_started_at=JOURNAL_STARTED, now=NOW,
        )
        self.assertEqual((duplicate, outcome), (reference, "reused"))
        reference.path.write_bytes(b'{"not":"the-original"}\n')
        reference.path.chmod(0o600)
        with self.assertRaisesRegex(MODULE.WitnessLiveObservationReferenceError, "cannot be published safely|differs"):
            MODULE.install_observation(
                document, evidence_root=self.root, identity=IDENTITY,
                journal_started_at=JOURNAL_STARTED, now=NOW,
            )

    def test_rejects_reference_path_digest_and_expired_observation(self) -> None:
        document = observation()
        reference, _outcome = MODULE.install_observation(
            document, evidence_root=self.root, identity=IDENTITY,
            journal_started_at=JOURNAL_STARTED, now=NOW,
        )
        bad_path = MODULE.reference_document(reference)
        bad_path["path"] = str(self.root / "elsewhere.json")
        with self.assertRaises(MODULE.WitnessLiveObservationReferenceError):
            MODULE.validate_reference(
                bad_path, evidence_root=self.root, identity=IDENTITY,
                journal_started_at=JOURNAL_STARTED, now=NOW,
            )
        bad_digest = MODULE.reference_document(reference)
        bad_digest["sha256"] = "1" * 64
        with self.assertRaises(MODULE.WitnessLiveObservationReferenceError):
            MODULE.validate_reference(
                bad_digest, evidence_root=self.root, identity=IDENTITY,
                journal_started_at=JOURNAL_STARTED, now=NOW,
            )
        expired = copy.deepcopy(document)
        expired["signed_proof"]["expires_at"] = (NOW + timedelta(seconds=89)).isoformat()  # type: ignore[index]
        expired["signed_proof_sha256"] = WITNESS._proof_sha256(expired["signed_proof"])  # type: ignore[arg-type]
        with self.assertRaises(MODULE.WitnessLiveObservationReferenceError):
            MODULE.install_observation(
                expired, evidence_root=self.root, identity=IDENTITY,
                journal_started_at=JOURNAL_STARTED, now=NOW,
            )

    def test_requires_root_and_private_directories(self) -> None:
        with mock.patch.object(MODULE.os, "geteuid", return_value=1000):
            with self.assertRaises(MODULE.WitnessLiveObservationReferenceError):
                MODULE.install_observation(
                    observation(), evidence_root=self.root, identity=IDENTITY,
                    journal_started_at=JOURNAL_STARTED, now=NOW,
                )
        self.root.chmod(0o755)
        with self.assertRaises(MODULE.WitnessLiveObservationReferenceError):
            MODULE.install_observation(
                observation(), evidence_root=self.root, identity=IDENTITY,
                journal_started_at=JOURNAL_STARTED, now=NOW,
            )

    def test_readback_rejects_a_nonprivate_canonical_directory(self) -> None:
        reference, _outcome = MODULE.install_observation(
            observation(), evidence_root=self.root, identity=IDENTITY,
            journal_started_at=JOURNAL_STARTED, now=NOW,
        )
        reference.path.parent.chmod(0o750)
        with self.assertRaises(MODULE.WitnessLiveObservationReferenceError):
            MODULE.validate_reference(
                MODULE.reference_document(reference),
                evidence_root=self.root,
                identity=IDENTITY,
                journal_started_at=JOURNAL_STARTED,
                now=NOW,
            )


if __name__ == "__main__":
    unittest.main()
