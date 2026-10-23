"""Physical-data accounting witness for internal_replace_user_sessions execute singleton.

Stdlib only; no application, custom-session, SQLite, network, subprocess, or .local access.
Verifies partial singleton registration of the already accepted replace execute-failure
characterization test without claiming new behavior credit or group/Stage closure.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import stat
import unittest

import tests.test_p1_00b_characterization_notification_mark_all_accounting as accounting

PACKET_PATH = (
    "docs/refactor/two-server/section-1/inventory/"
    "recent-characterization-business-768-live786-replace-execute-singleton.json"
)
PRIOR_PACKET_PATH = (
    "docs/refactor/two-server/section-1/inventory/"
    "recent-characterization-business-768-live786-reset-execute-singleton.json"
)
PRIOR_PACKET_SHA = "4ef4ffe40f33dfe4328573bdca380b220e33306e1bee5f18d5baf54aef7170a2"
PRIOR_PACKET_BYTES = 9510
PRIOR_ACCEPTED_COMMIT = "226e3b111a393bb834c4f8a91ac473f2d766f915"
PRIOR_MANIFEST_KEY = "business_768_live786_reset_execute_singleton_path"
NEW_MANIFEST_KEY = "business_768_live786_replace_execute_singleton_path"

BEFORE_MANIFEST_SHA = "0df6d76791eb445b0d59c9c87b16def7a53102b15292e89f64db2c4429a65d1d"
BEFORE_MANIFEST_BYTES = 4035

SOURCE_PATH = "tests/test_p1_00b_characterization_internal_replace_execute_failure.py"
SOURCE_SHA = "6a3ce45d737994d88d811f38226431801e495ffdb92ab13a4817ea5bc385e8a3"
SOURCE_BYTES = 15384
SOURCE_MODE = "100644"
SOURCE_COMMIT = "973e2a28092a8718c412718135eab181c3ecd4fd"
SOURCE_PARENT = "0bfd5783775a8612f9bf97d63a12e24c85ae9279"
SOURCE_HEAD = "226e3b111a393bb834c4f8a91ac473f2d766f915"
CANONICAL_SYMBOL = "api/routers/sessions.py:internal_replace_user_sessions"
SEED_ID = "API-AUTH-POST-SESSIONS-INTERNAL-REPLACE-USER-SESSIONS-L593"
SCENARIO = "internal_replace_execute_exception_before_revocation"
CLASS_NAME = "InternalReplaceExecuteFailureCharacterizationTests"
METHOD_NAME = "test_execute_exception_propagates_before_revocation_side_effects"
SELECTOR = f"{SOURCE_PATH}::{CLASS_NAME}::{METHOD_NAME}"

LEDGER_PATH = "docs/refactor/two-server/section-1/00-stage-ledger.md"
LEDGER_PREFIX_SHA = "a25deecb4880ed4c7ceb905ae5db5048c8d3fc7b58129bc6cfdcf4d31d2c05c2"
LEDGER_PREFIX_BYTES = 109329

EXPECTED_HISTORICAL_PROVENANCE = {
    "authorship": "GEMINI_WORKER",
    "worker_run_id": "p1-00b-799b-c004-replace-execute-worker-0",
    "worker_conversation_id": "e4983790-f40c-41bd-a268-31242e43fb54",
    "proposal_sha256": "4fcd5c9b43ebcdeff03c65d1313ef589b3a7043ace16ac1f3116f958c8d86d8b",
    "native_evidence_path": (
        ".local/two-site-refactor/pipeline/p1-00b-799b-c004-replace-execute/pre-review-tests-0.json"
    ),
    "native_evidence_sha256": "4a0ee646f1d07f63bf4c31b78f9a444812140a11acef5ac98b1f2b16c7ee0370",
    "native_state": "PASSED",
    "native_selectors": [SELECTOR],
    "reviewer_run_id": "p1-00b-799b-c004-replace-execute-reviewer-0",
    "reviewer_conversation_id": "c94bd9a0-630d-4095-a4cb-c51df1de163a",
    "reviewer_result_sha256": "296347e249ae1f9c5725c2496fc364eea0d949f094b77b94ab3af0aaa59bf39b",
    "reviewer_state": "VERIFIED",
    "unsupported_specs": [],
}


class ReplaceExecuteReconciliationTests(unittest.TestCase):
    def _helper(self) -> accounting.NotificationMarkAllAccountingTests:
        return accounting.NotificationMarkAllAccountingTests(
            "test_live_set_has_exact_singleton751_delta_and_consistent_counts"
        )

    def _read_ledger_bytes(self) -> bytes:
        path = accounting.ROOT / LEDGER_PATH
        self.assertFalse(path.is_symlink())
        info = path.stat()
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertGreaterEqual(info.st_size, LEDGER_PREFIX_BYTES)
        raw = path.read_bytes()
        self.assertEqual(len(raw), info.st_size)
        return raw

    def _reconstruct_before_manifest(self, manifest: dict) -> dict:
        evidence_sources: dict[str, str] = {}
        found_cutoff = False
        for key, val in manifest["evidence_sources"].items():
            evidence_sources[key] = val
            if key == PRIOR_MANIFEST_KEY:
                found_cutoff = True
                break
        self.assertTrue(found_cutoff)
        before = copy.deepcopy(manifest)
        before["evidence_sources"] = evidence_sources
        before["counts"] = {
            "baseline_evidenced_seed_count": 135,
            "reconciled_new_seed_count": 262,
            "evidenced_seed_count": 397,
            "remaining_seed_count": 68,
        }
        raw = (json.dumps(before, indent=2) + "\n").encode("utf-8")
        self.assertEqual(len(raw), BEFORE_MANIFEST_BYTES)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), BEFORE_MANIFEST_SHA)
        return before

    def _validate(
        self,
        *,
        manifest: dict,
        packet: dict,
        prior_packet: dict,
        prior_packet_bytes: bytes,
        source_bytes: bytes,
        ledger_bytes: bytes,
    ) -> None:
        helper = self._helper()
        orig_receipt = helper._json(accounting.RECEIPT)

        # 1. Physical source bytes, SHA, size, non-executable mode, and AST checks
        self.assertEqual(len(source_bytes), SOURCE_BYTES)
        self.assertEqual(hashlib.sha256(source_bytes).hexdigest(), SOURCE_SHA)
        source_stat = (accounting.ROOT / SOURCE_PATH).stat()
        self.assertEqual(source_stat.st_mode & 0o111, 0)

        tree = ast.parse(source_bytes.decode("utf-8"))
        classes = [
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == CLASS_NAME
        ]
        self.assertEqual(len(classes), 1)
        async_methods = [
            node for node in classes[0].body
            if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("test_")
        ]
        self.assertEqual([node.name for node in async_methods], [METHOD_NAME])
        handler_calls = [
            node for node in ast.walk(async_methods[0])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "internal_replace_user_sessions"
        ]
        self.assertEqual(len(handler_calls), 1)

        # 2. Prior reset singleton packet immutability and provenance
        self.assertEqual(len(prior_packet_bytes), PRIOR_PACKET_BYTES)
        self.assertEqual(hashlib.sha256(prior_packet_bytes).hexdigest(), PRIOR_PACKET_SHA)
        self.assertEqual(
            prior_packet["accounting"]["reconciled_units_in_this_packet_on_exact_acceptance"],
            1,
        )
        self.assertEqual(
            prior_packet["accounting"]["reconciled_scenarios_in_this_packet_on_exact_acceptance"],
            1,
        )
        self.assertEqual(
            prior_packet["accounting"]["remaining_unreconciled_units_on_exact_acceptance"],
            4,
        )
        self.assertEqual(
            prior_packet["accounting"]["remaining_unreconciled_scenarios_on_exact_acceptance"],
            6,
        )

        # 3. Packet header and single-record metadata
        self.assertEqual(packet["schema_version"], 1)
        self.assertEqual(packet["stage"], "P1-00b")
        self.assertEqual(packet["assignment_id"], "WA-P1-00B-BUSINESS-LIVE-20261023-799B")
        self.assertEqual(packet["parent_assignment_id"], "WA-P1-00B-FREEZE-20260904-001")
        self.assertEqual(
            packet["task_id"],
            "p1-00b-799b-c008-replace-execute-singleton-reconciliation",
        )
        self.assertEqual(
            packet["scenario_key"],
            "P1-00B-RECONCILE-V1.internal-replace-execute-singleton-799",
        )
        self.assertEqual(packet["source_head"], SOURCE_HEAD)

        records = packet["records"]
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["path"], SOURCE_PATH)
        self.assertEqual(rec["sha256"], SOURCE_SHA)
        self.assertEqual(rec["utf8_bytes"], SOURCE_BYTES)
        self.assertEqual(rec["mode"], SOURCE_MODE)
        self.assertEqual(rec["last_commit"], SOURCE_COMMIT)
        self.assertEqual(rec["parent"], SOURCE_PARENT)
        self.assertEqual(rec["canonical_symbol"], CANONICAL_SYMBOL)
        self.assertEqual(rec["seed_id"], SEED_ID)
        self.assertEqual(rec["seed_ids"], [SEED_ID])
        self.assertEqual(rec["scenario"], SCENARIO)
        self.assertEqual(rec["test_selectors"], [SELECTOR])
        self.assertEqual(rec["historical_provenance"], EXPECTED_HISTORICAL_PROVENANCE)

        contract = rec["characterized_contract"]
        self.assertEqual(contract["entrypoint"], CANONICAL_SYMBOL)
        self.assertEqual(
            contract["ordered_events"],
            [
                "body_call",
                "body_await",
                "header:X-Timestamp",
                "header:X-Signature",
                "header:X-API-Key",
                "verifier",
                "orm_execute",
            ],
        )
        self.assertEqual(
            contract["orm_statement"],
            {
                "is_select": True,
                "is_column_load": False,
                "is_relationship_load": False,
                "entity": "User",
                "table": "users",
                "where_clause": "users.id = :id_1",
                "compiled_params": {"id_1": 4079},
            },
        )
        deltas = contract["zero_side_effect_and_cleanup_deltas"]
        self.assertEqual(deltas["driver_sql_statements"], 0)
        self.assertEqual(deltas["force_clear_sessions_calls"], 0)
        self.assertEqual(deltas["force_clear_sessions_awaits"], 0)
        self.assertEqual(deltas["session_state_new_dirty_deleted"], [0, 0, 0])
        self.assertEqual(deltas["network_denied_connects_delta"], 0)
        self.assertEqual(deltas["live_redis_constructs_delta"], 0)

        # 4. Reconstruct BEFORE manifest and verify historical +1 delta & live counts
        before_manifest = self._reconstruct_before_manifest(manifest)
        for key, val in before_manifest["evidence_sources"].items():
            self.assertEqual(manifest["evidence_sources"].get(key), val)
        self.assertEqual(manifest["evidence_sources"].get(NEW_MANIFEST_KEY), PACKET_PATH)
        self.assertNotIn(NEW_MANIFEST_KEY, before_manifest["evidence_sources"])
        self.assertNotIn(PACKET_PATH, before_manifest["evidence_sources"].values())

        surface, baseline, before_evidenced = helper._derive(before_manifest, orig_receipt)
        self.assertEqual(
            (len(surface), len(baseline), len(before_evidenced)),
            (465, 135, 397),
        )
        self.assertIn(SEED_ID, surface)
        self.assertNotIn(SEED_ID, baseline)
        self.assertNotIn(SEED_ID, before_evidenced)

        historical_after = copy.deepcopy(before_manifest)
        historical_after["evidence_sources"][NEW_MANIFEST_KEY] = PACKET_PATH
        historical_after["counts"] = {
            "baseline_evidenced_seed_count": 135,
            "reconciled_new_seed_count": 263,
            "evidenced_seed_count": 398,
            "remaining_seed_count": 67,
        }
        _, _, after_replace = helper._derive(
            historical_after,
            orig_receipt,
            extra_documents={PACKET_PATH: packet},
        )
        self.assertEqual(after_replace - before_evidenced, {SEED_ID})
        self.assertEqual(before_evidenced - after_replace, set())
        self.assertEqual(len(after_replace), 398)
        self.assertEqual(len(surface - after_replace), 67)

        live_surface, live_baseline, live_evidenced = helper._derive(
            manifest,
            orig_receipt,
            extra_documents={PACKET_PATH: packet},
        )
        self.assertEqual(live_surface, surface)
        self.assertEqual(live_baseline, baseline)
        self.assertTrue(after_replace <= live_evidenced)
        expected_live_counts = {
            "baseline_evidenced_seed_count": len(live_baseline),
            "reconciled_new_seed_count": len(live_evidenced - live_baseline),
            "evidenced_seed_count": len(live_evidenced),
            "remaining_seed_count": len(live_surface - live_evidenced),
        }
        self.assertEqual(manifest["counts"], expected_live_counts)
        self.assertEqual(manifest["coverage_status"], "PARTIAL")
        self.assertEqual(manifest["canonical_blockers"], accounting.BLOCKERS)

        # 5. Promotion assessment
        promo = packet["promotion_assessment"]
        self.assertEqual(promo["promoted_seed_ids"], [SEED_ID])
        self.assertEqual(promo["delta_distinct_seed_count"], 1)
        self.assertEqual(promo["removed_seed_ids"], [])
        self.assertEqual(promo["duplicate_new_seed_ids"], [])
        self.assertEqual(promo["before_manifest_sha256"], BEFORE_MANIFEST_SHA)
        self.assertEqual(promo["before_manifest_utf8_bytes"], BEFORE_MANIFEST_BYTES)
        self.assertEqual(
            promo["canonical_before"],
            {
                "expected": 465,
                "baseline": 135,
                "reconciled_new": 262,
                "evidenced": 397,
                "remaining": 68,
            },
        )
        self.assertEqual(
            promo["canonical_after_on_exact_acceptance"],
            {
                "expected": 465,
                "baseline": 135,
                "reconciled_new": 263,
                "evidenced": 398,
                "remaining": 67,
            },
        )
        self.assertEqual(promo["added_behavior_units"], 0)
        self.assertEqual(promo["accepted_full_seeds"], 0)
        self.assertEqual(promo["raw_registration_delta"], 1)
        self.assertEqual(promo["full_seed_acceptance"], "NOT_ASSESSED")

        # 6. Accounting, cumulative/remaining derivation from prior reset packet, and ledger prefix
        acct = packet["accounting"]
        self.assertEqual(acct["pending_five"], 5)
        self.assertEqual(acct["units_since_reconciliation"], 5)
        self.assertEqual(acct["total_behavior_units"], 5)
        self.assertEqual(acct["total_handler_scenarios"], 7)
        self.assertEqual(
            acct["prior_reconciled_singleton_provenance"],
            {
                "path": PRIOR_PACKET_PATH,
                "sha256": PRIOR_PACKET_SHA,
                "utf8_bytes": PRIOR_PACKET_BYTES,
                "accepted_commit": PRIOR_ACCEPTED_COMMIT,
                "unit_count": 1,
                "scenario_count": 1,
            },
        )
        self.assertEqual(acct["reconciled_units_in_this_packet_on_exact_acceptance"], 1)
        self.assertEqual(acct["reconciled_scenarios_in_this_packet_on_exact_acceptance"], 1)
        self.assertEqual(acct["cumulative_reconciled_units_on_exact_acceptance"], 2)
        self.assertEqual(acct["cumulative_reconciled_scenarios_on_exact_acceptance"], 2)
        self.assertEqual(acct["remaining_unreconciled_units_on_exact_acceptance"], 3)
        self.assertEqual(acct["remaining_unreconciled_scenarios_on_exact_acceptance"], 5)
        self.assertFalse(acct["group_reconciliation_complete"])
        self.assertFalse(acct["canonical_manifest_and_ledger_integration_complete"])
        self.assertEqual(acct["ledger_integration_status"], "PENDING")
        self.assertEqual(acct["added_behavior_units"], 0)
        self.assertEqual(acct["accepted_full_seeds"], 0)
        self.assertEqual(acct["raw_registration_delta"], 1)

        prior_remaining = prior_packet["accounting"][
            "unreconciled_units_after_singleton_on_exact_acceptance"
        ]
        replaced_entries = [row for row in prior_remaining if row["path"] == SOURCE_PATH]
        self.assertEqual(
            replaced_entries,
            [
                {
                    "path": SOURCE_PATH,
                    "sha256": SOURCE_SHA,
                    "source_commit": SOURCE_COMMIT,
                    "handler_scenarios": 1,
                }
            ],
        )
        expected_remaining = [row for row in prior_remaining if row["path"] != SOURCE_PATH]
        self.assertEqual(len(expected_remaining), 3)
        self.assertEqual(sum(row["handler_scenarios"] for row in expected_remaining), 5)
        self.assertEqual(
            acct["unreconciled_units_after_singleton_on_exact_acceptance"],
            expected_remaining,
        )

        self.assertEqual(
            acct["preserved_deferred_and_failed_reservations"],
            {
                "deferred_a2_task_id": "p1-00b-799b-c006-session-prequery-reconciliation-a2",
                "deferred_a2_status": "UNACCEPTED_RESERVED_NEVER_REPLAY_OR_RELEASE",
            },
        )
        self.assertEqual(
            acct["previous_completed_group_provenance"],
            {
                "assignment_id": "WA-P1-00B-FIVE-SOURCE-RECONCILIATION-20261023-798D",
                "accepted_closeout_commit": "e5c08c61895961bce399da9d8c69f561d7538ea1",
                "status": "COMPLETED_PROVENANCE",
                "reconciled_behavior_units": 5,
                "reconciled_handler_scenarios": 7,
                "remaining_in_group": 0,
            },
        )
        self.assertEqual(
            acct["ledger_prefix_binding"],
            {
                "path": LEDGER_PATH,
                "unchanged_ledger_sha256": LEDGER_PREFIX_SHA,
                "unchanged_ledger_utf8_bytes": LEDGER_PREFIX_BYTES,
            },
        )
        self.assertGreaterEqual(len(ledger_bytes), LEDGER_PREFIX_BYTES)
        self.assertEqual(
            hashlib.sha256(ledger_bytes[:LEDGER_PREFIX_BYTES]).hexdigest(),
            LEDGER_PREFIX_SHA,
        )

        self.assertEqual(
            packet["strict_canonical_progress"],
            {"proven": 288, "partial": 177, "total": 465},
        )
        self.assertEqual(packet["origin_matrix_unresolved_cells"], 24)
        self.assertEqual(packet["canonical_blockers"], accounting.BLOCKERS)
        self.assertEqual(
            packet["seed_acceptance_status"],
            "FULL_SEED_ACCEPTANCE_NOT_ASSESSED",
        )
        self.assertFalse(packet["closures_authorized"])
        self.assertEqual(
            packet["stages"],
            {"P1-00": "COMPLETE", "P1-00b": "IN_PROGRESS"},
        )

    def test_exact_replace_source_registration_and_accounting(self) -> None:
        helper = self._helper()
        self._validate(
            manifest=helper._json(accounting.MANIFEST),
            packet=helper._json(PACKET_PATH),
            prior_packet=helper._json(PRIOR_PACKET_PATH),
            prior_packet_bytes=helper._read(PRIOR_PACKET_PATH),
            source_bytes=helper._read(SOURCE_PATH),
            ledger_bytes=self._read_ledger_bytes(),
        )

    def test_rejects_concrete_source_registration_and_pending_drift(self) -> None:
        helper = self._helper()
        manifest = helper._json(accounting.MANIFEST)
        packet = helper._json(PACKET_PATH)
        prior_packet = helper._json(PRIOR_PACKET_PATH)
        prior_packet_bytes = helper._read(PRIOR_PACKET_PATH)
        source_bytes = helper._read(SOURCE_PATH)
        ledger_bytes = self._read_ledger_bytes()

        self._validate(
            manifest=manifest,
            packet=packet,
            prior_packet=prior_packet,
            prior_packet_bytes=prior_packet_bytes,
            source_bytes=source_bytes,
            ledger_bytes=ledger_bytes,
        )

        # 1. Corrupted source bytes
        with self.assertRaises(AssertionError):
            self._validate(
                manifest=manifest,
                packet=packet,
                prior_packet=prior_packet,
                prior_packet_bytes=prior_packet_bytes,
                source_bytes=source_bytes[:-1] + b"#",
                ledger_bytes=ledger_bytes,
            )

        # 2. Wrong record selector
        bad_selector_packet = copy.deepcopy(packet)
        bad_selector_packet["records"][0]["test_selectors"] = [
            f"{SOURCE_PATH}::{CLASS_NAME}::test_wrong_selector"
        ]
        with self.assertRaises(AssertionError):
            self._validate(
                manifest=manifest,
                packet=bad_selector_packet,
                prior_packet=prior_packet,
                prior_packet_bytes=prior_packet_bytes,
                source_bytes=source_bytes,
                ledger_bytes=ledger_bytes,
            )

        # 3. Missing new manifest mapping
        missing_mapping_manifest = copy.deepcopy(manifest)
        del missing_mapping_manifest["evidence_sources"][NEW_MANIFEST_KEY]
        with self.assertRaises(AssertionError):
            self._validate(
                manifest=missing_mapping_manifest,
                packet=packet,
                prior_packet=prior_packet,
                prior_packet_bytes=prior_packet_bytes,
                source_bytes=source_bytes,
                ledger_bytes=ledger_bytes,
            )

        # 4. Incorrect raw counts
        bad_counts_manifest = copy.deepcopy(manifest)
        bad_counts_manifest["counts"]["evidenced_seed_count"] += 1
        with self.assertRaises(AssertionError):
            self._validate(
                manifest=bad_counts_manifest,
                packet=packet,
                prior_packet=prior_packet,
                prior_packet_bytes=prior_packet_bytes,
                source_bytes=source_bytes,
                ledger_bytes=ledger_bytes,
            )

        # 5. Premature pending decrement or group completion
        premature_decrement_packet = copy.deepcopy(packet)
        premature_decrement_packet["accounting"]["pending_five"] = 4
        with self.assertRaises(AssertionError):
            self._validate(
                manifest=manifest,
                packet=premature_decrement_packet,
                prior_packet=prior_packet,
                prior_packet_bytes=prior_packet_bytes,
                source_bytes=source_bytes,
                ledger_bytes=ledger_bytes,
            )

        premature_complete_packet = copy.deepcopy(packet)
        premature_complete_packet["accounting"]["group_reconciliation_complete"] = True
        with self.assertRaises(AssertionError):
            self._validate(
                manifest=manifest,
                packet=premature_complete_packet,
                prior_packet=prior_packet,
                prior_packet_bytes=prior_packet_bytes,
                source_bytes=source_bytes,
                ledger_bytes=ledger_bytes,
            )

        # 6. Healthy rerun afterward
        self._validate(
            manifest=manifest,
            packet=packet,
            prior_packet=prior_packet,
            prior_packet_bytes=prior_packet_bytes,
            source_bytes=source_bytes,
            ledger_bytes=ledger_bytes,
        )


if __name__ == "__main__":
    unittest.main()
