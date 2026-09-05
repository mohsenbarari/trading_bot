from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from core.market_intelligence.market_contracts import MarketObservation, derive_event_key
from core.market_intelligence.market_fact_archive import (
    _normalize_fact_payload,
    build_and_publish_fact,
    stable_fact_id,
)
from core.market_intelligence.market_fact_projection import (
    MarketFactProjectionError,
    _ensure_offer_dependency_archived,
    observation_payload,
)
from core.market_intelligence.market_store import (
    connect_market_store,
    initialize_market_store,
    upsert_observation,
)
from core.market_intelligence.private_pipeline_contracts import (
    content_hash,
    load_source_registry,
)


class ProjectionCursor:
    def __init__(self, archive):
        self.archive = archive

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, parameters=()):
        self.archive.queries.append((query, parameters))

    def fetchone(self):
        return (1,) if self.archive.parent_present else None


class ProjectionArchive:
    def __init__(self, *, parent_present=False):
        self.parent_present = parent_present
        self.queries = []

    def cursor(self):
        return ProjectionCursor(self)


class ExistingFactCursor:
    def __init__(self, row):
        self.row = row
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, parameters=()):
        self.queries.append((query, parameters))

    def fetchone(self):
        return self.row


class ExistingFactArchive:
    def __init__(self, row):
        self.cursor_instance = ExistingFactCursor(row)

    def cursor(self):
        return self.cursor_instance


class ParentDependencyRecoveryTests(unittest.TestCase):
    def private_gold_rows(self):
        temporary = tempfile.TemporaryDirectory()
        market = connect_market_store(Path(temporary.name) / "market.sqlite3")
        initialize_market_store(market)
        offer_key = derive_event_key("parent-recovery", "offer")
        trade_key = derive_event_key("parent-recovery", "trade")
        common = {
            "source_code": "PRIVATE_GOLD_CHANNEL",
            "source_family": "TELEGRAM_PRIVATE",
            "instrument": "MELTED_GOLD_PRIVATE",
            "market_label": "PRIVATE_GOLD",
            "settlement_term": "TODAY",
            "trade_form": "PHYSICAL",
            "side": "SELL",
            "price_unit": "TOMAN_PER_MESGHAL_750",
            "currency": "TOMAN",
            "quantity_unit": "LOT_COUNT",
            "parser_version": "parent-recovery-test-v1",
        }
        upsert_observation(
            market,
            MarketObservation(
                event_key=offer_key,
                event_time_utc="2026-09-05T06:00:00Z",
                available_at_utc="2026-09-05T06:00:01Z",
                event_type="OFFER",
                price="100000000",
                quantity="5",
                **common,
            ),
        )
        upsert_observation(
            market,
            MarketObservation(
                event_key=trade_key,
                event_time_utc="2026-09-05T06:00:10Z",
                available_at_utc="2026-09-05T06:00:11Z",
                event_type="TRADE",
                price="100000000",
                quantity="2",
                attributes={
                    "root_offer_event_key": offer_key.hex(),
                    "offer_quantity": "5",
                    "remaining_quantity": "3",
                },
                **common,
            ),
        )
        market.commit()
        parent = market.execute(
            "SELECT * FROM market_observations WHERE event_key=?", (offer_key,)
        ).fetchone()
        child = market.execute(
            "SELECT * FROM market_observations WHERE event_key=?", (trade_key,)
        ).fetchone()
        return temporary, market, parent, child

    def test_missing_archive_parent_is_replayed_from_real_observation(self):
        temporary, market, parent, child = self.private_gold_rows()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(market.close)
        archive = ProjectionArchive()
        expected = stable_fact_id(
            source_code="PRIVATE_GOLD_CHANNEL",
            event_key=bytes(parent["event_key"]).hex(),
            fact_kind="PRIVATE_GOLD_OFFER",
        )

        def publish(*_args, **_kwargs):
            archive.parent_present = True
            return SimpleNamespace(fact=SimpleNamespace(fact_id=expected))

        with patch(
            "core.market_intelligence.market_fact_projection.build_and_publish_fact",
            side_effect=publish,
        ) as publisher:
            counts = _ensure_offer_dependency_archived(
                market, archive, child, observation_payload(market, child)
            )
        publisher.assert_called_once()
        self.assertEqual(
            publisher.call_args.kwargs["event_key"], bytes(parent["event_key"]).hex()
        )
        self.assertTrue(archive.parent_present)
        self.assertEqual(counts, (1, 0))

    def test_existing_archive_parent_is_not_republished(self):
        temporary, market, _parent, child = self.private_gold_rows()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(market.close)
        archive = ProjectionArchive(parent_present=True)
        with patch(
            "core.market_intelligence.market_fact_projection.build_and_publish_fact"
        ) as publisher:
            counts = _ensure_offer_dependency_archived(
                market, archive, child, observation_payload(market, child)
            )
        publisher.assert_not_called()
        self.assertEqual(counts, (0, 0))

    def test_parent_identity_mismatch_is_rejected_before_publish(self):
        temporary, market, _parent, child = self.private_gold_rows()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(market.close)
        payload = observation_payload(market, child)
        payload["offer_fact_id"] = "a" * 64
        with patch(
            "core.market_intelligence.market_fact_projection.build_and_publish_fact"
        ) as publisher:
            with self.assertRaisesRegex(
                MarketFactProjectionError,
                "market_fact_projection_offer_dependency_identity_mismatch",
            ):
                _ensure_offer_dependency_archived(
                    market, ProjectionArchive(), child, payload
                )
        publisher.assert_not_called()

    def test_identical_fact_replay_repairs_projection_without_new_revision(self):
        payload = {
            "kind": "PRIVATE_GOLD_OFFER",
            "instrument": "MELTED_GOLD_PRIVATE",
            "side": "SELL",
            "settlement": "TODAY",
            "trade_form": "PHYSICAL",
            "offered_price_value": "100000000",
            "price_unit": "TOMAN_PER_MESGHAL_750",
            "quantity_value": "5",
            "quantity_unit": "LOT_COUNT",
            "lifetime_seconds": 120,
        }
        normalized = _normalize_fact_payload(payload)
        source = load_source_registry().by_code()["PRIVATE_GOLD_CHANNEL"]
        event_key = "1" * 64
        occurred = datetime(2026, 9, 5, 6, 0, tzinfo=timezone.utc)
        available = datetime(2026, 9, 5, 6, 0, 1, tzinfo=timezone.utc)
        persisted = datetime(2026, 9, 5, 6, 0, 2, tzinfo=timezone.utc)
        existing = (
            7,
            3,
            content_hash(normalized),
            event_key,
            source.fact_stream_id,
            event_key,
            "PRIVATE_GOLD_CHANNEL",
            occurred,
            available,
            persisted,
            "parent-recovery-test-v1",
            "ELIGIBLE",
            [],
            normalized.model_dump(mode="json"),
        )
        archive = ExistingFactArchive(existing)
        with patch(
            "core.market_intelligence.market_fact_archive._write_projection"
        ) as projection:
            result = build_and_publish_fact(
                archive,
                event_key=event_key,
                origin_event_key=event_key,
                source_code="PRIVATE_GOLD_CHANNEL",
                occurred_at_utc=occurred,
                available_at_utc=available,
                parser_version="parent-recovery-test-v1",
                quality_state="ELIGIBLE",
                quality_reason_codes=(),
                payload=payload,
            )
        self.assertFalse(result.changed)
        self.assertIsNone(result.delivery_sequence)
        self.assertEqual(result.fact.fact_revision, 3)
        projection.assert_called_once()


if __name__ == "__main__":
    unittest.main()
