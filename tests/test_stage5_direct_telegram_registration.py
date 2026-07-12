import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.redis import RedisStorage

from bot.handlers import start
from bot.states import Registration
from core.enums import UserRole
from core.registration_contracts import normalize_registration_mobile_number
from core.services import telegram_registration_intent_service as intent_service
from models.invitation import InvitationKind
from models.telegram_registration_intent import TelegramRegistrationIntentStatus


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeSession:
    def __init__(self, invitation=None, *, events=None, commit_error=None):
        self.invitation = invitation
        self.events = events if events is not None else []
        self.commit_error = commit_error

    async def execute(self, _statement):
        return FakeResult(self.invitation)

    async def commit(self):
        self.events.append("commit")
        if self.commit_error:
            raise self.commit_error


class FakeLookupSession:
    def __init__(self, *values):
        self.values = list(values)

    async def get(self, _model, _identity):
        return self.values.pop(0) if self.values else None


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeState:
    def __init__(self, data=None, *, events=None, storage=None, key=None, current_state=None):
        self.data = dict(data or {})
        self.events = events if events is not None else []
        self.states = []
        self.storage = storage
        self.key = key
        self.current_state = current_state

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.events.append("clear")
        self.data.clear()
        self.current_state = None

    async def get_state(self):
        return self.current_state

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def set_state(self, value):
        self.states.append(value)
        self.current_state = getattr(value, "state", value)


class ClearFailingState(FakeState):
    async def clear(self):
        self.events.append("clear_failed")
        raise RuntimeError("redis unavailable")


