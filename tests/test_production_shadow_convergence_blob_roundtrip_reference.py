from __future__ import annotations

import copy
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import production_shadow_convergence_blob_roundtrip as BLOB
from scripts import production_shadow_convergence_blob_roundtrip_reference as MODULE


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


def _proof(*, source: str, target: str, role: str, marker: str) -> dict[str, object]:
    document: dict[str, object] = {
        "schema": BLOB.ROLE_PROOF_SCHEMA,
        "status": "observed",
        **IDENTITY,
        "role": role,
        "scope": BLOB.SCOPE,
        "source_site": source,
        "target_site": target,
        "observed_at": NOW.isoformat().replace("+00:00", "Z"),
        "object_storage_private": True,
        "object_storage_versioned": True,
        "local_object_set_sha256": marker * 64,
        "local_object_count": 2,
        "local_keyring_sha256": str(int(marker) + 1) * 64,
        "versioned_readback_set_sha256": str(int(marker) + 2) * 64,
        "readback_sample_count": 1,
        "missing_object_count": 0,
        "corrupt_object_count": 0,
        "proof_sha256": BLOB.ZERO_SHA256,
    }
    document["proof_sha256"] = BLOB._proof_digest(document)
    return document


def observation() -> dict[str, object]:
    proofs: list[dict[str, object]] = []
    for source, target in BLOB.PAIRS:
        marker = "1" if source == "webapp_fi" else "4"
        proofs.extend((_proof(source=source, target=target, role=source, marker=marker), _proof(source=source, target=target, role=target, marker=marker)))
    return BLOB.build_observation(**IDENTITY, role_proofs=proofs, now=NOW)


class BlobRoundtripObservationReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "evidence"
        self.root.mkdir(mode=0o700)
        self.root.chmod(0o700)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_installs_only_canonical_root_only_observation_and_reads_it_back(self) -> None:
        reference, outcome = MODULE.install_observation(observation(), evidence_root=self.root, identity=IDENTITY, now=NOW)
        self.assertEqual(outcome, "created")
        self.assertEqual(reference.path, MODULE.canonical_observation_path(evidence_root=self.root, sha256=reference.sha256))
        self.assertEqual(reference.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(reference.path.stat().st_uid, 0)
        self.assertEqual(MODULE.validate_reference(MODULE.reference_document(reference), evidence_root=self.root, identity=IDENTITY, now=NOW), observation())
        self.assertNotIn("object_key", reference.path.read_text(encoding="ascii"))
        self.assertFalse((self.root / "convergence-gate" / "source-set").exists())

    def test_identical_retry_is_readback_verified_but_drift_fails_closed(self) -> None:
        record = observation()
        reference, _ = MODULE.install_observation(record, evidence_root=self.root, identity=IDENTITY, now=NOW)
        self.assertEqual(MODULE.install_observation(record, evidence_root=self.root, identity=IDENTITY, now=NOW), (reference, "reused"))
        reference.path.write_bytes(b"{}\n")
        reference.path.chmod(0o600)
        with self.assertRaisesRegex(MODULE.BlobRoundtripObservationReferenceError, "differs and will not be replaced"):
            MODULE.install_observation(record, evidence_root=self.root, identity=IDENTITY, now=NOW)
        wrong = MODULE.reference_document(reference)
        wrong["path"] = str(reference.path.with_name("other.json"))
        with self.assertRaisesRegex(MODULE.BlobRoundtripObservationReferenceError, "path"):
            MODULE.validate_reference(wrong, evidence_root=self.root, identity=IDENTITY, now=NOW)

    def test_rejects_invalid_observation_or_nonprivate_layout_before_publication(self) -> None:
        invalid = copy.deepcopy(observation())
        invalid["missing_object_count"] = 1
        invalid["blob_state_sha256"] = BLOB._observation_digest(invalid)
        with self.assertRaises(MODULE.BlobRoundtripObservationReferenceError):
            MODULE.install_observation(invalid, evidence_root=self.root, identity=IDENTITY, now=NOW)
        self.assertFalse((self.root / "convergence-gate").exists())
        self.root.chmod(0o755)
        with self.assertRaisesRegex(MODULE.BlobRoundtripObservationReferenceError, "0700"):
            MODULE.install_observation(observation(), evidence_root=self.root, identity=IDENTITY, now=NOW)

    def test_requires_root(self) -> None:
        with mock.patch.object(os, "geteuid", return_value=1000):
            with self.assertRaises(MODULE.BlobRoundtripObservationReferenceError):
                MODULE.install_observation(observation(), evidence_root=self.root, identity=IDENTITY, now=NOW)


if __name__ == "__main__":
    unittest.main()
