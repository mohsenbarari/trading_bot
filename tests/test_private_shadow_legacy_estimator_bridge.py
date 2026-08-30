from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from core.market_intelligence.coin_rate_engine import build_coin_rate_estimates
from core.market_intelligence.market_contracts import MarketObservation, derive_event_key
from core.market_intelligence.market_store import (
    connect_market_store,
    initialize_market_store,
    upsert_observation,
)
from core.market_intelligence.shadow_legacy_bridge import (
    AUTHORIZED_CUTOFF_UTC,
    BRIDGE_VERSION,
    GROUP_SOURCES,
    MARKET_BRIDGE_SOURCES,
    PRIVATE_SOURCES,
    BridgeError,
    deactivate_projected_rows,
    parser_version_allowed,
    project_shadow_to_legacy_market,
    sqlite_quick_check,
    unrelated_row_count,
    verify_source_read_only,
)
from scripts.project_group_market_to_estimator import project as project_groups
from scripts.project_private_shadow_to_legacy_market import main as private_main
from scripts.rollback_private_shadow_legacy_estimator_bridge import (
    main as rollback_main,
)
from scripts.run_private_shadow_legacy_estimator_bridge import main as orchestrator_main


_CONVERSATION_SCHEMA = """
CREATE TABLE imports(id INTEGER PRIMARY KEY,archive_path TEXT NOT NULL,archive_sha256 TEXT NOT NULL UNIQUE,imported_at_utc TEXT NOT NULL,cutoff_utc TEXT NOT NULL,message_count INTEGER NOT NULL,retained_message_count INTEGER NOT NULL,dropped_message_count INTEGER NOT NULL,extractor_version TEXT NOT NULL);
CREATE TABLE messages(import_id INTEGER NOT NULL,message_id INTEGER NOT NULL,event_time_utc TEXT NOT NULL,event_time_tehran TEXT NOT NULL,sender_hash TEXT,text TEXT NOT NULL,reply_to_message_id INTEGER,source_html_file TEXT NOT NULL,roles_json TEXT NOT NULL,relevance_json TEXT NOT NULL,PRIMARY KEY(import_id,message_id));
CREATE TABLE offers(id INTEGER PRIMARY KEY,import_id INTEGER NOT NULL,message_id INTEGER NOT NULL,offer_index INTEGER NOT NULL,commodity TEXT NOT NULL,price INTEGER NOT NULL,quantity INTEGER,side TEXT NOT NULL,settlement TEXT NOT NULL,trade_form TEXT NOT NULL,confidence REAL NOT NULL,source_text TEXT NOT NULL,price_raw TEXT,price_method TEXT,commodity_method TEXT,quantity_method TEXT,UNIQUE(import_id,message_id,offer_index));
CREATE TABLE confirmed_trades(id INTEGER PRIMARY KEY,import_id INTEGER NOT NULL,confirmation_message_id INTEGER NOT NULL,offer_message_id INTEGER,request_message_id INTEGER,event_time_utc TEXT NOT NULL,commodity TEXT NOT NULL,price INTEGER NOT NULL,price_raw TEXT,price_method TEXT,quantity INTEGER,quantity_method TEXT,reported_quantity INTEGER,is_aggregate INTEGER NOT NULL DEFAULT 0,training_eligible INTEGER NOT NULL DEFAULT 1,side TEXT NOT NULL,settlement TEXT NOT NULL,trade_form TEXT NOT NULL,confidence REAL NOT NULL,confirmation_type TEXT NOT NULL,evidence_json TEXT NOT NULL,context_json TEXT NOT NULL,UNIQUE(import_id,confirmation_message_id,request_message_id));
CREATE TABLE offer_market_quality(offer_id INTEGER PRIMARY KEY,event_time_utc TEXT NOT NULL,lifecycle_phase TEXT NOT NULL,live_range_weight REAL NOT NULL,live_flow_weight REAL NOT NULL,historical_training_weight REAL NOT NULL,realtime_eligible INTEGER NOT NULL,training_eligible INTEGER NOT NULL,cross_state TEXT NOT NULL,crossing_reference_price INTEGER,market_regime TEXT NOT NULL,regime_score REAL,regime_confidence REAL NOT NULL,regime_volatility_percent REAL,exclusion_reason TEXT);
CREATE TABLE trade_market_quality(trade_id INTEGER PRIMARY KEY,linked_offer_id INTEGER,training_eligible INTEGER NOT NULL,realtime_eligible INTEGER NOT NULL,training_weight REAL NOT NULL,market_regime TEXT NOT NULL,regime_score REAL,regime_confidence REAL NOT NULL,cross_state TEXT NOT NULL,exclusion_reason TEXT);
"""
_REPO = Path(__file__).resolve().parents[1]
_SHA = "a" * 40


def _store(path: Path) -> sqlite3.Connection:
    connection = connect_market_store(path)
    initialize_market_store(connection)
    return connection


