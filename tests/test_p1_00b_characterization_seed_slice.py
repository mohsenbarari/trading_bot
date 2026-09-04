"""P1-00b: first executed seed partition beyond admin toggle-block."""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

import schemas
from api.routers.chat import delete_message, send_message, update_message
from api.routers.chat_schemas import MessageSend, MessageUpdate
from api.routers.commodities import (
    add_alias_to_commodity,
    create_commodity,
    delete_alias,
    delete_commodity,
    update_alias,
    update_commodity_name,
)
from api.routers.trading_settings import (
    MarketScheduleOverrideUpsert,
    TradingSettingsUpdate,
    create_market_override,
    delete_market_override,
    reset_settings,
    update_market_override,
    update_settings,
)
from bot.handlers.admin_broadcast import (
    confirm_telegram_admin_broadcast,
    toggle_group_recipient,
    toggle_selected_user,
)
from bot.handlers.admin_commodities import (
    handle_add_aliases_and_create,
    handle_alias_add_name,
    handle_alias_delete_yes,
    handle_alias_edit_name,
    handle_commodity_edit_name,
    handle_delete_yes,
)
from bot.handlers.admin_users import (
    handle_admin_max_block_set,
    handle_limit_confirm,
    handle_set_user_role,
    handle_user_block_actions,
    handle_user_unblock,
    handle_user_unlimit,
    process_custom_max_block,
)
from bot.handlers.trade_execute import handle_channel_trade, handle_channel_trade_public
from bot.utils import redis_helpers
from core.commodity_defaults import IMAM_COMMODITY_NAME
from core.enums import MessageType, UserAccountStatus, UserRole
from core.services.telegram_admin_broadcast_service import TELEGRAM_ADMIN_BROADCAST_GROUP_ORDINARY
from models.commodity import Commodity, CommodityAlias
from models.market_schedule_override import MarketScheduleOverride, MarketScheduleOverrideType
from models.message import Message
from models.notification import Notification
from models.offer import OfferStatus
from models.telegram_admin_broadcast import TelegramAdminBroadcastAudienceType
from models.user import UserRole as ModelUserRole
from tests.test_p1_00_characterization_admin_account_status import (
    ACCOUNT_SEED,
    MappedAccountUser,
    _make_store,
    _persist_user,
    _reload_user,
)
from tests.test_p1_00b_characterization_family_overlap import (
    ADMIN_SQL,
    YieldingSession,
    _engine as _overlap_engine,
    _exec_sql,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
INV_DIR = REPO_ROOT / "docs/refactor/two-server/section-1/inventory"

BROADCAST_GROUP_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-TOGGLE-GROUP-RECIPIENT-L240"
BROADCAST_USER_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-TOGGLE-SELECTED-USER-L334"
MSG_SEND_SEED = "API-MSG-POST-CHAT-SEND-MESSAGE-L2028"
SET_ROLE_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-SET-USER-ROLE-L1090"
UNBLOCK_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-UNBLOCK-L925"
BROADCAST_CONFIRM_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-CONFIRM-TELEGRAM-ADMIN-BROADCAST-L593"
UNLIMIT_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-UNLIMIT-L998"
MAX_BLOCK_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-ADMIN-MAX-BLOCK-SET-L1723"
BLOCK_ACTIONS_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-BLOCK-ACTIONS-L798"
LIMIT_CONFIRM_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-LIMIT-CONFIRM-L1438"
MSG_UPDATE_SEED = "API-MSG-PUT-CHAT-UPDATE-MESSAGE-L2079"
MSG_DELETE_SEED = "API-MSG-DELETE-CHAT-DELETE-MESSAGE-L2183"
CUSTOM_MAX_BLOCK_SEED = "BOT-ADMIN-MESSAGE-ADMIN-USERS-PROCESS-CUSTOM-MAX-BLOCK-L1799"
CREATE_COMMODITY_SEED = "API-ADMIN-POST-COMMODITIES-CREATE-COMMODITY-L143"
UPDATE_COMMODITY_SEED = "API-ADMIN-PUT-COMMODITIES-UPDATE-COMMODITY-NAME-L189"
DELETE_COMMODITY_SEED = "API-ADMIN-DELETE-COMMODITIES-DELETE-COMMODITY-L243"
ADD_ALIAS_SEED = "API-ADMIN-POST-COMMODITIES-ADD-ALIAS-TO-COMMODITY-L340"
UPDATE_ALIAS_SEED = "API-ADMIN-PUT-COMMODITIES-UPDATE-ALIAS-L388"
DELETE_ALIAS_SEED = "API-ADMIN-DELETE-COMMODITIES-DELETE-ALIAS-L437"
CREATE_OVERRIDE_SEED = "API-ADMIN-POST-TRADING-SETTINGS-CREATE-MARKET-OVERRIDE-L222"
UPDATE_OVERRIDE_SEED = "API-ADMIN-PUT-TRADING-SETTINGS-UPDATE-MARKET-OVERRIDE-L253"
DELETE_OVERRIDE_SEED = "API-ADMIN-DELETE-TRADING-SETTINGS-DELETE-MARKET-OVERRIDE-L288"
BOT_ALIAS_ADD_SEED = "BOT-ADMIN-MESSAGE-ADMIN-COMMODITIES-HANDLE-ALIAS-ADD-NAME-L365"
BOT_ALIAS_EDIT_SEED = "BOT-ADMIN-MESSAGE-ADMIN-COMMODITIES-HANDLE-ALIAS-EDIT-NAME-L429"
BOT_ALIAS_DELETE_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-COMMODITIES-HANDLE-ALIAS-DELETE-YES-L487"
BOT_COMMODITY_EDIT_SEED = "BOT-ADMIN-MESSAGE-ADMIN-COMMODITIES-HANDLE-COMMODITY-EDIT-NAME-L537"
BOT_COMMODITY_CREATE_SEED = "BOT-ADMIN-MESSAGE-ADMIN-COMMODITIES-HANDLE-ADD-ALIASES-AND-CREATE-L622"
BOT_COMMODITY_DELETE_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-COMMODITIES-HANDLE-DELETE-YES-L685"
UPDATE_SETTINGS_SEED = "API-ADMIN-PUT-TRADING-SETTINGS-UPDATE-SETTINGS-L306"
RESET_SETTINGS_SEED = "API-ADMIN-POST-TRADING-SETTINGS-RESET-SETTINGS-L353"
TRADE_CHANNEL_SEED = "BOT-TRADE-CALLBACK-QUERY-TRADE-EXECUTE-HANDLE-CHANNEL-TRADE-L810"
TRADE_PUBLIC_SEED = "BOT-TRADE-CALLBACK-QUERY-TRADE-EXECUTE-HANDLE-CHANNEL-TRADE-PUBLIC-L829"
UNEVIDENCED_TRADE_GATEWAY_SEEDS = {TRADE_CHANNEL_SEED, TRADE_PUBLIC_SEED}
TOGGLE_BLOCK_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-ADMIN-TOGGLE-BLOCK-L1649"

SLICE_EXECUTED_SEED_IDS = {
    ACCOUNT_SEED,
    BROADCAST_GROUP_SEED,
    BROADCAST_USER_SEED,
    MSG_SEND_SEED,
    SET_ROLE_SEED,
    UNBLOCK_SEED,
    BROADCAST_CONFIRM_SEED,
    UNLIMIT_SEED,
    MAX_BLOCK_SEED,
    BLOCK_ACTIONS_SEED,
    LIMIT_CONFIRM_SEED,
    MSG_UPDATE_SEED,
    MSG_DELETE_SEED,
    CUSTOM_MAX_BLOCK_SEED,
    CREATE_COMMODITY_SEED,
    UPDATE_COMMODITY_SEED,
    DELETE_COMMODITY_SEED,
    ADD_ALIAS_SEED,
    UPDATE_ALIAS_SEED,
    DELETE_ALIAS_SEED,
    CREATE_OVERRIDE_SEED,
    UPDATE_OVERRIDE_SEED,
    DELETE_OVERRIDE_SEED,
    BOT_ALIAS_ADD_SEED,
    BOT_ALIAS_EDIT_SEED,
    BOT_ALIAS_DELETE_SEED,
    BOT_COMMODITY_EDIT_SEED,
    BOT_COMMODITY_CREATE_SEED,
    BOT_COMMODITY_DELETE_SEED,
    UPDATE_SETTINGS_SEED,
    RESET_SETTINGS_SEED,
}

SEED_EVIDENCE_NODES = {
    TOGGLE_BLOCK_SEED: (
        "tests/test_p1_00_characterization_admin_toggle_behavior.py"
        "::P100AdminToggleBehaviorTests"
        ".test_toggle_block_second_invoke_flips_again_and_has_no_command_identity"
    ),
    ACCOUNT_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_account_status_seed_commits_then_denies_without_write"
    ),
    BROADCAST_GROUP_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_broadcast_toggle_group_recipient_flips_fsm_and_denies_injects"
    ),
    BROADCAST_USER_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_broadcast_toggle_selected_user_flips_fsm_and_denies_injects"
    ),
    MSG_SEND_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_send_message_persists_publishes_retries_and_fails_before_commit_injects"
    ),
    SET_ROLE_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_set_user_role_persists_then_denies_and_fails_before_commit_injects"
    ),
    UNBLOCK_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_user_unblock_clears_restriction_then_denies_without_write_injects"
    ),
    BROADCAST_CONFIRM_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_confirm_telegram_admin_broadcast_persists_then_replays_key_injects"
    ),
    UNLIMIT_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_user_unlimit_clears_caps_then_denies_without_write_injects"
    ),
    MAX_BLOCK_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_admin_max_block_set_persists_then_denies_injects"
    ),
    BLOCK_ACTIONS_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_user_block_actions_persists_restriction_then_denies_injects"
    ),
    LIMIT_CONFIRM_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_limit_confirm_persists_caps_then_denies_injects"
    ),
    MSG_UPDATE_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_update_message_edits_then_denies_and_fails_injects"
    ),
    MSG_DELETE_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_delete_message_soft_deletes_then_denies_injects"
    ),
    CUSTOM_MAX_BLOCK_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_process_custom_max_block_persists_then_denies_injects"
    ),
    CREATE_COMMODITY_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_create_commodity_persists_then_rejects_digits_and_conflict"
    ),
    UPDATE_COMMODITY_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_update_commodity_name_persists_then_404s"
    ),
    DELETE_COMMODITY_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_delete_commodity_removes_then_404s_and_locks_imam"
    ),
    ADD_ALIAS_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_add_alias_to_commodity_persists_then_conflicts"
    ),
    UPDATE_ALIAS_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_update_alias_persists_then_404s"
    ),
    DELETE_ALIAS_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_delete_alias_removes_then_404s"
    ),
    CREATE_OVERRIDE_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_create_market_override_persists_then_rejects_duplicate"
    ),
    UPDATE_OVERRIDE_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_update_market_override_persists_then_404s"
    ),
    DELETE_OVERRIDE_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_delete_market_override_removes_then_404s"
    ),
    BOT_ALIAS_ADD_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_bot_alias_add_name_persists_then_denies_without_user"
    ),
    BOT_ALIAS_EDIT_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_bot_alias_edit_name_persists_then_denies_without_user"
    ),
    BOT_ALIAS_DELETE_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_bot_alias_delete_yes_persists_then_denies_non_admin"
    ),
    BOT_COMMODITY_EDIT_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_bot_commodity_edit_name_persists_then_denies_without_user"
    ),
    BOT_COMMODITY_CREATE_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_bot_add_aliases_and_create_persists_then_denies_without_user"
    ),
    BOT_COMMODITY_DELETE_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_bot_delete_yes_removes_then_denies_non_admin"
    ),
    UPDATE_SETTINGS_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_update_settings_persists_then_rejects_invalid_hours"
    ),
    RESET_SETTINGS_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_reset_settings_restores_defaults_after_update"
    ),
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
        self.state = None

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)
        self.updates += 1

    async def set_state(self, state):
        self.state = state

    async def clear(self):
        self.data.clear()


