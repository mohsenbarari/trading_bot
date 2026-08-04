"""Append-only inference-audit contract tests, isolated from runtime routes."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.market_intelligence.coin_catalog import CatalogCoinCommodityCandidate, CatalogCoinCommodityInference
from core.market_intelligence.coin_inference_audit import (
    CoinInferenceAuditCommand,
    CoinInferenceAuditConflictError,
    append_coin_inference_audit,
)
from models.coin_intelligence_inference_audit import CoinIntelligenceInferenceAudit


class _Result:
    def __init__(self, row):
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


def auto() -> CatalogCoinCommodityInference:
    return CatalogCoinCommodityInference(
        status="AUTO_SELECT",
        settlement_term="TOMORROW",
        candidates=(
            CatalogCoinCommodityCandidate(
                commodity_id=71,
                commodity_code="IMAM",
                commodity_name="امام",
                center_project_price=186_900,
                lower_project_price=185_500,
                upper_project_price=188_300,
                confidence="HIGH",
                distance_to_center_relative=0.000535,
            ),
        ),
        snapshot_generated_at_utc="2026-08-04T10:00:00Z",
        snapshot_receipt="b" * 64,
        reason=None,
    )


def command(decision=None, *, price=186_800) -> CoinInferenceAuditCommand:
    return CoinInferenceAuditCommand(
        decision_key="a" * 64,
        source_surface="WEBAPP",
        submitted_project_price=price,
        decision=decision or auto(),
    )


class CoinInferenceAuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_select_records_only_minimal_economic_audit_fields(self) -> None:
        db = _DB()
        result = await append_coin_inference_audit(db, command())
        self.assertIs(result, db.added[0])
        self.assertEqual((result.decision_status, result.selected_commodity_id, result.selected_commodity_name), ("AUTO_SELECT", 71, "امام"))
        self.assertEqual((result.inference_version, result.catalog_resolution_version, db.flushes), ("coin-inference-v2", "coin-catalog-resolution-v1", 1))
        self.assertEqual(result.candidate_scope, "ALL")
        self.assertFalse(any(token in name for name in result.__table__.columns.keys() for token in ("raw", "text", "user", "telegram", "message", "note")))

    async def test_exact_idempotent_replay_returns_existing_row_without_write(self) -> None:
        source = auto()
        existing = SimpleNamespace(
            source_surface="WEBAPP", decision_status="AUTO_SELECT", reason_code=None,
            settlement_term="TOMORROW", candidate_scope="ALL", submitted_project_price=186_800, candidate_count=1,
            selected_commodity_id=71, selected_commodity_code="IMAM", selected_commodity_name="امام",
            inference_version="coin-inference-v2", catalog_resolution_version="coin-catalog-resolution-v1",
            snapshot_receipt="b" * 64, snapshot_generated_at_utc=datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc),
        )
        db = _DB(existing)
        result = await append_coin_inference_audit(db, command(source))
        self.assertIs(result, existing)
        self.assertEqual((db.added, db.flushes), ([], 0))

    async def test_idempotency_key_cannot_be_reused_for_another_price(self) -> None:
        existing = SimpleNamespace(
            source_surface="WEBAPP", decision_status="AUTO_SELECT", reason_code=None,
            settlement_term="TOMORROW", candidate_scope="ALL", submitted_project_price=186_800, candidate_count=1,
            selected_commodity_id=71, selected_commodity_code="IMAM", selected_commodity_name="امام",
            inference_version="coin-inference-v2", catalog_resolution_version="coin-catalog-resolution-v1",
            snapshot_receipt="b" * 64, snapshot_generated_at_utc=datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc),
        )
        with self.assertRaises(CoinInferenceAuditConflictError):
            await append_coin_inference_audit(_DB(existing), command(price=186_900))

    async def test_confirm_and_abstain_have_no_hidden_selected_commodity(self) -> None:
        confirm = CatalogCoinCommodityInference(
            status="CONFIRM", settlement_term="CASH", candidates=auto().candidates,
            snapshot_generated_at_utc="2026-08-04T10:00:00Z", snapshot_receipt="b" * 64,
            reason="MULTIPLE_OR_LOW_CONFIDENCE_CANDIDATES",
        )
        abstain = CatalogCoinCommodityInference(
            status="ABSTAIN", settlement_term="CASH", candidates=(), snapshot_generated_at_utc=None,
            snapshot_receipt=None, reason="PRICE_OUTSIDE_PUBLISHED_RANGES",
        )
        confirm_db, abstain_db = _DB(), _DB()
        confirm_row = await append_coin_inference_audit(confirm_db, command(confirm, price=180_900))
        abstain_row = await append_coin_inference_audit(abstain_db, CoinInferenceAuditCommand("c" * 64, "TELEGRAM_BOT", 180_900, abstain))
        self.assertIsNone(confirm_row.selected_commodity_id)
        self.assertIsNone(abstain_row.selected_commodity_id)

    async def test_audit_refuses_noncanonical_or_private_reason_data(self) -> None:
        invalid_candidate = CatalogCoinCommodityInference(
            status="AUTO_SELECT", settlement_term="CASH", candidates=(
                CatalogCoinCommodityCandidate(
                    commodity_id=71, commodity_code="IMAM", commodity_name="not canonical",
                    center_project_price=186_900, lower_project_price=185_500,
                    upper_project_price=188_300, confidence="HIGH", distance_to_center_relative=0.0,
                ),
            ), snapshot_generated_at_utc="2026-08-04T10:00:00Z", snapshot_receipt="b" * 64, reason=None,
        )
        private_reason = CatalogCoinCommodityInference(
            status="ABSTAIN", settlement_term="CASH", candidates=(),
            snapshot_generated_at_utc=None, snapshot_receipt=None, reason="raw user text",
        )
        with self.assertRaisesRegex(ValueError, "catalog_candidate"):
            await append_coin_inference_audit(_DB(), command(invalid_candidate, price=186_800))
        with self.assertRaisesRegex(ValueError, "reason_invalid"):
            await append_coin_inference_audit(
                _DB(), CoinInferenceAuditCommand("d" * 64, "INTERNAL", 186_800, private_reason)
            )


class CoinInferenceAuditStorageTests(unittest.TestCase):
    def test_table_rejects_an_auto_decision_without_a_selected_commodity(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        CoinIntelligenceInferenceAudit.__table__.create(engine)
        session = Session(engine)
        try:
            session.add(
                CoinIntelligenceInferenceAudit(
                    decision_key="e" * 64,
                    source_surface="WEBAPP",
                    decision_status="AUTO_SELECT",
                    settlement_term="CASH",
                    candidate_scope="ALL",
                    submitted_project_price=186_800,
                    candidate_count=1,
                    inference_version="coin-inference-v2",
                    catalog_resolution_version="coin-catalog-resolution-v1",
                    snapshot_receipt="b" * 64,
                    snapshot_generated_at_utc=datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc),
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
