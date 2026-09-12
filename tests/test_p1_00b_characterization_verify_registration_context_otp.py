"""Hermetic characterization tests for verify_registration_context_otp state guards.

Stage: docs/refactor/two-server/section-1/stages/P1-00b.md (Stage P1-00b)
Assignment: WA-P1-00B-FREEZE-20260904-001
Gap Reference: docs/refactor/two-server/section-1/06-current-drift-register.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed Inventory: API-AUTH-POST-AUTH-VERIFY-REGISTRATION-CONTEXT-OTP-L1887

This unit provides bounded assertion evidence for inventory seed
API-AUTH-POST-AUTH-VERIFY-REGISTRATION-CONTEXT-OTP-L1887 only. This bounded unit
does not close the High gap, inventory, or Stage.
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

# Install fail-closed network fence prior to importing auth router
install_p1_00_network_fence()

import asyncio
import json
from typing import Any
import unittest.mock

import pytest
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from api.routers.auth import (
    RegistrationContextOTPVerifyRequest,
    RegistrationContextState,
    _assert_registration_context_same_origin,
    _load_registration_context,
    _registration_context_completion_key,
    _registration_context_cookie_name,
    _registration_context_handle,
    _update_registration_context,
    register_otp_verify,
    verify_registration_context_otp,
)
from models.invitation import Invitation


class FakeRedisBoundary:
    """Hermetic in-memory Redis test double for characterization tests."""

    def __init__(
        self,
        store: dict[str, Any] | None = None,
        ttls: dict[str, int] | None = None,
    ) -> None:
        self.store: dict[str, Any] = dict(store or {})
        self.ttls: dict[str, int] = dict(ttls or {})
        self.get = unittest.mock.AsyncMock(side_effect=self._get)
        self.ttl = unittest.mock.AsyncMock(side_effect=self._ttl)
        self.set = unittest.mock.AsyncMock(side_effect=self._set)
        self.setex = unittest.mock.AsyncMock(side_effect=self._setex)
        self.delete = unittest.mock.AsyncMock(side_effect=self._delete)

    async def _get(self, key: str) -> Any:
        return self.store.get(key)

    async def _ttl(self, key: str) -> int:
        if key in self.ttls:
            return self.ttls[key]
        if key in self.store:
            return -1
        return -2

    async def _set(self, key: str, value: Any, *args: Any, **kwargs: Any) -> bool:
        self.store[key] = value
        return True

    async def _setex(self, key: str, time: int, value: Any) -> bool:
        self.store[key] = value
        self.ttls[key] = int(time)
        return True

    async def _delete(self, *keys: str, **kwargs: Any) -> int:
        count = 0
        for k in keys:
            if self.store.pop(k, None) is not None:
                count += 1
        return count


def _build_request(
    *,
    cookies: dict[str, str] | None = None,
    headers: list[tuple[bytes, bytes]] | None = None,
    server_name: str = "testserver",
    server_port: int = 80,
    scheme: str = "http",
) -> Request:
    header_map: dict[bytes, bytes] = {
        b"host": server_name.encode("latin-1"),
        b"sec-fetch-site": b"same-origin",
        b"origin": f"{scheme}://{server_name}".encode("latin-1"),
    }
    if headers:
        for k, v in headers:
            header_map[k.lower()] = v
    if cookies:
        cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items()).encode("latin-1")
        header_map[b"cookie"] = cookie_header

    raw_headers = list(header_map.items())

    scope: dict[str, Any] = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "path": "/auth/registration-context/otp/verify",
        "raw_path": b"/auth/registration-context/otp/verify",
        "query_string": b"",
        "scheme": scheme,
        "server": (server_name, server_port),
        "client": ("127.0.0.1", 50000),
        "headers": raw_headers,
    }
    if cookies:
        scope["_cookies"] = dict(cookies)
    else:
        scope["_cookies"] = {}

    return Request(scope)


def test_verify_registration_context_otp_state_guard_registration_kind_rejects_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Characterize loaded RegistrationContextState with kind='registration' rejecting with 409."""
    handle = "test-char-handle-p100b-verify-kind-registration-001"
    invitation_token = "test-invitation-token-p100b-kind-reg-001"
    cookie_name = _registration_context_cookie_name()
    completion_key = _registration_context_completion_key(handle)
    verify_code = "729415"
    req = RegistrationContextOTPVerifyRequest(code=verify_code)

    call_order: list[str] = []

    fake_redis = FakeRedisBoundary()

    async def recorded_get(key: str) -> Any:
        call_order.append(f"redis_get:{key}")
        return await fake_redis._get(key)

    fake_redis.get.side_effect = recorded_get

    async def recorded_get_redis() -> FakeRedisBoundary:
        call_order.append("get_redis")
        return fake_redis

    mock_get_redis = unittest.mock.AsyncMock(side_effect=recorded_get_redis)
    monkeypatch.setattr("api.routers.auth.get_redis", mock_get_redis)

    initial_state = RegistrationContextState(
        version=1,
        kind="registration",
        invitation_token=invitation_token,
        progress="context_ready",
        handoff_claim_key="claim-sentinel-key-kind-reg-001",
    )
    fake_invitation = unittest.mock.MagicMock(spec=Invitation)

    async def recorded_load_context(
        db_arg: Any, redis_arg: Any, req_arg: Request
    ) -> tuple[str, RegistrationContextState, Any]:
        call_order.append("_load_registration_context")
        return handle, initial_state, fake_invitation

    mock_load_context = unittest.mock.AsyncMock(side_effect=recorded_load_context)
    monkeypatch.setattr("api.routers.auth._load_registration_context", mock_load_context)

    mock_update_context = unittest.mock.AsyncMock(
        side_effect=AssertionError(
            "_update_registration_context must not be called when kind is not invitation"
        )
    )
    monkeypatch.setattr(
        "api.routers.auth._update_registration_context",
        mock_update_context,
    )

    mock_register_otp_verify = unittest.mock.AsyncMock(
        side_effect=AssertionError(
            "register_otp_verify must not be called when kind is not invitation"
        )
    )
    monkeypatch.setattr(
        "api.routers.auth.register_otp_verify",
        mock_register_otp_verify,
    )

    fake_db = unittest.mock.AsyncMock(spec=AsyncSession)
    raw_request = _build_request(cookies={cookie_name: handle})
    assert _registration_context_handle(raw_request) == handle

    _assert_registration_context_same_origin(raw_request)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(verify_registration_context_otp(req, raw_request, db=fake_db))

    exc = exc_info.value
    assert exc.status_code == status.HTTP_409_CONFLICT
    assert exc.status_code == 409
    assert exc.detail == "مرحله ثبت\u200cنام با این درخواست سازگار نیست"
    assert exc.headers == {
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
    }
    assert exc.headers.get("Cache-Control") == "no-store"
    assert exc.headers.get("Pragma") == "no-cache"
    assert "set-cookie" not in exc.headers
    assert "Set-Cookie" not in exc.headers

    mock_get_redis.assert_awaited_once_with()
    assert fake_redis.get.await_count == 1
    fake_redis.get.assert_awaited_once_with(completion_key)
    mock_load_context.assert_awaited_once_with(fake_db, fake_redis, raw_request)
    assert call_order == [
        "get_redis",
        f"redis_get:{completion_key}",
        "_load_registration_context",
    ]

    assert mock_register_otp_verify.await_count == 0
    mock_register_otp_verify.assert_not_called()
    assert mock_update_context.await_count == 0
    mock_update_context.assert_not_called()

    assert fake_redis.set.await_count == 0
    fake_redis.set.assert_not_called()
    assert fake_redis.setex.await_count == 0
    fake_redis.setex.assert_not_called()
    assert fake_redis.delete.await_count == 0
    fake_redis.delete.assert_not_called()
    assert fake_redis.ttl.await_count == 0
    fake_redis.ttl.assert_not_called()

    assert fake_db.mock_calls == []


