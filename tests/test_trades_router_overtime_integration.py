"""Stage 5: approval intake routes into overtime; owner approve re-enters trade."""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from api.routers import trades
from core.offer_lifecycle import OfferRequestIntakePhase
from core.services.offer_overtime_request_service import (
    OvertimeRequestError,
    OvertimeRequestErrorCode,
    OvertimeRequestResult,
)
from models.offer import OfferStatus
from models.offer_request import OfferRequestStatus, OfferRequestWorkflow


CREATED = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
RECEIPT_OVERTIME = CREATED + timedelta(minutes=2, seconds=30)


class OvertimeIntakeRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_is_offer_expired_only_for_rejected_phase(self):
        offer = SimpleNamespace(
            created_at=CREATED,
            overtime_minutes_snapshot=5,
            status=OfferStatus.ACTIVE,
        )
        with patch(
            "core.trading_settings.get_trading_settings_async",
            AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=2)),
        ):
            # Inside overtime → APPROVAL → not a hard reject for the helper.
            expired = await trades._is_offer_expired_for_trade(
                offer,
                edge_received_at=RECEIPT_OVERTIME.replace(tzinfo=None),
            )
            self.assertFalse(expired)

            # Exact final → REJECTED
            expired_final = await trades._is_offer_expired_for_trade(
                offer,
                edge_received_at=(CREATED + timedelta(minutes=7)).replace(tzinfo=None),
            )
            self.assertTrue(expired_final)

    async def test_execute_routes_approval_to_overtime_create(self):
        from api.routers.trades import TradeCreate, _execute_trade_authoritatively
        from tests.test_trades_router_authoritative_guards import (
            FakeDB,
            FakeExecuteResult,
            make_context,
            make_offer,
            make_user,
        )

        locked_user = make_user(id=9)
        offer = make_offer(
            status=OfferStatus.ACTIVE,
            user_id=1,
            overtime_minutes_snapshot=5,
            created_at=CREATED.replace(tzinfo=None),
        )
        offer.offer_public_id = "ofr_ot_route"
        db = FakeDB(
            execute_results=[FakeExecuteResult(single=locked_user)],
            get_results=[offer],
        )
        ledger = SimpleNamespace(
            request_public_id="req_ot_1",
            offer_public_id="ofr_ot_route",
            result_status=OfferRequestStatus.OVERTIME_PRESENTED,
            requested_quantity=4,
            presented_at=RECEIPT_OVERTIME,
            decision_deadline_at=RECEIPT_OVERTIME + timedelta(seconds=30),
            workflow_kind=OfferRequestWorkflow.OVERTIME,
        )
        create_mock = AsyncMock(
            return_value=OvertimeRequestResult(ledger=ledger, duplicate_replay=False, promoted=True)
        )

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades.evaluate_current_market_schedule",
            AsyncMock(return_value=SimpleNamespace(is_open=True, timezone="Asia/Tehran")),
        ), patch(
            "api.routers.trades._lock_trade_idempotency_key",
            AsyncMock(return_value=True),
        ), patch(
            "api.routers.trades.load_offer_request_by_idempotency",
            AsyncMock(return_value=None),
        ), patch(
            "api.routers.trades._try_lock_trade_offer_execution",
            AsyncMock(return_value=True),
        ), patch(
            "api.routers.trades._classify_trade_request_intake",
            AsyncMock(return_value=(OfferRequestIntakePhase.APPROVAL, RECEIPT_OVERTIME.replace(tzinfo=None))),
        ), patch(
            "api.routers.trades.create_overtime_request",
            create_mock,
        ), patch(
            "api.routers.trades.get_active_customer_relation_for_customer",
            AsyncMock(return_value=None),
        ), patch(
            "core.trading_settings.get_trading_settings_async",
            AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=2)),
        ):
            response = await _execute_trade_authoritatively(
                TradeCreate(offer_id=7, quantity=4, idempotency_key="ot-route-1"),
                background_tasks=SimpleNamespace(add_task=lambda *a, **k: None),
                db=db,
                context=make_context(locked_user),
                edge_received_at=RECEIPT_OVERTIME.replace(tzinfo=None),
            )

        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.status_code, 202)
        import json

        payload = json.loads(response.body.decode())
        self.assertEqual(payload["workflow"], "overtime")
        self.assertEqual(payload["result_status"], OfferRequestStatus.OVERTIME_PRESENTED.value)
        self.assertTrue(payload["promoted"])
        create_mock.assert_awaited_once()
        db.commit.assert_awaited()

    async def test_approval_requires_idempotency_key(self):
        from api.routers.trades import TradeCreate, _execute_trade_authoritatively
        from tests.test_trades_router_authoritative_guards import (
            FakeDB,
            FakeExecuteResult,
            make_context,
            make_offer,
            make_user,
        )

        locked_user = make_user(id=9)
        offer = make_offer(status=OfferStatus.ACTIVE, user_id=1)
        db = FakeDB(
            execute_results=[FakeExecuteResult(single=locked_user)],
            get_results=[offer],
        )
        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades.evaluate_current_market_schedule",
            AsyncMock(return_value=SimpleNamespace(is_open=True)),
        ), patch(
            "api.routers.trades._try_lock_trade_offer_execution",
            AsyncMock(return_value=True),
        ), patch(
            "api.routers.trades._classify_trade_request_intake",
            AsyncMock(return_value=(OfferRequestIntakePhase.APPROVAL, RECEIPT_OVERTIME.replace(tzinfo=None))),
        ), patch(
            "core.trading_settings.get_trading_settings_async",
            AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=2)),
        ):
            with self.assertRaises(HTTPException) as caught:
                await _execute_trade_authoritatively(
                    TradeCreate(offer_id=7, quantity=4),
                    background_tasks=SimpleNamespace(add_task=lambda *a, **k: None),
                    db=db,
                    context=make_context(locked_user),
                    edge_received_at=RECEIPT_OVERTIME.replace(tzinfo=None),
                )
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(
            caught.exception.detail["error_code"],
            OvertimeRequestErrorCode.IDEMPOTENCY_REQUIRED.value,
        )


class OvertimePayloadHelperTests(unittest.TestCase):
    def test_overtime_error_mapping(self):
        with self.assertRaises(HTTPException) as caught:
            trades._raise_overtime_request_http_error(
                OvertimeRequestError(
                    OvertimeRequestErrorCode.SAME_OFFER_BUSY,
                    "busy",
                    remaining_seconds=12,
                )
            )
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["remaining_seconds"], 12)


if __name__ == "__main__":
    unittest.main()
