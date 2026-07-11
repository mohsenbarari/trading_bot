import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from api.routers.sync import _apply_item, _apply_versioned_user_patch, _build_upsert_stmt
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

        with patch("api.routers.sync.settings.registration_sync_v2_enabled", True):
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
