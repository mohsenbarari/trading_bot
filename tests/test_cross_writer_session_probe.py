from __future__ import annotations

import argparse
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core.dr_failover_orchestrator import failover_readiness_commitment
from scripts.full_matrix_live import cross_writer_session_probe as probe
from scripts.full_matrix_live import site_agent


class CrossWriterSessionProbeTests(unittest.TestCase):
    def _fixture_args(self, **overrides):
        values = {
            "user_id": 17,
            "primary_session_id": "11111111-1111-4111-8111-111111111111",
            "backup_session_id": "22222222-2222-4222-8222-222222222222",
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_fixture_descriptor_is_deterministic_and_rejects_unsafe_identity(self):
        fixture = probe._fixture_from_args(self._fixture_args())
        self.assertEqual(
            fixture.descriptor_sha256,
            probe._fixture_hash(
                user_id=17,
                primary_session_id="11111111-1111-4111-8111-111111111111",
                backup_session_id="22222222-2222-4222-8222-222222222222",
            ),
        )
        with self.assertRaises(probe.CrossWriterSessionProbeError):
            probe._fixture_from_args(self._fixture_args(primary_session_id="not-a-uuid"))
        with self.assertRaises(probe.CrossWriterSessionProbeError):
            probe._fixture_from_args(
                self._fixture_args(
                    backup_session_id="11111111-1111-4111-8111-111111111111"
                )
            )

    def test_ir_agent_accepts_only_identity_minimized_closed_session_result(self):
        prefix = "FMX_1234567890ABCDEF_SESSIONFAILOVER_DOER_"
        descriptor_sha256 = "a" * 64
        payload = {
            "schema": "three-site-full-matrix-cross-writer-session-probe-v1",
            "status": "passed",
            "mode": "verify",
            "role": "webapp_ir",
            "prefix": prefix,
            "writer_epoch": 7,
            "descriptor_sha256": descriptor_sha256,
            "observation": {
                "pre_promotion_session_accepted_after_ir_writer_activation": True,
                "post_promotion_websocket_reauthenticated_and_received_exact_event": True,
                "ir_writer_revoked_primary_session_fail_closed": True,
                "ir_writer_promoted_backup_session_and_authorized_it": True,
            },
            "cleanup": {
                "only_prefixed_session_fixture_rows_deleted": True,
                "exact_session_blacklist_keys_removed": True,
                "fixture_residue_zero": True,
            },
        }
        request = {
            "release_sha": "b" * 40,
            "context": {
                "prefix": prefix,
                "user_id": 17,
                "primary_session_id": "11111111-1111-4111-8111-111111111111",
                "backup_session_id": "22222222-2222-4222-8222-222222222222",
                "descriptor_sha256": descriptor_sha256,
                "observer": False,
            },
        }
        process = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )
        with (
            patch.object(site_agent, "_verify_release", return_value="b" * 40),
            patch.object(site_agent.subprocess, "run", return_value=process) as run,
        ):
            result = site_agent._cross_writer_session_verify(request)

        self.assertEqual(result["status"], "passed")
        probe_payload = result["probe_payload"]
        self.assertEqual(probe_payload["descriptor_sha256"], descriptor_sha256)
        self.assertNotIn("primary_session_id", json.dumps(result, sort_keys=True))
        argv = run.call_args.args[0]
        self.assertIn("cross_writer_session_probe.py", " ".join(argv))
        self.assertIn("--mode", argv)
        self.assertNotIn("--command", argv)

    def test_ir_agent_rejects_context_with_role_or_identity_mismatch(self):
        context = {
            "prefix": "FMX_1234567890ABCDEF_SESSIONFAILOVER_DOER_",
            "user_id": 17,
            "primary_session_id": "11111111-1111-4111-8111-111111111111",
            "backup_session_id": "22222222-2222-4222-8222-222222222222",
            "descriptor_sha256": "a" * 64,
            "observer": True,
        }
        with self.assertRaises(site_agent.SiteAgentError):
            site_agent._cross_writer_session_verify(
                {"release_sha": "b" * 40, "context": context}
            )

    def _failover_plan(self):
        plan = {
            "schema": "three-site-failover-operation-v1",
            "operation_id": "11111111-1111-4111-8111-111111111111",
            "operation_nonce": "22222222-2222-4222-8222-222222222222",
            "generated_at": "2026-07-26T00:00:00+00:00",
            "expires_at": "2026-07-26T00:10:00+00:00",
            "action": "promote_ir",
            "source_site": "webapp_fi",
            "target_site": "webapp_ir",
            "expected_epoch": 7,
            "target_epoch": 8,
            "release_sha": "b" * 40,
            "domain": "gold-trading.ir",
            "record": "app",
            "expected_current_ip": "192.0.2.10",
            "target_ip": "192.0.2.11",
            "classification": {
                "mode": "isolated", "confidence": "high", "consecutive_rounds": 3,
                "evidence_hash": "c" * 64,
                "campaign_id": "33333333-3333-4333-8333-333333333333",
                "policy_hash": "d" * 64,
            },
            "rpo_policy": {
                "mode": "zero_loss", "max_unreplicated_events": 0,
                "approval_reason": None, "approval_ticket": None,
            },
            "command_manifest_hash": "f" * 64,
            "approver_policy_hash": "a" * 64,
            "approvals": [{}],
        }
        plan["readiness_commitment"] = failover_readiness_commitment(
            operation_id=plan["operation_id"],
            operation_nonce=plan["operation_nonce"],
            action=plan["action"],
            source_site=plan["source_site"],
            target_site=plan["target_site"],
            expected_epoch=plan["expected_epoch"],
            target_epoch=plan["target_epoch"],
            release_sha=plan["release_sha"],
            domain=plan["domain"],
            record=plan["record"],
            command_manifest_hash=plan["command_manifest_hash"],
        )
        return plan

    def test_ir_agent_failover_operation_has_no_caller_selected_command_or_path(self):
        plan = self._failover_plan()
        # The plan parser intentionally rejects the synthetic approval before
        # any subprocess can be considered; role/action validation remains
        # closed even when an attacker supplies an execution-shaped key.
        request = {
            "release_sha": "b" * 40,
            "context": {
                "action": "target-ready",
                "plan": {**plan, "command": "should-never-run"},
                "source_tail_boundary": {},
                "readiness_evidence": None,
                "previous_proof_hash": None,
            },
        }
        with self.assertRaises(site_agent.SiteAgentError):
            site_agent._failover_site_operation(request)

    def test_ir_agent_runs_only_fixed_local_failover_agent(self):
        plan = self._failover_plan()
        boundary = {
            "mode": "proven",
            "origin_site": "webapp_fi",
            "target_site": "webapp_ir",
            "producer_epoch": 7,
            "final_sequence": 1,
            "final_transaction_hash": "c" * 64,
            "estimated_unreplicated_events": 0,
            "boundary_hash": "d" * 64,
        }

        def run(argv, **_kwargs):
            output = argv[argv.index("--output") + 1]
            with open(output, "w", encoding="utf-8") as handle:
                json.dump({"status": "ok", "operation_id": plan["operation_id"]}, handle)
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"status": "ok", "operation_id": plan["operation_id"]}),
                stderr="",
            )

        with (
            patch.object(site_agent, "_verify_release", return_value="b" * 40),
            patch.object(site_agent.subprocess, "run", side_effect=run) as mocked,
        ):
            result = site_agent._failover_site_operation(
                {
                    "release_sha": "b" * 40,
                    "context": {
                        "action": "target-ready",
                        "plan": plan,
                        "source_tail_boundary": boundary,
                        "readiness_evidence": None,
                        "previous_proof_hash": None,
                    },
                }
            )
        self.assertEqual(result["status"], "passed")
        argv = mocked.call_args.args[0]
        self.assertIn("run_three_site_staging_failover_site_agent.py", " ".join(argv))
        self.assertNotIn("--command", argv)
        self.assertNotIn("--cwd", argv)


if __name__ == "__main__":
    unittest.main()
