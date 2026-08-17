from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers import trades
from bot.handlers import offer_overtime_callbacks
from core.offer_overtime_request_forwarding import forward_overtime_requester_cancel
from core.offer_overtime_bot_copy import M15_REQUESTER_STATUS_CANCELLED
from core.telegram_delivery_overtime_requester_status_contract import (
    build_overtime_requester_cancel_callback_data,
)
from models.offer_request import OfferRequestStatus, OfferRequestWorkflow
from tests.test_trades_router_authoritative_guards import make_context, make_user


def _ledger(**overrides):
    values = {
        "request_public_id": "req_cancel_cross_1",
        "offer_public_id": "ofr_cancel_cross_1",
        "request_home_server": "iran",
        "request_source_server": "foreign",
        "request_source_surface": "telegram_bot",
        "workflow_kind": OfferRequestWorkflow.OVERTIME,
        "result_status": OfferRequestStatus.OVERTIME_PRESENTED,
        "requester_user_id": 9,
        "offer_owner_user_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _ClientContext:
    def __init__(self, response):
        self.post = AsyncMock(return_value=response)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _SessionContext:
    def __init__(self):
        self.rollback = AsyncMock()
        self.commit = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class OvertimeCancelForwardingClientTests(unittest.IsolatedAsyncioTestCase):
    async def _forward(self, response_body, *, status_code=200):
        response = SimpleNamespace(
            status_code=status_code,
            json=lambda: response_body,
            text=json.dumps(response_body),
        )
        client = _ClientContext(response)
        with patch(
            "core.offer_overtime_request_forwarding.peer_server_url_for",
            return_value="https://peer.invalid",
        ), patch(
            "core.offer_overtime_request_forwarding.current_server",
            return_value="foreign",
        ), patch(
            "core.offer_overtime_request_forwarding.sign_internal_payload",
            return_value="signature",
        ), patch(
            "core.offer_overtime_request_forwarding.httpx.AsyncClient",
            return_value=client,
        ):
            result = await forward_overtime_requester_cancel(
                "iran",
                {
                    "request_public_id": "req_cancel_cross_1",
                    "requester_user_id": 9,
                    "source_server": "foreign",
                },
            )
        return result, client

    async def test_valid_authoritative_receipt_is_accepted(self):
        body = {
            "request_public_id": "req_cancel_cross_1",
            "result_status": "overtime_cancelled_by_requester",
            "replayed": False,
        }
        result, client = await self._forward(body)
        self.assertEqual(result, (200, body))
        client.post.assert_awaited_once()

    async def test_success_without_matching_receipt_fails_closed(self):
        result, _client = await self._forward(
            {
                "request_public_id": "req_other",
                "result_status": "overtime_cancelled_by_requester",
                "replayed": False,
            }
        )
        self.assertEqual(result[0], 503)


class OvertimeCancelRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_cancel_forwards_from_request_source_to_remote_home(self):
        ledger = _ledger()
        db = SimpleNamespace(rollback=AsyncMock())
        receipt = {
            "request_public_id": ledger.request_public_id,
            "result_status": "overtime_cancelled_by_requester",
            "replayed": False,
        }
        with patch(
            "api.routers.trades.load_overtime_request_by_public_id",
            new=AsyncMock(return_value=ledger),
        ), patch("api.routers.trades.current_server", return_value="foreign"), patch(
            "api.routers.trades.forward_overtime_requester_cancel",
            new=AsyncMock(return_value=(200, receipt)),
        ) as forward:
            result = await trades.cancel_overtime_request(
                request_public_id=ledger.request_public_id,
                db=db,
                context=make_context(make_user(id=9)),
            )

        self.assertEqual(result, receipt)
        db.rollback.assert_awaited_once()
        forward.assert_awaited_once_with(
            "iran",
            {
                "request_public_id": ledger.request_public_id,
                "requester_user_id": 9,
                "source_server": "foreign",
            },
        )

    async def test_public_cancel_does_not_forward_from_unrelated_server(self):
        ledger = _ledger(request_source_server="iran")
        with patch(
            "api.routers.trades.load_overtime_request_by_public_id",
            new=AsyncMock(return_value=ledger),
        ), patch("api.routers.trades.current_server", return_value="foreign"):
            with self.assertRaises(HTTPException) as caught:
                await trades.cancel_overtime_request(
                    request_public_id=ledger.request_public_id,
                    db=SimpleNamespace(),
                    context=make_context(make_user(id=9)),
                )
        self.assertEqual(caught.exception.status_code, 409)

    async def test_internal_cancel_is_idempotent_on_authoritative_home(self):
        ledger = _ledger(
            result_status=OfferRequestStatus.OVERTIME_CANCELLED_BY_REQUESTER
        )
        request = SimpleNamespace(
            body=AsyncMock(return_value=b"{}"),
            headers={
                "x-source-server": "foreign",
                "x-timestamp": "1",
                "x-signature": "sig",
                "x-api-key": "key",
            },
        )
        db = SimpleNamespace(commit=AsyncMock())
        with patch(
            "api.routers.trades.verify_internal_signature", return_value=True
        ), patch("api.routers.trades.current_server", return_value="iran"), patch(
            "api.routers.trades.load_overtime_request_by_public_id",
            new=AsyncMock(return_value=ledger),
        ), patch(
            "api.routers.trades.cancel_by_requester", new=AsyncMock()
        ) as cancel:
            result = await trades.cancel_overtime_request_internal(
                internal_data=trades.InternalOvertimeRequesterCancelRequest(
                    request_public_id=ledger.request_public_id,
                    requester_user_id=9,
                    source_server="foreign",
                ),
                raw_request=request,
                db=db,
            )
        self.assertTrue(result["replayed"])
        cancel.assert_not_awaited()
        db.commit.assert_not_awaited()

    async def test_internal_cancel_mutates_once_on_authoritative_home(self):
        ledger = _ledger()
        request = SimpleNamespace(
            body=AsyncMock(return_value=b"{}"),
            headers={
                "x-source-server": "foreign",
                "x-timestamp": "1",
                "x-signature": "sig",
                "x-api-key": "key",
            },
        )
        db = SimpleNamespace(commit=AsyncMock())
        with patch(
            "api.routers.trades.verify_internal_signature", return_value=True
        ), patch("api.routers.trades.current_server", return_value="iran"), patch(
            "api.routers.trades.load_overtime_request_by_public_id",
            new=AsyncMock(return_value=ledger),
        ), patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=2)),
        ), patch(
            "api.routers.trades.cancel_by_requester", new=AsyncMock()
        ) as cancel:
            result = await trades.cancel_overtime_request_internal(
                internal_data=trades.InternalOvertimeRequesterCancelRequest(
                    request_public_id=ledger.request_public_id,
                    requester_user_id=9,
                    source_server="foreign",
                ),
                raw_request=request,
                db=db,
            )
        self.assertFalse(result["replayed"])
        cancel.assert_awaited_once()
        db.commit.assert_awaited_once()


