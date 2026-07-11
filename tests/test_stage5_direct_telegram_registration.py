import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

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
    def __init__(self, data=None, *, events=None, storage=None, key=None):
        self.data = dict(data or {})
        self.events = events if events is not None else []
        self.states = []
        self.storage = storage
        self.key = key

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.events.append("clear")
        self.data.clear()

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def set_state(self, value):
        self.states.append(value)


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
        chat=SimpleNamespace(id=88),
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
        ):
            for tier, should_begin in (("tier1", True), ("tier2", False)):
                msg = message()
                relation = SimpleNamespace(customer_tier=tier)
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
        ) as bind_ttl, patch.object(start, "set_anchor"):
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
        ), patch.object(start, "_send_registration_handoff", new=AsyncMock()) as send:
            await start._begin_direct_registration(
                msg,
                state,
                session=FakeSession(inv),
                invitation=inv,
            )

        self.assertEqual(state.data, {"old": "preserved"})
        send.assert_awaited_once_with(msg, resolution)

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


class Stage5ContactAndAddressTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = patch.object(start, "_direct_registration_runtime_ready", return_value=True)
        self.runtime.start()
        self.addAsyncCleanup(self.runtime.stop)

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
        ), patch.object(start, "set_anchor"):
            await start.handle_contact(msg, state)

        self.assertIn(start._REGISTRATION_STATE_CONTACT_VERIFIED_AT, state.data)
        self.assertEqual(state.states, [Registration.awaiting_address])
        self.assertIn("شماره تماس تایید شد", msg.answer.await_args.args[0])

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
