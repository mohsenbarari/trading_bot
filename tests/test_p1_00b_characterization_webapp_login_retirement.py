import contextlib
import hashlib
import hmac
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import urlencode

from fastapi import HTTPException

from api.routers.auth import WebAppLogin, webapp_login

try:
    from tests.test_auth_router_special_logins import build_webapp_init_data
except ImportError:
    build_webapp_init_data = None


class FailOnAccessSentinel:
    """Sentinel object that raises AssertionError on any attribute access, item access, or call."""

    def __init__(self, name: str) -> None:
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_call_count", 0)

    def __getattribute__(self, item: str):
        if item in (
            "_name",
            "_call_count",
            "__class__",
            "__repr__",
            "assert_not_called",
            "assert_not_awaited",
            "called",
            "call_count",
        ):
            return object.__getattribute__(self, item)
        name = object.__getattribute__(self, "_name")
        raise AssertionError(f"FailOnAccessSentinel: attribute '{item}' accessed on {name}")

    def __getitem__(self, item):
        name = object.__getattribute__(self, "_name")
        raise AssertionError(f"FailOnAccessSentinel: item access [{item!r}] on {name}")

    def __setattr__(self, key, value):
        name = object.__getattribute__(self, "_name")
        raise AssertionError(f"FailOnAccessSentinel: attribute mutation '{key}' on {name}")

    def __call__(self, *args, **kwargs):
        name = object.__getattribute__(self, "_name")
        raise AssertionError(f"FailOnCall: {name} was called")

    def assert_not_called(self) -> None:
        pass

    def assert_not_awaited(self) -> None:
        pass

    @property
    def called(self) -> bool:
        return False

    @property
    def call_count(self) -> int:
        return 0

    def __repr__(self) -> str:
        return f"<FailOnAccessSentinel:{object.__getattribute__(self, '_name')}>"


