"""P1-00b: first executed seed partition beyond admin toggle-block."""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from api.routers.chat import send_message
from api.routers.chat_schemas import MessageSend
from bot.handlers.admin_broadcast import toggle_group_recipient, toggle_selected_user
from bot.handlers.trade_execute import handle_channel_trade, handle_channel_trade_public
from bot.utils import redis_helpers
from core.enums import MessageType, UserAccountStatus, UserRole
from core.services.telegram_admin_broadcast_service import TELEGRAM_ADMIN_BROADCAST_GROUP_ORDINARY
from models.message import Message
from models.offer import OfferStatus
from tests.test_p1_00_characterization_admin_account_status import ACCOUNT_SEED

REPO_ROOT = Path(__file__).resolve().parents[1]
INV_DIR = REPO_ROOT / "docs/refactor/two-server/section-1/inventory"

BROADCAST_GROUP_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-TOGGLE-GROUP-RECIPIENT-L240"
BROADCAST_USER_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-TOGGLE-SELECTED-USER-L334"
MSG_SEND_SEED = "API-MSG-POST-CHAT-SEND-MESSAGE-L2028"
TRADE_CHANNEL_SEED = "BOT-TRADE-CALLBACK-QUERY-TRADE-EXECUTE-HANDLE-CHANNEL-TRADE-L810"
TRADE_PUBLIC_SEED = "BOT-TRADE-CALLBACK-QUERY-TRADE-EXECUTE-HANDLE-CHANNEL-TRADE-PUBLIC-L829"

SLICE_EXECUTED_SEED_IDS = {
    ACCOUNT_SEED,
    BROADCAST_GROUP_SEED,
    BROADCAST_USER_SEED,
    MSG_SEND_SEED,
    TRADE_CHANNEL_SEED,
    TRADE_PUBLIC_SEED,
}


class IsolatedConfirmRedis:
    """Client-construction isolate: eval fails so confirmation uses memory fallback."""

    async def eval(self, *args, **kwargs):
        raise RuntimeError("isolated-redis")

    async def aclose(self):
        return None


class FakeState:
    def __init__(self, **data):
        self.data = dict(data)
        self.updates = 0

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)
        self.updates += 1


class MsgSession:
    def __init__(self, sync_session, *, fail_commit=False):
        self._session = sync_session
        self.commit_count = 0
        self.fail_commit = fail_commit

    def add(self, obj):
        self._session.add(obj)

    async def commit(self):
        if self.fail_commit and self.commit_count == 0:
            raise RuntimeError("injected send_message_commit_failure")
        self._session.commit()
        self.commit_count += 1

    async def refresh(self, obj, attribute_names=None):
        self._session.refresh(obj)

    async def get(self, model, ident, **kwargs):
        return self._session.get(model, ident)

    async def rollback(self):
        self._session.rollback()


class _SessionBox:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _engine(path: str):
    engine = create_engine(f"sqlite:///{path}", future=True)

    def _on_connect(conn, _rec):
        conn.execute("PRAGMA foreign_keys=OFF")

    event.listen(engine, "connect", _on_connect)
    return engine


def _broadcast_callback(data: str):
    return SimpleNamespace(
        data=data,
        id="cb-broadcast",
        from_user=SimpleNamespace(id=1),
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=200),
            message_id=50,
            edit_reply_markup=AsyncMock(),
        ),
    )


def _trade_callback():
    return SimpleNamespace(
        id="cb-trade-slice",
        from_user=SimpleNamespace(id=300),
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=200),
            message_id=50,
            edit_reply_markup=AsyncMock(),
        ),
    )


