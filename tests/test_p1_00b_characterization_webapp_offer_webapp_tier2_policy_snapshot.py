"""Hermetic characterization test for ORIGIN-WEBAPP__WEBAPP__POLICY-SNAPSHOT."""
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
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks
from starlette.requests import Request

import api.routers.trades as trades_module
from api.routers.trades import TradeCreate, create_trade
from core.enums import UserRole
from core.offer_source import OfferSourceSurface, offer_home_server_for_source
from core.server_routing import SERVER_IRAN
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
from models.user import User
from tests.test_p1_00b_characterization_origin_interactions import (
    OriginSession,
    _make_offer,
    _make_owner,
    _request_patches,
    _sqlite_counts,
    _sqlite_requests,
)


class _WebappTier2OriginSession(OriginSession):
    def _is_sqlite_statement(self, statement) -> bool:
        blob = str(statement).lower()
        if "customer_relation" in blob:
            return True
        return super()._is_sqlite_statement(statement)

    def add(self, obj):
        if isinstance(obj, CustomerRelation):
            self._session.add(obj)
            return
        super().add(obj)

    async def execute(self, statement, params=None):
        blob = str(statement).lower()
        if "customer_relation" in blob:
            self._drop_duplicate_graph_inserts()
            with self._session.no_autoflush:
                result = self._session.execute(statement)
            try:
                return result.unique()
            except Exception:
                return result
        return await super().execute(statement, params=params)

    async def scalar(self, statement, params=None):
        blob = str(statement).lower()
        if "customer_relation" in blob:
            res = await self.execute(statement, params=params)
            return res.scalar_one_or_none()
        return await super().scalar(statement, params=params)

    async def scalars(self, statement, params=None):
        blob = str(statement).lower()
        if "customer_relation" in blob:
            res = await self.execute(statement, params=params)
            return res.scalars()
        return await super().scalars(statement, params=params)


