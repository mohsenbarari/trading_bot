"""HIGH-1: admin toggles commit without a command identity; retry flips again."""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import ast
import inspect
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin_users import handle_admin_toggle_block
from core.enums import UserAccountStatus, UserRole

REPO_ROOT = Path(__file__).resolve().parents[1]
INV_DIR = REPO_ROOT / "docs/refactor/two-server/section-1/inventory"
TOGGLE_SEEDS = {
    "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-ADMIN-TOGGLE-BLOCK-L1649": {
        "handler": "admin_users.handle_admin_toggle_block",
        "operation_kind": "TOGGLE_MUTATION",
        "contract_id": "BC-ADMIN-TOGGLE_MUTATION-V1",
    },
}


def _load_json(name: str) -> dict:
    return json.loads((INV_DIR / name).read_text(encoding="utf-8"))


class FakeScalarOneResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeSession:
    def __init__(self, value):
        self.value = value
        self.commit = AsyncMock()
        self.flush = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, stmt):
        return FakeScalarOneResult(self.value)


def _callback(data: str):
    return SimpleNamespace(
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
        data=data,
    )


def _target(*, role=UserRole.STANDARD, can_block_users=True, account_status=UserAccountStatus.ACTIVE):
    return SimpleNamespace(
        id=9,
        role=role,
        account_name="ali",
        can_block_users=can_block_users,
        max_blocked_users=5,
        account_status=account_status,
        trading_restricted_until=None,
        max_daily_trades=None,
        max_active_commodities=None,
        max_daily_requests=None,
        is_deleted=False,
    )


class P100AdminToggleBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def test_toggle_block_second_invoke_flips_again_and_has_no_command_identity(self):
        target = _target(can_block_users=True)
        session = FakeSession(target)
        answers = []

        async def _record_answer(callback, *args, **kwargs):
            answers.append(args[0] if args else kwargs.get("text"))
            return await callback.answer(*args, **kwargs)

        source = inspect.getsource(handle_admin_toggle_block)
        self.assertNotIn("idempotency", source)
        self.assertIn("can_block_users = not", source)
        self.assertNotIn("create_user_notification", source)
        self.assertNotIn("change_log", source)

        with patch("core.admin_authority.current_server", return_value="iran"), patch(
            "bot.handlers.admin_users.AsyncSessionLocal", return_value=session
        ), patch("bot.handlers.admin_users.get_block_settings_keyboard", return_value="KB"), patch(
            "bot.handlers.admin_users.answer_callback_query_via_runtime",
            new=AsyncMock(side_effect=_record_answer),
        ), patch(
            "bot.handlers.admin_users.edit_callback_message_via_runtime",
            new=AsyncMock(),
        ) as edit:
            await handle_admin_toggle_block(_callback("admin_toggle_block_9"), user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
            self.assertFalse(target.can_block_users)
            await handle_admin_toggle_block(_callback("admin_toggle_block_9"), user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
            self.assertTrue(target.can_block_users)
        self.assertEqual(session.commit.await_count, 2)
        self.assertEqual(len(answers), 2)
        self.assertIn("غیرفعال", answers[0])
        self.assertIn("فعال", answers[1])
        self.assertEqual(edit.await_count, 2)

    async def test_toggle_block_denied_paths_do_not_write(self):
        protected = _target(role=UserRole.SUPER_ADMIN)
        session = FakeSession(protected)
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=session):
            await handle_admin_toggle_block(_callback("admin_toggle_block_9"), user=None)
            await handle_admin_toggle_block(
                _callback("admin_toggle_block_9"),
                user=SimpleNamespace(role=UserRole.STANDARD),
            )
            await handle_admin_toggle_block(
                _callback("admin_toggle_block_9"),
                user=SimpleNamespace(role=UserRole.MIDDLE_MANAGER),
            )
        session.commit.assert_not_awaited()
        self.assertTrue(protected.can_block_users)

        missing = FakeSession(None)
        callback = _callback("admin_toggle_block_9")
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=missing):
            await handle_admin_toggle_block(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        missing.commit.assert_not_awaited()
        callback.answer.assert_awaited()

    def test_toggle_seeds_do_not_use_crud_retry_identity(self):
        payload = _load_json("scenario-records.json")
        seeds = {
            item["behavior_id"]: item
            for item in _load_json("surface-bot.json")["entries"]
        }
        rules = payload["seed_mapping"]["rules"]
        contract = next(item for item in payload["behavior_contracts"] if item["contract_id"] == "BC-ADMIN-TOGGLE_MUTATION-V1")
        self.assertEqual(contract["timeouts_retries"]["status"], "NOT_APPLICABLE")
        self.assertIn("flips", contract["timeouts_retries"]["reason"])
        self.assertEqual(contract["idempotency_key"]["status"], "NOT_APPLICABLE")
        self.assertIn("no command identity", contract["idempotency_key"]["reason"])
        crud = next(item for item in payload["behavior_contracts"] if item["contract_id"] == "BC-ADMIN-CRUD_MUTATION-V1")
        self.assertNotEqual(contract["contract_id"], crud["contract_id"])
        for behavior_id, expected in TOGGLE_SEEDS.items():
            seed = seeds[behavior_id]
            self.assertEqual(seed["handler"], expected["handler"])
            self.assertEqual(seed["operation_kind"], "TOGGLE_MUTATION")
            match = [
                rule
                for rule in rules
                if rule["family"] == seed["family"] and rule["operation_kind"] == seed["operation_kind"]
            ]
            self.assertEqual([item["contract_id"] for item in match], [expected["contract_id"]])
            self.assertIn("tests/test_p1_00_characterization_admin_toggle_behavior.py", seed["test_refs"])

    def test_remaining_admin_crud_seeds_are_not_boolean_toggles(self):
        seeds = [
            item
            for item in _load_json("surface-bot.json")["entries"]
            if item["family"] == "ADMIN" and item["operation_kind"] == "CRUD_MUTATION"
        ]
        self.assertGreaterEqual(len(seeds), 10)
        for seed in seeds:
            module, func_name = seed["handler"].split(".", 1)
            path = REPO_ROOT / "bot/handlers" / f"{module}.py"
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            node = next(
                item
                for item in tree.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == func_name
            )
            source = ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""
            self.assertNotIn(" = not ", source, seed["behavior_id"])
            self.assertNotIn("UserAccountStatus.INACTIVE if", source, seed["behavior_id"])


if __name__ == "__main__":
    unittest.main()
