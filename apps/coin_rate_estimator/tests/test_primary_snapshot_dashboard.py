from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from live_server import (
    StateStore,
    overlay_primary_snapshot_rates,
    render_input_cards,
    render_input_health_panel,
    render_model_input_audit,
)


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
                    underlying_source=(
                        "PRIVATE_PAPER_TOMORROW_CASH_BRIDGE"
                        if settlement == "CASH"
                        else "PRIVATE_PHYSICAL_TOMORROW"
                    ),
                )
            )

    def trace(
        component: str,
        point: str,
        unit: str,
        *,
        age: float = 10.0,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            component=component,
            source_codes=(component,),
            occurred_at_utc=generated_at - timedelta(seconds=age),
            available_at_utc=generated_at - timedelta(seconds=age),
            point_value=point,
            mean_value=point,
            unit=unit,
            sample_count=2,
            selection_method="TEST_PRIMARY_INPUT",
            fallback=False,
            freshness="FRESH",
            age_seconds=age,
        )

    inputs = (
        trace(
            "PRIVATE_GOLD_PAPER_NORMAL_TOMORROW",
            "96300000",
            "TOMAN_PER_MESGHAL_750",
        ),
        trace(
            "PRIVATE_GOLD_PHYSICAL_TOMORROW",
            "96150000",
            "TOMAN_PER_MESGHAL_750",
        ),
        trace("USD_HERAT_CASH", "207300", "TOMAN_PER_USD"),
        trace("USD_HERAT_TOMORROW", "208500", "TOMAN_PER_USD"),
        trace("XAUUSD", "4456.03", "USD_PER_TROY_OUNCE"),
        trace("USDT_IRT", "208999", "TOMAN_PER_USDT"),
        trace("SOURCE_INPUT_GROUP_1", "217000", "PROJECT_THOUSAND_TOMAN"),
        trace("SOURCE_INPUT_GROUP_2", "216900", "PROJECT_THOUSAND_TOMAN"),
        trace(
            "SOURCE_INPUT_MELTED_PRIMARY",
            "96150000",
            "TOMAN_PER_MESGHAL_750",
        ),
        trace("SOURCE_INPUT_USD_HERAT", "208500", "TOMAN_PER_USD"),
        trace("SOURCE_INPUT_XAUUSD", "4456.03", "USD_PER_TROY_OUNCE"),
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
        inputs=inputs,
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
        cash_inputs = projected["settlements"]["CASH"]["inputs"]
        tomorrow_inputs = projected["settlements"]["TOMORROW"]["inputs"]
        self.assertEqual(cash_inputs["melted_gold"]["point_price"], 96_300_000)
        self.assertEqual(tomorrow_inputs["melted_gold"]["point_price"], 96_150_000)
        self.assertEqual(cash_inputs["usd"]["point_price"], 207_300)
        self.assertEqual(tomorrow_inputs["usd"]["point_price"], 208_500)
        self.assertEqual(cash_inputs["xauusd"]["point_price"], 4456.03)
        self.assertFalse(cash_inputs["xauusd"]["is_proxy"])
        self.assertEqual(
            projected["input_health"]["collectors"]["private_gold_primary"]["status"],
            "HEALTHY",
        )
        self.assertEqual(
            projected["input_health"]["model_inputs"]["usd"]["settlements"],
            {"CASH": "OBSERVED", "TOMORROW": "OBSERVED"},
        )
        input_html = render_input_cards(projected["settlements"])
        audit_html = render_model_input_audit(projected["settlements"])
        health_html = render_input_health_panel(projected["input_health"])
        self.assertIn("کانال خصوصی آب‌شده", input_html)
        self.assertIn("اونس جهانی مستقیم", input_html)
        self.assertIn("اونس جهانی مستقیم", audit_html)
        self.assertNotIn("پراکسی اونس جهانی", input_html + audit_html)
        self.assertNotIn("UNKNOWN", health_html)

    def test_stale_primary_input_is_visible_as_stale_but_not_as_active_value(self) -> None:
        now = datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)
        primary = snapshot(now - timedelta(seconds=5))
        tomorrow_herat = next(
            item for item in primary.inputs if item.component == "USD_HERAT_TOMORROW"
        )
        tomorrow_herat.freshness = "STALE"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            path.write_text("{}", encoding="utf-8")
            with patch(
                "live_server.EstimatorSnapshotV2.model_validate",
                return_value=primary,
            ):
                projected = overlay_primary_snapshot_rates(
                    {"settlements": {}}, snapshot_path=path, now_utc=now
                )

        tomorrow = projected["settlements"]["TOMORROW"]["inputs"]["usd"]
        self.assertEqual(tomorrow["status"], "NO_DATA")
        self.assertIsNone(tomorrow["point_price"])
        self.assertEqual(
            projected["input_health"]["model_inputs"]["usd"]["settlements"]["TOMORROW"],
            "STALE",
        )

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