class RoleSession:
    """Async adapter over mapped sqlite users. Does not mock the durable write."""

    def __init__(self, sync_session, *, user_id=9, fail_commit=False):
        self._session = sync_session
        self.user_id = user_id
        self.fail_commit = fail_commit
        self.commit_count = 0

    async def execute(self, statement, params=None):
        blob = f"{statement} {getattr(statement, 'text', '')}"
        if "FROM users" in blob and "notifications" not in blob.lower():
            loaded = self._session.get(MappedAccountUser, self.user_id)
            return SimpleNamespace(scalar_one_or_none=lambda: loaded)
        return self._session.execute(statement)

    def add(self, obj):
        self._session.add(obj)

    async def flush(self):
        self._session.flush()

    async def refresh(self, obj, attribute_names=None):
        self._session.refresh(obj)

    async def commit(self):
        if self.fail_commit and self.commit_count == 0:
            raise RuntimeError("injected admin_role_commit_failure")
        self._session.commit()
        self.commit_count += 1

    async def rollback(self):
        self._session.rollback()


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
        loaded = self._session.get(model, ident)
        created = getattr(loaded, "created_at", None)
        if loaded is not None and created is not None and created.tzinfo is None:
            loaded.created_at = created.replace(tzinfo=timezone.utc)
        return loaded

    async def rollback(self):
        self._session.rollback()


class CrudSession:
    """Async adapter over isolated sqlite. Does not mock the durable write."""

    def __init__(self, sync_session, *, fail_commit=False, fail_label="injected_crud_commit_failure"):
        self._session = sync_session
        self.fail_commit = fail_commit
        self.fail_label = fail_label
        self.commit_count = 0

    def add(self, obj):
        self._session.add(obj)

    async def execute(self, statement, params=None):
        return self._session.execute(statement)

    async def get(self, model, ident, **kwargs):
        return self._session.get(model, ident)

    async def delete(self, obj):
        self._session.delete(obj)

    async def commit(self):
        if self.fail_commit and self.commit_count == 0:
            raise RuntimeError(self.fail_label)
        self._session.commit()
        self.commit_count += 1

    async def refresh(self, obj, attribute_names=None):
        self._session.refresh(obj, attribute_names=attribute_names)

    async def rollback(self):
        self._session.rollback()