def _conversation(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(_CONVERSATION_SCHEMA)
    connection.close()


def _private_offer(
    *,
    key: bytes,
    when: datetime,
    available: datetime | None = None,
    quality: str = "ELIGIBLE",
    parser: str = "private-gold-rules-v2",
    price: str = "45000000",
    settlement: str = "TODAY",
    form: str = "PHYSICAL",
    event_type: str = "OFFER",
    extra: dict | None = None,
) -> MarketObservation:
    attributes = {
        "paper_variant": "NOT_APPLICABLE",
        "conditional_reason": "NONE",
        "root_offer_event_key": key.hex(),
    }
    if extra:
        attributes.update(extra)
    return MarketObservation(
        event_key=key,
        source_code="PRIVATE_GOLD_CHANNEL",
        source_family="TELEGRAM_PRIVATE",
        event_time_utc=when,
        available_at_utc=available or when,
        instrument="MELTED_GOLD_PRIVATE",
        market_label="PRIVATE_GOLD_PHYSICAL",
        settlement_term=settlement,
        trade_form=form,
        event_type=event_type,
        side="SELL",
        price=Decimal(price),
        price_unit="TOMAN_PER_MESGHAL_750",
        currency="TOMAN",
        quantity=Decimal("10"),
        quantity_unit="LOT_COUNT",
        parser_version=parser,
        quality_state=quality,
        quality_policy_version="private-gold-v1",
        attributes=attributes,
    )


def _minute(key: bytes, when: datetime) -> MarketObservation:
    return MarketObservation(
        event_key=key,
        source_code="PRIVATE_GOLD_PAPER_MINUTE",
        source_family="TELEGRAM_PRIVATE",
        event_time_utc=when,
        available_at_utc=when,
        instrument="MELTED_GOLD_PRIVATE",
        market_label="PRIVATE_GOLD_PAPER_NORMAL",
        settlement_term="TOMORROW",
        trade_form="PAPER_NORMAL",
        event_type="QUOTE",
        side="MID",
        price=Decimal("45100000"),
        price_unit="TOMAN_PER_MESGHAL_750",
        currency="TOMAN",
        parser_version="private-gold-minute-v1",
        quality_state="ELIGIBLE",
        quality_policy_version="private-gold-v1",
    )


def _group(
    *,
    key: bytes,
    source: str,
    when: datetime,
    available: datetime | None = None,
    quality: str = "ELIGIBLE",
    instrument: str = "COIN_IMAM",
    event_type: str = "OFFER",
    extra: dict | None = None,
) -> MarketObservation:
    attributes = {"group_number": int(source[-1]), "resolution_reason": "EXPLICIT"}
    if extra:
        attributes.update(extra)
    return MarketObservation(
        event_key=key,
        source_code=source,
        source_family="GROUP",
        event_time_utc=when,
        available_at_utc=available or when,
        instrument=instrument,
        market_label="GROUP_" + instrument,
        settlement_term="CASH",
        trade_form="PHYSICAL",
        event_type=event_type,
        side="BUY",
        price=Decimal("186900"),
        price_unit="PROJECT_THOUSAND_TOMAN",
        currency="TOMAN",
        quantity=Decimal("5"),
        quantity_unit="COIN_COUNT",
        parser_version="coin-group-rules-v10-reviewed-fallbacks",
        quality_state=quality,
        quality_policy_version="coin-group-first-pass-v1",
        attributes=attributes,
    )


def _unrelated(key: bytes, when: datetime) -> MarketObservation:
    return MarketObservation(
        event_key=key,
        source_code="MELTED_FLOW",
        source_family="TELEGRAM_PUBLIC",
        event_time_utc=when,
        available_at_utc=when,
        instrument="MELTED_GOLD_FLOW",
        market_label="MELTED_PAPER_FLOW",
        settlement_term="TOMORROW",
        trade_form="PAPER_NORMAL",
        event_type="QUOTE",
        side="MID",
        price=Decimal("44000000"),
        price_unit="TOMAN_PER_MESGHAL_750",
        currency="TOMAN",
        parser_version="public-telegram-v1",
        quality_state="ELIGIBLE",
    )


class ShadowLegacyBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="shadow-legacy-bridge-")
        self.root = Path(self.temporary.name)
        self.shadow = self.root / "shadow.sqlite"
        self.legacy = self.root / "legacy.sqlite"
        self.ledger = self.root / "ledger.sqlite"
        self.conversation = self.root / "conversation.sqlite3"
        self.heartbeat = self.root / "health.json"
        self.market_lock = self.root / "market.lock"
        self.conversation_lock = self.root / "conversation.lock"
        self.when = datetime(2026, 8, 29, 9, 0, tzinfo=timezone.utc)
        os.environ["PRODUCT_ESTIMATOR_SNAPSHOT_MODE"] = "LEGACY"
        os.environ["APP_ENV_FILE"] = str(_REPO / "config" / "unit-test.env.example")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def seed_shadow(self, observations: list[MarketObservation]) -> None:
        connection = _store(self.shadow)
        for item in observations:
            upsert_observation(connection, item)
        connection.commit()
        connection.close()
        dest = _store(self.legacy)
        dest.commit()
        dest.close()

    def project(self, **kwargs):
        return project_shadow_to_legacy_market(
            source=self.shadow,
            destination=self.legacy,
            ledger=self.ledger,
            sources=kwargs.pop("sources", PRIVATE_SOURCES),
            cutoff_utc=kwargs.pop("cutoff_utc", AUTHORIZED_CUTOFF_UTC),
            **kwargs,
        )

    def dest_count(self, source: str | None = None) -> int:
        connection = sqlite3.connect(self.legacy)
        if source is None:
            value = connection.execute("SELECT COUNT(*) FROM market_observations").fetchone()[0]
        else:
            value = connection.execute(
                "SELECT COUNT(*) FROM market_observations WHERE source_code=?",
                (source,),
            ).fetchone()[0]
        connection.close()
        return int(value)

    def dest_quality(self, key: bytes) -> str:
        connection = sqlite3.connect(self.legacy)
        row = connection.execute(
            "SELECT quality_state FROM market_observations WHERE event_key=?",
            (key,),
        ).fetchone()
        connection.close()
        self.assertIsNotNone(row)
        return str(row[0])

    def dest_price(self, key: bytes) -> float:
        connection = sqlite3.connect(self.legacy)
        row = connection.execute(
            "SELECT price_num FROM market_observations WHERE event_key=?",
            (key,),
        ).fetchone()
        connection.close()
        self.assertIsNotNone(row)
        return float(row[0])

    def test_eligible_private_event_projects_once(self) -> None:
        key = derive_event_key("bridge-eligible", 1)
        self.seed_shadow([_private_offer(key=key, when=self.when)])
        first = self.project()
        self.assertEqual(first["projected"], 1)
        self.assertEqual(self.dest_count("PRIVATE_GOLD_CHANNEL"), 1)
        self.assertEqual(self.dest_price(key), 45000000.0)

    def test_second_run_is_idempotent(self) -> None:
        key = derive_event_key("bridge-idempotent", 1)
        self.seed_shadow([_private_offer(key=key, when=self.when)])
        self.project()
        second = self.project()
        self.assertEqual(second["projected"], 0)
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(self.dest_count("PRIVATE_GOLD_CHANNEL"), 1)

    def test_duplicate_delivery_does_not_duplicate_application(self) -> None:
        key = derive_event_key("bridge-duplicate", 1)
        self.seed_shadow([_private_offer(key=key, when=self.when)])
        self.project()
        self.project()
        self.project()
        self.assertEqual(self.dest_count("PRIVATE_GOLD_CHANNEL"), 1)

    def test_new_revision_updates_same_event_key(self) -> None:
        key = derive_event_key("bridge-revision", 1)
        self.seed_shadow([_private_offer(key=key, when=self.when, price="45000000")])
        self.project()
        shadow = connect_market_store(self.shadow)
        upsert_observation(
            shadow,
            _private_offer(
                key=key,
                when=self.when,
                available=datetime(2026, 8, 29, 9, 1, tzinfo=timezone.utc),
                price="45200000",
                extra={"fact_revision": 2},
            ),
        )
        shadow.commit()
        shadow.close()
        result = self.project()
        self.assertEqual(result["updated"], 1)
        self.assertEqual(self.dest_price(key), 45200000.0)

    def test_retraction_makes_fact_non_realtime(self) -> None:
        key = derive_event_key("bridge-retract", 1)
        self.seed_shadow([_private_offer(key=key, when=self.when)])
        self.project()
        shadow = sqlite3.connect(self.shadow)
        shadow.execute(
            "UPDATE market_observations SET quality_state='REJECTED' WHERE event_key=?",
            (key,),
        )
        shadow.commit()
        shadow.close()
        self.project()
        self.assertEqual(self.dest_quality(key), "REJECTED")

    def test_pending_review_and_rejected_are_not_dropped(self) -> None:
        pending = derive_event_key("bridge-pending", 1)
        rejected = derive_event_key("bridge-rejected", 1)
        self.seed_shadow(
            [
                _private_offer(key=pending, when=self.when, quality="PENDING_REVIEW"),
                _private_offer(
                    key=rejected,
                    when=datetime(2026, 8, 29, 9, 2, tzinfo=timezone.utc),
                    quality="REJECTED",
                ),
            ]
        )
        result = self.project()
        self.assertEqual(result["audit_only"], 2)
        self.assertEqual(self.dest_quality(pending), "PENDING_REVIEW")
        self.assertEqual(self.dest_quality(rejected), "REJECTED")

    def test_unknown_schema_fails_closed(self) -> None:
        self.seed_shadow([_private_offer(key=derive_event_key("schema", 1), when=self.when)])
        dest = sqlite3.connect(self.legacy)
        dest.execute("UPDATE market_store_metadata SET schema_version=99 WHERE singleton=1")
        dest.commit()
        dest.close()
        with self.assertRaises(Exception):
            self.project()

    def test_incompatible_parser_is_rejected(self) -> None:
        self.seed_shadow(
            [
                _private_offer(
                    key=derive_event_key("parser", 1),
                    when=self.when,
                    parser="unknown-experimental-parser-v1",
                )
            ]
        )
        with self.assertRaises(BridgeError):
            self.project()
        self.assertFalse(parser_version_allowed("staging-market-input-bridge-v5"))

    def test_canonical_toman_is_not_reconverted(self) -> None:
        key = derive_event_key("bridge-toman", 1)
        self.seed_shadow([_private_offer(key=key, when=self.when, price="45000000")])
        self.project()
        connection = sqlite3.connect(self.legacy)
        row = connection.execute(
            "SELECT price_num, price_unit, currency FROM market_observations WHERE event_key=?",
            (key,),
        ).fetchone()
        connection.close()
        self.assertEqual(row[0], 45000000.0)
        self.assertEqual(row[1], "TOMAN_PER_MESGHAL_750")
        self.assertEqual(row[2], "TOMAN")

    def test_available_at_is_preserved_exactly(self) -> None:
        key = derive_event_key("bridge-available", 1)
        event = datetime(2026, 8, 29, 8, 59, 10, tzinfo=timezone.utc)
        available = datetime(2026, 8, 29, 8, 59, 14, tzinfo=timezone.utc)
        self.seed_shadow([_private_offer(key=key, when=event, available=available)])
        self.project()
        connection = sqlite3.connect(self.legacy)
        row = connection.execute(
            "SELECT event_time_utc, available_at_utc FROM market_observations WHERE event_key=?",
            (key,),
        ).fetchone()
        connection.close()
        self.assertEqual(row[0], "2026-08-29T08:59:10Z")
        self.assertEqual(row[1], "2026-08-29T08:59:14Z")

    def test_unrelated_rows_are_preserved(self) -> None:
        public = derive_event_key("unrelated-public", 1)
        dest = _store(self.legacy)
        upsert_observation(dest, _unrelated(public, self.when))
        dest.commit()
        dest.close()
        self.seed_shadow([_private_offer(key=derive_event_key("related", 1), when=self.when)])
        dest = connect_market_store(self.legacy)
        before = unrelated_row_count(dest, tuple(PRIVATE_SOURCES))
        dest.close()
        self.project()
        dest = connect_market_store(self.legacy)
        after = unrelated_row_count(dest, tuple(PRIVATE_SOURCES))
        dest.close()
        self.assertEqual(before, after)
        self.assertEqual(self.dest_count("MELTED_FLOW"), 1)

    def test_source_is_read_only(self) -> None:
        self.seed_shadow([_private_offer(key=derive_event_key("ro", 1), when=self.when)])
        verify_source_read_only(self.shadow)
        before = Path(self.shadow).stat().st_mtime
        self.project()
        self.assertEqual(Path(self.shadow).stat().st_mtime, before)

    def test_mid_run_error_rolls_back(self) -> None:
        first = derive_event_key("rollback-a", 1)
        second = derive_event_key("rollback-b", 1)
        self.seed_shadow(
            [
                _private_offer(key=first, when=self.when),
                _private_offer(
                    key=second,
                    when=datetime(2026, 8, 29, 9, 1, tzinfo=timezone.utc),
                ),
            ]
        )
        calls = {"n": 0}

        def fail_second(connection, observation):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise RuntimeError("injected_failure")
            return upsert_observation(connection, observation)

        with patch(
            "core.market_intelligence.shadow_legacy_bridge.upsert_observation",
            side_effect=fail_second,
        ):
            with self.assertRaises(RuntimeError):
                self.project()
        self.assertEqual(self.dest_count("PRIVATE_GOLD_CHANNEL"), 0)

    def test_source_wal_can_change_during_read(self) -> None:
        key = derive_event_key("wal-source", 1)
        self.seed_shadow([_private_offer(key=key, when=self.when)])
        writer = connect_market_store(self.shadow)
        upsert_observation(
            writer,
            _private_offer(
                key=derive_event_key("wal-extra", 2),
                when=datetime(2026, 8, 29, 9, 3, tzinfo=timezone.utc),
            ),
        )
        writer.commit()
        result = self.project()
        writer.close()
        self.assertGreaterEqual(result["projected"] + result["unchanged"] + result["updated"], 1)

    def test_lock_contention_fails_closed(self) -> None:
        self.seed_shadow([_private_offer(key=derive_event_key("lock", 1), when=self.when)])
        _conversation(self.conversation)
        self.market_lock.write_bytes(b"")
        holder = open(self.market_lock, "a+b")
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        try:
            code = orchestrator_main(
                [
                    "--shadow-market-store",
                    str(self.shadow),
                    "--legacy-market-store",
                    str(self.legacy),
                    "--conversation-db",
                    str(self.conversation),
                    "--ledger",
                    str(self.ledger),
                    "--heartbeat",
                    str(self.heartbeat),
                    "--release-sha",
                    _SHA,
                    "--market-lock",
                    str(self.market_lock),
                    "--conversation-lock",
                    str(self.conversation_lock),
                    "--lock-timeout-seconds",
                    "1",
                    "--skip-quick-check",
                ]
            )
            self.assertEqual(code, 2)
            health = json.loads(self.heartbeat.read_text(encoding="utf-8"))
            self.assertEqual(health["status"], "FAILED")
            self.assertEqual(health["failure_reason_code"], "writer_lock_timeout")
        finally:
            holder.close()

    def test_checkpoint_resume_projects_only_new_facts(self) -> None:
        first = derive_event_key("resume-a", 1)
        self.seed_shadow([_private_offer(key=first, when=self.when)])
        self.project()
        extra = derive_event_key("resume-b", 2)
        shadow = connect_market_store(self.shadow)
        upsert_observation(
            shadow,
            _private_offer(
                key=extra,
                when=datetime(2026, 8, 29, 9, 4, tzinfo=timezone.utc),
            ),
        )
        shadow.commit()
        shadow.close()
        second = self.project()
        self.assertEqual(second["projected"], 1)
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(second["mode"], "incremental")

    def test_incremental_run_skips_old_projected_facts(self) -> None:
        old = derive_event_key("inc-old", 1)
        mid = derive_event_key("inc-mid", 2)
        new = derive_event_key("inc-new", 3)
        self.seed_shadow([_private_offer(key=old, when=self.when)])
        first = self.project()
        self.assertEqual(first["mode"], "full")
        # Make the original row genuinely older than the later incremental
        # watermark.  Leaving its insertion timestamp tied to wall-clock time
        # makes this assertion depend on the hour at which the suite runs and
        # correctly causes the bridge to revisit it as a possible backfill.
        shadow = connect_market_store(self.shadow)
        shadow.execute(
            "UPDATE market_observations SET inserted_at_utc=? WHERE event_key=?",
            (self.when.isoformat().replace("+00:00", "Z"), old),
        )
        shadow.commit()
        shadow.close()
        shadow = connect_market_store(self.shadow)
        upsert_observation(
            shadow,
            _private_offer(
                key=mid,
                when=datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),
            ),
        )
        shadow.commit()
        shadow.close()
        second = self.project()
        self.assertEqual(second["mode"], "incremental")
        shadow = connect_market_store(self.shadow)
        upsert_observation(
            shadow,
            _private_offer(
                key=new,
                when=datetime(2026, 8, 29, 12, 5, tzinfo=timezone.utc),
            ),
        )
        shadow.commit()
        shadow.close()
        third = self.project()
        self.assertEqual(third["mode"], "incremental")
        self.assertEqual(third["projected"], 1)
        self.assertEqual(third["unchanged"], 1)
        self.assertEqual(third["selected"], 2)
        self.assertEqual(self.dest_count("PRIVATE_GOLD_CHANNEL"), 3)

    def test_incremental_follows_slowest_source_watermark(self) -> None:
        channel = derive_event_key("wm-channel", 1)
        group = derive_event_key("wm-group", 1)
        late_channel = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
        self.seed_shadow(
            [
                _private_offer(key=channel, when=late_channel),
                _group(key=group, source="GROUP_1", when=self.when),
            ]
        )
        first = self.project(sources=MARKET_BRIDGE_SOURCES)
        self.assertEqual(first["mode"], "full")
        extra = derive_event_key("wm-group-late", 2)
        shadow = connect_market_store(self.shadow)
        upsert_observation(
            shadow,
            _group(
                key=extra,
                source="GROUP_1",
                when=datetime(2026, 8, 29, 9, 5, tzinfo=timezone.utc),
            ),
        )
        shadow.commit()
        shadow.close()
        second = self.project(sources=MARKET_BRIDGE_SOURCES)
        self.assertEqual(second["mode"], "incremental")
        self.assertEqual(self.dest_count("GROUP_1"), 2)

    def test_interrupted_run_can_be_repeated(self) -> None:
        key = derive_event_key("interrupt", 1)
        self.seed_shadow([_private_offer(key=key, when=self.when)])
        with patch(
            "core.market_intelligence.shadow_legacy_bridge.upsert_observation",
            side_effect=KeyboardInterrupt,
        ):
            with self.assertRaises(KeyboardInterrupt):
                self.project()
        result = self.project()
        self.assertEqual(result["projected"], 1)

    def test_no_raw_text_or_identity_crosses_the_boundary(self) -> None:
        key = derive_event_key("privacy", 1)
        self.seed_shadow([_private_offer(key=key, when=self.when)])
        shadow = sqlite3.connect(self.shadow)
        shadow.execute(
            """
            UPDATE market_observations
            SET attributes_json=?
            WHERE event_key=?
            """,
            (
                json.dumps(
                    {
                        "paper_variant": "NOT_APPLICABLE",
                        "raw_text": "should-not-travel",
                        "sender_user": "alice",
                        "telegram_message_id": 99,
                    }
                ),
                key,
            ),
        )
        shadow.commit()
        shadow.close()
        self.project()
        dest = sqlite3.connect(self.legacy)
        attributes = dest.execute(
            "SELECT attributes_json FROM market_observations WHERE event_key=?",
            (key,),
        ).fetchone()[0]
        dest.close()
        self.assertNotIn("raw_text", attributes)
        self.assertNotIn("alice", attributes)
        self.assertNotIn("telegram_message_id", attributes)
        ledger = sqlite3.connect(self.ledger)
        dumped = " ".join(
            str(row) for row in ledger.execute("SELECT * FROM projection_ledger")
        )
        ledger.close()
        self.assertNotIn("alice", dumped)
        self.assertNotIn("should-not-travel", dumped)

    def test_group_sources_project_together(self) -> None:
        g1 = derive_event_key("group-one", 1)
        g2 = derive_event_key("group-two", 1)
        self.seed_shadow(
            [
                _group(key=g1, source="GROUP_1", when=self.when),
                _group(
                    key=g2,
                    source="GROUP_2",
                    when=datetime(2026, 8, 29, 9, 1, tzinfo=timezone.utc),
                ),
            ]
        )
        result = self.project(sources=GROUP_SOURCES)
        self.assertEqual(result["projected"], 2)
        self.assertEqual(self.dest_count("GROUP_1"), 1)
        self.assertEqual(self.dest_count("GROUP_2"), 1)

    def test_private_minute_facts_project(self) -> None:
        key = derive_event_key("minute", 1)
        self.seed_shadow([_minute(key, self.when)])
        result = self.project()
        self.assertEqual(result["projected"], 1)
        self.assertEqual(self.dest_count("PRIVATE_GOLD_PAPER_MINUTE"), 1)

    def test_trade_keeps_root_offer_lifecycle(self) -> None:
        offer = derive_event_key("life-offer", 1)
        trade = derive_event_key("life-trade", 2)
        early = datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc)
        self.seed_shadow(
            [
                _private_offer(key=offer, when=early),
                _private_offer(
                    key=trade,
                    when=self.when,
                    event_type="TRADE",
                    extra={"root_offer_event_key": offer.hex()},
                ),
            ]
        )
        result = self.project()
        self.assertEqual(result["projected"], 2)
        self.assertEqual(self.dest_count("PRIVATE_GOLD_CHANNEL"), 2)

    def test_transported_trade_keeps_root_offer_fact_id(self) -> None:
        offer = derive_event_key("transport-offer", 1)
        trade = derive_event_key("transport-trade", 2)
        fact_id = "4" * 64
        self.seed_shadow(
            [
                _group(
                    key=offer,
                    source="GROUP_1",
                    when=self.when,
                    extra={"transfer_fact_id": fact_id},
                ),
                _group(
                    key=trade,
                    source="GROUP_1",
                    when=datetime(2026, 8, 29, 9, 1, tzinfo=timezone.utc),
                    event_type="TRADE",
                    extra={"root_offer_fact_id": fact_id},
                ),
            ]
        )
        self.project(sources=GROUP_SOURCES)
        connection = sqlite3.connect(self.legacy)
        attributes = json.loads(
            connection.execute(
                "SELECT attributes_json FROM market_observations WHERE event_key=?",
                (trade,),
            ).fetchone()[0]
        )
        connection.close()
        self.assertEqual(attributes["root_offer_fact_id"], fact_id)

    def test_invalid_transported_root_offer_fact_id_fails_closed(self) -> None:
        trade = derive_event_key("transport-invalid", 1)
        self.seed_shadow(
            [
                _group(
                    key=trade,
                    source="GROUP_1",
                    when=self.when,
                    event_type="TRADE",
                    extra={"root_offer_fact_id": "4" * 63},
                )
            ]
        )
        with self.assertRaisesRegex(BridgeError, "root_offer_fact_id_invalid"):
            self.project(sources=GROUP_SOURCES)

    def test_forced_full_reconcile_revisits_rows_before_overlap(self) -> None:
        old = derive_event_key("force-full-old", 1)
        new = derive_event_key("force-full-new", 2)
        self.seed_shadow([_private_offer(key=old, when=self.when)])
        self.project()
        later = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
        source = connect_market_store(self.shadow)
        upsert_observation(source, _private_offer(key=new, when=later))
        source.execute(
            "UPDATE market_observations SET inserted_at_utc=? WHERE event_key=?",
            (later.isoformat().replace("+00:00", "Z"), new),
        )
        source.commit()
        source.close()
        self.project()
        source = sqlite3.connect(self.shadow)
        source.execute(
            "UPDATE market_observations SET price_value='45200000',price_num=45200000,inserted_at_utc=? WHERE event_key=?",
            (self.when.isoformat().replace("+00:00", "Z"), old),
        )
        source.commit()
        source.close()
        incremental = self.project()
        self.assertEqual(incremental["mode"], "incremental")
        self.assertEqual(self.dest_price(old), 45000000.0)
        full = self.project(force_full_reconcile=True)
        self.assertEqual(full["mode"], "full")
        self.assertEqual(self.dest_price(old), 45200000.0)

    def test_missing_source_fact_is_retired(self) -> None:
        key = derive_event_key("retire-me", 1)
        self.seed_shadow([_private_offer(key=key, when=self.when)])
        self.project()
        shadow = sqlite3.connect(self.shadow)
        shadow.execute("DELETE FROM market_observations WHERE event_key=?", (key,))
        shadow.commit()
        shadow.close()
        result = self.project()
        self.assertEqual(result["removed"], 1)
        self.assertEqual(self.dest_quality(key), "IGNORED")

    def test_failure_heartbeat_has_no_payload(self) -> None:
        code = private_main(
            [
                "--shadow-market-store",
                str(self.root / "missing.sqlite"),
                "--legacy-market-store",
                str(self.legacy),
                "--ledger",
                str(self.ledger),
            ]
        )
        self.assertEqual(code, 2)

    def test_exact_release_binding(self) -> None:
        self.seed_shadow([_private_offer(key=derive_event_key("bind", 1), when=self.when)])
        _conversation(self.conversation)
        fake_root = self.root / "release-tree"
        fake_root.mkdir()
        (fake_root / "RELEASE_SHA").write_text("b" * 40 + "\n", encoding="utf-8")
        with patch(
            "scripts.run_private_shadow_legacy_estimator_bridge.REPO_ROOT",
            fake_root,
        ):
            code = orchestrator_main(
                [
                    "--shadow-market-store",
                    str(self.shadow),
                    "--legacy-market-store",
                    str(self.legacy),
                    "--conversation-db",
                    str(self.conversation),
                    "--ledger",
                    str(self.ledger),
                    "--heartbeat",
                    str(self.heartbeat),
                    "--release-sha",
                    _SHA,
                    "--market-lock",
                    str(self.market_lock),
                    "--conversation-lock",
                    str(self.conversation_lock),
                    "--skip-quick-check",
                ]
            )
        self.assertEqual(code, 2)

    def test_orchestrator_projects_groups_and_writes_health(self) -> None:
        self.seed_shadow(
            [
                _private_offer(key=derive_event_key("orch-private", 1), when=self.when),
                _group(
                    key=derive_event_key("orch-g1", 1),
                    source="GROUP_1",
                    when=self.when,
                ),
                _group(
                    key=derive_event_key("orch-g2", 1),
                    source="GROUP_2",
                    when=self.when,
                ),
            ]
        )
        _conversation(self.conversation)
        code = orchestrator_main(
            [
                "--shadow-market-store",
                str(self.shadow),
                "--legacy-market-store",
                str(self.legacy),
                "--conversation-db",
                str(self.conversation),
                "--ledger",
                str(self.ledger),
                "--heartbeat",
                str(self.heartbeat),
                "--release-sha",
                _SHA,
                "--market-lock",
                str(self.market_lock),
                "--conversation-lock",
                str(self.conversation_lock),
                "--skip-quick-check",
            ]
        )
        self.assertEqual(code, 0)
        health = json.loads(self.heartbeat.read_text(encoding="utf-8"))
        self.assertEqual(health["status"], "OK")
        self.assertEqual(health["release_sha"], _SHA)
        self.assertNotIn("alice", health)
        groups = project_groups(self.shadow, self.conversation)
        self.assertGreaterEqual(int(groups["eligible_offers"]), 2)

    def test_model_on_clone_keeps_fourteen_cells(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        fresh = now - timedelta(seconds=30)
        observations = [
            _private_offer(
                key=derive_event_key("model-cash", 1),
                when=fresh,
                settlement="TODAY",
            ),
            _private_offer(
                key=derive_event_key("model-tomorrow", 1),
                when=fresh,
                settlement="TOMORROW",
            ),
        ]
        for code in (
            "IMAM",
            "BAHAR",
            "HALF_BAHAR",
            "QUARTER_BAHAR",
            "HALF_LOW_DATE",
            "QUARTER_LOW_DATE",
        ):
            observations.append(
                _group(
                    key=derive_event_key("model-" + code, 1),
                    source="GROUP_1",
                    when=fresh,
                    instrument="COIN_" + code,
                )
            )
        self.seed_shadow(observations)
        # Force one tomorrow coin book with an explicit observation.
        shadow = connect_market_store(self.shadow)
        for code in (
            "IMAM",
            "BAHAR",
            "HALF_BAHAR",
            "QUARTER_BAHAR",
            "HALF_LOW_DATE",
            "QUARTER_LOW_DATE",
            "ONE_GRAM",
        ):
            upsert_observation(
                shadow,
                MarketObservation(
                    event_key=derive_event_key("model-t", code),
                    source_code="GROUP_1",
                    source_family="GROUP",
                    event_time_utc=fresh,
                    available_at_utc=fresh,
                    instrument="COIN_" + code,
                    market_label="GROUP_COIN_" + code,
                    settlement_term="TOMORROW",
                    trade_form="PHYSICAL",
                    event_type="OFFER",
                    side="BUY",
                    price=Decimal("186900"),
                    price_unit="PROJECT_THOUSAND_TOMAN",
                    currency="TOMAN",
                    quantity=Decimal("2"),
                    quantity_unit="COIN_COUNT",
                    parser_version="coin-group-rules-v10-reviewed-fallbacks",
                ),
            )
        shadow.commit()
        shadow.close()
        self.project(sources=MARKET_BRIDGE_SOURCES)
        dest = connect_market_store(self.legacy)
        estimates = build_coin_rate_estimates(
            dest, as_of_utc=datetime.now(timezone.utc) + timedelta(seconds=2)
        )
        dest.close()
        self.assertEqual(len(estimates), 14)
        one_gram_cash = next(
            item
            for item in estimates
            if item.commodity_code == "ONE_GRAM" and item.settlement_term == "CASH"
        )
        self.assertEqual(one_gram_cash.status, "NO_DATA")
        self.assertEqual(one_gram_cash.reason, "NO_SAFE_SAME_COMMODITY_ANCHOR")
        estimated = [item for item in estimates if item.status == "ESTIMATED"]
        self.assertGreaterEqual(len(estimated), 1)

    def test_product_mode_must_stay_legacy(self) -> None:
        self.seed_shadow([_private_offer(key=derive_event_key("mode", 1), when=self.when)])
        _conversation(self.conversation)
        os.environ["PRODUCT_ESTIMATOR_SNAPSHOT_MODE"] = "PRIVATE_PRIMARY"
        code = orchestrator_main(
            [
                "--shadow-market-store",
                str(self.shadow),
                "--legacy-market-store",
                str(self.legacy),
                "--conversation-db",
                str(self.conversation),
                "--ledger",
                str(self.ledger),
                "--heartbeat",
                str(self.heartbeat),
                "--release-sha",
                _SHA,
                "--market-lock",
                str(self.market_lock),
                "--conversation-lock",
                str(self.conversation_lock),
                "--skip-quick-check",
            ]
        )
        self.assertEqual(code, 2)

    def test_rollback_deactivates_projected_rows(self) -> None:
        key = derive_event_key("rollback-row", 1)
        self.seed_shadow([_private_offer(key=key, when=self.when)])
        self.project()
        code = rollback_main(
            ["--legacy-market-store", str(self.legacy), "--ledger", str(self.ledger)]
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.dest_quality(key), "IGNORED")

    def test_quick_check_helper(self) -> None:
        self.seed_shadow([_private_offer(key=derive_event_key("qc", 1), when=self.when)])
        self.assertEqual(sqlite_quick_check(self.shadow), "OK")

    def test_private_cli_rejects_missing_source(self) -> None:
        code = private_main(
            [
                "--shadow-market-store",
                str(self.shadow),
                "--legacy-market-store",
                str(self.legacy),
                "--ledger",
                str(self.ledger),
            ]
        )
        self.assertEqual(code, 2)


class BridgeInstallerTests(unittest.TestCase):
    def test_unit_hardening_and_release_placeholders(self) -> None:
        template = (
            _REPO
            / "deploy/coin_intelligence/systemd/coin-private-shadow-legacy-estimator-bridge.service.template"
        ).read_text(encoding="utf-8")
        timer = (
            _REPO
            / "deploy/coin_intelligence/systemd/coin-private-shadow-legacy-estimator-bridge.timer"
        ).read_text(encoding="utf-8")
        for token in (
            "Type=oneshot",
            "NoNewPrivileges=true",
            "ProtectSystem=strict",
            "ProtectHome=read-only",
            "PrivateTmp=true",
            "PrivateNetwork=true",
            "ReadOnlyPaths=@RELEASE_ROOT@ @SHADOW_STORE_DIR@",
            "UMask=0077",
            "RuntimeMaxSec=300",
            "PRODUCT_ESTIMATOR_SNAPSHOT_MODE=LEGACY",
            "flock --exclusive --timeout 300",
        ):
            self.assertIn(token, template)
        self.assertIn("OnUnitInactiveSec=15s", timer)
        self.assertNotIn("EnvironmentFile", template)

    def test_installer_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bridge-install-") as directory:
            root = Path(directory)
            systemd = root / "systemd"
            releases = root / "releases"
            state = root / "state"
            market = root / "production-market"
            estimator = root / "production-estimator"
            shadow_dir = root / "shadow"
            shadow_dir.mkdir()
            (shadow_dir / "market-store.sqlite").write_bytes(b"")
            (market / "market").mkdir(parents=True)
            (market / "staging").mkdir(parents=True)
            (estimator / "conversation").mkdir(parents=True)
            (market / "market" / "market.sqlite3").write_bytes(b"")
            (estimator / "conversation" / "conversation_events.sqlite3").write_bytes(b"")
            release_dir = releases / _SHA
            release_dir.mkdir(parents=True)
            (release_dir / "RELEASE_SHA").write_text(_SHA + "\n", encoding="utf-8")
            env = os.environ.copy()
            env.update(
                {
                    "APP_ENV_FILE": str(_REPO / "config" / "unit-test.env.example"),
                    "PRIVATE_SHADOW_LEGACY_BRIDGE_CONFIRM": (
                        "install-private-shadow-legacy-estimator-bridge"
                    ),
                    "PRIVATE_SHADOW_LEGACY_BRIDGE_RELEASE_SHA": _SHA,
                    "PRIVATE_SHADOW_LEGACY_BRIDGE_RELEASE_ROOT": str(releases),
                    "PRIVATE_SHADOW_LEGACY_BRIDGE_SHADOW_STORE": str(
                        shadow_dir / "market-store.sqlite"
                    ),
                    "COIN_INTELLIGENCE_MARKET_RUNTIME_ROOT": str(market),
                    "COIN_INTELLIGENCE_ESTIMATOR_RUNTIME_ROOT": str(estimator),
                    "PRIVATE_SHADOW_LEGACY_BRIDGE_STATE_ROOT": str(state),
                    "PRIVATE_SHADOW_LEGACY_BRIDGE_SYSTEMD_DIR": str(systemd),
                    "PRIVATE_SHADOW_LEGACY_BRIDGE_SKIP_SYSTEMCTL": "1",
                    "PRIVATE_SHADOW_LEGACY_BRIDGE_ACTIVATE_TIMER": "0",
                    "PROJECT_DIR": str(_REPO),
                }
            )
            script = _REPO / "scripts/install_private_shadow_legacy_estimator_bridge.sh"
            first = subprocess.run(
                ["bash", str(script)],
                check=False,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            service = (systemd / "coin-private-shadow-legacy-estimator-bridge.service").read_text(
                encoding="utf-8"
            )
            second = subprocess.run(
                ["bash", str(script)],
                check=False,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            again = (systemd / "coin-private-shadow-legacy-estimator-bridge.service").read_text(
                encoding="utf-8"
            )
            self.assertEqual(service, again)
            self.assertIn(_SHA, service)
            self.assertIn("ProtectSystem=strict", service)
            self.assertEqual(len(list(systemd.glob("*.service"))), 1)
            self.assertEqual(len(list(systemd.glob("*.timer"))), 1)
