"""Hermetic characterization test for ORIGIN-TELEGRAM_BOT__WEBAPP__POLICY-SNAPSHOT."""
from __future__ import annotations

import tempfile
from pathlib import Path

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from decimal import Decimal
import json
import sqlite3
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks, HTTPException
from starlette.requests import Request

import api.routers.trades as trades_module
from api.routers.trades import TradeCreate, create_trade
from core.enums import UserRole
from core.offer_source import OfferSourceSurface, offer_home_server_for_source
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN
from core.services.accountant_relation_service import EffectiveOwnerActor
from core.services.trade_contention_gate import (
    TradeContentionLease,
    trade_contention_lease_was_pre_gated,
)
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
from models.offer_request import (
    OfferRequestSourceSurface,
    OfferRequestStatus,
    OfferRequestWorkflow,
)
from models.trade import TradeStatus
from tests.test_p1_00b_characterization_origin_interactions import (
    OriginSession,
    _make_offer,
    _make_owner,
    _request_patches,
    _sqlite_counts,
    _sqlite_requests,
)


class P100bTelegramOfferWebappPolicySnapshotCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_telegram_offer_webapp_tier2_customer_snapshots_relation_and_commission(self):
        requester = _make_owner(id=5, role=UserRole.STANDARD, home_server=SERVER_IRAN)
        self.assertEqual(requester.id, 5)
        self.assertEqual(requester.role, UserRole.STANDARD)

        offer = _make_offer("TELEGRAM_BOT")
        self.assertEqual(offer.id, 7)
        self.assertEqual(offer.price, 123456)
        self.assertEqual(offer.user_id, 9)
        self.assertEqual(offer.user.id, 9)
        self.assertEqual(offer.home_server, SERVER_FOREIGN)
        self.assertEqual(offer.offer_public_id, "ofr_telegram_bot_7")
        self.assertEqual(
            offer_home_server_for_source(OfferSourceSurface.TELEGRAM_BOT),
            SERVER_FOREIGN,
        )

        tier2_relation = CustomerRelation(
            id=44,
            owner_user_id=9,
            customer_user_id=5,
            invitation_token="tok_tier2_fixture_relation_44",
            management_name="desk",
            customer_tier=CustomerTier.TIER_2,
            commission_rate=Decimal("1.5"),
            min_trade_quantity=1,
            max_trade_quantity=10,
            max_daily_trades=8,
            max_daily_commodity_volume=20,
            status=CustomerRelationStatus.ACTIVE,
        )

        context = EffectiveOwnerActor(
            owner_user=requester,
            actor_user=requester,
            relation=None,
            is_accountant_context=False,
        )

        trade_amount = 4
        trade_data = TradeCreate(
            offer_id=offer.id,
            offer_public_id=offer.offer_public_id,
            quantity=trade_amount,
            idempotency_key="idemp_telegram_bot_offer_webapp_tier2_snapshot_001",
        )

        background_tasks = BackgroundTasks()
        raw_request = Request(
            scope={
                "type": "http",
                "method": "POST",
                "path": "/trades",
                "headers": [],
                "client": ("127.0.0.1", 12345),
                "server": ("testserver", 80),
            }
        )

        mock_redis_client = AsyncMock()
        trade_contention_lease = TradeContentionLease(
            key=f"trade_contention:offer:{offer.id}",
            token="lease-token-telegram-webapp-tier2-1",
            acquired=True,
            _client=mock_redis_client,
            _owns_client=False,
        )
        self.assertTrue(trade_contention_lease.acquired)
        self.assertTrue(trade_contention_lease_was_pre_gated(trade_contention_lease))

        mock_forward = AsyncMock()
        base_patches = [
            p
            for p in _request_patches(offer.home_server)
            if getattr(p, "target", "")
            != "api.routers.trades.get_active_customer_relation_for_customer"
        ]

        async def _mock_get_active_customer_relation(*args, **kwargs):
            user_id = kwargs.get("customer_user_id", kwargs.get("customer_id", kwargs.get("user_id")))
            if user_id is None:
                for arg in args:
                    if isinstance(arg, int):
                        user_id = arg
                        break
            if user_id == 5:
                return tier2_relation
            if user_id == 9:
                return None
            return None

        patches = base_patches + [
            patch(
                "api.routers.trades.get_active_customer_relation_for_customer",
                new=AsyncMock(side_effect=_mock_get_active_customer_relation),
            ),
            patch("core.trade_forwarding.forward_trade_to_home_server", new=mock_forward),
        ]
        if hasattr(trades_module, "forward_trade_to_home_server"):
            patches.append(
                patch("api.routers.trades.forward_trade_to_home_server", new=mock_forward)
            )
        else:
            patches.append(
                patch("api.routers.trades.forward_trade_to_home_server", new=mock_forward, create=True)
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            sqlite_path = str(Path(temp_dir) / "test_telegram_offer_webapp_tier2_snapshot.db")
            engine, Session = _sqlite_requests(sqlite_path)
            sync_session = Session()
            origin_session = OriginSession(sync_session, offer=offer, owner=requester)

            try:
                with ExitStack() as stack:
                    for p in patches:
                        stack.enter_context(p)

                    counts_before = _sqlite_counts(sqlite_path)
                    self.assertEqual(counts_before["trades"], 0)
                    self.assertEqual(counts_before["offer_requests"], 0)

                    response = await create_trade(
                        trade_data=trade_data,
                        background_tasks=background_tasks,
                        raw_request=raw_request,
                        trade_contention_lease=trade_contention_lease,
                        db=origin_session,
                        context=context,
                    )

                    counts_after = _sqlite_counts(sqlite_path)
                    self.assertEqual(counts_after["trades"], 1)
                    self.assertEqual(counts_after["offer_requests"], 1)
                    self.assertIsNotNone(counts_after["trade_number"])
                    self.assertIsNotNone(counts_after["resulting_trade_id"])

                    with sqlite3.connect(sqlite_path) as conn:
                        conn.row_factory = sqlite3.Row
                        trade_rows = conn.execute("SELECT * FROM trades").fetchall()
                        request_rows = conn.execute("SELECT * FROM offer_requests").fetchall()

                    self.assertEqual(len(trade_rows), 1)
                    self.assertEqual(len(request_rows), 1)
                    trade_dict = dict(trade_rows[0])
                    request_dict = dict(request_rows[0])

                    self.assertIsNotNone(response)
                    self.assertIsInstance(response, dict)
                    self.assertEqual(response.get("status"), TradeStatus.COMPLETED.value)

                    self.assertEqual(
                        str(trade_dict.get("status")).lower(),
                        TradeStatus.COMPLETED.value,
                    )
                    self.assertEqual(trade_dict.get("quantity"), 4)
                    self.assertEqual(trade_dict.get("offer_id"), offer.id)
                    self.assertEqual(trade_dict.get("price"), 125400)

                    self.assertEqual(request_dict.get("resulting_trade_id"), trade_dict.get("id"))
                    self.assertEqual(
                        str(request_dict.get("result_status")).lower(),
                        OfferRequestStatus.COMPLETED_TRADE.value,
                    )
                    self.assertEqual(
                        str(request_dict.get("workflow_kind")).lower(),
                        OfferRequestWorkflow.DIRECT.value,
                    )
                    self.assertEqual(
                        str(request_dict.get("request_source_surface")).lower(),
                        OfferRequestSourceSurface.WEBAPP.value,
                    )
                    self.assertEqual(request_dict.get("request_home_server"), SERVER_FOREIGN)
                    self.assertEqual(request_dict.get("request_source_server"), SERVER_FOREIGN)
                    self.assertEqual(request_dict.get("local_offer_id"), 7)
                    self.assertEqual(request_dict.get("offer_public_id"), "ofr_telegram_bot_7")
                    self.assertEqual(request_dict.get("requester_user_id"), 5)
                    self.assertEqual(request_dict.get("requested_quantity"), 4)

                    self.assertEqual(request_dict.get("customer_relation_id"), 44)
                    self.assertEqual(request_dict.get("customer_owner_user_id"), 9)
                    self.assertEqual(
                        str(request_dict.get("customer_tier_snapshot")).lower(),
                        CustomerTier.TIER_2.value,
                    )
                    self.assertEqual(
                        request_dict.get("customer_management_name_snapshot"),
                        "desk",
                    )
                    self.assertEqual(
                        Decimal(str(request_dict.get("customer_commission_rate_snapshot"))),
                        Decimal("1.5"),
                    )

                    commission_context = request_dict.get("customer_commission_context")
                    if isinstance(commission_context, str):
                        commission_context = json.loads(commission_context)
                    self.assertIsInstance(commission_context, dict)
                    self.assertEqual(commission_context.get("min_trade_quantity"), 1)
                    self.assertEqual(commission_context.get("max_trade_quantity"), 10)
                    self.assertEqual(commission_context.get("max_daily_trades"), 8)
                    self.assertEqual(commission_context.get("max_daily_commodity_volume"), 20)

                    mock_forward.assert_not_awaited()
                    self.assertEqual(mock_forward.await_count, 0)

                    mock_redis_client.eval.assert_awaited_once()
                    self.assertEqual(mock_redis_client.eval.await_count, 1)
                    self.assertFalse(trade_contention_lease.acquired)
                    self.assertFalse(trade_contention_lease_was_pre_gated(trade_contention_lease))
            finally:
                origin_session.close()
                engine.dispose()

    async def test_telegram_offer_webapp_accountant_context_rejected_early_with_403(self):
        owner = _make_owner(id=5, role=UserRole.STANDARD, home_server=SERVER_IRAN)
        accountant_actor = _make_owner(
            id=15,
            role=UserRole.STANDARD,
            account_name="accountant_actor",
            mobile_number="09121111111",
            telegram_id=556,
            home_server=SERVER_IRAN,
        )
        accountant_relation = SimpleNamespace(
            id=88,
            owner_user_id=5,
            accountant_user_id=15,
            status="active",
        )
        context = EffectiveOwnerActor(
            owner_user=owner,
            actor_user=accountant_actor,
            relation=accountant_relation,
            is_accountant_context=True,
        )

        offer = _make_offer("TELEGRAM_BOT")
        self.assertEqual(offer.id, 7)
        self.assertEqual(offer.home_server, SERVER_FOREIGN)

        trade_amount = 4
        trade_data = TradeCreate(
            offer_id=offer.id,
            offer_public_id=offer.offer_public_id,
            quantity=trade_amount,
            idempotency_key="idemp_telegram_bot_offer_webapp_accountant_deny_001",
        )

        background_tasks = BackgroundTasks()
        raw_request = Request(
            scope={
                "type": "http",
                "method": "POST",
                "path": "/trades",
                "headers": [],
                "client": ("127.0.0.1", 12345),
                "server": ("testserver", 80),
            }
        )

        mock_redis_client = AsyncMock()
        trade_contention_lease = TradeContentionLease(
            key=f"trade_contention:offer:{offer.id}",
            token="lease-token-telegram-webapp-accountant-1",
            acquired=True,
            _client=mock_redis_client,
            _owns_client=False,
        )
        self.assertTrue(trade_contention_lease.acquired)
        self.assertTrue(trade_contention_lease_was_pre_gated(trade_contention_lease))

        mock_forward = AsyncMock()
        mock_transient = AsyncMock()
        patches = _request_patches(offer.home_server) + [
            patch("core.trade_forwarding.forward_trade_to_home_server", new=mock_forward),
        ]
        if hasattr(trades_module, "forward_trade_to_home_server"):
            patches.append(
                patch("api.routers.trades.forward_trade_to_home_server", new=mock_forward)
            )
        else:
            patches.append(
                patch("api.routers.trades.forward_trade_to_home_server", new=mock_forward, create=True)
            )
        if hasattr(trades_module, "_execute_trade_authoritatively_with_transient_retry"):
            patches.append(
                patch(
                    "api.routers.trades._execute_trade_authoritatively_with_transient_retry",
                    new=mock_transient,
                )
            )
        else:
            patches.append(
                patch(
                    "api.routers.trades._execute_trade_authoritatively_with_transient_retry",
                    new=mock_transient,
                    create=True,
                )
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            sqlite_path = str(Path(temp_dir) / "test_telegram_offer_webapp_accountant_deny.db")
            engine, Session = _sqlite_requests(sqlite_path)
            sync_session = Session()
            origin_session = OriginSession(sync_session, offer=offer, owner=owner)

            try:
                with ExitStack() as stack:
                    for p in patches:
                        stack.enter_context(p)

                    counts_before = _sqlite_counts(sqlite_path)
                    self.assertEqual(counts_before["trades"], 0)
                    self.assertEqual(counts_before["offer_requests"], 0)

                    with self.assertRaises(HTTPException) as cm:
                        await create_trade(
                            trade_data=trade_data,
                            background_tasks=background_tasks,
                            raw_request=raw_request,
                            trade_contention_lease=trade_contention_lease,
                            db=origin_session,
                            context=context,
                        )

                    self.assertEqual(cm.exception.status_code, 403)
                    self.assertEqual(cm.exception.detail, "حسابدار دسترسی به بازار ندارد.")

                    mock_forward.assert_not_awaited()
                    self.assertEqual(mock_forward.await_count, 0)
                    mock_transient.assert_not_awaited()
                    self.assertEqual(mock_transient.await_count, 0)

                    counts_after = _sqlite_counts(sqlite_path)
                    self.assertEqual(counts_after["trades"], 0)
                    self.assertEqual(counts_after["offer_requests"], 0)

                    mock_redis_client.eval.assert_awaited_once()
                    self.assertEqual(mock_redis_client.eval.await_count, 1)
                    self.assertFalse(trade_contention_lease.acquired)
                    self.assertFalse(trade_contention_lease_was_pre_gated(trade_contention_lease))
            finally:
                origin_session.close()
                engine.dispose()

    async def test_telegram_offer_webapp_super_admin_eligible_without_customer_relation(self):
        admin = _make_owner(id=5, role=UserRole.SUPER_ADMIN, home_server=SERVER_IRAN)
        self.assertEqual(admin.id, 5)
        self.assertEqual(admin.role, UserRole.SUPER_ADMIN)

        offer = _make_offer("TELEGRAM_BOT")
        self.assertEqual(offer.id, 7)
        self.assertEqual(offer.price, 123456)
        self.assertEqual(offer.home_server, SERVER_FOREIGN)
        self.assertEqual(offer.offer_public_id, "ofr_telegram_bot_7")

        context = EffectiveOwnerActor(
            owner_user=admin,
            actor_user=admin,
            relation=None,
            is_accountant_context=False,
        )

        trade_amount = 4
        trade_data = TradeCreate(
            offer_id=offer.id,
            offer_public_id=offer.offer_public_id,
            quantity=trade_amount,
            idempotency_key="idemp_telegram_bot_offer_webapp_super_admin_eligible_001",
        )

        background_tasks = BackgroundTasks()
        raw_request = Request(
            scope={
                "type": "http",
                "method": "POST",
                "path": "/trades",
                "headers": [],
                "client": ("127.0.0.1", 12345),
                "server": ("testserver", 80),
            }
        )

        mock_redis_client = AsyncMock()
        trade_contention_lease = TradeContentionLease(
            key=f"trade_contention:offer:{offer.id}",
            token="lease-token-telegram-webapp-super-admin-1",
            acquired=True,
            _client=mock_redis_client,
            _owns_client=False,
        )
        self.assertTrue(trade_contention_lease.acquired)
        self.assertTrue(trade_contention_lease_was_pre_gated(trade_contention_lease))

        mock_forward = AsyncMock()
        patches = _request_patches(offer.home_server, relation=None) + [
            patch("core.trade_forwarding.forward_trade_to_home_server", new=mock_forward),
        ]
        if hasattr(trades_module, "forward_trade_to_home_server"):
            patches.append(
                patch("api.routers.trades.forward_trade_to_home_server", new=mock_forward)
            )
        else:
            patches.append(
                patch("api.routers.trades.forward_trade_to_home_server", new=mock_forward, create=True)
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            sqlite_path = str(Path(temp_dir) / "test_telegram_offer_webapp_super_admin.db")
            engine, Session = _sqlite_requests(sqlite_path)
            sync_session = Session()
            origin_session = OriginSession(sync_session, offer=offer, owner=admin)

            try:
                with ExitStack() as stack:
                    for p in patches:
                        stack.enter_context(p)

                    counts_before = _sqlite_counts(sqlite_path)
                    self.assertEqual(counts_before["trades"], 0)
                    self.assertEqual(counts_before["offer_requests"], 0)

                    response = await create_trade(
                        trade_data=trade_data,
                        background_tasks=background_tasks,
                        raw_request=raw_request,
                        trade_contention_lease=trade_contention_lease,
                        db=origin_session,
                        context=context,
                    )

                    counts_after = _sqlite_counts(sqlite_path)
                    self.assertEqual(counts_after["trades"], 1)
                    self.assertEqual(counts_after["offer_requests"], 1)
                    self.assertIsNotNone(counts_after["trade_number"])
                    self.assertIsNotNone(counts_after["resulting_trade_id"])

                    with sqlite3.connect(sqlite_path) as conn:
                        conn.row_factory = sqlite3.Row
                        trade_rows = conn.execute("SELECT * FROM trades").fetchall()
                        request_rows = conn.execute("SELECT * FROM offer_requests").fetchall()

                    self.assertEqual(len(trade_rows), 1)
                    self.assertEqual(len(request_rows), 1)
                    trade_dict = dict(trade_rows[0])
                    request_dict = dict(request_rows[0])

                    self.assertIsNotNone(response)
                    self.assertIsInstance(response, dict)
                    self.assertEqual(response.get("status"), TradeStatus.COMPLETED.value)

                    self.assertEqual(
                        str(trade_dict.get("status")).lower(),
                        TradeStatus.COMPLETED.value,
                    )
                    self.assertEqual(trade_dict.get("quantity"), 4)
                    self.assertEqual(trade_dict.get("offer_id"), offer.id)
                    self.assertEqual(trade_dict.get("price"), 123456)

                    self.assertEqual(request_dict.get("resulting_trade_id"), trade_dict.get("id"))
                    self.assertEqual(
                        str(request_dict.get("result_status")).lower(),
                        OfferRequestStatus.COMPLETED_TRADE.value,
                    )
                    self.assertEqual(
                        str(request_dict.get("workflow_kind")).lower(),
                        OfferRequestWorkflow.DIRECT.value,
                    )
                    self.assertEqual(
                        str(request_dict.get("request_source_surface")).lower(),
                        OfferRequestSourceSurface.WEBAPP.value,
                    )
                    self.assertEqual(request_dict.get("request_home_server"), SERVER_FOREIGN)
                    self.assertEqual(request_dict.get("request_source_server"), SERVER_FOREIGN)
                    self.assertEqual(request_dict.get("local_offer_id"), 7)
                    self.assertEqual(request_dict.get("offer_public_id"), "ofr_telegram_bot_7")
                    self.assertEqual(request_dict.get("requester_user_id"), 5)
                    self.assertEqual(request_dict.get("requested_quantity"), 4)

                    self.assertIsNone(request_dict.get("customer_relation_id"))
                    self.assertIsNone(request_dict.get("customer_owner_user_id"))
                    self.assertIsNone(request_dict.get("customer_tier_snapshot"))
                    self.assertIsNone(request_dict.get("customer_management_name_snapshot"))
                    self.assertIsNone(request_dict.get("customer_commission_rate_snapshot"))
                    commission_context = request_dict.get("customer_commission_context")
                    if isinstance(commission_context, str):
                        commission_context = json.loads(commission_context)
                    self.assertIsNone(commission_context)

                    mock_forward.assert_not_awaited()
                    self.assertEqual(mock_forward.await_count, 0)

                    mock_redis_client.eval.assert_awaited_once()
                    self.assertEqual(mock_redis_client.eval.await_count, 1)
                    self.assertFalse(trade_contention_lease.acquired)
                    self.assertFalse(trade_contention_lease_was_pre_gated(trade_contention_lease))
            finally:
                origin_session.close()
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
