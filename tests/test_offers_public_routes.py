import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.offers import get_public_offer_by_public_id, get_public_offer_detail
from core.enums import UserRole


PUBLIC_ID = "ofr_public_identifier_12345"


def dump_model(value):
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value.dict()


class FakeExecuteResult:
    def __init__(self, *, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = list(rows or [])

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return SimpleNamespace(
            all=lambda: list(self._rows),
            first=lambda: self._rows[0] if self._rows else None,
        )


class FakeDB:
    def __init__(self, results):
        self.results = list(results)
        self.statements = []

    async def execute(self, stmt, *args, **kwargs):
        self.statements.append(str(stmt))
        if not self.results:
            raise AssertionError(f"Unexpected execute call: {stmt}")
        return self.results.pop(0)


def make_offer(*, home_server="foreign", user_id=11, status="active"):
    return SimpleNamespace(
        id=7,
        offer_public_id=PUBLIC_ID,
        home_server=home_server,
        user_id=user_id,
        status=status,
        offer_type="sell",
        commodity_id=3,
        commodity=SimpleNamespace(name="gold"),
        quantity=40,
        remaining_quantity=20,
        price=125000,
        is_wholesale=False,
        lot_sizes=[20],
        notes="public note",
        created_at=datetime(2026, 6, 19, 10, 0, tzinfo=timezone.utc),
        expire_reason=None,
        expired_at=None,
        expired_by_user_id=None,
        expired_by_actor_user_id=None,
        expire_source_surface=None,
        expire_source_server=None,
        channel_message_id=777,
    )


def make_ledger():
    ts = datetime(2026, 6, 19, 10, 5, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=90,
        version_id=3,
        request_home_server="iran",
        local_offer_id=7,
        offer_public_id=PUBLIC_ID,
        requester_user_id=22,
        actor_user_id=33,
        request_source_surface="telegram_bot",
        request_source_server="foreign",
        requested_quantity=20,
        idempotency_key="secret-idempotency-key",
        received_at=ts,
        decided_at=ts,
        result_status="completed_trade",
        public_failure_code=None,
        public_failure_message=None,
        internal_failure_code="db_timeout",
        internal_failure_context={"trace_id": "private"},
        resulting_trade_id=701,
        customer_relation_id=8,
        customer_owner_user_id=11,
        customer_tier_snapshot="tier1",
        customer_management_name_snapshot="VIP customer",
        customer_commission_rate_snapshot=1.25,
        customer_commission_context={"source": "snapshot"},
        archived=False,
        created_at=ts,
        updated_at=ts,
    )


class OffersPublicRoutesTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_route_returns_safe_fields_for_synced_iran_and_foreign_offers(self):
        safe_keys = {
            "offer_public_id",
            "public_link",
            "status",
            "offer_type",
            "commodity_name",
            "quantity",
            "remaining_quantity",
            "price",
            "is_wholesale",
            "lot_sizes",
            "notes",
            "created_at",
            "expires_at_ts",
            "safe_public_state_label",
            "interaction_available",
        }
        forbidden_keys = {
            "user_id",
            "channel_message_id",
            "home_server",
            "requester_user_id",
            "request_source_server",
            "customer_relation_id",
            "internal_failure_context",
            "publication_states",
        }

        for home_server in ("iran", "foreign"):
            db = FakeDB([FakeExecuteResult(scalar=make_offer(home_server=home_server))])
            with patch(
                "core.trading_settings.get_trading_settings_async",
                new=AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=20)),
            ):
                response = await get_public_offer_by_public_id(PUBLIC_ID, db=db)

            payload = dump_model(response)
            self.assertEqual(set(payload.keys()), safe_keys)
            self.assertTrue(forbidden_keys.isdisjoint(payload.keys()))
            self.assertEqual(payload["offer_public_id"], PUBLIC_ID)
            self.assertEqual(payload["public_link"], f"/market?offer={PUBLIC_ID}")
            self.assertIn("offers.offer_public_id", db.statements[0])

    async def test_public_detail_denies_unauthenticated_and_unrelated_viewers(self):
        offer = make_offer(user_id=11)
        with self.assertRaises(HTTPException) as unauthenticated:
            await get_public_offer_detail(
                PUBLIC_ID,
                limit=50,
                offset=0,
                db=FakeDB([FakeExecuteResult(scalar=offer)]),
                current_user=None,
            )
        self.assertEqual(unauthenticated.exception.status_code, 401)

        unrelated_db = FakeDB([
            FakeExecuteResult(scalar=offer),
            FakeExecuteResult(scalar=None),
        ])
        with self.assertRaises(HTTPException) as unrelated:
            await get_public_offer_detail(
                PUBLIC_ID,
                limit=50,
                offset=0,
                db=unrelated_db,
                current_user=SimpleNamespace(id=99, role=UserRole.STANDARD),
            )
        self.assertEqual(unrelated.exception.status_code, 403)
        self.assertEqual(len(unrelated_db.statements), 2)

    async def test_owner_detail_returns_bounded_sanitized_ledger_without_admin_publication_state(self):
        owner = SimpleNamespace(id=11, role=UserRole.STANDARD)
        ledger = make_ledger()
        db = FakeDB([
            FakeExecuteResult(scalar=make_offer(user_id=owner.id)),
            FakeExecuteResult(rows=[ledger]),
        ])

        with patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=20)),
        ):
            response = await get_public_offer_detail(PUBLIC_ID, limit=2, offset=1, db=db, current_user=owner)

        payload = dump_model(response)
        self.assertEqual(payload["viewer_visibility"], "owner")
        self.assertEqual(payload["request_ledger_limit"], 2)
        self.assertEqual(payload["request_ledger_offset"], 1)
        self.assertIsNone(payload["publication_states"])
        ledger_row = payload["request_ledger"][0]["data"]
        self.assertEqual(ledger_row["request_source_surface"], "telegram_bot")
        self.assertEqual(ledger_row["request_source_server"], "foreign")
        self.assertEqual(ledger_row["requested_quantity"], 20)
        self.assertEqual(ledger_row["resulting_trade_id"], 701)
        self.assertEqual(ledger_row["customer_management_name_snapshot"], "VIP customer")
        self.assertIn("received_at", ledger_row)
        self.assertNotIn("internal_failure_context", ledger_row)
        self.assertNotIn("internal_failure_code", ledger_row)
        self.assertIn("LIMIT", "\n".join(db.statements).upper())
        self.assertIn("OFFSET", "\n".join(db.statements).upper())

    async def test_admin_detail_includes_publication_state_and_admin_failure_context(self):
        publication_state = SimpleNamespace(
            surface="telegram_channel",
            publication_owner_server="foreign",
            status="lagged",
            surface_resource_id="channel:1",
            telegram_message_id=777,
            error_code="telegram_timeout",
            last_attempt_at=datetime(2026, 6, 19, 10, 6, tzinfo=timezone.utc),
            last_success_at=None,
            lagged_at=datetime(2026, 6, 19, 10, 7, tzinfo=timezone.utc),
            disabled_at=None,
        )
        db = FakeDB([
            FakeExecuteResult(scalar=make_offer(home_server="foreign")),
            FakeExecuteResult(rows=[make_ledger()]),
            FakeExecuteResult(rows=[publication_state]),
        ])

        with patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=20)),
        ):
            response = await get_public_offer_detail(
                PUBLIC_ID,
                limit=50,
                offset=0,
                db=db,
                current_user=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
            )

        payload = dump_model(response)
        self.assertEqual(payload["viewer_visibility"], "admin_audit")
        self.assertEqual(payload["request_ledger"][0]["data"]["internal_failure_context"], {"trace_id": "private"})
        self.assertEqual(payload["publication_states"][0]["surface"], "telegram_channel")
        self.assertEqual(payload["publication_states"][0]["error_code"], "telegram_timeout")


if __name__ == "__main__":
    unittest.main()
