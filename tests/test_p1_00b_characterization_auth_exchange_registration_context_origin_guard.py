"""Stage P1-00b characterization tests for exchange_registration_context origin guard.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Canonical Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-AUTH-POST-AUTH-EXCHANGE-REGISTRATION-CONTEXT-L1696
"""

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()
FENCE = install_p1_00_network_fence()

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException, Request

from api.routers.auth import (
    RegistrationContextExchangeRequest,
    exchange_registration_context,
)


class StrictAsyncDB:
    """Strict async DB double whose execute fails if awaited and exposes an execution count."""

    def __init__(self):
        self.execution_count = 0

    async def execute(self, *args, **kwargs):
        self.execution_count += 1
        raise AssertionError("db.execute must not be awaited or called on origin mismatch")


class AuthExchangeRegistrationContextOriginGuardCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cross_origin_rejects_before_redis_db_or_context_mutation_work(self):
        request_payload = RegistrationContextExchangeRequest(
            kind="invitation",
            token="opaque-test-token-xyz-12345",
            exchange_id="valid-exchange-id-12345",
        )
        raw_request = Request(
            {
                "type": "http",
                "method": "POST",
                "scheme": "https",
                "path": "/api/auth/registration-context/exchange",
                "headers": [
                    (b"host", b"app.example.com"),
                    (b"origin", b"https://attacker.evil.com"),
                ],
                "client": ("198.51.100.1", 44321),
                "server": ("app.example.com", 443),
            }
        )
        db_double = StrictAsyncDB()

        denied_connects_before = len(FENCE["denied_connects"])
        live_redis_before = len(FENCE["live_redis_constructs"])

        get_redis_spy = AsyncMock(side_effect=AssertionError("get_redis must not be called"))
        handle_spy = MagicMock(
            side_effect=AssertionError("_registration_context_handle must not be called")
        )
        is_completed_spy = AsyncMock(
            side_effect=AssertionError("_registration_context_is_completed must not be called")
        )
        load_handoff_claim_spy = AsyncMock(
            side_effect=AssertionError("_load_registration_handoff_claim must not be called")
        )
        resume_handoff_claim_spy = AsyncMock(
            side_effect=AssertionError("_resume_registration_handoff_claim must not be called")
        )
        consume_handoff_spy = AsyncMock(
            side_effect=AssertionError("_consume_direct_registration_handoff must not be called")
        )
        delete_context_spy = AsyncMock(
            side_effect=AssertionError("_delete_registration_context must not be called")
        )
        load_session_token_spy = AsyncMock(
            side_effect=AssertionError("_load_registration_session_token must not be called")
        )
        load_invitation_spy = AsyncMock(
            side_effect=AssertionError("_load_valid_invitation_by_token must not be called")
        )
        store_context_spy = AsyncMock(
            side_effect=AssertionError("_store_registration_context must not be called")
        )
        claim_handoff_spy = AsyncMock(
            side_effect=AssertionError("_claim_registration_handoff must not be called")
        )
        json_response_spy = MagicMock(
            side_effect=AssertionError("_registration_context_json_response must not be called")
        )
        set_cookie_spy = MagicMock(
            side_effect=AssertionError("_set_registration_context_cookie must not be called")
        )

        with patch("api.routers.auth.get_redis", get_redis_spy), patch(
            "api.routers.auth._registration_context_handle", handle_spy
        ), patch(
            "api.routers.auth._registration_context_is_completed", is_completed_spy
        ), patch(
            "api.routers.auth._load_registration_handoff_claim", load_handoff_claim_spy
        ), patch(
            "api.routers.auth._resume_registration_handoff_claim", resume_handoff_claim_spy
        ), patch(
            "api.routers.auth._consume_direct_registration_handoff", consume_handoff_spy
        ), patch(
            "api.routers.auth._delete_registration_context", delete_context_spy
        ), patch(
            "api.routers.auth._load_registration_session_token", load_session_token_spy
        ), patch(
            "api.routers.auth._load_valid_invitation_by_token", load_invitation_spy
        ), patch(
            "api.routers.auth._store_registration_context", store_context_spy
        ), patch(
            "api.routers.auth._claim_registration_handoff", claim_handoff_spy
        ), patch(
            "api.routers.auth._registration_context_json_response", json_response_spy
        ), patch(
            "api.routers.auth._set_registration_context_cookie", set_cookie_spy
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await exchange_registration_context(
                    req=request_payload,
                    raw_request=raw_request,
                    db=db_double,
                )

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "درخواست ثبت\u200cنام نامعتبر است")

        self.assertEqual(db_double.execution_count, 0)

        get_redis_spy.assert_not_called()
        handle_spy.assert_not_called()
        is_completed_spy.assert_not_called()
        load_handoff_claim_spy.assert_not_called()
        resume_handoff_claim_spy.assert_not_called()
        consume_handoff_spy.assert_not_called()
        delete_context_spy.assert_not_called()
        load_session_token_spy.assert_not_called()
        load_invitation_spy.assert_not_called()
        store_context_spy.assert_not_called()
        claim_handoff_spy.assert_not_called()
        json_response_spy.assert_not_called()
        set_cookie_spy.assert_not_called()

        self.assertEqual(len(FENCE["denied_connects"]), denied_connects_before)
        self.assertEqual(len(FENCE["live_redis_constructs"]), live_redis_before)


if __name__ == "__main__":
    unittest.main()
