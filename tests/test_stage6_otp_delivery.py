import json
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException, Response

from api.routers import auth
from core.enums import UserAccountStatus
from core.otp_sms_fallback_worker import run_otp_sms_fallback_cycle
from core.registration_contracts import (
    OTPDeliveryStateContract,
    OTPDeliveryStatus,
    TelegramOTPDeliveryCommand,
    TelegramOTPDeliveryOutcome,
    TelegramOTPDeliveryResponse,
)
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, override_current_server
from core.services.otp_delivery_state_service import OTPDeliveryClaim
from core.services.telegram_otp_delivery_service import deliver_telegram_otp_once
from core.sms import SMSDeliveryOutcome, send_otp_sms_result_async
from core.telegram_otp_transport import forward_telegram_otp_delivery
from core.utils import utc_now


def command(**overrides):
    values = {
        "otp_request_id": uuid4(),
        "telegram_id": 8_700_001,
        "otp_code": "12345",
        "expires_at": utc_now() + timedelta(seconds=120),
    }
    values.update(overrides)
    return TelegramOTPDeliveryCommand(**values)


def state(**overrides):
    request_id = overrides.pop("otp_request_id", uuid4())
    values = {
        "otp_request_id": request_id,
        "mobile_number": "09121112233",
        "code_key": "otp:09121112233",
        "telegram_id": 8_700_001,
        "created_at": utc_now(),
        "expires_at": utc_now() + timedelta(seconds=120),
    }
    values.update(overrides)
    return OTPDeliveryStateContract(**values)


class DedupeRedis:
    def __init__(self):
        self.values = {}

    async def set(self, key, value, *, ex=None, nx=False):
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)


class RequestRedis:
    async def get(self, _key):
        return None


class FakeExecuteResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeDB:
    def __init__(self, user):
        self.user = user

    async def execute(self, _stmt):
        return FakeExecuteResult(self.user)


