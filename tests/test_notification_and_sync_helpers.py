import hashlib
import hmac
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core import notifications, sync_push


class NotificationHelperTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_telegram_message_relays_to_foreign_when_running_on_iran(self):
        with patch.object(notifications.settings, "server_mode", "iran"), \
             patch("core.notifications.push_sync_direct") as push_sync_direct:
            await notifications.send_telegram_message(12345, "hello", parse_mode="HTML")

        push_sync_direct.assert_called_once()
        payload = push_sync_direct.call_args.args[0]
        self.assertEqual(payload["type"], "notification")
        self.assertEqual(payload["chat_id"], 12345)
        self.assertEqual(payload["text"], "hello")
        self.assertEqual(payload["parse_mode"], "HTML")
        self.assertIn("timestamp", payload)

    async def test_send_telegram_message_sends_directly_on_foreign(self):
        sent_calls = []

        class BotSpy:
            def __init__(self, token):
                self.token = token

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def send_message(self, chat_id, text, parse_mode):
                sent_calls.append((chat_id, text, parse_mode, self.token))

        with patch.object(notifications.settings, "server_mode", "foreign"), \
             patch.object(notifications.settings, "bot_token", "bot-token"), \
             patch("core.notifications.Bot", BotSpy):
            await notifications.send_telegram_message(54321, "ping")

        self.assertEqual(sent_calls, [(54321, "ping", "Markdown", "bot-token")])


