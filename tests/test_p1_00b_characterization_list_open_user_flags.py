"""Characterization test for api.routers.user_flags.list_open_user_flags.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-IDENTITY-GET-USER-FLAGS-LIST-OPEN-USER-FLAGS-L83

Characterizes the single open-list outcome for list_open_user_flags showing that
only open flags for non-deleted users are loaded and ordered by last_flagged_at desc, id desc.

Exclusions: Does not test or change resolve_user_flag, product source, production persistence,
documentation, inventories, EXECUTED_SEED_IDS, blockers, reconciliation, Stage state, network,
Redis, deploy, DNS, Web Writer, secrets, or the deferred accountants-list boundary. Does not
claim seed/blocker/Stage closure from this partial characterization.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from api.routers import user_flags
from core.enums import UserAccountStatus
from models.user import User, UserRole
from models.user_flag import UserFlag


class AsyncSessionAdapter:
    """Minimal async session adapter recording execute and commit counts."""

    def __init__(self, session: Session):
        self._session = session
        self.execute_calls: list[Any] = []
        self.execute_count: int = 0
        self.commit_calls: int = 0

    async def execute(self, statement, *args, **kwargs):
        self.execute_count += 1
        self.execute_calls.append(statement)
        return self._session.execute(statement, *args, **kwargs)

    async def commit(self):
        self.commit_calls += 1
        return self._session.commit()

    async def close(self):
        self._session.close()


class P100bListOpenUserFlagsCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterizes api.routers.user_flags.list_open_user_flags query and row ordering."""

    async def test_super_admin_open_list_preserves_query_and_row_order(self):
        """Assert list_open_user_flags loads open flags for active users ordered by last_flagged_at desc."""
        temp_dir = tempfile.mkdtemp()
        db_path = os.path.join(temp_dir, "test_list_open_user_flags.db")
        engine = create_engine(f"sqlite:///{db_path}")

        @event.listens_for(engine, "connect")
        def _register_sqlite_compat_functions(dbapi_connection, connection_record):
            def _btrim(val, chars=None):
                if val is None:
                    return None
                return val.strip() if chars is None else val.strip(chars)

            def _translate(val, from_chars, to_chars):
                if val is None:
                    return None
                return val.translate(str.maketrans(from_chars, to_chars))

            dbapi_connection.create_function("btrim", 1, lambda s: s.strip() if s is not None else None)
            dbapi_connection.create_function("btrim", 2, _btrim)
            dbapi_connection.create_function("translate", 3, _translate)

        try:
            with engine.begin() as conn:
                conn.exec_driver_sql(
                    """
                    CREATE TABLE users (
                        id INTEGER PRIMARY KEY,
                        account_name VARCHAR NOT NULL,
                        mobile_number VARCHAR NOT NULL,
                        normalized_account_name VARCHAR NOT NULL DEFAULT '',
                        normalized_mobile_number VARCHAR NOT NULL DEFAULT '',
                        telegram_id BIGINT,
                        username VARCHAR,
                        full_name VARCHAR NOT NULL,
                        address TEXT NOT NULL,
                        avatar_file_id VARCHAR(36),
                        role VARCHAR(14) NOT NULL,
                        account_status VARCHAR(8) NOT NULL,
                        deactivated_at DATETIME,
                        messenger_grace_expires_at DATETIME,
                        messenger_blocked_at DATETIME,
                        has_bot_access BOOLEAN NOT NULL DEFAULT 1,
                        bot_onboarding_required_step INTEGER NOT NULL DEFAULT 0,
                        bot_onboarding_completed_step INTEGER NOT NULL DEFAULT 0,
                        bot_onboarding_completed_at DATETIME,
                        is_deleted BOOLEAN NOT NULL DEFAULT 0,
                        deleted_at DATETIME,
                        admin_password_hash VARCHAR(255),
                        must_change_password BOOLEAN NOT NULL DEFAULT 0,
                        offer_overtime_minutes INTEGER NOT NULL DEFAULT 0,
                        trading_restricted_until DATETIME,
                        max_daily_trades INTEGER,
                        max_active_commodities INTEGER,
                        max_daily_requests INTEGER,
                        limitations_expire_at DATETIME,
                        trades_count INTEGER NOT NULL DEFAULT 0,
                        commodities_traded_count INTEGER NOT NULL DEFAULT 0,
                        channel_messages_count INTEGER NOT NULL DEFAULT 0,
                        counter_epoch BIGINT NOT NULL DEFAULT 1,
                        max_sessions INTEGER NOT NULL DEFAULT 1,
                        max_accountants INTEGER NOT NULL DEFAULT 3,
                        max_customers INTEGER NOT NULL DEFAULT 5,
                        home_server VARCHAR(16) NOT NULL DEFAULT 'foreign',
                        sync_version BIGINT NOT NULL DEFAULT 1,
                        can_block_users BOOLEAN NOT NULL DEFAULT 1,
                        max_blocked_users INTEGER NOT NULL DEFAULT 10,
                        last_seen_at DATETIME,
                        created_at DATETIME,
                        updated_at DATETIME
                    );
                    """
                )
                conn.exec_driver_sql(
                    """
                    CREATE TABLE user_flags (
                        id INTEGER PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id),
                        flag_type VARCHAR(64) NOT NULL,
                        reason_code VARCHAR(80) NOT NULL,
                        status VARCHAR(24) NOT NULL DEFAULT 'open',
                        severity VARCHAR(24) NOT NULL DEFAULT 'warning',
                        details JSON NOT NULL DEFAULT '{}',
                        trigger_count INTEGER NOT NULL DEFAULT 1,
                        first_flagged_at DATETIME NOT NULL,
                        last_flagged_at DATETIME NOT NULL,
                        resolved_at DATETIME,
                        resolved_by_user_id INTEGER REFERENCES users(id),
                        resolution_note TEXT,
                        created_at DATETIME,
                        updated_at DATETIME
                    );
                    """
                )

            session_factory = sessionmaker(bind=engine)

            with session_factory() as seed_session:
                u1 = User(
                    id=1,
                    account_name="user_1",
                    mobile_number="+989120000001",
                    normalized_account_name="user_1",
                    normalized_mobile_number="989120000001",
                    full_name="User One",
                    address="Tehran Address 1",
                    role=UserRole.STANDARD,
                    account_status=UserAccountStatus.ACTIVE,
                    is_deleted=False,
                )
                u2 = User(
                    id=2,
                    account_name="user_2",
                    mobile_number="+989120000002",
                    normalized_account_name="user_2",
                    normalized_mobile_number="989120000002",
                    full_name="User Two",
                    address="Tehran Address 2",
                    role=UserRole.STANDARD,
                    account_status=UserAccountStatus.ACTIVE,
                    is_deleted=False,
                )
                u3 = User(
                    id=3,
                    account_name="user_3",
                    mobile_number="+989120000003",
                    normalized_account_name="user_3",
                    normalized_mobile_number="989120000003",
                    full_name="User Three",
                    address="Tehran Address 3",
                    role=UserRole.STANDARD,
                    account_status=UserAccountStatus.ACTIVE,
                    is_deleted=False,
                )
                u4 = User(
                    id=4,
                    account_name="user_4",
                    mobile_number="+989120000004",
                    normalized_account_name="user_4",
                    normalized_mobile_number="989120000004",
                    full_name="User Four",
                    address="Tehran Address 4",
                    role=UserRole.STANDARD,
                    account_status=UserAccountStatus.ACTIVE,
                    is_deleted=True,
                    deleted_at=datetime(2026, 9, 8, 15, 0, 0),
                )

                f201 = UserFlag(
                    id=201,
                    user_id=1,
                    flag_type="session_replacement_frequency",
                    reason_code="high_frequency",
                    status="open",
                    severity="warning",
                    details={"rule": "frequency"},
                    trigger_count=1,
                    first_flagged_at=datetime(2026, 9, 8, 12, 0, 0),
                    last_flagged_at=datetime(2026, 9, 8, 12, 0, 0),
                )
                f202 = UserFlag(
                    id=202,
                    user_id=2,
                    flag_type="suspicious_ip",
                    reason_code="ip_conflict",
                    status="open",
                    severity="warning",
                    details={"rule": "ip"},
                    trigger_count=1,
                    first_flagged_at=datetime(2026, 9, 8, 13, 0, 0),
                    last_flagged_at=datetime(2026, 9, 8, 13, 0, 0),
                )
                f203 = UserFlag(
                    id=203,
                    user_id=3,
                    flag_type="device_mismatch",
                    reason_code="device_changed",
                    status="resolved",
                    severity="info",
                    details={"rule": "device"},
                    trigger_count=1,
                    first_flagged_at=datetime(2026, 9, 8, 14, 0, 0),
                    last_flagged_at=datetime(2026, 9, 8, 14, 0, 0),
                    resolved_at=datetime(2026, 9, 8, 14, 30, 0),
                    resolution_note="resolved note",
                )
                f204 = UserFlag(
                    id=204,
                    user_id=4,
                    flag_type="multiple_accounts",
                    reason_code="account_link",
                    status="open",
                    severity="warning",
                    details={"rule": "multiple"},
                    trigger_count=1,
                    first_flagged_at=datetime(2026, 9, 8, 15, 0, 0),
                    last_flagged_at=datetime(2026, 9, 8, 15, 0, 0),
                )

                seed_session.add_all([u1, u2, u3, u4, f201, f202, f203, f204])
                seed_session.commit()
                seed_session.execute(
                    text(
                        "UPDATE users SET normalized_account_name = account_name, "
                        "normalized_mobile_number = mobile_number"
                    )
                )
                seed_session.commit()

            actor = SimpleNamespace(id=99, role=UserRole.SUPER_ADMIN)
            handler_session = session_factory()
            adapter = AsyncSessionAdapter(handler_session)

            serialized_marker_202 = {"id": 202, "user_id": 2, "status": "open"}
            serialized_marker_201 = {"id": 201, "user_id": 1, "status": "open"}

            try:
                with patch.object(
                    user_flags,
                    "attach_user_management_relation_context",
                    new=AsyncMock(),
                ) as mock_attach, patch.object(
                    user_flags,
                    "_serialize_flag",
                    side_effect=[serialized_marker_202, serialized_marker_201],
                ) as mock_serialize, patch.object(
                    user_flags,
                    "build_user_management_search_filter",
                ) as mock_build_search:
                    result = await user_flags.list_open_user_flags(
                        search=None,
                        db=adapter,
                        actor=actor,
                    )
            finally:
                handler_session.close()

            mock_build_search.assert_not_called()
            self.assertEqual(adapter.execute_count, 1)
            self.assertEqual(len(adapter.execute_calls), 1)

            mock_attach.assert_awaited_once()
            attach_db, attach_users = mock_attach.call_args[0]
            self.assertIs(attach_db, adapter)
            self.assertEqual([u.id for u in attach_users], [2, 1])

            self.assertEqual(mock_serialize.call_count, 2)
            first_flag, first_user = mock_serialize.call_args_list[0][0]
            second_flag, second_user = mock_serialize.call_args_list[1][0]

            self.assertEqual(first_flag.id, 202)
            self.assertEqual(first_user.id, 2)
            self.assertEqual(second_flag.id, 201)
            self.assertEqual(second_user.id, 1)

            returned_flag_ids = [first_flag.id, second_flag.id]
            self.assertEqual(returned_flag_ids, [202, 201])
            self.assertNotIn(203, returned_flag_ids)
            self.assertNotIn(204, returned_flag_ids)

            self.assertEqual(result, [serialized_marker_202, serialized_marker_201])
        finally:
            engine.dispose()
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
