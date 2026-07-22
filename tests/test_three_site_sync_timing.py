from __future__ import annotations

from datetime import datetime, timedelta
import unittest

from core.three_site_sync_timing import (
    NORMAL_ROUTES,
    ROUTE_HOPS,
    SYNC_TIMING_POLICIES,
    SyncTimingEvidenceError,
    summarize_sync_timing_samples,
    sync_timing_policy,
    verify_sync_timing_evidence,
)
from tests.three_site_sync_timing_fixtures import make_sync_timing_artifact


class ThreeSiteSyncTimingTests(unittest.TestCase):
    def test_all_required_profiles_recompute_raw_percentiles(self):
        for scenario_id in SYNC_TIMING_POLICIES:
            with self.subTest(scenario_id=scenario_id):
                artifact = make_sync_timing_artifact(scenario_id)
                result = verify_sync_timing_evidence(
                    artifact,
                    scenario_id=scenario_id,
                )
                policy = sync_timing_policy(scenario_id)
                self.assertEqual(set(result["route_summary"]), set(policy["routes"]))
                self.assertEqual(
                    result["sample_count"],
                    len(policy["routes"]) * policy["minimum_samples_per_route"],
                )

    def test_missing_route_or_forged_summary_fails_closed(self):
        scenario_id = "three_site_sync_timing_steady_state"
        missing = make_sync_timing_artifact(scenario_id)
        missing_route = sync_timing_policy(scenario_id)["routes"][0]
        missing["samples"] = [
            sample for sample in missing["samples"] if sample["route"] != missing_route
        ]
        with self.assertRaisesRegex(SyncTimingEvidenceError, "coverage"):
            verify_sync_timing_evidence(missing, scenario_id=scenario_id)

        forged = make_sync_timing_artifact(scenario_id)
        forged["summary"]["routes"][missing_route]["p95_seconds"] += 1
        with self.assertRaisesRegex(SyncTimingEvidenceError, "summary"):
            verify_sync_timing_evidence(forged, scenario_id=scenario_id)

    def test_event_evidence_cannot_be_reused_across_samples(self):
        scenario_id = "three_site_sync_timing_steady_state"
        artifact = make_sync_timing_artifact(scenario_id)
        first = artifact["samples"][0]
        second = next(
            sample for sample in artifact["samples"][1:]
            if sample["route"] == first["route"]
        )
        second["hops"][0]["source_event_id"] = first["hops"][0]["source_event_id"]
        with self.assertRaisesRegex(SyncTimingEvidenceError, "reused"):
            verify_sync_timing_evidence(artifact, scenario_id=scenario_id)

    def test_wrong_hop_and_untrusted_clock_fail_closed(self):
        scenario_id = "three_site_sync_timing_steady_state"
        wrong_hop = make_sync_timing_artifact(scenario_id)
        wrong_hop["samples"][0]["hops"][0]["destination_site"] = "webapp_ir"
        with self.assertRaisesRegex(SyncTimingEvidenceError, "route"):
            verify_sync_timing_evidence(wrong_hop, scenario_id=scenario_id)

        bad_clock = make_sync_timing_artifact(scenario_id)
        bad_clock["clock"]["sites"]["webapp_ir"]["synchronized"] = False
        with self.assertRaisesRegex(SyncTimingEvidenceError, "not synchronized"):
            verify_sync_timing_evidence(bad_clock, scenario_id=scenario_id)

    def test_300_rps_and_backlog_are_semantic_requirements(self):
        load_scenario = "three_hundred_rps_fifty_fifty"
        below_load = make_sync_timing_artifact(load_scenario)
        below_load["load"]["observed_requests_per_second"] = 299.999
        with self.assertRaisesRegex(SyncTimingEvidenceError, "below policy"):
            verify_sync_timing_evidence(below_load, scenario_id=load_scenario)

        skewed_load = make_sync_timing_artifact(load_scenario)
        skewed_load["load"]["bot_request_count"] = 400
        skewed_load["load"]["webapp_request_count"] = 200
        with self.assertRaisesRegex(SyncTimingEvidenceError, "50/50"):
            verify_sync_timing_evidence(skewed_load, scenario_id=load_scenario)

        slow_finland = make_sync_timing_artifact(load_scenario)
        route_samples = [
            item for item in slow_finland["samples"]
            if item["route"] == "bot_fi_to_webapp_fi"
        ]
        for sample in route_samples[-6:]:
            hop = sample["hops"][0]
            created = datetime.fromisoformat(hop["origin_event_created_at"])
            hop["destination_received_at"] = (
                created + timedelta(milliseconds=190)
            ).isoformat()
            hop["destination_applied_at"] = (
                created + timedelta(milliseconds=200)
            ).isoformat()
            hop["source_acknowledged_at"] = (
                created + timedelta(milliseconds=210)
            ).isoformat()
            sample["controller_observed_duration_seconds"] = 0.2
        slow_finland["summary"] = summarize_sync_timing_samples(
            slow_finland["samples"]
        )
        with self.assertRaisesRegex(SyncTimingEvidenceError, "percentile SLO"):
            verify_sync_timing_evidence(slow_finland, scenario_id=load_scenario)

        recovery_scenario = "reconnect_flap_and_bounded_catchup"
        not_drained = make_sync_timing_artifact(recovery_scenario)
        not_drained["backlog"]["drained_to_zero"] = False
        with self.assertRaisesRegex(SyncTimingEvidenceError, "not proven"):
            verify_sync_timing_evidence(not_drained, scenario_id=recovery_scenario)

        no_live_ingress = make_sync_timing_artifact(recovery_scenario)
        no_live_ingress["backlog"]["live_ingress_events"] = 0
        with self.assertRaisesRegex(SyncTimingEvidenceError, "live ingress"):
            verify_sync_timing_evidence(
                no_live_ingress,
                scenario_id=recovery_scenario,
            )

    def test_policy_routes_are_topologically_closed(self):
        for scenario_id, policy in SYNC_TIMING_POLICIES.items():
            with self.subTest(scenario_id=scenario_id):
                self.assertTrue(policy["routes"])
                for route in policy["routes"]:
                    self.assertIn(route, ROUTE_HOPS)
                    self.assertIn(len(ROUTE_HOPS[route]), {1, 2})

        for scenario_id in (
            "three_site_sync_timing_steady_state",
            "three_hundred_rps_fifty_fifty",
        ):
            self.assertEqual(
                tuple(sync_timing_policy(scenario_id)["routes"]),
                NORMAL_ROUTES,
            )
            self.assertNotIn(
                "webapp_ir_to_webapp_fi",
                sync_timing_policy(scenario_id)["routes"],
            )


if __name__ == "__main__":
    unittest.main()
