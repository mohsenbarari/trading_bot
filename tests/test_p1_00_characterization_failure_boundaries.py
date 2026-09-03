"""Inject named P1-00 family×boundary cases on that family's own mutating path."""

from __future__ import annotations

import ast
import inspect
import json
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from core import offer_publication_worker as worker
from core import sync_worker
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
from core.enums import MessageType, NotificationLevel, UserAccountStatus, UserRole
from core.services import session_service
from core.services import telegram_admin_broadcast_delivery_service as admin_delivery
from core.services import telegram_admin_broadcast_service as admin_broadcast
from core.services import telegram_notification_outbox_service as tg_service
from core.services import trade_webapp_delivery_service as trade_webapp
from core.services import user_account_status_service as identity_status
from core.services.bot_access_policy import BotAccessDecision
from core.services.chat_service import persist_sent_direct_message
from core.services.market_schedule_service import MarketScheduleEvaluation
from core.services.market_transition_service import apply_market_schedule_transition
from core.services.offer_expiry_service import OfferExpiryCommand, expire_offers_authoritatively
from core.services.offer_request_ledger_service import (
    OfferRequestLedgerCommand,
    create_offer_request_ledger_entry,
)
from core.services.otp_delivery_state_service import OTPDeliveryClaim
from core.services.otp_sms_delivery_service import execute_claimed_otp_sms_delivery
from core.services.telegram_offer_channel_service import OfferChannelStateApplyResult
from core.sms import SMSDeliveryOutcome
from core.utils import utc_now
from models.telegram_admin_broadcast import (
    TelegramAdminBroadcastAudienceType,
    TelegramAdminBroadcastContentKind,
)
from models.offer import OfferStatus, OfferType
from models.offer_publication_state import OfferPublicationStatus, OfferPublicationSurface
from models.telegram_notification_outbox import TelegramNotificationOutboxStatus
from tests.test_telegram_notification_outbox_service import FakeDeliveryDB, _outbox

REPO_ROOT = Path(__file__).resolve().parents[1]
INV_DIR = REPO_ROOT / "docs/refactor/two-server/section-1/inventory"

FAMILY_OWNED_HELPERS = {
    "AUTH": {
        "create_session",
        "handle_login_session",
        "execute_claimed_otp_sms_delivery",
        "publish_session_revocation",
        "JOB_OTP_SMS_FALLBACK",
    },
    "IDENTITY": {
        "transition_user_account_status",
        "JOB_USER_ACCOUNT_STATUS",
    },
    "OFFER": {
        "expire_offers_authoritatively",
        "run_offer_channel_state_cycle",
        "JOB_OFFER_EXPIRY",
    },
    "REQUEST": {
        "create_offer_request_ledger_entry",
    },
    "TRADE": {
        "deliver_webapp_trade_notification",
        "JOB_TRADE_WEBAPP_DELIVERY",
    },
    "MARKET": {
        "apply_market_schedule_transition",
        "JOB_MARKET_SCHEDULE",
    },
    "MSG": {
        "persist_sent_direct_message",
    },
    "NOTIFY": {
        "mark_notification_read",
    },
    "TG": {
        "deliver_claimed_telegram_notification_outbox",
        "JOB_TELEGRAM_DELIVERY_QUEUE",
    },
    "ADMIN": {
        "create_telegram_admin_broadcast",
        "deliver_claimed_telegram_admin_broadcast_receipt",
        "handle_admin_toggle_block",
        "JOB_TELEGRAM_ADMIN_BROADCAST_DELIVERY",
    },
    "SYNC": {
        "send_sync_item",
        "mark_change_log_delivered",
        "JOB_SYNC_WORKER",
    },
    "OPS": {
        "JOB_CONNECTIVITY_MONITOR",
    },
}

FOREIGN_SHARED_HELPERS = {
    "deliver_claimed_telegram_notification_outbox": "TG",
    "run_offer_channel_state_cycle": "OFFER",
    "expire_offers_authoritatively": "OFFER",
}


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
        self.add_all = Mock()
        self.flush = AsyncMock()
        self.refresh = AsyncMock()
        self.rollback = AsyncMock()
        self.execute = AsyncMock()
        self.get = AsyncMock(return_value=None)
        self.begin = AsyncMock(return_value=_FakeSessionContext(self))


def _load_json(name: str) -> dict:
    return json.loads((INV_DIR / name).read_text(encoding="utf-8"))


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


def _request_command(key: str = "inj-request-1") -> OfferRequestLedgerCommand:
    return OfferRequestLedgerCommand(
        local_offer_id=1,
        offer_public_id="ofr_1",
        requested_quantity=1,
        request_home_server="iran",
        requester_user_id=1,
        actor_user_id=1,
        request_source_surface="WEBAPP",
        request_source_server="iran",
        idempotency_key=key,
    )


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


def cited_node_helper_names(node_id: str) -> set[str]:
    path, separator, rest = node_id.partition("::")
    if not separator:
        raise AssertionError(f"filename-only evidence is forbidden: {node_id}")
    class_name, _, func_name = rest.partition(".")
    tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"), filename=path)
    by_name: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            by_name[node.name] = node
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    by_name[item.name] = item
    if func_name not in by_name:
        raise AssertionError(f"missing cited function {node_id}")
    names = {func_name}
    stack = [by_name[func_name]]
    seen = {func_name}
    while stack:
        current = stack.pop()
        for child in ast.walk(current):
            if isinstance(child, ast.Name):
                names.add(child.id)
                if child.id in by_name and child.id not in seen:
                    seen.add(child.id)
                    stack.append(by_name[child.id])
            if isinstance(child, ast.Attribute):
                names.add(child.attr)
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                names.add(child.value)
    return names


def assert_cited_node_uses_family_path(family: str, node_id: str) -> None:
    names = cited_node_helper_names(node_id)
    owned = FAMILY_OWNED_HELPERS[family]
    talk = " ".join(sorted(names))
    if not (names & owned) and not any(helper in talk for helper in owned):
        raise AssertionError(f"{family} cited node {node_id} does not name an owned helper {sorted(owned)}")
    foreign = {
        helper
        for helper, owner in FOREIGN_SHARED_HELPERS.items()
        if owner != family and (helper in names or helper in talk)
    }
    owned_hit = (names & owned) or {helper for helper in owned if helper in talk}
    if foreign and not (owned_hit - set(FOREIGN_SHARED_HELPERS)):
        raise AssertionError(
            f"{family} cited node uses foreign-family helper {sorted(foreign)} as sole proof"
        )


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


async def _inject_auth_failure_before_db() -> None:
    db = _FakeDB(fail_commit=True)
    user = SimpleNamespace(id=11, role=UserRole.STANDARD)
    with patch.object(session_service, "_is_inactive_account", return_value=False), patch.object(
        session_service, "get_active_sessions", new=AsyncMock(return_value=[])
    ), patch.object(session_service, "_is_accountant_user", new=AsyncMock(return_value=False)), patch(
        "core.services.session_service.get_effective_max_sessions", return_value=2
    ):
        try:
            await session_service.handle_login_session(db, user, "refresh-token", "web")
        except RuntimeError as exc:
            assert "failure_before_db_commit" in str(exc)
        else:
            raise AssertionError("expected injected failure_before_db_commit")
    db.add.assert_called()
    db.commit.assert_awaited()


async def _inject_auth_commit_before() -> None:
    db = _FakeDB()
    user = SimpleNamespace(id=12, role=UserRole.STANDARD)
    old = SimpleNamespace(id=uuid4(), is_active=True, is_primary=True)
    published = AsyncMock(side_effect=RuntimeError("injected commit_before_event_side_effect"))
    with patch.object(session_service, "_is_inactive_account", return_value=False), patch.object(
        session_service, "get_active_sessions", new=AsyncMock(return_value=[old])
    ), patch.object(session_service, "_is_accountant_user", new=AsyncMock(return_value=False)), patch(
        "core.services.session_service.get_effective_max_sessions", return_value=1
    ), patch.object(session_service, "publish_session_revocation", new=published):
        try:
            await session_service.handle_login_session(db, user, "refresh-token", "web")
        except RuntimeError as exc:
            assert "commit_before_event_side_effect" in str(exc)
        else:
            raise AssertionError("expected injected commit_before_event_side_effect")
    db.commit.assert_awaited()
    published.assert_awaited()


