"""Hermetic characterization tests for clear_registration_context endpoint.

Stage: docs/refactor/two-server/section-1/stages/P1-00b.md (Stage P1-00b)
Assignment: WA-P1-00B-FREEZE-20260904-001
Gap Reference: docs/refactor/two-server/section-1/06-current-drift-register.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed Inventory: API-AUTH-POST-AUTH-CLEAR-REGISTRATION-CONTEXT-L1995

This unit provides bounded assertion evidence for inventory seed
API-AUTH-POST-AUTH-CLEAR-REGISTRATION-CONTEXT-L1995 only. This bounded unit
does not close the gap, inventory, or Stage.
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

# Install fail-closed network fence prior to importing auth router
install_p1_00_network_fence()

import asyncio
import hashlib
from http.cookies import SimpleCookie
import json
from typing import Any
import unittest.mock

import pytest
from fastapi import HTTPException, status
from starlette.requests import Request
from starlette.responses import Response

import api.routers.auth
from api.routers.auth import (
    REGISTRATION_CONTEXT_COOKIE_DEVELOPMENT,
    REGISTRATION_CONTEXT_COOKIE_PRODUCTION,
    REGISTRATION_CONTEXT_TTL_SECONDS,
    RegistrationContextState,
    RegistrationHandoffClaim,
    _assert_registration_context_same_origin,
    _clear_registration_context_cookie,
    _delete_registration_context,
    _registration_context_clear_cookie_header,
    _registration_context_completion_key,
    _registration_context_cookie_name,
    _registration_context_handle,
    _registration_context_key,
    _registration_context_secure_cookie,
    _registration_context_verified_key,
    _registration_no_store_headers,
    clear_registration_context,
)


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
        "path": "/auth/registration-context/clear",
        "raw_path": b"/auth/registration-context/clear",
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


def _assert_clear_acknowledgement_contract(response: Response) -> None:
    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert response.status_code == 204
    assert response.body == b""

    assert response.headers.get("Cache-Control") == "no-store"
    assert response.headers.get("Pragma") == "no-cache"

    assert "set-cookie" in response.headers
    set_cookie_raw = response.headers["set-cookie"]
    assert set_cookie_raw == _registration_context_clear_cookie_header()

    cookie_parser = SimpleCookie()
    cookie_parser.load(set_cookie_raw)
    cookie_name = _registration_context_cookie_name()
    assert cookie_name in cookie_parser

    morsel = cookie_parser[cookie_name]
    assert morsel.value == ""
    assert morsel["max-age"] in ("0", 0)
    assert morsel["path"] == "/"
    assert morsel["httponly"] is True
    assert "; httponly" in set_cookie_raw.lower()
    assert morsel["samesite"].lower() == "strict"

    expected_secure = _registration_context_secure_cookie()
    if expected_secure:
        assert bool(morsel["secure"]) is True
        assert "; secure" in set_cookie_raw.lower()
    else:
        assert bool(morsel["secure"]) is False
        assert "; secure" not in set_cookie_raw.lower()


def test_clear_registration_context_present_handle_cookie_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Characterize clear_registration_context with present opaque handle cookie.

    Proves:
    - _delete_registration_context is invoked with terminal=True
    - hashed context and verified keys are deleted, and hashed completion marker is deleted afterward
    - exact Redis get/delete calls and ordering
    - terminal store state is achieved
    - opaque handle itself is never used as a Redis key
    - exact acknowledgement contract: HTTP 204, empty body, no-store/no-cache headers, cleared cookie
    - get_redis is awaited exactly once
    - no DB dependency/provider is called
    """
    handle = "char-clear-opaque-handle-token-998877"
    cookie_name = _registration_context_cookie_name()

    context_key = _registration_context_key(handle)
    verified_key = _registration_context_verified_key(handle)
    completion_key = _registration_context_completion_key(handle)
    claim_key = "registration_claim:exch_char_test_12345"

    context_state = RegistrationContextState(
        kind="invitation",
        invitation_token="inv_char_token_abcdef123456",
        progress="context_ready",
        handoff_claim_key=claim_key,
    )
    claim = RegistrationHandoffClaim(
        exchange_id="exch_char_test_12345",
        context_handle=handle,
        state=context_state,
        terminal=False,
    )

    fake_redis = StrictFakeRedisBoundary(
        store={
            context_key: context_state.model_dump_json(),
            claim_key: claim.model_dump_json(),
            verified_key: "verified_marker",
            completion_key: "completion_marker",
        },
        ttls={
            claim_key: 300,
        },
    )
    mock_get_redis = unittest.mock.AsyncMock(return_value=fake_redis)
    monkeypatch.setattr("api.routers.auth.get_redis", mock_get_redis)

    mock_get_db = unittest.mock.AsyncMock(side_effect=AssertionError("Database must not be accessed"))
    monkeypatch.setattr("api.routers.auth.get_db", mock_get_db)

    real_delete_context = api.routers.auth._delete_registration_context
    delete_spy = unittest.mock.AsyncMock(side_effect=real_delete_context)
    monkeypatch.setattr("api.routers.auth._delete_registration_context", delete_spy)

    raw_request = _build_request(cookies={cookie_name: handle})

    # Execute clear_registration_context directly without mocking
    response = asyncio.run(clear_registration_context(raw_request))

    # 1. Verify deletion helper invocation
    delete_spy.assert_awaited_once_with(fake_redis, handle, terminal=True)

    # 2. Verify exact Redis get/delete calls and ordering
    expected_updated_claim_json = claim.model_copy(update={"terminal": True}).model_dump_json()
    expected_operations = [
        ("get", (context_key,), {}),
        ("get", (claim_key,), {}),
        ("ttl", (claim_key,), {}),
        ("setex", (claim_key, 300, expected_updated_claim_json), {}),
        ("delete", (context_key,), {}),
        ("delete", (verified_key,), {}),
        ("delete", (completion_key,), {}),
    ]
    assert fake_redis.operations == expected_operations

    assert fake_redis.get.call_args_list == [
        unittest.mock.call(context_key),
        unittest.mock.call(claim_key),
    ]
    assert fake_redis.delete.call_args_list == [
        unittest.mock.call(context_key),
        unittest.mock.call(verified_key),
        unittest.mock.call(completion_key),
    ]

    # Prove context and verified keys deleted first, and completion marker deleted afterward
    del_calls = [call.args[0] for call in fake_redis.delete.call_args_list]
    assert del_calls == [context_key, verified_key, completion_key]

    # 3. Verify terminal store state
    assert claim_key in fake_redis.store
    terminal_claim = RegistrationHandoffClaim.model_validate_json(fake_redis.store[claim_key])
    assert terminal_claim.terminal is True
    assert terminal_claim.exchange_id == "exch_char_test_12345"
    assert terminal_claim.context_handle == handle
    assert fake_redis.ttls[claim_key] == 300

    # Prove deleted keys are absent from store
    assert context_key not in fake_redis.store
    assert verified_key not in fake_redis.store
    assert completion_key not in fake_redis.store

    # 4. Prove opaque handle itself is never used as a Redis key
    assert handle not in fake_redis.store
    for op_name, op_args, _ in fake_redis.operations:
        assert handle not in op_args
        assert handle != op_args[0]

    expected_context_digest = hashlib.sha256(handle.encode("utf-8")).hexdigest()
    assert context_key == f"registration_context:{expected_context_digest}"
    assert verified_key == f"registration_context_verified:{expected_context_digest}"
    assert completion_key == f"registration_context_completion:{expected_context_digest}"
    assert handle not in context_key
    assert handle not in verified_key
    assert handle not in completion_key

    # 5. Assert exact acknowledgement contract
    _assert_clear_acknowledgement_contract(response)

    # 6. Assert get_redis awaited exactly once
    assert mock_get_redis.await_count == 1
    mock_get_redis.assert_awaited_once_with()

    # 7. Assert no database dependency/provider called
    assert mock_get_db.call_count == 0
    assert mock_get_db.mock_calls == []


