"""P1-00b: executed behavior partitions for some seeds; others stay blocked."""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin_users import handle_admin_toggle_block
from bot.handlers.trade_execute import _handle_channel_trade
from core.enums import MessageType, UserAccountStatus, UserRole
from core.services.chat_service import persist_sent_direct_message
from models.message import Message

REPO_ROOT = Path(__file__).resolve().parents[1]
INV_DIR = REPO_ROOT / "docs/refactor/two-server/section-1/inventory"
EXECUTED_PARTITIONS = {
    ("ADMIN", "TOGGLE_MUTATION"),
    ("MSG", "CRUD_MUTATION"),
    ("TRADE", "CRUD_MUTATION"),
}


def _load_json(name: str) -> dict:
    return json.loads((INV_DIR / name).read_text(encoding="utf-8"))


def _all_seeds() -> list[dict]:
    seeds = []
    for name, section in (
        ("surface-api.json", "api"),
        ("surface-bot.json", "bot"),
        ("surface-web.json", "web"),
        ("surface-jobs.json", "jobs"),
    ):
        for entry in _load_json(name)["entries"]:
            item = dict(entry)
            item["_section"] = section
            seeds.append(item)
    return seeds


class P100bSeedPartitionExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_toggle_partition_commits_then_denies_without_write(self):
        """ADMIN TOGGLE_MUTATION: live handle_admin_toggle_block flips and fail-closes."""
        from tests.test_p1_00_characterization_admin_toggle_behavior import (
            FakeSession,
            P100AdminToggleBehaviorTests,
            _callback,
            _target,
        )

        helper = P100AdminToggleBehaviorTests()
        await helper.test_toggle_block_second_invoke_flips_again_and_has_no_command_identity()
        await helper.test_toggle_block_denied_paths_do_not_write()
        self.assertTrue(_target().can_block_users)
        self.assertTrue(callable(_callback))
        self.assertTrue(callable(FakeSession))

    async def test_msg_crud_partition_persists_one_row_and_one_thread_sync(self):
        """MSG CRUD_MUTATION: persist_sent_direct_message writes one row then threads."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from tests.test_p1_00b_characterization_family_overlap import YieldingSession, _engine, _exec_sql, MSG_SQL

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "seed-msg.sqlite")
            engine = _engine(db_path)
            _exec_sql(engine, MSG_SQL)
            Message.__table__.create(engine, checkfirst=True)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            db = YieldingSession(Session())
            calls = {"n": 0}

            async def _thread(*_a, **_k):
                calls["n"] += 1

            try:
                with patch(
                    "core.services.chat_service.sync_direct_message_threading",
                    new=_thread,
                ), patch(
                    "core.services.chat_service.reload_direct_message",
                    new=AsyncMock(side_effect=lambda _db, message_id, **_k: SimpleNamespace(id=message_id)),
                ):
                    message = await persist_sent_direct_message(
                        db,
                        sender=SimpleNamespace(id=1),
                        receiver=SimpleNamespace(id=2),
                        content="seed-partition",
                        message_type=MessageType.TEXT,
                    )
            finally:
                db.close()
                engine.dispose()
            self.assertIsNotNone(message)
            self.assertEqual(calls["n"], 1)
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)

    async def test_trade_telegram_entry_partition_rejects_missing_user(self):
        """TRADE CRUD via Telegram entry: missing user never opens a session."""
        callback = SimpleNamespace(
            id="cb-seed",
            from_user=SimpleNamespace(id=1),
            answer=AsyncMock(),
            message=SimpleNamespace(chat=SimpleNamespace(id=2), message_id=3, edit_reply_markup=AsyncMock()),
        )
        with patch("bot.handlers.trade_execute.AsyncSessionLocal") as session_factory, patch(
            "bot.handlers.trade_execute._execute_trade_authoritatively_with_transient_retry",
            new=AsyncMock(side_effect=AssertionError("trade reached")),
        ):
            await _handle_channel_trade(
                callback,
                SimpleNamespace(offer_id=7, amount=1),
                None,
                SimpleNamespace(send_message=AsyncMock()),
            )
        session_factory.assert_not_called()
        callback.answer.assert_awaited()

    def test_unsupported_seeds_keep_the_explicit_high_blocker(self):
        payload = _load_json("scenario-records.json")
        rules = {(rule["family"], rule["operation_kind"]) for rule in payload["seed_mapping"]["rules"]}
        seeds = _all_seeds()
        unsupported = [
            seed["behavior_id"]
            for seed in seeds
            if (seed["family"], seed["operation_kind"]) not in EXECUTED_PARTITIONS
        ]
        self.assertGreater(len(unsupported), 0)
        self.assertLess(len(EXECUTED_PARTITIONS), len(rules))
        registry = {item["blocker_id"]: item for item in payload["blocker_registry"]["items"]}
        item = registry["P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE"]
        self.assertEqual(item["disposition"], "BLOCKED_EXPLICIT")
        self.assertIn(str(len(unsupported)), item["reason"])


if __name__ == "__main__":
    unittest.main()
