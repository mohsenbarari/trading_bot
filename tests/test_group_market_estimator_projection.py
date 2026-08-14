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


def test_projection_uses_opaque_ids_and_removes_later_ineligible_fact() -> None:
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
                attributes={"group_number": 1},
            ),
        )
        market.commit()
        market.close()

        assert project(market_path, conversation_path)["eligible_offers"] == 1
        connection = sqlite3.connect(conversation_path)
        row = connection.execute(
            "SELECT m.message_id,m.text,o.source_text,o.price FROM offers o JOIN messages m ON m.import_id=o.import_id AND m.message_id=o.message_id"
        ).fetchone()
        assert row is not None
        assert row[0] < 0
        assert row[1] == ""
        assert row[2].startswith("canonical:")
        assert row[3] == 186900
        connection.close()

        market = sqlite3.connect(market_path)
        market.execute(
            "UPDATE market_observations SET quality_state='REJECTED' WHERE event_key=?",
            (key,),
        )
        market.commit()
        market.close()
        assert project(market_path, conversation_path)["ineligible_removed"] == 1
        connection = sqlite3.connect(conversation_path)
        assert connection.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 0
        connection.close()


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
