import hashlib
import json
from pathlib import Path
import unittest


class RecentCharacterizationContinuationFourContractTests(unittest.TestCase):
    def test_valid_invitation_inputs_provenance_and_prior_seed_membership(self):
        repo_root = Path(__file__).resolve().parent.parent

        continuation_4_rel_path = (
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-4.json"
        )
        continuation_4_path = repo_root / continuation_4_rel_path
        self.assertTrue(
            continuation_4_path.is_file(),
            f"Continuation 4 file not found at {continuation_4_path}",
        )

        continuation_4_bytes = continuation_4_path.read_bytes()
        continuation_4_data = json.loads(continuation_4_bytes.decode("utf-8"))

        self.assertEqual(continuation_4_data.get("schema_version"), 1)
        self.assertEqual(continuation_4_data.get("stage"), "P1-00b")

        expected_predecessor_path = (
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-3.json"
        )
        expected_predecessor_sha = (
            "75e090c1ef0b42c935432664a1ea30bbb2d90094df5a4f185a64bd1ff85211df"
        )
        expected_predecessor_commit = "c7cc52cf61e5770348cbaa79ef8677804c62ee38"

        predecessor_binding = continuation_4_data.get("predecessor_inventory", {})
        self.assertEqual(predecessor_binding.get("path"), expected_predecessor_path)
        self.assertEqual(predecessor_binding.get("sha256"), expected_predecessor_sha)
        self.assertEqual(
            predecessor_binding.get("last_commit"),
            expected_predecessor_commit,
        )

        predecessor_file = repo_root / expected_predecessor_path
        self.assertTrue(predecessor_file.is_file())
        self.assertEqual(
            hashlib.sha256(predecessor_file.read_bytes()).hexdigest(),
            expected_predecessor_sha,
        )

        records = continuation_4_data.get("records", [])
        self.assertEqual(len(records), 7)

        record = records[0]
        expected_record_keys = {
            "path",
            "sha256",
            "last_commit",
            "seed_ids",
            "test_selectors",
        }
        self.assertEqual(set(record.keys()), expected_record_keys)

        expected_test_path = (
            "tests/test_p1_00b_characterization_admin_invitation_inputs_batch.py"
        )
        expected_test_sha = (
            "3961f3af3f4bacaceff115b37f6143f0c6b40b275459f507dd3d0d69a196b3ea"
        )
        expected_last_commit = "67cd61b9b4601394b61728aa314a592d8dd96492"
        self.assertEqual(record["path"], expected_test_path)
        self.assertEqual(record["sha256"], expected_test_sha)
        self.assertEqual(record["last_commit"], expected_last_commit)

        live_test_path = repo_root / expected_test_path
        self.assertTrue(live_test_path.is_file())
        live_test_bytes = live_test_path.read_bytes()
        self.assertEqual(
            hashlib.sha256(live_test_bytes).hexdigest(),
            expected_test_sha,
        )

        expected_selectors = [
            "tests/test_p1_00b_characterization_admin_invitation_inputs_batch.py::AdminInvitationInputsCharacterizationTests::test_account_name_valid_queue_suppresses_stale_delete_and_advances",
            "tests/test_p1_00b_characterization_admin_invitation_inputs_batch.py::AdminInvitationInputsCharacterizationTests::test_mobile_valid_queue_suppresses_stale_delete_and_advances",
        ]
        self.assertEqual(record["test_selectors"], expected_selectors)

        live_test_text = live_test_bytes.decode("utf-8")
        for selector in expected_selectors:
            method_name = selector.split("::")[-1]
            self.assertIn(f"def {method_name}(", live_test_text)

        expected_seed_ids = [
            "BOT-ADMIN-MESSAGE-ADMIN-PROCESS-INVITATION-ACCOUNT-NAME-L183",
            "BOT-ADMIN-MESSAGE-ADMIN-PROCESS-INVITATION-MOBILE-L230",
        ]
        self.assertEqual(record["seed_ids"], expected_seed_ids)

        surface_bot_path = (
            repo_root / "docs/refactor/two-server/section-1/inventory/surface-bot.json"
        )
        self.assertTrue(surface_bot_path.is_file())
        surface_bot_data = json.loads(surface_bot_path.read_bytes().decode("utf-8"))
        surface_bot_seed_set = {
            entry["behavior_id"] for entry in surface_bot_data.get("entries", [])
        }
        for seed_id in expected_seed_ids:
            self.assertIn(seed_id, surface_bot_seed_set)

        base_evidence_path = (
            repo_root / "docs/refactor/two-server/section-1/inventory/recent-characterization.json"
        )
        self.assertTrue(base_evidence_path.is_file())
        base_evidence_data = json.loads(base_evidence_path.read_bytes().decode("utf-8"))

        norm_record = next(
            (
                r
                for r in base_evidence_data.get("records", [])
                if r.get("last_commit") == "2a84eeb0bf7ea187f3a4df1690917eaafd3f15f3"
            ),
            None,
        )
        self.assertIsNotNone(norm_record)
        norm_seed_ids = norm_record.get("seed_ids", [])
        self.assertIn(expected_seed_ids[0], norm_seed_ids)
        self.assertIn(expected_seed_ids[1], norm_seed_ids)

        all_base_seeds = {
            seed_id
            for r in base_evidence_data.get("records", [])
            for seed_id in r.get("seed_ids", [])
        }
        record_seed_set = set(expected_seed_ids)
        self.assertTrue(record_seed_set.issubset(all_base_seeds))
        self.assertEqual(len(record_seed_set - all_base_seeds), 0)
        self.assertEqual(len(record_seed_set.intersection(all_base_seeds)), 2)

    def test_historical_invalid_invitation_inputs_provenance_and_prior_seed_membership(self):
        repo_root = Path(__file__).resolve().parent.parent

        continuation_4_rel_path = (
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-4.json"
        )
        continuation_4_path = repo_root / continuation_4_rel_path
        self.assertTrue(
            continuation_4_path.is_file(),
            f"Continuation 4 file not found at {continuation_4_path}",
        )

        continuation_4_bytes = continuation_4_path.read_bytes()
        continuation_4_data = json.loads(continuation_4_bytes.decode("utf-8"))

        self.assertEqual(continuation_4_data.get("schema_version"), 1)
        self.assertEqual(continuation_4_data.get("stage"), "P1-00b")

        records = continuation_4_data.get("records", [])
        self.assertEqual(len(records), 7)

        record = records[1]
        expected_record_keys = {
            "path",
            "sha256",
            "last_commit",
            "seed_ids",
            "test_selectors",
        }
        self.assertEqual(set(record.keys()), expected_record_keys)

        expected_test_path = (
            "tests/test_p1_00b_characterization_admin_invitation_inputs_batch.py"
        )
        expected_historical_sha = (
            "283e2e87da3049f4c74a1c16914bf129c2384fd9b204e8eb8412cdc22a56c091"
        )
        expected_last_commit = "d8ca931a61cdf1eb00fc31a796d38d4445e97fae"
        self.assertEqual(record["path"], expected_test_path)
        self.assertEqual(record["sha256"], expected_historical_sha)
        self.assertEqual(record["last_commit"], expected_last_commit)

        live_test_path = repo_root / expected_test_path
        self.assertTrue(live_test_path.is_file())
        live_test_bytes = live_test_path.read_bytes()
        self.assertEqual(
            hashlib.sha256(live_test_bytes).hexdigest(),
            "3961f3af3f4bacaceff115b37f6143f0c6b40b275459f507dd3d0d69a196b3ea",
        )

        expected_selectors = [
            "tests/test_p1_00b_characterization_admin_invitation_inputs_batch.py::AdminInvitationInputsCharacterizationTests::test_account_name_invalid_queue_suppresses_stale_prompt_delete",
            "tests/test_p1_00b_characterization_admin_invitation_inputs_batch.py::AdminInvitationInputsCharacterizationTests::test_mobile_invalid_queue_suppresses_stale_prompt_delete",
        ]
        self.assertEqual(record["test_selectors"], expected_selectors)

        live_test_text = live_test_bytes.decode("utf-8")
        for selector in expected_selectors:
            method_name = selector.split("::")[-1]
            self.assertIn(f"def {method_name}(", live_test_text)

        import ast
        tree = ast.parse(live_test_text)
        class_node = next(
            (
                n
                for n in tree.body
                if isinstance(n, ast.ClassDef)
                and n.name == "AdminInvitationInputsCharacterizationTests"
            ),
            None,
        )
        self.assertIsNotNone(class_node)
        live_methods = [
            n.name
            for n in class_node.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for selector in expected_selectors:
            method_name = selector.split("::")[-1]
            self.assertIn(method_name, live_methods)

        provenance_rel_path = (
            "tests/test_p1_00b_characterization_admin_invitation_invalid_d8_provenance.py"
        )
        provenance_path = repo_root / provenance_rel_path
        self.assertTrue(provenance_path.is_file())
        self.assertEqual(
            hashlib.sha256(provenance_path.read_bytes()).hexdigest(),
            "e65a8846ac903bd36a54968607a8c116eb1d495fd04a753113d70f521203557d",
        )

        from tests.test_p1_00b_characterization_admin_invitation_invalid_d8_provenance import (
            reconstruct_d8_source,
            InvitationInvalidD8ProvenanceTests,
        )
        reconstructed = reconstruct_d8_source(live_test_text)
        reconstructed_sha = hashlib.sha256(reconstructed.encode("utf-8")).hexdigest()
        self.assertEqual(reconstructed_sha, expected_historical_sha)

        provenance_case = InvitationInvalidD8ProvenanceTests()
        provenance_case.test_historical_invalid_queue_source_and_selectors()

        expected_seed_ids = [
            "BOT-ADMIN-MESSAGE-ADMIN-PROCESS-INVITATION-ACCOUNT-NAME-L183",
            "BOT-ADMIN-MESSAGE-ADMIN-PROCESS-INVITATION-MOBILE-L230",
        ]
        self.assertEqual(record["seed_ids"], expected_seed_ids)

        surface_bot_path = (
            repo_root / "docs/refactor/two-server/section-1/inventory/surface-bot.json"
        )
        self.assertTrue(surface_bot_path.is_file())
        surface_bot_data = json.loads(surface_bot_path.read_bytes().decode("utf-8"))
        surface_bot_seed_set = {
            entry["behavior_id"] for entry in surface_bot_data.get("entries", [])
        }
        for seed_id in expected_seed_ids:
            self.assertIn(seed_id, surface_bot_seed_set)

        base_evidence_path = (
            repo_root / "docs/refactor/two-server/section-1/inventory/recent-characterization.json"
        )
        self.assertTrue(base_evidence_path.is_file())
        base_evidence_data = json.loads(base_evidence_path.read_bytes().decode("utf-8"))

        norm_record = next(
            (
                r
                for r in base_evidence_data.get("records", [])
                if r.get("last_commit") == "2a84eeb0bf7ea187f3a4df1690917eaafd3f15f3"
            ),
            None,
        )
        self.assertIsNotNone(norm_record)
        norm_seed_ids = norm_record.get("seed_ids", [])
        self.assertIn(expected_seed_ids[0], norm_seed_ids)
        self.assertIn(expected_seed_ids[1], norm_seed_ids)

        all_base_seeds = {
            seed_id
            for r in base_evidence_data.get("records", [])
            for seed_id in r.get("seed_ids", [])
        }
        record_seed_set = set(expected_seed_ids)
        self.assertTrue(record_seed_set.issubset(all_base_seeds))
        self.assertEqual(len(record_seed_set - all_base_seeds), 0)
        self.assertEqual(len(record_seed_set.intersection(all_base_seeds)), 2)

    def test_chat_search_messages_scoped_provenance_and_selector_definitions(self):
        repo_root = Path(__file__).resolve().parent.parent

        continuation_4_rel_path = (
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-4.json"
        )
        continuation_4_path = repo_root / continuation_4_rel_path
        self.assertTrue(
            continuation_4_path.is_file(),
            f"Continuation 4 file not found at {continuation_4_path}",
        )

        continuation_4_bytes = continuation_4_path.read_bytes()
        continuation_4_data = json.loads(continuation_4_bytes.decode("utf-8"))

        self.assertEqual(continuation_4_data.get("schema_version"), 1)
        self.assertEqual(continuation_4_data.get("stage"), "P1-00b")

        records = continuation_4_data.get("records", [])
        self.assertEqual(len(records), 7)

        record = records[3]
        expected_record_keys = {
            "path",
            "sha256",
            "last_commit",
            "seed_ids",
            "test_selectors",
        }
        self.assertEqual(set(record.keys()), expected_record_keys)

        expected_test_path = (
            "tests/test_p1_00b_characterization_chat_search_messages_scoped.py"
        )
        expected_test_sha = (
            "8ac7d8e5e7012ccd4a65272d70cf69984922ecbbd033de438804e579fc5aee36"
        )
        expected_last_commit = "6b9ed96828a3fbf5e85bde014c345dbdd652abd4"
        self.assertEqual(record["path"], expected_test_path)
        self.assertEqual(record["sha256"], expected_test_sha)
        self.assertEqual(record["last_commit"], expected_last_commit)

        live_test_path = repo_root / expected_test_path
        self.assertTrue(live_test_path.is_file())
        live_test_bytes = live_test_path.read_bytes()
        self.assertEqual(
            hashlib.sha256(live_test_bytes).hexdigest(),
            expected_test_sha,
        )

        expected_selectors = [
            "tests/test_p1_00b_characterization_chat_search_messages_scoped.py::CharacterizeChatSearchMessagesScopedTests::test_allowed_customer_target_search_uses_scoped_query_and_preserves_order",
            "tests/test_p1_00b_characterization_chat_search_messages_scoped.py::CharacterizeChatSearchMessagesScopedTests::test_disallowed_customer_target_returns_404_before_query",
        ]
        self.assertEqual(record["test_selectors"], expected_selectors)

        live_test_text = live_test_bytes.decode("utf-8")
        for selector in expected_selectors:
            method_name = selector.split("::")[-1]
            self.assertIn(f"def {method_name}(", live_test_text)

        import ast
        tree = ast.parse(live_test_text)
        class_node = next(
            (
                n
                for n in tree.body
                if isinstance(n, ast.ClassDef)
                and n.name == "CharacterizeChatSearchMessagesScopedTests"
            ),
            None,
        )
        self.assertIsNotNone(class_node)
        live_methods = [
            n.name
            for n in class_node.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for selector in expected_selectors:
            method_name = selector.split("::")[-1]
            self.assertIn(method_name, live_methods)

        expected_seed_ids = [
            "API-MSG-GET-CHAT-SEARCH-MESSAGES-L800",
        ]
        self.assertEqual(record["seed_ids"], expected_seed_ids)

        surface_api_path = (
            repo_root / "docs/refactor/two-server/section-1/inventory/surface-api.json"
        )
        self.assertTrue(surface_api_path.is_file())
        surface_api_data = json.loads(surface_api_path.read_bytes().decode("utf-8"))
        surface_api_seed_set = {
            entry["behavior_id"] for entry in surface_api_data.get("entries", [])
        }
        for seed_id in expected_seed_ids:
            self.assertIn(seed_id, surface_api_seed_set)

    def test_chat_poll_nonempty_provenance_and_selector_definitions(self):
        repo_root = Path(__file__).resolve().parent.parent

        continuation_4_rel_path = (
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-4.json"
        )
        continuation_4_path = repo_root / continuation_4_rel_path
        self.assertTrue(
            continuation_4_path.is_file(),
            f"Continuation 4 file not found at {continuation_4_path}",
        )

        continuation_4_bytes = continuation_4_path.read_bytes()
        continuation_4_data = json.loads(continuation_4_bytes.decode("utf-8"))

        self.assertEqual(continuation_4_data.get("schema_version"), 1)
        self.assertEqual(continuation_4_data.get("stage"), "P1-00b")

        records = continuation_4_data.get("records", [])
        self.assertEqual(len(records), 7)

        record = records[4]
        expected_record_keys = {
            "path",
            "sha256",
            "last_commit",
            "seed_ids",
            "test_selectors",
        }
        self.assertEqual(set(record.keys()), expected_record_keys)

        expected_test_path = (
            "tests/test_p1_00b_characterization_chat_poll_nonempty.py"
        )
        expected_test_sha = (
            "4c984d1bf37b52320063c41850c486c9ace353ba5a0ea6b15629386a7a7c5127"
        )
        expected_last_commit = "7f87a32c7eb08e01472e4d78329c7592e3e8c11a"
        self.assertEqual(record["path"], expected_test_path)
        self.assertEqual(record["sha256"], expected_test_sha)
        self.assertEqual(record["last_commit"], expected_last_commit)

        live_test_path = repo_root / expected_test_path
        self.assertTrue(live_test_path.is_file())
        live_test_bytes = live_test_path.read_bytes()
        self.assertEqual(
            hashlib.sha256(live_test_bytes).hexdigest(),
            expected_test_sha,
        )

        expected_selectors = [
            "tests/test_p1_00b_characterization_chat_poll_nonempty.py::ChatPollNonemptyCharacterizationTests::test_mixed_visible_direct_and_room_rows_aggregate_exact_response",
            "tests/test_p1_00b_characterization_chat_poll_nonempty.py::ChatPollNonemptyCharacterizationTests::test_zero_unread_attribute_row_retains_muted_ids_and_room_mentions",
        ]
        self.assertEqual(record["test_selectors"], expected_selectors)

        live_test_text = live_test_bytes.decode("utf-8")
        for selector in expected_selectors:
            method_name = selector.split("::")[-1]
            self.assertIn(f"def {method_name}(", live_test_text)

        import ast
        tree = ast.parse(live_test_text)
        class_node = next(
            (
                n
                for n in tree.body
                if isinstance(n, ast.ClassDef)
                and n.name == "ChatPollNonemptyCharacterizationTests"
            ),
            None,
        )
        self.assertIsNotNone(class_node)
        live_methods = [
            n.name
            for n in class_node.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for selector in expected_selectors:
            method_name = selector.split("::")[-1]
            self.assertIn(method_name, live_methods)

        expected_seed_ids = [
            "API-MSG-GET-CHAT-POLL-MESSAGES-L2225",
        ]
        self.assertEqual(record["seed_ids"], expected_seed_ids)

        surface_api_path = (
            repo_root / "docs/refactor/two-server/section-1/inventory/surface-api.json"
        )
        self.assertTrue(surface_api_path.is_file())
        surface_api_data = json.loads(surface_api_path.read_bytes().decode("utf-8"))
        surface_api_seed_set = {
            entry["behavior_id"] for entry in surface_api_data.get("entries", [])
        }
        for seed_id in expected_seed_ids:
            self.assertIn(seed_id, surface_api_seed_set)

    def test_list_active_sessions_provenance_and_selector_definitions(self):
        repo_root = Path(__file__).resolve().parent.parent

        continuation_4_rel_path = (
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-4.json"
        )
        continuation_4_path = repo_root / continuation_4_rel_path
        self.assertTrue(
            continuation_4_path.is_file(),
            f"Continuation 4 file not found at {continuation_4_path}",
        )

        continuation_4_bytes = continuation_4_path.read_bytes()
        continuation_4_data = json.loads(continuation_4_bytes.decode("utf-8"))

        self.assertEqual(continuation_4_data.get("schema_version"), 1)
        self.assertEqual(continuation_4_data.get("stage"), "P1-00b")

        records = continuation_4_data.get("records", [])
        self.assertEqual(len(records), 7)

        record = records[5]
        expected_record_keys = {
            "path",
            "sha256",
            "last_commit",
            "seed_ids",
            "test_selectors",
        }
        self.assertEqual(set(record.keys()), expected_record_keys)

        expected_test_path = (
            "tests/test_p1_00b_characterization_list_active_sessions.py"
        )
        expected_test_sha = (
            "1dc4d947424977a1aa0c522e55a8f22490de61a4d24f97fe77a0fbf2c18a4fda"
        )
        expected_last_commit = "48865b90ac007a0e60f6b97f9056730a204cdcd6"
        self.assertEqual(record["path"], expected_test_path)
        self.assertEqual(record["sha256"], expected_test_sha)
        self.assertEqual(record["last_commit"], expected_last_commit)

        live_test_path = repo_root / expected_test_path
        self.assertTrue(live_test_path.is_file())
        live_test_bytes = live_test_path.read_bytes()
        self.assertEqual(
            hashlib.sha256(live_test_bytes).hexdigest(),
            expected_test_sha,
        )

        expected_selectors = [
            "tests/test_p1_00b_characterization_list_active_sessions.py::ListActiveSessionsCharacterizationTests::test_list_active_sessions_valid_bearer_token_marks_current_session",
            "tests/test_p1_00b_characterization_list_active_sessions.py::ListActiveSessionsCharacterizationTests::test_list_active_sessions_missing_authorization_header",
            "tests/test_p1_00b_characterization_list_active_sessions.py::ListActiveSessionsCharacterizationTests::test_list_active_sessions_undecodable_bearer_token",
        ]
        self.assertEqual(record["test_selectors"], expected_selectors)

        live_test_text = live_test_bytes.decode("utf-8")
        for selector in expected_selectors:
            method_name = selector.split("::")[-1]
            self.assertIn(f"def {method_name}(", live_test_text)

        import ast
        tree = ast.parse(live_test_text)
        class_node = next(
            (
                n
                for n in tree.body
                if isinstance(n, ast.ClassDef)
                and n.name == "ListActiveSessionsCharacterizationTests"
            ),
            None,
        )
        self.assertIsNotNone(class_node)
        live_methods = [
            n.name
            for n in class_node.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for selector in expected_selectors:
            method_name = selector.split("::")[-1]
            self.assertIn(method_name, live_methods)

        expected_seed_ids = [
            "API-AUTH-GET-SESSIONS-LIST-ACTIVE-SESSIONS-L1046",
        ]
        self.assertEqual(record["seed_ids"], expected_seed_ids)

        surface_api_path = (
            repo_root / "docs/refactor/two-server/section-1/inventory/surface-api.json"
        )
        self.assertTrue(surface_api_path.is_file())
        surface_api_data = json.loads(surface_api_path.read_bytes().decode("utf-8"))
        surface_api_seed_set = {
            entry["behavior_id"] for entry in surface_api_data.get("entries", [])
        }
        for seed_id in expected_seed_ids:
            self.assertIn(seed_id, surface_api_seed_set)

    def test_get_pending_requests_session_gates_provenance_and_selector_definitions(self):
        repo_root = Path(__file__).resolve().parent.parent

        continuation_4_rel_path = (
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-4.json"
        )
        continuation_4_path = repo_root / continuation_4_rel_path
        self.assertTrue(
            continuation_4_path.is_file(),
            f"Continuation 4 file not found at {continuation_4_path}",
        )

        continuation_4_bytes = continuation_4_path.read_bytes()
        continuation_4_data = json.loads(continuation_4_bytes.decode("utf-8"))

        self.assertEqual(continuation_4_data.get("schema_version"), 1)
        self.assertEqual(continuation_4_data.get("stage"), "P1-00b")

        records = continuation_4_data.get("records", [])
        self.assertEqual(len(records), 7)

        record = records[6]
        expected_record_keys = {
            "path",
            "sha256",
            "last_commit",
            "seed_ids",
            "test_selectors",
        }
        self.assertEqual(set(record.keys()), expected_record_keys)

        expected_test_path = (
            "tests/test_p1_00b_characterization_get_pending_requests_session_gates.py"
        )
        expected_test_sha = (
            "7c980046bac48cce58c31ce11059710a357e4700b277453e3858ee4870451ba7"
        )
        expected_last_commit = "0a10332571afea72348e43f507e8d0f5fbadd4e2"
        self.assertEqual(record["path"], expected_test_path)
        self.assertEqual(record["sha256"], expected_test_sha)
        self.assertEqual(record["last_commit"], expected_last_commit)

        live_test_path = repo_root / expected_test_path
        self.assertTrue(live_test_path.is_file())
        live_test_bytes = live_test_path.read_bytes()
        self.assertEqual(
            hashlib.sha256(live_test_bytes).hexdigest(),
            expected_test_sha,
        )

        expected_selectors = [
            "tests/test_p1_00b_characterization_get_pending_requests_session_gates.py::CharacterizationGetPendingRequestsSessionGatesTests::test_get_pending_requests_decode_failure_falls_back_to_query",
            "tests/test_p1_00b_characterization_get_pending_requests_session_gates.py::CharacterizationGetPendingRequestsSessionGatesTests::test_get_pending_requests_missing_caller_session_short_circuits",
            "tests/test_p1_00b_characterization_get_pending_requests_session_gates.py::CharacterizationGetPendingRequestsSessionGatesTests::test_get_pending_requests_no_authorization_header_queries_pending",
            "tests/test_p1_00b_characterization_get_pending_requests_session_gates.py::CharacterizationGetPendingRequestsSessionGatesTests::test_get_pending_requests_malformed_token_sid_raises_value_error_before_db",
        ]
        self.assertEqual(record["test_selectors"], expected_selectors)

        live_test_text = live_test_bytes.decode("utf-8")
        for selector in expected_selectors:
            method_name = selector.split("::")[-1]
            self.assertIn(f"def {method_name}(", live_test_text)

        import ast
        tree = ast.parse(live_test_text)
        class_node = next(
            (
                n
                for n in tree.body
                if isinstance(n, ast.ClassDef)
                and n.name == "CharacterizationGetPendingRequestsSessionGatesTests"
            ),
            None,
        )
        self.assertIsNotNone(class_node)
        live_methods = [
            n.name
            for n in class_node.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for selector in expected_selectors:
            method_name = selector.split("::")[-1]
            self.assertIn(method_name, live_methods)

        expected_seed_ids = [
            "API-AUTH-GET-SESSIONS-GET-PENDING-REQUESTS-L619",
        ]
        self.assertEqual(record["seed_ids"], expected_seed_ids)

        surface_api_path = (
            repo_root / "docs/refactor/two-server/section-1/inventory/surface-api.json"
        )
        self.assertTrue(surface_api_path.is_file())
        surface_api_data = json.loads(surface_api_path.read_bytes().decode("utf-8"))
        surface_api_seed_set = {
            entry["behavior_id"] for entry in surface_api_data.get("entries", [])
        }
        for seed_id in expected_seed_ids:
            self.assertIn(seed_id, surface_api_seed_set)


if __name__ == "__main__":
    unittest.main()
