"""Fixed authority record and characterized-contract prerequisite oracle.

Stage: P1-00b (docs/refactor/two-server/section-1/stages/P1-00b.md)
Assignment: WA-P1-00B-BUSINESS-LIVE-20261023-803B
Parent assignment: WA-P1-00B-FREEZE-20260904-001

Stdlib and accepted-accounting-reader helper only. Executes no application,
session, database, Git, or Provider calls and registers no canonical packet.
"""

from __future__ import annotations

import ast
import copy
import hashlib
from pathlib import Path
import stat
import tempfile
from typing import Any
import unittest
from unittest.mock import patch

import tests.test_p1_00b_characterization_notification_mark_all_accounting as mark_all_accounting

SOURCE_PATH = (
    "tests/test_p1_00b_characterization_internal_authority_execute_failure.py"
)
SOURCE_SHA256 = (
    "d7a60d5f329d0889a2fcba3d52b28cb0b41c72c51c913e278f3bfdf3637903d1"
)
SOURCE_UTF8_BYTES = 15378
SOURCE_MODE = "100644"
SOURCE_LAST_COMMIT = "f4dd10018ef832881894e276bfa214e070ce7422"
SOURCE_PARENT = "973e2a28092a8718c412718135eab181c3ecd4fd"
CANONICAL_SYMBOL = "api/routers/sessions.py:internal_session_authority_check"
SEED_ID = "API-AUTH-POST-SESSIONS-INTERNAL-SESSION-AUTHORITY-CHECK-L539"
SCENARIO_KEY = "internal_authority_execute_exception_before_inspection"
TEST_CLASS = "InternalAuthorityExecuteFailureCharacterizationTests"
TEST_METHOD = "test_execute_exception_propagates_before_authority_inspection"
TEST_SELECTOR = f"{SOURCE_PATH}::{TEST_CLASS}::{TEST_METHOD}"

_EXPECTED_RECORD: dict[str, Any] = {
    "path": SOURCE_PATH,
    "sha256": SOURCE_SHA256,
    "utf8_bytes": SOURCE_UTF8_BYTES,
    "mode": SOURCE_MODE,
    "last_commit": SOURCE_LAST_COMMIT,
    "parent": SOURCE_PARENT,
    "canonical_symbol": CANONICAL_SYMBOL,
    "seed_ids": [SEED_ID],
    "scenarios": [SCENARIO_KEY],
    "test_selectors": [TEST_SELECTOR],
    "characterized_contract": {
        "request_body": {
            "call_count": 1,
            "await_count": 1,
        },
        "headers_get_order": [
            {"header": "X-Timestamp", "default": None},
            {"header": "X-Signature", "default": None},
            {"header": "X-API-Key", "default": None},
        ],
        "synthetic_verifier": {
            "target": "api.routers.sessions.verify_session_authority_signature",
            "call_count": 1,
            "return_value": True,
            "positional_body_comparison": "equality_only",
            "positional_body_identity_claimed": False,
            "keyword_keys": ["timestamp", "signature", "api_key"],
        },
        "event_order": [
            "body_call",
            "body_await",
            "header:X-Timestamp",
            "header:X-Signature",
            "header:X-API-Key",
            "verifier",
            "orm_execute",
        ],
        "pre_driver_orm_select": {
            "statement_count": 1,
            "entity": "User",
            "table": "users",
            "filter_column": "id",
            "filter_operator": "eq",
            "target_user_id": 5079,
            "compiled_where": "users.id = :id_1",
            "compiled_params": {"id_1": 5079},
            "is_select": True,
            "is_column_load": False,
            "is_relationship_load": False,
        },
        "exception_propagation": {
            "exception_type": "RuntimeError",
            "sentinel_message": (
                "characterization-internal-authority-db-execute-failure-sentinel"
            ),
            "identical_exception_object": True,
            "propagates_before_authority_inspection": True,
        },
        "authority_inspection": {
            "target": "api.routers.sessions.inspect_local_session_authority",
            "call_count": 0,
            "await_count": 0,
        },
        "driver_sql": {
            "statement_count": 0,
            "statements": [],
        },
        "session_mutation_and_state": {
            "guarded_async_session_sync_methods": ["add", "add_all"],
            "guarded_async_session_async_methods": [
                "delete",
                "flush",
                "commit",
                "rollback",
                "refresh",
            ],
            "guarded_sync_session_methods": [
                "add",
                "add_all",
                "delete",
                "flush",
                "commit",
                "rollback",
                "refresh",
            ],
            "guarded_call_count": 0,
            "guarded_await_count": 0,
            "flush_called": False,
            "new_count": 0,
            "dirty_count": 0,
            "deleted_count": 0,
        },
        "network_and_redis_fence": {
            "denied_connects_delta": 0,
            "live_redis_constructs_delta": 0,
        },
    },
    "exclusions": {
        "verifier_internals": True,
        "verifier_body_identity_proof": True,
        "scalar_result_consumption": True,
        "inspection_internals": True,
        "success_outcome": True,
        "reset_outcome": True,
        "revocation_outcome": True,
        "persistence_side_effects": True,
        "retry_behavior": True,
        "process_reload": True,
        "concurrency": True,
        "origin_matrix": True,
        "http_depends_wiring": True,
        "full_seed_acceptance": True,
        "high_blocker_closure": True,
        "stage_acceptance": True,
    },
    "credits": {
        "added_behavior_units": 0,
        "seed_credit": 0,
        "origin_credit": 0,
        "stage_credit": 0,
        "reconciliation_credit": 0,
        "reconciled_units": 3,
        "group_total_units": 5,
        "reconciled_scenarios": 4,
        "group_total_scenarios": 7,
        "pending_five": 5,
        "canonical_packet_registered": False,
        "claims_accounting_composition": False,
        "claims_historical_git_or_provider_acceptance": False,
    },
    "unmet_follow_up_dependencies": {
        "canonical_union": True,
        "manifest_derive_composition_and_counts": True,
        "ledger_prefix_integration": True,
        "actual_authority_registration": True,
    },
}

