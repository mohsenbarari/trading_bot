"""Stage 4: durable overtime request state machine."""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services.offer_request_ledger_service import (
    TERMINAL_OFFER_REQUEST_STATUSES,
    apply_offer_request_decision,
)
from core.services.offer_overtime_request_service import (
    LEGAL_OVERTIME_TRANSITIONS,
    MAX_OUTSTANDING_REQUESTS_PER_REQUESTER,
    OVERTIME_COOLDOWN_SECONDS,
    OVERTIME_DECISION_SECONDS,
    OvertimeRequestCreateCommand,
    OvertimeRequestError,
    OvertimeRequestErrorCode,
    assert_legal_overtime_transition,
    cancel_by_requester,
    claim_owner_approval,
    cooldown_remaining_seconds,
    create_overtime_request,
    decision_deadline_at,
    expire_decision,
    mark_presented,
    promote_next_for_owner,
    record_completed_trade,
    reject_by_owner,
)
from core.services.telegram_overtime_owner_approval_queue_service import (
    OvertimeOwnerApprovalEnqueueOutcome,
    OvertimeOwnerApprovalQueueError,
)
from core.services.telegram_overtime_owner_approval_legacy_service import (
    LegacyOvertimeOwnerApprovalEnqueueOutcome,
)
from core.telegram_delivery_runtime_policy import TelegramDeliveryRuntimeMode
from models.offer import OfferStatus
from models.offer_request import (
    OVERTIME_NONTERMINAL_STATUSES,
    OVERTIME_TERMINAL_STATUSES,
    OfferRequest,
    OfferRequestSourceSurface,
    OfferRequestStatus,
    OfferRequestWorkflow,
)


CREATED = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
RECEIPT_IN_OVERTIME = CREATED + timedelta(minutes=2, seconds=30)


class TransitionTableTests(unittest.TestCase):
    def test_legal_happy_paths(self):
        assert_legal_overtime_transition(
            OfferRequestStatus.OVERTIME_QUEUED,
            OfferRequestStatus.OVERTIME_PRESENTED,
        )
        assert_legal_overtime_transition(
            OfferRequestStatus.OVERTIME_DELIVERING,
            OfferRequestStatus.OVERTIME_PRESENTED,
        )
        assert_legal_overtime_transition(
            OfferRequestStatus.OVERTIME_PRESENTED,
            OfferRequestStatus.COMPLETED_TRADE,
        )

    def test_illegal_transition_raises(self):
        with self.assertRaises(OvertimeRequestError) as caught:
            assert_legal_overtime_transition(
                OfferRequestStatus.OVERTIME_QUEUED,
                OfferRequestStatus.COMPLETED_TRADE,
            )
        self.assertEqual(caught.exception.code, OvertimeRequestErrorCode.ILLEGAL_TRANSITION)

    def test_ledger_terminal_set_includes_overtime_outcomes(self):
        for status in OVERTIME_TERMINAL_STATUSES:
            with self.subTest(status=status):
                self.assertIn(status, TERMINAL_OFFER_REQUEST_STATUSES)

    def test_overtime_terminal_is_immutable_via_ledger_helper(self):
        ledger = SimpleNamespace(
            result_status=OfferRequestStatus.OVERTIME_REJECTED_BY_OWNER,
            decided_at=CREATED,
            public_failure_code=None,
            public_failure_message=None,
            internal_failure_code=None,
            internal_failure_context=None,
            resulting_trade_id=None,
            terminal_reason="owner_rejected",
            decided_by_user_id=1,
        )
        with self.assertRaises(Exception):
            apply_offer_request_decision(
                ledger,
                result_status=OfferRequestStatus.COMPLETED_TRADE,
            )

    def test_cooldown_helper(self):
        now = CREATED + timedelta(seconds=10)
        self.assertEqual(
            cooldown_remaining_seconds(CREATED, now=now, window_seconds=30),
            20,
        )
        self.assertEqual(
            cooldown_remaining_seconds(CREATED, now=CREATED + timedelta(seconds=30)),
            0,
        )


class _MemResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalar_one_or_none(self):
        if not self._rows:
            return None
        row = self._rows[0]
        if isinstance(row, tuple):
            return row[0]
        return row

    def scalar_one(self):
        value = self.scalar_one_or_none()
        return 0 if value is None else value

    def scalars(self):
        return self


