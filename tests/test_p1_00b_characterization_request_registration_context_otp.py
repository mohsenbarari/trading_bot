"""Hermetic characterization tests for request_registration_context_otp active-OTP reuse.

Stage: docs/refactor/two-server/section-1/stages/P1-00b.md (Stage P1-00b)
Assignment: WA-P1-00B-FREEZE-20260904-001
Gap Reference: docs/refactor/two-server/section-1/06-current-drift-register.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed Inventory: API-AUTH-POST-AUTH-REQUEST-REGISTRATION-CONTEXT-OTP-L1838

This unit provides bounded assertion evidence for inventory seed
API-AUTH-POST-AUTH-REQUEST-REGISTRATION-CONTEXT-OTP-L1838 only. This bounded unit
does not close the gap, inventory, or Stage.
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
from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from api.routers.auth import (
    RegistrationContextState,
    _assert_registration_context_same_origin,
    _load_registration_context,
    _registration_context_completion_key,
    _registration_context_cookie_name,
    _registration_context_handle,
    _update_registration_context,
    register_otp_request,
    request_registration_context_otp,
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
        "path": "/auth/registration-context/otp/request",
        "raw_path": b"/auth/registration-context/otp/request",
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


def test_request_registration_context_otp_active_otp_reuse_context_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Characterize active-OTP reuse from context_ready updating state to otp_requested."""
    handle = "test-char-handle-p100b-active-otp-reuse-ready-001"
    invitation_token = "test-invitation-token-p100b-ready-001"
    cookie_name = _registration_context_cookie_name()
    active_otp_key = f"reg_otp:{invitation_token}"
    active_otp_code = "619420"
    active_otp_ttl = 114

    fake_redis = FakeRedisBoundary(
        store={active_otp_key: active_otp_code},
        ttls={active_otp_key: active_otp_ttl},
    )
    mock_get_redis = unittest.mock.AsyncMock(return_value=fake_redis)
    monkeypatch.setattr("api.routers.auth.get_redis", mock_get_redis)

    initial_state = RegistrationContextState(
        version=1,
        kind="invitation",
        invitation_token=invitation_token,
        progress="context_ready",
        handoff_claim_key="claim-sentinel-key-ready-001",
    )
    fake_invitation = unittest.mock.MagicMock(spec=Invitation)
    mock_load_context = unittest.mock.AsyncMock(
        return_value=(handle, initial_state, fake_invitation)
    )
    monkeypatch.setattr("api.routers.auth._load_registration_context", mock_load_context)

    mock_update_context = unittest.mock.AsyncMock(return_value=active_otp_ttl)
    monkeypatch.setattr(
        "api.routers.auth._update_registration_context",
        mock_update_context,
    )

    mock_register_otp = unittest.mock.AsyncMock(
        side_effect=AssertionError("register_otp_request must not be called during active-OTP reuse")
    )
    monkeypatch.setattr(
        "api.routers.auth.register_otp_request",
        mock_register_otp,
    )

    fake_db = unittest.mock.AsyncMock(spec=AsyncSession)
    raw_request = _build_request(cookies={cookie_name: handle})
    assert _registration_context_handle(raw_request) == handle

    _assert_registration_context_same_origin(raw_request)

    response = asyncio.run(request_registration_context_otp(raw_request, db=fake_db))

    assert response.status_code == status.HTTP_200_OK
    assert response.status_code == 200

    payload = json.loads(response.body.decode("utf-8"))
    assert payload == {
        "detail": "کد تایید ارسال شد",
        "expires_in": active_otp_ttl,
    }

    assert response.headers.get("Cache-Control") == "no-store"
    assert response.headers.get("Pragma") == "no-cache"
    assert "set-cookie" not in response.headers
    assert "Set-Cookie" not in response.headers

    mock_get_redis.assert_awaited_once_with()
    mock_load_context.assert_awaited_once_with(fake_db, fake_redis, raw_request)

    completion_key = _registration_context_completion_key(handle)
    assert fake_redis.get.await_count == 2
    assert fake_redis.get.await_args_list == [
        unittest.mock.call(completion_key),
        unittest.mock.call(active_otp_key),
    ]

    assert fake_redis.ttl.await_count == 1
    fake_redis.ttl.assert_awaited_once_with(active_otp_key)

    assert fake_redis.set.await_count == 0
    fake_redis.set.assert_not_called()
    assert fake_redis.delete.await_count == 0
    fake_redis.delete.assert_not_called()

    assert mock_update_context.await_count == 1
    expected_updated_state = initial_state.model_copy(update={"progress": "otp_requested"})
    mock_update_context.assert_awaited_once_with(fake_redis, handle, expected_updated_state)
    call_args, _ = mock_update_context.call_args
    assert call_args[0] is fake_redis
    assert call_args[1] == handle
    actual_state: RegistrationContextState = call_args[2]
    assert actual_state.progress == "otp_requested"
    assert actual_state.kind == "invitation"
    assert actual_state.invitation_token == invitation_token
    assert actual_state.handoff_claim_key == "claim-sentinel-key-ready-001"
    assert actual_state.version == 1

    assert mock_register_otp.await_count == 0
    mock_register_otp.assert_not_called()

    assert fake_db.mock_calls == []


