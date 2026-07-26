from __future__ import annotations

from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.full_matrix_live.scenario_handlers import (
    _failback_fi_lifecycle,
    _promote_ir_lifecycle,
    _verify_failed_back_to_fi_lifecycle,
    _verify_promoted_ir_lifecycle,
)


def _lease(*, active_site: str, role: str, epoch: int) -> dict:
    return {
        "active_site": active_site,
        "writer_epoch": epoch,
        "control_state": "active",
        "local_active_with_witness_lease": role == active_site,
    }


class FullMatrixWriterLifecycleTests(unittest.TestCase):
    def test_promotion_and_failback_are_schedule_bound_and_checkpointed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            root.chmod(0o700)
            plan = {
                "campaign_id": "11111111-1111-4111-8111-111111111111",
                "release_sha": "a" * 40,
                "_state_root": root,
            }
            args = SimpleNamespace(iteration=1)
            active = {"site": "webapp_fi", "epoch": 7}

            def observe(_plan, role):
                return _lease(
                    active_site=active["site"],
                    role=role,
                    epoch=active["epoch"],
                )

            def transition(_plan, *, scenario_id, iteration, action):
                self.assertEqual(iteration, 1)
                if action == "promote_ir":
                    self.assertEqual(scenario_id, "iran_international_cutoff_promotes_ir")
                    self.assertEqual(active, {"site": "webapp_fi", "epoch": 7})
                    active.update(site="webapp_ir", epoch=8)
                    return {
                        "status": "completed",
                        "operation_id": "22222222-2222-4222-8222-222222222222",
                        "plan_hash": "b" * 64,
                        "source_site": "webapp_fi",
                        "target_site": "webapp_ir",
                        "writer_epoch_before": 7,
                        "writer_epoch_after": 8,
                        "connectivity_mode": "isolated",
                        "connectivity_consecutive_rounds": 3,
                    }
                self.assertEqual(action, "failback_fi")
                self.assertEqual(scenario_id, "fi_epoch_reacquire_and_route_switch")
                self.assertEqual(active, {"site": "webapp_ir", "epoch": 8})
                active.update(site="webapp_fi", epoch=9)
                return {
                    "status": "completed",
                    "operation_id": "33333333-3333-4333-8333-333333333333",
                    "plan_hash": "c" * 64,
                    "source_site": "webapp_ir",
                    "target_site": "webapp_fi",
                    "writer_epoch_before": 8,
                    "writer_epoch_after": 9,
                    "connectivity_mode": "online",
                    "connectivity_consecutive_rounds": 3,
                }

            def ingress(_plan, *, expected_active_origin=None):
                self.assertEqual(expected_active_origin, active["site"])
                return {
                    "expected_active_origin": expected_active_origin,
                    "writer_epoch": active["epoch"],
                }

            with patch(
                "scripts.full_matrix_live.scenario_handlers._writer_lease_observation",
                side_effect=observe,
            ), patch(
                "scripts.full_matrix_live.scenario_handlers.execute_transition",
                side_effect=transition,
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._public_ingress_probe",
                side_effect=ingress,
            ):
                promotion, promotion_observations = _promote_ir_lifecycle(args, plan)
                self.assertTrue(all(promotion.values()))
                checkpoint = root / "writer-lifecycle.json"
                self.assertTrue(checkpoint.is_file())
                self.assertEqual(stat.S_IMODE(checkpoint.stat().st_mode), 0o600)

                observed_promotion, _oracle = _verify_promoted_ir_lifecycle(
                    args,
                    plan,
                    {"doer_observations": promotion_observations},
                )
                self.assertEqual(observed_promotion, promotion)

                failback, failback_observations = _failback_fi_lifecycle(args, plan)
                self.assertTrue(all(failback.values()))
                self.assertFalse(checkpoint.exists())

                observed_failback, _oracle = _verify_failed_back_to_fi_lifecycle(
                    args,
                    plan,
                    {"doer_observations": failback_observations},
                )
                self.assertEqual(observed_failback, failback)

    def test_promotion_refuses_non_isolated_schedule_result(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            root.chmod(0o700)
            plan = {
                "campaign_id": "11111111-1111-4111-8111-111111111111",
                "release_sha": "a" * 40,
                "_state_root": root,
            }
            args = SimpleNamespace(iteration=1)
            with patch(
                "scripts.full_matrix_live.scenario_handlers._writer_lease_observation",
                side_effect=lambda _plan, role: _lease(
                    active_site="webapp_fi", role=role, epoch=7
                ),
            ), patch(
                "scripts.full_matrix_live.scenario_handlers.execute_transition",
                return_value={
                    "status": "completed",
                    "source_site": "webapp_fi",
                    "target_site": "webapp_ir",
                    "writer_epoch_before": 7,
                    "writer_epoch_after": 8,
                    "connectivity_mode": "online",
                    "connectivity_consecutive_rounds": 3,
                },
            ), self.assertRaisesRegex(Exception, "Iran-isolation"):
                _promote_ir_lifecycle(args, plan)
            self.assertFalse((root / "writer-lifecycle.json").exists())


if __name__ == "__main__":
    unittest.main()