_READER_HELPER = mark_all_accounting.NotificationMarkAllAccountingTests(
    methodName="runTest"
)


def make_authority_record() -> dict[str, Any]:
    """Return a fresh mutable copy of the fixed authority prerequisite record."""
    return copy.deepcopy(_EXPECTED_RECORD)


def _assert_exact_value(actual: Any, expected: Any, label: str = "record") -> None:
    if type(actual) is not type(expected):
        raise AssertionError(
            f"{label}: expected type {type(expected).__name__}, "
            f"got {type(actual).__name__}"
        )
    if isinstance(expected, dict):
        if set(actual.keys()) != set(expected.keys()):
            raise AssertionError(
                f"{label}: key mismatch: actual={sorted(actual.keys())} "
                f"expected={sorted(expected.keys())}"
            )
        for key, exp_val in expected.items():
            if type(key) is not str:
                raise AssertionError(f"{label}: non-str key {key!r}")
            _assert_exact_value(actual[key], exp_val, f"{label}.{key}")
        return
    if isinstance(expected, list):
        if len(actual) != len(expected):
            raise AssertionError(
                f"{label}: length {len(actual)} != expected {len(expected)}"
            )
        for idx, (act_item, exp_item) in enumerate(zip(actual, expected)):
            _assert_exact_value(act_item, exp_item, f"{label}[{idx}]")
        return
    if actual != expected:
        raise AssertionError(f"{label}: {actual!r} != {expected!r}")


