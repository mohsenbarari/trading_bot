from __future__ import annotations

import tempfile
from pathlib import Path

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import inspect
import sqlite3
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks
from starlette.requests import Request

import api.routers.trades as trades_module
from api.routers.trades import TradeCreate, create_trade
from core.enums import UserRole
from core.server_routing import SERVER_IRAN
from core.services.accountant_relation_service import EffectiveOwnerActor
from core.services.trade_contention_gate import (
    TradeContentionLease,
    trade_contention_lease_was_pre_gated,
)
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


class P100bWebappOfferWebappTradeSuccessCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_webapp_offer_webapp_create_trade_persists_combined_path_once(self):
        owner = _make_owner()
        self.assertEqual(owner.role, UserRole.STANDARD)
        self.assertEqual(owner.home_server, SERVER_IRAN)

        offer = _make_offer("WEBAPP")
        self.assertEqual(offer.home_server, SERVER_IRAN)
        self.assertTrue(offer.offer_public_id.startswith("ofr_webapp_"))

        trade_amount = 4
        trade_data = TradeCreate(
            offer_id=offer.id,
            offer_public_id=offer.offer_public_id,
            quantity=trade_amount,
            idempotency_key="idemp_webapp_trade_success_001",
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

        context = EffectiveOwnerActor(
            owner_user=owner,
            actor_user=owner,
            relation=None,
            is_accountant_context=False,
        )

        mock_redis_client = AsyncMock()
        trade_contention_lease = TradeContentionLease(
            key=f"trade_contention:offer:{offer.id}",
            token="lease-token-webapp-1",
            acquired=True,
            _client=mock_redis_client,
            _owns_client=False,
        )
        self.assertTrue(trade_contention_lease.acquired)
        self.assertTrue(trade_contention_lease_was_pre_gated(trade_contention_lease))

        remote_forward_targets: list[str] = []
        for candidate in (
            "_forward_trade_to_remote_home",
            "forward_trade_to_remote_home",
            "_forward_trade_to_remote",
            "forward_trade_to_remote",
            "_forward_to_remote_home",
            "forward_to_remote_home",
            "_forward_remote_trade",
            "forward_remote_trade",
            "_forward_trade",
            "forward_trade",
            "_forward_trade_request",
            "forward_trade_request",
            "_forward_request_to_remote_home",
            "forward_request_to_remote_home",
        ):
            if hasattr(trades_module, candidate):
                remote_forward_targets.append(candidate)
                break

        if not remote_forward_targets:
            remote_forward_targets = [
                name
                for name, val in vars(trades_module).items()
                if inspect.iscoroutinefunction(val)
                and name != "_forward_trade_if_remote_home"
                and "forward" in name.lower()
            ]

        if not remote_forward_targets:
            remote_forward_targets = [
                name
                for name, val in vars(trades_module).items()
                if inspect.iscoroutinefunction(val)
                and name != "_forward_trade_if_remote_home"
                and "remote" in name.lower()
                and "trade" in name.lower()
            ]

        self.assertGreaterEqual(
            len(remote_forward_targets),
            1,
            "Expected at least one real remote-forward boundary in api.routers.trades",
        )

        remote_forward_mocks = {
            name: AsyncMock() for name in remote_forward_targets
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            sqlite_path = str(Path(temp_dir) / "test_webapp_trade_success.db")
            engine, Session = _sqlite_requests(sqlite_path)
            sync_session = Session()
            origin_session = OriginSession(sync_session, offer=offer, owner=owner)

            patches = _request_patches(SERVER_IRAN) + [
                patch.object(trades_module, name, new=mock_fn)
                for name, mock_fn in remote_forward_mocks.items()
            ]

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
                    response_id = response.get("id")
                    response_trade_number = response.get("trade_number")
                    response_status = response.get("status")

                    self.assertIsNotNone(response_id)
                    self.assertIsNotNone(response_trade_number)
                    self.assertEqual(response_id, trade_dict.get("id"))
                    self.assertEqual(response_trade_number, trade_dict.get("trade_number"))
                    self.assertEqual(response_status, TradeStatus.COMPLETED.value)

                    self.assertIn(
                        trade_dict.get("status"),
                        (TradeStatus.COMPLETED.value, TradeStatus.COMPLETED.name),
                    )
                    self.assertEqual(
                        str(trade_dict.get("status")).lower(),
                        TradeStatus.COMPLETED.value,
                    )
                    self.assertEqual(trade_dict.get("quantity"), trade_amount)
                    self.assertEqual(trade_dict.get("offer_id"), offer.id)

                    self.assertIsNotNone(trade_dict.get("trade_number"))
                    self.assertIsNotNone(request_dict.get("resulting_trade_id"))
                    self.assertEqual(request_dict.get("resulting_trade_id"), trade_dict.get("id"))
                    self.assertEqual(counts_after["resulting_trade_id"], trade_dict.get("id"))
                    self.assertEqual(counts_after["trade_number"], trade_dict.get("trade_number"))

                    self.assertIn(
                        request_dict.get("result_status"),
                        (
                            OfferRequestStatus.COMPLETED_TRADE.value,
                            OfferRequestStatus.COMPLETED_TRADE.name,
                        ),
                    )
                    self.assertEqual(
                        str(request_dict.get("result_status")).lower(),
                        OfferRequestStatus.COMPLETED_TRADE.value,
                    )
                    self.assertIn(
                        request_dict.get("workflow_kind"),
                        (
                            OfferRequestWorkflow.DIRECT.value,
                            OfferRequestWorkflow.DIRECT.name,
                        ),
                    )
                    self.assertEqual(
                        str(request_dict.get("workflow_kind")).lower(),
                        OfferRequestWorkflow.DIRECT.value,
                    )

                    self.assertIn(
                        request_dict.get("request_source_surface"),
                        (
                            OfferRequestSourceSurface.WEBAPP.value,
                            OfferRequestSourceSurface.WEBAPP.name,
                        ),
                    )
                    self.assertEqual(
                        str(request_dict.get("request_source_surface")).lower(),
                        OfferRequestSourceSurface.WEBAPP.value,
                    )
                    self.assertEqual(request_dict.get("request_home_server"), SERVER_IRAN)
                    self.assertEqual(request_dict.get("request_source_server"), SERVER_IRAN)

                    self.assertEqual(request_dict.get("offer_public_id"), offer.offer_public_id)
                    self.assertEqual(request_dict.get("local_offer_id"), offer.id)
                    self.assertEqual(offer.home_server, SERVER_IRAN)
                    self.assertEqual(offer.offer_public_id, "ofr_webapp_7")

                    for mock_fn in remote_forward_mocks.values():
                        mock_fn.assert_not_awaited()
                        self.assertEqual(mock_fn.await_count, 0)

                    mock_redis_client.eval.assert_awaited_once()
                    self.assertEqual(mock_redis_client.eval.await_count, 1)
                    self.assertFalse(trade_contention_lease.acquired)
                    self.assertFalse(trade_contention_lease_was_pre_gated(trade_contention_lease))
            finally:
                origin_session.close()
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
