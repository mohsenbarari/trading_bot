from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from core.market_intelligence.market_contracts import MarketObservation, derive_event_key
from core.market_intelligence.market_store import (
    connect_market_store,
    initialize_market_store,
    upsert_observation,
)
from scripts.project_group_market_to_estimator import main, project


_CONVERSATION_SCHEMA = """
CREATE TABLE imports(id INTEGER PRIMARY KEY,archive_path TEXT NOT NULL,archive_sha256 TEXT NOT NULL UNIQUE,imported_at_utc TEXT NOT NULL,cutoff_utc TEXT NOT NULL,message_count INTEGER NOT NULL,retained_message_count INTEGER NOT NULL,dropped_message_count INTEGER NOT NULL,extractor_version TEXT NOT NULL);
CREATE TABLE messages(import_id INTEGER NOT NULL,message_id INTEGER NOT NULL,event_time_utc TEXT NOT NULL,event_time_tehran TEXT NOT NULL,sender_hash TEXT,text TEXT NOT NULL,reply_to_message_id INTEGER,source_html_file TEXT NOT NULL,roles_json TEXT NOT NULL,relevance_json TEXT NOT NULL,PRIMARY KEY(import_id,message_id));
CREATE TABLE offers(id INTEGER PRIMARY KEY,import_id INTEGER NOT NULL,message_id INTEGER NOT NULL,offer_index INTEGER NOT NULL,commodity TEXT NOT NULL,price INTEGER NOT NULL,quantity INTEGER,side TEXT NOT NULL,settlement TEXT NOT NULL,trade_form TEXT NOT NULL,confidence REAL NOT NULL,source_text TEXT NOT NULL,price_raw TEXT,price_method TEXT,commodity_method TEXT,quantity_method TEXT,UNIQUE(import_id,message_id,offer_index));
CREATE TABLE confirmed_trades(id INTEGER PRIMARY KEY,import_id INTEGER NOT NULL,confirmation_message_id INTEGER NOT NULL,offer_message_id INTEGER,request_message_id INTEGER,event_time_utc TEXT NOT NULL,commodity TEXT NOT NULL,price INTEGER NOT NULL,price_raw TEXT,price_method TEXT,quantity INTEGER,quantity_method TEXT,reported_quantity INTEGER,is_aggregate INTEGER NOT NULL DEFAULT 0,training_eligible INTEGER NOT NULL DEFAULT 1,side TEXT NOT NULL,settlement TEXT NOT NULL,trade_form TEXT NOT NULL,confidence REAL NOT NULL,confirmation_type TEXT NOT NULL,evidence_json TEXT NOT NULL,context_json TEXT NOT NULL,UNIQUE(import_id,confirmation_message_id,request_message_id));
CREATE TABLE offer_market_quality(offer_id INTEGER PRIMARY KEY,event_time_utc TEXT NOT NULL,lifecycle_phase TEXT NOT NULL,live_range_weight REAL NOT NULL,live_flow_weight REAL NOT NULL,historical_training_weight REAL NOT NULL,realtime_eligible INTEGER NOT NULL,training_eligible INTEGER NOT NULL,cross_state TEXT NOT NULL,crossing_reference_price INTEGER,market_regime TEXT NOT NULL,regime_score REAL,regime_confidence REAL NOT NULL,regime_volatility_percent REAL,exclusion_reason TEXT);
CREATE TABLE trade_market_quality(trade_id INTEGER PRIMARY KEY,linked_offer_id INTEGER,training_eligible INTEGER NOT NULL,realtime_eligible INTEGER NOT NULL,training_weight REAL NOT NULL,market_regime TEXT NOT NULL,regime_score REAL,regime_confidence REAL NOT NULL,cross_state TEXT NOT NULL,exclusion_reason TEXT);
"""


