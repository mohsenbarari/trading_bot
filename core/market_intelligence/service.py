"""Local Shadow inference service shared by the WebApp API and Telegram bot."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from core.market_intelligence.bundle import (
    BundleValidationError,
    LoadedRuntimeBundle,
    load_runtime_bundle,
)
from core.market_intelligence.contracts import (
    RateShadowPrediction,
    RangeSnapshotProvider,
    ShadowObservation,
    SnapshotUnavailableError,
)
from core.market_intelligence.ranker import (
    CommodityRanker,
    estimator_state_snapshot,
    parse_time,
)


SETTLEMENT_TO_MODEL = {
    "cash": "CASH",
    "tomorrow": "TOMORROW",
    "CASH": "CASH",
    "TOMORROW": "TOMORROW",
}


class CoinIntelligenceShadowService:
    """Observe model decisions without returning a replacement commodity ID."""

    def __init__(
        self,
        *,
        snapshot_provider: RangeSnapshotProvider,
        bundle_path: str | Path | None = None,
        bundle: LoadedRuntimeBundle | None = None,
    ) -> None:
        self.snapshot_provider = snapshot_provider
        self._bundle_path = bundle_path
        self._bundle = bundle

    def _loaded_bundle(self) -> LoadedRuntimeBundle:
        if self._bundle is None:
            self._bundle = load_runtime_bundle(self._bundle_path)
        return self._bundle

    def observe_implicit_commodity(
        self,
        *,
        price: int,
        settlement: str,
        trade_form: str = "PHYSICAL",
        current_commodity: str,
        now: datetime | None = None,
    ) -> ShadowObservation:
        model_settlement = SETTLEMENT_TO_MODEL.get(str(settlement))
        if model_settlement is None:
            return ShadowObservation(
                status="UNSUPPORTED_MARKET_DIMENSION",
                settlement=str(settlement),
                current_commodity=current_commodity,
                inferred_commodity=None,
                agrees_with_current=None,
                requires_user_confirmation=True,
                decision_reason="UNSUPPORTED_SETTLEMENT",
                bundle_version=None,
                snapshot_version=None,
            )
        normalized_trade_form = str(trade_form).upper()
        if normalized_trade_form not in {"PHYSICAL", "PAPER"}:
            return ShadowObservation(
                status="UNSUPPORTED_MARKET_DIMENSION",
                settlement=model_settlement,
                current_commodity=current_commodity,
                inferred_commodity=None,
                agrees_with_current=None,
                requires_user_confirmation=True,
                decision_reason="UNSUPPORTED_TRADE_FORM",
                bundle_version=None,
                snapshot_version=None,
            )
        # The currently published rate bands are explicitly physical.  Keep
        # the request dimension so a future paper model can use it, but never
        # score a paper offer against a physical band in the meantime.
        if normalized_trade_form == "PAPER":
            return ShadowObservation(
                status="NO_MARKET_DATA",
                settlement=model_settlement,
                current_commodity=current_commodity,
                inferred_commodity=None,
                agrees_with_current=None,
                requires_user_confirmation=True,
                decision_reason="PAPER_RATE_SNAPSHOT_NOT_AVAILABLE",
                bundle_version=None,
                snapshot_version=None,
            )
        try:
            bundle = self._loaded_bundle()
        except (BundleValidationError, OSError, ValueError):
            return ShadowObservation(
                status="BUNDLE_UNAVAILABLE",
                settlement=model_settlement,
                current_commodity=current_commodity,
                inferred_commodity=None,
                agrees_with_current=None,
                requires_user_confirmation=True,
                decision_reason="BUNDLE_VALIDATION_FAILED",
                bundle_version=None,
                snapshot_version=None,
            )
        try:
            state = self.snapshot_provider.load()
            snapshot = estimator_state_snapshot(
                state,
                settlement=model_settlement,
                trade_form=normalized_trade_form,
                model_version=bundle.model_sha256,
                feature_schema_version=bundle.feature_schema_version,
                snapshot_version=(
                    f"{bundle.bundle_version}:"
                    f"{state.get('generated_at_utc')}:"
                    f"{model_settlement}:{normalized_trade_form}"
                ),
            )
        except (SnapshotUnavailableError, TypeError, ValueError, KeyError):
            return ShadowObservation(
                status="SNAPSHOT_UNAVAILABLE",
                settlement=model_settlement,
                current_commodity=current_commodity,
                inferred_commodity=None,
                agrees_with_current=None,
                requires_user_confirmation=True,
                decision_reason="LOCAL_RANGE_SNAPSHOT_UNAVAILABLE",
                bundle_version=bundle.bundle_version,
                snapshot_version=None,
            )
        result = CommodityRanker(bundle.ranker_policy).infer(
            price,
            price_unit="PROJECT_THOUSAND_TOMAN",
            settlement=model_settlement,
            trade_form=normalized_trade_form,
            snapshot=snapshot,
            now=now or datetime.now(timezone.utc),
            allowed_commodities=bundle.canonical_commodity_names,
        )
        inferred = result.inferred_commodity
        return ShadowObservation(
            status=result.status,
            settlement=model_settlement,
            current_commodity=current_commodity,
            inferred_commodity=inferred,
            agrees_with_current=(
                inferred == current_commodity
                if inferred is not None
                else None
            ),
            requires_user_confirmation=result.requires_user_confirmation,
            decision_reason=result.decision_reason,
            bundle_version=bundle.bundle_version,
            snapshot_version=result.input_snapshot_version,
        )

    def estimate_canonical_rate(
        self,
        *,
        commodity: str,
        settlement: str,
        trade_form: str = "PHYSICAL",
        now: datetime | None = None,
    ) -> RateShadowPrediction:
        """Read one validated range without making it authoritative."""

        requested_at = now or datetime.now(timezone.utc)
        model_settlement = SETTLEMENT_TO_MODEL.get(str(settlement))
        normalized_trade_form = str(trade_form).upper()

        def unavailable(
            status: str,
            reason: str,
            *,
            bundle_version: str | None = None,
            feature_schema_version: str | None = None,
            snapshot_version: str | None = None,
        ) -> RateShadowPrediction:
            return RateShadowPrediction(
                status=status,
                commodity=str(commodity),
                settlement=model_settlement or str(settlement),
                trade_form=normalized_trade_form,
                center_project_price=None,
                lower_project_price=None,
                upper_project_price=None,
                confidence_label=None,
                method=None,
                decision_reason=reason,
                anchor_kind=None,
                anchor_age_seconds=None,
                bundle_version=bundle_version,
                feature_schema_version=feature_schema_version,
                snapshot_version=snapshot_version,
            )

        if model_settlement is None:
            return unavailable(
                "UNSUPPORTED_MARKET_DIMENSION",
                "UNSUPPORTED_SETTLEMENT",
            )
        if normalized_trade_form != "PHYSICAL":
            return unavailable(
                "NO_MARKET_DATA",
                "PAPER_RATE_SNAPSHOT_NOT_AVAILABLE",
            )
        try:
            bundle = self._loaded_bundle()
        except (BundleValidationError, OSError, ValueError):
            return unavailable(
                "BUNDLE_UNAVAILABLE",
                "BUNDLE_VALIDATION_FAILED",
            )
        if commodity not in bundle.canonical_commodity_names:
            return unavailable(
                "NO_MARKET_DATA",
                "NON_CANONICAL_COMMODITY",
                bundle_version=bundle.bundle_version,
                feature_schema_version=bundle.feature_schema_version,
            )
        snapshot_version = None
        try:
            state = self.snapshot_provider.load()
            snapshot_version = (
                f"{bundle.bundle_version}:"
                f"{state.get('generated_at_utc')}:"
                f"{model_settlement}:{normalized_trade_form}"
            )
            snapshot = estimator_state_snapshot(
                state,
                settlement=model_settlement,
                trade_form=normalized_trade_form,
                model_version=bundle.model_sha256,
                feature_schema_version=bundle.feature_schema_version,
                snapshot_version=snapshot_version,
            )
            generated_at = parse_time(str(snapshot["generated_at_utc"]))
        except (
            SnapshotUnavailableError,
            TypeError,
            ValueError,
            KeyError,
        ):
            return unavailable(
                "SNAPSHOT_UNAVAILABLE",
                "LOCAL_RANGE_SNAPSHOT_UNAVAILABLE",
                bundle_version=bundle.bundle_version,
                feature_schema_version=bundle.feature_schema_version,
                snapshot_version=snapshot_version,
            )
        freshness_seconds = (requested_at - generated_at).total_seconds()
        if (
            freshness_seconds < 0
            or freshness_seconds
            > bundle.ranker_policy.maximum_snapshot_age_seconds
        ):
            return unavailable(
                "STALE_INPUT",
                "LOCAL_RANGE_SNAPSHOT_STALE",
                bundle_version=bundle.bundle_version,
                feature_schema_version=bundle.feature_schema_version,
                snapshot_version=snapshot_version,
            )
        rate = next(
            (
                item
                for item in snapshot.get("rates") or ()
                if str(item.get("commodity_name")) == commodity
            ),
            None,
        )
        if rate is None:
            return unavailable(
                "NO_MARKET_DATA",
                "CANONICAL_RATE_NOT_FOUND",
                bundle_version=bundle.bundle_version,
                feature_schema_version=bundle.feature_schema_version,
                snapshot_version=snapshot_version,
            )
        try:
            center = int(rate["estimated_project_price"])
            tolerance = rate["tolerance"]
            lower = int(tolerance["lower_project_price"])
            upper = int(tolerance["upper_project_price"])
            if center <= 0 or lower <= 0 or upper <= 0 or not (
                lower <= center <= upper
            ):
                raise ValueError("invalid range")
        except (KeyError, TypeError, ValueError):
            return unavailable(
                "NO_MARKET_DATA",
                "CANONICAL_RATE_INVALID",
                bundle_version=bundle.bundle_version,
                feature_schema_version=bundle.feature_schema_version,
                snapshot_version=snapshot_version,
            )
        group_anchor = rate.get("group_offer_anchor") or {}
        transfer = rate.get("coin_anchor_transfer") or {}
        anchor_age_raw = (
            group_anchor.get("age_seconds")
            if group_anchor.get("age_seconds") is not None
            else transfer.get("anchor_age_seconds")
        )
        anchor_age = (
            max(0, int(float(anchor_age_raw)))
            if anchor_age_raw is not None
            else None
        )
        anchor_kind = (
            group_anchor.get("reference_source")
            or transfer.get("source")
            or transfer.get("method")
        )
        settlement_payload = (
            (state.get("settlements") or {}).get(model_settlement) or {}
        )
        source_inputs = settlement_payload.get("inputs") or {}
        compact_sources = {}
        for source_name in (
            "melted_gold",
            "generic_coin",
            "usd",
            "usdt",
            "xauusd",
        ):
            source = source_inputs.get(source_name) or {}
            observed_at = (
                source.get("last_event_utc")
                or source.get("last_observed_utc")
            )
            try:
                source_age = max(
                    0,
                    int(
                        (
                            requested_at - parse_time(str(observed_at))
                        ).total_seconds()
                    ),
                )
            except (TypeError, ValueError):
                source_age = None
            compact_sources[source_name] = {
                "status": str(source.get("status") or "NO_DATA"),
                "average_price": source.get("average_price"),
                "sample_count": int(source.get("sample_count") or 0),
                "observed_at_utc": observed_at,
                "age_seconds": source_age,
                "selection": source.get("selection"),
                "quote_kind": source.get("selected_quote_kind"),
                "trade_form": source.get("selected_trade_form"),
            }
        regime = settlement_payload.get("market_regime") or {}
        flow = settlement_payload.get("market_pressure") or {}
        evidence = {
            "schema_version": "COIN_SHADOW_FEATURE_EVIDENCE_V1",
            "as_of_utc": requested_at.isoformat(),
            "generated_at_utc": snapshot.get("generated_at_utc"),
            "commodity": str(commodity),
            "settlement": model_settlement,
            "trade_form": normalized_trade_form,
            "sources": compact_sources,
            "market_regime": {
                "status": regime.get("status"),
                "label": regime.get("regime"),
                "direction_score": regime.get("direction_score"),
                "confidence": regime.get("confidence"),
                "volatility_percent": regime.get("volatility_percent"),
            },
            "order_flow": {
                "status": flow.get("status"),
                "direction": flow.get("direction"),
                "estimator_score": flow.get("estimator_score"),
            },
            "rate": {
                "intrinsic_toman": rate.get("intrinsic_toman"),
                "bubble_ratio": rate.get("bubble_ratio"),
                "market_pressure_score": rate.get(
                    "market_pressure_score"
                ),
            },
        }
        return RateShadowPrediction(
            status="ESTIMATED",
            commodity=str(commodity),
            settlement=model_settlement,
            trade_form=normalized_trade_form,
            center_project_price=center,
            lower_project_price=lower,
            upper_project_price=upper,
            confidence_label=(
                str(rate.get("confidence"))
                if rate.get("confidence") is not None
                else None
            ),
            method=(
                str(rate.get("method"))
                if rate.get("method") is not None
                else None
            ),
            decision_reason="VALIDATED_CANONICAL_RATE",
            anchor_kind=str(anchor_kind) if anchor_kind is not None else None,
            anchor_age_seconds=anchor_age,
            bundle_version=bundle.bundle_version,
            feature_schema_version=bundle.feature_schema_version,
            snapshot_version=snapshot_version,
            evidence=evidence,
        )
