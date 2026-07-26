from __future__ import annotations

import copy
from types import SimpleNamespace
import unittest
import uuid

from core.dr_full_matrix_failover_schedule import (
    FullMatrixFailoverScheduleError,
    build_schedule,
    scheduled_entry,
    validate_schedule,
    verify_scheduled_plan,
)
from core.three_site_full_matrix_campaign import (
    PHASE_SCENARIOS,
    scenarios_for_execution_class,
)


class FullMatrixFailoverScheduleTests(unittest.TestCase):
    def _schedule(self, execution_class: str = "shared-host-safe"):
        campaign = str(uuid.uuid4())
        group = str(uuid.uuid4())
        release = "a" * 40
        value = build_schedule(
            campaign_id=campaign,
            gate_group_id=group,
            execution_class=execution_class,
            release_sha=release,
        )
        return value, campaign, group, release

    def test_shared_schedule_is_catalog_ordered_paired_and_deterministic(self):
        value, campaign, group, release = self._schedule()
        catalog = scenarios_for_execution_class("shared-host-safe")
        selected = [
            scenario
            for scenarios in catalog.values()
            for scenario in scenarios
            if scenario in {
                *PHASE_SCENARIOS["partitions_failover"],
                *PHASE_SCENARIOS["recovery_failback"],
                "session_failover_contract",
            }
        ]
        self.assertEqual(len(value["entries"]), len(selected) * 2 * 2)
        self.assertEqual(
            [entry["sequence"] for entry in value["entries"]],
            list(range(1, len(value["entries"]) + 1)),
        )
        first = value["entries"][0]
        self.assertEqual(first["action"], "promote_ir")
        self.assertEqual(value["entries"][1]["action"], "failback_fi")
        self.assertEqual(
            build_schedule(
                campaign_id=campaign,
                gate_group_id=group,
                execution_class="shared-host-safe",
                release_sha=release,
            ),
            value,
        )
        selected_entry = scheduled_entry(
            value,
            operation_id=first["operation_id"],
            scenario_id=first["scenario_id"],
            iteration=1,
            action="promote_ir",
        )
        self.assertEqual(selected_entry, first)
        verified = verify_scheduled_plan(
            value,
            plan=SimpleNamespace(
                operation_id=first["operation_id"],
                operation_nonce=first["operation_nonce"],
                release_sha=release,
                source_site=first["source_site"],
                target_site=first["target_site"],
                action=first["action"],
            ),
            scenario_id=first["scenario_id"],
            iteration=1,
        )
        self.assertEqual(verified["sequence"], 1)

    def test_dedicated_schedule_contains_only_destructive_catalog_members(self):
        value, _campaign, _group, _release = self._schedule(
            "dedicated-host-destructive"
        )
        catalog = {
            scenario
            for scenarios in scenarios_for_execution_class(
                "dedicated-host-destructive"
            ).values()
            for scenario in scenarios
        }
        self.assertTrue(value["entries"])
        self.assertTrue(
            {entry["scenario_id"] for entry in value["entries"]}.issubset(catalog)
        )

    def test_rejects_reorder_action_drift_and_non_transition_selection(self):
        value, campaign, group, release = self._schedule()
        cases = []
        reordered = copy.deepcopy(value)
        reordered["entries"][0], reordered["entries"][1] = (
            reordered["entries"][1],
            reordered["entries"][0],
        )
        cases.append(reordered)
        drifted = copy.deepcopy(value)
        drifted["entries"][0]["target_site"] = "webapp_fi"
        cases.append(drifted)
        nonce_drifted = copy.deepcopy(value)
        nonce_drifted["entries"][0]["operation_nonce"] = str(uuid.uuid4())
        cases.append(nonce_drifted)
        for invalid in cases:
            with self.subTest(invalid=invalid["entries"][0]):
                with self.assertRaises(FullMatrixFailoverScheduleError):
                    validate_schedule(
                        invalid,
                        campaign_id=campaign,
                        gate_group_id=group,
                        execution_class="shared-host-safe",
                        release_sha=release,
                        repetitions=2,
                    )
        with self.assertRaises(FullMatrixFailoverScheduleError):
            build_schedule(
                campaign_id=campaign,
                gate_group_id=group,
                execution_class="shared-host-safe",
                release_sha=release,
                transition_scenarios={"bot_and_webapp_offers_concurrent"},
            )


if __name__ == "__main__":
    unittest.main()