async def _inject_auth_side_effect_before_ack() -> None:
    claim = OTPDeliveryClaim(
        claim_id=uuid4(),
        request_id=uuid4(),
        mobile_number="09121112233",
        otp_code="12345",
        lease_until=utc_now() + timedelta(seconds=30),
    )
    send = AsyncMock(return_value=SMSDeliveryOutcome.ACCEPTED)
    with patch(
        "core.services.otp_sms_delivery_service.mark_sms_provider_attempt_started",
        new=AsyncMock(return_value=True),
    ), patch(
        "core.services.otp_sms_delivery_service.send_otp_sms_result_async",
        new=send,
    ), patch(
        "core.services.otp_sms_delivery_service.record_sms_delivery_result",
        new=AsyncMock(side_effect=RuntimeError("injected side_effect_before_ack")),
    ):
        result = await execute_claimed_otp_sms_delivery(object(), claim=claim)
    assert result.provider_attempted is True
    assert result.result_recorded is False
    send.assert_awaited_once()


async def _auth_same_identity_session_slot() -> None:
    db = _FakeDB()
    user = SimpleNamespace(id=13, role=UserRole.STANDARD)
    durable: list = []

    async def get_active(*_args, **_kwargs):
        return [session for session in durable if session.is_active]

    async def fake_create(*_args, **kwargs):
        session = SimpleNamespace(
            id=uuid4(),
            is_active=True,
            is_primary=kwargs.get("is_primary", True),
        )
        durable.append(session)
        return session

    revoke = AsyncMock()
    with patch.object(session_service, "_is_inactive_account", return_value=False), patch.object(
        session_service, "get_active_sessions", new=get_active
    ), patch.object(session_service, "create_session", new=fake_create), patch.object(
        session_service, "_is_accountant_user", new=AsyncMock(return_value=False)
    ), patch(
        "core.services.session_service.get_effective_max_sessions", return_value=1
    ), patch.object(session_service, "publish_session_revocation", new=revoke):
        first = await session_service.handle_login_session(db, user, "same-user-token-a", "web")
        persisted = [session for session in durable if session.is_active]
        assert len(persisted) == 1
        second = await session_service.handle_login_session(db, user, "same-user-token-b", "web")
    assert first["action"] == "session_created"
    assert second["action"] == "session_created"
    assert first["session"].is_active is False
    assert second["session"].is_active is True
    assert second["replaced_session_count"] == 1
    assert sum(1 for session in durable if session.is_active) == 1
    revoke.assert_awaited_once()


async def _inject_auth_two_concurrent() -> None:
    await _auth_same_identity_session_slot()


async def _inject_auth_process_restart() -> None:
    db = _FakeDB()
    user = SimpleNamespace(id=14, role=UserRole.STANDARD, max_sessions=1)
    reconstructed = SimpleNamespace(
        id=uuid4(),
        is_active=True,
        is_primary=True,
        refresh_token_hash="persisted-same-restart-token",
        device_name="web",
        device_ip=None,
        platform=None,
        home_server="foreign",
        last_active_at=None,
        expires_at=None,
    )
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: reconstructed))
    create = AsyncMock(side_effect=AssertionError("AUTH restart must replay the persisted session"))
    with patch.object(session_service, "_is_inactive_account", return_value=False), patch.object(
        session_service, "create_session", new=create
    ), patch.object(session_service, "_is_accountant_user", new=AsyncMock(return_value=False)), patch(
        "core.services.session_service.get_effective_max_sessions", return_value=1
    ), patch.object(session_service, "publish_session_revocation", new=AsyncMock()):
        replay = await session_service.handle_login_session(
            db,
            user,
            "same-restart-token",
            "web",
            suspended_refresh_token="same-restart-token",
        )
    assert replay["action"] == "session_created"
    assert replay["session"] is reconstructed
    assert replay["replaced_session_count"] == 0
    assert reconstructed.is_active is True
    create.assert_not_awaited()
    db.commit.assert_awaited()


async def _inject_identity_failure_before_db() -> None:
    db = _FakeDB(fail_commit=True)
    user = SimpleNamespace(
        id=21,
        account_status=UserAccountStatus.ACTIVE,
        deactivated_at=None,
        messenger_grace_expires_at=None,
        messenger_blocked_at=None,
        telegram_id=None,
    )
    with patch.object(identity_status, "_invalidate_overtime_for_inactive_user", new=AsyncMock()), patch(
        "core.services.user_account_status_service.create_user_notification",
        new=AsyncMock(),
    ):
        await identity_status.transition_user_account_status(db, user, UserAccountStatus.INACTIVE)
        try:
            await db.commit()
        except RuntimeError as exc:
            assert "failure_before_db_commit" in str(exc)
        else:
            raise AssertionError("expected injected failure_before_db_commit")
    assert user.account_status == UserAccountStatus.INACTIVE


async def _inject_identity_two_concurrent() -> None:
    db = _FakeDB()
    same_user = SimpleNamespace(
        id=22,
        account_status=UserAccountStatus.ACTIVE,
        deactivated_at=None,
        messenger_grace_expires_at=None,
        messenger_blocked_at=None,
        telegram_id=None,
    )
    notify = AsyncMock()
    with patch.object(identity_status, "_invalidate_overtime_for_inactive_user", new=AsyncMock()), patch(
        "core.services.user_account_status_service.create_user_notification",
        new=notify,
    ):
        first = await identity_status.transition_user_account_status(db, same_user, UserAccountStatus.INACTIVE)
        second = await identity_status.transition_user_account_status(db, same_user, UserAccountStatus.INACTIVE)
    assert first.changed is True
    assert second.changed is False
    assert notify.await_count == 1


async def _inject_identity_process_restart() -> None:
    db = _FakeDB()
    reconstructed = SimpleNamespace(
        id=23,
        account_status=UserAccountStatus.INACTIVE,
        deactivated_at=utc_now(),
        messenger_grace_expires_at=utc_now(),
        messenger_blocked_at=None,
        telegram_id=None,
    )
    user = reconstructed
    notify = AsyncMock()
    with patch.object(identity_status, "_invalidate_overtime_for_inactive_user", new=AsyncMock()), patch(
        "core.services.user_account_status_service.create_user_notification",
        new=notify,
    ):
        replay = await identity_status.transition_user_account_status(db, user, UserAccountStatus.INACTIVE)
    assert replay.changed is False
    notify.assert_not_awaited()


async def _inject_offer_failure_before_db() -> None:
    db = _FakeDB(fail_commit=True)
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


async def _inject_offer_commit_before() -> None:
    offers = [_make_offer(id=21, offer_public_id="ofr_21", version_id=4)]
    items = [_candidate(offer) for offer in offers]
    report, _, apply_state = await _run_channel_cycle(
        items,
        OfferChannelStateApplyResult(ok=False, response_class="5xx", reason="telegram_server_error"),
    )
    assert report.retryable_failed == 1
    assert apply_state.await_count == 1
    assert worker._channel_state_next_retry_at(items[0].state) is not None


async def _inject_offer_side_effect_before_ack() -> None:
    offer = _make_offer(id=22, offer_public_id="ofr_22", version_id=5)
    item = _candidate(offer)
    apply_state = AsyncMock(
        side_effect=RuntimeError("injected side_effect_before_ack"),
    )
    fake_db = _FakeDB()
    backlog = worker.OfferChannelStateBacklog()
    with patch("core.offer_publication_worker.assert_background_job_authority"), patch(
        "core.offer_publication_worker.AsyncSessionLocal", return_value=_FakeSessionContext(fake_db)
    ), patch(
        "core.offer_publication_worker._load_channel_state_reconciliation_candidates",
        new=AsyncMock(return_value=[item]),
    ), patch(
        "core.offer_publication_worker._channel_state_backlog", new=AsyncMock(return_value=backlog)
    ), patch(
        "core.offer_publication_worker._try_acquire_channel_state_lock",
        new=AsyncMock(return_value=True),
    ), patch(
        "core.offer_publication_worker.apply_offer_channel_state_with_result",
        new=apply_state,
    ), patch(
        "core.offer_publication_worker._channel_edit_spacing_seconds", return_value=0
    ):
        try:
            report = await worker.run_offer_channel_state_cycle(limit=1)
        except RuntimeError as exc:
            assert "side_effect_before_ack" in str(exc)
            report = SimpleNamespace(applied=0)
    assert apply_state.await_count == 1
    assert report.applied == 0


