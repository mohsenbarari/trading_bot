from __future__ import annotations

import argparse
import ast
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.three_site_execution_safety import (
    DEDICATED_HOST_DESTRUCTIVE,
    SHARED_HOST_SAFE,
)
from core.three_site_full_matrix_campaign import (
    CUSTOMER_ACTOR_PAIR_POLICIES,
    CUSTOMER_LIFECYCLE_MATRIX,
    PHASE_SCENARIOS,
    scenarios_for_execution_class,
)
from scripts.full_matrix_live.recipes import (
    IMPLEMENTED_RECIPES,
    RECIPES,
    missing_recipes,
    recipe_for,
)
from scripts.full_matrix_live.object_storage_protocol import (
    ObjectStorageProtocolError,
    build_request,
    build_response,
    canonical_bytes,
    public_key_b64,
    public_key_id,
    verify_request,
    verify_response,
)
from scripts.full_matrix_live.scenario_handlers import (
    CONVERGENCE_HANDLER_IDS,
    COMBINED_WORKLOAD_LIVE_IDS,
    CUSTOMER_ACTOR_LIVE_HANDLER_IDS,
    MESSENGER_REGRESSION_LIVE_HANDLER_IDS,
    TELEGRAM_QUEUE_REGRESSION_LIVE_HANDLER_IDS,
    APPLICATION_REGRESSION_LIVE_HANDLER_IDS,
    DR_FAULT_POLICY_FIXTURE_IDS,
    DR_POLICY_FIXTURE_IDS,
    FAILOVER_FAULT_POLICY_FIXTURE_IDS,
    IMPLEMENTED_HANDLER_IDS,
    MIGRATION_FIXTURE_IDS,
    QUEUE_POLICY_FIXTURE_IDS,
    RELEASE_TRANSITION_POLICY_FIXTURE_IDS,
    RECOVERY_POLICY_FIXTURE_IDS,
    RUNTIME_POLICY_FIXTURE_IDS,
    SECURITY_POLICY_FIXTURE_IDS,
    _backup_restore_paths,
    _convergence_outcome,
    _combined_workload_scenarios,
    _dr_fault_policy_fixture_outcome,
    _dr_policy_fixture_outcome,
    _failover_fault_policy_fixture_outcome,
    _final_writer_route_outcome,
    _migration_fixture_outcome,
    _queue_policy_fixture_outcome,
    _release_transition_policy_outcome,
    _recovery_policy_fixture_outcome,
    _runtime_policy_fixture_outcome,
    _security_policy_fixture_outcome,
    _legacy_rollback_paths,
    _customer_actor_outcome,
    _retain_customer_actor_evidence,
    _validate_customer_actor_payload,
    _validate_messenger_regression_payload,
    _messenger_regression_outcome,
    _validate_telegram_queue_regression_payload,
    _telegram_queue_regression_outcome,
    _validate_application_regression_payload,
    _application_regression_outcome,
    _validate_legacy_rollback_state,
    _validate_backup_restore_state,
)
from scripts.full_matrix_live.runner import _dedicated_host_provider_preflight
from scripts.full_matrix_drivers.driver import (
    LiveDriverError,
    _scenario_result,
    _write_once,
)
from scripts.build_full_matrix_object_storage_agent_bundle import build as build_agent_bundle
from scripts.install_full_matrix_object_storage_agent import _bundle as verify_agent_bundle


REPO_ROOT = Path(__file__).resolve().parents[1]