def invitation(**overrides):
    values = {
        "id": 41,
        "token": "INV-stage5-token",
        "mobile_number": "09121112233",
        "role": UserRole.STANDARD,
        "kind": InvitationKind.STANDARD,
        "is_used": False,
        "registered_user_id": None,
        "completed_at": None,
        "completed_via": None,
        "revoked_at": None,
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def message(*, telegram_id=7001, phone="09121112233", contact_user_id=None, text=None):
    answer = AsyncMock(return_value=SimpleNamespace(message_id=77))
    return SimpleNamespace(
        bot=SimpleNamespace(),
        chat=SimpleNamespace(id=88, type="private"),
        from_user=SimpleNamespace(
            id=telegram_id,
            username="stage5_user",
            full_name="Stage 5 User",
        ),
        contact=SimpleNamespace(
            user_id=telegram_id if contact_user_id is None else contact_user_id,
            phone_number=phone,
        ),
        text=text,
        answer=answer,
    )


def callback(state_message=None, *, telegram_id=7001):
    return SimpleNamespace(
        from_user=SimpleNamespace(
            id=telegram_id,
            username="stage5_user",
            full_name="Stage 5 User",
        ),
        message=state_message or message(telegram_id=telegram_id),
        answer=AsyncMock(),
    )


def ready_state_data(inv=None, *, telegram_id=7001, address="Tehran exact address"):
    inv = inv or invitation()
    return {
        start._REGISTRATION_STATE_TOKEN: inv.token,
        start._REGISTRATION_STATE_MOBILE: "09121112233",
        start._REGISTRATION_STATE_EXPIRES_AT: inv.expires_at.isoformat(),
        start._REGISTRATION_STATE_TELEGRAM_ID: telegram_id,
        start._REGISTRATION_STATE_CONTACT_VERIFIED_AT: (
            datetime.now(timezone.utc) - timedelta(seconds=5)
        ).isoformat(),
        start._REGISTRATION_STATE_ADDRESS: address,
    }


class Stage5MobileContractTests(unittest.TestCase):
    def test_user_facing_webapp_url_switches_to_canonical_iran_origin_with_new_flow(self):
        with patch.object(start.settings, "frontend_url", "https://legacy.example"), patch.object(
            start.settings, "invitation_contract_v2_enabled", False
        ), patch.object(start.settings, "registration_sync_v2_enabled", False), patch.object(
            start.settings, "telegram_direct_registration_enabled", False
        ), patch.object(
            start.settings, "telegram_registration_reconciliation_enabled", False
        ):
            self.assertEqual(start._user_facing_webapp_url(), "https://legacy.example")

        with patch.object(start.settings, "invitation_contract_v2_enabled", True), patch.object(
            start, "user_facing_webapp_url", return_value="https://iran.example"
        ):
            self.assertEqual(start._user_facing_webapp_url(), "https://iran.example")

    def test_normalizes_supported_iran_mobile_representations(self):
        for raw in (
            "09121112233",
            "+989121112233",
            "00989121112233",
            "989121112233",
            "9121112233",
            "۰۹۱۲۱۱۱۲۲۳۳",
            " 09121112233 ",
        ):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_registration_mobile_number(raw), "09121112233")

    def test_rejects_ambiguous_or_non_mobile_values(self):
        for raw in (None, "", "0912 111 2233", "0912-111-2233", "08121112233", "0912111223"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    normalize_registration_mobile_number(raw)


class Stage5DirectEntryTests(unittest.IsolatedAsyncioTestCase):
    def test_customer_relation_lifecycle_distinguishes_telegram_first_and_web_first(self):
        pending_invitation = invitation(
            token="CUST-lifecycle-pending",
            kind=InvitationKind.CUSTOMER,
        )
        pending_relation = SimpleNamespace(
            invitation_token=pending_invitation.token,
            customer_tier="tier1",
            status="pending",
            customer_user_id=None,
            deleted_at=None,
            expires_at=pending_invitation.expires_at,
        )
        self.assertTrue(
            start._customer_relation_allows_direct_registration(
                pending_invitation,
                pending_relation,
            )
        )
        for status, customer_user_id, deleted_at in (
            ("active", None, None),
            ("pending", 91, None),
            ("expired", None, None),
            ("revoked", None, None),
            ("deleted", None, datetime.now(timezone.utc)),
        ):
            with self.subTest(status=status, customer_user_id=customer_user_id):
                relation = SimpleNamespace(
                    **{
                        **pending_relation.__dict__,
                        "status": status,
                        "customer_user_id": customer_user_id,
                        "deleted_at": deleted_at,
                    }
                )
                self.assertFalse(
                    start._customer_relation_allows_direct_registration(
                        pending_invitation,
                        relation,
                    )
                )

        web_invitation = invitation(
            token="CUST-lifecycle-web",
            kind=InvitationKind.CUSTOMER,
            is_used=True,
            registered_user_id=42,
            completed_at=datetime.now(timezone.utc),
            completed_via="web",
        )
        web_relation = SimpleNamespace(
            invitation_token=web_invitation.token,
            customer_tier="tier1",
            status="active",
            customer_user_id=42,
            deleted_at=None,
            expires_at=web_invitation.expires_at,
        )
        self.assertTrue(
            start._customer_relation_allows_direct_registration(
                web_invitation,
                web_relation,
            )
        )
        web_relation.customer_user_id = 43
        self.assertFalse(
            start._customer_relation_allows_direct_registration(
                web_invitation,
                web_relation,
            )
        )

    async def test_direct_registration_rejects_group_before_database_or_fsm_mutation(self):
        inv = invitation()
        msg = message()
        msg.chat.type = "group"
        state = FakeState()
        with patch.object(start.settings, "telegram_direct_registration_enabled", True), patch.object(
            start.settings, "telegram_registration_reconciliation_enabled", True
        ), patch.object(
            start.settings, "registration_sync_v2_enabled", True
        ), patch.object(start, "delete_previous_anchor", new=AsyncMock()), patch.object(
            start, "AsyncSessionLocal"
        ) as session_factory:
            await start.handle_start_with_token(
                msg,
                SimpleNamespace(args=inv.token),
                state,
                user=None,
            )
        session_factory.assert_not_called()
        self.assertEqual(state.events, [])
        self.assertIn("خصوصی", msg.answer.await_args.args[0])

    async def test_standard_route_requires_both_flags_before_entering_direct_fsm(self):
        inv = invitation()
        command = SimpleNamespace(args=inv.token)
        for direct_enabled, reconciliation_enabled, should_begin in (
            (True, True, True),
            (True, False, False),
            (False, True, False),
        ):
            msg = message()
            state = FakeState()
            with self.subTest(
                direct=direct_enabled,
                reconciliation=reconciliation_enabled,
            ), patch.object(
                start.settings,
                "telegram_direct_registration_enabled",
                direct_enabled,
            ), patch.object(
                start.settings,
                "telegram_registration_reconciliation_enabled",
                reconciliation_enabled,
            ), patch.object(
                start.settings,
                "registration_sync_v2_enabled",
                True,
            ), patch.object(
                start, "delete_previous_anchor", new=AsyncMock()
            ), patch.object(
                start, "AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(inv))
            ), patch.object(start, "public_webapp_url_for_links", return_value="https://app.example"), patch.object(
                start, "audit_log"
            ), patch.object(
                start, "_begin_direct_registration", new=AsyncMock()
            ) as begin, patch.object(start, "set_anchor"):
                await start.handle_start_with_token(msg, command, state, user=None)

            if should_begin:
                begin.assert_awaited_once()
            else:
                begin.assert_not_awaited()
                self.assertIn("وب‌اپ", msg.answer.await_args.args[0])

    async def test_customer_route_allows_tier1_and_keeps_tier2_web_only(self):
        inv = invitation(
            token="CUST-stage5-token",
            kind=InvitationKind.CUSTOMER,
        )
        command = SimpleNamespace(args=inv.token)
        with patch.object(start.settings, "telegram_direct_registration_enabled", True), patch.object(
            start.settings, "telegram_registration_reconciliation_enabled", True
        ), patch.object(
            start.settings, "registration_sync_v2_enabled", True
        ):
            for tier, should_begin in (("tier1", True), ("tier2", False)):
                msg = message()
                relation = SimpleNamespace(
                    invitation_token=inv.token,
                    customer_tier=tier,
                    status="pending",
                    customer_user_id=None,
                    deleted_at=None,
                    expires_at=inv.expires_at,
                )
                with self.subTest(tier=tier), patch.object(
                    start, "delete_previous_anchor", new=AsyncMock()
                ), patch.object(
                    start, "AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(inv))
                ), patch.object(
                    start, "public_webapp_url_for_links", return_value="https://app.example"
                ), patch.object(start, "audit_log"), patch.object(
                    start,
                    "get_customer_relation_by_invitation_token",
                    new=AsyncMock(return_value=relation),
                ), patch.object(
                    start, "_begin_direct_registration", new=AsyncMock()
                ) as begin:
                    await start.handle_start_with_token(
                        msg,
                        command,
                        FakeState(),
                        user=None,
                    )

                if should_begin:
                    begin.assert_awaited_once()
                else:
                    begin.assert_not_awaited()
                    self.assertIn("وب‌اپ", msg.answer.await_args.args[0])

    async def test_begin_binds_invitation_identity_and_contact_state(self):
        inv = invitation()
        state = FakeState()
        msg = message()

        with patch.object(start, "get_registration_intent_for_invitation", new=AsyncMock(return_value=None)), patch.object(
            start, "_bound_registration_fsm_ttl", new=AsyncMock()
        ) as bind_ttl, patch.object(start, "set_anchor"), patch.object(
            start, "audit_log"
        ) as audit:
            await start._begin_direct_registration(
                msg,
                state,
                session=FakeSession(inv),
                invitation=inv,
            )

        self.assertEqual(state.data[start._REGISTRATION_STATE_TOKEN], inv.token)
        self.assertEqual(state.data[start._REGISTRATION_STATE_MOBILE], "09121112233")
        self.assertEqual(state.data[start._REGISTRATION_STATE_TELEGRAM_ID], 7001)
        self.assertEqual(state.states, [Registration.awaiting_contact])
        bind_ttl.assert_awaited_once()
        audit.assert_called_once_with(
            "telegram_registration.invitation_opened",
            target_type="invitation",
            target_id=inv.id,
            result="success",
            extra={"surface": "telegram"},
        )
        self.assertTrue(msg.answer.await_args.kwargs["reply_markup"].keyboard[0][0].request_contact)

    async def test_existing_intent_resumes_without_overwriting_fsm(self):
        inv = invitation()
        state = FakeState({"old": "preserved"})
        msg = message()
        existing = SimpleNamespace(id=uuid4())
        resolution = start.RegistrationHandoffResolution(
            status=TelegramRegistrationIntentStatus.RETRY_WAIT
        )

        with patch.object(
            start, "get_registration_intent_for_invitation", new=AsyncMock(return_value=existing)
        ), patch.object(
            start, "_load_registration_handoff_resolution", new=AsyncMock(return_value=resolution)
        ), patch.object(start, "_send_registration_handoff", new=AsyncMock()) as send, patch.object(
            start, "audit_log"
        ) as audit:
            await start._begin_direct_registration(
                msg,
                state,
                session=FakeSession(inv),
                invitation=inv,
            )

        self.assertEqual(state.data, {"old": "preserved"})
        send.assert_awaited_once_with(msg, resolution)
        audit.assert_not_called()

    async def test_existing_intent_clears_only_registration_owned_fsm(self):
        inv = invitation()
        existing = SimpleNamespace(id=uuid4())
        resolution = start.RegistrationHandoffResolution(
            status=TelegramRegistrationIntentStatus.RETRY_WAIT
        )

        for current_state, should_clear in (
            (Registration.awaiting_address.state, True),
            ("Trade:awaiting_amount", False),
        ):
            with self.subTest(current_state=current_state):
                state = FakeState(
                    {"old": "transient"},
                    current_state=current_state,
                )
                with patch.object(
                    start,
                    "get_registration_intent_for_invitation",
                    new=AsyncMock(return_value=existing),
                ), patch.object(
                    start,
                    "_load_registration_handoff_resolution",
                    new=AsyncMock(return_value=resolution),
                ), patch.object(start, "_send_registration_handoff", new=AsyncMock()):
                    await start._begin_direct_registration(
                        message(),
                        state,
                        session=FakeSession(inv),
                        invitation=inv,
                    )

                self.assertEqual(state.events, ["clear"] if should_clear else [])
                self.assertEqual(state.data, {} if should_clear else {"old": "transient"})

    async def test_entry_ttl_failure_clears_partial_state(self):
        inv = invitation()
        state = FakeState()

        with patch.object(start, "get_registration_intent_for_invitation", new=AsyncMock(return_value=None)), patch.object(
            start, "_bound_registration_fsm_ttl", new=AsyncMock(side_effect=RuntimeError("redis"))
        ):
            with self.assertRaises(RuntimeError):
                await start._begin_direct_registration(
                    message(), state, session=FakeSession(inv), invitation=inv
                )

        self.assertEqual(state.data, {})
        self.assertEqual(state.events, ["clear", "clear"])

    async def test_malformed_invitation_mobile_is_terminal_without_fsm(self):
        inv = invitation(mobile_number="invalid")
        state = FakeState()
        msg = message()

        with patch.object(start, "get_registration_intent_for_invitation", new=AsyncMock(return_value=None)):
            await start._begin_direct_registration(
                msg,
                state,
                session=FakeSession(inv),
                invitation=inv,
            )

        self.assertEqual(state.data, {})
        self.assertEqual(state.states, [])
        self.assertIn("دعوت‌کننده", msg.answer.await_args.args[0])

    async def test_begin_rejects_non_pending_or_invalid_expiry_before_fsm_write(self):
        for inv, derived_state, expected in (
            (invitation(revoked_at=datetime.now(timezone.utc)), "revoked", "دیگر معتبر"),
            (invitation(expires_at="not-a-time"), "pending", "پایان یافته"),
        ):
            state = FakeState()
            msg = message()
            with self.subTest(derived_state=derived_state), patch.object(
                start,
                "get_registration_intent_for_invitation",
                new=AsyncMock(return_value=None),
            ), patch.object(start, "derive_invitation_state", return_value=derived_state), patch.object(
                start, "_write_registration_fsm", new=AsyncMock()
            ) as write_fsm:
                await start._begin_direct_registration(
                    msg,
                    state,
                    session=FakeSession(inv),
                    invitation=inv,
                )
            write_fsm.assert_not_awaited()
            self.assertIn(expected, msg.answer.await_args.args[0])

    async def test_start_entry_failure_is_contained_for_standard_and_customer_invites(self):
        cases = (
            (
                invitation(),
                None,
            ),
            (
                invitation(token="CUST-stage5-failure", kind=InvitationKind.CUSTOMER),
                SimpleNamespace(
                    invitation_token="CUST-stage5-failure",
                    customer_tier="tier1",
                    status="pending",
                    customer_user_id=None,
                    deleted_at=None,
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                ),
            ),
        )
        for inv, relation in cases:
            msg = message()
            state = FakeState({"partial": "value"})
            with self.subTest(kind=inv.kind), patch.object(
                start, "_direct_registration_runtime_ready", return_value=True
            ), patch.object(start, "delete_previous_anchor", new=AsyncMock()), patch.object(
                start,
                "AsyncSessionLocal",
                return_value=FakeSessionContext(FakeSession(inv)),
            ), patch.object(start, "audit_log"), patch.object(
                start,
                "get_customer_relation_by_invitation_token",
                new=AsyncMock(return_value=relation),
            ), patch.object(
                start,
                "evaluate_invitation_bot_access",
                return_value=SimpleNamespace(allowed=True),
            ), patch.object(
                start,
                "_begin_direct_registration",
                new=AsyncMock(side_effect=RuntimeError("redis down")),
            ):
                await start.handle_start_with_token(
                    msg,
                    SimpleNamespace(args=inv.token),
                    state,
                    user=None,
                )

            self.assertEqual(state.data, {})
            self.assertIn("موقتاً", msg.answer.await_args.args[0])

    async def test_existing_user_start_is_pending_or_denied_before_panel(self):
        user = SimpleNamespace(id=19, role=UserRole.STANDARD)
        for handler, command in (
            (start.handle_start_with_token, SimpleNamespace(args="INV-existing")),
            (start.handle_start_without_token, None),
        ):
            for activation, allowed, expected in (
                (SimpleNamespace(reason="pending"), True, "هنوز نهایی"),
                (None, False, None),
            ):
                msg = message()
                state = FakeState()
                with self.subTest(handler=handler.__name__, activation=activation), patch.object(
                    start, "_direct_registration_runtime_ready", return_value=True
                ), patch.object(start, "delete_previous_anchor", new=AsyncMock()), patch.object(
                    start,
                    "AsyncSessionLocal",
                    return_value=FakeSessionContext(FakeSession()),
                ), patch.object(
                    start,
                    "registration_activation_block_for_user",
                    new=AsyncMock(return_value=activation),
                ), patch.object(
                    start,
                    "evaluate_bot_access",
                    new=AsyncMock(return_value=SimpleNamespace(allowed=allowed, reason="inactive")),
                ), patch.object(start, "set_anchor"):
                    if command is None:
                        await handler(msg, state, user)
                    else:
                        await handler(msg, command, state, user)

                response = msg.answer.await_args.args[0]
                if expected:
                    self.assertIn(expected, response)
                else:
                    self.assertTrue(response)

    async def test_existing_allowed_user_reaches_panel_with_or_without_token(self):
        user = SimpleNamespace(id=19, role=UserRole.STANDARD)
        for handler, command in (
            (start.handle_start_with_token, SimpleNamespace(args="INV-existing")),
            (start.handle_start_without_token, None),
        ):
            msg = message()
            with self.subTest(handler=handler.__name__), patch.object(
                start, "_direct_registration_runtime_ready", return_value=True
            ), patch.object(start, "delete_previous_anchor", new=AsyncMock()), patch.object(
                start,
                "AsyncSessionLocal",
                return_value=FakeSessionContext(FakeSession()),
            ), patch.object(
                start,
                "registration_activation_block_for_user",
                new=AsyncMock(return_value=None),
            ), patch.object(
                start,
                "evaluate_bot_access",
                new=AsyncMock(return_value=SimpleNamespace(allowed=True, reason=None)),
            ), patch.object(
                start, "build_linked_account_panel_message", new=AsyncMock(return_value="panel")
            ), patch.object(start, "get_persistent_menu_keyboard", return_value="menu"), patch.object(
                start, "_user_facing_webapp_url", return_value="https://app.example"
            ), patch.object(start, "set_anchor"):
                if command is None:
                    await handler(msg, FakeState(), user)
                else:
                    await handler(msg, command, FakeState(), user)
            self.assertTrue(msg.answer.await_args.args[0])

    async def test_runtime_ready_bot_denial_falls_back_to_web_invitation(self):
        inv = invitation()
        msg = message()
        with patch.object(start, "_direct_registration_runtime_ready", return_value=True), patch.object(
            start, "delete_previous_anchor", new=AsyncMock()
        ), patch.object(
            start,
            "AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession(inv)),
        ), patch.object(start, "audit_log"), patch.object(
            start,
            "evaluate_invitation_bot_access",
            return_value=SimpleNamespace(allowed=False),
        ), patch.object(start, "build_register_link_line", return_value="web-link"), patch.object(
            start, "_begin_direct_registration", new=AsyncMock()
        ) as begin, patch.object(start, "set_anchor"):
            await start.handle_start_with_token(
                msg,
                SimpleNamespace(args=inv.token),
                FakeState(),
                user=None,
            )
        begin.assert_not_awaited()
        self.assertIn("web-link", msg.answer.await_args.args[0])


class Stage5ContactAndAddressTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = patch.object(start, "_direct_registration_runtime_ready", return_value=True)
        self.runtime.start()
        self.addAsyncCleanup(self.runtime.stop)

    async def test_group_address_is_rejected_without_echo_or_intent(self):
        state = FakeState(
            ready_state_data(),
            current_state=Registration.awaiting_address.state,
        )
        msg = message(text="sensitive exact address")
        msg.chat.type = "supergroup"
        with patch.object(start, "create_or_reuse_ready_registration_intent", new=AsyncMock()) as create:
            await start.handle_address(msg, state)
        create.assert_not_awaited()
        self.assertEqual(state.data, {})
        response = msg.answer.await_args.args[0]
        self.assertIn("خصوصی", response)
        self.assertNotIn("sensitive", response)

    async def test_contact_and_address_read_or_write_failures_are_user_safe(self):
        contact_message = message()
        with patch.object(start, "delete_previous_anchor", new=AsyncMock()), patch.object(
            start, "_read_registration_fsm", new=AsyncMock(return_value=None)
        ):
            await start.handle_contact(contact_message, FakeState())
        self.assertIn("موقتاً", contact_message.answer.await_args.args[0])

        inv = invitation()
        contact_data = ready_state_data(inv)
        contact_data.pop(start._REGISTRATION_STATE_CONTACT_VERIFIED_AT)
        contact_data.pop(start._REGISTRATION_STATE_ADDRESS)
        contact_state = FakeState(contact_data)
        contact_message = message()
        with patch.object(start, "delete_previous_anchor", new=AsyncMock()), patch.object(
            start,
            "_write_registration_fsm",
            new=AsyncMock(side_effect=RuntimeError("redis down")),
        ):
            await start.handle_contact(contact_message, contact_state)
        self.assertEqual(contact_state.events, ["clear"])
        self.assertIn("موقتاً", contact_message.answer.await_args.args[0])

        address_message = message(text="Tehran exact address")
        with patch.object(start, "delete_previous_anchor", new=AsyncMock()), patch.object(
            start, "_read_registration_fsm", new=AsyncMock(return_value=None)
        ):
            await start.handle_address(address_message, FakeState())
        self.assertIn("موقتاً", address_message.answer.await_args.args[0])

        address_state = FakeState(ready_state_data(inv))
        address_message = message(text="Tehran exact address")
        with patch.object(start, "delete_previous_anchor", new=AsyncMock()), patch.object(
            start,
            "_write_registration_fsm",
            new=AsyncMock(side_effect=RuntimeError("redis down")),
        ):
            await start.handle_address(address_message, address_state)
        self.assertEqual(address_state.events, ["clear"])
        self.assertIn("موقتاً", address_message.answer.await_args.args[0])

    async def test_group_contact_and_non_contact_handlers_stop_before_mutation(self):
        group_message = message()
        group_message.chat.type = "group"
        state = FakeState(current_state=Registration.awaiting_contact.state)
        with patch.object(start, "delete_previous_anchor", new=AsyncMock()) as delete_anchor:
            await start.handle_contact(group_message, state)
        delete_anchor.assert_not_awaited()
        self.assertEqual(state.events, ["clear"])

        typed = message(text="09121112233")
        typed.chat.type = "group"
        state = FakeState(current_state=Registration.awaiting_contact.state)
        await start.handle_contact_non_contact(typed, state)
        self.assertEqual(state.events, ["clear"])

        typed = message(text="09121112233")
        with patch.object(start, "_direct_registration_runtime_ready", return_value=False), patch.object(
            start, "handle_contact", new=AsyncMock()
        ) as legacy:
            await start.handle_contact_non_contact(typed, FakeState())
        legacy.assert_awaited_once()

    async def test_legacy_contact_and_address_redirect_without_optional_link(self):
        for handler, msg, data in (
            (start.handle_contact, message(), {}),
            (
                start.handle_contact,
                message(),
                {start._REGISTRATION_STATE_TOKEN: "INV-no-link"},
            ),
            (
                start.handle_address,
                message(text="Tehran exact address"),
                {start._REGISTRATION_STATE_TOKEN: "INV-no-link"},
            ),
        ):
            with self.subTest(handler=handler.__name__, data=data), patch.object(
                start, "_direct_registration_runtime_ready", return_value=False
            ), patch.object(start, "delete_previous_anchor", new=AsyncMock()), patch.object(
                start, "build_register_link_line", return_value=None
            ), patch.object(start, "set_anchor"):
                await handler(msg, FakeState(data))
            self.assertIn("وب‌اپ", msg.answer.await_args.args[0])

    async def test_contact_requires_sender_ownership_and_exact_invited_mobile(self):
        base = ready_state_data()
        base.pop(start._REGISTRATION_STATE_CONTACT_VERIFIED_AT)
        base.pop(start._REGISTRATION_STATE_ADDRESS)

        for msg, expected in (
            (message(contact_user_id=9000), "مستقیماً"),
            (message(phone="09129999999"), "مطابقت ندارد"),
            (message(phone="0912 111 2233"), "مطابقت ندارد"),
        ):
            state = FakeState(base)
            with self.subTest(expected=expected), patch.object(
                start, "delete_previous_anchor", new=AsyncMock()
            ):
                await start.handle_contact(msg, state)
            self.assertIn(expected, msg.answer.await_args.args[0])
            self.assertNotIn(Registration.awaiting_address, state.states)

        msg = message()
        msg.contact = SimpleNamespace(user_id=msg.from_user.id)
        state = FakeState(base)
        with patch.object(start, "delete_previous_anchor", new=AsyncMock()):
            await start.handle_contact(msg, state)
        self.assertIn("مطابقت ندارد", msg.answer.await_args.args[0])
        self.assertNotIn(Registration.awaiting_address, state.states)

    async def test_contact_accepts_telegram_international_format_and_advances(self):
        data = ready_state_data()
        data.pop(start._REGISTRATION_STATE_CONTACT_VERIFIED_AT)
        data.pop(start._REGISTRATION_STATE_ADDRESS)
        state = FakeState(data)
        msg = message(phone="+989121112233")

        with patch.object(start, "delete_previous_anchor", new=AsyncMock()), patch.object(
            start, "_bound_registration_fsm_ttl", new=AsyncMock()
        ), patch.object(start, "set_anchor"), patch.object(start, "audit_log") as audit:
            await start.handle_contact(msg, state)

        self.assertIn(start._REGISTRATION_STATE_CONTACT_VERIFIED_AT, state.data)
        self.assertEqual(state.states, [Registration.awaiting_address])
        self.assertIn("شماره تماس تایید شد", msg.answer.await_args.args[0])
        audit.assert_called_once_with(
            "telegram_registration.contact_verified",
            target_type="telegram_registration_step",
            result="success",
            extra={"surface": "telegram"},
        )

    async def test_typed_contact_instruction_does_not_mutate_state(self):
        state = FakeState({"binding": "unchanged"})
        msg = message(text="09121112233")

        await start.handle_contact_non_contact(msg, state)

        self.assertEqual(state.data, {"binding": "unchanged"})
        self.assertEqual(state.states, [])
        self.assertIn("دکمه", msg.answer.await_args.args[0])

    async def test_expired_or_wrong_telegram_state_fails_closed(self):
        data = ready_state_data(telegram_id=9999)
        data.pop(start._REGISTRATION_STATE_CONTACT_VERIFIED_AT)
        data.pop(start._REGISTRATION_STATE_ADDRESS)
        state = FakeState(data)
        msg = message(telegram_id=7001)

        with patch.object(start, "delete_previous_anchor", new=AsyncMock()):
            await start.handle_contact(msg, state)

        self.assertEqual(state.data, {})
        self.assertIn("پایان یافته", msg.answer.await_args.args[0])

    async def test_address_preserves_exact_value_and_requires_ten_characters(self):
        data = ready_state_data()
        data.pop(start._REGISTRATION_STATE_ADDRESS)
        for value in (None, 1234567890, "123456789"):
            msg = message(text=value)
            state = FakeState(data)
            with self.subTest(value=value), patch.object(
                start, "delete_previous_anchor", new=AsyncMock()
            ):
                await start.handle_address(msg, state)
            self.assertIn("حداقل", msg.answer.await_args.args[0])
            self.assertNotIn(start._REGISTRATION_STATE_ADDRESS, state.data)

        exact = " 12345678 "
        msg = message(text=exact)
        state = FakeState(data)
        with patch.object(start, "delete_previous_anchor", new=AsyncMock()), patch.object(
            start, "_bound_registration_fsm_ttl", new=AsyncMock()
        ), patch.object(start, "set_anchor"):
            await start.handle_address(msg, state)
        self.assertEqual(state.data[start._REGISTRATION_STATE_ADDRESS], exact)
        self.assertEqual(state.states, [Registration.awaiting_confirmation])
        self.assertIn(exact, msg.answer.await_args.args[0])

    async def test_address_without_contact_proof_clears_state(self):
        data = ready_state_data()
        data.pop(start._REGISTRATION_STATE_CONTACT_VERIFIED_AT)
        state = FakeState(data)
        msg = message(text="valid address")

        with patch.object(start, "delete_previous_anchor", new=AsyncMock()):
            await start.handle_address(msg, state)

        self.assertEqual(state.data, {})
        self.assertIn("پایان یافته", msg.answer.await_args.args[0])


class Stage5ConfirmationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = patch.object(start, "_direct_registration_runtime_ready", return_value=True)
        self.runtime.start()
        self.addAsyncCleanup(self.runtime.stop)

    async def test_confirmation_commits_intent_before_clearing_fsm_and_hands_off(self):
        events = []
        inv = invitation()
        session = FakeSession(inv, events=events)
        state = FakeState(ready_state_data(inv), events=events)
        cb = callback()
        intent_id = uuid4()
        creation = SimpleNamespace(intent=SimpleNamespace(id=intent_id), created=True)
        resolution = start.RegistrationHandoffResolution(
            status=TelegramRegistrationIntentStatus.RETRY_WAIT
        )

        with patch.object(start, "AsyncSessionLocal", return_value=FakeSessionContext(session)), patch.object(
            start, "create_or_reuse_ready_registration_intent", new=AsyncMock(return_value=creation)
        ) as create_intent, patch.object(
            start, "_wait_for_registration_handoff", new=AsyncMock(return_value=resolution)
        ) as wait, patch.object(start, "_send_registration_handoff", new=AsyncMock()) as send, patch.object(
            start, "audit_log"
        ):
            await start.handle_registration_confirm(cb, state)

        self.assertEqual(events, ["commit", "clear"])
        create_intent.assert_awaited_once()
        self.assertEqual(create_intent.await_args.kwargs["address"], "Tehran exact address")
        wait.assert_awaited_once_with(intent_id=intent_id, telegram_id=7001)
        send.assert_awaited_once_with(cb.message, resolution)

    async def test_edit_address_rejects_group_missing_state_and_supports_retry(self):
        group_callback = callback()
        group_callback.message.chat.type = "group"
        group_state = FakeState(current_state=Registration.awaiting_confirmation.state)
        await start.handle_registration_edit_address(group_callback, group_state)
        self.assertEqual(group_state.events, ["clear"])

        missing_callback = callback()
        with patch.object(
            start, "_read_registration_fsm", new=AsyncMock(return_value=None)
        ):
            await start.handle_registration_edit_address(missing_callback, FakeState())
        self.assertTrue(missing_callback.answer.await_args.kwargs["show_alert"])

        inv = invitation()
        retry_callback = callback()
        retry_state = FakeState(ready_state_data(inv))
        with patch.object(start, "_write_registration_fsm", new=AsyncMock()) as write:
            await start.handle_registration_edit_address(retry_callback, retry_state)
        write.assert_awaited_once()
        self.assertIn("دوباره", retry_callback.answer.await_args.args[0])
        self.assertIn("آدرس", retry_callback.message.answer.await_args.args[0])

    async def test_confirmation_rejects_non_private_missing_message_or_missing_state(self):
        group_callback = callback()
        group_callback.message.chat.type = "group"
        group_state = FakeState(current_state=Registration.awaiting_confirmation.state)
        await start.handle_registration_confirm(group_callback, group_state)
        self.assertEqual(group_state.events, ["clear"])

        no_message = callback()
        no_message.message = None
        with patch.object(
            start, "_reject_non_private_registration", new=AsyncMock(return_value=False)
        ):
            await start.handle_registration_confirm(no_message, FakeState())
        self.assertTrue(no_message.answer.await_args.kwargs["show_alert"])

        missing_state = callback()
        with patch.object(
            start, "_read_registration_fsm", new=AsyncMock(return_value=None)
        ):
            await start.handle_registration_confirm(missing_state, FakeState())
        self.assertTrue(missing_state.answer.await_args.kwargs["show_alert"])

    async def test_confirmation_rejects_malformed_mobile_and_invalid_customer_relation(self):
        malformed = invitation(mobile_number="not-a-mobile")
        malformed_state = FakeState(ready_state_data(invitation(token=malformed.token)))
        malformed_callback = callback()
        with patch.object(
            start,
            "AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession(malformed)),
        ), patch.object(
            start, "create_or_reuse_ready_registration_intent", new=AsyncMock()
        ) as create:
            await start.handle_registration_confirm(malformed_callback, malformed_state)
        create.assert_not_awaited()
        self.assertIn("قابل تایید", malformed_callback.answer.await_args.args[0])

        customer = invitation(token="CUST-invalid-relation", kind=InvitationKind.CUSTOMER)
        customer_state = FakeState(ready_state_data(customer))
        customer_callback = callback()
        with patch.object(
            start,
            "AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession(customer)),
        ), patch.object(
            start,
            "get_customer_relation_by_invitation_token",
            new=AsyncMock(return_value=None),
        ), patch.object(
            start, "create_or_reuse_ready_registration_intent", new=AsyncMock()
        ) as create:
            await start.handle_registration_confirm(customer_callback, customer_state)
        create.assert_not_awaited()
        self.assertIn("مشتری معتبر نیست", customer_callback.answer.await_args.args[0])

    async def test_duplicate_handoff_claim_stops_after_durable_intent_ack(self):
        inv = invitation()
        state = FakeState(ready_state_data(inv))
        cb = callback()
        creation = SimpleNamespace(intent=SimpleNamespace(id=uuid4()), created=True)
        with patch.object(
            start,
            "AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession(inv)),
        ), patch.object(
            start,
            "create_or_reuse_ready_registration_intent",
            new=AsyncMock(return_value=creation),
        ), patch.object(
            start,
            "_claim_registration_handoff_message",
            new=AsyncMock(return_value=False),
        ), patch.object(
            start, "_wait_for_registration_handoff", new=AsyncMock()
        ) as wait, patch.object(start, "audit_log"):
            await start.handle_registration_confirm(cb, state)

        wait.assert_not_awaited()
        self.assertIn("در حال بررسی", cb.message.answer.await_args.args[0])

    async def test_confirmation_message_rejects_group_and_guides_private_user(self):
        group = message(text="confirm")
        group.chat.type = "group"
        group_state = FakeState(current_state=Registration.awaiting_confirmation.state)
        await start.handle_registration_confirmation_message(group, group_state)
        self.assertEqual(group_state.events, ["clear"])

        private = message(text="confirm")
        await start.handle_registration_confirmation_message(private, FakeState())
        self.assertIn("دکمه", private.answer.await_args.args[0])

    async def test_edit_address_without_callback_message_remains_safe(self):
        inv = invitation()
        for write_error in (None, RuntimeError("redis down")):
            cb = callback()
            cb.message = None
            state = FakeState(ready_state_data(inv))
            write = AsyncMock(side_effect=write_error)
            with self.subTest(write_error=write_error), patch.object(
                start, "_reject_non_private_registration", new=AsyncMock(return_value=False)
            ), patch.object(start, "_write_registration_fsm", new=write):
                await start.handle_registration_edit_address(cb, state)
            self.assertTrue(cb.answer.await_count)

    async def test_invalid_confirmation_state_is_cleared_before_database_access(self):
        state = FakeState(ready_state_data())
        cb = callback()
        with patch.object(start, "_direct_registration_runtime_ready", return_value=False), patch.object(
            start, "AsyncSessionLocal"
        ) as session_factory:
            await start.handle_registration_confirm(cb, state)
        session_factory.assert_not_called()
        self.assertEqual(state.data, {})
        self.assertIn("منقضی", cb.answer.await_args.args[0])

    async def test_tier1_customer_confirmation_reaches_intent_creation(self):
        inv = invitation(token="CUST-valid-confirm", kind=InvitationKind.CUSTOMER)
        relation = SimpleNamespace(
            invitation_token=inv.token,
            customer_tier="tier1",
            status="pending",
            customer_user_id=None,
            deleted_at=None,
            expires_at=inv.expires_at,
        )
        state = FakeState(ready_state_data(inv))
        cb = callback()
        creation = SimpleNamespace(intent=SimpleNamespace(id=uuid4()), created=True)
        with patch.object(
            start,
            "AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession(inv)),
        ), patch.object(
            start,
            "get_customer_relation_by_invitation_token",
            new=AsyncMock(return_value=relation),
        ), patch.object(
            start,
            "evaluate_invitation_bot_access",
            return_value=SimpleNamespace(allowed=True),
        ), patch.object(
            start,
            "create_or_reuse_ready_registration_intent",
            new=AsyncMock(return_value=creation),
        ) as create, patch.object(
            start,
            "_claim_registration_handoff_message",
            new=AsyncMock(return_value=False),
        ), patch.object(start, "audit_log"):
            await start.handle_registration_confirm(cb, state)
        create.assert_awaited_once()

    async def test_commit_failure_retains_confirmation_state_for_retry(self):
        inv = invitation()
        session = FakeSession(inv, commit_error=RuntimeError("db unavailable"))
        state = FakeState(ready_state_data(inv))
        cb = callback()
        creation = SimpleNamespace(intent=SimpleNamespace(id=uuid4()), created=True)

        with patch.object(start, "AsyncSessionLocal", return_value=FakeSessionContext(session)), patch.object(
            start, "create_or_reuse_ready_registration_intent", new=AsyncMock(return_value=creation)
        ), patch.object(start, "_wait_for_registration_handoff", new=AsyncMock()) as wait:
            await start.handle_registration_confirm(cb, state)

        self.assertNotIn("clear", state.events)
        self.assertIn(start._REGISTRATION_STATE_ADDRESS, state.data)
        self.assertTrue(cb.answer.await_args.kwargs["show_alert"])
        wait.assert_not_awaited()

    async def test_post_commit_clear_failure_still_acknowledges_durable_intent(self):
        inv = invitation()
        events = []
        state = ClearFailingState(ready_state_data(inv), events=events)
        cb = callback()
        intent_id = uuid4()
        creation = SimpleNamespace(intent=SimpleNamespace(id=intent_id), created=True)
        with patch.object(
            start,
            "AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession(inv, events=events)),
        ), patch.object(
            start,
            "create_or_reuse_ready_registration_intent",
            new=AsyncMock(return_value=creation),
        ), patch.object(
            start,
            "_wait_for_registration_handoff",
            new=AsyncMock(return_value=None),
        ), patch.object(start, "audit_log"):
            await start.handle_registration_confirm(cb, state)

        self.assertEqual(events, ["commit", "clear_failed"])
        self.assertEqual(cb.answer.await_args_list[-1].args[0], "درخواست ثبت شد.")
        self.assertIn("در حال بررسی", cb.message.answer.await_args_list[0].args[0])

    async def test_post_commit_poll_failure_returns_explicit_pending_state(self):
        inv = invitation()
        state = FakeState(ready_state_data(inv))
        cb = callback()
        creation = SimpleNamespace(intent=SimpleNamespace(id=uuid4()), created=True)

        with patch.object(
            start, "AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(inv))
        ), patch.object(
            start, "create_or_reuse_ready_registration_intent", new=AsyncMock(return_value=creation)
        ), patch.object(
            start,
            "_wait_for_registration_handoff",
            new=AsyncMock(side_effect=RuntimeError("db unavailable")),
        ), patch.object(start, "audit_log"):
            await start.handle_registration_confirm(cb, state)

        self.assertEqual(state.data, {})
        self.assertIn("هنوز نهایی نشده", cb.message.answer.await_args_list[-1].args[0])

    async def test_confirmation_rechecks_revocation_expiry_mobile_and_policy(self):
        cases = (
            (invitation(revoked_at=datetime.now(timezone.utc)), "معتبر نیست"),
            (invitation(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)), "پایان یافته"),
            (invitation(mobile_number="09129999999"), "قابل تایید نیست"),
            (invitation(role=UserRole.WATCH), "فقط برای وب‌اپ"),
        )
        for inv, callback_text in cases:
            state = FakeState(ready_state_data(invitation(token=inv.token)))
            cb = callback()
            with self.subTest(callback_text=callback_text), patch.object(
                start, "AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(inv))
            ), patch.object(
                start, "create_or_reuse_ready_registration_intent", new=AsyncMock()
            ) as create_intent:
                await start.handle_registration_confirm(cb, state)
            self.assertIn(callback_text, cb.answer.await_args.args[0])
            self.assertEqual(state.data, {})
            create_intent.assert_not_awaited()

    async def test_edit_address_ttl_failure_fails_closed(self):
        inv = invitation()
        state = FakeState(ready_state_data(inv))
        cb = callback()

        with patch.object(
            start, "_bound_registration_fsm_ttl", new=AsyncMock(side_effect=RuntimeError("redis"))
        ):
            await start.handle_registration_edit_address(cb, state)

        self.assertEqual(state.data, {})
        self.assertTrue(cb.answer.await_args.kwargs["show_alert"])
        self.assertIn("موقتاً", cb.message.answer.await_args.args[0])

    async def test_stale_edit_callback_clears_state_without_durable_mutation(self):
        state = FakeState(ready_state_data(telegram_id=9000))
        cb = callback(telegram_id=7001)

        with patch.object(start, "create_or_reuse_ready_registration_intent", new=AsyncMock()) as create:
            await start.handle_registration_edit_address(cb, state)

        self.assertEqual(state.data, {})
        self.assertTrue(cb.answer.await_args.kwargs["show_alert"])
        create.assert_not_awaited()

    async def test_changed_payload_replay_is_terminal_but_transient_error_is_retryable(self):
        inv = invitation()
        for code, should_clear in (("changed_payload_replay", True), ("foreign_authority_required", False)):
            state = FakeState(ready_state_data(inv))
            cb = callback()
            with self.subTest(code=code), patch.object(
                start, "AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(inv))
            ), patch.object(
                start,
                "create_or_reuse_ready_registration_intent",
                new=AsyncMock(side_effect=start.TelegramRegistrationIntentError(code)),
            ):
                await start.handle_registration_confirm(cb, state)
            self.assertEqual("clear" in state.events, should_clear)

    async def test_pending_and_terminal_handoff_messages_are_distinct(self):
        msg = message()
        await start._send_registration_handoff(msg, None)
        self.assertIn("هنوز نهایی نشده", msg.answer.await_args.args[0])

        msg = message()
        rejected = start.RegistrationHandoffResolution(
            status=TelegramRegistrationIntentStatus.REJECTED,
            reason="invitation_revoked",
        )
        await start._send_registration_handoff(msg, rejected)
        self.assertIn("دیگر معتبر نیست", msg.answer.await_args.args[0])

    async def test_handoff_resolution_requires_matching_allowed_user_projection(self):
        intent_id = uuid4()
        success_intent = SimpleNamespace(
            status=TelegramRegistrationIntentStatus.RECONCILED_CREATED,
            authoritative_user_id=19,
            projected_user_id=19,
            last_error_code=None,
        )
        matching_user = SimpleNamespace(id=19, telegram_id=7001, role=UserRole.STANDARD)
        cases = (
            (FakeLookupSession(None), True, None),
            (
                FakeLookupSession(
                    SimpleNamespace(
                        status=TelegramRegistrationIntentStatus.READY,
                        authoritative_user_id=None,
                        last_error_code=None,
                    )
                ),
                True,
                TelegramRegistrationIntentStatus.READY,
            ),
            (FakeLookupSession(success_intent, None), True, None),
            (
                FakeLookupSession(success_intent, SimpleNamespace(telegram_id=9999)),
                True,
                None,
            ),
            (FakeLookupSession(success_intent, matching_user), False, None),
            (
                FakeLookupSession(success_intent, matching_user),
                True,
                TelegramRegistrationIntentStatus.RECONCILED_CREATED,
            ),
        )
        for lookup, access_allowed, expected_status in cases:
            with self.subTest(expected_status=expected_status, access_allowed=access_allowed), patch.object(
                start,
                "AsyncSessionLocal",
                return_value=FakeSessionContext(lookup),
            ), patch.object(
                start,
                "evaluate_bot_access",
                new=AsyncMock(return_value=SimpleNamespace(allowed=access_allowed)),
            ):
                resolution = await start._load_registration_handoff_resolution(
                    intent_id=intent_id,
                    telegram_id=7001,
                )
            self.assertEqual(
                resolution.status if resolution is not None else None,
                expected_status,
            )

        terminal = SimpleNamespace(
            status=TelegramRegistrationIntentStatus.REJECTED,
            authoritative_user_id=None,
            last_error_code="identity_conflict",
        )
        with patch.object(
            start,
            "AsyncSessionLocal",
            return_value=FakeSessionContext(FakeLookupSession(terminal)),
        ):
            resolution = await start._load_registration_handoff_resolution(
                intent_id=intent_id,
                telegram_id=7001,
            )
        self.assertEqual(resolution.reason, "identity_conflict")

    async def test_handoff_reuses_current_panel_for_all_success_outcomes(self):
        for status, expected_flags in (
            (
                TelegramRegistrationIntentStatus.RECONCILED_CREATED,
                {"newly_linked": False, "already_linked": False, "address_registered": True},
            ),
            (
                TelegramRegistrationIntentStatus.RECONCILED_LINKED_EXISTING,
                {"newly_linked": True, "already_linked": False, "address_registered": False},
            ),
            (
                TelegramRegistrationIntentStatus.RECONCILED_ALREADY_LINKED,
                {"newly_linked": False, "already_linked": True, "address_registered": False},
            ),
        ):
            msg = message()
            user = SimpleNamespace(id=19, telegram_id=7001, role=UserRole.STANDARD)
            resolution = start.RegistrationHandoffResolution(status=status, user=user)
            with self.subTest(status=status), patch.object(
                start,
                "_ensure_registration_onboarding",
                new=AsyncMock(return_value=(user, None)),
            ), patch.object(
                start,
                "build_linked_account_panel_message",
                new=AsyncMock(return_value="existing panel"),
            ) as build_panel, patch.object(
                start, "get_persistent_menu_keyboard", return_value="existing menu"
            ), patch.object(
                start, "public_webapp_url_for_links", return_value="https://app.example"
            ), patch.object(start, "set_anchor") as set_anchor:
                await start._send_registration_handoff(msg, resolution)

            self.assertEqual(msg.answer.await_args.args[0], "existing panel")
            self.assertEqual(msg.answer.await_args.kwargs["reply_markup"], "existing menu")
            for key, value in expected_flags.items():
                self.assertEqual(build_panel.await_args.kwargs[key], value)
            set_anchor.assert_called_once_with(88, 77)

    async def test_handoff_starts_required_tutorial_before_exposing_panel(self):
        msg = message()
        user = SimpleNamespace(id=19, telegram_id=7001, role=UserRole.STANDARD)
        resolution = start.RegistrationHandoffResolution(
            status=TelegramRegistrationIntentStatus.RECONCILED_CREATED,
            user=user,
        )
        with patch.object(
            start,
            "_ensure_registration_onboarding",
            new=AsyncMock(return_value=(user, start.OFFER_TUTORIAL_STEP)),
        ), patch.object(
            start,
            "build_linked_account_panel_message",
            new=AsyncMock(),
        ) as build_panel, patch.object(start, "set_anchor") as set_anchor:
            await start._send_registration_handoff(msg, resolution)

        self.assertIn("راهنمای سریع ثبت آفر", msg.answer.await_args.args[0])
        self.assertIsNotNone(msg.answer.await_args.kwargs["reply_markup"])
        build_panel.assert_not_awaited()
        set_anchor.assert_called_once_with(88, 77)

    async def test_registration_handoff_persists_tutorial_gate_monotonically(self):
        user = SimpleNamespace(
            id=19,
            telegram_id=7001,
            is_deleted=False,
            bot_onboarding_required_step=0,
            bot_onboarding_completed_step=0,
        )
        session = FakeSession(user)
        with patch.object(
            start,
            "AsyncSessionLocal",
            return_value=FakeSessionContext(session),
        ):
            loaded_user, pending_step = await start._ensure_registration_onboarding(user)

        self.assertIs(loaded_user, user)
        self.assertEqual(user.bot_onboarding_required_step, start.BOT_ONBOARDING_REQUIRED_STEP)
        self.assertEqual(user.bot_onboarding_completed_step, 0)
        self.assertEqual(pending_step, start.OFFER_TUTORIAL_STEP)
        self.assertEqual(session.events, ["commit"])

        user.bot_onboarding_completed_step = start.BOT_ONBOARDING_REQUIRED_STEP
        user.bot_onboarding_required_step = start.BOT_ONBOARDING_REQUIRED_STEP
        with patch.object(
            start,
            "AsyncSessionLocal",
            return_value=FakeSessionContext(session),
        ):
            _, pending_step = await start._ensure_registration_onboarding(user)
        self.assertIsNone(pending_step)

    async def test_handoff_wait_stops_on_terminal_and_returns_pending_at_zero_timeout(self):
        pending = start.RegistrationHandoffResolution(
            status=TelegramRegistrationIntentStatus.RETRY_WAIT
        )
        terminal = start.RegistrationHandoffResolution(
            status=TelegramRegistrationIntentStatus.REJECTED,
            reason="identity_conflict",
        )
        with patch.object(
            start,
            "_load_registration_handoff_resolution",
            new=AsyncMock(side_effect=[pending, terminal]),
        ), patch.object(start.asyncio, "sleep", new=AsyncMock()) as sleep:
            result = await start._wait_for_registration_handoff(
                intent_id=uuid4(),
                telegram_id=7001,
                timeout_seconds=1,
            )
        self.assertIs(result, terminal)
        sleep.assert_awaited()

        with patch.object(
            start,
            "_load_registration_handoff_resolution",
            new=AsyncMock(return_value=pending),
        ):
            result = await start._wait_for_registration_handoff(
                intent_id=uuid4(),
                telegram_id=7001,
                timeout_seconds=0,
            )
        self.assertIs(result, pending)


