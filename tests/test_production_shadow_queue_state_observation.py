from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest

from scripts import orchestrate_production_shadow_convergence_gate as BRIDGE
from scripts import production_shadow_queue_state_observation as MODULE


NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
CAMPAIGN_ID = "22222222-2222-4222-8222-222222222222"
OPERATION_ID = "11111111-1111-4111-8111-111111111111"
RELEASE_SHA = "a" * 40
TREE_SHA = "b" * 40
MANIFEST_SHA = "c" * 64
PLAN_SHA = "d" * 64
APPROVAL_SHA = "e" * 64


def identity() -> dict[str, str]:
    return {
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "release_tree_sha": TREE_SHA,
        "manifest_sha256": MANIFEST_SHA,
        "plan_sha256": PLAN_SHA,
        "approval_sha256": APPROVAL_SHA,
        "phase_started_at": (NOW - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
    }


def snapshot(role: str, *, observed_at: datetime = NOW, counters: dict[str, int] | None = None) -> dict[str, object]:
    document: dict[str, object] = {
        "schema": MODULE.ROLE_SNAPSHOT_SCHEMA,
        "status": "observed-redacted",
        **{key: value for key, value in identity().items() if key != "phase_started_at"},
        "role": role,
        "captured_at": (observed_at - timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "queue_counters": counters or {counter: 0 for counter in MODULE.QUEUE_COUNTERS},
        "queue_state_sha256": "0" * 64,
    }
    document["queue_state_sha256"] = MODULE._role_snapshot_digest(document)
    return document


def role_snapshots() -> dict[str, dict[str, object]]:
    return {role: snapshot(role) for role in MODULE.RUNTIME_ROLES}


class QueueStateObservationTests(unittest.TestCase):
    def test_reduces_exact_redacted_snapshots_to_gate_compatible_zero_observation(self) -> None:
        document = MODULE.build_queue_observation(
            identity=identity(),
            role_snapshots=role_snapshots(),
            now=NOW,
        )
        self.assertEqual(document["schema"], BRIDGE.QUEUE_OBSERVATION_SCHEMA)
        self.assertEqual(set(document), {
            "schema", "status", "campaign_id", "operation_id", "release_sha",
            "release_tree_sha", "manifest_sha256", "plan_sha256", "approval_sha256",
            "observed_at", *MODULE.QUEUE_COUNTERS, "queue_state_sha256",
        })
        self.assertTrue(all(document[counter] == 0 for counter in MODULE.QUEUE_COUNTERS))
        context = SimpleNamespace(
            manifest={
                "campaign_id": CAMPAIGN_ID,
                "operation_id": OPERATION_ID,
                "release_sha": RELEASE_SHA,
                "release_tree_sha": TREE_SHA,
                "artifacts": {"cutover_approval_sha256": APPROVAL_SHA},
            },
            manifest_sha256=MANIFEST_SHA,
            plan_sha256=PLAN_SHA,
        )
        self.assertEqual(BRIDGE._validate_queue_observation(document, context=context), NOW)

    def test_rejects_missing_role_nonzero_counter_identity_and_digest_drift(self) -> None:
        cases: list[tuple[str, dict[str, dict[str, object]]]] = []
        missing = role_snapshots()
        del missing["webapp_ir"]
        cases.append(("snapshot set", missing))
        live = role_snapshots()
        live["bot_fi"] = snapshot("bot_fi", counters={
            **{counter: 0 for counter in MODULE.QUEUE_COUNTERS},
            "due_otp_job_count": 1,
        })
        cases.append(("live or due", live))
        drift = role_snapshots()
        drift["webapp_fi"] = copy.deepcopy(drift["webapp_fi"])
        drift["webapp_fi"]["manifest_sha256"] = "f" * 64
        cases.append(("identity", drift))
        digest = role_snapshots()
        digest["webapp_ir"] = copy.deepcopy(digest["webapp_ir"])
        digest["webapp_ir"]["queue_state_sha256"] = "f" * 64
        cases.append(("digest", digest))
        for label, snapshots in cases:
            with self.subTest(label=label):
                with self.assertRaises(MODULE.QueueStateObservationError):
                    MODULE.build_queue_observation(
                        identity=identity(), role_snapshots=snapshots, now=NOW
                    )

    def test_rejects_stale_or_skewed_redacted_snapshots(self) -> None:
        stale = role_snapshots()
        stale["bot_fi"] = snapshot("bot_fi", observed_at=NOW - timedelta(minutes=16))
        with self.assertRaisesRegex(MODULE.QueueStateObservationError, "freshness"):
            MODULE.build_queue_observation(identity=identity(), role_snapshots=stale, now=NOW)
        skewed = role_snapshots()
        skewed["webapp_ir"] = snapshot("webapp_ir", observed_at=NOW - timedelta(minutes=3))
        with self.assertRaisesRegex(MODULE.QueueStateObservationError, "skew"):
            MODULE.build_queue_observation(identity=identity(), role_snapshots=skewed, now=NOW)

    def test_validator_rejects_nonzero_or_freshness_drift_without_reducer_state(self) -> None:
        snapshots = role_snapshots()
        document = MODULE.build_queue_observation(
            identity=identity(), role_snapshots=snapshots, now=NOW
        )
        altered = dict(document)
        altered["telegram_lease_count"] = 1
        with self.assertRaisesRegex(MODULE.QueueStateObservationError, "live or due"):
            MODULE.validate_queue_observation(
                altered, identity=identity(), role_snapshots=snapshots, now=NOW
            )
        altered = dict(document)
        altered["observed_at"] = (NOW - timedelta(minutes=16)).isoformat().replace("+00:00", "Z")
        with self.assertRaisesRegex(MODULE.QueueStateObservationError, "freshness"):
            MODULE.validate_queue_observation(
                altered, identity=identity(), role_snapshots=snapshots, now=NOW
            )


if __name__ == "__main__":
    unittest.main()
