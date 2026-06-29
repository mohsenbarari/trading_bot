import asyncio
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from core import server_routing, trade_forwarding


class ServerRoutingTests(unittest.TestCase):
    def test_normalize_server_maps_aliases_and_defaults(self):
        self.assertEqual(server_routing.normalize_server(None), server_routing.SERVER_FOREIGN)
        self.assertEqual(server_routing.normalize_server("DE"), server_routing.SERVER_FOREIGN)
        self.assertEqual(server_routing.normalize_server("german"), server_routing.SERVER_FOREIGN)
        self.assertEqual(server_routing.normalize_server("IR"), server_routing.SERVER_IRAN)
        self.assertEqual(server_routing.normalize_server("unknown", default=server_routing.SERVER_IRAN), server_routing.SERVER_IRAN)

    def test_current_and_peer_server_helpers_follow_normalized_server_mode(self):
        with patch.object(server_routing.settings, "server_mode", "IR"):
            self.assertEqual(server_routing.current_server(), server_routing.SERVER_IRAN)
            self.assertEqual(server_routing.peer_server_name(), server_routing.SERVER_FOREIGN)

        with patch.object(server_routing.settings, "server_mode", "foreign"):
            self.assertEqual(server_routing.current_server(), server_routing.SERVER_FOREIGN)
            self.assertEqual(server_routing.peer_server_name(), server_routing.SERVER_IRAN)

    def test_current_server_override_is_scoped_and_normalized(self):
        with patch.object(server_routing.settings, "server_mode", "foreign"):
            self.assertEqual(server_routing.current_server(), server_routing.SERVER_FOREIGN)
            with server_routing.override_current_server("IR"):
                self.assertEqual(server_routing.current_server(), server_routing.SERVER_IRAN)
                self.assertEqual(server_routing.peer_server_name(), server_routing.SERVER_FOREIGN)
            self.assertEqual(server_routing.current_server(), server_routing.SERVER_FOREIGN)

    def test_current_server_override_is_context_local_for_concurrent_tasks(self):
        async def read_with_override(server: str) -> tuple[str, str]:
            with server_routing.override_current_server(server):
                before = server_routing.current_server()
                await asyncio.sleep(0)
                after = server_routing.current_server()
                return before, after

        async def run_concurrent_reads() -> tuple[tuple[str, str], tuple[str, str]]:
            return await asyncio.gather(
                read_with_override("iran"),
                read_with_override("foreign"),
            )

        with patch.object(server_routing.settings, "server_mode", "foreign"):
            iran_result, foreign_result = asyncio.run(run_concurrent_reads())

        self.assertEqual(iran_result, (server_routing.SERVER_IRAN, server_routing.SERVER_IRAN))
        self.assertEqual(foreign_result, (server_routing.SERVER_FOREIGN, server_routing.SERVER_FOREIGN))

    def test_host_from_request_handles_missing_headers_original_host_and_normalization(self):
        self.assertEqual(server_routing._host_from_request(None), "")
        self.assertEqual(server_routing._host_from_request(SimpleNamespace()), "")

        request = SimpleNamespace(
            headers={"x-original-host": "Mini-App.362514.ir:443, proxy", "host": "ignored"},
            client=SimpleNamespace(host="127.0.0.1"),
        )
        self.assertEqual(server_routing._host_from_request(request), "mini-app.362514.ir")

    def test_server_from_request_prefers_forwarded_host_and_telegram_override(self):
        request = SimpleNamespace(
            headers={"x-forwarded-host": "coin.gold-trade.ir:443", "host": "coin.362514.ir"},
            client=SimpleNamespace(host="127.0.0.1"),
        )

        with patch.object(server_routing.settings, "iran_server_aliases", "coin.gold-trade.ir"), \
             patch.object(server_routing.settings, "foreign_server_aliases", "coin.362514.ir"):
            self.assertEqual(server_routing.server_from_request(request), server_routing.SERVER_IRAN)
            self.assertEqual(
                server_routing.server_from_request(request, force_telegram_foreign=True),
                server_routing.SERVER_FOREIGN,
            )

    def test_server_from_request_ignores_forwarded_host_from_untrusted_peer(self):
        request = SimpleNamespace(
            headers={"x-forwarded-host": "coin.gold-trade.ir:443", "host": "coin.362514.ir"},
            client=SimpleNamespace(host="198.51.100.10"),
        )

        with patch.object(server_routing.settings, "iran_server_aliases", "coin.gold-trade.ir"), \
             patch.object(server_routing.settings, "foreign_server_aliases", "coin.362514.ir"):
            self.assertEqual(server_routing._host_from_request(request), "coin.362514.ir")
            self.assertEqual(server_routing.server_from_request(request), server_routing.SERVER_FOREIGN)

    def test_server_from_request_uses_configured_domains_aliases_and_current_server_fallback(self):
        request_iran = SimpleNamespace(headers={"host": "iran.custom.example"})
        request_foreign = SimpleNamespace(headers={"host": "foreign.custom.example"})
        request_builtin_foreign = SimpleNamespace(headers={"host": "coin.362514.ir:8443"})
        request_unknown = SimpleNamespace(headers={"host": "unknown.internal"})

        with patch.object(server_routing.settings, "iran_server_domain", "iran.custom.example"), \
             patch.object(server_routing.settings, "iran_server_url", None), \
             patch.object(server_routing.settings, "frontend_url", "https://iran.custom.example"), \
             patch.object(server_routing.settings, "foreign_server_domain", "foreign.custom.example"), \
             patch.object(server_routing.settings, "iran_server_aliases", ""), \
             patch.object(server_routing.settings, "foreign_server_aliases", "coin.362514.ir"), \
             patch.object(server_routing.settings, "server_mode", "IRAN"):
            self.assertEqual(server_routing.server_from_request(request_iran), server_routing.SERVER_IRAN)
            self.assertEqual(server_routing.server_from_request(request_foreign), server_routing.SERVER_FOREIGN)
            self.assertEqual(server_routing.server_from_request(request_builtin_foreign), server_routing.SERVER_FOREIGN)
            self.assertEqual(server_routing.server_from_request(request_unknown), server_routing.SERVER_IRAN)

    def test_server_from_request_uses_current_server_for_ambiguous_shared_host(self):
        request = SimpleNamespace(headers={"host": "staging.362514.ir"})

        with patch.object(server_routing.settings, "iran_server_domain", None), \
             patch.object(server_routing.settings, "iran_server_url", None), \
             patch.object(server_routing.settings, "frontend_url", None), \
             patch.object(server_routing.settings, "foreign_server_domain", None), \
             patch.object(server_routing.settings, "foreign_server_url", None), \
             patch.object(server_routing.settings, "germany_server_url", None), \
             patch.object(server_routing.settings, "iran_server_aliases", "staging.362514.ir"), \
             patch.object(server_routing.settings, "foreign_server_aliases", "staging.362514.ir"), \
             patch.object(server_routing.settings, "server_mode", "foreign"):
            self.assertEqual(server_routing.server_from_request(request), server_routing.SERVER_FOREIGN)

        with patch.object(server_routing.settings, "iran_server_domain", None), \
             patch.object(server_routing.settings, "iran_server_url", None), \
             patch.object(server_routing.settings, "frontend_url", None), \
             patch.object(server_routing.settings, "foreign_server_domain", None), \
             patch.object(server_routing.settings, "foreign_server_url", None), \
             patch.object(server_routing.settings, "germany_server_url", None), \
             patch.object(server_routing.settings, "iran_server_aliases", "staging.362514.ir"), \
             patch.object(server_routing.settings, "foreign_server_aliases", "staging.362514.ir"), \
             patch.object(server_routing.settings, "server_mode", "iran"):
            self.assertEqual(server_routing.server_from_request(request), server_routing.SERVER_IRAN)

    def test_peer_server_url_for_uses_specific_urls_and_legacy_fallback(self):
        with patch.object(server_routing.settings, "server_mode", "foreign"), \
             patch.object(server_routing.settings, "iran_server_url", "https://iran.example/"), \
             patch.object(server_routing.settings, "germany_server_url", "https://germany.example/"), \
             patch.object(server_routing.settings, "peer_server_url", None), \
             patch.object(server_routing.settings, "foreign_server_url", None):
            self.assertEqual(server_routing.peer_server_url_for("iran"), "https://iran.example")
            self.assertIsNone(server_routing.peer_server_url_for("foreign"))

        with patch.object(server_routing.settings, "server_mode", "iran"), \
             patch.object(server_routing.settings, "germany_server_url", "https://germany.example/"), \
             patch.object(server_routing.settings, "peer_server_url", None), \
             patch.object(server_routing.settings, "foreign_server_url", None):
            self.assertEqual(server_routing.peer_server_url_for("foreign"), "https://germany.example")

        with patch.object(server_routing.settings, "server_mode", "iran"), \
             patch.object(server_routing.settings, "germany_server_url", None), \
             patch.object(server_routing.settings, "peer_server_url", "https://legacy-peer.example/"), \
             patch.object(server_routing.settings, "foreign_server_url", None):
            self.assertEqual(server_routing.peer_server_url_for("foreign"), "https://legacy-peer.example")

        with patch.object(server_routing.settings, "server_mode", "iran"), \
             patch.object(server_routing.settings, "germany_server_url", None), \
             patch.object(server_routing.settings, "peer_server_url", None), \
             patch.object(server_routing.settings, "foreign_server_url", "https://legacy-foreign.example/"):
            self.assertEqual(server_routing.peer_server_url_for("unknown-target"), "https://legacy-foreign.example")

        with patch.object(server_routing.settings, "server_mode", "iran"), \
             patch.object(server_routing.settings, "germany_server_url", None), \
             patch.object(server_routing.settings, "peer_server_url", None), \
             patch.object(server_routing.settings, "foreign_server_url", None):
            self.assertIsNone(server_routing.peer_server_url_for("foreign"))

    def test_default_peer_server_url_and_is_remote_home_follow_current_server(self):
        with patch.object(server_routing.settings, "server_mode", "foreign"), \
             patch.object(server_routing.settings, "iran_server_url", "https://iran.example/"):
            self.assertEqual(server_routing.default_peer_server_url(), "https://iran.example")
            self.assertFalse(server_routing.is_remote_home(None))
            self.assertFalse(server_routing.is_remote_home("germany"))
            self.assertTrue(server_routing.is_remote_home("iran"))


