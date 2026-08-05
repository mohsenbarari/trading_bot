"""Stage 6: terminal offer/account events invalidate overtime and release seats."""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services.offer_expiry_service import (
    OfferExpiryCommand,
    OfferExpiryReason,
    OfferExpirySourceSurface,
    expire_offer_authoritatively,
    expire_offers_authoritatively,
)
from core.services.offer_overtime_request_service import (
    invalidate_overtime_requests_for_offer,
    invalidate_overtime_requests_for_user,
    list_nonterminal_overtime_requests,
)
from models.offer import OfferStatus
from models.offer_request import (
    OfferRequest,
    OfferRequestSourceSurface,
    OfferRequestStatus,
    OfferRequestWorkflow,
)


CREATED = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


class _MemResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _MemoryDB:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.flush = AsyncMock()
        self.commit = AsyncMock()

    async def execute(self, stmt):
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        select_part, _, rest = compiled.partition("FROM")
        where_part = rest.split("WHERE", 1)[1] if "WHERE" in rest else rest
        matches = [
            row
            for row in self.rows
            if row.workflow_kind == OfferRequestWorkflow.OVERTIME
            and row.result_status
            in (
                OfferRequestStatus.OVERTIME_QUEUED,
                OfferRequestStatus.OVERTIME_DELIVERING,
                OfferRequestStatus.OVERTIME_PRESENTED,
            )
        ]
        if "local_offer_id =" in where_part:
            offer_id = int(where_part.split("local_offer_id =", 1)[1].split()[0].strip())
            matches = [row for row in matches if int(row.local_offer_id) == offer_id]
        if "offer_public_id =" in where_part:
            public_id = where_part.split("offer_public_id =", 1)[1].split("'")[1]
            matches = [row for row in matches if row.offer_public_id == public_id]
        if "offer_owner_user_id =" in where_part:
            owner_id = int(where_part.split("offer_owner_user_id =", 1)[1].split()[0].strip())
            matches = [row for row in matches if int(row.offer_owner_user_id) == owner_id]
        if "requester_user_id =" in where_part:
            requester_id = int(where_part.split("requester_user_id =", 1)[1].split()[0].strip())
            matches = [row for row in matches if int(row.requester_user_id) == requester_id]
        if "request_home_server =" in where_part:
            home = where_part.split("request_home_server =", 1)[1].split("'")[1]
            matches = [row for row in matches if row.request_home_server == home]
        # Occupying probe / next queued used by promote — return empty by default.
        if "overtime_delivering" in where_part and "overtime_presented" in where_part and "overtime_queued" not in where_part:
            occupying = [
                row
                for row in self.rows
                if row.result_status
                in (
                    OfferRequestStatus.OVERTIME_DELIVERING,
                    OfferRequestStatus.OVERTIME_PRESENTED,
                )
            ]
            return _MemResult(occupying[:1])
        if "result_status = 'overtime_queued'" in where_part:
            queued = [
                row
                for row in self.rows
                if row.result_status == OfferRequestStatus.OVERTIME_QUEUED
            ]
            queued.sort(key=lambda row: (int(row.queue_sequence or 0), int(getattr(row, "id", 0) or 0)))
            return _MemResult(queued[:1])
        return _MemResult(matches)


def _row(**overrides):
    data = dict(
        id=1,
        request_home_server="iran",
        local_offer_id=7,
        offer_public_id="ofr_7",
        requester_user_id=9,
        actor_user_id=9,
        request_source_surface=OfferRequestSourceSurface.WEBAPP,
        request_source_server="iran",
        requested_quantity=4,
        idempotency_key="ot:term:1",
        workflow_kind=OfferRequestWorkflow.OVERTIME,
        offer_owner_user_id=1,
        queue_sequence=1,
        result_status=OfferRequestStatus.OVERTIME_PRESENTED,
        received_at=CREATED,
    )
    data.update(overrides)
    return OfferRequest(**data)


class BulkInvalidateTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalidate_for_offer_covers_queued_delivering_presented(self):
        rows = [
            _row(id=1, result_status=OfferRequestStatus.OVERTIME_QUEUED, queue_sequence=1),
            _row(
                id=2,
                result_status=OfferRequestStatus.OVERTIME_DELIVERING,
                queue_sequence=2,
                idempotency_key="ot:2",
                offer_public_id="ofr_7",
            ),
            _row(
                id=3,
                result_status=OfferRequestStatus.OVERTIME_PRESENTED,
                queue_sequence=3,
                idempotency_key="ot:3",
                # different offer — must survive
                local_offer_id=8,
                offer_public_id="ofr_8",
            ),
        ]
        # Same-offer exclusivity normally prevents multiple nonterminal on one offer;
        # the helper still clears every matching nonterminal row for the offer.
        rows[1].local_offer_id = 7
        rows[1].offer_public_id = "ofr_7"
        db = _MemoryDB(rows)
        offer = SimpleNamespace(id=7, offer_public_id="ofr_7", home_server="iran", user_id=1)

        with patch(
            "core.services.offer_overtime_request_service.current_server",
            return_value="iran",
        ), patch(
            "core.services.offer_overtime_request_service.promote_next_for_owner",
            new=AsyncMock(return_value=None),
        ) as promote_mock:
            invalidated = await invalidate_overtime_requests_for_offer(
                db,
                offer,
                reason="offer_expired:manual",
                normal_lifetime_minutes=2,
            )

        statuses = {row.id: row.result_status for row in rows}
        self.assertEqual(statuses[1], OfferRequestStatus.OVERTIME_INVALIDATED)
        self.assertEqual(statuses[2], OfferRequestStatus.OVERTIME_INVALIDATED)
        self.assertEqual(statuses[3], OfferRequestStatus.OVERTIME_PRESENTED)
        self.assertEqual(len(invalidated), 2)
        promote_mock.assert_awaited()

    async def test_invalidate_for_user_covers_owner_and_requester_roles(self):
        rows = [
            _row(id=1, offer_owner_user_id=5, requester_user_id=9),
            _row(
                id=2,
                offer_owner_user_id=1,
                requester_user_id=5,
                local_offer_id=9,
                offer_public_id="ofr_9",
                idempotency_key="ot:req",
            ),
            _row(
                id=3,
                offer_owner_user_id=1,
                requester_user_id=9,
                local_offer_id=10,
                offer_public_id="ofr_10",
                idempotency_key="ot:other",
            ),
        ]
        db = _MemoryDB(rows)
        with patch(
            "core.services.offer_overtime_request_service.current_server",
            return_value="iran",
        ), patch(
            "core.services.offer_overtime_request_service.promote_next_for_owner",
            new=AsyncMock(return_value=None),
        ):
            invalidated = await invalidate_overtime_requests_for_user(
                db,
                user_id=5,
                reason="account_inactive",
                request_home_server="iran",
                normal_lifetime_minutes=2,
            )
        self.assertEqual({row.id for row in invalidated}, {1, 2})
        self.assertEqual(rows[2].result_status, OfferRequestStatus.OVERTIME_PRESENTED)


class ExpiryServiceFanoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_expire_offer_authoritatively_invalidates_overtime(self):
        db = SimpleNamespace(commit=AsyncMock())
        offer = SimpleNamespace(
            id=7,
            status=OfferStatus.ACTIVE,
            home_server="iran",
            offer_public_id="ofr_7",
            user_id=1,
        )
        invalidate = AsyncMock()
        with patch(
            "core.services.offer_expiry_service.current_server",
            return_value="iran",
        ), patch(
            "core.services.offer_expiry_service._invalidate_overtime_after_offer_expiry",
            new=invalidate,
        ):
            await expire_offer_authoritatively(
                db,
                offer,
                OfferExpiryCommand(
                    reason=OfferExpiryReason.MARKET_CLOSED,
                    source_surface=OfferExpirySourceSurface.SYSTEM,
                    source_server="iran",
                ),
            )
        self.assertEqual(offer.status, OfferStatus.EXPIRED)
        invalidate.assert_awaited_once()
        self.assertEqual(
            invalidate.await_args.kwargs["reason"],
            "offer_expired:market_closed",
        )

    async def test_expire_offers_batch_invalidates_once_for_batch(self):
        db = SimpleNamespace(commit=AsyncMock())
        offers = [
            SimpleNamespace(id=1, status=OfferStatus.ACTIVE, home_server="iran", offer_public_id="a"),
            SimpleNamespace(id=2, status=OfferStatus.ACTIVE, home_server="iran", offer_public_id="b"),
        ]
        invalidate = AsyncMock()
        with patch(
            "core.services.offer_expiry_service.current_server",
            return_value="iran",
        ), patch(
            "core.services.offer_expiry_service._invalidate_overtime_after_offer_expiry",
            new=invalidate,
        ):
            await expire_offers_authoritatively(
                db,
                offers,
                OfferExpiryCommand(
                    reason=OfferExpiryReason.TIME_LIMIT,
                    source_surface=OfferExpirySourceSurface.SYSTEM,
                    source_server="iran",
                ),
            )
        invalidate.assert_awaited_once()
        self.assertEqual(len(invalidate.await_args.args[1]), 2)


class ListFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_requires_identity_filter(self):
        with self.assertRaises(ValueError):
            await list_nonterminal_overtime_requests(_MemoryDB())


if __name__ == "__main__":
    unittest.main()
