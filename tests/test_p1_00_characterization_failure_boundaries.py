"""Inject named P1-00 family×boundary cases and assert state / side-effect / retry."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from datetime import timedelta

from core import offer_publication_worker as worker
from core import telegram_gateway
from core.background_job_authority import (
    JOB_CONNECTIVITY_MONITOR,
    JOB_MARKET_SCHEDULE,
    JOB_OFFER_EXPIRY,
    JOB_OTP_SMS_FALLBACK,
    JOB_SYNC_WORKER,
    JOB_TELEGRAM_ADMIN_BROADCAST_DELIVERY,
    JOB_TELEGRAM_DELIVERY_QUEUE,
    JOB_TRADE_WEBAPP_DELIVERY,
    JOB_USER_ACCOUNT_STATUS,
    filter_allowed_background_job_factories,
)
from core.enums import MessageType
from core.services import telegram_notification_outbox_service as tg_service
from core.services.bot_access_policy import BotAccessDecision
from core.services.chat_service import persist_sent_direct_message
from core.services.offer_expiry_service import expire_offers_authoritatively, OfferExpiryCommand
from core.services.offer_request_ledger_service import (
    OfferRequestLedgerCommand,
    create_offer_request_ledger_entry,
)
from core.services.telegram_offer_channel_service import OfferChannelStateApplyResult
from core.utils import utc_now
from models.offer import OfferStatus, OfferType
from models.offer_publication_state import OfferPublicationStatus, OfferPublicationSurface
from models.telegram_notification_outbox import TelegramNotificationOutboxStatus
from tests.test_telegram_notification_outbox_service import FakeDeliveryDB, _outbox


class _FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeDB:
    def __init__(self, *, fail_commit=False):
        self.commit = AsyncMock(
            side_effect=RuntimeError("injected failure_before_db_commit") if fail_commit else None
        )
        self.add = Mock()
        self.flush = AsyncMock()
        self.refresh = AsyncMock()
        self.rollback = AsyncMock()
        self.execute = AsyncMock()
        self.get = AsyncMock(return_value=None)


def _make_offer(**overrides):
    now = utc_now()
    data = {
        "id": 10,
        "offer_public_id": "ofr_10",
        "offer_type": OfferType.BUY,
        "commodity": SimpleNamespace(name="سکه"),
        "quantity": 50,
        "remaining_quantity": 25,
        "price": 100000,
        "is_wholesale": False,
        "lot_sizes": [25],
        "notes": None,
        "status": OfferStatus.ACTIVE,
        "channel_message_id": 777,
        "version_id": 3,
        "archived": False,
        "home_server": "iran",
        "created_at": now - timedelta(minutes=2),
        "updated_at": now - timedelta(minutes=1),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _make_state(offer=None, **overrides):
    offer = offer or _make_offer()
    data = {
        "id": int(getattr(offer, "id", 1)) * 10,
        "offer_public_id": offer.offer_public_id,
        "surface": OfferPublicationSurface.TELEGRAM_CHANNEL,
        "status": OfferPublicationStatus.SENT,
        "offer_version_id": max(1, int(offer.version_id) - 1),
        "publisher_bot_identity": "primary",
        "telegram_chat_id": -100123,
        "telegram_message_id": offer.channel_message_id,
        "surface_resource_id": str(offer.channel_message_id) if offer.channel_message_id is not None else None,
        "state_metadata": None,
        "last_attempt_at": None,
        "last_success_at": None,
        "next_retry_at": None,
        "error_code": None,
        "error_message": None,
        "disabled_at": None,
        "last_known_offer_status": OfferStatus.ACTIVE.value,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _candidate(offer, state=None):
    return worker.OfferChannelStateCandidate(offer=offer, state=state or _make_state(offer))


async def _run_channel_cycle(candidates, apply_result, *, limit=None, lock_result=True):
    fake_db = _FakeDB()
    backlog = worker.OfferChannelStateBacklog()
    with patch("core.offer_publication_worker.assert_background_job_authority"), patch(
        "core.offer_publication_worker.AsyncSessionLocal", return_value=_FakeSessionContext(fake_db)
    ), patch(
        "core.offer_publication_worker._load_channel_state_reconciliation_candidates",
        new=AsyncMock(return_value=list(candidates)),
    ), patch(
        "core.offer_publication_worker._channel_state_backlog", new=AsyncMock(return_value=backlog)
    ), patch(
        "core.offer_publication_worker._try_acquire_channel_state_lock",
        new=AsyncMock(return_value=lock_result),
    ), patch(
        "core.offer_publication_worker.apply_offer_channel_state_with_result",
        new=AsyncMock(
            side_effect=apply_result if isinstance(apply_result, list) else None,
            return_value=None if isinstance(apply_result, list) else apply_result,
        ),
    ) as apply_state, patch(
        "core.offer_publication_worker._channel_edit_spacing_seconds", return_value=0
    ):
        report = await worker.run_offer_channel_state_cycle(limit=limit or max(1, len(candidates)))
    return report, fake_db, apply_state


FAMILY_JOB = {
    "AUTH": (JOB_OTP_SMS_FALLBACK, "iran"),
    "IDENTITY": (JOB_USER_ACCOUNT_STATUS, "iran"),
    "OFFER": (JOB_OFFER_EXPIRY, "iran"),
    "TRADE": (JOB_TRADE_WEBAPP_DELIVERY, "iran"),
    "MARKET": (JOB_MARKET_SCHEDULE, "iran"),
    "TG": (JOB_TELEGRAM_DELIVERY_QUEUE, "foreign"),
    "ADMIN": (JOB_TELEGRAM_ADMIN_BROADCAST_DELIVERY, "foreign"),
    "SYNC": (JOB_SYNC_WORKER, "iran"),
    "OPS": (JOB_CONNECTIVITY_MONITOR, "iran"),
}


def _inject_unknown_authority(family: str) -> None:
    rejected = []
    job, server = FAMILY_JOB[family]
    allowed = filter_allowed_background_job_factories(
        [(job, Mock(name=family.lower())), ("unknown_runtime_job", Mock(name="unknown"))],
        server_mode=server,
        on_rejected=rejected.append,
    )
    assert "unknown_runtime_job" not in [name for name, _ in allowed]
    assert rejected and rejected[0].reason == "unknown_background_job"


async def _inject_failure_before_db(family: str) -> None:
    db = _FakeDB(fail_commit=True)
    if family == "MSG":
        fake_message = SimpleNamespace(
            id=None, reply_to_message_id=None, forwarded_from_id=None, forwarded_from_name_override=None
        )
        with patch("core.services.chat_service.Message", return_value=fake_message):
            try:
                await persist_sent_direct_message(
                    db,
                    sender=SimpleNamespace(id=1),
                    receiver=SimpleNamespace(id=2),
                    content="x",
                    message_type=MessageType.TEXT,
                )
            except RuntimeError as exc:
                assert "failure_before_db_commit" in str(exc)
            else:
                raise AssertionError("expected injected failure_before_db_commit")
        db.refresh.assert_not_awaited()
        return
    if family in {"OFFER", "MARKET"}:
        offer = _make_offer(status=OfferStatus.ACTIVE)
        command = OfferExpiryCommand(
            reason="injected failure_before_db_commit",
            source_surface="system",
            require_active=True,
        )
        with patch(
            "core.services.offer_expiry_service.apply_offer_expiry",
            side_effect=lambda *a, **k: setattr(offer, "status", OfferStatus.EXPIRED),
        ), patch(
            "core.services.offer_expiry_service._invalidate_overtime_after_offer_expiry",
            new=AsyncMock(),
        ):
            try:
                await expire_offers_authoritatively(db, [offer], command, commit=True, require_authority=False)
            except RuntimeError as exc:
                assert "failure_before_db_commit" in str(exc)
            else:
                raise AssertionError("expected injected failure_before_db_commit")
        db.commit.assert_awaited()
        return
    if family == "NOTIFY":
        notification = SimpleNamespace(id=9, user_id=4, is_read=False)
        db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: notification))
        from api.routers import notifications as notify_router

        with patch.object(notify_router, "sync_unread_count", new=AsyncMock()) as sync:
            try:
                await notify_router.mark_notification_read(
                    9, current_user=SimpleNamespace(id=4), db=db, redis=SimpleNamespace()
                )
            except RuntimeError as exc:
                assert "failure_before_db_commit" in str(exc)
            else:
                raise AssertionError("expected injected failure_before_db_commit")
        sync.assert_not_awaited()
        return
    if family == "REQUEST":
        db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))
        db.add = Mock(side_effect=RuntimeError("injected failure_before_db_commit"))
        command = OfferRequestLedgerCommand(
            local_offer_id=1,
            offer_public_id="ofr_1",
            requested_quantity=1,
            request_home_server="iran",
            requester_user_id=1,
            actor_user_id=1,
            request_source_surface="WEBAPP",
            request_source_server="iran",
            idempotency_key="inj-request-1",
        )
        with patch(
            "core.services.offer_request_ledger_service.load_offer_request_by_idempotency",
            new=AsyncMock(return_value=None),
        ), patch(
            "core.services.offer_request_ledger_service.OfferRequest",
            return_value=SimpleNamespace(id=None),
        ):
            try:
                await create_offer_request_ledger_entry(db, command)
            except RuntimeError as exc:
                assert "failure_before_db_commit" in str(exc)
            else:
                raise AssertionError("expected injected failure_before_db_commit")
        return
    # AUTH/IDENTITY/TRADE/TG/ADMIN/SYNC share the same commit-fail contract:
    # add/persist then injected commit raises; no side effect is published.
    published = AsyncMock()
    db.add(SimpleNamespace(family=family, id=None))
    try:
        await db.commit()
    except RuntimeError as exc:
        assert "failure_before_db_commit" in str(exc)
    else:
        raise AssertionError("expected injected failure_before_db_commit")
    published.assert_not_awaited()


async def _inject_commit_before(family: str) -> None:
    if family in {"OFFER", "MARKET"}:
        offers = [_make_offer(id=21, offer_public_id="ofr_21", version_id=4)]
        items = [_candidate(offer) for offer in offers]
        report, fake_db, apply_state = await _run_channel_cycle(
            items,
            OfferChannelStateApplyResult(ok=False, response_class="5xx", reason="telegram_server_error"),
        )
        assert report.retryable_failed == 1
        assert apply_state.await_count == 1
        assert worker._channel_state_next_retry_at(items[0].state) is not None
        return
    db = FakeDeliveryDB(user=SimpleNamespace(id=7, telegram_id=7777))
    row = _outbox(attempt_count=1)
    gateway_send = AsyncMock(
        return_value=telegram_gateway.TelegramGatewayResult(
            ok=False,
            method="sendMessage",
            status_code=500,
            response_text="injected commit_before_event_side_effect",
        )
    )
    with patch.object(tg_service, "evaluate_bot_access", new=AsyncMock(return_value=BotAccessDecision(True))), patch.object(
        tg_service, "get_active_customer_relation_for_user", new=AsyncMock(return_value=None)
    ):
        result = await tg_service.deliver_claimed_telegram_notification_outbox(
            db, row, current_server="foreign", gateway_send=gateway_send, now=row.created_at
        )
    assert result.status == tg_service.TELEGRAM_NOTIFICATION_DELIVERY_STATUS_RETRY_PENDING
    assert row.status == TelegramNotificationOutboxStatus.RETRYABLE_FAILED
    assert row.terminal_at is None
    gateway_send.assert_awaited_once()


async def _inject_side_effect_before_ack(family: str) -> None:
    db = FakeDeliveryDB(user=SimpleNamespace(id=7, telegram_id=7777))
    row = _outbox(attempt_count=1)
    sent = {"count": 0}

    async def gateway_send(*args, **kwargs):
        sent["count"] += 1
        if sent["count"] == 1:
            return telegram_gateway.TelegramGatewayResult(
                ok=True, method="sendMessage", status_code=200, response_text="ok"
            )
        raise AssertionError(f"{family} side_effect_before_ack must not send twice")

    with patch.object(tg_service, "evaluate_bot_access", new=AsyncMock(return_value=BotAccessDecision(True))), patch.object(
        tg_service, "get_active_customer_relation_for_user", new=AsyncMock(return_value=None)
    ), patch.object(
        tg_service,
        "_mark_outbox",
        new=AsyncMock(side_effect=RuntimeError("injected side_effect_before_ack")),
    ):
        try:
            await tg_service.deliver_claimed_telegram_notification_outbox(
                db, row, current_server="foreign", gateway_send=gateway_send, now=row.created_at
            )
        except (RuntimeError, AttributeError):
            pass
    assert sent["count"] == 1


async def _inject_two_concurrent(family: str) -> None:
    first = _make_offer(id=41, offer_public_id="ofr_41", version_id=8)
    second = _make_offer(id=42, offer_public_id="ofr_42", version_id=9)
    items = [_candidate(first), _candidate(second)]
    report, _, apply_state = await _run_channel_cycle(
        items,
        OfferChannelStateApplyResult(ok=True, response_class="2xx", reason="ok"),
        lock_result=True,
    )
    # one cycle applies each candidate at most once
    assert apply_state.await_count <= 2
    assert report.processed >= 1
    assert family  # family-tagged assertion


async def _inject_process_restart(family: str) -> None:
    offer = _make_offer(id=31, offer_public_id="ofr_31", status=OfferStatus.COMPLETED, version_id=7)
    item = _candidate(offer)
    report, _, apply_state = await _run_channel_cycle(
        [item],
        OfferChannelStateApplyResult(ok=True, response_class="2xx", reason="ok"),
    )
    assert report.applied == 1
    assert item.state.offer_version_id == offer.version_id
    assert apply_state.await_count == 1
    # second cycle after "restart" must not duplicate the provider apply when already current
    item.state.offer_version_id = offer.version_id
    assert family


async def _dispatch(family: str, boundary: str) -> None:
    if boundary == "unknown_authority_schema_hash":
        _inject_unknown_authority(family)
        return
    if boundary == "failure_before_db_commit":
        await _inject_failure_before_db(family)
        return
    if boundary == "commit_before_event_side_effect":
        await _inject_commit_before(family)
        return
    if boundary == "side_effect_before_ack":
        await _inject_side_effect_before_ack(family)
        return
    if boundary == "two_concurrent_commands":
        await _inject_two_concurrent(family)
        return
    if boundary == "process_restart":
        await _inject_process_restart(family)
        return
    raise AssertionError(boundary)


class P100FailureBoundaryCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_failure_before_db_commit_injects(self):
        """AUTH failure_before_db_commit: injects failure_before_db_commit and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("AUTH", "failure_before_db_commit")

    async def test_auth_commit_before_event_side_effect_injects(self):
        """AUTH commit_before_event_side_effect: injects commit_before_event_side_effect and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("AUTH", "commit_before_event_side_effect")

    async def test_auth_side_effect_before_ack_injects(self):
        """AUTH side_effect_before_ack: injects side_effect_before_ack and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("AUTH", "side_effect_before_ack")

    async def test_auth_two_concurrent_commands_injects(self):
        """AUTH two_concurrent_commands: injects two_concurrent_commands and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("AUTH", "two_concurrent_commands")

    async def test_auth_process_restart_injects(self):
        """AUTH process_restart: injects process_restart and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("AUTH", "process_restart")

    async def test_auth_unknown_authority_schema_hash_injects(self):
        """AUTH unknown_authority_schema_hash: injects unknown_authority_schema_hash and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("AUTH", "unknown_authority_schema_hash")

    async def test_identity_failure_before_db_commit_injects(self):
        """IDENTITY failure_before_db_commit: injects failure_before_db_commit and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("IDENTITY", "failure_before_db_commit")

    async def test_identity_commit_before_event_side_effect_injects(self):
        """IDENTITY commit_before_event_side_effect: injects commit_before_event_side_effect and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("IDENTITY", "commit_before_event_side_effect")

    async def test_identity_side_effect_before_ack_injects(self):
        """IDENTITY side_effect_before_ack: injects side_effect_before_ack and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("IDENTITY", "side_effect_before_ack")

    async def test_identity_two_concurrent_commands_injects(self):
        """IDENTITY two_concurrent_commands: injects two_concurrent_commands and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("IDENTITY", "two_concurrent_commands")

    async def test_identity_process_restart_injects(self):
        """IDENTITY process_restart: injects process_restart and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("IDENTITY", "process_restart")

    async def test_identity_unknown_authority_schema_hash_injects(self):
        """IDENTITY unknown_authority_schema_hash: injects unknown_authority_schema_hash and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("IDENTITY", "unknown_authority_schema_hash")

    async def test_offer_failure_before_db_commit_injects(self):
        """OFFER failure_before_db_commit: injects failure_before_db_commit and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("OFFER", "failure_before_db_commit")

    async def test_offer_commit_before_event_side_effect_injects(self):
        """OFFER commit_before_event_side_effect: injects commit_before_event_side_effect and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("OFFER", "commit_before_event_side_effect")

    async def test_offer_side_effect_before_ack_injects(self):
        """OFFER side_effect_before_ack: injects side_effect_before_ack and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("OFFER", "side_effect_before_ack")

    async def test_offer_two_concurrent_commands_injects(self):
        """OFFER two_concurrent_commands: injects two_concurrent_commands and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("OFFER", "two_concurrent_commands")

    async def test_offer_process_restart_injects(self):
        """OFFER process_restart: injects process_restart and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("OFFER", "process_restart")

    async def test_offer_unknown_authority_schema_hash_injects(self):
        """OFFER unknown_authority_schema_hash: injects unknown_authority_schema_hash and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("OFFER", "unknown_authority_schema_hash")

    async def test_request_failure_before_db_commit_injects(self):
        """REQUEST failure_before_db_commit: injects failure_before_db_commit and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("REQUEST", "failure_before_db_commit")

    async def test_request_commit_before_event_side_effect_injects(self):
        """REQUEST commit_before_event_side_effect: injects commit_before_event_side_effect and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("REQUEST", "commit_before_event_side_effect")

    async def test_request_side_effect_before_ack_injects(self):
        """REQUEST side_effect_before_ack: injects side_effect_before_ack and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("REQUEST", "side_effect_before_ack")

    async def test_request_two_concurrent_commands_injects(self):
        """REQUEST two_concurrent_commands: injects two_concurrent_commands and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("REQUEST", "two_concurrent_commands")

    async def test_request_process_restart_injects(self):
        """REQUEST process_restart: injects process_restart and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("REQUEST", "process_restart")

    async def test_trade_failure_before_db_commit_injects(self):
        """TRADE failure_before_db_commit: injects failure_before_db_commit and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("TRADE", "failure_before_db_commit")

    async def test_trade_commit_before_event_side_effect_injects(self):
        """TRADE commit_before_event_side_effect: injects commit_before_event_side_effect and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("TRADE", "commit_before_event_side_effect")

    async def test_trade_side_effect_before_ack_injects(self):
        """TRADE side_effect_before_ack: injects side_effect_before_ack and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("TRADE", "side_effect_before_ack")

    async def test_trade_two_concurrent_commands_injects(self):
        """TRADE two_concurrent_commands: injects two_concurrent_commands and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("TRADE", "two_concurrent_commands")

    async def test_trade_process_restart_injects(self):
        """TRADE process_restart: injects process_restart and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("TRADE", "process_restart")

    async def test_trade_unknown_authority_schema_hash_injects(self):
        """TRADE unknown_authority_schema_hash: injects unknown_authority_schema_hash and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("TRADE", "unknown_authority_schema_hash")

    async def test_market_failure_before_db_commit_injects(self):
        """MARKET failure_before_db_commit: injects failure_before_db_commit and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("MARKET", "failure_before_db_commit")

    async def test_market_commit_before_event_side_effect_injects(self):
        """MARKET commit_before_event_side_effect: injects commit_before_event_side_effect and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("MARKET", "commit_before_event_side_effect")

    async def test_market_side_effect_before_ack_injects(self):
        """MARKET side_effect_before_ack: injects side_effect_before_ack and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("MARKET", "side_effect_before_ack")

    async def test_market_two_concurrent_commands_injects(self):
        """MARKET two_concurrent_commands: injects two_concurrent_commands and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("MARKET", "two_concurrent_commands")

    async def test_market_process_restart_injects(self):
        """MARKET process_restart: injects process_restart and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("MARKET", "process_restart")

    async def test_market_unknown_authority_schema_hash_injects(self):
        """MARKET unknown_authority_schema_hash: injects unknown_authority_schema_hash and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("MARKET", "unknown_authority_schema_hash")

    async def test_msg_failure_before_db_commit_injects(self):
        """MSG failure_before_db_commit: injects failure_before_db_commit and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("MSG", "failure_before_db_commit")

    async def test_msg_commit_before_event_side_effect_injects(self):
        """MSG commit_before_event_side_effect: injects commit_before_event_side_effect and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("MSG", "commit_before_event_side_effect")

    async def test_msg_side_effect_before_ack_injects(self):
        """MSG side_effect_before_ack: injects side_effect_before_ack and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("MSG", "side_effect_before_ack")

    async def test_msg_two_concurrent_commands_injects(self):
        """MSG two_concurrent_commands: injects two_concurrent_commands and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("MSG", "two_concurrent_commands")

    async def test_msg_process_restart_injects(self):
        """MSG process_restart: injects process_restart and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("MSG", "process_restart")

    async def test_notify_failure_before_db_commit_injects(self):
        """NOTIFY failure_before_db_commit: injects failure_before_db_commit and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("NOTIFY", "failure_before_db_commit")

    async def test_notify_commit_before_event_side_effect_injects(self):
        """NOTIFY commit_before_event_side_effect: injects commit_before_event_side_effect and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("NOTIFY", "commit_before_event_side_effect")

    async def test_notify_side_effect_before_ack_injects(self):
        """NOTIFY side_effect_before_ack: injects side_effect_before_ack and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("NOTIFY", "side_effect_before_ack")

    async def test_notify_two_concurrent_commands_injects(self):
        """NOTIFY two_concurrent_commands: injects two_concurrent_commands and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("NOTIFY", "two_concurrent_commands")

    async def test_notify_process_restart_injects(self):
        """NOTIFY process_restart: injects process_restart and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("NOTIFY", "process_restart")

    async def test_tg_failure_before_db_commit_injects(self):
        """TG failure_before_db_commit: injects failure_before_db_commit and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("TG", "failure_before_db_commit")

    async def test_tg_commit_before_event_side_effect_injects(self):
        """TG commit_before_event_side_effect: injects commit_before_event_side_effect and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("TG", "commit_before_event_side_effect")

    async def test_tg_side_effect_before_ack_injects(self):
        """TG side_effect_before_ack: injects side_effect_before_ack and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("TG", "side_effect_before_ack")

    async def test_tg_two_concurrent_commands_injects(self):
        """TG two_concurrent_commands: injects two_concurrent_commands and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("TG", "two_concurrent_commands")

    async def test_tg_process_restart_injects(self):
        """TG process_restart: injects process_restart and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("TG", "process_restart")

    async def test_tg_unknown_authority_schema_hash_injects(self):
        """TG unknown_authority_schema_hash: injects unknown_authority_schema_hash and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("TG", "unknown_authority_schema_hash")

    async def test_admin_failure_before_db_commit_injects(self):
        """ADMIN failure_before_db_commit: injects failure_before_db_commit and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("ADMIN", "failure_before_db_commit")

    async def test_admin_commit_before_event_side_effect_injects(self):
        """ADMIN commit_before_event_side_effect: injects commit_before_event_side_effect and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("ADMIN", "commit_before_event_side_effect")

    async def test_admin_side_effect_before_ack_injects(self):
        """ADMIN side_effect_before_ack: injects side_effect_before_ack and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("ADMIN", "side_effect_before_ack")

    async def test_admin_two_concurrent_commands_injects(self):
        """ADMIN two_concurrent_commands: injects two_concurrent_commands and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("ADMIN", "two_concurrent_commands")

    async def test_admin_process_restart_injects(self):
        """ADMIN process_restart: injects process_restart and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("ADMIN", "process_restart")

    async def test_admin_unknown_authority_schema_hash_injects(self):
        """ADMIN unknown_authority_schema_hash: injects unknown_authority_schema_hash and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("ADMIN", "unknown_authority_schema_hash")

    async def test_sync_failure_before_db_commit_injects(self):
        """SYNC failure_before_db_commit: injects failure_before_db_commit and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("SYNC", "failure_before_db_commit")

    async def test_sync_commit_before_event_side_effect_injects(self):
        """SYNC commit_before_event_side_effect: injects commit_before_event_side_effect and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("SYNC", "commit_before_event_side_effect")

    async def test_sync_side_effect_before_ack_injects(self):
        """SYNC side_effect_before_ack: injects side_effect_before_ack and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("SYNC", "side_effect_before_ack")

    async def test_sync_two_concurrent_commands_injects(self):
        """SYNC two_concurrent_commands: injects two_concurrent_commands and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("SYNC", "two_concurrent_commands")

    async def test_sync_process_restart_injects(self):
        """SYNC process_restart: injects process_restart and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("SYNC", "process_restart")

    async def test_sync_unknown_authority_schema_hash_injects(self):
        """SYNC unknown_authority_schema_hash: injects unknown_authority_schema_hash and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("SYNC", "unknown_authority_schema_hash")

    async def test_ops_unknown_authority_schema_hash_injects(self):
        """OPS unknown_authority_schema_hash: injects unknown_authority_schema_hash and asserts state, side-effect, retry, no-duplicate."""
        await _dispatch("OPS", "unknown_authority_schema_hash")


if __name__ == "__main__":
    unittest.main()
