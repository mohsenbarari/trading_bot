"""Characterization tests for complete_telegram_account_link_internal handler.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Seed: API-AUTH-POST-AUTH-COMPLETE-TELEGRAM-ACCOUNT-LINK-INTERNAL-L251
Gap: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

Freezes the enabled valid-command LINKED_EXISTING first-terminal behavior and
the non-first-terminal replay behavior of the internal account-link completion handler.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from fastapi import Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.auth import complete_telegram_account_link_internal
from core.config import settings
from core.registration_contracts import (
    TelegramRegistrationCommandResponse,
    TelegramRegistrationOutcome,
)
from core.server_routing import SERVER_FOREIGN
from core.services.authoritative_telegram_account_link_service import (
    AuthoritativeTelegramAccountLinkResult,
)
from core.telegram_account_link_contracts import TelegramAccountLinkCommand


class P100bAuthCompleteTelegramAccountLinkInternalCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Characterization tests for enabled valid-command account link paths."""

    async def test_complete_telegram_account_link_internal_enabled_first_terminal_success(
        self,
    ) -> None:
        fixed_command_id = UUID("11111111-1111-1111-1111-111111111111")
        fixed_idempotency_key = "telegram-account-link:" + "a" * 40
        fixed_mobile_number = "09121111111"
        fixed_telegram_id = 100000001
        fixed_telegram_username = "linked_user_one"
        fixed_telegram_full_name = "Linked User One"
        fixed_address = "Tehran Province, Tehran, District 1, Street 10, Unit 4"
        fixed_authoritative_user_id = 42

        command = TelegramAccountLinkCommand(
            command_id=fixed_command_id,
            idempotency_key=fixed_idempotency_key,
            mode="existing_linked_user",
            mobile_number=fixed_mobile_number,
            telegram_id=fixed_telegram_id,
            telegram_username=fixed_telegram_username,
            telegram_full_name=fixed_telegram_full_name,
            address=fixed_address,
        )
        raw_bytes = command.model_dump_json().encode("utf-8")

        request = MagicMock(spec=Request, name="request_sentinel_first_terminal")
        request.body = AsyncMock(return_value=raw_bytes)
        response = Response()
        db_sentinel = MagicMock(spec=AsyncSession, name="db_sentinel_first_terminal")

        service_result = AuthoritativeTelegramAccountLinkResult(
            outcome=TelegramRegistrationOutcome.LINKED_EXISTING,
            authoritative_user_id=fixed_authoritative_user_id,
            replayed=False,
            first_terminal_transition=True,
        )

        with patch.object(settings, "registration_sync_v2_enabled", True), \
             patch("api.routers.auth._verify_foreign_internal_command") as mock_verify, \
             patch(
                 "api.routers.auth.complete_authoritative_telegram_account_link",
                 new_callable=AsyncMock,
             ) as mock_complete_link, \
             patch("api.routers.auth.audit_log") as mock_audit_log:

            mock_complete_link.return_value = service_result

            result = await complete_telegram_account_link_internal(
                raw_request=request,
                response=response,
                db=db_sentinel,
            )

            self.assertEqual(request.body.await_count, 1)
            request.body.assert_awaited_once_with()

            self.assertEqual(mock_verify.call_count, 1)
            mock_verify.assert_called_once_with(request, raw_bytes)
            self.assertIs(mock_verify.call_args.args[0], request)
            self.assertEqual(mock_verify.call_args.args[1], raw_bytes)

            self.assertEqual(response.headers.get("Cache-Control"), "no-store")
            self.assertEqual(response.headers["Cache-Control"], "no-store")

            self.assertEqual(mock_complete_link.await_count, 1)
            mock_complete_link.assert_awaited_once_with(
                db_sentinel,
                command=command,
                source_server=SERVER_FOREIGN,
            )
            self.assertIs(mock_complete_link.await_args.args[0], db_sentinel)

            passed_command = mock_complete_link.await_args.kwargs["command"]
            self.assertIsInstance(passed_command, TelegramAccountLinkCommand)
            self.assertEqual(passed_command.command_id, fixed_command_id)
            self.assertEqual(passed_command.idempotency_key, fixed_idempotency_key)
            self.assertEqual(passed_command.mode, "existing_linked_user")
            self.assertEqual(passed_command.mobile_number, fixed_mobile_number)
            self.assertEqual(passed_command.telegram_id, fixed_telegram_id)
            self.assertEqual(passed_command.telegram_username, fixed_telegram_username)
            self.assertEqual(passed_command.telegram_full_name, fixed_telegram_full_name)
            self.assertEqual(passed_command.address, fixed_address)
            self.assertEqual(passed_command, command)

            self.assertEqual(
                mock_complete_link.await_args.kwargs["source_server"],
                SERVER_FOREIGN,
            )

            self.assertIsInstance(result, TelegramRegistrationCommandResponse)
            self.assertEqual(result.command_id, fixed_command_id)
            self.assertEqual(result.outcome, TelegramRegistrationOutcome.LINKED_EXISTING)
            self.assertEqual(result.outcome.value, "linked_existing")
            self.assertEqual(result.authoritative_user_id, fixed_authoritative_user_id)
            self.assertTrue(result.terminal)
            self.assertIs(result.terminal, True)

            self.assertEqual(mock_audit_log.call_count, 1)
            mock_audit_log.assert_called_once_with(
                "telegram_account_link.completed",
                target_type="telegram_account_link_command",
                target_id=str(fixed_command_id),
                result="success",
                extra={"outcome": "linked_existing"},
            )

    async def test_complete_telegram_account_link_internal_enabled_replay(
        self,
    ) -> None:
        replay_command_id = UUID("22222222-2222-2222-2222-222222222222")
        replay_idempotency_key = "telegram-account-link:" + "b" * 40
        replay_mobile_number = "09122222222"
        replay_telegram_id = 200000002
        replay_telegram_username = "linked_user_two"
        replay_telegram_full_name = "Linked User Two"
        replay_address = "Tehran Province, Tehran, District 2, Street 20, Unit 8"
        replay_authoritative_user_id = 84

        replay_command = TelegramAccountLinkCommand(
            command_id=replay_command_id,
            idempotency_key=replay_idempotency_key,
            mode="existing_linked_user",
            mobile_number=replay_mobile_number,
            telegram_id=replay_telegram_id,
            telegram_username=replay_telegram_username,
            telegram_full_name=replay_telegram_full_name,
            address=replay_address,
        )
        replay_raw_bytes = replay_command.model_dump_json().encode("utf-8")

        replay_request = MagicMock(spec=Request, name="request_sentinel_replay")
        replay_request.body = AsyncMock(return_value=replay_raw_bytes)
        replay_response = Response()
        replay_db_sentinel = MagicMock(spec=AsyncSession, name="db_sentinel_replay")

        replay_service_result = AuthoritativeTelegramAccountLinkResult(
            outcome=TelegramRegistrationOutcome.LINKED_EXISTING,
            authoritative_user_id=replay_authoritative_user_id,
            replayed=True,
            first_terminal_transition=False,
        )

        with patch.object(settings, "registration_sync_v2_enabled", True), \
             patch("api.routers.auth._verify_foreign_internal_command") as mock_verify, \
             patch(
                 "api.routers.auth.complete_authoritative_telegram_account_link",
                 new_callable=AsyncMock,
             ) as mock_complete_link, \
             patch("api.routers.auth.audit_log") as mock_audit_log:

            mock_complete_link.return_value = replay_service_result

            result = await complete_telegram_account_link_internal(
                raw_request=replay_request,
                response=replay_response,
                db=replay_db_sentinel,
            )

            self.assertEqual(replay_request.body.await_count, 1)
            replay_request.body.assert_awaited_once_with()

            self.assertEqual(mock_verify.call_count, 1)
            mock_verify.assert_called_once_with(replay_request, replay_raw_bytes)
            self.assertIs(mock_verify.call_args.args[0], replay_request)
            self.assertEqual(mock_verify.call_args.args[1], replay_raw_bytes)

            self.assertEqual(replay_response.headers.get("Cache-Control"), "no-store")
            self.assertEqual(replay_response.headers["Cache-Control"], "no-store")

            self.assertEqual(mock_complete_link.await_count, 1)
            mock_complete_link.assert_awaited_once_with(
                replay_db_sentinel,
                command=replay_command,
                source_server=SERVER_FOREIGN,
            )
            self.assertIs(mock_complete_link.await_args.args[0], replay_db_sentinel)

            passed_command = mock_complete_link.await_args.kwargs["command"]
            self.assertIsInstance(passed_command, TelegramAccountLinkCommand)
            self.assertEqual(passed_command.command_id, replay_command_id)
            self.assertEqual(passed_command.idempotency_key, replay_idempotency_key)
            self.assertEqual(passed_command.mode, "existing_linked_user")
            self.assertEqual(passed_command.mobile_number, replay_mobile_number)
            self.assertEqual(passed_command.telegram_id, replay_telegram_id)
            self.assertEqual(passed_command.telegram_username, replay_telegram_username)
            self.assertEqual(passed_command.telegram_full_name, replay_telegram_full_name)
            self.assertEqual(passed_command.address, replay_address)
            self.assertEqual(passed_command, replay_command)

            self.assertEqual(
                mock_complete_link.await_args.kwargs["source_server"],
                SERVER_FOREIGN,
            )

            self.assertIsInstance(result, TelegramRegistrationCommandResponse)
            self.assertEqual(result.command_id, replay_command_id)
            self.assertEqual(result.outcome, TelegramRegistrationOutcome.LINKED_EXISTING)
            self.assertEqual(result.outcome.value, "linked_existing")
            self.assertEqual(result.authoritative_user_id, replay_authoritative_user_id)
            self.assertTrue(result.terminal)
            self.assertIs(result.terminal, True)

            mock_audit_log.assert_not_called()
            self.assertEqual(mock_audit_log.call_count, 0)
