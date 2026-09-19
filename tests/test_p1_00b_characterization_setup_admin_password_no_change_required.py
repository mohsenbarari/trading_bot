"""Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Canonical seed: docs/refactor/two-server/section-1/inventory/surface-api.json#API-AUTH-POST-AUTH-SETUP-ADMIN-PASSWORD-L2915
Exact scenario: P1-00B-SINGLE-V1.setup-admin-password-no-change-required
Partial-evidence status: PARTIAL (characterization of must_change_password=false rejection outcome)
Rejected-reconciliation deferral: defer_prior_unit=P1-00B-RECONCILE-FIVE-AUTH-TOKEN-OTP-LOGIN-C6-20260919-V2
Exclusions: invalid-token, missing-user, telegram fallback, must_change_password=true, valid or other short-password behavior, password hashing, password mutation, commit/rollback behavior beyond proving absence, endpoint dependency injection and request validation, persistence, real database/table/session, Redis/network/provider calls, other auth handlers, product/source changes, existing-test edits, documentation/inventory/count/reconciliation edits, seed or blocker closure, Stage closure, production, deploy, DNS, Web Writer, secrets, and Git.
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import operator
import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.auth import SetupPasswordRequest, setup_admin_password
from models.user import User

TARGET_DECODED_SUB = "840219551"
TARGET_USER_ID = 840219551
SENTINEL_EXISTING_HASH = "preexisting-sentinel-admin-hash-xyz890"
THREE_CHAR_PASSWORD = "abc"


class ScalarResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


def _validate_user_id_statement(stmt: Any, expected_user_id: int) -> None:
    whereclause = getattr(stmt, "whereclause", None)
    if whereclause is None:
        raise AssertionError("SQL statement must contain a WHERE clause")

    left = getattr(whereclause, "left", None)
    if left is None:
        raise AssertionError("WHERE clause must have a left operand")

    col_name = getattr(left, "name", None) or getattr(left, "key", None)
    if col_name != "id":
        raise AssertionError(f"WHERE clause must target 'id', got '{col_name}'")

    table = getattr(left, "table", None)
    if table is not None:
        table_name = getattr(table, "name", None)
        if table_name != "users":
            raise AssertionError(f"WHERE clause table must be 'users', got '{table_name}'")

    op = whereclause.operator
    if op not in (operator.eq, getattr(operator, "__eq__", operator.eq)):
        raise AssertionError(f"WHERE clause operator must be equality, got {op}")

    right = getattr(whereclause, "right", None)
    if right is None:
        raise AssertionError("WHERE clause must have a right operand")

    bound_val = getattr(right, "value", None)
    if bound_val != expected_user_id:
        raise AssertionError(f"WHERE clause value must be {expected_user_id}, got {bound_val}")

    sql_str = str(stmt)
    if "users.id =" not in sql_str:
        raise AssertionError(f"Compiled SQL statement missing 'users.id =', got: {sql_str}")


class StrictSingleLookupAsyncSession:
    def __init__(self, expected_user_id: int, user: User) -> None:
        self._expected_user_id = expected_user_id
        self._user = user
        self.execute_count = 0
        self.statements: list[Any] = []
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def execute(self, stmt: Any) -> ScalarResult:
        if self.execute_count >= 1:
            raise AssertionError(
                f"Unexpected second execute() call with statement: {stmt}"
            )
        self.execute_count += 1
        _validate_user_id_statement(stmt, self._expected_user_id)
        self.statements.append(stmt)
        return ScalarResult(self._user)

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"Unexpected database session attribute access: {name}")


class SetupAdminPasswordNoChangeRequiredCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_existing_password_requirement_false_rejects_before_validation_or_mutation(
        self,
    ) -> None:
        fence = install_p1_00_network_fence()
        initial_denied = len(fence["denied_connects"])
        initial_redis = len(fence["live_redis_constructs"])

        user = User(
            id=TARGET_USER_ID,
            account_name="admin_no_change",
            mobile_number="+989121112233",
            normalized_account_name="admin_no_change",
            normalized_mobile_number="+989121112233",
            full_name="No Change Admin",
            address="Tehran, District 1",
            must_change_password=False,
            admin_password_hash=SENTINEL_EXISTING_HASH,
        )

        db = StrictSingleLookupAsyncSession(
            expected_user_id=TARGET_USER_ID,
            user=user,
        )
        req = SetupPasswordRequest(password=THREE_CHAR_PASSWORD)

        with patch("jose.jwt.decode", return_value={"sub": TARGET_DECODED_SUB}), patch(
            "api.routers.auth.get_password_hash"
        ) as mock_hash:
            with self.assertRaises(HTTPException) as exc_info:
                await setup_admin_password(
                    req=req,
                    db=db,
                    token="test-bearer-token",
                )

        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "شما نیازی به تغییر رمز عبور ندارید")

        self.assertEqual(db.execute_count, 1)
        self.assertEqual(len(db.statements), 1)
        _validate_user_id_statement(db.statements[0], TARGET_USER_ID)

        self.assertIs(user.must_change_password, False)
        self.assertEqual(user.admin_password_hash, SENTINEL_EXISTING_HASH)

        mock_hash.assert_not_called()
        db.commit.assert_not_called()
        db.rollback.assert_not_called()

        self.assertEqual(len(fence["denied_connects"]), initial_denied)
        self.assertEqual(len(fence["live_redis_constructs"]), initial_redis)


if __name__ == "__main__":
    unittest.main()