class Stage6ContractAndForeignDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def test_contract_is_strict_and_never_accepts_non_five_digit_code(self):
        with self.assertRaises(ValueError):
            command(otp_code="1234")
        with self.assertRaises(ValueError):
            TelegramOTPDeliveryCommand.model_validate(
                {**command().model_dump(), "unexpected": True}
            )

    async def test_foreign_delivery_dedupes_exact_command_and_rejects_changed_replay(self):
        redis = DedupeRedis()
        original = command()
        gateway_result = SimpleNamespace(ok=True, status_code=200, error=None)
        with override_current_server(SERVER_FOREIGN), patch(
            "core.services.telegram_otp_delivery_service.telegram_gateway.send_message",
            new=AsyncMock(return_value=gateway_result),
        ) as send:
            first = await deliver_telegram_otp_once(redis, command=original)
            replay = await deliver_telegram_otp_once(redis, command=original)
            changed = await deliver_telegram_otp_once(
                redis,
                command=original.model_copy(update={"otp_code": "54321"}),
            )

        self.assertEqual(first.outcome, TelegramOTPDeliveryOutcome.SENT)
        self.assertEqual(replay.outcome, TelegramOTPDeliveryOutcome.DUPLICATE_SENT)
        self.assertEqual(changed.outcome, TelegramOTPDeliveryOutcome.INVALID)
        send.assert_awaited_once()
        self.assertNotIn("12345", " ".join(redis.values.values()))

    async def test_foreign_delivery_classifies_rate_limit_and_enforces_surface(self):
        result = SimpleNamespace(ok=False, status_code=429, error=None)
        with override_current_server(SERVER_FOREIGN), patch(
            "core.services.telegram_otp_delivery_service.telegram_gateway.send_message",
            new=AsyncMock(return_value=result),
        ):
            response = await deliver_telegram_otp_once(DedupeRedis(), command=command())
        self.assertEqual(response.outcome, TelegramOTPDeliveryOutcome.RATE_LIMITED)

        with override_current_server(SERVER_IRAN):
            with self.assertRaisesRegex(RuntimeError, "requires_foreign"):
                await deliver_telegram_otp_once(DedupeRedis(), command=command())

    async def test_internal_endpoint_rejects_wrong_surface_and_unknown_fields(self):
        cmd = command()
        request = SimpleNamespace(
            body=AsyncMock(return_value=json.dumps(cmd.model_dump(mode="json")).encode()),
            headers={"x-source-server": SERVER_IRAN},
        )
        with override_current_server(SERVER_IRAN), patch.object(
            auth, "verify_internal_signature", return_value=True
        ):
            with self.assertRaises(HTTPException) as exc:
                await auth.deliver_telegram_otp_internal(request, Response())
        self.assertEqual(exc.exception.status_code, 403)

        invalid_request = SimpleNamespace(
            body=AsyncMock(
                return_value=json.dumps(
                    {**cmd.model_dump(mode="json"), "unknown": True},
                    default=str,
                ).encode()
            ),
            headers={"x-source-server": SERVER_IRAN},
        )
        with override_current_server(SERVER_FOREIGN), patch.object(
            auth, "verify_internal_signature", return_value=True
        ):
            with self.assertRaises(HTTPException) as exc:
                await auth.deliver_telegram_otp_internal(invalid_request, Response())
        self.assertEqual(exc.exception.status_code, 422)

    async def test_internal_endpoint_returns_explicit_deduped_result_without_cache(self):
        cmd = command()
        request = SimpleNamespace(
            body=AsyncMock(return_value=json.dumps(cmd.model_dump(mode="json")).encode()),
            headers={"x-source-server": SERVER_IRAN},
        )
        api_response = Response()
        expected = TelegramOTPDeliveryResponse(
            otp_request_id=cmd.otp_request_id,
            outcome=TelegramOTPDeliveryOutcome.DUPLICATE_SENT,
        )
        with override_current_server(SERVER_FOREIGN), patch.object(
            auth, "verify_internal_signature", return_value=True
        ), patch.object(
            auth.settings, "telegram_login_otp_enabled", True
        ), patch.object(
            auth, "get_redis", new=AsyncMock(return_value=object())
        ), patch.object(
            auth, "deliver_telegram_otp_once", new=AsyncMock(return_value=expected)
        ), patch.object(auth, "audit_log"):
            result = await auth.deliver_telegram_otp_internal(request, api_response)
        self.assertEqual(result, expected)
        self.assertEqual(api_response.headers["Cache-Control"], "no-store")

    async def test_iran_transport_uses_canonical_signed_post_to_foreign(self):
        cmd = command()
        response = SimpleNamespace(
            status_code=200,
            text="{}",
            json=lambda: TelegramOTPDeliveryResponse(
                otp_request_id=cmd.otp_request_id,
                outcome=TelegramOTPDeliveryOutcome.SENT,
            ).model_dump(mode="json"),
        )

        class ClientContext:
            def __init__(self):
                self.post = AsyncMock(return_value=response)

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        client = ClientContext()
        with override_current_server(SERVER_IRAN), patch(
            "core.telegram_otp_transport.peer_server_url_for",
            return_value="https://foreign.example",
        ), patch(
            "core.telegram_otp_transport.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "core.telegram_otp_transport.sign_internal_payload",
            return_value="signature",
        ):
            status_code, body = await forward_telegram_otp_delivery(cmd)

        self.assertEqual(status_code, 200)
        self.assertEqual(body["outcome"], "sent")
        call = client.post.await_args
        self.assertEqual(
            call.args[0],
            "https://foreign.example/api/auth/internal/telegram-otp/deliver",
        )
        self.assertEqual(call.kwargs["headers"]["X-Source-Server"], SERVER_IRAN)
        self.assertEqual(call.kwargs["headers"]["X-Signature"], "signature")
        self.assertIn('"otp_code":"12345"', call.kwargs["content"])


