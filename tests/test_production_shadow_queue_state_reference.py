from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import unittest

from scripts import production_shadow_queue_state_observation as OBSERVATION
from scripts import production_shadow_queue_state_reference as MODULE


NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
IDENTITY = {
    "campaign_id": "22222222-2222-4222-8222-222222222222",
    "operation_id": "11111111-1111-4111-8111-111111111111",
    "release_sha": "a" * 40,
    "release_tree_sha": "b" * 40,
    "manifest_sha256": "c" * 64,
    "plan_sha256": "d" * 64,
    "approval_sha256": "e" * 64,
    "phase_started_at": (NOW - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
}


def role_snapshot(role: str) -> dict[str, object]:
    document: dict[str, object] = {
        "schema": OBSERVATION.ROLE_SNAPSHOT_SCHEMA,
        "status": "observed-redacted",
        **{key: value for key, value in IDENTITY.items() if key != "phase_started_at"},
        "role": role,
        "captured_at": (NOW - timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
        "observed_at": NOW.isoformat().replace("+00:00", "Z"),
        "queue_counters": {counter: 0 for counter in OBSERVATION.QUEUE_COUNTERS},
        "queue_state_sha256": "0" * 64,
    }
    document["queue_state_sha256"] = OBSERVATION._role_snapshot_digest(document)
    return document


def observation() -> dict[str, object]:
    return OBSERVATION.build_queue_observation(
        identity=IDENTITY,
        role_snapshots={role: role_snapshot(role) for role in OBSERVATION.RUNTIME_ROLES},
        now=NOW,
    )


class QueueStateReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "evidence"
        self.root.mkdir(mode=0o700)
        os.chmod(self.root, 0o700)
        self.manifest = {"deployment": {"controller_evidence_root": os.fspath(self.root)}}

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_installs_at_the_only_pure_input_path_and_validates_readback(self) -> None:
        record = observation()
        reference = MODULE.install_queue_state_observation(
            record, manifest=self.manifest, identity=IDENTITY, now=NOW
        )
        self.assertEqual(
            reference.path,
            MODULE.canonical_queue_state_observation_path(
                self.manifest, digest=reference.sha256
            ),
        )
        self.assertEqual(
            reference.path.parent,
            self.root / "convergence-gate" / "observation-inputs" / "incoming" / "pure-observations",
        )
        self.assertEqual(stat_mode(reference.path), 0o600)
        self.assertEqual(
            MODULE.validate_queue_state_observation_reference(
                reference, manifest=self.manifest, identity=IDENTITY, now=NOW
            ),
            record,
        )

    def test_identical_retry_is_readback_verified_but_collision_or_reference_drift_fails_closed(self) -> None:
        record = observation()
        reference = MODULE.install_queue_state_observation(
            record, manifest=self.manifest, identity=IDENTITY, now=NOW
        )
        self.assertEqual(
            MODULE.install_queue_state_observation(
                record, manifest=self.manifest, identity=IDENTITY, now=NOW
            ),
            reference,
        )
        reference.path.write_text("{}\n", encoding="ascii")
        os.chmod(reference.path, 0o600)
        with self.assertRaisesRegex(MODULE.QueueStateReferenceError, "digest"):
            MODULE.validate_queue_state_observation_reference(
                reference, manifest=self.manifest, identity=IDENTITY, now=NOW
            )
        with self.assertRaisesRegex(MODULE.QueueStateReferenceError, "collision"):
            MODULE.install_queue_state_observation(
                record, manifest=self.manifest, identity=IDENTITY, now=NOW
            )
        wrong = copy.copy(reference)
        object.__setattr__(wrong, "path", reference.path.with_name("other.json"))
        with self.assertRaisesRegex(MODULE.QueueStateReferenceError, "path"):
            MODULE.validate_queue_state_observation_reference(
                wrong, manifest=self.manifest, identity=IDENTITY, now=NOW
            )

    def test_rejects_non_root_only_layout_and_invalid_record_before_write(self) -> None:
        os.chmod(self.root, 0o755)
        with self.assertRaisesRegex(MODULE.QueueStateReferenceError, "0700"):
            MODULE.install_queue_state_observation(
                observation(), manifest=self.manifest, identity=IDENTITY, now=NOW
            )
        os.chmod(self.root, 0o700)
        invalid = observation()
        invalid["telegram_lease_count"] = 1
        with self.assertRaises(MODULE.QueueStateReferenceError):
            MODULE.install_queue_state_observation(
                invalid, manifest=self.manifest, identity=IDENTITY, now=NOW
            )
        self.assertFalse((self.root / "convergence-gate").exists())


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


if __name__ == "__main__":
    unittest.main()
