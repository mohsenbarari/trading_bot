from __future__ import annotations

from datetime import datetime, timezone
import re
import unittest
from unittest.mock import patch

from core.market_intelligence.contracts import (
    RateShadowPrediction,
    ShadowObservation,
)
from core.market_intelligence.ledger import (
    persist_parser_observation,
    persist_rate_prediction,
    shadow_subject_fingerprint,
)
from core.market_intelligence import project_events
from core.sync_registry import SyncPolicy, get_sync_registry_entry
from models.coin_intelligence_shadow import (
    CoinIntelligenceShadowFeatureSnapshot,
    CoinIntelligenceShadowParserResult,
    CoinIntelligenceShadowPrediction,
    CoinIntelligenceShadowRun,
)
from models.offer import Offer, OfferStatus, OfferType


NOW = datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc)


class FakeAsyncSession:
    def __init__(self, *, scalar_result=None) -> None:
        self.added = []
        self.committed = False
        self.rolled_back = False
        self.flushed = False
        self.scalar_result = scalar_result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def add(self, value) -> None:
        self.added.append(value)

    async def scalar(self, _statement):
        return self.scalar_result

    async def commit(self) -> None:
        self.committed = True

    async def flush(self) -> None:
        self.flushed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class FakeSyncSession:
    def __init__(self, *, new=(), dirty=()) -> None:
        self.new = tuple(new)
        self.dirty = tuple(dirty)
        self.info = {}