class Stage6RequestAndCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_endpoint_selects_stage6_owner_when_flag_is_enabled(self):
        redis = DedupeRedis()
        user = SimpleNamespace(
            is_deleted=False,
            account_status=UserAccountStatus.ACTIVE,
            home_server=SERVER_IRAN,
            telegram_id=8_700_001,
        )
        expected = {"detail": "sent", "method": "telegram", "expires_in": 120}
        with patch.object(auth.settings, "telegram_login_otp_enabled", True), patch.object(
            auth, "get_redis", new=AsyncMock(return_value=redis)
        ), patch.object(
            auth, "assert_login_allowed_for_server", new=AsyncMock()
        ), patch.object(
            auth, "_request_stage6_login_otp", new=AsyncMock(return_value=expected)
        ) as stage6:
            result = await auth.request_otp(
                auth.OTPRequest(mobile_number="09121112233"),
                raw_request=SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1")),
                db=FakeDB(user),
            )
        self.assertEqual(result, expected)
        stage6.assert_awaited_once_with(redis, mobile="09121112233", user=user)

    async def test_telegram_ack_schedules_fallback_without_immediate_sms(self):
        otp_state = state()
        delivery = TelegramOTPDeliveryResponse(
            otp_request_id=otp_state.otp_request_id,
            outcome=TelegramOTPDeliveryOutcome.SENT,
        )
        user = SimpleNamespace(telegram_id=otp_state.telegram_id)
        with override_current_server(SERVER_IRAN), patch.object(
            auth.settings, "telegram_login_otp_enabled", True
        ), patch.object(
            auth.settings, "otp_sms_auto_fallback_enabled", True
        ), patch.object(
            auth.settings, "staging_log_otp_codes", False
        ), patch.object(
            auth, "_generate_otp_code", return_value="12345"
        ), patch.object(
            auth, "build_otp_delivery_state", return_value=otp_state
        ), patch.object(
            auth, "create_otp_delivery_state", new=AsyncMock(return_value=True)
        ), patch.object(
            auth, "arm_sms_fallback", new=AsyncMock(return_value=True)
        ) as arm, patch.object(
            auth,
            "forward_telegram_otp_delivery",
            new=AsyncMock(return_value=(200, delivery.model_dump(mode="json"))),
        ) as forward, patch.object(
            auth, "schedule_sms_fallback", new=AsyncMock(return_value=True)
        ) as schedule, patch.object(
            auth, "_deliver_stage6_sms", new=AsyncMock()
        ) as sms, patch.object(auth, "audit_log"):
            result = await auth._request_stage6_login_otp(
                RequestRedis(), mobile=otp_state.mobile_number, user=user
            )

        self.assertEqual(result["method"], "telegram")
        self.assertEqual(result["sms_fallback_in"], 40)
        self.assertEqual(forward.await_args.args[0].otp_code, "12345")
        arm.assert_awaited_once()
        schedule.assert_awaited_once()
        sms.assert_not_awaited()

    async def test_telegram_timeout_and_missing_telegram_use_same_code_immediate_sms(self):
        for telegram_id, forward_result in (
            (8_700_001, (504, {"detail": "timeout"})),
            (None, None),
        ):
            with self.subTest(telegram_id=telegram_id):
                otp_state = state(telegram_id=telegram_id)
                user = SimpleNamespace(telegram_id=telegram_id) if telegram_id else None
                redis = RequestRedis()
                with override_current_server(SERVER_IRAN), patch.object(
                    auth.settings, "telegram_login_otp_enabled", True
                ), patch.object(
                    auth.settings, "otp_sms_auto_fallback_enabled", True
                ), patch.object(
                    auth.settings, "staging_log_otp_codes", False
                ), patch.object(
                    auth, "_generate_otp_code", return_value="12345"
                ), patch.object(
                    auth, "build_otp_delivery_state", return_value=otp_state
                ), patch.object(
                    auth, "create_otp_delivery_state", new=AsyncMock(return_value=True)
                ), patch.object(
                    auth, "arm_sms_fallback", new=AsyncMock(return_value=True)
                ), patch.object(
                    auth,
                    "forward_telegram_otp_delivery",
                    new=AsyncMock(return_value=forward_result or (500, {})),
                ) as forward, patch.object(
                    auth,
                    "_deliver_stage6_sms",
                    new=AsyncMock(return_value=SMSDeliveryOutcome.ACCEPTED),
                ) as sms, patch.object(auth, "audit_log"):
                    result = await auth._request_stage6_login_otp(
                        redis, mobile=otp_state.mobile_number, user=user
                    )

                self.assertEqual(result["method"], "sms")
                sms.assert_awaited_once_with(redis, state=otp_state)
                self.assertEqual(forward.await_count, 1 if telegram_id else 0)

    async def test_recovery_is_armed_before_transport_and_schedule_failure_sends_sms(self):
        otp_state = state()
        delivery = TelegramOTPDeliveryResponse(
            otp_request_id=otp_state.otp_request_id,
            outcome=TelegramOTPDeliveryOutcome.SENT,
        )
        events = []

        async def arm(*_args, **_kwargs):
            events.append("arm")
            return True

        async def forward(*_args, **_kwargs):
            events.append("forward")
            return 200, delivery.model_dump(mode="json")

        async def send_sms(*_args, **_kwargs):
            events.append("sms")
            return SMSDeliveryOutcome.ACCEPTED

        with override_current_server(SERVER_IRAN), patch.object(
            auth.settings, "telegram_login_otp_enabled", True
        ), patch.object(
            auth.settings, "otp_sms_auto_fallback_enabled", True
        ), patch.object(
            auth.settings, "staging_log_otp_codes", False
        ), patch.object(
            auth, "_generate_otp_code", return_value="12345"
        ), patch.object(
            auth, "build_otp_delivery_state", return_value=otp_state
        ), patch.object(
            auth, "create_otp_delivery_state", new=AsyncMock(return_value=True)
        ), patch.object(
            auth, "arm_sms_fallback", new=AsyncMock(side_effect=arm)
        ), patch.object(
            auth, "forward_telegram_otp_delivery", new=AsyncMock(side_effect=forward)
        ), patch.object(
            auth, "schedule_sms_fallback", new=AsyncMock(return_value=False)
        ), patch.object(
            auth, "_deliver_stage6_sms", new=AsyncMock(side_effect=send_sms)
        ), patch.object(auth, "audit_log"):
            result = await auth._request_stage6_login_otp(
                RequestRedis(), mobile=otp_state.mobile_number, user=SimpleNamespace(
                    telegram_id=otp_state.telegram_id
                )
            )

        self.assertEqual(events, ["arm", "forward", "sms"])
        self.assertEqual(result["method"], "sms")

    async def test_arm_failure_skips_telegram_and_uses_immediate_sms(self):
        otp_state = state()
        with override_current_server(SERVER_IRAN), patch.object(
            auth.settings, "telegram_login_otp_enabled", True
        ), patch.object(
            auth.settings, "otp_sms_auto_fallback_enabled", True
        ), patch.object(
            auth.settings, "staging_log_otp_codes", False
        ), patch.object(
            auth, "_generate_otp_code", return_value="12345"
        ), patch.object(
            auth, "build_otp_delivery_state", return_value=otp_state
        ), patch.object(
            auth, "create_otp_delivery_state", new=AsyncMock(return_value=True)
        ), patch.object(
            auth, "arm_sms_fallback", new=AsyncMock(return_value=False)
        ), patch.object(
            auth, "forward_telegram_otp_delivery", new=AsyncMock()
        ) as forward, patch.object(
            auth,
            "_deliver_stage6_sms",
            new=AsyncMock(return_value=SMSDeliveryOutcome.ACCEPTED),
        ) as sms, patch.object(auth, "audit_log"):
            result = await auth._request_stage6_login_otp(
                RequestRedis(),
                mobile=otp_state.mobile_number,
                user=SimpleNamespace(telegram_id=otp_state.telegram_id),
            )

        self.assertEqual(result["method"], "sms")
        forward.assert_not_awaited()
        sms.assert_awaited_once_with(ANY, state=otp_state)

    async def test_explicit_sms_failure_cancels_but_ambiguous_outcome_does_not(self):
        for outcome, expected_cancel_count in (
            (SMSDeliveryOutcome.FAILED, 1),
            (SMSDeliveryOutcome.AMBIGUOUS, 0),
        ):
            with self.subTest(outcome=outcome):
                otp_state = state(telegram_id=None)
                with override_current_server(SERVER_IRAN), patch.object(
                    auth.settings, "telegram_login_otp_enabled", True
                ), patch.object(
                    auth.settings, "otp_sms_auto_fallback_enabled", True
                ), patch.object(
                    auth.settings, "staging_log_otp_codes", False
                ), patch.object(
                    auth, "_generate_otp_code", return_value="12345"
                ), patch.object(
                    auth, "build_otp_delivery_state", return_value=otp_state
                ), patch.object(
                    auth, "create_otp_delivery_state", new=AsyncMock(return_value=True)
                ), patch.object(
                    auth, "_deliver_stage6_sms", new=AsyncMock(return_value=outcome)
                ), patch.object(
                    auth, "cancel_otp_delivery", new=AsyncMock()
                ) as cancel, patch.object(auth, "audit_log"):
                    with self.assertRaises(HTTPException) as exc:
                        await auth._request_stage6_login_otp(
                            RequestRedis(), mobile=otp_state.mobile_number, user=None
                        )

                self.assertEqual(exc.exception.status_code, 500)
                self.assertEqual(cancel.await_count, expected_cancel_count)

    async def test_stage6_refuses_staging_otp_logging(self):
        with override_current_server(SERVER_IRAN), patch.object(
            auth.settings, "environment", "staging"
        ), patch.object(auth.settings, "staging_log_otp_codes", True):
            with self.assertRaises(HTTPException) as exc:
                await auth._request_stage6_login_otp(
                    RequestRedis(),
                    mobile="09121112233",
                    user=SimpleNamespace(telegram_id=123),
                )
        self.assertEqual(exc.exception.status_code, 503)

    async def test_legacy_resend_claims_structured_state_instead_of_second_send_path(self):
        otp_state = state()
        redis = SimpleNamespace(
            get=AsyncMock(side_effect=["12345", None]),
            ttl=AsyncMock(return_value=80),
            setex=AsyncMock(),
        )
        user = SimpleNamespace(
            is_deleted=False,
            account_status="active",
            home_server=SERVER_IRAN,
        )
        with patch.object(auth.settings, "telegram_login_otp_enabled", True), patch.object(
            auth, "get_redis", new=AsyncMock(return_value=redis)
        ), patch.object(
            auth, "assert_login_allowed_for_server", new=AsyncMock()
        ), patch.object(
            auth, "load_otp_delivery_state", new=AsyncMock(return_value=otp_state)
        ), patch.object(
            auth,
            "_deliver_stage6_sms",
            new=AsyncMock(return_value=SMSDeliveryOutcome.ACCEPTED),
        ) as deliver:
            result = await auth.resend_otp_sms(
                auth.OTPRequest(mobile_number=otp_state.mobile_number),
                raw_request=SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1")),
                db=FakeDB(user),
            )
        self.assertEqual(result["expires_in"], 80)
        deliver.assert_awaited_once_with(redis, state=otp_state)

    async def test_verify_replay_loses_atomic_consume_race_before_session_creation(self):
        redis = DedupeRedis()
        redis.values["otp:09121112233"] = "12345"
        with patch.object(auth.settings, "telegram_login_otp_enabled", True), patch.object(
            auth, "get_redis", new=AsyncMock(return_value=redis)
        ), patch.object(
            auth, "consume_otp_code", new=AsyncMock(return_value=False)
        ) as consume:
            with self.assertRaises(HTTPException) as exc:
                await auth.verify_otp(
                    auth.OTPVerify(mobile_number="09121112233", code="12345"),
                    raw_request=SimpleNamespace(
                        headers={},
                        client=SimpleNamespace(host="127.0.0.1"),
                    ),
                    db=FakeDB(None),
                )
        self.assertEqual(exc.exception.status_code, 400)
        consume.assert_awaited_once()