class Stage5RedisTTLTests(unittest.IsolatedAsyncioTestCase):
    def test_datetime_relation_and_rejection_helpers_fail_closed(self):
        self.assertIsNone(start._utc_datetime(None))
        self.assertIsNone(start._utc_datetime("not-a-time"))
        naive = datetime(2026, 7, 12, 10, 0)
        self.assertEqual(start._utc_datetime(naive).tzinfo, timezone.utc)
        with patch.object(start.settings, "telegram_direct_registration_enabled", False):
            self.assertFalse(start._direct_registration_enabled())

        inv = invitation(kind=InvitationKind.CUSTOMER)
        base = {
            "invitation_token": inv.token,
            "customer_tier": "tier1",
            "status": "pending",
            "customer_user_id": None,
            "deleted_at": None,
            "expires_at": inv.expires_at,
        }
        self.assertFalse(start._customer_relation_allows_direct_registration(inv, None))
        for override in (
            {"invitation_token": "CUST-other"},
            {"customer_tier": "tier2"},
            {"deleted_at": datetime.now(timezone.utc)},
        ):
            relation = SimpleNamespace(**{**base, **override})
            self.assertFalse(start._customer_relation_allows_direct_registration(inv, relation))
        self.assertIn("دعوت‌نامه جدید", start._registration_rejection_message("unknown"))

    async def test_non_private_callback_is_rejected_with_alert(self):
        cb = callback()
        cb.message.chat.type = "group"
        state = FakeState(current_state=Registration.awaiting_confirmation.state)

        rejected = await start._reject_non_private_registration(cb, state)

        self.assertTrue(rejected)
        cb.answer.assert_awaited_once()
        self.assertTrue(cb.answer.await_args.kwargs["show_alert"])
        self.assertEqual(state.events, ["clear"])

    async def test_non_private_unknown_event_without_answer_method_is_still_rejected(self):
        event = SimpleNamespace(chat=None, message=None)
        self.assertTrue(await start._reject_non_private_registration(event, FakeState()))

    async def test_ttl_helpers_reject_invalid_expired_and_unsupported_storage(self):
        state = FakeState()
        for expires_at, reason in (
            ("invalid", "expiry_invalid"),
            (datetime.now(timezone.utc) - timedelta(seconds=1), "expired"),
        ):
            with self.subTest(helper="bound", reason=reason), self.assertRaisesRegex(
                RuntimeError,
                reason,
            ):
                await start._bound_registration_fsm_ttl(state, expires_at=expires_at)
            with self.subTest(helper="write", reason=reason), self.assertRaisesRegex(
                RuntimeError,
                reason,
            ):
                await start._write_registration_fsm(
                    state,
                    data={},
                    next_state=Registration.awaiting_contact,
                    expires_at=expires_at,
                )

        real_state = FSMContext(
            storage=SimpleNamespace(),
            key=StorageKey(bot_id=1, chat_id=2, user_id=3),
        )
        future = datetime.now(timezone.utc) + timedelta(minutes=1)
        with self.assertRaisesRegex(RuntimeError, "storage_unsupported"):
            await start._bound_registration_fsm_ttl(real_state, expires_at=future)
        with self.assertRaisesRegex(RuntimeError, "storage_unsupported"):
            await start._write_registration_fsm(
                real_state,
                data={},
                next_state=Registration.awaiting_contact,
                expires_at=future,
            )

        await start._bound_registration_fsm_ttl(FakeState(), expires_at=future)

    async def test_atomic_fsm_write_rejects_partial_pipeline_result(self):
        class Pipeline:
            def set(self, *_args, **_kwargs):
                return self

            async def execute(self):
                return [True, False]

        redis = SimpleNamespace(pipeline=MagicMock(return_value=Pipeline()))
        storage = RedisStorage(redis=redis)
        state = FSMContext(
            storage=storage,
            key=StorageKey(bot_id=1, chat_id=2, user_id=3),
        )
        with self.assertRaisesRegex(RuntimeError, "atomic_write_failed"):
            await start._write_registration_fsm(
                state,
                data={"key": "value"},
                next_state=Registration.awaiting_contact,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
            )

    async def test_fsm_read_clear_and_handoff_claim_failures_are_isolated(self):
        state = FakeState()
        state.get_data = AsyncMock(side_effect=RuntimeError("redis down"))
        self.assertIsNone(await start._read_registration_fsm(state))

        no_probe = SimpleNamespace()
        await start._clear_registration_owned_fsm(no_probe)

        probe_failure = FakeState()
        probe_failure.get_state = AsyncMock(side_effect=RuntimeError("redis down"))
        await start._clear_registration_owned_fsm(probe_failure)

        clear_failure = ClearFailingState(
            current_state=Registration.awaiting_contact.state
        )
        await start._clear_registration_owned_fsm(clear_failure)
        self.assertEqual(clear_failure.events, ["clear_failed"])

        self.assertTrue(
            await start._claim_registration_handoff_message(
                FakeState(),
                intent_id=uuid4(),
            )
        )
        failing_redis_state = FakeState(
            storage=SimpleNamespace(redis=SimpleNamespace(set=AsyncMock(side_effect=RuntimeError("down"))))
        )
        self.assertFalse(
            await start._claim_registration_handoff_message(
                failing_redis_state,
                intent_id=uuid4(),
            )
        )

    async def test_handoff_success_requires_both_authoritative_and_projected_ids(self):
        for intent in (
            SimpleNamespace(
                status=TelegramRegistrationIntentStatus.RECONCILED_CREATED,
                authoritative_user_id=None,
                projected_user_id=19,
            ),
            SimpleNamespace(
                status=TelegramRegistrationIntentStatus.RECONCILED_CREATED,
                authoritative_user_id=19,
                projected_user_id=None,
            ),
        ):
            with self.subTest(intent=intent), patch.object(
                start,
                "AsyncSessionLocal",
                return_value=FakeSessionContext(FakeLookupSession(intent)),
            ):
                self.assertIsNone(
                    await start._load_registration_handoff_resolution(
                        intent_id=uuid4(),
                        telegram_id=7001,
                    )
                )

    async def test_real_fsm_context_writes_state_data_and_expiry_in_one_pipeline(self):
        class Pipeline:
            def __init__(self):
                self.calls = []

            def set(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return self

            async def execute(self):
                return [True, True]

        pipeline = Pipeline()
        redis = SimpleNamespace(pipeline=MagicMock(return_value=pipeline))
        storage = RedisStorage(redis=redis)
        state = FSMContext(
            storage=storage,
            key=StorageKey(bot_id=1, chat_id=2, user_id=3),
        )
        expiry = datetime.now(timezone.utc) + timedelta(seconds=90)

        await start._write_registration_fsm(
            state,
            data={"registration_invitation_token": "opaque"},
            next_state=Registration.awaiting_contact,
            expires_at=expiry,
        )

        redis.pipeline.assert_called_once_with(transaction=True)
        self.assertEqual(len(pipeline.calls), 2)
        self.assertTrue(all(0 < call[1]["ex"] <= 90 for call in pipeline.calls))
        self.assertEqual(pipeline.calls[1][0][1], Registration.awaiting_contact.state)

    async def test_binds_both_redis_keys_to_invitation_lifetime(self):
        redis = SimpleNamespace(expire=AsyncMock(side_effect=[True, True]))
        key_builder = SimpleNamespace(build=MagicMock(side_effect=["state-key", "data-key"]))
        storage = SimpleNamespace(redis=redis, key_builder=key_builder)
        state = FakeState(storage=storage, key=SimpleNamespace())

        await start._bound_registration_fsm_ttl(
            state,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=90),
        )

        self.assertEqual(redis.expire.await_count, 2)
        ttls = [call.args[1] for call in redis.expire.await_args_list]
        self.assertTrue(all(0 < ttl <= 90 for ttl in ttls))

    async def test_rejects_partial_redis_expiry(self):
        redis = SimpleNamespace(expire=AsyncMock(side_effect=[True, False]))
        storage = SimpleNamespace(
            redis=redis,
            key_builder=SimpleNamespace(build=MagicMock(side_effect=["state-key", "data-key"])),
        )
        with self.assertRaisesRegex(RuntimeError, "ttl_not_applied"):
            await start._bound_registration_fsm_ttl(
                FakeState(storage=storage, key=SimpleNamespace()),
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=90),
            )


