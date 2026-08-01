from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from aiogram.methods import GetUpdates, SendMessage

from api.routers import auth
from bot.utils import trade_suggestion_messages as suggestion_messages
from core import sms, utils, web_push
from core.external_effect_execution_gate import (
    EXTERNAL_EFFECT_SCOPE_SMS_PROVIDER_DELIVERY,
    EXTERNAL_EFFECT_SCOPE_TELEGRAM_BOT_API_EFFECT,
    EXTERNAL_EFFECT_SCOPE_TELEGRAM_DIRECT_NOTIFICATION_EFFECT,
    EXTERNAL_EFFECT_SCOPE_TELEGRAM_OTP_DELIVERY,
    EXTERNAL_EFFECT_SCOPE_WEB_PUSH_DELIVERY,
    ExternalEffectExecutionGateError,
)
from core.registration_contracts import TelegramOTPDeliveryCommand
from core.server_routing import SERVER_FOREIGN, override_current_server
from core.services import otp_sms_delivery_service, telegram_otp_delivery_service
from core.services.invitation_sms_delivery_service import deliver_invitation_sms_once
from core.services.otp_delivery_state_service import OTPDeliveryClaim
from core.utils import utc_now
import run_bot


def _blocked(reason: str = "authorization expired") -> ExternalEffectExecutionGateError:
    return ExternalEffectExecutionGateError(reason)


class _OneMessagePubSub:
    def __init__(self) -> None:
        self.subscribe = AsyncMock()
        self.unsubscribe = AsyncMock()
        self.close = AsyncMock()
        self._seen = False

    async def get_message(self, **_kwargs):
        if self._seen:
            raise AssertionError("listener must stop after an authorization refusal")
        self._seen = True
        return {
            "type": "message",
            "channel": "events:offer:updated",
            "data": '{"offer_id": 9}',
        }


class ExternalEffectExecutionProviderBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_sms_provider_adapters_reject_before_sync_or_async_http(self):
        with patch(
            "core.sms.require_external_effect_execution_authorization",
            side_effect=_blocked("authorization wrong scope"),
        ) as sync_authorize, patch("core.sms.httpx.post") as sync_post:
            with self.assertRaisesRegex(ExternalEffectExecutionGateError, "wrong scope"):
                sms._post_smsir_result("v1/send/bulk", {})
        sync_authorize.assert_called_once_with(EXTERNAL_EFFECT_SCOPE_SMS_PROVIDER_DELIVERY)
        sync_post.assert_not_called()

        with patch(
            "core.sms.require_external_effect_execution_authorization",
            side_effect=_blocked("authorization term changed"),
        ) as async_authorize, patch("core.sms.httpx.AsyncClient") as async_client:
            with self.assertRaisesRegex(ExternalEffectExecutionGateError, "term changed"):
                await sms._post_smsir_result_async("v1/send/verify", {})
        async_authorize.assert_called_once_with(EXTERNAL_EFFECT_SCOPE_SMS_PROVIDER_DELIVERY)
        async_client.assert_not_called()

    async def test_stage6_sms_paths_reject_before_otp_claim_or_provider_marker(self):
        state = SimpleNamespace(otp_request_id=uuid4())
        with patch.object(
            auth,
            "require_external_effect_execution_authorization",
            side_effect=_blocked("authorization missing"),
        ) as auth_authorize, patch.object(auth, "claim_sms_delivery", new=AsyncMock()) as claim_sms:
            with self.assertRaisesRegex(ExternalEffectExecutionGateError, "missing"):
                await auth._deliver_stage6_sms(object(), state=state)
        auth_authorize.assert_called_once_with(EXTERNAL_EFFECT_SCOPE_SMS_PROVIDER_DELIVERY)
        claim_sms.assert_not_awaited()

        claim = OTPDeliveryClaim(
            claim_id=uuid4(),
            request_id=uuid4(),
            mobile_number="09121112233",
            otp_code="12345",
            lease_until=utc_now() + timedelta(seconds=30),
        )
        with patch(
            "core.services.otp_sms_delivery_service.require_external_effect_execution_authorization",
            side_effect=_blocked(),
        ) as otp_authorize, patch(
            "core.services.otp_sms_delivery_service.mark_sms_provider_attempt_started",
            new=AsyncMock(),
        ) as mark_provider_started:
            with self.assertRaisesRegex(ExternalEffectExecutionGateError, "expired"):
                await otp_sms_delivery_service.execute_claimed_otp_sms_delivery(
                    object(),
                    claim=claim,
                )
        otp_authorize.assert_called_once_with(EXTERNAL_EFFECT_SCOPE_SMS_PROVIDER_DELIVERY)
        mark_provider_started.assert_not_awaited()

    async def test_invitation_sms_rejects_before_durable_claim_or_sender(self):
        db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())
        sender = Mock()
        with patch(
            "core.services.invitation_sms_delivery_service.require_external_effect_execution_authorization",
            side_effect=_blocked("authorization expired"),
        ) as invitation_authorize, patch(
            "core.services.invitation_sms_delivery_service.current_server",
            return_value="iran",
        ):
            with self.assertRaisesRegex(ExternalEffectExecutionGateError, "expired"):
                await deliver_invitation_sms_once(
                    db,
                    invitation_id=7,
                    newly_created=True,
                    sender=sender,
                )
        invitation_authorize.assert_called_once_with(EXTERNAL_EFFECT_SCOPE_SMS_PROVIDER_DELIVERY)
        db.execute.assert_not_awaited()
        db.commit.assert_not_awaited()
        sender.assert_not_called()

    async def test_web_push_rejects_before_loading_subscription_or_provider_call(self):
        db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())
        with patch.object(web_push, "is_web_push_configured", return_value=True), patch.object(
            web_push,
            "require_external_effect_execution_authorization",
            side_effect=_blocked("authorization missing"),
        ) as web_push_authorize, patch.object(web_push.asyncio, "to_thread", new=AsyncMock()) as provider_call:
            with self.assertRaisesRegex(ExternalEffectExecutionGateError, "missing"):
                await web_push.send_web_push_to_user(db, 7, {"title": "test"})
        web_push_authorize.assert_called_once_with(EXTERNAL_EFFECT_SCOPE_WEB_PUSH_DELIVERY)
        db.execute.assert_not_awaited()
        db.commit.assert_not_awaited()
        provider_call.assert_not_awaited()

    async def test_telegram_otp_rejects_before_redis_receipt_and_gateway(self):
        redis = SimpleNamespace(set=AsyncMock(), get=AsyncMock())
        command = TelegramOTPDeliveryCommand(
            otp_request_id=uuid4(),
            telegram_id=8700001,
            otp_code="12345",
            expires_at=utc_now() + timedelta(seconds=120),
        )
        with override_current_server(SERVER_FOREIGN), patch(
            "core.services.telegram_otp_delivery_service.require_external_effect_execution_authorization",
            side_effect=_blocked("authorization term changed"),
        ) as telegram_otp_authorize, patch(
            "core.services.telegram_otp_delivery_service.telegram_gateway.send_message",
            new=AsyncMock(),
        ) as gateway_send:
            with self.assertRaisesRegex(ExternalEffectExecutionGateError, "term changed"):
                await telegram_otp_delivery_service.deliver_telegram_otp_once(
                    redis,
                    command=command,
                )
        telegram_otp_authorize.assert_called_once_with(EXTERNAL_EFFECT_SCOPE_TELEGRAM_OTP_DELIVERY)
        redis.set.assert_not_awaited()
        gateway_send.assert_not_awaited()

    async def test_direct_telegram_gateway_notification_rejects_before_send(self):
        with patch("core.utils.os.getenv", return_value="token"), patch(
            "core.db.require_external_effect_execution_authorization",
            side_effect=_blocked("authorization wrong scope"),
        ) as direct_notification_authorize, patch.object(utils.telegram_gateway, "send_message", new=AsyncMock()) as gateway_send:
            with self.assertRaisesRegex(ExternalEffectExecutionGateError, "wrong scope"):
                await utils.send_telegram_notification(8700001, "test")
        direct_notification_authorize.assert_called_once_with(
            EXTERNAL_EFFECT_SCOPE_TELEGRAM_DIRECT_NOTIFICATION_EFFECT
        )
        gateway_send.assert_not_awaited()

    async def test_direct_bot_api_middleware_rejects_effectful_methods_but_not_get_updates(self):
        middleware = run_bot._external_effect_request_middleware()
        provider_request = AsyncMock(return_value={"ok": True})
        with patch(
            "run_bot.require_external_effect_execution_authorization",
            side_effect=_blocked("authorization expired"),
        ) as bot_api_authorize:
            with self.assertRaisesRegex(ExternalEffectExecutionGateError, "expired"):
                await middleware(
                    provider_request,
                    object(),
                    SendMessage(chat_id=8700001, text="test"),
                )
        bot_api_authorize.assert_called_once_with(EXTERNAL_EFFECT_SCOPE_TELEGRAM_BOT_API_EFFECT)
        provider_request.assert_not_awaited()

        provider_request = AsyncMock(return_value=[])
        with patch("run_bot.require_external_effect_execution_authorization") as authorize:
            result = await middleware(provider_request, object(), GetUpdates())
        self.assertEqual(result, [])
        authorize.assert_not_called()
        provider_request.assert_awaited_once()

    async def test_listener_propagates_effect_gate_refusal_and_never_retries_it(self):
        pubsub = _OneMessagePubSub()
        redis_client = SimpleNamespace(
            pubsub=lambda: pubsub,
            aclose=AsyncMock(),
        )
        with patch(
            "bot.utils.trade_suggestion_messages.require_application_writer_term",
            return_value=None,
        ), patch(
            "bot.utils.trade_suggestion_messages.redis.Redis",
            return_value=redis_client,
        ), patch(
            "bot.utils.trade_suggestion_messages.sync_trade_suggestions_for_offer",
            new=AsyncMock(side_effect=_blocked("authorization expired")),
        ) as sync:
            with self.assertRaisesRegex(ExternalEffectExecutionGateError, "expired"):
                await suggestion_messages.listen_trade_suggestion_events(object())
        sync.assert_awaited_once()
        pubsub.unsubscribe.assert_awaited_once()
        pubsub.close.assert_awaited_once()
        redis_client.aclose.assert_awaited_once()

    async def test_trade_suggestion_edit_rejects_before_telegram_api_call(self):
        bot = SimpleNamespace(edit_message_reply_markup=AsyncMock())
        with patch(
            "bot.utils.trade_suggestion_messages.require_external_effect_execution_authorization",
            side_effect=_blocked("authorization missing"),
        ) as suggestion_authorize:
            with self.assertRaisesRegex(ExternalEffectExecutionGateError, "missing"):
                await suggestion_messages._clear_suggestion_markup(bot, 1, 2)
        suggestion_authorize.assert_called_once_with(EXTERNAL_EFFECT_SCOPE_TELEGRAM_BOT_API_EFFECT)
        bot.edit_message_reply_markup.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