class Stage6AsyncSMSAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_sms_adapter_classifies_acceptance_and_ambiguous_timeout(self):
        accepted_response = SimpleNamespace(
            status_code=200,
            json=lambda: {
                "status": 1,
                "data": {"messageId": 77},
            },
        )

        class ClientContext:
            def __init__(self, response=None, error=None):
                self.client = SimpleNamespace(
                    post=AsyncMock(
                        return_value=response,
                        side_effect=error,
                    )
                )

            async def __aenter__(self):
                return self.client

            async def __aexit__(self, exc_type, exc, tb):
                return False

        with patch("core.sms.settings.smsir_api_key", "configured"), patch(
            "core.sms.settings.smsir_otp_template_id", "123"
        ), patch(
            "core.sms.httpx.AsyncClient",
            return_value=ClientContext(response=accepted_response),
        ):
            accepted = await send_otp_sms_result_async("09121112233", "12345")
        self.assertEqual(accepted, SMSDeliveryOutcome.ACCEPTED)

        with patch("core.sms.settings.smsir_api_key", "configured"), patch(
            "core.sms.settings.smsir_otp_template_id", "123"
        ), patch(
            "core.sms.httpx.AsyncClient",
            return_value=ClientContext(error=TimeoutError("timeout")),
        ):
            ambiguous = await send_otp_sms_result_async("09121112233", "12345")
        self.assertEqual(ambiguous, SMSDeliveryOutcome.AMBIGUOUS)


