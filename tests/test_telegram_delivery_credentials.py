import unittest
from unittest.mock import AsyncMock, patch

from pydantic import SecretStr

from core.telegram_delivery_credentials import (
    TelegramDeliveryCredentialConfigurationError,
    TelegramDeliveryCredentialRegistry,
    TelegramPublisherLaneConfiguration,
    TelegramPublisherLaneHealthState,
)
from core.telegram_gateway import TelegramGatewayResult


class TelegramDeliveryCredentialRegistryTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _publisher_lanes(**overrides):
        lanes = {
            f"publisher_{index}": TelegramPublisherLaneConfiguration(
                token=f"publisher-{index}-token",
                expected_bot_id=1_000 + index,
                expected_username=f"publisher_{index}_bot",
            )
            for index in range(1, 6)
        }
        for identity, values in overrides.items():
            existing = lanes[identity]
            lanes[identity] = TelegramPublisherLaneConfiguration(
                token=values.get("token", existing.token),
                expected_bot_id=values.get("expected_bot_id", existing.expected_bot_id),
                expected_username=values.get(
                    "expected_username", existing.expected_username
                ),
                enabled=values.get("enabled", existing.enabled),
                capabilities=values.get("capabilities", existing.capabilities),
            )
        return lanes

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
        with self.assertRaises(TypeError):
            await calls["primary"](
                "getMe",
                {},
                _credential=registry.resolve("channel_editor"),
            )

    async def test_each_publisher_gateway_is_bound_to_its_own_credential(self):
        registry = TelegramDeliveryCredentialRegistry.from_values(
            primary_token="primary-token",
            editor_enabled=False,
            publisher_lanes=self._publisher_lanes(),
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
            for index in range(1, 6):
                await calls[f"publisher_{index}"]("getMe", {})

        self.assertEqual(
            [call.kwargs["bot_token"] for call in gateway.await_args_list],
            [f"publisher-{index}-token" for index in range(1, 6)],
        )

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

    def test_five_publisher_lanes_require_distinct_complete_configuration(self):
        registry = TelegramDeliveryCredentialRegistry.from_values(
            primary_token="primary-token",
            editor_enabled=False,
            publisher_lanes=self._publisher_lanes(),
        )

        self.assertEqual(
            registry.bot_identities,
            (
                "primary",
                "publisher_1",
                "publisher_2",
                "publisher_3",
                "publisher_4",
                "publisher_5",
            ),
        )
        lane = registry.publisher_lane("publisher_3")
        self.assertEqual(lane.expected_bot_id, 1_003)
        self.assertEqual(lane.expected_username, "publisher_3_bot")
        self.assertEqual(lane.health_state, TelegramPublisherLaneHealthState.UNVERIFIED)
        self.assertNotIn("publisher-3-token", repr(registry))
        self.assertNotIn("publisher-3-token", repr(lane))

        incomplete_lanes = {
            identity: lane
            for identity, lane in self._publisher_lanes().items()
            if identity != "publisher_5"
        }
        with self.assertRaisesRegex(
            TelegramDeliveryCredentialConfigurationError,
            "telegram_publisher_lane_set_invalid",
        ):
            TelegramDeliveryCredentialRegistry.from_values(
                primary_token="primary-token",
                editor_enabled=False,
                publisher_lanes=incomplete_lanes,
            )

        cases = (
            ({"publisher_3": {"enabled": False}}, "lane_disabled:publisher_3"),
            ({"publisher_3": {"token": "primary-token"}}, "credentials_must_be_distinct"),
            ({"publisher_3": {"expected_bot_id": 1_002}}, "identity_not_distinct"),
            ({"publisher_3": {"expected_username": "publisher_2_bot"}}, "identity_not_distinct"),
            ({"publisher_3": {"expected_username": "bad name"}}, "expected_username_invalid"),
            ({"publisher_3": {"capabilities": frozenset()}}, "capability_incomplete"),
        )
        for overrides, reason in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(
                TelegramDeliveryCredentialConfigurationError,
                reason,
            ):
                TelegramDeliveryCredentialRegistry.from_values(
                    primary_token="primary-token",
                    editor_enabled=False,
                    publisher_lanes=self._publisher_lanes(**overrides),
                )


if __name__ == "__main__":
    unittest.main()
