"""Adversarial proof that historical peer forwarders cannot open a client."""

from __future__ import annotations

import unittest
from unittest.mock import patch

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
from scripts import dev_admin


def forbidden_call(*_args, **_kwargs):
    raise AssertionError("retired direct peer primitive was reached")


class LegacyDirectPeerForwarderRetirementTests(unittest.IsolatedAsyncioTestCase):
    async def test_trade_and_offer_forwarders_stop_before_peer_url_or_httpx(self):
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

        self.assertEqual((trade_status, expiry_status), (503, 503))

    async def test_session_and_customer_invite_forwarders_stop_before_peer_url_or_httpx(self):
        with patch("core.session_authority.peer_server_url_for", side_effect=forbidden_call), patch(
            "core.session_authority.httpx.AsyncClient", side_effect=forbidden_call
        ):
            session_status, _ = await session_authority.fetch_remote_session_authority("iran", 7)

        with patch("core.customer_invite_forwarding.peer_server_url_for", side_effect=forbidden_call), patch(
            "core.customer_invite_forwarding.httpx.AsyncClient", side_effect=forbidden_call
        ):
            invite_status, _ = await customer_invite_forwarding.forward_customer_invite_to_iran(
                {"owner_user_id": 7, "mobile_number": "09120000000"}
            )

        self.assertEqual((session_status, invite_status), (503, 503))

    async def test_telegram_forwarders_stop_before_peer_url_or_httpx(self):
        with patch("core.telegram_registration_transport.peer_server_url_for", side_effect=forbidden_call), patch(
            "core.telegram_registration_transport.httpx.AsyncClient", side_effect=forbidden_call
        ):
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

        self.assertEqual((registration_status, otp_status), (503, 503))

    async def test_standard_invitation_stops_before_peer_url_or_httpx(self):
        with patch("core.invitation_creation_forwarding.peer_server_url_for", side_effect=forbidden_call), patch(
            "core.invitation_creation_forwarding.httpx.AsyncClient", side_effect=forbidden_call
        ):
            status, _ = await invitation_creation_forwarding.forward_standard_invitation_to_iran(
                {"requester_user_id": 7, "idempotency_key": "test"}
            )

        self.assertEqual(status, 503)

    async def test_customer_invite_health_and_gate_stop_before_peer_url_httpx_or_redis(self):
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

    async def test_dev_admin_session_reset_stops_before_user_or_peer_data_is_read(self):
        class UnreadableUser:
            def __getattr__(self, _name):
                raise AssertionError("retired session reset read user data")

        status, payload = await dev_admin.forward_remote_session_reset(UnreadableUser(), "iran")

        self.assertEqual(status, 503)
        self.assertEqual(payload, {"detail": "Legacy direct peer session reset is retired."})


if __name__ == "__main__":
    unittest.main()