class Stage6WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_is_iran_only_claims_once_and_records_provider_outcome(self):
        otp_state = state()
        claim = OTPDeliveryClaim(
            request_id=otp_state.otp_request_id,
            mobile_number=otp_state.mobile_number,
            otp_code="12345",
        )
        with override_current_server(SERVER_IRAN), patch(
            "core.otp_sms_fallback_worker.settings.telegram_login_otp_enabled", True
        ), patch(
            "core.otp_sms_fallback_worker.settings.otp_sms_auto_fallback_enabled", True
        ), patch(
            "core.otp_sms_fallback_worker.get_redis_client", return_value=object()
        ), patch(
            "core.otp_sms_fallback_worker.due_otp_request_ids",
            new=AsyncMock(return_value=[otp_state.otp_request_id]),
        ), patch(
            "core.otp_sms_fallback_worker.load_otp_delivery_state",
            new=AsyncMock(return_value=otp_state),
        ), patch(
            "core.otp_sms_fallback_worker.claim_sms_delivery",
            new=AsyncMock(return_value=claim),
        ) as claim_mock, patch(
            "core.otp_sms_fallback_worker.send_otp_sms_result_async",
            new=AsyncMock(return_value=SMSDeliveryOutcome.AMBIGUOUS),
        ), patch(
            "core.otp_sms_fallback_worker.record_sms_delivery_result",
            new=AsyncMock(return_value=True),
        ) as record:
            report = await run_otp_sms_fallback_cycle()

        self.assertEqual(report.outcome_counts, {"ambiguous": 1})
        claim_mock.assert_awaited_once()
        self.assertTrue(claim_mock.await_args.kwargs["require_due"])
        self.assertEqual(record.await_args.kwargs["outcome"], OTPDeliveryStatus.AMBIGUOUS)

        with override_current_server(SERVER_FOREIGN), self.assertRaises(Exception):
            await run_otp_sms_fallback_cycle()

    def test_background_factory_places_fallback_only_on_iran(self):
        import main

        for server, expected in ((SERVER_IRAN, True), (SERVER_FOREIGN, False)):
            with self.subTest(server=server), override_current_server(server), patch.object(
                main.settings, "telegram_login_otp_enabled", True
            ), patch.object(main.settings, "otp_sms_auto_fallback_enabled", True):
                names = {name for name, _ in main._background_job_factories()}
            self.assertEqual("otp_sms_fallback" in names, expected)


class Stage6StaticSafetyTests(unittest.TestCase):
    def test_staging_deploy_never_enables_otp_logging(self):
        with open("scripts/deploy_staging.sh", encoding="utf-8") as handle:
            script = handle.read()
        self.assertNotIn("STAGING_LOG_OTP_CODES=true", script)
        self.assertNotIn("set_env_value STAGING_LOG_OTP_CODES true", script)
        self.assertIn("set_env_value STAGING_LOG_OTP_CODES false", script)
