"""798D data-only witness; no application import, provider or subprocess."""
from __future__ import annotations
import ast
import copy
import hashlib
import json
from pathlib import Path
import stat
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKET = "docs/refactor/two-server/section-1/inventory/recent-characterization-business-768-live786-five-source-closeout-manual.json"
MANIFEST = "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json"
LEDGER = "docs/refactor/two-server/section-1/00-stage-ledger.md"
KEY = "business_768_live786_five_source_closeout_path"
OLD_PACKET = "docs/refactor/two-server/section-1/inventory/recent-characterization-business-768-live786-manual783-provenance.json"
OLD_PACKET_SHA = "685032585be7452977b4fda3bf4dbb2cbaf37c127131743a69c1fdf233ac9a5f"
BEFORE_MANIFEST_SHA = "5ba41aba13a6e400da96ec97ee1a1d272cba0daa26ec872a98e64b8bb88bc909"
BEFORE_LEDGER_BYTES = 103009
BEFORE_LEDGER_SHA = "0d1f820028719ebffad3167619cba05e6153e9ce6a9618217ce34881b0f9e54e"
ACCEPTED_LEDGER_PREFIX_BYTES = 109329
ACCEPTED_LEDGER_PREFIX_SHA = "a25deecb4880ed4c7ceb905ae5db5048c8d3fc7b58129bc6cfdcf4d31d2c05c2"
PACKET_SHA = "46b231dbcf808f556bb5f798c50fc9444a4e6fc7ce1dfa8ec5e6d8855567a4d3"
ANCHOR = 'id="live786-five-source-atomic-closeout-798d"'
BLOCKERS = ["P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE", "P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN"]
OLD_KEYS = ["baseline_path","base_path","continuation_path","continuation_2_path","continuation_3_path","continuation_4_path","continuation_5_path","continuation_6_path","continuation_7_path","continuation_8_path","continuation_9_path","continuation_10_path","continuation_11_path","continuation_13_path","continuation_16_path","continuation_17_path","notification_mark_all_750_path","trade_read_prequery_757_path","business_766_a2_path","business_766_b3_path","business_768_market_overrides_a2_path","business_768_market_overrides_b3_path","business_768_live786_manual783_path"]
# Expected Source literals were independently inspected, not read from the packet.
EXPECTED = [["tests/test_p1_00b_characterization_market_overrides_all_failure_sqlite.py","29effa3f16a5fc125f1cc50b291e2af8cb9bcebc76ad16bb15d47e9ef5e7b778",23877,"563ce20195caf3e21dca416b63170470e6f87042","58c6388574a54a80296baabc23f64c5b8b66458a",["API-ADMIN-GET-TRADING-SETTINGS-LIST-MARKET-OVERRIDES-L214"],["MarketOverridesAllFailureSQLiteCharacterizationTests::test_all_failure_propagates_after_real_scalars_without_downstream_work"]],["tests/test_p1_00b_characterization_market_overrides_empty_sqlite_recovery.py","9147db87a11804da1a9a1d28dffaf56dd7ba8e564e6ba90b6aa8e4a88de8ebcc",24887,"446288e2f8d549809fbd5a9b00faea32e7d0bfa5","1b2913ebd031f63a76f8513f7f7fedd96993313e",["API-ADMIN-GET-TRADING-SETTINGS-LIST-MARKET-OVERRIDES-L214"],["MarketOverridesEmptySQLiteCharacterizationTests::test_explicit_clean_sync_flush_is_rejected"]],["tests/test_p1_00b_characterization_market_overrides_nonempty_sqlite.py","f296c4ee7e9f821b426e26004fd59d5ce72f4cb2dfea69e4af06113e45218947",33752,"dead25e559f3fbdeaddd986e3db232192a96d45b","446288e2f8d549809fbd5a9b00faea32e7d0bfa5",["API-ADMIN-GET-TRADING-SETTINGS-LIST-MARKET-OVERRIDES-L214"],["MarketOverridesNonemptySQLiteCharacterizationTests::test_explicit_clean_sync_flush_is_rejected"]],["tests/test_p1_00b_characterization_trading_settings_read_dependency_failures_batch.py","9e759e3fccf7c8e716c02dacbbad6319893ca8375befc8321b4bc55b59af0002",7662,"401462d8af10e4ee3feadb1a392eecf6387b76a7","f1c2ec5b11cba5352cf5b188667243f8ae356819",["API-ADMIN-GET-TRADING-SETTINGS-GET-SETTINGS-L195","API-ADMIN-GET-TRADING-SETTINGS-GET-MARKET-STATE-L202"],["TradingSettingsReadDependencyFailuresCharacterizationTests::test_get_settings_propagates_loader_failure_before_serialization","TradingSettingsReadDependencyFailuresCharacterizationTests::test_get_market_state_propagates_runtime_view_failure_before_response"]],["tests/test_p1_00b_characterization_admin_users_cancel_fsm_clear_failures_batch.py","f1f5fcb5aa7fa1d01c08f2a33b4595c04021242036f4544860afd35382d5ceec",9528,"a47494a02451e13df07d6b7464197af6333508d8","401462d8af10e4ee3feadb1a392eecf6387b76a7",["BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-LIMIT-CANCEL-L1563","BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-SEARCH-CANCEL-L657"],["AdminUsersCancelFSMClearFailuresCharacterizationTests::test_limit_cancel_propagates_fsm_clear_failure_before_session_or_delivery","AdminUsersCancelFSMClearFailuresCharacterizationTests::test_user_search_cancel_propagates_fsm_clear_failure_before_navigation_or_delivery"]]]
ACCOUNTING = {"pending_five":5,"units_since_reconciliation":5,"total_behavior_units":5,"total_handler_scenarios":7,"reconciled_behavior_units_on_exact_acceptance":5,"reconciled_handler_scenarios_on_exact_acceptance":7,"remaining_behavior_units_on_exact_acceptance":0,"remaining_handler_scenarios_on_exact_acceptance":0,"behavior_units_added":0,"added_distinct_seeds":0,"current_group_global_reconciliation_complete_on_exact_acceptance":True,"canonical_manifest_and_ledger_integration_complete_on_exact_acceptance":True,"global_project_reconciliation_complete":False,"manual_SDK_counter_or_checkpoint_edits":False,"previous_775_group":"COMPLETED_PROVENANCE; never reopened or counted again","historical_pending_counts_and_false_flags_unchanged":True}
HISTORICAL_COUNTS = {"baseline_evidenced_seed_count":135,"reconciled_new_seed_count":261,"evidenced_seed_count":396,"remaining_seed_count":69}


