from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import coin_estimator as estimator_module
from coin_estimator import (
    COMMODITY_SPECS,
    NO_DATA_TOKEN,
    TRUSTED_TRAINING_SOURCE_KINDS,
    apply_low_date_family_band_separation,
    enforce_cash_tomorrow_term_structure,
    average_market_value,
    asymmetric_tolerance,
    calibration_rows,
    estimate_rates,
    fresh_transfer_anchor_qhat,
    load_conversation_offer_labels,
    load_group_confirmed_trade_labels,
    latest_melted_events_by_type,
    low_date_family_sibling_name,
    market_order_flow,
    quantile,
    select_effective_usd_average,
    select_empirical_cash_tomorrow_ratio,
    select_generic_coin_average,
    select_group_offer_anchor,
    select_historical_group_anchor,
    select_live_xauusd_average,
    select_melted_average,
    summarize_order_flow,
    weighted_quantile,
)
from live_server import (
    GroupLiveInputControl,
    ensure_manual_entry_schema,
    fa_datetime,
    health_response,
    insert_manual_entry,
    insert_manual_trade_for_open_offer,
    list_open_manual_offers,
    parse_offer_text,
    persist_message,
    render_group_activity_fragment,
    render_page,
    render_shadow_page,
)


def make_market_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE price_events (
            id INTEGER PRIMARY KEY,
            instrument TEXT NOT NULL,
            market_label TEXT NOT NULL,
            settlement_term TEXT NOT NULL,
            trade_form TEXT NOT NULL,
            event_type TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity_num REAL,
            price_num REAL NOT NULL,
            event_time_utc TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE external_market_observations (
            id INTEGER PRIMARY KEY,
            instrument_code TEXT NOT NULL,
            observed_at_utc TEXT NOT NULL,
            interval_seconds INTEGER NOT NULL,
            quote_kind TEXT NOT NULL,
            normalized_price_num REAL,
            volume_value TEXT,
            UNIQUE(instrument_code, observed_at_utc, interval_seconds, quote_kind)
        )
        """
    )
    connection.executemany(
        """
        INSERT INTO price_events(
            instrument, market_label, settlement_term, trade_form,
            event_type, side, quantity_num, price_num, event_time_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("MELTED_GOLD", "آبشده نقدی", "TODAY", "PHYSICAL", "QUOTE", "UNKNOWN", None, 80_000_000, "2026-07-20T10:00:30Z"),
            ("MELTED_GOLD", "آبشده نقدی", "TODAY", "PHYSICAL", "QUOTE", "UNKNOWN", None, 82_000_000, "2026-07-20T10:00:45Z"),
            ("GOLD_COIN", "سکه نقدی", "TODAY", "PHYSICAL", "QUOTE", "UNKNOWN", None, 185_000_000, "2026-07-20T10:00:40Z"),
            ("MELTED_GOLD", "آبشده نقدی", "TODAY", "PHYSICAL", "QUOTE", "UNKNOWN", None, 1, "2026-07-20T09:59:00Z"),
            ("MELTED_GOLD", "آبشده رسمی", "TODAY", "PHYSICAL", "QUOTE", "UNKNOWN", None, 83_000_000, "2026-07-20T10:01:30Z"),
            ("MELTED_GOLD", "آبشده امروزی", "TODAY", "PAPER", "QUOTE", "UNKNOWN", None, 85_000_000, "2026-07-20T10:01:40Z"),
            ("MELTED_GOLD", "آبشده حواله", "UNKNOWN", "PAPER", "QUOTE", "UNKNOWN", None, 84_000_000, "2026-07-20T10:02:30Z"),
        ],
    )
    connection.commit()
    connection.close()


