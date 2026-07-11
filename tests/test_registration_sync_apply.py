import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from api.routers.sync import (
    _apply_item,
    _apply_user_counter_event,
    _apply_versioned_user_patch,
    _build_upsert_stmt,
)
from core.user_counter_sync import user_counter_event_content_hash
from models.accountant_relation import AccountantRelation
from models.customer_relation import CustomerRelation
from models.invitation import Invitation
from models.user import User


class _ApplyDB:
    def __init__(self, *, rowcount=1, execute_results=None):
        self.rowcount = rowcount
        self.execute_results = list(execute_results or [])
        self.statements = []

    @asynccontextmanager
    async def begin_nested(self):
        yield

    async def execute(self, statement, *args, **kwargs):
        self.statements.append((statement, args, kwargs))
        if self.execute_results:
            result = self.execute_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return type("Result", (), {"rowcount": self.rowcount})()


class _ScalarCollection:
    def __init__(self, values):
        self.values = list(values)

    def all(self):
        return list(self.values)

    def first(self):
        return self.values[0] if self.values else None


class _QueryResult:
    def __init__(self, values=(), *, rowcount=1):
        self.values = list(values)
        self.rowcount = rowcount

    def scalars(self):
        return _ScalarCollection(self.values)

    def scalar_one_or_none(self):
        if len(self.values) > 1:
            raise RuntimeError("multiple rows")
        return self.values[0] if self.values else None