class SyncPushHelperTests(unittest.TestCase):
    def setUp(self):
        self.original_client = sync_push._http_client
        self.original_cooldowns = dict(sync_push._target_cooldowns)
        sync_push._http_client = None
        sync_push._target_cooldowns.clear()

    def tearDown(self):
        existing = sync_push._http_client
        if existing and hasattr(existing, "close") and callable(existing.close):
            try:
                existing.close()
            except Exception:
                pass
        sync_push._http_client = self.original_client
        sync_push._target_cooldowns.clear()
        sync_push._target_cooldowns.update(self.original_cooldowns)

    def test_push_sync_direct_skips_when_configuration_missing(self):
        with patch("core.server_routing.default_peer_server_url", return_value=None), \
             patch("core.config.settings.sync_api_key", "secret"), \
             patch.object(sync_push, "_executor") as executor:
            sync_push.push_sync_direct({"id": 1})

        executor.submit.assert_not_called()

        with patch("core.server_routing.default_peer_server_url", return_value="https://peer.example"), \
             patch("core.config.settings.sync_api_key", None), \
             patch.object(sync_push, "_executor") as executor:
            sync_push.push_sync_direct({"id": 1})

        executor.submit.assert_not_called()

    def test_push_sync_direct_normalizes_url_and_submits_background_task(self):
        payload = {"table": "offers", "id": 8}

        with patch("core.server_routing.default_peer_server_url", return_value="https://peer.example/"), \
             patch("core.config.settings.sync_api_key", "secret"), \
             patch.object(sync_push, "_executor") as executor:
            sync_push.push_sync_direct(payload)

        executor.submit.assert_called_once_with(sync_push._do_push, payload, "https://peer.example", "secret")

    def test_push_sync_direct_skips_targets_in_cooldown(self):
        payload = {"table": "offers", "id": 9}
        sync_push._target_cooldowns["https://peer.example"] = 999999999.0

        with patch("core.server_routing.default_peer_server_url", return_value="https://peer.example/"), \
             patch("core.config.settings.sync_api_key", "secret"), \
             patch.object(sync_push, "_executor") as executor, \
             patch("core.sync_push.time.monotonic", return_value=100.0):
            sync_push.push_sync_direct(payload)

        executor.submit.assert_not_called()

    def test_sync_push_helper_branches_cover_invalid_cooldowns_and_submit_failure(self):
        with patch("core.config.settings.sync_direct_push_cooldown_seconds", "bad"):
            self.assertEqual(sync_push._get_cooldown_seconds(), 90.0)

        with patch("core.config.settings.sync_direct_push_cooldown_seconds", 0.0):
            sync_push._mark_target_cooldown("https://peer.example", "disabled")
        self.assertFalse(sync_push._target_is_in_cooldown("https://peer.example"))

        sync_push._target_cooldowns["https://peer.example"] = 100.0
        with patch("core.sync_push.time.monotonic", return_value=200.0):
            self.assertFalse(sync_push._target_is_in_cooldown("https://peer.example"))
        self.assertNotIn("https://peer.example", sync_push._target_cooldowns)

        with patch("core.server_routing.default_peer_server_url", return_value="https://peer.example"), \
             patch("core.config.settings.sync_api_key", "secret"), \
             patch.object(sync_push, "_executor") as executor, \
             patch.object(sync_push, "logger") as logger:
            executor.submit.side_effect = RuntimeError("submit down")
            sync_push.push_sync_direct({"id": 1})

        logger.warning.assert_called_once()

    def test_teardown_tolerates_close_failure(self):
        class ClosingClient:
            is_closed = False

            def close(self):
                raise RuntimeError("close failed")

        sync_push._http_client = ClosingClient()

    def test_do_push_sends_signed_payload_to_sync_receive(self):
        recorded = {}

        class ClientSpy:
            def post(self, url, content, headers):
                recorded["url"] = url
                recorded["content"] = content
                recorded["headers"] = headers
                return SimpleNamespace(status_code=200)

        payload = {"table": "offers", "id": 8}
        timestamp = 1_700_000_222

        with patch("core.sync_push._get_client", return_value=ClientSpy()), \
             patch("core.sync_push.time.time", return_value=timestamp):
            sync_push._do_push(payload, "https://peer.example", "secret")

        expected_body = json.dumps([payload], sort_keys=True, default=str)
        expected_signature = hmac.new(
            b"secret",
            f"{timestamp}:{expected_body}".encode(),
            hashlib.sha256,
        ).hexdigest()

        self.assertEqual(recorded["url"], "https://peer.example/api/sync/receive")
        self.assertEqual(recorded["content"], expected_body)
        self.assertEqual(recorded["headers"]["X-API-Key"], "secret")
        self.assertEqual(recorded["headers"]["X-Timestamp"], str(timestamp))
        self.assertEqual(recorded["headers"]["X-Signature"], expected_signature)
        self.assertFalse(sync_push._target_is_in_cooldown("https://peer.example"))

    def test_do_push_swallows_failed_responses_and_transport_errors(self):
        class FailingResponseClient:
            def post(self, url, content, headers):
                return SimpleNamespace(status_code=500, text="boom")

        class ExplodingClient:
            def post(self, url, content, headers):
                raise RuntimeError("network down")

        with patch("core.sync_push._get_client", return_value=FailingResponseClient()), \
             patch("core.config.settings.sync_direct_push_cooldown_seconds", 60.0), \
             patch("core.sync_push.time.monotonic", return_value=100.0):
            sync_push._do_push({"id": 1}, "https://peer.example", "secret")
            self.assertTrue(sync_push._target_is_in_cooldown("https://peer.example"))

        sync_push._clear_target_cooldown("https://peer.example")

        with patch("core.sync_push._get_client", return_value=ExplodingClient()), \
             patch("core.config.settings.sync_direct_push_cooldown_seconds", 60.0), \
             patch("core.sync_push.time.monotonic", return_value=200.0):
            sync_push._do_push({"id": 1}, "https://peer.example", "secret")
            self.assertTrue(sync_push._target_is_in_cooldown("https://peer.example"))

    def test_get_client_reuses_open_client_and_recreates_closed_one(self):
        open_client = SimpleNamespace(is_closed=False)
        sync_push._http_client = open_client
        self.assertIs(sync_push._get_client(), open_client)

        created_clients = []

        class CreatedClient(SimpleNamespace):
            pass

        def factory(**kwargs):
            client = CreatedClient(is_closed=False, init_kwargs=kwargs)
            created_clients.append(client)
            return client

        sync_push._http_client = SimpleNamespace(is_closed=True)
        with patch("core.sync_push.httpx.Client", side_effect=factory):
            client = sync_push._get_client()

        self.assertIs(client, created_clients[0])
        self.assertEqual(client.init_kwargs["timeout"], 5.0)
        self.assertFalse(client.init_kwargs["verify"])


if __name__ == "__main__":
    unittest.main()