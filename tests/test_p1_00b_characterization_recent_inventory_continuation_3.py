import hashlib
import json
import os
from pathlib import Path
import unittest

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()


class RecentCharacterizationContinuationThreeContractTests(unittest.TestCase):
    def test_manifest_indexes_six_units_and_is_sha_bound_to_second_continuation(self):
        repo_root = Path(__file__).resolve().parent.parent

        manifest_rel_path = "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-3.json"
        manifest_path = repo_root / manifest_rel_path
        self.assertTrue(manifest_path.is_file(), f"Manifest not found at {manifest_path}")

        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))

        expected_top_level_keys = {
            "schema_version",
            "stage",
            "predecessor_inventory",
            "records",
        }
        self.assertEqual(set(manifest.keys()), expected_top_level_keys)
        self.assertIs(type(manifest["schema_version"]), int)
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["stage"], "P1-00b")

        predecessor = manifest["predecessor_inventory"]
        expected_predecessor_keys = {"path", "sha256", "last_commit"}
        self.assertEqual(set(predecessor.keys()), expected_predecessor_keys)
        self.assertEqual(
            predecessor["path"],
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-2.json",
        )
        self.assertEqual(
            predecessor["sha256"],
            "77227a80651a3cfc911450cbfcb6cce37f94ac035796739263dc77dbeb8c7e03",
        )
        self.assertEqual(
            predecessor["last_commit"],
            "3de519e1351ebda576eafa4c27649acbb2d03f37",
        )

        predecessor_path = repo_root / predecessor["path"]
        self.assertTrue(predecessor_path.is_file(), f"Predecessor not found at {predecessor_path}")
        live_predecessor_sha256 = hashlib.sha256(predecessor_path.read_bytes()).hexdigest()
        self.assertEqual(live_predecessor_sha256, predecessor["sha256"])

        records = manifest["records"]
        self.assertEqual(len(records), 6)

        expected_records = [
            {
                "path": "tests/test_p1_00b_characterization_list_pending_invitations_real_async_session.py",
                "sha256": "4cedea553a1791aeff7ef55d802ce23da2bc45579fa77e596f89d99c41948ce2",
                "last_commit": "ef38ffda3e1f8750e6b95e1a8ab378bb34792a49",
                "seed_ids": [
                    "API-IDENTITY-GET-INVITATIONS-LIST-PENDING-INVITATIONS-L266",
                ],
                "test_selectors": [
                    "tests/test_p1_00b_characterization_list_pending_invitations_real_async_session.py::ListPendingInvitationsRealAsyncSessionCharacterizationTests::test_owned_active_standard_invitation_returns_sanitized_sms_projection_with_real_async_session",
                ],
            },
            {
                "path": "tests/test_p1_00b_characterization_delete_pending_invitation_real_async_session.py",
                "sha256": "efc48fbf2ca0469153a51a81cb0be3233c5c1a88df9ee7f2c56111e35288b580",
                "last_commit": "a0162194a65ded95984b8203b550b42aee3c9496",
                "seed_ids": [
                    "API-IDENTITY-DELETE-INVITATIONS-DELETE-PENDING-INVITATION-L294",
                ],
                "test_selectors": [
                    "tests/test_p1_00b_characterization_delete_pending_invitation_real_async_session.py::DeletePendingInvitationRealAsyncSessionCharacterizationTests::test_owned_active_standard_invitation_revokes_releases_and_commits_with_real_async_session",
                ],
            },
            {
                "path": "tests/test_p1_00b_characterization_notification_disable_push_subscription_real_async_session.py",
                "sha256": "97828742810d76ea08ad47dcc33f0cc2752ad9f8418bd8cdf3a0531f93c7e719",
                "last_commit": "6d18fab30be925c151cc49e1b637fa616706a6af",
                "seed_ids": [
                    "API-NOTIFY-DELETE-NOTIFICATIONS-DISABLE-PUSH-SUBSCRIPTION-L178",
                ],
                "test_selectors": [
                    "tests/test_p1_00b_characterization_notification_disable_push_subscription_real_async_session.py::NotificationDisablePushSubscriptionRealAsyncSessionCharacterizationTests::test_owned_enabled_subscription_disables_and_persists_with_real_async_session",
                ],
            },
            {
                "path": "tests/test_p1_00b_characterization_notification_upsert_push_subscription_real_async_session.py",
                "sha256": "15bed5729db30897903e6378900cf22dd3a91f720044b9739b35457a2db7948e",
                "last_commit": "8b94b8eec4ae70368b47240ed4d72f6d2dfae9bc",
                "seed_ids": [
                    "API-NOTIFY-POST-NOTIFICATIONS-UPSERT-PUSH-SUBSCRIPTION-L138",
                ],
                "test_selectors": [
                    "tests/test_p1_00b_characterization_notification_upsert_push_subscription_real_async_session.py::NotificationUpsertPushSubscriptionRealAsyncSessionCharacterizationTests::test_configured_new_subscription_creates_and_persists_with_real_async_session",
                ],
            },
            {
                "path": "tests/test_p1_00b_characterization_admin_messages_current_history_clear_batch.py",
                "sha256": "b308c8647b507c0813f43a2a357af5cdb3662261a6971eae9d994fa406aeef50",
                "last_commit": "d35d50291bb9265567cacaf223f0ce7f692c14f2",
                "seed_ids": [
                    "API-ADMIN-GET-ADMIN-MESSAGES-GET-CURRENT-MARKET-MESSAGE-L180",
                    "API-ADMIN-GET-ADMIN-MESSAGES-GET-MARKET-MESSAGE-HISTORY-L194",
                    "API-ADMIN-DELETE-ADMIN-MESSAGES-CLEAR-CURRENT-MARKET-MESSAGE-L235",
                ],
                "test_selectors": [
                    "tests/test_p1_00b_characterization_admin_messages_current_history_clear_batch.py::AdminMessagesCurrentHistoryClearCharacterizationTests::test_get_current_market_message_valid_cache_hit_returns_exact_payload_without_downstream_work",
                    "tests/test_p1_00b_characterization_admin_messages_current_history_clear_batch.py::AdminMessagesCurrentHistoryClearCharacterizationTests::test_get_market_message_history_explicit_limit_preserves_order_and_serializes_exactly",
                    "tests/test_p1_00b_characterization_admin_messages_current_history_clear_batch.py::AdminMessagesCurrentHistoryClearCharacterizationTests::test_clear_current_market_message_missing_current_clears_cache_and_publishes_in_order",
                ],
            },
            {
                "path": "tests/test_p1_00b_characterization_chat_activity_success_batch.py",
                "sha256": "13e2125a230f5b1195c01e39b350537cf33bdf49158fa95e1ecc87c71a95e9e3",
                "last_commit": "2a7996d625e7e5e7cfd89d2ac2df192c6a61a58c",
                "seed_ids": [
                    "API-MSG-POST-CHAT-SEND-DIRECT-ACTIVITY-SIGNAL-L939",
                    "API-MSG-POST-CHAT-SEND-ROOM-ACTIVITY-SIGNAL-L962",
                ],
                "test_selectors": [
                    "tests/test_p1_00b_characterization_chat_activity_success_batch.py::ChatActivitySuccessCharacterizationTests::test_send_direct_activity_signal_authorized_publishes_exact_event",
                    "tests/test_p1_00b_characterization_chat_activity_success_batch.py::ChatActivitySuccessCharacterizationTests::test_send_room_activity_signal_active_group_publishes_exact_event",
                ],
            },
        ]

        expected_record_keys = {
            "path",
            "sha256",
            "last_commit",
            "seed_ids",
            "test_selectors",
        }
        all_seed_ids = []
        all_selectors = []

        for rec, exp_rec in zip(records, expected_records):
            self.assertEqual(set(rec.keys()), expected_record_keys)
            self.assertEqual(rec["path"], exp_rec["path"])
            self.assertEqual(rec["sha256"], exp_rec["sha256"])
            self.assertEqual(rec["last_commit"], exp_rec["last_commit"])
            self.assertEqual(rec["seed_ids"], exp_rec["seed_ids"])
            self.assertEqual(rec["test_selectors"], exp_rec["test_selectors"])

            target_file = repo_root / rec["path"]
            self.assertTrue(target_file.is_file(), f"Target file not found at {target_file}")
            live_sha256 = hashlib.sha256(target_file.read_bytes()).hexdigest()
            self.assertEqual(live_sha256, rec["sha256"], f"SHA256 mismatch for {rec['path']}")

            all_seed_ids.extend(rec["seed_ids"])
            all_selectors.extend(rec["test_selectors"])

        self.assertEqual(len(records), 6)
        self.assertEqual(len(all_seed_ids), 9)
        self.assertEqual(len(all_selectors), 9)

        unique_seed_ids = set(all_seed_ids)
        self.assertEqual(len(unique_seed_ids), 9)

        surface_api_path = repo_root / "docs/refactor/two-server/section-1/inventory/surface-api.json"
        self.assertTrue(surface_api_path.is_file(), f"Surface API inventory not found at {surface_api_path}")
        surface_api_data = json.loads(surface_api_path.read_bytes().decode("utf-8"))
        surface_api_ids = {entry["behavior_id"] for entry in surface_api_data["entries"]}
        self.assertTrue(unique_seed_ids.issubset(surface_api_ids))

        coverage_path = repo_root / "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json"
        self.assertTrue(coverage_path.is_file(), f"Coverage manifest not found at {coverage_path}")
        coverage_data = json.loads(coverage_path.read_bytes().decode("utf-8"))

        baseline_file = repo_root / coverage_data["evidence_sources"]["baseline_path"]
        baseline_data = json.loads(baseline_file.read_bytes().decode("utf-8"))
        blocker_items = baseline_data.get("blocker_registry", {}).get("items", [])
        blocker = next(
            item for item in blocker_items
            if item.get("id") == "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE"
            or item.get("blocker_id") == "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE"
        )
        reason = blocker["reason"]
        start_delim = "Evidenced seeds are "
        end_delim = ". Sibling family"
        start_idx = reason.index(start_delim) + len(start_delim)
        end_idx = reason.index(end_delim, start_idx)
        baseline_seed_set = {s.strip() for s in reason[start_idx:end_idx].split(",") if s.strip()}
        self.assertEqual(len(baseline_seed_set), 135)

        base_file = repo_root / coverage_data["evidence_sources"]["base_path"]
        base_data = json.loads(base_file.read_bytes().decode("utf-8"))
        base_ids = set()
        for rec in base_data.get("records", []):
            base_ids.update(rec.get("seed_ids", []))

        continuation_file = repo_root / coverage_data["evidence_sources"]["continuation_path"]
        continuation_data = json.loads(continuation_file.read_bytes().decode("utf-8"))
        continuation_ids = set()
        for rec in continuation_data.get("records", []):
            continuation_ids.update(rec.get("seed_ids", []))

        continuation_2_file = repo_root / coverage_data["evidence_sources"]["continuation_2_path"]
        continuation_2_data = json.loads(continuation_2_file.read_bytes().decode("utf-8"))
        continuation_2_ids = set()
        for rec in continuation_2_data.get("records", []):
            continuation_2_ids.update(rec.get("seed_ids", []))

        prior_evidenced_union = baseline_seed_set | base_ids | continuation_ids | continuation_2_ids
        self.assertEqual(len(prior_evidenced_union), 356)

        new_ids = unique_seed_ids - prior_evidenced_union
        expected_new_ids = {
            "API-IDENTITY-GET-INVITATIONS-LIST-PENDING-INVITATIONS-L266",
            "API-IDENTITY-DELETE-INVITATIONS-DELETE-PENDING-INVITATION-L294",
            "API-NOTIFY-DELETE-NOTIFICATIONS-DISABLE-PUSH-SUBSCRIPTION-L178",
            "API-NOTIFY-POST-NOTIFICATIONS-UPSERT-PUSH-SUBSCRIPTION-L138",
            "API-MSG-POST-CHAT-SEND-DIRECT-ACTIVITY-SIGNAL-L939",
            "API-MSG-POST-CHAT-SEND-ROOM-ACTIVITY-SIGNAL-L962",
        }
        self.assertEqual(new_ids, expected_new_ids)
        self.assertEqual(len(new_ids), 6)

        already_evidenced_ids = unique_seed_ids & prior_evidenced_union
        expected_baseline_ids = {
            "API-ADMIN-GET-ADMIN-MESSAGES-GET-CURRENT-MARKET-MESSAGE-L180",
            "API-ADMIN-GET-ADMIN-MESSAGES-GET-MARKET-MESSAGE-HISTORY-L194",
            "API-ADMIN-DELETE-ADMIN-MESSAGES-CLEAR-CURRENT-MARKET-MESSAGE-L235",
        }
        self.assertEqual(already_evidenced_ids, expected_baseline_ids)
        self.assertTrue(expected_baseline_ids.issubset(baseline_seed_set))


if __name__ == "__main__":
    unittest.main()