class RegistrationSyncApplyTests(unittest.IsolatedAsyncioTestCase):
    def test_versioned_upserts_use_newer_only_guards_on_natural_keys(self):
        cases = [
            (
                User,
                "users",
                {"id": 7, "account_name": "user7", "sync_version": 3},
                "ON CONFLICT (id)",
            ),
            (
                Invitation,
                "invitations",
                {"id": 8, "token": "INV-test", "sync_version": 4},
                "ON CONFLICT (token)",
            ),
            (
                CustomerRelation,
                "customer_relations",
                {"id": 9, "invitation_token": "CUST-test", "sync_version": 5},
                "ON CONFLICT (invitation_token)",
            ),
            (
                AccountantRelation,
                "accountant_relations",
                {"id": 10, "invitation_token": "ACCT-test", "sync_version": 6},
                "ON CONFLICT (invitation_token)",
            ),
        ]

        with patch("api.routers.sync.settings.registration_sync_v2_enabled", True):
            for model, table_name, data, conflict_fragment in cases:
                with self.subTest(table_name=table_name):
                    statement = _build_upsert_stmt(model, table_name, data)
                    sql = str(
                        statement.compile(
                            dialect=postgresql.dialect(),
                            compile_kwargs={"literal_binds": False},
                        )
                    )
                    self.assertIn(conflict_fragment, sql)
                    self.assertIn(
                        f"WHERE {table_name}.sync_version < excluded.sync_version",
                        sql,
                    )
                    if table_name == "users":
                        data_with_last_seen = {**data, "last_seen_at": "2026-07-11T10:00:00"}
                        statement = _build_upsert_stmt(model, table_name, data_with_last_seen)
                        sql = str(statement.compile(dialect=postgresql.dialect()))
                        self.assertIn("greatest", sql.lower())

    async def test_user_natural_key_fallback_is_version_guarded_and_monotonic(self):
        duplicate = IntegrityError(
            "insert users",
            {},
            RuntimeError("duplicate key value violates unique constraint"),
        )
        applied = type("Result", (), {"rowcount": 1})()
        db = _ApplyDB(execute_results=[duplicate, applied])

        with patch("api.routers.sync.settings.registration_sync_v2_enabled", True), patch(
            "api.routers.sync._apply_versioned_user_insert",
            new=AsyncMock(return_value=None),
        ):
            result = await _apply_item(
                db,
                "users",
                "INSERT",
                7,
                {
                    "id": 7,
                    "telegram_id": 9988,
                    "account_name": "user7",
                    "mobile_number": "09120000000",
                    "sync_version": 4,
                    "last_seen_at": "2026-07-11T10:00:00",
                },
                User,
                [],
                source_server="iran",
            )

        self.assertEqual(result, "ok")
        self.assertEqual(len(db.statements), 2)
        fallback_sql = str(db.statements[1][0]).lower()
        self.assertIn("users.sync_version <", fallback_sql)
        self.assertIn("greatest", fallback_sql)

    async def test_user_insert_updates_natural_identity_match_with_different_id(self):
        local_user = SimpleNamespace(id=91, sync_version=2)
        db = _ApplyDB(
            execute_results=[
                _QueryResult([local_user]),
                _QueryResult([local_user]),
                _QueryResult(rowcount=1),
            ]
        )
        with patch("api.routers.sync.settings.registration_sync_v2_enabled", True):
            result = await _apply_item(
                db,
                "users",
                "INSERT",
                7,
                {
                    "id": 7,
                    "account_name": "same_user",
                    "mobile_number": "09120000000",
                    "full_name": "Same User",
                    "address": "Authoritative address",
                    "role": "عادی",
                    "sync_version": 4,
                    "_sync_identity": {
                        "current": {"account_name": "same_user"},
                        "previous": {},
                    },
                },
                User,
                [],
                source_server="iran",
            )

        self.assertEqual(result, "ok")
        update_statement = db.statements[-1][0]
        compiled = update_statement.compile(dialect=postgresql.dialect())
        self.assertIn(91, compiled.params.values())

    async def test_user_insert_rejects_unrelated_local_numeric_id_collision(self):
        db = _ApplyDB(
            execute_results=[
                _QueryResult([]),
                _QueryResult([SimpleNamespace(id=7, account_name="different_user")]),
            ]
        )
        with patch("api.routers.sync.settings.registration_sync_v2_enabled", True):
            result = await _apply_item(
                db,
                "users",
                "INSERT",
                7,
                {
                    "id": 7,
                    "account_name": "source_user",
                    "mobile_number": "09120000000",
                    "sync_version": 4,
                    "_sync_identity": {
                        "current": {"account_name": "source_user"},
                        "previous": {},
                    },
                },
                User,
                [],
                source_server="iran",
            )

        self.assertEqual(result, "error")
        self.assertEqual(len(db.statements), 2)

    async def test_unversioned_iran_user_insert_uses_legacy_compatibility_upsert(self):
        db = _ApplyDB()

        with patch("api.routers.sync.settings.registration_sync_v2_enabled", True):
            result = await _apply_item(
                db,
                "users",
                "INSERT",
                7,
                {
                    "id": 7,
                    "telegram_id": 9988,
                    "account_name": "legacy_user",
                    "mobile_number": "09120000000",
                },
                User,
                [],
                source_server="iran",
            )

        self.assertEqual(result, "ok")
        self.assertEqual(len(db.statements), 1)
        self.assertIn("INSERT INTO users", str(db.statements[0][0]))

    async def test_iran_user_patch_is_version_guarded_and_last_seen_is_monotonic(self):
        db = _ApplyDB()
        result = await _apply_versioned_user_patch(
            db,
            record_id=7,
            data={
                "id": 7,
                "sync_version": 4,
                "address": "authoritative address",
                "last_seen_at": "2026-07-11T10:00:00",
            },
            source_server="iran",
        )

        self.assertEqual(result, "ok")
        sql = str(db.statements[0][0])
        self.assertIn("users.sync_version <", sql)
        self.assertIn("greatest", sql.lower())
        self.assertIn("address", sql)

    async def test_versioned_user_patch_resolves_different_local_id_by_natural_identity(self):
        local_user = SimpleNamespace(id=91, sync_version=2)
        db = _ApplyDB(execute_results=[_QueryResult([local_user])])
        result = await _apply_versioned_user_patch(
            db,
            record_id=7,
            data={
                "id": 7,
                "sync_version": 4,
                "address": "authoritative address",
                "_sync_identity": {
                    "current": {"account_name": "same_user"},
                    "previous": {},
                },
            },
            source_server="iran",
        )

        self.assertEqual(result, "ok")
        update_statement = db.statements[1][0]
        compiled = update_statement.compile(dialect=postgresql.dialect())
        self.assertIn(91, compiled.params.values())
        self.assertNotIn("_sync_identity", str(update_statement))

    async def test_versioned_user_patch_rejects_split_identity_resolution(self):
        db = _ApplyDB(
            execute_results=[
                _QueryResult(
                    [
                        SimpleNamespace(id=91),
                        SimpleNamespace(id=92),
                    ]
                )
            ]
        )
        result = await _apply_versioned_user_patch(
            db,
            record_id=7,
            data={
                "id": 7,
                "sync_version": 4,
                "address": "must not apply",
                "_sync_identity": {
                    "current": {
                        "account_name": "first_user",
                        "mobile_number": "09120000000",
                    },
                    "previous": {},
                },
            },
            source_server="iran",
        )

        self.assertEqual(result, "error")
        self.assertEqual(len(db.statements), 1)

    async def test_unversioned_iran_patch_does_not_compare_shared_updated_at(self):
        db = _ApplyDB()
        result = await _apply_versioned_user_patch(
            db,
            record_id=7,
            data={
                "id": 7,
                "address": "delayed but source-ordered address",
                "updated_at": "2026-07-11T09:00:00",
            },
            source_server="iran",
        )

        self.assertEqual(result, "ok")
        sql = str(db.statements[0][0]).lower()
        self.assertNotIn("users.updated_at <=", sql)
        self.assertNotIn("updated_at=now()", sql.replace(" ", "").lower())

    async def test_foreign_user_patch_is_monotonic_and_never_writes_identity_or_version(self):
        db = _ApplyDB()
        result = await _apply_versioned_user_patch(
            db,
            record_id=7,
            data={
                "id": 7,
                "sync_version": 99,
                "bot_onboarding_required_step": 2,
                "bot_onboarding_completed_step": 1,
                "last_seen_at": "2026-07-11T10:00:00",
                "address": "must be ignored",
            },
            source_server="foreign",
        )

        self.assertEqual(result, "ok")
        sql = str(db.statements[0][0])
        self.assertIn("greatest", sql.lower())
        self.assertIn("bot_onboarding_required_step", sql)
        self.assertIn("bot_onboarding_completed_step", sql)
        self.assertNotIn("address=", sql.replace(" ", ""))
        self.assertNotIn("sync_version=", sql.replace(" ", ""))
        self.assertIn("updated_at=users.updated_at", sql.replace(" ", "").lower())

    async def test_counter_event_applies_once_with_epoch_and_local_identity(self):
        user = SimpleNamespace(
            id=91,
            counter_epoch=1,
            trades_count=3,
            commodities_traded_count=4,
            channel_messages_count=5,
        )
        event_id = UUID("11111111-2222-4333-8444-555555555555")
        db = _ApplyDB(
            execute_results=[
                _QueryResult([user]),
                _QueryResult([user]),
                _QueryResult([event_id]),
                _QueryResult([]),
                _QueryResult(rowcount=1),
            ]
        )
        result = await _apply_user_counter_event(
            db,
            record_id=7,
            data={
                "_counter_event_id": str(event_id),
                "_counter_event_kind": "increment",
                "_counter_epoch": 1,
                "_counter_deltas": {"trades_count": 1, "commodities_traded_count": 6},
                "_counter_occurred_at": "2026-07-11T12:00:00+00:00",
                "_sync_identity": {
                    "current": {"account_name": "same_user"},
                    "previous": {},
                },
            },
            source_server="foreign",
        )

        self.assertEqual(result, "ok")
        self.assertIn("FOR UPDATE", str(db.statements[0][0]))
        update_statement = db.statements[-1][0]
        sql = str(update_statement)
        self.assertNotIn("counter_epoch", sql)
        self.assertIn("trades_count", sql)
        self.assertIn("commodities_traded_count", sql)
        compiled = update_statement.compile(dialect=postgresql.dialect())
        self.assertIn(91, compiled.params.values())
        self.assertIn(4, compiled.params.values())

    async def test_duplicate_counter_event_does_not_update_user(self):
        user = SimpleNamespace(
            id=91,
            counter_epoch=3,
            trades_count=4,
            commodities_traded_count=10,
            channel_messages_count=5,
        )
        event_id = "11111111-2222-4333-8444-555555555555"
        event_hash = user_counter_event_content_hash(
            source_server="foreign",
            event_id=event_id,
            kind="increment",
            epoch=3,
            deltas={"trades_count": 1},
            occurred_at="2026-07-11T12:00:00+00:00",
        )
        db = _ApplyDB(
            execute_results=[
                _QueryResult([user]),
                _QueryResult([user]),
                _QueryResult([]),
                _QueryResult(
                    [
                        SimpleNamespace(
                            source_server="foreign",
                            user_id=91,
                            event_hash=event_hash,
                        )
                    ]
                ),
            ]
        )
        result = await _apply_user_counter_event(
            db,
            record_id=7,
            data={
                "_counter_event_id": event_id,
                "_counter_event_kind": "increment",
                "_counter_epoch": 3,
                "_counter_deltas": {"trades_count": 1},
                "_counter_occurred_at": "2026-07-11T12:00:00+00:00",
                "_sync_identity": {
                    "current": {"account_name": "same_user"},
                    "previous": {},
                },
            },
            source_server="foreign",
        )

        self.assertEqual(result, "ignored")
        self.assertEqual(len(db.statements), 4)

    async def test_unknown_source_fails_without_database_write(self):
        db = _ApplyDB()
        result = await _apply_versioned_user_patch(
            db,
            record_id=7,
            data={"id": 7, "sync_version": 2, "address": "x"},
            source_server="unknown",
        )
        self.assertEqual(result, "error")
        self.assertEqual(db.statements, [])


if __name__ == "__main__":
    unittest.main()
