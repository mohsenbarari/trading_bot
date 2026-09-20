import hashlib
import json
from pathlib import Path
import unittest


class RecentCharacterizationContinuationContractTests(unittest.TestCase):
    def test_manifest_indexes_five_accepted_units_and_is_sha_bound_to_base_inventory(self):
        repo_root = Path(__file__).resolve().parent.parent

        base_inventory_rel_path = "docs/refactor/two-server/section-1/inventory/recent-characterization.json"
        base_path = repo_root / base_inventory_rel_path
        self.assertTrue(base_path.is_file(), f"Base inventory not found at {base_path}")

        continuation_rel_path = "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation.json"
        continuation_path = repo_root / continuation_rel_path
        self.assertTrue(continuation_path.is_file(), f"Continuation manifest not found at {continuation_path}")

        base_bytes = base_path.read_bytes()
        calculated_base_sha256 = hashlib.sha256(base_bytes).hexdigest()
        base_data = json.loads(base_bytes.decode("utf-8"))

        continuation_bytes = continuation_path.read_bytes()
        continuation_data = json.loads(continuation_bytes.decode("utf-8"))

        # Validate exact top-level keys
        expected_top_level_keys = {"schema_version", "stage", "base_inventory", "records"}
        self.assertEqual(set(continuation_data.keys()), expected_top_level_keys)

        # Validate top-level schema_version, stage
        self.assertIs(type(continuation_data["schema_version"]), int)
        self.assertEqual(continuation_data["schema_version"], 1)
        self.assertEqual(continuation_data["stage"], "P1-00b")
        self.assertEqual(continuation_data["stage"], base_data["stage"])

        # Validate exact base_inventory keys
        base_inventory_binding = continuation_data["base_inventory"]
        self.assertIsInstance(base_inventory_binding, dict)
        expected_base_inventory_keys = {"path", "sha256", "inspected_head"}
        self.assertEqual(set(base_inventory_binding.keys()), expected_base_inventory_keys)

        # Validate exact binding values
        authoritative_path = "docs/refactor/two-server/section-1/inventory/recent-characterization.json"
        authoritative_sha256 = "5a235244378a63ad9da935e5e70c98593d4664f5f36cfcca3a610743aeef33eb"
        authoritative_head = "5c2b95722ece7b0d22c13ee3c861dfb96a930c92"

        self.assertEqual(base_inventory_binding["path"], authoritative_path)
        self.assertEqual(base_inventory_binding["sha256"], authoritative_sha256)
        self.assertEqual(base_inventory_binding["inspected_head"], authoritative_head)

        # Compare binding values to actual base inventory
        self.assertEqual(base_inventory_binding["sha256"], calculated_base_sha256)
        self.assertEqual(base_inventory_binding["inspected_head"], base_data["inspected_head"])

        # Validate records schema and length
        records = continuation_data["records"]
        self.assertIsInstance(records, list)
        self.assertEqual(len(records), 41)

        expected_record_keys = {"path", "sha256", "last_commit", "seed_ids", "test_selectors"}
        for record in records:
            self.assertIsInstance(record, dict)
            self.assertEqual(set(record.keys()), expected_record_keys)
            self.assertEqual(len(record["last_commit"]), 40)
            self.assertTrue(all(c in "0123456789abcdef" for c in record["last_commit"]))

        # Validate record 0 (commit 40c251e10d010f13711c3a70aa85cfa13fc08616)
        expected_record_0 = {
            "path": "tests/test_p1_00b_characterization_blocks_accountant_entry_denials_batch.py",
            "sha256": "12a17b5187bae1525a719121d86fe214cb48d29b5566e99a93c452421f821bb5",
            "last_commit": "40c251e10d010f13711c3a70aa85cfa13fc08616",
            "seed_ids": [
                "API-IDENTITY-GET-BLOCKS-GET-MY-BLOCKED-USERS-L122",
                "API-IDENTITY-POST-BLOCKS-BLOCK-A-USER-L135",
                "API-IDENTITY-DELETE-BLOCKS-UNBLOCK-A-USER-L173",
                "API-IDENTITY-GET-BLOCKS-SEARCH-USERS-L223",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_blocks_accountant_entry_denials_batch.py::BlocksAccountantEntryDenialsCharacterizationTests::test_blocked_users_denies_accountant_before_service_or_database_work",
                "tests/test_p1_00b_characterization_blocks_accountant_entry_denials_batch.py::BlocksAccountantEntryDenialsCharacterizationTests::test_block_user_denies_accountant_before_target_lookup_or_mutation",
                "tests/test_p1_00b_characterization_blocks_accountant_entry_denials_batch.py::BlocksAccountantEntryDenialsCharacterizationTests::test_unblock_user_denies_accountant_before_mutation",
                "tests/test_p1_00b_characterization_blocks_accountant_entry_denials_batch.py::BlocksAccountantEntryDenialsCharacterizationTests::test_search_users_denies_accountant_before_search",
            ],
        }
        self.assertEqual(records[0], expected_record_0)

        # Validate record 1 (commit bea41649bc19859d9109911ea49e52ad70aefd61)
        expected_record_1 = {
            "path": "tests/test_p1_00b_characterization_accountants_customer_actor_guards_batch.py",
            "sha256": "4f0b242e893edbbb62833d8444244e11c4b4aff6c8660a89d4405e9602453ff6",
            "last_commit": "bea41649bc19859d9109911ea49e52ad70aefd61",
            "seed_ids": [
                "API-IDENTITY-GET-ACCOUNTANTS-LIST-MY-ACCOUNTANTS-L167",
                "API-IDENTITY-GET-ACCOUNTANTS-GET-MY-ACCOUNTANT-L199",
                "API-IDENTITY-POST-ACCOUNTANTS-CREATE-MY-ACCOUNTANT-L227",
                "API-IDENTITY-PATCH-ACCOUNTANTS-UPDATE-MY-ACCOUNTANT-L367",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_accountants_customer_actor_guards_batch.py::AccountantsCustomerActorGuardsCharacterizationTests::test_list_my_accountants_denies_customer_actor_before_reads",
                "tests/test_p1_00b_characterization_accountants_customer_actor_guards_batch.py::AccountantsCustomerActorGuardsCharacterizationTests::test_get_my_accountant_denies_customer_actor_before_relation_lookup",
                "tests/test_p1_00b_characterization_accountants_customer_actor_guards_batch.py::AccountantsCustomerActorGuardsCharacterizationTests::test_create_my_accountant_denies_customer_actor_before_payload_or_creation",
                "tests/test_p1_00b_characterization_accountants_customer_actor_guards_batch.py::AccountantsCustomerActorGuardsCharacterizationTests::test_update_my_accountant_denies_customer_actor_before_payload_or_update",
            ],
        }
        self.assertEqual(records[1], expected_record_1)

        # Validate record 2 (commit bd66914a21cd0b195e3de8f840a825e963eeb0b1)
        expected_record_2 = {
            "path": "tests/test_p1_00b_characterization_origin_internal_telegram_tier2_real_session_guard.py",
            "sha256": "82881bf0e1f393187fc39fdb9b45bce99da10634626c1f69ef6bfa9f245b7e83",
            "last_commit": "bd66914a21cd0b195e3de8f840a825e963eeb0b1",
            "seed_ids": [
                "BOT-TRADE-CALLBACK-QUERY-TRADE-EXECUTE-HANDLE-CHANNEL-TRADE-PUBLIC-L829",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_origin_internal_telegram_tier2_real_session_guard.py::P100bOriginInternalTelegramTier2RealSessionGuardCharacterizationTests::test_internal_offer_telegram_tier2_denied_before_offer_lookup_with_real_async_session",
            ],
        }
        self.assertEqual(records[2], expected_record_2)

        # Validate record 3 (commit 1bd64068310ac3bed27ff3c06038f881e255dd84)
        expected_record_3 = {
            "path": "tests/test_p1_00b_characterization_origin_internal_telegram_admin_double_click.py",
            "sha256": "0fdfb74f3ca0cc648d28a9cc180e922d0e4aeecc75f8482419abb259b2c93371",
            "last_commit": "1bd64068310ac3bed27ff3c06038f881e255dd84",
            "seed_ids": [
                "BOT-TRADE-CALLBACK-QUERY-TRADE-EXECUTE-HANDLE-CHANNEL-TRADE-PUBLIC-L829",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_origin_internal_telegram_admin_double_click.py::P100bOriginInternalTelegramAdminDoubleClickCharacterizationTests::test_internal_offer_telegram_super_admin_double_click_persists_once_with_policy_snapshot",
            ],
        }
        self.assertEqual(records[3], expected_record_3)

        # Validate record 4 (commit 5d239e7f5af3b4836e253b27e8eeffcb70f08439)
        expected_record_4 = {
            "path": "tests/test_p1_00b_characterization_admin_users_unlinked_navigation_guards_batch.py",
            "sha256": "2c36dde5235517ea28007c308d00b4c2328f4a680e8a2542c4b369db27592dee",
            "last_commit": "5d239e7f5af3b4836e253b27e8eeffcb70f08439",
            "seed_ids": [
                "BOT-ADMIN-MESSAGE-ADMIN-USERS-HANDLE-USERS-MENU-L489",
                "BOT-ADMIN-MESSAGE-ADMIN-USERS-HANDLE-USERS-LIST-COMMAND-L512",
                "BOT-ADMIN-MESSAGE-ADMIN-USERS-HANDLE-BACK-TO-ADMIN-L602",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_admin_users_unlinked_navigation_guards_batch.py::AdminUsersUnlinkedNavigationGuardsCharacterizationTests::test_users_menu_without_user_stops_before_delivery_or_state",
                "tests/test_p1_00b_characterization_admin_users_unlinked_navigation_guards_batch.py::AdminUsersUnlinkedNavigationGuardsCharacterizationTests::test_users_list_command_without_user_stops_before_state_runtime_or_render",
                "tests/test_p1_00b_characterization_admin_users_unlinked_navigation_guards_batch.py::AdminUsersUnlinkedNavigationGuardsCharacterizationTests::test_back_to_admin_without_user_stops_before_state_runtime_or_delivery",
            ],
        }
        self.assertEqual(records[4], expected_record_4)

        # Validate record 5 (commit ebde678d2861a08f4d0fab523490887d3efbebde)
        expected_record_5 = {
            "path": "tests/test_p1_00b_characterization_blocks_read_status_batch.py",
            "sha256": "5235e2a30a7c1ffb33fd2a46516b1956fd0904ef4d45309f04b459bf7b94a401",
            "last_commit": "ebde678d2861a08f4d0fab523490887d3efbebde",
            "seed_ids": [
                "API-IDENTITY-GET-BLOCKS-GET-MY-BLOCK-STATUS-L106",
                "API-IDENTITY-GET-BLOCKS-CHECK-BLOCK-STATUS-L203",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_blocks_read_status_batch.py::BlocksReadStatusCharacterizationTests::test_get_my_block_status_delegates_identity_and_returns_service_payload",
                "tests/test_p1_00b_characterization_blocks_read_status_batch.py::BlocksReadStatusCharacterizationTests::test_check_block_status_returns_directional_identity_result",
            ],
        }
        self.assertEqual(records[5], expected_record_5)

        # Validate record 6 (commit d83894825c12e076dea335a5603766028bc473fb)
        expected_record_6 = {
            "path": "tests/test_p1_00b_characterization_accountants_context_denial_batch.py",
            "sha256": "3a422136178aa6f5c6c5be198630517d98502e0a52105a335242d1c6be1b2c6a",
            "last_commit": "d83894825c12e076dea335a5603766028bc473fb",
            "seed_ids": [
                "API-IDENTITY-GET-ACCOUNTANTS-LIST-MY-ACCOUNTANT-SESSIONS-L280",
                "API-IDENTITY-DELETE-ACCOUNTANTS-TERMINATE-MY-ACCOUNTANT-SESSION-L296",
                "API-IDENTITY-DELETE-ACCOUNTANTS-CANCEL-MY-PENDING-ACCOUNTANT-L338",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_accountants_context_denial_batch.py::AccountantsContextDenialBatchCharacterizationTests::test_list_my_accountant_sessions_accountant_context_denied_before_session_lookup",
                "tests/test_p1_00b_characterization_accountants_context_denial_batch.py::AccountantsContextDenialBatchCharacterizationTests::test_terminate_my_accountant_session_accountant_context_denied_before_uuid_or_mutation",
                "tests/test_p1_00b_characterization_accountants_context_denial_batch.py::AccountantsContextDenialBatchCharacterizationTests::test_cancel_my_pending_accountant_accountant_context_denied_before_unlink_or_audit",
            ],
        }
        self.assertEqual(records[6], expected_record_6)

        # Validate record 7 (commit 1a5eb920a837544fe7b9706e26391bc8bcd0d17d)
        expected_record_7 = {
            "path": "tests/test_p1_00b_characterization_customers_context_denial_batch.py",
            "sha256": "2c4914972f2bc5da66a1b23685271048914fef365fa67b01abbda42780e7208f",
            "last_commit": "1a5eb920a837544fe7b9706e26391bc8bcd0d17d",
            "seed_ids": [
                "API-IDENTITY-GET-CUSTOMERS-LIST-MY-CUSTOMER-SESSIONS-L817",
                "API-IDENTITY-DELETE-CUSTOMERS-TERMINATE-MY-CUSTOMER-SESSION-L833",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_customers_context_denial_batch.py::CustomersContextDenialBatchCharacterizationTests::test_list_my_customer_sessions_accountant_context_denied_before_relation_or_session_lookup",
                "tests/test_p1_00b_characterization_customers_context_denial_batch.py::CustomersContextDenialBatchCharacterizationTests::test_terminate_my_customer_session_accountant_context_denied_before_uuid_or_mutation",
            ],
        }
        self.assertEqual(records[7], expected_record_7)

        # Validate record 8 (commit 7ec33ad7c7eb860a0232f2b726e537961855f48b)
        expected_record_8 = {
            "path": "tests/test_p1_00b_characterization_customers_owner_relations_context_denial_batch.py",
            "sha256": "76679b786e620e83552111f03fba7c24eaa243d78f095460b40c19d11bb79081",
            "last_commit": "7ec33ad7c7eb860a0232f2b726e537961855f48b",
            "seed_ids": [
                "API-IDENTITY-GET-CUSTOMERS-LIST-MY-CUSTOMERS-L386",
                "API-IDENTITY-GET-CUSTOMERS-GET-MY-CUSTOMER-RELATION-L624",
                "API-IDENTITY-GET-CUSTOMERS-GET-MY-CUSTOMER-TRADE-STATS-L708",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_customers_owner_relations_context_denial_batch.py::CustomersOwnerRelationsContextDenialBatchCharacterizationTests::test_list_my_customers_accountant_context_denied_before_relation_or_invitation_lookup",
                "tests/test_p1_00b_characterization_customers_owner_relations_context_denial_batch.py::CustomersOwnerRelationsContextDenialBatchCharacterizationTests::test_get_my_customer_relation_accountant_context_denied_before_relation_lookup",
                "tests/test_p1_00b_characterization_customers_owner_relations_context_denial_batch.py::CustomersOwnerRelationsContextDenialBatchCharacterizationTests::test_get_my_customer_trade_stats_accountant_context_denied_before_days_or_relation_lookup",
            ],
        }
        self.assertEqual(records[8], expected_record_8)

        # Validate record 9 (commit 1eeb25f3b7db953eda2898fbc2f81ea0c33196e0)
        expected_record_9 = {
            "path": "tests/test_p1_00b_characterization_customers_owner_mutations_context_denial_batch.py",
            "sha256": "022f9f734d8386c9153e5d7fbd5da0c0d63bff6fd87b98ca1a80224cb1eed0ba",
            "last_commit": "1eeb25f3b7db953eda2898fbc2f81ea0c33196e0",
            "seed_ids": [
                "API-IDENTITY-DELETE-CUSTOMERS-UNLINK-MY-CUSTOMER-L640",
                "API-IDENTITY-PATCH-CUSTOMERS-UPDATE-MY-CUSTOMER-L674",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_customers_owner_mutations_context_denial_batch.py::CustomersOwnerMutationsContextDenialBatchCharacterizationTests::test_unlink_my_customer_accountant_context_denied_before_relation_mutation",
                "tests/test_p1_00b_characterization_customers_owner_mutations_context_denial_batch.py::CustomersOwnerMutationsContextDenialBatchCharacterizationTests::test_update_my_customer_accountant_context_denied_before_payload_serialization_or_relation_mutation",
            ],
        }
        self.assertEqual(records[9], expected_record_9)

        # Validate record 10 (commit 8da58ad4c33932108a8b36f1cd616f8f3d64ab44)
        expected_record_10 = {
            "path": "tests/test_p1_00b_characterization_issue_telegram_link_token.py",
            "sha256": "1715ffe2fb873cb142d66d52e64c1d000486d8e31e7bc2fb93bc7fe2b94fbec7",
            "last_commit": "8da58ad4c33932108a8b36f1cd616f8f3d64ab44",
            "seed_ids": [
                "API-AUTH-POST-AUTH-ISSUE-TELEGRAM-LINK-TOKEN-L1307",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_issue_telegram_link_token.py::TestIssueTelegramLinkTokenCharacterization::test_already_linked_user_characterization",
            ],
        }
        self.assertEqual(records[10], expected_record_10)

        # Validate record 11 (commit 4b8c6573cefab54920cc1fff9fe04b46f04c4c39)
        expected_record_11 = {
            "path": "tests/test_p1_00b_characterization_retired_register_otp_request.py",
            "sha256": "958f521641d2b237d70bf5eadd1f88b6a9f148bc5ace915365c00d4ab54b10d2",
            "last_commit": "4b8c6573cefab54920cc1fff9fe04b46f04c4c39",
            "seed_ids": [
                "API-AUTH-POST-AUTH-RETIRED-REGISTER-OTP-REQUEST-L1501",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_retired_register_otp_request.py::test_retired_register_otp_request_direct_contract",
            ],
        }
        self.assertEqual(records[11], expected_record_11)

        # Validate record 12 (commit 3bd2f8a9396bdbf4838666de2cf4b8853a0b0dfe)
        expected_record_12 = {
            "path": "tests/test_p1_00b_characterization_retired_register_otp_verify.py",
            "sha256": "62715c536adde34f5060a67d1a57c6d46fae10f971798dceef8298f2dacf5ce1",
            "last_commit": "3bd2f8a9396bdbf4838666de2cf4b8853a0b0dfe",
            "seed_ids": [
                "API-AUTH-POST-AUTH-RETIRED-REGISTER-OTP-VERIFY-L1539",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_retired_register_otp_verify.py::test_retired_register_otp_verify_direct_contract",
            ],
        }
        self.assertEqual(records[12], expected_record_12)

        # Validate record 13 (commit 94c8a1eee05186dab3c6d03720db8fd894199de2)
        expected_record_13 = {
            "path": "tests/test_p1_00b_characterization_retired_register_complete.py",
            "sha256": "28a129a35ed1f55e6f04853a171085f8f6f1b8de0f636dadf4e2584cbfbc987a",
            "last_commit": "94c8a1eee05186dab3c6d03720db8fd894199de2",
            "seed_ids": [
                "API-AUTH-POST-AUTH-RETIRED-REGISTER-COMPLETE-L1580",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_retired_register_complete.py::test_retired_register_complete_direct_contract",
            ],
        }
        self.assertEqual(records[13], expected_record_13)

        # Validate record 14 (commit 581a2e70ef6cf92e5f61cd92599c289fd777d78c)
        expected_record_14 = {
            "path": "tests/test_p1_00b_characterization_read_users_me_relation_access.py",
            "sha256": "eeea6dacc37324f101487faa133e8e921602a9e1d4d852ded3f440b50a5048b5",
            "last_commit": "581a2e70ef6cf92e5f61cd92599c289fd777d78c",
            "seed_ids": [
                "API-AUTH-GET-AUTH-READ-USERS-ME-L1295",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_read_users_me_relation_access.py::TestP100bCharacterizationReadUsersMeRelationAccess::test_read_users_me_tier2_customer_telegram_denial_projection",
            ],
        }
        self.assertEqual(records[14], expected_record_14)

        # Validate record 15 (commit 286c5d5c16da70342ae2a0ee6766d2d894d7f3b1)
        expected_record_15 = {
            "path": "tests/test_p1_00b_characterization_update_my_avatar.py",
            "sha256": "f6b00604206fc5652ac1c23523dc5b9e15f3f29e48fc53e46de6a1317087061b",
            "last_commit": "286c5d5c16da70342ae2a0ee6766d2d894d7f3b1",
            "seed_ids": [
                "API-AUTH-PUT-AUTH-UPDATE-MY-AVATAR-L1356",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_update_my_avatar.py::UpdateMyAvatarCharacterizationTests::test_update_my_avatar_success_preserves_strict_observable_order_and_relations",
            ],
        }
        self.assertEqual(records[15], expected_record_15)

        # Validate record 16 (commit dd776b964806870c341670a8dc0eefeea297aa6b)
        expected_record_16 = {
            "path": "tests/test_p1_00b_characterization_update_my_address.py",
            "sha256": "adf1d457e149957edbe2f22a746cab1b597bf5c3d0a3a2a821751bc31a21b5ec",
            "last_commit": "dd776b964806870c341670a8dc0eefeea297aa6b",
            "seed_ids": [
                "API-AUTH-PUT-AUTH-UPDATE-MY-ADDRESS-L1375",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_update_my_address.py::UpdateMyAddressCharacterizationTests::test_update_my_address_success_strict_order_and_normalization",
            ],
        }
        self.assertEqual(records[16], expected_record_16)

        # Validate record 17 (commit 833a9f598c8ce90fe9ba97fa4dcae189d4e1fd5d)
        expected_record_17 = {
            "path": "tests/test_p1_00b_characterization_update_my_offer_overtime_postcommit.py",
            "sha256": "5e86a6b54fb02582de8daaf2302200b489e6fcc1da5c0b84539e8851b55a2a09",
            "last_commit": "833a9f598c8ce90fe9ba97fa4dcae189d4e1fd5d",
            "seed_ids": [
                "API-AUTH-PUT-AUTH-UPDATE-MY-OFFER-OVERTIME-L1387",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_update_my_offer_overtime_postcommit.py::UpdateMyOfferOvertimePostcommitCharacterizationTests::test_update_my_offer_overtime_telegram_present_success_phase_order_and_response",
            ],
        }
        self.assertEqual(records[17], expected_record_17)

        # Validate record 18 (commit f0d85139903e2190400e8f76927f6005e071f503)
        expected_record_18 = {
            "path": "tests/test_p1_00b_characterization_update_offer_overtime_internal_failures.py",
            "sha256": "5a5d412242d16363dfc12c8526e7b253c0f16c8266060496db306988198ea388",
            "last_commit": "f0d85139903e2190400e8f76927f6005e071f503",
            "seed_ids": [
                "API-AUTH-POST-AUTH-UPDATE-OFFER-OVERTIME-INTERNAL-L1433",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_update_offer_overtime_internal_failures.py::TestP100bUpdateOfferOvertimeInternalFailures::test_telegram_id_present_success_executes_complete_order_and_returns_response",
            ],
        }
        self.assertEqual(records[18], expected_record_18)

        # Validate record 19 (commit d888f32afc2405a5e9bb1571ded18750579b7844)
        expected_record_19 = {
            "path": "tests/test_p1_00b_characterization_read_registration_context.py",
            "sha256": "160faad279f50ab5066a25ba0475304db23cf3be125fdddb9e338cdae96a734d",
            "last_commit": "d888f32afc2405a5e9bb1571ded18750579b7844",
            "seed_ids": [
                "API-AUTH-POST-AUTH-READ-REGISTRATION-CONTEXT-L1819",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_read_registration_context.py::test_read_registration_context_completed_cookie_response",
            ],
        }
        self.assertEqual(records[19], expected_record_19)

        # Validate record 20 (commit aa331248751eded912b3bd04e8353f62c85bfb09)
        expected_record_20 = {
            "path": "tests/test_p1_00b_characterization_auth_complete_telegram_account_link_internal.py",
            "sha256": "3dc5b38c93f50b66af007ee02c590489e238f705199050036039dfc2046b99c8",
            "last_commit": "aa331248751eded912b3bd04e8353f62c85bfb09",
            "seed_ids": [
                "API-AUTH-POST-AUTH-COMPLETE-TELEGRAM-ACCOUNT-LINK-INTERNAL-L251",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_auth_complete_telegram_account_link_internal.py::P100bAuthCompleteTelegramAccountLinkInternalCharacterizationTests::test_complete_telegram_account_link_internal_enabled_first_terminal_success",
            ],
        }
        self.assertEqual(records[20], expected_record_20)

        # Validate record 21 (commit e305b8c9b8a89336a40de0a3e2aacd6b6a1f5cc7)
        expected_record_21 = {
            "path": "tests/test_p1_00b_characterization_request_registration_context_otp.py",
            "sha256": "ca1476bdc6e58705b1ff7697b393e430775c0fb8577c45b3a324929b29d38c6c",
            "last_commit": "e305b8c9b8a89336a40de0a3e2aacd6b6a1f5cc7",
            "seed_ids": [
                "API-AUTH-POST-AUTH-REQUEST-REGISTRATION-CONTEXT-OTP-L1838",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_request_registration_context_otp.py::test_request_registration_context_otp_active_otp_reuse_context_ready",
            ],
        }
        self.assertEqual(records[21], expected_record_21)

        # Validate record 22 (commit b2d5d04ef51fbd72ec6e01ae5f2def2b03d576a1)
        expected_record_22 = {
            "path": "tests/test_p1_00b_characterization_verify_registration_context_otp.py",
            "sha256": "227c5d5ce03f4c835987debf45107a6c609bae30f864b67a338a79c1fe6f8613",
            "last_commit": "b2d5d04ef51fbd72ec6e01ae5f2def2b03d576a1",
            "seed_ids": [
                "API-AUTH-POST-AUTH-VERIFY-REGISTRATION-CONTEXT-OTP-L1887",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_verify_registration_context_otp.py::test_verify_registration_context_otp_state_guard_invitation_otp_verified_returns_idempotent_200",
            ],
        }
        self.assertEqual(records[22], expected_record_22)

        # Validate record 23 (commit 44485afa1a867b39524ec71b07953e59577a6a7c)
        expected_record_23 = {
            "path": "tests/test_p1_00b_characterization_complete_registration_context.py",
            "sha256": "6bb692ad335d964e27cee10ef36c14b247d4741fdee01b0dd8920bf6baa97458",
            "last_commit": "44485afa1a867b39524ec71b07953e59577a6a7c",
            "seed_ids": [
                "API-AUTH-POST-AUTH-COMPLETE-REGISTRATION-CONTEXT-L1952",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_complete_registration_context.py::test_complete_registration_context_completion_marker_idempotence",
            ],
        }
        self.assertEqual(records[23], expected_record_23)

        # Validate record 24 (commit c8765d44d7f6c5b13d7a6661aa1a026490924a3e)
        expected_record_24 = {
            "path": "tests/test_p1_00b_characterization_clear_registration_context.py",
            "sha256": "787f8a89c64bd4b05aed537df42767f509cf509a8a8889259876336ad571e45a",
            "last_commit": "c8765d44d7f6c5b13d7a6661aa1a026490924a3e",
            "seed_ids": [
                "API-AUTH-POST-AUTH-CLEAR-REGISTRATION-CONTEXT-L1995",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_clear_registration_context.py::test_clear_registration_context_present_handle_cookie_path",
            ],
        }
        self.assertEqual(records[24], expected_record_24)

        # Validate record 25 (commit 611d88b2052958e4514b0cda675fb8eceda9e186)
        expected_record_25 = {
            "path": "tests/test_p1_00b_characterization_refresh_access_token_server_id.py",
            "sha256": "e2cb8862eb1411c6b7e95985ae65cdf9c9d5e988e16ea800d78b4b74b9a896aa",
            "last_commit": "611d88b2052958e4514b0cda675fb8eceda9e186",
            "seed_ids": [
                "API-AUTH-POST-AUTH-REFRESH-ACCESS-TOKEN-L2011",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_refresh_access_token_server_id.py::TestP100bCharacterizationRefreshAccessTokenServerId::test_server_id_precedence_truthy_session_wins_over_truthy_user",
                "tests/test_p1_00b_characterization_refresh_access_token_server_id.py::TestP100bCharacterizationRefreshAccessTokenServerId::test_server_id_precedence_absent_session_uses_truthy_user",
                "tests/test_p1_00b_characterization_refresh_access_token_server_id.py::TestP100bCharacterizationRefreshAccessTokenServerId::test_server_id_precedence_both_absent_uses_authoritative_server_foreign",
            ],
        }
        self.assertEqual(records[25], expected_record_25)

        # Validate record 26 (commit 5a9a75ffac17113e54b789643bb94542b4fc155b)
        expected_record_26 = {
            "path": "tests/test_p1_00b_characterization_dev_login_remote_key_gate.py",
            "sha256": "c16fab24b8c238228b73a3ced6a11eeb530e905a5c16873a8b3efdb2279ad9aa",
            "last_commit": "5a9a75ffac17113e54b789643bb94542b4fc155b",
            "seed_ids": [
                "API-AUTH-POST-AUTH-DEV-LOGIN-L2080",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_dev_login_remote_key_gate.py::P100bDevLoginRemoteKeyGateCharacterizationTests::test_dev_login_remote_valid_key_existing_user_success",
                "tests/test_p1_00b_characterization_dev_login_remote_key_gate.py::P100bDevLoginRemoteKeyGateCharacterizationTests::test_dev_login_remote_missing_key_rejected_before_downstream_work",
                "tests/test_p1_00b_characterization_dev_login_remote_key_gate.py::P100bDevLoginRemoteKeyGateCharacterizationTests::test_dev_login_remote_invalid_key_rejected_before_downstream_work",
            ],
        }
        self.assertEqual(records[26], expected_record_26)

        # Validate record 27 (commit b18eb5bcf0cb925830999d41551cd7536b47befe)
        expected_record_27 = {
            "path": "tests/test_p1_00b_characterization_auth_resend_otp_sms_structured_state.py",
            "sha256": "320144e113f7f87b716a171f7df6233a208cdf1a838a94566df2850877e43936",
            "last_commit": "b18eb5bcf0cb925830999d41551cd7536b47befe",
            "seed_ids": [
                "API-AUTH-POST-AUTH-RESEND-OTP-SMS-L2612",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_auth_resend_otp_sms_structured_state.py::TestAuthResendOtpSmsStructuredState::test_resend_otp_sms_structured_state_accepted",
                "tests/test_p1_00b_characterization_auth_resend_otp_sms_structured_state.py::TestAuthResendOtpSmsStructuredState::test_resend_otp_sms_structured_state_delivery_failed",
                "tests/test_p1_00b_characterization_auth_resend_otp_sms_structured_state.py::TestAuthResendOtpSmsStructuredState::test_resend_otp_sms_structured_state_delivery_ambiguous",
            ],
        }
        self.assertEqual(records[27], expected_record_27)

        # Validate record 28 (commit 95baac0101d09076cbcf148b1fc8f5897f61b224)
        expected_record_28 = {
            "path": "tests/test_p1_00b_characterization_verify_otp_structured_state.py",
            "sha256": "049f4a9e7baabbef0c9bf1d1d71329bbe26b8600935cb34786ac21e2000ee9c6",
            "last_commit": "95baac0101d09076cbcf148b1fc8f5897f61b224",
            "seed_ids": [
                "API-AUTH-POST-AUTH-VERIFY-OTP-L2695",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_verify_otp_structured_state.py::TestVerifyOtpStructuredStateCharacterization::test_verify_otp_active_standard_user_success_path",
                "tests/test_p1_00b_characterization_verify_otp_structured_state.py::TestVerifyOtpStructuredStateCharacterization::test_verify_otp_atomic_consume_loss_rejection",
            ],
        }
        self.assertEqual(records[28], expected_record_28)

        # Validate record 29 (commit 2f4dff1c3264ebf63444931bafd5d1de96471074)
        expected_record_29 = {
            "path": "tests/test_p1_00b_characterization_webapp_login_retirement.py",
            "sha256": "cf6ced52b4d0f5f4bf004615f9e14bb9381f7b7a33fcb5ab5659510b069276d1",
            "last_commit": "2f4dff1c3264ebf63444931bafd5d1de96471074",
            "seed_ids": [
                "API-AUTH-POST-AUTH-WEBAPP-LOGIN-L2899",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_webapp_login_retirement.py::WebAppLoginRetirementCharacterizationTests::test_webapp_login_rejects_deliberately_malformed_init_data_before_dependencies",
                "tests/test_p1_00b_characterization_webapp_login_retirement.py::WebAppLoginRetirementCharacterizationTests::test_webapp_login_rejects_realistic_valid_shaped_init_data_before_dependencies",
            ],
        }
        self.assertEqual(records[29], expected_record_29)

        # Validate record 30 (commit aba9412eaa85cf884a6de94f78f358c06d4de4d9)
        expected_record_30 = {
            "path": "tests/test_p1_00b_characterization_setup_admin_password_no_change_required.py",
            "sha256": "2f454c46e85348a4168f470de85126a1d084bda1e6250cae08dc12f922ecc0a9",
            "last_commit": "aba9412eaa85cf884a6de94f78f358c06d4de4d9",
            "seed_ids": [
                "API-AUTH-POST-AUTH-SETUP-ADMIN-PASSWORD-L2915",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_setup_admin_password_no_change_required.py::SetupAdminPasswordNoChangeRequiredCharacterizationTests::test_existing_password_requirement_false_rejects_before_validation_or_mutation",
            ],
        }
        self.assertEqual(records[30], expected_record_30)

        # Validate record 31 (commit 01dfbe69c40a5c34ff6631a63f7885897901a9c8)
        expected_record_31 = {
            "path": "tests/test_p1_00b_characterization_auth_deliver_telegram_otp_internal.py",
            "sha256": "1e2ce816468d5f555bb453cf02010ffac236c71b4080c525d9f396f5c942f58d",
            "last_commit": "01dfbe69c40a5c34ff6631a63f7885897901a9c8",
            "seed_ids": [
                "API-AUTH-POST-AUTH-DELIVER-TELEGRAM-OTP-INTERNAL-L297",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_auth_deliver_telegram_otp_internal.py::P100bAuthDeliverTelegramOtpInternalCharacterizationTests::test_deliver_telegram_otp_internal_enabled_sent_success",
                "tests/test_p1_00b_characterization_auth_deliver_telegram_otp_internal.py::P100bAuthDeliverTelegramOtpInternalCharacterizationTests::test_deliver_telegram_otp_internal_enabled_delivery_failure_propagates",
            ],
        }
        self.assertEqual(records[31], expected_record_31)

        # Validate record 32 (commit d9a99c9b298f425a4bf98505482f64bad03b8f54)
        expected_record_32 = {
            "path": "tests/test_p1_00b_characterization_internal_session_authority_check.py",
            "sha256": "f02a5cac72b8f91077eb995f3c2221efd59c6548182896f8b32a4478ab448f6f",
            "last_commit": "d9a99c9b298f425a4bf98505482f64bad03b8f54",
            "seed_ids": [
                "API-AUTH-POST-SESSIONS-INTERNAL-SESSION-AUTHORITY-CHECK-L539",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_internal_session_authority_check.py::InternalSessionAuthorityCheckCharacterizationTests::test_internal_session_authority_check_invalid_signature",
                "tests/test_p1_00b_characterization_internal_session_authority_check.py::InternalSessionAuthorityCheckCharacterizationTests::test_internal_session_authority_check_valid_signature_missing_user",
                "tests/test_p1_00b_characterization_internal_session_authority_check.py::InternalSessionAuthorityCheckCharacterizationTests::test_internal_session_authority_check_valid_signature_present_user",
            ],
        }
        self.assertEqual(records[32], expected_record_32)

        # Validate record 33 (commit b822a59bf00e15e2ef70cb1ba003f09493059de4)
        expected_record_33 = {
            "path": "tests/test_p1_00b_characterization_sessions_logout_all_caller_authorization.py",
            "sha256": "93f668f8132c4e728731c7e891f5c023bb6692bf58f0dc0f454c7fe277c2cfdc",
            "last_commit": "b822a59bf00e15e2ef70cb1ba003f09493059de4",
            "seed_ids": [
                "API-AUTH-POST-SESSIONS-LOGOUT-ALL-SESSIONS-L1146",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_sessions_logout_all_caller_authorization.py::SessionsLogoutAllCallerAuthorizationCharacterizationTests::test_logout_all_sessions_caller_absent_raises_403_and_protects_sinks",
                "tests/test_p1_00b_characterization_sessions_logout_all_caller_authorization.py::SessionsLogoutAllCallerAuthorizationCharacterizationTests::test_logout_all_sessions_caller_non_primary_raises_403_and_protects_sinks",
            ],
        }
        self.assertEqual(records[33], expected_record_33)

        # Validate record 34 (commit a4543a63bfe9a5695a702a254ebf54d4fe9cbdbb)
        expected_record_34 = {
            "path": "tests/test_p1_00b_characterization_pending_login_requests.py",
            "sha256": "6bbb85e76b4427675869e940cca1b1f5d1e540c8ac5d605f0db38575e763a4bd",
            "last_commit": "a4543a63bfe9a5695a702a254ebf54d4fe9cbdbb",
            "seed_ids": [
                "API-AUTH-GET-SESSIONS-GET-PENDING-LOGIN-REQUESTS-L1191",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_pending_login_requests.py::CharacterizationPendingLoginRequestsTests::test_get_pending_login_requests_empty_result",
                "tests/test_p1_00b_characterization_pending_login_requests.py::CharacterizationPendingLoginRequestsTests::test_get_pending_login_requests_populated_result",
            ],
        }
        self.assertEqual(records[34], expected_record_34)

        # Validate record 35 (commit 2adffdd65aae659c5ef2bb69f830898d7d920ce7)
        expected_record_35 = {
            "path": "tests/test_p1_00b_characterization_poll_login_request_status.py",
            "sha256": "efb433fec3a32a3e1be0ecd0772adf00078b77e5930fac8150cf1ae0f903cecc",
            "last_commit": "2adffdd65aae659c5ef2bb69f830898d7d920ce7",
            "seed_ids": [
                "API-AUTH-GET-SESSIONS-POLL-LOGIN-REQUEST-STATUS-L1311",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_poll_login_request_status.py::PollLoginRequestStatusContractCharacterizationTests::test_poll_login_request_status_missing_request",
                "tests/test_p1_00b_characterization_poll_login_request_status.py::PollLoginRequestStatusContractCharacterizationTests::test_poll_login_request_status_pending_at_exact_expiration_boundary",
                "tests/test_p1_00b_characterization_poll_login_request_status.py::PollLoginRequestStatusContractCharacterizationTests::test_poll_login_request_status_pending_expired_minimally_before_fixed_clock",
            ],
        }
        self.assertEqual(records[35], expected_record_35)

        # Validate record 36 (commit 21f848985c266ca4163c66cd607822a09c2a1e5d)
        expected_record_36 = {
            "path": "tests/test_p1_00b_characterization_main_public_config_seed.py",
            "sha256": "956ce1daaa898dd8ba7af41fed12cd8d954a734a3c10a9ef55657aead8d77405",
            "last_commit": "21f848985c266ca4163c66cd607822a09c2a1e5d",
            "seed_ids": [
                "API-OPS-GET-MAIN-GET-PUBLIC-CONFIG-L531",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_main_public_config_seed.py::MainPublicConfigSeedCharacterizationTests::test_get_public_config_returns_exact_public_settings_without_side_effects",
            ],
        }
        self.assertEqual(records[36], expected_record_36)

        # Validate record 37 (commit 96ecb285adf818ccfe676c5620c1c83221528482)
        expected_record_37 = {
            "path": "tests/test_p1_00b_characterization_main_root_inprocess_branches.py",
            "sha256": "5303dc6bc37f0807202d821cfa5993f90d4efd9d8288ab31296306dc1fa33733",
            "last_commit": "96ecb285adf818ccfe676c5620c1c83221528482",
            "seed_ids": [
                "API-OPS-GET-MAIN-ROOT-L614",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_main_root_inprocess_branches.py::MainRootInProcessBranchesCharacterizationTests::test_root_freezes_present_and_absent_static_dir_branches_in_process",
            ],
        }
        self.assertEqual(records[37], expected_record_37)

        # Validate record 38 (commit 40d6235e96d76e4594406927b2abce190613ef8f)
        expected_record_38 = {
            "path": "tests/test_p1_00b_characterization_send_typing_signal_success.py",
            "sha256": "8fe48437ad5eb24c9d5d4636e6ba73ccf42a82d43043883924e6c7bd00f93e4c",
            "last_commit": "40d6235e96d76e4594406927b2abce190613ef8f",
            "seed_ids": [
                "API-MSG-POST-CHAT-SEND-TYPING-SIGNAL-L918",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_send_typing_signal_success.py::SendTypingSignalSuccessCharacterizationTests::test_send_typing_signal_success_preserves_permission_name_and_publication_contract",
            ],
        }
        self.assertEqual(records[38], expected_record_38)

        # Validate record 39 (commit c5262fa5b5b5fda48f97973646e2a751ff34b23b)
        expected_record_39 = {
            "path": "tests/test_p1_00b_characterization_invitation_public_entry_guards_batch.py",
            "sha256": "03a94fd239571f5985feef90ae9df7b3a84b35124c214254a6c1818dc61cf432",
            "last_commit": "c5262fa5b5b5fda48f97973646e2a751ff34b23b",
            "seed_ids": [
                "API-IDENTITY-GET-INVITATIONS-LOOKUP-INVITATION-L382",
                "API-IDENTITY-GET-INVITATIONS-VALIDATE-INVITATION-L437",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_invitation_public_entry_guards_batch.py::InvitationPublicEntryGuardsCharacterizationTests::test_lookup_invitation_rejects_noncanonical_short_code_before_reads",
                "tests/test_p1_00b_characterization_invitation_public_entry_guards_batch.py::InvitationPublicEntryGuardsCharacterizationTests::test_validate_invitation_retires_raw_bearer_without_reads",
            ],
        }
        self.assertEqual(records[39], expected_record_39)

        # Validate record 40 (commit 5c1d1cfd240285484bcc067dc08c413114651748)
        expected_record_40 = {
            "path": "tests/test_p1_00b_characterization_admin_users_unlinked_block_settings_guards_batch.py",
            "sha256": "c3a99a09681783dd035c8b67bf7c313b3970d77ac6d898ee0a86a2623ffd25cd",
            "last_commit": "5c1d1cfd240285484bcc067dc08c413114651748",
            "seed_ids": [
                "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-BLOCK-SETTINGS-L1607",
                "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-ADMIN-SET-MAX-BLOCK-L1698",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_admin_users_unlinked_block_settings_guards_batch.py::AdminUsersUnlinkedBlockSettingsGuardsCharacterizationTests::test_user_block_settings_without_user_stops_before_parse_session_or_delivery",
                "tests/test_p1_00b_characterization_admin_users_unlinked_block_settings_guards_batch.py::AdminUsersUnlinkedBlockSettingsGuardsCharacterizationTests::test_admin_set_max_block_without_user_stops_before_parse_session_or_delivery",
            ],
        }
        self.assertEqual(records[40], expected_record_40)

        # Validate six-record slice and six new unique canonical seed IDs
        six_record_slice = records[25:31]
        self.assertEqual(len(six_record_slice), 6)
        expected_six_record_seed_ids = [
            "API-AUTH-POST-AUTH-REFRESH-ACCESS-TOKEN-L2011",
            "API-AUTH-POST-AUTH-DEV-LOGIN-L2080",
            "API-AUTH-POST-AUTH-RESEND-OTP-SMS-L2612",
            "API-AUTH-POST-AUTH-VERIFY-OTP-L2695",
            "API-AUTH-POST-AUTH-WEBAPP-LOGIN-L2899",
            "API-AUTH-POST-AUTH-SETUP-ADMIN-PASSWORD-L2915",
        ]
        six_record_seed_ids = [seed_id for record in six_record_slice for seed_id in record["seed_ids"]]
        self.assertEqual(six_record_seed_ids, expected_six_record_seed_ids)
        self.assertEqual(len(set(six_record_seed_ids)), 6)
        prior_25_seed_set = set(seed_id for record in records[:25] for seed_id in record["seed_ids"])
        self.assertTrue(set(six_record_seed_ids).isdisjoint(prior_25_seed_set))

        # Validate five-record slice and five new unique canonical seed IDs
        five_record_slice = records[31:36]
        self.assertEqual(len(five_record_slice), 5)
        expected_five_record_seed_ids = [
            "API-AUTH-POST-AUTH-DELIVER-TELEGRAM-OTP-INTERNAL-L297",
            "API-AUTH-POST-SESSIONS-INTERNAL-SESSION-AUTHORITY-CHECK-L539",
            "API-AUTH-POST-SESSIONS-LOGOUT-ALL-SESSIONS-L1146",
            "API-AUTH-GET-SESSIONS-GET-PENDING-LOGIN-REQUESTS-L1191",
            "API-AUTH-GET-SESSIONS-POLL-LOGIN-REQUEST-STATUS-L1311",
        ]
        five_record_seed_ids = [seed_id for record in five_record_slice for seed_id in record["seed_ids"]]
        self.assertEqual(five_record_seed_ids, expected_five_record_seed_ids)
        self.assertEqual(len(set(five_record_seed_ids)), 5)
        prior_31_seed_set = set(seed_id for record in records[:31] for seed_id in record["seed_ids"])
        self.assertTrue(set(five_record_seed_ids).isdisjoint(prior_31_seed_set))

        # Validate inventory membership for five-record slice
        surface_paths = [
            "docs/refactor/two-server/section-1/inventory/surface-api.json",
            "docs/refactor/two-server/section-1/inventory/surface-bot.json",
            "docs/refactor/two-server/section-1/inventory/surface-web.json",
            "docs/refactor/two-server/section-1/inventory/surface-jobs.json",
        ]
        inventory_seed_set = set()
        for rel_path in surface_paths:
            surface_file_path = repo_root / rel_path
            self.assertTrue(surface_file_path.is_file(), f"Surface file not found at {surface_file_path}")
            surface_data = json.loads(surface_file_path.read_bytes().decode("utf-8"))
            inventory_seed_set.update(entry["behavior_id"] for entry in surface_data["entries"])
        self.assertTrue(set(five_record_seed_ids).issubset(inventory_seed_set))

        # Validate appended eighth five-record slice (records 36 through 40)
        eighth_record_slice = records[36:41]
        self.assertEqual(len(eighth_record_slice), 5)
        expected_eighth_slice_seed_ids = [
            "API-OPS-GET-MAIN-GET-PUBLIC-CONFIG-L531",
            "API-OPS-GET-MAIN-ROOT-L614",
            "API-MSG-POST-CHAT-SEND-TYPING-SIGNAL-L918",
            "API-IDENTITY-GET-INVITATIONS-LOOKUP-INVITATION-L382",
            "API-IDENTITY-GET-INVITATIONS-VALIDATE-INVITATION-L437",
            "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-BLOCK-SETTINGS-L1607",
            "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-ADMIN-SET-MAX-BLOCK-L1698",
        ]
        eighth_slice_seed_ids = [seed_id for record in eighth_record_slice for seed_id in record["seed_ids"]]
        self.assertEqual(eighth_slice_seed_ids, expected_eighth_slice_seed_ids)
        self.assertEqual(len(eighth_slice_seed_ids), 7)
        self.assertEqual(len(set(eighth_slice_seed_ids)), 7)
        self.assertTrue(set(eighth_slice_seed_ids).issubset(inventory_seed_set))

        expected_eighth_slice_selectors = [
            "tests/test_p1_00b_characterization_main_public_config_seed.py::MainPublicConfigSeedCharacterizationTests::test_get_public_config_returns_exact_public_settings_without_side_effects",
            "tests/test_p1_00b_characterization_main_root_inprocess_branches.py::MainRootInProcessBranchesCharacterizationTests::test_root_freezes_present_and_absent_static_dir_branches_in_process",
            "tests/test_p1_00b_characterization_send_typing_signal_success.py::SendTypingSignalSuccessCharacterizationTests::test_send_typing_signal_success_preserves_permission_name_and_publication_contract",
            "tests/test_p1_00b_characterization_invitation_public_entry_guards_batch.py::InvitationPublicEntryGuardsCharacterizationTests::test_lookup_invitation_rejects_noncanonical_short_code_before_reads",
            "tests/test_p1_00b_characterization_invitation_public_entry_guards_batch.py::InvitationPublicEntryGuardsCharacterizationTests::test_validate_invitation_retires_raw_bearer_without_reads",
            "tests/test_p1_00b_characterization_admin_users_unlinked_block_settings_guards_batch.py::AdminUsersUnlinkedBlockSettingsGuardsCharacterizationTests::test_user_block_settings_without_user_stops_before_parse_session_or_delivery",
            "tests/test_p1_00b_characterization_admin_users_unlinked_block_settings_guards_batch.py::AdminUsersUnlinkedBlockSettingsGuardsCharacterizationTests::test_admin_set_max_block_without_user_stops_before_parse_session_or_delivery",
        ]
        eighth_slice_selectors = [selector for record in eighth_record_slice for selector in record["test_selectors"]]
        self.assertEqual(eighth_slice_selectors, expected_eighth_slice_selectors)
        self.assertEqual(len(eighth_slice_selectors), 7)
        self.assertEqual(len(set(eighth_slice_selectors)), 7)

        # Validate five-new versus two-baseline-overlap distinction
        scenario_records_path = repo_root / "docs/refactor/two-server/section-1/inventory/scenario-records.json"
        self.assertTrue(scenario_records_path.is_file(), f"Scenario records file not found at {scenario_records_path}")
        scenario_data = json.loads(scenario_records_path.read_bytes().decode("utf-8"))
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
        self.assertEqual(len(baseline_seed_set), 135)

        prior_36_seed_set = set(seed_id for record in records[:36] for seed_id in record["seed_ids"])
        new_five_seed_ids = [
            "API-OPS-GET-MAIN-GET-PUBLIC-CONFIG-L531",
            "API-OPS-GET-MAIN-ROOT-L614",
            "API-MSG-POST-CHAT-SEND-TYPING-SIGNAL-L918",
            "API-IDENTITY-GET-INVITATIONS-LOOKUP-INVITATION-L382",
            "API-IDENTITY-GET-INVITATIONS-VALIDATE-INVITATION-L437",
        ]
        baseline_overlap_seed_ids = [
            "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-BLOCK-SETTINGS-L1607",
            "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-ADMIN-SET-MAX-BLOCK-L1698",
        ]
        self.assertEqual(set(new_five_seed_ids) | set(baseline_overlap_seed_ids), set(eighth_slice_seed_ids))
        self.assertTrue(set(new_five_seed_ids).isdisjoint(set(baseline_overlap_seed_ids)))
        self.assertTrue(set(new_five_seed_ids).isdisjoint(prior_36_seed_set))
        self.assertTrue(set(new_five_seed_ids).isdisjoint(baseline_seed_set))
        self.assertTrue(set(baseline_overlap_seed_ids).isdisjoint(prior_36_seed_set))
        self.assertTrue(set(baseline_overlap_seed_ids).issubset(baseline_seed_set))

        # Validate seed occurrences and distinct seed IDs
        all_seed_ids = [seed_id for record in records for seed_id in record["seed_ids"]]
        self.assertEqual(len(all_seed_ids), 58)
        self.assertEqual(len(set(all_seed_ids)), 57)

        # Validate selector occurrences and distinct selectors
        all_selectors = [selector for record in records for selector in record["test_selectors"]]
        self.assertEqual(len(all_selectors), 73)
        self.assertEqual(len(set(all_selectors)), 73)

        # Verify that each recorded SHA-256 equals current bytes of its referenced file
        for record in records:
            char_file_path = repo_root / record["path"]
            self.assertTrue(char_file_path.is_file(), f"Characterization test file not found at {char_file_path}")
            char_bytes = char_file_path.read_bytes()
            char_sha256 = hashlib.sha256(char_bytes).hexdigest()
            self.assertEqual(record["sha256"], char_sha256)


if __name__ == "__main__":
    unittest.main()
