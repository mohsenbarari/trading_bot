"""Causal, same-book commodity validation tests for private coin groups."""

from __future__ import annotations

import unittest

from core.market_intelligence.coin_group_resolution import (
    CoinPriceAnchor,
    resolve_coin_group_offers,
    resolved_coin_group_observations,
)
from core.market_intelligence.coin_groups import CoinGroupMessageInput


def source(text: str, **changes: object) -> CoinGroupMessageInput:
    values: dict[str, object] = {
        "group_number": 1,
        "source_event_id": "private-message-7",
        "published_at_utc": "2026-08-04T10:10:00Z",
        "available_at_utc": "2026-08-04T10:10:05Z",
        "text": text,
    }
    values.update(changes)
    return CoinGroupMessageInput(**values)  # type: ignore[arg-type]


def anchor(code: str, price: int, at: str, **changes: object) -> CoinPriceAnchor:
    values: dict[str, object] = {
        "commodity_code": code,
        "price_project_thousand_toman": price,
        "event_time_utc": at,
        "available_at_utc": at,
        "settlement_term": "TOMORROW",
        "trade_form": "PHYSICAL",
    }
    values.update(changes)
    return CoinPriceAnchor(**values)  # type: ignore[arg-type]


class CoinGroupResolutionTests(unittest.TestCase):
    def test_explicit_name_requires_and_passes_same_book_prior_context(self) -> None:
        result = resolve_coin_group_offers(
            source("امام فروش 186,900 / 5 تا"),
            anchors=(
                anchor("IMAM", 186_700, "2026-08-04T10:08:00Z"),
                anchor("IMAM", 186_800, "2026-08-04T10:09:00Z"),
                anchor("BAHAR", 181_700, "2026-08-04T10:08:30Z"),
                anchor("BAHAR", 181_800, "2026-08-04T10:09:30Z"),
            ),
        )[0]
        assert (result.commodity_code, result.quality_state) == ("IMAM", "ELIGIBLE")
        assert result.anchor_count == 2

    def test_named_typo_is_rejected_not_silently_relabelled(self) -> None:
        result = resolve_coin_group_offers(
            source("امام خرید 181,900 / 5 تا"),
            anchors=(
                anchor("IMAM", 186_500, "2026-08-04T10:08:00Z"),
                anchor("IMAM", 186_700, "2026-08-04T10:09:00Z"),
                anchor("BAHAR", 181_700, "2026-08-04T10:08:30Z"),
                anchor("BAHAR", 181_800, "2026-08-04T10:09:30Z"),
            ),
        )[0]
        assert (result.commodity_code, result.quality_state) == ("IMAM", "REJECTED")
        assert "CONFLICTS" in result.resolution_reason

    def test_unnamed_offer_can_be_resolved_only_by_decisive_prior_context(self) -> None:
        result = resolve_coin_group_offers(
            source("خرید 181,900 / 5 تا فردایی"),
            anchors=(
                anchor("BAHAR", 181_700, "2026-08-04T10:08:00Z"),
                anchor("BAHAR", 181_800, "2026-08-04T10:09:00Z"),
                anchor("IMAM", 186_500, "2026-08-04T10:08:30Z"),
                anchor("IMAM", 186_700, "2026-08-04T10:09:30Z"),
            ),
        )[0]
        assert (result.commodity_code, result.quality_state) == ("BAHAR", "ELIGIBLE")
        assert result.resolution_reason.startswith("UNNAMED")

    def test_future_wrong_book_or_too_thin_anchor_cannot_validate(self) -> None:
        result = resolve_coin_group_offers(
            source("ربع بهار فروش 52,300 / 5 تا"),
            anchors=(
                anchor("QUARTER_BAHAR", 52_250, "2026-08-04T10:11:00Z"),
                anchor("QUARTER_BAHAR", 52_300, "2026-08-04T10:09:00Z", settlement_term="CASH"),
                anchor("QUARTER_BAHAR", 52_300, "2026-08-04T10:09:30Z"),
            ),
        )[0]
        assert result.quality_state == "PENDING_REVIEW"
        assert result.anchor_count == 0

    def test_resolved_fact_waits_until_reconciliation_is_available_and_has_no_private_fields(self) -> None:
        observations = resolved_coin_group_observations(
            source("بهار فروش 181,900 / 5 تا"),
            anchors=(
                anchor("BAHAR", 181_700, "2026-08-04T10:08:00Z"),
                anchor("BAHAR", 181_800, "2026-08-04T10:09:00Z"),
            ),
            resolution_available_at_utc="2026-08-04T10:12:00Z",
        )
        observation = observations[0].normalized()
        assert (observation.quality_state, observation.available_at_utc) == ("ELIGIBLE", "2026-08-04T10:12:00Z")
        assert "private-message" not in observation.attributes_json
        assert "raw" not in observation.attributes_json
