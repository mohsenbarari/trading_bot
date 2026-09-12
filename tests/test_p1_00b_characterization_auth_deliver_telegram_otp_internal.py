"""Stage P1-00b characterization tests for internal Telegram OTP delivery.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Surface seed: API-AUTH-POST-AUTH-DELIVER-TELEGRAM-OTP-INTERNAL-L297
Gap: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

This module characterises the enabled downstream contract of the real
deliver_telegram_otp_internal handler under frozen seed conditions.
It does not grant seed promotion or Stage P1-00b closure.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from fastapi import Request, Response

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.auth import deliver_telegram_otp_internal
from core.config import settings
from core.registration_contracts import (
    TelegramOTPDeliveryCommand,
    TelegramOTPDeliveryOutcome,
    TelegramOTPDeliveryResponse,
)


class P100bAuthDeliverTelegramOtpInternalCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Characterization tests for enabled valid-command delivery paths."""

    async def test_deliver_telegram_otp_internal_enabled_sent_success(self) -> None:
        fixed_request_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        fixed_telegram_id = 111111111
        fixed_otp_code = "12345"
        fixed_expires_at = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)

        command = TelegramOTPDeliveryCommand(
            otp_request_id=fixed_request_id,
            telegram_id=fixed_telegram_id,
            otp_code=fixed_otp_code,
            expires_at=fixed_expires_at,
        )
        raw_bytes = command.model_dump_json().encode("utf-8")

        request = MagicMock(spec=Request, name="request_sentinel_sent")
        request.body = AsyncMock(return_value=raw_bytes)
        response = Response()

        redis_sentinel = MagicMock(name="redis_sentinel_sent")
        sent_response_sentinel = TelegramOTPDeliveryResponse(
            otp_request_id=fixed_request_id,
            outcome=TelegramOTPDeliveryOutcome.SENT,
            terminal=True,
        )

        with patch.object(settings, "telegram_login_otp_enabled", True), \
             patch("api.routers.auth._verify_iran_internal_command") as mock_verify, \
             patch("api.routers.auth.get_redis", new_callable=AsyncMock) as mock_get_redis, \
             patch("api.routers.auth.deliver_telegram_otp_once", new_callable=AsyncMock) as mock_deliver_once, \
             patch("api.routers.auth.audit_log") as mock_audit_log, \
             patch("api.routers.auth.record_otp_event") as mock_record_otp_event:

            mock_get_redis.return_value = redis_sentinel
            mock_deliver_once.return_value = sent_response_sentinel

            result = await deliver_telegram_otp_internal(
                raw_request=request,
                response=response,
            )

            self.assertEqual(request.body.await_count, 1)
            request.body.assert_awaited_once_with()

            self.assertEqual(mock_verify.call_count, 1)
            mock_verify.assert_called_once_with(request, raw_bytes)
            self.assertIs(mock_verify.call_args.args[0], request)
            self.assertEqual(mock_verify.call_args.args[1], raw_bytes)

            self.assertEqual(response.headers.get("Cache-Control"), "no-store")
            self.assertEqual(response.headers["Cache-Control"], "no-store")

            self.assertEqual(mock_get_redis.await_count, 1)
            mock_get_redis.assert_awaited_once_with()

            self.assertEqual(mock_deliver_once.await_count, 1)
            mock_deliver_once.assert_awaited_once_with(redis_sentinel, command=command)
            self.assertIs(mock_deliver_once.await_args.args[0], redis_sentinel)

            delivered_command = mock_deliver_once.await_args.kwargs["command"]
            self.assertIsInstance(delivered_command, TelegramOTPDeliveryCommand)
            self.assertEqual(delivered_command.otp_request_id, fixed_request_id)
            self.assertEqual(delivered_command.telegram_id, fixed_telegram_id)
            self.assertEqual(delivered_command.otp_code, fixed_otp_code)
            self.assertEqual(delivered_command.expires_at, fixed_expires_at)
            self.assertEqual(delivered_command, command)

            self.assertIs(result, sent_response_sentinel)

            self.assertEqual(mock_audit_log.call_count, 1)
            mock_audit_log.assert_called_once_with(
                "otp.telegram_delivery_result",
                target_type="otp_request",
                target_id=str(fixed_request_id),
                result="success",
                extra={"outcome": "sent"},
            )

            self.assertEqual(mock_record_otp_event.call_count, 1)
            mock_record_otp_event.assert_called_once_with(
                event="telegram_delivery_result",
                outcome="sent",
            )

    async def test_deliver_telegram_otp_internal_enabled_delivery_failure_propagates(self) -> None:
        fixed_request_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        fixed_telegram_id = 222222222
        fixed_otp_code = "67890"
        fixed_expires_at = datetime(2026, 9, 12, 13, 0, 0, tzinfo=timezone.utc)

        command = TelegramOTPDeliveryCommand(
            otp_request_id=fixed_request_id,
            telegram_id=fixed_telegram_id,
            otp_code=fixed_otp_code,
            expires_at=fixed_expires_at,
        )
        raw_bytes = command.model_dump_json().encode("utf-8")

        request = MagicMock(spec=Request, name="request_sentinel_failure")
        request.body = AsyncMock(return_value=raw_bytes)
        response = Response()

        redis_sentinel = MagicMock(name="redis_sentinel_failure")
        delivery_error = RuntimeError("synthetic delivery worker exception")

        with patch.object(settings, "telegram_login_otp_enabled", True), \
             patch("api.routers.auth._verify_iran_internal_command") as mock_verify, \
             patch("api.routers.auth.get_redis", new_callable=AsyncMock) as mock_get_redis, \
             patch("api.routers.auth.deliver_telegram_otp_once", new_callable=AsyncMock) as mock_deliver_once, \
             patch("api.routers.auth.audit_log") as mock_audit_log, \
             patch("api.routers.auth.record_otp_event") as mock_record_otp_event:

            mock_get_redis.return_value = redis_sentinel
            mock_deliver_once.side_effect = delivery_error

            with self.assertRaises(RuntimeError) as cm:
                await deliver_telegram_otp_internal(
                    raw_request=request,
                    response=response,
                )

            self.assertIs(cm.exception, delivery_error)

            self.assertEqual(request.body.await_count, 1)
            request.body.assert_awaited_once_with()

            self.assertEqual(mock_verify.call_count, 1)
            mock_verify.assert_called_once_with(request, raw_bytes)
            self.assertIs(mock_verify.call_args.args[0], request)
            self.assertEqual(mock_verify.call_args.args[1], raw_bytes)

            self.assertEqual(response.headers.get("Cache-Control"), "no-store")
            self.assertEqual(response.headers["Cache-Control"], "no-store")

            self.assertEqual(mock_get_redis.await_count, 1)
            mock_get_redis.assert_awaited_once_with()

            self.assertEqual(mock_deliver_once.await_count, 1)
            mock_deliver_once.assert_awaited_once_with(redis_sentinel, command=command)
            self.assertIs(mock_deliver_once.await_args.args[0], redis_sentinel)

            delivered_command = mock_deliver_once.await_args.kwargs["command"]
            self.assertIsInstance(delivered_command, TelegramOTPDeliveryCommand)
            self.assertEqual(delivered_command.otp_request_id, fixed_request_id)
            self.assertEqual(delivered_command.telegram_id, fixed_telegram_id)
            self.assertEqual(delivered_command.otp_code, fixed_otp_code)
            self.assertEqual(delivered_command.expires_at, fixed_expires_at)
            self.assertEqual(delivered_command, command)

            mock_audit_log.assert_not_called()
            self.assertEqual(mock_audit_log.call_count, 0)

            mock_record_otp_event.assert_not_called()
            self.assertEqual(mock_record_otp_event.call_count, 0)


if __name__ == "__main__":
    unittest.main()
