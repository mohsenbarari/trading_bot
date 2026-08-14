"""Tests for the explicit public Telegram collector command boundary."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from core.market_intelligence.public_telegram.transport import PublicTelegramCredentials
from scripts.collect_coin_market_telegram import main


class CollectCoinMarketTelegramCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "market").mkdir()
        (self.root / "session").mkdir()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _invoke(self, *arguments: str) -> tuple[int, dict[str, object]]:
        stream = io.StringIO()
        with redirect_stdout(stream):
            result = main(arguments)
        return result, json.loads(stream.getvalue())

    def test_collect_uses_only_root_bound_paths_and_redacted_results(self) -> None:
        observed = {}

        async def fake_collect(settings, **kwargs):
            observed["settings"] = settings
            observed["kwargs"] = kwargs
            return {"MELTED_AGGREGATE": {"messages": 2, "events": 1, "ignored": 1, "linked_melted_flow_trades": 0}}

        credentials = PublicTelegramCredentials(12345, "a" * 32, "+15551234567")
        with patch(
            "scripts.collect_coin_market_telegram.PublicTelegramCredentials.from_environment",
            return_value=credentials,
        ), patch(
            "scripts.collect_coin_market_telegram.collect_public_market_telegram",
            side_effect=fake_collect,
        ):
            result, payload = self._invoke(
                "--runtime-root", str(self.root),
                "--market-store", "market/market.sqlite3",
                "--session", "session/public-reader",
                "--source", "MELTED_AGGREGATE",
            )
        self.assertEqual((result, payload["status"]), (0, "COLLECTED"))
        self.assertEqual(observed["settings"].market_store_path, self.root / "market" / "market.sqlite3")
        self.assertFalse(observed["settings"].allow_interactive_login)
        self.assertNotIn(str(self.root), json.dumps(payload))

    def test_outside_or_unprepared_paths_fail_before_credentials_are_read(self) -> None:
        with patch(
            "scripts.collect_coin_market_telegram.PublicTelegramCredentials.from_environment"
        ) as credentials:
            result, payload = self._invoke(
                "--runtime-root", str(self.root),
                "--market-store", str(self.root.parent / "outside.sqlite3"),
                "--session", "session/public-reader",
            )
        self.assertEqual((result, payload["reason"], credentials.call_count), (2, "market_store_outside_runtime_root", 0))
        result, payload = self._invoke(
            "--runtime-root", str(self.root),
            "--market-store", "market/market.sqlite3",
            "--session", "missing/public-reader",
        )
        self.assertEqual((result, payload["reason"]), (2, "telegram_session_parent_unavailable"))

    def test_documented_direct_execution_resolves_the_local_package(self) -> None:
        script = Path(__file__).resolve().parents[1] / "scripts/collect_coin_market_telegram.py"
        environment = dict(os.environ)
        for key in (
            "COIN_MARKET_TELEGRAM_API_ID",
            "COIN_MARKET_TELEGRAM_API_HASH",
            "COIN_MARKET_TELEGRAM_PHONE",
        ):
            environment.pop(key, None)
        completed = subprocess.run(
            [
                sys.executable,
                str(script),
                "--runtime-root",
                str(self.root),
                "--market-store",
                "market/market.sqlite3",
                "--session",
                "session/public-reader",
            ],
            cwd=self.root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        payload = json.loads(completed.stdout)
        self.assertEqual(
            (payload["status"], payload["reason"]),
            ("FAILED", "public_telegram_api_id_missing_or_invalid"),
        )
        self.assertEqual(completed.stderr, "")


if __name__ == "__main__":
    unittest.main()
