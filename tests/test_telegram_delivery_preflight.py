import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.telegram_delivery_credentials import TelegramDeliveryCredentialRegistry
from core.telegram_delivery_preflight import (
    TelegramDeliveryPreflightConfigurationError,
    TelegramDeliveryPreflightFailedError,
    run_configured_telegram_delivery_preflight,
    run_telegram_delivery_preflight,
)
from core.telegram_gateway import TelegramGatewayResult


PRIMARY_BOT_ID = 111_111_111
EDITOR_BOT_ID = 222_222_222
CHANNEL_ID = -100_333_333_333


def _registry(*, editor=False):
    return TelegramDeliveryCredentialRegistry.from_values(
        primary_token="primary-secret-token",
        editor_enabled=editor,
        editor_token="editor-secret-token" if editor else None,
    )


class _ReadbackGateway:
    def __init__(
        self,
        *,
        role,
        bot_id,
        channel_id=CHANNEL_ID,
        channel_type="channel",
        status="administrator",
        is_anonymous=False,
        is_bot=True,
        member_bot_id=None,
        permissions=None,
        override_results=None,
    ):
        self.role = role
        self.bot_id = bot_id
        self.channel_id = channel_id
        self.channel_type = channel_type
        self.status = status
        self.is_anonymous = is_anonymous
        self.is_bot = is_bot
        self.member_bot_id = bot_id if member_bot_id is None else member_bot_id
        default_permissions = {
            "can_be_edited": False,
            "can_manage_chat": True,
            "can_delete_messages": False,
            "can_manage_video_chats": False,
            "can_restrict_members": False,
            "can_promote_members": False,
            "can_change_info": False,
            "can_invite_users": False,
            "can_post_stories": False,
            "can_edit_stories": False,
            "can_delete_stories": False,
            "can_post_messages": role == "primary",
            "can_edit_messages": True,
        }
        default_permissions.update(permissions or {})
        self.permissions = default_permissions
        self.override_results = dict(override_results or {})
        self.calls = []

    async def __call__(
        self,
        method,
        payload,
        *,
        timeout=10,
        idempotency_key=None,
    ):
        self.calls.append((method, payload, timeout, idempotency_key))
        override = self.override_results.get(method)
        if isinstance(override, Exception):
            raise override
        if override is not None:
            return override
        if method == "getMe":
            result = {"id": self.bot_id, "is_bot": self.is_bot, "first_name": self.role}
        elif method == "getChat":
            result = {
                "id": self.channel_id,
                "type": self.channel_type,
                "title": "redacted-test-channel",
            }
        elif method == "getChatMember":
            result = {
                "status": self.status,
                "user": {"id": self.member_bot_id, "is_bot": self.is_bot},
                "is_anonymous": self.is_anonymous,
                **self.permissions,
            }
        else:
            raise AssertionError(f"unexpected method: {method}")
        return TelegramGatewayResult(
            ok=True,
            method=method,
            status_code=200,
            response_json={"ok": True, "result": result},
        )


async def _run(*, editor=False, primary=None, editor_gateway=None, **overrides):
    primary_gateway = primary or _ReadbackGateway(
        role="primary",
        bot_id=PRIMARY_BOT_ID,
    )
    calls = {"primary": primary_gateway}
    if editor:
        calls["channel_editor"] = editor_gateway or _ReadbackGateway(
            role="channel_editor",
            bot_id=EDITOR_BOT_ID,
        )
    values = {
        "credential_registry": _registry(editor=editor),
        "channel_id": CHANNEL_ID,
        "expected_channel_id": CHANNEL_ID,
        "expected_primary_bot_id": PRIMARY_BOT_ID,
        "editor_enabled": editor,
        "expected_editor_bot_id": EDITOR_BOT_ID if editor else None,
        "timeout_seconds": 7.5,
        "gateway_calls": calls,
    }
    values.update(overrides)
    return await run_telegram_delivery_preflight(**values)


class TelegramDeliveryPreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_primary_only_preflight_verifies_three_readbacks_and_redacts_ids(self):
        gateway = _ReadbackGateway(role="primary", bot_id=PRIMARY_BOT_ID)
        report = await _run(primary=gateway)

        self.assertEqual(report.approved_bot_identities, ("primary",))
        self.assertEqual(len(report.identities), 1)
        identity = report.identities[0]
        self.assertEqual(identity.bot_identity, "primary")
        self.assertEqual(
            identity.effective_permissions,
            ("can_manage_chat", "can_post_messages", "can_edit_messages"),
        )
        self.assertEqual(
            [call[0] for call in gateway.calls],
            ["getMe", "getChat", "getChatMember"],
        )
        self.assertEqual(gateway.calls[1][1], {"chat_id": CHANNEL_ID})
        self.assertEqual(
            gateway.calls[2][1],
            {"chat_id": CHANNEL_ID, "user_id": PRIMARY_BOT_ID},
        )
        self.assertTrue(all(call[2] == 7.5 for call in gateway.calls))
        rendered = repr(report)
        self.assertNotIn(str(PRIMARY_BOT_ID), rendered)
        self.assertNotIn(str(CHANNEL_ID), rendered)
        self.assertNotIn("primary-secret-token", rendered)

    async def test_editor_preflight_requires_distinct_identity_and_minimal_permissions(self):
        report = await _run(editor=True)
        self.assertEqual(
            report.approved_bot_identities,
            ("primary", "channel_editor"),
        )
        editor = report.identities[1]
        self.assertEqual(editor.bot_identity, "channel_editor")
        self.assertEqual(
            editor.effective_permissions,
            ("can_manage_chat", "can_edit_messages"),
        )
        self.assertNotEqual(
            report.identities[0].credential_fingerprint,
            report.identities[1].credential_fingerprint,
        )
        self.assertNotEqual(
            report.identities[0].bot_fingerprint,
            report.identities[1].bot_fingerprint,
        )

    async def test_registry_binds_each_preflight_readback_to_its_exact_token(self):
        primary_gateway = _ReadbackGateway(
            role="primary",
            bot_id=PRIMARY_BOT_ID,
        )
        editor_gateway = _ReadbackGateway(
            role="channel_editor",
            bot_id=EDITOR_BOT_ID,
        )

        async def provider(method, payload, **kwargs):
            token = kwargs["bot_token"]
            if token == "primary-secret-token":
                target = primary_gateway
            elif token == "editor-secret-token":
                target = editor_gateway
            else:
                raise AssertionError("unexpected token")
            return await target(
                method,
                payload,
                timeout=kwargs["timeout"],
                idempotency_key=kwargs["idempotency_key"],
            )

        gateway = AsyncMock(side_effect=provider)
        with patch(
            "core.telegram_delivery_credentials.telegram_gateway.post_telegram_method",
            gateway,
        ):
            report = await run_telegram_delivery_preflight(
                credential_registry=_registry(editor=True),
                channel_id=CHANNEL_ID,
                expected_channel_id=CHANNEL_ID,
                expected_primary_bot_id=PRIMARY_BOT_ID,
                editor_enabled=True,
                expected_editor_bot_id=EDITOR_BOT_ID,
            )

        self.assertEqual(
            report.approved_bot_identities,
            ("primary", "channel_editor"),
        )
        tokens = [call.kwargs["bot_token"] for call in gateway.await_args_list]
        self.assertEqual(tokens[:3], ["primary-secret-token"] * 3)
        self.assertEqual(tokens[3:], ["editor-secret-token"] * 3)

    async def test_missing_or_mismatched_expected_configuration_fails_before_network(self):
        cases = (
            ({"expected_channel_id": None}, "expected_channel_id_missing"),
            ({"channel_id": None}, "channel_id_missing"),
            ({"expected_channel_id": -100999}, "channel_configuration_mismatch"),
            ({"expected_primary_bot_id": None}, "expected_primary_bot_id_missing"),
            (
                {"editor": True, "expected_editor_bot_id": None},
                "expected_editor_bot_id_missing",
            ),
            (
                {"editor": True, "expected_editor_bot_id": PRIMARY_BOT_ID},
                "bot_ids_must_be_distinct",
            ),
            ({"expected_primary_bot_id": 12.5}, "expected_primary_bot_id_missing"),
            ({"channel_id": -100.5}, "channel_id_missing"),
            ({"timeout_seconds": float("nan")}, "timeout_invalid"),
        )
        for values, reason in cases:
            editor = bool(values.pop("editor", False))
            primary = _ReadbackGateway(role="primary", bot_id=PRIMARY_BOT_ID)
            editor_gateway = _ReadbackGateway(
                role="channel_editor",
                bot_id=EDITOR_BOT_ID,
            )
            with self.subTest(reason=reason), self.assertRaisesRegex(
                TelegramDeliveryPreflightConfigurationError,
                reason,
            ):
                await _run(
                    editor=editor,
                    primary=primary,
                    editor_gateway=editor_gateway,
                    **values,
                )
            self.assertEqual(primary.calls, [])
            self.assertEqual(editor_gateway.calls, [])

    async def test_registry_and_gateway_roles_must_exactly_match_enabled_lanes(self):
        primary = _ReadbackGateway(role="primary", bot_id=PRIMARY_BOT_ID)
        editor = _ReadbackGateway(role="channel_editor", bot_id=EDITOR_BOT_ID)
        with self.assertRaisesRegex(
            TelegramDeliveryPreflightConfigurationError,
            "credential_roles_mismatch",
        ):
            await _run(
                editor=True,
                credential_registry=_registry(editor=False),
                primary=primary,
                editor_gateway=editor,
            )
        self.assertEqual(primary.calls, [])

        with self.assertRaisesRegex(
            TelegramDeliveryPreflightConfigurationError,
            "gateway_roles_mismatch",
        ):
            await _run(gateway_calls={"primary": primary, "unexpected": editor})
        self.assertEqual(primary.calls, [])

        with self.assertRaisesRegex(
            TelegramDeliveryPreflightConfigurationError,
            "gateway_roles_mismatch",
        ):
            await _run(gateway_calls={})
        self.assertEqual(primary.calls, [])

    async def test_provider_envelope_and_transport_fail_closed_without_response_leak(self):
        bad_envelope = TelegramGatewayResult(
            ok=True,
            method="getMe",
            status_code=200,
            response_json={"ok": False, "description": "sensitive provider text"},
        )
        for override, reason in (
            (bad_envelope, "readback_failed:primary:getMe"),
            (ConnectionError("token-and-target-detail"), "transport_failed:primary:getMe"),
        ):
            gateway = _ReadbackGateway(
                role="primary",
                bot_id=PRIMARY_BOT_ID,
                override_results={"getMe": override},
            )
            with self.subTest(reason=reason), self.assertRaisesRegex(
                TelegramDeliveryPreflightFailedError,
                reason,
            ) as captured:
                await _run(primary=gateway)
            rendered = str(captured.exception)
            self.assertNotIn("sensitive provider text", rendered)
            self.assertNotIn("token-and-target-detail", rendered)

    async def test_wrong_bot_channel_or_member_identity_is_rejected(self):
        cases = (
            (
                _ReadbackGateway(role="primary", bot_id=999_999_999),
                "bot_identity_mismatch",
            ),
            (
                _ReadbackGateway(
                    role="primary",
                    bot_id=PRIMARY_BOT_ID,
                    channel_id=-100999,
                ),
                "channel_identity_mismatch",
            ),
            (
                _ReadbackGateway(
                    role="primary",
                    bot_id=PRIMARY_BOT_ID,
                    channel_type="supergroup",
                ),
                "channel_identity_mismatch",
            ),
            (
                _ReadbackGateway(
                    role="primary",
                    bot_id=PRIMARY_BOT_ID,
                    member_bot_id=999_999_999,
                ),
                "member_identity_mismatch",
            ),
        )
        for gateway, reason in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(
                TelegramDeliveryPreflightFailedError,
                reason,
            ):
                await _run(primary=gateway)

    async def test_non_admin_anonymous_or_non_bot_membership_is_rejected(self):
        cases = (
            ({"status": "member"}, "not_administrator"),
            ({"is_anonymous": True}, "anonymous_administrator"),
            ({"is_bot": False}, "bot_identity_mismatch"),
        )
        for values, reason in cases:
            gateway = _ReadbackGateway(
                role="primary",
                bot_id=PRIMARY_BOT_ID,
                **values,
            )
            with self.subTest(reason=reason), self.assertRaisesRegex(
                TelegramDeliveryPreflightFailedError,
                reason,
            ):
                await _run(primary=gateway)

    async def test_primary_requires_both_post_and_edit_permissions(self):
        for missing, reason in (
            ("can_manage_chat", "manage_permission_missing"),
            ("can_post_messages", "primary_permissions_missing"),
            ("can_edit_messages", "primary_permissions_missing"),
        ):
            gateway = _ReadbackGateway(
                role="primary",
                bot_id=PRIMARY_BOT_ID,
                permissions={missing: False},
            )
            with self.subTest(missing=missing), self.assertRaisesRegex(
                TelegramDeliveryPreflightFailedError,
                reason,
            ):
                await _run(primary=gateway)

    async def test_editor_rejects_every_excess_administrator_permission(self):
        forbidden = (
            "can_delete_messages",
            "can_manage_video_chats",
            "can_restrict_members",
            "can_promote_members",
            "can_change_info",
            "can_invite_users",
            "can_post_stories",
            "can_edit_stories",
            "can_delete_stories",
            "can_post_messages",
            "can_pin_messages",
            "can_manage_topics",
            "can_manage_direct_messages",
            "can_manage_tags",
        )
        for permission in forbidden:
            gateway = _ReadbackGateway(
                role="channel_editor",
                bot_id=EDITOR_BOT_ID,
                permissions={permission: True},
            )
            with self.subTest(permission=permission), self.assertRaisesRegex(
                TelegramDeliveryPreflightFailedError,
                "editor_permissions_excessive",
            ):
                await _run(editor=True, editor_gateway=gateway)

    async def test_editor_missing_edit_or_malformed_permission_is_rejected(self):
        missing = _ReadbackGateway(
            role="channel_editor",
            bot_id=EDITOR_BOT_ID,
            permissions={"can_edit_messages": False},
        )
        with self.assertRaisesRegex(
            TelegramDeliveryPreflightFailedError,
            "editor_edit_permission_missing",
        ):
            await _run(editor=True, editor_gateway=missing)

        malformed = _ReadbackGateway(
            role="channel_editor",
            bot_id=EDITOR_BOT_ID,
            permissions={"can_edit_messages": "yes"},
        )
        with self.assertRaisesRegex(
            TelegramDeliveryPreflightFailedError,
            "malformed_permission",
        ):
            await _run(editor=True, editor_gateway=malformed)

        malformed_required = _ReadbackGateway(
            role="channel_editor",
            bot_id=EDITOR_BOT_ID,
            permissions={"can_delete_messages": None},
        )
        with self.assertRaisesRegex(
            TelegramDeliveryPreflightFailedError,
            "malformed_permission",
        ):
            await _run(editor=True, editor_gateway=malformed_required)

        future_permission = _ReadbackGateway(
            role="channel_editor",
            bot_id=EDITOR_BOT_ID,
            permissions={"can_manage_future_surface": True},
        )
        with self.assertRaisesRegex(
            TelegramDeliveryPreflightFailedError,
            "editor_permissions_excessive",
        ):
            await _run(editor=True, editor_gateway=future_permission)

    async def test_configured_wrapper_reads_only_explicit_queue_identity_settings(self):
        settings = SimpleNamespace(
            channel_id=CHANNEL_ID,
            telegram_delivery_queue_expected_channel_id=CHANNEL_ID,
            telegram_delivery_queue_expected_primary_bot_id=PRIMARY_BOT_ID,
            telegram_delivery_queue_channel_editor_enabled=False,
            telegram_delivery_queue_expected_channel_editor_bot_id=None,
            telegram_delivery_queue_preflight_timeout_seconds=5.0,
        )
        registry = _registry()
        gateway = _ReadbackGateway(role="primary", bot_id=PRIMARY_BOT_ID)
        with patch.object(
            TelegramDeliveryCredentialRegistry,
            "build_gateway_calls",
            return_value={"primary": gateway},
        ):
            report = await run_configured_telegram_delivery_preflight(
                settings=settings,
                credential_registry=registry,
            )
        self.assertEqual(report.approved_bot_identities, ("primary",))
        self.assertTrue(all(call[2] == 5.0 for call in gateway.calls))


if __name__ == "__main__":
    unittest.main()
