from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from live_server import StateStore, overlay_primary_snapshot_rates


INSTRUMENTS = (
    "COIN_IMAM",
    "COIN_BAHAR",
    "COIN_QUARTER_BAHAR",
    "COIN_HALF_BAHAR",
    "COIN_QUARTER_LOW_DATE",
    "COIN_HALF_LOW_DATE",
    "COIN_ONE_GRAM",
)


def snapshot(generated_at: datetime) -> SimpleNamespace:
    rates = []
    for settlement in ("CASH", "TOMORROW"):
        for index, instrument in enumerate(INSTRUMENTS, start=1):
            value = 50_000 + index * 1_000
            rates.append(
                SimpleNamespace(
                    instrument=instrument,
                    settlement=settlement,
                    status="ESTIMATED",
                    value=value,
                    lower_bound=value - 100,
                    upper_bound=value + 100,
                    confidence="HIGH",
                    method="TEST_METHOD",
                    reason_code=None,
                    market_regime="RANGE",
                    anchor_age_seconds=30.0,
                )
            )
    return SimpleNamespace(
        feed_mode="PRIVATE_PRIMARY",
        status="OK",
        generated_at_utc=generated_at,
        rates=tuple(rates),
        reason_codes=(),
        model_version="coin-rate-engine-v8",
        snapshot_id="a" * 64,
        snapshot_version=42,
    )


class PrimarySnapshotDashboardTests(unittest.TestCase):
    def test_projects_complete_fresh_primary_grid(self) -> None:
        now = datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            path.write_text(json.dumps({"snapshot": {}}), encoding="utf-8")
            with patch(
                "live_server.EstimatorSnapshotV2.model_validate",
                return_value=snapshot(now - timedelta(seconds=5)),
            ):
                projected = overlay_primary_snapshot_rates(
                    {"service_status": "INPUT_CRITICAL", "settlements": {}},
                    snapshot_path=path,
                    now_utc=now,
                )

        self.assertEqual(projected["service_status"], "RUNNING")
        self.assertEqual(projected["input_health"]["status"], "HEALTHY")
        self.assertEqual(len(projected["settlements"]["CASH"]["rates"]), 7)
        self.assertEqual(len(projected["settlements"]["TOMORROW"]["rates"]), 7)
        imam = projected["settlements"]["CASH"]["rates"][0]
        self.assertEqual(imam["commodity_name"], "امام")
        self.assertEqual(imam["estimated_project_price"], 51_000)
        self.assertEqual(imam["estimated_price_toman"], 51_000_000)

    def test_stale_snapshot_fails_closed_to_existing_state(self) -> None:
        now = datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)
        existing = {"service_status": "INPUT_CRITICAL", "settlements": {}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            path.write_text("{}", encoding="utf-8")
            with patch(
                "live_server.EstimatorSnapshotV2.model_validate",
                return_value=snapshot(now - timedelta(minutes=4)),
            ):
                projected = overlay_primary_snapshot_rates(
                    existing,
                    snapshot_path=path,
                    now_utc=now,
                )
        self.assertEqual(projected, existing)

    def test_state_store_reads_primary_snapshot_at_request_time(self) -> None:
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            path.write_text("{}", encoding="utf-8")
            store = StateStore(primary_snapshot_path=path)
            with patch(
                "live_server.EstimatorSnapshotV2.model_validate",
                return_value=snapshot(now),
            ):
                self.assertEqual(store.get()["service_status"], "RUNNING")


if __name__ == "__main__":
    unittest.main()