class Stage5IntentLookupTests(unittest.IsolatedAsyncioTestCase):
    async def test_activation_waits_for_exact_local_projection_not_remote_numeric_id(self):
        user = SimpleNamespace(
            id=42,
            telegram_id=7001,
            mobile_number="09121112233",
            address="Exact registered address",
        )
        pending = SimpleNamespace(
            status=TelegramRegistrationIntentStatus.RETRY_WAIT,
            projected_user_id=None,
            last_error_code="projection_pending",
        )
        success = SimpleNamespace(
            status=TelegramRegistrationIntentStatus.RECONCILED_CREATED,
            authoritative_user_id=100,
            projected_user_id=42,
            last_error_code=None,
        )
        for intent, blocked in ((pending, True), (success, False)):
            db = SimpleNamespace(
                execute=AsyncMock(
                    side_effect=(
                        [FakeResult(None), FakeResult(intent)]
                        if blocked
                        else [FakeResult(intent)]
                    )
                )
            )
            with patch.object(
                intent_service,
                "evaluate_bot_access",
                new=AsyncMock(return_value=SimpleNamespace(allowed=True, reason=None)),
            ):
                result = await intent_service.registration_activation_block_for_user(
                    db,
                    user=user,
                )
            self.assertEqual(result is not None, blocked)

    async def test_activation_ignores_unrelated_terminal_history_and_applies_current_policy(self):
        user = SimpleNamespace(
            id=42,
            telegram_id=7001,
            mobile_number="09121112233",
            address="Exact registered address",
        )
        no_relevant_intent = SimpleNamespace(
            execute=AsyncMock(side_effect=[FakeResult(None), FakeResult(None)])
        )
        result = await intent_service.registration_activation_block_for_user(
            no_relevant_intent,
            user=user,
        )
        self.assertIsNone(result)

        success = SimpleNamespace(
            status=TelegramRegistrationIntentStatus.RECONCILED_CREATED,
            projected_user_id=42,
        )
        denied_db = SimpleNamespace(execute=AsyncMock(return_value=FakeResult(success)))
        with patch.object(
            intent_service,
            "evaluate_bot_access",
            new=AsyncMock(return_value=SimpleNamespace(allowed=False, reason="accountant")),
        ):
            result = await intent_service.registration_activation_block_for_user(
                denied_db,
                user=user,
            )
        self.assertEqual(result.reason, "accountant")

    async def test_lookup_uses_deterministic_identity_without_mutating_intent(self):
        row = SimpleNamespace(
            invitation_token="INV-stage5-token",
            telegram_id=7001,
            status=TelegramRegistrationIntentStatus.RETRY_WAIT,
        )
        db = SimpleNamespace(execute=AsyncMock(return_value=FakeResult(row)))

        with patch.object(intent_service, "current_server", return_value="foreign"):
            found = await intent_service.get_registration_intent_for_invitation(
                db,
                invitation_token="INV-stage5-token",
                telegram_id=7001,
            )

        self.assertIs(found, row)
        db.execute.assert_awaited_once()
        self.assertEqual(row.status, TelegramRegistrationIntentStatus.RETRY_WAIT)

    async def test_lookup_fails_closed_on_wrong_authority_or_identity_collision(self):
        db = SimpleNamespace(execute=AsyncMock())
        with patch.object(intent_service, "current_server", return_value="iran"):
            with self.assertRaisesRegex(
                intent_service.TelegramRegistrationIntentError,
                "foreign_authority_required",
            ):
                await intent_service.get_registration_intent_for_invitation(
                    db,
                    invitation_token="INV-stage5-token",
                    telegram_id=7001,
                )
        db.execute.assert_not_awaited()

        collision = SimpleNamespace(
            invitation_token="INV-other-token",
            telegram_id=7001,
        )
        db.execute.return_value = FakeResult(collision)
        with patch.object(intent_service, "current_server", return_value="foreign"):
            with self.assertRaisesRegex(
                intent_service.TelegramRegistrationIntentError,
                "intent_identity_conflict",
            ):
                await intent_service.get_registration_intent_for_invitation(
                    db,
                    invitation_token="INV-stage5-token",
                    telegram_id=7001,
                )


if __name__ == "__main__":
    unittest.main()