def test_clear_registration_context_absent_cookie_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Characterize clear_registration_context when no cookie is present.

    Proves:
    - _delete_registration_context is invoked with handle=None and terminal=True
    - terminal deletion helper performs no Redis get/delete operations
    - completion-key helper and completion-marker deletion are skipped
    - exact acknowledgement contract: HTTP 204, empty body, no-store/no-cache headers, cleared cookie
    - get_redis is awaited exactly once
    - no DB dependency/provider is called
    """
    fake_redis = StrictFakeRedisBoundary()
    mock_get_redis = unittest.mock.AsyncMock(return_value=fake_redis)
    monkeypatch.setattr("api.routers.auth.get_redis", mock_get_redis)

    mock_get_db = unittest.mock.AsyncMock(side_effect=AssertionError("Database must not be accessed"))
    monkeypatch.setattr("api.routers.auth.get_db", mock_get_db)

    real_delete_context = api.routers.auth._delete_registration_context
    delete_spy = unittest.mock.AsyncMock(side_effect=real_delete_context)
    monkeypatch.setattr("api.routers.auth._delete_registration_context", delete_spy)

    completion_key_spy = unittest.mock.Mock(
        side_effect=api.routers.auth._registration_context_completion_key
    )
    monkeypatch.setattr(
        "api.routers.auth._registration_context_completion_key",
        completion_key_spy,
    )

    raw_request = _build_request(cookies=None)

    cookie_name = _registration_context_cookie_name()
    assert cookie_name not in raw_request.cookies
    assert _registration_context_handle(raw_request) is None

    # Execute clear_registration_context directly without mocking
    response = asyncio.run(clear_registration_context(raw_request))

    # 1. Prove terminal deletion helper receives handle=None with terminal=True
    delete_spy.assert_awaited_once_with(fake_redis, None, terminal=True)

    # 2. Prove no Redis get/delete operations were performed
    assert fake_redis.get.call_count == 0
    assert fake_redis.delete.call_count == 0
    assert fake_redis.ttl.call_count == 0
    assert fake_redis.setex.call_count == 0
    assert len(fake_redis.operations) == 0
    fake_redis.get.assert_not_called()
    fake_redis.delete.assert_not_called()

    # 3. Prove completion-key helper and completion-marker deletion were skipped
    completion_key_spy.assert_not_called()

    # 4. Assert exact acknowledgement contract
    _assert_clear_acknowledgement_contract(response)

    # 5. Assert get_redis awaited exactly once
    assert mock_get_redis.await_count == 1
    mock_get_redis.assert_awaited_once_with()

    # 6. Assert no database dependency/provider called
    assert mock_get_db.call_count == 0
    assert mock_get_db.mock_calls == []


def test_clear_registration_context_cross_site_request_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify cross-site requests fail closed with 403 before Redis or DB operations."""
    mock_get_redis = unittest.mock.AsyncMock()
    monkeypatch.setattr("api.routers.auth.get_redis", mock_get_redis)

    mock_get_db = unittest.mock.AsyncMock(side_effect=AssertionError("Database must not be accessed"))
    monkeypatch.setattr("api.routers.auth.get_db", mock_get_db)

    raw_request = _build_request(
        headers=[(b"sec-fetch-site", b"cross-site")],
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(clear_registration_context(raw_request))

    exc = exc_info.value
    assert isinstance(exc, HTTPException)
    assert exc.status_code == status.HTTP_403_FORBIDDEN
    assert exc.status_code == 403
    assert exc.detail in (
        "درخواست ثبت\u200cنام نامعتبر است",
        "درخواست ثبتنام نامعتبر است",
    )

    mock_get_redis.assert_not_called()
    assert mock_get_db.mock_calls == []


def test_strict_fake_redis_boundary_rejects_unexpected_operation() -> None:
    """Prove strict fake Redis boundary fails on unexpected Redis operations."""
    fake = StrictFakeRedisBoundary()
    with pytest.raises(AssertionError, match="Unexpected Redis operation requested: flushdb"):
        _ = fake.flushdb
