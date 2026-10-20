"""751R live-set accounting witness; stdlib only, no application/SDK execution.

The two750 scenarios remain ONE accepted Source unit and ONE possible partial
evidenced seed. This witness adds zero product behavior. Historical acceptance
comes from actual Root custody, not from passing these metadata assertions.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path
import stat
import unittest

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = "docs/refactor/two-server/section-1/inventory/"
MANIFEST = INVENTORY + "seed-handler-coverage.json"
RECEIPT = INVENTORY + "recent-characterization-notification-mark-all-accounting-751.json"
SOURCE = "tests/test_p1_00b_characterization_notification_mark_all_prequery_failures.py"
KEY = "notification_mark_all_750_path"
SEED = "API-NOTIFY-POST-NOTIFICATIONS-MARK-ALL-NOTIFICATIONS-READ-L318"
CLASS = "NotificationMarkAllPrequeryFailureCharacterizationTests"
METHODS = (
    "test_select_builder_exception_precedes_database_and_unread_sync",
    "test_initial_execute_exception_precedes_rows_commit_and_unread_sync",
)
SCENARIOS = (
    "mark-all-select-builder-exception-before-db",
    "mark-all-initial-execute-exception-before-row-mutation",
)
SOURCE_SHA = "0656e24fd03450c0bec7990384aff66c3cf84e7024d275bad127a4a3a3fe9e31"
SOURCE_COMMIT = "88303d3cb7a8cb629b693496c4f8bb61fc231709"
SOURCE_PARENT = "4becf2bb4c482b95178ffc7983ff9607ae15c60b"
ROOT750_SHA = "7786f2438cd5caf7f01d29a3af06d3fdd87e8f0f0cd9e8c0b022ed75744757cd"
POST750_SHA = "259ee43336460723ab813721f7c010be012cbda3529a336403b8417cd9319954"
BEFORE_MANIFEST_SHA = "3e6b945f82907b24905e168f05f5c58745b5cecaa232abf4fa71997408e9ee0e"
HISTORICAL751_MANIFEST_SHA = "0409de821c074a3e31e8916ad3dec6bcb62580cbfcd4c76be5a636334d7f01dd"
PRIOR_SET_SHA = "9e878c1f1a701a6891ced3dc76c9cd5cd7d995a250d8187ff83eb68866cd4fe6"
SURFACE_SET_SHA = "f3fed11dff487348bc998306b4eb8c17657e00d81c794bb09a1b3cbdc3f28d91"
BEFORE_MANIFEST_BYTES = "{\n  \"schema_version\": 1,\n  \"stage\": \"P1-00b\",\n  \"assignment_id\": \"WA-P1-00B-FREEZE-20260904-001\",\n  \"inventory\": {\n    \"expected_seed_count\": 465,\n    \"paths\": [\n      \"docs/refactor/two-server/section-1/inventory/surface-api.json\",\n      \"docs/refactor/two-server/section-1/inventory/surface-bot.json\",\n      \"docs/refactor/two-server/section-1/inventory/surface-web.json\",\n      \"docs/refactor/two-server/section-1/inventory/surface-jobs.json\"\n    ]\n  },\n  \"evidence_sources\": {\n    \"baseline_path\": \"docs/refactor/two-server/section-1/inventory/scenario-records.json\",\n    \"base_path\": \"docs/refactor/two-server/section-1/inventory/recent-characterization.json\",\n    \"continuation_path\": \"docs/refactor/two-server/section-1/inventory/recent-characterization-continuation.json\",\n    \"continuation_2_path\": \"docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-2.json\",\n    \"continuation_3_path\": \"docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-3.json\",\n    \"continuation_4_path\": \"docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-4.json\",\n    \"continuation_5_path\": \"docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-5.json\",\n    \"continuation_6_path\": \"docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-6.json\",\n    \"continuation_7_path\": \"docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-7.json\",\n    \"continuation_8_path\": \"docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-8.json\",\n    \"continuation_9_path\": \"docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-9.json\",\n    \"continuation_10_path\": \"docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-10.json\",\n    \"continuation_11_path\": \"docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-11.json\",\n    \"continuation_13_path\": \"docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-13.json\",\n    \"continuation_16_path\": \"docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json\",\n    \"continuation_17_path\": \"docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-17.json\"\n  },\n  \"counts\": {\n    \"baseline_evidenced_seed_count\": 135,\n    \"reconciled_new_seed_count\": 254,\n    \"evidenced_seed_count\": 389,\n    \"remaining_seed_count\": 76\n  },\n  \"coverage_status\": \"PARTIAL\",\n  \"canonical_blockers\": [\n    \"P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE\",\n    \"P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN\"\n  ]\n}\n".encode("utf-8")
BLOCKERS = [
    "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE",
    "P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN",
]


def _set_sha(values: set[str]) -> str:
    return hashlib.sha256(json.dumps(
        sorted(values), ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


class NotificationMarkAllAccountingTests(unittest.TestCase):
    def _read(self, relative: str) -> bytes:
        self.assertTrue(relative.startswith((INVENTORY, "tests/")))
        self.assertNotIn("..", relative.split("/"))
        path = ROOT / relative
        self.assertFalse(path.is_symlink())
        info = path.stat()
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertLessEqual(info.st_size, 2_000_000)
        body = path.read_bytes()
        self.assertEqual(len(body), info.st_size)
        return body

    def _json(self, relative: str) -> dict:
        value = json.loads(self._read(relative).decode("utf-8"))
        self.assertIs(type(value), dict)
        return value

    def _historical751_manifest(self) -> dict:
        # Immutable390 fixture, not a hard pin on any future current-live body.
        self.assertEqual(hashlib.sha256(BEFORE_MANIFEST_BYTES).hexdigest(), BEFORE_MANIFEST_SHA)
        historical = json.loads(BEFORE_MANIFEST_BYTES)
        historical["evidence_sources"][KEY] = RECEIPT
        historical["counts"] = {
            "baseline_evidenced_seed_count": 135,
            "reconciled_new_seed_count": 255,
            "evidenced_seed_count": 390,
            "remaining_seed_count": 75,
        }
        raw = (json.dumps(historical, indent=2) + "\n").encode("utf-8")
        self.assertEqual(hashlib.sha256(raw).hexdigest(), HISTORICAL751_MANIFEST_SHA)
        return historical

    def _derive(
        self, manifest: dict, receipt: dict, extra_documents: dict | None = None
    ) -> tuple[set[str], set[str], set[str]]:
        surface = set()
        for relative in manifest["inventory"]["paths"]:
            document = self._json(relative)
            ids = [row["behavior_id"] for row in document["entries"]]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertFalse(surface.intersection(ids))
            surface.update(ids)
        baseline_doc = self._json(manifest["evidence_sources"]["baseline_path"])
        matching = [
            row for row in baseline_doc["blocker_registry"]["items"]
            if row.get("blocker_id", row.get("id")) == BLOCKERS[0]
        ]
        self.assertEqual(len(matching), 1)
        reason = matching[0]["reason"]
        self.assertEqual(reason.count("Evidenced seeds are "), 1)
        seed_text = reason.split("Evidenced seeds are ", 1)[1].split(
            ". Sibling family", 1
        )[0]
        baseline_ids = [value.strip() for value in seed_text.split(",") if value.strip()]
        self.assertEqual(len(baseline_ids), len(set(baseline_ids)))
        baseline = set(baseline_ids)
        evidenced = set(baseline)
        for key, relative in manifest["evidence_sources"].items():
            if key == "baseline_path":
                continue
            if relative == RECEIPT:
                document = receipt
            elif extra_documents is not None and relative in extra_documents:
                # Pure in-memory canary inputs; never a future acceptance claim.
                document = extra_documents[relative]
            else:
                document = self._json(relative)
            for row in document.get("records", []):
                ids = row.get("seed_ids", [])
                self.assertIs(type(ids), list)
                self.assertTrue(all(type(value) is str for value in ids))
                evidenced.update(ids)
        self.assertTrue(evidenced <= surface)
        return surface, baseline, evidenced

    def _assert_live_accounting(
        self, manifest: dict, receipt: dict, extra_documents: dict | None = None
    ) -> None:
        self.assertEqual(hashlib.sha256(BEFORE_MANIFEST_BYTES).hexdigest(), BEFORE_MANIFEST_SHA)
        before = json.loads(BEFORE_MANIFEST_BYTES)
        self.assertEqual(manifest["schema_version"], 1)
        self.assertIs(type(manifest["schema_version"]), int)
        self.assertEqual(manifest["inventory"], before["inventory"])
        self.assertEqual(manifest["coverage_status"], "PARTIAL")
        self.assertEqual(manifest["canonical_blockers"], BLOCKERS)
        for key, relative in before["evidence_sources"].items():
            self.assertEqual(manifest["evidence_sources"][key], relative)
        self.assertIn(KEY, manifest["evidence_sources"])
        self.assertEqual(manifest["evidence_sources"][KEY], RECEIPT)
        self.assertNotIn(RECEIPT, before["evidence_sources"].values())

        records = receipt["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["seed_ids"], [SEED])
        self.assertEqual(len(records[0]["seed_ids"]), len(set(records[0]["seed_ids"])))
        self.assertEqual(records[0]["path"], SOURCE)
        self.assertEqual(records[0]["sha256"], SOURCE_SHA)
        self.assertEqual(records[0]["last_commit"], SOURCE_COMMIT)
        self.assertEqual(records[0]["test_selectors"], [
            SOURCE + "::" + CLASS + "::" + method for method in METHODS
        ])

        surface, baseline, prior = self._derive(before, receipt)
        self.assertEqual((len(surface), len(baseline), len(prior)), (465, 135, 389))
        self.assertEqual(_set_sha(surface), SURFACE_SET_SHA)
        self.assertEqual(_set_sha(prior), PRIOR_SET_SHA)
        self.assertIn(SEED, surface)
        self.assertNotIn(SEED, prior)

        # Derive exactly this proposal from actual named records, independently
        # of declared counts. Additional later accepted evidence may extend live
        # coverage; it cannot rewrite751's singleton historical contribution.
        singleton = self._historical751_manifest()
        _, _, after751 = self._derive(singleton, receipt)
        self.assertEqual(after751 - prior, {SEED})
        self.assertEqual(prior - after751, set())
        self.assertEqual(len(after751), 390)
        self.assertEqual(len(surface - after751), 75)

        actual_surface, actual_baseline, actual_live = self._derive(
            manifest, receipt, extra_documents
        )
        self.assertEqual(actual_surface, surface)
        self.assertEqual(actual_baseline, baseline)
        self.assertTrue(after751 <= actual_live)
        counts = manifest["counts"]
        expected_counts = {
            "baseline_evidenced_seed_count": len(baseline),
            "reconciled_new_seed_count": len(actual_live - baseline),
            "evidenced_seed_count": len(actual_live),
            "remaining_seed_count": len(surface - actual_live),
        }
        self.assertEqual(counts, expected_counts)
        self.assertTrue(all(type(value) is int for value in counts.values()))

        promotion = receipt["promotion_assessment"]
        self.assertEqual(promotion["promoted_seed_ids"], [SEED])
        self.assertEqual(promotion["delta_distinct_seed_count"], 1)
        self.assertEqual(promotion["removed_seed_ids"], [])
        self.assertEqual(promotion["duplicate_new_seed_ids"], [])
        self.assertEqual(promotion["canonical_before"], {
            "expected": 465, "baseline": 135, "reconciled_new": 254,
            "evidenced": 389, "remaining": 76,
        })
        self.assertEqual(promotion["canonical_after_on_exact_acceptance"], {
            "expected": 465, "baseline": 135, "reconciled_new": 255,
            "evidenced": 390, "remaining": 75,
        })
        self.assertEqual(promotion["full_seed_acceptance"], "NOT_ASSESSED")

    def test_registration_binds_actual750_source_and_two_scenarios(self) -> None:
        receipt = self._json(RECEIPT)
        self.assertEqual(receipt["schema_version"], 1)
        self.assertEqual(receipt["assignment_id"],
                         "WA-P1-00B-NOTIFICATION-MARK-ALL-ACCOUNTING-20261019-751R")
        source = receipt["source"]
        self.assertEqual(source["accepted_source_commit"], SOURCE_COMMIT)
        self.assertEqual(source["accepted_parent"], SOURCE_PARENT)
        self.assertEqual(source["accepted_file"], {
            "path": SOURCE, "sha256": SOURCE_SHA, "utf8_bytes": 7570, "mode": "100644",
        })
        self.assertEqual(source["source_canonical_seed_credit"], 0)
        body = self._read(SOURCE)
        self.assertEqual(len(body), 7570)
        self.assertEqual(hashlib.sha256(body).hexdigest(), SOURCE_SHA)
        self.assertEqual((ROOT / SOURCE).stat().st_mode & 0o111, 0)
        tree = ast.parse(body.decode("utf-8"))
        classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == CLASS]
        self.assertEqual(len(classes), 1)
        methods = {
            node.name: node for node in classes[0].body
            if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("test_")
        }
        self.assertEqual(set(methods), set(METHODS))
        for method in METHODS:
            calls = [
                node for node in ast.walk(methods[method])
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "notifications"
                and node.func.attr == "mark_all_notifications_read"
            ]
            self.assertEqual(len(calls), 1)

        handlers = receipt["registered_handlers"]
        self.assertEqual(len(handlers), 2)
        self.assertEqual([row["scenario_key"] for row in handlers], list(SCENARIOS))
        self.assertEqual([row["selector"] for row in handlers], [
            SOURCE + "::" + CLASS + "::" + method for method in METHODS
        ])
        for row in handlers:
            self.assertEqual(row["seed_id"], SEED)
            self.assertEqual(row["canonical_symbol"], "api/routers/notifications.py:mark_all_notifications_read")
            self.assertEqual(row["source_commit"], SOURCE_COMMIT)
            self.assertEqual(row["source_file_sha256"], SOURCE_SHA)
            self.assertTrue(row["evidence_limits"])
        surface = self._json(INVENTORY + "surface-api.json")
        matching = [row for row in surface["entries"] if row["behavior_id"] == SEED]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["handler"], "notifications.mark_all_notifications_read")
        self.assertEqual(matching[0]["path"], "/api/notifications/mark-all-read")

        actor = receipt["actor"]
        self.assertEqual(actor["source_Root_acceptance"]["sha256"], ROOT750_SHA)
        self.assertEqual(actor["source_Root_posthold"]["sha256"], POST750_SHA)
        self.assertEqual(actor["source_author_agent"], "/root/ae741_controls_review")
        self.assertEqual(actor["source_reviewer_agent"], "/root/cost_report_review")
        self.assertFalse(actor["genuine_Gemini_Worker_or_Reviewer_claim"])
        accounting = receipt["accounting"]
        self.assertEqual(accounting["accepted_Source_units"], 1)
        self.assertEqual(accounting["accepted_handler_scenarios"], 2)
        self.assertEqual(accounting["behavior_units_added_by_this_metadata_or_witness"], 0)
        self.assertEqual(accounting["units_since_reconciliation"], 1)
        self.assertEqual(accounting["maximum_accepted_units_before_reconciliation"], 5)
        self.assertFalse(accounting["current_group_global_reconciliation_complete"])
        previous = receipt["previous_group_provenance"]
        self.assertEqual(previous["accepted_closeout_commit"],
                         "8b8d23571b04c9907e2e9586941a964629191635")
        self.assertEqual(previous["status"], "COMPLETED_PROVENANCE")
        self.assertEqual((previous["Source_units"], previous["handler_scenarios"]), (5, 7))
        self.assertEqual((previous["pending_five"], previous["units_since_reconciliation"]), (5, 5))
        self.assertEqual(previous["remaining_behavior_units"], 0)
        self.assertEqual(receipt["origin_matrix_unresolved_cells"], 24)
        self.assertEqual(receipt["canonical_blockers"], BLOCKERS)
        self.assertEqual(receipt["seed_acceptance_status"], "FULL_SEED_ACCEPTANCE_NOT_ASSESSED")
        self.assertFalse(receipt["closures_authorized"])
        self.assertEqual(receipt["stages"], {"P1-00": "COMPLETE", "P1-00b": "IN_PROGRESS"})

    def test_live_set_has_exact_singleton751_delta_and_consistent_counts(self) -> None:
        self._assert_live_accounting(self._json(MANIFEST), self._json(RECEIPT))

    def test_live_oracle_rejects_missing_mapping_duplicate_seed_and_bad_counts(self) -> None:
        manifest, receipt = self._json(MANIFEST), self._json(RECEIPT)
        self._assert_live_accounting(manifest, receipt)
        missing = copy.deepcopy(manifest)
        del missing["evidence_sources"][KEY]
        with self.assertRaises(AssertionError):
            self._assert_live_accounting(missing, receipt)
        duplicate = copy.deepcopy(receipt)
        duplicate["records"][0]["seed_ids"].append(SEED)
        with self.assertRaises(AssertionError):
            self._assert_live_accounting(manifest, duplicate)
        wrong_counts = copy.deepcopy(manifest)
        wrong_counts["counts"]["evidenced_seed_count"] += 1
        with self.assertRaises(AssertionError):
            self._assert_live_accounting(wrong_counts, receipt)
        wrong_seed = copy.deepcopy(receipt)
        wrong_seed["records"][0]["seed_ids"] = []
        with self.assertRaises(AssertionError):
            self._assert_live_accounting(manifest, wrong_seed)
        # The complete healthy oracle still succeeds after every isolated fault.
        self._assert_live_accounting(manifest, receipt)

    def test_generic_live_oracle_survives_test_only391_and_guards_surface_and_counts(self) -> None:
        # Synthetic in-memory391 is a recurrence guard, NOT a real receipt,
        # product scenario, Source unit, promotion, or acceptance observation.
        receipt = self._json(RECEIPT)
        historical = self._historical751_manifest()
        surface, baseline, historical_seeds = self._derive(historical, receipt)
        next_seed = sorted(surface - historical_seeds)[0]
        future_path = INVENTORY + "TEST_ONLY_future_accounting.json"
        future = copy.deepcopy(historical)
        future["evidence_sources"]["TEST_ONLY_future_path"] = future_path
        documents = {future_path: {"records": [{"seed_ids": [next_seed]}]}}
        future_seeds = historical_seeds | {next_seed}
        future["counts"] = {
            "baseline_evidenced_seed_count": len(baseline),
            "reconciled_new_seed_count": len(future_seeds - baseline),
            "evidenced_seed_count": len(future_seeds),
            "remaining_seed_count": len(surface - future_seeds),
        }
        self.assertEqual((len(future_seeds), len(surface - future_seeds)), (391, 74))
        self._assert_live_accounting(future, receipt, documents)
        wrong_counts = copy.deepcopy(future)
        wrong_counts["counts"]["evidenced_seed_count"] = 390
        with self.assertRaises(AssertionError):
            self._assert_live_accounting(wrong_counts, receipt, documents)
        outside_surface = copy.deepcopy(documents)
        outside_surface[future_path]["records"][0]["seed_ids"] = [
            "TEST_ONLY_NOT_A_CANONICAL_SURFACE_SEED"
        ]
        with self.assertRaises(AssertionError):
            self._assert_live_accounting(future, receipt, outside_surface)
        # Restore both synthetic and exact historical oracles after each fault.
        self._assert_live_accounting(future, receipt, documents)
        self._assert_live_accounting(historical, receipt)
