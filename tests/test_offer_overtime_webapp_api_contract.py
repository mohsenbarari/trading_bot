"""Stage 10: WebApp HTTP/WebSocket contract for offer overtime."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

from fastapi import HTTPException

from api.routers import offers as offers_router
from api.routers import trades
from api.routers.realtime import project_public_event_payload
from core.offer_request_policy import (
    OfferRequestVisibility,
    allowed_offer_request_fields,
    sanitize_offer_request_payload,
)
from core.services.offer_overtime_request_service import (
    OvertimeRequestError,
    OvertimeRequestErrorCode,
)
from models.offer import OfferStatus
from models.offer_request import OfferRequestStatus, OfferRequestWorkflow
from tests.test_trades_router_authoritative_guards import make_context, make_user


CREATED = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
PRESENTED = CREATED + timedelta(minutes=2, seconds=30)
DEADLINE = PRESENTED + timedelta(seconds=30)


def _presented_ledger(**overrides):
    data = {
        "id": 99,
        "request_public_id": "req_ot_web_1",
        "offer_public_id": "ofr_ot_web_1",
        "local_offer_id": 7,
        "request_home_server": "iran",
        "workflow_kind": OfferRequestWorkflow.OVERTIME,
        "result_status": OfferRequestStatus.OVERTIME_PRESENTED,
        "requested_quantity": 4,
        "presented_at": PRESENTED.replace(tzinfo=None),
        "decision_deadline_at": DEADLINE.replace(tzinfo=None),
        "offer_owner_user_id": 1,
        "requester_user_id": 9,
        "actor_user_id": 9,
        "idempotency_key": "idem-ot-1",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class LifecycleResponseContractTests(unittest.TestCase):
    def test_active_lifecycle_exposes_interaction_flags(self):
        # Normal lifetime 2m, snapshot 5m; wall clock 3m after create → overtime.
        created_at = datetime.utcnow() - timedelta(minutes=3)
        offer = SimpleNamespace(
            id=1,
            status=OfferStatus.ACTIVE,
            created_at=created_at,
            overtime_minutes_snapshot=5,
            overtime_trade_committed=False,
        )
        fields = offers_router._offer_lifecycle_response_fields(
            offer,
            start_settings=SimpleNamespace(offer_expiry_minutes=2),
        )
        self.assertEqual(fields["lifecycle_phase"], "overtime")
        self.assertFalse(fields["accepts_automatic_trade"])
        self.assertTrue(fields["accepts_overtime_request"])
        self.assertTrue(fields["accepts_new_public_interaction"])
        self.assertFalse(fields["overtime_trade_committed"])
        self.assertIsInstance(fields["expires_at_ts"], int)

    def test_terminal_lifecycle_keeps_committed_marker_and_false_flags(self):
        offer = SimpleNamespace(
            id=2,
            status=OfferStatus.COMPLETED,
            overtime_minutes_snapshot=3,
            overtime_trade_committed=True,
        )
        fields = offers_router._offer_lifecycle_response_fields(offer)
        self.assertTrue(fields["overtime_trade_committed"])
        self.assertEqual(fields["overtime_minutes_snapshot"], 3)
        self.assertFalse(fields["accepts_overtime_request"])
        self.assertFalse(fields["accepts_automatic_trade"])
        self.assertIsNone(fields["lifecycle_phase"])

    def test_offer_response_models_accept_optional_lifecycle_defaults(self):
        """Old clients ignore unknown keys; new optional fields have safe defaults."""
        private = offers_router.OfferResponse.model_validate(
            {
                "id": 1,
                "offer_public_id": "ofr_x",
                "public_link": "/market?offer=ofr_x",
                "user_id": None,
                "user_account_name": "",
                "is_own_offer": False,
                "offer_type": "sell",
                "settlement_type": "cash",
                "commodity_id": 1,
                "commodity_name": "gold",
                "quantity": 10,
                "remaining_quantity": 10,
                "price": 1,
                "raw_price": 1,
                "market_published_price": 1,
                "viewer_effective_price": 1,
                "is_wholesale": True,
                "lot_sizes": None,
                "original_lot_sizes": None,
                "notes": None,
                "status": "active",
                "channel_message_id": None,
                "created_at": "1405/01/01",
            }
        )
        self.assertFalse(private.overtime_trade_committed)
        self.assertIsNone(private.accepts_overtime_request)
        public = offers_router.PublicOfferResponse.model_validate(
            {
                "offer_public_id": "ofr_x",
                "public_link": "/market?offer=ofr_x",
                "status": "active",
                "offer_type": "sell",
                "settlement_type": "cash",
                "commodity_name": "gold",
                "quantity": 10,
                "remaining_quantity": 10,
                "price": 1,
                "is_wholesale": True,
                "lot_sizes": None,
                "notes": None,
                "created_at": "1405/01/01",
                "safe_public_state_label": "فعال",
                "interaction_available": True,
            }
        )
        self.assertFalse(public.overtime_trade_committed)


class RealtimeAllowlistContractTests(unittest.TestCase):
    def test_created_and_updated_allow_lifecycle_flags(self):
        created = project_public_event_payload(
            "offer:created",
            {
                "id": 1,
                "offer_public_id": "ofr_1",
                "lifecycle_phase": "overtime",
                "accepts_overtime_request": True,
                "accepts_automatic_trade": False,
                "overtime_trade_committed": False,
                "requester_user_id": 99,
                "idempotency_key": "secret",
            },
        )
        self.assertEqual(created["lifecycle_phase"], "overtime")
        self.assertTrue(created["accepts_overtime_request"])
        self.assertNotIn("requester_user_id", created)
        self.assertNotIn("idempotency_key", created)

        updated = project_public_event_payload(
            "offer:updated",
            {
                "id": 1,
                "status": "active",
                "accepts_overtime_request": True,
                "overtime_trade_committed": False,
                "mobile_number": "0912",
            },
        )
        self.assertTrue(updated["accepts_overtime_request"])
        self.assertNotIn("mobile_number", updated)

    def test_terminal_events_allow_committed_marker(self):
        expired = project_public_event_payload(
            "offer:expired",
            {
                "id": 3,
                "status": "expired",
                "overtime_trade_committed": True,
                "lifecycle_phase": None,
                "overtime_minutes_snapshot": 4,
                "requester_user_id": 9,
            },
        )
        self.assertTrue(expired["overtime_trade_committed"])
        self.assertEqual(expired["overtime_minutes_snapshot"], 4)
        self.assertNotIn("requester_user_id", expired)


class OvertimePublicPayloadTests(unittest.TestCase):
    def test_payload_exposes_countdown_without_requester_identity(self):
        now = DEADLINE.replace(tzinfo=None) - timedelta(seconds=12)
        payload = trades._overtime_request_public_payload(
            _presented_ledger(),
            viewer_role="owner",
            now=now,
        )
        self.assertEqual(payload["workflow"], "overtime")
        self.assertEqual(payload["request_public_id"], "req_ot_web_1")
        self.assertEqual(payload["remaining_decision_seconds"], 12)
        self.assertTrue(payload["is_occupying"])
        self.assertTrue(payload["is_actionable"])
        self.assertEqual(payload["viewer_role"], "owner")
        self.assertNotIn("requester_user_id", payload)
        self.assertNotIn("id", payload)
        self.assertNotIn("idempotency_key", payload)


class OfferRequestPolicyVisibilityTests(unittest.TestCase):
    def test_public_visibility_keeps_opaque_request_id_without_owner_only_fields(self):
        allowed = allowed_offer_request_fields(OfferRequestVisibility.PUBLIC_LINK)
        self.assertIn("request_public_id", allowed)
        self.assertIn("workflow_kind", allowed)
        self.assertIn("presented_at", allowed)
        self.assertNotIn("idempotency_key", allowed)
        projected = sanitize_offer_request_payload(
            {
                "request_public_id": "req_x",
                "workflow_kind": "overtime",
                "requester_user_id": 9,
                "idempotency_key": "secret",
                "result_status": "overtime_presented",
            },
            OfferRequestVisibility.PUBLIC_LINK,
        )
        self.assertEqual(projected["request_public_id"], "req_x")
        self.assertNotIn("requester_user_id", projected)
        self.assertNotIn("idempotency_key", projected)


class OvertimeReconnectEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_owner_returns_current_occupying_request(self):
        occupying = _presented_ledger()
        queued = _presented_ledger(
            request_public_id="req_ot_web_2",
            result_status=OfferRequestStatus.OVERTIME_QUEUED,
            presented_at=None,
            decision_deadline_at=None,
        )
        with patch(
            "api.routers.trades.list_nonterminal_overtime_requests",
            new=AsyncMock(return_value=[occupying, queued]),
        ), patch("api.routers.trades.current_server", return_value="iran"):
            body = await trades.get_pending_owner_overtime_requests(
                db=SimpleNamespace(),
                context=make_context(make_user(id=1)),
            )
        self.assertEqual(body["viewer_role"], "owner")
        self.assertEqual(body["current"]["request_public_id"], "req_ot_web_1")
        self.assertEqual(len(body["items"]), 2)
        self.assertNotIn("requester_user_id", body["current"])

    async def test_pending_requester_lists_own_rows(self):
        row = _presented_ledger()
        with patch(
            "api.routers.trades.list_nonterminal_overtime_requests",
            new=AsyncMock(return_value=[row]),
        ) as list_requests, patch("api.routers.trades.current_server", return_value="iran"):
            body = await trades.get_pending_requester_overtime_requests(
                db=SimpleNamespace(),
                context=make_context(make_user(id=9)),
            )
        self.assertEqual(body["viewer_role"], "requester")
        self.assertEqual(body["items"][0]["request_public_id"], "req_ot_web_1")
        list_requests.assert_awaited_once_with(
            ANY,
            requester_user_id=9,
            request_source_server="iran",
        )

    async def test_pending_requester_recovers_remote_home_request_created_on_web(self):
        row = _presented_ledger(
            request_home_server="foreign",
            request_source_server="iran",
        )
        with patch(
            "api.routers.trades.list_nonterminal_overtime_requests",
            new=AsyncMock(return_value=[row]),
        ) as list_requests, patch("api.routers.trades.current_server", return_value="iran"):
            body = await trades.get_pending_requester_overtime_requests(
                db=SimpleNamespace(),
                context=make_context(make_user(id=9)),
            )

        self.assertEqual(body["items"][0]["request_home_server"], "foreign")
        list_requests.assert_awaited_once_with(
            ANY,
            requester_user_id=9,
            request_source_server="iran",
        )
        self.assertNotIn("request_home_server", list_requests.await_args.kwargs)

    async def test_get_one_forbids_unrelated_user(self):
        with patch(
            "api.routers.trades.load_overtime_request_by_public_id",
            new=AsyncMock(return_value=_presented_ledger()),
        ):
            with self.assertRaises(HTTPException) as caught:
                await trades.get_overtime_request(
                    request_public_id="req_ot_web_1",
                    db=SimpleNamespace(),
                    context=make_context(make_user(id=77)),
                )
        self.assertEqual(caught.exception.status_code, 403)

    async def test_get_one_allows_owner_and_requester(self):
        ledger = _presented_ledger()
        with patch(
            "api.routers.trades.load_overtime_request_by_public_id",
            new=AsyncMock(return_value=ledger),
        ), patch("api.routers.trades.current_server", return_value="iran"):
            owner_body = await trades.get_overtime_request(
                request_public_id="req_ot_web_1",
                db=SimpleNamespace(),
                context=make_context(make_user(id=1)),
            )
            requester_body = await trades.get_overtime_request(
                request_public_id="req_ot_web_1",
                db=SimpleNamespace(),
                context=make_context(make_user(id=9)),
            )
        self.assertEqual(owner_body["viewer_role"], "owner")
        self.assertEqual(requester_body["viewer_role"], "requester")
        self.assertTrue(owner_body["is_local_home"])


class OvertimeDecisionAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_approve_rejects_non_owner(self):
        ledger = _presented_ledger()
        with patch(
            "api.routers.trades.load_overtime_request_by_public_id",
            new=AsyncMock(return_value=ledger),
        ), patch("api.routers.trades.current_server", return_value="iran"), patch(
            "api.routers.trades.claim_owner_approval",
            new=AsyncMock(
                side_effect=OvertimeRequestError(
                    OvertimeRequestErrorCode.NOT_OWNER,
                    "فقط صاحب این لفظ می‌تواند درباره این درخواست تصمیم بگیرد.",
                )
            ),
        ):
            with self.assertRaises(HTTPException) as caught:
                await trades.approve_overtime_request(
                    request_public_id="req_ot_web_1",
                    background_tasks=SimpleNamespace(),
                    db=SimpleNamespace(get=AsyncMock()),
                    context=make_context(make_user(id=77)),
                )
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.detail["error_code"], "not_owner")

    async def test_reject_rejects_non_owner(self):
        ledger = _presented_ledger()
        with patch(
            "api.routers.trades.load_overtime_request_by_public_id",
            new=AsyncMock(return_value=ledger),
        ), patch("api.routers.trades.current_server", return_value="iran"), patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=2)),
        ), patch(
            "api.routers.trades.reject_by_owner",
            new=AsyncMock(
                side_effect=OvertimeRequestError(
                    OvertimeRequestErrorCode.NOT_OWNER,
                    "فقط صاحب این لفظ می‌تواند درباره این درخواست تصمیم بگیرد.",
                )
            ),
        ):
            with self.assertRaises(HTTPException) as caught:
                await trades.reject_overtime_request(
                    request_public_id="req_ot_web_1",
                    db=SimpleNamespace(),
                    context=make_context(make_user(id=77)),
                )
        self.assertEqual(caught.exception.status_code, 403)

    async def test_cancel_rejects_non_requester(self):
        ledger = _presented_ledger()
        with patch(
            "api.routers.trades.load_overtime_request_by_public_id",
            new=AsyncMock(return_value=ledger),
        ), patch("api.routers.trades.current_server", return_value="iran"), patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=2)),
        ), patch(
            "api.routers.trades.cancel_by_requester",
            new=AsyncMock(
                side_effect=OvertimeRequestError(
                    OvertimeRequestErrorCode.NOT_REQUESTER,
                    "فقط درخواست‌دهنده می‌تواند این درخواست را لغو کند.",
                )
            ),
        ):
            with self.assertRaises(HTTPException) as caught:
                await trades.cancel_overtime_request(
                    request_public_id="req_ot_web_1",
                    db=SimpleNamespace(),
                    context=make_context(make_user(id=1)),
                )
        self.assertEqual(caught.exception.status_code, 403)

    async def test_sequential_primary_key_does_not_load_by_id(self):
        """Routes accept opaque public ids; loaders never query by sequential pk."""
        with patch(
            "api.routers.trades.load_overtime_request_by_public_id",
            new=AsyncMock(return_value=None),
        ) as load_mock:
            with self.assertRaises(HTTPException) as caught:
                await trades.get_overtime_request(
                    request_public_id="99",
                    db=SimpleNamespace(),
                    context=make_context(make_user(id=1)),
                )
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(load_mock.await_args.args[1], "99")


class OpaqueIdLookupContractTests(unittest.TestCase):
    def test_load_helper_filters_on_request_public_id_column(self):
        import inspect

        from core.services import offer_overtime_request_service as service

        source = inspect.getsource(service.load_overtime_request_by_public_id)
        self.assertIn("request_public_id", source)
        self.assertNotIn("OfferRequest.id ==", source)


if __name__ == "__main__":
    unittest.main()
