import asyncio
import json
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException, Response
import httpx
import yaml

from api.routers import auth
from core.enums import UserAccountStatus
from core.otp_sms_fallback_worker import run_otp_sms_fallback_cycle
from core.registration_contracts import (
    OTPDeliveryStatus,
    TelegramOTPDeliveryCommand,
    TelegramOTPDeliveryOutcome,
    TelegramOTPDeliveryResponse,
)
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, override_current_server
from core.services.otp_delivery_state_service import OTPDeliveryClaim, OTPDueSelection
from core.services.otp_delivery_state_service import (
    build_otp_delivery_state,
    validate_otp_delivery_runtime_settings,
)
from core.services.otp_sms_delivery_service import (
    OTPSMSAttemptResult,
    delivery_status,
    execute_claimed_otp_sms_delivery,
)
from core.services.telegram_otp_delivery_service import _text, deliver_telegram_otp_once
from core.sms import SMSDeliveryOutcome, send_otp_sms_result_async
from core.telegram_otp_transport import forward_telegram_otp_delivery
from core.utils import utc_now

TEST_MOBILE = "09121112233"
TEST_TELEGRAM_ID = 8_700_001
TEST_STATE_SECRET = "stage6-test-state-secret-0123456789abcdef"
_ORIGINAL_STATE_SECRET = auth.settings.otp_delivery_state_secret


def setUpModule():
    auth.settings.otp_delivery_state_secret = TEST_STATE_SECRET


def tearDownModule():
    auth.settings.otp_delivery_state_secret = _ORIGINAL_STATE_SECRET


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
    overrides.pop("telegram_id", None)
    created_at = overrides.pop("created_at", utc_now())
    expires_at = overrides.pop("expires_at", created_at + timedelta(seconds=120))
    built = build_otp_delivery_state(
        mobile=TEST_MOBILE,
        ttl_seconds=120,
        now=created_at,
    )
    return built.model_copy(
        update={
            "otp_request_id": request_id,
            "expires_at": expires_at,
            **overrides,
        }
    )


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
    def test_delivery_text_normalization_handles_absent_bytes_and_objects(self):
        self.assertIsNone(_text(None))
        self.assertEqual(_text(b"sent"), "sent")
        self.assertEqual(_text(123), "123")

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

    async def test_foreign_delivery_classifies_expiry_provider_error_and_unreachable(self):
        with override_current_server(SERVER_FOREIGN):
            expired = await deliver_telegram_otp_once(
                DedupeRedis(),
                command=command(expires_at=utc_now() - timedelta(seconds=1)),
            )
        self.assertEqual(expired.outcome, TelegramOTPDeliveryOutcome.INVALID)

        cases = (
            (RuntimeError("gateway failed"), TelegramOTPDeliveryOutcome.PROVIDER_ERROR),
            (SimpleNamespace(ok=False, status_code=400), TelegramOTPDeliveryOutcome.UNREACHABLE),
            (SimpleNamespace(ok=False, status_code=500), TelegramOTPDeliveryOutcome.PROVIDER_ERROR),
        )
        for gateway_result, expected in cases:
            side_effect = gateway_result if isinstance(gateway_result, Exception) else None
            return_value = None if side_effect else gateway_result
            with self.subTest(expected=expected), override_current_server(
                SERVER_FOREIGN
            ), patch(
                "core.services.telegram_otp_delivery_service.telegram_gateway.send_message",
                new=AsyncMock(side_effect=side_effect, return_value=return_value),
            ):
                result = await deliver_telegram_otp_once(
                    DedupeRedis(),
                    command=command(),
                )
            self.assertEqual(result.outcome, expected)

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

        with override_current_server(SERVER_FOREIGN), patch.object(
            auth, "verify_internal_signature", return_value=False
        ):
            with self.assertRaises(HTTPException) as exc:
                await auth.deliver_telegram_otp_internal(request, Response())
        self.assertEqual(exc.exception.status_code, 401)

        wrong_source = SimpleNamespace(
            body=AsyncMock(return_value=json.dumps(cmd.model_dump(mode="json")).encode()),
            headers={"x-source-server": SERVER_FOREIGN},
        )
        with override_current_server(SERVER_FOREIGN), patch.object(
            auth, "verify_internal_signature", return_value=True
        ):
            with self.assertRaises(HTTPException) as exc:
                await auth.deliver_telegram_otp_internal(wrong_source, Response())
        self.assertEqual(exc.exception.status_code, 401)

    async def test_internal_endpoint_feature_off_is_retryable(self):
        cmd = command()
        request = SimpleNamespace(
            body=AsyncMock(return_value=json.dumps(cmd.model_dump(mode="json")).encode()),
            headers={"x-source-server": SERVER_IRAN},
        )
        response = Response()
        with override_current_server(SERVER_FOREIGN), patch.object(
            auth, "verify_internal_signature", return_value=True
        ), patch.object(auth.settings, "telegram_login_otp_enabled", False), patch.object(
            auth, "deliver_telegram_otp_once", new=AsyncMock()
        ) as deliver:
            result = await auth.deliver_telegram_otp_internal(request, response)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(result.outcome, TelegramOTPDeliveryOutcome.FEATURE_DISABLED)
        deliver.assert_not_awaited()

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

    async def test_iran_transport_classifies_wrong_role_peer_and_network_failures(self):
        cmd = command()
        with override_current_server(SERVER_FOREIGN):
            status, _body = await forward_telegram_otp_delivery(cmd)
        self.assertEqual(status, 403)

        with override_current_server(SERVER_IRAN), patch(
            "core.telegram_otp_transport.peer_server_url_for",
            return_value=None,
        ):
            status, _body = await forward_telegram_otp_delivery(cmd)
        self.assertEqual(status, 503)

        class ClientContext:
            def __init__(self, outcome):
                self.outcome = outcome

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, *_args, **_kwargs):
                if isinstance(self.outcome, BaseException):
                    raise self.outcome
                return self.outcome

        request = httpx.Request("POST", "https://foreign.example")
        outcomes = (
            (httpx.ReadTimeout("timeout", request=request), 504),
            (httpx.ConnectError("offline", request=request), 503),
            (
                SimpleNamespace(
                    status_code=502,
                    text="invalid",
                    json=lambda: (_ for _ in ()).throw(ValueError("bad json")),
                ),
                502,
            ),
        )
        for outcome, expected in outcomes:
            with self.subTest(outcome=type(outcome).__name__), override_current_server(
                SERVER_IRAN
            ), patch(
                "core.telegram_otp_transport.peer_server_url_for",
                return_value="https://foreign.example",
            ), patch(
                "core.telegram_otp_transport.httpx.AsyncClient",
                return_value=ClientContext(outcome),
            ):
                status, body = await forward_telegram_otp_delivery(
                    cmd,
                    timeout_seconds=0.25,
                )
            self.assertEqual(status, expected)
            self.assertIn("detail", body)


