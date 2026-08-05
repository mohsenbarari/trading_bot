"""Contracts for privacy-minimized P7 accepted-choice telemetry."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.market_intelligence.coin_catalog import CatalogCoinCommodityCandidate
from core.market_intelligence.coin_inference_outcome import (
    CoinInferenceAcceptedSelection,
    append_coin_inference_accepted_selection,
)
from models.coin_intelligence_inference_audit import CoinIntelligenceInferenceAudit
from models.coin_intelligence_inference_outcome import CoinIntelligenceInferenceOutcome


def candidate(commodity_id: int = 71) -> CatalogCoinCommodityCandidate:
    return CatalogCoinCommodityCandidate(
        commodity_id=commodity_id,
        commodity_code="IMAM",
        commodity_name="امام",
        center_project_price=186_900,
        lower_project_price=185_500,
        upper_project_price=188_300,
        confidence="HIGH",
        distance_to_center_relative=0.0,
    )


class _Result:
    def __init__(self, row=None):
        self.row = row

    def scalar_one_or_none(self):
        return self.row


class _DB:
    def __init__(self, existing=None):
        self.existing = existing
        self.added = []
        self.flushes = 0

    async def execute(self, _statement):
        return _Result(self.existing)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flushes += 1


def audit(*, status="AUTO_SELECT", selected=71, surface="WEBAPP"):
    return SimpleNamespace(
        source_surface=surface,
        decision_status=status,
        selected_commodity_id=selected if status == "AUTO_SELECT" else None,
    )


class CoinInferenceOutcomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_accepted_selection_keeps_only_opaque_and_canonical_fields(self) -> None:
        db = _DB()
        with patch(
            "core.market_intelligence.coin_inference_outcome.load_coin_inference_audit",
            new=AsyncMock(return_value=audit()),
        ):
            row = await append_coin_inference_accepted_selection(
                db,
                CoinInferenceAcceptedSelection("a" * 64, "WEBAPP", candidate()),
            )
        self.assertIs(row, db.added[0])
        self.assertEqual(
            (row.source_surface, row.outcome_kind, row.selected_commodity_id, db.flushes),
            ("WEBAPP", "OFFER_ACCEPTED_SELECTION", 71, 1),
        )
        self.assertFalse(
            any(
                token in name
                for name in row.__table__.columns.keys()
                for token in ("raw", "text", "user", "telegram", "message", "note", "offer")
            )
        )

    async def test_exact_retry_is_idempotent(self) -> None:
        existing = SimpleNamespace(
            decision_key="a" * 64,
            source_surface="WEBAPP",
            outcome_kind="OFFER_ACCEPTED_SELECTION",
            selected_commodity_id=71,
            selected_commodity_code="IMAM",
            selected_commodity_name="امام",
        )
        db = _DB(existing)
        with patch(
            "core.market_intelligence.coin_inference_outcome.load_coin_inference_audit",
            new=AsyncMock(return_value=audit()),
        ):
            row = await append_coin_inference_accepted_selection(
                db,
                CoinInferenceAcceptedSelection("a" * 64, "WEBAPP", candidate()),
            )
        self.assertIs(row, existing)
        self.assertEqual((db.added, db.flushes), ([], 0))

    async def test_auto_selection_cannot_be_rewritten_to_another_candidate(self) -> None:
        with patch(
            "core.market_intelligence.coin_inference_outcome.load_coin_inference_audit",
            new=AsyncMock(return_value=audit(selected=71)),
        ):
            with self.assertRaisesRegex(ValueError, "auto_choice_mismatch"):
                await append_coin_inference_accepted_selection(
                    _DB(),
                    CoinInferenceAcceptedSelection("a" * 64, "WEBAPP", candidate(72)),
                )

    async def test_unknown_or_nonselectable_receipts_are_rejected(self) -> None:
        for receipt, expected in ((None, "decision_unknown"), (audit(status="ABSTAIN"), "not_selectable")):
            with self.subTest(expected=expected), patch(
                "core.market_intelligence.coin_inference_outcome.load_coin_inference_audit",
                new=AsyncMock(return_value=receipt),
            ):
                with self.assertRaisesRegex(ValueError, expected):
                    await append_coin_inference_accepted_selection(
                        _DB(),
                        CoinInferenceAcceptedSelection("a" * 64, "WEBAPP", candidate()),
                    )


class CoinInferenceOutcomeStorageTests(unittest.TestCase):
    def test_migration_is_additive_and_protects_append_only_history(self) -> None:
        migration = (
            Path(__file__).resolve().parents[1]
            / "migrations/versions/d4e8a2b6c1f0_add_coin_intelligence_inference_outcomes.py"
        ).read_text(encoding="utf-8")
        self.assertIn('down_revision: Union[str, Sequence[str], None] = "d3f7a1c9e4b5"', migration)
        self.assertIn("ForeignKeyConstraint", migration)
        self.assertIn("ondelete=\"RESTRICT\"", migration)
        self.assertIn("trg_coin_intelligence_inference_outcome_immutable", migration)
        self.assertIn("must be archived before downgrade", migration)

    def test_schema_is_append_only_and_rejects_invalid_rows(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        CoinIntelligenceInferenceAudit.__table__.create(engine)
        CoinIntelligenceInferenceOutcome.__table__.create(engine)
        session = Session(engine)
        try:
            session.add(
                CoinIntelligenceInferenceOutcome(
                    outcome_key="b" * 64,
                    decision_key="a" * 64,
                    source_surface="WEBAPP",
                    outcome_kind="OFFER_ACCEPTED_SELECTION",
                    selected_commodity_id=0,
                    selected_commodity_code="IMAM",
                    selected_commodity_name="امام",
                )
            )
            with self.assertRaises(IntegrityError):
                session.commit()
        finally:
            session.rollback()
            session.close()
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
