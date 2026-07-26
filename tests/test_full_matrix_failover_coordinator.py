from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from scripts.full_matrix_live.failover_coordinator import (
    FullMatrixFailoverCoordinatorError,
    _pull_operation,
    _source_epoch,
    execute_transition,
    preflight_transition_system,
)


def _plan() -> dict:
    return {
        "_roles": {
            "webapp_fi": {"transport": "ssh"},
            "webapp_ir": {"transport": "object-storage-agent"},
        }
    }


def _writer_probe(*, role: str, active_site: str, epoch: int) -> dict:
    state = {
        "active_site": active_site,
        "writer_epoch": epoch,
        "control_state": "active",
        "transition_id": "transition-1",
        "witness_lease_id_sha256": "a" * 64,
        "witness_lease_issued_at": "2026-07-26T00:00:00+00:00",
        "witness_lease_expires_at": "2026-07-26T00:05:00+00:00",
        "witness_proof_hash": "b" * 64,
        "lease_refresh_count_for_epoch": 1,
        "database_now": "2026-07-26T00:01:00+00:00",
        "local_active_with_witness_lease": True,
        "local_active_reasons": [],
    }
    return {
        "schema": "three-site-full-matrix-site-probe-v1",
        "status": "passed",
        "operation": "writer_lease_state",
        "role": role,
        "result": state,
    }


