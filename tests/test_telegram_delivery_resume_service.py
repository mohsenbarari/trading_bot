from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from core.services import telegram_delivery_resume_service as service
from core.telegram_delivery_credentials import TelegramDeliveryCredentialRegistry
from core.telegram_delivery_preflight import (
    TelegramDeliveryPreflightFailedError,
    TelegramDeliveryPreflightIdentityReport,
    TelegramDeliveryPreflightRateLimitedError,
    TelegramDeliveryPreflightReport,
)


NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
REQUEST_ID = "resume-request-00000001"


def _settings(*, editor_enabled: bool = False):
    return SimpleNamespace(
        channel_id=-100123,
        telegram_delivery_queue_expected_channel_id=-100123,
        telegram_delivery_queue_channel_editor_enabled=editor_enabled,
        telegram_delivery_queue_retry_after_safety_seconds=0.25,
    )


def _registry(*, editor_enabled: bool = False):
    return TelegramDeliveryCredentialRegistry.from_values(
        primary_token="primary-secret",
        editor_enabled=editor_enabled,
        editor_token="editor-secret" if editor_enabled else None,
    )


def _preflight_report(*, editor_enabled: bool = False, identity_only: bool = False):
    roles = ["primary"]
    if editor_enabled:
        roles.append("channel_editor")
    fingerprints = _registry(editor_enabled=editor_enabled).fingerprints()
    identities = []
    for role in roles:
        permissions = (
            ("can_manage_chat", "can_post_messages", "can_edit_messages")
            if role == "primary"
            else ("can_manage_chat", "can_edit_messages")
        )
        identities.append(
            TelegramDeliveryPreflightIdentityReport(
                bot_identity=role,
                credential_fingerprint=fingerprints[role],
                bot_fingerprint=f"bot-{role}",
                channel_fingerprint="channel-safe",
                member_status=(
                    "durable_destination_pause" if identity_only else "administrator"
                ),
                effective_permissions=() if identity_only else permissions,
            )
        )
    return TelegramDeliveryPreflightReport(
        approved_bot_identities=tuple(roles),
        channel_fingerprint="channel-safe",
        identities=tuple(identities),
    )


def _prepared(*, editor_enabled: bool = False):
    identities = ("primary", "channel_editor") if editor_enabled else ("primary",)
    return service._PreparedResume(
        operation_id=17,
        state=service.TELEGRAM_RESUME_REQUESTED,
        destination_key="channel:-100123",
        bot_identities=identities,
        round_pause_job_ids=(31,),
        round_pause_evidence_hash="a" * 64,
        idempotent_replay=False,
    )


def _completed_report(*, replay: bool = False):
    return service.TelegramDeliveryResumeReport(
        operation_id=17,
        request_id=REQUEST_ID,
        destination_key="channel:-100123",
        state=service.TELEGRAM_RESUME_COMPLETED,
        resumed_job_ids=(31,),
        attempt_count=1,
        idempotent_replay=replay,
    )