class TradeForwardingSignatureTests(unittest.TestCase):
    def test_verify_internal_signature_accepts_valid_signed_payload(self):
        body = b'{"offer_id":1,"quantity":5}'
        timestamp = 1_700_000_000

        with patch.object(trade_forwarding.settings, "sync_api_key", "secret"), \
             patch("core.trade_forwarding.time.time", return_value=timestamp):
            signature = trade_forwarding.sign_internal_payload(body.decode(), timestamp)

            self.assertTrue(
                trade_forwarding.verify_internal_signature(body, str(timestamp), signature, "secret")
            )

    def test_verify_internal_signature_rejects_stale_missing_or_wrong_key_payloads(self):
        body = b'{"offer_id":1}'

        with patch.object(trade_forwarding.settings, "sync_api_key", "secret"), \
             patch("core.trade_forwarding.time.time", return_value=200):
            fresh_signature = trade_forwarding.sign_internal_payload(body.decode(), 200)

            self.assertFalse(trade_forwarding.verify_internal_signature(body, None, fresh_signature, "secret"))
            self.assertFalse(trade_forwarding.verify_internal_signature(body, "not-a-number", fresh_signature, "secret"))
            self.assertFalse(trade_forwarding.verify_internal_signature(body, "100", fresh_signature, "secret"))
            self.assertFalse(trade_forwarding.verify_internal_signature(body, "200", fresh_signature, "wrong"))


class ForwardTradeToHomeServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_forward_trade_returns_service_unavailable_without_peer_url(self):
        with patch("core.trade_forwarding.peer_server_url_for", return_value=None):
            status_code, body = await trade_forwarding.forward_trade_to_home_server("iran", {"offer_id": 12})

        self.assertEqual(status_code, 503)
        self.assertEqual(body, {"detail": "سرور مرجع معامله در دسترس نیست."})

    async def test_forward_trade_posts_signed_payload_and_returns_json(self):
        recorded: dict[str, object] = {}

        class Response:
            status_code = 201
            text = ""

            def json(self):
                return {"ok": True, "trade_id": 55}

        class ClientSpy:
            def __init__(self, *args, **kwargs):
                recorded["init"] = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, content, headers):
                recorded["url"] = url
                recorded["content"] = content
                recorded["headers"] = headers
                return Response()

        payload = {"offer_id": 12, "quantity": 5}
        timestamp = 1_700_000_123
        expected_signature = None

        with patch("core.trade_forwarding.peer_server_url_for", return_value="https://iran.example"), \
             patch("core.trade_forwarding.current_server", return_value="foreign"), \
             patch.object(trade_forwarding.settings, "sync_api_key", "secret"), \
             patch.object(trade_forwarding.settings, "trade_forward_timeout_seconds", 9.5), \
             patch("core.trade_forwarding.time.time", return_value=timestamp), \
             patch("core.trade_forwarding.httpx.AsyncClient", ClientSpy):
            expected_signature = trade_forwarding.sign_internal_payload(
                trade_forwarding._json_body(payload),
                timestamp,
            )
            status_code, body = await trade_forwarding.forward_trade_to_home_server("iran", payload)

        self.assertEqual(status_code, 201)
        self.assertEqual(body, {"ok": True, "trade_id": 55})
        self.assertEqual(recorded["init"], {"timeout": 9.5, "verify": False})
        self.assertEqual(recorded["url"], "https://iran.example/api/trades/internal/execute")
        self.assertEqual(recorded["content"], trade_forwarding._json_body(payload))

        headers = recorded["headers"]
        self.assertEqual(headers["X-API-Key"], "secret")
        self.assertEqual(headers["X-Timestamp"], str(timestamp))
        self.assertEqual(headers["X-Source-Server"], "foreign")
        self.assertEqual(headers["X-Signature"], expected_signature)

    async def test_forward_trade_tls_verification_can_use_boolean_or_ca_bundle(self):
        recorded: list[dict[str, object]] = []

        class Response:
            status_code = 200
            text = ""

            def json(self):
                return {"ok": True}

        class ClientSpy:
            def __init__(self, *args, **kwargs):
                recorded.append(kwargs)

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, content, headers):
                return Response()

        with patch("core.trade_forwarding.peer_server_url_for", return_value="https://iran.example"), \
             patch("core.trade_forwarding.httpx.AsyncClient", ClientSpy), \
             patch.object(trade_forwarding.settings, "trade_forward_verify_tls", True), \
             patch.object(trade_forwarding.settings, "trade_forward_ca_bundle", None):
            await trade_forwarding.forward_trade_to_home_server("iran", {"offer_id": 1})

        with patch("core.trade_forwarding.peer_server_url_for", return_value="https://iran.example"), \
             patch("core.trade_forwarding.httpx.AsyncClient", ClientSpy), \
             patch.object(trade_forwarding.settings, "trade_forward_verify_tls", False), \
             patch.object(trade_forwarding.settings, "trade_forward_ca_bundle", "/etc/ssl/internal-ca.pem"):
            await trade_forwarding.forward_trade_to_home_server("iran", {"offer_id": 1})

        self.assertEqual(recorded[0]["verify"], True)
        self.assertEqual(recorded[1]["verify"], "/etc/ssl/internal-ca.pem")

    async def test_forward_trade_maps_timeout_and_request_errors(self):
        class TimeoutClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, content, headers):
                raise httpx.TimeoutException("timed out")

        class ErrorClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, content, headers):
                raise httpx.RequestError("network down")

        with patch("core.trade_forwarding.peer_server_url_for", return_value="https://iran.example"), \
             patch("core.trade_forwarding.httpx.AsyncClient", TimeoutClient):
            status_code, body = await trade_forwarding.forward_trade_to_home_server("iran", {"offer_id": 1})

        self.assertEqual(status_code, 504)
        self.assertIn("مهلت ارتباط", body["detail"])

        with patch("core.trade_forwarding.peer_server_url_for", return_value="https://iran.example"), \
             patch("core.trade_forwarding.httpx.AsyncClient", ErrorClient):
            status_code, body = await trade_forwarding.forward_trade_to_home_server("iran", {"offer_id": 1})

        self.assertEqual(status_code, 503)
        self.assertIn("ارتباط با سرور مرجع معامله برقرار نشد", body["detail"])

    async def test_forward_trade_returns_safe_fallback_for_invalid_json(self):
        class Response:
            status_code = 502
            text = "bad gateway with internal upstream detail"

            def json(self):
                raise ValueError("invalid json")

        class ClientSpy:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, content, headers):
                return Response()

        with patch("core.trade_forwarding.peer_server_url_for", return_value="https://iran.example"), \
             patch("core.trade_forwarding.httpx.AsyncClient", ClientSpy):
            status_code, body = await trade_forwarding.forward_trade_to_home_server("iran", {"offer_id": 1})

        self.assertEqual(status_code, 502)
        self.assertEqual(body, {"detail": "پاسخ نامعتبر از سرور مرجع معامله"})

    async def test_forward_trade_warning_logs_are_structured_and_redacted(self):
        records: list[logging.LogRecord] = []

        class CaptureHandler(logging.Handler):
            def emit(self, record):
                records.append(record)

        class TimeoutClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, content, headers):
                raise httpx.TimeoutException("timed out")

        logger = logging.getLogger("core.trade_forwarding")
        handler = CaptureHandler()
        logger.addHandler(handler)
        try:
            with patch("core.trade_forwarding.peer_server_url_for", return_value="https://iran.example"), \
                 patch("core.trade_forwarding.current_server", return_value="foreign"), \
                 patch("core.trade_forwarding.httpx.AsyncClient", TimeoutClient):
                await trade_forwarding.forward_trade_to_home_server(
                    "iran",
                    {"offer_id": 1, "idempotency_key": "secret-idem", "responder_mobile": "09120000000"},
                )
        finally:
            logger.removeHandler(handler)

        self.assertTrue(records)
        record = records[-1]
        self.assertEqual(record.getMessage(), "trade_forward.timeout")
        self.assertEqual(record.source_server, "foreign")
        self.assertEqual(record.target_server, "iran")
        self.assertEqual(record.offer_id, 1)
        self.assertTrue(record.has_idempotency_key)
        self.assertNotIn("09120000000", str(record.__dict__))
        self.assertNotIn("secret-idem", str(record.__dict__))


if __name__ == "__main__":
    unittest.main()
