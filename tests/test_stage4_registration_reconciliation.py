import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException, Response
from pydantic import ValidationError

from api.routers import auth
from core.registration_contracts import (
    TelegramRegistrationCommand,
    TelegramRegistrationCommandResponse,
    TelegramRegistrationOutcome,
)
from core.services.authoritative_telegram_account_link_service import (
    AuthoritativeTelegramAccountLinkResult,
)
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, override_current_server
from core.services.authoritative_registration_service import AuthoritativeRegistrationResult
from core.services.telegram_registration_intent_service import (
    TelegramRegistrationIntentAttempt,
    TelegramRegistrationIntentError,
    intent_to_command,
)
from core.telegram_account_link_contracts import (
    TelegramAccountLinkCommand,
    account_link_command_hash,
    build_telegram_account_link_command,
)
from core.telegram_registration_reconciliation_worker import (
    _process_attempt,
    _retry_delay_seconds,
)
from core.services.telegram_registration_intent_service import RegistrationProjectionResolution
from core.trade_forwarding import _json_body


def _registration_command(**overrides):
    expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    values = {
        "command_id": uuid4(),
        "idempotency_key": f"telegram-registration:{uuid4().hex}",
        "invitation_token": f"INV-{uuid4().hex}",
        "mobile_number": "09121112233",
        "telegram_id": 8_100_001,
        "telegram_username": "stage4_user",
        "telegram_full_name": "Stage 4 User",
        "address": "Stage 4 exact address",
        "contact_verified_at": expiry - timedelta(minutes=20),
        "local_completed_at": expiry - timedelta(minutes=10),
        "invitation_expires_at_snapshot": expiry,
    }
    values.update(overrides)
    return TelegramRegistrationCommand(**values)