class ThreeSiteFullMatrixLiveDriverTests(unittest.TestCase):

    def test_dedicated_preflight_requires_four_active_read_only_provider_hosts(self):
        active = {
            "status": "passed",
            "read_only": True,
            "delete_operation_available": False,
            "roles": {
                role: {"status": "ACTIVE"}
                for role in ("bot_fi", "webapp_fi", "webapp_ir", "witness")
            },
        }
        with (
            patch(
                "scripts.provision_arvan_witness_recovery_vps.read_private_text",
                return_value="private-token",
            ),
            patch(
                "scripts.provision_arvan_full_matrix_destructive_hosts.inspect_existing_hosts",
                return_value=active,
            ),
        ):
            observed = _dedicated_host_provider_preflight(
                {"execution_class": DEDICATED_HOST_DESTRUCTIVE}
            )
        self.assertTrue(observed["required"])
        self.assertEqual(observed["inspection"], active)

        inactive = copy.deepcopy(active)
        inactive["roles"]["witness"]["status"] = "SHUTOFF"
        with (
            patch(
                "scripts.provision_arvan_witness_recovery_vps.read_private_text",
                return_value="private-token",
            ),
            patch(
                "scripts.provision_arvan_full_matrix_destructive_hosts.inspect_existing_hosts",
                return_value=inactive,
            ),
            self.assertRaises(Exception),
        ):
            _dedicated_host_provider_preflight(
                {"execution_class": DEDICATED_HOST_DESTRUCTIVE}
            )

        self.assertEqual(
            _dedicated_host_provider_preflight({"execution_class": SHARED_HOST_SAFE}),
            {"required": False, "inspection": None},
        )

    @staticmethod
    def _customer_payload(scenario_id: str, prefix: str) -> dict:
        pairs = []
        completed = {
            "trade_count": 1,
            "offer_request_count": 1,
            "offer_request_status_counts": {"completed_trade": 1},
            "offer_requests": [
                {
                    "requester_matches": True,
                    "actor_matches": True,
                    "result_status": "completed_trade",
                    "resulting_trade": True,
                }
            ],
        }
        for actor_pair, policy in CUSTOMER_ACTOR_PAIR_POLICIES.items():
            common = {
                "actor_pair": actor_pair,
                "execution_policy": policy,
                "passed": True,
                "counterparty_privacy_preserved": True,
            }
            if policy == "positive_all_eligible_surfaces":
                pairs.append(
                    {
                        **common,
                        "result": "eligible_surface_trade_completed",
                        "webapp_status": "success",
                        "telegram_status": "success",
                        "webapp": completed,
                        "telegram": completed,
                        "telegram_sent_count": 0,
                    }
                )
            elif policy == "positive_webapp_tier2_request_telegram_denied":
                pairs.append(
                    {
                        **common,
                        "result": "webapp_trade_completed_and_telegram_request_denied",
                        "webapp_status": "success",
                        "telegram_status": "rejected",
                        "webapp": completed,
                        "telegram": {"trade_count": 0},
                        "telegram_sent_count": 0,
                    }
                )
            else:
                pairs.append(
                    {
                        **common,
                        "result": "tier2_offer_creation_denied_with_zero_mutation",
                        "webapp_status": "rejected",
                        "telegram_status": "rejected",
                        "side_effect_delta": {
                            "notifications": 0,
                            "offer_requests": 0,
                            "offers": 0,
                            "publication_states": 0,
                            "trades": 0,
                        },
                    }
                )
        lifecycle = CUSTOMER_LIFECYCLE_MATRIX[scenario_id]
        return {
            "schema": "three-site-full-matrix-customer-actor-probe-v1",
            "status": "passed",
            "scenario_id": scenario_id,
            "writer_role": lifecycle["webapp_writer"],
            "runtime_state": lifecycle["runtime_state"],
            "server_mode": "iran",
            "prefix": prefix,
            "pair_count": len(pairs),
            "pairs": pairs,
            "cleanup": {"deleted_total": 0, "residue_zero": True},
        }

    def test_customer_actor_matrix_retains_one_independent_proof_per_pair(self):
        scenario_id = "customer_actor_matrix_iran_active_outage"
        prefix = "FMX_1234567890ABCDEF_CUSTOMERACTORMATRIXI_ORACLE_"
        payload = self._customer_payload(scenario_id, prefix)
        writer_role = CUSTOMER_LIFECYCLE_MATRIX[scenario_id]["webapp_writer"]
        _validate_customer_actor_payload(
            payload,
            scenario_id=scenario_id,
            writer_role=writer_role,
            prefix=prefix,
        )
        outcome = _customer_actor_outcome(
            payload,
            scenario_id=scenario_id,
            writer_role=writer_role,
        )
        self.assertEqual(outcome["exact_pair_count"], 17)
        self.assertTrue(outcome["bounded_fixture_cleanup_residue_zero"])
        with tempfile.TemporaryDirectory() as raw_root:
            artifact_root = Path(raw_root)
            artifact_root.chmod(0o700)
            args = argparse.Namespace(
                artifact_root=artifact_root,
                operation_id="12345678-1234-4234-9234-123456789abc",
            )
            retained = _retain_customer_actor_evidence(
                args,
                scenario_id=scenario_id,
                observations={"probe": payload},
            )
            self.assertEqual(len(retained), 17)
            self.assertEqual(len({item["evidence"]["path"] for item in retained}), 17)
            self.assertTrue(all((artifact_root / item["evidence"]["path"]).exists() for item in retained))

        forged = copy.deepcopy(payload)
        forged["pairs"][-1]["side_effect_delta"]["offers"] = 1
        with self.assertRaises(Exception):
            _validate_customer_actor_payload(
                forged,
                scenario_id=scenario_id,
                writer_role=writer_role,
                prefix=prefix,
            )

    def test_customer_actor_lifecycle_handlers_enable_all_four_states_together(self):
        self.assertEqual(CUSTOMER_ACTOR_LIVE_HANDLER_IDS, frozenset(CUSTOMER_LIFECYCLE_MATRIX))
        self.assertTrue(CUSTOMER_ACTOR_LIVE_HANDLER_IDS <= IMPLEMENTED_HANDLER_IDS)
        self.assertTrue(CUSTOMER_ACTOR_LIVE_HANDLER_IDS <= IMPLEMENTED_RECIPES)

    def test_messenger_upload_download_handler_requires_writer_fenced_cleanup_contract(self):
        prefix = "FMX_1234567890ABCDEF_MESSENGER_DOER_"
        payload = {
            "schema": "three-site-full-matrix-messenger-regression-probe-v1",
            "status": "passed",
            "scenario_id": "notifications_webpush_messenger_files",
            "role": "webapp_fi",
            "prefix": prefix,
            "writer_epoch": 7,
            "observation": {
                "uploaded_immutable_blob": True,
                "dr_file_intent_and_delivery_created": True,
                "sender_download_authorized": True,
                "recipient_download_authorized": True,
                "unrelated_user_denied": True,
                "notification_persisted_with_durable_webpush_fanout": True,
            },
            "cleanup": {
                "active_fixture_rows_removed": True,
                "encrypted_blob_retention_owned_by_dr": True,
            },
        }
        self.assertEqual(
            MESSENGER_REGRESSION_LIVE_HANDLER_IDS,
            frozenset(
                {
                    "messenger_upload_download_regression",
                    "notifications_webpush_messenger_files",
                }
            ),
        )
        self.assertTrue(MESSENGER_REGRESSION_LIVE_HANDLER_IDS <= IMPLEMENTED_HANDLER_IDS)
        self.assertTrue(MESSENGER_REGRESSION_LIVE_HANDLER_IDS <= IMPLEMENTED_RECIPES)
        _validate_messenger_regression_payload(
            payload,
            prefix=prefix,
            scenario_id="notifications_webpush_messenger_files",
        )
        outcome = _messenger_regression_outcome(
            payload,
            prefix=prefix,
            scenario_id="notifications_webpush_messenger_files",
        )
        self.assertTrue(all(outcome.values()))

        forged = copy.deepcopy(payload)
        forged["observation"]["unrelated_user_denied"] = False
        with self.assertRaises(Exception):
            _validate_messenger_regression_payload(
                forged,
                prefix=prefix,
                scenario_id="notifications_webpush_messenger_files",
            )

    def test_telegram_queue_regression_requires_reserved_lane_and_fake_provider_contract(self):
        campaign_id = "12345678-1234-4234-9234-123456789abc"
        prefix = "FMX_1234567890ABCDEF_QUEUE_DOER_"
        payload = {
            "schema": "three-site-full-matrix-telegram-queue-regression-probe-v1",
            "status": "passed",
            "scenario_id": "queue_publication_edit_callback_private",
            "role": "bot_fi",
            "prefix": prefix,
            "campaign_id": campaign_id,
            "run_id": (
                "full-matrix:12345678-1234-4234-9234-123456789abc:"
                "queue_publication_edit_callback_private:1234567890abcdef"
            ),
            "observation": {
                "full_matrix_lane_is_reserved_and_operationally_invisible": True,
                "publication_edit_callback_and_private_jobs_enqueued": True,
                "each_fixture_job_claimed_under_lease_fence": True,
                "fake_provider_outcomes_applied_without_network_call": True,
                "all_fixture_jobs_terminal": True,
            },
            "cleanup": {
                "only_exact_reserved_fixture_rows_deleted": True,
                "fixture_residue_zero": True,
            },
        }
        self.assertEqual(
            TELEGRAM_QUEUE_REGRESSION_LIVE_HANDLER_IDS,
            frozenset({"queue_publication_edit_callback_private"}),
        )
        self.assertTrue(TELEGRAM_QUEUE_REGRESSION_LIVE_HANDLER_IDS <= IMPLEMENTED_HANDLER_IDS)
        self.assertTrue(TELEGRAM_QUEUE_REGRESSION_LIVE_HANDLER_IDS <= IMPLEMENTED_RECIPES)
        _validate_telegram_queue_regression_payload(
            payload,
            campaign_id=campaign_id,
            prefix=prefix,
        )
        outcome = _telegram_queue_regression_outcome(
            payload,
            campaign_id=campaign_id,
            prefix=prefix,
        )
        self.assertTrue(outcome["provider_boundary_is_fake_and_network_free"])
        forged = copy.deepcopy(payload)
        forged["run_id"] = (
            "full-matrix:aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa:"
            "queue_publication_edit_callback_private:1234567890abcdef"
        )
        with self.assertRaises(Exception):
            _validate_telegram_queue_regression_payload(
                forged,
                campaign_id=campaign_id,
                prefix=prefix,
            )

    def test_application_regressions_require_real_http_and_websocket_contracts(self):
        payloads = {
            "market_trade_account_admin_regression": {
                "fixture_trade_created_by_real_router": True,
                "market_cursor_pages_exact": True,
                "trade_history_cursor_pages_exact": True,
                "account_identity_endpoint_authorized": True,
                "admin_listing_route_authorized": True,
            },
            "websocket_reconnect_and_cursor_reconcile": {
                "fixture_trade_created_by_real_router": True,
                "first_websocket_receives_exact_user_event": True,
                "reconnect_websocket_receives_new_exact_user_event": True,
                "market_cursor_pages_exact": True,
                "trade_history_cursor_pages_exact": True,
            },
        }
        self.assertEqual(
            APPLICATION_REGRESSION_LIVE_HANDLER_IDS,
            frozenset(payloads),
        )
        self.assertTrue(APPLICATION_REGRESSION_LIVE_HANDLER_IDS <= IMPLEMENTED_HANDLER_IDS)
        self.assertTrue(APPLICATION_REGRESSION_LIVE_HANDLER_IDS <= IMPLEMENTED_RECIPES)
        for scenario_id, observation in payloads.items():
            prefix = f"FMX_1234567890ABCDEF_APP_{scenario_id.upper()}_DOER_"
            payload = {
                "schema": "three-site-full-matrix-application-regression-probe-v1",
                "status": "passed",
                "scenario_id": scenario_id,
                "role": "webapp_fi",
                "prefix": prefix,
                "writer_epoch": 7,
                "observation": observation,
                "cleanup": {
                    "only_prefixed_fixture_rows_deleted": True,
                    "fixture_residue_zero": True,
                },
            }
            _validate_application_regression_payload(
                payload,
                scenario_id=scenario_id,
                prefix=prefix,
            )
            self.assertTrue(
                all(
                    _application_regression_outcome(
                        payload,
                        scenario_id=scenario_id,
                        prefix=prefix,
                    ).values()
                )
            )
            forged = copy.deepcopy(payload)
            forged["cleanup"]["fixture_residue_zero"] = False
            with self.assertRaises(Exception):
                _validate_application_regression_payload(
                    forged,
                    scenario_id=scenario_id,
                    prefix=prefix,
                )

    def test_timing_scenario_retains_and_binds_independent_timing_evidence(self):
        with tempfile.TemporaryDirectory() as raw_root:
            artifact_root = Path(raw_root)
            artifact_root.chmod(0o700)

            def retained(name: str, payload: dict) -> dict:
                digest, size = _write_once(
                    artifact_root / name,
                    payload,
                    label=f"test {name}",
                )
                return {"path": name, "sha256": digest, "size": size}

            runner_ref = retained("runner.json", {"runner": "passed"})
            oracle_ref = retained("oracle.json", {"oracle": "passed"})
            timing_ref = retained(
                "timing.json",
                {"schema": "three-site-sync-timing-evidence-v1", "samples": []},
            )
            args = argparse.Namespace(
                operation="scenario",
                operation_id="12345678-1234-4234-9234-123456789abc",
                campaign_id="22345678-1234-4234-9234-123456789abc",
                gate_group_id="32345678-1234-4234-9234-123456789abc",
                execution_class="shared-host-safe",
                campaign_hash="a" * 64,
                release_sha="b" * 40,
                activation_sha="b" * 40,
                artifact_root=artifact_root,
                phase="steady_state",
                scenario_id="three_site_sync_timing_steady_state",
                iteration=1,
                attempt=1,
                failed=None,
            )
            oracle = {
                "expected_outcome": {"scenario_gate_passed": True},
                "observed_outcome": {"scenario_gate_passed": True},
                "oracle_contract": {"scenario_gate_passed": True},
                "oracle_observed": {"scenario_gate_passed": True},
                "customer_assertions": [],
                "sync_timing": {
                    "policy": {"minimum_samples_per_route": 20},
                    "observed": {
                        "policy_satisfied": True,
                        "sample_count": 80,
                        "observed_requests_per_second": 20.0,
                    },
                    "evidence": timing_ref,
                },
            }
            result = _scenario_result(
                args,
                started_at="2026-07-26T00:00:00+00:00",
                finished_at="2026-07-26T00:00:01+00:00",
                duration_seconds=1.0,
                runner={"status": "passed"},
                oracle=oracle,
                runner_ref=runner_ref,
                oracle_ref=oracle_ref,
            )
            self.assertEqual(result["status"], "passed")
            evidence = json.loads(
                (artifact_root / result["artifact_path"]).read_text(encoding="utf-8")
            )
            self.assertIn(timing_ref, evidence["evidence_refs"])
            self.assertIn(
                {
                    "name": "synchronization_timing",
                    "status": "passed",
                    "expected": True,
                    "observed": True,
                    "evidence_refs": ["timing.json"],
                },
                evidence["assertions"],
            )

            malformed = dict(oracle)
            malformed["sync_timing"] = dict(oracle["sync_timing"])
            malformed["sync_timing"]["observed"] = {
                **oracle["sync_timing"]["observed"],
                "policy_satisfied": False,
            }
            with self.assertRaises(LiveDriverError):
                _scenario_result(
                    args,
                    started_at="2026-07-26T00:00:00+00:00",
                    finished_at="2026-07-26T00:00:01+00:00",
                    duration_seconds=1.0,
                    runner={"status": "passed"},
                    oracle=malformed,
                    runner_ref=runner_ref,
                    oracle_ref=oracle_ref,
                )
    def test_disabled_baseline_keeps_queue_gate_off(self):
        source = (
            REPO_ROOT / "core/telegram_delivery_runtime_policy.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "TELEGRAM_DELIVERY_QUEUE_IMPLEMENTATION_READY = False",
            source,
        )

    def test_recipe_catalog_is_exhaustive_and_execution_class_exact(self):
        expected = {
            scenario
            for scenarios in PHASE_SCENARIOS.values()
            for scenario in scenarios
        }
        self.assertEqual(set(RECIPES), expected)
        self.assertEqual(
            {
                scenario
                for scenario, recipe in RECIPES.items()
                if recipe.execution_class == SHARED_HOST_SAFE
            },
            {
                scenario
                for scenarios in scenarios_for_execution_class(SHARED_HOST_SAFE).values()
                for scenario in scenarios
            },
        )
        self.assertEqual(
            {
                scenario
                for scenario, recipe in RECIPES.items()
                if recipe.execution_class == DEDICATED_HOST_DESTRUCTIVE
            },
            {
                scenario
                for scenarios in scenarios_for_execution_class(
                    DEDICATED_HOST_DESTRUCTIVE
                ).values()
                for scenario in scenarios
            },
        )

    def test_recipe_implementation_registry_is_exact_and_complete(self):
        self.assertEqual(set(missing_recipes()), set(RECIPES) - set(IMPLEMENTED_RECIPES))
        self.assertEqual(set(IMPLEMENTED_RECIPES), set(IMPLEMENTED_HANDLER_IDS))
        self.assertEqual(missing_recipes(), ())
        self.assertTrue(all(recipe.implemented for recipe in RECIPES.values()))

    def test_combined_workload_catalog_separates_bot_and_live_webapp_writer(self):
        self.assertEqual(
            set(COMBINED_WORKLOAD_LIVE_IDS),
            {
                "bot_and_webapp_offers_concurrent",
                "requests_trades_partial_settlement",
                "writer_renewal_and_dr_relay_under_load",
            },
        )
        offers = _combined_workload_scenarios(
            "bot_and_webapp_offers_concurrent"
        )
        trades = _combined_workload_scenarios(
            "requests_trades_partial_settlement"
        )
        renewal = _combined_workload_scenarios(
            "writer_renewal_and_dr_relay_under_load"
        )
        self.assertEqual(set(offers), {"bot_fi", "webapp_fi"})
        self.assertEqual(set(trades), {"bot_fi", "webapp_fi"})
        self.assertEqual(set(renewal), {"bot_fi", "webapp_fi"})
        self.assertTrue(set(offers["bot_fi"]).isdisjoint(offers["webapp_fi"]))
        self.assertTrue(set(trades["bot_fi"]).isdisjoint(trades["webapp_fi"]))
        self.assertEqual(len(offers["bot_fi"]), 4)
        self.assertEqual(len(trades["webapp_fi"]), 12)

    def test_legacy_rollback_state_is_operation_scoped_and_closed(self):
        operation_id = "12345678-1234-4234-9234-123456789abc"
        paths = _legacy_rollback_paths(operation_id)
        state = {
            "schema": "three-site-full-matrix-active-fault-v1",
            "kind": "legacy_rollback_rehearsal",
            "operation_id": operation_id,
            "scenario_id": "legacy_rollback_rehearsed",
            **paths,
            "expected_head": "001_add_user_block",
            "created_at": "2026-07-26T00:00:00+00:00",
        }
        self.assertEqual(_validate_legacy_rollback_state(state), state)
        hostile = dict(state)
        hostile["database"] = "postgres"
        with self.assertRaises(Exception):
            _validate_legacy_rollback_state(hostile)
        hostile = dict(state)
        hostile["schema_after"] = "/tmp/other.schema_after"
        with self.assertRaises(Exception):
            _validate_legacy_rollback_state(hostile)

    def test_destructive_recipes_have_explicit_non_generic_doers(self):
        destructive = scenarios_for_execution_class(DEDICATED_HOST_DESTRUCTIVE)
        for phase, scenarios in destructive.items():
            for scenario_id in scenarios:
                recipe = recipe_for(phase, scenario_id)
                self.assertTrue(recipe.doer.startswith("destructive_"))
                self.assertNotEqual(recipe.oracle, "writer_route_state")

    def test_migration_fixture_handlers_exercise_exact_release_policies(self):
        expected_boolean = {
            "integer_id_collision_fixtures": (
                "integer_primary_key_not_global_identity"
            ),
            "natural_identity_cross_site_collision": (
                "natural_identity_wins_over_local_database_id"
            ),
            "unique_ids_real_business_conflict_quarantined": (
                "same_epoch_multi_origin_quarantined"
            ),
            "counter_double_increment_fixture": (
                "counter_replay_has_one_idempotency_identity"
            ),
            "delete_update_resurrection_fixture": (
                "delayed_predelete_update_is_stale"
            ),
            "backup_counts_pass_semantic_parity_fails": (
                "semantic_parity_fails_closed"
            ),
        }
        self.assertEqual(set(expected_boolean), set(MIGRATION_FIXTURE_IDS))
        for scenario_id, assertion in expected_boolean.items():
            self.assertIs(_migration_fixture_outcome(scenario_id)[assertion], True)

    def test_backup_restore_state_names_are_operation_bound_and_exact(self):
        operation_id = "12345678-1234-4234-9234-123456789abc"
        paths = _backup_restore_paths(operation_id)
        state = {
            "schema": "three-site-full-matrix-active-fault-v1",
            "kind": "backup_restore_rehearsal",
            "operation_id": operation_id,
            "scenario_id": "backup_restore_rehearsed",
            **paths,
            "created_at": "2026-07-26T00:00:00+00:00",
        }
        self.assertEqual(_validate_backup_restore_state(state), state)

    def test_final_writer_route_requires_exact_replicated_single_writer_state(self):
        lease = {
            "active_site": "webapp_fi",
            "writer_epoch": 7,
            "control_state": "active",
            "transition_id": "transition-7",
            "witness_lease_id_sha256": "a" * 64,
            "witness_lease_issued_at": "2026-07-26T12:00:00+00:00",
            "witness_lease_expires_at": "2026-07-26T12:05:00+00:00",
            "witness_proof_hash": "b" * 64,
            "lease_refresh_count_for_epoch": 3,
            "database_now": "2026-07-26T12:01:00+00:00",
            "local_active_with_witness_lease": True,
            "local_active_reasons": [],
        }
        standby = dict(lease)
        standby["local_active_with_witness_lease"] = False
        standby["local_active_reasons"] = ["writer_active_site_mismatch"]
        convergence = {
            "scenario_gate_passed": True,
            "all_stream_epochs_exactly_applied": True,
            "webapp_writer_state_equal": True,
        }
        snapshots = {
            role: {
                "managed_fault_container_count": 0,
                "managed_fault_network_count": 0,
            }
            for role in ("bot_fi", "webapp_fi", "webapp_ir", "witness")
        }

        outcome = _final_writer_route_outcome(
            convergence=convergence,
            writer_fi=lease,
            writer_ir=standby,
            host_snapshots=snapshots,
        )
        self.assertEqual(outcome["normal_writer_site"], "webapp_fi")
        self.assertIs(outcome["webapp_ir_is_verified_standby"], True)

        standby["witness_lease_id_sha256"] = "c" * 64
        with self.assertRaises(Exception):
            _final_writer_route_outcome(
                convergence=convergence,
                writer_fi=lease,
                writer_ir=standby,
                host_snapshots=snapshots,
            )

    def test_dr_policy_fixture_handlers_use_closed_ordering_decisions(self):
        expected_boolean = {
            "destination_sequence_private_gap_regression": (
                "destination_private_sequence_gap_blocks"
            ),
            "duplicate_gap_out_of_order_replay": (
                "gap_blocks_out_of_order_event"
            ),
            "same_event_replay_is_idempotent": (
                "same_event_hash_is_duplicate_not_apply"
            ),
            "same_sequence_hash_conflict_quarantine": (
                "same_sequence_different_hash_quarantined"
            ),
            "stale_term_terminal_and_destructive_rejected": (
                "older_term_event_is_stale"
            ),
            "table_priority_cannot_overtake_stream_sequence": (
                "higher_priority_later_sequence_blocked"
            ),
        }
        self.assertEqual(set(expected_boolean), set(DR_POLICY_FIXTURE_IDS))
        for scenario_id, assertion in expected_boolean.items():
            self.assertIs(_dr_policy_fixture_outcome(scenario_id)[assertion], True)

    def test_dr_fault_fixtures_cover_atomicity_and_promotion_boundaries(self):
        expected_boolean = {
            "acknowledged_source_event_absent_target_blocks_promotion": (
                "absent_target_receipt_blocks_promotion"
            ),
            "blob_database_asymmetric_failure_resume": (
                "database_commit_before_file_publish_resumes_by_hash"
            ),
            "missing_or_corrupt_blob_blocks_readiness": (
                "missing_or_corrupt_blob_is_explicit_blocker"
            ),
            "receive_ack_apply_checkpoint_boundaries": (
                "only_applied_checkpoint_is_terminal_ack"
            ),
            "transaction_group_partial_and_corrupt": (
                "partial_group_deferred_without_apply"
            ),
        }
        self.assertEqual(set(expected_boolean), set(DR_FAULT_POLICY_FIXTURE_IDS))
        for scenario_id, assertion in expected_boolean.items():
            self.assertIs(
                _dr_fault_policy_fixture_outcome(scenario_id)[assertion],
                True,
            )

    def test_queue_policy_fixture_handlers_cover_crash_and_provider_boundaries(self):
        expected_boolean = {
            "claim_limiter_provider_crash_boundaries": (
                "provider_crash_lease_recovered"
            ),
            "duplicate_worker_stale_owner_redis_loss": (
                "stale_owner_result_rejected"
            ),
            "enqueue_commit_crash_boundaries": (
                "retry_reuses_committed_main_job"
            ),
            "provider_success_outcome_ambiguity": (
                "success_without_message_identity_is_ambiguous"
            ),
            "rate_limit_timeout_malformed_response": (
                "malformed_payload_is_terminal"
            ),
            "reconciliation_owner_loss_restart": (
                "new_owner_reconciles_without_resend"
            ),
        }
        self.assertEqual(set(expected_boolean), set(QUEUE_POLICY_FIXTURE_IDS))
        for scenario_id, assertion in expected_boolean.items():
            self.assertIs(_queue_policy_fixture_outcome(scenario_id)[assertion], True)

    def test_failover_fault_fixtures_cover_partition_route_and_race_boundaries(self):
        expected_boolean = {
            "arvan_control_failure_rate_limit": (
                "rate_limited_unchanged_readback_retries_same_mutation"
            ),
            "arvan_pop_split_origin_is_safe": (
                "mixed_pop_origins_fail_closed"
            ),
            "asymmetric_ack_both_directions": (
                "ack_before_destination_apply_is_quarantined"
            ),
            "bot_fi_webapp_fi_partition": (
                "partitioned_delivery_remains_durable_pending"
            ),
            "certificate_expiry_during_national_outage": (
                "expired_certificate_never_bypasses_route_oracle"
            ),
            "controller_restart_each_failover_cutpoint": (
                "same_operation_and_plan_resume_at_every_cutpoint"
            ),
            "controller_restart_mid_arvan_mutation": (
                "target_readback_completes_without_second_put"
            ),
            "deployment_or_migration_during_transition_rejected": (
                "handoff_blocks_deployment_and_migration"
            ),
            "dns_global_national_asymmetry": (
                "split_domestic_votes_are_ambiguous"
            ),
            "duplicate_operator_commands_race": (
                "identical_command_resumes_without_epoch_increment"
            ),
            "iran_international_cutoff_promotes_ir": (
                "three_stable_isolated_rounds_authorize_ir_promotion"
            ),
            "object_storage_interruption": (
                "resumed_object_is_verified_by_full_content_hash"
            ),
            "queue_work_inflight_during_promotion": (
                "old_epoch_inflight_work_is_fenced"
            ),
            "simultaneous_promotion_attempt_single_epoch": (
                "first_operation_reserves_single_transition"
            ),
            "webapp_fi_webapp_ir_partition": (
                "partitioned_delivery_remains_durable_pending"
            ),
        }
        self.assertEqual(
            set(expected_boolean),
            set(FAILOVER_FAULT_POLICY_FIXTURE_IDS),
        )
        for scenario_id, assertion in expected_boolean.items():
            self.assertIs(
                _failover_fault_policy_fixture_outcome(scenario_id)[assertion],
                True,
            )

    def test_release_transition_fixtures_cover_commit_cutover_and_rollback(self):
        expected_boolean = {
            "business_event_delivery_commit_boundaries": (
                "root_commit_finalizes_all_events_once"
            ),
            "runtime_cutover_and_forward_rollback": (
                "cutover_has_exactly_one_queue_executor"
            ),
        }
        self.assertEqual(
            set(expected_boolean),
            set(RELEASE_TRANSITION_POLICY_FIXTURE_IDS),
        )
        for scenario_id, assertion in expected_boolean.items():
            self.assertIs(
                _release_transition_policy_outcome(scenario_id)[assertion],
                True,
            )

    def test_recovery_fixtures_cover_outage_barrier_drain_and_resume(self):
        expected_boolean = {
            "bot_remains_active_all_outage_classes": (
                "bot_authority_is_not_a_webapp_writer_transition_target"
            ),
            "database_blob_inverse_completion_reconciles": (
                "committed_database_missing_blob_blocks"
            ),
            "fi_epoch_reacquire_and_route_switch": (
                "fi_cannot_reacquire_without_approved_failback"
            ),
            "file_transfer_interruption_resumes_by_hash": (
                "interrupted_file_resumes_to_exact_sha256"
            ),
            "final_write_barrier_with_live_arrivals": (
                "live_post_fence_arrival_blocks_failback"
            ),
            "ir_remains_active_during_recovery": (
                "link_return_without_convergence_keeps_ir_active"
            ),
            "old_http_websocket_connections_drained": (
                "any_old_http_or_websocket_connection_blocks"
            ),
            "recovery_and_failback_restart_resume": (
                "identical_failback_operation_resumes"
            ),
            "short_medium_long_outage_rules": (
                "short_boundary_is_inclusive_at_120_seconds"
            ),
        }
        self.assertEqual(
            set(expected_boolean),
            set(RECOVERY_POLICY_FIXTURE_IDS),
        )
        for scenario_id, assertion in expected_boolean.items():
            self.assertIs(
                _recovery_policy_fixture_outcome(scenario_id)[assertion],
                True,
            )

    def test_runtime_policy_fixtures_cover_queue_relay_capacity_and_dpi(self):
        expected = {
            "ambiguous_client_command_retry_is_idempotent",
            "batch_flush_inflight_boundaries",
            "database_redis_blob_storage_watermarks",
            "dpi_request_byte_budget_enforced",
            "dropped_wakeup_still_durably_drains",
            "finland_directions_one_fifty_events_each",
            "healthy_link_never_accumulates_backlog",
            "recovery_eta_and_non_starvation",
            "relay_preserves_origin_without_echo",
            "webapp_dr_three_hundred_events_amplified",
        }
        self.assertEqual(set(RUNTIME_POLICY_FIXTURE_IDS), expected)
        for scenario_id in expected:
            self.assertTrue(
                all(_runtime_policy_fixture_outcome(scenario_id).values()),
                scenario_id,
            )

    def test_security_policy_fixtures_reject_tamper_and_rotation_mismatch(self):
        expected_boolean = {
            "expired_plan_only_safe_fenced_recovery": (
                "expired_after_source_fence_requires_safe_rollback"
            ),
            "fake_event_and_raw_sql_bypass_denied": (
                "forged_signed_event_body_rejected"
            ),
            "hostile_artifact_path_and_signature_denied": (
                "signed_context_tamper_rejected"
            ),
            "protocol_schema_key_rotation_mismatch": (
                "rotated_secret_rejects_old_signature"
            ),
            "restored_old_epoch_effects_remain_fenced": (
                "restored_pending_old_epoch_cancelled"
            ),
            "startup_mutation_on_fenced_standby_rejected": (
                "fenced_startup_cannot_be_local_writer"
            ),
            "wrong_pairwise_identity_and_nonce_replay": (
                "same_key_and_nonce_replay_rejected"
            ),
        }
        self.assertEqual(set(expected_boolean), set(SECURITY_POLICY_FIXTURE_IDS))
        for scenario_id, assertion in expected_boolean.items():
            self.assertIs(
                _security_policy_fixture_outcome(scenario_id)[assertion],
                True,
            )

    def test_convergence_handlers_require_exact_all_epoch_stream_and_blob_parity(self):
        sites = ("bot_fi", "webapp_fi", "webapp_ir")
        digest = "a" * 64
        empty_digest = "b" * 64
        writer = [
            {
                "authority": "webapp",
                "active_site": "webapp_fi",
                "writer_epoch": 3,
                "control_state": "active",
                "transition_id": "12345678-1234-4234-9234-123456789abc",
            }
        ]
        states = {}
        for site in sites:
            sources = [
                {
                    "origin_site": site,
                    "producer_epoch": 3,
                    "destination_site": destination,
                    "source_sequence": 7,
                    "source_transaction_hash": digest,
                }
                for destination in sites
                if destination != site
            ]
            destinations = [
                {
                    "origin_site": origin,
                    "producer_epoch": 3,
                    "destination_site": site,
                    "received_sequence": 7,
                    "applied_sequence": 7,
                    "received_transaction_hash": digest,
                    "applied_transaction_hash": digest,
                }
                for origin in sites
                if origin != site
            ]
            webapp = site != "bot_fi"
            states[site] = {
                "database_business_sha256": digest,
                "database_table_set_sha256": digest,
                "database_table_count": 12,
                "database_row_count": 24,
                "source_streams": sources,
                "destination_streams": destinations,
                "unresolved_conflict_count": 0,
                "blob_set_sha256": digest if webapp else empty_digest,
                "blob_count": 1 if webapp else 0,
                "blob_manifest_count": 1 if webapp else 0,
                "blob_readback_count": 1 if webapp else 0,
                "event_delivery_status_counts": {"acknowledged": 2},
                "effect_status_counts": (
                    {"succeeded": 1} if site == "bot_fi" else {}
                ),
                "telegram_job_status_counts": (
                    {"sent": 1} if site == "bot_fi" else {}
                ),
                "writer_state_sha256": digest,
                "writer_state": writer if webapp else [],
                "runtime_producer_epoch": 3,
            }
        self.assertEqual(
            set(CONVERGENCE_HANDLER_IDS),
            {
                "applied_checkpoint_conflict_effect_gates",
                "database_and_blob_final_parity",
                "queue_jobs_effects_conflicts_reconciled",
            },
        )
        for scenario_id in CONVERGENCE_HANDLER_IDS:
            self.assertIs(
                _convergence_outcome(
                    copy.deepcopy(states),
                    scenario_id=scenario_id,
                )["scenario_gate_passed"],
                True,
            )
        drifted = copy.deepcopy(states)
        drifted["webapp_ir"]["destination_streams"][0]["applied_sequence"] = 6
        with self.assertRaises(Exception):
            _convergence_outcome(
                drifted,
                scenario_id="applied_checkpoint_conflict_effect_gates",
            )

    def test_sealed_driver_has_no_shell_execution_surface(self):
        path = REPO_ROOT / "scripts/full_matrix_drivers/driver.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertNotIn("shell=True", source)
        self.assertNotIn("create_subprocess_shell", source)
        self.assertIn("os.memfd_create", source)
        self.assertIn("fcntl.F_SEAL_WRITE", source)
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"system", "popen"}
        ]
        self.assertEqual(calls, [])

    def test_convergence_observer_uses_per_site_access_and_put(self):
        source = (
            REPO_ROOT / "scripts/run_three_site_staging_convergence_observer.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'CONFIG_SCHEMA = "three-site-staging-convergence-observer-config-v2"',
            source,
        )
        self.assertIn('expected_transport = "local" if site == "bot_fi" else "ssh"', source)
        self.assertIn('client.generate_presigned_url(', source)
        self.assertIn('"put_object"', source)
        self.assertNotIn("generate_presigned_post", source)

    def test_convergence_snapshot_uses_database_readonly_transaction_options(self):
        source = (
            REPO_ROOT / "scripts/collect_three_site_staging_convergence_snapshot.py"
        ).read_text(encoding="utf-8")
        self.assertIn('isolation_level="REPEATABLE READ"', source)
        self.assertIn("postgresql_readonly=True", source)
        self.assertNotIn("SET TRANSACTION ISOLATION LEVEL", source)

    def test_object_storage_control_protocol_is_signed_bound_and_replay_safe(self):
        controller = Ed25519PrivateKey.generate()
        agent = Ed25519PrivateKey.generate()
        controller_public = controller.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        agent_public = agent.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        now = datetime.now(timezone.utc)
        request = build_request(
            private_key=controller,
            controller_key_id=public_key_id(controller_public),
            request_id=str(uuid.uuid4()),
            campaign_id="full-matrix-destructive-20260726",
            release_sha="a" * 40,
            sequence=4,
            attempt=1,
            operation="host_snapshot",
            context={
                "compose_file": "/root/secure-envs/full-matrix/roles/webapp-ir.compose.yml",
                "env_file": "/root/secure-envs/full-matrix/roles/webapp-ir.env",
                "project_name": "full-matrix-webapp-ir",
                "storage_root": "/srv/trading-bot-three-site-staging-data",
            },
            issued_at=now,
            expires_at=now + timedelta(minutes=5),
        )
        verified = verify_request(
            request,
            controller_public_key_b64=public_key_b64(controller_public),
            expected_release_sha="a" * 40,
            expected_campaign_id="full-matrix-destructive-20260726",
            minimum_sequence=4,
            now=now,
        )
        request_hash = __import__("hashlib").sha256(
            canonical_bytes(verified)
        ).hexdigest()
        response = build_response(
            private_key=agent,
            agent_key_id=public_key_id(agent_public),
            request=verified,
            request_sha256=request_hash,
            status="passed",
            result={"status": "passed"},
            completed_at=now,
        )
        observed = verify_response(
            response,
            agent_public_key_b64=public_key_b64(agent_public),
            request=verified,
            request_sha256=request_hash,
        )
        self.assertEqual(observed["status"], "passed")
        with self.assertRaises(ObjectStorageProtocolError):
            verify_request(
                request,
                controller_public_key_b64=public_key_b64(controller_public),
                expected_release_sha="a" * 40,
                expected_campaign_id="full-matrix-destructive-20260726",
                minimum_sequence=5,
                now=now,
            )
        forged = dict(request)
        forged["operation"] = "scenario_execute"
        with self.assertRaises(ObjectStorageProtocolError):
            verify_request(
                forged,
                controller_public_key_b64=public_key_b64(controller_public),
                expected_release_sha="a" * 40,
                expected_campaign_id="full-matrix-destructive-20260726",
                minimum_sequence=4,
                now=now,
            )

    def test_object_storage_control_rejects_shell_execution_surfaces(self):
        controller = Ed25519PrivateKey.generate()
        public = controller.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        now = datetime.now(timezone.utc)
        with self.assertRaises(ObjectStorageProtocolError):
            build_request(
                private_key=controller,
                controller_key_id=public_key_id(public),
                request_id=str(uuid.uuid4()),
                campaign_id="full-matrix-destructive-20260726",
                release_sha="b" * 40,
                sequence=1,
                attempt=1,
                operation="scenario_execute",
                context={"command": "id"},
                issued_at=now,
                expires_at=now + timedelta(minutes=5),
            )

    def test_object_storage_agent_has_no_shell_or_subprocess_shell_surface(self):
        for relative in (
            "scripts/full_matrix_live/object_storage_agent.py",
            "scripts/full_matrix_live/object_storage_controller.py",
            "scripts/full_matrix_live/site_agent.py",
        ):
            source = (REPO_ROOT / relative).read_text(encoding="utf-8")
            tree = ast.parse(source)
            self.assertNotIn("shell=True", source)
            self.assertNotIn("create_subprocess_shell", source)
            calls = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"system", "popen"}
            ]
            self.assertEqual(calls, [], relative)

    def test_object_storage_agent_bundle_is_exact_and_hash_manifested(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = root / "agent-config.json"
            key = root / "agent-ed25519.pem"
            identity = root / "agent-age-identity.txt"
            for path, payload in (
                (config, b'{"schema":"test"}\n'),
                (key, b"private-test-key\n"),
                (identity, b"AGE-SECRET-KEY-TEST\n"),
            ):
                path.write_bytes(payload)
                path.chmod(0o600)
            output = root / "agent-bundle.tar.gz"
            args = type(
                "Args",
                (),
                {
                    "agent_config": config,
                    "agent_signing_key": key,
                    "agent_age_identity": identity,
                    "release_sha": "c" * 40,
                    "campaign_id": "full-matrix-destructive-20260726",
                    "output": output,
                },
            )()
            result = build_agent_bundle(args)
            manifest, payloads = verify_agent_bundle(output)
            self.assertEqual(result["status"], "built")
            self.assertEqual(manifest["release_sha"], "c" * 40)
            self.assertEqual(set(manifest["files"]), set(payloads))
            self.assertIn("object_storage_agent.py", payloads)
            self.assertIn("agent-ed25519.pem", payloads)


if __name__ == "__main__":
    unittest.main()