class OvertimeCancelBotIngressTests(unittest.IsolatedAsyncioTestCase):
    async def test_bot_cancels_foreign_source_request_on_iran_offer(self):
        ledger = _ledger()
        session = _SessionContext()
        callback = SimpleNamespace(
            data=build_overtime_requester_cancel_callback_data(
                request_public_id=ledger.request_public_id
            )
        )
        receipt = {
            "request_public_id": ledger.request_public_id,
            "result_status": "overtime_cancelled_by_requester",
            "replayed": False,
        }
        with patch(
            "bot.handlers.offer_overtime_callbacks.AsyncSessionLocal",
            return_value=session,
        ), patch(
            "bot.handlers.offer_overtime_callbacks.load_overtime_request_by_public_id",
            new=AsyncMock(return_value=ledger),
        ) as load, patch(
            "bot.handlers.offer_overtime_callbacks.current_server",
            return_value="foreign",
        ), patch(
            "bot.handlers.offer_overtime_callbacks.forward_overtime_requester_cancel",
            new=AsyncMock(return_value=(200, receipt)),
        ) as forward, patch(
            "bot.handlers.offer_overtime_callbacks.edit_callback_message_via_runtime",
            new=AsyncMock(),
        ) as edit, patch(
            "bot.handlers.offer_overtime_callbacks._answer",
            new=AsyncMock(),
        ) as answer, patch(
            "bot.handlers.offer_overtime_callbacks.cancel_by_requester",
            new=AsyncMock(),
        ) as local_cancel:
            await offer_overtime_callbacks.handle_overtime_requester_cancel(
                callback=callback,
                user=SimpleNamespace(id=9),
                bot=SimpleNamespace(),
            )

        load.assert_awaited_once_with(
            session,
            ledger.request_public_id,
            for_update=False,
        )
        session.rollback.assert_awaited_once()
        forward.assert_awaited_once_with(
            "iran",
            {
                "request_public_id": ledger.request_public_id,
                "requester_user_id": 9,
                "source_server": "foreign",
            },
        )
        local_cancel.assert_not_awaited()
        edit.assert_awaited_once()
        session.commit.assert_awaited_once()
        answer.assert_awaited_once_with(
            callback,
            M15_REQUESTER_STATUS_CANCELLED,
            alert=False,
        )


if __name__ == "__main__":
    unittest.main()