async def _inject_offer_two_concurrent() -> None:
    offer = _make_offer(id=41, offer_public_id="ofr_same", version_id=8)
    item = _candidate(offer)
    ok = OfferChannelStateApplyResult(ok=True, response_class="2xx", reason="ok")
    winner, _, apply_winner = await _run_channel_cycle([item], ok, lock_result=True)
    loser, _, apply_loser = await _run_channel_cycle([item], ok, lock_result=False)
    assert winner.applied == 1
    assert apply_winner.await_count == 1
    assert loser.skipped_locked == 1
    assert loser.applied == 0
    assert apply_loser.await_count == 0


async def _inject_offer_process_restart() -> None:
    offer = _make_offer(id=31, offer_public_id="ofr_same", status=OfferStatus.COMPLETED, version_id=7)
    item = _candidate(offer)
    ok = OfferChannelStateApplyResult(ok=True, response_class="2xx", reason="ok")
    first, _, apply_first = await _run_channel_cycle([item], ok)
    assert first.applied == 1
    assert apply_first.await_count == 1
    reconstructed = item
    reconstructed.state.offer_version_id = offer.version_id
    replay, _, apply_replay = await _run_channel_cycle([reconstructed], ok)
    assert replay.applied == 0
    assert apply_replay.await_count == 0


async def _inject_request_failure_before_db() -> None:
    db = _FakeDB()
    db.add = Mock(side_effect=RuntimeError("injected failure_before_db_commit"))
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))
    with patch(
        "core.services.offer_request_ledger_service.load_offer_request_by_idempotency",
        new=AsyncMock(return_value=None),
    ), patch(
        "core.services.offer_request_ledger_service.OfferRequest",
        return_value=SimpleNamespace(id=None),
    ):
        try:
            await create_offer_request_ledger_entry(db, _request_command())
        except RuntimeError as exc:
            assert "failure_before_db_commit" in str(exc)
        else:
            raise AssertionError("expected injected failure_before_db_commit")


async def _inject_request_two_concurrent() -> None:
    db = _FakeDB()
    existing = SimpleNamespace(id=9, idempotency_key="same-key")
    created = SimpleNamespace(id=8, idempotency_key="same-key")
    with patch(
        "core.services.offer_request_ledger_service.load_offer_request_by_idempotency",
        new=AsyncMock(side_effect=[None, existing]),
    ), patch(
        "core.services.offer_request_ledger_service.OfferRequest",
        return_value=created,
    ):
        first = await create_offer_request_ledger_entry(db, _request_command("same-key"))
        second = await create_offer_request_ledger_entry(db, _request_command("same-key"))
    assert first.duplicate_replay is False
    assert second.duplicate_replay is True
    assert second.ledger is existing


async def _inject_request_process_restart() -> None:
    db = _FakeDB()
    reconstructed = SimpleNamespace(id=10, idempotency_key="restart-key")
    with patch(
        "core.services.offer_request_ledger_service.load_offer_request_by_idempotency",
        new=AsyncMock(return_value=reconstructed),
    ):
        replay = await create_offer_request_ledger_entry(db, _request_command("restart-key"))
    assert replay.duplicate_replay is True
    assert replay.ledger is reconstructed
    db.add.assert_not_called()


async def _inject_trade_failure_before_db() -> None:
    db = _FakeDB(fail_commit=True)
    receipt = SimpleNamespace(id=1, status="pending")
    with patch.object(
        trade_webapp, "upsert_trade_delivery_receipt",
        new=AsyncMock(return_value=SimpleNamespace(receipt=receipt, created=True, changed=True, terminal_preserved=False)),
    ), patch.object(trade_webapp, "_is_sent", return_value=False), patch.object(
        trade_webapp, "claim_receipt_by_identity_for_delivery", new=AsyncMock(return_value=receipt)
    ), patch.object(trade_webapp, "skip_receipt_after_outage_if_needed", new=AsyncMock(return_value=None)), patch.object(
        trade_webapp, "_skip_if_production_test_isolation_suppressed", new=AsyncMock(return_value=False)
    ), patch.object(
        trade_webapp, "complete_webapp_receipt_with_notification",
        new=AsyncMock(return_value=SimpleNamespace(notification=None, notification_created=True, sent_changed=True, realtime_payload={})),
    ):
        try:
            await trade_webapp.deliver_webapp_trade_notification(
                db,
                trade_number=1,
                recipient_user_id=2,
                message="trade done",
                current_server="iran",
            )
        except RuntimeError as exc:
            assert "failure_before_db_commit" in str(exc)
        else:
            raise AssertionError("expected injected failure_before_db_commit")
    db.commit.assert_awaited()


async def _inject_trade_commit_before() -> None:
    db = _FakeDB()
    receipt = SimpleNamespace(id=2, status="sent")
    published = AsyncMock(return_value=(False, "injected commit_before_event_side_effect"))
    with patch.object(
        trade_webapp, "upsert_trade_delivery_receipt",
        new=AsyncMock(return_value=SimpleNamespace(receipt=receipt, created=True, changed=True, terminal_preserved=False)),
    ), patch.object(trade_webapp, "_is_sent", return_value=False), patch.object(
        trade_webapp, "claim_receipt_by_identity_for_delivery", new=AsyncMock(return_value=receipt)
    ), patch.object(trade_webapp, "skip_receipt_after_outage_if_needed", new=AsyncMock(return_value=None)), patch.object(
        trade_webapp, "_skip_if_production_test_isolation_suppressed", new=AsyncMock(return_value=False)
    ), patch.object(
        trade_webapp, "complete_webapp_receipt_with_notification",
        new=AsyncMock(return_value=SimpleNamespace(
            notification=SimpleNamespace(id=9),
            notification_created=True,
            sent_changed=True,
            realtime_payload={"extra_payload": {}},
        )),
    ), patch.object(trade_webapp, "_safe_publish_webapp_side_effect", new=published):
        result = await trade_webapp.deliver_webapp_trade_notification(
            db,
            trade_number=2,
            recipient_user_id=3,
            message="trade done",
            current_server="iran",
        )
    db.commit.assert_awaited()
    published.assert_awaited()
    assert result.realtime_published is False
    assert result.publish_error_class == "injected commit_before_event_side_effect"


async def _inject_trade_two_concurrent() -> None:
    db = _FakeDB()
    with patch.object(
        trade_webapp, "upsert_trade_delivery_receipt",
        new=AsyncMock(return_value=SimpleNamespace(receipt=SimpleNamespace(id=3, status="pending"), created=False, changed=False, terminal_preserved=False)),
    ), patch.object(trade_webapp, "_is_sent", return_value=False), patch.object(
        trade_webapp, "claim_receipt_by_identity_for_delivery", new=AsyncMock(return_value=None)
    ):
        loser = await trade_webapp.deliver_webapp_trade_notification(
            db,
            trade_number=3,
            recipient_user_id=4,
            message="trade done",
            current_server="iran",
        )
    with patch.object(
        trade_webapp, "upsert_trade_delivery_receipt",
        new=AsyncMock(return_value=SimpleNamespace(receipt=SimpleNamespace(id=3, status="sent"), created=False, changed=False, terminal_preserved=False)),
    ), patch.object(trade_webapp, "_is_sent", return_value=True):
        winner = await trade_webapp.deliver_webapp_trade_notification(
            db,
            trade_number=3,
            recipient_user_id=4,
            message="trade done",
            current_server="iran",
        )
    assert loser.status == trade_webapp.WEBAPP_DELIVERY_STATUS_CLAIM_BUSY
    assert winner.status == trade_webapp.WEBAPP_DELIVERY_STATUS_ALREADY_SENT


