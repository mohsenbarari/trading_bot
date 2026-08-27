from __future__ import annotations

from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

import pytest

from core.market_intelligence.coin_group_resolution import (
    CoinPriceAnchor,
    resolve_coin_group_offers,
)
from core.market_intelligence.coin_groups import CoinGroupMessageInput
from core.market_intelligence.coin_prediction_anchors import (
    CoinPredictionAnchorError,
    _project_price,
    load_coin_prediction_anchors,
)


def _ledger(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE coin_estimate_predictions(
            id INTEGER PRIMARY KEY,
            prediction_time_utc TEXT NOT NULL,
            created_at_utc TEXT NOT NULL,
            model_id TEXT NOT NULL,
            commodity TEXT NOT NULL,
            settlement TEXT NOT NULL,
            estimated_price_toman REAL NOT NULL
        )
        """
    )
    return connection


def test_prediction_ledger_is_unit_safe_causal_and_downsampled() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "predictions.sqlite3"
        connection = _ledger(path)
        connection.executemany(
            "INSERT INTO coin_estimate_predictions VALUES(?,?,?,?,?,?,?)",
            (
                (1, "2026-08-16T10:00:00Z", "2026-08-16T10:00:05Z", "MAIN_ONLINE", "امام", "TOMORROW", 188_500_000),
                (2, "2026-08-16T10:10:00Z", "2026-08-16T10:10:05Z", "MAIN_ONLINE", "امام", "TOMORROW", 188_600_000),
                (3, "2026-08-16T10:16:00Z", "2026-08-16T10:16:05Z", "MAIN_ONLINE", "بهار", "TOMORROW", 185_000_000),
                (4, "2026-08-16T10:17:00Z", "2026-08-16T10:17:05Z", "MAIN_ONLINE", "بهار", "TOMORROW", 185_000_500),
                (5, "2026-08-16T10:18:00Z", "2026-08-16T10:18:05Z", "SHADOW1_PREVIOUS", "امام", "TOMORROW", 200_000_000),
                (6, "2026-08-16T10:19:00Z", "2026-08-16T10:19:05Z", "MAIN_ONLINE", "ناشناخته", "TOMORROW", 188_600_000),
                (7, "2026-08-16T10:31:00Z", "2026-08-16T10:31:05Z", "MAIN_ONLINE", "امام", "TOMORROW", 188_700_000),
            ),
        )
        connection.commit()
        connection.close()

        loaded = load_coin_prediction_anchors(
            path,
            earliest_event_time_utc="2026-08-16T10:20:00Z",
            as_of_utc="2026-08-16T10:30:00Z",
        )

        assert loaded.rows_seen == 5
        assert loaded.rows_rejected == 2
        assert [
            (
                anchor.commodity_code,
                anchor.price_project_thousand_toman,
                anchor.event_time_utc,
                anchor.available_at_utc,
                anchor.evidence_kind,
            )
            for anchor in loaded.anchors
        ] == [
            (
                "IMAM",
                188_500,
                "2026-08-16T10:00:00Z",
                "2026-08-16T10:00:05Z",
                "MODEL_SNAPSHOT",
            ),
            (
                "IMAM",
                188_600,
                "2026-08-16T10:10:00Z",
                "2026-08-16T10:10:05Z",
                "MODEL_SNAPSHOT",
            ),
            (
                "BAHAR",
                185_000,
                "2026-08-16T10:16:00Z",
                "2026-08-16T10:16:05Z",
                "MODEL_SNAPSHOT",
            ),
        ]


def test_prediction_ledger_rejects_missing_schema() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "predictions.sqlite3"
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE unrelated(id INTEGER)")
        connection.close()

        with pytest.raises(
            CoinPredictionAnchorError,
            match="coin_prediction_ledger_schema_invalid",
        ):
            load_coin_prediction_anchors(
                path,
                earliest_event_time_utc="2026-08-16T10:20:00Z",
                as_of_utc="2026-08-16T10:30:00Z",
            )


def test_prediction_ledger_reads_only_the_selected_authority_epoch() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "predictions.sqlite3"
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE coin_estimate_predictions(
              id INTEGER PRIMARY KEY,
              prediction_time_utc TEXT NOT NULL,
              created_at_utc TEXT NOT NULL,
              model_id TEXT NOT NULL,
              commodity TEXT NOT NULL,
              settlement TEXT NOT NULL,
              estimated_price_toman INTEGER NOT NULL,
              authority_epoch TEXT NOT NULL
            );
            CREATE TABLE coin_estimate_prediction_authority(
              singleton INTEGER PRIMARY KEY,
              active_epoch TEXT NOT NULL,
              active_feed_mode TEXT NOT NULL,
              updated_at_utc TEXT NOT NULL
            );
            INSERT INTO coin_estimate_prediction_authority
            VALUES(1,'PRIVATE_PRIMARY:model-v2','PRIVATE_PRIMARY',
                   '2026-08-16T10:09:01Z');
            """
        )
        connection.executemany(
            "INSERT INTO coin_estimate_predictions VALUES(?,?,?,?,?,?,?,?)",
            (
                (
                    1,
                    "2026-08-16T10:08:00Z",
                    "2026-08-16T10:08:01Z",
                    "MAIN_ONLINE",
                    "امام",
                    "TOMORROW",
                    180_000_000,
                    "LEGACY_BASELINE",
                ),
                (
                    2,
                    "2026-08-16T10:09:00Z",
                    "2026-08-16T10:09:01Z",
                    "MAIN_ONLINE",
                    "امام",
                    "TOMORROW",
                    188_000_000,
                    "PRIVATE_PRIMARY:model-v2",
                ),
            ),
        )
        connection.commit()
        connection.close()

        loaded = load_coin_prediction_anchors(
            path,
            earliest_event_time_utc="2026-08-16T10:10:00Z",
            as_of_utc="2026-08-16T10:11:00Z",
        )

        assert loaded.rows_seen == 1
        assert [item.price_project_thousand_toman for item in loaded.anchors] == [
            188_000
        ]


