import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.repeat_offer import BotRepeatOfferCandidate
from core.services.bot_access_policy import BotAccessDecision
from core.services.telegram_delivery_queue_service import (
    canonical_telegram_delivery_payload,
)
from core.services.telegram_notification_outbox_service import (
    TELEGRAM_NOTIFICATION_SOURCE_OFFER_REPEAT_RESPONSE,
    TELEGRAM_OFFER_REPEAT_MENU_REFRESH_TEXT,
    TELEGRAM_OFFER_REPEAT_RESPONSE_KIND_MENU_REFRESH,
    telegram_notification_dedupe_key,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDestinationClass,
    TelegramFeederKind,
    TelegramFreshnessOutcome,
)
from core.telegram_delivery_repeat_offer_freshness import (
    REPEAT_OFFER_RESPONSE_TEMPLATE_VERSION,
    RepeatOfferResponseSnapshot,
    build_repeat_offer_response_payload,
    build_repeat_offer_response_snapshot,
    telegram_repeat_offer_response_destination_key,
    telegram_repeat_offer_response_source_natural_id,
    validate_repeat_offer_response_telegram_delivery_freshness,
)
from models.telegram_notification_outbox import TelegramNotificationOutboxStatus


NOW = datetime(2026, 7, 18, 9, 0, tzinfo=timezone.utc)


class FakeScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeDB:
    def __init__(self, *, outbox=None, user=None):
        self.outbox = outbox
        self.user = user

    async def execute(self, _stmt):
        return FakeScalarResult(self.outbox)

    async def get(self, _model, _record_id):
        return self.user