def _trade_user(**overrides):
    data = {
        "id": 5,
        "telegram_id": 555,
        "mobile_number": "0935",
        "account_name": "buyer",
        "role": UserRole.STANDARD,
        "account_status": UserAccountStatus.ACTIVE,
        "is_deleted": False,
        "trading_restricted_until": None,
        "limitations_expire_at": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _trade_offer(**overrides):
    data = {
        "id": 7,
        "user_id": 2,
        "status": OfferStatus.ACTIVE,
        "home_server": "foreign",
        "remaining_quantity": 10,
        "quantity": 10,
        "is_wholesale": False,
        "lot_sizes": None,
        "offer_public_id": "ofr_slice",
        "user": SimpleNamespace(id=2),
        "commodity": SimpleNamespace(id=1),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class P100bSeedSliceExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_account_status_seed_commits_then_denies_without_write(self):
        from tests.test_p1_00_characterization_admin_account_status import (
            P100AdminAccountStatusCharacterizationTests,
        )

        helper = P100AdminAccountStatusCharacterizationTests()
        await helper.test_handler_uses_live_transition_then_may_commit_again()
        await helper.test_handler_denied_paths_do_not_write()

    async def test_broadcast_toggle_group_recipient_flips_fsm_and_denies_injects(self):
        state = FakeState(target_groups=[])
        answers = []
        markup_calls = {"n": 0}

        async def _answer(callback, text=None, show_alert=False):
            answers.append((text, show_alert))

        async def _markup(*_a, **_k):
            markup_calls["n"] += 1

        callback = _broadcast_callback(f"tgb:gt:{TELEGRAM_ADMIN_BROADCAST_GROUP_ORDINARY}")
        with patch("bot.handlers.admin_broadcast.answer_callback_query_via_runtime", new=_answer), patch(
            "bot.handlers.admin_broadcast.edit_callback_reply_markup_via_runtime",
            new=_markup,
        ):
            await toggle_group_recipient(
                callback,
                state,
                user=SimpleNamespace(role=UserRole.STANDARD),
            )
            self.assertEqual(state.data.get("target_groups"), [])
            self.assertEqual(state.updates, 0)
            self.assertEqual(answers[-1][0], "عدم دسترسی")

            admin = SimpleNamespace(role=UserRole.SUPER_ADMIN)
            await toggle_group_recipient(callback, state, user=admin)
            self.assertEqual(state.data["target_groups"], [TELEGRAM_ADMIN_BROADCAST_GROUP_ORDINARY])
            await toggle_group_recipient(callback, state, user=admin)
            self.assertEqual(state.data["target_groups"], [])
            await toggle_group_recipient(
                _broadcast_callback("tgb:gt:not-a-group"),
                FakeState(target_groups=[]),
                user=admin,
            )
        self.assertEqual(markup_calls["n"], 2)
        self.assertTrue(any(text == "گروه نامعتبر است." for text, _alert in answers))

    async def test_broadcast_toggle_selected_user_flips_fsm_and_denies_injects(self):
        state = FakeState(selected_user_ids=[], last_search_query="")
        answers = []

        async def _answer(callback, text=None, show_alert=False):
            answers.append((text, show_alert))

        callback = _broadcast_callback("tgb:tu:9")
        with patch("bot.handlers.admin_broadcast.answer_callback_query_via_runtime", new=_answer), patch(
            "bot.handlers.admin_broadcast.edit_callback_message_via_runtime",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_broadcast.search_telegram_admin_broadcast_recipients",
            new=AsyncMock(side_effect=AssertionError("search reached without query")),
        ):
            await toggle_selected_user(
                callback,
                state,
                user=SimpleNamespace(role=UserRole.STANDARD),
            )
            self.assertEqual(state.data.get("selected_user_ids"), [])
            self.assertEqual(state.updates, 0)

            admin = SimpleNamespace(role=UserRole.SUPER_ADMIN)
            await toggle_selected_user(callback, state, user=admin)
            self.assertEqual(state.data["selected_user_ids"], [9])
            await toggle_selected_user(callback, state, user=admin)
            self.assertEqual(state.data["selected_user_ids"], [])
            await toggle_selected_user(_broadcast_callback("tgb:tu:bad"), FakeState(), user=admin)
        self.assertTrue(any(text == "کاربر نامعتبر است." for text, _alert in answers))

    async def test_send_message_persists_publishes_retries_and_fails_before_commit_injects(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "msg-send.sqlite")
            engine = _engine(db_path)
            Message.__table__.create(engine, checkfirst=True)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            sender = SimpleNamespace(id=1)
            receiver = SimpleNamespace(id=9)
            published: list[tuple] = []
            thread_calls = {"n": 0}

            async def _prepare(_db, **_k):
                return receiver, "hello-seed"

            async def _thread(*_a, **_k):
                thread_calls["n"] += 1

            async def _reload(_db, message_id, **_k):
                return _db._session.get(Message, message_id)

            async def _serialize(_db, message, viewer_user_id=None):
                return SimpleNamespace(id=message.id, content=message.content, sender_id=sender.id)

            async def _ident(*_a, **_k):
                return {}

            async def _name(*_a, **_k):
                return "alice"

            def _serializer(_identity_map, _serialized):
                return lambda message: {"id": message.id, "content": message.content}

            async def _publish(user_id, event, payload):
                published.append((user_id, event, payload))

            fail_db = MsgSession(Session(), fail_commit=True)
            with patch("api.routers.chat.prepare_direct_message_send", new=_prepare), patch(
                "core.services.chat_service.sync_direct_message_threading",
                new=_thread,
            ), patch(
                "core.services.chat_service.reload_direct_message",
                new=_reload,
            ), patch(
                "api.routers.chat._serialize_direct_message_with_accountant_contract",
                new=_serialize,
            ), patch(
                "api.routers.chat.load_accountant_chat_identity_map",
                new=_ident,
            ), patch(
                "api.routers.chat.resolve_direct_sender_display_name",
                new=_name,
            ), patch(
                "api.routers.chat._build_direct_message_accountant_serializer",
                new=_serializer,
            ), patch(
                "api.routers.chat.publish_user_event",
                new=_publish,
            ):
                with self.assertRaisesRegex(RuntimeError, "injected send_message_commit_failure"):
                    await send_message(
                        MessageSend(receiver_id=9, content="hello-seed"),
                        current_user=sender,
                        db=fail_db,
                    )
                with sqlite3.connect(db_path) as conn:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)
                self.assertEqual(published, [])

                ok_db = MsgSession(Session())
                first = await send_message(
                    MessageSend(receiver_id=9, content="hello-seed"),
                    current_user=sender,
                    db=ok_db,
                )
                self.assertEqual(first.id, 1)
                self.assertEqual(published[-1][0], 9)
                self.assertEqual(published[-1][1], "chat:message")
                self.assertGreaterEqual(ok_db.commit_count, 1)

                again = await send_message(
                    MessageSend(receiver_id=9, content="hello-seed"),
                    current_user=sender,
                    db=ok_db,
                )
                self.assertEqual(again.id, 2)
                self.assertEqual(len(published), 2)
                self.assertEqual(thread_calls["n"], 2)
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 2)
            engine.dispose()

    async def test_channel_trade_handlers_deny_then_confirm_then_execute_injects(self):
        redis_helpers._memory_fallback["confirmations"].clear()
        answers: list[str] = []
        execute = AsyncMock(return_value=None)
        offer = _trade_offer()
        session = SimpleNamespace(refresh=AsyncMock(), rollback=AsyncMock())

        async def _answer(callback, text=None, show_alert=False):
            answers.append(text or "")

        async def _isolated_redis():
            return IsolatedConfirmRedis()

        bot = SimpleNamespace(send_message=AsyncMock())
        channel_data = SimpleNamespace(offer_id=7, amount=1)
        public_data = SimpleNamespace(offer_public_id="ofr_slice", amount=1)
        user = _trade_user()
        patches = [
            patch("bot.handlers.trade_execute.answer_callback_query_via_runtime", new=_answer),
            patch("bot.handlers.trade_execute.AsyncSessionLocal", return_value=_SessionBox(session)),
            patch("bot.handlers.trade_execute._load_callback_offer", new=AsyncMock(return_value=offer)),
            patch(
                "bot.handlers.trade_execute.is_trade_blocked_by_principals",
                new=AsyncMock(return_value=(False, None, None, None)),
            ),
            patch("bot.handlers.trade_execute.is_remote_home", return_value=False),
            patch(
                "bot.handlers.trade_execute.validate_offer_trade_amount",
                return_value=(True, None, 1, [1]),
            ),
            patch("bot.utils.redis_helpers.get_redis_client", new=_isolated_redis),
            patch(
                "bot.handlers.trade_execute._execute_confirmed_channel_trade_via_shared_command",
                new=execute,
            ),
            patch("bot.handlers.trade_execute.edit_callback_reply_markup_via_runtime", new=AsyncMock()),
            patch("bot.handlers.trade_execute.upsert_trade_suggestion_record", new=AsyncMock()),
            patch("bot.handlers.trade_execute.schedule_trade_suggestion_cleanup", new=AsyncMock()),
            patch("bot.handlers.trade_execute.schedule_trade_suggestion_pending_reset", new=AsyncMock()),
            patch("bot.handlers.trade_execute.build_trade_amount_buttons", return_value="KB"),
        ]
        for item in patches:
            item.start()
        try:
            await handle_channel_trade(
                _trade_callback(),
                channel_data,
                user=_trade_user(id=2),
                bot=bot,
            )
            self.assertEqual(execute.await_count, 0)
            self.assertGreaterEqual(session.refresh.await_count, 1)

            await handle_channel_trade(_trade_callback(), channel_data, user=user, bot=bot)
            self.assertEqual(execute.await_count, 0)
            self.assertIn("برای تایید دوباره روی همان دکمه بزنید", answers[-1])

            await handle_channel_trade(_trade_callback(), channel_data, user=user, bot=bot)
            self.assertEqual(execute.await_count, 1)

            redis_helpers._memory_fallback["confirmations"].clear()
            await handle_channel_trade_public(_trade_callback(), public_data, user=user, bot=bot)
            self.assertEqual(execute.await_count, 1)
            await handle_channel_trade_public(_trade_callback(), public_data, user=user, bot=bot)
            self.assertEqual(execute.await_count, 2)

            execute.side_effect = RuntimeError("injected channel_trade_execute_failure")
            redis_helpers._memory_fallback["confirmations"].clear()
            await handle_channel_trade(_trade_callback(), channel_data, user=user, bot=bot)
            with self.assertRaisesRegex(RuntimeError, "injected channel_trade_execute_failure"):
                await handle_channel_trade(_trade_callback(), channel_data, user=user, bot=bot)
        finally:
            for item in reversed(patches):
                item.stop()
            redis_helpers._memory_fallback["confirmations"].clear()
        self.assertGreaterEqual(session.refresh.await_count, 1)


if __name__ == "__main__":
    unittest.main()