async def _inject_trade_process_restart() -> None:
    db = _FakeDB()
    reconstructed = SimpleNamespace(id=4, status="sent")
    with patch.object(
        trade_webapp, "upsert_trade_delivery_receipt",
        new=AsyncMock(return_value=SimpleNamespace(receipt=reconstructed, created=False, changed=False, terminal_preserved=False)),
    ), patch.object(trade_webapp, "_is_sent", return_value=True):
        replay = await trade_webapp.deliver_webapp_trade_notification(
            db,
            trade_number=4,
            recipient_user_id=5,
            message="trade done",
            current_server="iran",
        )
    assert replay.status == trade_webapp.WEBAPP_DELIVERY_STATUS_ALREADY_SENT


async def _inject_market_failure_before_db() -> None:
    db = _FakeDB(fail_commit=True)
    evaluation = MarketScheduleEvaluation(
        is_open=True, reason="daily_window_open", next_transition_at=None, timezone="Asia/Tehran"
    )
    with patch("core.services.market_transition_service._acquire_market_runtime_lock", new=AsyncMock()), patch(
        "core.services.market_transition_service.get_market_runtime_state",
        new=AsyncMock(return_value=None),
    ), patch("core.services.market_transition_service.current_server", return_value="iran"):
        try:
            await apply_market_schedule_transition(db, evaluation)
        except RuntimeError as exc:
            assert "failure_before_db_commit" in str(exc)
        else:
            raise AssertionError("expected injected failure_before_db_commit")
    db.add.assert_called()
    db.commit.assert_awaited()


async def _inject_market_commit_before() -> None:
    db = _FakeDB()
    evaluation = MarketScheduleEvaluation(
        is_open=True, reason="daily_window_open", next_transition_at=None, timezone="Asia/Tehran"
    )
    cache = Mock(side_effect=RuntimeError("injected commit_before_event_side_effect"))
    with patch("core.services.market_transition_service._acquire_market_runtime_lock", new=AsyncMock()), patch(
        "core.services.market_transition_service.get_market_runtime_state",
        new=AsyncMock(return_value=None),
    ), patch("core.services.market_transition_service.current_server", return_value="iran"), patch(
        "core.services.market_transition_service.invalidate_market_runtime_view_cache",
        new=cache,
    ):
        try:
            await apply_market_schedule_transition(db, evaluation)
        except RuntimeError as exc:
            assert "commit_before_event_side_effect" in str(exc)
        else:
            raise AssertionError("expected injected commit_before_event_side_effect")
    db.commit.assert_awaited()
    cache.assert_called()


async def _inject_market_two_concurrent() -> None:
    db = _FakeDB()
    evaluation = MarketScheduleEvaluation(
        is_open=True, reason="daily_window_open", next_transition_at=None, timezone="Asia/Tehran"
    )
    holder = {"state": None}

    async def get_state(*_args, **_kwargs):
        return holder["state"]

    with patch("core.services.market_transition_service._acquire_market_runtime_lock", new=AsyncMock()), patch(
        "core.services.market_transition_service.get_market_runtime_state",
        new=get_state,
    ), patch("core.services.market_transition_service.current_server", return_value="iran"):
        first = await apply_market_schedule_transition(db, evaluation)
        holder["state"] = first.state or SimpleNamespace(is_open=True)
        second = await apply_market_schedule_transition(db, evaluation)
    assert db.add.call_count == 1
    assert first.changed is False
    assert second.changed is False


async def _inject_market_process_restart() -> None:
    db = _FakeDB()
    evaluation = MarketScheduleEvaluation(
        is_open=True, reason="daily_window_open", next_transition_at=None, timezone="Asia/Tehran"
    )
    holder = {"state": None}

    async def get_state(*_args, **_kwargs):
        return holder["state"]

    with patch("core.services.market_transition_service._acquire_market_runtime_lock", new=AsyncMock()), patch(
        "core.services.market_transition_service.get_market_runtime_state",
        new=get_state,
    ), patch("core.services.market_transition_service.current_server", return_value="iran"):
        first = await apply_market_schedule_transition(db, evaluation)
        reconstructed = first.state or SimpleNamespace(is_open=True)
        holder["state"] = reconstructed
        replay = await apply_market_schedule_transition(db, evaluation)
    assert db.add.call_count == 1
    assert replay.changed is False
    assert reconstructed.is_open is True


async def _inject_msg_failure_before_db() -> None:
    db = _FakeDB(fail_commit=True)
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


async def _inject_msg_commit_before() -> None:
    db = _FakeDB()
    fake_message = SimpleNamespace(
        id=88, reply_to_message_id=None, forwarded_from_id=None, forwarded_from_name_override=None
    )
    threading = AsyncMock(side_effect=RuntimeError("injected commit_before_event_side_effect"))
    with patch("core.services.chat_service.Message", return_value=fake_message), patch(
        "core.services.chat_service.sync_direct_message_threading",
        new=threading,
    ):
        try:
            await persist_sent_direct_message(
                db,
                sender=SimpleNamespace(id=1),
                receiver=SimpleNamespace(id=2),
                content="x",
                message_type=MessageType.TEXT,
            )
        except RuntimeError as exc:
            assert "commit_before_event_side_effect" in str(exc)
        else:
            raise AssertionError("expected injected commit_before_event_side_effect")
    db.commit.assert_awaited()
    threading.assert_awaited()


async def _inject_msg_two_concurrent() -> None:
    db = _FakeDB()
    first_message = SimpleNamespace(id=1, reply_to_message_id=None, forwarded_from_id=None, forwarded_from_name_override=None)
    second_message = SimpleNamespace(id=2, reply_to_message_id=None, forwarded_from_id=None, forwarded_from_name_override=None)
    with patch(
        "core.services.chat_service.Message",
        side_effect=[first_message, second_message],
    ), patch("core.services.chat_service.sync_direct_message_threading", new=AsyncMock()), patch(
        "core.services.chat_service.reload_direct_message",
        new=AsyncMock(side_effect=[first_message, second_message]),
    ):
        one = await persist_sent_direct_message(
            db, sender=SimpleNamespace(id=1), receiver=SimpleNamespace(id=2), content="a", message_type=MessageType.TEXT
        )
        two = await persist_sent_direct_message(
            db, sender=SimpleNamespace(id=1), receiver=SimpleNamespace(id=2), content="b", message_type=MessageType.TEXT
        )
    assert one.id != two.id


async def _inject_msg_process_restart() -> None:
    db = _FakeDB()
    fake_message = SimpleNamespace(id=77, reply_to_message_id=None, forwarded_from_id=None, forwarded_from_name_override=None)
    with patch("core.services.chat_service.Message", return_value=fake_message), patch(
        "core.services.chat_service.sync_direct_message_threading", new=AsyncMock()
    ), patch("core.services.chat_service.reload_direct_message", new=AsyncMock(return_value=fake_message)):
        persisted = await persist_sent_direct_message(
            db, sender=SimpleNamespace(id=1), receiver=SimpleNamespace(id=2), content="x", message_type=MessageType.TEXT
        )
    assert persisted.id == 77
    db.commit.assert_awaited()


async def _inject_notify_failure_before_db() -> None:
    db = _FakeDB(fail_commit=True)
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


async def _inject_notify_commit_before() -> None:
    db = _FakeDB()
    notification = SimpleNamespace(id=10, user_id=5, is_read=False)
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: notification))
    from api.routers import notifications as notify_router

    sync = AsyncMock(side_effect=RuntimeError("injected commit_before_event_side_effect"))
    with patch.object(notify_router, "sync_unread_count", new=sync):
        try:
            await notify_router.mark_notification_read(
                10, current_user=SimpleNamespace(id=5), db=db, redis=SimpleNamespace()
            )
        except RuntimeError as exc:
            assert "commit_before_event_side_effect" in str(exc)
        else:
            raise AssertionError("expected injected commit_before_event_side_effect")
    assert notification.is_read is True
    db.commit.assert_awaited()
    sync.assert_awaited()


