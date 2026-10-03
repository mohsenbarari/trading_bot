import ast
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

HISTORICAL_SNAPSHOT_REL_PATH = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-seed-coverage-387-snapshot.json"
)
HISTORICAL_SNAPSHOT_SHA256 = (
    "3a2662a8e60068566582744461bb12cfc3788d3ea04857ca23f99028548f25b7"
)


def load_historical_seed_coverage_snapshot(
    snapshot_path: Path | str | None = None,
    *,
    expected_sha256: str = HISTORICAL_SNAPSHOT_SHA256,
) -> dict:
    if snapshot_path is not None:
        target_path = Path(snapshot_path)
    else:
        repo_root = Path(__file__).resolve().parent.parent
        target_path = repo_root / HISTORICAL_SNAPSHOT_REL_PATH

    if not target_path.is_file():
        raise FileNotFoundError(f"Historical snapshot not found at {target_path}")

    raw_bytes = target_path.read_bytes()
    actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"Historical snapshot byte drift detected at {target_path}: "
            f"expected SHA256 {expected_sha256}, got {actual_sha256}"
        )
    return json.loads(raw_bytes.decode("utf-8"))


class SeedHandlerCoverageContractTests(unittest.TestCase):
    def test_canonical_remaining_seed_count_matches_inventory_and_reconciled_records(self):
        repo_root = Path(__file__).resolve().parent.parent

        manifest = load_historical_seed_coverage_snapshot(
            repo_root / HISTORICAL_SNAPSHOT_REL_PATH
        )

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
            "continuation_6_path",
            "continuation_7_path",
            "continuation_8_path",
            "continuation_9_path",
            "continuation_10_path",
            "continuation_11_path",
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
        self.assertEqual(
            manifest["evidence_sources"]["continuation_6_path"],
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-6.json",
        )
        self.assertEqual(
            manifest["evidence_sources"]["continuation_7_path"],
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-7.json",
        )
        self.assertEqual(
            manifest["evidence_sources"]["continuation_8_path"],
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-8.json",
        )
        self.assertEqual(
            manifest["evidence_sources"]["continuation_9_path"],
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-9.json",
        )
        self.assertEqual(
            manifest["evidence_sources"]["continuation_10_path"],
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-10.json",
        )
        self.assertEqual(
            manifest["evidence_sources"]["continuation_11_path"],
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-11.json",
        )

        expected_counts_keys = {
            "baseline_evidenced_seed_count",
            "reconciled_new_seed_count",
            "evidenced_seed_count",
            "remaining_seed_count",
        }
        self.assertEqual(set(manifest["counts"].keys()), expected_counts_keys)
        self.assertEqual(manifest["counts"]["baseline_evidenced_seed_count"], 135)
        self.assertEqual(manifest["counts"]["reconciled_new_seed_count"], 252)
        self.assertEqual(manifest["counts"]["evidenced_seed_count"], 387)
        self.assertEqual(manifest["counts"]["remaining_seed_count"], 78)

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

        prior_383_evidenced_set = prior_381_evidenced_set | continuation_5_seed_set
        self.assertEqual(len(prior_383_evidenced_set), 383)

        continuation_6_file_path = repo_root / manifest["evidence_sources"]["continuation_6_path"]
        self.assertTrue(
            continuation_6_file_path.is_file(),
            f"Continuation 6 evidence file not found at {continuation_6_file_path}",
        )
        continuation_6_bytes = continuation_6_file_path.read_bytes()
        continuation_6_data = json.loads(continuation_6_bytes.decode("utf-8"))

        self.assertEqual(continuation_6_data.get("schema_version"), 1)
        self.assertEqual(continuation_6_data.get("stage"), "P1-00b")

        continuation_6_predecessor = continuation_6_data["predecessor_inventory"]
        self.assertEqual(
            continuation_6_predecessor["path"],
            manifest["evidence_sources"]["continuation_5_path"],
        )
        self.assertEqual(
            continuation_6_predecessor["sha256"],
            hashlib.sha256(continuation_5_bytes).hexdigest(),
        )
        self.assertEqual(
            continuation_6_predecessor["last_commit"],
            "f9c21b3bfc04e3711ad5882ce112f6abdcf582b9",
        )

        continuation_6_records = continuation_6_data.get("records", [])
        self.assertEqual(len(continuation_6_records), 3)

        continuation_6_paths = [r.get("path") for r in continuation_6_records]
        self.assertEqual(len(continuation_6_paths), 3)
        self.assertEqual(len(set(continuation_6_paths)), 3)

        continuation_6_seed_ids = []
        continuation_6_selectors = []
        for record in continuation_6_records:
            continuation_6_seed_ids.extend(record.get("seed_ids", []))
            continuation_6_selectors.extend(record.get("test_selectors", []))
        self.assertEqual(len(continuation_6_seed_ids), 3)
        self.assertEqual(len(continuation_6_selectors), 7)
        self.assertEqual(len(set(continuation_6_selectors)), 7)

        expected_continuation_6_seed_ids = [
            "API-AUTH-POST-SESSIONS-REQUEST-SINGLE-SESSION-RECOVERY-IDENTITY-L807",
            "API-AUTH-POST-SESSIONS-CANCEL-SINGLE-SESSION-RECOVERY-L717",
            "API-AUTH-POST-SESSIONS-CANCEL-SINGLE-SESSION-RECOVERY-L717",
        ]
        self.assertEqual(continuation_6_seed_ids, expected_continuation_6_seed_ids)
        continuation_6_seed_set = set(continuation_6_seed_ids)
        self.assertEqual(len(continuation_6_seed_set), 2)
        self.assertTrue(continuation_6_seed_set.issubset(inventory_seed_set))

        self.assertTrue(continuation_6_seed_set.isdisjoint(prior_383_evidenced_set))
        self.assertTrue(continuation_6_seed_set.isdisjoint(baseline_seed_set))

        all_flattened_recent_ids = (
            flattened_recent_ids
            + continuation_3_seed_ids
            + continuation_4_seed_ids
            + continuation_5_seed_ids
            + continuation_6_seed_ids
        )
        all_recent_seed_set = set(all_flattened_recent_ids)
        self.assertTrue(all_recent_seed_set.issubset(inventory_seed_set))
        reconciled_new_beyond_baseline = all_recent_seed_set - baseline_seed_set
        self.assertEqual(len(reconciled_new_beyond_baseline), 250)

        continuation_6_checkpoint_evidenced_set = (
            prior_356_evidenced_set
            | continuation_3_seed_set
            | continuation_4_seed_set
            | continuation_5_seed_set
            | continuation_6_seed_set
        )
        self.assertEqual(len(continuation_6_checkpoint_evidenced_set), 385)

        continuation_6_remaining_seed_set = (
            inventory_seed_set - continuation_6_checkpoint_evidenced_set
        )
        self.assertEqual(len(continuation_6_remaining_seed_set), 80)
        self.assertEqual(
            len(continuation_6_checkpoint_evidenced_set)
            + len(continuation_6_remaining_seed_set),
            465,
        )
        self.assertEqual(
            len(baseline_seed_set) + len(reconciled_new_beyond_baseline), 385
        )

        continuation_7_file_path = repo_root / manifest["evidence_sources"]["continuation_7_path"]
        self.assertTrue(
            continuation_7_file_path.is_file(),
            f"Continuation 7 evidence file not found at {continuation_7_file_path}",
        )
        continuation_7_bytes = continuation_7_file_path.read_bytes()
        continuation_7_data = json.loads(continuation_7_bytes.decode("utf-8"))

        self.assertEqual(continuation_7_data.get("schema_version"), 1)
        self.assertEqual(continuation_7_data.get("stage"), "P1-00b")

        continuation_7_predecessor = continuation_7_data["predecessor_inventory"]
        self.assertEqual(
            continuation_7_predecessor["path"],
            manifest["evidence_sources"]["continuation_6_path"],
        )
        self.assertEqual(
            continuation_7_predecessor["sha256"],
            hashlib.sha256(continuation_6_bytes).hexdigest(),
        )
        self.assertEqual(
            continuation_7_predecessor["last_commit"],
            "5a0b45537b750020152833b17dd127b5665af798",
        )

        continuation_7_records = continuation_7_data.get("records", [])
        self.assertEqual(len(continuation_7_records), 2)

        continuation_8_file_path = repo_root / manifest["evidence_sources"]["continuation_8_path"]
        self.assertTrue(
            continuation_8_file_path.is_file(),
            f"Continuation 8 evidence file not found at {continuation_8_file_path}",
        )
        continuation_8_bytes = continuation_8_file_path.read_bytes()
        continuation_8_data = json.loads(continuation_8_bytes.decode("utf-8"))

        self.assertEqual(continuation_8_data.get("schema_version"), 1)
        self.assertEqual(continuation_8_data.get("stage"), "P1-00b")

        continuation_8_predecessor = continuation_8_data["predecessor_inventory"]
        self.assertEqual(
            continuation_8_predecessor["path"],
            manifest["evidence_sources"]["continuation_7_path"],
        )
        self.assertEqual(
            continuation_8_predecessor["sha256"],
            hashlib.sha256(continuation_7_bytes).hexdigest(),
        )
        self.assertEqual(
            continuation_8_predecessor["last_commit"],
            "534e6a419f415f4f208b8621bd37b800941636f1",
        )

        continuation_8_records = continuation_8_data.get("records", [])
        self.assertEqual(len(continuation_8_records), 1)

        continuation_9_file_path = repo_root / manifest["evidence_sources"]["continuation_9_path"]
        self.assertTrue(
            continuation_9_file_path.is_file(),
            f"Continuation 9 evidence file not found at {continuation_9_file_path}",
        )
        continuation_9_bytes = continuation_9_file_path.read_bytes()
        continuation_9_data = json.loads(continuation_9_bytes.decode("utf-8"))

        self.assertEqual(continuation_9_data.get("schema_version"), 1)
        self.assertEqual(continuation_9_data.get("stage"), "P1-00b")

        continuation_9_predecessor = continuation_9_data["predecessor_inventory"]
        self.assertEqual(
            continuation_9_predecessor["path"],
            manifest["evidence_sources"]["continuation_8_path"],
        )
        self.assertEqual(
            continuation_9_predecessor["sha256"],
            hashlib.sha256(continuation_8_bytes).hexdigest(),
        )
        self.assertEqual(
            continuation_9_predecessor["last_commit"],
            "8d4bd2a4bbfd8647a52f33717e881fa962c0aca5",
        )

        continuation_9_records = continuation_9_data.get("records", [])
        self.assertEqual(len(continuation_9_records), 1)

        continuation_10_file_path = repo_root / manifest["evidence_sources"]["continuation_10_path"]
        self.assertTrue(
            continuation_10_file_path.is_file(),
            f"Continuation 10 evidence file not found at {continuation_10_file_path}",
        )
        continuation_10_bytes = continuation_10_file_path.read_bytes()
        continuation_10_data = json.loads(continuation_10_bytes.decode("utf-8"))

        self.assertEqual(continuation_10_data.get("schema_version"), 1)
        self.assertEqual(continuation_10_data.get("stage"), "P1-00b")

        continuation_10_predecessor = continuation_10_data["predecessor_inventory"]
        self.assertEqual(
            continuation_10_predecessor["path"],
            manifest["evidence_sources"]["continuation_9_path"],
        )
        self.assertEqual(
            continuation_10_predecessor["sha256"],
            hashlib.sha256(continuation_9_bytes).hexdigest(),
        )
        self.assertEqual(
            continuation_10_predecessor["last_commit"],
            "d90a545db84dc5d3d2cc8ab6f0b4cd3243ade0a8",
        )

        continuation_10_records = continuation_10_data.get("records", [])
        self.assertEqual(len(continuation_10_records), 1)

        continuation_11_file_path = repo_root / manifest["evidence_sources"]["continuation_11_path"]
        self.assertTrue(
            continuation_11_file_path.is_file(),
            f"Continuation 11 evidence file not found at {continuation_11_file_path}",
        )
        continuation_11_bytes = continuation_11_file_path.read_bytes()
        continuation_11_data = json.loads(continuation_11_bytes.decode("utf-8"))

        self.assertEqual(continuation_11_data.get("schema_version"), 1)
        self.assertEqual(continuation_11_data.get("stage"), "P1-00b")

        continuation_11_predecessor = continuation_11_data["predecessor_inventory"]
        self.assertEqual(
            continuation_11_predecessor["path"],
            manifest["evidence_sources"]["continuation_10_path"],
        )
        self.assertEqual(
            continuation_11_predecessor["sha256"],
            hashlib.sha256(continuation_10_bytes).hexdigest(),
        )
        self.assertEqual(
            continuation_11_predecessor["last_commit"],
            "6d4ad41d24afd7451157ebe724a1418d4050e22e",
        )

        continuation_11_records = continuation_11_data.get("records", [])
        self.assertEqual(len(continuation_11_records), 2)

        expected_records_7_11 = [
            {
                "path": "tests/test_p1_00b_characterization_admin_users_toggle_block_unlinked.py",
                "sha256": "c8c7858a246565c52a0a267e4e47a2374cbb8652031faf0b4de356c11f69838f",
                "last_commit": "431a5da5ef1c987c72eabd3571534e7843d1c5fd",
                "seed_ids": [
                    "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-ADMIN-TOGGLE-BLOCK-L1649"
                ],
                "test_selectors": [
                    "tests/test_p1_00b_characterization_admin_users_toggle_block_unlinked.py::AdminUsersToggleBlockUnlinkedCharacterizationTests::test_toggle_block_without_user_stops_before_parse_session_or_delivery"
                ],
            },
            {
                "path": "tests/test_p1_00b_characterization_trade_expire_offer_db_entry_failure.py",
                "sha256": "fe8ff44778f49517a437f05a7a6cc7697cfb154bbaf736a09d05867ca699c6f1",
                "last_commit": "6b76cb3c23b3507e3048d71b6e6b9817ec058a4a",
                "seed_ids": [
                    "BOT-TRADE-CALLBACK-QUERY-TRADE-MANAGE-HANDLE-EXPIRE-OFFER-L118"
                ],
                "test_selectors": [
                    "tests/test_p1_00b_characterization_trade_expire_offer_db_entry_failure.py::TradeExpireOfferDbEntryFailureCharacterizationTests::test_db_factory_failure_propagates_and_releases_lease_without_delivery"
                ],
            },
            {
                "path": "tests/test_p1_00b_characterization_admin_broadcast_search_state.py",
                "sha256": "27fbe303758d108f2e30c1ac0d407126a74641b7d51d65b20864ca0e1e15d295",
                "last_commit": "b6ced906adf8a1847bd8962263d1006af55baa62",
                "seed_ids": [
                    "BOT-ADMIN-MESSAGE-ADMIN-BROADCAST-PROCESS-SELECTED-RECIPIENT-SEARCH-L308"
                ],
                "test_selectors": [
                    "tests/test_p1_00b_characterization_admin_broadcast_search_state.py::AdminBroadcastSearchStateCharacterizationTests::test_matched_recipients_preserve_selected_marker_and_search_delivery",
                    "tests/test_p1_00b_characterization_admin_broadcast_search_state.py::AdminBroadcastSearchStateCharacterizationTests::test_empty_results_preserve_selected_count_and_no_match_delivery",
                    "tests/test_p1_00b_characterization_admin_broadcast_search_state.py::AdminBroadcastSearchStateCharacterizationTests::test_malformed_selected_id_fails_before_session_or_delivery",
                ],
            },
            {
                "path": "tests/test_p1_00b_characterization_admin_broadcast_finish_selected_linked.py",
                "sha256": "77defed1ed10d923b7d32d89a44a5f20ebc380496f1021ebeb299dd45af340fe",
                "last_commit": "b48ae0bf6ef28731e070a0e016ae40daac6cdffa",
                "seed_ids": [
                    "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-FINISH-SELECTED-RECIPIENTS-L395"
                ],
                "test_selectors": [
                    "tests/test_p1_00b_characterization_admin_broadcast_finish_selected_linked.py::AdminBroadcastFinishSelectedLinkedCharacterizationTests::test_empty_selection_alerts_without_transition_or_edit",
                    "tests/test_p1_00b_characterization_admin_broadcast_finish_selected_linked.py::AdminBroadcastFinishSelectedLinkedCharacterizationTests::test_nonempty_selection_prompts_then_acknowledges",
                ],
            },
            {
                "path": "tests/test_p1_00b_characterization_admin_broadcast_selected_user_rejection.py",
                "sha256": "874fd169c5bd5931e61c32696762b9c1cc3ea6daf6cb76dce2164b97308a930b",
                "last_commit": "3f7933fc134303fdc09837550be931eafac4c0a2",
                "seed_ids": [
                    "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-TOGGLE-SELECTED-USER-L334"
                ],
                "test_selectors": [
                    "tests/test_p1_00b_characterization_admin_broadcast_selected_user_rejection.py::AdminBroadcastSelectedUserRejectionCharacterizationTests::test_malformed_target_alerts_without_state_or_delivery",
                    "tests/test_p1_00b_characterization_admin_broadcast_selected_user_rejection.py::AdminBroadcastSelectedUserRejectionCharacterizationTests::test_new_target_at_cap_alerts_without_mutation_or_delivery",
                ],
            },
            {
                "path": "tests/test_p1_00b_characterization_terminate_session_malformed_guard.py",
                "sha256": "ea75b1cd5f9f341608dd039acc24a81714067bd0fa2e0d2dd1bd47fafa21c4d0",
                "last_commit": "e7f957d7900e9cfc5150b6cad83e1daf7c29bb81",
                "seed_ids": [
                    "API-AUTH-DELETE-SESSIONS-TERMINATE-SESSION-L1075"
                ],
                "test_selectors": [
                    "tests/test_p1_00b_characterization_terminate_session_malformed_guard.py::TerminateSessionMalformedGuardCharacterizationTests::test_valid_bearer_and_real_guard_precede_malformed_uuid_rejection",
                    "tests/test_p1_00b_characterization_terminate_session_malformed_guard.py::TerminateSessionMalformedGuardCharacterizationTests::test_accountant_guard_rejects_before_malformed_uuid_path",
                ],
            },
            {
                "path": "tests/test_p1_00b_characterization_terminate_session_missing_target.py",
                "sha256": "672a23ece8460a17aa2d9766c472d8fc6035a87e3a2f4e17acccdd6da128eab9",
                "last_commit": "03659a9ff3af3c1efeb7e97ab1ce9d782109c257",
                "seed_ids": [
                    "API-AUTH-DELETE-SESSIONS-TERMINATE-SESSION-L1075"
                ],
                "test_selectors": [
                    "tests/test_p1_00b_characterization_terminate_session_missing_target.py::TerminateSessionMissingTargetCharacterizationTests::test_valid_uuid_missing_target_returns_404"
                ],
            },
        ]

        all_records_7_11 = (
            continuation_7_records
            + continuation_8_records
            + continuation_9_records
            + continuation_10_records
            + continuation_11_records
        )
        self.assertEqual(len(all_records_7_11), 7)

        seen_record_paths = [r["path"] for r in all_records_7_11]
        self.assertEqual(len(seen_record_paths), 7)
        self.assertEqual(len(set(seen_record_paths)), 7, "Duplicate record paths found in continuations 7-11")

        all_selectors_7_11 = []
        for r in all_records_7_11:
            all_selectors_7_11.extend(r.get("test_selectors", []))
        self.assertEqual(len(all_selectors_7_11), 12)
        self.assertEqual(len(set(all_selectors_7_11)), 12, "Duplicate selectors found in continuations 7-11")

        for record, expected_data in zip(all_records_7_11, expected_records_7_11):
            self.assertEqual(record["path"], expected_data["path"])
            self.assertEqual(record["sha256"], expected_data["sha256"])
            self.assertEqual(record["last_commit"], expected_data["last_commit"])
            self.assertEqual(record["seed_ids"], expected_data["seed_ids"])
            self.assertEqual(record["test_selectors"], expected_data["test_selectors"])

            record_file_path = repo_root / expected_data["path"]
            self.assertTrue(record_file_path.is_file(), f"Record file not found at {record_file_path}")
            record_bytes = record_file_path.read_bytes()
            self.assertEqual(
                hashlib.sha256(record_bytes).hexdigest(),
                expected_data["sha256"],
                f"SHA256 mismatch for {record_file_path}",
            )

            tree = ast.parse(record_bytes.decode("utf-8"), filename=str(record_file_path))
            classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}

            for selector in expected_data["test_selectors"]:
                parts = selector.split("::")
                self.assertEqual(len(parts), 3, f"Malformed selector: {selector}")
                selector_path, class_name, test_name = parts
                self.assertEqual(
                    selector_path,
                    expected_data["path"],
                    f"Selector path does not match record path: {selector}",
                )
                self.assertIn(
                    class_name,
                    classes,
                    f"Class {class_name} not found in AST of {record_file_path}",
                )
                class_node = classes[class_name]
                methods = {
                    node.name
                    for node in class_node.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                self.assertIn(
                    test_name,
                    methods,
                    f"Method {test_name} not found in {class_name} in AST of {record_file_path}",
                )

        # Distinct terminate-session records intentionally share a seed
        term_record_1 = continuation_11_records[0]
        term_record_2 = continuation_11_records[1]
        self.assertNotEqual(term_record_1["path"], term_record_2["path"])
        self.assertEqual(term_record_1["seed_ids"], term_record_2["seed_ids"])
        self.assertEqual(
            term_record_1["seed_ids"],
            ["API-AUTH-DELETE-SESSIONS-TERMINATE-SESSION-L1075"],
        )

        # Continuation 7 seed-set accounting
        continuation_7_seed_ids = []
        for r in continuation_7_records:
            continuation_7_seed_ids.extend(r.get("seed_ids", []))
        self.assertEqual(len(continuation_7_seed_ids), 2)
        admin_toggle_seed = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-ADMIN-TOGGLE-BLOCK-L1649"
        trade_expiry_seed = "BOT-TRADE-CALLBACK-QUERY-TRADE-MANAGE-HANDLE-EXPIRE-OFFER-L118"
        self.assertEqual(continuation_7_seed_ids, [admin_toggle_seed, trade_expiry_seed])
        self.assertIn(admin_toggle_seed, baseline_seed_set)
        self.assertIn(admin_toggle_seed, continuation_6_checkpoint_evidenced_set)
        self.assertIn(admin_toggle_seed, inventory_seed_set)
        self.assertIn(trade_expiry_seed, inventory_seed_set)
        self.assertNotIn(trade_expiry_seed, baseline_seed_set)
        self.assertNotIn(trade_expiry_seed, continuation_6_checkpoint_evidenced_set)

        continuation_7_evidenced_set = (
            continuation_6_checkpoint_evidenced_set | {trade_expiry_seed}
        )
        self.assertEqual(len(continuation_7_evidenced_set), 386)

        # Continuation 8 seed-set accounting
        continuation_8_seed_ids = []
        for r in continuation_8_records:
            continuation_8_seed_ids.extend(r.get("seed_ids", []))
        self.assertEqual(len(continuation_8_seed_ids), 1)
        broadcast_search_seed = (
            "BOT-ADMIN-MESSAGE-ADMIN-BROADCAST-PROCESS-SELECTED-RECIPIENT-SEARCH-L308"
        )
        self.assertEqual(continuation_8_seed_ids, [broadcast_search_seed])
        self.assertIn(broadcast_search_seed, baseline_seed_set)
        self.assertIn(broadcast_search_seed, continuation_6_checkpoint_evidenced_set)
        self.assertIn(broadcast_search_seed, inventory_seed_set)
        continuation_8_evidenced_set = (
            continuation_7_evidenced_set | set(continuation_8_seed_ids)
        )
        self.assertEqual(len(continuation_8_evidenced_set), 386)

        # Continuation 9 seed-set accounting
        continuation_9_seed_ids = []
        for r in continuation_9_records:
            continuation_9_seed_ids.extend(r.get("seed_ids", []))
        self.assertEqual(len(continuation_9_seed_ids), 1)
        broadcast_finish_seed = (
            "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-FINISH-SELECTED-RECIPIENTS-L395"
        )
        self.assertEqual(continuation_9_seed_ids, [broadcast_finish_seed])
        self.assertIn(broadcast_finish_seed, baseline_seed_set)
        self.assertIn(broadcast_finish_seed, continuation_6_checkpoint_evidenced_set)
        self.assertIn(broadcast_finish_seed, inventory_seed_set)
        continuation_9_evidenced_set = (
            continuation_8_evidenced_set | set(continuation_9_seed_ids)
        )
        self.assertEqual(len(continuation_9_evidenced_set), 386)

        # Continuation 10 seed-set accounting
        continuation_10_seed_ids = []
        for r in continuation_10_records:
            continuation_10_seed_ids.extend(r.get("seed_ids", []))
        self.assertEqual(len(continuation_10_seed_ids), 1)
        broadcast_toggle_seed = (
            "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-TOGGLE-SELECTED-USER-L334"
        )
        self.assertEqual(continuation_10_seed_ids, [broadcast_toggle_seed])
        self.assertIn(broadcast_toggle_seed, baseline_seed_set)
        self.assertIn(broadcast_toggle_seed, continuation_6_checkpoint_evidenced_set)
        self.assertIn(broadcast_toggle_seed, inventory_seed_set)
        continuation_10_evidenced_set = (
            continuation_9_evidenced_set | set(continuation_10_seed_ids)
        )
        self.assertEqual(len(continuation_10_evidenced_set), 386)

        # Four-baseline-family reconciliation overlap checks
        four_baseline_reconciliation_seeds = {
            admin_toggle_seed,
            broadcast_search_seed,
            broadcast_finish_seed,
            broadcast_toggle_seed,
        }
        self.assertEqual(len(four_baseline_reconciliation_seeds), 4)
        self.assertTrue(four_baseline_reconciliation_seeds.issubset(baseline_seed_set))
        self.assertTrue(
            four_baseline_reconciliation_seeds.issubset(
                continuation_6_checkpoint_evidenced_set
            )
        )

        all_flattened_recent_ids_through_10 = (
            all_flattened_recent_ids
            + continuation_7_seed_ids
            + continuation_8_seed_ids
            + continuation_9_seed_ids
            + continuation_10_seed_ids
        )
        reconciled_new_through_10 = (
            set(all_flattened_recent_ids_through_10) - baseline_seed_set
        )
        self.assertEqual(len(reconciled_new_through_10), 251)
        self.assertEqual(len(continuation_10_evidenced_set), 386)
        self.assertEqual(
            len(baseline_seed_set) + len(reconciled_new_through_10), 386
        )

        # Continuation 11 seed-set accounting
        continuation_11_seed_ids = []
        for r in continuation_11_records:
            continuation_11_seed_ids.extend(r.get("seed_ids", []))
        self.assertEqual(len(continuation_11_seed_ids), 2)
        terminate_session_seed = "API-AUTH-DELETE-SESSIONS-TERMINATE-SESSION-L1075"
        self.assertEqual(
            continuation_11_seed_ids,
            [terminate_session_seed, terminate_session_seed],
        )
        continuation_11_seed_set = set(continuation_11_seed_ids)
        self.assertEqual(len(continuation_11_seed_set), 1)
        self.assertIn(terminate_session_seed, inventory_seed_set)
        self.assertNotIn(terminate_session_seed, baseline_seed_set)
        self.assertNotIn(terminate_session_seed, continuation_10_evidenced_set)

        all_flattened_recent_ids_through_11 = (
            all_flattened_recent_ids_through_10 + continuation_11_seed_ids
        )
        final_recent_seed_set = set(all_flattened_recent_ids_through_11)
        self.assertTrue(final_recent_seed_set.issubset(inventory_seed_set))

        final_reconciled_new_beyond_baseline = (
            final_recent_seed_set - baseline_seed_set
        )
        self.assertEqual(len(final_reconciled_new_beyond_baseline), 252)

        final_evidenced_set = (
            continuation_10_evidenced_set | continuation_11_seed_set
        )
        self.assertEqual(len(final_evidenced_set), 387)

        final_remaining_seed_set = inventory_seed_set - final_evidenced_set
        self.assertEqual(len(final_remaining_seed_set), 78)
        self.assertEqual(
            len(final_evidenced_set) + len(final_remaining_seed_set), 465
        )
        self.assertEqual(
            len(baseline_seed_set) + len(final_reconciled_new_beyond_baseline), 387
        )

        self.assertEqual(
            manifest["counts"]["baseline_evidenced_seed_count"], len(baseline_seed_set)
        )
        self.assertEqual(
            manifest["counts"]["reconciled_new_seed_count"],
            len(final_reconciled_new_beyond_baseline),
        )
        self.assertEqual(
            manifest["counts"]["evidenced_seed_count"], len(final_evidenced_set)
        )
        self.assertEqual(
            manifest["counts"]["remaining_seed_count"], len(final_remaining_seed_set)
        )

    def test_historical_consumer_ignores_live_content_and_rejects_snapshot_drift(self):
        """Witness historical snapshot reader isolation against live manifest drift.

        Stage P1-00b IN_PROGRESS, P1-00 COMPLETE; active WA-P1-00B-GATE-FIRST-RECONCILIATION-20261002-169 digest 5f8cbf6411079ac471fbf364568a2f54997abc60253b67ec72aae88eae3cc23e.
        Canonical blocker P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE remains open; P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN also open.
        Evidence reconciliation 5/5; canonical integration of C13 and shared C16/C17 remains pending.
        Keep pending_five=5, units_since_reconciliation=5, global_reconciliation_complete=false, canonical 387/465 (135 baseline +252 new), 78 remaining, 24 unresolved origins.
        Exclude read_user self-success, super-admin rejection, authorization policy coverage, mutation, persistence, retry, reload, origin cells, seed/blocker/Stage closure and sixth behavior.
        State this scope in the bounded new witness docstring; no ledger edit is authorized.
        Preserve accepted C13 and C16 tests and all receipts. C17 consumer remains unresolved; no rejected artifacts may be reused.
        Retain verbatim: defer_prior_unit=normal169-c17-historical-coverage-reader-isolation-20261003-c005; defer_prior_unit=normal169-historical-coverage-isolation-20261003-c004; defer_prior_unit=normal169-c16-receipt-custody-20261003-c001; defer_prior_unit=normal169-c16-machine-linkage-20261003-c001-v2. These are deferrals, never acceptance evidence. No scope or gate closure is claimed by this migration.
        """
        repo_root = Path(__file__).resolve().parent.parent

        real_snapshot_path = repo_root / HISTORICAL_SNAPSHOT_REL_PATH
        self.assertTrue(
            real_snapshot_path.is_file(),
            f"Repository snapshot not found at {real_snapshot_path}",
        )
        real_snapshot_bytes = real_snapshot_path.read_bytes()
        real_snapshot_sha256 = hashlib.sha256(real_snapshot_bytes).hexdigest()
        self.assertEqual(real_snapshot_sha256, HISTORICAL_SNAPSHOT_SHA256)

        real_live_rel_path = "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json"
        real_live_path = repo_root / real_live_rel_path
        self.assertTrue(
            real_live_path.is_file(),
            f"Live manifest not found at {real_live_path}",
        )
        real_live_bytes_before = real_live_path.read_bytes()

        expected_sha256 = HISTORICAL_SNAPSHOT_SHA256
        expected_manifest = json.loads(real_snapshot_bytes.decode("utf-8"))

        try:
            with tempfile.TemporaryDirectory() as temp_dir_str:
                temp_dir = Path(temp_dir_str)
                temp_snapshot_dir = temp_dir / "docs/refactor/two-server/section-1/inventory"
                temp_snapshot_dir.mkdir(parents=True, exist_ok=True)
                temp_snapshot_path = (
                    temp_snapshot_dir
                    / "recent-characterization-seed-coverage-387-snapshot.json"
                )
                temp_live_path = temp_snapshot_dir / "seed-handler-coverage.json"

                temp_snapshot_path.write_bytes(real_snapshot_bytes)
                expected_temp_path = temp_snapshot_path

                self.assertFalse(temp_live_path.exists())
                result_absent = load_historical_seed_coverage_snapshot(
                    expected_temp_path, expected_sha256=expected_sha256
                )
                self.assertEqual(result_absent, expected_manifest)

                canary_bytes = (
                    b'{"canary": "nonsecret-live-mutation-canary-payload", "stage": "CANARY"}\n'
                )
                temp_live_path.write_bytes(canary_bytes)
                self.assertTrue(temp_live_path.is_file())
                result_canary = load_historical_seed_coverage_snapshot(
                    expected_temp_path, expected_sha256=expected_sha256
                )
                self.assertEqual(result_canary, expected_manifest)

                drifted_snapshot_bytes = real_snapshot_bytes + b"\n"
                temp_snapshot_path.write_bytes(drifted_snapshot_bytes)
                self.assertEqual(
                    json.loads(drifted_snapshot_bytes.decode("utf-8")),
                    expected_manifest,
                )
                with self.assertRaises(ValueError) as ctx:
                    load_historical_seed_coverage_snapshot(
                        expected_temp_path, expected_sha256=expected_sha256
                    )
                rejection_msg = str(ctx.exception)
                self.assertIn(str(expected_temp_path), rejection_msg)
                self.assertIn(expected_sha256, rejection_msg)

                temp_snapshot_path.write_bytes(real_snapshot_bytes)
                self.assertEqual(temp_snapshot_path.read_bytes(), real_snapshot_bytes)
                result_restored = load_historical_seed_coverage_snapshot(
                    expected_temp_path, expected_sha256=expected_sha256
                )
                self.assertEqual(result_restored, expected_manifest)
                self.assertEqual(temp_snapshot_path.read_bytes(), real_snapshot_bytes)

        finally:
            self.assertEqual(real_live_path.read_bytes(), real_live_bytes_before)
            self.assertEqual(real_snapshot_path.read_bytes(), real_snapshot_bytes)


if __name__ == "__main__":
    unittest.main()