class TelegramDeliveryResumeServiceTests(unittest.IsolatedAsyncioTestCase):
    def _limiter(self):
        return SimpleNamespace(
            clear_destination_gate_after_database_resume=AsyncMock(),
            extend_bot_cooldown=AsyncMock(),
        )

    async def test_runs_full_preflight_before_database_and_redis_activation(self):
        limiter = self._limiter()
        preflight = AsyncMock(return_value=_preflight_report(editor_enabled=True))
        prepared = _prepared(editor_enabled=True)
        with patch.object(
            service,
            "_prepare_resume",
            new=AsyncMock(return_value=prepared),
        ) as prepare, patch.object(
            service,
            "_apply_database_resume",
            new=AsyncMock(),
        ) as apply_db, patch.object(
            service,
            "_complete_after_redis",
            new=AsyncMock(return_value=_completed_report()),
        ) as complete:
            result = await service.resume_configured_telegram_channel(
                session_factory=lambda: None,
                current_server="foreign",
                settings=_settings(editor_enabled=True),
                credential_registry=_registry(editor_enabled=True),
                dispatch_limiter=limiter,
                request_id=REQUEST_ID,
                requested_by="on-call-1",
                preflight_runner=preflight,
                now_factory=lambda: NOW,
            )

        self.assertEqual(result.state, service.TELEGRAM_RESUME_COMPLETED)
        prepare.assert_awaited_once()
        preflight.assert_awaited_once_with(
            settings=_settings(editor_enabled=True),
            credential_registry=_registry(editor_enabled=True),
            bot_identities=("primary", "channel_editor"),
            identity_only_bot_identities=(),
        )
        apply_db.assert_awaited_once()
        limiter.clear_destination_gate_after_database_resume.assert_awaited_once_with(
            "channel:-100123"
        )
        complete.assert_awaited_once()

    async def test_identity_only_preflight_can_never_authorize_resume(self):
        limiter = self._limiter()
        record_failure = AsyncMock()
        with patch.object(
            service,
            "_prepare_resume",
            new=AsyncMock(return_value=_prepared()),
        ), patch.object(
            service,
            "_record_failure",
            new=record_failure,
        ), patch.object(
            service,
            "_apply_database_resume",
            new=AsyncMock(),
        ) as apply_db:
            with self.assertRaisesRegex(
                service.TelegramDeliveryResumeValidationError,
                "requires_full_channel_preflight",
            ):
                await service.resume_configured_telegram_channel(
                    session_factory=lambda: None,
                    current_server="foreign",
                    settings=_settings(),
                    credential_registry=_registry(),
                    dispatch_limiter=limiter,
                    request_id=REQUEST_ID,
                    requested_by="on-call-1",
                    preflight_runner=AsyncMock(
                        return_value=_preflight_report(identity_only=True)
                    ),
                    now_factory=lambda: NOW,
                )
        record_failure.assert_awaited_once()
        apply_db.assert_not_awaited()
        limiter.clear_destination_gate_after_database_resume.assert_not_awaited()

    async def test_preflight_failure_keeps_database_and_redis_untouched(self):
        limiter = self._limiter()
        failure = TelegramDeliveryPreflightFailedError(
            "telegram_preflight_primary_permissions_missing"
        )
        record_failure = AsyncMock()
        with patch.object(
            service,
            "_prepare_resume",
            new=AsyncMock(return_value=_prepared()),
        ), patch.object(
            service,
            "_record_failure",
            new=record_failure,
        ), patch.object(
            service,
            "_apply_database_resume",
            new=AsyncMock(),
        ) as apply_db:
            with self.assertRaises(TelegramDeliveryPreflightFailedError):
                await service.resume_configured_telegram_channel(
                    session_factory=lambda: None,
                    current_server="foreign",
                    settings=_settings(),
                    credential_registry=_registry(),
                    dispatch_limiter=limiter,
                    request_id=REQUEST_ID,
                    requested_by="on-call-1",
                    preflight_runner=AsyncMock(side_effect=failure),
                    now_factory=lambda: NOW,
                )
        record_failure.assert_awaited_once()
        apply_db.assert_not_awaited()
        limiter.clear_destination_gate_after_database_resume.assert_not_awaited()

    async def test_preflight_429_honors_authoritative_delay_without_polling(self):
        limiter = self._limiter()
        failure = TelegramDeliveryPreflightRateLimitedError(
            "telegram_preflight_rate_limited:primary:getMe",
            retry_after_seconds=17.0,
            bot_identity="primary",
            method="getMe",
        )
        with patch.object(
            service,
            "_prepare_resume",
            new=AsyncMock(return_value=_prepared()),
        ), patch.object(
            service,
            "_record_failure",
            new=AsyncMock(),
        ):
            with self.assertRaises(TelegramDeliveryPreflightRateLimitedError):
                await service.resume_configured_telegram_channel(
                    session_factory=lambda: None,
                    current_server="foreign",
                    settings=_settings(),
                    credential_registry=_registry(),
                    dispatch_limiter=limiter,
                    request_id=REQUEST_ID,
                    requested_by="on-call-1",
                    preflight_runner=AsyncMock(side_effect=failure),
                    now_factory=lambda: NOW,
                )
        limiter.extend_bot_cooldown.assert_awaited_once_with(
            "primary",
            until=NOW + timedelta(seconds=17.25),
        )
        limiter.clear_destination_gate_after_database_resume.assert_not_awaited()

    async def test_redis_failure_leaves_operation_incomplete_for_idempotent_retry(self):
        limiter = self._limiter()
        limiter.clear_destination_gate_after_database_resume.side_effect = RuntimeError(
            "redis down"
        )
        record_failure = AsyncMock()
        with patch.object(
            service,
            "_prepare_resume",
            new=AsyncMock(return_value=_prepared()),
        ), patch.object(
            service,
            "_apply_database_resume",
            new=AsyncMock(),
        ) as apply_db, patch.object(
            service,
            "_record_failure",
            new=record_failure,
        ):
            with self.assertRaisesRegex(
                service.TelegramDeliveryResumeIncompleteError,
                "redis_gate_not_cleared",
            ):
                await service.resume_configured_telegram_channel(
                    session_factory=lambda: None,
                    current_server="foreign",
                    settings=_settings(),
                    credential_registry=_registry(),
                    dispatch_limiter=limiter,
                    request_id=REQUEST_ID,
                    requested_by="on-call-1",
                    preflight_runner=AsyncMock(return_value=_preflight_report()),
                    now_factory=lambda: NOW,
                )
        apply_db.assert_awaited_once()
        record_failure.assert_awaited_once()

    async def test_completed_request_is_replayed_without_network_or_redis(self):
        limiter = self._limiter()
        replay = _completed_report(replay=True)
        preflight = AsyncMock()
        with patch.object(
            service,
            "_prepare_resume",
            new=AsyncMock(return_value=replay),
        ):
            result = await service.resume_configured_telegram_channel(
                session_factory=lambda: None,
                current_server="foreign",
                settings=_settings(),
                credential_registry=_registry(),
                dispatch_limiter=limiter,
                request_id=REQUEST_ID,
                requested_by="on-call-1",
                preflight_runner=preflight,
                now_factory=lambda: NOW,
            )
        self.assertTrue(result.idempotent_replay)
        preflight.assert_not_awaited()
        limiter.clear_destination_gate_after_database_resume.assert_not_awaited()

    async def test_iran_and_scope_mismatch_fail_before_database_or_network(self):
        limiter = self._limiter()
        preflight = AsyncMock()
        session_factory = unittest.mock.MagicMock()
        with self.assertRaisesRegex(
            service.TelegramDeliveryResumeValidationError,
            "foreign_local",
        ):
            await service.resume_configured_telegram_channel(
                session_factory=session_factory,
                current_server="iran",
                settings=_settings(),
                credential_registry=_registry(),
                dispatch_limiter=limiter,
                request_id=REQUEST_ID,
                requested_by="on-call-1",
                preflight_runner=preflight,
                now_factory=lambda: NOW,
            )
        session_factory.assert_not_called()
        preflight.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
