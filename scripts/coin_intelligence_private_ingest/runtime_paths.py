"""Runtime path contract for the private market-event ingestion pipeline."""

from __future__ import annotations

import os
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _configured_path(name: str, default: str) -> Path:
    value = os.environ.get(name, default).strip()
    if not value:
        raise RuntimeError(f"{name} must not be empty")
    path = Path(value).expanduser().resolve()
    if path == REPOSITORY_ROOT or REPOSITORY_ROOT in path.parents:
        raise RuntimeError(f"{name} must be outside the repository checkout")
    return path


# These defaults are deliberately generic and outside the repository. A real
# deployment should set all paths explicitly in its secret/runtime env file.
DATA_ROOT = _configured_path(
    "COIN_INTELLIGENCE_DATA_ROOT", "/var/lib/trading-bot/coin-intelligence"
)
PRIVATE_ROOT = _configured_path(
    "COIN_PRIVATE_EVENT_ROOT", str(DATA_ROOT / "private-channel-ingest")
)
PIPELINE_ROOT = PRIVATE_ROOT / "pipeline"
DROP_ROOT = PRIVATE_ROOT / "manual-backfill-drop"

CONVERSATION_DB = _configured_path(
    "COIN_CONVERSATION_DB", str(DATA_ROOT / "conversation_events.sqlite3")
)
CONVERSATION_LABEL_DB = _configured_path(
    "COIN_CONVERSATION_LABEL_DB", str(CONVERSATION_DB)
)
MARKET_DB = _configured_path(
    "COIN_MARKET_DB", str(DATA_ROOT / "market_prices.sqlite3")
)


def ensure_runtime_directories() -> None:
    """Create only the bounded directories owned by this pipeline."""

    PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
    PIPELINE_ROOT.mkdir(parents=True, exist_ok=True)
