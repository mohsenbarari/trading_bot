"""Reconciliation witness for internal session verifier failure characterization."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import stat
import unittest

from tests.test_p1_00b_characterization_ledger_prefix_helper import read_ledger_prefix
import tests.test_p1_00b_characterization_notification_mark_all_accounting as accounting

PACKET_KEY = "business_768_live786_verifier_only_path"
PACKET_PATH = (
    "docs/refactor/two-server/section-1/inventory/"
    "recent-characterization-business-768-live786-verifier-only.json"
)
PRIOR_KEY = "business_768_live786_replace_execute_singleton_path"
PRIOR_PATH = (
    "docs/refactor/two-server/section-1/inventory/"
    "recent-characterization-business-768-live786-replace-execute-singleton.json"
)
PRIOR_SHA = "cf251e42b72c41490e205499dbc269704d75ba422dbf730ef1027d29101d579c"
PRIOR_BYTES = 9738
SOURCE_HEAD = "c9e9b30cd919472b1e253bc9b6da53da644171a8"
SOURCE_PATH = "tests/test_p1_00b_characterization_internal_session_verifier_failures_batch.py"
SOURCE_SHA = "6ed021334ea4384e5f1b98897a1aeb6c45f0cfed29105bb90e9a227ff8d803c8"
SOURCE_BYTES = 10936
SOURCE_COMMIT = "9a3bfcebe29399253b7ee60c605e72cf73e19d82"
SOURCE_PARENT = "777ed75419ed7083d875c262da2beb8103017d03"
BEFORE_MANIFEST_SHA = "70ccb37b5aefa9c92c284927752e8e15053b054e81a6f79125150a10024c136b"
BEFORE_MANIFEST_BYTES = 4218
LEDGER_PATH = "docs/refactor/two-server/section-1/00-stage-ledger.md"
LEDGER_PREFIX_SHA = "a25deecb4880ed4c7ceb905ae5db5048c8d3fc7b58129bc6cfdcf4d31d2c05c2"
LEDGER_PREFIX_BYTES = 109329
CLASS_NAME = "InternalSessionVerifierFailuresCharacterizationTests"
HELPER_NAME = "_assert_verifier_exception_stops_before_sql_and_side_effects"
HANDLER_SPECS = (
    (
        "reset_verifier_exception_before_sql_and_reset",
        "test_reset_verifier_exception_stops_before_sql_and_reset",
        "internal_reset_user_sessions",
        "api/routers/sessions.py:internal_reset_user_sessions",
        "API-AUTH-POST-SESSIONS-INTERNAL-RESET-USER-SESSIONS-L562",
    ),
    (
        "replace_verifier_exception_before_sql_and_revoke",
        "test_replace_verifier_exception_stops_before_sql_and_revoke",
        "internal_replace_user_sessions",
        "api/routers/sessions.py:internal_replace_user_sessions",
        "API-AUTH-POST-SESSIONS-INTERNAL-REPLACE-USER-SESSIONS-L593",
    ),
)
SELECTORS = [f"{SOURCE_PATH}::{CLASS_NAME}::{row[1]}" for row in HANDLER_SPECS]
SEEDS = [row[4] for row in HANDLER_SPECS]
SYMBOLS = [row[3] for row in HANDLER_SPECS]
SCENARIOS = [row[0] for row in HANDLER_SPECS]
EXPECTED_PROVENANCE = {
    "authorship": "GEMINI_WORKER",
    "worker_run_id": "p1-00b-799b-c002-session-verifier-worker-0",
    "worker_conversation_id": "e3bd97ba-a3ed-4e7d-9b38-bfcc9b5cb287",
    "proposal_sha256": "63527dbee338e08f91e50576f734adcba1aa00d8add9724da6dc28a376174861",
    "native_evidence_path": (
        ".local/two-site-refactor/pipeline/"
        "p1-00b-799b-c002-session-verifier/pre-review-tests-0.json"
    ),
    "native_evidence_sha256": "cc2786582c7b9e1415524adcc1b3fca113316d14c33e5effb497043d8fc371af",
    "native_state": "PASSED",
    "native_selectors": SELECTORS,
    "unsupported_specs": [],
    "reviewer_run_id": "p1-00b-799b-c002-session-verifier-reviewer-0",
    "reviewer_conversation_id": "63814c9f-720e-42e8-9589-1d3ec0929bce",
    "reviewer_result_sha256": "65e2c48e8ab2c0d78f6d6dbba71b54d26c11e8a93a236bf30321f5edcf749f33",
    "reviewer_state": "VERIFIED",
}


class VerifierReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._helper = accounting.NotificationMarkAllAccountingTests(
            "test_live_set_has_exact_singleton751_delta_and_consistent_counts"
        )

    def _read_ledger_prefix(self) -> bytes:
        return read_ledger_prefix(
            accounting.ROOT / LEDGER_PATH,
            LEDGER_PREFIX_BYTES,
            LEDGER_PREFIX_SHA,
        )

    def _reconstruct_before_manifest(self, manifest: dict) -> tuple[dict, bytes]:
        evidence = manifest["evidence_sources"]
        keys = list(evidence.keys())
        self.assertIn(PRIOR_KEY, keys)
        self.assertIn(PACKET_KEY, keys)
        prior_idx = keys.index(PRIOR_KEY)
        packet_idx = keys.index(PACKET_KEY)
        self.assertEqual(packet_idx, prior_idx + 1)
        before_sources = {k: evidence[k] for k in keys[: prior_idx + 1]}
        before = {
            "schema_version": 1,
            "stage": "P1-00b",
            "assignment_id": "WA-P1-00B-FREEZE-20260904-001",
            "inventory": copy.deepcopy(manifest["inventory"]),
            "evidence_sources": before_sources,
            "counts": {
                "baseline_evidenced_seed_count": 135,
                "reconciled_new_seed_count": 263,
                "evidenced_seed_count": 398,
                "remaining_seed_count": 67,
            },
            "coverage_status": "PARTIAL",
            "canonical_blockers": list(accounting.BLOCKERS),
        }
        raw = (json.dumps(before, indent=2) + "\n").encode("utf-8")
        return before, raw

    def _validate(
        self,
        manifest: dict,
        packet: dict,
        prior_packet: dict,
        source_bytes: bytes,
        prior_bytes: bytes,
        ledger_prefix: bytes,
    ) -> None:
        self.assertEqual(len(source_bytes), SOURCE_BYTES)
        self.assertEqual(hashlib.sha256(source_bytes).hexdigest(), SOURCE_SHA)
        self.assertEqual(((accounting.ROOT / SOURCE_PATH).stat().st_mode & 0o111), 0)

        tree = ast.parse(source_bytes.decode("utf-8"))
        classes = [
            n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == CLASS_NAME
        ]
        self.assertEqual(len(classes), 1)
        async_methods = {
            n.name: n
            for n in classes[0].body
            if isinstance(n, ast.AsyncFunctionDef) and n.name.startswith("test_")
        }
        self.assertEqual(set(async_methods.keys()), {row[1] for row in HANDLER_SPECS})
        for _, method_name, handler_ident, _, _ in HANDLER_SPECS:
            method_node = async_methods[method_name]
            awaited_calls = [
                n.value
                for n in ast.walk(method_node)
                if isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
            ]
            self.assertEqual(len(awaited_calls), 1)
            call = awaited_calls[0]
            self.assertIsInstance(call.func, ast.Attribute)
            self.assertIsInstance(call.func.value, ast.Name)
            self.assertEqual((call.func.value.id, call.func.attr), ("self", HELPER_NAME))
            self.assertEqual(len(call.args), 1)
            self.assertIsInstance(call.args[0], ast.Name)
            self.assertEqual(call.args[0].id, handler_ident)

        self.assertEqual(packet["schema_version"], 1)
        self.assertEqual(packet["stage"], "P1-00b")
        self.assertEqual(packet["assignment_id"], "WA-P1-00B-BUSINESS-LIVE-20261023-799B")
        self.assertEqual(packet["parent_assignment_id"], "WA-P1-00B-FREEZE-20260904-001")
        self.assertEqual(packet["task_id"], "p1-00b-799b-c010-verifier-only-reconciliation")
        self.assertEqual(
            packet["scenario_key"], "P1-00B-RECONCILE-V1.internal-verifier-only-799"
        )
        self.assertEqual(packet["source_head"], SOURCE_HEAD)

        records = packet["records"]
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["path"], SOURCE_PATH)
        self.assertEqual(record["sha256"], SOURCE_SHA)
        self.assertEqual(record["utf8_bytes"], SOURCE_BYTES)
        self.assertEqual(record["mode"], "100644")
        self.assertEqual(record["last_commit"], SOURCE_COMMIT)
        self.assertEqual(record["parent"], SOURCE_PARENT)
        self.assertEqual(record["canonical_symbols"], SYMBOLS)
        self.assertEqual(record["seed_ids"], SEEDS)
        self.assertEqual(record["scenarios"], SCENARIOS)
        self.assertEqual(record["test_selectors"], SELECTORS)
        self.assertEqual(record["historical_provenance"], EXPECTED_PROVENANCE)

        contract = record["characterized_contract"]
        self.assertEqual(contract["entrypoints"], SYMBOLS)
        self.assertEqual(
            contract["ordered_events"],
            [
                "request.body",
                "headers.get:X-Timestamp",
                "headers.get:X-Signature",
                "headers.get:X-API-Key",
                "verify_session_authority_signature",
            ],
        )
        req_args = contract["request_and_verifier_arguments"]
        self.assertEqual(req_args["body_call_count"], 1)
        self.assertEqual(req_args["body_await_count"], 1)
        self.assertEqual(req_args["headers_property_access_count"], 3)
        self.assertEqual(
            req_args["header_get_keys"], ["X-Timestamp", "X-Signature", "X-API-Key"]
        )
        self.assertEqual(req_args["verifier_call_count"], 1)
        self.assertEqual(req_args["verifier_positional_args_count"], 1)
        self.assertTrue(req_args["verifier_body_identity_preserved"])
        self.assertEqual(
            req_args["verifier_kwargs_keys"], ["timestamp", "signature", "api_key"]
        )
        zero_Check = contract["zero_side_effect_and_fence_invariants"]
        self.assertEqual(zero_Check["select_calls"], 0)
        self.assertEqual(zero_Check["reset_user_session_state_calls"], 0)
        self.assertEqual(zero_Check["reset_user_session_state_awaits"], 0)
        self.assertEqual(zero_Check["force_clear_sessions_calls"], 0)
        self.assertEqual(zero_Check["force_clear_sessions_awaits"], 0)
        self.assertEqual(zero_Check["network_denied_connects_delta"], 0)
        self.assertEqual(zero_Check["live_redis_constructs_delta"], 0)
        self.assertTrue(zero_Check["handler_identities_unchanged"])
        self.assertEqual(len(record["exclusions"]), 6)

        handlers = packet["registered_handlers"]
        self.assertEqual(len(handlers), 2)
        for idx, (scenario, method_name, _, symbol, seed) in enumerate(HANDLER_SPECS):
            row = handlers[idx]
            self.assertEqual(row["scenario"], scenario)
            self.assertEqual(row["scenario_key"], scenario)
            self.assertEqual(row["selector"], f"{SOURCE_PATH}::{CLASS_NAME}::{method_name}")
            self.assertEqual(row["canonical_symbol"], symbol)
            self.assertEqual(row["seed_id"], seed)
            self.assertEqual(row["source_path"], SOURCE_PATH)
            self.assertEqual(row["source_file_sha256"], SOURCE_SHA)
            self.assertEqual(row["source_commit"], SOURCE_COMMIT)

        self.assertEqual(len(prior_bytes), PRIOR_BYTES)
        self.assertEqual(hashlib.sha256(prior_bytes).hexdigest(), PRIOR_SHA)
        prior_acc = prior_packet["accounting"]
        acc = packet["accounting"]
        self.assertEqual(acc["pending_five"], 5)
        self.assertEqual(acc["units_since_reconciliation"], 5)
        self.assertEqual(acc["total_behavior_units"], 5)
        self.assertEqual(acc["total_handler_scenarios"], 7)
        self.assertEqual(
            acc["prior_reconciled_singleton_provenance"],
            {
                "path": PRIOR_PATH,
                "sha256": PRIOR_SHA,
                "utf8_bytes": PRIOR_BYTES,
                "accepted_commit": SOURCE_HEAD,
                "cumulative_reconciled_units": prior_acc[
                    "cumulative_reconciled_units_on_exact_acceptance"
                ],
                "cumulative_reconciled_scenarios": prior_acc[
                    "cumulative_reconciled_scenarios_on_exact_acceptance"
                ],
            },
        )
        this_units = acc["reconciled_units_in_this_packet_on_exact_acceptance"]
        this_scenarios = acc["reconciled_scenarios_in_this_packet_on_exact_acceptance"]
        self.assertEqual((this_units, this_scenarios), (1, 2))
        self.assertEqual(
            acc["cumulative_reconciled_units_on_exact_acceptance"],
            prior_acc["cumulative_reconciled_units_on_exact_acceptance"] + this_units,
        )
        self.assertEqual(
            acc["cumulative_reconciled_scenarios_on_exact_acceptance"],
            prior_acc["cumulative_reconciled_scenarios_on_exact_acceptance"] + this_scenarios,
        )
        self.assertEqual(
            acc["cumulative_reconciled_units_on_exact_acceptance"], 3
        )
        self.assertEqual(
            acc["cumulative_reconciled_scenarios_on_exact_acceptance"], 4
        )
        self.assertEqual(
            acc["remaining_unreconciled_units_on_exact_acceptance"],
            prior_acc["remaining_unreconciled_units_on_exact_acceptance"] - this_units,
        )
        self.assertEqual(
            acc["remaining_unreconciled_scenarios_on_exact_acceptance"],
            prior_acc["remaining_unreconciled_scenarios_on_exact_acceptance"] - this_scenarios,
        )
        self.assertEqual(
            (
                acc["remaining_unreconciled_units_on_exact_acceptance"],
                acc["remaining_unreconciled_scenarios_on_exact_acceptance"],
            ),
            (2, 3),
        )
        expected_remaining = [
            row
            for row in prior_acc["unreconciled_units_after_singleton_on_exact_acceptance"]
            if row["path"] != SOURCE_PATH
        ]
        self.assertEqual(len(expected_remaining), 2)
        self.assertEqual(
            acc["unreconciled_units_after_verifier_on_exact_acceptance"],
            expected_remaining,
        )
        self.assertEqual(
            sum(r["handler_scenarios"] for r in expected_remaining),
            acc["remaining_unreconciled_scenarios_on_exact_acceptance"],
        )
        self.assertFalse(acc["group_reconciliation_complete"])
        self.assertFalse(acc["canonical_manifest_and_ledger_integration_complete"])
        self.assertEqual(acc["ledger_integration_status"], "PENDING")
        self.assertTrue(acc["no_sixth_behavior_accepted_before_complete_reconciliation"])
        self.assertEqual(acc["added_behavior_units"], 0)
        self.assertEqual(acc["accepted_full_seeds"], 0)
        self.assertEqual(acc["raw_registration_delta"], 0)
        self.assertEqual(
            acc["preserved_deferred_and_failed_reservations"],
            {
                "deferred_a2_task_id": "p1-00b-799b-c006-session-prequery-reconciliation-a2",
                "deferred_a2_status": "UNACCEPTED_RESERVED_NEVER_REPLAY_OR_RELEASE",
                "failed_authority_singleton_task_id": (
                    "p1-00b-799b-c009-authority-execute-singleton-reconciliation"
                ),
                "failed_authority_singleton_status": (
                    "UNACCEPTED_RESERVED_NEVER_REPLAY_OR_RELEASE"
                ),
            },
        )
        self.assertEqual(
            acc["previous_completed_group_provenance"],
            prior_acc["previous_completed_group_provenance"],
        )
        self.assertEqual(len(ledger_prefix), LEDGER_PREFIX_BYTES)
        self.assertEqual(hashlib.sha256(ledger_prefix).hexdigest(), LEDGER_PREFIX_SHA)
        self.assertEqual(
            acc["ledger_prefix_binding"],
            {
                "path": LEDGER_PATH,
                "unchanged_ledger_sha256": LEDGER_PREFIX_SHA,
                "unchanged_ledger_utf8_bytes": LEDGER_PREFIX_BYTES,
            },
        )

        before_manifest, before_raw = self._reconstruct_before_manifest(manifest)
        self.assertEqual(len(before_raw), BEFORE_MANIFEST_BYTES)
        self.assertEqual(hashlib.sha256(before_raw).hexdigest(), BEFORE_MANIFEST_SHA)
        self.assertEqual(manifest["evidence_sources"][PACKET_KEY], PACKET_PATH)

        receipt751 = self._helper._json(accounting.RECEIPT)
        surface, baseline, before_set = self._helper._derive(before_manifest, receipt751)
        self.assertEqual((len(surface), len(baseline), len(before_set)), (465, 135, 398))
        self.assertTrue(set(SEEDS) <= before_set)

        cutoff_manifest = copy.deepcopy(before_manifest)
        cutoff_manifest["evidence_sources"][PACKET_KEY] = PACKET_PATH
        _, _, after_set = self._helper._derive(
            cutoff_manifest, receipt751, {PACKET_PATH: packet}
        )
        self.assertEqual(after_set, before_set)
        self.assertEqual(after_set - before_set, set())
        self.assertEqual(before_set - after_set, set())

        live_surface, live_baseline, live_set = self._helper._derive(
            manifest, receipt751, {PACKET_PATH: packet}
        )
        self.assertEqual((live_surface, live_baseline), (surface, baseline))
        self.assertTrue(after_set <= live_set)
        self.assertEqual(
            manifest["counts"],
            {
                "baseline_evidenced_seed_count": len(live_baseline),
                "reconciled_new_seed_count": len(live_set - live_baseline),
                "evidenced_seed_count": len(live_set),
                "remaining_seed_count": len(live_surface - live_set),
            },
        )

        promo = packet["promotion_assessment"]
        self.assertEqual(promo["promoted_seed_ids"], [])
        self.assertEqual(promo["already_registered_seed_ids"], SEEDS)
        self.assertEqual(promo["delta_distinct_seed_count"], 0)
        self.assertEqual(promo["removed_seed_ids"], [])
        self.assertEqual(promo["duplicate_new_seed_ids"], [])
        self.assertEqual(promo["before_manifest_sha256"], BEFORE_MANIFEST_SHA)
        self.assertEqual(promo["before_manifest_utf8_bytes"], BEFORE_MANIFEST_BYTES)
        expected_canonical = {
            "expected": 465,
            "baseline": 135,
            "reconciled_new": 263,
            "evidenced": 398,
            "remaining": 67,
        }
        self.assertEqual(promo["canonical_before"], expected_canonical)
        self.assertEqual(promo["canonical_after_on_exact_acceptance"], expected_canonical)
        self.assertEqual(promo["added_behavior_units"], 0)
        self.assertEqual(promo["accepted_full_seeds"], 0)
        self.assertEqual(promo["raw_registration_delta"], 0)
        self.assertEqual(promo["full_seed_acceptance"], "NOT_ASSESSED")
        self.assertEqual(
            packet["strict_canonical_progress"],
            {"proven": 288, "partial": 177, "total": 465},
        )
        self.assertEqual(packet["origin_matrix_unresolved_cells"], 24)
        self.assertEqual(packet["canonical_blockers"], accounting.BLOCKERS)
        self.assertEqual(
            packet["seed_acceptance_status"], "FULL_SEED_ACCEPTANCE_NOT_ASSESSED"
        )
        self.assertFalse(packet["closures_authorized"])
        self.assertEqual(packet["stages"], {"P1-00": "COMPLETE", "P1-00b": "IN_PROGRESS"})

    def test_exact_verifier_source_registration_and_accounting(self) -> None:
        self._validate(
            self._helper._json(accounting.MANIFEST),
            self._helper._json(PACKET_PATH),
            self._helper._json(PRIOR_PATH),
            self._helper._read(SOURCE_PATH),
            self._helper._read(PRIOR_PATH),
            self._read_ledger_prefix(),
        )

    def test_rejects_concrete_registration_and_pending_drift(self) -> None:
        manifest_raw = self._helper._read(accounting.MANIFEST)
        packet_raw = self._helper._read(PACKET_PATH)
        prior_raw = self._helper._read(PRIOR_PATH)
        source_raw = self._helper._read(SOURCE_PATH)
        ledger_prefix = self._read_ledger_prefix()

        manifest = json.loads(manifest_raw.decode("utf-8"))
        packet = json.loads(packet_raw.decode("utf-8"))
        prior_packet = json.loads(prior_raw.decode("utf-8"))

        self._validate(manifest, packet, prior_packet, source_raw, prior_raw, ledger_prefix)

        with self.assertRaises(AssertionError):
            self._validate(
                manifest,
                packet,
                prior_packet,
                source_raw + b"\n",
                prior_raw,
                ledger_prefix,
            )

        for idx in (0, 1):
            bad_selector_packet = copy.deepcopy(packet)
            bad_selector_packet["registered_handlers"][idx]["selector"] += "_wrong"
            with self.assertRaises(AssertionError):
                self._validate(
                    manifest,
                    bad_selector_packet,
                    prior_packet,
                    source_raw,
                    prior_raw,
                    ledger_prefix,
                )

        missing_mapping = copy.deepcopy(manifest)
        del missing_mapping["evidence_sources"][PACKET_KEY]
        with self.assertRaises(AssertionError):
            self._validate(
                missing_mapping, packet, prior_packet, source_raw, prior_raw, ledger_prefix
            )

        bad_counts = copy.deepcopy(manifest)
        bad_counts["counts"]["evidenced_seed_count"] += 1
        with self.assertRaises(AssertionError):
            self._validate(
                bad_counts, packet, prior_packet, source_raw, prior_raw, ledger_prefix
            )

        premature_decrement = copy.deepcopy(packet)
        premature_decrement["accounting"]["pending_five"] = 4
        with self.assertRaises(AssertionError):
            self._validate(
                manifest,
                premature_decrement,
                prior_packet,
                source_raw,
                prior_raw,
                ledger_prefix,
            )

        premature_complete = copy.deepcopy(packet)
        premature_complete["accounting"]["group_reconciliation_complete"] = True
        with self.assertRaises(AssertionError):
            self._validate(
                manifest,
                premature_complete,
                prior_packet,
                source_raw,
                prior_raw,
                ledger_prefix,
            )

        self._validate(manifest, packet, prior_packet, source_raw, prior_raw, ledger_prefix)
        self.assertEqual(self._helper._read(accounting.MANIFEST), manifest_raw)
        self.assertEqual(self._helper._read(PACKET_PATH), packet_raw)
        self.assertEqual(self._helper._read(PRIOR_PATH), prior_raw)
        self.assertEqual(self._helper._read(SOURCE_PATH), source_raw)
        self.assertEqual(self._read_ledger_prefix(), ledger_prefix)


if __name__ == "__main__":
    unittest.main()