class P100bWebappOfferWebappTier2PolicySnapshotCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_webapp_offer_webapp_tier2_customer_snapshots_relation_and_commission(self):
        requester = _make_owner(id=5, role=UserRole.STANDARD, home_server=SERVER_IRAN)
        self.assertEqual(requester.id, 5)
        self.assertEqual(requester.role, UserRole.STANDARD)

        offer = _make_offer("WEBAPP")
        self.assertEqual(offer.id, 7)
        self.assertEqual(offer.price, 123456)
        self.assertEqual(offer.user_id, 9)
        self.assertEqual(offer.user.id, 9)
        self.assertEqual(offer.home_server, SERVER_IRAN)
        self.assertEqual(offer.offer_public_id, "ofr_webapp_7")
        self.assertEqual(
            offer_home_server_for_source(OfferSourceSurface.WEBAPP),
            SERVER_IRAN,
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
            idempotency_key="idemp_webapp_offer_webapp_tier2_snapshot_001",
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
            token="lease-token-webapp-webapp-tier2-1",
            acquired=True,
            _client=mock_redis_client,
            _owns_client=False,
        )
        self.assertTrue(trade_contention_lease.acquired)
        self.assertTrue(trade_contention_lease_was_pre_gated(trade_contention_lease))

        mock_forward = AsyncMock()
        mock_enforce_trade_limits = AsyncMock()

        patches = [
            patch(
                "api.routers.trades.enforce_customer_trade_limits_for_trade",
                new=mock_enforce_trade_limits,
            ),
            patch("core.trade_forwarding.forward_trade_to_home_server", new=mock_forward),
        ] + _request_patches(offer.home_server)[2:]
        if hasattr(trades_module, "forward_trade_to_home_server"):
            patches.append(
                patch("api.routers.trades.forward_trade_to_home_server", new=mock_forward)
            )
        else:
            patches.append(
                patch("api.routers.trades.forward_trade_to_home_server", new=mock_forward, create=True)
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            sqlite_path = str(Path(temp_dir) / "test_webapp_offer_webapp_tier2_snapshot.db")
            engine, Session = _sqlite_requests(sqlite_path)
            original_indexes = set(CustomerRelation.__table__.indexes)
            original_index_identities = {id(idx) for idx in original_indexes}
            original_index_names = sorted(idx.name for idx in original_indexes if idx.name is not None)
            discarded_indexes = []
            try:
                seen_indexes = set()
                for idx in list(CustomerRelation.__table__.indexes):
                    if idx.name in seen_indexes:
                        CustomerRelation.__table__.indexes.discard(idx)
                        discarded_indexes.append(idx)
                    else:
                        seen_indexes.add(idx.name)
                CustomerRelation.__table__.create(engine, checkfirst=True)
            finally:
                for idx in discarded_indexes:
                    CustomerRelation.__table__.indexes.add(idx)

            self.assertEqual(CustomerRelation.__table__.indexes, original_indexes)
            self.assertEqual(
                {id(idx) for idx in CustomerRelation.__table__.indexes},
                original_index_identities,
            )
            self.assertEqual(
                sorted(idx.name for idx in CustomerRelation.__table__.indexes if idx.name is not None),
                original_index_names,
            )
            sync_session = Session()
            origin_session = _WebappTier2OriginSession(sync_session, offer=offer, owner=requester)

            if sync_session.get(User, 9) is None and getattr(offer, "user", None) is not None:
                sync_session.add(offer.user)
                sync_session.commit()
            if sync_session.get(User, 5) is None:
                sync_session.add(requester)
                sync_session.commit()

            sync_session.add(tier2_relation)
            sync_session.commit()

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
                        relation_rows = conn.execute("SELECT * FROM customer_relations").fetchall()

                    self.assertEqual(len(trade_rows), 1)
                    self.assertEqual(len(request_rows), 1)
                    self.assertEqual(len(relation_rows), 1)
                    trade_dict = dict(trade_rows[0])
                    request_dict = dict(request_rows[0])
                    relation_dict = dict(relation_rows[0])

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
                    self.assertEqual(request_dict.get("request_home_server"), SERVER_IRAN)
                    self.assertEqual(request_dict.get("request_source_server"), SERVER_IRAN)
                    self.assertEqual(request_dict.get("local_offer_id"), 7)
                    self.assertEqual(request_dict.get("offer_public_id"), "ofr_webapp_7")
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

                    self.assertEqual(relation_dict.get("id"), 44)
                    self.assertEqual(relation_dict.get("owner_user_id"), 9)
                    self.assertEqual(relation_dict.get("customer_user_id"), 5)
                    self.assertEqual(
                        str(relation_dict.get("status")).lower(),
                        CustomerRelationStatus.ACTIVE.value,
                    )
                    self.assertIsNone(relation_dict.get("deleted_at"))

                    persisted_relation = sync_session.get(CustomerRelation, 44)
                    self.assertIsNotNone(persisted_relation)
                    self.assertEqual(persisted_relation.id, 44)
                    self.assertEqual(persisted_relation.owner_user_id, 9)
                    self.assertEqual(persisted_relation.customer_user_id, 5)
                    self.assertEqual(persisted_relation.status, CustomerRelationStatus.ACTIVE)
                    self.assertIsNone(persisted_relation.deleted_at)

                    mock_forward.assert_not_awaited()
                    self.assertEqual(mock_forward.await_count, 0)

                    mock_redis_client.eval.assert_awaited_once()
                    self.assertEqual(mock_redis_client.eval.await_count, 1)
                    self.assertFalse(trade_contention_lease.acquired)
                    self.assertFalse(trade_contention_lease_was_pre_gated(trade_contention_lease))

                    mock_enforce_trade_limits.assert_awaited_once()
                    self.assertEqual(mock_enforce_trade_limits.await_count, 1)
                    enforce_call = mock_enforce_trade_limits.call_args
                    enforce_session = enforce_call.args[0] if enforce_call.args else enforce_call.kwargs.get("db")
                    self.assertIs(enforce_session, origin_session)
                    enforce_relations = enforce_call.kwargs.get("customer_relations")
                    self.assertIsNotNone(enforce_relations)
                    self.assertIn(5, enforce_relations)
                    self.assertIs(enforce_relations[5], tier2_relation)
                    self.assertIn(9, enforce_relations)
                    self.assertIsNone(enforce_relations[9])
                    self.assertEqual(int(enforce_call.kwargs.get("quantity")), 4)
                    self.assertEqual(enforce_call.kwargs.get("timezone_name"), "Asia/Tehran")
            finally:
                origin_session.close()
                engine.dispose()
                self.assertEqual(CustomerRelation.__table__.indexes, original_indexes)
                self.assertEqual(
                    {id(idx) for idx in CustomerRelation.__table__.indexes},
                    original_index_identities,
                )


if __name__ == "__main__":
    unittest.main()