class FullMatrixFailoverCoordinatorTests(unittest.TestCase):
    def test_source_epoch_uses_only_active_local_witness_writer(self):
        plan = _plan()
        response = {
            "stdout": json.dumps(_writer_probe(role="webapp_fi", active_site="webapp_fi", epoch=8))
        }
        with patch(
            "scripts.full_matrix_live.failover_coordinator.run_compose_role_service",
            return_value=response,
        ) as run:
            self.assertEqual(_source_epoch(plan, source_site="webapp_fi"), 8)
        command = run.call_args.kwargs["command"]
        self.assertEqual(command[-1], "writer_lease_state")

        response["stdout"] = json.dumps(
            _writer_probe(role="webapp_fi", active_site="webapp_ir", epoch=8)
        )
        with patch(
            "scripts.full_matrix_live.failover_coordinator.run_compose_role_service",
            return_value=response,
        ), self.assertRaises(FullMatrixFailoverCoordinatorError):
            _source_epoch(plan, source_site="webapp_fi")

    def test_wa_ir_operation_is_only_a_closed_pull_envelope(self):
        plan = _plan()
        parsed = SimpleNamespace(
            operation_id="11111111-1111-4111-8111-111111111111",
            release_sha="a" * 40,
        )
        evidence = {
            "status": "ok",
            "operation_id": parsed.operation_id,
            "evidence_hash": "b" * 64,
        }
        response = {
            "status": "passed",
            "role": "webapp_ir",
            "production_touched": False,
            "result": {
                "schema": "three-site-full-matrix-site-agent-result-v1",
                "status": "passed",
                "role": "webapp_ir",
                "release_sha": parsed.release_sha,
                "operation": "failover_site_operation",
                "result": {
                    "status": "passed",
                    "action": "target-ready",
                    "evidence": evidence,
                },
            },
        }
        invoke = _pull_operation(plan)
        with patch(
            "scripts.full_matrix_live.failover_coordinator.run_role_agent_operation",
            return_value=response,
        ) as dispatch:
            result = invoke(
                parsed,
                {"approved": "plan"},
                "target-ready",
                {"boundary": "proof"},
                None,
                None,
            )
        self.assertEqual(result, evidence)
        context = dispatch.call_args.kwargs["context"]
        self.assertEqual(
            set(context),
            {
                "action",
                "plan",
                "source_tail_boundary",
                "readiness_evidence",
                "previous_proof_hash",
            },
        )
        self.assertNotIn("command", context)
        self.assertNotIn("path", context)

    def test_wa_ir_pull_rejects_unbound_or_wrong_action_response(self):
        plan = _plan()
        parsed = SimpleNamespace(
            operation_id="11111111-1111-4111-8111-111111111111",
            release_sha="a" * 40,
        )
        response = {
            "result": {
                "schema": "three-site-full-matrix-site-agent-result-v1",
                "status": "passed",
                "role": "webapp_ir",
                "release_sha": parsed.release_sha,
                "operation": "failover_site_operation",
                "result": {
                    "status": "passed",
                    "action": "safe-fence",
                    "evidence": {"operation_id": parsed.operation_id},
                },
            }
        }
        with patch(
            "scripts.full_matrix_live.failover_coordinator.run_role_agent_operation",
            return_value=response,
        ), self.assertRaises(FullMatrixFailoverCoordinatorError):
            _pull_operation(plan)(parsed, {}, "target-ready", None, None, None)

    def test_preflight_is_read_only_and_builds_every_transition_in_memory(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            root.chmod(0o700)
            entry = {
                "sequence": 1,
                "iteration": 1,
                "scenario_id": "session_failover_contract",
                "action": "promote_ir",
                "source_site": "webapp_fi",
                "target_site": "webapp_ir",
                "operation_id": "11111111-1111-4111-8111-111111111111",
                "operation_nonce": "22222222-2222-4222-8222-222222222222",
            }
            plan = {
                "_inventory": {"fixture": True},
                "_bindings": {
                    "failover_schedule": {"payload": {"entries": [entry]}},
                    "inventory_approval": {"payload": {"fixture": True}},
                    "human_approval_policy": {"payload": {"fixture": True}},
                },
            }
            classification = {
                "mode": "online",
                "confidence": "high",
                "consecutive_rounds": 3,
                "evidence_hash": "a" * 64,
                "campaign_id": "fixture",
                "policy_hash": "b" * 64,
            }
            backend = Mock()
            backend.preflight_static.return_value = classification

            def prepared(**kwargs):
                expected = kwargs["expected_epoch"]
                return ({
                    "operation_id": entry["operation_id"],
                    "operation_nonce": entry["operation_nonce"],
                    "source_site": entry["source_site"],
                    "target_site": entry["target_site"],
                    "expected_epoch": expected,
                    "target_epoch": expected + 1,
                }, {}, {})

            paths = {
                "backend_config": root / "backend.json",
                "relay_credentials": root / "relay.env",
                "witness_relay_public_key_file": root / "witness.pub",
                "journal_root": root,
            }
            with patch(
                "scripts.full_matrix_live.failover_coordinator._control",
                return_value=paths,
            ), patch(
                "scripts.full_matrix_live.failover_coordinator.load_staging_backend_config",
                return_value=object(),
            ), patch(
                "scripts.full_matrix_live.failover_coordinator.PullFailoverBackend",
                return_value=backend,
            ), patch(
                "scripts.full_matrix_live.failover_coordinator._source_epoch",
                return_value=9,
            ), patch(
                "scripts.full_matrix_live.failover_coordinator.prepare_plan",
                side_effect=prepared,
            ) as prepare, patch(
                "scripts.full_matrix_live.failover_coordinator.request_receipt",
            ) as receipt:
                result = preflight_transition_system(plan)

            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["initial_writer_epoch"], 9)
            self.assertEqual(result["scheduled_transition_count"], 1)
            self.assertTrue(result["jit_journal_root_empty"])
            self.assertFalse(result["site_mutation_performed"])
            self.assertFalse(receipt.called)
            self.assertEqual(prepare.call_count, 1)
            self.assertEqual(list(root.iterdir()), [])

    def test_preflight_refuses_to_normalize_a_retained_jit_artifact(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            root.chmod(0o700)
            (root / "retained-operation").mkdir(mode=0o700)
            with self.assertRaisesRegex(FullMatrixFailoverCoordinatorError, "not empty"):
                from scripts.full_matrix_live.failover_coordinator import _assert_empty_jit_journal_root

                _assert_empty_jit_journal_root(root)

    def test_power_loss_cutpoint_cannot_be_selected_for_another_transition(self):
        with self.assertRaisesRegex(FullMatrixFailoverCoordinatorError, "power-loss cutpoint"):
            execute_transition(
                {},
                scenario_id="iran_international_cutoff_promotes_ir",
                iteration=1,
                action="promote_ir",
                pause_after_source_drain_for_power_loss=True,
            )
        with self.assertRaisesRegex(FullMatrixFailoverCoordinatorError, "power-loss cutpoint"):
            execute_transition(
                {},
                scenario_id="power_loss_between_fence_and_enable",
                iteration=1,
                action="failback_fi",
                pause_after_source_drain_for_power_loss=True,
            )

    def test_power_cut_resume_does_not_recontact_powered_off_fi_for_materialization(self):
        parsed = SimpleNamespace(
            operation_id="11111111-1111-4111-8111-111111111111",
            plan_hash="a" * 64,
            action="promote_ir",
            source_site="webapp_fi",
            target_site="webapp_ir",
            expected_epoch=8,
            target_epoch=9,
            classification={
                "mode": "isolated", "consecutive_rounds": 3,
            },
        )
        paths = {
            "backend_config": Path("/tmp/backend"),
            "relay_credentials": Path("/tmp/relay"),
            "witness_relay_public_key_file": Path("/tmp/witness"),
            "journal_root": Path("/tmp/journal"),
        }
        plan = {
            "_roles": {"webapp_ir": {"transport": "object-storage-agent"}},
            "_inventory": {},
            "_bindings": {
                "inventory_approval": {"payload": {}},
                "human_approval_policy": {"payload": {}},
            },
        }
        backend = Mock()
        backend.preflight.return_value = None
        with patch(
            "scripts.full_matrix_live.failover_coordinator._control", return_value=paths
        ), patch(
            "scripts.full_matrix_live.failover_coordinator.load_staging_backend_config",
            return_value=object(),
        ), patch(
            "scripts.full_matrix_live.failover_coordinator._existing_plan",
            return_value=(parsed, {"approved": True}, Path("/tmp/journal/journal.jsonl")),
        ), patch(
            "scripts.full_matrix_live.failover_coordinator._journal_started", return_value=True
        ), patch(
            "scripts.full_matrix_live.failover_coordinator.verify_human_failover_approval"
        ), patch(
            "scripts.full_matrix_live.failover_coordinator.read_secure_text", return_value="public-key"
        ), patch(
            "scripts.full_matrix_live.failover_coordinator.PullFailoverBackend",
            side_effect=[Mock(), backend],
        ), patch(
            "scripts.full_matrix_live.failover_coordinator.WitnessOperationLedger", return_value=Mock()
        ), patch(
            "scripts.full_matrix_live.failover_coordinator.run_orchestration",
            new=AsyncMock(return_value={"status": "completed"}),
        ):
            result = execute_transition(
                plan,
                scenario_id="power_loss_between_fence_and_enable",
                iteration=1,
                action="promote_ir",
            )
        self.assertEqual(result["status"], "completed")
        backend.materialize_webapp_fi_inputs.assert_not_called()


if __name__ == "__main__":
    unittest.main()
