"""Hermetic characterization tests for complete_registration_context endpoint.

Stage: docs/refactor/two-server/section-1/stages/P1-00b.md (Stage P1-00b)
Assignment: WA-P1-00B-FREEZE-20260904-001
Gap Reference: docs/refactor/two-server/section-1/06-current-drift-register.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed Inventory: API-AUTH-POST-AUTH-COMPLETE-REGISTRATION-CONTEXT-L1952

This unit provides bounded assertion evidence for inventory seed
API-AUTH-POST-AUTH-COMPLETE-REGISTRATION-CONTEXT-L1952 only. This bounded unit
does not close the gap, inventory, or Stage.
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

# Install fail-closed network fence prior to importing auth router
install_p1_00_network_fence()

import asyncio
from http.cookies import SimpleCookie
import json
from typing import Any
import unittest.mock

import pytest
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from api.routers.auth import (
    RegisterComplete,
    RegistrationContextCompleteRequest,
    RegistrationContextState,
    _assert_registration_context_same_origin,
    _delete_registration_context,
    _load_registration_context,
    _registration_context_completion_key,
    _registration_context_cookie_name,
    _registration_context_handle,
    _registration_context_secure_cookie,
    _store_registration_context_completion,
    complete_registration_context,
    register_complete,
)
from models.invitation import Invitation


class StrictFakeRedisBoundary:
    """Hermetic in-memory strict Redis test double for characterization tests."""

    def __init__(
        self,
        store: dict[str, Any] | None = None,
        ttls: dict[str, int] | None = None,
    ) -> None:
        self.store: dict[str, Any] = dict(store or {})
        self.ttls: dict[str, int] = dict(ttls or {})
        self.operations: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

        self.get = unittest.mock.AsyncMock(side_effect=self._get)
        self.set = unittest.mock.AsyncMock(side_effect=self._set)
        self.setex = unittest.mock.AsyncMock(side_effect=self._setex)
        self.delete = unittest.mock.AsyncMock(side_effect=self._delete)
        self.ttl = unittest.mock.AsyncMock(side_effect=self._ttl)

    async def _get(self, key: str) -> Any:
        self.operations.append(("get", (key,), {}))
        return self.store.get(key)

    async def _set(self, key: str, value: Any, *args: Any, **kwargs: Any) -> bool:
        self.operations.append(("set", (key, value, *args), kwargs))
        self.store[key] = value
        return True

    async def _setex(self, key: str, time_or_seconds: int, value: Any) -> bool:
        self.operations.append(("setex", (key, time_or_seconds, value), {}))
        self.store[key] = value
        self.ttls[key] = int(time_or_seconds)
        return True

    async def _ttl(self, key: str) -> int:
        self.operations.append(("ttl", (key,), {}))
        if key not in self.store and key not in self.ttls:
            return -2
        return self.ttls.get(key, -1)

    async def _delete(self, *keys: str, **kwargs: Any) -> int:
        self.operations.append(("delete", tuple(keys), kwargs))
        count = 0
        for k in keys:
            if self.store.pop(k, None) is not None:
                count += 1
            self.ttls.pop(k, None)
        return count

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        raise AssertionError(f"Unexpected Redis operation requested: {name}")


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
        "path": "/auth/registration-context/complete",
        "raw_path": b"/auth/registration-context/complete",
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


def test_complete_registration_context_completion_marker_idempotence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Characterize existing completion marker returning idempotent HTTP 200."""
    handle = "char-handle-p100b-complete-idempotent-001"
    cookie_name = _registration_context_cookie_name()
    completion_key = _registration_context_completion_key(handle)
    valid_address = "Tehran, Azadi Avenue, Building 100"
    req = RegistrationContextCompleteRequest(address=valid_address)

    fake_redis = StrictFakeRedisBoundary(
        store={
            completion_key: "registration_complete",
        }
    )
    mock_get_redis = unittest.mock.AsyncMock(return_value=fake_redis)
    monkeypatch.setattr("api.routers.auth.get_redis", mock_get_redis)

    mock_load_context = unittest.mock.AsyncMock(
        side_effect=AssertionError("_load_registration_context must not be called")
    )
    monkeypatch.setattr("api.routers.auth._load_registration_context", mock_load_context)

    mock_register_complete = unittest.mock.AsyncMock(
        side_effect=AssertionError("register_complete must not be called")
    )
    monkeypatch.setattr("api.routers.auth.register_complete", mock_register_complete)

    mock_store_completion = unittest.mock.AsyncMock(
        side_effect=AssertionError("_store_registration_context_completion must not be called")
    )
    monkeypatch.setattr(
        "api.routers.auth._store_registration_context_completion",
        mock_store_completion,
    )

    mock_delete_context = unittest.mock.AsyncMock(
        side_effect=AssertionError("_delete_registration_context must not be called")
    )
    monkeypatch.setattr(
        "api.routers.auth._delete_registration_context",
        mock_delete_context,
    )

    fake_db = unittest.mock.AsyncMock(spec=AsyncSession)
    raw_request = _build_request(cookies={cookie_name: handle})
    assert _registration_context_handle(raw_request) == handle

    _assert_registration_context_same_origin(raw_request)

    response = asyncio.run(
        complete_registration_context(req, raw_request, db=fake_db)
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.status_code == 200
    assert json.loads(response.body.decode("utf-8")) == {
        "status": "registration_complete"
    }

    assert response.headers.get("Cache-Control") == "no-store"
    assert response.headers.get("Pragma") == "no-cache"

    assert "set-cookie" in response.headers
    set_cookie_raw = response.headers["set-cookie"]
    cookie_parser = SimpleCookie()
    cookie_parser.load(set_cookie_raw)
    assert cookie_name in cookie_parser
    morsel = cookie_parser[cookie_name]
    assert morsel.value == handle
    assert morsel["path"] == "/"
    assert morsel["httponly"] is True
    assert morsel["samesite"].lower() == "strict"
    expected_secure = _registration_context_secure_cookie()
    if expected_secure:
        assert bool(morsel["secure"]) is True
        assert "; secure" in set_cookie_raw.lower()
    else:
        assert bool(morsel["secure"]) is False
        assert "; secure" not in set_cookie_raw.lower()

    # Redis assertions: get_redis awaited once and only completion-key lookup performed
    assert mock_get_redis.await_count == 1
    mock_get_redis.assert_awaited_once_with()
    assert fake_redis.get.await_count == 1
    fake_redis.get.assert_awaited_once_with(completion_key)
    assert fake_redis.operations == [("get", (completion_key,), {})]
    assert fake_redis.set.call_count == 0
    assert fake_redis.setex.call_count == 0
    assert fake_redis.delete.call_count == 0
    assert fake_redis.ttl.call_count == 0

    # Uncalled helpers
    assert mock_load_context.call_count == 0
    mock_load_context.assert_not_called()
    assert mock_register_complete.call_count == 0
    mock_register_complete.assert_not_called()
    assert mock_store_completion.call_count == 0
    mock_store_completion.assert_not_called()
    assert mock_delete_context.call_count == 0
    mock_delete_context.assert_not_called()

    # Database boundary untouched
    assert fake_db.mock_calls == []


@pytest.mark.parametrize("progress", ["context_ready", "otp_requested"])
def test_complete_registration_context_non_otp_verified_progress_rejects_409(
    monkeypatch: pytest.MonkeyPatch,
    progress: str,
) -> None:
    """Characterize loaded RegistrationContextState with non-otp_verified progress rejecting with 409."""
    handle = f"char-handle-p100b-complete-non-otp-{progress}-002"
    invitation_token = f"char-token-p100b-complete-{progress}-002"
    cookie_name = _registration_context_cookie_name()
    completion_key = _registration_context_completion_key(handle)
    valid_address = "Tehran, Valiasr Avenue, Building 200"
    req = RegistrationContextCompleteRequest(address=valid_address)

    call_order: list[str] = []

    fake_redis = StrictFakeRedisBoundary()

    async def recorded_get(key: str) -> Any:
        call_order.append(f"redis_get:{key}")
        return await fake_redis._get(key)

    fake_redis.get.side_effect = recorded_get

    async def recorded_get_redis() -> StrictFakeRedisBoundary:
        call_order.append("get_redis")
        return fake_redis

    mock_get_redis = unittest.mock.AsyncMock(side_effect=recorded_get_redis)
    monkeypatch.setattr("api.routers.auth.get_redis", mock_get_redis)

    initial_state = RegistrationContextState(
        version=1,
        kind="invitation",
        invitation_token=invitation_token,
        progress=progress,  # type: ignore[arg-type]
        handoff_claim_key=f"claim-sentinel-key-{progress}-002",
    )
    state_snapshot = initial_state.model_dump()
    fake_invitation = unittest.mock.MagicMock(spec=Invitation)

    async def recorded_load_context(
        db_arg: Any, redis_arg: Any, req_arg: Request
    ) -> tuple[str, RegistrationContextState, Any]:
        call_order.append("_load_registration_context")
        return handle, initial_state, fake_invitation

    mock_load_context = unittest.mock.AsyncMock(side_effect=recorded_load_context)
    monkeypatch.setattr("api.routers.auth._load_registration_context", mock_load_context)

    mock_register_complete = unittest.mock.AsyncMock(
        side_effect=AssertionError("register_complete must not be called")
    )
    monkeypatch.setattr("api.routers.auth.register_complete", mock_register_complete)

    mock_store_completion = unittest.mock.AsyncMock(
        side_effect=AssertionError("_store_registration_context_completion must not be called")
    )
    monkeypatch.setattr(
        "api.routers.auth._store_registration_context_completion",
        mock_store_completion,
    )

    mock_delete_context = unittest.mock.AsyncMock(
        side_effect=AssertionError("_delete_registration_context must not be called")
    )
    monkeypatch.setattr(
        "api.routers.auth._delete_registration_context",
        mock_delete_context,
    )

    fake_db = unittest.mock.AsyncMock(spec=AsyncSession)
    raw_request = _build_request(cookies={cookie_name: handle})
    assert _registration_context_handle(raw_request) == handle

    _assert_registration_context_same_origin(raw_request)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(complete_registration_context(req, raw_request, db=fake_db))

    exc = exc_info.value
    assert exc.status_code == status.HTTP_409_CONFLICT
    assert exc.status_code == 409
    assert exc.detail == "لطفاً ابتدا کد تایید را وارد کنید"
    assert exc.headers == {
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
    }
    assert exc.headers.get("Cache-Control") == "no-store"
    assert exc.headers.get("Pragma") == "no-cache"
    assert "set-cookie" not in exc.headers
    assert "Set-Cookie" not in exc.headers

    # Assert completion marker is checked before context loader
    assert call_order == [
        "get_redis",
        f"redis_get:{completion_key}",
        "_load_registration_context",
    ]
    mock_get_redis.assert_awaited_once_with()
    assert fake_redis.get.await_count == 1
    fake_redis.get.assert_awaited_once_with(completion_key)
    mock_load_context.assert_awaited_once_with(fake_db, fake_redis, raw_request)

    # register_complete not called
    assert mock_register_complete.call_count == 0
    mock_register_complete.assert_not_called()

    # No completion/context mutation occurs
    assert mock_store_completion.call_count == 0
    mock_store_completion.assert_not_called()
    assert mock_delete_context.call_count == 0
    mock_delete_context.assert_not_called()
    assert fake_redis.operations == [("get", (completion_key,), {})]
    assert fake_redis.set.call_count == 0
    assert fake_redis.setex.call_count == 0
    assert fake_redis.delete.call_count == 0
    assert fake_redis.ttl.call_count == 0

    # Initial state is unchanged
    assert initial_state.model_dump() == state_snapshot
    assert initial_state.progress == progress

    # Supplied DB boundary has no calls beyond being passed unchanged to mocked loader
    assert fake_db.mock_calls == []


def test_complete_registration_context_register_complete_http_exception_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Characterize propagation of register_complete HTTPException with merged headers and zero mutation."""
    handle = "char-handle-p100b-complete-otp-verified-err-003"
    invitation_token = "char-token-p100b-complete-otp-verified-003"
    cookie_name = _registration_context_cookie_name()
    completion_key = _registration_context_completion_key(handle)
    valid_address = "Tehran, Shariati Avenue, Building 300"
    req = RegistrationContextCompleteRequest(address=valid_address)

    call_order: list[str] = []

    fake_redis = StrictFakeRedisBoundary()

    async def recorded_get(key: str) -> Any:
        call_order.append(f"redis_get:{key}")
        return await fake_redis._get(key)

    fake_redis.get.side_effect = recorded_get

    async def recorded_get_redis() -> StrictFakeRedisBoundary:
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
    state_snapshot = initial_state.model_dump()
    fake_invitation = unittest.mock.MagicMock(spec=Invitation)

    async def recorded_load_context(
        db_arg: Any, redis_arg: Any, req_arg: Request
    ) -> tuple[str, RegistrationContextState, Any]:
        call_order.append("_load_registration_context")
        return handle, initial_state, fake_invitation

    mock_load_context = unittest.mock.AsyncMock(side_effect=recorded_load_context)
    monkeypatch.setattr("api.routers.auth._load_registration_context", mock_load_context)

    distinctive_status = 418
    distinctive_detail = "sentinel-authoritative-registration-failure-detail-418"
    distinctive_header_key = "X-Sentinel-Registration-Error"
    distinctive_header_value = "sentinel-error-value-xyz"

    async def recorded_register_complete(*args: Any, **kwargs: Any) -> Any:
        call_order.append("register_complete")
        raise HTTPException(
            status_code=distinctive_status,
            detail=distinctive_detail,
            headers={distinctive_header_key: distinctive_header_value},
        )

    mock_register_complete = unittest.mock.AsyncMock(side_effect=recorded_register_complete)
    monkeypatch.setattr("api.routers.auth.register_complete", mock_register_complete)

    mock_store_completion = unittest.mock.AsyncMock(
        side_effect=AssertionError("_store_registration_context_completion must not be called")
    )
    monkeypatch.setattr(
        "api.routers.auth._store_registration_context_completion",
        mock_store_completion,
    )

    mock_delete_context = unittest.mock.AsyncMock(
        side_effect=AssertionError("_delete_registration_context must not be called")
    )
    monkeypatch.setattr(
        "api.routers.auth._delete_registration_context",
        mock_delete_context,
    )

    fake_db = unittest.mock.AsyncMock(spec=AsyncSession)
    raw_request = _build_request(cookies={cookie_name: handle})
    assert _registration_context_handle(raw_request) == handle

    _assert_registration_context_same_origin(raw_request)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(complete_registration_context(req, raw_request, db=fake_db))

    exc = exc_info.value
    assert exc.status_code == distinctive_status
    assert exc.detail == distinctive_detail
    assert exc.headers.get(distinctive_header_key) == distinctive_header_value
    assert exc.headers.get("Cache-Control") == "no-store"
    assert exc.headers.get("Pragma") == "no-cache"
    assert "set-cookie" not in exc.headers
    assert "Set-Cookie" not in exc.headers

    # Execution order
    assert call_order == [
        "get_redis",
        f"redis_get:{completion_key}",
        "_load_registration_context",
        "register_complete",
    ]
    mock_get_redis.assert_awaited_once_with()
    assert fake_redis.get.await_count == 1
    fake_redis.get.assert_awaited_once_with(completion_key)
    mock_load_context.assert_awaited_once_with(fake_db, fake_redis, raw_request)

    # Assert register_complete arguments
    assert mock_register_complete.await_count == 1
    call_args, call_kwargs = mock_register_complete.call_args
    assert len(call_args) == 1
    passed_register_req = call_args[0]
    assert isinstance(passed_register_req, RegisterComplete)
    assert passed_register_req.token == initial_state.invitation_token
    assert passed_register_req.token == invitation_token
    assert passed_register_req.address == req.address
    assert passed_register_req.address == valid_address
    assert call_kwargs["raw_request"] is raw_request
    assert call_kwargs["db"] is fake_db
    assert call_kwargs["verified_invitation_token"] == initial_state.invitation_token
    assert call_kwargs["verified_invitation_token"] == invitation_token

    # Store and delete are not called
    assert mock_store_completion.call_count == 0
    mock_store_completion.assert_not_called()
    assert mock_delete_context.call_count == 0
    mock_delete_context.assert_not_called()

    # Redis state is not mutated
    assert fake_redis.operations == [("get", (completion_key,), {})]
    assert fake_redis.set.call_count == 0
    assert fake_redis.setex.call_count == 0
    assert fake_redis.delete.call_count == 0
    assert fake_redis.ttl.call_count == 0

    # Original state remains unchanged
    assert initial_state.model_dump() == state_snapshot
    assert initial_state.progress == "otp_verified"
    assert initial_state.invitation_token == invitation_token


def test_strict_fake_redis_boundary_rejects_unexpected_operation() -> None:
    """Prove strict fake Redis boundary fails on unexpected Redis operations."""
    fake = StrictFakeRedisBoundary()
    with pytest.raises(AssertionError, match="Unexpected Redis operation requested: flushdb"):
        _ = fake.flushdb
