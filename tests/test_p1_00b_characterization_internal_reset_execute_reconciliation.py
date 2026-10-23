"""Singleton partial reconciliation witness for internal_reset_user_sessions ORM execute failure.

Stdlib-only physical-data witness; no application, database, network, subprocess,
or .local execution.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import stat
import unittest

import tests.test_p1_00b_characterization_notification_mark_all_accounting as accounting

MANIFEST_PATH = accounting.MANIFEST
SINGLETON_KEY = "business_768_live786_reset_execute_singleton_path"
SINGLETON_PATH = (
    accounting.INVENTORY
    + "recent-characterization-business-768-live786-reset-execute-singleton.json"
)
LAST_BEFORE_KEY = "business_768_live786_five_source_closeout_path"
BEFORE_MANIFEST_SHA256 = (
    "2f45800099e0aa2017b6146982cc210d0fa699b1a844616d425919c3be352e9b"
)
BEFORE_MANIFEST_UTF8_BYTES = 3856

LEDGER_PATH = "docs/refactor/two-server/section-1/00-stage-ledger.md"
LEDGER_PREFIX_SHA256 = (
    "a25deecb4880ed4c7ceb905ae5db5048c8d3fc7b58129bc6cfdcf4d31d2c05c2"
)
LEDGER_PREFIX_UTF8_BYTES = 109329

SOURCE_PATH = "tests/test_p1_00b_characterization_internal_reset_execute_failure.py"
SOURCE_SHA256 = "ef84b9aeed432fedd70dfc0590c3d9b7ec5324fd204e0fe676165e1a0906edec"
SOURCE_UTF8_BYTES = 15348
SOURCE_MODE = "100644"
SOURCE_COMMIT = "0bfd5783775a8612f9bf97d63a12e24c85ae9279"
SOURCE_PARENT = "9a3bfcebe29399253b7ee60c605e72cf73e19d82"
SOURCE_CLASS = "InternalResetExecuteFailureCharacterizationTests"
SOURCE_METHOD = "test_execute_exception_propagates_before_reset_side_effects"
SOURCE_SELECTOR = f"{SOURCE_PATH}::{SOURCE_CLASS}::{SOURCE_METHOD}"
CANONICAL_SYMBOL = "api/routers/sessions.py:internal_reset_user_sessions"
SEED_ID = "API-AUTH-POST-SESSIONS-INTERNAL-RESET-USER-SESSIONS-L562"
SCENARIO_NAME = "internal_reset_execute_exception_before_reset"

EXPECTED_PROVENANCE = {
    "authorship": "GEMINI_WORKER",
    "worker_run_id": "p1-00b-799b-c003-reset-execute-worker-1",
    "worker_conversation_id": "38f6ee4b-272a-4136-a328-faf8a697dcdb",
    "proposal_sha256": "3c7303c92c50f27d035f4797a9c6a76f78a769591738e1f920b2866c8ed8b54a",
    "native_evidence_path": ".local/two-site-refactor/pipeline/p1-00b-799b-c003-reset-execute/pre-review-tests-1.json",
    "native_evidence_sha256": "ed3112dd53eb98c9b40128cf76339b9080d2f567f15fee73e891549f1d2777a8",
    "native_state": "PASSED",
    "native_selectors": [SOURCE_SELECTOR],
    "reviewer_run_id": "p1-00b-799b-c003-reset-execute-reviewer-1",
    "reviewer_conversation_id": "b7c89947-f452-416c-a03b-e4ee3ec25b43",
    "reviewer_result_sha256": "345218cca4834c5cb24a966ea6adb3584437d9bfb77eb6cc6f28b72c5b65d73a",
    "reviewer_state": "VERIFIED",
    "unsupported_specs": [],
}

EXPECTED_UNRECONCILED_UNITS = [
    {
        "path": "tests/test_p1_00b_characterization_internal_session_body_failures_batch.py",
        "sha256": "b5f2772f283424f94c0f8436b8575f58f7ec65a2e57500ea4422bc9c9785c4e9",
        "source_commit": "777ed75419ed7083d875c262da2beb8103017d03",
        "handler_scenarios": 2,
    },
    {
        "path": "tests/test_p1_00b_characterization_internal_session_verifier_failures_batch.py",
        "sha256": "6ed021334ea4384e5f1b98897a1aeb6c45f0cfed29105bb90e9a227ff8d803c8",
        "source_commit": "9a3bfcebe29399253b7ee60c605e72cf73e19d82",
        "handler_scenarios": 2,
    },
    {
        "path": "tests/test_p1_00b_characterization_internal_replace_execute_failure.py",
        "sha256": "6a3ce45d737994d88d811f38226431801e495ffdb92ab13a4817ea5bc385e8a3",
        "source_commit": "973e2a28092a8718c412718135eab181c3ecd4fd",
        "handler_scenarios": 1,
    },
    {
        "path": "tests/test_p1_00b_characterization_internal_authority_execute_failure.py",
        "sha256": "d7a60d5f329d0889a2fcba3d52b28cb0b41c72c51c913e278f3bfdf3637903d1",
        "source_commit": "f4dd10018ef832881894e276bfa214e070ce7422",
        "handler_scenarios": 1,
    },
]


class ResetExecuteReconciliationTests(unittest.TestCase):
    def _read_ledger_bytes(self) -> bytes:
        path = accounting.ROOT / LEDGER_PATH
        self.assertFalse(path.is_symlink())
        info = path.stat()
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertLessEqual(info.st_size, 2_000_000)
        body = path.read_bytes()
        self.assertEqual(len(body), info.st_size)
        return body

    def _reconstruct_before_manifest(self, manifest: dict) -> dict:
        evidence_sources: dict[str, str] = {}
        found_cutoff = False
        for key, value in manifest["evidence_sources"].items():
            evidence_sources[key] = value
            if key == LAST_BEFORE_KEY:
                found_cutoff = True
                break
        self.assertTrue(found_cutoff)
        before = {
            "schema_version": manifest["schema_version"],
            "stage": manifest["stage"],
            "assignment_id": manifest["assignment_id"],
            "inventory": copy.deepcopy(manifest["inventory"]),
            "evidence_sources": evidence_sources,
            "counts": {
                "baseline_evidenced_seed_count": 135,
                "reconciled_new_seed_count": 261,
                "evidenced_seed_count": 396,
                "remaining_seed_count": 69,
            },
            "coverage_status": manifest["coverage_status"],
            "canonical_blockers": copy.deepcopy(manifest["canonical_blockers"]),
        }
        raw_before = (json.dumps(before, indent=2) + "\n").encode("utf-8")
        self.assertEqual(len(raw_before), BEFORE_MANIFEST_UTF8_BYTES)
        self.assertEqual(
            hashlib.sha256(raw_before).hexdigest(),
            BEFORE_MANIFEST_SHA256,
        )
        return before

    def _validate(
        self,
        helper: accounting.NotificationMarkAllAccountingTests,
        manifest: dict,
        singleton_doc: dict,
        source_bytes: bytes,
        ledger_bytes: bytes,
    ) -> None:
        # 1. Source physical bytes, hash, mode, AST selector, and handler call.
        self.assertEqual(len(source_bytes), SOURCE_UTF8_BYTES)
        self.assertEqual(
            hashlib.sha256(source_bytes).hexdigest(),
            SOURCE_SHA256,
        )
        source_stat = (accounting.ROOT / SOURCE_PATH).stat()
        self.assertEqual(source_stat.st_mode & 0o111, 0)

        tree = ast.parse(source_bytes.decode("utf-8"))
        classes = [
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == SOURCE_CLASS
        ]
        self.assertEqual(len(classes), 1)
        async_methods = {
            node.name: node
            for node in classes[0].body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name.startswith("test_")
        }
        self.assertEqual(set(async_methods.keys()), {SOURCE_METHOD})
        handler_calls = [
            node
            for node in ast.walk(async_methods[SOURCE_METHOD])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "internal_reset_user_sessions"
        ]
        self.assertEqual(len(handler_calls), 1)

        # 2. Singleton document structure, provenance, contract, exclusions, and accounting.
        self.assertEqual(singleton_doc["schema_version"], 1)
        self.assertEqual(singleton_doc["stage"], "P1-00b")
        self.assertEqual(
            singleton_doc["assignment_id"],
            "WA-P1-00B-BUSINESS-LIVE-20261023-799B",
        )
        self.assertEqual(
            singleton_doc["parent_assignment_id"],
            "WA-P1-00B-FREEZE-20260904-001",
        )
        self.assertEqual(
            singleton_doc["source_head"],
            "f4dd10018ef832881894e276bfa214e070ce7422",
        )

        records = singleton_doc["records"]
        self.assertIs(type(records), list)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["path"], SOURCE_PATH)
        self.assertEqual(rec["sha256"], SOURCE_SHA256)
        self.assertEqual(rec["utf8_bytes"], SOURCE_UTF8_BYTES)
        self.assertEqual(rec["mode"], SOURCE_MODE)
        self.assertEqual(rec["last_commit"], SOURCE_COMMIT)
        self.assertEqual(rec["parent"], SOURCE_PARENT)
        self.assertEqual(rec["canonical_symbol"], CANONICAL_SYMBOL)
        self.assertEqual(rec["seed_id"], SEED_ID)
        self.assertEqual(rec["seed_ids"], [SEED_ID])
        self.assertEqual(rec["scenario"], SCENARIO_NAME)
        self.assertEqual(rec["test_selectors"], [SOURCE_SELECTOR])
        self.assertEqual(rec["historical_provenance"], EXPECTED_PROVENANCE)

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
                "compiled_params": {"id_1": 3079},
            },
        )
        deltas = contract["zero_side_effect_and_cleanup_deltas"]
        self.assertEqual(deltas["driver_sql_statements"], 0)
        self.assertEqual(deltas["reset_user_session_state_calls"], 0)
        self.assertEqual(deltas["reset_user_session_state_awaits"], 0)
        self.assertEqual(deltas["session_state_new_dirty_deleted"], [0, 0, 0])
        self.assertEqual(deltas["network_denied_connects_delta"], 0)
        self.assertEqual(deltas["live_redis_constructs_delta"], 0)
        self.assertEqual(len(rec["exclusions"]), 5)

        surface_api = helper._json(accounting.INVENTORY + "surface-api.json")
        matching_surface = [
            row for row in surface_api["entries"] if row["behavior_id"] == SEED_ID
        ]
        self.assertEqual(len(matching_surface), 1)

        acct = singleton_doc["accounting"]
        self.assertEqual(acct["pending_five"], 5)
        self.assertEqual(acct["units_since_reconciliation"], 5)
        self.assertEqual(acct["total_behavior_units"], 5)
        self.assertEqual(acct["total_handler_scenarios"], 7)
        self.assertEqual(
            acct["reconciled_units_in_this_packet_on_exact_acceptance"], 1
        )
        self.assertEqual(
            acct["reconciled_scenarios_in_this_packet_on_exact_acceptance"], 1
        )
        self.assertEqual(
            acct["remaining_unreconciled_units_on_exact_acceptance"], 4
        )
        self.assertEqual(
            acct["remaining_unreconciled_scenarios_on_exact_acceptance"], 6
        )
        self.assertFalse(acct["group_reconciliation_complete"])
        self.assertFalse(acct["canonical_manifest_and_ledger_integration_complete"])
        self.assertEqual(acct["ledger_integration_status"], "PENDING")
        self.assertEqual(acct["added_behavior_units"], 0)
        self.assertEqual(acct["accepted_full_seeds"], 0)
        self.assertEqual(acct["raw_registration_delta"], 1)
        self.assertEqual(
            acct["unreconciled_units_after_singleton_on_exact_acceptance"],
            EXPECTED_UNRECONCILED_UNITS,
        )
        self.assertEqual(
            sum(
                row["handler_scenarios"]
                for row in acct[
                    "unreconciled_units_after_singleton_on_exact_acceptance"
                ]
            ),
            6,
        )
        self.assertEqual(
            acct["preserved_deferred_and_failed_reservations"][
                "deferred_a2_task_id"
            ],
            "p1-00b-799b-c006-session-prequery-reconciliation-a2",
        )
        prev = acct["previous_completed_group_provenance"]
        self.assertEqual(
            prev["assignment_id"],
            "WA-P1-00B-FIVE-SOURCE-RECONCILIATION-20261023-798D",
        )
        self.assertEqual(
            prev["accepted_closeout_commit"],
            "e5c08c61895961bce399da9d8c69f561d7538ea1",
        )
        self.assertEqual(
            (prev["reconciled_behavior_units"], prev["reconciled_handler_scenarios"]),
            (5, 7),
        )
        self.assertEqual(prev["remaining_in_group"], 0)

        self.assertEqual(
            acct["ledger_prefix_binding"],
            {
                "path": LEDGER_PATH,
                "unchanged_ledger_sha256": LEDGER_PREFIX_SHA256,
                "unchanged_ledger_utf8_bytes": LEDGER_PREFIX_UTF8_BYTES,
            },
        )
        self.assertGreaterEqual(len(ledger_bytes), LEDGER_PREFIX_UTF8_BYTES)
        self.assertEqual(
            hashlib.sha256(ledger_bytes[:LEDGER_PREFIX_UTF8_BYTES]).hexdigest(),
            LEDGER_PREFIX_SHA256,
        )

        self.assertEqual(
            singleton_doc["strict_canonical_progress"],
            {"proven": 288, "partial": 177, "total": 465},
        )
        self.assertEqual(singleton_doc["origin_matrix_unresolved_cells"], 24)
        self.assertEqual(singleton_doc["canonical_blockers"], accounting.BLOCKERS)
        self.assertEqual(
            singleton_doc["seed_acceptance_status"],
            "FULL_SEED_ACCEPTANCE_NOT_ASSESSED",
        )
        self.assertFalse(singleton_doc["closures_authorized"])
        self.assertEqual(
            singleton_doc["stages"],
            {"P1-00": "COMPLETE", "P1-00b": "IN_PROGRESS"},
        )

        # 3. Manifest BEFORE reconstruction, historical singleton +1, and live counts.
        self.assertIn(SINGLETON_KEY, manifest["evidence_sources"])
        self.assertEqual(
            manifest["evidence_sources"][SINGLETON_KEY],
            SINGLETON_PATH,
        )
        before = self._reconstruct_before_manifest(manifest)
        self.assertNotIn(SINGLETON_KEY, before["evidence_sources"])
        self.assertNotIn(SINGLETON_PATH, before["evidence_sources"].values())
        for key, rel in before["evidence_sources"].items():
            self.assertEqual(manifest["evidence_sources"][key], rel)
        self.assertEqual(manifest["coverage_status"], "PARTIAL")
        self.assertEqual(manifest["canonical_blockers"], accounting.BLOCKERS)

        original_notif_receipt = helper._json(accounting.RECEIPT)
        extra_docs = {SINGLETON_PATH: singleton_doc}
        surface, baseline, prior = helper._derive(
            before, original_notif_receipt, extra_docs
        )
        self.assertEqual((len(surface), len(baseline), len(prior)), (465, 135, 396))
        self.assertIn(SEED_ID, surface)
        self.assertNotIn(SEED_ID, baseline)
        self.assertNotIn(SEED_ID, prior)

        historical_singleton = copy.deepcopy(before)
        historical_singleton["evidence_sources"][SINGLETON_KEY] = SINGLETON_PATH
        historical_singleton["counts"] = {
            "baseline_evidenced_seed_count": 135,
            "reconciled_new_seed_count": 262,
            "evidenced_seed_count": 397,
            "remaining_seed_count": 68,
        }
        _, _, after_singleton = helper._derive(
            historical_singleton, original_notif_receipt, extra_docs
        )
        self.assertEqual(len(after_singleton), 397)
        self.assertEqual(after_singleton - prior, {SEED_ID})
        self.assertEqual(prior - after_singleton, set())
        self.assertEqual(len(surface - after_singleton), 68)

        live_surface, live_baseline, live_evidenced = helper._derive(
            manifest, original_notif_receipt, extra_docs
        )
        self.assertEqual(live_surface, surface)
        self.assertEqual(live_baseline, baseline)
        self.assertTrue(after_singleton <= live_evidenced)
        expected_live_counts = {
            "baseline_evidenced_seed_count": len(live_baseline),
            "reconciled_new_seed_count": len(live_evidenced - live_baseline),
            "evidenced_seed_count": len(live_evidenced),
            "remaining_seed_count": len(live_surface - live_evidenced),
        }
        self.assertEqual(manifest["counts"], expected_live_counts)

        promo = singleton_doc["promotion_assessment"]
        self.assertEqual(promo["promoted_seed_ids"], [SEED_ID])
        self.assertEqual(promo["delta_distinct_seed_count"], 1)
        self.assertEqual(promo["removed_seed_ids"], [])
        self.assertEqual(promo["duplicate_new_seed_ids"], [])
        self.assertEqual(promo["before_manifest_sha256"], BEFORE_MANIFEST_SHA256)
        self.assertEqual(promo["before_manifest_utf8_bytes"], BEFORE_MANIFEST_UTF8_BYTES)
        self.assertEqual(
            promo["canonical_before"],
            {
                "expected": 465,
                "baseline": 135,
                "reconciled_new": 261,
                "evidenced": 396,
                "remaining": 69,
            },
        )
        self.assertEqual(
            promo["canonical_after_on_exact_acceptance"],
            {
                "expected": 465,
                "baseline": 135,
                "reconciled_new": 262,
                "evidenced": 397,
                "remaining": 68,
            },
        )
        self.assertEqual(promo["added_behavior_units"], 0)
        self.assertEqual(promo["accepted_full_seeds"], 0)
        self.assertEqual(promo["raw_registration_delta"], 1)
        self.assertEqual(promo["full_seed_acceptance"], "NOT_ASSESSED")

    def test_exact_reset_source_registration_and_accounting(self) -> None:
        helper = accounting.NotificationMarkAllAccountingTests()
        manifest = helper._json(MANIFEST_PATH)
        singleton_doc = helper._json(SINGLETON_PATH)
        source_bytes = helper._read(SOURCE_PATH)
        ledger_bytes = self._read_ledger_bytes()
        self._validate(helper, manifest, singleton_doc, source_bytes, ledger_bytes)

    def test_rejects_concrete_source_registration_and_pending_drift(self) -> None:
        helper = accounting.NotificationMarkAllAccountingTests()
        manifest = helper._json(MANIFEST_PATH)
        singleton_doc = helper._json(SINGLETON_PATH)
        source_bytes = helper._read(SOURCE_PATH)
        ledger_bytes = self._read_ledger_bytes()
        self._validate(helper, manifest, singleton_doc, source_bytes, ledger_bytes)

        # 1. Corrupted source bytes
        corrupted_source = source_bytes[:-1] + (
            b"X" if source_bytes[-1:] != b"X" else b"Y"
        )
        with self.assertRaises(AssertionError):
            self._validate(
                helper, manifest, singleton_doc, corrupted_source, ledger_bytes
            )

        # 2. Wrong selector
        wrong_selector_doc = copy.deepcopy(singleton_doc)
        wrong_selector_doc["records"][0]["test_selectors"] = [
            f"{SOURCE_PATH}::{SOURCE_CLASS}::test_nonexistent_selector"
        ]
        with self.assertRaises(AssertionError):
            self._validate(
                helper, manifest, wrong_selector_doc, source_bytes, ledger_bytes
            )

        # 3. Missing new manifest mapping
        missing_mapping_manifest = copy.deepcopy(manifest)
        del missing_mapping_manifest["evidence_sources"][SINGLETON_KEY]
        with self.assertRaises(AssertionError):
            self._validate(
                helper,
                missing_mapping_manifest,
                singleton_doc,
                source_bytes,
                ledger_bytes,
            )

        # 4. Wrong counts
        wrong_counts_manifest = copy.deepcopy(manifest)
        wrong_counts_manifest["counts"]["evidenced_seed_count"] += 1
        with self.assertRaises(AssertionError):
            self._validate(
                helper,
                wrong_counts_manifest,
                singleton_doc,
                source_bytes,
                ledger_bytes,
            )

        # 5. Premature pending reset or group completion
        premature_pending_doc = copy.deepcopy(singleton_doc)
        premature_pending_doc["accounting"]["pending_five"] = 4
        with self.assertRaises(AssertionError):
            self._validate(
                helper, manifest, premature_pending_doc, source_bytes, ledger_bytes
            )

        premature_complete_doc = copy.deepcopy(singleton_doc)
        premature_complete_doc["accounting"]["group_reconciliation_complete"] = True
        with self.assertRaises(AssertionError):
            self._validate(
                helper, manifest, premature_complete_doc, source_bytes, ledger_bytes
            )

        # Rerun healthy validation afterward.
        self._validate(helper, manifest, singleton_doc, source_bytes, ledger_bytes)


if __name__ == "__main__":
    unittest.main()