class _HttpResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://isolated-commodities.local")
            raise httpx.HTTPStatusError("isolated-http-status", request=request, response=self)

    def json(self):
        return self._payload


def _isolated_commodity_client(db):
    class IsolatedCommodityClient:
        """Client-construction isolate: no socket; routes to live commodity callables."""

        def __init__(self, *args, **kwargs):
            self.db = db

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def _call(self, method, url, payload=None):
            parts = [item for item in urlparse(url).path.rstrip("/").split("/") if item]
            payload = payload or {}
            try:
                if method == "GET":
                    return _HttpResp(200, [])
                if method == "POST" and parts[-1] == "aliases":
                    created = await add_alias_to_commodity(
                        int(parts[-2]),
                        alias=schemas.CommodityAliasCreate(alias=payload["alias"]),
                        db=self.db,
                        source="bot",
                    )
                    return _HttpResp(201, {"id": created.id, "alias": created.alias})
                if method == "POST" and parts[-1] == "commodities":
                    created = await create_commodity(
                        commodity_data=schemas.CommodityCreate(name=payload["commodity_data"]["name"]),
                        aliases=payload.get("aliases") or [],
                        db=self.db,
                        source="bot",
                    )
                    return _HttpResp(201, {"id": created.id, "name": created.name})
                if method == "PUT" and parts[-2] == "aliases":
                    updated = await update_alias(
                        int(parts[-1]),
                        schemas.CommodityAliasCreate(alias=payload["alias"]),
                        db=self.db,
                        source="bot",
                    )
                    return _HttpResp(200, {"id": updated.id, "alias": updated.alias})
                if method == "PUT" and parts[-2] == "commodities":
                    updated = await update_commodity_name(
                        int(parts[-1]),
                        schemas.CommodityCreate(name=payload["name"]),
                        db=self.db,
                        source="bot",
                    )
                    return _HttpResp(200, {"id": updated.id, "name": updated.name})
                if method == "DELETE" and parts[-2] == "aliases":
                    await delete_alias(int(parts[-1]), db=self.db, source="bot")
                    return _HttpResp(204, {})
                if method == "DELETE" and parts[-2] == "commodities":
                    await delete_commodity(int(parts[-1]), db=self.db, source="bot")
                    return _HttpResp(204, {})
            except HTTPException as exc:
                return _HttpResp(exc.status_code, {"detail": exc.detail})
            return _HttpResp(404, {"detail": url})

        async def get(self, url, headers=None):
            return await self._call("GET", url)

        async def post(self, url, json=None, headers=None):
            return await self._call("POST", url, json)

        async def put(self, url, json=None, headers=None):
            return await self._call("PUT", url, json)

        async def delete(self, url, headers=None):
            return await self._call("DELETE", url)

    return IsolatedCommodityClient


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


def _admin_crud_store(path: str):
    engine = _engine(path)
    Commodity.__table__.create(engine, checkfirst=True)
    CommodityAlias.__table__.create(engine, checkfirst=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS offers (id INTEGER PRIMARY KEY, commodity_id INTEGER, status VARCHAR)"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, commodity_id INTEGER)"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY)"))
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS market_schedule_overrides ("
                "id INTEGER PRIMARY KEY, date DATE NOT NULL, override_type VARCHAR NOT NULL, "
                "open_time_local TIME, close_time_local TIME, note VARCHAR(255), "
                "created_by_user_id INTEGER, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "updated_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS trading_settings ("
                "key VARCHAR(100) PRIMARY KEY, value TEXT NOT NULL, updated_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS admin_market_messages ("
                "id INTEGER PRIMARY KEY, content TEXT NOT NULL, created_by_id INTEGER NOT NULL, "
                "reused_from_id INTEGER, is_active BOOLEAN NOT NULL DEFAULT 1, "
                "notified_recipients_count INTEGER NOT NULL DEFAULT 0, "
                "published_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME)"
            )
        )
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    return engine, Session


def _bot_commodity_patches(db):
    return [
        patch("bot.handlers.admin_commodities.httpx.AsyncClient", _isolated_commodity_client(db)),
        patch("bot.handlers.admin_commodities.delete_user_message", new=AsyncMock()),
        patch(
            "bot.handlers.admin_commodities.answer_incoming_message_via_runtime",
            new=AsyncMock(return_value=SimpleNamespace(message_id=70)),
        ),
        patch(
            "bot.handlers.admin_commodities.edit_callback_message_via_runtime",
            new=AsyncMock(return_value=SimpleNamespace(message_id=71)),
        ),
        patch("bot.handlers.admin_commodities.edit_interaction_result_via_runtime", new=AsyncMock()),
        patch("bot.handlers.admin_commodities.update_anchor", new=AsyncMock()),
        patch("bot.handlers.admin_commodities.clear_state_retain_anchor", new=AsyncMock()),
        patch("bot.handlers.admin_commodities.answer_callback_query_via_runtime", new=AsyncMock()),
        patch("bot.handlers.admin_commodities.show_aliases_list", new=AsyncMock()),
        patch("bot.handlers.admin_commodities.show_commodity_list", new=AsyncMock()),
        patch("bot.handlers.admin_commodities.asyncio.sleep", new=AsyncMock()),
    ]


def _commodity_message(text: str):
    return SimpleNamespace(text=text, bot=SimpleNamespace(), chat=SimpleNamespace(id=1))