def test_projection_uses_opaque_ids_and_keeps_conditional_fact_audit_only() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        market_path = root / "market.sqlite3"
        conversation_path = root / "conversation.sqlite3"
        destination = sqlite3.connect(conversation_path)
        destination.executescript(_CONVERSATION_SCHEMA)
        destination.close()
        market = connect_market_store(market_path)
        initialize_market_store(market)
        key = derive_event_key("test-current-group", 1)
        upsert_observation(
            market,
            MarketObservation(
                event_key=key,
                source_code="GROUP_1",
                source_family="GROUP",
                event_time_utc=datetime(2026, 8, 13, 9, tzinfo=timezone.utc),
                available_at_utc=datetime(2026, 8, 13, 9, 0, 3, tzinfo=timezone.utc),
                instrument="COIN_IMAM",
                market_label="GROUP_COIN_IMAM",
                settlement_term="CASH",
                trade_form="PHYSICAL",
                event_type="OFFER",
                side="BUY",
                price=Decimal("186900"),
                price_unit="PROJECT_THOUSAND_TOMAN",
                currency="TOMAN",
                quantity=Decimal("5"),
                quantity_unit="COIN_COUNT",
                parse_confidence=0.99,
                parser_version="coin-group-context-v1",
                quality_state="ELIGIBLE",
                quality_policy_version="coin-group-resolution-v1",
                is_conditional=False,
                attributes={
                    "group_number": 1,
                    "resolution_reason": "HUMAN_REVIEWED_FIELD_CORRECTION",
                    "human_feedback_revision": 3,
                },
            ),
        )
        market.commit()
        market.close()

        report = project(market_path, conversation_path)
        assert report["eligible_offers"] == 1
        assert report["group_1_latest_canonical_event_utc"] == "2026-08-13T09:00:00Z"
        assert report["group_1_latest_eligible_event_utc"] == "2026-08-13T09:00:00Z"
        assert report["group_2_latest_canonical_event_utc"] is None
        connection = sqlite3.connect(conversation_path)
        row = connection.execute(
            "SELECT m.message_id,m.text,m.event_time_utc,m.relevance_json,o.source_text,o.price FROM offers o JOIN messages m ON m.import_id=o.import_id AND m.message_id=o.message_id"
        ).fetchone()
        assert row is not None
        assert row[0] < 0
        assert row[1] == ""
        assert row[2] == "2026-08-13T09:00:03Z"
        relevance = json.loads(row[3])
        assert relevance["source_event_time_utc"] == "2026-08-13T09:00:00Z"
        assert relevance["canonical_settlement_term"] == "CASH"
        assert relevance["canonical_trade_form"] == "PHYSICAL"
        assert relevance["canonical_quality_state"] == "ELIGIBLE"
        assert relevance["canonical_is_conditional"] is False
        assert (
            relevance["canonical_resolution_reason"]
            == "HUMAN_REVIEWED_FIELD_CORRECTION"
        )
        assert relevance["human_feedback_revision"] == 3
        assert relevance["parser_version"] == "coin-group-context-v1"
        assert row[4].startswith("canonical:")
        assert row[5] == 186900
        connection.close()

        market = sqlite3.connect(market_path)
        market.execute(
            "UPDATE market_observations SET is_conditional=1 WHERE event_key=?",
            (key,),
        )
        market.commit()
        market.close()
        report = project(market_path, conversation_path)
        assert (report["eligible_offers"], report["audit_only_offers"]) == (0, 1)
        connection = sqlite3.connect(conversation_path)
        assert connection.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 1
        quality = connection.execute(
            "SELECT realtime_eligible,training_eligible,exclusion_reason FROM offer_market_quality"
        ).fetchone()
        assert quality == (0, 0, "CONDITIONAL_GROUP_FACT")
        connection.close()


def test_projection_reconciles_missing_source_and_excludes_late_arrival() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        market_path = root / "market.sqlite3"
        conversation_path = root / "conversation.sqlite3"
        destination = sqlite3.connect(conversation_path)
        destination.executescript(_CONVERSATION_SCHEMA)
        destination.close()
        market = connect_market_store(market_path)
        initialize_market_store(market)
        current_key = derive_event_key("projection-current", 1)
        late_key = derive_event_key("projection-late", 2)
        for key, minute, available_minute in (
            (current_key, 0, 1),
            (late_key, 2, 12),
        ):
            upsert_observation(
                market,
                MarketObservation(
                    event_key=key,
                    source_code="GROUP_2",
                    source_family="GROUP",
                    event_time_utc=datetime(2026, 8, 13, 9, minute, tzinfo=timezone.utc),
                    available_at_utc=datetime(2026, 8, 13, 9, available_minute, tzinfo=timezone.utc),
                    instrument="COIN_IMAM",
                    market_label="GROUP_COIN_IMAM",
                    settlement_term="CASH",
                    trade_form="PHYSICAL",
                    event_type="OFFER",
                    side="SELL",
                    price=Decimal("186900"),
                    price_unit="PROJECT_THOUSAND_TOMAN",
                    currency="TOMAN",
                    quantity=Decimal("5"),
                    quantity_unit="COIN_COUNT",
                    parser_version="coin-group-context-v2",
                ),
            )
        market.commit()
        market.close()

        first = project(market_path, conversation_path)
        assert (first["eligible_offers"], first["audit_offers"]) == (1, 2)
        destination = sqlite3.connect(conversation_path)
        assert destination.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 2
        late_quality = destination.execute(
            """
            SELECT realtime_eligible,training_eligible,exclusion_reason
            FROM offer_market_quality
            WHERE exclusion_reason IS NOT NULL
            """
        ).fetchone()
        assert late_quality == (0, 0, "CANONICAL_FACT_ARRIVED_TOO_LATE")
        destination.close()

        market = sqlite3.connect(market_path)
        market.execute("DELETE FROM market_observations WHERE event_key=?", (current_key,))
        market.commit()
        market.close()
        second = project(market_path, conversation_path)
        assert second["ineligible_removed"] == 1
        destination = sqlite3.connect(conversation_path)
        assert destination.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 1
        destination.close()