class _SessionContext:
    def __init__(self):
        self.session = AsyncMock()

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class Stage4ContractTests(unittest.TestCase):
    def test_account_link_command_is_deterministic_strict_and_preserves_exact_address(self):
        proof = datetime.now(timezone.utc)
        first = build_telegram_account_link_command(
            mode="link_token",
            link_token="x" * 40,
            mobile_number="۰۹۱۲۱۱۱۲۲۳۳",
            telegram_id=77,
            telegram_username="user",
            telegram_full_name="User Name",
            address="  exact address  ",
            contact_verified_at=proof,
        )
        second = build_telegram_account_link_command(
            mode="link_token",
            link_token="x" * 40,
            mobile_number="09121112233",
            telegram_id=77,
            telegram_username="user",
            telegram_full_name="User Name",
            address="  exact address  ",
            contact_verified_at=proof,
        )
        self.assertEqual(first.command_id, second.command_id)
        self.assertEqual(first.idempotency_key, second.idempotency_key)
        self.assertEqual(first.address, "  exact address  ")
        with self.assertRaises(ValidationError):
            TelegramAccountLinkCommand.model_validate(
                {**first.model_dump(mode="json"), "unexpected": True}
            )

    def test_account_link_receipt_hash_ignores_retry_snapshots_but_not_business_fields(self):
        first = build_telegram_account_link_command(
            mode="link_token",
            link_token="r" * 40,
            mobile_number="09121112233",
            telegram_id=77,
            telegram_username="first-profile",
            telegram_full_name="First Profile",
            address="exact address value",
            contact_verified_at=datetime.now(timezone.utc),
        )
        retry = first.model_copy(
            update={
                "telegram_username": "later-profile",
                "telegram_full_name": "Later Profile",
                "contact_verified_at": datetime.now(timezone.utc) + timedelta(minutes=5),
            }
        )
        changed_address = first.model_copy(update={"address": "different exact address"})

        self.assertEqual(first.command_id, retry.command_id)
        self.assertEqual(account_link_command_hash(first), account_link_command_hash(retry))
        self.assertNotEqual(
            account_link_command_hash(first),
            account_link_command_hash(changed_address),
        )
        with self.assertRaises(ValidationError):
            build_telegram_account_link_command(
                mode="existing_linked_user",
                link_token="x" * 40,
                mobile_number="09121112233",
                telegram_id=77,
                telegram_username=None,
                telegram_full_name=None,
                address="valid address value",
                contact_verified_at=None,
            )

    def test_intent_to_command_rejects_partial_state(self):
        command = _registration_command()
        ready = SimpleNamespace(
            id=command.command_id,
            idempotency_key=command.idempotency_key,
            invitation_token=command.invitation_token,
            normalized_mobile=command.mobile_number,
            telegram_id=command.telegram_id,
            telegram_username=command.telegram_username,
            telegram_full_name=command.telegram_full_name,
            address=command.address,
            contact_verified_at=command.contact_verified_at,
            completed_at=command.local_completed_at,
            invitation_expires_at_snapshot=command.invitation_expires_at_snapshot,
        )
        self.assertEqual(intent_to_command(ready), command)
        ready.address = None
        with self.assertRaisesRegex(TelegramRegistrationIntentError, "intent_not_ready"):
            intent_to_command(ready)

    def test_retry_backoff_is_deterministic_and_bounded(self):
        intent_id = uuid4()
        self.assertEqual(_retry_delay_seconds(intent_id, 2), _retry_delay_seconds(intent_id, 2))
        self.assertGreaterEqual(_retry_delay_seconds(intent_id, 1), 2.0)
        self.assertLessEqual(_retry_delay_seconds(intent_id, 100), 300.0)

    def test_proof_time_accepts_one_second_before_and_exact_expiry_but_not_after(self):
        from core.services import authoritative_registration_service as registration

        expiry = datetime.now(timezone.utc).replace(microsecond=0)
        invitation = SimpleNamespace(expires_at=expiry.replace(tzinfo=None))
        for proof_time in (expiry - timedelta(seconds=1), expiry):
            command = _registration_command(
                contact_verified_at=proof_time - timedelta(seconds=1),
                local_completed_at=proof_time,
                invitation_expires_at_snapshot=expiry,
            )
            request = registration.AuthoritativeRegistrationRequest.for_telegram(
                command=command,
                source_server=SERVER_FOREIGN,
                received_at=proof_time,
            )
            registration._validate_invitation_time(request, invitation)
        with self.assertRaises(ValidationError):
            _registration_command(
                contact_verified_at=expiry,
                local_completed_at=expiry + timedelta(seconds=1),
                invitation_expires_at_snapshot=expiry,
            )