def _build_realistic_valid_shaped_init_data() -> str:
    """Construct a realistic valid-shaped Telegram Mini App query payload with valid HMAC."""
    bot_token = "123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ"
    user_payload = {
        "id": 987654321,
        "first_name": "Hamid",
        "last_name": "Alavi",
        "username": "hamid_tester",
        "language_code": "fa",
    }
    if build_webapp_init_data is not None:
        return build_webapp_init_data(
            bot_token=bot_token,
            user_payload=user_payload,
            auth_date=1700000000,
        )
    user_json = json.dumps(user_payload, separators=(",", ":"))
    parsed_data = {
        "auth_date": "1700000000",
        "query_id": "AAEAAAE",
        "user": user_json,
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calc_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    parsed_data["hash"] = calc_hash
    return urlencode(parsed_data)


@contextlib.contextmanager
def assert_retired_boundaries_untouched():
    """Context manager setting up fail-on-call patches across all 5 dependency boundaries."""
    fail_settings = FailOnAccessSentinel("settings")

    # Request-derived routing/IP work
    mock_client_ip = Mock(name="client_ip_from_request", side_effect=AssertionError("client_ip_from_request was called"))
    mock_server_from_request = Mock(name="server_from_request", side_effect=AssertionError("server_from_request was called"))
    mock_home_server = Mock(name="_login_home_server", side_effect=AssertionError("_login_home_server was called"))
    mock_assert_login = Mock(name="assert_login_allowed_for_server", side_effect=AssertionError("assert_login_allowed_for_server was called"))
    mock_prepare_login = Mock(name="prepare_verified_login_for_server", side_effect=AssertionError("prepare_verified_login_for_server was called"))

    # Session handling
    mock_handle_login = AsyncMock(name="handle_login_session", side_effect=AssertionError("handle_login_session was called"))
    mock_get_active_sessions = AsyncMock(name="get_active_sessions", side_effect=AssertionError("get_active_sessions was called"))
    mock_deactivate_session = AsyncMock(name="deactivate_session", side_effect=AssertionError("deactivate_session was called"))
    mock_publish_revocation = AsyncMock(name="publish_session_revocation", side_effect=AssertionError("publish_session_revocation was called"))
    mock_hash_token = Mock(name="hash_token", side_effect=AssertionError("hash_token was called"))
    mock_get_session_by_refresh = AsyncMock(name="get_session_by_refresh_token", side_effect=AssertionError("get_session_by_refresh_token was called"))

    # Access / refresh token creation
    mock_create_access_token = Mock(name="create_access_token", side_effect=AssertionError("create_access_token was called"))
    mock_create_refresh_token = Mock(name="create_refresh_token", side_effect=AssertionError("create_refresh_token was called"))

    # Telegram init-data parsing or HMAC validation
    mock_hmac_new = Mock(name="hmac.new", side_effect=AssertionError("hmac.new was called"))
    mock_sha256 = Mock(name="hashlib.sha256", side_effect=AssertionError("hashlib.sha256 was called"))
    mock_secret_equals = Mock(name="constant_time_secret_equals", side_effect=AssertionError("constant_time_secret_equals was called"))

    boundaries = {
        "settings": fail_settings,
        "client_ip_from_request": mock_client_ip,
        "server_from_request": mock_server_from_request,
        "_login_home_server": mock_home_server,
        "assert_login_allowed_for_server": mock_assert_login,
        "prepare_verified_login_for_server": mock_prepare_login,
        "handle_login_session": mock_handle_login,
        "get_active_sessions": mock_get_active_sessions,
        "deactivate_session": mock_deactivate_session,
        "publish_session_revocation": mock_publish_revocation,
        "hash_token": mock_hash_token,
        "get_session_by_refresh_token": mock_get_session_by_refresh,
        "create_access_token": mock_create_access_token,
        "create_refresh_token": mock_create_refresh_token,
        "hmac.new": mock_hmac_new,
        "hashlib.sha256": mock_sha256,
        "constant_time_secret_equals": mock_secret_equals,
    }

    patches = [
        patch("api.routers.auth.settings", new=fail_settings),
        patch("api.routers.auth.client_ip_from_request", new=mock_client_ip),
        patch("api.routers.auth.server_from_request", new=mock_server_from_request),
        patch("api.routers.auth._login_home_server", new=mock_home_server),
        patch("api.routers.auth.assert_login_allowed_for_server", new=mock_assert_login),
        patch("api.routers.auth.prepare_verified_login_for_server", new=mock_prepare_login),
        patch("api.routers.auth.handle_login_session", new=mock_handle_login),
        patch("api.routers.auth.get_active_sessions", new=mock_get_active_sessions),
        patch("api.routers.auth.deactivate_session", new=mock_deactivate_session),
        patch("api.routers.auth.publish_session_revocation", new=mock_publish_revocation),
        patch("api.routers.auth.hash_token", new=mock_hash_token),
        patch("api.routers.auth.get_session_by_refresh_token", new=mock_get_session_by_refresh),
        patch("api.routers.auth.create_access_token", new=mock_create_access_token),
        patch("api.routers.auth.create_refresh_token", new=mock_create_refresh_token),
        patch("api.routers.auth.hmac.new", new=mock_hmac_new),
        patch("api.routers.auth.hashlib.sha256", new=mock_sha256),
        patch("api.routers.auth.constant_time_secret_equals", new=mock_secret_equals),
    ]

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield boundaries

    for name, boundary in boundaries.items():
        boundary.assert_not_called()
        if isinstance(boundary, AsyncMock):
            boundary.assert_not_awaited()


class WebAppLoginRetirementCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests proving webapp_login is retired and side-effect-free before dependency work."""

    async def test_webapp_login_rejects_deliberately_malformed_init_data_before_dependencies(self) -> None:
        malformed_init_data = "malformed_telegram_init_data_content=%%INVALID%%&&broken==value&&&no_hash"
        request_sentinel = FailOnAccessSentinel("raw_request")
        db_sentinel = FailOnAccessSentinel("db")

        with assert_retired_boundaries_untouched():
            with self.assertRaises(HTTPException) as exc_info:
                await webapp_login(
                    WebAppLogin(init_data=malformed_init_data),
                    raw_request=request_sentinel,
                    db=db_sentinel,
                )

            self.assertEqual(exc_info.exception.status_code, 410)
            self.assertEqual(exc_info.exception.detail, "Telegram Mini App login is retired")
            request_sentinel.assert_not_called()
            db_sentinel.assert_not_called()

    async def test_webapp_login_rejects_realistic_valid_shaped_init_data_before_dependencies(self) -> None:
        valid_shaped_init_data = _build_realistic_valid_shaped_init_data()
        request_sentinel = FailOnAccessSentinel("raw_request")
        db_sentinel = FailOnAccessSentinel("db")

        with assert_retired_boundaries_untouched():
            with self.assertRaises(HTTPException) as exc_info:
                await webapp_login(
                    WebAppLogin(init_data=valid_shaped_init_data),
                    raw_request=request_sentinel,
                    db=db_sentinel,
                )

            self.assertEqual(exc_info.exception.status_code, 410)
            self.assertEqual(exc_info.exception.detail, "Telegram Mini App login is retired")
            request_sentinel.assert_not_called()
            db_sentinel.assert_not_called()