class Stage6RequestAndCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    def test_auth_otp_helpers_cover_all_bounded_outcomes(self):
        for outcome, expected in (
            (auth.TelegramRegistrationOutcome.CREATED, "telegram_registration.reconciled_created"),
            (auth.TelegramRegistrationOutcome.LINKED_EXISTING, "telegram_registration.reconciled_linked_existing"),
            (auth.TelegramRegistrationOutcome.ALREADY_LINKED, "telegram_registration.reconciled_already_linked"),
            (auth.TelegramRegistrationOutcome.IDENTITY_CONFLICT, "telegram_registration.rejected"),
        ):
            self.assertEqual(auth._registration_outcome_event(outcome), expected)

        with patch.object(auth.secrets, "randbelow", return_value=0):
            self.assertEqual(auth._generate_otp_code(), "10000")

        telegram_state = state(telegram_delivery_status=OTPDeliveryStatus.PENDING)
        self.assertEqual(auth._otp_delivery_method(telegram_state), "telegram")
        sms_state = state(
            telegram_delivery_status=OTPDeliveryStatus.FAILED,
            sms_delivery_status=OTPDeliveryStatus.ACCEPTED,
        )
        self.assertEqual(auth._otp_delivery_method(sms_state), "sms")
        untouched = state(
            telegram_delivery_status=OTPDeliveryStatus.FAILED,
            sms_delivery_status=OTPDeliveryStatus.NOT_ATTEMPTED,
        )
        self.assertIsNone(auth._otp_delivery_method(untouched))

        with self.assertRaises(Exception):
            auth.OTPVerify(code="12345")

    async def test_stage6_sms_claim_paths_are_explicit_and_audited(self):
        otp_state = state()
        for refreshed, expected in (
            (otp_state.model_copy(update={"sms_delivery_status": OTPDeliveryStatus.ACCEPTED}), SMSDeliveryOutcome.ACCEPTED),
            (otp_state, SMSDeliveryOutcome.AMBIGUOUS),
            (None, SMSDeliveryOutcome.AMBIGUOUS),
        ):
            with self.subTest(refreshed=refreshed), patch.object(
                auth, "claim_sms_delivery", new=AsyncMock(return_value=None)
            ), patch.object(
                auth, "load_otp_delivery_state", new=AsyncMock(return_value=refreshed)
            ):
                self.assertEqual(
                    await auth._deliver_stage6_sms(object(), state=otp_state),
                    expected,
                )

        claim = SimpleNamespace(request_id=otp_state.otp_request_id)
        attempt = OTPSMSAttemptResult(
            outcome=SMSDeliveryOutcome.ACCEPTED,
            provider_attempted=True,
            result_recorded=True,
        )
        with patch.object(
            auth, "claim_sms_delivery", new=AsyncMock(return_value=claim)
        ), patch.object(
            auth, "execute_claimed_otp_sms_delivery", new=AsyncMock(return_value=attempt)
        ), patch.object(auth, "audit_log") as audit, patch.object(auth, "record_otp_event"):
            outcome = await auth._deliver_stage6_sms(object(), state=otp_state)
        self.assertEqual(outcome, SMSDeliveryOutcome.ACCEPTED)
        audit.assert_called_once()

    async def test_stage6_request_rejects_wrong_owner_invalid_runtime_and_orphan_active_code(self):
        with override_current_server(SERVER_FOREIGN), self.assertRaises(HTTPException) as exc:
            await auth._request_stage6_login_otp(
                RequestRedis(), mobile=TEST_MOBILE, user=None
            )
        self.assertEqual(exc.exception.status_code, 503)

        with override_current_server(SERVER_IRAN), patch.object(
            auth.settings, "staging_log_otp_codes", False
        ), patch.object(
            auth, "validate_otp_delivery_runtime_settings", side_effect=RuntimeError("bad")
        ):
            with self.assertRaises(HTTPException) as exc:
                await auth._request_stage6_login_otp(
                    RequestRedis(), mobile=TEST_MOBILE, user=None
                )
        self.assertEqual(exc.exception.status_code, 503)

        redis = SimpleNamespace(ttl=AsyncMock(return_value=37))
        with override_current_server(SERVER_IRAN), patch.object(
            auth.settings, "staging_log_otp_codes", False
        ), patch.object(auth, "_generate_otp_code", return_value="12345"), patch.object(
            auth, "create_otp_delivery_state", new=AsyncMock(return_value=False)
        ), patch.object(
            auth, "load_otp_delivery_state", new=AsyncMock(return_value=None)
        ):
            response = await auth._request_stage6_login_otp(
                redis, mobile=TEST_MOBILE, user=None
            )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(json.loads(response.body)["retry_after"], 37)

    async def test_stage6_transport_and_scheduler_exceptions_use_immediate_same_code_sms(self):
        for failure_point in ("arm", "forward", "schedule"):
            otp_state = state()
            delivery = TelegramOTPDeliveryResponse(
                otp_request_id=otp_state.otp_request_id,
                outcome=TelegramOTPDeliveryOutcome.SENT,
            )
            arm = AsyncMock(return_value=True)
            forward = AsyncMock(return_value=(200, delivery.model_dump(mode="json")))
            schedule = AsyncMock(return_value=True)
            {"arm": arm, "forward": forward, "schedule": schedule}[failure_point].side_effect = RuntimeError("down")
            with self.subTest(failure_point=failure_point), override_current_server(SERVER_IRAN), patch.object(
                auth.settings, "telegram_login_otp_enabled", True
            ), patch.object(auth.settings, "otp_sms_auto_fallback_enabled", True), patch.object(
                auth.settings, "staging_log_otp_codes", False
            ), patch.object(auth, "_generate_otp_code", return_value="12345"), patch.object(
                auth, "build_otp_delivery_state", return_value=otp_state
            ), patch.object(
                auth, "create_otp_delivery_state", new=AsyncMock(return_value=True)
            ), patch.object(auth, "arm_sms_fallback", new=arm), patch.object(
                auth, "forward_telegram_otp_delivery", new=forward
            ), patch.object(auth, "schedule_sms_fallback", new=schedule), patch.object(
                auth,
                "_deliver_stage6_sms",
                new=AsyncMock(return_value=SMSDeliveryOutcome.ACCEPTED),
            ) as sms, patch.object(auth, "audit_log"):
                result = await auth._request_stage6_login_otp(
                    RequestRedis(),
                    mobile=TEST_MOBILE,
                    user=SimpleNamespace(telegram_id=TEST_TELEGRAM_ID),
                )
            self.assertEqual(result["method"], "sms")
            sms.assert_awaited_once_with(ANY, state=otp_state)
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
        user = SimpleNamespace(telegram_id=TEST_TELEGRAM_ID)
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
        ) as sms, patch.object(auth, "audit_log") as audit:
            result = await auth._request_stage6_login_otp(
                RequestRedis(), mobile=TEST_MOBILE, user=user
            )

        self.assertEqual(result["method"], "telegram")
        self.assertEqual(result["sms_fallback_in"], 40)
        self.assertEqual(forward.await_args.args[0].otp_code, "12345")
        arm.assert_awaited_once()
        schedule.assert_awaited_once()
        sms.assert_not_awaited()
        audit.assert_any_call(
            "otp.sms_fallback_scheduled",
            target_type="otp_request",
            target_id=str(otp_state.otp_request_id),
            result="success",
            extra={"fallback_seconds": 40, "lifecycle_state": "scheduled"},
        )

    async def test_active_request_returns_structured_absolute_timing_without_new_code(self):
        otp_state = state(
            telegram_delivery_status=OTPDeliveryStatus.ACCEPTED,
            sms_fallback_at=utc_now() + timedelta(seconds=40),
        )
        redis = SimpleNamespace(ttl=AsyncMock(return_value=119))
        with override_current_server(SERVER_IRAN), patch.object(
            auth.settings, "telegram_login_otp_enabled", True
        ), patch.object(
            auth.settings, "otp_sms_auto_fallback_enabled", True
        ), patch.object(
            auth.settings, "server_mode", SERVER_IRAN
        ), patch.object(
            auth, "create_otp_delivery_state", new=AsyncMock(return_value=False)
        ), patch.object(
            auth, "load_otp_delivery_state", new=AsyncMock(return_value=otp_state)
        ), patch.object(
            auth, "_generate_otp_code", return_value="12345"
        ):
            response = await auth._request_stage6_login_otp(
                redis,
                mobile=TEST_MOBILE,
                user=SimpleNamespace(telegram_id=TEST_TELEGRAM_ID),
            )

        self.assertEqual(response.status_code, 429)
        payload = json.loads(response.body)
        self.assertEqual(payload["code"], "otp_active")
        self.assertEqual(payload["method"], "telegram")
        self.assertEqual(payload["otp_request_id"], str(otp_state.otp_request_id))
        self.assertIn("expires_at", payload)
        self.assertIn("sms_fallback_at", payload)

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
                        redis, mobile=TEST_MOBILE, user=user
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
                RequestRedis(), mobile=TEST_MOBILE, user=SimpleNamespace(
                    telegram_id=TEST_TELEGRAM_ID
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
                mobile=TEST_MOBILE,
                user=SimpleNamespace(telegram_id=TEST_TELEGRAM_ID),
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
                            RequestRedis(), mobile=TEST_MOBILE, user=None
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
                auth.OTPRequest(mobile_number=TEST_MOBILE),
                raw_request=SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1")),
                db=FakeDB(user),
            )
        self.assertGreaterEqual(result["expires_in"], 119)
        self.assertIn("expires_at", result)
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

    async def test_malformed_request_state_is_bounded_and_audited_without_verification_500(self):
        request_id = uuid4()
        with patch.object(auth.settings, "telegram_login_otp_enabled", True), patch.object(
            auth, "get_redis", new=AsyncMock(return_value=DedupeRedis())
        ), patch.object(
            auth, "load_otp_delivery_state", new=AsyncMock(side_effect=ValueError("bad state"))
        ), patch.object(auth, "audit_log") as audit:
            with self.assertRaises(HTTPException) as exc:
                await auth.verify_otp(
                    auth.OTPVerify(
                        otp_request_id=request_id,
                        mobile_number=TEST_MOBILE,
                        code="12345",
                    ),
                    raw_request=SimpleNamespace(
                        headers={},
                        client=SimpleNamespace(host="127.0.0.1"),
                    ),
                    db=FakeDB(None),
                )

        self.assertEqual(exc.exception.status_code, 400)
        self.assertEqual(exc.exception.detail, "کد تایید نامعتبر یا منقضی شده است")
        audit.assert_called_once_with(
            "otp.delivery_state_invalid",
            target_type="otp_request",
            target_id=str(request_id),
            result="denied",
            reason="invalid_delivery_state",
        )

    async def test_malformed_mobile_state_does_not_override_a_valid_code_or_raise_500(self):
        redis = DedupeRedis()
        redis.values[f"otp:{TEST_MOBILE}"] = "12345"
        with patch.object(auth.settings, "telegram_login_otp_enabled", True), patch.object(
            auth, "get_redis", new=AsyncMock(return_value=redis)
        ), patch.object(
            auth, "load_otp_delivery_state", new=AsyncMock(side_effect=KeyError("created_at"))
        ), patch.object(
            auth, "consume_otp_code", new=AsyncMock(return_value=True)
        ), patch.object(
            auth, "_ensure_otp_verify_not_locked", new=AsyncMock()
        ), patch.object(
            auth, "_find_pending_invitation_for_mobile", new=AsyncMock(return_value=None)
        ), patch.object(auth, "audit_log") as audit:
            with self.assertRaises(HTTPException) as exc:
                await auth.verify_otp(
                    auth.OTPVerify(mobile_number=TEST_MOBILE, code="12345"),
                    raw_request=SimpleNamespace(
                        headers={},
                        client=SimpleNamespace(host="127.0.0.1"),
                    ),
                    db=FakeDB(None),
                )

        self.assertEqual(exc.exception.status_code, 404)
        audit.assert_any_call(
            "otp.delivery_state_invalid",
            target_type="otp_request",
            target_id=None,
            result="denied",
            reason="invalid_delivery_state",
        )

    async def test_successful_atomic_consume_keeps_request_id_for_verification_audit(self):
        redis = DedupeRedis()
        redis.values[f"otp:{TEST_MOBILE}"] = "12345"
        otp_state = state()
        with patch.object(auth.settings, "telegram_login_otp_enabled", True), patch.object(
            auth, "get_redis", new=AsyncMock(return_value=redis)
        ), patch.object(
            auth, "load_otp_delivery_state", new=AsyncMock(return_value=otp_state)
        ), patch.object(
            auth, "consume_otp_code", new=AsyncMock(return_value=True)
        ), patch.object(
            auth, "_ensure_otp_verify_not_locked", new=AsyncMock()
        ), patch.object(
            auth, "_find_pending_invitation_for_mobile", new=AsyncMock(return_value=None)
        ), patch.object(auth, "audit_log") as audit:
            with self.assertRaises(HTTPException) as exc:
                await auth.verify_otp(
                    auth.OTPVerify(mobile_number=TEST_MOBILE, code="12345"),
                    raw_request=SimpleNamespace(
                        headers={},
                        client=SimpleNamespace(host="127.0.0.1"),
                    ),
                    db=FakeDB(None),
                )

        self.assertEqual(exc.exception.status_code, 404)
        audit.assert_any_call(
            "otp.verified",
            target_type="otp_request",
            target_id=str(otp_state.otp_request_id),
            result="success",
        )

    async def test_request_id_verification_rejects_unresolvable_or_mismatched_mobile(self):
        request_id = uuid4()
        otp_state = state(otp_request_id=request_id)
        redis = DedupeRedis()
        for mobile_result, supplied_mobile in (
            (RuntimeError("invalid state"), None),
            (TEST_MOBILE, "09129999999"),
        ):
            with self.subTest(mobile_result=mobile_result), patch.object(
                auth.settings, "telegram_login_otp_enabled", True
            ), patch.object(auth, "get_redis", new=AsyncMock(return_value=redis)), patch.object(
                auth,
                "_load_otp_delivery_state_for_verification",
                new=AsyncMock(return_value=otp_state),
            ), patch.object(
                auth,
                "mobile_for_delivery_state",
                side_effect=mobile_result if isinstance(mobile_result, Exception) else None,
                return_value=None if isinstance(mobile_result, Exception) else mobile_result,
            ):
                with self.assertRaises(HTTPException) as exc:
                    await auth.verify_otp(
                        auth.OTPVerify(
                            otp_request_id=request_id,
                            mobile_number=supplied_mobile,
                            code="12345",
                        ),
                        raw_request=SimpleNamespace(
                            headers={}, client=SimpleNamespace(host="127.0.0.1")
                        ),
                        db=FakeDB(None),
                    )
            self.assertEqual(exc.exception.status_code, 400)

    async def test_request_id_happy_identity_reaches_atomic_consume_without_reload(self):
        request_id = uuid4()
        otp_state = state(otp_request_id=request_id)
        redis = DedupeRedis()
        redis.values[f"otp:{TEST_MOBILE}"] = "12345"
        load = AsyncMock(return_value=otp_state)
        with patch.object(auth.settings, "telegram_login_otp_enabled", True), patch.object(
            auth, "get_redis", new=AsyncMock(return_value=redis)
        ), patch.object(
            auth, "_load_otp_delivery_state_for_verification", new=load
        ), patch.object(auth, "mobile_for_delivery_state", return_value=TEST_MOBILE), patch.object(
            auth, "_ensure_otp_verify_not_locked", new=AsyncMock()
        ), patch.object(auth, "consume_otp_code", new=AsyncMock(return_value=False)):
            with self.assertRaises(HTTPException) as exc:
                await auth.verify_otp(
                    auth.OTPVerify(
                        otp_request_id=request_id,
                        mobile_number=TEST_MOBILE,
                        code="12345",
                    ),
                    raw_request=SimpleNamespace(
                        headers={}, client=SimpleNamespace(host="127.0.0.1")
                    ),
                    db=FakeDB(None),
                )
        self.assertEqual(exc.exception.status_code, 400)
        load.assert_awaited_once_with(redis, request_id=request_id)

    async def test_request_id_verification_does_not_require_repeating_the_mobile(self):
        request_id = uuid4()
        otp_state = state(otp_request_id=request_id)
        redis = DedupeRedis()
        redis.values[f"otp:{TEST_MOBILE}"] = "12345"
        with patch.object(auth.settings, "telegram_login_otp_enabled", True), patch.object(
            auth, "get_redis", new=AsyncMock(return_value=redis)
        ), patch.object(
            auth,
            "_load_otp_delivery_state_for_verification",
            new=AsyncMock(return_value=otp_state),
        ), patch.object(
            auth, "mobile_for_delivery_state", return_value=TEST_MOBILE
        ), patch.object(
            auth, "_ensure_otp_verify_not_locked", new=AsyncMock()
        ), patch.object(auth, "consume_otp_code", new=AsyncMock(return_value=False)):
            with self.assertRaises(HTTPException) as exc:
                await auth.verify_otp(
                    auth.OTPVerify(
                        otp_request_id=request_id,
                        code="12345",
                    ),
                    raw_request=SimpleNamespace(
                        headers={}, client=SimpleNamespace(host="127.0.0.1")
                    ),
                    db=FakeDB(None),
                )

        self.assertEqual(exc.exception.status_code, 400)


    async def test_unvalidated_verify_request_without_identity_is_rejected(self):
        invalid = auth.OTPVerify.model_construct(
            mobile_number=None,
            otp_request_id=None,
            code="12345",
        )
        with patch.object(auth.settings, "telegram_login_otp_enabled", True), patch.object(
            auth, "get_redis", new=AsyncMock(return_value=DedupeRedis())
        ):
            with self.assertRaises(HTTPException) as exc:
                await auth.verify_otp(
                    invalid,
                    raw_request=SimpleNamespace(
                        headers={}, client=SimpleNamespace(host="127.0.0.1")
                    ),
                    db=FakeDB(None),
                )
        self.assertEqual(exc.exception.status_code, 400)


