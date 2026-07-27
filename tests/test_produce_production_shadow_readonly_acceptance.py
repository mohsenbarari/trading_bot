from __future__ import annotations

import os
import unittest
from unittest import mock

from scripts import produce_production_shadow_readonly_acceptance as MODULE


OPERATION_ID = "22222222-2222-4222-8222-222222222222"
RELEASE_SHA = "a" * 40


class FakeCursor:
    def __init__(self, *, database_role: str = "bot_fi_observer") -> None:
        self.database_role = database_role
        self.last_sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql):
        self.last_sql = sql

    def fetchone(self):
        if "current_setting" in self.last_sql:
            return ("on", "on", self.database_role)
        if "FROM pg_roles" in self.last_sql:
            return (False, False, False, False, False, True)
        if "alembic_version" in self.last_sql:
            return ("target_2",)
        raise AssertionError(self.last_sql)

    def fetchall(self):
        if "pg_tables" in self.last_sql:
            return [("accounts",), ("offers",)]
        raise AssertionError(self.last_sql)

    def copy_expert(self, _sql, writer):
        writer.write(b"row\n")


class FakeConnection:
    def __init__(self, *, database_role: str = "bot_fi_observer") -> None:
        self.cursor_value = FakeCursor(database_role=database_role)
        self.readonly = None
        self.closed = False
        self.rolled_back = 0

    def set_session(self, *, readonly, autocommit):
        self.readonly = (readonly, autocommit)

    def cursor(self):
        return self.cursor_value

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed = True


class ProductionShadowReadonlyAcceptanceTests(unittest.TestCase):
    def environment(self) -> dict[str, str]:
        return {
            "SYNC_DATABASE_URL": "postgresql://safe:redacted@db/service",
            "RELEASE_SHA": RELEASE_SHA,
            "PHYSICAL_SITE": "bot_fi",
            "BACKGROUND_JOBS_ENABLED": "false",
        }

    def test_acceptance_is_read_only_provider_free_and_redacted(self):
        connection = FakeConnection()
        with (
            mock.patch.dict(os.environ, self.environment(), clear=True),
            mock.patch.object(
                MODULE.psycopg2,
                "connect",
                return_value=connection,
            ) as connect,
            mock.patch.object(
                MODULE,
                "_fingerprint_from_streams",
                return_value=("f" * 64, 20, 2),
            ),
        ):
            result = MODULE.collect_acceptance(
                operation_id=OPERATION_ID,
                role="bot_fi",
                release_sha=RELEASE_SHA,
                expected_revision="target_2",
            )
        self.assertEqual(result["status"], "read-only-accepted")
        self.assertTrue(result["transaction_read_only"])
        self.assertTrue(result["default_transaction_read_only"])
        self.assertFalse(result["provider_credentials_present"])
        self.assertFalse(result["business_write_attempted"])
        self.assertEqual(connection.readonly, (True, False))
        self.assertGreaterEqual(connection.rolled_back, 1)
        self.assertTrue(connection.closed)
        self.assertNotIn("safe:redacted", str(result))
        options = connect.call_args.kwargs["options"]
        self.assertIn("default_transaction_read_only=on", options)

    def test_provider_credential_blocks_before_database_contact(self):
        environment = {
            **self.environment(),
            "BOT_TOKEN": "must-not-be-used",
        }
        with (
            mock.patch.dict(os.environ, environment, clear=True),
            mock.patch.object(MODULE.psycopg2, "connect") as connect,
            self.assertRaisesRegex(
                MODULE.ReadonlyAcceptanceError,
                "provider-free",
            ),
        ):
            MODULE.collect_acceptance(
                operation_id=OPERATION_ID,
                role="bot_fi",
                release_sha=RELEASE_SHA,
                expected_revision="target_2",
            )
        connect.assert_not_called()

    def test_non_observer_role_and_revision_drift_fail_closed(self):
        for database_role, expected_error in (
            ("bot_fi_app", "observer role"),
            ("bot_fi_observer", "migration revision"),
        ):
            with self.subTest(database_role=database_role):
                connection = FakeConnection(database_role=database_role)
                if expected_error == "migration revision":
                    connection.cursor_value.fetchone = mock.Mock(
                        side_effect=[
                            ("on", "on", "bot_fi_observer"),
                            (False, False, False, False, False, True),
                            ("wrong_revision",),
                        ]
                    )
                with (
                    mock.patch.dict(
                        os.environ,
                        self.environment(),
                        clear=True,
                    ),
                    mock.patch.object(
                        MODULE.psycopg2,
                        "connect",
                        return_value=connection,
                    ),
                    self.assertRaisesRegex(
                        MODULE.ReadonlyAcceptanceError,
                        expected_error,
                    ),
                ):
                    MODULE.collect_acceptance(
                        operation_id=OPERATION_ID,
                        role="bot_fi",
                        release_sha=RELEASE_SHA,
                        expected_revision="target_2",
                    )


if __name__ == "__main__":
    unittest.main()