def make_conversation_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE messages (
            import_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            event_time_utc TEXT NOT NULL,
            PRIMARY KEY(import_id, message_id)
        );
        CREATE TABLE offers (
            id INTEGER PRIMARY KEY,
            import_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            commodity TEXT NOT NULL,
            price INTEGER NOT NULL,
            quantity INTEGER,
            side TEXT NOT NULL,
            settlement TEXT NOT NULL,
            trade_form TEXT NOT NULL,
            confidence REAL NOT NULL,
            offer_index INTEGER NOT NULL DEFAULT 0,
            source_text TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE confirmed_trades (
            id INTEGER PRIMARY KEY,
            import_id INTEGER NOT NULL,
            confirmation_message_id INTEGER NOT NULL,
            offer_message_id INTEGER,
            event_time_utc TEXT NOT NULL,
            commodity TEXT NOT NULL,
            price INTEGER NOT NULL,
            quantity INTEGER,
            side TEXT NOT NULL,
            settlement TEXT NOT NULL,
            trade_form TEXT NOT NULL,
            confidence REAL NOT NULL
        );
        INSERT INTO messages VALUES (1, 10, '2026-07-20T10:00:20Z');
        INSERT INTO messages VALUES (1, 11, '2026-07-20T10:00:50Z');
        INSERT INTO offers(
          id, import_id, message_id, commodity, price, side, settlement, trade_form, confidence
        ) VALUES
          (1, 1, 10, 'امام', 184900, 'BUY', 'CASH', 'PHYSICAL', 0.95),
          (2, 1, 11, 'امام', 186000, 'SELL', 'CASH', 'PHYSICAL', 0.95);
        """
    )
    connection.commit()
    connection.close()


def model() -> dict:
    rows = []
    for index, spec in enumerate(COMMODITY_SPECS.values(), 1):
        calibration = {
            "source": "TEST",
            "sample_count": 10,
            "bubble_ratio_median": 0.1 if not spec.low_date else 0.0,
            "bubble_ratio_q10": 0.0,
            "bubble_ratio_q90": 0.2,
            "confidence": "LOW",
            "direct_settlement_sample_count": 10,
        }
        rows.append(
            {
                "id": index,
                "name": spec.name,
                "status": "SUPPORTED",
                "coefficient": spec.coefficient,
                "low_date": spec.low_date,
                "settlements": {"CASH": calibration, "TOMORROW": calibration},
            }
        )
    return {
        "model_kind": "TEST_HYBRID",
        "commodities": rows,
    }


class EstimatorTests(unittest.TestCase):
    def test_group_live_control_persists_and_replays_on_reconnect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            control_path = Path(directory) / "group-live-control.json"
            control = GroupLiveInputControl(control_path)
            self.assertTrue(control.get()["enabled"])
            disconnected = control.set_enabled(False, changed_by="bahar")
            self.assertFalse(disconnected["enabled"])
            self.assertIsNotNone(disconnected["disabled_since_utc"])
            restored = GroupLiveInputControl(control_path)
            self.assertFalse(restored.get()["enabled"])
            connected = restored.set_enabled(True, changed_by="bahar")
            self.assertTrue(connected["enabled"])
            self.assertIsNone(connected["disabled_since_utc"])

    def test_disconnected_live_group_events_stay_out_but_history_remains_available(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            conversation_path = Path(directory) / "conversation.sqlite3"
            make_conversation_db(conversation_path)
            cutoff = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)
            paused = select_group_offer_anchor(
                conversation_path,
                commodity="امام",
                settlement="CASH",
                trade_form="PHYSICAL",
                end=datetime(2026, 7, 20, 10, 1, tzinfo=timezone.utc),
                group_live_events_before=cutoff,
            )
            self.assertEqual(paused["status"], "NO_DATA")

            historical = select_historical_group_anchor(
                conversation_path,
                commodity="امام",
                settlement="CASH",
                trade_form="PHYSICAL",
                end=datetime(2026, 7, 20, 10, 1, tzinfo=timezone.utc),
                group_live_events_before=datetime(
                    2026, 7, 20, 10, 1, 1, tzinfo=timezone.utc
                ),
            )
            self.assertEqual(historical["status"], "OBSERVED")

            resumed = select_group_offer_anchor(
                conversation_path,
                commodity="امام",
                settlement="CASH",
                trade_form="PHYSICAL",
                end=datetime(2026, 7, 20, 10, 1, tzinfo=timezone.utc),
            )
            self.assertEqual(resumed["status"], "OBSERVED")

    def test_estimator_control_disables_only_recent_group_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            market_path = Path(directory) / "market.sqlite3"
            conversation_path = Path(directory) / "conversation.sqlite3"
            make_market_db(market_path)
            make_conversation_db(conversation_path)
            estimator_model = model()
            estimator_model["group_offer_anchor"] = {"enabled": True}
            end = datetime(2026, 7, 20, 10, 1, tzinfo=timezone.utc)
            connected = estimate_rates(
                estimator_model, market_path, end, conversation_path
            )
            disconnected = estimate_rates(
                estimator_model,
                market_path,
                end,
                conversation_path,
                live_group_events_enabled=False,
                group_live_events_before=datetime(
                    2026, 7, 20, 10, 0, tzinfo=timezone.utc
                ),
            )

        connected_anchor = connected["settlements"]["CASH"]["rates"][0][
            "group_offer_anchor"
        ]
        disconnected_anchor = disconnected["settlements"]["CASH"]["rates"][0][
            "group_offer_anchor"
        ]
        self.assertEqual(connected_anchor["status"], "OBSERVED")
        self.assertEqual(disconnected_anchor["status"], "NO_DATA")
        self.assertEqual(
            disconnected["live_group_event_control"]["status"],
            "DISCONNECTED_LIVE_ONLY",
        )
        self.assertTrue(
            disconnected["live_group_event_control"][
                "historical_group_data_enabled"
            ]
        )

    def test_operator_manual_confirmed_trade_is_a_trusted_training_source(self) -> None:
        self.assertIn(
            "OPERATOR_MANUAL_CONFIRMED_TRADE",
            TRUSTED_TRAINING_SOURCE_KINDS,
        )

    def test_offer_text_suggestions_and_raw_text_excludes_operator_clock(self) -> None:
        suggested = parse_offer_text("۲ تا ربع تاریخ پایین ۵۴٬۰۰۰ ف فردا حواله")
        self.assertEqual(suggested["commodity"], "ربع تاریخ پایین")
        self.assertEqual(suggested["settlement"], "TOMORROW")
        self.assertEqual(suggested["trade_form"], "PAPER")
        self.assertEqual(suggested["side"], "SELL")
        self.assertEqual(suggested["price"], 54_000)
        self.assertEqual(suggested["quantity"], 2)

        with tempfile.TemporaryDirectory() as directory:
            conversation_path = Path(directory) / "conversation.sqlite3"
            make_conversation_db(conversation_path)
            insert_manual_entry(
                conversation_path,
                {
                    "commodity": "ربع تاریخ پایین",
                    "settlement": "TOMORROW",
                    "trade_form": "PAPER",
                    "side": "SELL",
                    "price": "54000",
                    "quantity": "2",
                    "offer_time": "2026-07-20T13:30",
                    "raw_offer_text": (
                        "۱۳:۳۰ — ۲ تا ربع تاریخ پایین ۵۴٬۰۰۰ ف فردا حواله"
                    ),
                },
            )
            connection = sqlite3.connect(conversation_path)
            try:
                raw = connection.execute(
                    "SELECT raw_offer_text FROM manual_coin_offers"
                ).fetchone()[0]
            finally:
                connection.close()
        self.assertEqual(raw, "۲ تا ربع تاریخ پایین ۵۴٬۰۰۰ ف فردا حواله")

    def test_manual_operator_trade_becomes_the_recent_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            conversation_path = Path(directory) / "conversation.sqlite3"
            make_conversation_db(conversation_path)
            ensure_manual_entry_schema(conversation_path)
            inserted = insert_manual_entry(
                conversation_path,
                {
                    "commodity": "امام",
                    "settlement": "CASH",
                    "trade_form": "PHYSICAL",
                    "side": "SELL",
                    "price": "186500",
                    "quantity": "12",
                    "offer_time": "2026-07-20T13:30",
                    "trade_confirmed": "1",
                    "trade_time": "2026-07-20T13:31",
                    "trade_price": "186400",
                    "trade_quantity": "8",
                    "description": "operator verified",
                },
            )
            self.assertIsNotNone(inserted["trade_id"])
            anchor = select_group_offer_anchor(
                conversation_path,
                commodity="امام",
                settlement="CASH",
                trade_form="PHYSICAL",
                end=datetime(2026, 7, 20, 10, 2, tzinfo=timezone.utc),
            )
            self.assertEqual(anchor["reference_source"], "RECENT_CONFIRMED_TRADE_WEIGHTED_MEDIAN")
            self.assertEqual(anchor["reference_price_toman"], 186_400_000)
            self.assertEqual(anchor["offer_count"], 2)
            self.assertEqual(anchor["trade_count"], 1)

    def test_manual_historical_offer_is_available_to_training_loader(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            conversation_path = Path(directory) / "conversation.sqlite3"
            make_conversation_db(conversation_path)
            ensure_manual_entry_schema(conversation_path)
            insert_manual_entry(
                conversation_path,
                {
                    "commodity": "ربع بهار",
                    "settlement": "TOMORROW",
                    "trade_form": "PAPER",
                    "side": "BUY",
                    "price": "52000",
                    "offer_time": "2026-07-20T13:00",
                    "description": "manual historical offer",
                },
            )
            labels, stats = load_conversation_offer_labels(conversation_path)
            manual = [row for row in labels if row["source_kind"] == "OPERATOR_MANUAL_OFFER"]
            self.assertEqual(len(manual), 1)
            self.assertEqual(manual[0]["commodity_name"], "ربع بهار")
            self.assertEqual(manual[0]["lifecycle_training_weight"], 1 / 3)
            self.assertEqual(stats["manual_offers_total"], 1)

    def test_open_manual_offer_can_be_confirmed_later_without_duplication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            conversation_path = Path(directory) / "conversation.sqlite3"
            make_conversation_db(conversation_path)
            ensure_manual_entry_schema(conversation_path)
            inserted = insert_manual_entry(
                conversation_path,
                {
                    "commodity": "نیم بهار",
                    "settlement": "CASH",
                    "trade_form": "PHYSICAL",
                    "side": "SELL",
                    "price": "92000",
                    "quantity": "10",
                    "offer_time": "2026-07-20T13:30",
                },
            )
            self.assertEqual(len(list_open_manual_offers(conversation_path)), 1)
            trade = insert_manual_trade_for_open_offer(
                conversation_path,
                {
                    "offer_id": str(inserted["offer_id"]),
                    "trade_time": "2026-07-20T13:32",
                    "trade_quantity": "6",
                },
            )
            self.assertIsNotNone(trade["trade_id"])
            self.assertEqual(list_open_manual_offers(conversation_path), [])
            labels = load_group_confirmed_trade_labels(conversation_path)
            manual = [row for row in labels if row["source_kind"] == "OPERATOR_MANUAL_CONFIRMED_TRADE"]
            self.assertEqual(len(manual), 1)
            self.assertEqual(manual[0]["quantity"], 6)

    def test_live_persistence_rolls_back_and_retries_sqlite_lock(self) -> None:
        connection = MagicMock()
        message = SimpleNamespace(
            id=123,
            date=datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc),
            message="test",
            fwd_from=None,
        )
        with (
            patch("live_server.parse_message", return_value=[object()]),
            patch(
                "live_server.upsert_raw_post",
                side_effect=[sqlite3.OperationalError("database is locked"), 44],
            ) as upsert,
            patch("live_server.replace_price_events", return_value=1),
            patch("live_server.infer_naghdp_trade_sides"),
            patch("live_server.time.sleep") as sleep,
        ):
            count = persist_message(connection, "MELTED_FLOW", "NaghdP", message)

        self.assertEqual(count, 1)
        self.assertEqual(upsert.call_count, 2)
        connection.rollback.assert_called_once()
        connection.commit.assert_called_once()
        sleep.assert_called_once_with(0.2)

    def test_live_persistence_rolls_back_non_retryable_database_error(self) -> None:
        connection = MagicMock()
        message = SimpleNamespace(
            id=123,
            date=datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc),
            message="test",
            fwd_from=None,
        )
        with (
            patch("live_server.parse_message", return_value=[object()]),
            patch(
                "live_server.upsert_raw_post",
                side_effect=sqlite3.OperationalError("malformed database schema"),
            ) as upsert,
        ):
            with self.assertRaisesRegex(sqlite3.OperationalError, "malformed"):
                persist_message(connection, "MELTED_FLOW", "NaghdP", message)

        self.assertEqual(upsert.call_count, 1)
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()

    def test_recent_group_offers_build_book_band_not_latest_price_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            market_path = root / "market.sqlite3"
            conversation_path = root / "conversation.sqlite3"
            make_market_db(market_path)
            make_conversation_db(conversation_path)
            anchor = select_group_offer_anchor(
                conversation_path,
                commodity="امام",
                settlement="CASH",
                trade_form="PHYSICAL",
                end=datetime(2026, 7, 20, 10, 1, tzinfo=timezone.utc),
            )
            self.assertEqual(anchor["latest_price_toman"], 186_000_000)
            self.assertEqual(anchor["best_bid_toman"], 184_900_000)
            self.assertEqual(anchor["best_ask_toman"], 186_000_000)
            self.assertEqual(anchor["reference_price_toman"], 185_450_000)
            self.assertEqual(anchor["reference_source"], "ACTIVE_TWO_SIDED_BOOK_MID")

            candidate = model()
            candidate["group_offer_anchor"] = {
                "enabled": True,
                "window_seconds": 600,
                "minimum_confidence": 0.8,
            }
            result = estimate_rates(
                candidate,
                market_path,
                datetime(2026, 7, 20, 10, 1, tzinfo=timezone.utc),
                conversation_path,
            )
            imam = result["settlements"]["CASH"]["rates"][0]
            self.assertEqual(imam["estimated_price_toman"], 185_450_000)
            self.assertEqual(
                imam["method"],
                "RECENT_TRADE_THEN_ACTIVE_BOOK_BAND_5M+ASYMMETRIC_REGIME_AND_ORDER_FLOW_TOLERANCE",
            )

    def test_recent_confirmed_trade_has_priority_over_offer_book(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            conversation_path = root / "conversation.sqlite3"
            make_conversation_db(conversation_path)
            connection = sqlite3.connect(conversation_path)
            connection.execute(
                """
                INSERT INTO confirmed_trades VALUES (
                  1, 1, 20, NULL, '2026-07-20T10:00:55Z', 'امام',
                  185700, 20, 'BUY', 'CASH', 'PHYSICAL', 0.98
                )
                """
            )
            connection.commit()
            connection.close()

            anchor = select_group_offer_anchor(
                conversation_path,
                commodity="امام",
                settlement="CASH",
                trade_form="PHYSICAL",
                end=datetime(2026, 7, 20, 10, 1, tzinfo=timezone.utc),
            )

        self.assertEqual(anchor["trade_count"], 1)
        self.assertEqual(anchor["reference_price_toman"], 185_700_000)
        self.assertEqual(
            anchor["reference_source"], "RECENT_CONFIRMED_TRADE_WEIGHTED_MEDIAN"
        )

    def test_offer_older_than_five_minutes_is_not_live_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            conversation_path = root / "conversation.sqlite3"
            make_conversation_db(conversation_path)
            anchor = select_group_offer_anchor(
                conversation_path,
                commodity="امام",
                settlement="CASH",
                trade_form="PHYSICAL",
                end=datetime(2026, 7, 20, 10, 6, tzinfo=timezone.utc),
                seconds=600,
            )

        self.assertEqual(anchor["status"], "NO_DATA")
        self.assertEqual(anchor["window_seconds"], 300)

    def test_newer_quality_offer_is_not_suppressed_by_an_older_trade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            conversation_path = Path(directory) / "conversation.sqlite3"
            make_conversation_db(conversation_path)
            connection = sqlite3.connect(conversation_path)
            connection.execute(
                """
                INSERT INTO confirmed_trades VALUES (
                  1, 1, 20, NULL, '2026-07-20T10:00:55Z', 'امام',
                  187300, 10, 'BUY', 'CASH', 'PHYSICAL', 0.98
                )
                """
            )
            connection.execute(
                "INSERT INTO messages VALUES (2, 30, '2026-07-20T10:20:00Z')"
            )
            connection.execute(
                """
                INSERT INTO offers(
                  id,import_id,message_id,commodity,price,quantity,side,
                  settlement,trade_form,confidence
                ) VALUES (3,2,30,'امام',186500,10,'SELL','CASH','PHYSICAL',0.95)
                """
            )
            connection.commit()
            connection.close()

            anchor = select_historical_group_anchor(
                conversation_path,
                commodity="امام",
                settlement="CASH",
                trade_form="PHYSICAL",
                end=datetime(2026, 7, 20, 10, 30, tzinfo=timezone.utc),
            )

        self.assertEqual(anchor["latest_kind"], "OFFER")
        self.assertTrue(anchor["latest_is_consistent"])
        self.assertEqual(anchor["reference_price_toman"], 186_500_000)
        self.assertIn("OFFER", anchor["reference_source"])

    def test_fresh_transfer_anchor_does_not_inherit_wide_structural_floor(self) -> None:
        qhat = fresh_transfer_anchor_qhat(
            {
                "age_seconds": 40 * 60,
                "latest_kind": "TRADE",
                "relative_mad": 0.001,
            },
            structural_qhat=0.0183,
        )
        self.assertLess(qhat, 0.004)
        self.assertGreaterEqual(qhat, 0.0025)

    def test_empirical_settlement_ratio_uses_near_synchronous_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            conversation_path = Path(directory) / "conversation.sqlite3"
            make_conversation_db(conversation_path)
            connection = sqlite3.connect(conversation_path)
            for index in range(5):
                cash_message = 100 + index * 2
                future_message = cash_message + 1
                minute = 10 + index
                connection.execute(
                    "INSERT INTO messages VALUES (?,?,?)",
                    (2, cash_message, f"2026-07-20T10:{minute:02d}:00Z"),
                )
                connection.execute(
                    "INSERT INTO messages VALUES (?,?,?)",
                    (2, future_message, f"2026-07-20T10:{minute:02d}:20Z"),
                )
                connection.execute(
                    """INSERT INTO offers(
                       id,import_id,message_id,commodity,price,quantity,side,
                       settlement,trade_form,confidence
                       ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (100 + index * 2, 2, cash_message, "امام", 200000, 10, "BUY", "CASH", "PHYSICAL", 0.95),
                )
                connection.execute(
                    """INSERT INTO offers(
                       id,import_id,message_id,commodity,price,quantity,side,
                       settlement,trade_form,confidence
                       ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (101 + index * 2, 2, future_message, "امام", 201000, 10, "SELL", "TOMORROW", "PHYSICAL", 0.95),
                )
            connection.commit()
            connection.close()

            ratio = select_empirical_cash_tomorrow_ratio(
                conversation_path,
                commodity="امام",
                trade_form="PHYSICAL",
                end=datetime(2026, 7, 20, 11, 0, tzinfo=timezone.utc),
            )

        self.assertEqual(ratio["status"], "OBSERVED")
        self.assertEqual(ratio["scope"], "COMMODITY")
        self.assertEqual(ratio["pair_count"], 5)
        self.assertAlmostEqual(ratio["ratio"], 1.005)

    def test_empirical_ratio_snapshot_cache_is_copy_safe(self) -> None:
        estimator_module._EMPIRICAL_RATIO_SNAPSHOT_CACHE.clear()
        end = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
        result = {"status": "OBSERVED", "ratio": 1.002, "sample": [1]}
        with patch.object(
            estimator_module,
            "_select_empirical_cash_tomorrow_ratio_uncached",
            return_value=result,
        ) as raw:
            first = select_empirical_cash_tomorrow_ratio(
                Path("/tmp/ratio-cache.sqlite3"),
                commodity="امام",
                trade_form="PHYSICAL",
                end=end,
            )
            first["sample"].append(2)
            second = select_empirical_cash_tomorrow_ratio(
                Path("/tmp/ratio-cache.sqlite3"),
                commodity="امام",
                trade_form="PHYSICAL",
                end=end,
            )
        self.assertEqual(raw.call_count, 1)
        self.assertEqual(second["sample"], [1])

    def test_calibration_prefers_recent_bubble_regime(self) -> None:
        rows = [
            {
                "bubble_ratio": 0.0,
                "source_weight": 1.0,
                "event_time_utc": f"2026-07-10T10:00:0{index}Z",
                "source_kind": "TELEGRAM_GROUP_CONFIRMED_TRADE",
            }
            for index in range(3)
        ]
        rows.append(
            {
                "bubble_ratio": 0.2,
                "source_weight": 1.0,
                "event_time_utc": "2026-07-20T10:00:00Z",
                "source_kind": "TELEGRAM_GROUP_CONFIRMED_TRADE",
            }
        )

        calibration = calibration_rows(rows, "TEST_RECENCY")

        self.assertEqual(calibration["bubble_ratio_median"], 0.2)
        self.assertEqual(calibration["recency_half_life_days"], 1.0)

    def test_offer_class_cannot_collectively_outweigh_confirmed_trades(self) -> None:
        rows = [
            {
                "bubble_ratio": 0.10,
                "source_weight": 1.5,
                "event_time_utc": "2026-07-20T10:00:00Z",
                "source_kind": "TELEGRAM_GROUP_CONFIRMED_TRADE",
            }
        ]
        rows.extend(
            {
                "bubble_ratio": 0.30,
                "source_weight": 1.0 / 3.0,
                "event_time_utc": f"2026-07-20T10:00:{index + 1:02d}Z",
                "source_kind": "TELEGRAM_GROUP_OFFER",
                "side": "BUY" if index % 2 else "SELL",
            }
            for index in range(20)
        )

        calibration = calibration_rows(rows, "TEST_HIERARCHY")

        self.assertLess(
            calibration["offer_weight_scale_for_hierarchical_cap"], 1.0
        )
        self.assertEqual(calibration["bubble_ratio_median"], 0.10)
        self.assertEqual(
            calibration["maximum_offer_training_share_with_trades"], 0.40
        )

    def test_confirmed_trade_loader_excludes_training_ineligible_audit_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "conversation.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                """
                CREATE TABLE confirmed_trades (
                    id INTEGER PRIMARY KEY,
                    confirmation_message_id INTEGER NOT NULL,
                    event_time_utc TEXT NOT NULL,
                    commodity TEXT NOT NULL,
                    price INTEGER NOT NULL,
                    quantity INTEGER,
                    side TEXT NOT NULL,
                    settlement TEXT NOT NULL,
                    trade_form TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    context_json TEXT NOT NULL,
                    training_eligible INTEGER NOT NULL
                )
                """
            )
            connection.executemany(
                """
                INSERT INTO confirmed_trades VALUES (
                    ?, ?, '2026-07-20T10:00:00Z', 'ربع', 52000, 10,
                    'SELL', 'CASH', 'PHYSICAL', 0.95, '{}', ?
                )
                """,
                [(1, 1001, 1), (2, 1002, 0)],
            )
            connection.commit()
            connection.close()

            labels = load_group_confirmed_trade_labels(path)

            self.assertEqual(len(labels), 1)
            self.assertEqual(labels[0]["confirmation_message_id"], 1001)

    def test_quantile_interpolates(self) -> None:
        self.assertEqual(quantile([0, 10], 0.5), 5)

    def test_weighted_quantile_respects_source_weight(self) -> None:
        self.assertEqual(weighted_quantile([1, 10], [10, 1], 0.5), 1)

    def test_cash_melted_uses_same_minute_official_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.sqlite3"
            make_market_db(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            result = select_melted_average(
                connection,
                "CASH",
                datetime(2026, 7, 20, 10, 2, tzinfo=timezone.utc),
                seconds=60,
            )
            self.assertEqual(result["average_price"], 83_000_000)
            self.assertEqual(result["selection"], "SAME_MINUTE_PHYSICAL_UNDERLYING_FALLBACK")
            self.assertEqual(result["selected_market_label"], "آبشده رسمی")

    def test_cash_melted_never_uses_paper_today_quote(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.sqlite3"
            make_market_db(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            result = select_melted_average(
                connection,
                "CASH",
                datetime(2026, 7, 20, 10, 2, tzinfo=timezone.utc),
                seconds=60,
            )
            self.assertNotEqual(result["average_price"], 85_000_000)
            self.assertEqual(result["selected_trade_form"], "PHYSICAL")

    def test_cash_melted_bridges_last_physical_with_current_paper_delta(self) -> None:
        """A stale physical base may move only by the observed paper delta."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.sqlite3"
            make_market_db(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            connection.executemany(
                """
                INSERT INTO price_events(
                    instrument, market_label, settlement_term, trade_form,
                    event_type, side, quantity_num, price_num, event_time_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "MELTED_GOLD", "آبشده امروزی", "TODAY", "PAPER",
                        "QUOTE", "UNKNOWN", None, 85_000_000,
                        "2026-07-20T10:01:25Z",
                    ),
                    (
                        "MELTED_GOLD", "آبشده امروزی", "TODAY", "PAPER",
                        "QUOTE", "UNKNOWN", None, 87_000_000,
                        "2026-07-20T10:09:45Z",
                    ),
                ],
            )
            connection.commit()
            result = select_melted_average(
                connection,
                "CASH",
                datetime(2026, 7, 20, 10, 10, tzinfo=timezone.utc),
                seconds=60,
            )
            self.assertEqual(
                result["selection"], "PHYSICAL_BASE_PLUS_PAPER_DELTA_BRIDGE"
            )
            self.assertEqual(result["selected_trade_form"], "PHYSICAL_BRIDGED_BY_PAPER")
            self.assertAlmostEqual(
                result["average_price"], 83_000_000 * 87_000_000 / 85_000_000
            )

    def test_usdt_is_not_reclassified_as_cash_herat_without_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.sqlite3"
            make_market_db(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            connection.execute(
                """
                INSERT INTO external_market_observations(
                    instrument_code, observed_at_utc, interval_seconds,
                    quote_kind, normalized_price_num
                ) VALUES ('USDT_IRT', '2026-07-20T10:09:30Z', 0, 'MID', 188500)
                """
            )
            result = select_effective_usd_average(
                connection,
                "CASH",
                datetime(2026, 7, 20, 10, 10, tzinfo=timezone.utc),
            )

            # USDT is a market-movement driver only.  Without a real Herat
            # anchor, its nominal price must never be presented as cash Herat.
            self.assertIsNone(result["average_price"])
            self.assertEqual(result["selection"], "NO_CASH_HERAT_ANCHOR")
            self.assertFalse(result["is_usdt_proxy"])
            self.assertEqual(
                result["fallback_rejected"],
                "DIRECT_USDT_PRICE_SUBSTITUTION_FORBIDDEN",
            )

    def test_tomorrow_uses_paper_herat_but_not_havale_coin_as_physical_quote(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.sqlite3"
            make_market_db(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            connection.executemany(
                """
                INSERT INTO price_events(
                    instrument, market_label, settlement_term, trade_form,
                    event_type, side, quantity_num, price_num, event_time_utc
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                [
                    (
                        "USD_HERAT",
                        "دلار هرات فردایی کاغذی",
                        "TOMORROW",
                        "PAPER",
                        "TRADE",
                        "UNKNOWN",
                        190_000,
                        "2026-07-20T10:09:30Z",
                    ),
                    (
                        "GOLD_COIN",
                        "سکه حواله",
                        "UNKNOWN",
                        "PAPER",
                        "QUOTE",
                        "UNKNOWN",
                        189_500_000,
                        "2026-07-20T10:09:35Z",
                    ),
                ],
            )
            end = datetime(2026, 7, 20, 10, 10, tzinfo=timezone.utc)
            usd = select_effective_usd_average(
                connection, "TOMORROW", end
            )
            coin = select_generic_coin_average(
                connection, "TOMORROW", end
            )

            self.assertEqual(usd["average_price"], 190_000)
            self.assertEqual(usd["selected_trade_form"], "PAPER")
            self.assertFalse(usd["is_usdt_proxy"])
            self.assertEqual(coin["status"], "NO_DATA")
            self.assertEqual(coin["selection"], "NO_DATA")
            self.assertEqual(
                coin["excluded_input_reason"],
                "AMBIGUOUS_SETTLEMENT_NOT_MODEL_ELIGIBLE",
            )
            self.assertEqual(
                coin["excluded_observations"][0]["market_label"],
                "سکه حواله",
            )

    def test_generic_coin_requires_exact_settlement_and_form_axes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.sqlite3"
            make_market_db(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            connection.executemany(
                """
                INSERT INTO price_events(
                    instrument, market_label, settlement_term, trade_form,
                    event_type, side, quantity_num, price_num, event_time_utc
                ) VALUES ('GOLD_COIN', 'سکه نقدی', ?, 'PHYSICAL',
                          'QUOTE', 'UNKNOWN', NULL, ?, ?)
                """,
                [
                    ("UNKNOWN", 190_000_000, "2026-07-20T10:09:30Z"),
                    ("TODAY", 189_000_000, "2026-07-20T10:09:32Z"),
                    ("TOMORROW", 191_000_000, "2026-07-20T10:09:35Z"),
                ],
            )
            end = datetime(2026, 7, 20, 10, 10, tzinfo=timezone.utc)

            cash = select_generic_coin_average(connection, "CASH", end)
            tomorrow = select_generic_coin_average(connection, "TOMORROW", end)

            # The UNKNOWN physical quote cannot be silently relabelled TODAY.
            self.assertEqual(cash["average_price"], 189_000_000)
            self.assertEqual(cash["selected_settlement_term"], "TODAY")
            self.assertEqual(tomorrow["average_price"], 191_000_000)
            self.assertEqual(tomorrow["selected_settlement_term"], "TOMORROW")

    def test_ime_standardized_gold_and_coin_are_corroboration_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.sqlite3"
            make_market_db(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            connection.executemany(
                """
                INSERT INTO external_market_observations(
                    instrument_code, observed_at_utc, interval_seconds,
                    quote_kind, normalized_price_num
                ) VALUES (?, ?, 0, 'LAST', ?)
                """,
                [
                    ("IME_GOLD_BAR", "2026-07-20T10:09:40Z", 81_500_000),
                    ("IME_GOLD_COIN_IMAM", "2026-07-20T10:09:45Z", 184_500_000),
                ],
            )
            end = datetime(2026, 7, 20, 10, 10, tzinfo=timezone.utc)
            melted = select_melted_average(connection, "CASH", end)
            coin = select_generic_coin_average(connection, "CASH", end)

            self.assertIsNone(melted["average_price"])
            self.assertEqual(
                melted["excluded_fallback"],
                "IME_CORROBORATION_ONLY_NOT_DIRECT_MELTED_INPUT",
            )
            self.assertIsNone(coin["average_price"])
            self.assertEqual(
                coin["excluded_fallback"],
                "IME_CORROBORATION_ONLY_NOT_DIRECT_COIN_INPUT",
            )

            tomorrow_melted = select_melted_average(connection, "TOMORROW", end)
            tomorrow_coin = select_generic_coin_average(connection, "TOMORROW", end)
            self.assertIsNone(tomorrow_melted["average_price"])
            self.assertEqual(tomorrow_coin["status"], "NO_DATA")
            self.assertEqual(
                tomorrow_coin["excluded_fallback"],
                "IME_CASH_CERTIFICATE_NOT_VALID_TOMORROW_DIRECT_ANCHOR",
            )

    def test_live_xau_uses_fresh_corroborated_paxg_only_as_proxy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.sqlite3"
            make_market_db(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            connection.execute(
                """
                INSERT INTO external_market_observations(
                    instrument_code, observed_at_utc, interval_seconds,
                    quote_kind, normalized_price_num
                ) VALUES ('PAXG_USD_PROXY', '2026-07-20T10:09:45Z', 0, 'MID', 4368.5)
                """
            )
            connection.commit()

            result = select_live_xauusd_average(
                connection,
                datetime(2026, 7, 20, 10, 10, tzinfo=timezone.utc),
                seconds=90,
            )

            self.assertEqual(result["status"], "ESTIMATED")
            self.assertTrue(result["is_proxy"])
            self.assertEqual(result["proxy_instrument"], "PAXG_USD_PROXY")
            self.assertEqual(result["point_price"], 4368.5)

            historical = estimator_module.historical_market_context(
                connection,
                "CASH",
                datetime(2026, 7, 20, 10, 10, tzinfo=timezone.utc),
            )
            self.assertEqual(historical["xauusd"]["status"], "NO_DATA")

    def test_live_xau_direct_quote_always_wins_over_proxy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.sqlite3"
            make_market_db(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            connection.execute(
                """
                INSERT INTO price_events(
                    instrument, market_label, settlement_term, trade_form,
                    event_type, side, quantity_num, price_num, event_time_utc
                ) VALUES (
                    'XAUUSD', 'اونس جهانی', 'UNKNOWN', 'UNKNOWN',
                    'QUOTE', 'UNKNOWN', NULL, 4366, '2026-07-20T10:09:50Z'
                )
                """
            )
            connection.execute(
                """
                INSERT INTO external_market_observations(
                    instrument_code, observed_at_utc, interval_seconds,
                    quote_kind, normalized_price_num
                ) VALUES ('PAXG_USD_PROXY', '2026-07-20T10:09:55Z', 0, 'MID', 4368.5)
                """
            )
            connection.commit()

            result = select_live_xauusd_average(
                connection,
                datetime(2026, 7, 20, 10, 10, tzinfo=timezone.utc),
                seconds=90,
            )

            self.assertEqual(result["status"], "OBSERVED")
            self.assertFalse(result["is_proxy"])
            self.assertEqual(result["point_price"], 4366)
            self.assertEqual(result["price_source"], "TELEGRAM_DIRECT_XAUUSD")

    def test_live_xau_rejects_proxy_outside_recent_direct_range(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.sqlite3"
            make_market_db(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            connection.execute(
                """
                INSERT INTO price_events(
                    instrument, market_label, settlement_term, trade_form,
                    event_type, side, quantity_num, price_num, event_time_utc
                ) VALUES (
                    'XAUUSD', 'اونس جهانی', 'UNKNOWN', 'UNKNOWN',
                    'QUOTE', 'UNKNOWN', NULL, 4366, '2026-07-20T10:00:30Z'
                )
                """
            )
            connection.execute(
                """
                INSERT INTO external_market_observations(
                    instrument_code, observed_at_utc, interval_seconds,
                    quote_kind, normalized_price_num
                ) VALUES ('PAXG_USD_PROXY', '2026-07-20T10:09:55Z', 0, 'MID', 5000)
                """
            )
            connection.commit()

            result = select_live_xauusd_average(
                connection,
                datetime(2026, 7, 20, 10, 10, tzinfo=timezone.utc),
                seconds=90,
            )

            self.assertEqual(result["status"], "NO_DATA")
            self.assertFalse(result["is_proxy"])
            self.assertEqual(
                result["fallback_status"],
                "PAXG_PROXY_OUTSIDE_RECENT_XAU_BAND",
            )

    def test_consecutive_buy_offers_create_positive_pressure(self) -> None:
        rows = [
            {
                "event_type": "OFFER",
                "side": "BUY",
                "quantity_num": None,
                "event_time_utc": f"2026-07-20T10:00:{second:02d}Z",
            }
            for second in (10, 20, 30, 40, 50)
        ]
        flow = summarize_order_flow(
            rows,
            datetime(2026, 7, 20, 10, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(flow["direction"], "BUY_PRESSURE")
        self.assertEqual(flow["latest_offer_streak_count"], 5)
        self.assertGreater(flow["score"], 0.5)

    def test_explicit_melted_flow_feed_is_preferred_without_changing_price_average(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.sqlite3"
            make_market_db(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            connection.executemany(
                """
                INSERT INTO price_events(
                    instrument, market_label, settlement_term, trade_form,
                    event_type, side, quantity_num, price_num, event_time_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "MELTED_GOLD_FLOW",
                        "جریان آبشده امروز",
                        "TODAY",
                        "PAPER",
                        "OFFER",
                        "BUY",
                        None,
                        99_000_000,
                        "2026-07-20T10:00:50Z",
                    ),
                    (
                        "MELTED_GOLD",
                        "آبشده امروزی",
                        "TODAY",
                        "PAPER",
                        "OFFER",
                        "SELL",
                        None,
                        85_000_000,
                        "2026-07-20T10:00:55Z",
                    ),
                ],
            )
            connection.commit()

            flow = market_order_flow(
                connection,
                "CASH",
                datetime(2026, 7, 20, 10, 1, tzinfo=timezone.utc),
            )
            melted_price = average_market_value(
                connection,
                end=datetime(2026, 7, 20, 10, 1, tzinfo=timezone.utc),
                instrument="MELTED_GOLD",
                market_label="آبشده نقدی",
            )

            melted_flow = flow["by_instrument"]["MELTED_GOLD"]["paper"]
            self.assertEqual(melted_flow["source_instrument"], "MELTED_GOLD_FLOW")
            self.assertEqual(melted_flow["buy_offer_count"], 1)
            self.assertEqual(melted_flow["sell_offer_count"], 0)
            self.assertEqual(melted_price["average_price"], 81_000_000)

    def test_tomorrow_paper_reference_is_explicit_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.sqlite3"
            make_market_db(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            result = select_melted_average(
                connection,
                "TOMORROW",
                datetime(2026, 7, 20, 10, 3, tzinfo=timezone.utc),
                seconds=60,
            )
            self.assertEqual(result["average_price"], 84_000_000)
            self.assertEqual(result["selected_trade_form"], "PAPER")
            self.assertEqual(result["selection"], "SAME_MINUTE_PAPER_REFERENCE_FALLBACK")

    def test_buy_pressure_expands_positive_tolerance_only(self) -> None:
        calibration = {
            "bubble_ratio_median": 0.10,
            "bubble_ratio_q10": 0.08,
            "bubble_ratio_q90": 0.12,
        }
        positive = asymmetric_tolerance(
            intrinsic=100_000_000,
            estimated_price=110_000_000,
            adjusted_ratio=0.10,
            calibration=calibration,
            pressure_score=0.8,
        )
        neutral = asymmetric_tolerance(
            intrinsic=100_000_000,
            estimated_price=110_000_000,
            adjusted_ratio=0.10,
            calibration=calibration,
            pressure_score=0.0,
        )
        self.assertGreater(
            positive["positive_tolerance_percent"],
            neutral["positive_tolerance_percent"],
        )
        self.assertAlmostEqual(
            positive["negative_tolerance_percent"],
            neutral["negative_tolerance_percent"],
        )

    def test_conformal_floor_widens_an_undercovered_interval(self) -> None:
        tolerance = asymmetric_tolerance(
            intrinsic=100_000_000,
            estimated_price=110_000_000,
            adjusted_ratio=0.10,
            calibration={
                "bubble_ratio_median": 0.10,
                "bubble_ratio_q10": 0.099,
                "bubble_ratio_q90": 0.101,
            },
            pressure_score=0.0,
            conformal_floor=0.02,
        )
        self.assertGreaterEqual(tolerance["negative_tolerance_percent"], 1.9)
        self.assertGreaterEqual(tolerance["positive_tolerance_percent"], 1.9)
        self.assertEqual(tolerance["conformal_floor_percent"], 2.0)

    def test_average_uses_only_trailing_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.sqlite3"
            make_market_db(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            result = average_market_value(
                connection,
                end=datetime(2026, 7, 20, 10, 1, tzinfo=timezone.utc),
                instrument="MELTED_GOLD",
                market_label="آبشده نقدی",
            )
            self.assertEqual(result["status"], "OBSERVED")
            self.assertEqual(result["sample_count"], 2)
            self.assertEqual(result["average_price"], 81_000_000)

    def test_missing_window_is_explicit_and_not_forward_filled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.sqlite3"
            make_market_db(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            result = average_market_value(
                connection,
                end=datetime(2026, 7, 20, 10, 3, tzinfo=timezone.utc),
                instrument="MELTED_GOLD",
                market_label="آبشده نقدی",
            )
            self.assertEqual(result["status"], "NO_DATA")
            self.assertEqual(result["llm_value"], NO_DATA_TOKEN)
            self.assertIsNone(result["average_price"])

    def test_imam_uses_current_generic_coin_average(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.sqlite3"
            make_market_db(path)
            result = estimate_rates(
                model(),
                path,
                datetime(2026, 7, 20, 10, 1, tzinfo=timezone.utc),
            )
            imam = result["settlements"]["CASH"]["rates"][0]
            self.assertEqual(imam["commodity_name"], "امام")
            self.assertEqual(imam["estimated_price_toman"], 185_000_000)
            self.assertEqual(
                imam["method"].split("+")[0],
                "DIRECT_GENERIC_COIN_LATEST_PLUS_30S_MEAN_ASSUMED_IMAM",
            )

    def test_cash_coin_uses_structural_estimate_without_live_coin_quote(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            market_path = Path(directory) / "market.sqlite3"
            conversation_path = Path(directory) / "conversation.sqlite3"
            make_market_db(market_path)
            make_conversation_db(conversation_path)
            connection = sqlite3.connect(market_path)
            try:
                connection.execute(
                    "DELETE FROM price_events WHERE instrument='GOLD_COIN'"
                )
                connection.commit()
            finally:
                connection.close()
            connection = sqlite3.connect(conversation_path)
            try:
                connection.execute("DELETE FROM offers")
                connection.execute("DELETE FROM messages")
                connection.commit()
            finally:
                connection.close()

            result = estimate_rates(
                model(),
                market_path,
                datetime(2026, 7, 20, 10, 1, tzinfo=timezone.utc),
                conversation_path,
            )

        imam = result["settlements"]["CASH"]["rates"][0]
        self.assertEqual(imam["status"], "ESTIMATED")
        self.assertIsNotNone(imam["estimated_project_price"])
        self.assertNotIn("RECENT_TRADE_THEN_ACTIVE_BOOK_BAND", imam["method"])
        self.assertIsNotNone(imam["intrinsic_toman"])

    def test_melted_latest_by_type_uses_newest_event_in_five_second_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.sqlite3"
            make_market_db(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            connection.execute(
                """
                INSERT INTO price_events(
                    instrument,market_label,settlement_term,trade_form,
                    event_type,side,quantity_num,price_num,event_time_utc
                ) VALUES ('MELTED_GOLD','آبشده نقدی','TODAY','PHYSICAL','QUOTE','UNKNOWN',NULL,81000000,'2026-07-20T10:00:45Z')
                """
            )
            connection.commit()
            result = latest_melted_events_by_type(
                connection,
                end=datetime(2026, 7, 20, 10, 1, tzinfo=timezone.utc),
                seconds=60,
                bucket_seconds=5,
            )
            matching = [
                row for row in result["by_type"]
                if row["market_label"] == "آبشده نقدی"
                and row["event_type"] == "QUOTE"
            ]
            self.assertEqual(len(matching), 1)
            self.assertEqual(matching[0]["latest_price"], 81_000_000)
            connection.close()

    def test_low_date_without_own_anchor_uses_melted_intrinsic_not_imam_premium(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            market_path = Path(directory) / "market.sqlite3"
            conversation_path = Path(directory) / "conversation.sqlite3"
            make_market_db(market_path)
            result = estimate_rates(
                model(),
                market_path,
                datetime(2026, 7, 20, 10, 1, tzinfo=timezone.utc),
                conversation_path,
            )

        bahar = next(
            row
            for row in result["settlements"]["CASH"]["rates"]
            if row["commodity_name"] == "بهار"
        )
        self.assertEqual(bahar["method"], "LOW_DATE_INTRINSIC_PLUS_BOUNDED_RESIDUAL")
        self.assertEqual(bahar["estimated_project_price"], 184_750)
        self.assertEqual(bahar["bubble_ratio"], 0.0)

    def test_cash_uses_live_tomorrow_anchor_with_last_settlement_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            market_path = Path(directory) / "market.sqlite3"
            conversation_path = Path(directory) / "conversation.sqlite3"
            make_market_db(market_path)
            make_conversation_db(conversation_path)
            connection = sqlite3.connect(market_path)
            try:
                connection.execute(
                    "DELETE FROM price_events WHERE instrument='GOLD_COIN'"
                )
                connection.commit()
            finally:
                connection.close()
            connection = sqlite3.connect(conversation_path)
            try:
                connection.execute("DELETE FROM offers")
                connection.execute("DELETE FROM messages")
                connection.commit()
            finally:
                connection.close()
            ensure_manual_entry_schema(conversation_path)
            insert_manual_entry(
                conversation_path,
                {
                    "commodity": "امام",
                    "settlement": "CASH",
                    "trade_form": "PHYSICAL",
                    "side": "BUY",
                    "price": "180000",
                    "offer_time": "2026-07-20T13:24",
                    "trade_confirmed": "1",
                    "trade_time": "2026-07-20T13:25",
                },
            )
            insert_manual_entry(
                conversation_path,
                {
                    "commodity": "امام",
                    "settlement": "TOMORROW",
                    "trade_form": "PHYSICAL",
                    "side": "BUY",
                    "price": "181800",
                    "offer_time": "2026-07-20T13:28",
                    "trade_confirmed": "1",
                    "trade_time": "2026-07-20T13:29",
                },
            )

            result = estimate_rates(
                model(),
                market_path,
                datetime(2026, 7, 20, 10, 1, tzinfo=timezone.utc),
                conversation_path,
            )

        imam = result["settlements"]["CASH"]["rates"][0]
        self.assertEqual(imam["status"], "ESTIMATED")
        self.assertEqual(imam["estimated_project_price"], 180_000)
        self.assertEqual(
            imam["method"],
            "CURRENT_TOMORROW_ANCHOR_X_LAST_CASH_TOMORROW_RATIO",
        )
        self.assertEqual(
            imam["settlement_ratio_anchor"]["ratio"],
            180_000 / 181_800,
        )
        self.assertEqual(
            imam["settlement_ratio_anchor"]["quality_gate"], "CONFIRMED_PAIR"
        )

    def test_low_date_cash_uses_same_settlement_imam_ratio_not_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            market_path = Path(directory) / "market.sqlite3"
            conversation_path = Path(directory) / "conversation.sqlite3"
            make_market_db(market_path)
            make_conversation_db(conversation_path)
            connection = sqlite3.connect(conversation_path)
            try:
                connection.execute("DELETE FROM offers")
                connection.execute("DELETE FROM messages")
                connection.commit()
            finally:
                connection.close()
            ensure_manual_entry_schema(conversation_path)
            for commodity, price, offer_time in (
                ("بهار", "177000", "2026-07-20T13:24"),
                ("امام", "180000", "2026-07-20T13:24"),
                ("امام", "181000", "2026-07-20T13:30"),
            ):
                insert_manual_entry(
                    conversation_path,
                    {
                        "commodity": commodity,
                        "settlement": "CASH",
                        "trade_form": "PHYSICAL",
                        "side": "BUY",
                        "price": price,
                        "offer_time": offer_time,
                    },
                )

            result = estimate_rates(
                model(),
                market_path,
                datetime(2026, 7, 20, 10, 1, tzinfo=timezone.utc),
                conversation_path,
            )

        bahar = next(
            row
            for row in result["settlements"]["CASH"]["rates"]
            if row["commodity_name"] == "بهار"
        )
        self.assertEqual(bahar["estimated_project_price"], 178_000)
        self.assertEqual(
            bahar["method"],
            "CURRENT_SAME_SETTLEMENT_IMAM_ANCHOR_X_"
            "LAST_LOW_DATE_TO_IMAM_RATIO",
        )
        self.assertEqual(
            bahar["low_date_ratio_anchor"]["ratio_to_imam"],
            177_000 / 180_000,
        )

    def test_page_explains_no_data(self) -> None:
        body = render_page(
            {
                "service_status": "RUNNING",
                "generated_at_utc": "2026-07-20T10:00:00Z",
                "window_start_utc": "2026-07-20T09:59:00Z",
                "window_end_utc": "2026-07-20T10:00:00Z",
                "settlements": {},
            }
        ).decode("utf-8")
        self.assertIn(NO_DATA_TOKEN, body)
        self.assertIn("توصیه خرید یا فروش نیست", body)
        self.assertIn("نبض بازار", body)
        self.assertIn('class="market-pulse surface-panel"', body)
        self.assertIn("مدل‌های سایه", body)
        self.assertNotIn("پایش موازی یکپارچه", body)
        self.assertNotIn("سایه ۱ — مدل قبلی", body)
        self.assertIn('id="freshness-content"', body)
        self.assertIn('getElementById("freshness-fragment")', body)

    def test_page_renders_end_to_end_input_health(self) -> None:
        body = render_page(
            {
                "service_status": "DEGRADED",
                "generated_at_utc": "2026-08-13T17:00:00Z",
                "window_start_utc": "2026-08-13T16:59:00Z",
                "window_end_utc": "2026-08-13T17:00:00Z",
                "settlements": {},
                "input_health": {
                    "status": "DEGRADED",
                    "reason_codes": ["WALLEX_PUBLIC_API_COLLECTOR_REPORTED_FAILURE"],
                    "collectors": {
                        "public_market_telegram": {"status": "HEALTHY", "heartbeat_age_seconds": 4},
                        "wallex_public_api": {"status": "DEGRADED", "heartbeat_age_seconds": 2},
                        "coin_group_projection": {
                            "status": "HEALTHY",
                            "heartbeat_age_seconds": 8,
                            "details": {
                                "group_1_latest_canonical_event_utc": "2026-08-13T16:59:58Z",
                                "group_1_latest_eligible_event_utc": "2026-08-11T08:51:10Z",
                                "group_1_pending_review_total": 59,
                                "group_1_rejected_total": 3,
                                "group_2_latest_canonical_event_utc": "2026-08-13T16:59:59Z",
                                "group_2_latest_eligible_event_utc": "2026-08-13T16:59:30Z",
                                "group_2_pending_review_total": 57,
                                "group_2_rejected_total": 3,
                            },
                        },
                    },
                    "model_inputs": {
                        "coin_groups": {
                            "status": "HISTORICAL_ONLY",
                            "settlements": {"CASH": "HISTORICAL", "TOMORROW": "NO_DATA"},
                            "latest_observation_age_seconds": 300,
                        }
                    },
                },
            }
        ).decode("utf-8")
        self.assertIn("سلامت ورودی‌های مدل", body)
        self.assertIn("تلگرام بازار عمومی", body)
        self.assertIn("نیازمند توجه", body)
        self.assertIn("ورودی گروه‌های سکه", body)
        self.assertIn("فقط لنگر تاریخی", body)
        self.assertIn("جدیدترین لنگر واجدشرایط مدل ۵ دقیقه پیش", body)
        self.assertNotIn("آخرین آپدیت گروه‌های تلگرامی", body)
        self.assertIn("ورود گروه‌ها تا مدل", body)
        self.assertIn("جدیدترین رویداد canonical گروه", body)
        self.assertIn("آخرین ورودی واجدشرایط", body)
        self.assertIn("در انتظار بررسی ۵۹", body)

    def test_health_response_is_unavailable_for_critical_inputs(self) -> None:
        status, payload = health_response(
            {
                "service_status": "INPUT_CRITICAL",
                "generated_at_utc": "2026-08-13T17:00:00Z",
                "input_health": {
                    "status": "CRITICAL",
                    "reason_codes": ["MODEL_INPUT_XAUUSD_NO_DATA"],
                    "collectors": {},
                    "model_inputs": {
                        "xauusd": {
                            "status": "NO_DATA",
                            "importance": "CRITICAL",
                            "settlements": {"CASH": "NO_DATA", "TOMORROW": "NO_DATA"},
                        }
                    },
                },
            }
        )
        self.assertEqual(status.value, 503)
        self.assertEqual(payload["status"], "CRITICAL")
        self.assertNotIn("latest_observation_utc", payload["model_inputs"]["xauusd"])

    def test_shadow_models_render_only_on_the_dedicated_polished_page(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            body = render_shadow_page(
                {
                    "service_status": "RUNNING",
                    "generated_at_utc": "2026-07-20T10:00:00Z",
                    "window_end_utc": "2026-07-20T10:00:00Z",
                    "settlements": {},
                },
                home_path="/rates/",
                shadow_path="/rates/shadow",
                shadow_data_path="/rates/shadow.json",
                shadow_estimate_path="/rates/shadow.html",
                shadow_state_path=root / "shadow-1.json",
                research_shadow_state_path=root / "shadow-2.json",
                ml_shadow_state_path=root / "shadow-3.json",
            ).decode("utf-8")

        self.assertIn('class="shadow-hero"', body)
        self.assertIn('class="shadow-panel comparison-panel"', body)
        self.assertIn('class="shadow-panel outcome-panel"', body)
        self.assertIn("سایه ۱ — مدل قبلی", body)
        self.assertIn("سایه ۲ — بازگشایی صبح", body)
        self.assertIn("سایه ۳ — یادگیری ماشین", body)
        self.assertIn("تنها معیار معتبر برای ارتقای یک سایه", body)
        self.assertIn("@media (max-width: 680px)", body)

    def test_main_dashboard_does_not_read_shadow_model_state(self) -> None:
        with patch(
            "live_server.load_shadow_dashboard_payload",
            side_effect=AssertionError("shadow state belongs to the dedicated page"),
        ):
            body = render_page(
                {
                    "service_status": "RUNNING",
                    "generated_at_utc": "2026-07-20T10:00:00Z",
                    "window_start_utc": "2026-07-20T09:59:00Z",
                    "window_end_utc": "2026-07-20T10:00:00Z",
                    "settlements": {},
                },
                shadow_path="/rates/shadow",
            ).decode("utf-8")

        self.assertIn("href='/rates/shadow'", body)
        self.assertNotIn("جدول مقایسهٔ یکپارچه", body)

    def test_page_exposes_group_live_toggle_without_hiding_activity_contract(self) -> None:
        body = render_page(
            {
                "service_status": "RUNNING",
                "generated_at_utc": "2026-07-20T10:00:00Z",
                "window_start_utc": "2026-07-20T09:59:00Z",
                "window_end_utc": "2026-07-20T10:00:00Z",
                "settlements": {},
            },
            group_live_control_path="/rates/test/group-live-control",
            group_live_control={
                "enabled": False,
                "disabled_since_utc": "2026-07-20T09:55:00Z",
                "changed_at_utc": "2026-07-20T09:55:00Z",
            },
        ).decode("utf-8")
        self.assertIn("اتصال و اعمال رویدادهای صف‌شده", body)
        self.assertIn("رویدادها همچنان ذخیره و نمایش داده می", body)
        self.assertIn('name="action" value="connect"', body)

    def test_group_activity_marks_expired_records_as_unavailable_to_the_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            conversation_path = Path(directory) / "conversation.sqlite3"
            make_conversation_db(conversation_path)
            connection = sqlite3.connect(conversation_path)
            try:
                connection.execute("ALTER TABLE messages ADD COLUMN source_html_file TEXT")
                connection.execute("UPDATE messages SET source_html_file='group_1'")
                connection.execute(
                    """
                    INSERT INTO confirmed_trades(
                      id, import_id, confirmation_message_id, offer_message_id,
                      event_time_utc, commodity, price, quantity, side,
                      settlement, trade_form, confidence
                    ) VALUES (1, 1, 10, 10, '2026-07-19T09:00:00Z',
                              'امام', 184900, 1, 'BUY', 'CASH', 'PHYSICAL', 0.95)
                    """
                )
                connection.commit()
            finally:
                connection.close()
            body = render_group_activity_fragment(conversation_path)
        self.assertIn("بدون آفر فعال برای مدل", body)
        self.assertIn("بدون معامله فعال برای مدل", body)
        self.assertIn("آخرین سوابق گروهیِ پذیرفته‌شدهٔ مدل", body)
        self.assertEqual(body.count(fa_datetime("2026-07-20T10:00:50Z")), 2)
        self.assertEqual(body.count(fa_datetime("2026-07-19T09:00:00Z")), 2)

    def test_estimate_fragment_includes_top_ticker_cards(self) -> None:
        fragment = render_page(
            {
                "service_status": "RUNNING",
                "generated_at_utc": "2026-07-20T10:00:00Z",
                "window_start_utc": "2026-07-20T09:59:00Z",
                "window_end_utc": "2026-07-20T10:00:00Z",
                "settlements": {
                    "CASH": {
                        "inputs": {
                            "melted_gold": {"average_price": 15000000, "sample_count": 5, "status": "OBSERVED"},
                            "xauusd": {"average_price": 2400.5, "sample_count": 12, "status": "OBSERVED"},
                            "usd": {"average_price": 60000, "sample_count": 8, "status": "ESTIMATED", "is_estimated": True},
                        }
                    }
                },
            },
            estimate_fragment=True,
        ).decode("utf-8")
        self.assertIn("top-ticker", fragment)
        self.assertIn("input-card", fragment)
        self.assertIn("اونس جهانی", fragment)
        self.assertIn("دلار هرات", fragment)
        self.assertIn("لیست نرخ سکه و مسکوکات", fragment)
        self.assertIn('id="freshness-fragment"', fragment)

    def test_page_renders_exact_settlement_inputs_and_group_provenance(self) -> None:
        state = {
            "service_status": "DEGRADED",
            "generated_at_utc": "2026-08-15T08:21:05Z",
            "window_start_utc": "2026-08-15T08:20:05Z",
            "window_end_utc": "2026-08-15T08:21:05Z",
            "settlements": {
                "CASH": {
                    "inputs": {
                        "melted_gold": {
                            "status": "OBSERVED",
                            "average_price": 82_875_000,
                            "point_price": 82_800_000,
                            "latest_event_utc": "2026-08-15T08:20:26Z",
                            "selection": "PRIMARY",
                            "selected_market_label": "آبشده نقدی",
                            "selected_trade_form": "PHYSICAL",
                        },
                        "usd": {
                            "status": "ESTIMATED",
                            "average_price": 186_637.5,
                            "point_price": None,
                            "anchor_event_utc": "2026-08-13T12:29:10Z",
                            "anchor_age_seconds": 158_155,
                            "selection": "CASH_HERAT_ANCHOR_USD_HERAT_TOMORROW_UP",
                            "price_source": "USD_HERAT_CASH_TIME_AND_TOMORROW_BASIS_ESTIMATE",
                            "market_movement_driver": "USD_HERAT_TOMORROW",
                        },
                        "usdt": {
                            "status": "OBSERVED",
                            "average_price": 187_116.4,
                            "point_price": 187_103,
                            "latest_event_utc": "2026-08-15T08:21:04Z",
                            "selection": "WALLEX_USDT_IRT",
                        },
                        "xauusd": {
                            "status": "ESTIMATED",
                            "average_price": 4_377.15,
                            "point_price": 4_376.91,
                            "latest_event_utc": "2026-08-15T08:20:57Z",
                            "selection": "BINANCE_PAXG_STABLECOIN_CORROBORATED_PROXY",
                            "is_proxy": True,
                        },
                        "generic_coin": {
                            "status": "NO_DATA",
                            "selection": "NO_DATA",
                            "excluded_input_reason": "AMBIGUOUS_SETTLEMENT_NOT_MODEL_ELIGIBLE",
                            "excluded_observations": [
                                {
                                    "market_label": "سکه نقدی",
                                    "settlement_term": "UNKNOWN",
                                    "trade_form": "PHYSICAL",
                                    "point_price": 189_200_000,
                                    "latest_event_utc": "2026-08-15T08:20:40Z",
                                }
                            ],
                        },
                        "order_flow": {"status": "OBSERVED", "estimator_score": 0.12},
                        "market_regime": {
                            "status": "OBSERVED",
                            "regime": "RANGE",
                            "direction_score": 0.1,
                            "confidence": 0.8,
                        },
                    },
                    "rates": [
                        {
                            "commodity_name": "امام",
                            "method": "FRESHNESS_WEIGHTED_GROUP_ANCHOR_X_CURRENT_MELTED_BLEND_STRUCTURAL_REGIME",
                            "group_offer_anchor": {"status": "NO_DATA"},
                            "historical_group_anchor": {
                                "status": "OBSERVED",
                                "reference_price_toman": 187_500_000,
                                "event_time_utc": "2026-08-15T08:04:21Z",
                                "age_seconds": 1_244,
                                "offer_count": 1,
                                "trade_count": 0,
                                "reference_source": "LATEST_QUALITY_OFFER_RECENCY_VALIDATED",
                            },
                            "anchor_weight": 0.8436,
                        }
                    ],
                },
                "TOMORROW": {
                    "inputs": {
                        "melted_gold": {
                            "status": "OBSERVED",
                            "average_price": 82_935_714,
                            "point_price": 82_900_000,
                            "latest_event_utc": "2026-08-15T08:20:29Z",
                            "selection": "SAME_MINUTE_PAPER_REFERENCE_FALLBACK",
                            "selected_market_label": "آبشده امروزی",
                            "selected_trade_form": "PAPER",
                        },
                        "usd": {
                            "status": "OBSERVED",
                            "average_price": 186_733.3,
                            "point_price": 186_750,
                            "latest_event_utc": "2026-08-15T08:20:16Z",
                            "selection": "ALL_EVENTS",
                            "price_source": "USD_HERAT",
                            "selected_settlement_term": "TOMORROW",
                            "selected_trade_form": "PAPER",
                        },
                        "usdt": {
                            "status": "OBSERVED",
                            "average_price": 187_116.4,
                            "point_price": 187_103,
                            "latest_event_utc": "2026-08-15T08:21:04Z",
                            "selection": "WALLEX_USDT_IRT",
                        },
                        "xauusd": {
                            "status": "ESTIMATED",
                            "average_price": 4_377.15,
                            "point_price": 4_376.91,
                            "latest_event_utc": "2026-08-15T08:20:57Z",
                            "selection": "BINANCE_PAXG_STABLECOIN_CORROBORATED_PROXY",
                            "is_proxy": True,
                        },
                        "generic_coin": {"status": "NO_DATA", "selection": "NO_DATA"},
                        "order_flow": {"status": "OBSERVED", "estimator_score": -0.08},
                        "market_regime": {
                            "status": "OBSERVED",
                            "regime": "DIRECTIONAL",
                            "direction_score": -0.2,
                            "confidence": 0.7,
                        },
                    },
                    "rates": [
                        {
                            "commodity_name": "امام",
                            "method": "CURRENT_CASH_ESTIMATE_X_ROBUST_EMPIRICAL_TOMORROW_CASH_RATIO",
                            "group_offer_anchor": {"status": "NO_DATA"},
                        }
                    ],
                },
            },
        }
        with patch(
            "live_server.read_melted_minute_averages",
            side_effect=AssertionError("dashboard must render the model snapshot directly"),
        ):
            body = render_page(
                state,
                market_db=Path("/not/read/by/dashboard.sqlite3"),
            ).decode("utf-8")

        self.assertIn("دفتر دقیق ورودی‌های تخمین", body)
        self.assertIn("دفتر ورودی نقدی", body)
        self.assertIn("دفتر ورودی فردایی", body)
        self.assertIn("مقدار واقعاً مصرف‌شده", body)
        self.assertIn("آبشده نقدی", body)
        self.assertIn("آبشده امروزی", body)
        self.assertIn("PRIMARY", body)
        self.assertIn("SAME_MINUTE_PAPER_REFERENCE_FALLBACK", body)
        self.assertIn("پراکسی تأییدشدهٔ PAXG", body)
        self.assertIn("سن لنگر", body)
        self.assertIn("اثر واقعی گروه‌های سکه در هر نرخ", body)
        self.assertIn("LATEST_QUALITY_OFFER_RECENCY_VALIDATED", body)
        self.assertIn("وزن ۸۴.۴٪", body)
        self.assertIn("از نرخ نقدی مشتق شده", body)
        self.assertIn("نرخ عمومی سکه؛ مستقل از گروه‌های معاملاتی", body)
        self.assertIn("داده موجود؛ خارج از قرارداد", body)
        self.assertIn("تسویهٔ صریح ندارد و وارد مدل نشده", body)

    def test_analytics_query_and_render(self) -> None:
        from live_server import parse_shamsi_to_utc_iso, query_user_analytics, render_analytics_page
        db_path = Path(tempfile.gettempdir()) / "test_analytics_db.sqlite3"
        iso_start = parse_shamsi_to_utc_iso("1405/05/10", is_end=False)
        self.assertIsNotNone(iso_start)
        res = query_user_analytics(db_path, range_type="today")
        self.assertIn("groups", res)
        self.assertIn("summary", res["groups"].get(1, {}))
        body = render_analytics_page(db_path, range_type="today").decode("utf-8")
        self.assertIn("آمار و تحلیل", body)
        self.assertIn("تعداد کل آفرها", body)
        self.assertIn("تعداد کل معاملات", body)
        self.assertIn("گروه ۱", body)
        self.assertIn("گروه ۲", body)

    def test_session_store_and_authentication(self) -> None:
        from live_server import SessionStore, render_login_page
        db_path = Path(tempfile.gettempdir()) / "test_sessions_db.sqlite3"
        store = SessionStore(db_path)
        token = store.create_session("bahar")
        self.assertIsNotNone(token)
        self.assertEqual(store.validate_session(token), "bahar")
        store.revoke_session(token)
        self.assertIsNone(store.validate_session(token))
        login_body = render_login_page("/login", error="خطا").decode("utf-8")
        self.assertIn("ورود به سامانه", login_body)
        self.assertIn("خطا", login_body)

    def test_query_user_details(self) -> None:
        from live_server import query_user_details, render_user_details_pdf_page
        db_path = Path(tempfile.gettempdir()) / "test_user_details_db.sqlite3"
        res = query_user_details(db_path, "TestUser", 1, "offer", range_type="today")
        self.assertEqual(res["username"], "TestUser")
        self.assertEqual(res["group"], 1)
        self.assertEqual(res["total_items"], 0)
        self.assertIn("items", res)
        pdf_body = render_user_details_pdf_page(db_path, "TestUser", 1, "offer", range_type="today").decode("utf-8")
        self.assertIn("گزارش آمار و فعالیت کاربر", pdf_body)
        self.assertIn("TestUser", pdf_body)

    def test_low_date_family_band_does_not_overlap_sibling(self) -> None:
        self.assertEqual(low_date_family_sibling_name("بهار"), "امام")
        self.assertEqual(low_date_family_sibling_name("نیم تاریخ پایین"), "نیم بهار")
        self.assertEqual(low_date_family_sibling_name("امام"), None)
        rates = [
            {
                "commodity_name": "امام",
                "status": "ESTIMATED",
                "estimated_price_toman": 184_100_000,
                "tolerance": {
                    "lower_price_toman": 182_800_000,
                    "upper_price_toman": 185_400_000,
                    "lower_project_price": 182_800,
                    "upper_project_price": 185_400,
                },
            },
            {
                "commodity_name": "بهار",
                "status": "ESTIMATED",
                "estimated_price_toman": 181_850_000,
                "tolerance": {
                    "lower_price_toman": 180_550_000,
                    "upper_price_toman": 183_150_000,
                    "lower_project_price": 180_550,
                    "upper_project_price": 183_150,
                },
            },
        ]
        apply_low_date_family_band_separation(rates)
        bahar = rates[1]["tolerance"]
        self.assertLess(
            bahar["upper_price_toman"],
            rates[0]["tolerance"]["lower_price_toman"],
        )
        self.assertEqual(
            bahar["family_band_cap"]["policy"],
            "LOW_DATE_SAME_COEFFICIENT_NO_OVERLAP",
        )
        self.assertGreaterEqual(
            bahar["upper_price_toman"], rates[1]["estimated_price_toman"]
        )

    def test_cash_tomorrow_term_structure_repairs_inversion(self) -> None:
        settlements = {
            "CASH": {
                "rates": [
                    {
                        "commodity_name": "امام",
                        "status": "ESTIMATED",
                        "estimated_price_toman": 185_300_000,
                        "estimated_project_price": 185_300,
                    }
                ]
            },
            "TOMORROW": {
                "rates": [
                    {
                        "commodity_name": "امام",
                        "status": "ESTIMATED",
                        "estimated_price_toman": 184_900_000,
                        "estimated_project_price": 184_900,
                        "method": "CURRENT_CASH_ESTIMATE_X_ROBUST_EMPIRICAL_TOMORROW_CASH_RATIO",
                        "online_residual_calibration": {
                            "status": "APPLIED",
                            "correction_ratio": -0.0074,
                        },
                        "settlement_ratio_anchor": {"ratio": 1.0054},
                    }
                ]
            },
        }
        audits = enforce_cash_tomorrow_term_structure(settlements)
        tom = settlements["TOMORROW"]["rates"][0]
        self.assertEqual(len(audits), 1)
        self.assertGreaterEqual(
            tom["estimated_price_toman"],
            settlements["CASH"]["rates"][0]["estimated_price_toman"],
        )
        self.assertIn(
            tom["term_structure_floor"]["policy"],
            {
                "TOMORROW_NOT_BELOW_CASH",
                "TOMORROW_NOT_BELOW_REOPEN_RATIO_FLOOR",
            },
        )
        self.assertGreaterEqual(
            tom["estimated_project_price"],
            settlements["CASH"]["rates"][0]["estimated_project_price"],
        )

    def test_observed_tomorrow_book_never_gets_lifted_by_inferred_cash(self) -> None:
        settlements = {
            "CASH": {
                "rates": [
                    {
                        "commodity_name": "امام",
                        "status": "ESTIMATED",
                        "estimated_price_toman": 187_000_000,
                        "estimated_project_price": 187_000,
                        "group_offer_anchor": {"status": "NO_DATA"},
                        "tolerance": {
                            "lower_price_toman": 185_900_000,
                            "upper_price_toman": 188_500_000,
                        },
                    }
                ]
            },
            "TOMORROW": {
                "rates": [
                    {
                        "commodity_name": "امام",
                        "status": "ESTIMATED",
                        "estimated_price_toman": 185_500_000,
                        "estimated_project_price": 185_500,
                        "group_offer_anchor": {
                            "status": "OBSERVED",
                            "reference_price_toman": 185_500_000,
                        },
                    }
                ]
            },
        }
        audits = enforce_cash_tomorrow_term_structure(settlements)
        cash = settlements["CASH"]["rates"][0]
        tomorrow = settlements["TOMORROW"]["rates"][0]
        self.assertEqual(len(audits), 1)
        self.assertEqual(cash["estimated_price_toman"], 185_500_000)
        self.assertEqual(tomorrow["estimated_price_toman"], 185_500_000)
        self.assertEqual(
            cash["term_structure_cap"]["policy"],
            "CASH_NOT_ABOVE_OBSERVED_TOMORROW_BOOK",
        )


if __name__ == "__main__":
    unittest.main()
