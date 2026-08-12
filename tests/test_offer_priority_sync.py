"""Safety tests for the Iran -> foreign offer publication acceleration."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

from core.offer_priority_sync import _is_recent_committed_change, dispatch_offer_priority_sync_once
from core.config import settings
from core.server_routing import SERVER_IRAN, override_current_server
from core.sync_worker import SYNC_OUTBOUND_TABLE_PRIORITY
from datetime import datetime, timedelta, timezone


class OfferPrioritySyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.item = {
            "table": "offers",
            "id": 44,
            "change_log_id": 901,
            "data": {"offer_public_id": "ofr-priority-1", "home_server": "iran"},
        }

    async def test_acknowledged_delivery_marks_only_the_committed_outbox_row(self):
        response = SimpleNamespace(status_code=200)
        with (
            override_current_server(SERVER_IRAN),
            patch.object(settings, "sync_api_key", "test-sync-key"),
            patch("core.offer_priority_sync.peer_server_url_for", return_value="https://foreign.example"),
            patch("core.offer_priority_sync.load_latest_pending_offer_priority_sync_item", new=AsyncMock(return_value=self.item)),
            patch("core.sync_worker.send_sync_item", new=AsyncMock(return_value=response)) as send,
            patch("core.sync_worker.peer_response_is_success", return_value=True),
            patch("core.sync_worker.mark_change_log_delivered", new=AsyncMock(return_value=1)) as mark,
        ):
            result = await dispatch_offer_priority_sync_once(
                object(),
                offer_public_id="ofr-priority-1",
                client=object(),
            )

        self.assertTrue(result.attempted)
        self.assertTrue(result.delivered)
        self.assertEqual(result.change_log_id, 901)
        send.assert_awaited_once_with(
            ANY,
            self.item,
            "https://foreign.example",
            "test-sync-key",
            timeout_seconds=2.0,
        )
        mark.assert_awaited_once_with(self.item)

    async def test_unacknowledged_delivery_keeps_durable_outbox_for_recovery(self):
        response = SimpleNamespace(status_code=503)
        with (
            override_current_server(SERVER_IRAN),
            patch.object(settings, "sync_api_key", "test-sync-key"),
            patch("core.offer_priority_sync.peer_server_url_for", return_value="https://foreign.example"),
            patch("core.offer_priority_sync.load_latest_pending_offer_priority_sync_item", new=AsyncMock(return_value=self.item)),
            patch("core.sync_worker.send_sync_item", new=AsyncMock(return_value=response)),
            patch("core.sync_worker.peer_response_is_success", return_value=False),
            patch("core.sync_worker.mark_change_log_delivered", new=AsyncMock()) as mark,
        ):
            result = await dispatch_offer_priority_sync_once(
                object(),
                offer_public_id="ofr-priority-1",
                client=object(),
            )

        self.assertTrue(result.attempted)
        self.assertFalse(result.delivered)
        self.assertEqual(result.skipped_reason, "peer_not_acknowledged")
        mark.assert_not_awaited()

    async def test_non_iran_process_never_attempts_foreign_publication_handoff(self):
        with patch("core.offer_priority_sync.load_latest_pending_offer_priority_sync_item", new=AsyncMock()) as load:
            result = await dispatch_offer_priority_sync_once(object(), client=object())
        self.assertFalse(result.attempted)
        self.assertEqual(result.skipped_reason, "not_iran_source")
        load.assert_not_awaited()

    def test_offer_changes_are_first_class_durable_sync_priority(self):
        self.assertEqual(SYNC_OUTBOUND_TABLE_PRIORITY[0], "offers")

    def test_fast_lane_rejects_historical_backlog(self):
        now = datetime(2026, 8, 10, tzinfo=timezone.utc)
        recent = SimpleNamespace(timestamp=now - timedelta(seconds=20))
        old = SimpleNamespace(timestamp=now - timedelta(minutes=3))
        with patch("core.offer_priority_sync.utc_now", return_value=now):
            self.assertTrue(_is_recent_committed_change(recent))
            self.assertFalse(_is_recent_committed_change(old))