def test_prediction_family_envelopes_admit_current_ranges_but_reject_swaps() -> None:
    assert _project_price(112_000_000, commodity_code="HALF_BAHAR") == 112_000
    assert _project_price(60_000_000, commodity_code="QUARTER_BAHAR") == 60_000
    assert _project_price(55_000_000, commodity_code="QUARTER_LOW_DATE") == 55_000
    assert _project_price(55_000_000, commodity_code="IMAM") is None
    assert _project_price(222_000_000, commodity_code="QUARTER_BAHAR") is None


def test_intrabracket_price_transition_is_retained_for_historical_causality() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "predictions.sqlite3"
        connection = _ledger(path)
        connection.executemany(
            "INSERT INTO coin_estimate_predictions VALUES(?,?,?,?,?,?,?)",
            (
                (1, "2026-08-16T10:00:00Z", "2026-08-16T10:00:01Z", "MAIN_ONLINE", "امام", "TOMORROW", 188_000_000),
                (2, "2026-08-16T10:05:00Z", "2026-08-16T10:05:01Z", "MAIN_ONLINE", "امام", "TOMORROW", 190_000_000),
                (3, "2026-08-16T10:10:00Z", "2026-08-16T10:10:01Z", "MAIN_ONLINE", "امام", "TOMORROW", 191_000_000),
            ),
        )
        connection.commit()
        connection.close()

        loaded = load_coin_prediction_anchors(
            path,
            earliest_event_time_utc="2026-08-16T10:08:00Z",
            as_of_utc="2026-08-16T10:12:00Z",
        )
        assert [item.price_project_thousand_toman for item in loaded.anchors] == [
            188_000,
            190_000,
            191_000,
        ]


def _source(text: str) -> CoinGroupMessageInput:
    return CoinGroupMessageInput(
        group_number=1,
        source_event_id=10,
        published_at_utc="2026-08-16T10:10:00Z",
        available_at_utc="2026-08-16T10:10:02Z",
        text=text,
    )


def _model_anchor(code: str, price: int, at: str) -> CoinPriceAnchor:
    return CoinPriceAnchor(
        commodity_code=code,
        price_project_thousand_toman=price,
        event_time_utc=at,
        available_at_utc=at,
        settlement_term="TOMORROW",
        trade_form="PHYSICAL",
        evidence_kind="MODEL_SNAPSHOT",
    )


def test_unnamed_offer_is_resolved_by_distinct_prior_model_ranges() -> None:
    result = resolve_coin_group_offers(
        _source("5 تا 188/600 ف"),
        anchors=(
            _model_anchor("IMAM", 188_400, "2026-08-16T10:07:00Z"),
            _model_anchor("IMAM", 188_500, "2026-08-16T10:08:00Z"),
            _model_anchor("BAHAR", 185_000, "2026-08-16T10:07:30Z"),
            _model_anchor("BAHAR", 185_100, "2026-08-16T10:08:30Z"),
        ),
    )[0]

    assert (result.commodity_code, result.quality_state) == ("IMAM", "ELIGIBLE")
    assert result.authoritative_anchor_count == 0
    assert "MODEL_PRICE_RANGE" in result.resolution_reason


def test_overlapping_model_ranges_still_require_review() -> None:
    result = resolve_coin_group_offers(
        _source("5 تا 94/000 ف"),
        anchors=(
            _model_anchor("HALF_BAHAR", 95_000, "2026-08-16T10:07:00Z"),
            _model_anchor("HALF_BAHAR", 95_100, "2026-08-16T10:08:00Z"),
            _model_anchor("HALF_LOW_DATE", 93_000, "2026-08-16T10:07:30Z"),
            _model_anchor("HALF_LOW_DATE", 93_100, "2026-08-16T10:08:30Z"),
        ),
    )[0]

    assert result.commodity_code is None
    assert result.quality_state == "PENDING_REVIEW"


def test_model_range_cannot_reject_an_explicit_commodity() -> None:
    result = resolve_coin_group_offers(
        _source("بهار 5 تا 188/600 ف"),
        anchors=(
            _model_anchor("IMAM", 188_400, "2026-08-16T10:07:00Z"),
            _model_anchor("IMAM", 188_500, "2026-08-16T10:08:00Z"),
            _model_anchor("BAHAR", 185_000, "2026-08-16T10:07:30Z"),
            _model_anchor("BAHAR", 185_100, "2026-08-16T10:08:30Z"),
        ),
    )[0]

    assert (result.commodity_code, result.quality_state) == ("BAHAR", "ELIGIBLE")
    assert "NONAUTHORITATIVE" in result.resolution_reason