class _MemoryDB:
    """Minimal async session stand-in that understands the overtime queries."""

    def __init__(self, *, offers=None):
        self.rows: list[OfferRequest] = []
        self.offers = dict(offers or {})
        self.added: list[OfferRequest] = []
        self.get_calls: list[tuple[object, object, dict]] = []
        self.flush = AsyncMock(side_effect=self._flush)

    def add(self, obj):
        self.added.append(obj)
        if obj not in self.rows:
            self.rows.append(obj)

    async def _flush(self):
        for index, row in enumerate(self.rows, start=1):
            if getattr(row, "id", None) is None:
                row.id = index

    async def get(self, model, ident, **kwargs):
        self.get_calls.append((model, ident, kwargs))
        if model.__name__ == "Offer":
            return self.offers.get(int(ident))
        return None

    async def execute(self, stmt):
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        # Full-entity SELECTs list every column name; match only on the
        # projection + WHERE/ORDER fragments to avoid false positives.
        select_part, _, rest = compiled.partition("FROM")
        where_part = rest.split("WHERE", 1)[1] if "WHERE" in rest else rest
        select_l = select_part.lower()
        where_l = where_part.lower()

        # Count queries
        if "count(" in select_l:
            if "offer_owner_user_id" in where_l and "requester_user_id" in where_l:
                owner_id = self._extract_int(where_part, "offer_owner_user_id")
                requester_id = self._extract_int(where_part, "requester_user_id")
                count = sum(
                    1
                    for row in self.rows
                    if row.requester_user_id == requester_id
                    and row.offer_owner_user_id == owner_id
                    and row.result_status in OVERTIME_NONTERMINAL_STATUSES
                )
                return _MemResult([count])
            requester_id = self._extract_int(where_part, "requester_user_id")
            count = sum(
                1
                for row in self.rows
                if row.requester_user_id == requester_id
                and row.result_status in OVERTIME_NONTERMINAL_STATUSES
            )
            return _MemResult([count])

        if "max(" in select_l and "queue_sequence" in select_l:
            owner_id = self._extract_int(where_part, "offer_owner_user_id")
            home = "iran" if "'iran'" in where_part else "foreign"
            values = [
                int(row.queue_sequence or 0)
                for row in self.rows
                if row.offer_owner_user_id == owner_id and row.request_home_server == home
            ]
            return _MemResult([max(values) if values else 0])

        # Cooldown: SELECT decided_at ... ORDER BY decided_at
        if "decided_at" in select_l and "decided_at" in where_l:
            requester_id = self._extract_int(where_part, "requester_user_id")
            public_id = None
            if "offer_public_id =" in where_part:
                public_id = where_part.split("offer_public_id =", 1)[1].split("'")[1]
            matches = [
                row.decided_at
                for row in self.rows
                if row.requester_user_id == requester_id
                and (public_id is None or row.offer_public_id == public_id)
                and row.decided_at is not None
                and row.result_status
                in (
                    OfferRequestStatus.OVERTIME_REJECTED_BY_OWNER,
                    OfferRequestStatus.OVERTIME_DECISION_EXPIRED,
                )
            ]
            matches.sort(reverse=True)
            return _MemResult(matches[:1])

        # Idempotency lookup
        if "idempotency_key" in where_l:
            key = where_part.split("idempotency_key =", 1)[1].split("'")[1]
            matches = [row for row in self.rows if row.idempotency_key == key]
            return _MemResult(matches[:1])

        # FIFO next queued (status equals overtime_queued; order by sequence)
        if (
            "result_status = 'overtime_queued'" in where_part
            and "queue_sequence" in where_l
            and "offer_owner_user_id" in where_l
        ):
            owner_id = self._extract_int(where_part, "offer_owner_user_id")
            home = "iran" if "'iran'" in where_part else "foreign"
            matches = [
                row
                for row in self.rows
                if row.offer_owner_user_id == owner_id
                and row.request_home_server == home
                and row.result_status == OfferRequestStatus.OVERTIME_QUEUED
            ]
            matches.sort(
                key=lambda row: (int(row.queue_sequence or 0), int(getattr(row, "id", 0) or 0))
            )
            return _MemResult(matches[:1])

        # Active nonterminal request for one offer
        if "offer_public_id" in where_l and any(
            status.value in where_part for status in OVERTIME_NONTERMINAL_STATUSES
        ):
            public_id = where_part.split("offer_public_id =", 1)[1].split("'")[1]
            matches = [
                row
                for row in self.rows
                if row.offer_public_id == public_id
                and row.result_status in OVERTIME_NONTERMINAL_STATUSES
            ]
            return _MemResult(matches[:1])

        # Owner occupying probe (delivering/presented only)
        if (
            "offer_owner_user_id" in where_l
            and "overtime_delivering" in where_part
            and "overtime_presented" in where_part
            and "overtime_queued" not in where_part
        ):
            owner_id = self._extract_int(where_part, "offer_owner_user_id")
            home = "iran" if "'iran'" in where_part else "foreign"
            matches = [
                row
                for row in self.rows
                if row.offer_owner_user_id == owner_id
                and row.request_home_server == home
                and row.result_status
                in (
                    OfferRequestStatus.OVERTIME_DELIVERING,
                    OfferRequestStatus.OVERTIME_PRESENTED,
                )
            ]
            return _MemResult(matches[:1])

        return _MemResult([])

    @staticmethod
    def _extract_int(compiled: str, column: str) -> int | None:
        token = f"{column} ="
        if token not in compiled:
            token = f"offer_requests.{column} ="
        if token not in compiled:
            return None
        fragment = compiled.split(token, 1)[1].strip()
        number = ""
        for char in fragment:
            if char.isdigit():
                number += char
            elif number:
                break
        return int(number) if number else None


