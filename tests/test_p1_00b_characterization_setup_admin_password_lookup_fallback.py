from __future__ import annotations

import operator
import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.auth import SetupPasswordRequest, setup_admin_password
from models.user import User

TARGET_DECODED_SUB = "987654321"
TARGET_USER_ID = 987654321
SENTINEL_NEW_PASSWORD = "sentinel-secure-password-2026"
SENTINEL_PASSWORD_HASH = "deterministic-sentinel-hash-abc123"


class ScalarResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class StrictAsyncSession:
    def __init__(self, execute_results: list[ScalarResult] | None = None) -> None:
        self.execute_results = list(execute_results or [])
        self.statements: list[Any] = []
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def execute(self, stmt: Any) -> ScalarResult:
        if not self.execute_results:
            raise AssertionError(f"Unexpected execute() call with statement: {stmt}")
        self.statements.append(stmt)
        return self.execute_results.pop(0)

    def assert_no_unused_results(self) -> None:
        if self.execute_results:
            raise AssertionError(
                f"Unused database results remaining: {len(self.execute_results)}"
            )


def _assert_where_targets_column_and_value(
    test_case: unittest.TestCase,
    stmt: Any,
    expected_column_name: str,
    expected_value: int,
) -> None:
    whereclause = getattr(stmt, "whereclause", None)
    test_case.assertIsNotNone(whereclause, "SQL statement must contain a WHERE clause")

    left = getattr(whereclause, "left", None)
    test_case.assertIsNotNone(left, "WHERE clause must have a left operand")
    column_name = getattr(left, "name", None) or getattr(left, "key", None)
    test_case.assertEqual(
        column_name,
        expected_column_name,
        f"WHERE clause left operand must target column '{expected_column_name}', got '{column_name}'",
    )

    table = getattr(left, "table", None)
    if table is not None:
        table_name = getattr(table, "name", None)
        test_case.assertEqual(
            table_name,
            "users",
            f"WHERE clause left operand must target 'users' table, got '{table_name}'",
        )

    test_case.assertIn(
        whereclause.operator,
        (operator.eq, getattr(operator, "__eq__", operator.eq)),
        f"WHERE clause operator must be equality, got {whereclause.operator}",
    )

    right = getattr(whereclause, "right", None)
    test_case.assertIsNotNone(right, "WHERE clause must have a right operand")
    bound_value = getattr(right, "value", None)
    test_case.assertEqual(
        bound_value,
        expected_value,
        f"WHERE clause right operand value must be {expected_value}, got {bound_value}",
    )

    sql_str = str(stmt)
    test_case.assertIn(
        f"users.{expected_column_name} =",
        sql_str,
        f"Compiled SQL statement must contain 'users.{expected_column_name} =', got: {sql_str}",
    )


class SetupAdminPasswordLookupFallbackCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_setup_admin_password_id_miss_telegram_fallback_success(self) -> None:
        user = User(
            id=101,
            telegram_id=TARGET_USER_ID,
            must_change_password=True,
            admin_password_hash=None,
        )
        db = StrictAsyncSession([ScalarResult(None), ScalarResult(user)])
        req = SetupPasswordRequest(password=SENTINEL_NEW_PASSWORD)

        with patch("jose.jwt.decode", return_value={"sub": TARGET_DECODED_SUB}), patch(
            "api.routers.auth.get_password_hash",
            return_value=SENTINEL_PASSWORD_HASH,
        ) as mock_hash:
            response = await setup_admin_password(
                req=req,
                db=db,  # type: ignore[arg-type]
                token="test-oauth-token",
            )

        self.assertEqual(len(db.statements), 2)
        _assert_where_targets_column_and_value(
            self, db.statements[0], "id", TARGET_USER_ID
        )
        _assert_where_targets_column_and_value(
            self, db.statements[1], "telegram_id", TARGET_USER_ID
        )

        mock_hash.assert_called_once_with(SENTINEL_NEW_PASSWORD)
        self.assertEqual(user.admin_password_hash, SENTINEL_PASSWORD_HASH)
        self.assertIs(user.must_change_password, False)
        db.commit.assert_awaited_once()
        db.rollback.assert_not_called()
        db.assert_no_unused_results()
        self.assertEqual(response, {"detail": "رمز عبور با موفقیت ثبت شد"})

    async def test_setup_admin_password_both_lookups_missing_raises_404(self) -> None:
        db = StrictAsyncSession([ScalarResult(None), ScalarResult(None)])
        req = SetupPasswordRequest(password=SENTINEL_NEW_PASSWORD)

        with patch("jose.jwt.decode", return_value={"sub": TARGET_DECODED_SUB}), patch(
            "api.routers.auth.get_password_hash",
            return_value=SENTINEL_PASSWORD_HASH,
        ) as mock_hash:
            with self.assertRaises(HTTPException) as exc_info:
                await setup_admin_password(
                    req=req,
                    db=db,  # type: ignore[arg-type]
                    token="test-oauth-token",
                )

        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, "Not Found")

        self.assertEqual(len(db.statements), 2)
        _assert_where_targets_column_and_value(
            self, db.statements[0], "id", TARGET_USER_ID
        )
        _assert_where_targets_column_and_value(
            self, db.statements[1], "telegram_id", TARGET_USER_ID
        )

        mock_hash.assert_not_called()
        db.commit.assert_not_called()
        db.rollback.assert_not_called()
        db.assert_no_unused_results()


if __name__ == "__main__":
    unittest.main()
