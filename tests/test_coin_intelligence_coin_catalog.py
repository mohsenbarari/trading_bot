"""Catalog-boundary tests for product-neutral coin inference."""

from __future__ import annotations

from types import SimpleNamespace
import unittest

from core.market_intelligence.coin_catalog import (
    resolve_coin_inference_against_catalog,
    resolve_coin_inference_edit_candidates,
)
from core.market_intelligence.coin_inference import (
    CoinCommodityCandidate,
    CoinCommodityInference,
)


class _Result:
    def __init__(self, rows: list[object]):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _CatalogDB:
    def __init__(self, rows_by_name: dict[str, list[object]]):
        self.rows_by_name = rows_by_name
        self.executed = []

    async def execute(self, statement):
        self.executed.append(statement)
        # The bind keeps this fake independent of SQLAlchemy's dialect rendering.
        name = statement.compile().params["name_1"]
        return _Result(self.rows_by_name.get(name, []))


def candidate(code: str, name: str) -> CoinCommodityCandidate:
    return CoinCommodityCandidate(
        commodity_code=code,
        commodity_name=name,
        center_project_price=186_900,
        lower_project_price=185_500,
        upper_project_price=188_300,
        confidence="HIGH",
        distance_to_center_relative=0.000535,
    )


def inference(status: str = "AUTO_SELECT", *candidates: CoinCommodityCandidate) -> CoinCommodityInference:
    return CoinCommodityInference(
        status=status,
        settlement_term="TOMORROW",
        candidates=tuple(candidates or (candidate("IMAM", "امام"),)),
        snapshot_generated_at_utc="2026-08-04T10:00:00Z",
        snapshot_receipt="a" * 64,
        reason=None,
    )


class CoinCatalogResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_select_maps_only_the_exact_canonical_catalog_name(self) -> None:
        db = _CatalogDB({"امام": [SimpleNamespace(id=71, name="امام")]})
        result = await resolve_coin_inference_against_catalog(db, inference())
        self.assertEqual((result.status, result.candidates[0].commodity_id, result.candidates[0].commodity_name), ("AUTO_SELECT", 71, "امام"))

    async def test_confirm_requires_every_candidate_to_be_available_by_exact_name(self) -> None:
        db = _CatalogDB({"امام": [SimpleNamespace(id=71, name="امام")]})
        result = await resolve_coin_inference_against_catalog(
            db,
            inference("CONFIRM", candidate("IMAM", "امام"), candidate("BAHAR", "بهار")),
        )
        self.assertEqual((result.status, result.reason, result.candidates), ("ABSTAIN", "CATALOG_CANONICAL_NAME_UNAVAILABLE", ()))

    async def test_alias_like_or_duplicate_row_never_maps(self) -> None:
        alias_like = _CatalogDB({"امام": [SimpleNamespace(id=71, name="سکه امامی")]})
        duplicate = _CatalogDB({"امام": [SimpleNamespace(id=71, name="امام"), SimpleNamespace(id=72, name="امام")]})
        alias_result = await resolve_coin_inference_against_catalog(alias_like, inference())
        duplicate_result = await resolve_coin_inference_against_catalog(duplicate, inference())
        self.assertEqual(alias_result.status, "ABSTAIN")
        self.assertEqual(duplicate_result.status, "ABSTAIN")

    async def test_abstained_ranker_result_never_queries_catalog(self) -> None:
        db = _CatalogDB({"امام": [SimpleNamespace(id=71, name="امام")]})
        source = CoinCommodityInference(
            status="ABSTAIN",
            settlement_term="CASH",
            candidates=(),
            snapshot_generated_at_utc=None,
            snapshot_receipt=None,
            reason="SNAPSHOT_STALE_OR_FUTURE",
        )
        result = await resolve_coin_inference_against_catalog(db, source)
        self.assertEqual((result.status, result.reason, db.executed), ("ABSTAIN", "SNAPSHOT_STALE_OR_FUTURE", []))

    async def test_edit_choices_are_existing_same_family_canonical_commodities(self) -> None:
        db = _CatalogDB(
            {
                "ربع بهار": [SimpleNamespace(id=73, name="ربع بهار")],
                "ربع تاریخ پایین": [SimpleNamespace(id=75, name="ربع تاریخ پایین")],
            }
        )
        decision = await resolve_coin_inference_against_catalog(
            db,
            inference(
                "CONFIRM",
                candidate("QUARTER_BAHAR", "ربع بهار"),
            ),
        )

        choices = await resolve_coin_inference_edit_candidates(db, decision)

        self.assertEqual(
            [(item.commodity_id, item.commodity_code, item.commodity_name) for item in choices],
            [
                (73, "QUARTER_BAHAR", "ربع بهار"),
                (75, "QUARTER_LOW_DATE", "ربع تاریخ پایین"),
            ],
        )

    async def test_low_date_edit_scope_never_reintroduces_normal_date_variant(self) -> None:
        db = _CatalogDB(
            {
                "نیم بهار": [SimpleNamespace(id=74, name="نیم بهار")],
                "نیم تاریخ پایین": [SimpleNamespace(id=76, name="نیم تاریخ پایین")],
            }
        )
        decision = await resolve_coin_inference_against_catalog(
            db,
            inference(
                "CONFIRM",
                candidate("HALF_LOW_DATE", "نیم تاریخ پایین"),
            ),
        )

        choices = await resolve_coin_inference_edit_candidates(
            db,
            decision,
            candidate_scope="LOW_DATE_ONLY",
        )

        self.assertEqual(
            [(item.commodity_code, item.commodity_name) for item in choices],
            [("HALF_LOW_DATE", "نیم تاریخ پایین")],
        )


if __name__ == "__main__":
    unittest.main()
