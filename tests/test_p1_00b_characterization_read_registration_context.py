"""Hermetic characterization tests for read_registration_context endpoint.

Stage: docs/refactor/two-server/section-1/stages/P1-00b.md (Stage P1-00b)
Assignment: WA-P1-00B-FREEZE-20260904-001
Gap Reference: docs/refactor/two-server/section-1/06-current-drift-register.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed Inventory: API-AUTH-POST-AUTH-READ-REGISTRATION-CONTEXT-L1819

This unit provides bounded assertion evidence for inventory seed
API-AUTH-POST-AUTH-READ-REGISTRATION-CONTEXT-L1819 only. This bounded unit
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
    REGISTRATION_CONTEXT_COOKIE_DEVELOPMENT,
    REGISTRATION_CONTEXT_COOKIE_PRODUCTION,
    REGISTRATION_CONTEXT_TTL_SECONDS,
    _assert_registration_context_same_origin,
    _clear_registration_context_cookie,
    _load_registration_context,
    _registration_context_clear_cookie_header,
    _registration_context_completion_key,
    _registration_context_cookie_name,
    _registration_context_handle,
    _registration_context_is_completed,
    _registration_context_secure_cookie,
    _registration_no_store_headers,
    _set_registration_context_cookie,
    read_registration_context,
)


class FakeRedisBoundary:
    """Hermetic in-memory Redis test double for characterization tests."""

    def __init__(self, store: dict[str, Any] | None = None) -> None:
        self.store: dict[str, Any] = dict(store or {})
        self.get = unittest.mock.AsyncMock(side_effect=self._get)
        self.set = unittest.mock.AsyncMock(side_effect=self._set)
        self.delete = unittest.mock.AsyncMock(side_effect=self._delete)

    async def _get(self, key: str) -> Any:
        return self.store.get(key)

    async def _set(self, key: str, value: Any, *args: Any, **kwargs: Any) -> bool:
        self.store[key] = value
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
        "path": "/auth/registration-context",
        "raw_path": b"/auth/registration-context",
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


def test_read_registration_context_completed_cookie_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Characterize completed-cookie path returning registration_complete with refreshed cookie."""
    handle = "test-char-handle-p100b-20260912-completed-xyz"
    cookie_name = _registration_context_cookie_name()
    completion_key = _registration_context_completion_key(handle)

    fake_redis = FakeRedisBoundary(store={completion_key: "completed_marker"})
    mock_get_redis = unittest.mock.AsyncMock(return_value=fake_redis)
    monkeypatch.setattr("api.routers.auth.get_redis", mock_get_redis)

    mock_load_context = unittest.mock.AsyncMock(
        side_effect=AssertionError("_load_registration_context must be suppressed on completed cookie")
    )
    monkeypatch.setattr("api.routers.auth._load_registration_context", mock_load_context)

    fake_db = unittest.mock.AsyncMock(spec=AsyncSession)
    raw_request = _build_request(cookies={cookie_name: handle})

    response = asyncio.run(read_registration_context(raw_request, db=fake_db))

    assert response.status_code == status.HTTP_200_OK
    assert response.status_code == 200

    payload = json.loads(response.body.decode("utf-8"))
    assert payload == {"status": "registration_complete"}

    assert response.headers.get("Cache-Control") == "no-store"
    assert response.headers.get("Pragma") == "no-cache"

    assert "set-cookie" in response.headers
    set_cookie_raw = response.headers["set-cookie"]
    cookie_parser = SimpleCookie()
    cookie_parser.load(set_cookie_raw)
    assert cookie_name in cookie_parser

    morsel = cookie_parser[cookie_name]
    assert morsel.value == handle
    assert int(morsel["max-age"]) == REGISTRATION_CONTEXT_TTL_SECONDS
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

    assert mock_get_redis.await_count == 1
    mock_get_redis.assert_awaited_once_with()

    assert fake_redis.get.await_count == 1
    fake_redis.get.assert_awaited_once_with(completion_key)
    assert fake_redis.set.await_count == 0
    fake_redis.set.assert_not_called()
    assert fake_redis.delete.await_count == 0
    fake_redis.delete.assert_not_called()

    assert mock_load_context.await_count == 0
    mock_load_context.assert_not_called()

    assert fake_db.mock_calls == []