class Live786FiveSourceCloseoutTests(unittest.TestCase):
    def _read(self, relative):
        self.assertIs(type(relative), str)
        rel = Path(relative)
        self.assertFalse(rel.is_absolute())
        self.assertNotIn("..", rel.parts)
        path = ROOT / rel
        self.assertFalse(path.is_symlink())
        self.assertTrue(path.resolve(strict=True).is_relative_to(ROOT))
        info = path.stat()
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertLessEqual(info.st_size, 2_000_000)
        body = path.read_bytes()
        self.assertEqual(len(body), info.st_size)
        return body

    def _json(self, relative):
        value = json.loads(self._read(relative))
        self.assertIs(type(value), dict)
        return value

    def _validate(self, packet, manifest, ledger, source_bytes=None):
        self.assertEqual(packet["status"], "DOCUMENTATION_ONLY_PROPOSAL_PENDING_EXACT_ACCEPTANCE")
        self.assertEqual(packet["assignment_id"], "WA-P1-00B-FIVE-SOURCE-RECONCILIATION-20261023-798D")
        self.assertFalse(packet["own_future_commit_or_receipt_claimed"])
        self.assertIs(packet["conditional_on_exact_four_path_acceptance"], True)
        self.assertEqual(packet["document_provenance"]["authorship"], "CODEX_AUTHORED")
        self.assertFalse(packet["document_provenance"]["Gemini_Worker_Reviewer_TASK_READY_or_CONTINUE_claimed_for_this_document"])
        self.assertFalse(packet["document_provenance"]["rejected_provider_output_adopted"])
        self.assertEqual(packet["accounting"], ACCOUNTING)
        self.assertEqual(packet["counts"], {
            "strict_PROVEN_FOR_REQUESTED_SCENARIO":288,"strict_PARTIAL_OR_SEED_ONLY":177,
            "total_seeds":465, **HISTORICAL_COUNTS})
        self.assertEqual(packet["coverage_status"], "PARTIAL")
        self.assertEqual(packet["seed_acceptance_status"], "FULL_SEED_ACCEPTANCE_NOT_ASSESSED")
        self.assertEqual(packet["canonical_blockers"], BLOCKERS)
        self.assertEqual(packet["origin_matrix_unresolved_cells"], 24)
        self.assertFalse(packet["closures_authorized"])
        self.assertEqual(packet["stages"], {"P1-00":"COMPLETE","P1-00b":"IN_PROGRESS"})
        records = packet["records"]
        self.assertEqual(len(records), 5)
        self.assertEqual(len({r["last_commit"] for r in records}), 5)
        self.assertEqual(sum(len(r["test_selectors"]) for r in records), 7)
        for number, (row, values) in enumerate(zip(records, EXPECTED, strict=True), 1):
            expected = dict(zip(("path","sha256","utf8_bytes","last_commit","parent","seed_ids"), values[:6], strict=True))
            expected["test_selectors"] = [values[0] + "::" + suffix for suffix in values[6]]
            self.assertEqual(row["unit"], number)
            self.assertEqual(row["mode"], "100644")
            self.assertEqual({key:row[key] for key in expected}, expected)
            self.assertTrue(row["evidence_limit"])
            self.assertIs(type(row["historical_evidence"]), dict)
            source = self._read(expected["path"]) if source_bytes is None else source_bytes[expected["path"]]
            self.assertEqual(len(source), expected["utf8_bytes"])
            self.assertEqual(hashlib.sha256(source).hexdigest(), expected["sha256"])
            tree = ast.parse(source)
            for selector in expected["test_selectors"]:
                path, cls, method = selector.split("::")
                self.assertEqual(path, expected["path"])
                classes = [x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == cls]
                self.assertEqual(len(classes), 1)
                methods = [x for x in classes[0].body if isinstance(x, ast.AsyncFunctionDef) and x.name == method]
                self.assertEqual(len(methods), 1)
        self.assertEqual(packet["manifest_registration"]["prior_sha256"], BEFORE_MANIFEST_SHA)
        self.assertEqual(packet["manifest_registration"]["only_added_mapping"], {KEY:PACKET})
        self.assertIs(packet["manifest_registration"]["prior_mappings_values_and_counts_unchanged"], True)
        self.assertEqual(manifest["evidence_sources"][KEY], PACKET)
        # Reconstruct the exact historical BEFORE from current legacy fields;
        # future ADDITIVE mappings/count changes need not break this witness.
        historical = {k:manifest[k] for k in (
            "schema_version","stage","assignment_id","inventory","evidence_sources",
            "counts","coverage_status","canonical_blockers")}
        historical["evidence_sources"] = {k:manifest["evidence_sources"][k] for k in OLD_KEYS}
        historical["counts"] = HISTORICAL_COUNTS
        self.assertEqual(hashlib.sha256((json.dumps(historical, indent=2)+"\n").encode()).hexdigest(), BEFORE_MANIFEST_SHA)
        baseline_doc = self._json(manifest["evidence_sources"]["baseline_path"])
        blockers = [r for r in baseline_doc["blocker_registry"]["items"]
                    if r.get("blocker_id", r.get("id")) == BLOCKERS[0]]
        self.assertEqual(len(blockers), 1)
        reason = blockers[0]["reason"].split("Evidenced seeds are ", 1)[1].split(". Sibling family", 1)[0]
        baseline = {s.strip() for s in reason.split(",") if s.strip()}
        self.assertEqual(len(baseline), 135)
        target_ids = {s for r in records for s in r["seed_ids"]}
        self.assertEqual(len(target_ids), 5)
        self.assertTrue(target_ids <= baseline)
        prior = set(baseline)
        for key, relative in manifest["evidence_sources"].items():
            if key in ("baseline_path", KEY):
                continue
            for record in self._json(relative).get("records", []):
                prior.update(record.get("seed_ids", []))
        self.assertEqual(prior | target_ids, prior)
        surface = set()
        for relative in manifest["inventory"]["paths"]:
            surface.update(r["behavior_id"] for r in self._json(relative)["entries"])
        self.assertEqual(len(surface), 465)
        self.assertTrue(prior <= surface)
        self.assertEqual(manifest["counts"], {
            "baseline_evidenced_seed_count":len(baseline),
            "reconciled_new_seed_count":len(prior - baseline),
            "evidenced_seed_count":len(prior),
            "remaining_seed_count":len(surface - prior)})
        old_packet = self._read(OLD_PACKET)
        self.assertEqual(len(old_packet), 9112)
        self.assertEqual(hashlib.sha256(old_packet).hexdigest(), OLD_PACKET_SHA)
        self.assertEqual(packet["ledger_integration"]["prior_utf8_bytes"], BEFORE_LEDGER_BYTES)
        self.assertEqual(packet["ledger_integration"]["prior_sha256"], BEFORE_LEDGER_SHA)
        self.assertIs(packet["ledger_integration"]["exact_prefix_unchanged"], True)
        self.assertEqual(hashlib.sha256(ledger[:BEFORE_LEDGER_BYTES]).hexdigest(), BEFORE_LEDGER_SHA)
        self.assertGreaterEqual(len(ledger), ACCEPTED_LEDGER_PREFIX_BYTES)
        prefix = ledger[:ACCEPTED_LEDGER_PREFIX_BYTES]
        self.assertEqual(hashlib.sha256(prefix).hexdigest(), ACCEPTED_LEDGER_PREFIX_SHA)
        self.assertEqual(prefix.decode().count(ANCHOR), 1)
        self.assertIn("5/5 Source units;7/7 scenarios; remaining0", prefix.decode())

    def test_complete_five_source_union_and_atomic_accounting(self):
        raw = self._read(PACKET)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), PACKET_SHA)
        self._validate(json.loads(raw), self._json(MANIFEST), self._read(LEDGER))

    def test_rejects_source_selector_duplication_reset_and_partial_closure(self):
        packet, manifest, ledger = self._json(PACKET), self._json(MANIFEST), self._read(LEDGER)
        self._validate(packet, manifest, ledger)
        mutations = [
            ("source-commit", lambda p:p["records"][0].__setitem__("last_commit", "0"*40)),
            ("selector", lambda p:p["records"][1].__setitem__("test_selectors", ["wrong::Class::test_wrong"])),
            ("git-mode", lambda p:p["records"][1].__setitem__("mode", "100755")),
            ("duplicate-unit", lambda p:p["records"].append(copy.deepcopy(p["records"][0]))),
            ("counter-reset", lambda p:p["accounting"].__setitem__("pending_five", 0)),
            ("partial-closeout", lambda p:p["accounting"].__setitem__("remaining_behavior_units_on_exact_acceptance", 4)),
            ("seed-credit", lambda p:p["accounting"].__setitem__("added_distinct_seeds", 1)),
            ("stage-credit", lambda p:p.__setitem__("closures_authorized", True)),
        ]
        for name, mutate in mutations:
            with self.subTest(name=name):
                bad = copy.deepcopy(packet)
                mutate(bad)
                with self.assertRaises(AssertionError):
                    self._validate(bad, manifest, ledger)
                self._validate(packet, manifest, ledger)
        sources = {r[0]:self._read(r[0]) for r in EXPECTED}
        changed = dict(sources)
        first = EXPECTED[0][0]
        changed[first] = b"!" + changed[first][1:]
        with self.assertRaises(AssertionError):
            self._validate(packet, manifest, ledger, changed)
        bad_manifest = copy.deepcopy(manifest)
        bad_manifest["evidence_sources"].pop(KEY)
        with self.assertRaises(KeyError):
            self._validate(packet, bad_manifest, ledger)
        with self.assertRaises(AssertionError):
            self._validate(packet, manifest, b"!" + ledger[1:])
        self._validate(packet, manifest, ledger)


if __name__ == "__main__":
    unittest.main()