async def _inject_notify_two_concurrent() -> None:
    db = _FakeDB()
    notification = SimpleNamespace(id=11, user_id=6, is_read=False)
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: notification))
    from api.routers import notifications as notify_router

    with patch.object(notify_router, "sync_unread_count", new=AsyncMock()) as sync:
        await notify_router.mark_notification_read(11, current_user=SimpleNamespace(id=6), db=db, redis=SimpleNamespace())
        await notify_router.mark_notification_read(11, current_user=SimpleNamespace(id=6), db=db, redis=SimpleNamespace())
    assert notification.is_read is True
    assert db.commit.await_count == 1
    assert sync.await_count == 1


async def _inject_notify_process_restart() -> None:
    db = _FakeDB()
    reconstructed = SimpleNamespace(id=12, user_id=7, is_read=True)
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: reconstructed))
    from api.routers import notifications as notify_router

    with patch.object(notify_router, "sync_unread_count", new=AsyncMock()) as sync:
        replay = await notify_router.mark_notification_read(
            12, current_user=SimpleNamespace(id=7), db=db, redis=SimpleNamespace()
        )
    assert reconstructed.is_read is True
    assert replay is None
    db.commit.assert_not_awaited()
    sync.assert_not_awaited()


async def _inject_tg_failure_before_db() -> None:
    db = FakeDeliveryDB(user=SimpleNamespace(id=7, telegram_id=7777))
    row = _outbox(attempt_count=1)
    row.text = ""
    gateway_send = AsyncMock()
    with patch.object(
        tg_service,
        "_mark_outbox",
        new=AsyncMock(side_effect=RuntimeError("injected failure_before_db_commit")),
    ):
        try:
            await tg_service.deliver_claimed_telegram_notification_outbox(
                db, row, current_server="foreign", gateway_send=gateway_send, now=row.created_at
            )
        except RuntimeError as exc:
            assert "failure_before_db_commit" in str(exc)
        else:
            raise AssertionError("expected injected failure_before_db_commit")
    gateway_send.assert_not_awaited()


async def _inject_tg_commit_before() -> None:
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
    gateway_send.assert_awaited_once()


async def _inject_tg_side_effect_before_ack() -> None:
    db = FakeDeliveryDB(user=SimpleNamespace(id=7, telegram_id=7777))
    row = _outbox(attempt_count=1)
    sent = {"count": 0}

    async def gateway_send(*args, **kwargs):
        sent["count"] += 1
        if sent["count"] == 1:
            return telegram_gateway.TelegramGatewayResult(
                ok=True, method="sendMessage", status_code=200, response_text="ok"
            )
        raise AssertionError("TG side_effect_before_ack must not send twice")

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


async def _inject_tg_two_concurrent() -> None:
    db = FakeDeliveryDB(user=SimpleNamespace(id=7, telegram_id=7777))
    same_outbox_row = _outbox(attempt_count=1)
    gateway_send = AsyncMock(
        return_value=telegram_gateway.TelegramGatewayResult(
            ok=True, method="sendMessage", status_code=200, response_text="ok"
        )
    )
    with patch.object(tg_service, "evaluate_bot_access", new=AsyncMock(return_value=BotAccessDecision(True))), patch.object(
        tg_service, "get_active_customer_relation_for_user", new=AsyncMock(return_value=None)
    ):
        first = await tg_service.deliver_claimed_telegram_notification_outbox(
            db, same_outbox_row, current_server="foreign", gateway_send=gateway_send, now=same_outbox_row.created_at
        )
        second = await tg_service.deliver_claimed_telegram_notification_outbox(
            db, same_outbox_row, current_server="foreign", gateway_send=gateway_send, now=same_outbox_row.created_at
        )
    assert first.status == tg_service.TELEGRAM_NOTIFICATION_DELIVERY_STATUS_SENT
    assert same_outbox_row.status == TelegramNotificationOutboxStatus.SENT
    assert second.status == tg_service.TELEGRAM_NOTIFICATION_DELIVERY_STATUS_ALREADY_TERMINAL
    assert gateway_send.await_count == 1


async def _inject_tg_process_restart() -> None:
    db = FakeDeliveryDB(user=SimpleNamespace(id=7, telegram_id=7777))
    reconstructed = _outbox(attempt_count=1)
    reconstructed.status = TelegramNotificationOutboxStatus.SENT
    gateway_send = AsyncMock()
    with patch.object(tg_service, "evaluate_bot_access", new=AsyncMock(return_value=BotAccessDecision(True))), patch.object(
        tg_service, "get_active_customer_relation_for_user", new=AsyncMock(return_value=None)
    ):
        replay = await tg_service.deliver_claimed_telegram_notification_outbox(
            db, reconstructed, current_server="foreign", gateway_send=gateway_send, now=reconstructed.created_at
        )
    assert replay.status == tg_service.TELEGRAM_NOTIFICATION_DELIVERY_STATUS_ALREADY_TERMINAL
    gateway_send.assert_not_awaited()


async def _inject_admin_failure_before_db() -> None:
    db = _FakeDB()
    db.flush = AsyncMock(side_effect=RuntimeError("injected failure_before_db_commit"))
    actor = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN)
    with patch.object(admin_broadcast, "get_telegram_admin_broadcast_by_creation_key", new=AsyncMock(return_value=None)), patch.object(
        admin_broadcast,
        "resolve_telegram_admin_broadcast_recipients",
        new=AsyncMock(return_value=[]),
    ):
        try:
            await admin_broadcast.create_telegram_admin_broadcast(
                db,
                actor=actor,
                content="hello",
                audience_type=TelegramAdminBroadcastAudienceType.ALL,
                creation_key="admin-fail-key-01",
            )
        except RuntimeError as exc:
            assert "failure_before_db_commit" in str(exc)
        else:
            raise AssertionError("expected injected failure_before_db_commit")


async def _inject_admin_commit_before() -> None:
    db = _FakeDB()
    receipt = SimpleNamespace(
        broadcast_id=3,
        recipient_user_id=9,
        telegram_message_id=None,
        reason=None,
        status="pending",
        attempt_count=1,
    )
    user = SimpleNamespace(id=9, telegram_id=7777)
    gateway_send = AsyncMock(
        return_value=telegram_gateway.TelegramGatewayResult(
            ok=False, method="sendMessage", status_code=500, response_text="injected commit_before_event_side_effect"
        )
    )
    with patch.object(admin_delivery, "_assert_legacy_direct_delivery_owner"), patch.object(
        admin_delivery, "_is_terminal", return_value=False
    ), patch.object(
        admin_delivery,
        "validate_telegram_admin_broadcast_record",
        return_value=(TelegramAdminBroadcastContentKind.TEXT, "hello", None),
    ), patch.object(admin_delivery, "evaluate_bot_access", new=AsyncMock(return_value=BotAccessDecision(True))), patch.object(
        admin_delivery, "finalize_telegram_admin_broadcast_status", new=AsyncMock()
    ), patch.object(admin_delivery, "_mark_receipt", new=AsyncMock()) as marked:
        db.get = AsyncMock(side_effect=[SimpleNamespace(id=3, content="hello"), user])
        result = await admin_delivery.deliver_claimed_telegram_admin_broadcast_receipt(
            db, receipt, current_server="foreign", gateway_send=gateway_send
        )
    assert result.status != admin_delivery.TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_SENT
    marked.assert_awaited()
    gateway_send.assert_awaited()


