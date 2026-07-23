"""Feature-flagged adapter invoked by the existing shared offer parser."""

from __future__ import annotations

import asyncio
from functools import lru_cache

from core.config import settings
from core.market_intelligence.contracts import ShadowObservation
from core.market_intelligence.observability import record_shadow_observation
from core.market_intelligence.service import CoinIntelligenceShadowService
from core.market_intelligence.snapshot import AtomicJsonSnapshotProvider


@lru_cache(maxsize=1)
def _configured_service() -> CoinIntelligenceShadowService | None:
    snapshot_path = str(
        settings.coin_intelligence_snapshot_path or ""
    ).strip()
    if not settings.coin_intelligence_shadow_enabled or not snapshot_path:
        return None
    bundle_path = str(settings.coin_intelligence_bundle_path or "").strip()
    return CoinIntelligenceShadowService(
        snapshot_provider=AtomicJsonSnapshotProvider(snapshot_path),
        bundle_path=bundle_path or None,
    )


async def observe_implicit_commodity_shadow(
    *,
    price: int,
    settlement: str,
    current_commodity: str,
) -> None:
    """Observe safely; every failure is isolated from offer parsing."""

    try:
        service = _configured_service()
        if service is None:
            return
        observation = await asyncio.to_thread(
            service.observe_implicit_commodity,
            price=price,
            settlement=settlement,
            current_commodity=current_commodity,
        )
        record_shadow_observation(observation)
    except Exception:
        # Shadow mode is prohibited from changing parser availability or output.
        try:
            record_shadow_observation(
                ShadowObservation(
                    status="RUNTIME_ERROR",
                    settlement=str(settlement),
                    current_commodity=current_commodity,
                    inferred_commodity=None,
                    agrees_with_current=None,
                    requires_user_confirmation=True,
                    decision_reason="UNEXPECTED_SHADOW_RUNTIME_ERROR",
                    bundle_version=None,
                    snapshot_version=None,
                )
            )
        except Exception:
            return
