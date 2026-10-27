"""Product-owned market-data primitives for commodity inference.

The package deliberately contains no collector, scheduler, Telegram credential,
or remote-service dependency.  Those integrations arrive only after the
versioned Market Store contract is established.
"""

from .market_contracts import MarketObservation, MarketStoreContractError
from .market_store import (
    MarketStoreMigrationRequired,
    connect_market_store,
    initialize_market_store,
)

__all__ = [
    "MarketObservation",
    "MarketStoreContractError",
    "MarketStoreMigrationRequired",
    "connect_market_store",
    "initialize_market_store",
]
