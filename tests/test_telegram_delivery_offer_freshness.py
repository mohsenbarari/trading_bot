import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core import telegram_delivery_offer_freshness as freshness
from core.enums import SettlementType
from core.services.telegram_delivery_queue_service import (
    canonical_telegram_delivery_payload,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDestinationClass,
    TelegramFreshnessOutcome,
)
from models.offer import OfferStatus, OfferType
from models.offer_publication_state import OfferPublicationSurface


NOW = datetime(2026, 7, 17, 4, 30, tzinfo=timezone.utc)
CHANNEL_ID = -100123456


def make_offer(**overrides):
    data = {
        "offer_public_id": "ofr_freshness_1",
        "id": 101,
        "version_id": 3,
        "status": OfferStatus.ACTIVE,
        "offer_type": OfferType.SELL,
        "settlement_type": SettlementType.CASH,
        "commodity": SimpleNamespace(name="سکه"),
        "quantity": 10,
        "remaining_quantity": 10,
        "price": 72_500_000,
        "notes": "تحویل امروز",
        "is_wholesale": True,
        "lot_sizes": None,
        "channel_message_id": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_state(**overrides):
    data = {
        "surface": OfferPublicationSurface.TELEGRAM_CHANNEL,
        "publisher_bot_identity": "primary",
        "telegram_chat_id": CHANNEL_ID,
        "telegram_message_id": 700,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_job(action=TelegramDeliveryAction.OFFER_PUBLISH, **overrides):
    is_publish = action == TelegramDeliveryAction.OFFER_PUBLISH
    status = {
        freshness.TelegramDeliveryAction.TRADED_OFFER_EDIT: OfferStatus.COMPLETED,
        freshness.TelegramDeliveryAction.EXPIRED_OFFER_EDIT: OfferStatus.EXPIRED,
        freshness.TelegramDeliveryAction.CANCELLED_OFFER_EDIT: OfferStatus.CANCELLED,
    }.get(action, OfferStatus.ACTIVE)
    payload = freshness._expected_payload(
        make_offer(status=status),
        action=action,
        expected_channel_id=CHANNEL_ID,
        message_id=None if is_publish else 700,
    )
    _, payload_hash = canonical_telegram_delivery_payload(payload)
    data = {
        "action_kind": action,
        "destination_class": TelegramDestinationClass.CHANNEL,
        "destination_key": freshness.telegram_channel_destination_key(CHANNEL_ID),
        "payload": payload,
        "payload_hash": payload_hash,
        "method": "sendMessage" if is_publish else "editMessageText",
        "bot_identity": "primary" if is_publish else "channel_editor",
        "source_natural_id": "ofr_freshness_1",
        "source_version": 3,
        "freshness_deadline_at": NOW + timedelta(minutes=2),
    }
    data.update(overrides)
    if "payload" in overrides and "payload_hash" not in overrides:
        try:
            _, data["payload_hash"] = canonical_telegram_delivery_payload(
                data["payload"]
            )
        except Exception:
            data["payload_hash"] = "invalid"
    return SimpleNamespace(**data)


class TelegramDeliveryOfferFreshnessTests(unittest.IsolatedAsyncioTestCase):
    async def decide(self, job, *, offer=None, state=None):
        with patch.object(
            freshness,
            "_load_offer",
            new=AsyncMock(return_value=offer),
        ), patch.object(
            freshness,
            "_load_publication_state",
            new=AsyncMock(return_value=state),
        ):
            return await freshness.validate_offer_telegram_delivery_freshness(
                object(),
                job,
                NOW,
                expected_channel_id=CHANNEL_ID,
            )

    def test_validator_requires_explicit_nonzero_integer_channel(self):
        for value in (None, True, 0, "-100123"):
            with self.subTest(value=value):
                with self.assertRaises(
                    freshness.TelegramOfferFreshnessConfigurationError
                ):
                    freshness.OfferTelegramDeliveryFreshnessValidator(value)

    async def test_publish_current_active_offer_is_sendable(self):
        decision = await self.decide(
            make_job(),
            offer=make_offer(),
            state=make_state(telegram_message_id=None),
        )

        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SEND)
        self.assertEqual(decision.reason, "offer_freshness_current")

    async def test_static_route_and_payload_mismatches_are_quarantined(self):
        invalid_jobs = (
            make_job(destination_class=TelegramDestinationClass.PRIVATE),
            make_job(destination_key="channel:-100999"),
            make_job(payload={"chat_id": -100999, "text": "offer"}),
            make_job(payload={"chat_id": CHANNEL_ID, "text": ""}),
            make_job(method="editMessageText"),
            make_job(bot_identity="channel_editor"),
            make_job(
                payload={
                    "chat_id": CHANNEL_ID,
                    "text": "offer",
                    "message_id": 700,
                }
            ),
        )

        for job in invalid_jobs:
            with self.subTest(job=job):
                decision = await self.decide(job)
                self.assertEqual(
                    decision.outcome,
                    TelegramFreshnessOutcome.QUARANTINED,
                )

    async def test_payload_must_match_hash_and_authoritative_renderer(self):
        hash_mismatch = make_job(payload_hash="0" * 64)
        stale_payload = dict(make_job().payload)
        stale_payload["text"] = "stale offer snapshot"

        decisions = (
            await self.decide(hash_mismatch, offer=make_offer()),
            await self.decide(
                make_job(payload=stale_payload),
                offer=make_offer(),
            ),
        )

        self.assertEqual(
            decisions[0].reason,
            "offer_freshness_payload_hash_mismatch",
        )
        self.assertEqual(
            decisions[1].reason,
            "offer_freshness_payload_not_authoritative",
        )
        self.assertTrue(
            all(
                decision.outcome == TelegramFreshnessOutcome.QUARANTINED
                for decision in decisions
            )
        )

    async def test_every_current_offer_action_accepts_only_its_canonical_payload(self):
        statuses = {
            TelegramDeliveryAction.OFFER_PUBLISH: OfferStatus.ACTIVE,
            TelegramDeliveryAction.PARTIAL_OFFER_EDIT: OfferStatus.ACTIVE,
            TelegramDeliveryAction.OTHER_ACTIVE_OFFER_EDIT: OfferStatus.ACTIVE,
            TelegramDeliveryAction.RECONCILIATION_EDIT: OfferStatus.ACTIVE,
            TelegramDeliveryAction.TRADED_OFFER_EDIT: OfferStatus.COMPLETED,
            TelegramDeliveryAction.EXPIRED_OFFER_EDIT: OfferStatus.EXPIRED,
            TelegramDeliveryAction.CANCELLED_OFFER_EDIT: OfferStatus.CANCELLED,
        }

        for action, status in statuses.items():
            with self.subTest(action=action):
                decision = await self.decide(
                    make_job(action),
                    offer=make_offer(status=status),
                    state=(
                        make_state(telegram_message_id=None)
                        if action == TelegramDeliveryAction.OFFER_PUBLISH
                        else make_state()
                    ),
                )
                self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SEND)

    async def test_publish_terminal_missing_or_expired_offer_is_superseded(self):
        cases = (
            (None, make_job(), "offer_freshness_source_missing"),
            (
                make_offer(status=OfferStatus.COMPLETED),
                make_job(),
                "offer_freshness_not_publishable",
            ),
            (
                make_offer(),
                make_job(freshness_deadline_at=NOW),
                "offer_freshness_publish_deadline_passed",
            ),
        )

        for offer, job, reason in cases:
            with self.subTest(reason=reason):
                decision = await self.decide(job, offer=offer)
                self.assertEqual(
                    decision.outcome,
                    TelegramFreshnessOutcome.SUPERSEDED,
                )
                self.assertEqual(decision.reason, reason)

    async def test_publish_version_drift_reclassifies_or_waits(self):
        older = await self.decide(
            make_job(source_version=2),
            offer=make_offer(version_id=3),
        )
        ahead = await self.decide(
            make_job(source_version=4),
            offer=make_offer(version_id=3),
        )

        self.assertEqual(older.outcome, TelegramFreshnessOutcome.RECLASSIFY)
        self.assertEqual(
            older.replacement_action,
            TelegramDeliveryAction.OFFER_PUBLISH,
        )
        self.assertEqual(ahead.outcome, TelegramFreshnessOutcome.WAIT_DEPENDENCY)
        invalid = await self.decide(
            make_job(source_version=0),
            offer=make_offer(version_id=3),
        )
        self.assertEqual(invalid.outcome, TelegramFreshnessOutcome.QUARANTINED)

    async def test_already_published_offer_is_noop_only_with_canonical_identity(self):
        sent = await self.decide(
            make_job(),
            offer=make_offer(),
            state=make_state(),
        )
        wrong_channel = await self.decide(
            make_job(),
            offer=make_offer(),
            state=make_state(telegram_chat_id=-100999),
        )
        wrong_publisher = await self.decide(
            make_job(),
            offer=make_offer(),
            state=make_state(publisher_bot_identity="channel_editor"),
        )

        self.assertEqual(sent.outcome, TelegramFreshnessOutcome.SENT_NOOP)
        self.assertEqual(
            wrong_channel.outcome,
            TelegramFreshnessOutcome.QUARANTINED,
        )
        self.assertEqual(
            wrong_publisher.outcome,
            TelegramFreshnessOutcome.QUARANTINED,
        )

    async def test_pending_publication_identity_fragments_fail_closed(self):
        wrong_pending_channel = await self.decide(
            make_job(),
            offer=make_offer(),
            state=make_state(
                telegram_message_id=None,
                telegram_chat_id=-100999,
            ),
        )
        orphan_resource = await self.decide(
            make_job(),
            offer=make_offer(),
            state=make_state(
                telegram_message_id=None,
                surface_resource_id="700",
            ),
        )
        invalid_message = await self.decide(
            make_job(),
            offer=make_offer(),
            state=make_state(
                telegram_message_id=0,
                surface_resource_id=None,
            ),
        )
        legacy_only = await self.decide(
            make_job(),
            offer=make_offer(channel_message_id=700),
            state=None,
        )

        self.assertEqual(
            wrong_pending_channel.outcome,
            TelegramFreshnessOutcome.QUARANTINED,
        )
        self.assertEqual(
            orphan_resource.outcome,
            TelegramFreshnessOutcome.QUARANTINED,
        )
        self.assertEqual(
            invalid_message.outcome,
            TelegramFreshnessOutcome.QUARANTINED,
        )
        self.assertEqual(
            legacy_only.outcome,
            TelegramFreshnessOutcome.WAIT_DEPENDENCY,
        )

    async def test_partial_edit_reclassifies_every_terminal_offer_state(self):
        expected = {
            OfferStatus.COMPLETED: TelegramDeliveryAction.TRADED_OFFER_EDIT,
            OfferStatus.EXPIRED: TelegramDeliveryAction.EXPIRED_OFFER_EDIT,
            OfferStatus.CANCELLED: TelegramDeliveryAction.CANCELLED_OFFER_EDIT,
        }

        for status, replacement in expected.items():
            with self.subTest(status=status):
                decision = await self.decide(
                    make_job(TelegramDeliveryAction.PARTIAL_OFFER_EDIT),
                    offer=make_offer(status=status),
                    state=make_state(),
                )
                self.assertEqual(
                    decision.outcome,
                    TelegramFreshnessOutcome.RECLASSIFY,
                )
                self.assertEqual(decision.replacement_action, replacement)

    async def test_partial_edit_waits_for_active_publication_but_noops_terminal(self):
        active = await self.decide(
            make_job(TelegramDeliveryAction.PARTIAL_OFFER_EDIT),
            offer=make_offer(),
            state=None,
        )
        terminal = await self.decide(
            make_job(TelegramDeliveryAction.PARTIAL_OFFER_EDIT),
            offer=make_offer(status=OfferStatus.EXPIRED),
            state=None,
        )

        self.assertEqual(active.outcome, TelegramFreshnessOutcome.WAIT_DEPENDENCY)
        self.assertEqual(terminal.outcome, TelegramFreshnessOutcome.SENT_NOOP)

    async def test_terminal_edit_requires_matching_state_and_empty_keyboard(self):
        current = await self.decide(
            make_job(TelegramDeliveryAction.EXPIRED_OFFER_EDIT),
            offer=make_offer(status=OfferStatus.EXPIRED),
            state=make_state(),
        )
        changed = await self.decide(
            make_job(TelegramDeliveryAction.EXPIRED_OFFER_EDIT),
            offer=make_offer(status=OfferStatus.COMPLETED),
            state=make_state(),
        )
        active = await self.decide(
            make_job(TelegramDeliveryAction.EXPIRED_OFFER_EDIT),
            offer=make_offer(status=OfferStatus.ACTIVE),
            state=make_state(),
        )
        bad_keyboard = make_job(TelegramDeliveryAction.EXPIRED_OFFER_EDIT)
        bad_keyboard.payload["reply_markup"] = {
            "inline_keyboard": [[{"text": "stale"}]]
        }
        malformed = await self.decide(bad_keyboard)

        self.assertEqual(current.outcome, TelegramFreshnessOutcome.SEND)
        self.assertEqual(changed.outcome, TelegramFreshnessOutcome.RECLASSIFY)
        self.assertEqual(
            changed.replacement_action,
            TelegramDeliveryAction.TRADED_OFFER_EDIT,
        )
        self.assertEqual(active.outcome, TelegramFreshnessOutcome.SUPERSEDED)
        self.assertEqual(
            malformed.outcome,
            TelegramFreshnessOutcome.QUARANTINED,
        )

    async def test_edit_identity_mismatch_and_invalid_publisher_are_quarantined(self):
        wrong_message = make_job(TelegramDeliveryAction.PARTIAL_OFFER_EDIT)
        wrong_message.payload["message_id"] = 701
        decisions = (
            await self.decide(
                wrong_message,
                offer=make_offer(),
                state=make_state(),
            ),
            await self.decide(
                make_job(TelegramDeliveryAction.PARTIAL_OFFER_EDIT),
                offer=make_offer(),
                state=make_state(publisher_bot_identity=None),
            ),
        )

        for decision in decisions:
            self.assertEqual(
                decision.outcome,
                TelegramFreshnessOutcome.QUARANTINED,
            )

    async def test_terminal_without_publication_is_noop_and_stale_version_reclassifies(self):
        missing = await self.decide(
            make_job(TelegramDeliveryAction.CANCELLED_OFFER_EDIT),
            offer=make_offer(status=OfferStatus.CANCELLED),
            state=None,
        )
        stale = await self.decide(
            make_job(
                TelegramDeliveryAction.CANCELLED_OFFER_EDIT,
                source_version=2,
            ),
            offer=make_offer(version_id=3, status=OfferStatus.CANCELLED),
            state=make_state(),
        )

        self.assertEqual(missing.outcome, TelegramFreshnessOutcome.SENT_NOOP)
        self.assertEqual(stale.outcome, TelegramFreshnessOutcome.RECLASSIFY)
        self.assertEqual(
            stale.replacement_action,
            TelegramDeliveryAction.CANCELLED_OFFER_EDIT,
        )

    async def test_unsupported_action_and_source_with_orphan_publication_quarantine(self):
        unsupported = await self.decide(
            make_job(TelegramDeliveryAction.TRADE_RESULT)
        )
        orphan = await self.decide(
            make_job(),
            offer=None,
            state=make_state(),
        )
        orphan_pending = await self.decide(
            make_job(),
            offer=None,
            state=make_state(
                telegram_chat_id=None,
                telegram_message_id=None,
            ),
        )

        self.assertEqual(
            unsupported.outcome,
            TelegramFreshnessOutcome.QUARANTINED,
        )
        self.assertEqual(orphan.outcome, TelegramFreshnessOutcome.QUARANTINED)
        self.assertEqual(
            orphan_pending.outcome,
            TelegramFreshnessOutcome.QUARANTINED,
        )

    async def test_callable_wrapper_delegates_with_bound_channel(self):
        validator = freshness.OfferTelegramDeliveryFreshnessValidator(CHANNEL_ID)
        with patch.object(
            freshness,
            "validate_offer_telegram_delivery_freshness",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    outcome=TelegramFreshnessOutcome.SEND
                )
            ),
        ) as validate:
            result = await validator(object(), make_job(), NOW)

        self.assertEqual(result.outcome, TelegramFreshnessOutcome.SEND)
        self.assertEqual(validate.await_args.kwargs["expected_channel_id"], CHANNEL_ID)


if __name__ == "__main__":
    unittest.main()
