from __future__ import annotations

import copy
import unittest

from core.three_site_sync_timing import verify_sync_timing_evidence
from scripts.build_three_site_sync_timing_evidence import MANIFEST_SCHEMA, TimingBuildError, build
from scripts.collect_three_site_sync_timing_snapshot import (
    SCHEMA as SITE_SCHEMA,
    _like_prefix,
)
from tests.three_site_sync_timing_fixtures import make_sync_timing_artifact


def _inputs(scenario_id: str):  # noqa: ANN202
    timing = make_sync_timing_artifact(scenario_id)
    release = "a" * 40
    prefix = f"corr:{scenario_id}:"
    snapshots = {
        site: {
            "schema": SITE_SCHEMA,
            "site": site,
            "logical_authority": "foreign" if site == "bot_fi" else "webapp",
            "release_sha": release,
            "correlation_prefix": prefix,
            "captured_at": timing["captured_at"],
            "clock": timing["clock"]["sites"][site],
            "events": [],
            "deliveries": [],
            "receipts": [],
            "backlog": {"pending_events": 0, "oldest_pending_at": None},
        }
        for site in ("bot_fi", "webapp_fi", "webapp_ir")
    }
    manifest_samples = []
    for sample in timing["samples"]:
        manifest_samples.append(
            {
                "sample_id": sample["sample_id"],
                "correlation_id": sample["correlation_id"],
                "route": sample["route"],
                "controller_observed_duration_seconds": sample[
                    "controller_observed_duration_seconds"
                ],
            }
        )
        for hop in sample["hops"]:
            source = snapshots[hop["source_site"]]
            destination = snapshots[hop["destination_site"]]
            source["events"].append(
                {
                    "event_id": hop["source_event_id"],
                    "correlation_id": sample["correlation_id"],
                    "origin_physical_site": sample["hops"][0]["source_site"],
                    "producer_epoch": 1,
                    "producer_sequence": len(source["events"]) + 1,
                    "envelope_hash": hop["source_envelope_hash"],
                    "created_at": hop["origin_event_created_at"],
                    "payload_bytes": hop["payload_bytes"],
                }
            )
            source["deliveries"].append(
                {
                    "event_id": hop["source_event_id"],
                    "destination_site": hop["destination_site"],
                    "status": "acknowledged",
                    "attempt_count": hop["attempt_count"],
                    "enqueued_at": hop["delivery_enqueued_at"],
                    "first_attempt_at": hop["first_attempt_at"],
                    "last_attempt_at": hop["first_attempt_at"],
                    "acknowledged_at": hop["source_acknowledged_at"],
                    "acknowledgement_hash": "b" * 64,
                    "relay_site": hop["source_site"],
                    "last_error_code": None,
                }
            )
            destination["receipts"].append(
                {
                    "event_id": hop["source_event_id"],
                    "destination_site": hop["destination_site"],
                    "origin_physical_site": sample["hops"][0]["source_site"],
                    "received_from_site": hop["source_site"],
                    "relay_site": (
                        hop["source_site"]
                        if hop["source_site"] != sample["hops"][0]["source_site"]
                        else None
                    ),
                    "status": "applied",
                    "received_at": hop["destination_received_at"],
                    "applied_at": hop["destination_applied_at"],
                    "envelope_hash": hop["source_envelope_hash"],
                    "receipt_hash": hop["destination_receipt_hash"],
                }
            )
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "scenario_id": scenario_id,
        "release_sha": release,
        "state": timing["state"],
        "correlation_prefix": prefix,
        "captured_at": timing["captured_at"],
        "load": timing["load"],
        "samples": manifest_samples,
        "backlog": timing["backlog"],
    }
    return timing, manifest, snapshots


class BuildThreeSiteSyncTimingEvidenceTests(unittest.TestCase):
    def test_snapshot_correlation_prefix_escapes_sql_like_wildcards(self):
        self.assertEqual(
            _like_prefix("corr:three_site%probe\\"),
            r"corr:three\_site\%probe\\%",
        )

    def test_independent_site_snapshots_build_valid_timing_artifact(self):
        scenario_id = "three_site_sync_timing_steady_state"
        expected, manifest, snapshots = _inputs(scenario_id)
        result = build(
            manifest=manifest,
            snapshots=snapshots,
            scenario_id=scenario_id,
        )
        verified = verify_sync_timing_evidence(result, scenario_id=scenario_id)
        self.assertEqual(verified["sample_count"], len(expected["samples"]))
        self.assertEqual(result["summary"], expected["summary"])

    def test_missing_applied_receipt_cannot_be_reported_as_synchronized(self):
        scenario_id = "three_site_sync_timing_steady_state"
        _expected, manifest, snapshots = _inputs(scenario_id)
        snapshots = copy.deepcopy(snapshots)
        snapshots["webapp_fi"]["receipts"][0]["status"] = "received"
        with self.assertRaisesRegex(TimingBuildError, "applied-and-acknowledged"):
            build(
                manifest=manifest,
                snapshots=snapshots,
                scenario_id=scenario_id,
            )

    def test_destination_receipt_must_match_route_origin_and_envelope(self):
        scenario_id = "three_site_sync_timing_steady_state"
        _expected, manifest, snapshots = _inputs(scenario_id)
        snapshots = copy.deepcopy(snapshots)
        snapshots["webapp_fi"]["receipts"][0]["envelope_hash"] = "f" * 64
        with self.assertRaisesRegex(TimingBuildError, "identity/hash"):
            build(
                manifest=manifest,
                snapshots=snapshots,
                scenario_id=scenario_id,
            )


if __name__ == "__main__":
    unittest.main()