class CoinIntelligenceShadowLedgerTests(
    unittest.IsolatedAsyncioTestCase
):
    def test_subject_fingerprint_is_stable_and_opaque(self) -> None:
        first = shadow_subject_fingerprint("offer", "public-offer-123")
        second = shadow_subject_fingerprint("OFFER", "public-offer-123")

        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{64}$")
        self.assertNotIn("public-offer", first)

    async def test_parser_persistence_contains_no_raw_input(self) -> None:
        session = FakeAsyncSession()
        observation = ShadowObservation(
            status="INFERRED",
            settlement="CASH",
            current_commodity="امام",
            inferred_commodity="ربع بهار",
            agrees_with_current=False,
            requires_user_confirmation=False,
            decision_reason="UNIQUE_HIGH_CONFIDENCE_RANGE_MATCH",
            bundle_version="bundle-v1",
            snapshot_version="snapshot-v1",
        )

        run_id = await persist_parser_observation(
            observation,
            requested_at=NOW,
            idempotency_material="opaque-test-event",
            session_factory=lambda: session,
        )

        self.assertTrue(session.committed)
        self.assertTrue(session.flushed)
        self.assertRegex(run_id, r"^[0-9a-f-]{36}$")
        run = next(
            value
            for value in session.added
            if isinstance(value, CoinIntelligenceShadowRun)
        )
        result = next(
            value
            for value in session.added
            if isinstance(value, CoinIntelligenceShadowParserResult)
        )
        self.assertRegex(run.idempotency_key, r"^[0-9a-f]{64}$")
        self.assertFalse(run.training_eligible)
        self.assertEqual(result.current_commodity, "امام")
        self.assertEqual(result.disagreement_fields, ["commodity"])
        self.assertEqual(result.candidate_payload, {})

    async def test_rate_prediction_is_explicitly_non_authoritative(self) -> None:
        session = FakeAsyncSession()
        prediction = RateShadowPrediction(
            status="ESTIMATED",
            commodity="امام",
            settlement="CASH",
            trade_form="PHYSICAL",
            center_project_price=184_000,
            lower_project_price=182_500,
            upper_project_price=185_500,
            confidence_label="MEDIUM",
            method="TEST_METHOD",
            decision_reason="VALIDATED_CANONICAL_RATE",
            anchor_kind="STRUCTURAL",
            anchor_age_seconds=900,
            bundle_version="bundle-v1",
            feature_schema_version="features-v1",
            snapshot_version="snapshot-v1",
            evidence={
                "schema_version": "features-v1",
                "sources": {
                    "melted_gold": {
                        "status": "OBSERVED",
                        "age_seconds": 30,
                        "selection": "PHYSICAL",
                    },
                    "xauusd": {
                        "status": "NO_DATA",
                        "age_seconds": None,
                    },
                },
            },
        )

        await persist_rate_prediction(
            prediction,
            commodity_id=7,
            requested_at=NOW,
            source_surface="project_foreign",
            subject_kind="OFFER",
            subject_fingerprint=shadow_subject_fingerprint("OFFER", "abc"),
            event_idempotency_key="offer:abc",
            observed_project_price=184_100,
            session_factory=lambda: session,
        )

        row = next(
            value
            for value in session.added
            if isinstance(value, CoinIntelligenceShadowPrediction)
        )
        self.assertFalse(row.is_authoritative)
        self.assertEqual(row.center_project_price, 184_000)
        self.assertEqual(
            row.diagnostics,
            {"observed_offer_price_project": 184_100},
        )
        feature = next(
            value
            for value in session.added
            if isinstance(value, CoinIntelligenceShadowFeatureSnapshot)
        )
        self.assertRegex(feature.payload_sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(feature.missing_fields, ["xauusd"])
        self.assertEqual(feature.source_ages["melted_gold"], 30)

    async def test_duplicate_business_event_returns_existing_run(self) -> None:
        session = FakeAsyncSession(scalar_result="existing-run-id")
        prediction = RateShadowPrediction(
            status="NO_MARKET_DATA",
            commodity="امام",
            settlement="CASH",
            trade_form="PHYSICAL",
            center_project_price=None,
            lower_project_price=None,
            upper_project_price=None,
            confidence_label=None,
            method=None,
            decision_reason="NO_DATA",
            anchor_kind=None,
            anchor_age_seconds=None,
            bundle_version="newer-bundle",
            feature_schema_version="features-v1",
            snapshot_version="newer-snapshot",
        )

        run_id = await persist_rate_prediction(
            prediction,
            commodity_id=7,
            requested_at=NOW,
            source_surface="project_foreign",
            subject_kind="OFFER",
            subject_fingerprint=shadow_subject_fingerprint("OFFER", "abc"),
            event_idempotency_key="offer:abc",
            session_factory=lambda: session,
        )

        self.assertEqual(run_id, "existing-run-id")
        self.assertFalse(session.added)
        self.assertFalse(session.committed)

    def test_shadow_tables_are_local_internal_bookkeeping(self) -> None:
        for name in {
            "coin_intelligence_shadow_runs",
            "coin_intelligence_shadow_feature_snapshots",
            "coin_intelligence_shadow_predictions",
            "coin_intelligence_shadow_parser_results",
            "coin_intelligence_shadow_outcomes",
        }:
            with self.subTest(name=name):
                self.assertEqual(
                    get_sync_registry_entry(name).policy,
                    SyncPolicy.INTERNAL_BOOKKEEPING,
                )

    def test_committed_offer_dispatches_only_opaque_local_identity(self) -> None:
        offer = Offer(
            id=17,
            offer_public_id="must-not-be-captured",
            offer_type=OfferType.BUY,
            commodity_id=4,
            quantity=5,
            remaining_quantity=5,
            price=184_000,
            status=OfferStatus.ACTIVE,
        )
        session = FakeSyncSession(new=(offer,))
        with patch.object(
            project_events.settings,
            "coin_intelligence_shadow_enabled",
            True,
        ), patch.object(
            project_events.settings,
            "coin_intelligence_shadow_project_events_enabled",
            True,
        ):
            project_events._capture_after_flush(session, None)

        captured = session.info[
            "_coin_intelligence_shadow_project_events"
        ]
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].kind, "OFFER")
        self.assertEqual(captured[0].local_id, 17)
        self.assertFalse(
            any(
                re.search("must-not-be-captured", repr(value))
                for value in captured
            )
        )

        with patch(
            "core.market_intelligence.shadow.schedule_project_market_event"
        ) as schedule:
            project_events._dispatch_after_commit(session)

        dispatched = schedule.call_args.args[0]
        self.assertEqual(dispatched.kind, captured[0].kind)
        self.assertEqual(dispatched.local_id, captured[0].local_id)
        self.assertIsNotNone(dispatched.observed_after_commit_at_utc)
        self.assertIsNotNone(dispatched.observed_after_commit_at_utc.tzinfo)
        self.assertNotIn(
            "_coin_intelligence_shadow_project_events",
            session.info,
        )


if __name__ == "__main__":
    unittest.main()
