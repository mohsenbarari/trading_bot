import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.services import telegram_interaction_outbox_service as interaction_service
from core.services.telegram_notification_outbox_queue_feedback import (
    TelegramNotificationOutboxQueueFeedbackError,
    _apply_interaction_anchor_result,
)
from core.services.telegram_notification_outbox_service import (
    TelegramNotificationEnqueueResult,
    TelegramNotificationRecipient,
    telegram_notification_dedupe_key,
)
from core.telegram_delivery_interaction_result_contract import (
    TelegramInteractionAnchorEffect,
    TelegramInteractionResultRequirement,
    build_interaction_result_contract,
    serialize_interaction_result_contract,
)
from core.telegram_delivery_notification_action_freshness import (
    _validate_interaction_anchor_freshness,
    _validate_source_contract,
    build_telegram_notification_action_snapshot,
    telegram_notification_action_source_natural_id,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDeliveryState,
    TelegramDestinationClass,
    TelegramFreshnessOutcome,
)
from core.utils import utc_now
from models.telegram_interaction_anchor_state import TelegramInteractionAnchorState
from models.telegram_notification_outbox import TelegramNotificationOutboxStatus


class ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


def recipient():
    return TelegramNotificationRecipient(user_id=7, telegram_id=7007)


def persistent_menu():
    return {"keyboard": [[{"text": "منوی اصلی"}]], "resize_keyboard": True}


def anchor_contract(*, generation=1, logical_key="user:7:panel:1"):
    return build_interaction_result_contract(
        logical_message_key=logical_key,
        method="sendMessage",
        destination_class=TelegramDestinationClass.PRIVATE,
        result_requirement=TelegramInteractionResultRequirement.CAPTURE_MESSAGE_ID,
        anchor_effect=TelegramInteractionAnchorEffect.SET_CURRENT,
        anchor_generation=generation,
        authenticated=True,
        persistent_menu_present=True,
    )


def action_outbox(*, contract, outbox_id=10):
    source_id = "panel:7:1"
    return SimpleNamespace(
        id=outbox_id,
        dedupe_key=telegram_notification_dedupe_key(
            source_type="queue_action:general_immediate",
            source_id=source_id,
            recipient_user_id=7,
        ),
        source_type="queue_action:general_immediate",
        source_id=source_id,
        recipient_user_id=7,
        telegram_id_at_enqueue=7007,
        text="پنل کاربر",
        parse_mode=None,
        status=TelegramNotificationOutboxStatus.PENDING,
        extra_payload={
            "interaction_result": serialize_interaction_result_contract(contract),
            "queue_action": "general_immediate",
            "reply_markup": persistent_menu(),
            "user_sync_version": 4,
        },
    )


class TelegramInteractionOutboxServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_anchor_interaction_reuses_notification_outbox_without_anchor_state(self):
        outbox = SimpleNamespace(id=11)
        db = SimpleNamespace()
        with (
            patch.object(
                interaction_service,
                "_find_existing",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                interaction_service,
                "enqueue_telegram_action_notification_once",
                new=AsyncMock(
                    return_value=TelegramNotificationEnqueueResult(outbox, True)
                ),
            ) as enqueue,
        ):
            result = await interaction_service.enqueue_private_interaction_once(
                db,
                current_server="foreign",
                recipient=recipient(),
                action=TelegramDeliveryAction.GENERAL_IMMEDIATE,
                source_id="panel:7:read-only",
                logical_message_key="user:7:notice:1",
                text="پیام عادی",
                user_sync_version=4,
            )

        self.assertTrue(result.notification.created)
        self.assertIsNone(result.anchor_state)
        self.assertEqual(
            result.contract.anchor_effect,
            TelegramInteractionAnchorEffect.PRESERVE_CURRENT,
        )
        self.assertIs(
            enqueue.await_args.kwargs["interaction_result"],
            result.contract,
        )

    async def test_anchor_enqueue_allocates_generation_and_binds_outbox_atomically(self):
        outbox = SimpleNamespace(id=12)
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[ScalarResult(None), ScalarResult(None)]),
            add=Mock(),
            flush=AsyncMock(),
        )
        with (
            patch.object(
                interaction_service,
                "_find_existing",
                new=AsyncMock(side_effect=[None, None]),
            ),
            patch.object(
                interaction_service,
                "enqueue_telegram_action_notification_once",
                new=AsyncMock(
                    return_value=TelegramNotificationEnqueueResult(outbox, True)
                ),
            ),
        ):
            result = await interaction_service.enqueue_private_interaction_once(
                db,
                current_server="foreign",
                recipient=recipient(),
                action=TelegramDeliveryAction.GENERAL_IMMEDIATE,
                source_id="panel:7:anchor:1",
                logical_message_key="user:7:panel:1",
                text="پنل کاربر",
                user_sync_version=4,
                reply_markup=persistent_menu(),
                result_requirement=(
                    TelegramInteractionResultRequirement.CAPTURE_MESSAGE_ID
                ),
                anchor_effect=TelegramInteractionAnchorEffect.SET_CURRENT,
            )

        self.assertEqual(result.contract.anchor_generation, 1)
        self.assertEqual(result.anchor_state.desired_outbox_id, 12)
        self.assertEqual(result.anchor_state.desired_generation, 1)
        db.add.assert_called_once_with(result.anchor_state)
        db.flush.assert_awaited_once()

    async def test_idempotent_replay_returns_old_receipt_without_rewinding_newer_anchor(self):
        contract = anchor_contract(generation=1)
        existing = action_outbox(contract=contract, outbox_id=12)
        state = TelegramInteractionAnchorState(
            chat_id=7007,
            recipient_user_id=7,
            desired_generation=2,
            desired_outbox_id=13,
            desired_logical_message_key="user:7:panel:2",
        )
        db = SimpleNamespace(get=AsyncMock(return_value=state))
        with (
            patch.object(
                interaction_service,
                "_find_existing",
                new=AsyncMock(return_value=existing),
            ),
            patch.object(
                interaction_service,
                "enqueue_telegram_action_notification_once",
                new=AsyncMock(
                    return_value=TelegramNotificationEnqueueResult(existing, False)
                ),
            ),
        ):
            result = await interaction_service.enqueue_private_interaction_once(
                db,
                current_server="foreign",
                recipient=recipient(),
                action=TelegramDeliveryAction.GENERAL_IMMEDIATE,
                source_id="panel:7:1",
                logical_message_key="user:7:panel:1",
                text="پنل کاربر",
                user_sync_version=4,
                reply_markup=persistent_menu(),
                result_requirement=(
                    TelegramInteractionResultRequirement.CAPTURE_MESSAGE_ID
                ),
                anchor_effect=TelegramInteractionAnchorEffect.SET_CURRENT,
            )

        self.assertFalse(result.notification.created)
        self.assertEqual(result.contract.anchor_generation, 1)
        self.assertEqual(state.desired_generation, 2)
        self.assertEqual(state.desired_outbox_id, 13)

    async def test_iran_surface_and_non_capture_anchor_fail_closed(self):
        db = SimpleNamespace()
        with self.assertRaises(interaction_service.TelegramInteractionOutboxSurfaceError):
            await interaction_service.enqueue_private_interaction_once(
                db,
                current_server="iran",
                recipient=recipient(),
                action=TelegramDeliveryAction.GENERAL_IMMEDIATE,
                source_id="x",
                logical_message_key="x",
                text="x",
                user_sync_version=1,
            )
        with patch.object(
            interaction_service,
            "_find_existing",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaisesRegex(ValueError, "requires_message_result"):
                await interaction_service.enqueue_private_interaction_once(
                    db,
                    current_server="foreign",
                    recipient=recipient(),
                    action=TelegramDeliveryAction.GENERAL_IMMEDIATE,
                    source_id="x",
                    logical_message_key="x",
                    text="x",
                    user_sync_version=1,
                    reply_markup=persistent_menu(),
                    anchor_effect=TelegramInteractionAnchorEffect.SET_CURRENT,
                )

    async def test_invalid_recipient_is_rejected_before_database_or_advisory_lock(self):
        db = SimpleNamespace(execute=AsyncMock())

        with self.assertRaisesRegex(ValueError, "recipient_invalid"):
            await interaction_service.enqueue_private_interaction_once(
                db,
                current_server="foreign",
                recipient=TelegramNotificationRecipient(
                    user_id=7,
                    telegram_id=True,
                ),
                action=TelegramDeliveryAction.GENERAL_IMMEDIATE,
                source_id="panel:invalid-route",
                logical_message_key="user:7:panel:invalid-route",
                text="پنل کاربر",
                user_sync_version=4,
                reply_markup=persistent_menu(),
                result_requirement=(
                    TelegramInteractionResultRequirement.CAPTURE_MESSAGE_ID
                ),
                anchor_effect=TelegramInteractionAnchorEffect.SET_CURRENT,
            )

        db.execute.assert_not_awaited()

    def test_interaction_metadata_participates_in_source_and_version_fingerprint(self):
        first = action_outbox(contract=anchor_contract(generation=1))
        second = action_outbox(contract=anchor_contract(generation=2))
        user = SimpleNamespace(
            id=7,
            telegram_id=7007,
            sync_version=4,
            account_status="active",
            messenger_blocked_at=None,
        )

        first_identity = telegram_notification_action_source_natural_id(first)
        second_identity = telegram_notification_action_source_natural_id(second)
        first_snapshot = build_telegram_notification_action_snapshot(first, user)
        second_snapshot = build_telegram_notification_action_snapshot(second, user)

        self.assertNotEqual(first_identity, second_identity)
        self.assertNotEqual(first_snapshot.source_version, second_snapshot.source_version)


class TelegramInteractionAnchorFeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_action_source_bypasses_interaction_contract_parser(self):
        db = SimpleNamespace(execute=AsyncMock())
        outbox = SimpleNamespace(source_type="offer_success_preview")
        job = SimpleNamespace(action_kind=TelegramDeliveryAction.OFFER_SUCCESS)

        with patch(
            "core.services.telegram_notification_outbox_queue_feedback."
            "telegram_notification_action_interaction_result_contract",
            side_effect=AssertionError("non-action source must not be parsed"),
        ):
            await _apply_interaction_anchor_result(
                db,
                outbox=outbox,
                job=job,
                now=utc_now(),
            )

        db.execute.assert_not_awaited()

    async def test_freshness_sends_only_the_current_desired_anchor_generation(self):
        contract = anchor_contract(generation=3)
        outbox = action_outbox(contract=contract, outbox_id=15)
        source = _validate_source_contract(outbox)
        matching = TelegramInteractionAnchorState(
            chat_id=7007,
            recipient_user_id=7,
            desired_generation=3,
            desired_outbox_id=15,
            desired_logical_message_key=contract.logical_message_key,
        )
        db = SimpleNamespace(get=AsyncMock(return_value=matching))

        current = await _validate_interaction_anchor_freshness(
            db,
            job=SimpleNamespace(payload={"chat_id": 7007}),
            outbox=outbox,
            source=source,
        )
        matching.desired_generation = 4
        matching.desired_outbox_id = 16
        matching.desired_logical_message_key = "new"
        stale = await _validate_interaction_anchor_freshness(
            db,
            job=SimpleNamespace(payload={"chat_id": 7007}),
            outbox=outbox,
            source=source,
        )

        self.assertIsNone(current)
        self.assertEqual(stale.outcome, TelegramFreshnessOutcome.SUPERSEDED)
        self.assertEqual(
            stale.reason,
            "notification_action_freshness_anchor_superseded",
        )

    async def test_anchor_freshness_never_retargets_a_relinked_chat(self):
        contract = anchor_contract(generation=3)
        outbox = action_outbox(contract=contract, outbox_id=15)
        source = _validate_source_contract(outbox)
        anchor = TelegramInteractionAnchorState(
            chat_id=7007,
            recipient_user_id=7,
            desired_generation=3,
            desired_outbox_id=15,
            desired_logical_message_key=contract.logical_message_key,
        )
        db = SimpleNamespace(get=AsyncMock(return_value=anchor))

        decision = await _validate_interaction_anchor_freshness(
            db,
            job=SimpleNamespace(payload={"chat_id": 8008}),
            outbox=outbox,
            source=source,
        )

        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SUPERSEDED)
        self.assertEqual(
            decision.reason,
            "notification_action_freshness_anchor_route_changed",
        )
        db.get.assert_not_awaited()

    async def test_sent_result_activates_matching_generation(self):
        contract = anchor_contract(generation=3)
        outbox = action_outbox(contract=contract, outbox_id=15)
        anchor = TelegramInteractionAnchorState(
            chat_id=7007,
            recipient_user_id=7,
            desired_generation=3,
            desired_outbox_id=15,
            desired_logical_message_key=contract.logical_message_key,
        )
        db = SimpleNamespace(execute=AsyncMock(return_value=ScalarResult(anchor)))
        job = SimpleNamespace(
            action_kind=TelegramDeliveryAction.GENERAL_IMMEDIATE,
            payload={"chat_id": 7007},
            state=TelegramDeliveryState.SENT,
            telegram_message_id=91,
        )

        await _apply_interaction_anchor_result(
            db,
            outbox=outbox,
            job=job,
            now=utc_now(),
        )

        self.assertEqual(anchor.active_generation, 3)
        self.assertEqual(anchor.active_outbox_id, 15)
        self.assertEqual(anchor.active_message_id, 91)
        self.assertEqual(
            anchor.active_logical_message_key,
            contract.logical_message_key,
        )

    async def test_late_old_generation_cannot_replace_newer_active_anchor(self):
        old_contract = anchor_contract(generation=3, logical_key="old")
        outbox = action_outbox(contract=old_contract, outbox_id=15)
        anchor = TelegramInteractionAnchorState(
            chat_id=7007,
            recipient_user_id=7,
            desired_generation=4,
            desired_outbox_id=16,
            desired_logical_message_key="new",
            active_generation=4,
            active_outbox_id=16,
            active_message_id=92,
            active_logical_message_key="new",
        )
        db = SimpleNamespace(execute=AsyncMock(return_value=ScalarResult(anchor)))
        job = SimpleNamespace(
            action_kind=TelegramDeliveryAction.GENERAL_IMMEDIATE,
            payload={"chat_id": 7007},
            state=TelegramDeliveryState.SENT,
            telegram_message_id=91,
        )

        await _apply_interaction_anchor_result(
            db,
            outbox=outbox,
            job=job,
            now=utc_now(),
        )

        self.assertEqual(anchor.active_generation, 4)
        self.assertEqual(anchor.active_message_id, 92)
        self.assertEqual(anchor.active_logical_message_key, "new")

    async def test_matching_generation_with_wrong_outbox_rolls_back_feedback(self):
        contract = anchor_contract(generation=3)
        outbox = action_outbox(contract=contract, outbox_id=15)
        anchor = TelegramInteractionAnchorState(
            chat_id=7007,
            recipient_user_id=7,
            desired_generation=3,
            desired_outbox_id=99,
            desired_logical_message_key=contract.logical_message_key,
        )
        db = SimpleNamespace(execute=AsyncMock(return_value=ScalarResult(anchor)))
        job = SimpleNamespace(
            action_kind=TelegramDeliveryAction.GENERAL_IMMEDIATE,
            payload={"chat_id": 7007},
            state=TelegramDeliveryState.SENT,
            telegram_message_id=91,
        )

        with self.assertRaisesRegex(
            TelegramNotificationOutboxQueueFeedbackError,
            "desired_identity_mismatch",
        ):
            await _apply_interaction_anchor_result(
                db,
                outbox=outbox,
                job=job,
                now=utc_now(),
            )


if __name__ == "__main__":
    unittest.main()