class Stage6ConfigurationTests(unittest.TestCase):
    def test_exact_timing_and_iran_state_secret_are_fail_closed(self):
        valid = SimpleNamespace(
            telegram_login_otp_enabled=True,
            otp_sms_auto_fallback_enabled=True,
            otp_ttl_seconds=120,
            otp_sms_auto_fallback_seconds=40,
            otp_delivery_state_secret=TEST_STATE_SECRET,
            server_mode=SERVER_IRAN,
        )
        validate_otp_delivery_runtime_settings(valid)

        for changed in (
            {"otp_ttl_seconds": 119},
            {"otp_ttl_seconds": 121},
            {"otp_sms_auto_fallback_seconds": 39},
            {"otp_sms_auto_fallback_seconds": 41},
            {"otp_delivery_state_secret": "too-short"},
        ):
            with self.subTest(changed=changed), self.assertRaises(RuntimeError):
                validate_otp_delivery_runtime_settings(
                    SimpleNamespace(**{**vars(valid), **changed})
                )

        validate_otp_delivery_runtime_settings(
            SimpleNamespace(**{**vars(valid), "telegram_login_otp_enabled": False})
        )


class Stage6ClaimedSMSProtocolTests(unittest.IsolatedAsyncioTestCase):
    def test_delivery_status_maps_all_provider_outcomes(self):
        self.assertEqual(
            delivery_status(SMSDeliveryOutcome.ACCEPTED),
            OTPDeliveryStatus.ACCEPTED,
        )
        self.assertEqual(
            delivery_status(SMSDeliveryOutcome.AMBIGUOUS),
            OTPDeliveryStatus.AMBIGUOUS,
        )
        self.assertEqual(
            delivery_status(SMSDeliveryOutcome.FAILED),
            OTPDeliveryStatus.FAILED,
        )

    def _claim(self):
        return OTPDeliveryClaim(
            claim_id=uuid4(),
            request_id=uuid4(),
            mobile_number=TEST_MOBILE,
            otp_code="12345",
            lease_until=utc_now() + timedelta(seconds=30),
        )

    async def test_provider_is_not_called_when_durable_start_marker_fails(self):
        claim = self._claim()
        with patch(
            "core.services.otp_sms_delivery_service.mark_sms_provider_attempt_started",
            new=AsyncMock(return_value=False),
        ), patch(
            "core.services.otp_sms_delivery_service.send_otp_sms_result_async",
            new=AsyncMock(),
        ) as send:
            result = await execute_claimed_otp_sms_delivery(object(), claim=claim)
        self.assertFalse(result.provider_attempted)
        self.assertFalse(result.result_recorded)
        self.assertEqual(result.outcome, SMSDeliveryOutcome.AMBIGUOUS)
        send.assert_not_awaited()

    async def test_result_write_loss_is_ambiguous_and_never_retries_provider(self):
        claim = self._claim()
        with patch(
            "core.services.otp_sms_delivery_service.mark_sms_provider_attempt_started",
            new=AsyncMock(return_value=True),
        ), patch(
            "core.services.otp_sms_delivery_service.send_otp_sms_result_async",
            new=AsyncMock(return_value=SMSDeliveryOutcome.ACCEPTED),
        ) as send, patch(
            "core.services.otp_sms_delivery_service.record_sms_delivery_result",
            new=AsyncMock(return_value=False),
        ):
            result = await execute_claimed_otp_sms_delivery(object(), claim=claim)
        self.assertTrue(result.provider_attempted)
        self.assertFalse(result.result_recorded)
        self.assertEqual(result.outcome, SMSDeliveryOutcome.AMBIGUOUS)
        send.assert_awaited_once()

    async def test_cancellation_after_provider_start_is_propagated_for_lease_recovery(self):
        claim = self._claim()
        with patch(
            "core.services.otp_sms_delivery_service.mark_sms_provider_attempt_started",
            new=AsyncMock(return_value=True),
        ), patch(
            "core.services.otp_sms_delivery_service.send_otp_sms_result_async",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ), patch(
            "core.services.otp_sms_delivery_service.record_sms_delivery_result",
            new=AsyncMock(),
        ) as record:
            with self.assertRaises(asyncio.CancelledError):
                await execute_claimed_otp_sms_delivery(object(), claim=claim)
        record.assert_not_awaited()

    async def test_provider_marker_send_and_result_exceptions_fail_closed(self):
        claim = self._claim()
        with patch(
            "core.services.otp_sms_delivery_service.mark_sms_provider_attempt_started",
            new=AsyncMock(side_effect=RuntimeError("redis unavailable")),
        ):
            marker = await execute_claimed_otp_sms_delivery(object(), claim=claim)
        self.assertFalse(marker.provider_attempted)

        cases = (
            (RuntimeError("provider"), None),
            (None, RuntimeError("redis")),
        )
        for send_error, record_error in cases:
            send = AsyncMock(
                side_effect=send_error,
                return_value=SMSDeliveryOutcome.FAILED,
            )
            record = AsyncMock(
                side_effect=record_error,
                return_value=True,
            )
            with self.subTest(send_error=send_error, record_error=record_error), patch(
                "core.services.otp_sms_delivery_service.mark_sms_provider_attempt_started",
                new=AsyncMock(return_value=True),
            ), patch(
                "core.services.otp_sms_delivery_service.send_otp_sms_result_async",
                new=send,
            ), patch(
                "core.services.otp_sms_delivery_service.record_sms_delivery_result",
                new=record,
            ):
                result = await execute_claimed_otp_sms_delivery(object(), claim=claim)
            self.assertEqual(result.outcome, SMSDeliveryOutcome.AMBIGUOUS)


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
            claim_id=uuid4(),
            request_id=otp_state.otp_request_id,
            mobile_number=TEST_MOBILE,
            otp_code="12345",
            lease_until=utc_now() + timedelta(seconds=30),
        )
        with override_current_server(SERVER_IRAN), patch(
            "core.otp_sms_fallback_worker.settings.telegram_login_otp_enabled", True
        ), patch(
            "core.otp_sms_fallback_worker.settings.otp_sms_auto_fallback_enabled", True
        ), patch(
            "core.otp_sms_fallback_worker.get_redis_client", return_value=object()
        ), patch(
            "core.otp_sms_fallback_worker.select_due_otp_requests",
            new=AsyncMock(return_value=OTPDueSelection(
                request_ids=(otp_state.otp_request_id,),
                isolated_counts={},
            )),
        ), patch(
            "core.otp_sms_fallback_worker.load_otp_delivery_state",
            new=AsyncMock(return_value=otp_state),
        ), patch(
            "core.otp_sms_fallback_worker.claim_sms_delivery",
            new=AsyncMock(return_value=claim),
        ) as claim_mock, patch(
            "core.otp_sms_fallback_worker.execute_claimed_otp_sms_delivery",
            new=AsyncMock(return_value=OTPSMSAttemptResult(
                outcome=SMSDeliveryOutcome.AMBIGUOUS,
                provider_attempted=True,
                result_recorded=True,
            )),
        ) as execute:
            report = await run_otp_sms_fallback_cycle()

        self.assertEqual(report.outcome_counts, {"ambiguous": 1})
        claim_mock.assert_awaited_once()
        self.assertTrue(claim_mock.await_args.kwargs["require_due"])
        execute.assert_awaited_once_with(ANY, claim=claim)

        with override_current_server(SERVER_FOREIGN), self.assertRaises(Exception):
            await run_otp_sms_fallback_cycle()

    async def test_worker_isolates_one_invalid_item_and_continues_valid_delivery(self):
        invalid_id = uuid4()
        otp_state = state()
        claim = OTPDeliveryClaim(
            claim_id=uuid4(),
            request_id=otp_state.otp_request_id,
            mobile_number=TEST_MOBILE,
            otp_code="12345",
            lease_until=utc_now() + timedelta(seconds=30),
        )
        load = AsyncMock(side_effect=[ValueError("corrupt state"), otp_state])
        isolate = AsyncMock(return_value="pending")
        with override_current_server(SERVER_IRAN), patch(
            "core.otp_sms_fallback_worker.settings.telegram_login_otp_enabled", True
        ), patch(
            "core.otp_sms_fallback_worker.settings.otp_sms_auto_fallback_enabled", True
        ), patch(
            "core.otp_sms_fallback_worker.get_redis_client", return_value=object()
        ), patch(
            "core.otp_sms_fallback_worker.select_due_otp_requests",
            new=AsyncMock(return_value=OTPDueSelection(
                request_ids=(invalid_id, otp_state.otp_request_id),
                isolated_counts={},
            )),
        ), patch(
            "core.otp_sms_fallback_worker.load_otp_delivery_state", new=load
        ), patch(
            "core.otp_sms_fallback_worker.isolate_invalid_otp_fallback_state",
            new=isolate,
        ), patch(
            "core.otp_sms_fallback_worker.claim_sms_delivery",
            new=AsyncMock(return_value=claim),
        ), patch(
            "core.otp_sms_fallback_worker.execute_claimed_otp_sms_delivery",
            new=AsyncMock(return_value=OTPSMSAttemptResult(
                outcome=SMSDeliveryOutcome.ACCEPTED,
                provider_attempted=True,
                result_recorded=True,
            )),
        ):
            report = await run_otp_sms_fallback_cycle()

        self.assertEqual(report.due_count, 2)
        self.assertEqual(report.outcome_counts, {"worker_exception": 1, "accepted": 1})
        isolate.assert_awaited_once_with(
            ANY,
            request_id=invalid_id,
            reason="worker_exception",
        )

    async def test_worker_does_not_terminally_isolate_transient_redis_failure(self):
        request_id = uuid4()
        isolate = AsyncMock()
        with override_current_server(SERVER_IRAN), patch(
            "core.otp_sms_fallback_worker.settings.telegram_login_otp_enabled", True
        ), patch(
            "core.otp_sms_fallback_worker.settings.otp_sms_auto_fallback_enabled", True
        ), patch(
            "core.otp_sms_fallback_worker.get_redis_client", return_value=object()
        ), patch(
            "core.otp_sms_fallback_worker.select_due_otp_requests",
            new=AsyncMock(return_value=OTPDueSelection(
                request_ids=(request_id,),
                isolated_counts={},
            )),
        ), patch(
            "core.otp_sms_fallback_worker.load_otp_delivery_state",
            new=AsyncMock(side_effect=ConnectionError("redis unavailable")),
        ), patch(
            "core.otp_sms_fallback_worker.isolate_invalid_otp_fallback_state",
            new=isolate,
        ):
            with self.assertRaises(ConnectionError):
                await run_otp_sms_fallback_cycle()

        isolate.assert_not_awaited()

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

    def test_staging_compose_maps_otp_state_secret_only_to_iran_app(self):
        compose = yaml.safe_load(
            Path("deploy/staging/docker-compose.staging.yml").read_text(encoding="utf-8")
        )
        services = compose["services"]
        self.assertEqual(
            services["app"]["environment"]["OTP_DELIVERY_STATE_SECRET"],
            "${IRAN_OTP_DELIVERY_STATE_SECRET:-}",
        )
        self.assertEqual(
            services["app"]["environment"]["IRAN_OTP_DELIVERY_STATE_SECRET"],
            "",
        )
        for name, service in services.items():
            if name == "app" or not service.get("env_file"):
                continue
            with self.subTest(service=name):
                environment = service.get("environment") or {}
                self.assertEqual(environment.get("OTP_DELIVERY_STATE_SECRET"), "")
                self.assertEqual(environment.get("IRAN_OTP_DELIVERY_STATE_SECRET"), "")

        example = Path("deploy/staging/env.staging.example").read_text(encoding="utf-8")
        self.assertIn("\nIRAN_OTP_DELIVERY_STATE_SECRET=\n", example)
        self.assertNotIn("\nOTP_DELIVERY_STATE_SECRET=", example)
