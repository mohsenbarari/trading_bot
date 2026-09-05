"""Historical evidence may fail without disconnecting live subscriptions."""

import asyncio
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from core.market_intelligence.private_capture import CaptureRuntimeError
from core.market_intelligence.private_capture_telegram import TelegramCaptureProvider
from core.market_intelligence import private_pipeline_foundation as foundation


class ReplayLiveIsolationTests(unittest.TestCase):
    def provider(self, failure=None):
        provider = object.__new__(TelegramCaptureProvider)
        provider.backfill_not_before = foundation.utc_now()
        provider.backfill_in_progress = False
        provider.replay_blocked_reason = None
        provider.reconciliation_truncated = False
        provider.config = SimpleNamespace(sources=[SimpleNamespace(source_code="MELTED_FLOW")])
        provider._ensure_replay_run = Mock(return_value="run")
        provider._backfill_source_to_cutoff = AsyncMock()
        provider.engine = SimpleNamespace(state=SimpleNamespace(
            complete_replay_run=Mock(side_effect=failure),
        ))
        return provider

    def test_incomplete_history_keeps_live_path_open_and_readiness_degraded(self):
        provider = self.provider(CaptureRuntimeError("capture_replay_source_incomplete"))
        asyncio.run(provider._run_initial_replay(object()))
        self.assertFalse(provider.backfill_in_progress)
        self.assertEqual(provider.replay_blocked_reason, "capture_replay_source_incomplete")
        self.assertEqual(provider.live_status, "live-degraded")
        provider.engine.state.complete_replay_run.assert_called_once_with("run")

    def test_integrity_and_storage_failures_still_stop_capture(self):
        for failure in (
            CaptureRuntimeError("capture_replay_manifest_tampered"),
            CaptureRuntimeError("capture_replay_completion_race"),
            OSError("durable write failed"),
        ):
            with self.subTest(error=str(failure)):
                provider = self.provider(failure)
                with self.assertRaises(type(failure)):
                    asyncio.run(provider._run_initial_replay(object()))
                self.assertFalse(provider.backfill_in_progress)

    def test_source_failure_is_not_misclassified_as_completion_failure(self):
        provider = self.provider()
        provider._backfill_source_to_cutoff.side_effect = CaptureRuntimeError(
            "capture_replay_source_incomplete"
        )
        with self.assertRaises(CaptureRuntimeError):
            asyncio.run(provider._run_initial_replay(object()))
        provider.engine.state.complete_replay_run.assert_not_called()

    def test_complete_replay_retains_normal_live_readiness(self):
        provider = self.provider()
        asyncio.run(provider._run_initial_replay(object()))
        self.assertEqual(provider.live_status, "live-ready")
        provider.reconciliation_truncated = True
        self.assertEqual(provider.live_status, "live-degraded")

    def test_health_rejects_quarantined_replay_even_with_fresh_starting_heartbeat(self):
        for extra in (
            {"replay_blocked_reason": "capture_replay_source_incomplete"},
            {"sources": {"MELTED_FLOW": {"explicit_backfill": {"quarantined": 1}}}},
        ):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as directory:
                state = Path(directory) / "market-capture-account1"
                state.mkdir()
                document = {
                    "schema": "market_capture_engine/1.0",
                    "role": "market-capture-account1",
                    "mode": "live",
                    "status": "live-starting",
                    "updated_at_utc": foundation.utc_now().isoformat(),
                    "pid": os.getpid(),
                    "sources": {key: {} for key in (
                        "MELTED_PRIMARY_FLOW", "MELTED_AGGREGATE", "MELTED_FLOW", "USD_HERAT", "XAUUSD"
                    )},
                }
                with patch.dict(os.environ, {"MARKET_PIPELINE_STATE_ROOT": directory}), patch("os.kill"):
                    (state / "health.json").write_text(json.dumps(document))
                    self.assertEqual(foundation.run_healthcheck("market-capture-account1", 60), 0)
                    if "sources" in extra:
                        document["sources"].update(extra["sources"])
                    else:
                        document.update(extra)
                    (state / "health.json").write_text(json.dumps(document))
                    self.assertEqual(foundation.run_healthcheck("market-capture-account1", 60), 1)

    def test_live_run_reconciles_all_sources_after_historical_failure(self):
        provider = self.provider(CaptureRuntimeError("capture_replay_source_incomplete"))
        sources = ("MELTED_FLOW", "MELTED_PRIMARY_FLOW", "USD_HERAT", "MELTED_AGGREGATE", "XAUUSD")
        provider.config = SimpleNamespace(
            api_id=1, api_hash="test", connection_retries=1,
            reconciliation_interval_seconds=300,
            sources=[SimpleNamespace(source_code=s, peer_id=i + 1) for i, s in enumerate(sources)],
        )
        provider.session_path = Path("unused.session")
        provider.stop = threading.Event()
        provider.heartbeat = None
        provider._fatal = None
        provider._entity_by_source = {}
        provider._peer_by_runtime_id = {}
        client = SimpleNamespace(
            connect=AsyncMock(), disconnect=AsyncMock(),
            is_user_authorized=AsyncMock(return_value=True),
            get_entity=AsyncMock(side_effect=lambda peer: SimpleNamespace(id=peer)),
            on=lambda event: lambda callback: callback,
        )
        reconciled = []
        async def reconcile(_client, policy):
            reconciled.append(policy.source_code)
            if len(reconciled) == len(sources):
                provider.stop.set()
        provider._reconcile_source = reconcile
        telethon = SimpleNamespace(
            TelegramClient=lambda *args, **kwargs: client,
            events=SimpleNamespace(NewMessage=lambda: None, MessageEdited=lambda: None, MessageDeleted=lambda: None),
            utils=SimpleNamespace(get_peer_id=lambda entity: entity.id),
        )
        with patch.dict("sys.modules", {"telethon": telethon}):
            asyncio.run(provider.run())
        self.assertEqual(reconciled, list(sources))
        self.assertTrue(provider._ready_for_live_updates)
        self.assertEqual(provider.live_status, "live-degraded")
        client.disconnect.assert_awaited_once()
