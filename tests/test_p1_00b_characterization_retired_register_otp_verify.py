"""Characterization tests for retired register OTP verify endpoint.

Stage: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment: WA-P1-00B-FREEZE-20260904-001
Gap Reference: docs/refactor/two-server/section-1/06-current-drift-register.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed Inventory: docs/refactor/two-server/section-1/inventory/surface-api.json#API-AUTH-POST-AUTH-RETIRED-REGISTER-OTP-VERIFY-L1539

This unit provides bounded assertion evidence for inventory seed
API-AUTH-POST-AUTH-RETIRED-REGISTER-OTP-VERIFY-L1539 only. This unit does not
close the tracked blocker, does not promote the seed inventory, and does not close
the Stage.
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

# Install fail-closed network fence prior to importing auth router
install_p1_00_network_fence()

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status

import api.routers.auth as auth_router
from api.routers.auth import (
    LEGACY_RAW_REGISTRATION_RETIRED_DETAIL,
    RegisterOTPVerify,
    retired_register_otp_verify,
)


def test_retired_register_otp_verify_direct_contract() -> None:
    """Freeze direct 410 Gone contract and assert response headers and token/code immutability."""
    token_value = "opaque-characterization-invitation-token-20260912-abc123xyz"
    code_value = "12345"
    token_bytes_expected = token_value.encode("utf-8")
    code_bytes_expected = code_value.encode("utf-8")
    req = RegisterOTPVerify(token=token_value, code=code_value)

    assert req.token == token_value
    assert req.token.encode("utf-8") == token_bytes_expected
    assert req.code == code_value
    assert req.code.encode("utf-8") == code_bytes_expected

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(retired_register_otp_verify(req))

    exc = exc_info.value
    assert isinstance(exc, HTTPException)
    assert exc.status_code == status.HTTP_410_GONE
    assert exc.status_code == 410
    assert exc.detail == LEGACY_RAW_REGISTRATION_RETIRED_DETAIL
    assert exc.detail == "این مسیر ثبت‌نام بازنشسته شده است؛ صفحه را دوباره بارگذاری کنید"
    assert exc.headers == {
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
    }
    assert "Set-Cookie" not in (exc.headers or {})
    assert len(exc.headers) == 2

    # Assert RegisterOTPVerify token and code remain byte-for-byte unchanged across rejection
    assert req.token == token_value
    assert req.token.encode("utf-8") == token_bytes_expected
    assert bytes(req.token, "utf-8") == token_bytes_expected
    assert req.code == code_value
    assert req.code.encode("utf-8") == code_bytes_expected
    assert bytes(req.code, "utf-8") == code_bytes_expected


def test_retired_register_otp_verify_early_rejection_suppresses_downstream_phases() -> None:
    """Assert rejection occurs before active verification, DB, Redis, OTP lookup, receipt, or lockout phases."""
    token_value = "opaque-early-rejection-token-20260912-phase-guard-456uvw"
    code_value = "54321"
    token_bytes_expected = token_value.encode("utf-8")
    code_bytes_expected = code_value.encode("utf-8")
    req = RegisterOTPVerify(token=token_value, code=code_value)

    assert req.token == token_value
    assert req.token.encode("utf-8") == token_bytes_expected
    assert req.code == code_value
    assert req.code.encode("utf-8") == code_bytes_expected

    # Construct guarded fake Redis object covering OTP lookup, verification-receipt
    # persistence, OTP consumption/deletion, and failure-state deletion.
    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(
        side_effect=AssertionError("fake_redis.get boundary must not be invoked")
    )
    fake_redis.setex = AsyncMock(
        side_effect=AssertionError("fake_redis.setex boundary must not be invoked")
    )
    fake_redis.delete = AsyncMock(
        side_effect=AssertionError("fake_redis.delete boundary must not be invoked")
    )

    guard_register_otp_verify = AsyncMock(
        side_effect=AssertionError("register_otp_verify boundary must not be invoked")
    )
    guard_get_db = AsyncMock(
        side_effect=AssertionError("get_db boundary must not be invoked")
    )
    guard_get_redis = AsyncMock(
        return_value=fake_redis,
    )
    guard_normalize_persian_numerals = MagicMock(
        side_effect=AssertionError("normalize_persian_numerals boundary must not be invoked")
    )
    guard_redis_text = MagicMock(
        side_effect=AssertionError("_redis_text boundary must not be invoked")
    )
    guard_ensure_otp_verify_not_locked = AsyncMock(
        side_effect=AssertionError("_ensure_otp_verify_not_locked boundary must not be invoked")
    )
    guard_constant_time_secret_equals = MagicMock(
        side_effect=AssertionError("constant_time_secret_equals boundary must not be invoked")
    )
    guard_record_otp_verify_failure = AsyncMock(
        side_effect=AssertionError("_record_otp_verify_failure boundary must not be invoked")
    )
    guard_clear_otp_verify_subject_failures = AsyncMock(
        side_effect=AssertionError("_clear_otp_verify_subject_failures boundary must not be invoked")
    )

    with (
        patch.object(auth_router, "register_otp_verify", guard_register_otp_verify),
        patch.object(auth_router, "get_db", guard_get_db),
        patch.object(auth_router, "get_redis", guard_get_redis),
        patch.object(auth_router, "normalize_persian_numerals", guard_normalize_persian_numerals),
        patch.object(auth_router, "_redis_text", guard_redis_text),
        patch.object(auth_router, "_ensure_otp_verify_not_locked", guard_ensure_otp_verify_not_locked),
        patch.object(auth_router, "constant_time_secret_equals", guard_constant_time_secret_equals),
        patch.object(auth_router, "_record_otp_verify_failure", guard_record_otp_verify_failure),
        patch.object(auth_router, "_clear_otp_verify_subject_failures", guard_clear_otp_verify_subject_failures),
    ):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(retired_register_otp_verify(req))

    exc = exc_info.value
    assert isinstance(exc, HTTPException)
    assert exc.status_code == status.HTTP_410_GONE
    assert exc.status_code == 410
    assert exc.detail == LEGACY_RAW_REGISTRATION_RETIRED_DETAIL
    assert exc.detail == "این مسیر ثبت‌نام بازنشسته شده است؛ صفحه را دوباره بارگذاری کنید"
    assert exc.headers == {
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
    }
    assert "Set-Cookie" not in (exc.headers or {})
    assert len(exc.headers) == 2

    # Independently assert zero calls and zero awaits for every boundary guard
    guard_register_otp_verify.assert_not_called()
    guard_register_otp_verify.assert_not_awaited()
    assert guard_register_otp_verify.call_count == 0
    assert guard_register_otp_verify.await_count == 0

    guard_get_db.assert_not_called()
    guard_get_db.assert_not_awaited()
    assert guard_get_db.call_count == 0
    assert guard_get_db.await_count == 0

    guard_get_redis.assert_not_called()
    guard_get_redis.assert_not_awaited()
    assert guard_get_redis.call_count == 0
    assert guard_get_redis.await_count == 0

    guard_normalize_persian_numerals.assert_not_called()
    assert guard_normalize_persian_numerals.call_count == 0

    guard_redis_text.assert_not_called()
    assert guard_redis_text.call_count == 0

    guard_ensure_otp_verify_not_locked.assert_not_called()
    guard_ensure_otp_verify_not_locked.assert_not_awaited()
    assert guard_ensure_otp_verify_not_locked.call_count == 0
    assert guard_ensure_otp_verify_not_locked.await_count == 0

    guard_constant_time_secret_equals.assert_not_called()
    assert guard_constant_time_secret_equals.call_count == 0

    guard_record_otp_verify_failure.assert_not_called()
    guard_record_otp_verify_failure.assert_not_awaited()
    assert guard_record_otp_verify_failure.call_count == 0
    assert guard_record_otp_verify_failure.await_count == 0

    guard_clear_otp_verify_subject_failures.assert_not_called()
    guard_clear_otp_verify_subject_failures.assert_not_awaited()
    assert guard_clear_otp_verify_subject_failures.call_count == 0
    assert guard_clear_otp_verify_subject_failures.await_count == 0

    # Independently assert zero calls and zero awaits for guarded fake Redis operations
    fake_redis.get.assert_not_called()
    fake_redis.get.assert_not_awaited()
    assert fake_redis.get.call_count == 0
    assert fake_redis.get.await_count == 0

    fake_redis.setex.assert_not_called()
    fake_redis.setex.assert_not_awaited()
    assert fake_redis.setex.call_count == 0
    assert fake_redis.setex.await_count == 0

    fake_redis.delete.assert_not_called()
    fake_redis.delete.assert_not_awaited()
    assert fake_redis.delete.call_count == 0
    assert fake_redis.delete.await_count == 0

    # Assert RegisterOTPVerify token and code remain byte-for-byte unchanged across rejection
    assert req.token == token_value
    assert req.token.encode("utf-8") == token_bytes_expected
    assert bytes(req.token, "utf-8") == token_bytes_expected
    assert req.code == code_value
    assert req.code.encode("utf-8") == code_bytes_expected
    assert bytes(req.code, "utf-8") == code_bytes_expected