def test_read_registration_context_missing_cookie_410_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Characterize missing-or-expired context path with no cookie raising HTTP 410."""
    fake_redis = FakeRedisBoundary()
    mock_get_redis = unittest.mock.AsyncMock(return_value=fake_redis)
    monkeypatch.setattr("api.routers.auth.get_redis", mock_get_redis)

    fake_db = unittest.mock.AsyncMock(spec=AsyncSession)
    raw_request = _build_request(cookies=None)

    cookie_name = _registration_context_cookie_name()
    assert cookie_name not in raw_request.cookies
    assert _registration_context_handle(raw_request) is None

    # Confirm request headers are valid and not cross-site rejected
    _assert_registration_context_same_origin(raw_request)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(read_registration_context(raw_request, db=fake_db))

    exc = exc_info.value
    assert isinstance(exc, HTTPException)
    assert exc.status_code == status.HTTP_410_GONE
    assert exc.status_code == 410
    assert exc.status_code != status.HTTP_403_FORBIDDEN
    assert exc.detail in (
        "جلسه ثبت\u200cنام در دسترس نیست یا منقضی شده است",
        "جلسه ثبتنام در دسترس نیست یا منقضی شده است",
    )
    assert exc.detail == "جلسه ثبت\u200cنام در دسترس نیست یا منقضی شده است"

    assert exc.headers is not None
    assert exc.headers.get("Cache-Control") == "no-store"
    assert exc.headers.get("Pragma") == "no-cache"
    assert "Set-Cookie" in exc.headers

    clear_header = exc.headers["Set-Cookie"]
    assert clear_header == _registration_context_clear_cookie_header()

    cookie_parser = SimpleCookie()
    cookie_parser.load(clear_header)
    assert cookie_name in cookie_parser

    morsel = cookie_parser[cookie_name]
    assert morsel["max-age"] in ("0", 0)
    assert morsel["path"] == "/"
    assert morsel["httponly"] is True
    assert "; httponly" in clear_header.lower()
    assert morsel["samesite"].lower() == "strict"

    expected_secure = _registration_context_secure_cookie()
    if expected_secure:
        assert bool(morsel["secure"]) is True
        assert "; secure" in clear_header.lower()
    else:
        assert bool(morsel["secure"]) is False
        assert "; secure" not in clear_header.lower()

    assert mock_get_redis.await_count == 1
    mock_get_redis.assert_awaited_once_with()

    assert fake_redis.get.await_count == 0
    fake_redis.get.assert_not_called()
    assert fake_redis.set.await_count == 0
    fake_redis.set.assert_not_called()
    assert fake_redis.delete.await_count == 0
    fake_redis.delete.assert_not_called()

    assert fake_db.mock_calls == []


def test_read_registration_context_cross_site_request_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify cross-site requests fail closed with 403 before Redis or DB operations."""
    mock_get_redis = unittest.mock.AsyncMock()
    monkeypatch.setattr("api.routers.auth.get_redis", mock_get_redis)

    fake_db = unittest.mock.AsyncMock(spec=AsyncSession)
    raw_request = _build_request(
        headers=[(b"sec-fetch-site", b"cross-site")],
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(read_registration_context(raw_request, db=fake_db))

    exc = exc_info.value
    assert isinstance(exc, HTTPException)
    assert exc.status_code == status.HTTP_403_FORBIDDEN
    assert exc.status_code == 403
    assert exc.detail in (
        "درخواست ثبت\u200cنام نامعتبر است",
        "درخواست ثبتنام نامعتبر است",
    )

    mock_get_redis.assert_not_called()
    assert fake_db.mock_calls == []