def _validate_source_ast(source_text: str) -> None:
    tree = ast.parse(source_text)
    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == TEST_CLASS
    ]
    if len(classes) != 1:
        raise AssertionError(f"Expected sole class {TEST_CLASS}, found {len(classes)}")
    cls_node = classes[0]
    async_test_methods = {
        node.name: node
        for node in cls_node.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("test_")
    }
    sync_test_methods = [
        node.name
        for node in cls_node.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    ]
    if set(async_test_methods.keys()) != {TEST_METHOD} or sync_test_methods:
        raise AssertionError(
            f"Unexpected test methods in {TEST_CLASS}: "
            f"async={sorted(async_test_methods)}, sync={sync_test_methods}"
        )
    helper_methods = {
        node.name: node
        for node in cls_node.body
        if isinstance(node, ast.FunctionDef)
    }
    if "_assert_user_select_statement" not in helper_methods:
        raise AssertionError("Missing _assert_user_select_statement helper")

    method_node = async_test_methods[TEST_METHOD]
    called_names = {
        node.func.id
        for node in ast.walk(method_node)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    if "internal_session_authority_check" not in called_names:
        raise AssertionError("Missing internal_session_authority_check call in AST")

    string_literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    required_literals = {
        "X-Timestamp",
        "X-Signature",
        "X-API-Key",
        "timestamp",
        "signature",
        "api_key",
        "body_call",
        "body_await",
        "header:X-Timestamp",
        "header:X-Signature",
        "header:X-API-Key",
        "verifier",
        "orm_execute",
        "FROM users",
        "WHERE users.id = :id_1",
        "users.id = :id_1",
        "api.routers.sessions.verify_session_authority_signature",
        "api.routers.sessions.inspect_local_session_authority",
        "characterization-internal-authority-db-execute-failure-sentinel",
        "denied_connects",
        "live_redis_constructs",
    }
    missing_literals = required_literals - string_literals
    if missing_literals:
        raise AssertionError(f"Missing required source literals: {sorted(missing_literals)}")

    verifier_identity_Assertions = [
        node
        for node in ast.walk(method_node)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "assertIs"
        and any(
            isinstance(arg, ast.Name) and arg.id == "raw_body"
            for arg in node.args
        )
    ]
    if verifier_identity_Assertions:
        raise AssertionError("Source must not assert raw_body object identity")


def assert_authority_record(record: dict[str, Any]) -> None:
    """Validate the authority prerequisite record and physical accepted source."""
    _assert_exact_value(record, _EXPECTED_RECORD, "record")

    selector = record["test_selectors"][0]
    selector_parts = selector.split("::")
    if selector_parts != [SOURCE_PATH, TEST_CLASS, TEST_METHOD]:
        raise AssertionError(f"Invalid selector decomposition: {selector_parts!r}")

    raw_bytes = _READER_HELPER._read(record["path"])
    if len(raw_bytes) != SOURCE_UTF8_BYTES:
        raise AssertionError(
            f"Physical UTF-8 byte length {len(raw_bytes)} != {SOURCE_UTF8_BYTES}"
        )
    actual_sha = hashlib.sha256(raw_bytes).hexdigest()
    if actual_sha != SOURCE_SHA256:
        raise AssertionError(
            f"Physical SHA256 {actual_sha} != expected {SOURCE_SHA256}"
        )

    physical_path = mark_all_accounting.ROOT / record["path"]
    st_mode = physical_path.stat().st_mode
    if not stat.S_ISREG(st_mode) or (st_mode & 0o111) != 0:
        raise AssertionError(f"Physical file mode invalid for 100644: {oct(st_mode)}")

    _validate_source_ast(raw_bytes.decode("utf-8"))


class AuthorityRecordContractOracleTests(unittest.TestCase):
    def test_fixed_record_matches_physical_accepted_source(self) -> None:
        first = make_authority_record()
        second = make_authority_record()
        self.assertIsNot(first, second)
        self.assertIsNot(
            first["characterized_contract"],
            second["characterized_contract"],
        )
        self.assertEqual(first, second)

        assert_authority_record(first)

        first["seed_ids"].append("MUTATED-LOCAL-COPY")
        first["characterized_contract"]["request_body"]["call_count"] = 99
        first["credits"]["seed_credit"] = 5
        self.assertEqual(second["seed_ids"], [SEED_ID])
        self.assertEqual(
            second["characterized_contract"]["request_body"]["call_count"],
            1,
        )
        self.assertEqual(second["credits"]["seed_credit"], 0)
        assert_authority_record(second)

        original_bytes = _READER_HELPER._read(SOURCE_PATH)
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            tmp_source = tmp_root / SOURCE_PATH
            tmp_source.parent.mkdir(parents=True, exist_ok=True)
            tmp_source.write_bytes(original_bytes)
            tmp_source.chmod(0o644)
            with patch.object(mark_all_accounting, "ROOT", tmp_root):
                assert_authority_record(make_authority_record())

        assert_authority_record(make_authority_record())

    def test_domain_source_selector_and_overclaim_corruptions_reject(self) -> None:
        baseline = make_authority_record()
        assert_authority_record(baseline)

        mutations: list[tuple[str, Any]] = [
            (
                "wrong_domain_symbol",
                lambda r: r.__setitem__(
                    "canonical_symbol",
                    "api/routers/sessions.py:reset_session_authority",
                ),
            ),
            (
                "wrong_seed_id",
                lambda r: r.__setitem__(
                    "seed_ids",
                    ["API-NOTIFY-POST-NOTIFICATIONS-MARK-ALL-NOTIFICATIONS-READ-L318"],
                ),
            ),
            (
                "non_singleton_seed_ids",
                lambda r: r["seed_ids"].append(SEED_ID),
            ),
            (
                "wrong_scenario",
                lambda r: r.__setitem__(
                    "scenarios",
                    ["internal_session_verifier_failure_stops_before_db"],
                ),
            ),
            (
                "wrong_selector_method",
                lambda r: r.__setitem__(
                    "test_selectors",
                    [f"{SOURCE_PATH}::{TEST_CLASS}::test_nonexistent"],
                ),
            ),
            (
                "wrong_source_path",
                lambda r: r.__setitem__(
                    "path",
                    "tests/test_p1_00b_characterization_internal_reset_execute_failure.py",
                ),
            ),
            (
                "wrong_sha256",
                lambda r: r.__setitem__("sha256", "0" * 64),
            ),
            (
                "wrong_utf8_bytes",
                lambda r: r.__setitem__("utf8_bytes", SOURCE_UTF8_BYTES + 1),
            ),
            (
                "wrong_mode",
                lambda r: r.__setitem__("mode", "100755"),
            ),
            (
                "wrong_commit",
                lambda r: r.__setitem__("last_commit", SOURCE_PARENT),
            ),
            (
                "wrong_header_order",
                lambda r: r["characterized_contract"]["headers_get_order"].reverse(),
            ),
            (
                "verifier_body_identity_overclaim",
                lambda r: r["characterized_contract"]["synthetic_verifier"].update(
                    {
                        "positional_body_comparison": "identity",
                        "positional_body_identity_claimed": True,
                    }
                ),
            ),
            (
                "verifier_body_identity_exclusion_dropped",
                lambda r: r["exclusions"].__setitem__(
                    "verifier_body_identity_proof",
                    False,
                ),
            ),
            (
                "scalar_result_overclaim",
                lambda r: r["exclusions"].__setitem__(
                    "scalar_result_consumption",
                    False,
                ),
            ),
            (
                "persistence_overclaim",
                lambda r: r["exclusions"].__setitem__(
                    "persistence_side_effects",
                    False,
                ),
            ),
            (
                "inspection_called_drift",
                lambda r: r["characterized_contract"]["authority_inspection"].__setitem__(
                    "call_count",
                    1,
                ),
            ),
            (
                "driver_sql_drift",
                lambda r: r["characterized_contract"]["driver_sql"].__setitem__(
                    "statement_count",
                    1,
                ),
            ),
            (
                "nonzero_seed_credit_drift",
                lambda r: r["credits"].__setitem__("seed_credit", 1),
            ),
            (
                "nonzero_reconciliation_credit_drift",
                lambda r: r["credits"].__setitem__("reconciliation_credit", 1),
            ),
            (
                "reconciled_units_drift",
                lambda r: r["credits"].__setitem__("reconciled_units", 4),
            ),
            (
                "accounting_composition_overclaim",
                lambda r: r["credits"].__setitem__(
                    "claims_accounting_composition",
                    True,
                ),
            ),
            (
                "follow_up_dependency_dropped",
                lambda r: r["unmet_follow_up_dependencies"].__setitem__(
                    "actual_authority_registration",
                    False,
                ),
            ),
            (
                "bool_int_scalar_type_confusion",
                lambda r: r["credits"].__setitem__("seed_credit", False),
            ),
        ]

        for label, mutate in mutations:
            with self.subTest(case=label):
                corrupted = make_authority_record()
                mutate(corrupted)
                with self.assertRaises(AssertionError, msg=label):
                    assert_authority_record(corrupted)
                assert_authority_record(make_authority_record())

        original_bytes = _READER_HELPER._read(SOURCE_PATH)
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            tmp_source = tmp_root / SOURCE_PATH
            tmp_source.parent.mkdir(parents=True, exist_ok=True)

            same_len_corrupted = bytearray(original_bytes)
            same_len_corrupted[-1] = ord("#") if same_len_corrupted[-1] != ord("#") else ord("\n")
            tmp_source.write_bytes(bytes(same_len_corrupted))
            tmp_source.chmod(0o644)
            with patch.object(mark_all_accounting, "ROOT", tmp_root):
                with self.assertRaises(AssertionError):
                    assert_authority_record(make_authority_record())

            truncated_bytes = original_bytes[:-1]
            tmp_source.write_bytes(truncated_bytes)
            with patch.object(mark_all_accounting, "ROOT", tmp_root):
                with self.assertRaises(AssertionError):
                    assert_authority_record(make_authority_record())

            tmp_source.write_bytes(original_bytes)
            tmp_source.chmod(0o644)
            with patch.object(mark_all_accounting, "ROOT", tmp_root):
                assert_authority_record(make_authority_record())

        assert_authority_record(baseline)


if __name__ == "__main__":
    unittest.main()
