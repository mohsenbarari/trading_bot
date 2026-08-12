"""Stage 2: Iran self-service save, bot forward, and outage rejection."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers.auth import update_my_offer_overtime, update_offer_overtime_internal
from core.offer_overtime_preference_transport import (
    OFFER_OVERTIME_PREFERENCE_INTERNAL_PATH,
    forward_offer_overtime_preference_to_iran,
)
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, override_current_server
from core.services.offer_overtime_preference_service import (
    BOT_SAVE_UNAVAILABLE_MESSAGE,
    INVALID_OVERTIME_VALUE_MESSAGE,
    OVERTIME_NOT_AVAILABLE_MESSAGE,
    REACHABILITY_WARNING_MESSAGE,
    SAVE_SUCCESS_NONZERO_MESSAGE,
    SAVE_SUCCESS_ZERO_MESSAGE,
    OfferOvertimePreferenceError,
    OfferOvertimePreferenceNotAllowedError,
    OfferOvertimePreferenceTransportError,
    persist_overtime_preference,
    save_overtime_preference_from_bot,
)
import schemas


class _FakeRequest:
    def __init__(self, body: bytes, headers=None):
        self._body = body
        self.headers = headers or {
            "x-timestamp": "1",
            "x-signature": "sig",
            "x-api-key": "key",
            "x-source-server": SERVER_FOREIGN,
        }

    async def body(self):
        return self._body


class PersistPreferenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_iran_save_writes_the_value_and_returns_approved_copy(self):
        user = SimpleNamespace(id=7, offer_overtime_minutes=0)
        db = object()
        with patch(
            "core.services.offer_overtime_preference_service.evaluate_overtime_preference_eligibility",
            new=AsyncMock(return_value=SimpleNamespace(allowed=True, reason=None)),
        ):
            result = await persist_overtime_preference(db, user, 5)

        self.assertEqual(user.offer_overtime_minutes, 5)
        self.assertEqual(result.offer_overtime_minutes, 5)
        self.assertEqual(result.detail, SAVE_SUCCESS_NONZERO_MESSAGE.format(minutes=5))
        self.assertEqual(result.warning, REACHABILITY_WARNING_MESSAGE)

    async def test_zero_save_uses_the_disabled_copy_without_warning(self):
        user = SimpleNamespace(id=7, offer_overtime_minutes=4)
        with patch(
            "core.services.offer_overtime_preference_service.evaluate_overtime_preference_eligibility",
            new=AsyncMock(return_value=SimpleNamespace(allowed=True, reason=None)),
        ):
            result = await persist_overtime_preference(object(), user, 0)

        self.assertEqual(user.offer_overtime_minutes, 0)
        self.assertEqual(result.detail, SAVE_SUCCESS_ZERO_MESSAGE)
        self.assertIsNone(result.warning)

    async def test_ineligible_account_is_refused_without_a_write(self):
        user = SimpleNamespace(id=7, offer_overtime_minutes=0)
        with patch(
            "core.services.offer_overtime_preference_service.evaluate_overtime_preference_eligibility",
            new=AsyncMock(return_value=SimpleNamespace(allowed=False, reason="accountant")),
        ):
            with self.assertRaises(OfferOvertimePreferenceNotAllowedError) as caught:
                await persist_overtime_preference(object(), user, 3)
        self.assertEqual(user.offer_overtime_minutes, 0)
        self.assertEqual(caught.exception.message, OVERTIME_NOT_AVAILABLE_MESSAGE)

    async def test_invalid_value_is_refused_without_a_write(self):
        user = SimpleNamespace(id=7, offer_overtime_minutes=2)
        with self.assertRaises(OfferOvertimePreferenceError) as caught:
            await persist_overtime_preference(object(), user, 11)
        self.assertEqual(user.offer_overtime_minutes, 2)
        self.assertEqual(caught.exception.message, INVALID_OVERTIME_VALUE_MESSAGE)


class WebAppEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_webapp_save_on_iran_commits_and_invalidates_cache(self):
        user = SimpleNamespace(id=7, telegram_id=99, offer_overtime_minutes=0)
        db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())
        with override_current_server(SERVER_IRAN), patch(
            "core.services.offer_overtime_preference_service.evaluate_overtime_preference_eligibility",
            new=AsyncMock(return_value=SimpleNamespace(allowed=True, reason=None)),
        ), patch(
            "core.cache.invalidate_user_cache",
            new=AsyncMock(),
        ) as cache_mock:
            result = await update_my_offer_overtime(
                payload=schemas.UserOfferOvertimeUpdate(offer_overtime_minutes=3),
                current_user=user,
                db=db,
            )

        self.assertEqual(user.offer_overtime_minutes, 3)
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once_with(user)
        cache_mock.assert_awaited_once_with(99)
        self.assertEqual(result.offer_overtime_minutes, 3)
        self.assertEqual(result.warning, REACHABILITY_WARNING_MESSAGE)

    async def test_webapp_save_on_foreign_is_rejected_without_a_write(self):
        user = SimpleNamespace(id=7, telegram_id=None, offer_overtime_minutes=0)
        db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())
        with override_current_server(SERVER_FOREIGN):
            from fastapi import HTTPException

            with self.assertRaises(HTTPException) as caught:
                await update_my_offer_overtime(
                    payload=schemas.UserOfferOvertimeUpdate(offer_overtime_minutes=3),
                    current_user=user,
                    db=db,
                )
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.detail, BOT_SAVE_UNAVAILABLE_MESSAGE)
        self.assertEqual(user.offer_overtime_minutes, 0)
        db.commit.assert_not_awaited()


class InternalEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_internal_command_persists_on_iran(self):
        import json

        user = SimpleNamespace(id=11, telegram_id=None, is_deleted=False, offer_overtime_minutes=0)
        db = SimpleNamespace(
            get=AsyncMock(return_value=user),
            commit=AsyncMock(),
            refresh=AsyncMock(),
        )
        body = json.dumps({"user_id": 11, "offer_overtime_minutes": 4}).encode()
        request = _FakeRequest(body)
        with override_current_server(SERVER_IRAN), patch(
            "api.routers.auth.verify_internal_signature",
            return_value=True,
        ), patch(
            "core.services.offer_overtime_preference_service.evaluate_overtime_preference_eligibility",
            new=AsyncMock(return_value=SimpleNamespace(allowed=True, reason=None)),
        ):
            result = await update_offer_overtime_internal(raw_request=request, db=db)

        self.assertEqual(user.offer_overtime_minutes, 4)
        self.assertEqual(result.offer_overtime_minutes, 4)
        db.commit.assert_awaited_once()


class BotForwardTests(unittest.IsolatedAsyncioTestCase):
    async def test_bot_save_forwards_and_never_writes_locally(self):
        user = SimpleNamespace(id=21, offer_overtime_minutes=0)
        db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())
        iran_body = {
            "offer_overtime_minutes": 6,
            "detail": SAVE_SUCCESS_NONZERO_MESSAGE.format(minutes=6),
            "warning": REACHABILITY_WARNING_MESSAGE,
        }
        with override_current_server(SERVER_FOREIGN), patch(
            "core.services.offer_overtime_preference_service.evaluate_overtime_preference_eligibility",
            new=AsyncMock(return_value=SimpleNamespace(allowed=True, reason=None)),
        ), patch(
            "core.offer_overtime_preference_transport.forward_offer_overtime_preference_to_iran",
            new=AsyncMock(return_value=(200, iran_body)),
        ) as forward_mock:
            result = await save_overtime_preference_from_bot(db, user, 6)

        forward_mock.assert_awaited_once_with(
            {"user_id": 21, "offer_overtime_minutes": 6}
        )
        self.assertEqual(user.offer_overtime_minutes, 0)
        db.commit.assert_not_awaited()
        self.assertEqual(result.offer_overtime_minutes, 6)
        self.assertEqual(result.warning, REACHABILITY_WARNING_MESSAGE)

    async def test_bot_save_outage_raises_approved_copy_without_local_write(self):
        user = SimpleNamespace(id=21, offer_overtime_minutes=1)
        with override_current_server(SERVER_FOREIGN), patch(
            "core.services.offer_overtime_preference_service.evaluate_overtime_preference_eligibility",
            new=AsyncMock(return_value=SimpleNamespace(allowed=True, reason=None)),
        ), patch(
            "core.offer_overtime_preference_transport.forward_offer_overtime_preference_to_iran",
            new=AsyncMock(return_value=(503, {"detail": BOT_SAVE_UNAVAILABLE_MESSAGE})),
        ):
            with self.assertRaises(OfferOvertimePreferenceTransportError) as caught:
                await save_overtime_preference_from_bot(object(), user, 2)
        self.assertEqual(user.offer_overtime_minutes, 1)
        self.assertEqual(caught.exception.message, BOT_SAVE_UNAVAILABLE_MESSAGE)


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_transport_rejects_outside_foreign_and_missing_peer(self):
        with override_current_server(SERVER_IRAN):
            status, body = await forward_offer_overtime_preference_to_iran(
                {"user_id": 1, "offer_overtime_minutes": 2}
            )
        self.assertEqual(status, 403)
        self.assertEqual(body["detail"], BOT_SAVE_UNAVAILABLE_MESSAGE)

        with override_current_server(SERVER_FOREIGN), patch(
            "core.offer_overtime_preference_transport.peer_server_url_for",
            return_value=None,
        ):
            status, body = await forward_offer_overtime_preference_to_iran(
                {"user_id": 1, "offer_overtime_minutes": 2}
            )
        self.assertEqual(status, 503)
        self.assertEqual(body["detail"], BOT_SAVE_UNAVAILABLE_MESSAGE)

    async def test_transport_posts_signed_body_to_the_iran_path(self):
        class _Response:
            status_code = 200

            def json(self):
                return {"offer_overtime_minutes": 2, "detail": "ok", "warning": None}

        class _Client:
            def __init__(self, calls):
                self.calls = calls

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return _Response()

        calls = []
        with override_current_server(SERVER_FOREIGN), patch(
            "core.offer_overtime_preference_transport.peer_server_url_for",
            return_value="https://iran.example",
        ), patch(
            "core.offer_overtime_preference_transport.sign_internal_payload",
            return_value="signature",
        ), patch(
            "core.offer_overtime_preference_transport.httpx.AsyncClient",
            return_value=_Client(calls),
        ):
            status, body = await forward_offer_overtime_preference_to_iran(
                {"user_id": 9, "offer_overtime_minutes": 2}
            )

        self.assertEqual(status, 200)
        self.assertEqual(body["offer_overtime_minutes"], 2)
        self.assertEqual(
            calls[0][0],
            f"https://iran.example{OFFER_OVERTIME_PREFERENCE_INTERNAL_PATH}",
        )
        self.assertEqual(calls[0][1]["headers"]["X-Source-Server"], SERVER_FOREIGN)
        self.assertEqual(calls[0][1]["headers"]["X-Signature"], "signature")


if __name__ == "__main__":
    unittest.main()
