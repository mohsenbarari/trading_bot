"""Characterization tests for retired register OTP request endpoint.

Stage: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment: WA-P1-00B-FREEZE-20260904-001
Gap Reference: docs/refactor/two-server/section-1/06-current-drift-register.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed Inventory: docs/refactor/two-server/section-1/inventory/surface-api.json#API-AUTH-POST-AUTH-RETIRED-REGISTER-OTP-REQUEST-L1501

This unit provides bounded assertion evidence for inventory seed
API-AUTH-POST-AUTH-RETIRED-REGISTER-OTP-REQUEST-L1501 only. This unit does not
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
    RegisterOTPRequest,
    retired_register_otp_request,
)


def test_retired_register_otp_request_direct_contract() -> None:
    """Freeze direct 410 Gone contract and assert response headers and token immutability."""
    token_value = "opaque-characterization-invitation-token-20260912-abc123xyz"
    token_bytes_expected = token_value.encode("utf-8")
    req = RegisterOTPRequest(token=token_value)

    assert req.token == token_value
    assert req.token.encode("utf-8") == token_bytes_expected

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(retired_register_otp_request(req))

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

    # Assert RegisterOTPRequest token remains byte-for-byte unchanged across rejection
    assert req.token == token_value
    assert req.token.encode("utf-8") == token_bytes_expected
    assert bytes(req.token, "utf-8") == token_bytes_expected


def test_retired_register_otp_request_early_rejection_suppresses_downstream_phases() -> None:
    """Assert rejection occurs before active registration, DB, Redis, OTP, staging, or SMS phases."""
    token_value = "opaque-early-rejection-token-20260912-phase-guard-456uvw"
    token_bytes_expected = token_value.encode("utf-8")
    req = RegisterOTPRequest(token=token_value)

    assert req.token == token_value
    assert req.token.encode("utf-8") == token_bytes_expected

    guard_register_otp_request = AsyncMock(
        side_effect=AssertionError("register_otp_request boundary must not be invoked")
    )
    guard_load_valid_invitation = AsyncMock(
        side_effect=AssertionError("_load_valid_invitation_by_token boundary must not be invoked")
    )
    guard_get_redis = AsyncMock(
        side_effect=AssertionError("get_redis boundary must not be invoked")
    )
    guard_generate_otp_code = MagicMock(
        side_effect=AssertionError("_generate_otp_code boundary must not be invoked")
    )
    guard_deliver_otp_via_staging_log = MagicMock(
        side_effect=AssertionError("_deliver_otp_via_staging_log boundary must not be invoked")
    )
    guard_send_otp_sms = MagicMock(
        side_effect=AssertionError("send_otp_sms boundary must not be invoked")
    )

    with (
        patch.object(auth_router, "register_otp_request", guard_register_otp_request),
        patch.object(auth_router, "_load_valid_invitation_by_token", guard_load_valid_invitation),
        patch.object(auth_router, "get_redis", guard_get_redis),
        patch.object(auth_router, "_generate_otp_code", guard_generate_otp_code),
        patch.object(auth_router, "_deliver_otp_via_staging_log", guard_deliver_otp_via_staging_log),
        patch.object(auth_router, "send_otp_sms", guard_send_otp_sms),
    ):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(retired_register_otp_request(req))

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
    guard_register_otp_request.assert_not_called()
    guard_register_otp_request.assert_not_awaited()
    assert guard_register_otp_request.call_count == 0
    assert guard_register_otp_request.await_count == 0

    guard_load_valid_invitation.assert_not_called()
    guard_load_valid_invitation.assert_not_awaited()
    assert guard_load_valid_invitation.call_count == 0
    assert guard_load_valid_invitation.await_count == 0

    guard_get_redis.assert_not_called()
    guard_get_redis.assert_not_awaited()
    assert guard_get_redis.call_count == 0
    assert guard_get_redis.await_count == 0

    guard_generate_otp_code.assert_not_called()
    assert guard_generate_otp_code.call_count == 0

    guard_deliver_otp_via_staging_log.assert_not_called()
    assert guard_deliver_otp_via_staging_log.call_count == 0

    guard_send_otp_sms.assert_not_called()
    assert guard_send_otp_sms.call_count == 0

    # Assert RegisterOTPRequest token remains byte-for-byte unchanged across rejection
    assert req.token == token_value
    assert req.token.encode("utf-8") == token_bytes_expected
    assert bytes(req.token, "utf-8") == token_bytes_expected