async def _inject_admin_side_effect_before_ack() -> None:
    db = _FakeDB()
    receipt = SimpleNamespace(
        broadcast_id=4,
        recipient_user_id=9,
        telegram_message_id=None,
        reason=None,
        status="pending",
        attempt_count=1,
    )
    user = SimpleNamespace(id=9, telegram_id=7777)
    sent = {"count": 0}

    async def gateway_send(*args, **kwargs):
        sent["count"] += 1
        return telegram_gateway.TelegramGatewayResult(
            ok=True, method="sendMessage", status_code=200, response_text="ok"
        )

    with patch.object(admin_delivery, "_assert_legacy_direct_delivery_owner"), patch.object(
        admin_delivery, "_is_terminal", return_value=False
    ), patch.object(
        admin_delivery,
        "validate_telegram_admin_broadcast_record",
        return_value=(TelegramAdminBroadcastContentKind.TEXT, "hello", None),
    ), patch.object(admin_delivery, "evaluate_bot_access", new=AsyncMock(return_value=BotAccessDecision(True))), patch.object(
        admin_delivery, "finalize_telegram_admin_broadcast_status", new=AsyncMock()
    ), patch.object(
        admin_delivery,
        "_mark_receipt",
        new=AsyncMock(side_effect=RuntimeError("injected side_effect_before_ack")),
    ):
        db.get = AsyncMock(side_effect=[SimpleNamespace(id=4, content="hello"), user])
        try:
            await admin_delivery.deliver_claimed_telegram_admin_broadcast_receipt(
                db, receipt, current_server="foreign", gateway_send=gateway_send
            )
        except RuntimeError as exc:
            assert "side_effect_before_ack" in str(exc)
        else:
            raise AssertionError("expected injected side_effect_before_ack")
    assert sent["count"] == 1


async def _inject_admin_two_concurrent() -> None:
    db = _FakeDB()
    actor = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN)
    existing = SimpleNamespace(broadcast=SimpleNamespace(id=8), recipients=[], receipt_count=0)
    with patch.object(
        admin_broadcast, "get_telegram_admin_broadcast_by_creation_key", new=AsyncMock(return_value=existing)
    ):
        first = await admin_broadcast.create_telegram_admin_broadcast(
            db, actor=actor, content="hello", audience_type=TelegramAdminBroadcastAudienceType.ALL, creation_key="same-admin-key-01"
        )
        second = await admin_broadcast.create_telegram_admin_broadcast(
            db, actor=actor, content="hello", audience_type=TelegramAdminBroadcastAudienceType.ALL, creation_key="same-admin-key-01"
        )
    assert first is existing
    assert second is existing
    db.add.assert_not_called()


async def _inject_admin_process_restart() -> None:
    db = _FakeDB()
    actor = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN)
    reconstructed = SimpleNamespace(broadcast=SimpleNamespace(id=8), recipients=[], receipt_count=0)
    with patch.object(
        admin_broadcast, "get_telegram_admin_broadcast_by_creation_key", new=AsyncMock(return_value=reconstructed)
    ):
        replay = await admin_broadcast.create_telegram_admin_broadcast(
            db, actor=actor, content="hello", audience_type=TelegramAdminBroadcastAudienceType.ALL, creation_key="same-admin-key-01"
        )
    assert replay is reconstructed
    db.add.assert_not_called()


async def _inject_sync_failure_before_db() -> None:
    db = _FakeDB(fail_commit=True)
    db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))
    with patch("core.db.AsyncSessionLocal", return_value=_FakeSessionContext(db)), patch(
        "core.sync_worker.coerce_positive_int", return_value=17
    ):
        try:
            await sync_worker.mark_change_log_delivered({"change_log_id": 17})
        except RuntimeError as exc:
            assert "failure_before_db_commit" in str(exc)
        else:
            raise AssertionError("expected injected failure_before_db_commit")
    db.commit.assert_awaited()


async def _inject_sync_side_effect_before_ack() -> None:
    sent = AsyncMock(return_value=SimpleNamespace(status_code=200))
    marked = AsyncMock(side_effect=RuntimeError("injected side_effect_before_ack"))
    with patch.object(sync_worker, "send_sync_item", new=sent), patch.object(
        sync_worker, "mark_change_log_delivered", new=marked
    ):
        await sync_worker.send_sync_item(
            object(),
            {"change_log_id": 18},
            "https://peer.example/api/sync/receive",
            "unit-test-key",
        )
        try:
            await sync_worker.mark_change_log_delivered({"change_log_id": 18})
        except RuntimeError as exc:
            assert "side_effect_before_ack" in str(exc)
        else:
            raise AssertionError("expected injected side_effect_before_ack")
    sent.assert_awaited()
    marked.assert_awaited()


async def _inject_sync_two_concurrent() -> None:
    db = _FakeDB()
    db.execute = AsyncMock(side_effect=[SimpleNamespace(rowcount=1), SimpleNamespace(rowcount=0)])
    with patch("core.db.AsyncSessionLocal", return_value=_FakeSessionContext(db)), patch(
        "core.sync_worker.coerce_positive_int", return_value=19
    ):
        first = await sync_worker.mark_change_log_delivered({"change_log_id": 19})
        second = await sync_worker.mark_change_log_delivered({"change_log_id": 19})
    assert first == 1
    assert second == 0


async def _inject_sync_process_restart() -> None:
    db = _FakeDB()
    reconstructed = {"change_log_id": 20}
    db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=0))
    with patch("core.db.AsyncSessionLocal", return_value=_FakeSessionContext(db)), patch(
        "core.sync_worker.coerce_positive_int", return_value=20
    ):
        replay = await sync_worker.mark_change_log_delivered(reconstructed)
    assert replay == 0


INJECTORS = {
    ("AUTH", "failure_before_db_commit"): _inject_auth_failure_before_db,
    ("AUTH", "commit_before_event_side_effect"): _inject_auth_commit_before,
    ("AUTH", "side_effect_before_ack"): _inject_auth_side_effect_before_ack,
    ("AUTH", "two_concurrent_commands"): _inject_auth_two_concurrent,
    ("AUTH", "process_restart"): _inject_auth_process_restart,
    ("IDENTITY", "failure_before_db_commit"): _inject_identity_failure_before_db,
    ("IDENTITY", "two_concurrent_commands"): _inject_identity_two_concurrent,
    ("IDENTITY", "process_restart"): _inject_identity_process_restart,
    ("OFFER", "failure_before_db_commit"): _inject_offer_failure_before_db,
    ("OFFER", "commit_before_event_side_effect"): _inject_offer_commit_before,
    ("OFFER", "side_effect_before_ack"): _inject_offer_side_effect_before_ack,
    ("OFFER", "two_concurrent_commands"): _inject_offer_two_concurrent,
    ("OFFER", "process_restart"): _inject_offer_process_restart,
    ("REQUEST", "failure_before_db_commit"): _inject_request_failure_before_db,
    ("REQUEST", "two_concurrent_commands"): _inject_request_two_concurrent,
    ("REQUEST", "process_restart"): _inject_request_process_restart,
    ("TRADE", "failure_before_db_commit"): _inject_trade_failure_before_db,
    ("TRADE", "commit_before_event_side_effect"): _inject_trade_commit_before,
    ("TRADE", "two_concurrent_commands"): _inject_trade_two_concurrent,
    ("TRADE", "process_restart"): _inject_trade_process_restart,
    ("MARKET", "failure_before_db_commit"): _inject_market_failure_before_db,
    ("MARKET", "commit_before_event_side_effect"): _inject_market_commit_before,
    ("MARKET", "two_concurrent_commands"): _inject_market_two_concurrent,
    ("MARKET", "process_restart"): _inject_market_process_restart,
    ("MSG", "failure_before_db_commit"): _inject_msg_failure_before_db,
    ("MSG", "commit_before_event_side_effect"): _inject_msg_commit_before,
    ("NOTIFY", "failure_before_db_commit"): _inject_notify_failure_before_db,
    ("NOTIFY", "commit_before_event_side_effect"): _inject_notify_commit_before,
    ("NOTIFY", "two_concurrent_commands"): _inject_notify_two_concurrent,
    ("NOTIFY", "process_restart"): _inject_notify_process_restart,
    ("TG", "failure_before_db_commit"): _inject_tg_failure_before_db,
    ("TG", "commit_before_event_side_effect"): _inject_tg_commit_before,
    ("TG", "side_effect_before_ack"): _inject_tg_side_effect_before_ack,
    ("TG", "two_concurrent_commands"): _inject_tg_two_concurrent,
    ("TG", "process_restart"): _inject_tg_process_restart,
    ("ADMIN", "failure_before_db_commit"): _inject_admin_failure_before_db,
    ("ADMIN", "commit_before_event_side_effect"): _inject_admin_commit_before,
    ("ADMIN", "side_effect_before_ack"): _inject_admin_side_effect_before_ack,
    ("ADMIN", "two_concurrent_commands"): _inject_admin_two_concurrent,
    ("ADMIN", "process_restart"): _inject_admin_process_restart,
    ("SYNC", "failure_before_db_commit"): _inject_sync_failure_before_db,
    ("SYNC", "side_effect_before_ack"): _inject_sync_side_effect_before_ack,
    ("SYNC", "two_concurrent_commands"): _inject_sync_two_concurrent,
    ("SYNC", "process_restart"): _inject_sync_process_restart,
}