def test_pending_unresolved_offer_is_visible_but_never_model_eligible() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        market_path = root / "market.sqlite3"
        conversation_path = root / "conversation.sqlite3"
        destination = sqlite3.connect(conversation_path)
        destination.executescript(_CONVERSATION_SCHEMA)
        destination.close()
        market = connect_market_store(market_path)
        initialize_market_store(market)
        upsert_observation(
            market,
            MarketObservation(
                event_key=derive_event_key("pending-audit", 1),
                source_code="GROUP_1",
                source_family="GROUP",
                event_time_utc="2026-08-15T10:56:50Z",
                available_at_utc="2026-08-15T10:56:55Z",
                instrument="COIN_UNRESOLVED",
                market_label="GROUP_COIN_UNRESOLVED",
                settlement_term="TOMORROW",
                trade_form="PHYSICAL",
                event_type="OFFER",
                side="SELL",
                price=Decimal("188600"),
                price_unit="PROJECT_THOUSAND_TOMAN",
                currency="TOMAN",
                quantity=Decimal("5"),
                quantity_unit="COIN_COUNT",
                parse_confidence=0.0,
                parser_version="coin-group-test",
                quality_state="PENDING_REVIEW",
                quality_policy_version="test",
            ),
        )
        market.commit()
        market.close()

        report = project(market_path, conversation_path)
        assert (report["eligible_offers"], report["audit_only_offers"]) == (0, 1)
        connection = sqlite3.connect(conversation_path)
        row = connection.execute(
            """
            SELECT o.commodity,q.realtime_eligible,q.training_eligible,q.exclusion_reason
            FROM offers o JOIN offer_market_quality q ON q.offer_id=o.id
            """
        ).fetchone()
        connection.close()
        assert row == ("نامشخص", 0, 0, "CANONICAL_QUALITY_NOT_ELIGIBLE")


def test_projection_links_canonical_trade_to_its_opaque_root_offer() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        market_path = root / "market.sqlite3"
        conversation_path = root / "conversation.sqlite3"
        destination = sqlite3.connect(conversation_path)
        destination.executescript(_CONVERSATION_SCHEMA)
        destination.close()
        market = connect_market_store(market_path)
        initialize_market_store(market)
        offer_key = derive_event_key("coin-group-offer-v1", 1, 101, 0)
        trade_key = derive_event_key("coin-group-trade-v1", 1, 101, 104)
        for key, event_type, minute, price, quantity, attributes in (
            (
                offer_key,
                "OFFER",
                0,
                "188750",
                "10",
                {"group_number": 1},
            ),
            (
                trade_key,
                "TRADE",
                1,
                "188500",
                "5",
                {
                    "group_number": 1,
                    "confirmation_kind": "RECIPROCAL_OFFERER_CONFIRMATION",
                    "is_aggregate": False,
                    "root_offer_event_key": offer_key.hex(),
                },
            ),
        ):
            upsert_observation(
                market,
                MarketObservation(
                    event_key=key,
                    source_code="GROUP_1",
                    source_family="GROUP",
                    event_time_utc=datetime(2026, 8, 13, 9, minute, tzinfo=timezone.utc),
                    available_at_utc=datetime(
                        2026, 8, 13, 9, minute, 3, tzinfo=timezone.utc
                    ),
                    instrument="COIN_IMAM",
                    market_label="GROUP_COIN_IMAM",
                    settlement_term="CASH",
                    trade_form="PHYSICAL",
                    event_type=event_type,
                    side="SELL",
                    price=Decimal(price),
                    price_unit="PROJECT_THOUSAND_TOMAN",
                    currency="TOMAN",
                    quantity=Decimal(quantity),
                    quantity_unit="COIN_COUNT",
                    parse_confidence=0.99,
                    parser_version="coin-group-trade-projection-test",
                    quality_state="ELIGIBLE",
                    quality_policy_version="test",
                    attributes=attributes,
                ),
            )
        market.commit()
        market.close()

        report = project(market_path, conversation_path)
        assert (report["eligible_offers"], report["eligible_trades"]) == (1, 1)
        connection = sqlite3.connect(conversation_path)
        offer = connection.execute("SELECT id,message_id FROM offers").fetchone()
        trade = connection.execute(
            "SELECT offer_message_id,price,quantity,confirmation_type FROM confirmed_trades"
        ).fetchone()
        quality = connection.execute(
            "SELECT linked_offer_id FROM trade_market_quality"
        ).fetchone()
        connection.close()

        assert offer is not None and trade is not None and quality is not None
        assert trade[0] == offer[1]
        assert (trade[1], trade[2]) == (188500, 5)
        assert trade[3] == "RECIPROCAL_OFFERER_CONFIRMATION"
        assert quality[0] == offer[0]


def test_projection_command_records_failure_heartbeat() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        conversation_path = root / "conversation.sqlite3"
        conversation_path.touch()
        health_path = root / "group-health.json"

        result = main(
            [
                "--market-store",
                str(root / "missing-market.sqlite3"),
                "--conversation-db",
                str(conversation_path),
                "--health-state",
                str(health_path),
            ]
        )
        health = json.loads(health_path.read_text(encoding="utf-8"))

    assert result == 2
    source = health["sources"]["COIN_GROUP_PROJECTION"]
    assert source["status"] == "FAILED"
    assert source["error_code"] == "GROUP_PROJECTION_PROJECTIONERROR"