def _commodity_callback(data: str):
    return SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        bot=SimpleNamespace(),
        message=SimpleNamespace(chat=SimpleNamespace(id=1), message_id=10),
    )


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

    async def test_set_user_role_persists_then_denies_and_fails_before_commit_injects(self):
        engine, Session, sync = _make_store()
        _persist_user(sync, role=ModelUserRole.STANDARD)
        answers = []

        async def _answer(callback, text=None, show_alert=False):
            answers.append(text or "")

        callback = SimpleNamespace(
            data="set_user_role_9_MIDDLE_MANAGER",
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock()),
        )
        with patch("core.admin_authority.current_server", return_value="iran"), patch(
            "bot.handlers.admin_users.answer_callback_query_via_runtime",
            new=_answer,
        ), patch(
            "bot.handlers.admin_users.get_user_profile_text",
            new=AsyncMock(return_value="profile"),
        ), patch(
            "bot.handlers.admin_users.get_user_profile_return_keyboard",
            return_value="KB",
        ), patch(
            "bot.handlers.admin_users.edit_callback_message_via_runtime",
            new=AsyncMock(),
        ):
            await handle_set_user_role(
                callback,
                user=SimpleNamespace(role=UserRole.STANDARD),
            )
            reloaded, fresh = _reload_user(Session)
            self.assertEqual(reloaded.role, ModelUserRole.STANDARD)
            fresh.close()

            fail_db = RoleSession(sync, fail_commit=True)
            with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(fail_db)):
                with self.assertRaisesRegex(RuntimeError, "injected admin_role_commit_failure"):
                    await handle_set_user_role(
                        callback,
                        user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    )
            failed, failed_fresh = _reload_user(Session)
            self.assertEqual(failed.role, ModelUserRole.STANDARD)
            failed_fresh.close()

            ok_db = RoleSession(sync)
            with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(ok_db)):
                await handle_set_user_role(
                    callback,
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                )
                await handle_set_user_role(
                    callback,
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                )
            changed, later = _reload_user(Session)
            self.assertEqual(changed.role, ModelUserRole.MIDDLE_MANAGER)
            self.assertGreaterEqual(ok_db.commit_count, 2)
            self.assertTrue(any("نقش کاربر تغییر کرد" in text for text in answers))
            later.close()
        sync.close()
        engine.dispose()

    async def test_user_unblock_clears_restriction_then_denies_without_write_injects(self):
        engine, Session, sync = _make_store()
        _persist_user(sync)
        until = datetime.utcnow() + timedelta(days=2)
        sync.get(MappedAccountUser, 9).trading_restricted_until = until
        sync.commit()
        answers = []

        async def _answer(callback, text=None, show_alert=False):
            answers.append(text or "")

        callback = SimpleNamespace(
            data="user_unblock_9",
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock()),
        )
        db = RoleSession(sync)
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(db)), patch(
            "bot.handlers.admin_users.answer_callback_query_via_runtime",
            new=_answer,
        ), patch(
            "bot.handlers.admin_users.get_user_profile_text",
            new=AsyncMock(return_value="profile"),
        ), patch(
            "bot.handlers.admin_users.get_user_settings_keyboard",
            return_value="KB",
        ), patch(
            "bot.handlers.admin_users.edit_callback_message_via_runtime",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_users.configured_telegram_delivery_runtime",
            return_value=SimpleNamespace(mode="isolated"),
        ), patch(
            "bot.handlers.admin_users.send_delayed_removal_notification",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_users.asyncio.create_task",
            side_effect=lambda coro: coro.close() or SimpleNamespace(),
        ):
            await handle_user_unblock(callback, user=SimpleNamespace(role=UserRole.STANDARD))
            still, peek = _reload_user(Session)
            self.assertEqual(still.trading_restricted_until, until)
            peek.close()

            with patch("core.admin_authority.current_server", return_value="foreign"):
                await handle_user_unblock(
                    callback,
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                )
            blocked, blocked_fresh = _reload_user(Session)
            self.assertEqual(blocked.trading_restricted_until, until)
            blocked_fresh.close()

            with patch("core.admin_authority.current_server", return_value="iran"):
                await handle_user_unblock(
                    callback,
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                )
            cleared, later = _reload_user(Session)
            self.assertIsNone(cleared.trading_restricted_until)
            self.assertGreaterEqual(db.commit_count, 1)
            self.assertTrue(any("رفع مسدودیت" in text for text in answers))
            later.close()
        sync.close()
        engine.dispose()

    async def test_confirm_telegram_admin_broadcast_persists_then_replays_key_injects(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "broadcast-confirm.sqlite")
            engine = _overlap_engine(db_path)
            _exec_sql(engine, ADMIN_SQL)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            creation_key = "p1-00b-broadcast-confirm-key"
            state = FakeState(
                content="hello-seed-broadcast",
                audience_type=TelegramAdminBroadcastAudienceType.ALL.value,
                target_groups=[],
                selected_user_ids=[],
                creation_key=creation_key,
                confirm_completed=False,
            )
            published = {"n": 0}

            async def _publish(*_a, **_k):
                published["n"] += 1

            fail_db = YieldingSession(Session())
            original_commit = fail_db.commit

            async def _fail_once():
                raise RuntimeError("injected broadcast_confirm_commit_failure")

            fail_db.commit = _fail_once
            with patch(
                "core.services.telegram_admin_broadcast_service.resolve_telegram_admin_broadcast_recipients",
                new=AsyncMock(return_value=()),
            ), patch(
                "bot.handlers.admin_broadcast.answer_callback_query_via_runtime",
                new=AsyncMock(),
            ), patch(
                "bot.handlers.admin_broadcast.edit_callback_message_via_runtime",
                new=AsyncMock(),
            ), patch(
                "bot.handlers.admin_broadcast._publish_confirm_result",
                new=_publish,
            ), patch(
                "bot.handlers.admin_broadcast._load_broadcast_by_creation_key",
                new=AsyncMock(return_value=None),
            ):
                await confirm_telegram_admin_broadcast(
                    _broadcast_callback("tgb:confirm"),
                    FakeState(**state.data),
                    user=SimpleNamespace(id=1, role=UserRole.STANDARD),
                )
                with sqlite3.connect(db_path) as conn:
                    self.assertEqual(
                        conn.execute("SELECT COUNT(*) FROM telegram_admin_broadcasts").fetchone()[0],
                        0,
                    )

                with patch(
                    "bot.handlers.admin_broadcast.AsyncSessionLocal",
                    return_value=_SessionBox(fail_db),
                ):
                    await confirm_telegram_admin_broadcast(
                        _broadcast_callback("tgb:confirm"),
                        FakeState(**state.data),
                        user=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
                    )
                await fail_db.rollback()
                fail_db.close()
                with sqlite3.connect(db_path) as conn:
                    self.assertEqual(
                        conn.execute("SELECT COUNT(*) FROM telegram_admin_broadcasts").fetchone()[0],
                        0,
                    )

                ok_db = YieldingSession(Session())
                with patch(
                    "bot.handlers.admin_broadcast.AsyncSessionLocal",
                    return_value=_SessionBox(ok_db),
                ):
                    await confirm_telegram_admin_broadcast(
                        _broadcast_callback("tgb:confirm"),
                        state,
                        user=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
                    )
                    await confirm_telegram_admin_broadcast(
                        _broadcast_callback("tgb:confirm"),
                        FakeState(confirm_completed=True, creation_key=creation_key),
                        user=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
                    )
            with sqlite3.connect(db_path) as conn:
                count = conn.execute("SELECT COUNT(*) FROM telegram_admin_broadcasts").fetchone()[0]
                key = conn.execute(
                    "SELECT creation_key FROM telegram_admin_broadcasts"
                ).fetchone()[0]
            self.assertEqual(count, 1)
            self.assertEqual(key, creation_key)
            self.assertEqual(published["n"], 1)
            fail_db.commit = original_commit
            engine.dispose()

    async def test_user_unlimit_clears_caps_then_denies_without_write_injects(self):
        engine, Session, sync = _make_store()
        _persist_user(sync)
        row = sync.get(MappedAccountUser, 9)
        row.max_daily_trades = 2
        row.max_active_commodities = 3
        row.max_daily_requests = 4
        row.limitations_expire_at = datetime.utcnow() + timedelta(days=1)
        sync.commit()
        answers = []

        async def _answer(callback, text=None, show_alert=False):
            answers.append(text or "")

        callback = SimpleNamespace(data="user_unlimit_9", answer=AsyncMock(), message=SimpleNamespace())
        db = RoleSession(sync)
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(db)), patch(
            "bot.handlers.admin_users.answer_callback_query_via_runtime",
            new=_answer,
        ), patch(
            "bot.handlers.admin_users.get_user_profile_text",
            new=AsyncMock(return_value="profile"),
        ), patch(
            "bot.handlers.admin_users.get_user_settings_keyboard",
            return_value="KB",
        ), patch(
            "bot.handlers.admin_users.edit_callback_message_via_runtime",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_users.configured_telegram_delivery_runtime",
            return_value=SimpleNamespace(mode="isolated"),
        ), patch(
            "bot.handlers.admin_users.send_delayed_removal_notification",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_users.asyncio.create_task",
            side_effect=lambda coro: coro.close() or SimpleNamespace(),
        ):
            await handle_user_unlimit(callback, user=SimpleNamespace(role=UserRole.STANDARD))
            still, peek = _reload_user(Session)
            self.assertEqual(still.max_daily_trades, 2)
            peek.close()
            with patch("core.admin_authority.current_server", return_value="iran"):
                await handle_user_unlimit(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
            cleared, later = _reload_user(Session)
            self.assertIsNone(cleared.max_daily_trades)
            self.assertIsNone(cleared.max_active_commodities)
            self.assertIsNone(cleared.max_daily_requests)
            self.assertIsNone(cleared.limitations_expire_at)
            self.assertTrue(any("محدودیت‌ها برداشته شد" in text for text in answers))
            later.close()
        sync.close()
        engine.dispose()

    async def test_admin_max_block_set_persists_then_denies_injects(self):
        engine, Session, sync = _make_store()
        _persist_user(sync)
        answers = []

        async def _answer(callback, text=None, show_alert=False):
            answers.append(text or "")

        callback = SimpleNamespace(data="admin_max_block_set_9_10", answer=AsyncMock(), message=SimpleNamespace())
        db = RoleSession(sync)
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(db)), patch(
            "bot.handlers.admin_users.answer_callback_query_via_runtime",
            new=_answer,
        ), patch(
            "bot.handlers.admin_users.attach_customer_management_names",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_users.get_block_settings_keyboard",
            return_value="KB",
        ), patch(
            "bot.handlers.admin_users.edit_callback_message_via_runtime",
            new=AsyncMock(),
        ), patch(
            "core.admin_authority.current_server",
            return_value="iran",
        ):
            await handle_admin_max_block_set(callback, user=SimpleNamespace(role=UserRole.STANDARD))
            still, peek = _reload_user(Session)
            self.assertEqual(still.max_blocked_users, 5)
            peek.close()
            await handle_admin_max_block_set(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
            changed, later = _reload_user(Session)
            self.assertEqual(changed.max_blocked_users, 10)
            self.assertTrue(any("سقف بلاک به 10" in text for text in answers))
            later.close()
        sync.close()
        engine.dispose()

    async def test_user_block_actions_persists_restriction_then_denies_injects(self):
        engine, Session, sync = _make_store()
        _persist_user(sync)
        answers = []

        async def _answer(callback, text=None, show_alert=False):
            answers.append(text or "")

        callback = SimpleNamespace(data="user_block_apply_9_30", answer=AsyncMock(), message=SimpleNamespace())
        db = RoleSession(sync)
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(db)), patch(
            "bot.handlers.admin_users.answer_callback_query_via_runtime",
            new=_answer,
        ), patch(
            "bot.handlers.admin_users.get_user_profile_text",
            new=AsyncMock(return_value="profile"),
        ), patch(
            "bot.handlers.admin_users.get_user_settings_keyboard",
            return_value="KB",
        ), patch(
            "bot.handlers.admin_users.edit_callback_message_via_runtime",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_users.configured_telegram_delivery_runtime",
            return_value=SimpleNamespace(mode="isolated"),
        ), patch(
            "bot.handlers.admin_users.send_telegram_notification",
            new=AsyncMock(),
        ):
            await handle_user_block_actions(callback, user=SimpleNamespace(role=UserRole.STANDARD))
            still, peek = _reload_user(Session)
            self.assertIsNone(still.trading_restricted_until)
            peek.close()
            with patch("core.admin_authority.current_server", return_value="iran"):
                await handle_user_block_actions(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
            blocked, later = _reload_user(Session)
            self.assertIsNotNone(blocked.trading_restricted_until)
            self.assertGreaterEqual(later.query(Notification).count(), 1)
            later.close()
        sync.close()
        engine.dispose()

    async def test_limit_confirm_persists_caps_then_denies_injects(self):
        engine, Session, sync = _make_store()
        _persist_user(sync)
        expire_at = datetime.utcnow() + timedelta(days=3)
        state = FakeState(
            limit_target_user_id=9,
            limit_expire_at=expire_at,
            limit_max_trades=4,
            limit_max_commodities=2,
            limit_max_requests=1,
        )
        answers = []

        async def _answer(callback, text=None, show_alert=False):
            answers.append(text or "")

        callback = SimpleNamespace(data="limit_confirm_9", answer=AsyncMock(), message=SimpleNamespace())
        db = RoleSession(sync)
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(db)), patch(
            "bot.handlers.admin_users.answer_callback_query_via_runtime",
            new=_answer,
        ), patch(
            "bot.handlers.admin_users.get_user_profile_text",
            new=AsyncMock(return_value="profile"),
        ), patch(
            "bot.handlers.admin_users.get_user_settings_keyboard",
            return_value="KB",
        ), patch(
            "bot.handlers.admin_users.edit_callback_message_via_runtime",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_users.configured_telegram_delivery_runtime",
            return_value=SimpleNamespace(mode="isolated"),
        ), patch(
            "bot.handlers.admin_users.send_telegram_notification",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_users.clear_state_retain_anchors",
            new=AsyncMock(),
        ):
            await handle_limit_confirm(
                callback,
                user=SimpleNamespace(role=UserRole.STANDARD),
                state=FakeState(**state.data),
            )
            still, peek = _reload_user(Session)
            self.assertIsNone(still.max_daily_trades)
            peek.close()
            with patch("core.admin_authority.current_server", return_value="iran"):
                await handle_limit_confirm(
                    callback,
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    state=state,
                )
            limited, later = _reload_user(Session)
            self.assertEqual(limited.max_daily_trades, 4)
            self.assertEqual(limited.max_active_commodities, 2)
            self.assertEqual(limited.max_daily_requests, 1)
            self.assertGreaterEqual(later.query(Notification).count(), 1)
            later.close()
        sync.close()
        engine.dispose()

    async def test_update_message_edits_then_denies_and_fails_injects(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "msg-update.sqlite")
            engine = _engine(db_path)
            Message.__table__.create(engine, checkfirst=True)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            sync = Session()
            now = datetime.now(timezone.utc)
            sync.add(
                Message(
                    sender_id=1,
                    receiver_id=9,
                    content="old-text",
                    message_type=MessageType.TEXT,
                    is_read=False,
                    created_at=now,
                )
            )
            sync.commit()
            sender = SimpleNamespace(id=1)

            async def _reload(_db, message_id, **_k):
                return _db._session.get(Message, message_id)

            async def _serialize(_db, message, viewer_user_id=None):
                return SimpleNamespace(id=message.id, content=message.content)

            fail_db = MsgSession(Session(), fail_commit=True)
            with patch(
                "core.services.chat_service.ensure_direct_message_chat_link",
                new=AsyncMock(),
            ), patch(
                "core.services.chat_service.reload_direct_message",
                new=_reload,
            ), patch(
                "api.routers.chat._serialize_direct_message_with_accountant_contract",
                new=_serialize,
            ):
                with self.assertRaisesRegex(RuntimeError, "injected send_message_commit_failure"):
                    await update_message(1, MessageUpdate(content="new-text"), current_user=sender, db=fail_db)
                with sqlite3.connect(db_path) as conn:
                    self.assertEqual(conn.execute("SELECT content FROM messages WHERE id=1").fetchone()[0], "old-text")
                from fastapi import HTTPException

                ok_db = MsgSession(Session())
                with self.assertRaises(HTTPException) as denied:
                    await update_message(
                        1,
                        MessageUpdate(content="new-text"),
                        current_user=SimpleNamespace(id=9),
                        db=ok_db,
                    )
                self.assertEqual(denied.exception.status_code, 403)
                updated = await update_message(
                    1,
                    MessageUpdate(content="new-text"),
                    current_user=sender,
                    db=ok_db,
                )
                self.assertEqual(updated.content, "new-text")
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT content FROM messages WHERE id=1").fetchone()[0], "new-text")
            sync.close()
            engine.dispose()

    async def test_delete_message_soft_deletes_then_denies_injects(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "msg-delete.sqlite")
            engine = _engine(db_path)
            Message.__table__.create(engine, checkfirst=True)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            sync = Session()
            now = datetime.now(timezone.utc)
            sync.add(
                Message(
                    sender_id=1,
                    receiver_id=9,
                    content="to-delete",
                    message_type=MessageType.TEXT,
                    is_read=False,
                    created_at=now,
                    is_deleted=False,
                )
            )
            sync.commit()
            sender = SimpleNamespace(id=1)

            async def _reload(_db, message_id, **_k):
                return _db._session.get(Message, message_id)

            ok_db = MsgSession(Session())
            with patch(
                "core.services.chat_service.ensure_direct_message_chat_link",
                new=AsyncMock(),
            ), patch(
                "core.services.chat_service.reload_direct_message",
                new=_reload,
            ):
                from fastapi import HTTPException

                with self.assertRaises(HTTPException) as denied:
                    await delete_message(1, current_user=SimpleNamespace(id=9), db=ok_db)
                self.assertEqual(denied.exception.status_code, 403)
                await delete_message(1, current_user=sender, db=ok_db)
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT is_deleted FROM messages WHERE id=1").fetchone()[0], 1)
            sync.close()
            engine.dispose()

    async def test_process_custom_max_block_persists_then_denies_injects(self):
        engine, Session, sync = _make_store()
        _persist_user(sync)
        answers = []

        async def _answer(message, user, text=None, **_k):
            answers.append(text or "")
            return SimpleNamespace(message_id=70)

        message = SimpleNamespace(
            text="18",
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=1),
        )
        state = FakeState(custom_max_block_user_id=9, anchor_id=1)
        db = RoleSession(sync)
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(db)), patch(
            "bot.handlers.admin_users.delete_user_message",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_users.answer_incoming_message_via_runtime",
            new=_answer,
        ), patch(
            "bot.handlers.admin_users.attach_customer_management_names",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_users.get_block_settings_keyboard",
            return_value="KB",
        ), patch(
            "bot.handlers.admin_users.update_anchor",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_users.clear_state_retain_anchors",
            new=AsyncMock(),
        ), patch(
            "core.admin_authority.current_server",
            return_value="iran",
        ):
            await process_custom_max_block(
                message,
                user=SimpleNamespace(role=UserRole.STANDARD),
                state=FakeState(custom_max_block_user_id=9, anchor_id=1),
            )
            still, peek = _reload_user(Session)
            self.assertEqual(still.max_blocked_users, 5)
            peek.close()
            await process_custom_max_block(
                SimpleNamespace(text="not-a-number", bot=SimpleNamespace(), chat=SimpleNamespace(id=1)),
                user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                state=FakeState(custom_max_block_user_id=9, anchor_id=1),
            )
            invalid, peek2 = _reload_user(Session)
            self.assertEqual(invalid.max_blocked_users, 5)
            peek2.close()
            await process_custom_max_block(
                message,
                user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                state=state,
            )
            changed, later = _reload_user(Session)
            self.assertEqual(changed.max_blocked_users, 18)
            self.assertTrue(any("سقف بلاک: 18" in text for text in answers))
            later.close()
        sync.close()
        engine.dispose()

    async def test_create_commodity_persists_then_rejects_digits_and_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "commodity-create.sqlite")
            engine, Session = _admin_crud_store(db_path)
            db = CrudSession(Session())
            with self.assertRaises(HTTPException) as digits:
                await create_commodity(
                    commodity_data=schemas.CommodityCreate(name="ربع403"),
                    aliases=[],
                    db=db,
                    source="bot",
                )
            self.assertEqual(digits.exception.status_code, 400)
            created = await create_commodity(
                commodity_data=schemas.CommodityCreate(name="ربع"),
                aliases=["ربع طلا"],
                db=db,
                source="bot",
            )
            self.assertEqual(created.name, "ربع")
            with self.assertRaises(HTTPException) as conflict:
                await create_commodity(
                    commodity_data=schemas.CommodityCreate(name="ربع"),
                    aliases=[],
                    db=db,
                    source="bot",
                )
            self.assertEqual(conflict.exception.status_code, 409)
            with sqlite3.connect(db_path) as conn:
                names = [row[0] for row in conn.execute("SELECT name FROM commodities").fetchall()]
                aliases = [row[0] for row in conn.execute("SELECT alias FROM commodity_aliases").fetchall()]
            self.assertEqual(names, ["ربع"])
            self.assertEqual(aliases, ["ربع طلا"])
            engine.dispose()

    async def test_update_commodity_name_persists_then_404s(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "commodity-update.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            sync.add(Commodity(name="ربع"))
            sync.commit()
            db = CrudSession(Session())
            with self.assertRaises(HTTPException) as missing:
                await update_commodity_name(
                    99,
                    schemas.CommodityCreate(name="نیم"),
                    db=db,
                    source="bot",
                )
            self.assertEqual(missing.exception.status_code, 404)
            updated = await update_commodity_name(
                1,
                schemas.CommodityCreate(name="نیم"),
                db=db,
                source="bot",
            )
            self.assertEqual(updated.name, "نیم")
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT name FROM commodities WHERE id=1").fetchone()[0], "نیم")
            sync.close()
            engine.dispose()

    async def test_delete_commodity_removes_then_404s_and_locks_imam(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "commodity-delete.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            sync.add(Commodity(name="ربع"))
            sync.add(Commodity(name=IMAM_COMMODITY_NAME))
            sync.commit()
            db = CrudSession(Session())
            with self.assertRaises(HTTPException) as missing:
                await delete_commodity(99, db=db, source="bot")
            self.assertEqual(missing.exception.status_code, 404)
            with self.assertRaises(HTTPException) as locked:
                await delete_commodity(2, db=db, source="bot")
            self.assertEqual(locked.exception.status_code, 409)
            await delete_commodity(1, db=db, source="bot")
            with sqlite3.connect(db_path) as conn:
                names = [row[0] for row in conn.execute("SELECT name FROM commodities").fetchall()]
            self.assertEqual(names, [IMAM_COMMODITY_NAME])
            sync.close()
            engine.dispose()

    async def test_add_alias_to_commodity_persists_then_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "alias-add.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            sync.add(Commodity(name="ربع"))
            sync.add(Commodity(name="نیم"))
            sync.commit()
            db = CrudSession(Session())
            added = await add_alias_to_commodity(
                1,
                alias=schemas.CommodityAliasCreate(alias="ربع طلا"),
                db=db,
                source="bot",
            )
            self.assertEqual(added.alias, "ربع طلا")
            with self.assertRaises(HTTPException) as conflict:
                await add_alias_to_commodity(
                    1,
                    alias=schemas.CommodityAliasCreate(alias="نیم"),
                    db=db,
                    source="bot",
                )
            self.assertEqual(conflict.exception.status_code, 409)
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(
                    conn.execute("SELECT alias FROM commodity_aliases WHERE commodity_id=1").fetchone()[0],
                    "ربع طلا",
                )
            sync.close()
            engine.dispose()

    async def test_update_alias_persists_then_404s(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "alias-update.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            commodity = Commodity(name="ربع")
            sync.add(commodity)
            sync.flush()
            sync.add(CommodityAlias(commodity_id=commodity.id, alias="ربع طلا"))
            sync.commit()
            db = CrudSession(Session())
            with self.assertRaises(HTTPException) as missing:
                await update_alias(
                    99,
                    schemas.CommodityAliasCreate(alias="نیم طلا"),
                    db=db,
                    source="bot",
                )
            self.assertEqual(missing.exception.status_code, 404)
            updated = await update_alias(
                1,
                schemas.CommodityAliasCreate(alias="نیم طلا"),
                db=db,
                source="bot",
            )
            self.assertEqual(updated.alias, "نیم طلا")
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT alias FROM commodity_aliases WHERE id=1").fetchone()[0], "نیم طلا")
            sync.close()
            engine.dispose()

    async def test_delete_alias_removes_then_404s(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "alias-delete.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            commodity = Commodity(name="ربع")
            sync.add(commodity)
            sync.flush()
            sync.add(CommodityAlias(commodity_id=commodity.id, alias="ربع طلا"))
            sync.commit()
            db = CrudSession(Session())
            with self.assertRaises(HTTPException) as missing:
                await delete_alias(99, db=db, source="bot")
            self.assertEqual(missing.exception.status_code, 404)
            await delete_alias(1, db=db, source="bot")
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM commodity_aliases").fetchone()[0], 0)
            sync.close()
            engine.dispose()

    async def test_create_market_override_persists_then_rejects_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "override-create.sqlite")
            engine, Session = _admin_crud_store(db_path)
            db = CrudSession(Session())
            actor = SimpleNamespace(id=1)
            payload = MarketScheduleOverrideUpsert(
                date="2026-09-10",
                override_type=MarketScheduleOverrideType.CLOSED_ALL_DAY,
                note="تعطیل",
            )
            with self.assertRaises(HTTPException) as bad_date:
                await create_market_override(
                    MarketScheduleOverrideUpsert(
                        date="not-a-date",
                        override_type=MarketScheduleOverrideType.CLOSED_ALL_DAY,
                    ),
                    db=db,
                    current_user=actor,
                )
            self.assertEqual(bad_date.exception.status_code, 400)
            created = await create_market_override(payload, db=db, current_user=actor)
            self.assertEqual(created.date, "2026-09-10")
            with self.assertRaises(HTTPException) as duplicate:
                await create_market_override(payload, db=db, current_user=actor)
            self.assertEqual(duplicate.exception.status_code, 400)
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM market_schedule_overrides").fetchone()[0], 1)
            engine.dispose()

    async def test_update_market_override_persists_then_404s(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "override-update.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            sync.add(
                MarketScheduleOverride(
                    date=date(2026, 9, 10),
                    override_type=MarketScheduleOverrideType.CLOSED_ALL_DAY,
                    created_by_user_id=1,
                    created_at=datetime.now(timezone.utc),
                )
            )
            sync.commit()
            db = CrudSession(Session())
            with self.assertRaises(HTTPException) as missing:
                await update_market_override(
                    99,
                    MarketScheduleOverrideUpsert(
                        date="2026-09-11",
                        override_type=MarketScheduleOverrideType.OPEN_ALL_DAY,
                    ),
                    db=db,
                )
            self.assertEqual(missing.exception.status_code, 404)
            updated = await update_market_override(
                1,
                MarketScheduleOverrideUpsert(
                    date="2026-09-11",
                    override_type=MarketScheduleOverrideType.OPEN_ALL_DAY,
                    note="باز",
                ),
                db=db,
            )
            self.assertEqual(updated.date, "2026-09-11")
            self.assertEqual(updated.override_type, MarketScheduleOverrideType.OPEN_ALL_DAY)
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(
                    conn.execute("SELECT note FROM market_schedule_overrides WHERE id=1").fetchone()[0],
                    "باز",
                )
            sync.close()
            engine.dispose()

    async def test_delete_market_override_removes_then_404s(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "override-delete.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            sync.add(
                MarketScheduleOverride(
                    date=date(2026, 9, 10),
                    override_type=MarketScheduleOverrideType.CLOSED_ALL_DAY,
                    created_by_user_id=1,
                    created_at=datetime.now(timezone.utc),
                )
            )
            sync.commit()
            db = CrudSession(Session())
            with self.assertRaises(HTTPException) as missing:
                await delete_market_override(99, db=db)
            self.assertEqual(missing.exception.status_code, 404)
            result = await delete_market_override(1, db=db)
            self.assertEqual(result, {"success": True})
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM market_schedule_overrides").fetchone()[0], 0)
            sync.close()
            engine.dispose()

    async def test_bot_alias_add_name_persists_then_denies_without_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "bot-alias-add.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            sync.add(Commodity(name="ربع"))
            sync.commit()
            db = CrudSession(Session())
            patches = _bot_commodity_patches(db)
            for item in patches:
                item.start()
            try:
                await handle_alias_add_name(_commodity_message("ربع طلا"), FakeState(commodity_id=1), user=None)
                with sqlite3.connect(db_path) as conn:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM commodity_aliases").fetchone()[0], 0)
                await handle_alias_add_name(
                    _commodity_message("ربع طلا"),
                    FakeState(commodity_id=1),
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                )
            finally:
                for item in reversed(patches):
                    item.stop()
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT alias FROM commodity_aliases").fetchone()[0], "ربع طلا")
            sync.close()
            engine.dispose()

    async def test_bot_alias_edit_name_persists_then_denies_without_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "bot-alias-edit.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            commodity = Commodity(name="ربع")
            sync.add(commodity)
            sync.flush()
            sync.add(CommodityAlias(commodity_id=commodity.id, alias="ربع طلا"))
            sync.commit()
            db = CrudSession(Session())
            patches = _bot_commodity_patches(db)
            for item in patches:
                item.start()
            try:
                await handle_alias_edit_name(
                    _commodity_message("نیم طلا"),
                    FakeState(alias_id=1, commodity_id=1),
                    user=None,
                )
                with sqlite3.connect(db_path) as conn:
                    self.assertEqual(conn.execute("SELECT alias FROM commodity_aliases").fetchone()[0], "ربع طلا")
                await handle_alias_edit_name(
                    _commodity_message("نیم طلا"),
                    FakeState(alias_id=1, commodity_id=1),
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                )
            finally:
                for item in reversed(patches):
                    item.stop()
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT alias FROM commodity_aliases").fetchone()[0], "نیم طلا")
            sync.close()
            engine.dispose()

    async def test_bot_alias_delete_yes_persists_then_denies_non_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "bot-alias-delete.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            commodity = Commodity(name="ربع")
            sync.add(commodity)
            sync.flush()
            sync.add(CommodityAlias(commodity_id=commodity.id, alias="ربع طلا"))
            sync.commit()
            db = CrudSession(Session())
            patches = _bot_commodity_patches(db)
            for item in patches:
                item.start()
            try:
                await handle_alias_delete_yes(
                    _commodity_callback("alias_delete_confirm_yes_1_1"),
                    user=SimpleNamespace(role=UserRole.STANDARD),
                    state=FakeState(alias_to_delete_id=1, commodity_id=1),
                )
                with sqlite3.connect(db_path) as conn:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM commodity_aliases").fetchone()[0], 1)
                await handle_alias_delete_yes(
                    _commodity_callback("alias_delete_confirm_yes_1_1"),
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    state=FakeState(alias_to_delete_id=1, commodity_id=1),
                )
            finally:
                for item in reversed(patches):
                    item.stop()
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM commodity_aliases").fetchone()[0], 0)
            sync.close()
            engine.dispose()

    async def test_bot_commodity_edit_name_persists_then_denies_without_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "bot-commodity-edit.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            sync.add(Commodity(name="ربع"))
            sync.commit()
            db = CrudSession(Session())
            patches = _bot_commodity_patches(db)
            for item in patches:
                item.start()
            try:
                await handle_commodity_edit_name(_commodity_message("نیم"), FakeState(commodity_id=1), user=None)
                with sqlite3.connect(db_path) as conn:
                    self.assertEqual(conn.execute("SELECT name FROM commodities").fetchone()[0], "ربع")
                await handle_commodity_edit_name(
                    _commodity_message("نیم"),
                    FakeState(commodity_id=1),
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                )
            finally:
                for item in reversed(patches):
                    item.stop()
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT name FROM commodities").fetchone()[0], "نیم")
            sync.close()
            engine.dispose()

    async def test_bot_add_aliases_and_create_persists_then_denies_without_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "bot-commodity-create.sqlite")
            engine, Session = _admin_crud_store(db_path)
            db = CrudSession(Session())
            patches = _bot_commodity_patches(db)
            for item in patches:
                item.start()
            try:
                await handle_add_aliases_and_create(
                    _commodity_message("ربع طلا"),
                    FakeState(name="ربع"),
                    user=None,
                )
                with sqlite3.connect(db_path) as conn:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM commodities").fetchone()[0], 0)
                await handle_add_aliases_and_create(
                    _commodity_message("ربع طلا"),
                    FakeState(name="ربع"),
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                )
            finally:
                for item in reversed(patches):
                    item.stop()
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT name FROM commodities").fetchone()[0], "ربع")
                self.assertIn("ربع طلا", [row[0] for row in conn.execute("SELECT alias FROM commodity_aliases")])
            engine.dispose()

    async def test_bot_delete_yes_removes_then_denies_non_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "bot-commodity-delete.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            sync.add(Commodity(name="ربع"))
            sync.commit()
            db = CrudSession(Session())
            patches = _bot_commodity_patches(db)
            for item in patches:
                item.start()
            try:
                await handle_delete_yes(
                    _commodity_callback("comm_delete_confirm_yes_1"),
                    user=SimpleNamespace(role=UserRole.STANDARD),
                    state=FakeState(commodity_to_delete_id=1),
                )
                with sqlite3.connect(db_path) as conn:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM commodities").fetchone()[0], 1)
                await handle_delete_yes(
                    _commodity_callback("comm_delete_confirm_yes_1"),
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    state=FakeState(commodity_to_delete_id=1),
                )
            finally:
                for item in reversed(patches):
                    item.stop()
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM commodities").fetchone()[0], 0)
            sync.close()
            engine.dispose()

    async def test_update_settings_persists_then_rejects_invalid_hours(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "settings-update.sqlite")
            engine, Session = _admin_crud_store(db_path)

            def _box():
                return _SessionBox(CrudSession(Session()))

            with patch("core.db.AsyncSessionLocal", side_effect=_box):
                with self.assertRaises(HTTPException) as invalid:
                    await update_settings(
                        TradingSettingsUpdate(market_open_time_local="18:00", market_close_time_local="09:00")
                    )
                self.assertEqual(invalid.exception.status_code, 400)
                updated = await update_settings(TradingSettingsUpdate(offer_min_quantity=7))
                self.assertEqual(updated.offer_min_quantity, 7)
            with sqlite3.connect(db_path) as conn:
                raw = conn.execute("SELECT value FROM trading_settings WHERE key='offer_min_quantity'").fetchone()
            self.assertIsNotNone(raw)
            self.assertEqual(json.loads(raw[0]), 7)
            engine.dispose()

    async def test_reset_settings_restores_defaults_after_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "settings-reset.sqlite")
            engine, Session = _admin_crud_store(db_path)

            def _box():
                return _SessionBox(CrudSession(Session()))

            with patch("core.db.AsyncSessionLocal", side_effect=_box):
                await update_settings(TradingSettingsUpdate(offer_min_quantity=9))
                reset = await reset_settings()
                self.assertEqual(reset.offer_min_quantity, 5)
            with sqlite3.connect(db_path) as conn:
                raw = conn.execute("SELECT value FROM trading_settings WHERE key='offer_min_quantity'").fetchone()
            self.assertEqual(json.loads(raw[0]), 5)
            engine.dispose()

    async def test_channel_trade_handlers_gateway_confirm_is_not_crud_evidence(self):
        self.assertTrue(UNEVIDENCED_TRADE_GATEWAY_SEEDS.isdisjoint(SLICE_EXECUTED_SEED_IDS))

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