class P100FailureBoundaryCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_failure_before_db_commit_injects(self):
        """AUTH failure_before_db_commit: injects handle_login_session / create_session."""
        await _inject_auth_failure_before_db()

    async def test_auth_commit_before_event_side_effect_injects(self):
        """AUTH commit_before_event_side_effect: injects handle_login_session then publish_session_revocation."""
        await _inject_auth_commit_before()

    async def test_auth_side_effect_before_ack_injects(self):
        """AUTH side_effect_before_ack: injects execute_claimed_otp_sms_delivery send-before-ack."""
        await _inject_auth_side_effect_before_ack()

    async def test_auth_two_concurrent_commands_injects(self):
        """AUTH two_concurrent_commands: injects two handle_login_session creates."""
        await _inject_auth_two_concurrent()

    async def test_auth_process_restart_injects(self):
        """AUTH process_restart: reconstructs persisted session and replays same-restart-token."""
        await _inject_auth_process_restart()

    async def test_auth_unknown_authority_schema_hash_injects(self):
        """AUTH unknown_authority_schema_hash: injects JOB_OTP_SMS_FALLBACK unknown job."""
        _inject_unknown_authority("AUTH")

    async def test_identity_failure_before_db_commit_injects(self):
        """IDENTITY failure_before_db_commit: injects transition_user_account_status then commit."""
        await _inject_identity_failure_before_db()

    async def test_identity_two_concurrent_commands_injects(self):
        """IDENTITY two_concurrent_commands: injects two transition_user_account_status calls."""
        await _inject_identity_two_concurrent()

    async def test_identity_process_restart_injects(self):
        """IDENTITY process_restart: injects transition_user_account_status replay."""
        await _inject_identity_process_restart()

    async def test_identity_unknown_authority_schema_hash_injects(self):
        """IDENTITY unknown_authority_schema_hash: injects JOB_USER_ACCOUNT_STATUS unknown job."""
        _inject_unknown_authority("IDENTITY")

    async def test_offer_failure_before_db_commit_injects(self):
        """OFFER failure_before_db_commit: injects expire_offers_authoritatively."""
        await _inject_offer_failure_before_db()

    async def test_offer_commit_before_event_side_effect_injects(self):
        """OFFER commit_before_event_side_effect: injects run_offer_channel_state_cycle retry."""
        await _inject_offer_commit_before()

    async def test_offer_side_effect_before_ack_injects(self):
        """OFFER side_effect_before_ack: injects run_offer_channel_state_cycle apply failure."""
        await _inject_offer_side_effect_before_ack()

    async def test_offer_two_concurrent_commands_injects(self):
        """OFFER two_concurrent_commands: injects run_offer_channel_state_cycle two candidates."""
        await _inject_offer_two_concurrent()

    async def test_offer_process_restart_injects(self):
        """OFFER process_restart: injects run_offer_channel_state_cycle once."""
        await _inject_offer_process_restart()

    async def test_offer_unknown_authority_schema_hash_injects(self):
        """OFFER unknown_authority_schema_hash: injects JOB_OFFER_EXPIRY unknown job."""
        _inject_unknown_authority("OFFER")

    async def test_request_failure_before_db_commit_injects(self):
        """REQUEST failure_before_db_commit: injects create_offer_request_ledger_entry."""
        await _inject_request_failure_before_db()

    async def test_request_two_concurrent_commands_injects(self):
        """REQUEST two_concurrent_commands: injects create_offer_request_ledger_entry idempotency."""
        await _inject_request_two_concurrent()

    async def test_request_process_restart_injects(self):
        """REQUEST process_restart: injects create_offer_request_ledger_entry replay."""
        await _inject_request_process_restart()

    async def test_trade_failure_before_db_commit_injects(self):
        """TRADE failure_before_db_commit: injects deliver_webapp_trade_notification commit."""
        await _inject_trade_failure_before_db()

    async def test_trade_commit_before_event_side_effect_injects(self):
        """TRADE commit_before_event_side_effect: injects deliver_webapp_trade_notification publish-after-commit."""
        await _inject_trade_commit_before()

    async def test_trade_two_concurrent_commands_injects(self):
        """TRADE two_concurrent_commands: injects deliver_webapp_trade_notification claim busy."""
        await _inject_trade_two_concurrent()

    async def test_trade_process_restart_injects(self):
        """TRADE process_restart: injects deliver_webapp_trade_notification already sent."""
        await _inject_trade_process_restart()

    async def test_trade_unknown_authority_schema_hash_injects(self):
        """TRADE unknown_authority_schema_hash: injects JOB_TRADE_WEBAPP_DELIVERY unknown job."""
        _inject_unknown_authority("TRADE")

    async def test_market_failure_before_db_commit_injects(self):
        """MARKET failure_before_db_commit: injects apply_market_schedule_transition commit."""
        await _inject_market_failure_before_db()

    async def test_market_commit_before_event_side_effect_injects(self):
        """MARKET commit_before_event_side_effect: injects apply_market_schedule_transition cache after commit."""
        await _inject_market_commit_before()

    async def test_market_two_concurrent_commands_injects(self):
        """MARKET two_concurrent_commands: injects apply_market_schedule_transition no-op."""
        await _inject_market_two_concurrent()

    async def test_market_process_restart_injects(self):
        """MARKET process_restart: injects apply_market_schedule_transition replay."""
        await _inject_market_process_restart()

    async def test_market_unknown_authority_schema_hash_injects(self):
        """MARKET unknown_authority_schema_hash: injects JOB_MARKET_SCHEDULE unknown job."""
        _inject_unknown_authority("MARKET")

    async def test_msg_failure_before_db_commit_injects(self):
        """MSG failure_before_db_commit: injects persist_sent_direct_message commit."""
        await _inject_msg_failure_before_db()

    async def test_msg_commit_before_event_side_effect_injects(self):
        """MSG commit_before_event_side_effect: injects persist_sent_direct_message then threading."""
        await _inject_msg_commit_before()

    def test_msg_two_concurrent_and_restart_are_not_evidenced(self):
        """MSG persist has no command identity; collision cells stay unresolved for P1-00b."""
        payload = _load_json("scenario-records.json")
        for variant_id in ("MSG__TWO-CONCURRENT-COMMANDS", "MSG__PROCESS-RESTART"):
            cell = next(item for item in payload["family_boundary_variants"] if item["variant_id"] == variant_id)
            self.assertEqual(cell["applicability"], "HIGH_BLOCKER")
            self.assertEqual(cell["eligibility"]["decision"], "UNRESOLVED")
            self.assertEqual(cell["named_blocker"]["resolution_stage"], "P1-00b")
        self.assertNotIn(("MSG", "two_concurrent_commands"), INJECTORS)
        self.assertNotIn(("MSG", "process_restart"), INJECTORS)

    async def test_notify_failure_before_db_commit_injects(self):
        """NOTIFY failure_before_db_commit: injects mark_notification_read commit."""
        await _inject_notify_failure_before_db()

    async def test_notify_commit_before_event_side_effect_injects(self):
        """NOTIFY commit_before_event_side_effect: injects mark_notification_read then sync_unread_count."""
        await _inject_notify_commit_before()

    async def test_notify_two_concurrent_commands_injects(self):
        """NOTIFY two_concurrent_commands: injects two mark_notification_read calls."""
        await _inject_notify_two_concurrent()

    async def test_notify_process_restart_injects(self):
        """NOTIFY process_restart: injects mark_notification_read already-read replay."""
        await _inject_notify_process_restart()

    async def test_tg_failure_before_db_commit_injects(self):
        """TG failure_before_db_commit: injects deliver_claimed_telegram_notification_outbox commit."""
        await _inject_tg_failure_before_db()

    async def test_tg_commit_before_event_side_effect_injects(self):
        """TG commit_before_event_side_effect: injects deliver_claimed_telegram_notification_outbox retry."""
        await _inject_tg_commit_before()

    async def test_tg_side_effect_before_ack_injects(self):
        """TG side_effect_before_ack: injects deliver_claimed_telegram_notification_outbox send-before-ack."""
        await _inject_tg_side_effect_before_ack()

    async def test_tg_two_concurrent_commands_injects(self):
        """TG two_concurrent_commands: injects deliver_claimed_telegram_notification_outbox busy/sent."""
        await _inject_tg_two_concurrent()

    async def test_tg_process_restart_injects(self):
        """TG process_restart: injects deliver_claimed_telegram_notification_outbox already sent."""
        await _inject_tg_process_restart()

    async def test_tg_unknown_authority_schema_hash_injects(self):
        """TG unknown_authority_schema_hash: injects JOB_TELEGRAM_DELIVERY_QUEUE unknown job."""
        _inject_unknown_authority("TG")

    async def test_admin_failure_before_db_commit_injects(self):
        """ADMIN failure_before_db_commit: injects create_telegram_admin_broadcast flush."""
        await _inject_admin_failure_before_db()

    async def test_admin_commit_before_event_side_effect_injects(self):
        """ADMIN commit_before_event_side_effect: injects deliver_claimed_telegram_admin_broadcast_receipt."""
        await _inject_admin_commit_before()

    async def test_admin_side_effect_before_ack_injects(self):
        """ADMIN side_effect_before_ack: injects deliver_claimed_telegram_admin_broadcast_receipt send-before-ack."""
        await _inject_admin_side_effect_before_ack()

    async def test_admin_two_concurrent_commands_injects(self):
        """ADMIN two_concurrent_commands: injects create_telegram_admin_broadcast creation_key."""
        await _inject_admin_two_concurrent()

    async def test_admin_process_restart_injects(self):
        """ADMIN process_restart: injects create_telegram_admin_broadcast replay."""
        await _inject_admin_process_restart()

    async def test_admin_unknown_authority_schema_hash_injects(self):
        """ADMIN unknown_authority_schema_hash: injects JOB_TELEGRAM_ADMIN_BROADCAST_DELIVERY unknown job."""
        _inject_unknown_authority("ADMIN")

    async def test_sync_failure_before_db_commit_injects(self):
        """SYNC failure_before_db_commit: injects mark_change_log_delivered commit."""
        await _inject_sync_failure_before_db()

    async def test_sync_side_effect_before_ack_injects(self):
        """SYNC side_effect_before_ack: injects send_sync_item then mark_change_log_delivered."""
        await _inject_sync_side_effect_before_ack()

    async def test_sync_two_concurrent_commands_injects(self):
        """SYNC two_concurrent_commands: injects two mark_change_log_delivered calls."""
        await _inject_sync_two_concurrent()

    async def test_sync_process_restart_injects(self):
        """SYNC process_restart: injects mark_change_log_delivered already synced."""
        await _inject_sync_process_restart()

    async def test_sync_unknown_authority_schema_hash_injects(self):
        """SYNC unknown_authority_schema_hash: injects JOB_SYNC_WORKER unknown job."""
        _inject_unknown_authority("SYNC")

    async def test_ops_unknown_authority_schema_hash_injects(self):
        """OPS unknown_authority_schema_hash: injects JOB_CONNECTIVITY_MONITOR unknown job."""
        _inject_unknown_authority("OPS")

    def test_eligible_cells_fail_when_cited_node_uses_foreign_helper_as_sole_proof(self):
        payload = _load_json("scenario-records.json")
        for cell in payload["family_boundary_variants"]:
            if cell["applicability"] != "ELIGIBLE":
                continue
            family = cell["family"]
            for case in cell["evidence_cases"]:
                assert_cited_node_uses_family_path(family, case["node_id"])
        fake_node = (
            "tests/test_p1_00_characterization_failure_boundaries.py"
            "::P100FailureBoundaryCharacterizationTests.test_tg_side_effect_before_ack_injects"
        )
        with self.assertRaises(AssertionError):
            assert_cited_node_uses_family_path("AUTH", fake_node)
        with self.assertRaises(AssertionError):
            assert_cited_node_uses_family_path("MARKET", fake_node)

    def test_eligible_concurrency_and_restart_use_same_identity(self):
        payload = _load_json("scenario-records.json")
        for cell in payload["family_boundary_variants"]:
            if cell["applicability"] != "ELIGIBLE":
                continue
            if cell["failure_boundary"] not in {"two_concurrent_commands", "process_restart"}:
                continue
            refs = cell.get("evidence_refs") or []
            if any("test_p1_00_characterization_concurrent_restart.py" in ref for ref in refs):
                continue
            injector = INJECTORS[(cell["family"], cell["failure_boundary"])]
            src = inspect.getsource(injector)
            tree = ast.parse(src)
            for name in {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}:
                helper = globals().get(name)
                if callable(helper) and name.startswith("_"):
                    src += "\n" + inspect.getsource(helper)
            if cell["failure_boundary"] == "two_concurrent_commands":
                self.assertNotIn('content="a"', src, cell["variant_id"])
                self.assertNotIn('"token-a"', src, cell["variant_id"])
                self.assertFalse(
                    "ofr_41" in src and "ofr_42" in src,
                    cell["variant_id"],
                )
                self.assertFalse(
                    "first = _outbox" in src and "second = _outbox" in src,
                    cell["variant_id"],
                )
                self.assertTrue(
                    any(
                        token in src
                        for token in (
                            "same-user",
                            "same-key",
                            "same-admin-key",
                            "ofr_same",
                            "trade_number=3",
                            "change_log_id\": 19",
                            "notification_id",
                            "id=11",
                            "holder[",
                            "durable",
                            "same_user",
                            "same_outbox_row",
                        )
                    )
                    or "same" in src
                    or "existing" in src,
                    cell["variant_id"],
                )
                self.assertTrue(
                    any(
                        token in src
                        for token in (
                            "replaced_session_count",
                            "is_active is False",
                            "duplicate_replay",
                            "skipped_locked",
                            "CLAIM_BUSY",
                            "ALREADY_SENT",
                            "ALREADY_TERMINAL",
                            "await_count == 1",
                            "call_count == 1",
                            "changed is False",
                            "rowcount",
                            "db.add.assert_not_called",
                            "notify.await_count == 1",
                            "commit.await_count == 1",
                        )
                    ),
                    cell["variant_id"],
                )
            else:
                concurrent = INJECTORS.get((cell["family"], "two_concurrent_commands"))
                self.assertIsNot(injector, concurrent, cell["variant_id"])
                self.assertNotIn("_auth_same_identity_session_slot", src, cell["variant_id"])
                if concurrent is not None:
                    self.assertNotIn(concurrent.__name__, inspect.getsource(injector), cell["variant_id"])
                    concurrent_src = inspect.getsource(concurrent)
                    conc_tree = ast.parse(concurrent_src)
                    for name in {node.id for node in ast.walk(conc_tree) if isinstance(node, ast.Name)}:
                        helper = globals().get(name)
                        if callable(helper) and name.startswith("_"):
                            concurrent_src += "\n" + inspect.getsource(helper)
                    self.assertNotIn("reconstructed", concurrent_src, cell["variant_id"])
                self.assertIn("reconstructed", src, cell["variant_id"])
                self.assertNotIn("same-user-token-b", src, cell["variant_id"])
                if cell["family"] == "AUTH":
                    self.assertIn("suspended_refresh_token", src)
                    self.assertIn("same-restart-token", src)
                    self.assertNotIn("same-user-token-a", src)
                if cell["family"] == "NOTIFY":
                    self.assertIn("is_read=True", src)
                    self.assertNotIn("is_read=False", src)
                    self.assertEqual(src.count("mark_notification_read"), 1, cell["variant_id"])


if __name__ == "__main__":
    unittest.main()
