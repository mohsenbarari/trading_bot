import hashlib
import json
from pathlib import Path
import unittest


class SeedHandlerCoverageContractTests(unittest.TestCase):
    def test_canonical_remaining_seed_count_matches_inventory_and_reconciled_records(self):
        repo_root = Path(__file__).resolve().parent.parent

        manifest_rel_path = "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json"
        manifest_path = repo_root / manifest_rel_path
        self.assertTrue(manifest_path.is_file(), f"Manifest not found at {manifest_path}")

        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))

        expected_top_level_keys = {
            "schema_version",
            "stage",
            "assignment_id",
            "inventory",
            "evidence_sources",
            "counts",
            "coverage_status",
            "canonical_blockers",
        }
        self.assertEqual(set(manifest.keys()), expected_top_level_keys)
        self.assertIs(type(manifest["schema_version"]), int)
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["stage"], "P1-00b")
        self.assertEqual(manifest["assignment_id"], "WA-P1-00B-FREEZE-20260904-001")
        self.assertEqual(manifest["coverage_status"], "PARTIAL")
        self.assertEqual(
            manifest["canonical_blockers"],
            [
                "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE",
                "P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN",
            ],
        )

        expected_inventory_keys = {"expected_seed_count", "paths"}
        self.assertEqual(set(manifest["inventory"].keys()), expected_inventory_keys)
        self.assertEqual(manifest["inventory"]["expected_seed_count"], 465)
        expected_surface_paths = [
            "docs/refactor/two-server/section-1/inventory/surface-api.json",
            "docs/refactor/two-server/section-1/inventory/surface-bot.json",
            "docs/refactor/two-server/section-1/inventory/surface-web.json",
            "docs/refactor/two-server/section-1/inventory/surface-jobs.json",
        ]
        self.assertEqual(manifest["inventory"]["paths"], expected_surface_paths)

        expected_evidence_keys = {
            "baseline_path",
            "base_path",
            "continuation_path",
            "continuation_2_path",
            "continuation_3_path",
            "continuation_4_path",
            "continuation_5_path",
        }
        self.assertEqual(set(manifest["evidence_sources"].keys()), expected_evidence_keys)
        self.assertEqual(
            manifest["evidence_sources"]["baseline_path"],
            "docs/refactor/two-server/section-1/inventory/scenario-records.json",
        )
        self.assertEqual(
            manifest["evidence_sources"]["base_path"],
            "docs/refactor/two-server/section-1/inventory/recent-characterization.json",
        )
        self.assertEqual(
            manifest["evidence_sources"]["continuation_path"],
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation.json",
        )
        self.assertEqual(
            manifest["evidence_sources"]["continuation_2_path"],
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-2.json",
        )
        self.assertEqual(
            manifest["evidence_sources"]["continuation_3_path"],
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-3.json",
        )
        self.assertEqual(
            manifest["evidence_sources"]["continuation_4_path"],
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-4.json",
        )
        self.assertEqual(
            manifest["evidence_sources"]["continuation_5_path"],
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-5.json",
        )

        expected_counts_keys = {
            "baseline_evidenced_seed_count",
            "reconciled_new_seed_count",
            "evidenced_seed_count",
            "remaining_seed_count",
        }
        self.assertEqual(set(manifest["counts"].keys()), expected_counts_keys)
        self.assertEqual(manifest["counts"]["baseline_evidenced_seed_count"], 135)
        self.assertEqual(manifest["counts"]["reconciled_new_seed_count"], 248)
        self.assertEqual(manifest["counts"]["evidenced_seed_count"], 383)
        self.assertEqual(manifest["counts"]["remaining_seed_count"], 82)

        expected_per_file_counts = [217, 203, 30, 15]
        inventory_behavior_ids = []
        for rel_path, expected_len in zip(expected_surface_paths, expected_per_file_counts):
            surface_file_path = repo_root / rel_path
            self.assertTrue(surface_file_path.is_file(), f"Surface file not found at {surface_file_path}")
            surface_data = json.loads(surface_file_path.read_bytes().decode("utf-8"))
            entries = surface_data["entries"]
            self.assertEqual(len(entries), expected_len, f"Entry count mismatch for {rel_path}")
            file_ids = [entry["behavior_id"] for entry in entries]
            self.assertEqual(len(file_ids), expected_len)
            inventory_behavior_ids.extend(file_ids)

        self.assertEqual(len(inventory_behavior_ids), 465)
        inventory_seed_set = set(inventory_behavior_ids)
        self.assertEqual(len(inventory_seed_set), 465)

        surface_summary_path = repo_root / "docs/refactor/two-server/section-1/inventory/surface-behavior-inventory.json"
        self.assertTrue(surface_summary_path.is_file(), f"Surface behavior inventory summary not found at {surface_summary_path}")
        surface_summary_data = json.loads(surface_summary_path.read_bytes().decode("utf-8"))
        self.assertEqual(surface_summary_data.get("executing_assignment_id"), "WA-P1-00B-FREEZE-20260904-001")

        baseline_file_path = repo_root / manifest["evidence_sources"]["baseline_path"]
        self.assertTrue(baseline_file_path.is_file(), f"Baseline scenario records not found at {baseline_file_path}")
        scenario_data = json.loads(baseline_file_path.read_bytes().decode("utf-8"))
        blocker_items = scenario_data.get("blocker_registry", {}).get("items", [])
        blocker = next(
            (
                item
                for item in blocker_items
                if item.get("id") == "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE"
                or item.get("blocker_id") == "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE"
            ),
            None,
        )
        self.assertIsNotNone(blocker, "Blocker P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE not found in blocker_registry.items")
        reason = blocker["reason"]
        start_delim = "Evidenced seeds are "
        end_delim = ". Sibling family"
        self.assertIn(start_delim, reason)
        self.assertIn(end_delim, reason)
        start_idx = reason.index(start_delim) + len(start_delim)
        end_idx = reason.index(end_delim, start_idx)
        raw_seeds_str = reason[start_idx:end_idx]
        baseline_seed_ids = [s.strip() for s in raw_seeds_str.split(",") if s.strip()]
        baseline_seed_set = set(baseline_seed_ids)
        self.assertEqual(len(baseline_seed_ids), 135)
        self.assertEqual(len(baseline_seed_set), 135)
        self.assertTrue(baseline_seed_set.issubset(inventory_seed_set))

        base_file_path = repo_root / manifest["evidence_sources"]["base_path"]
        self.assertTrue(base_file_path.is_file(), f"Base evidence file not found at {base_file_path}")
        base_bytes = base_file_path.read_bytes()
        calculated_base_sha256 = hashlib.sha256(base_bytes).hexdigest()
        base_data = json.loads(base_bytes.decode("utf-8"))

        continuation_file_path = repo_root / manifest["evidence_sources"]["continuation_path"]
        self.assertTrue(continuation_file_path.is_file(), f"Continuation evidence file not found at {continuation_file_path}")
        continuation_bytes = continuation_file_path.read_bytes()
        continuation_data = json.loads(continuation_bytes.decode("utf-8"))

        self.assertEqual(base_data.get("stage"), "P1-00b")
        self.assertEqual(continuation_data.get("stage"), "P1-00b")

        base_inventory_binding = continuation_data["base_inventory"]
        self.assertEqual(base_inventory_binding["path"], manifest["evidence_sources"]["base_path"])
        self.assertEqual(base_inventory_binding["sha256"], calculated_base_sha256)
        self.assertEqual(base_inventory_binding["inspected_head"], base_data["inspected_head"])

        prior_recent_ids = []
        for record in base_data.get("records", []):
            prior_recent_ids.extend(record.get("seed_ids", []))
        continuation_records = continuation_data.get("records", [])
        self.assertEqual(len(continuation_records), 51)
        for record in continuation_records[:5]:
            prior_recent_ids.extend(record.get("seed_ids", []))

        prior_recent_seed_set = set(prior_recent_ids)
        prior_checkpoint_set = baseline_seed_set | prior_recent_seed_set
        self.assertEqual(len(prior_checkpoint_set), 312)

        second_five_seed_ids = []
        for record in continuation_records[5:10]:
            second_five_seed_ids.extend(record.get("seed_ids", []))
        self.assertEqual(len(second_five_seed_ids), 12)
        second_five_seed_set = set(second_five_seed_ids)
        self.assertEqual(len(second_five_seed_set), 12)
        self.assertTrue(second_five_seed_set.issubset(inventory_seed_set))
        self.assertTrue(second_five_seed_set.isdisjoint(prior_checkpoint_set))

        prior_324_checkpoint_set = prior_checkpoint_set | second_five_seed_set
        self.assertEqual(len(prior_324_checkpoint_set), 324)

        third_five_seed_ids = []
        for record in continuation_records[10:15]:
            third_five_seed_ids.extend(record.get("seed_ids", []))
        self.assertEqual(len(third_five_seed_ids), 5)
        third_five_seed_set = set(third_five_seed_ids)
        self.assertEqual(len(third_five_seed_set), 5)
        self.assertTrue(third_five_seed_set.issubset(inventory_seed_set))
        self.assertTrue(third_five_seed_set.isdisjoint(prior_324_checkpoint_set))

        prior_329_checkpoint_set = prior_324_checkpoint_set | third_five_seed_set
        self.assertEqual(len(prior_329_checkpoint_set), 329)

        fourth_five_seed_ids = []
        for record in continuation_records[15:20]:
            fourth_five_seed_ids.extend(record.get("seed_ids", []))
        self.assertEqual(len(fourth_five_seed_ids), 5)
        fourth_five_seed_set = set(fourth_five_seed_ids)
        self.assertEqual(len(fourth_five_seed_set), 5)
        self.assertTrue(fourth_five_seed_set.issubset(inventory_seed_set))
        self.assertTrue(fourth_five_seed_set.isdisjoint(prior_329_checkpoint_set))

        prior_334_checkpoint_set = prior_329_checkpoint_set | fourth_five_seed_set
        self.assertEqual(len(prior_334_checkpoint_set), 334)

        fifth_five_seed_ids = []
        for record in continuation_records[20:25]:
            fifth_five_seed_ids.extend(record.get("seed_ids", []))
        self.assertEqual(len(fifth_five_seed_ids), 5)
        fifth_five_seed_set = set(fifth_five_seed_ids)
        self.assertEqual(len(fifth_five_seed_set), 5)
        self.assertTrue(fifth_five_seed_set.issubset(inventory_seed_set))
        self.assertTrue(fifth_five_seed_set.isdisjoint(prior_334_checkpoint_set))

        prior_339_checkpoint_set = prior_334_checkpoint_set | fifth_five_seed_set
        self.assertEqual(len(prior_339_checkpoint_set), 339)

        sixth_slice_records = continuation_records[25:31]
        self.assertEqual(len(sixth_slice_records), 6)
        expected_new_seed_ids = [
            "API-AUTH-POST-AUTH-REFRESH-ACCESS-TOKEN-L2011",
            "API-AUTH-POST-AUTH-DEV-LOGIN-L2080",
            "API-AUTH-POST-AUTH-RESEND-OTP-SMS-L2612",
            "API-AUTH-POST-AUTH-VERIFY-OTP-L2695",
            "API-AUTH-POST-AUTH-WEBAPP-LOGIN-L2899",
            "API-AUTH-POST-AUTH-SETUP-ADMIN-PASSWORD-L2915",
        ]
        sixth_slice_seed_ids = []
        for record in sixth_slice_records:
            sixth_slice_seed_ids.extend(record.get("seed_ids", []))
        self.assertEqual(sixth_slice_seed_ids, expected_new_seed_ids)
        self.assertEqual(len(sixth_slice_seed_ids), 6)
        sixth_slice_seed_set = set(sixth_slice_seed_ids)
        self.assertEqual(len(sixth_slice_seed_set), 6)
        self.assertTrue(sixth_slice_seed_set.issubset(inventory_seed_set))
        self.assertTrue(sixth_slice_seed_set.isdisjoint(prior_339_checkpoint_set))

        prior_345_checkpoint_set = prior_339_checkpoint_set | sixth_slice_seed_set
        self.assertEqual(len(prior_345_checkpoint_set), 345)

        seventh_slice_records = continuation_records[31:36]
        self.assertEqual(len(seventh_slice_records), 5)
        expected_seventh_slice_seed_ids = [
            "API-AUTH-POST-AUTH-DELIVER-TELEGRAM-OTP-INTERNAL-L297",
            "API-AUTH-POST-SESSIONS-INTERNAL-SESSION-AUTHORITY-CHECK-L539",
            "API-AUTH-POST-SESSIONS-LOGOUT-ALL-SESSIONS-L1146",
            "API-AUTH-GET-SESSIONS-GET-PENDING-LOGIN-REQUESTS-L1191",
            "API-AUTH-GET-SESSIONS-POLL-LOGIN-REQUEST-STATUS-L1311",
        ]
        seventh_slice_seed_ids = []
        for record in seventh_slice_records:
            seventh_slice_seed_ids.extend(record.get("seed_ids", []))
        self.assertEqual(seventh_slice_seed_ids, expected_seventh_slice_seed_ids)
        self.assertEqual(len(seventh_slice_seed_ids), 5)
        seventh_slice_seed_set = set(seventh_slice_seed_ids)
        self.assertEqual(len(seventh_slice_seed_set), 5)
        self.assertTrue(seventh_slice_seed_set.issubset(inventory_seed_set))
        self.assertTrue(seventh_slice_seed_set.isdisjoint(prior_345_checkpoint_set))

        prior_350_checkpoint_set = prior_345_checkpoint_set | seventh_slice_seed_set
        self.assertEqual(len(prior_350_checkpoint_set), 350)

        eighth_slice_records = continuation_records[36:41]
        self.assertEqual(len(eighth_slice_records), 5)
        expected_eighth_slice_seed_ids = [
            "API-OPS-GET-MAIN-GET-PUBLIC-CONFIG-L531",
            "API-OPS-GET-MAIN-ROOT-L614",
            "API-MSG-POST-CHAT-SEND-TYPING-SIGNAL-L918",
            "API-IDENTITY-GET-INVITATIONS-LOOKUP-INVITATION-L382",
            "API-IDENTITY-GET-INVITATIONS-VALIDATE-INVITATION-L437",
            "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-BLOCK-SETTINGS-L1607",
            "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-ADMIN-SET-MAX-BLOCK-L1698",
        ]
        eighth_slice_seed_ids = []
        for record in eighth_slice_records:
            eighth_slice_seed_ids.extend(record.get("seed_ids", []))
        self.assertEqual(eighth_slice_seed_ids, expected_eighth_slice_seed_ids)
        self.assertEqual(len(eighth_slice_seed_ids), 7)
        eighth_slice_seed_set = set(eighth_slice_seed_ids)
        self.assertEqual(len(eighth_slice_seed_set), 7)
        self.assertTrue(eighth_slice_seed_set.issubset(inventory_seed_set))

        expected_eighth_slice_new_seed_ids = [
            "API-OPS-GET-MAIN-GET-PUBLIC-CONFIG-L531",
            "API-OPS-GET-MAIN-ROOT-L614",
            "API-MSG-POST-CHAT-SEND-TYPING-SIGNAL-L918",
            "API-IDENTITY-GET-INVITATIONS-LOOKUP-INVITATION-L382",
            "API-IDENTITY-GET-INVITATIONS-VALIDATE-INVITATION-L437",
        ]
        expected_eighth_slice_baseline_seed_ids = [
            "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-BLOCK-SETTINGS-L1607",
            "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-ADMIN-SET-MAX-BLOCK-L1698",
        ]
        eighth_slice_new_set = set(expected_eighth_slice_new_seed_ids)
        eighth_slice_baseline_set = set(expected_eighth_slice_baseline_seed_ids)
        self.assertEqual(eighth_slice_new_set | eighth_slice_baseline_set, eighth_slice_seed_set)
        self.assertTrue(eighth_slice_new_set.isdisjoint(eighth_slice_baseline_set))

        # Prove five new IDs are canonical inventory members absent from all prior evidence
        self.assertTrue(eighth_slice_new_set.issubset(inventory_seed_set))
        self.assertTrue(eighth_slice_new_set.isdisjoint(prior_350_checkpoint_set))
        self.assertTrue(eighth_slice_new_set.isdisjoint(baseline_seed_set))

        # Prove two block-settings IDs are canonical baseline members that do not increase unique coverage
        self.assertTrue(eighth_slice_baseline_set.issubset(inventory_seed_set))
        self.assertTrue(eighth_slice_baseline_set.issubset(baseline_seed_set))
        self.assertTrue(eighth_slice_baseline_set.issubset(prior_350_checkpoint_set))

        prior_355_checkpoint_set = prior_350_checkpoint_set | eighth_slice_seed_set
        self.assertEqual(len(prior_355_checkpoint_set), 355)

        ninth_slice_records = continuation_records[41:46]
        self.assertEqual(len(ninth_slice_records), 5)
        expected_ninth_slice_seed_id = "API-IDENTITY-GET-USERS-PUBLIC-READ-PUBLIC-USER-L548"
        ninth_slice_seed_ids = []
        for record in ninth_slice_records:
            ninth_slice_seed_ids.extend(record.get("seed_ids", []))
        self.assertEqual(ninth_slice_seed_ids, [expected_ninth_slice_seed_id] * 5)
        self.assertEqual(len(ninth_slice_seed_ids), 5)
        ninth_slice_seed_set = set(ninth_slice_seed_ids)
        self.assertEqual(len(ninth_slice_seed_set), 1)

        # Prove ninth slice's sole canonical seed is an inventory member disjoint from both baseline and prior evidence
        self.assertTrue(ninth_slice_seed_set.issubset(inventory_seed_set))
        self.assertTrue(ninth_slice_seed_set.isdisjoint(prior_355_checkpoint_set))
        self.assertTrue(ninth_slice_seed_set.isdisjoint(baseline_seed_set))

        prior_356_checkpoint_set = prior_355_checkpoint_set | ninth_slice_seed_set
        self.assertEqual(len(prior_356_checkpoint_set), 356)

        tenth_slice_records = continuation_records[46:51]
        self.assertEqual(len(tenth_slice_records), 5)
        expected_tenth_slice_seed_id = "API-IDENTITY-GET-USERS-PUBLIC-READ-PUBLIC-USER-L548"
        tenth_slice_seed_ids = []
        for record in tenth_slice_records:
            tenth_slice_seed_ids.extend(record.get("seed_ids", []))
        self.assertEqual(tenth_slice_seed_ids, [expected_tenth_slice_seed_id] * 5)
        self.assertEqual(len(tenth_slice_seed_ids), 5)
        tenth_slice_seed_set = set(tenth_slice_seed_ids)
        self.assertEqual(len(tenth_slice_seed_set), 1)

        # Prove tenth slice's sole seed is already contained in the prior 356-evidenced checkpoint and does not double count
        self.assertTrue(tenth_slice_seed_set.issubset(inventory_seed_set))
        self.assertTrue(tenth_slice_seed_set.issubset(prior_356_checkpoint_set))
        self.assertTrue(tenth_slice_seed_set.isdisjoint(baseline_seed_set))

        total_evidenced_set = prior_356_checkpoint_set | tenth_slice_seed_set
        self.assertEqual(len(total_evidenced_set), 356)

        continuation_2_file_path = repo_root / manifest["evidence_sources"]["continuation_2_path"]
        self.assertTrue(
            continuation_2_file_path.is_file(),
            f"Continuation 2 evidence file not found at {continuation_2_file_path}",
        )
        continuation_2_bytes = continuation_2_file_path.read_bytes()
        continuation_2_data = json.loads(continuation_2_bytes.decode("utf-8"))

        self.assertEqual(continuation_2_data.get("stage"), "P1-00b")

        predecessor_binding = continuation_2_data["predecessor_inventory"]
        self.assertEqual(
            predecessor_binding["path"],
            manifest["evidence_sources"]["continuation_path"],
        )
        self.assertEqual(
            predecessor_binding["sha256"],
            hashlib.sha256(continuation_bytes).hexdigest(),
        )
        self.assertEqual(
            predecessor_binding["last_commit"],
            "2852e5a8f0a4aca4758103e9eae57ec4eaca9107",
        )

        continuation_2_records = continuation_2_data.get("records", [])
        self.assertEqual(len(continuation_2_records), 5)

        continuation_2_seed_ids = []
        continuation_2_selectors = []
        for record in continuation_2_records:
            continuation_2_seed_ids.extend(record.get("seed_ids", []))
            continuation_2_selectors.extend(record.get("test_selectors", []))
        self.assertEqual(len(continuation_2_seed_ids), 6)
        self.assertEqual(len(continuation_2_selectors), 6)

        expected_continuation_2_unique_seed_ids = {
            "API-IDENTITY-GET-USERS-PUBLIC-READ-PUBLIC-USER-L548",
            "API-IDENTITY-GET-USERS-PUBLIC-SEARCH-PUBLIC-USERS-L428",
            "API-IDENTITY-GET-USERS-PUBLIC-LIST-PROJECT-USERS-DIRECTORY-L512",
        }
        continuation_2_seed_set = set(continuation_2_seed_ids)
        self.assertEqual(continuation_2_seed_set, expected_continuation_2_unique_seed_ids)
        self.assertEqual(len(continuation_2_seed_set), 3)

        # Prove those IDs are already in the prior 356-evidenced set
        self.assertTrue(continuation_2_seed_set.issubset(total_evidenced_set))

        flattened_recent_ids = (
            prior_recent_ids
            + second_five_seed_ids
            + third_five_seed_ids
            + fourth_five_seed_ids
            + fifth_five_seed_ids
            + sixth_slice_seed_ids
            + seventh_slice_seed_ids
            + eighth_slice_seed_ids
            + ninth_slice_seed_ids
            + tenth_slice_seed_ids
            + continuation_2_seed_ids
        )
        recent_seed_set = set(flattened_recent_ids)
        self.assertTrue(recent_seed_set.issubset(inventory_seed_set))
        new_beyond_baseline = recent_seed_set - baseline_seed_set
        self.assertEqual(len(new_beyond_baseline), 221)

        prior_356_evidenced_set = total_evidenced_set | continuation_2_seed_set
        self.assertEqual(len(prior_356_evidenced_set), 356)

        continuation_3_file_path = repo_root / manifest["evidence_sources"]["continuation_3_path"]
        self.assertTrue(
            continuation_3_file_path.is_file(),
            f"Continuation 3 evidence file not found at {continuation_3_file_path}",
        )
        continuation_3_bytes = continuation_3_file_path.read_bytes()
        continuation_3_data = json.loads(continuation_3_bytes.decode("utf-8"))

        self.assertEqual(continuation_3_data.get("stage"), "P1-00b")

        continuation_3_predecessor = continuation_3_data["predecessor_inventory"]
        self.assertEqual(
            continuation_3_predecessor["path"],
            manifest["evidence_sources"]["continuation_2_path"],
        )
        self.assertEqual(
            continuation_3_predecessor["sha256"],
            hashlib.sha256(continuation_2_bytes).hexdigest(),
        )
        self.assertEqual(
            continuation_3_predecessor["last_commit"],
            "3de519e1351ebda576eafa4c27649acbb2d03f37",
        )

        continuation_3_records = continuation_3_data.get("records", [])
        self.assertEqual(len(continuation_3_records), 18)

        # Historical assertion on prior 16 records
        prior_16_records = continuation_3_records[:16]
        self.assertEqual(len(prior_16_records), 16)
        prior_16_seed_ids = []
        prior_16_selectors = []
        for record in prior_16_records:
            prior_16_seed_ids.extend(record.get("seed_ids", []))
            prior_16_selectors.extend(record.get("test_selectors", []))
        self.assertEqual(len(prior_16_seed_ids), 22)
        self.assertEqual(len(prior_16_selectors), 32)

        expected_prior_16_unique_seed_ids = {
            "API-IDENTITY-GET-INVITATIONS-LIST-PENDING-INVITATIONS-L266",
            "API-IDENTITY-DELETE-INVITATIONS-DELETE-PENDING-INVITATION-L294",
            "API-NOTIFY-DELETE-NOTIFICATIONS-DISABLE-PUSH-SUBSCRIPTION-L178",
            "API-NOTIFY-POST-NOTIFICATIONS-UPSERT-PUSH-SUBSCRIPTION-L138",
            "API-ADMIN-GET-ADMIN-MESSAGES-GET-CURRENT-MARKET-MESSAGE-L180",
            "API-ADMIN-GET-ADMIN-MESSAGES-GET-MARKET-MESSAGE-HISTORY-L194",
            "API-ADMIN-DELETE-ADMIN-MESSAGES-CLEAR-CURRENT-MARKET-MESSAGE-L235",
            "API-MSG-POST-CHAT-SEND-DIRECT-ACTIVITY-SIGNAL-L939",
            "API-MSG-POST-CHAT-SEND-ROOM-ACTIVITY-SIGNAL-L962",
            "API-AUTH-POST-SESSIONS-VERIFY-MY-SESSION-L1031",
            "API-NOTIFY-GET-NOTIFICATIONS-GET-PUSH-PUBLIC-KEY-L133",
            "BOT-TG-CALLBACK-QUERY-TELEGRAM-PUBLISHER-CHANNEL-CALLBACKS-PUBLIC-TRADE-CALLBACK-L46",
            "API-MSG-GET-CHAT-GET-CHAT-FILE-L2773",
            "API-NOTIFY-POST-NOTIFICATIONS-SEND-TEST-PUSH-NOTIFICATION-L201",
            "API-MSG-GET-REALTIME-SSE-STREAM-L575",
            "API-OPS-GET-MAIN-SERVE-FRONTEND-L587",
            "API-REQUEST-GET-TRADES-GET-PENDING-OWNER-OVERTIME-REQUESTS-L4891",
            "API-REQUEST-GET-TRADES-GET-PENDING-REQUESTER-OVERTIME-REQUESTS-L4927",
            "API-REQUEST-GET-TRADES-GET-OVERTIME-REQUEST-L4958",
            "API-REQUEST-POST-TRADES-REJECT-OVERTIME-REQUEST-L5091",
            "API-REQUEST-POST-TRADES-CANCEL-OVERTIME-REQUEST-L5129",
            "BOT-IDENTITY-CALLBACK-QUERY-BLOCK-MANAGE-START-SEARCH-L282",
        }
        prior_16_seed_set = set(prior_16_seed_ids)
        self.assertEqual(prior_16_seed_set, expected_prior_16_unique_seed_ids)
        self.assertEqual(len(prior_16_seed_set), 22)
        self.assertTrue(prior_16_seed_set.issubset(inventory_seed_set))

        continuation_3_seed_ids = []
        continuation_3_selectors = []
        for record in continuation_3_records:
            continuation_3_seed_ids.extend(record.get("seed_ids", []))
            continuation_3_selectors.extend(record.get("test_selectors", []))
        self.assertEqual(len(continuation_3_seed_ids), 24)
        self.assertEqual(len(continuation_3_selectors), 35)

        two_new_canonical_seed_ids = {
            "API-OFFER-POST-OFFERS-PARSE-OFFER-TEXT-L3184",
            "API-OPS-GET-MAIN-GET-METRICS-L540",
        }
        self.assertEqual(len(two_new_canonical_seed_ids), 2)
        self.assertTrue(two_new_canonical_seed_ids.issubset(inventory_seed_set))

        expected_continuation_3_unique_seed_ids = expected_prior_16_unique_seed_ids | two_new_canonical_seed_ids
        continuation_3_seed_set = set(continuation_3_seed_ids)
        self.assertEqual(continuation_3_seed_set, expected_continuation_3_unique_seed_ids)
        self.assertEqual(len(continuation_3_seed_set), 24)
        self.assertTrue(continuation_3_seed_set.issubset(inventory_seed_set))

        continuation_3_new_seed_ids = {
            "API-IDENTITY-GET-INVITATIONS-LIST-PENDING-INVITATIONS-L266",
            "API-IDENTITY-DELETE-INVITATIONS-DELETE-PENDING-INVITATION-L294",
            "API-NOTIFY-DELETE-NOTIFICATIONS-DISABLE-PUSH-SUBSCRIPTION-L178",
            "API-NOTIFY-POST-NOTIFICATIONS-UPSERT-PUSH-SUBSCRIPTION-L138",
            "API-MSG-POST-CHAT-SEND-DIRECT-ACTIVITY-SIGNAL-L939",
            "API-MSG-POST-CHAT-SEND-ROOM-ACTIVITY-SIGNAL-L962",
            "API-AUTH-POST-SESSIONS-VERIFY-MY-SESSION-L1031",
            "API-NOTIFY-GET-NOTIFICATIONS-GET-PUSH-PUBLIC-KEY-L133",
            "BOT-TG-CALLBACK-QUERY-TELEGRAM-PUBLISHER-CHANNEL-CALLBACKS-PUBLIC-TRADE-CALLBACK-L46",
            "API-MSG-GET-CHAT-GET-CHAT-FILE-L2773",
            "API-NOTIFY-POST-NOTIFICATIONS-SEND-TEST-PUSH-NOTIFICATION-L201",
            "API-MSG-GET-REALTIME-SSE-STREAM-L575",
            "API-OPS-GET-MAIN-SERVE-FRONTEND-L587",
            "API-REQUEST-GET-TRADES-GET-PENDING-OWNER-OVERTIME-REQUESTS-L4891",
            "API-REQUEST-GET-TRADES-GET-PENDING-REQUESTER-OVERTIME-REQUESTS-L4927",
            "API-REQUEST-GET-TRADES-GET-OVERTIME-REQUEST-L4958",
            "API-REQUEST-POST-TRADES-REJECT-OVERTIME-REQUEST-L5091",
            "API-REQUEST-POST-TRADES-CANCEL-OVERTIME-REQUEST-L5129",
            "BOT-IDENTITY-CALLBACK-QUERY-BLOCK-MANAGE-START-SEARCH-L282",
            "API-OFFER-POST-OFFERS-PARSE-OFFER-TEXT-L3184",
            "API-OPS-GET-MAIN-GET-METRICS-L540",
        }
        continuation_3_baseline_seed_ids = {
            "API-ADMIN-GET-ADMIN-MESSAGES-GET-CURRENT-MARKET-MESSAGE-L180",
            "API-ADMIN-GET-ADMIN-MESSAGES-GET-MARKET-MESSAGE-HISTORY-L194",
            "API-ADMIN-DELETE-ADMIN-MESSAGES-CLEAR-CURRENT-MARKET-MESSAGE-L235",
        }
        self.assertEqual(continuation_3_seed_set - prior_356_evidenced_set, continuation_3_new_seed_ids)
        self.assertEqual(len(continuation_3_seed_set - prior_356_evidenced_set), 21)
        self.assertTrue(continuation_3_baseline_seed_ids.issubset(baseline_seed_set))
        self.assertTrue(continuation_3_baseline_seed_ids.issubset(prior_356_evidenced_set))

        six_new_canonical_seed_ids = {
            "API-OPS-GET-MAIN-SERVE-FRONTEND-L587",
            "API-REQUEST-GET-TRADES-GET-PENDING-OWNER-OVERTIME-REQUESTS-L4891",
            "API-REQUEST-GET-TRADES-GET-PENDING-REQUESTER-OVERTIME-REQUESTS-L4927",
            "API-REQUEST-GET-TRADES-GET-OVERTIME-REQUEST-L4958",
            "API-REQUEST-POST-TRADES-REJECT-OVERTIME-REQUEST-L5091",
            "API-REQUEST-POST-TRADES-CANCEL-OVERTIME-REQUEST-L5129",
        }
        added_repair_seed_id = "BOT-IDENTITY-CALLBACK-QUERY-BLOCK-MANAGE-START-SEARCH-L282"
        added_repair_seed_set = {added_repair_seed_id}

        prior_368_evidenced_set = prior_356_evidenced_set | (
            (continuation_3_seed_set - two_new_canonical_seed_ids) - six_new_canonical_seed_ids - added_repair_seed_set
        )
        self.assertEqual(len(prior_368_evidenced_set), 368)
        self.assertEqual(len(six_new_canonical_seed_ids), 6)
        self.assertTrue(six_new_canonical_seed_ids.issubset(inventory_seed_set))
        self.assertTrue(six_new_canonical_seed_ids.isdisjoint(prior_368_evidenced_set))
        self.assertTrue(six_new_canonical_seed_ids.isdisjoint(baseline_seed_set))

        prior_374_evidenced_set = prior_368_evidenced_set | six_new_canonical_seed_ids
        self.assertEqual(len(prior_374_evidenced_set), 374)
        self.assertEqual(len(added_repair_seed_set), 1)
        self.assertTrue(added_repair_seed_set.issubset(inventory_seed_set))
        self.assertTrue(added_repair_seed_set.isdisjoint(prior_374_evidenced_set))
        self.assertTrue(added_repair_seed_set.isdisjoint(baseline_seed_set))

        # Historical 375/90 checkpoint assertions preserved
        prior_375_evidenced_set = prior_374_evidenced_set | added_repair_seed_set
        self.assertEqual(len(prior_375_evidenced_set), 375)
        prior_remaining_seed_set = inventory_seed_set - prior_375_evidenced_set
        self.assertEqual(len(prior_remaining_seed_set), 90)

        # Explicitly prove two new canonical seeds are inventory members, disjoint from prior 375 seeds, and disjoint from each other
        self.assertTrue(two_new_canonical_seed_ids.issubset(inventory_seed_set))
        self.assertTrue(two_new_canonical_seed_ids.isdisjoint(prior_375_evidenced_set))
        self.assertTrue(two_new_canonical_seed_ids.isdisjoint(baseline_seed_set))
        self.assertEqual(len(two_new_canonical_seed_ids), 2)

        continuation_4_file_path = repo_root / manifest["evidence_sources"]["continuation_4_path"]
        self.assertTrue(
            continuation_4_file_path.is_file(),
            f"Continuation 4 evidence file not found at {continuation_4_file_path}",
        )
        continuation_4_bytes = continuation_4_file_path.read_bytes()
        continuation_4_data = json.loads(continuation_4_bytes.decode("utf-8"))

        self.assertEqual(continuation_4_data.get("stage"), "P1-00b")

        continuation_4_predecessor = continuation_4_data["predecessor_inventory"]
        self.assertEqual(
            continuation_4_predecessor["path"],
            manifest["evidence_sources"]["continuation_3_path"],
        )
        self.assertEqual(
            continuation_4_predecessor["sha256"],
            hashlib.sha256(continuation_3_bytes).hexdigest(),
        )
        self.assertEqual(
            continuation_4_predecessor["last_commit"],
            "c7cc52cf61e5770348cbaa79ef8677804c62ee38",
        )

        continuation_4_records = continuation_4_data.get("records", [])
        self.assertEqual(len(continuation_4_records), 7)

        continuation_4_seed_ids = []
        continuation_4_selectors = []
        for record in continuation_4_records:
            continuation_4_seed_ids.extend(record.get("seed_ids", []))
            continuation_4_selectors.extend(record.get("test_selectors", []))
        self.assertEqual(len(continuation_4_seed_ids), 9)
        self.assertEqual(len(continuation_4_selectors), 17)

        expected_continuation_4_selectors = [
            "tests/test_p1_00b_characterization_admin_invitation_inputs_batch.py::AdminInvitationInputsCharacterizationTests::test_account_name_valid_queue_suppresses_stale_delete_and_advances",
            "tests/test_p1_00b_characterization_admin_invitation_inputs_batch.py::AdminInvitationInputsCharacterizationTests::test_mobile_valid_queue_suppresses_stale_delete_and_advances",
            "tests/test_p1_00b_characterization_admin_invitation_inputs_batch.py::AdminInvitationInputsCharacterizationTests::test_account_name_invalid_queue_suppresses_stale_prompt_delete",
            "tests/test_p1_00b_characterization_admin_invitation_inputs_batch.py::AdminInvitationInputsCharacterizationTests::test_mobile_invalid_queue_suppresses_stale_prompt_delete",
            "tests/test_p1_00b_characterization_admin_invitation_cancel_queue.py::AdminInvitationCancelQueueCharacterizationTests::test_cancel_queue_without_user_clears_state_suppresses_deletes_and_returns_fallback_panel",
            "tests/test_p1_00b_characterization_admin_invitation_cancel_queue.py::AdminInvitationCancelQueueCharacterizationTests::test_cancel_queue_with_user_uses_navigation_panel_and_exact_callback_response",
            "tests/test_p1_00b_characterization_chat_search_messages_scoped.py::CharacterizeChatSearchMessagesScopedTests::test_allowed_customer_target_search_uses_scoped_query_and_preserves_order",
            "tests/test_p1_00b_characterization_chat_search_messages_scoped.py::CharacterizeChatSearchMessagesScopedTests::test_disallowed_customer_target_returns_404_before_query",
            "tests/test_p1_00b_characterization_chat_poll_nonempty.py::ChatPollNonemptyCharacterizationTests::test_mixed_visible_direct_and_room_rows_aggregate_exact_response",
            "tests/test_p1_00b_characterization_chat_poll_nonempty.py::ChatPollNonemptyCharacterizationTests::test_zero_unread_attribute_row_retains_muted_ids_and_room_mentions",
            "tests/test_p1_00b_characterization_list_active_sessions.py::ListActiveSessionsCharacterizationTests::test_list_active_sessions_valid_bearer_token_marks_current_session",
            "tests/test_p1_00b_characterization_list_active_sessions.py::ListActiveSessionsCharacterizationTests::test_list_active_sessions_missing_authorization_header",
            "tests/test_p1_00b_characterization_list_active_sessions.py::ListActiveSessionsCharacterizationTests::test_list_active_sessions_undecodable_bearer_token",
            "tests/test_p1_00b_characterization_get_pending_requests_session_gates.py::CharacterizationGetPendingRequestsSessionGatesTests::test_get_pending_requests_decode_failure_falls_back_to_query",
            "tests/test_p1_00b_characterization_get_pending_requests_session_gates.py::CharacterizationGetPendingRequestsSessionGatesTests::test_get_pending_requests_missing_caller_session_short_circuits",
            "tests/test_p1_00b_characterization_get_pending_requests_session_gates.py::CharacterizationGetPendingRequestsSessionGatesTests::test_get_pending_requests_no_authorization_header_queries_pending",
            "tests/test_p1_00b_characterization_get_pending_requests_session_gates.py::CharacterizationGetPendingRequestsSessionGatesTests::test_get_pending_requests_malformed_token_sid_raises_value_error_before_db",
        ]
        self.assertEqual(continuation_4_selectors, expected_continuation_4_selectors)

        expected_continuation_4_seed_ids = [
            "BOT-ADMIN-MESSAGE-ADMIN-PROCESS-INVITATION-ACCOUNT-NAME-L183",
            "BOT-ADMIN-MESSAGE-ADMIN-PROCESS-INVITATION-MOBILE-L230",
            "BOT-ADMIN-MESSAGE-ADMIN-PROCESS-INVITATION-ACCOUNT-NAME-L183",
            "BOT-ADMIN-MESSAGE-ADMIN-PROCESS-INVITATION-MOBILE-L230",
            "BOT-ADMIN-CALLBACK-QUERY-ADMIN-CANCEL-INVITATION-CREATION-L402",
            "API-MSG-GET-CHAT-SEARCH-MESSAGES-L800",
            "API-MSG-GET-CHAT-POLL-MESSAGES-L2225",
            "API-AUTH-GET-SESSIONS-LIST-ACTIVE-SESSIONS-L1046",
            "API-AUTH-GET-SESSIONS-GET-PENDING-REQUESTS-L619",
        ]
        self.assertEqual(continuation_4_seed_ids, expected_continuation_4_seed_ids)
        continuation_4_seed_set = set(continuation_4_seed_ids)
        self.assertEqual(len(continuation_4_seed_set), 7)
        self.assertTrue(continuation_4_seed_set.issubset(inventory_seed_set))

        first_three_seed_ids = []
        for r in continuation_4_records[:3]:
            first_three_seed_ids.extend(r.get("seed_ids", []))
        first_three_seed_set = set(first_three_seed_ids)
        self.assertEqual(len(first_three_seed_set), 3)

        # Cancellation seed is in baseline seed set and absent from prior recent evidence
        cancellation_seed_id = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-CANCEL-INVITATION-CREATION-L402"
        self.assertIn(cancellation_seed_id, baseline_seed_set)
        self.assertNotIn(cancellation_seed_id, prior_recent_seed_set)

        # Invitation-input seeds are already present in prior recent evidence
        invitation_input_seed_ids = {
            "BOT-ADMIN-MESSAGE-ADMIN-PROCESS-INVITATION-ACCOUNT-NAME-L183",
            "BOT-ADMIN-MESSAGE-ADMIN-PROCESS-INVITATION-MOBILE-L230",
        }
        self.assertTrue(invitation_input_seed_ids.issubset(prior_recent_seed_set))

        # First three records add zero net new seeds to prior 377 evidenced coverage
        prior_377_evidenced_set = prior_356_evidenced_set | continuation_3_seed_set
        self.assertEqual(len(prior_377_evidenced_set), 377)
        self.assertTrue(first_three_seed_set.issubset(prior_377_evidenced_set))
        self.assertEqual(len(first_three_seed_set - prior_377_evidenced_set), 0)
        self.assertEqual(len(first_three_seed_set - (baseline_seed_set | prior_recent_seed_set)), 0)

        # Exact checks for chat search record (record 3)
        chat_search_record = continuation_4_records[3]
        expected_chat_search_path = "tests/test_p1_00b_characterization_chat_search_messages_scoped.py"
        expected_chat_search_sha = "8ac7d8e5e7012ccd4a65272d70cf69984922ecbbd033de438804e579fc5aee36"
        expected_chat_search_commit = "6b9ed96828a3fbf5e85bde014c345dbdd652abd4"
        expected_chat_search_seed = "API-MSG-GET-CHAT-SEARCH-MESSAGES-L800"
        expected_chat_search_selectors = [
            "tests/test_p1_00b_characterization_chat_search_messages_scoped.py::CharacterizeChatSearchMessagesScopedTests::test_allowed_customer_target_search_uses_scoped_query_and_preserves_order",
            "tests/test_p1_00b_characterization_chat_search_messages_scoped.py::CharacterizeChatSearchMessagesScopedTests::test_disallowed_customer_target_returns_404_before_query",
        ]
        self.assertEqual(chat_search_record["path"], expected_chat_search_path)
        self.assertEqual(chat_search_record["sha256"], expected_chat_search_sha)
        self.assertEqual(chat_search_record["last_commit"], expected_chat_search_commit)
        self.assertEqual(chat_search_record["seed_ids"], [expected_chat_search_seed])
        self.assertEqual(chat_search_record["test_selectors"], expected_chat_search_selectors)

        chat_search_file_path = repo_root / expected_chat_search_path
        self.assertTrue(chat_search_file_path.is_file(), f"Chat search test file not found at {chat_search_file_path}")
        chat_search_bytes = chat_search_file_path.read_bytes()
        self.assertEqual(hashlib.sha256(chat_search_bytes).hexdigest(), expected_chat_search_sha)
        chat_search_text = chat_search_bytes.decode("utf-8")
        for selector in expected_chat_search_selectors:
            method_name = selector.split("::")[-1]
            self.assertIn(f"def {method_name}(", chat_search_text)

        # Exact checks for chat poll record (record 4)
        chat_poll_record = continuation_4_records[4]
        expected_chat_poll_path = "tests/test_p1_00b_characterization_chat_poll_nonempty.py"
        expected_chat_poll_sha = "4c984d1bf37b52320063c41850c486c9ace353ba5a0ea6b15629386a7a7c5127"
        expected_chat_poll_commit = "7f87a32c7eb08e01472e4d78329c7592e3e8c11a"
        expected_chat_poll_seed = "API-MSG-GET-CHAT-POLL-MESSAGES-L2225"
        expected_chat_poll_selectors = [
            "tests/test_p1_00b_characterization_chat_poll_nonempty.py::ChatPollNonemptyCharacterizationTests::test_mixed_visible_direct_and_room_rows_aggregate_exact_response",
            "tests/test_p1_00b_characterization_chat_poll_nonempty.py::ChatPollNonemptyCharacterizationTests::test_zero_unread_attribute_row_retains_muted_ids_and_room_mentions",
        ]
        self.assertEqual(chat_poll_record["path"], expected_chat_poll_path)
        self.assertEqual(chat_poll_record["sha256"], expected_chat_poll_sha)
        self.assertEqual(chat_poll_record["last_commit"], expected_chat_poll_commit)
        self.assertEqual(chat_poll_record["seed_ids"], [expected_chat_poll_seed])
        self.assertEqual(chat_poll_record["test_selectors"], expected_chat_poll_selectors)

        chat_poll_file_path = repo_root / expected_chat_poll_path
        self.assertTrue(chat_poll_file_path.is_file(), f"Chat poll test file not found at {chat_poll_file_path}")
        chat_poll_bytes = chat_poll_file_path.read_bytes()
        self.assertEqual(hashlib.sha256(chat_poll_bytes).hexdigest(), expected_chat_poll_sha)
        chat_poll_text = chat_poll_bytes.decode("utf-8")
        for selector in expected_chat_poll_selectors:
            method_name = selector.split("::")[-1]
            self.assertIn(f"def {method_name}(", chat_poll_text)

        # Prove these two seeds are members of the 465-seed source inventory, absent from prior 377, disjoint from each other and from baseline
        continuation_4_new_seeds = {expected_chat_search_seed, expected_chat_poll_seed}
        self.assertEqual(len(continuation_4_new_seeds), 2)
        self.assertTrue(continuation_4_new_seeds.issubset(inventory_seed_set))
        self.assertTrue(continuation_4_new_seeds.isdisjoint(prior_377_evidenced_set))
        self.assertTrue(continuation_4_new_seeds.isdisjoint(baseline_seed_set))
        self.assertNotEqual(expected_chat_search_seed, expected_chat_poll_seed)

        # Exact checks for list active sessions record (record 5)
        list_active_sessions_record = continuation_4_records[5]
        expected_list_active_sessions_path = (
            "tests/test_p1_00b_characterization_list_active_sessions.py"
        )
        expected_list_active_sessions_sha = (
            "1dc4d947424977a1aa0c522e55a8f22490de61a4d24f97fe77a0fbf2c18a4fda"
        )
        expected_list_active_sessions_commit = "48865b90ac007a0e60f6b97f9056730a204cdcd6"
        expected_list_active_sessions_seed = (
            "API-AUTH-GET-SESSIONS-LIST-ACTIVE-SESSIONS-L1046"
        )
        expected_list_active_sessions_selectors = [
            "tests/test_p1_00b_characterization_list_active_sessions.py::ListActiveSessionsCharacterizationTests::test_list_active_sessions_valid_bearer_token_marks_current_session",
            "tests/test_p1_00b_characterization_list_active_sessions.py::ListActiveSessionsCharacterizationTests::test_list_active_sessions_missing_authorization_header",
            "tests/test_p1_00b_characterization_list_active_sessions.py::ListActiveSessionsCharacterizationTests::test_list_active_sessions_undecodable_bearer_token",
        ]
        self.assertEqual(
            list_active_sessions_record["path"], expected_list_active_sessions_path
        )
        self.assertEqual(
            list_active_sessions_record["sha256"], expected_list_active_sessions_sha
        )
        self.assertEqual(
            list_active_sessions_record["last_commit"],
            expected_list_active_sessions_commit,
        )
        self.assertEqual(
            list_active_sessions_record["seed_ids"], [expected_list_active_sessions_seed]
        )
        self.assertEqual(
            list_active_sessions_record["test_selectors"],
            expected_list_active_sessions_selectors,
        )

        list_active_sessions_file_path = repo_root / expected_list_active_sessions_path
        self.assertTrue(
            list_active_sessions_file_path.is_file(),
            f"List active sessions test file not found at {list_active_sessions_file_path}",
        )
        list_active_sessions_bytes = list_active_sessions_file_path.read_bytes()
        self.assertEqual(
            hashlib.sha256(list_active_sessions_bytes).hexdigest(),
            expected_list_active_sessions_sha,
        )
        list_active_sessions_text = list_active_sessions_bytes.decode("utf-8")
        for selector in expected_list_active_sessions_selectors:
            method_name = selector.split("::")[-1]
            self.assertIn(f"def {method_name}(", list_active_sessions_text)

        prior_379_evidenced_set = prior_377_evidenced_set | continuation_4_new_seeds
        self.assertEqual(len(prior_379_evidenced_set), 379)

        # Prove seed is in the 465 source inventory and absent from all 379 previously evidenced seeds
        self.assertTrue({expected_list_active_sessions_seed}.issubset(inventory_seed_set))
        self.assertTrue({expected_list_active_sessions_seed}.isdisjoint(prior_379_evidenced_set))
        self.assertTrue({expected_list_active_sessions_seed}.isdisjoint(baseline_seed_set))

        # Exact checks for get pending requests session gates record (record 6)
        get_pending_requests_record = continuation_4_records[6]
        expected_get_pending_requests_path = (
            "tests/test_p1_00b_characterization_get_pending_requests_session_gates.py"
        )
        expected_get_pending_requests_sha = (
            "7c980046bac48cce58c31ce11059710a357e4700b277453e3858ee4870451ba7"
        )
        expected_get_pending_requests_commit = (
            "0a10332571afea72348e43f507e8d0f5fbadd4e2"
        )
        expected_get_pending_requests_seed = (
            "API-AUTH-GET-SESSIONS-GET-PENDING-REQUESTS-L619"
        )
        expected_get_pending_requests_selectors = [
            "tests/test_p1_00b_characterization_get_pending_requests_session_gates.py::CharacterizationGetPendingRequestsSessionGatesTests::test_get_pending_requests_decode_failure_falls_back_to_query",
            "tests/test_p1_00b_characterization_get_pending_requests_session_gates.py::CharacterizationGetPendingRequestsSessionGatesTests::test_get_pending_requests_missing_caller_session_short_circuits",
            "tests/test_p1_00b_characterization_get_pending_requests_session_gates.py::CharacterizationGetPendingRequestsSessionGatesTests::test_get_pending_requests_no_authorization_header_queries_pending",
            "tests/test_p1_00b_characterization_get_pending_requests_session_gates.py::CharacterizationGetPendingRequestsSessionGatesTests::test_get_pending_requests_malformed_token_sid_raises_value_error_before_db",
        ]
        self.assertEqual(
            get_pending_requests_record["path"], expected_get_pending_requests_path
        )
        self.assertEqual(
            get_pending_requests_record["sha256"], expected_get_pending_requests_sha
        )
        self.assertEqual(
            get_pending_requests_record["last_commit"],
            expected_get_pending_requests_commit,
        )
        self.assertEqual(
            get_pending_requests_record["seed_ids"], [expected_get_pending_requests_seed]
        )
        self.assertEqual(
            get_pending_requests_record["test_selectors"],
            expected_get_pending_requests_selectors,
        )

        get_pending_requests_file_path = repo_root / expected_get_pending_requests_path
        self.assertTrue(
            get_pending_requests_file_path.is_file(),
            f"Get pending requests test file not found at {get_pending_requests_file_path}",
        )
        get_pending_requests_bytes = get_pending_requests_file_path.read_bytes()
        self.assertEqual(
            hashlib.sha256(get_pending_requests_bytes).hexdigest(),
            expected_get_pending_requests_sha,
        )
        get_pending_requests_text = get_pending_requests_bytes.decode("utf-8")
        for selector in expected_get_pending_requests_selectors:
            method_name = selector.split("::")[-1]
            self.assertIn(f"def {method_name}(", get_pending_requests_text)

        prior_380_evidenced_set = (
            prior_379_evidenced_set | {expected_list_active_sessions_seed}
        )
        self.assertEqual(len(prior_380_evidenced_set), 380)

        # Prove the new seed is in the 465 inventory and absent from all 380 previously evidenced seeds and baseline before union
        self.assertTrue({expected_get_pending_requests_seed}.issubset(inventory_seed_set))
        self.assertTrue({expected_get_pending_requests_seed}.isdisjoint(prior_380_evidenced_set))
        self.assertTrue({expected_get_pending_requests_seed}.isdisjoint(baseline_seed_set))

        continuation_5_file_path = repo_root / manifest["evidence_sources"]["continuation_5_path"]
        self.assertTrue(
            continuation_5_file_path.is_file(),
            f"Continuation 5 evidence file not found at {continuation_5_file_path}",
        )
        continuation_5_bytes = continuation_5_file_path.read_bytes()
        continuation_5_data = json.loads(continuation_5_bytes.decode("utf-8"))

        self.assertEqual(continuation_5_data.get("stage"), "P1-00b")

        continuation_5_predecessor = continuation_5_data["predecessor_inventory"]
        self.assertEqual(
            continuation_5_predecessor["path"],
            manifest["evidence_sources"]["continuation_4_path"],
        )
        self.assertEqual(
            continuation_5_predecessor["sha256"],
            hashlib.sha256(continuation_4_bytes).hexdigest(),
        )
        self.assertEqual(
            continuation_5_predecessor["last_commit"],
            "bcf435472e73b916a9555650f99af948eb257004",
        )

        continuation_5_records = continuation_5_data.get("records", [])
        self.assertEqual(len(continuation_5_records), 2)

        continuation_5_seed_ids = []
        continuation_5_selectors = []
        for record in continuation_5_records:
            continuation_5_seed_ids.extend(record.get("seed_ids", []))
            continuation_5_selectors.extend(record.get("test_selectors", []))
        self.assertEqual(len(continuation_5_seed_ids), 2)
        self.assertEqual(len(continuation_5_selectors), 2)

        expected_continuation_5_seed_ids = [
            "API-AUTH-GET-SESSIONS-GET-SINGLE-SESSION-RECOVERY-STATUS-L749",
            "API-AUTH-GET-SESSIONS-GET-PENDING-SINGLE-SESSION-RECOVERY-PROMPTS-L788",
        ]
        self.assertEqual(continuation_5_seed_ids, expected_continuation_5_seed_ids)
        continuation_5_seed_set = set(continuation_5_seed_ids)
        self.assertEqual(len(continuation_5_seed_set), 2)
        self.assertTrue(continuation_5_seed_set.issubset(inventory_seed_set))

        prior_381_evidenced_set = (
            prior_380_evidenced_set | {expected_get_pending_requests_seed}
        )
        self.assertEqual(len(prior_381_evidenced_set), 381)

        self.assertTrue(continuation_5_seed_set.isdisjoint(prior_381_evidenced_set))
        self.assertTrue(continuation_5_seed_set.isdisjoint(baseline_seed_set))

        all_flattened_recent_ids = (
            flattened_recent_ids
            + continuation_3_seed_ids
            + continuation_4_seed_ids
            + continuation_5_seed_ids
        )
        all_recent_seed_set = set(all_flattened_recent_ids)
        self.assertTrue(all_recent_seed_set.issubset(inventory_seed_set))
        reconciled_new_beyond_baseline = all_recent_seed_set - baseline_seed_set
        self.assertEqual(len(reconciled_new_beyond_baseline), 248)

        final_evidenced_set = (
            prior_356_evidenced_set
            | continuation_3_seed_set
            | continuation_4_seed_set
            | continuation_5_seed_set
        )
        self.assertEqual(len(final_evidenced_set), 383)

        remaining_seed_set = inventory_seed_set - final_evidenced_set
        self.assertEqual(len(remaining_seed_set), 82)
        self.assertEqual(len(final_evidenced_set) + len(remaining_seed_set), 465)
        self.assertEqual(len(baseline_seed_set) + len(reconciled_new_beyond_baseline), 383)

        self.assertEqual(manifest["counts"]["baseline_evidenced_seed_count"], len(baseline_seed_set))
        self.assertEqual(manifest["counts"]["reconciled_new_seed_count"], len(reconciled_new_beyond_baseline))
        self.assertEqual(manifest["counts"]["evidenced_seed_count"], len(final_evidenced_set))
        self.assertEqual(manifest["counts"]["remaining_seed_count"], len(remaining_seed_set))


if __name__ == "__main__":
    unittest.main()
