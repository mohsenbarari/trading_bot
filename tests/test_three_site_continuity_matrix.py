from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import unittest

MATRIX_PATH = (
    Path(__file__).resolve().parents[1] / "docs" / "THREE_SITE_CONTINUITY_MATRIX.md"
)
SYNC_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "core" / "sync_registry.py"
MATRIX_SCHEMA = "gold-trade-three-site-continuity-matrix-v1"
PHYSICAL_REPLAY_CONTRACT = "physical_base_wal_required"
P0_SLOTS = {
    "P0_UPLOAD_IN_FLIGHT",
    "P0_SESSION_AUTH_CONTINUITY",
    "P0_EXTERNAL_EFFECTS_DURING_IR_ISOLATION",
}
SELECTED_P0_POLICIES = {
    "P0_UPLOAD_IN_FLIGHT": "cancel_and_expire_unfinalized_uploads",
    "P0_SESSION_AUTH_CONTINUITY": "invalidate_sessions_on_promotion",
    "P0_EXTERNAL_EFFECTS_DURING_IR_ISOLATION": (
        "defer_external_effects_until_term_fenced_executor"
    ),
}


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AssertionError(f"duplicate continuity-matrix JSON key: {key}")
        result[key] = value
    return result


def _matrix() -> tuple[str, dict[str, object]]:
    source = MATRIX_PATH.read_text(encoding="utf-8")
    match = re.search(r"```json\n(?P<payload>\{.*?\})\n```", source, flags=re.DOTALL)
    if match is None:
        raise AssertionError("three-site continuity matrix JSON block is missing")
    return source, json.loads(match.group("payload"), object_pairs_hook=_strict_object)


def _legacy_registry_inventory() -> dict[str, str]:
    """Read only the registry literals without importing runtime settings."""

    tree = ast.parse(SYNC_REGISTRY_PATH.read_text(encoding="utf-8"))
    inventory: dict[str, str] = {}
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_entry"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and isinstance(node.args[1], ast.Attribute)
            and isinstance(node.args[1].value, ast.Name)
            and node.args[1].value.id == "SyncPolicy"
        ):
            continue
        table_name = node.args[0].value
        policy_name = node.args[1].attr
        if table_name in inventory:
            raise AssertionError(f"duplicate legacy registry table: {table_name}")
        inventory[table_name] = policy_name
    return inventory


class ThreeSiteContinuityMatrixTests(unittest.TestCase):
    def test_inventory_is_exactly_the_current_legacy_registry(self) -> None:
        _, matrix = _matrix()
        entries = matrix["entries"]
        self.assertIsInstance(entries, list)

        by_table = {
            entry["table"]: entry
            for entry in entries
            if isinstance(entry, dict) and isinstance(entry.get("table"), str)
        }
        self.assertEqual(len(by_table), len(entries), "matrix tables must be unique")

        registry = _legacy_registry_inventory()
        self.assertEqual(set(by_table), set(registry))
        for table_name, policy_name in registry.items():
            with self.subTest(table=table_name):
                self.assertEqual(by_table[table_name]["legacy_policy"], policy_name)
                self.assertEqual(by_table[table_name]["db_replay"], PHYSICAL_REPLAY_CONTRACT)

        counts = {}
        for entry in entries:
            policy = entry["legacy_policy"]
            counts[policy] = counts.get(policy, 0) + 1
        self.assertEqual(
            counts,
            {
                "SYNC": 23,
                "NO_SYNC": 19,
                "INTERNAL_BOOKKEEPING": 3,
            },
        )

    def test_p0_decisions_are_explicit_and_blocked(self) -> None:
        _, matrix = _matrix()
        self.assertEqual(matrix["schema"], MATRIX_SCHEMA)
        self.assertEqual(matrix["status"], "blocked/unresolved")
        self.assertEqual(matrix["db_replay_contract"], PHYSICAL_REPLAY_CONTRACT)

        p0_slots = matrix["p0_decision_slots"]
        self.assertEqual(set(p0_slots), P0_SLOTS)
        for slot_name, slot in p0_slots.items():
            with self.subTest(slot=slot_name):
                self.assertEqual(slot["status"], "blocked/unresolved")
                self.assertEqual(slot["selected_policy"], SELECTED_P0_POLICIES[slot_name])
                self.assertTrue(slot["profiles"])

        entries = matrix["entries"]
        p0_usage = {
            slot_name: {
                entry["table"]
                for entry in entries
                if slot_name in entry["p0_slots"]
            }
            for slot_name in P0_SLOTS
        }
        self.assertEqual(
            p0_usage["P0_UPLOAD_IN_FLIGHT"],
            {"chat_files", "messages", "upload_batches", "upload_sessions"},
        )
        self.assertEqual(
            p0_usage["P0_SESSION_AUTH_CONTINUITY"],
            {
                "session_login_requests",
                "single_session_recovery_admin_targets",
                "single_session_recovery_requests",
                "user_sessions",
            },
        )
        self.assertIn("market_channel_notice_receipts", p0_usage["P0_EXTERNAL_EFFECTS_DURING_IR_ISOLATION"])
        self.assertIn("telegram_notification_outbox", p0_usage["P0_EXTERNAL_EFFECTS_DURING_IR_ISOLATION"])

    def test_document_never_claims_that_legacy_no_sync_may_skip_standby_replay(self) -> None:
        source, matrix = _matrix()
        self.assertIn("do **not** say that a table may be absent from a", source)
        self.assertIn("exactly one application writer", source)
        self.assertEqual(len(matrix["entries"]), 45)


if __name__ == "__main__":
    unittest.main()