def test_request_registration_context_otp_active_otp_reuse_already_otp_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Characterize active-OTP reuse from otp_requested without updating context."""
    handle = "test-char-handle-p100b-active-otp-reuse-requested-002"
    invitation_token = "test-invitation-token-p100b-requested-002"
    cookie_name = _registration_context_cookie_name()
    active_otp_key = f"reg_otp:{invitation_token}"
    active_otp_code = "938152"
    active_otp_ttl = 89

    fake_redis = FakeRedisBoundary(
        store={active_otp_key: active_otp_code},
        ttls={active_otp_key: active_otp_ttl},
    )
    mock_get_redis = unittest.mock.AsyncMock(return_value=fake_redis)
    monkeypatch.setattr("api.routers.auth.get_redis", mock_get_redis)

    initial_state = RegistrationContextState(
        version=1,
        kind="invitation",
        invitation_token=invitation_token,
        progress="otp_requested",
        handoff_claim_key="claim-sentinel-key-requested-002",
    )
    fake_invitation = unittest.mock.MagicMock(spec=Invitation)
    mock_load_context = unittest.mock.AsyncMock(
        return_value=(handle, initial_state, fake_invitation)
    )
    monkeypatch.setattr("api.routers.auth._load_registration_context", mock_load_context)

    mock_update_context = unittest.mock.AsyncMock(
        side_effect=AssertionError(
            "_update_registration_context must not be called when progress is already otp_requested"
        )
    )
    monkeypatch.setattr(
        "api.routers.auth._update_registration_context",
        mock_update_context,
    )

    mock_register_otp = unittest.mock.AsyncMock(
        side_effect=AssertionError("register_otp_request must not be called during active-OTP reuse")
    )
    monkeypatch.setattr(
        "api.routers.auth.register_otp_request",
        mock_register_otp,
    )

    fake_db = unittest.mock.AsyncMock(spec=AsyncSession)
    raw_request = _build_request(cookies={cookie_name: handle})
    assert _registration_context_handle(raw_request) == handle

    _assert_registration_context_same_origin(raw_request)

    response = asyncio.run(request_registration_context_otp(raw_request, db=fake_db))

    assert response.status_code == status.HTTP_200_OK
    assert response.status_code == 200

    payload = json.loads(response.body.decode("utf-8"))
    assert payload == {
        "detail": "کد تایید ارسال شد",
        "expires_in": active_otp_ttl,
    }

    assert response.headers.get("Cache-Control") == "no-store"
    assert response.headers.get("Pragma") == "no-cache"
    assert "set-cookie" not in response.headers
    assert "Set-Cookie" not in response.headers

    mock_get_redis.assert_awaited_once_with()
    mock_load_context.assert_awaited_once_with(fake_db, fake_redis, raw_request)

    completion_key = _registration_context_completion_key(handle)
    assert fake_redis.get.await_count == 2
    assert fake_redis.get.await_args_list == [
        unittest.mock.call(completion_key),
        unittest.mock.call(active_otp_key),
    ]

    assert fake_redis.ttl.await_count == 1
    fake_redis.ttl.assert_awaited_once_with(active_otp_key)

    assert fake_redis.set.await_count == 0
    fake_redis.set.assert_not_called()
    assert fake_redis.delete.await_count == 0
    fake_redis.delete.assert_not_called()

    assert mock_update_context.await_count == 0
    mock_update_context.assert_not_called()

    assert mock_register_otp.await_count == 0
    mock_register_otp.assert_not_called()

    assert fake_db.mock_calls == []
