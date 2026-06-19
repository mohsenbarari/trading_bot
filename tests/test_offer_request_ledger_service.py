import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.services.offer_request_ledger_service import (
    OfferRequestLedgerCommand,
    OfferRequestTerminalStateError,
    apply_offer_request_decision,
    build_offer_request_history_query,
    create_offer_request_ledger_entry,
    customer_relation_snapshot,
)
from models.customer_relation import CustomerTier
from models.offer_request import OfferRequestStatus


class _Result:
    def __init__(self, value=None):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDB:
    def __init__(self, existing=None):
        self.existing = existing
        self.added = []
        self.execute = AsyncMock(return_value=_Result(existing))
        self.flush = AsyncMock()

    def add(self, obj):
        self.added.append(obj)


class OfferRequestLedgerServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_webapp_and_bot_requests_record_source_metadata(self):
        db = _FakeDB()
        received_at = datetime(2026, 6, 19, 10, 0, tzinfo=timezone.utc)

        with patch("core.services.offer_request_ledger_service.current_server", return_value="iran"):
            web_result = await create_offer_request_ledger_entry(
                db,
                OfferRequestLedgerCommand(
                    request_home_server="iran",
                    local_offer_id=7,
                    offer_public_id="ofr_web",
                    requester_user_id=5,
                    actor_user_id=5,
                    request_source_surface="webapp",
                    request_source_server="iran",
                    requested_quantity=20,
                    idempotency_key="web:1",
                    received_at=received_at,
                ),
            )

        self.assertFalse(web_result.duplicate_replay)
        self.assertEqual(web_result.ledger.offer_public_id, "ofr_web")
        self.assertEqual(web_result.ledger.request_home_server, "iran")
        self.assertEqual(web_result.ledger.request_source_surface.value, "webapp")
        self.assertEqual(web_result.ledger.request_source_server, "iran")
        self.assertEqual(web_result.ledger.received_at, received_at)

        db = _FakeDB()
        with patch("core.services.offer_request_ledger_service.current_server", return_value="foreign"):
            bot_result = await create_offer_request_ledger_entry(
                db,
                OfferRequestLedgerCommand(
                    request_home_server="foreign",
                    local_offer_id=8,
                    offer_public_id="ofr_bot",
                    requester_user_id=6,
                    actor_user_id=6,
                    request_source_surface="telegram_bot",
                    request_source_server="foreign",
                    requested_quantity=10,
                    idempotency_key="telegram_callback:abc",
                ),
            )

        self.assertEqual(bot_result.ledger.request_source_surface.value, "telegram_bot")
        self.assertEqual(bot_result.ledger.request_source_server, "foreign")

    async def test_forwarded_request_preserves_original_source_metadata(self):
        db = _FakeDB()
        with patch("core.services.offer_request_ledger_service.current_server", return_value="foreign"):
            result = await create_offer_request_ledger_entry(
                db,
                OfferRequestLedgerCommand(
                    request_home_server="foreign",
                    local_offer_id=9,
                    offer_public_id="ofr_forwarded",
                    requester_user_id=5,
                    actor_user_id=44,
                    request_source_surface="webapp",
                    request_source_server="iran",
                    requested_quantity=3,
                    idempotency_key="web-forwarded:1",
                ),
            )

        self.assertEqual(result.ledger.request_home_server, "foreign")
        self.assertEqual(result.ledger.request_source_surface.value, "webapp")
        self.assertEqual(result.ledger.request_source_server, "iran")
        self.assertEqual(result.ledger.actor_user_id, 44)

    async def test_customer_snapshot_and_rejected_request_are_durable_without_trade(self):
        relation = SimpleNamespace(
            id=17,
            owner_user_id=4,
            customer_tier=CustomerTier.TIER_2,
            management_name="مشتری مهم",
            commission_rate=Decimal("0.70"),
            min_trade_quantity=5,
            max_trade_quantity=50,
            max_daily_trades=3,
            max_daily_commodity_volume=100,
        )
        snapshot = customer_relation_snapshot(relation)
        db = _FakeDB()

        result = await create_offer_request_ledger_entry(
            db,
            OfferRequestLedgerCommand(
                request_home_server="iran",
                local_offer_id=12,
                offer_public_id="ofr_rejected",
                requester_user_id=5,
                actor_user_id=5,
                request_source_surface="webapp",
                request_source_server="iran",
                requested_quantity=4,
                result_status=OfferRequestStatus.REJECTED_BUSINESS_RULE,
                public_failure_code="customer_limit",
                public_failure_message="محدودیت مقدار معامله",
                **snapshot,
            ),
        )

        self.assertEqual(result.ledger.result_status, OfferRequestStatus.REJECTED_BUSINESS_RULE)
        self.assertIsNotNone(result.ledger.decided_at)
        self.assertIsNone(result.ledger.resulting_trade_id)
        self.assertEqual(result.ledger.customer_relation_id, 17)
        self.assertEqual(result.ledger.customer_owner_user_id, 4)
        self.assertEqual(result.ledger.customer_tier_snapshot, "tier2")
        self.assertEqual(result.ledger.customer_management_name_snapshot, "مشتری مهم")
        self.assertEqual(result.ledger.customer_commission_rate_snapshot, Decimal("0.70"))
        self.assertEqual(result.ledger.customer_commission_context["max_daily_trades"], 3)

    async def test_duplicate_idempotency_replay_returns_existing_row_without_insert(self):
        existing = SimpleNamespace(id=31, result_status=OfferRequestStatus.COMPLETED_TRADE)
        db = _FakeDB(existing=existing)

        result = await create_offer_request_ledger_entry(
            db,
            OfferRequestLedgerCommand(
                request_home_server="foreign",
                local_offer_id=7,
                offer_public_id="ofr_existing",
                requester_user_id=5,
                actor_user_id=5,
                request_source_surface="telegram_bot",
                request_source_server="foreign",
                requested_quantity=1,
                idempotency_key="telegram_callback:existing",
            ),
        )

        self.assertTrue(result.duplicate_replay)
        self.assertIs(result.ledger, existing)
        self.assertEqual(db.added, [])

    async def test_terminal_rows_cannot_change_to_contradictory_outcome(self):
        ledger = SimpleNamespace(result_status=OfferRequestStatus.COMPLETED_TRADE, decided_at=datetime.now(timezone.utc))

        with self.assertRaises(OfferRequestTerminalStateError):
            apply_offer_request_decision(ledger, result_status=OfferRequestStatus.REJECTED_CONFLICT)

        apply_offer_request_decision(ledger, result_status=OfferRequestStatus.COMPLETED_TRADE, resulting_trade_id=55)
        self.assertEqual(ledger.resulting_trade_id, 55)

    def test_history_query_is_paginated_and_ordered(self):
        stmt = build_offer_request_history_query(offer_public_id="ofr_history", limit=500, offset=-10)
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("offer_requests.offer_public_id = 'ofr_history'", compiled)
        self.assertIn("ORDER BY offer_requests.received_at DESC, offer_requests.id DESC", compiled)
        self.assertIn("LIMIT 100", compiled)
        self.assertIn("OFFSET 0", compiled)


if __name__ == "__main__":
    unittest.main()
