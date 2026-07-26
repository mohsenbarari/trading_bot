from __future__ import annotations

import argparse
import unittest
from unittest.mock import patch

from scripts.full_matrix_live.recipes import recipe_for
from scripts.full_matrix_live.scenario_handlers import (
    IMPLEMENTED_HANDLER_IDS,
    _SESSION_FAILOVER_ID,
    _fixture_from_prepare,
    _verify_session_fixture_on_ir,
    hash_summary,
    verify_scenario,
)


def _fixture_payload() -> dict:
    return {
        "schema": "three-site-full-matrix-cross-writer-session-probe-v1",
        "status": "passed",
        "mode": "prepare",
        "role": "webapp_fi",
        "prefix": "FMX_1234567890ABCDEF_SESSIONFAILOVER_DOER_",
        "writer_epoch": 7,
        "observation": {"pre_promotion_primary_session_authorized": True},
        "fixture": {
            "user_id": 17,
            "primary_session_id": "11111111-1111-4111-8111-111111111111",
            "backup_session_id": "22222222-2222-4222-8222-222222222222",
            "descriptor_sha256": "a" * 64,
        },
    }


class FullMatrixSessionFailoverTests(unittest.TestCase):
    def test_session_recipe_is_enabled_only_with_exact_handler(self):
        recipe = recipe_for("application_regression", _SESSION_FAILOVER_ID)
        self.assertTrue(recipe.implemented)
        self.assertIn(_SESSION_FAILOVER_ID, IMPLEMENTED_HANDLER_IDS)

    def test_prepare_fixture_is_private_descriptor_only(self):
        fixture = _fixture_from_prepare(
            _fixture_payload(),
            prefix="FMX_1234567890ABCDEF_SESSIONFAILOVER_DOER_",
        )
        self.assertEqual(fixture["writer_epoch"], 7)
        self.assertEqual(fixture["descriptor_sha256"], "a" * 64)
        self.assertEqual(fixture["primary_session_id"], "11111111-1111-4111-8111-111111111111")

    def test_ir_verification_returns_only_minimized_session_evidence(self):
        fixture = _fixture_from_prepare(
            _fixture_payload(),
            prefix="FMX_1234567890ABCDEF_SESSIONFAILOVER_DOER_",
        )
        payload = {
            "schema": "three-site-full-matrix-cross-writer-session-probe-v1",
            "status": "passed",
            "mode": "verify",
            "role": "webapp_ir",
            "prefix": fixture["prefix"],
            "writer_epoch": 8,
            "descriptor_sha256": fixture["descriptor_sha256"],
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
        control = {
            "result": {
                "schema": "three-site-full-matrix-site-agent-result-v1",
                "status": "passed",
                "role": "webapp_ir",
                "operation": "cross_writer_session_verify",
                "result": {"status": "passed", "probe_payload": payload},
            }
        }
        plan = {"_roles": {"webapp_ir": {"transport": "object-storage-agent"}}}
        with patch(
            "scripts.full_matrix_live.scenario_handlers.run_role_agent_operation",
            return_value=control,
        ) as call:
            result = _verify_session_fixture_on_ir(plan, fixture=fixture, observer=False)
        self.assertNotIn("primary_session_id", result)
        self.assertNotIn("backup_session_id", result)
        self.assertEqual(result["writer_epoch"], 8)
        context = call.call_args.kwargs["context"]
        self.assertEqual(context["primary_session_id"], fixture["primary_session_id"])
        self.assertNotIn("command", context)

    def test_oracle_can_prove_complementary_session_contract(self):
        contract = {
            "two_private_session_fixtures_never_enter_public_evidence": True,
            "fi_to_ir_transition_uses_schedule_bound_witness_receipt": True,
            "wa_ir_mutations_use_encrypted_object_storage_pull_only": True,
            "doer_and_oracle_session_continuity_are_separately_proved": True,
            "final_failback_and_private_fixture_cleanup_are_oracle_owned": True,
        }
        doer = {
            "two_private_fi_session_fixtures_prepared": True,
            "schedule_bound_promotion_to_ir_completed": True,
            "doer_session_continuity_proved_on_ir": True,
        }
        oracle = {
            **doer,
            "oracle_session_continuity_proved_on_ir": True,
            "schedule_bound_failback_to_fi_completed": True,
            "private_session_fixture_residue_zero": True,
        }
        runner = {
            "recipe_contract": contract,
            "expected_outcome": doer,
            "recipe_contract_sha256": hash_summary(contract),
            "doer_observations": {"safe": "metadata"},
            "doer_observations_sha256": hash_summary({"safe": "metadata"}),
        }
        args = argparse.Namespace(operation_id="11111111-1111-4111-8111-111111111111")
        with patch(
            "scripts.full_matrix_live.scenario_handlers._session_failover_oracle",
            return_value=(oracle, {"safe": "oracle-metadata"}),
        ):
            result = verify_scenario(
                args,
                {},
                recipe_for("application_regression", _SESSION_FAILOVER_ID),
                runner,
            )
        self.assertEqual(result["observed_outcome"], oracle)
        self.assertTrue(
            result["oracle_contract"]["doer_and_oracle_session_proofs_are_complementary"]
        )


if __name__ == "__main__":
    unittest.main()