def test_verify_registration_context_otp_state_guard_invitation_context_ready_rejects_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Characterize loaded invitation RegistrationContextState with progress='context_ready' rejecting with 409."""
    handle = "test-char-handle-p100b-verify-progress-context-ready-002"
    invitation_token = "test-invitation-token-p100b-context-ready-002"
    cookie_name = _registration_context_cookie_name()
    completion_key = _registration_context_completion_key(handle)
    verify_code = "481036"
    req = RegistrationContextOTPVerifyRequest(code=verify_code)

    call_order: list[str] = []

    fake_redis = FakeRedisBoundary()

    async def recorded_get(key: str) -> Any:
        call_order.append(f"redis_get:{key}")
        return await fake_redis._get(key)

    fake_redis.get.side_effect = recorded_get

    async def recorded_get_redis() -> FakeRedisBoundary:
        call_order.append("get_redis")
        return fake_redis

    mock_get_redis = unittest.mock.AsyncMock(side_effect=recorded_get_redis)
    monkeypatch.setattr("api.routers.auth.get_redis", mock_get_redis)

    initial_state = RegistrationContextState(
        version=1,
        kind="invitation",
        invitation_token=invitation_token,
        progress="context_ready",
        handoff_claim_key="claim-sentinel-key-context-ready-002",
    )
    fake_invitation = unittest.mock.MagicMock(spec=Invitation)

    async def recorded_load_context(
        db_arg: Any, redis_arg: Any, req_arg: Request
    ) -> tuple[str, RegistrationContextState, Any]:
        call_order.append("_load_registration_context")
        return handle, initial_state, fake_invitation

    mock_load_context = unittest.mock.AsyncMock(side_effect=recorded_load_context)
    monkeypatch.setattr("api.routers.auth._load_registration_context", mock_load_context)

    mock_update_context = unittest.mock.AsyncMock(
        side_effect=AssertionError(
            "_update_registration_context must not be called when progress is context_ready"
        )
    )
    monkeypatch.setattr(
        "api.routers.auth._update_registration_context",
        mock_update_context,
    )

    mock_register_otp_verify = unittest.mock.AsyncMock(
        side_effect=AssertionError(
            "register_otp_verify must not be called when progress is context_ready"
        )
    )
    monkeypatch.setattr(
        "api.routers.auth.register_otp_verify",
        mock_register_otp_verify,
    )

    fake_db = unittest.mock.AsyncMock(spec=AsyncSession)
    raw_request = _build_request(cookies={cookie_name: handle})
    assert _registration_context_handle(raw_request) == handle

    _assert_registration_context_same_origin(raw_request)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(verify_registration_context_otp(req, raw_request, db=fake_db))

    exc = exc_info.value
    assert exc.status_code == status.HTTP_409_CONFLICT
    assert exc.status_code == 409
    assert exc.detail == "مرحله ثبت\u200cنام با این درخواست سازگار نیست"
    assert exc.headers == {
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
    }
    assert exc.headers.get("Cache-Control") == "no-store"
    assert exc.headers.get("Pragma") == "no-cache"
    assert "set-cookie" not in exc.headers
    assert "Set-Cookie" not in exc.headers

    mock_get_redis.assert_awaited_once_with()
    assert fake_redis.get.await_count == 1
    fake_redis.get.assert_awaited_once_with(completion_key)
    mock_load_context.assert_awaited_once_with(fake_db, fake_redis, raw_request)
    assert call_order == [
        "get_redis",
        f"redis_get:{completion_key}",
        "_load_registration_context",
    ]

    assert mock_register_otp_verify.await_count == 0
    mock_register_otp_verify.assert_not_called()
    assert mock_update_context.await_count == 0
    mock_update_context.assert_not_called()

    assert fake_redis.set.await_count == 0
    fake_redis.set.assert_not_called()
    assert fake_redis.setex.await_count == 0
    fake_redis.setex.assert_not_called()
    assert fake_redis.delete.await_count == 0
    fake_redis.delete.assert_not_called()
    assert fake_redis.ttl.await_count == 0
    fake_redis.ttl.assert_not_called()

    assert fake_db.mock_calls == []


def test_verify_registration_context_otp_state_guard_invitation_otp_verified_returns_idempotent_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Characterize loaded invitation RegistrationContextState with progress='otp_verified' returning idempotent 200."""
    handle = "test-char-handle-p100b-verify-progress-otp-verified-003"
    invitation_token = "test-invitation-token-p100b-otp-verified-003"
    cookie_name = _registration_context_cookie_name()
    completion_key = _registration_context_completion_key(handle)
    verify_code = "619420"
    req = RegistrationContextOTPVerifyRequest(code=verify_code)

    call_order: list[str] = []

    fake_redis = FakeRedisBoundary()

    async def recorded_get(key: str) -> Any:
        call_order.append(f"redis_get:{key}")
        return await fake_redis._get(key)

    fake_redis.get.side_effect = recorded_get

    async def recorded_get_redis() -> FakeRedisBoundary:
        call_order.append("get_redis")
        return fake_redis

    mock_get_redis = unittest.mock.AsyncMock(side_effect=recorded_get_redis)
    monkeypatch.setattr("api.routers.auth.get_redis", mock_get_redis)

    initial_state = RegistrationContextState(
        version=1,
        kind="invitation",
        invitation_token=invitation_token,
        progress="otp_verified",
        handoff_claim_key="claim-sentinel-key-otp-verified-003",
    )
    fake_invitation = unittest.mock.MagicMock(spec=Invitation)

    async def recorded_load_context(
        db_arg: Any, redis_arg: Any, req_arg: Request
    ) -> tuple[str, RegistrationContextState, Any]:
        call_order.append("_load_registration_context")
        return handle, initial_state, fake_invitation

    mock_load_context = unittest.mock.AsyncMock(side_effect=recorded_load_context)
    monkeypatch.setattr("api.routers.auth._load_registration_context", mock_load_context)

    mock_update_context = unittest.mock.AsyncMock(
        side_effect=AssertionError(
            "_update_registration_context must not be called when progress is otp_verified"
        )
    )
    monkeypatch.setattr(
        "api.routers.auth._update_registration_context",
        mock_update_context,
    )

    mock_register_otp_verify = unittest.mock.AsyncMock(
        side_effect=AssertionError(
            "register_otp_verify must not be called when progress is otp_verified"
        )
    )
    monkeypatch.setattr(
        "api.routers.auth.register_otp_verify",
        mock_register_otp_verify,
    )

    fake_db = unittest.mock.AsyncMock(spec=AsyncSession)
    raw_request = _build_request(cookies={cookie_name: handle})
    assert _registration_context_handle(raw_request) == handle

    _assert_registration_context_same_origin(raw_request)

    response = asyncio.run(verify_registration_context_otp(req, raw_request, db=fake_db))

    assert response.status_code == status.HTTP_200_OK
    assert response.status_code == 200

    payload = json.loads(response.body.decode("utf-8"))
    assert payload == {"detail": "کد تایید شد"}

    assert response.headers.get("Cache-Control") == "no-store"
    assert response.headers.get("Pragma") == "no-cache"
    assert "set-cookie" not in response.headers
    assert "Set-Cookie" not in response.headers

    mock_get_redis.assert_awaited_once_with()
    assert fake_redis.get.await_count == 1
    fake_redis.get.assert_awaited_once_with(completion_key)
    mock_load_context.assert_awaited_once_with(fake_db, fake_redis, raw_request)
    assert call_order == [
        "get_redis",
        f"redis_get:{completion_key}",
        "_load_registration_context",
    ]

    assert mock_register_otp_verify.await_count == 0
    mock_register_otp_verify.assert_not_called()
    assert mock_update_context.await_count == 0
    mock_update_context.assert_not_called()

    assert fake_redis.set.await_count == 0
    fake_redis.set.assert_not_called()
    assert fake_redis.setex.await_count == 0
    fake_redis.setex.assert_not_called()
    assert fake_redis.delete.await_count == 0
    fake_redis.delete.assert_not_called()
    assert fake_redis.ttl.await_count == 0
    fake_redis.ttl.assert_not_called()

    assert fake_db.mock_calls == []
