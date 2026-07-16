import unittest
from unittest.mock import AsyncMock, patch

from pydantic import SecretStr

from core.telegram_delivery_credentials import (
    TelegramDeliveryCredentialConfigurationError,
    TelegramDeliveryCredentialRegistry,
)
from core.telegram_gateway import TelegramGatewayResult


class TelegramDeliveryCredentialRegistryTests(unittest.IsolatedAsyncioTestCase):
    def test_primary_only_registry_has_no_editor_fallback(self):
        registry = TelegramDeliveryCredentialRegistry.from_values(
            primary_token="primary-secret-token",
            editor_enabled=False,
            editor_token="unused-editor-token",
        )
        self.assertEqual(registry.bot_identities, ("primary",))
        self.assertNotIn("primary-secret-token", repr(registry))
        self.assertNotIn("primary-secret-token", repr(registry.resolve("primary")))
        with self.assertRaises(TypeError):
            registry._credentials["primary"] = registry.resolve("primary")
        with self.assertRaisesRegex(
            TelegramDeliveryCredentialConfigurationError,
            "telegram_lane_credential_not_enabled:channel_editor",
        ):
            registry.resolve("channel_editor")

    def test_editor_requires_a_distinct_nonempty_secret(self):
        cases = (
            (None, "channel_editor_telegram_credential_missing"),
            ("", "channel_editor_telegram_credential_missing"),
            ("same-token", "telegram_lane_credentials_must_be_distinct"),
        )
        for editor_token, reason in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(
                TelegramDeliveryCredentialConfigurationError,
                reason,
            ):
                TelegramDeliveryCredentialRegistry.from_values(
                    primary_token="same-token",
                    editor_enabled=True,
                    editor_token=editor_token,
                )

    def test_secret_str_is_unwrapped_without_exposing_token_in_fingerprint_map(self):
        registry = TelegramDeliveryCredentialRegistry.from_values(
            primary_token=SecretStr("primary-token"),
            editor_enabled=True,
            editor_token=SecretStr("editor-token"),
        )
        fingerprints = registry.fingerprints()
        self.assertEqual(set(fingerprints), {"primary", "channel_editor"})
        self.assertNotEqual(fingerprints["primary"], fingerprints["channel_editor"])
        rendered = repr(fingerprints)
        self.assertNotIn("primary-token", rendered)
        self.assertNotIn("editor-token", rendered)

    async def test_gateway_calls_bind_exact_lane_tokens_without_fallback(self):
        registry = TelegramDeliveryCredentialRegistry.from_values(
            primary_token="primary-token",
            editor_enabled=True,
            editor_token="editor-token",
        )
        gateway = AsyncMock(
            side_effect=lambda method, payload, **kwargs: TelegramGatewayResult(
                ok=True,
                method=method,
                status_code=200,
                response_json={"ok": True, "result": True},
            )
        )
        with patch(
            "core.telegram_delivery_credentials.telegram_gateway.post_telegram_method",
            gateway,
        ):
            calls = registry.build_gateway_calls()
            await calls["primary"]("sendMessage", {"chat_id": 1, "text": "p"})
            await calls["channel_editor"](
                "editMessageText",
                {"chat_id": -1, "message_id": 2, "text": "e"},
            )

        self.assertEqual(gateway.await_args_list[0].kwargs["bot_token"], "primary-token")
        self.assertEqual(gateway.await_args_list[1].kwargs["bot_token"], "editor-token")

    def test_unknown_identity_and_missing_primary_fail_closed(self):
        with self.assertRaisesRegex(
            TelegramDeliveryCredentialConfigurationError,
            "primary_telegram_credential_missing",
        ):
            TelegramDeliveryCredentialRegistry.from_values(
                primary_token=None,
                editor_enabled=False,
            )
        registry = TelegramDeliveryCredentialRegistry.from_values(
            primary_token="primary-token",
            editor_enabled=False,
        )
        with self.assertRaisesRegex(
            TelegramDeliveryCredentialConfigurationError,
            "telegram_bot_identity_not_allowlisted",
        ):
            registry.resolve("unknown")


if __name__ == "__main__":
    unittest.main()