def _user(**overrides):
    values = {
        "id": 7,
        "telegram_id": 7007,
        "sync_version": 3,
        "role": "user",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _outbox(**overrides):
    source_id = overrides.pop("source_id", "repeat-success:bot-repeat:abc")
    recipient_user_id = overrides.pop("recipient_user_id", 7)
    values = {
        "id": 91,
        "dedupe_key": telegram_notification_dedupe_key(
            source_type=TELEGRAM_NOTIFICATION_SOURCE_OFFER_REPEAT_RESPONSE,
            source_id=source_id,
            recipient_user_id=recipient_user_id,
        ),
        "source_type": TELEGRAM_NOTIFICATION_SOURCE_OFFER_REPEAT_RESPONSE,
        "source_id": source_id,
        "recipient_user_id": recipient_user_id,
        "telegram_id_at_enqueue": 7007,
        "text": TELEGRAM_OFFER_REPEAT_MENU_REFRESH_TEXT,
        "parse_mode": None,
        "status": TelegramNotificationOutboxStatus.PENDING,
        "telegram_message_id": None,
        "queue_job_id": 811,
        "queue_handed_off_at": NOW,
        "extra_payload": {
            "response_kind": TELEGRAM_OFFER_REPEAT_RESPONSE_KIND_MENU_REFRESH
        },
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _payload(**overrides):
    values = {
        "chat_id": 7007,
        "text": TELEGRAM_OFFER_REPEAT_MENU_REFRESH_TEXT,
        "parse_mode": None,
        "reply_markup": {
            "keyboard": [[{"text": "🔁 ف ن سکه 10 عدد 100000"}]],
            "resize_keyboard": True,
            "is_persistent": True,
        },
    }
    values.update(overrides)
    return values


def _job(*, outbox=None, payload=None, **overrides):
    outbox = outbox or _outbox()
    payload = payload or _payload()
    _normalized, payload_hash = canonical_telegram_delivery_payload(payload)
    values = {
        "id": 811,
        "action_kind": TelegramDeliveryAction.OFFER_REPEAT_RESPONSE,
        "feeder_kind": TelegramFeederKind.OFFER_CONTROL,
        "destination_class": TelegramDestinationClass.PRIVATE,
        "method": "sendMessage",
        "bot_identity": "primary",
        "template_version": REPEAT_OFFER_RESPONSE_TEMPLATE_VERSION,
        "delivery_deadline_at": None,
        "freshness_deadline_at": None,
        "campaign_id": None,
        "run_id": None,
        "source_natural_id": telegram_repeat_offer_response_source_natural_id(
            outbox
        ),
        "source_version": 3,
        "destination_key": telegram_repeat_offer_response_destination_key(7),
        "payload": payload,
        "payload_hash": payload_hash,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TelegramDeliveryRepeatOfferFreshnessTests(unittest.IsolatedAsyncioTestCase):
    async def _validate(self, *, outbox=None, user=None, job=None, current_payload=None):
        outbox = outbox or _outbox()
        user = user or _user()
        job = job or _job(outbox=outbox)
        current_payload = current_payload or _payload()
        with patch(
            "core.telegram_delivery_repeat_offer_freshness.evaluate_bot_access",
            new=AsyncMock(return_value=BotAccessDecision(True)),
        ), patch(
            "core.telegram_delivery_repeat_offer_freshness."
            "build_repeat_offer_response_snapshot",
            new=AsyncMock(
                return_value=RepeatOfferResponseSnapshot(
                    payload=current_payload,
                    source_version=3,
                )
            ),
        ):
            return await validate_repeat_offer_response_telegram_delivery_freshness(
                FakeDB(outbox=outbox, user=user),
                job,
                NOW,
            )

    async def test_current_bound_response_is_sendable(self):
        decision = await self._validate()
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SEND)

    async def test_keyboard_change_reclassifies_same_response(self):
        changed = _payload(
            reply_markup={
                "keyboard": [[{"text": "🔁 خ ن ربع 20 عدد 200000"}]],
                "resize_keyboard": True,
                "is_persistent": True,
            }
        )
        decision = await self._validate(current_payload=changed)
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.RECLASSIFY)
        self.assertEqual(
            decision.replacement_action,
            TelegramDeliveryAction.OFFER_REPEAT_RESPONSE,
        )

    async def test_unlinked_recipient_supersedes_response(self):
        decision = await self._validate(user=_user(telegram_id=None))
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SUPERSEDED)

    async def test_terminal_sent_outbox_is_idempotent_noop(self):
        outbox = _outbox(
            status=TelegramNotificationOutboxStatus.SENT,
            telegram_message_id=55,
        )
        decision = await self._validate(outbox=outbox, job=_job(outbox=outbox))
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SENT_NOOP)

    async def test_wrong_feeder_is_quarantined(self):
        decision = await self._validate(
            job=_job(feeder_kind=TelegramFeederKind.ADMIN_SYSTEM)
        )
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.QUARANTINED)

    async def test_payload_builder_serializes_current_repeat_keyboard(self):
        candidate = BotRepeatOfferCandidate(
            source_offer_id=41,
            source_offer_public_id="ofr_source_41",
            draft_text="ف ن سکه 10 عدد 100000",
            button_text="🔁 ف ن سکه 10 عدد 100000",
        )
        with patch(
            "core.telegram_delivery_repeat_offer_freshness."
            "load_latest_bot_repeat_offer_candidate",
            new=AsyncMock(return_value=candidate),
        ):
            payload = await build_repeat_offer_response_payload(
                FakeDB(),
                _outbox(),
                _user(),
            )

        self.assertEqual(payload["chat_id"], 7007)
        self.assertEqual(
            payload["reply_markup"]["keyboard"][0][0]["text"],
            candidate.button_text,
        )

    async def test_candidate_identity_changes_snapshot_version_even_with_same_button(self):
        first = BotRepeatOfferCandidate(
            source_offer_id=41,
            source_offer_public_id="ofr_source_41",
            draft_text="ف ن سکه 10 عدد 100000",
            button_text="🔁 ف ن سکه 10 عدد 100000",
        )
        second = BotRepeatOfferCandidate(
            source_offer_id=42,
            source_offer_public_id="ofr_source_42",
            draft_text=first.draft_text,
            button_text=first.button_text,
        )
        loader = AsyncMock(side_effect=[first, second])
        with patch(
            "core.telegram_delivery_repeat_offer_freshness."
            "load_latest_bot_repeat_offer_candidate",
            new=loader,
        ):
            first_snapshot = await build_repeat_offer_response_snapshot(
                FakeDB(),
                _outbox(),
                _user(),
            )
            second_snapshot = await build_repeat_offer_response_snapshot(
                FakeDB(),
                _outbox(),
                _user(),
            )

        self.assertEqual(first_snapshot.payload, second_snapshot.payload)
        self.assertNotEqual(
            first_snapshot.source_version,
            second_snapshot.source_version,
        )


if __name__ == "__main__":
    unittest.main()
