"""Active regressions for retirement of direct FI<->IR HTTP surfaces.

These tests do not resolve a peer, open a client, or contact a network.  They
prove the architecture fence runs before each historical direct HTTP boundary.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, Mock, patch

from core import connectivity, notifications, sync_push, sync_worker
from core.legacy_direct_fi_ir_transport_fence import (
    LegacyDirectFiIrTransportRetiredError,
)


class LegacyDirectPeerHttpTransportFenceTests(unittest.IsolatedAsyncioTestCase):
    def test_sync_push_public_boundary_rejects_before_peer_url_or_executor(self) -> None:
        with patch("core.server_routing.default_peer_server_url") as peer_url, patch.object(
            sync_push, "_executor"
        ) as executor, patch("core.sync_push.httpx.Client") as client_constructor:
            with self.assertRaises(LegacyDirectFiIrTransportRetiredError):
                sync_push.push_sync_direct({"table": "offers", "id": 7})

        peer_url.assert_not_called()
        executor.submit.assert_not_called()
        client_constructor.assert_not_called()

    def test_sync_push_internal_boundaries_reject_before_client_or_request(self) -> None:
        with patch("core.sync_push._get_client") as get_client:
            with self.assertRaises(LegacyDirectFiIrTransportRetiredError):
                sync_push._do_push({"table": "offers", "id": 7}, "https://peer.invalid", "key")
        get_client.assert_not_called()

        with patch("core.sync_push.httpx.Client") as client_constructor, patch(
            "core.sync_push.assert_runtime_sync_transport_allowed"
        ) as transport_guard:
            with self.assertRaises(LegacyDirectFiIrTransportRetiredError):
                sync_push._get_client()
        client_constructor.assert_not_called()
        transport_guard.assert_not_called()

    async def test_iran_notification_relay_rejects_before_legacy_push(self) -> None:
        with patch.object(notifications.settings, "server_mode", "iran"), patch(
            "core.notifications.push_sync_direct"
        ) as push_direct:
            with self.assertRaises(LegacyDirectFiIrTransportRetiredError):
                await notifications.send_telegram_message(42, "blocked")

        push_direct.assert_not_called()

    async def test_sync_worker_rejects_before_redis_peer_url_or_http_client(self) -> None:
        client = AsyncMock()
        with self.assertRaises(LegacyDirectFiIrTransportRetiredError):
            await sync_worker.send_sync_item(client, {"id": 7}, "https://peer.invalid", "key")
        client.post.assert_not_awaited()

        with patch("core.sync_worker.redis.Redis") as redis_constructor, patch(
            "core.sync_worker.default_peer_server_url"
        ) as peer_url, patch("core.sync_worker.httpx.AsyncClient") as client_constructor, patch(
            "core.sync_worker.assert_background_job_authority"
        ) as authority_guard:
            with self.assertRaises(LegacyDirectFiIrTransportRetiredError):
                await sync_worker.main()

        redis_constructor.assert_not_called()
        peer_url.assert_not_called()
        client_constructor.assert_not_called()
        authority_guard.assert_not_called()

    async def test_iran_connectivity_never_creates_a_peer_http_client(self) -> None:
        with patch.object(connectivity.settings, "server_mode", "iran"), patch(
            "core.connectivity.httpx.AsyncClient"
        ) as client_constructor:
            result = await connectivity.check_connectivity()

        self.assertFalse(result)
        client_constructor.assert_not_called()

    def test_iran_connectivity_target_factory_is_independently_fenced(self) -> None:
        with self.assertRaises(LegacyDirectFiIrTransportRetiredError):
            connectivity._iran_connectivity_target_url()


if __name__ == "__main__":
    unittest.main()
