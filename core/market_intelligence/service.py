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
    RangeSnapshotProvider,
    ShadowObservation,
    SnapshotUnavailableError,
)
from core.market_intelligence.ranker import (
    CommodityRanker,
    estimator_state_snapshot,
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