def _offer(**overrides):
    data = {
        "id": 7,
        "offer_public_id": "ofr_ot_7",
        "user_id": 1,
        "home_server": "iran",
        "status": OfferStatus.ACTIVE,
        "created_at": CREATED,
        "overtime_minutes_snapshot": 5,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _command(offer, **overrides):
    values = dict(
        offer=offer,
        requester_user_id=9,
        actor_user_id=9,
        requested_quantity=4,
        idempotency_key="ot:1",
        request_source_surface=OfferRequestSourceSurface.WEBAPP,
        request_source_server="iran",
        receipt_at=RECEIPT_IN_OVERTIME,
        normal_lifetime_minutes=2,
        request_home_server="iran",
    )
    values.update(overrides)
    return OvertimeRequestCreateCommand(**values)


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def _create(self, db, offer, **overrides):
        """Create with frozen ``now`` so decision clocks are deterministic."""
        return await create_overtime_request(
            db,
            _command(offer, **overrides),
            now=RECEIPT_IN_OVERTIME,
        )

    async def test_create_promotes_webapp_request_when_owner_free(self):
        offer = _offer()
        db = _MemoryDB(offers={7: offer})
        with patch(
            "core.services.offer_overtime_request_service.current_server",
            return_value="iran",
        ), patch(
            "core.services.offer_request_ledger_service.current_server",
            return_value="iran",
        ):
            result = await self._create(db, offer)

        self.assertFalse(result.duplicate_replay)
        self.assertTrue(result.promoted)
        self.assertEqual(result.ledger.result_status, OfferRequestStatus.OVERTIME_PRESENTED)
        self.assertEqual(result.ledger.workflow_kind, OfferRequestWorkflow.OVERTIME)
        self.assertEqual(result.ledger.offer_owner_user_id, 1)
        self.assertEqual(result.ledger.presented_at, RECEIPT_IN_OVERTIME)
        self.assertEqual(
            result.ledger.decision_deadline_at,
            decision_deadline_at(RECEIPT_IN_OVERTIME),
        )

    async def test_webapp_request_for_bot_offer_promotes_to_telegram_delivering(self):
        offer = _offer(home_server="foreign")
        db = _MemoryDB(offers={7: offer})
        enqueue = AsyncMock(
            return_value=OvertimeOwnerApprovalEnqueueOutcome(
                enqueued=True,
                job_id=42,
                job_created=True,
            )
        )
        with patch(
            "core.services.offer_overtime_request_service.current_server",
            return_value="foreign",
        ), patch(
            "core.services.offer_request_ledger_service.current_server",
            return_value="foreign",
        ), patch(
            "core.services.telegram_overtime_owner_approval_queue_service."
            "enqueue_overtime_owner_approval_delivery",
            enqueue,
        ), patch(
            "core.telegram_delivery_runtime_policy."
            "configured_telegram_delivery_producer_mode",
            return_value=TelegramDeliveryRuntimeMode.QUEUE_V1,
        ):
            result = await self._create(
                db,
                offer,
                request_home_server="foreign",
            )

        self.assertEqual(result.ledger.result_status, OfferRequestStatus.OVERTIME_DELIVERING)
        self.assertIsNone(result.ledger.presented_at)
        self.assertEqual(result.ledger.telegram_delivery_job_id, 42)
        offer_get = next(call for call in db.get_calls if call[0].__name__ == "Offer")
        self.assertTrue(offer_get[2].get("options"))
        enqueue.assert_awaited_once()

        await mark_presented(
            db,
            result.ledger,
            presented_at=RECEIPT_IN_OVERTIME,
            telegram_message_id=555,
        )
        self.assertEqual(result.ledger.result_status, OfferRequestStatus.OVERTIME_PRESENTED)
        self.assertEqual(result.ledger.telegram_message_id, 555)

    async def test_queue_v1_payload_error_invalidates_instead_of_raising(self):
        offer = _offer(home_server="foreign")
        db = _MemoryDB(offers={7: offer})
        enqueue = AsyncMock(
            side_effect=OvertimeOwnerApprovalQueueError(
                "overtime_owner_approval_payload_invalid"
            )
        )
        with patch(
            "core.services.offer_overtime_request_service.current_server",
            return_value="foreign",
        ), patch(
            "core.services.offer_request_ledger_service.current_server",
            return_value="foreign",
        ), patch(
            "core.services.telegram_overtime_owner_approval_queue_service."
            "enqueue_overtime_owner_approval_delivery",
            enqueue,
        ), patch(
            "core.telegram_delivery_runtime_policy."
            "configured_telegram_delivery_producer_mode",
            return_value=TelegramDeliveryRuntimeMode.QUEUE_V1,
        ):
            result = await self._create(
                db,
                offer,
                request_home_server="foreign",
            )

        self.assertEqual(
            result.ledger.result_status,
            OfferRequestStatus.OVERTIME_INVALIDATED,
        )
        enqueue.assert_awaited_once()

    async def test_legacy_runtime_uses_active_notification_outbox_not_queue_v1(self):
        offer = _offer(home_server="foreign")
        db = _MemoryDB(offers={7: offer})
        enqueue = AsyncMock(
            return_value=LegacyOvertimeOwnerApprovalEnqueueOutcome(
                enqueued=True,
                outbox_id=81,
                outbox_created=True,
            )
        )
        refresh_channel = AsyncMock(return_value=True)
        with patch(
            "core.services.offer_overtime_request_service.current_server",
            return_value="foreign",
        ), patch(
            "core.services.offer_request_ledger_service.current_server",
            return_value="foreign",
        ), patch(
            "core.services.telegram_overtime_owner_approval_legacy_service."
            "enqueue_legacy_overtime_owner_approval_delivery",
            enqueue,
        ), patch(
            "core.telegram_delivery_runtime_policy."
            "configured_telegram_delivery_producer_mode",
            return_value=TelegramDeliveryRuntimeMode.LEGACY,
        ), patch(
            "core.services.telegram_offer_channel_service."
            "request_offer_channel_state_refresh",
            refresh_channel,
        ):
            result = await self._create(db, offer, request_home_server="foreign")

        self.assertEqual(result.ledger.result_status, OfferRequestStatus.OVERTIME_DELIVERING)
        self.assertIsNone(result.ledger.presented_at)
        self.assertIsNone(result.ledger.telegram_delivery_job_id)
        enqueue.assert_awaited_once()
        refresh_channel.assert_awaited_once_with(db, offer, now=RECEIPT_IN_OVERTIME)

    async def test_bot_request_for_webapp_offer_stays_on_webapp_owner_surface(self):
        offer = _offer(home_server="iran")
        db = _MemoryDB(offers={7: offer})
        enqueue = AsyncMock()
        with patch(
            "core.services.offer_overtime_request_service.current_server",
            return_value="iran",
        ), patch(
            "core.services.offer_request_ledger_service.current_server",
            return_value="iran",
        ), patch(
            "core.services.telegram_overtime_owner_approval_queue_service."
            "enqueue_overtime_owner_approval_delivery",
            enqueue,
        ), patch(
            "core.telegram_delivery_runtime_policy."
            "configured_telegram_delivery_producer_mode",
            return_value=TelegramDeliveryRuntimeMode.QUEUE_V1,
        ):
            result = await self._create(
                db,
                offer,
                request_source_surface=OfferRequestSourceSurface.TELEGRAM_BOT,
                request_source_server="foreign",
                request_home_server="iran",
            )

        self.assertEqual(result.ledger.result_status, OfferRequestStatus.OVERTIME_PRESENTED)
        self.assertEqual(result.ledger.presented_at, RECEIPT_IN_OVERTIME)
        enqueue.assert_not_awaited()

    async def test_idempotent_replay(self):
        offer = _offer()
        db = _MemoryDB(offers={7: offer})
        with patch(
            "core.services.offer_overtime_request_service.current_server",
            return_value="iran",
        ), patch(
            "core.services.offer_request_ledger_service.current_server",
            return_value="iran",
        ):
            first = await self._create(db, offer)
            second = await self._create(db, offer)
        self.assertFalse(first.duplicate_replay)
        self.assertTrue(second.duplicate_replay)
        self.assertIs(second.ledger, first.ledger)

    async def test_same_offer_contention(self):
        offer = _offer()
        db = _MemoryDB(offers={7: offer})
        with patch(
            "core.services.offer_overtime_request_service.current_server",
            return_value="iran",
        ), patch(
            "core.services.offer_request_ledger_service.current_server",
            return_value="iran",
        ):
            await self._create(db, offer, idempotency_key="ot:a", requester_user_id=9)
            with self.assertRaises(OvertimeRequestError) as caught:
                await self._create(db, offer, idempotency_key="ot:b", requester_user_id=10)
        self.assertEqual(caught.exception.code, OvertimeRequestErrorCode.SAME_OFFER_BUSY)
        self.assertIn("ثانیه", caught.exception.detail)

    async def test_fifo_queues_second_offer_of_same_owner(self):
        offer_a = _offer(id=7, offer_public_id="ofr_a")
        offer_b = _offer(id=8, offer_public_id="ofr_b")
        db = _MemoryDB(offers={7: offer_a, 8: offer_b})
        with patch(
            "core.services.offer_overtime_request_service.current_server",
            return_value="iran",
        ), patch(
            "core.services.offer_request_ledger_service.current_server",
            return_value="iran",
        ):
            first = await self._create(
                db, offer_a, idempotency_key="ot:1", requester_user_id=9
            )
            second = await self._create(
                db, offer_b, idempotency_key="ot:2", requester_user_id=10
            )
        self.assertEqual(first.ledger.result_status, OfferRequestStatus.OVERTIME_PRESENTED)
        self.assertEqual(second.ledger.result_status, OfferRequestStatus.OVERTIME_QUEUED)

        await reject_by_owner(
            db,
            first.ledger,
            decided_by_user_id=1,
            now=RECEIPT_IN_OVERTIME + timedelta(seconds=5),
            normal_lifetime_minutes=2,
        )
        self.assertEqual(second.ledger.result_status, OfferRequestStatus.OVERTIME_PRESENTED)

    async def test_separate_home_servers_have_independent_owner_seats(self):
        iran_offer = _offer(id=7, offer_public_id="ofr_iran", home_server="iran")
        foreign_offer = _offer(id=8, offer_public_id="ofr_foreign", home_server="foreign")
        db = _MemoryDB(offers={7: iran_offer, 8: foreign_offer})
        enqueue = AsyncMock(
            return_value=OvertimeOwnerApprovalEnqueueOutcome(
                enqueued=True,
                job_id=43,
                job_created=True,
            )
        )
        with patch(
            "core.services.offer_overtime_request_service.current_server",
            return_value="iran",
        ), patch(
            "core.services.offer_request_ledger_service.current_server",
            return_value="iran",
        ):
            iran = await self._create(
                db, iran_offer, idempotency_key="ot:iran", request_home_server="iran"
            )
        with patch(
            "core.services.offer_overtime_request_service.current_server",
            return_value="foreign",
        ), patch(
            "core.services.offer_request_ledger_service.current_server",
            return_value="foreign",
        ), patch(
            "core.services.telegram_overtime_owner_approval_queue_service."
            "enqueue_overtime_owner_approval_delivery",
            enqueue,
        ), patch(
            "core.telegram_delivery_runtime_policy."
            "configured_telegram_delivery_producer_mode",
            return_value=TelegramDeliveryRuntimeMode.QUEUE_V1,
        ):
            foreign = await self._create(
                db,
                foreign_offer,
                idempotency_key="ot:foreign",
                request_home_server="foreign",
                request_source_server="foreign",
                requester_user_id=11,
            )
        self.assertEqual(iran.ledger.result_status, OfferRequestStatus.OVERTIME_PRESENTED)
        self.assertEqual(foreign.ledger.result_status, OfferRequestStatus.OVERTIME_DELIVERING)
        self.assertEqual(foreign.ledger.telegram_delivery_job_id, 43)
        enqueue.assert_awaited_once()

    async def test_requester_limit_and_per_owner_limit(self):
        offers = {
            i: _offer(id=i, offer_public_id=f"ofr_{i}", user_id=i)
            for i in range(1, 5)
        }
        # Same owner for offers 4 used for per-owner limit; first three different owners.
        offers[4] = _offer(id=4, offer_public_id="ofr_4", user_id=1)
        db = _MemoryDB(offers=offers)
        with patch(
            "core.services.offer_overtime_request_service.current_server",
            return_value="iran",
        ), patch(
            "core.services.offer_request_ledger_service.current_server",
            return_value="iran",
        ):
            for index in range(1, MAX_OUTSTANDING_REQUESTS_PER_REQUESTER + 1):
                await self._create(
                    db,
                    offers[index],
                    idempotency_key=f"ot:{index}",
                    requester_user_id=99,
                )
            with self.assertRaises(OvertimeRequestError) as limit_caught:
                await self._create(
                    db,
                    _offer(id=5, offer_public_id="ofr_5", user_id=5),
                    idempotency_key="ot:5",
                    requester_user_id=99,
                )
            self.assertEqual(limit_caught.exception.code, OvertimeRequestErrorCode.REQUESTER_LIMIT)

        # Fresh requester against an owner they already have open.
        db2 = _MemoryDB(offers={1: offers[1], 4: offers[4]})
        # Seed an open request for requester 50 against owner 1
        open_row = OfferRequest(
            request_home_server="iran",
            local_offer_id=1,
            offer_public_id="ofr_1",
            requester_user_id=50,
            actor_user_id=50,
            request_source_surface=OfferRequestSourceSurface.WEBAPP,
            request_source_server="iran",
            requested_quantity=1,
            idempotency_key="seed",
            workflow_kind=OfferRequestWorkflow.OVERTIME,
            offer_owner_user_id=1,
            queue_sequence=1,
            result_status=OfferRequestStatus.OVERTIME_PRESENTED,
            received_at=RECEIPT_IN_OVERTIME,
        )
        db2.add(open_row)
        with patch(
            "core.services.offer_overtime_request_service.current_server",
            return_value="iran",
        ), patch(
            "core.services.offer_request_ledger_service.current_server",
            return_value="iran",
        ):
            with self.assertRaises(OvertimeRequestError) as owner_caught:
                await self._create(
                    db2,
                    offers[4],
                    idempotency_key="ot:owner-limit",
                    requester_user_id=50,
                )
        self.assertEqual(owner_caught.exception.code, OvertimeRequestErrorCode.REQUESTER_OWNER_LIMIT)

    async def test_cancel_versus_reject_race_first_wins(self):
        offer = _offer()
        db = _MemoryDB(offers={7: offer})
        with patch(
            "core.services.offer_overtime_request_service.current_server",
            return_value="iran",
        ), patch(
            "core.services.offer_request_ledger_service.current_server",
            return_value="iran",
        ):
            created = await self._create(db, offer)
            await cancel_by_requester(
                db,
                created.ledger,
                requester_user_id=9,
                now=RECEIPT_IN_OVERTIME + timedelta(seconds=1),
                normal_lifetime_minutes=2,
            )
            with self.assertRaises(OvertimeRequestError) as caught:
                await reject_by_owner(
                    db,
                    created.ledger,
                    decided_by_user_id=1,
                    now=RECEIPT_IN_OVERTIME + timedelta(seconds=2),
                )
        self.assertEqual(caught.exception.code, OvertimeRequestErrorCode.ALREADY_TERMINAL)
        self.assertEqual(
            created.ledger.result_status,
            OfferRequestStatus.OVERTIME_CANCELLED_BY_REQUESTER,
        )

    async def test_decision_timeout_and_cooldown(self):
        offer = _offer()
        db = _MemoryDB(offers={7: offer})
        with patch(
            "core.services.offer_overtime_request_service.current_server",
            return_value="iran",
        ), patch(
            "core.services.offer_request_ledger_service.current_server",
            return_value="iran",
        ):
            created = await self._create(db, offer)
            deadline = created.ledger.decision_deadline_at
            await expire_decision(
                db,
                created.ledger,
                now=deadline,
                normal_lifetime_minutes=2,
            )
            self.assertEqual(
                created.ledger.result_status,
                OfferRequestStatus.OVERTIME_DECISION_EXPIRED,
            )
            retry_at = deadline + timedelta(seconds=5)
            with self.assertRaises(OvertimeRequestError) as caught:
                await create_overtime_request(
                    db,
                    _command(
                        offer,
                        idempotency_key="ot:retry",
                        receipt_at=retry_at,
                    ),
                    now=retry_at,
                )
        self.assertEqual(caught.exception.code, OvertimeRequestErrorCode.COOLDOWN_ACTIVE)

    async def test_owner_approval_claim_and_completed_trade(self):
        offer = _offer()
        db = _MemoryDB(offers={7: offer})
        with patch(
            "core.services.offer_overtime_request_service.current_server",
            return_value="iran",
        ), patch(
            "core.services.offer_request_ledger_service.current_server",
            return_value="iran",
        ):
            created = await self._create(db, offer)
            await claim_owner_approval(
                created.ledger,
                decided_by_user_id=1,
                now=RECEIPT_IN_OVERTIME + timedelta(seconds=1),
            )
            record_completed_trade(
                created.ledger,
                resulting_trade_id=77,
                decided_by_user_id=1,
                decided_at=RECEIPT_IN_OVERTIME + timedelta(seconds=1),
            )
        self.assertEqual(created.ledger.result_status, OfferRequestStatus.COMPLETED_TRADE)
        self.assertEqual(created.ledger.resulting_trade_id, 77)

    async def test_exact_decision_deadline_rejects_owner(self):
        offer = _offer()
        db = _MemoryDB(offers={7: offer})
        with patch(
            "core.services.offer_overtime_request_service.current_server",
            return_value="iran",
        ), patch(
            "core.services.offer_request_ledger_service.current_server",
            return_value="iran",
        ):
            created = await self._create(db, offer)
            with self.assertRaises(OvertimeRequestError) as caught:
                await claim_owner_approval(
                    created.ledger,
                    decided_by_user_id=1,
                    now=created.ledger.decision_deadline_at,
                )
        self.assertEqual(caught.exception.code, OvertimeRequestErrorCode.DECISION_EXPIRED)

    async def test_requires_idempotency_and_approval_intake(self):
        offer = _offer()
        db = _MemoryDB(offers={7: offer})
        with self.assertRaises(OvertimeRequestError) as missing_key:
            await create_overtime_request(db, _command(offer, idempotency_key=""))
        self.assertEqual(missing_key.exception.code, OvertimeRequestErrorCode.IDEMPOTENCY_REQUIRED)

        with patch(
            "core.services.offer_overtime_request_service.current_server",
            return_value="iran",
        ), patch(
            "core.services.offer_request_ledger_service.current_server",
            return_value="iran",
        ):
            with self.assertRaises(OvertimeRequestError) as intake:
                await create_overtime_request(
                    db,
                    _command(
                        offer,
                        receipt_at=CREATED + timedelta(seconds=10),  # still normal time
                    ),
                    now=CREATED + timedelta(seconds=10),
                )
        self.assertEqual(intake.exception.code, OvertimeRequestErrorCode.INTAKE_REJECTED)


if __name__ == "__main__":
    unittest.main()