class Stage4InternalEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_registration_endpoint_requires_signature_iran_and_foreign_source(self):
        command = _registration_command()
        body = _json_body(command.model_dump(mode="json")).encode()
        request = SimpleNamespace(
            body=AsyncMock(return_value=body),
            headers={"x-source-server": SERVER_FOREIGN},
        )
        with patch.object(auth, "verify_internal_signature", return_value=False):
            with self.assertRaises(HTTPException) as exc:
                await auth.reconcile_telegram_registration_internal(
                    request,
                    Response(),
                    db=AsyncMock(),
                )
        self.assertEqual(exc.exception.status_code, 401)

        with patch.object(auth, "verify_internal_signature", return_value=True), patch.object(
            auth, "current_server", return_value=SERVER_FOREIGN
        ):
            with self.assertRaises(HTTPException) as exc:
                await auth.reconcile_telegram_registration_internal(
                    request,
                    Response(),
                    db=AsyncMock(),
                )
        self.assertEqual(exc.exception.status_code, 403)

    async def test_registration_endpoint_feature_off_is_retryable_without_db_mutation(self):
        command = _registration_command()
        body = _json_body(command.model_dump(mode="json")).encode()
        request = SimpleNamespace(
            body=AsyncMock(return_value=body),
            headers={"x-source-server": SERVER_FOREIGN},
        )
        response = Response()
        with patch.object(auth, "verify_internal_signature", return_value=True), patch.object(
            auth, "current_server", return_value=SERVER_IRAN
        ), patch.object(
            auth.settings, "telegram_registration_reconciliation_enabled", False
        ), patch.object(
            auth, "complete_invitation_registration", new=AsyncMock()
        ) as complete:
            result = await auth.reconcile_telegram_registration_internal(
                request,
                response,
                db=AsyncMock(),
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(result.outcome, TelegramRegistrationOutcome.FEATURE_DISABLED)
        self.assertFalse(result.terminal)
        complete.assert_not_awaited()

    async def test_registration_endpoint_generates_trusted_iran_receive_time(self):
        command = _registration_command()
        body = _json_body(command.model_dump(mode="json")).encode()
        request = SimpleNamespace(
            body=AsyncMock(return_value=body),
            headers={"x-source-server": SERVER_FOREIGN},
        )
        response = Response()
        captured = {}

        async def complete(_db, registration_request):
            captured["request"] = registration_request
            return AuthoritativeRegistrationResult(
                outcome=TelegramRegistrationOutcome.CREATED,
                authoritative_user_id=9,
                first_terminal_transition=False,
            )

        before = datetime.now(timezone.utc)
        with patch.object(auth, "verify_internal_signature", return_value=True), patch.object(
            auth, "current_server", return_value=SERVER_IRAN
        ), patch.object(
            auth.settings, "telegram_registration_reconciliation_enabled", True
        ), patch.object(
            auth, "complete_invitation_registration", new=complete
        ):
            result = await auth.reconcile_telegram_registration_internal(
                request,
                response,
                db=AsyncMock(),
            )
        after = datetime.now(timezone.utc)
        self.assertEqual(result.outcome, TelegramRegistrationOutcome.CREATED)
        self.assertEqual(captured["request"].source_server, SERVER_FOREIGN)
        self.assertGreaterEqual(captured["request"].received_at, before)
        self.assertLessEqual(captured["request"].received_at, after)

    async def test_unknown_command_field_is_rejected_before_service(self):
        command = _registration_command().model_dump(mode="json")
        command["manual_review"] = True
        request = SimpleNamespace(
            body=AsyncMock(return_value=json.dumps(command).encode()),
            headers={"x-source-server": SERVER_FOREIGN},
        )
        with patch.object(auth, "verify_internal_signature", return_value=True), patch.object(
            auth, "current_server", return_value=SERVER_IRAN
        ), patch.object(auth, "complete_invitation_registration", new=AsyncMock()) as complete:
            with self.assertRaises(HTTPException) as exc:
                await auth.reconcile_telegram_registration_internal(
                    request,
                    Response(),
                    db=AsyncMock(),
                )
        self.assertEqual(exc.exception.status_code, 422)
        complete.assert_not_awaited()

    async def test_account_link_endpoint_uses_sync_v2_gate_and_strict_iran_service(self):
        command = build_telegram_account_link_command(
            mode="link_token",
            link_token="z" * 40,
            mobile_number="09121112233",
            telegram_id=91,
            telegram_username="linked_user",
            telegram_full_name="Linked User",
            address=None,
            contact_verified_at=datetime.now(timezone.utc),
        )
        body = _json_body(command.model_dump(mode="json")).encode()
        request = SimpleNamespace(
            body=AsyncMock(return_value=body),
            headers={"x-source-server": SERVER_FOREIGN},
        )
        response = Response()
        with patch.object(auth, "verify_internal_signature", return_value=True), patch.object(
            auth, "current_server", return_value=SERVER_IRAN
        ), patch.object(auth.settings, "registration_sync_v2_enabled", False), patch.object(
            auth, "complete_authoritative_telegram_account_link", new=AsyncMock()
        ) as complete:
            disabled = await auth.complete_telegram_account_link_internal(
                request,
                response,
                db=AsyncMock(),
            )
        self.assertEqual(response.status_code, 503)
        self.assertFalse(disabled.terminal)
        complete.assert_not_awaited()

        response = Response()
        with patch.object(auth, "verify_internal_signature", return_value=True), patch.object(
            auth, "current_server", return_value=SERVER_IRAN
        ), patch.object(auth.settings, "registration_sync_v2_enabled", True), patch.object(
            auth,
            "complete_authoritative_telegram_account_link",
            new=AsyncMock(
                return_value=AuthoritativeTelegramAccountLinkResult(
                    outcome=TelegramRegistrationOutcome.LINKED_EXISTING,
                    authoritative_user_id=44,
                )
            ),
        ) as complete:
            linked = await auth.complete_telegram_account_link_internal(
                request,
                response,
                db=AsyncMock(),
            )
        self.assertEqual(linked.outcome, TelegramRegistrationOutcome.LINKED_EXISTING)
        self.assertEqual(linked.authoritative_user_id, 44)
        complete.assert_awaited_once()


class Stage4WorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        command = _registration_command()
        self.attempt = TelegramRegistrationIntentAttempt(
            intent_id=command.command_id,
            attempt=3,
            command=command,
        )

    async def test_transport_failure_schedules_retry(self):
        with patch(
            "core.telegram_registration_reconciliation_worker.forward_telegram_registration_command",
            new=AsyncMock(return_value=(503, {"detail": "down"})),
        ), patch(
            "core.telegram_registration_reconciliation_worker._schedule_retry",
            new=AsyncMock(return_value="retry_wait"),
        ) as schedule:
            result = await _process_attempt(
                self.attempt,
                sync_wait_seconds=0,
                sync_poll_seconds=0.01,
            )
        self.assertEqual(result, "retry_wait")
        schedule.assert_awaited_once()

    async def test_protocol_failure_statuses_are_classified_for_operations(self):
        for status_code, expected in (
            (401, "authentication_configuration"),
            (403, "authentication_configuration"),
            (404, "mixed_version_or_route"),
            (422, "protocol_invalid_response"),
        ):
            with self.subTest(status_code=status_code), patch(
                "core.telegram_registration_reconciliation_worker.forward_telegram_registration_command",
                new=AsyncMock(return_value=(status_code, {"detail": "invalid"})),
            ), patch(
                "core.telegram_registration_reconciliation_worker._schedule_retry",
                new=AsyncMock(return_value="retry_wait"),
            ) as schedule:
                result = await _process_attempt(
                    self.attempt,
                    sync_wait_seconds=0,
                    sync_poll_seconds=0.01,
                )

            self.assertEqual(result, "retry_wait")
            self.assertEqual(schedule.await_args.kwargs["error_code"], expected)

    async def test_valid_terminal_domain_rejection_does_not_retry_forever(self):
        response = TelegramRegistrationCommandResponse(
            command_id=self.attempt.command.command_id,
            outcome=TelegramRegistrationOutcome.IDENTITY_CONFLICT,
        )
        session_context = _SessionContext()
        with patch(
            "core.telegram_registration_reconciliation_worker.forward_telegram_registration_command",
            new=AsyncMock(return_value=(422, response.model_dump(mode="json"))),
        ), patch(
            "core.telegram_registration_reconciliation_worker.AsyncSessionLocal",
            return_value=session_context,
        ), patch(
            "core.telegram_registration_reconciliation_worker.finalize_registration_intent",
            new=AsyncMock(return_value=True),
        ) as finalize, patch(
            "core.telegram_registration_reconciliation_worker.audit_log"
        ), patch(
            "core.telegram_registration_reconciliation_worker._schedule_retry",
            new=AsyncMock(),
        ) as schedule:
            result = await _process_attempt(
                self.attempt,
                sync_wait_seconds=0,
                sync_poll_seconds=0.01,
            )

        self.assertEqual(result, TelegramRegistrationOutcome.IDENTITY_CONFLICT.value)
        finalize.assert_awaited_once()
        schedule.assert_not_awaited()

    async def test_success_waits_for_projection_before_terminal_state(self):
        response = TelegramRegistrationCommandResponse(
            command_id=self.attempt.command.command_id,
            outcome=TelegramRegistrationOutcome.LINKED_EXISTING,
            authoritative_user_id=81,
        )
        with patch(
            "core.telegram_registration_reconciliation_worker.forward_telegram_registration_command",
            new=AsyncMock(return_value=(200, response.model_dump(mode="json"))),
        ), patch(
            "core.telegram_registration_reconciliation_worker._wait_for_projection",
            new=AsyncMock(return_value=None),
        ), patch(
            "core.telegram_registration_reconciliation_worker._schedule_retry",
            new=AsyncMock(return_value="retry_wait"),
        ) as schedule:
            result = await _process_attempt(
                self.attempt,
                sync_wait_seconds=0,
                sync_poll_seconds=0.01,
            )
        self.assertEqual(result, "retry_wait")
        self.assertEqual(schedule.await_args.kwargs["error_code"], "projection_pending")
        self.assertEqual(schedule.await_args.kwargs["authoritative_user_id"], 81)

    async def test_success_finalizes_only_after_projection(self):
        response = TelegramRegistrationCommandResponse(
            command_id=self.attempt.command.command_id,
            outcome=TelegramRegistrationOutcome.CREATED,
            authoritative_user_id=82,
        )
        session_context = _SessionContext()
        with patch(
            "core.telegram_registration_reconciliation_worker.forward_telegram_registration_command",
            new=AsyncMock(return_value=(200, response.model_dump(mode="json"))),
        ), patch(
            "core.telegram_registration_reconciliation_worker._wait_for_projection",
            new=AsyncMock(return_value=RegistrationProjectionResolution(local_user_id=42)),
        ), patch(
            "core.telegram_registration_reconciliation_worker.AsyncSessionLocal",
            return_value=session_context,
        ), patch(
            "core.telegram_registration_reconciliation_worker.finalize_registration_intent",
            new=AsyncMock(return_value=True),
        ) as finalize, patch(
            "core.telegram_registration_reconciliation_worker.audit_log"
        ):
            result = await _process_attempt(
                self.attempt,
                sync_wait_seconds=0,
                sync_poll_seconds=0.01,
            )
        self.assertEqual(result, TelegramRegistrationOutcome.CREATED.value)
        finalize.assert_awaited_once()
        self.assertEqual(finalize.await_args.kwargs["projected_user_id"], 42)
        session_context.session.commit.assert_awaited_once()


class Stage4BackgroundPlacementTests(unittest.TestCase):
    def test_job_factory_is_flagged_and_foreign_only(self):
        import main

        with override_current_server(SERVER_FOREIGN), patch.object(
            main.settings, "telegram_registration_reconciliation_enabled", False
        ):
            disabled_names = {name for name, _ in main._background_job_factories()}
        self.assertNotIn("telegram_registration_reconciliation", disabled_names)

        with override_current_server(SERVER_FOREIGN), patch.object(
            main.settings, "telegram_registration_reconciliation_enabled", True
        ):
            foreign_names = {name for name, _ in main._background_job_factories()}
        self.assertIn("telegram_registration_reconciliation", foreign_names)

        with override_current_server(SERVER_IRAN), patch.object(
            main.settings, "telegram_registration_reconciliation_enabled", True
        ):
            iran_names = {name for name, _ in main._background_job_factories()}
        self.assertNotIn("telegram_registration_reconciliation", iran_names)


class Stage4ForeignWriterGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_account_link_writer_fails_closed_when_sync_v2_is_enabled(self):
        from bot.handlers import link_account

        with patch.object(link_account.settings, "registration_sync_v2_enabled", True):
            with self.assertRaisesRegex(RuntimeError, "legacy_foreign_account_link_forbidden"):
                await link_account.finalize_account_link(
                    AsyncMock(),
                    SimpleNamespace(),
                    SimpleNamespace(),
                )


if __name__ == "__main__":
    unittest.main()
