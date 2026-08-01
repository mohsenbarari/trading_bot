"""API contracts proving no retired direct FI<->IR route reaches a peer."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from api.routers import sync as sync_router
from core import (
    customer_invite,
    customer_invite_forwarding,
    invitation_creation_forwarding,
    offer_expiry_forwarding,
    session_authority,
    telegram_otp_transport,
    telegram_registration_transport,
    trade_forwarding,
)
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, override_current_server
from core.legacy_direct_fi_ir_transport_fence import LegacyDirectFiIrTransportRetiredError
from scripts import dev_admin, seed_shared_sync_tables, sync_repair_tool


def forbidden_call(*_args, **_kwargs):
    raise AssertionError("retired direct peer primitive was reached")


class LegacyDirectPeerTransportApiContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_retired_sync_http_endpoints_are_gone_before_app_dependencies(self) -> None:
        app = FastAPI()
        app.include_router(sync_router.router, prefix="/api/sync")
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            receive = await client.post("/api/sync/receive", json=[])
            resync = await client.post("/api/sync/resync")

        for response in (receive, resync):
            with self.subTest(path=response.request.url.path):
                self.assertEqual(response.status_code, 410)
                detail = str(response.json()["detail"])
                self.assertIn("FI-to-IR", detail)
                self.assertIn("IR-to-FI", detail)
                self.assertIn("Object Storage", detail)
                self.assertIn("Witness", detail)

    async def test_trade_offer_and_session_forwarders_stop_before_peer_url_or_httpx(self) -> None:
        with patch("core.trade_forwarding.peer_server_url_for", side_effect=forbidden_call), patch(
            "core.trade_forwarding.httpx.AsyncClient", side_effect=forbidden_call
        ):
            trade_status, _ = await trade_forwarding.forward_trade_to_home_server("iran", {"offer_id": 1})

        with patch("core.offer_expiry_forwarding.peer_server_url_for", side_effect=forbidden_call), patch(
            "core.offer_expiry_forwarding.httpx.AsyncClient", side_effect=forbidden_call
        ):
            expiry_status, _ = await offer_expiry_forwarding.forward_offer_expiry_to_home_server(
                "foreign", {"offer_id": 1}
            )

        with patch("core.session_authority.peer_server_url_for", side_effect=forbidden_call), patch(
            "core.session_authority.httpx.AsyncClient", side_effect=forbidden_call
        ):
            session_status, _ = await session_authority.fetch_remote_session_authority("iran", 7)

        self.assertEqual((trade_status, expiry_status, session_status), (503, 503, 503))

    async def test_invitation_and_telegram_forwarders_stop_before_peer_url_or_httpx(self) -> None:
        with patch("core.customer_invite_forwarding.peer_server_url_for", side_effect=forbidden_call), patch(
            "core.customer_invite_forwarding.httpx.AsyncClient", side_effect=forbidden_call
        ):
            customer_status, _ = await customer_invite_forwarding.forward_customer_invite_to_iran(
                {"owner_user_id": 7, "mobile_number": "09120000000"}
            )

        with override_current_server(SERVER_FOREIGN), patch(
            "core.invitation_creation_forwarding.peer_server_url_for", side_effect=forbidden_call
        ), patch("core.invitation_creation_forwarding.httpx.AsyncClient", side_effect=forbidden_call):
            invitation_status, _ = await invitation_creation_forwarding.forward_standard_invitation_to_iran(
                {"requester_user_id": 7, "idempotency_key": "test"}
            )

        with override_current_server(SERVER_FOREIGN), patch(
            "core.telegram_registration_transport.peer_server_url_for", side_effect=forbidden_call
        ), patch("core.telegram_registration_transport.httpx.AsyncClient", side_effect=forbidden_call):
            registration_status, _ = await telegram_registration_transport._post_signed_iran_command(
                path="/api/auth/internal/telegram-registration/reconcile",
                payload={"idempotency_key": "test"},
                command_id="test",
                event="test",
                timeout_seconds=None,
            )

        with override_current_server(SERVER_IRAN), patch(
            "core.telegram_otp_transport.peer_server_url_for", side_effect=forbidden_call
        ), patch("core.telegram_otp_transport.httpx.AsyncClient", side_effect=forbidden_call):
            otp_status, _ = await telegram_otp_transport.forward_telegram_otp_delivery(object())

        self.assertEqual(
            (customer_status, invitation_status, registration_status, otp_status),
            (503, 503, 503, 503),
        )

    async def test_customer_invite_health_and_readiness_stop_before_peer_or_redis(self) -> None:
        with patch("core.customer_invite.peer_server_url_for", side_effect=forbidden_call), patch(
            "core.customer_invite.httpx.AsyncClient", side_effect=forbidden_call
        ):
            health, reason = await customer_invite._fetch_iran_sync_health()

        with patch("core.customer_invite.current_server", return_value=SERVER_FOREIGN), patch(
            "core.customer_invite.peer_server_url_for", side_effect=forbidden_call
        ), patch("core.customer_invite.httpx.AsyncClient", side_effect=forbidden_call), patch(
            "core.customer_invite._foreign_local_sync_queues_clean", side_effect=forbidden_call
        ):
            gate = await customer_invite.check_customer_invite_sync_ready(wait_seconds=0)

        self.assertIsNone(health)
        self.assertEqual(reason, "legacy_direct_transport_retired")
        self.assertFalse(gate.ready)
        self.assertEqual(gate.reason, "legacy_direct_transport_retired")
        self.assertIn("بازنشسته", gate.message or "")

    async def test_ops_cli_direct_session_and_seed_paths_stop_before_user_or_http(self) -> None:
        class UnreadableUser:
            def __getattr__(self, _name):
                raise AssertionError("retired session-reset route read user data")

        status, payload = await dev_admin.forward_remote_session_reset(UnreadableUser(), "iran")
        self.assertEqual(status, 503)
        self.assertEqual(payload, {"detail": "Legacy direct peer session reset is retired."})

        with self.assertRaises(LegacyDirectFiIrTransportRetiredError):
            await seed_shared_sync_tables.send_items(
                "https://peer.invalid", "must-not-be-read", [{"id": 1}]
            )

    def test_sync_repair_apply_cli_stops_before_database_or_peer_resolution(self) -> None:
        with self.assertRaises(LegacyDirectFiIrTransportRetiredError):
            sync_repair_tool._target_url(SimpleNamespace(target_url="https://peer.invalid"))
        with self.assertRaises(LegacyDirectFiIrTransportRetiredError):
            sync_repair_tool._send_items("https://peer.invalid", "must-not-be-read", [{"id": 1}])

        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = sync_repair_tool.main(
                [
                    "replay-row",
                    "--table",
                    "offers",
                    "--identity",
                    '{"offer_public_id":"ofr_test"}',
                    "--apply",
                ]
            )

        self.assertEqual(exit_code, 2)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "blocked_legacy_direct_fi_ir_transport_retired")
        self.assertEqual(payload["component"], "sync-repair-tool")


if __name__ == "__main__":
    unittest.main()
