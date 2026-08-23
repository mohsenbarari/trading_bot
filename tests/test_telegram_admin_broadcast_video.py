import hashlib
import json
import unittest
from dataclasses import asdict
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers import admin_broadcast
from core import telegram_gateway
from core.enums import UserRole
from core.services import telegram_admin_broadcast_delivery_service as delivery_service
from core.services import telegram_admin_broadcast_service as service
from core.services.telegram_delivery_queue_service import (
    SUPPORTED_TELEGRAM_QUEUE_METHODS,
    TelegramDeliveryQueueValidationError,
    enqueue_telegram_delivery_job,
)
from core.telegram_delivery_admin_broadcast_freshness import (
    ADMIN_BROADCAST_TEMPLATE_VERSION,
    ADMIN_BROADCAST_VIDEO_TEMPLATE_VERSION,
    build_telegram_admin_broadcast_payload,
    telegram_admin_broadcast_source_natural_id,
)
from core.telegram_delivery_queue_contract import (
    TELEGRAM_NON_IDEMPOTENT_SEND_METHODS,
    TelegramDeliveryAction,
    TelegramDeliveryJob,
    TelegramDeliveryOutcome,
    TelegramDeliveryState,
    TelegramDestinationClass,
    TelegramFeederKind,
    apply_gateway_result,
    telegram_action_allows_method,
)
from core.telegram_multi_publisher_contract import (
    TELEGRAM_PUBLISHER_OWNER_REQUIRED_METHODS,
)
from models.telegram_admin_broadcast import (
    TelegramAdminBroadcastAudienceType,
    TelegramAdminBroadcastContentKind,
    TelegramAdminBroadcastReceipt,
    TelegramAdminBroadcastReceiptStatus,
    TelegramAdminBroadcastStatus,
)
from tests.test_bot_admin_broadcast_interaction_queue import (
    _SessionContext,
    _callback,
    _state,
    _user,
)
from tests.test_telegram_admin_broadcast_delivery_service import (
    FakeDeliveryDB,
    make_broadcast,
    make_receipt,
)
from tests.test_telegram_admin_broadcast_service import FakeQueueDB
from tests.test_telegram_gateway_policy import FakeAsyncClientContext, FakeResponse


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def _video_message(**overrides):
    values = {
        "text": None,
        "caption": "آموزش امکانات بات",
        "video": SimpleNamespace(
            file_id="AgAC-central-bot-file",
            file_unique_id="AQADunique01",
            duration=12,
            width=640,
            height=360,
            file_size=2048,
        ),
        "document": None,
        "animation": None,
        "media_group_id": None,
        "answer": AsyncMock(),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TelegramAdminBroadcastVideoServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_caption_and_file_token_validation(self):
        with self.assertRaisesRegex(
            service.TelegramAdminBroadcastValidationError,
            "video_caption_required",
        ):
            service.validate_telegram_admin_broadcast_caption("   ")
        with self.assertRaisesRegex(
            service.TelegramAdminBroadcastValidationError,
            "video_caption_too_long",
        ):
            service.validate_telegram_admin_broadcast_caption(
                "x" * (service.TELEGRAM_BROADCAST_VIDEO_CAPTION_MAX_LENGTH + 1)
            )
        with self.assertRaisesRegex(
            service.TelegramAdminBroadcastValidationError,
            "telegram_media_file_id_invalid",
        ):
            service.validate_telegram_file_id_token(
                "bad\nfile",
                field_name="telegram_media_file_id",
                max_length=1024,
            )
        self.assertEqual(
            service.validate_telegram_admin_broadcast_content("  متن  "),
            "متن",
        )

    async def test_create_video_broadcast_stores_metadata_without_binary(self):
        db = FakeQueueDB()
        recipients = (
            service.TelegramAdminBroadcastRecipient(
                user_id=7, telegram_id=9007, account_name="user7"
            ),
        )
        with patch(
            "core.services.telegram_admin_broadcast_service.resolve_telegram_admin_broadcast_recipients",
            new=AsyncMock(return_value=recipients),
        ):
            result = await service.create_telegram_admin_broadcast(
                db,
                actor=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
                content=" آموزش امکانات بات ",
                audience_type=TelegramAdminBroadcastAudienceType.ALL,
                content_kind=TelegramAdminBroadcastContentKind.VIDEO,
                telegram_media_file_id="AgAC-central-bot-file",
                telegram_media_file_unique_id="AQADunique01",
                media_duration_seconds=12,
                media_width=640,
                media_height=360,
                media_file_size=2048,
            )
        self.assertEqual(result.broadcast.content_kind, TelegramAdminBroadcastContentKind.VIDEO)
        self.assertEqual(result.broadcast.content, "آموزش امکانات بات")
        self.assertEqual(result.broadcast.telegram_media_file_id, "AgAC-central-bot-file")
        self.assertEqual(result.receipt_count, 1)

    async def test_text_create_rejects_media_injection_and_non_superadmin(self):
        with self.assertRaisesRegex(
            service.TelegramAdminBroadcastValidationError,
            "text_media_forbidden",
        ):
            await service.create_telegram_admin_broadcast(
                FakeQueueDB(),
                actor=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
                content="متن",
                audience_type=TelegramAdminBroadcastAudienceType.ALL,
                telegram_media_file_id="injected-file",
            )
        with self.assertRaisesRegex(
            service.TelegramAdminBroadcastValidationError,
            "superadmin_required",
        ):
            await service.create_telegram_admin_broadcast(
                FakeQueueDB(),
                actor=SimpleNamespace(id=2, role=UserRole.MIDDLE_MANAGER),
                content="آموزش",
                audience_type=TelegramAdminBroadcastAudienceType.ALL,
                content_kind=TelegramAdminBroadcastContentKind.VIDEO,
                telegram_media_file_id="AgAC-central-bot-file",
                telegram_media_file_unique_id="AQADunique01",
            )

    async def test_inspect_status_is_count_only(self):
        broadcast = SimpleNamespace(
            id=9,
            status=TelegramAdminBroadcastStatus.RUNNING,
            content_kind=TelegramAdminBroadcastContentKind.VIDEO,
        )

        class _InspectDB:
            async def get(self, _model, _object_id):
                return broadcast

            async def execute(self, _statement):
                return SimpleNamespace(
                    all=lambda: [
                        (TelegramAdminBroadcastReceiptStatus.PENDING, 2),
                        (TelegramAdminBroadcastReceiptStatus.SENT, 5),
                    ]
                )

        counts = await service.inspect_telegram_admin_broadcast_status(
            _InspectDB(),
            broadcast_id=9,
        )
        rendered = json.dumps(asdict(counts), ensure_ascii=False)
        self.assertEqual(counts.pending, 2)
        self.assertEqual(counts.sent, 5)
        self.assertEqual(counts.content_kind, "video")
        self.assertNotIn("AgAC", rendered)
        self.assertNotIn("file_id", rendered)
        self.assertNotIn("telegram_id", rendered)


class TelegramAdminBroadcastVideoQueueTests(unittest.IsolatedAsyncioTestCase):
    def test_action_method_matrix_and_publisher_exclusion(self):
        self.assertIn("sendVideo", SUPPORTED_TELEGRAM_QUEUE_METHODS)
        self.assertIn("sendVideo", TELEGRAM_NON_IDEMPOTENT_SEND_METHODS)
        self.assertNotIn("sendVideo", TELEGRAM_PUBLISHER_OWNER_REQUIRED_METHODS)
        self.assertTrue(
            telegram_action_allows_method(
                TelegramDeliveryAction.ADMIN_BROADCAST, "sendMessage"
            )
        )
        self.assertTrue(
            telegram_action_allows_method(
                TelegramDeliveryAction.ADMIN_BROADCAST, "sendVideo"
            )
        )
        self.assertFalse(
            telegram_action_allows_method(
                TelegramDeliveryAction.GENERAL_ANNOUNCEMENT, "sendVideo"
            )
        )
        self.assertFalse(
            telegram_action_allows_method(
                TelegramDeliveryAction.ADMIN_BROADCAST, "sendDocument"
            )
        )

    async def test_enqueue_rejects_send_video_for_other_actions(self):
        with self.assertRaisesRegex(
            TelegramDeliveryQueueValidationError,
            "telegram_method_not_allowlisted_for_action",
        ):
            await enqueue_telegram_delivery_job(
                SimpleNamespace(),
                current_server="foreign",
                feeder=TelegramFeederKind.ADMIN_SYSTEM,
                source_natural_id="other:1:content-v1:abc",
                source_version=1,
                action=TelegramDeliveryAction.GENERAL_ANNOUNCEMENT,
                bot_identity="primary",
                destination_key="private:user:1",
                destination_class=TelegramDestinationClass.PRIVATE,
                method="sendVideo",
                payload={"chat_id": 1, "video": "AgAC-x", "caption": "x"},
                template_version="other-v1",
            )

    def test_text_source_identity_stays_stable(self):
        receipt = SimpleNamespace(dedupe_key="telegram-admin-broadcast:51:9")
        broadcast = SimpleNamespace(content="پیام مدیریتی")
        snapshot = json.dumps(
            {
                "content": "پیام مدیریتی",
                "dedupe_key": "telegram-admin-broadcast:51:9",
                "template_version": ADMIN_BROADCAST_TEMPLATE_VERSION,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()[:24]
        self.assertEqual(
            telegram_admin_broadcast_source_natural_id(receipt, broadcast),
            f"telegram-admin-broadcast:51:9:content-v1:{fingerprint}",
        )

    def test_video_payload_is_file_id_json_only(self):
        payload = build_telegram_admin_broadcast_payload(
            SimpleNamespace(
                content="آموزش امکانات بات",
                content_kind=TelegramAdminBroadcastContentKind.VIDEO,
                telegram_media_file_id="AgAC-central-bot-file",
                telegram_media_file_unique_id="AQADunique01",
            ),
            SimpleNamespace(telegram_id=9001),
        )
        self.assertEqual(
            payload,
            {
                "chat_id": 9001,
                "video": "AgAC-central-bot-file",
                "caption": "آموزش امکانات بات",
                "parse_mode": None,
                "supports_streaming": True,
            },
        )
        rendered = json.dumps(payload)
        self.assertNotIn("base64", rendered)
        self.assertNotIn(".mp4", rendered)
        self.assertNotIn("http", rendered)

    def test_wrong_file_identifier_is_terminal(self):
        job = TelegramDeliveryJob(
            id=1,
            dedupe_key="telegram-admin-broadcast:51:9",
            feeder=TelegramFeederKind.ADMIN_SYSTEM,
            feeder_rank=1,
            source_natural_id="telegram-admin-broadcast:51:9:content-v1:abc",
            source_version=1,
            destination_key="private:user:9",
            destination_class=TelegramDestinationClass.PRIVATE,
            method="sendVideo",
            payload={
                "chat_id": 9001,
                "video": "AgAC-central-bot-file",
                "caption": "کپشن",
            },
            action=TelegramDeliveryAction.ADMIN_BROADCAST,
            created_sequence=1,
            state=TelegramDeliveryState.LEASED,
            worker_id="worker",
            lease_token=1,
            lease_until=NOW,
        )
        decision = apply_gateway_result(
            job,
            telegram_gateway.TelegramGatewayResult(
                ok=False,
                method="sendVideo",
                status_code=400,
                response_text="Bad Request: wrong file identifier/HTTP URL specified",
            ),
            now=NOW,
            retry_after_safety_seconds=0.1,
        )
        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.TERMINAL_FAILED)
        self.assertEqual(job.state, TelegramDeliveryState.TERMINAL_FAILED)
        self.assertNotIn("AgAC-central-bot-file", decision.reason)


class TelegramAdminBroadcastVideoGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_video_uses_json_file_id_not_multipart(self):
        client = FakeAsyncClientContext(response=FakeResponse())
        with patch("core.telegram_gateway.current_server", return_value="foreign"), patch(
            "core.telegram_gateway.httpx.AsyncClient",
            return_value=client,
        ):
            result = await telegram_gateway.send_video_by_file_id(
                9001,
                "AgAC-central-bot-file",
                caption="آموزش امکانات بات",
                bot_token="token",
            )
        self.assertTrue(result.ok)
        request = client.post.await_args
        self.assertEqual(request.kwargs["json"]["video"], "AgAC-central-bot-file")
        self.assertEqual(request.kwargs["json"]["caption"], "آموزش امکانات بات")
        self.assertTrue(request.kwargs["json"]["supports_streaming"])
        self.assertNotIn("files", request.kwargs)
        self.assertNotIn("data", request.kwargs)

    async def test_send_video_rejects_path_or_url_before_http(self):
        client = FakeAsyncClientContext(response=FakeResponse())
        with patch("core.telegram_gateway.current_server", return_value="foreign"), patch(
            "core.telegram_gateway.httpx.AsyncClient",
            return_value=client,
        ):
            result = await telegram_gateway.send_video_by_file_id(
                9001,
                "/tmp/1.mp4",
                caption="آموزش",
                bot_token="token",
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "invalid_file_identifier")
        client.post.assert_not_awaited()


class TelegramAdminBroadcastVideoLegacyDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_video_uses_video_sender_not_text(self):
        receipt = make_receipt()
        db = FakeDeliveryDB(
            broadcast=make_broadcast(
                content="آموزش امکانات بات",
                content_kind=TelegramAdminBroadcastContentKind.VIDEO,
                telegram_media_file_id="AgAC-central-bot-file",
                telegram_media_file_unique_id="AQADunique01",
            ),
            user=SimpleNamespace(id=9, telegram_id=9010),
        )
        gateway_send = AsyncMock()
        video_calls = []

        async def fake_video(chat_id, file_id, **kwargs):
            video_calls.append((chat_id, file_id, kwargs))
            return telegram_gateway.TelegramGatewayResult(
                ok=True,
                method="sendVideo",
                response_json={"result": {"message_id": 888}},
            )

        with patch(
            "core.services.telegram_admin_broadcast_delivery_service.evaluate_bot_access",
            new=AsyncMock(return_value=SimpleNamespace(allowed=True, reason=None)),
        ), patch(
            "core.services.telegram_admin_broadcast_delivery_service.finalize_telegram_admin_broadcast_status",
            new=AsyncMock(),
        ):
            result = await delivery_service.deliver_claimed_telegram_admin_broadcast_receipt(
                db,
                receipt,
                current_server="foreign",
                gateway_send=gateway_send,
                gateway_send_video=fake_video,
            )
        self.assertEqual(
            result.status,
            delivery_service.TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_SENT,
        )
        gateway_send.assert_not_awaited()
        self.assertEqual(video_calls[0][0], 9010)
        self.assertEqual(video_calls[0][1], "AgAC-central-bot-file")
        self.assertEqual(video_calls[0][2]["caption"], "آموزش امکانات بات")

    def test_file_identifier_classifier_is_terminal(self):
        classified = delivery_service.classify_telegram_admin_broadcast_failure(
            telegram_gateway.TelegramGatewayResult(
                ok=False,
                method="sendVideo",
                status_code=400,
                response_text="Bad Request: wrong file identifier/HTTP URL specified",
            )
        )
        self.assertEqual(
            classified.status,
            TelegramAdminBroadcastReceiptStatus.TERMINAL_FAILED,
        )
        self.assertEqual(classified.reason, "telegram_malformed_payload")


class TelegramAdminBroadcastVideoHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_preview_and_confirm_hide_file_id(self):
        message = _video_message()
        preview_state = _state({"audience_type": "all"})
        user = _user()
        with (
            patch.object(
                admin_broadcast,
                "_estimate_recipient_count",
                new=AsyncMock(return_value=4),
            ),
            patch.object(
                admin_broadcast,
                "answer_incoming_message_via_runtime",
                new=AsyncMock(),
            ) as answer,
        ):
            await admin_broadcast.process_broadcast_message_text(
                message,
                preview_state,
                user,
            )
        preview = answer.await_args.args[2]
        self.assertEqual(
            answer.await_args.kwargs["source_key"],
            "admin-broadcast-preview-video",
        )
        self.assertIn("آموزش امکانات بات", preview)
        self.assertNotIn("AgAC-central-bot-file", preview)
        self.assertIn("گیرندگان واجدشرایط: 4", preview)

        confirm_state = _state(
            {
                "audience_type": "all",
                "content": "آموزش امکانات بات",
                "content_kind": TelegramAdminBroadcastContentKind.VIDEO.value,
                "telegram_media_file_id": "AgAC-central-bot-file",
                "telegram_media_file_unique_id": "AQADunique01",
                "target_groups": [],
                "selected_user_ids": [],
                "creation_key": "opaque-video-confirm-key-01",
            }
        )
        callback = _callback("tgb:confirm")
        create = AsyncMock(
            return_value=SimpleNamespace(
                broadcast=SimpleNamespace(id=77),
                receipt_count=4,
            )
        )
        with (
            patch.object(
                admin_broadcast,
                "AsyncSessionLocal",
                return_value=_SessionContext(SimpleNamespace(commit=AsyncMock())),
            ),
            patch.object(
                admin_broadcast,
                "create_telegram_admin_broadcast",
                new=create,
            ),
            patch.object(
                admin_broadcast,
                "edit_callback_message_via_runtime",
                new=AsyncMock(),
            ) as edit,
        ):
            await admin_broadcast.confirm_telegram_admin_broadcast(
                callback,
                confirm_state,
                user,
            )
            await admin_broadcast.confirm_telegram_admin_broadcast(
                callback,
                confirm_state,
                user,
            )
        self.assertEqual(create.await_count, 1)
        queued = edit.await_args.args[2]
        self.assertEqual(
            edit.await_args.kwargs["source_key"],
            "admin-broadcast-confirm-queued-video",
        )
        self.assertIn("ویدئو در صف ارسال قرار گرفت", queued)
        self.assertNotIn("AgAC-central-bot-file", queued)

    async def test_document_media_group_and_empty_caption_are_rejected(self):
        user = _user()
        cases = (
            (
                _video_message(video=None, document=SimpleNamespace(file_id="doc")),
                "admin-broadcast-video-as-document",
                "ویدئو را به‌صورت Video ارسال کنید.",
            ),
            (
                _video_message(media_group_id="12"),
                "admin-broadcast-video-as-album",
                "هر ویدئو را جداگانه ارسال کنید.",
            ),
            (
                _video_message(caption=""),
                "admin-broadcast-video-caption-empty",
                "کپشن ویدئو را وارد کنید.",
            ),
            (
                _video_message(
                    caption="x" * (service.TELEGRAM_BROADCAST_VIDEO_CAPTION_MAX_LENGTH + 1)
                ),
                "admin-broadcast-video-caption-too-long",
                "کپشن ویدئو نباید بیشتر از ۱۰۲۴ کاراکتر باشد.",
            ),
        )
        for message, source_key, text in cases:
            with self.subTest(source_key=source_key):
                state = _state({"audience_type": "all"})
                with patch.object(
                    admin_broadcast,
                    "answer_incoming_message_via_runtime",
                    new=AsyncMock(),
                ) as answer:
                    await admin_broadcast.process_broadcast_message_text(
                        message,
                        state,
                        user,
                    )
                self.assertEqual(answer.await_args.kwargs["source_key"], source_key)
                self.assertEqual(answer.await_args.args[2], text)
                state.clear.assert_not_awaited()
                self.assertNotIn("AgAC-central-bot-file", answer.await_args.args[2])


if __name__ == "__main__":
    unittest.main()
