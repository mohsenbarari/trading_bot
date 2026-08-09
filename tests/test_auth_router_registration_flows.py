import json
import unittest
from datetime import datetime, timedelta
from http.cookies import SimpleCookie
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient

from api.routers import auth as auth_router
from api.routers.auth import (
    RegisterComplete,
    RegisterOTPRequest,
    RegisterOTPVerify,
    RegistrationContextCompleteRequest,
    RegistrationContextExchangeRequest,
    RegistrationContextOTPVerifyRequest,
    RegistrationContextState,
    clear_registration_context,
    complete_registration_context,
    exchange_registration_context,
    read_registration_context,
    register_complete,
    register_otp_request,
    register_otp_verify,
    request_registration_context_otp,
    verify_registration_context_otp,
)
from core.registration_contracts import TelegramRegistrationOutcome
from core.services.authoritative_registration_service import (
    AuthoritativeRegistrationError,
    AuthoritativeRegistrationResult,
)
from models.session import Platform
from models.invitation import InvitationCompletionSurface, InvitationKind
from models.user import UserRole


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeDB:
    def __init__(self, execute_results=None, commit_side_effect=None):
        self.execute_results = list(execute_results or [])
        self.commit = AsyncMock(side_effect=commit_side_effect)
        self.flush = AsyncMock(side_effect=self._flush)
        self.refresh = AsyncMock(side_effect=self._refresh)
        self.rollback = AsyncMock()
        self.added = []

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)

    def add(self, item):
        self.added.append(item)

    async def _flush(self):
        for item in self.added:
            if getattr(item, "id", None) is None:
                item.id = 77

    async def _refresh(self, item):
        if getattr(item, "id", None) is None:
            item.id = 77
        return item


class FakeRedis:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.ttl_map = {key: 600 for key in self.values}
        self.setex_calls = []
        self.set_calls = []
        self.delete_calls = []
        self.expire_calls = []
        self.incr_calls = []

    async def get(self, key):
        return self.values.get(key)

    async def setex(self, key, ttl, value):
        self.setex_calls.append((key, ttl, value))
        self.values[key] = value
        self.ttl_map[key] = ttl

    async def set(self, key, value, *, ex=None, nx=False):
        self.set_calls.append((key, value, ex, nx))
        if nx and key in self.values:
            return None
        self.values[key] = value
        if ex is not None:
            self.ttl_map[key] = ex
        return True

    async def delete(self, key):
        self.delete_calls.append(key)
        self.values.pop(key, None)
        self.ttl_map.pop(key, None)

    async def incr(self, key):
        self.incr_calls.append(key)
        value = int(self.values.get(key, 0)) + 1
        self.values[key] = str(value)
        return value

    async def expire(self, key, ttl):
        self.expire_calls.append((key, ttl))
        return True

    async def ttl(self, key):
        return self.ttl_map.get(key, -2)


def make_request(headers=None, host="127.0.0.1"):
    return SimpleNamespace(headers=headers or {}, client=SimpleNamespace(host=host))


def make_context_request(
    *,
    cookie: str | None = None,
    origin: str | None = None,
    host: str = "testserver",
    forwarded_host: str | None = None,
) -> Request:
    headers = [(b"host", host.encode())]
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    if origin:
        headers.append((b"origin", origin.encode()))
    if forwarded_host:
        headers.append((b"x-forwarded-host", forwarded_host.encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/api/auth/registration-context",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 1234),
            "server": (host, 80),
        }
    )


def response_cookie_value(response, name: str) -> str:
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    return cookie[name].value


class AuthRouterRegistrationFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_invitation_loader_rejects_used_revoked_and_legacy_rows(self):
        base = {
            "is_used": False,
            "revoked_at": None,
            "kind": InvitationKind.STANDARD,
            "expires_at": datetime.utcnow() + timedelta(minutes=5),
        }
        for changes, detail in (
            ({"is_used": True}, "استفاده شده"),
            ({"revoked_at": datetime.utcnow()}, "لغو شده"),
            ({"kind": InvitationKind.LEGACY_UNKNOWN}, "قدیمی"),
        ):
            invitation = SimpleNamespace(**{**base, **changes})
            db = FakeDB([FakeExecuteResult(invitation)])
            with self.subTest(changes=changes), self.assertRaises(HTTPException) as exc:
                await auth_router._load_valid_invitation_by_token(db, "INV-test")
            self.assertIn(detail, exc.exception.detail)

        valid = SimpleNamespace(**base)
        db = FakeDB([FakeExecuteResult(valid)])
        loaded, accountant, customer = await auth_router._load_valid_invitation_by_token(
            db, "INV-valid"
        )
        self.assertIs(loaded, valid)
        self.assertIsNone(accountant)
        self.assertIsNone(customer)

    async def test_public_raw_registration_routes_are_unconditionally_retired_before_state_access(self):
        claimed_token = "INV-claimed-route-secret"
        unclaimed_token = "INV-unclaimed-route-secret"
        claimed_registration_token = "REG-claimed-route-secret"
        unclaimed_registration_token = "REG-unclaimed-route-secret"
        claim_state = RegistrationContextState(
            kind="invitation",
            invitation_token=claimed_token,
            progress="otp_verified",
        )
        claimed_redis = FakeRedis(
            {
                auth_router._registration_handoff_claim_key(claimed_token): (
                    auth_router.RegistrationHandoffClaim(
                        exchange_id="exchange_" + ("a" * 64),
                        context_handle="claimed-context-handle",
                        state=claim_state,
                    ).model_dump_json()
                ),
                auth_router._registration_handoff_claim_key(claimed_registration_token): (
                    auth_router.RegistrationHandoffClaim(
                        exchange_id="exchange_" + ("b" * 64),
                        context_handle="claimed-registration-context-handle",
                        state=claim_state.model_copy(update={"kind": "registration"}),
                    ).model_dump_json()
                ),
                f"reg_otp:{claimed_token}": "12345",
                f"reg_verified:{claimed_token}": "1",
                auth_router._registration_session_key(claimed_registration_token): claimed_token,
            }
        )
        initial_redis_values = dict(claimed_redis.values)
        redis_factory = AsyncMock(return_value=claimed_redis)
        invitation_loader = AsyncMock(
            side_effect=AssertionError("retired raw routes must not inspect the database")
        )
        registration_service = AsyncMock(
            side_effect=AssertionError("retired raw routes must not mutate registration state")
        )

        cases = (
            (
                "/api/auth/register-otp-request",
                lambda token: {"token": token},
                claimed_token,
                unclaimed_token,
            ),
            (
                "/api/auth/register-otp-verify",
                lambda token: {"token": token, "code": "12345"},
                claimed_token,
                unclaimed_token,
            ),
            (
                "/api/auth/register-complete",
                lambda token: {"token": token, "address": "Tehran secure address"},
                claimed_token,
                unclaimed_token,
            ),
            (
                "/api/auth/register-complete",
                lambda token: {
                    "registration_token": token,
                    "address": "Tehran secure address",
                },
                claimed_registration_token,
                unclaimed_registration_token,
            ),
        )
        app = FastAPI()
        app.include_router(auth_router.router, prefix="/api/auth")
        with patch("api.routers.auth.get_redis", new=redis_factory), patch(
            "api.routers.auth._load_valid_invitation_by_token",
            new=invitation_loader,
        ), patch(
            "api.routers.auth.complete_invitation_registration",
            new=registration_service,
        ), patch("api.routers.auth.send_otp_sms") as send_sms:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                for path, payload_factory, claimed, unclaimed in cases:
                    for disposition, token in (
                        ("claimed", claimed),
                        ("unclaimed", unclaimed),
                    ):
                        with self.subTest(path=path, disposition=disposition):
                            rejected = await client.post(path, json=payload_factory(token))
                        self.assertEqual(rejected.status_code, 410)
                        self.assertEqual(rejected.headers["Cache-Control"], "no-store")
                        self.assertNotIn(token, rejected.text)

        redis_factory.assert_not_awaited()
        invitation_loader.assert_not_awaited()
        registration_service.assert_not_awaited()
        send_sms.assert_not_called()
        self.assertEqual(claimed_redis.values, initial_redis_values)

    async def test_registration_context_exchange_is_one_shot_masked_and_cookie_hardened(self):
        raw_token = "INV-first-exchange-secret"
        opaque_handle = "opaque-cookie-handle"
        victim_exchange_id = "exchange_" + ("a" * 64)
        attacker_exchange_id = "exchange_" + ("b" * 64)
        invitation = SimpleNamespace(
            token=raw_token,
            account_name="user1",
            mobile_number="09120000000",
            role=UserRole.STANDARD,
            expires_at=datetime.utcnow() + timedelta(minutes=5),
        )
        redis = FakeRedis()
        loader = AsyncMock(return_value=(invitation, None, None))

        with patch.object(auth_router.settings, "environment", "production"), patch(
            "api.routers.auth.get_redis", new=AsyncMock(return_value=redis)
        ), patch(
            "api.routers.auth._load_valid_invitation_by_token", new=loader
        ), patch(
            "api.routers.auth.secrets.token_urlsafe", return_value=opaque_handle
        ):
            response = await exchange_registration_context(
                RegistrationContextExchangeRequest(
                    kind="invitation",
                    token=raw_token,
                    exchange_id=victim_exchange_id,
                ),
                raw_request=make_context_request(origin="http://testserver"),
                db=FakeDB(),
            )

            body_text = response.body.decode()
            self.assertNotIn(raw_token, body_text)
            self.assertNotIn(opaque_handle, body_text)
            self.assertNotIn("09120000000", body_text)
            self.assertIn("0912****000", body_text)
            self.assertEqual(json.loads(body_text)["progress"], "context_ready")
            cookie = response.headers["set-cookie"]
            self.assertIn("__Host-web_registration=", cookie)
            self.assertIn("HttpOnly", cookie)
            self.assertIn("Secure", cookie)
            self.assertIn("SameSite=strict", cookie)
            self.assertIn("Path=/", cookie)
            self.assertNotIn("Domain=", cookie)
            self.assertTrue(any(key.startswith("registration_context:") for key in redis.values))
            self.assertFalse(any(raw_token in key or opaque_handle in key for key in redis.values))

            replay_response = await exchange_registration_context(
                RegistrationContextExchangeRequest(
                    kind="invitation",
                    token=raw_token,
                    exchange_id=victim_exchange_id,
                ),
                raw_request=make_context_request(origin="http://testserver"),
                db=FakeDB(),
            )
            self.assertEqual(
                response_cookie_value(replay_response, "__Host-web_registration"),
                opaque_handle,
            )

            # Even after the victim proves OTP ownership, another holder of
            # the invitation bearer has a different random browser binding and
            # can never receive the verified opaque context.
            context_key = auth_router._registration_context_key(opaque_handle)
            verified_state = RegistrationContextState.model_validate_json(
                redis.values[context_key]
            ).model_copy(update={"progress": "otp_verified"})
            redis.values[context_key] = verified_state.model_dump_json()

            cookie_bound_replay = await exchange_registration_context(
                RegistrationContextExchangeRequest(
                    kind="invitation",
                    token=raw_token,
                    exchange_id=attacker_exchange_id,
                ),
                raw_request=make_context_request(
                    cookie=f"__Host-web_registration={opaque_handle}",
                    origin="http://testserver",
                ),
                db=FakeDB(),
            )
            self.assertEqual(
                response_cookie_value(
                    cookie_bound_replay,
                    "__Host-web_registration",
                ),
                opaque_handle,
            )
            self.assertEqual(
                json.loads(cookie_bound_replay.body)["progress"],
                "otp_verified",
            )

            with self.assertRaises(HTTPException) as replay:
                await exchange_registration_context(
                    RegistrationContextExchangeRequest(
                        kind="invitation",
                        token=raw_token,
                        exchange_id=attacker_exchange_id,
                    ),
                    raw_request=make_context_request(origin="http://testserver"),
                    db=FakeDB(),
                )
            with self.assertRaises(HTTPException) as wrong_cookie_replay:
                await exchange_registration_context(
                    RegistrationContextExchangeRequest(
                        kind="invitation",
                        token=raw_token,
                        exchange_id="exchange_" + ("c" * 64),
                    ),
                    raw_request=make_context_request(
                        cookie="__Host-web_registration=wrong-context-handle",
                        origin="http://testserver",
                    ),
                    db=FakeDB(),
                )
        self.assertEqual(replay.exception.status_code, 409)
        self.assertNotIn("Set-Cookie", replay.exception.headers)
        self.assertEqual(wrong_cookie_replay.exception.status_code, 409)
        self.assertNotIn("Set-Cookie", wrong_cookie_replay.exception.headers)

    async def test_registration_context_refresh_expiry_wrong_kind_and_origin_boundaries(self):
        handle = "refresh-context-handle"
        raw_token = "INV-refresh-secret"
        state = RegistrationContextState(
            kind="invitation",
            invitation_token=raw_token,
            progress="otp_requested",
        )
        key = auth_router._registration_context_key(handle)
        redis = FakeRedis({key: state.model_dump_json()})
        invitation = SimpleNamespace(
            token=raw_token,
            account_name="refresh_user",
            mobile_number="09121112233",
            role=UserRole.STANDARD,
            expires_at=datetime.utcnow() + timedelta(minutes=5),
        )

        with patch.object(auth_router.settings, "environment", "test"), patch(
            "api.routers.auth.get_redis", new=AsyncMock(return_value=redis)
        ), patch(
            "api.routers.auth._load_valid_invitation_by_token",
            new=AsyncMock(return_value=(invitation, None, None)),
        ):
            response = await read_registration_context(
                raw_request=make_context_request(
                    cookie=f"web_registration={handle}",
                    origin="http://testserver",
                ),
                db=FakeDB(),
            )
        payload = json.loads(response.body)
        self.assertEqual(payload["progress"], "otp_requested")
        self.assertEqual(payload["mobile_number"], "0912****233")
        self.assertNotIn(raw_token, response.body.decode())

        with patch.object(auth_router.settings, "environment", "test"), patch(
            "api.routers.auth.get_redis", new=AsyncMock(return_value=FakeRedis())
        ):
            with self.assertRaises(HTTPException) as expired:
                await read_registration_context(
                    raw_request=make_context_request(cookie=f"web_registration={handle}"),
                    db=FakeDB(),
                )
        self.assertEqual(expired.exception.status_code, 410)
        self.assertIn("Max-Age=0", expired.exception.headers["Set-Cookie"])

        with patch.object(auth_router.settings, "environment", "test"), patch(
            "api.routers.auth.get_redis", new=AsyncMock(return_value=FakeRedis())
        ):
            with self.assertRaises(HTTPException) as wrong_kind:
                await exchange_registration_context(
                    RegistrationContextExchangeRequest(
                        kind="registration",
                        token=raw_token,
                        exchange_id="wrong_kind_exchange",
                    ),
                    raw_request=make_context_request(),
                    db=FakeDB(),
                )
        self.assertEqual(wrong_kind.exception.status_code, 400)

        with self.assertRaises(HTTPException) as cross_origin:
            await exchange_registration_context(
                RegistrationContextExchangeRequest(
                    kind="invitation",
                    token=raw_token,
                    exchange_id="origin_exchange_1",
                ),
                raw_request=make_context_request(
                    origin="http://attacker.example",
                    forwarded_host="attacker.example",
                ),
                db=FakeDB(),
            )
        self.assertEqual(cross_origin.exception.status_code, 403)

    async def test_invitation_context_otp_and_completion_are_response_loss_safe(self):
        raw_token = "INV-otp-secret"
        first_handle = "first-context-handle"
        state = RegistrationContextState(
            kind="invitation",
            invitation_token=raw_token,
            progress="context_ready",
        )
        first_key = auth_router._registration_context_key(first_handle)
        redis = FakeRedis({first_key: state.model_dump_json()})
        invitation = SimpleNamespace(
            token=raw_token,
            account_name="otp_user",
            mobile_number="09120000000",
            role=UserRole.STANDARD,
            expires_at=datetime.utcnow() + timedelta(minutes=5),
        )
        request = make_context_request(cookie=f"web_registration={first_handle}")

        with patch.object(auth_router.settings, "environment", "test"), patch(
            "api.routers.auth.get_redis", new=AsyncMock(return_value=redis)
        ), patch(
            "api.routers.auth._load_valid_invitation_by_token",
            new=AsyncMock(return_value=(invitation, None, None)),
        ), patch(
            "api.routers.auth._generate_otp_code", return_value="12345"
        ), patch(
            "api.routers.auth.send_otp_sms", return_value=True
        ):
            request_receipt = await request_registration_context_otp(
                raw_request=request,
                db=FakeDB(),
            )
        self.assertNotIn(raw_token, request_receipt.body.decode())
        stored_state = RegistrationContextState.model_validate_json(redis.values[first_key])
        self.assertEqual(stored_state.progress, "otp_requested")

        # Simulate a lost response/crash after the OTP write but before the
        # context phase update. Refresh reconciles from the active OTP proof.
        redis.values[first_key] = state.model_dump_json()
        with patch.object(auth_router.settings, "environment", "test"), patch(
            "api.routers.auth.get_redis", new=AsyncMock(return_value=redis)
        ), patch(
            "api.routers.auth._load_valid_invitation_by_token",
            new=AsyncMock(return_value=(invitation, None, None)),
        ):
            request_loss_recovery = await read_registration_context(
                raw_request=request,
                db=FakeDB(),
            )
        self.assertEqual(json.loads(request_loss_recovery.body)["progress"], "otp_requested")

        with patch.object(auth_router.settings, "environment", "test"), patch(
            "api.routers.auth.get_redis", new=AsyncMock(return_value=redis)
        ), patch(
            "api.routers.auth._load_valid_invitation_by_token",
            new=AsyncMock(return_value=(invitation, None, None)),
        ):
            verify_receipt = await verify_registration_context_otp(
                RegistrationContextOTPVerifyRequest(code="12345"),
                raw_request=request,
                db=FakeDB(),
            )
        self.assertNotIn(raw_token, verify_receipt.body.decode())
        self.assertIn(first_key, redis.values)
        verified_state = RegistrationContextState.model_validate_json(redis.values[first_key])
        self.assertEqual(verified_state.progress, "otp_verified")
        verified_key = auth_router._registration_context_verified_key(first_handle)
        self.assertIn(verified_key, redis.values)
        self.assertLessEqual(redis.ttl_map[verified_key], redis.ttl_map[first_key])
        self.assertNotIn(f"reg_verified:{raw_token}", redis.values)

        # Lost verify response: the old cookie remains authoritative and a read
        # resumes the verified phase without replaying the OTP.
        with patch.object(auth_router.settings, "environment", "test"), patch(
            "api.routers.auth.get_redis", new=AsyncMock(return_value=redis)
        ), patch(
            "api.routers.auth._load_valid_invitation_by_token",
            new=AsyncMock(return_value=(invitation, None, None)),
        ):
            resumed = await read_registration_context(raw_request=request, db=FakeDB())
        self.assertEqual(json.loads(resumed.body)["progress"], "otp_verified")

        complete_mock = AsyncMock(
            return_value={
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "token_type": "bearer",
            }
        )
        with patch.object(auth_router.settings, "environment", "test"), patch(
            "api.routers.auth.get_redis", new=AsyncMock(return_value=redis)
        ), patch(
            "api.routers.auth._load_valid_invitation_by_token",
            new=AsyncMock(return_value=(invitation, None, None)),
        ), patch("api.routers.auth.register_complete", new=complete_mock):
            complete_receipt = await complete_registration_context(
                RegistrationContextCompleteRequest(address="Tehran complete address"),
                raw_request=request,
                db=FakeDB(),
            )
        self.assertNotIn(first_key, redis.values)
        self.assertEqual(response_cookie_value(complete_receipt, "web_registration"), first_handle)
        self.assertNotIn(raw_token, complete_receipt.body.decode())
        internal_request = complete_mock.await_args.args[0]
        self.assertEqual(internal_request.token, raw_token)
        self.assertEqual(
            complete_mock.await_args.kwargs["verified_invitation_token"],
            raw_token,
        )
        self.assertNotIn(verified_key, redis.values)

        # Lost completion response never re-runs registration and exposes only
        # the non-sensitive terminal outcome.
        with patch.object(auth_router.settings, "environment", "test"), patch(
            "api.routers.auth.get_redis", new=AsyncMock(return_value=redis)
        ):
            recovered_completion = await complete_registration_context(
                RegistrationContextCompleteRequest(address="Tehran complete address"),
                raw_request=request,
                db=FakeDB(),
            )
        self.assertEqual(json.loads(recovered_completion.body), {"status": "registration_complete"})
        complete_mock.assert_awaited_once()
        self.assertEqual(
            response_cookie_value(recovered_completion, "web_registration"),
            first_handle,
        )

        # Back/refresh at the invitation landing can carry a new tab binding;
        # the exact completion cookie remains authoritative until navigation is
        # acknowledged and must not be downgraded to a terminal claim error.
        with patch.object(auth_router.settings, "environment", "test"), patch(
            "api.routers.auth.get_redis", new=AsyncMock(return_value=redis)
        ):
            exchange_after_back = await exchange_registration_context(
                RegistrationContextExchangeRequest(
                    kind="invitation",
                    token=raw_token,
                    exchange_id="exchange_" + ("d" * 64),
                ),
                raw_request=request,
                db=FakeDB(),
            )
        self.assertEqual(
            json.loads(exchange_after_back.body),
            {"status": "registration_complete"},
        )
        self.assertEqual(
            response_cookie_value(exchange_after_back, "web_registration"),
            first_handle,
        )

    async def test_context_verify_proof_is_handle_bound_and_cannot_outlive_near_expiry_context(self):
        raw_token = "INV-near-expiry-secret"
        handle = "near-expiry-context-handle"
        context_key = auth_router._registration_context_key(handle)
        verified_key = auth_router._registration_context_verified_key(handle)
        state = RegistrationContextState(
            kind="invitation",
            invitation_token=raw_token,
            progress="otp_requested",
        )
        redis = FakeRedis(
            {
                context_key: state.model_dump_json(),
                f"reg_otp:{raw_token}": "12345",
            }
        )
        redis.ttl_map[context_key] = 2
        invitation = SimpleNamespace(
            token=raw_token,
            account_name="near_expiry",
            mobile_number="09120000000",
            role=UserRole.STANDARD,
            expires_at=datetime.utcnow() + timedelta(minutes=5),
        )
        request = make_context_request(cookie=f"web_registration={handle}")

        with patch.object(auth_router.settings, "environment", "test"), patch(
            "api.routers.auth.get_redis", new=AsyncMock(return_value=redis)
        ), patch(
            "api.routers.auth._load_valid_invitation_by_token",
            new=AsyncMock(return_value=(invitation, None, None)),
        ):
            response = await verify_registration_context_otp(
                RegistrationContextOTPVerifyRequest(code="12345"),
                raw_request=request,
                db=FakeDB(),
            )

        self.assertEqual(json.loads(response.body), {"detail": "کد تایید شد"})
        self.assertIn(verified_key, redis.values)
        self.assertLessEqual(redis.ttl_map[verified_key], 2)
        self.assertNotIn(f"reg_verified:{raw_token}", redis.values)

        # Simulate Redis expiry. No invitation-token proof remains that a raw
        # caller (or a late interleaving request) could turn into auth tokens.
        redis.values.pop(context_key, None)
        redis.ttl_map.pop(context_key, None)
        redis.values.pop(verified_key, None)
        redis.ttl_map.pop(verified_key, None)
        registration_service = AsyncMock()
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.complete_invitation_registration",
            new=registration_service,
        ):
            with self.assertRaises(HTTPException) as rejected:
                await register_complete(
                    RegisterComplete(token=raw_token, address="Tehran secure address"),
                    raw_request=make_request(),
                    db=FakeDB(),
                    verified_invitation_token="INV-no-longer-authoritative",
                )
        self.assertEqual(rejected.exception.status_code, 409)
        registration_service.assert_not_awaited()

    async def test_durable_web_completion_recovers_when_redis_marker_write_was_lost(self):
        raw_token = "INV-durable-web-completion"
        handle = "durable-completion-handle"
        claim_key = auth_router._registration_handoff_claim_key(raw_token)
        state = RegistrationContextState(
            kind="invitation",
            invitation_token=raw_token,
            progress="otp_verified",
            handoff_claim_key=claim_key,
        )
        claim = auth_router.RegistrationHandoffClaim(
            exchange_id="exchange_" + ("a" * 64),
            context_handle=handle,
            state=state,
        )
        context_key = auth_router._registration_context_key(handle)
        redis = FakeRedis(
            {
                context_key: state.model_dump_json(),
                claim_key: claim.model_dump_json(),
            }
        )
        completed_invitation = SimpleNamespace(
            token=raw_token,
            is_used=True,
            registered_user_id=77,
            completed_at=datetime.utcnow(),
            completed_via=InvitationCompletionSurface.WEB,
        )
        request = make_context_request(cookie=f"web_registration={handle}")

        with patch.object(auth_router.settings, "environment", "test"), patch(
            "api.routers.auth.get_redis", new=AsyncMock(return_value=redis)
        ):
            recovered = await read_registration_context(
                raw_request=request,
                db=FakeDB([FakeExecuteResult(completed_invitation)]),
            )

        self.assertEqual(json.loads(recovered.body), {"status": "registration_complete"})
        self.assertEqual(response_cookie_value(recovered, "web_registration"), handle)
        self.assertNotIn(context_key, redis.values)
        self.assertIn(auth_router._registration_context_completion_key(handle), redis.values)
        terminal_claim = auth_router.RegistrationHandoffClaim.model_validate_json(
            redis.values[claim_key]
        )
        self.assertTrue(terminal_claim.terminal)

        # A subsequent refresh reads only the bounded completion marker.
        with patch.object(auth_router.settings, "environment", "test"), patch(
            "api.routers.auth.get_redis", new=AsyncMock(return_value=redis)
        ):
            refreshed = await read_registration_context(
                raw_request=request,
                db=FakeDB(),
            )
        self.assertEqual(json.loads(refreshed.body), {"status": "registration_complete"})

    async def test_used_non_web_or_incomplete_rows_never_synthesize_web_completion(self):
        raw_token = "INV-not-proven-web-completion"
        for suffix, invitation in (
            (
                "telegram",
                SimpleNamespace(
                    token=raw_token,
                    is_used=True,
                    registered_user_id=77,
                    completed_at=datetime.utcnow(),
                    completed_via=InvitationCompletionSurface.TELEGRAM,
                ),
            ),
            (
                "incomplete",
                SimpleNamespace(
                    token=raw_token,
                    is_used=True,
                    registered_user_id=None,
                    completed_at=None,
                    completed_via=InvitationCompletionSurface.WEB,
                ),
            ),
        ):
            with self.subTest(surface=suffix):
                handle = f"not-web-{suffix}-handle"
                state = RegistrationContextState(
                    kind="invitation",
                    invitation_token=raw_token,
                    progress="otp_verified",
                )
                context_key = auth_router._registration_context_key(handle)
                redis = FakeRedis({context_key: state.model_dump_json()})
                with patch.object(auth_router.settings, "environment", "test"), patch(
                    "api.routers.auth.get_redis", new=AsyncMock(return_value=redis)
                ):
                    with self.assertRaises(HTTPException) as rejected:
                        await read_registration_context(
                            raw_request=make_context_request(
                                cookie=f"web_registration={handle}"
                            ),
                            db=FakeDB([FakeExecuteResult(invitation)]),
                        )
                self.assertEqual(rejected.exception.status_code, 410)
                self.assertNotIn(context_key, redis.values)
                self.assertNotIn(
                    auth_router._registration_context_completion_key(handle),
                    redis.values,
                )

    async def test_direct_registration_exchange_consumes_reg_bearer_and_clear_is_terminal(self):
        registration_token = "REG-direct-secret"
        invitation_token = "INV-direct-internal"
        handle = "direct-context-handle"
        legacy_key = auth_router._registration_session_key(registration_token)
        redis = FakeRedis({legacy_key: invitation_token})
        invitation = SimpleNamespace(
            token=invitation_token,
            account_name="direct_user",
            mobile_number="09123334444",
            role=UserRole.STANDARD,
            expires_at=datetime.utcnow() + timedelta(minutes=5),
        )

        with patch.object(auth_router.settings, "environment", "test"), patch(
            "api.routers.auth.get_redis", new=AsyncMock(return_value=redis)
        ), patch(
            "api.routers.auth._load_valid_invitation_by_token",
            new=AsyncMock(return_value=(invitation, None, None)),
        ), patch("api.routers.auth.secrets.token_urlsafe", return_value=handle):
            response = await exchange_registration_context(
                RegistrationContextExchangeRequest(
                    kind="registration",
                    token=registration_token,
                    exchange_id="login_exchange_123",
                ),
                raw_request=make_context_request(),
                db=FakeDB(),
            )

        payload = json.loads(response.body)
        self.assertEqual(payload["kind"], "registration")
        self.assertEqual(payload["progress"], "otp_verified")
        self.assertFalse(payload["requires_otp"])
        self.assertNotIn(registration_token, response.body.decode())
        self.assertNotIn(invitation_token, response.body.decode())
        self.assertNotIn(legacy_key, redis.values)

        # A retry repairs the narrow crash window between claim persistence and
        # legacy-session cleanup without creating a raw invitation proof.
        redis.values[legacy_key] = invitation_token
        redis.ttl_map[legacy_key] = 600
        with patch.object(auth_router.settings, "environment", "test"), patch(
            "api.routers.auth.get_redis", new=AsyncMock(return_value=redis)
        ), patch(
            "api.routers.auth._load_valid_invitation_by_token",
            new=AsyncMock(return_value=(invitation, None, None)),
        ):
            repaired = await exchange_registration_context(
                RegistrationContextExchangeRequest(
                    kind="registration",
                    token=registration_token,
                    exchange_id="login_exchange_123",
                ),
                raw_request=make_context_request(),
                db=FakeDB(),
            )
        self.assertEqual(json.loads(repaired.body)["progress"], "otp_verified")
        self.assertNotIn(f"reg_verified:{invitation_token}", redis.values)
        self.assertNotIn(legacy_key, redis.values)

        with patch.object(auth_router.settings, "environment", "test"), patch(
            "api.routers.auth.get_redis", new=AsyncMock(return_value=redis)
        ):
            clear_response = await clear_registration_context(
                raw_request=make_context_request(cookie=f"web_registration={handle}")
            )
        self.assertEqual(clear_response.status_code, 204)
        self.assertIn("Max-Age=0", clear_response.headers["set-cookie"])
        self.assertNotIn(auth_router._registration_context_key(handle), redis.values)

        with patch.object(auth_router.settings, "environment", "test"), patch(
            "api.routers.auth.get_redis", new=AsyncMock(return_value=redis)
        ):
            with self.assertRaises(HTTPException) as cleared_replay:
                await exchange_registration_context(
                    RegistrationContextExchangeRequest(
                        kind="registration",
                        token=registration_token,
                        exchange_id="login_exchange_123",
                    ),
                    raw_request=make_context_request(),
                    db=FakeDB(),
                )
        self.assertEqual(cleared_replay.exception.status_code, 410)

    async def test_register_otp_request_rejects_invalid_invitation_states_and_rate_limit(self):
        req = RegisterOTPRequest(token="abc")
        redis = FakeRedis()

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth._load_valid_invitation_by_token",
            new=AsyncMock(side_effect=HTTPException(status_code=404, detail="دعوت‌نامه نامعتبر است")),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await register_otp_request(req, db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 404)

        valid_invitation = SimpleNamespace(
            is_used=False,
            expires_at=datetime.utcnow() + timedelta(minutes=5),
            mobile_number="0912",
        )
        rate_limited_redis = FakeRedis({"otp_limit:0912": "1"})
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=rate_limited_redis)), patch(
            "api.routers.auth._load_valid_invitation_by_token",
            new=AsyncMock(return_value=(valid_invitation, None, None)),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await register_otp_request(req, db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 429)
        self.assertEqual(exc_info.exception.detail, "لطفاً ۲ دقیقه صبر کنید")

    async def test_register_otp_request_sets_otp_and_returns_success(self):
        req = RegisterOTPRequest(token="abc")
        invitation = SimpleNamespace(
            is_used=False,
            expires_at=datetime.utcnow() + timedelta(minutes=5),
            mobile_number="09120000000",
        )
        redis = FakeRedis()

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth._load_valid_invitation_by_token",
            new=AsyncMock(return_value=(invitation, None, None)),
        ), patch(
            "api.routers.auth._generate_otp_code",
            return_value="12345",
        ), patch("api.routers.auth.send_otp_sms", return_value=True) as send_sms_mock:
            result = await register_otp_request(req, db=FakeDB())

        self.assertEqual(
            redis.setex_calls,
            [("reg_otp:abc", 120, "12345"), ("otp_limit:09120000000", 120, "1")],
        )
        send_sms_mock.assert_called_once_with("09120000000", "12345")
        self.assertEqual(result, {"detail": "کد تایید ارسال شد", "expires_in": 120})

        redis = FakeRedis()
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth._load_valid_invitation_by_token",
            new=AsyncMock(return_value=(invitation, None, None)),
        ), patch(
            "api.routers.auth._generate_otp_code",
            return_value="12345",
        ), patch("api.routers.auth.send_otp_sms", return_value=False):
            with self.assertRaises(HTTPException) as exc_info:
                await register_otp_request(req, db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 500)
        self.assertEqual(exc_info.exception.detail, "خطا در ارسال پیامک")
        self.assertEqual(redis.delete_calls, ["reg_otp:abc", "otp_limit:09120000000"])

    async def test_register_otp_request_can_deliver_via_staging_log_without_sms(self):
        req = RegisterOTPRequest(token="abc")
        invitation = SimpleNamespace(
            is_used=False,
            expires_at=datetime.utcnow() + timedelta(minutes=5),
            mobile_number="09120000000",
        )
        redis = FakeRedis()

        with patch.object(register_otp_request.__globals__["settings"], "environment", "staging"), patch.object(
            register_otp_request.__globals__["settings"], "staging_log_otp_codes", True
        ), patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth._load_valid_invitation_by_token",
            new=AsyncMock(return_value=(invitation, None, None)),
        ), patch(
            "api.routers.auth._generate_otp_code",
            return_value="54321",
        ), patch("api.routers.auth.send_otp_sms") as send_sms_mock, self.assertLogs(
            "api.routers.auth", level="WARNING"
        ) as captured:
            result = await register_otp_request(req, db=FakeDB())

        self.assertEqual(
            redis.setex_calls,
            [("reg_otp:abc", 120, "54321"), ("otp_limit:09120000000", 120, "1")],
        )
        send_sms_mock.assert_not_called()
        self.assertEqual(result, {"detail": "کد تایید در لاگ staging ثبت شد", "expires_in": 120})
        self.assertIn("STAGING_AUTH_VALUE_FOR_TEST_ONLY", "\n".join(captured.output))
        self.assertIn("value=54321", "\n".join(captured.output))

    async def test_private_register_otp_verify_persists_only_the_supplied_handle_proof(self):
        req = RegisterOTPVerify(token="abc", code="12345")
        verification_key = auth_router._registration_context_verified_key("unit-context-handle")
        redis = FakeRedis()

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)):
            with self.assertRaises(HTTPException) as exc_info:
                await register_otp_verify(
                    req,
                    db=FakeDB(),
                    verification_key=verification_key,
                    verification_ttl=17,
                )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "کد تایید نامعتبر یا منقضی شده است")

        redis = FakeRedis({"reg_otp:abc": "12345"})
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)):
            result = await register_otp_verify(
                req,
                db=FakeDB(),
                verification_key=verification_key,
                verification_ttl=17,
            )

        self.assertEqual(redis.delete_calls, ["reg_otp:abc"])
        self.assertIn((verification_key, 17, "1"), redis.setex_calls)
        self.assertNotIn("reg_verified:abc", redis.values)
        self.assertEqual(result, {"detail": "کد تایید شد"})

    async def test_register_otp_verify_throttles_repeated_invalid_codes(self):
        req = RegisterOTPVerify(token="abc", code="00000")
        redis = FakeRedis({"reg_otp:abc": "12345"})
        verification_key = auth_router._registration_context_verified_key("throttle-handle")

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)):
            for _ in range(4):
                with self.assertRaises(HTTPException) as exc_info:
                    await register_otp_verify(
                        req,
                        db=FakeDB(),
                        verification_key=verification_key,
                        verification_ttl=30,
                    )
                self.assertEqual(exc_info.exception.status_code, 400)

            with self.assertRaises(HTTPException) as exc_info:
                await register_otp_verify(
                    req,
                    db=FakeDB(),
                    verification_key=verification_key,
                    verification_ttl=30,
                )

        self.assertEqual(exc_info.exception.status_code, 429)
        self.assertEqual(exc_info.exception.detail, "تعداد تلاش‌های ناموفق زیاد است. چند دقیقه دیگر دوباره تلاش کنید.")
        self.assertIn("reg_otp:abc", redis.delete_calls)
        self.assertNotIn("reg_otp:abc", redis.values)
        self.assertTrue(any(call[0].startswith("otp_verify_lock:subject:") for call in redis.setex_calls))

    async def test_private_register_complete_requires_matching_verified_context_token(self):
        req = RegisterComplete(token="abc", address="Tehran address")
        redis = FakeRedis()

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)):
            with self.assertRaises(HTTPException) as exc_info:
                await register_complete(
                    req,
                    raw_request=make_request(),
                    db=FakeDB(),
                    verified_invitation_token="different-context-token",
                )
        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertEqual(exc_info.exception.detail, "مرحله ثبت‌نام نامعتبر است")

        redis = FakeRedis()
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.complete_invitation_registration",
            new=AsyncMock(
                side_effect=AuthoritativeRegistrationError(
                    TelegramRegistrationOutcome.INVITATION_NOT_FOUND,
                    public_detail="دعوت‌نامه نامعتبر است",
                )
            ),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await register_complete(
                    req,
                    raw_request=make_request(),
                    db=FakeDB(),
                    verified_invitation_token="abc",
                )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "دعوت‌نامه نامعتبر است")

    async def test_register_complete_maps_authoritative_transaction_error_without_issuing_session(self):
        redis = FakeRedis()
        db = FakeDB()

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.complete_invitation_registration",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ), patch("api.routers.auth.handle_login_session", new=AsyncMock()) as session_mock:
            with self.assertRaises(HTTPException) as exc_info:
                await register_complete(
                    RegisterComplete(token="abc", address="Tehran address"),
                    raw_request=make_request(),
                    db=db,
                    verified_invitation_token="abc",
                )

        self.assertEqual(exc_info.exception.status_code, 500)
        self.assertEqual(exc_info.exception.detail, "خطا در ثبت کاربر")
        session_mock.assert_not_awaited()

    async def test_register_complete_rejects_incomplete_authoritative_result_before_session(self):
        redis = FakeRedis()
        incomplete_results = (
            AuthoritativeRegistrationResult(
                outcome=TelegramRegistrationOutcome.CREATED,
                authoritative_user_id=77,
                user=None,
            ),
            AuthoritativeRegistrationResult(
                outcome=TelegramRegistrationOutcome.CREATED,
                authoritative_user_id=None,
                user=SimpleNamespace(id=77),
            ),
        )
        for registration_result in incomplete_results:
            with self.subTest(registration_result=registration_result), patch(
                "api.routers.auth.get_redis", new=AsyncMock(return_value=redis)
            ), patch(
                "api.routers.auth.complete_invitation_registration",
                new=AsyncMock(return_value=registration_result),
            ), patch(
                "api.routers.auth.handle_login_session", new=AsyncMock()
            ) as session_mock:
                with self.assertRaises(HTTPException) as exc:
                    await register_complete(
                        RegisterComplete(token="abc", address="Tehran address"),
                        raw_request=make_request(),
                        db=FakeDB(),
                        verified_invitation_token="abc",
                    )
            self.assertEqual(exc.exception.status_code, 500)
            session_mock.assert_not_awaited()

    async def test_register_complete_creates_user_marks_invitation_and_issues_tokens(self):
        new_user = SimpleNamespace(
            id=77,
            account_name="user1",
            full_name="User One",
            mobile_number="09120000000",
            address="Tehran address",
            home_server="iran",
            has_bot_access=True,
            telegram_id=None,
        )
        registration_result = AuthoritativeRegistrationResult(
            outcome=TelegramRegistrationOutcome.CREATED,
            authoritative_user_id=77,
            user=new_user,
            announce_project_user=True,
            first_terminal_transition=True,
        )
        redis = FakeRedis({"reg_otp:abc": "12345"})
        db = FakeDB()
        request = make_request(
            headers={"user-agent": "Mobile Safari", "x-platform": "web", "x-device-name": "iPhone"},
            host="10.0.0.8",
        )
        session = SimpleNamespace(id="session-1")

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.complete_invitation_registration",
            new=AsyncMock(return_value=registration_result),
        ) as complete_mock, patch(
            "api.routers.auth.publish_project_user_joined_web_notifications",
            new=AsyncMock(),
        ) as notification_mock, patch(
            "api.routers.auth._login_home_server",
            return_value="foreign",
        ) as home_server_mock, patch(
            "api.routers.auth.create_refresh_token",
            return_value="refresh-token",
        ) as refresh_mock, patch(
            "api.routers.auth.handle_login_session",
            new=AsyncMock(return_value={"session": session}),
        ) as handle_session_mock, patch(
            "api.routers.auth.create_access_token",
            return_value="access-token",
        ) as access_mock:
            result = await register_complete(
                RegisterComplete(token="abc", address="Tehran address"),
                raw_request=request,
                db=db,
                verified_invitation_token="abc",
            )

        complete_mock.assert_awaited_once()
        self.assertIs(complete_mock.await_args.args[0], db)
        service_request = complete_mock.await_args.args[1]
        self.assertEqual(service_request.invitation_token, "abc")
        self.assertEqual(service_request.address, "Tehran address")
        self.assertEqual(service_request.source_surface.value, "webapp")
        self.assertEqual(service_request.identity_proof_type.value, "web_otp")
        home_server_mock.assert_not_called()
        notification_mock.assert_awaited_once_with(
            new_user_id=77,
            account_name="user1",
            full_name="User One",
        )
        self.assertEqual(redis.delete_calls, ["reg_otp:abc"])
        refresh_mock.assert_called_once_with(subject=77, expires_delta=timedelta(days=30))
        handle_session_mock.assert_awaited_once()
        self.assertEqual(handle_session_mock.await_args.args, (db, new_user, "refresh-token"))
        self.assertEqual(handle_session_mock.await_args.kwargs["device_name"], "iPhone")
        self.assertEqual(handle_session_mock.await_args.kwargs["device_ip"], "10.0.0.8")
        self.assertEqual(handle_session_mock.await_args.kwargs["platform"], Platform.WEB)
        self.assertEqual(handle_session_mock.await_args.kwargs["home_server"], "iran")
        access_mock.assert_called_once_with(
            subject=77,
            expires_delta=timedelta(minutes=60),
            session_id="session-1",
            server_id="iran",
        )
        self.assertEqual(
            result,
            {
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "token_type": "bearer",
            },
        )

    async def test_post_commit_cleanup_failure_does_not_block_tokens_and_session_failure_keeps_proof(self):
        new_user = SimpleNamespace(
            id=88,
            account_name="cleanup_user",
            full_name="Cleanup User",
            home_server="iran",
        )
        registration_result = AuthoritativeRegistrationResult(
            outcome=TelegramRegistrationOutcome.CREATED,
            authoritative_user_id=88,
            user=new_user,
            announce_project_user=False,
            first_terminal_transition=True,
        )
        redis = FakeRedis({"reg_otp:cleanup": "12345"})
        redis.delete = AsyncMock(side_effect=RuntimeError("redis unavailable"))
        session = SimpleNamespace(id="cleanup-session")

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.complete_invitation_registration",
            new=AsyncMock(return_value=registration_result),
        ), patch(
            "api.routers.auth.create_refresh_token",
            return_value="refresh-cleanup",
        ), patch(
            "api.routers.auth.handle_login_session",
            new=AsyncMock(return_value={"session": session}),
        ), patch(
            "api.routers.auth.create_access_token",
            return_value="access-cleanup",
        ), patch("api.routers.auth.logger.warning") as warning_mock:
            result = await register_complete(
                RegisterComplete(token="cleanup", address="Cleanup test address"),
                raw_request=make_request(),
                db=FakeDB(),
                verified_invitation_token="cleanup",
            )

        self.assertEqual(result["access_token"], "access-cleanup")
        self.assertEqual(warning_mock.call_count, 1)

        retry_redis = FakeRedis({"reg_otp:retry": "12345"})
        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=retry_redis)), patch(
            "api.routers.auth.complete_invitation_registration",
            new=AsyncMock(return_value=registration_result),
        ), patch(
            "api.routers.auth.create_refresh_token",
            return_value="refresh-retry",
        ), patch(
            "api.routers.auth.handle_login_session",
            new=AsyncMock(side_effect=RuntimeError("session commit failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "session commit failed"):
                await register_complete(
                    RegisterComplete(token="retry", address="Cleanup test address"),
                    raw_request=make_request(),
                    db=FakeDB(),
                    verified_invitation_token="retry",
                )
        self.assertEqual(retry_redis.delete_calls, [])

    async def test_private_register_complete_schema_excludes_legacy_registration_token(self):
        self.assertNotIn("registration_token", RegisterComplete.model_fields)
        req = RegisterComplete.model_validate(
            {"registration_token": "REG-123", "address": "Tehran address"}
        )
        redis_mock = AsyncMock()
        complete_mock = AsyncMock()

        with patch("api.routers.auth.get_redis", new=redis_mock), patch(
            "api.routers.auth.complete_invitation_registration",
            new=complete_mock,
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await register_complete(
                    req,
                    raw_request=make_request(),
                    db=FakeDB(),
                    verified_invitation_token="INV-123",
                )

        self.assertEqual(exc_info.exception.status_code, 400)
        redis_mock.assert_not_awaited()
        complete_mock.assert_not_awaited()

    async def test_private_register_complete_requires_kw_only_verified_invitation_token(self):
        with self.assertRaises(TypeError):
            await register_complete(
                RegisterComplete(token="INV-123", address="Tehran address"),
                raw_request=make_request(),
                db=FakeDB(),
            )

    async def test_register_complete_binds_pending_accountant_relation_and_disables_bot_access(self):
        relation = SimpleNamespace(
            accountant_user_id=77,
            status="active",
            activated_at=datetime.now(),
            deleted_at=None,
        )
        new_user = SimpleNamespace(id=77, home_server="iran", has_bot_access=False)
        registration_result = AuthoritativeRegistrationResult(
            outcome=TelegramRegistrationOutcome.CREATED,
            authoritative_user_id=77,
            user=new_user,
            accountant_relation=relation,
        )
        redis = FakeRedis()
        db = FakeDB()
        session = SimpleNamespace(id="session-acc")

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.complete_invitation_registration",
            new=AsyncMock(return_value=registration_result),
        ), patch(
            "api.routers.auth.create_refresh_token",
            return_value="refresh-acc",
        ), patch(
            "api.routers.auth.handle_login_session",
            new=AsyncMock(return_value={"session": session}),
        ), patch(
            "api.routers.auth.create_access_token",
            return_value="access-acc",
        ):
            result = await register_complete(
                RegisterComplete(token="ACCT-token", address="Tehran, Valiasr"),
                raw_request=make_request(),
                db=db,
                verified_invitation_token="ACCT-token",
            )

        self.assertFalse(new_user.has_bot_access)
        self.assertEqual(relation.accountant_user_id, 77)
        self.assertEqual(relation.status, "active")
        self.assertIsNotNone(relation.activated_at)
        self.assertEqual(result["access_token"], "access-acc")

    async def test_register_complete_rejects_missing_accountant_relation(self):
        redis = FakeRedis()

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.complete_invitation_registration",
            new=AsyncMock(
                side_effect=AuthoritativeRegistrationError(
                    TelegramRegistrationOutcome.INVALID_RELATION,
                    public_detail="دعوت‌نامه حسابدار نامعتبر یا منقضی شده است",
                )
            ),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await register_complete(
                    RegisterComplete(token="ACCT-token", address="Tehran address"),
                    raw_request=make_request(),
                    db=FakeDB(),
                    verified_invitation_token="ACCT-token",
                )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "دعوت‌نامه حسابدار نامعتبر یا منقضی شده است")

    async def test_register_complete_binds_pending_customer_relation_and_disables_bot_access(self):
        relation = SimpleNamespace(
            customer_user_id=77,
            management_name="mohsen",
            status="active",
            activated_at=datetime.now(),
            deleted_at=None,
        )
        new_user = SimpleNamespace(id=77, home_server="iran", full_name="mohsen", has_bot_access=False)
        registration_result = AuthoritativeRegistrationResult(
            outcome=TelegramRegistrationOutcome.CREATED,
            authoritative_user_id=77,
            user=new_user,
            customer_relation=relation,
        )
        redis = FakeRedis()
        db = FakeDB()
        session = SimpleNamespace(id="session-cust")

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.complete_invitation_registration",
            new=AsyncMock(return_value=registration_result),
        ), patch(
            "api.routers.auth.create_refresh_token",
            return_value="refresh-cust",
        ), patch(
            "api.routers.auth.handle_login_session",
            new=AsyncMock(return_value={"session": session}),
        ), patch(
            "api.routers.auth.create_access_token",
            return_value="access-cust",
        ):
            result = await register_complete(
                RegisterComplete(token="CUST-token", address="Tehran, Vanak"),
                raw_request=make_request(),
                db=db,
                verified_invitation_token="CUST-token",
            )

        self.assertEqual(new_user.full_name, "mohsen")
        self.assertFalse(new_user.has_bot_access)
        self.assertEqual(relation.customer_user_id, 77)
        self.assertEqual(relation.status, "active")
        self.assertIsNotNone(relation.activated_at)
        self.assertEqual(result["access_token"], "access-cust")

    async def test_register_complete_rejects_missing_customer_relation(self):
        redis = FakeRedis()

        with patch("api.routers.auth.get_redis", new=AsyncMock(return_value=redis)), patch(
            "api.routers.auth.complete_invitation_registration",
            new=AsyncMock(
                side_effect=AuthoritativeRegistrationError(
                    TelegramRegistrationOutcome.INVALID_RELATION,
                    public_detail="دعوت‌نامه مشتری نامعتبر یا منقضی شده است",
                )
            ),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await register_complete(
                    RegisterComplete(token="CUST-token", address="Tehran address"),
                    raw_request=make_request(),
                    db=FakeDB(),
                    verified_invitation_token="CUST-token",
                )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "دعوت‌نامه مشتری نامعتبر یا منقضی شده است")


if __name__ == "__main__":
    unittest.main()
