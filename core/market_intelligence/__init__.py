"""Versioned, fail-closed market-intelligence runtime.

The first repository slice is deliberately Shadow-only: it may observe an
implicitly defaulted coin offer, but it cannot change parsing or persistence.
"""

from core.market_intelligence.bundle import (
    SUPPORTED_CODE_VERSION,
    BundleValidationError,
    LoadedRuntimeBundle,
    load_runtime_bundle,
)
from core.market_intelligence.ranker import (
    CommodityInferenceResult,
    CommodityRanker,
    RankerPolicy,
)

__all__ = [
    "SUPPORTED_CODE_VERSION",
    "BundleValidationError",
    "CommodityInferenceResult",
    "CommodityRanker",
    "LoadedRuntimeBundle",
    "RankerPolicy",
    "load_runtime_bundle",
]
