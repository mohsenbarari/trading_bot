"""Explicit, local-only publisher for a rate-ready atomic market Snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from .market_snapshot import (
    MarketSnapshotError,
    build_market_snapshot,
    publish_market_snapshot_atomically,
)
from .market_store import (
    MarketStoreError,
    MarketStoreMigrationRequired,
    connect_market_store_read_only,
    snapshot_input_watermark,
    verify_market_store_read_only,
)


SNAPSHOT_PUBLISHER_VERSION = "market-snapshot-publisher-v3"


class MarketSnapshotPublisherError(RuntimeError):
    """An operationally safe failure before an artifact can be published."""


@dataclass(frozen=True, slots=True)
class MarketSnapshotPublishResult:
    """Privacy-safe result for one explicit publisher invocation."""

    status: str
    snapshot_digest: str | None
    generated_at_utc: str | None
    estimated_rate_count: int
    no_data_rate_count: int
    reason: str | None
    input_watermark: dict[str, int | str] | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _distinct_paths(market_store_path: Path | str, snapshot_path: Path | str) -> None:
    store = Path(market_store_path).expanduser().resolve()
    snapshot = Path(snapshot_path).expanduser().resolve()
    if store == snapshot:
        raise MarketSnapshotPublisherError("snapshot_publisher_store_target_conflict")


def _default_watermark_path(snapshot_path: Path) -> Path:
    return snapshot_path.with_name(f".{snapshot_path.name}.input-watermark.json")


def _save_watermark(path: Path, watermark: dict[str, int | str], *, snapshot_digest: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "schema_version": 1,
        "publisher_version": SNAPSHOT_PUBLISHER_VERSION,
        "snapshot_digest": snapshot_digest,
        "watermark": watermark,
        "updated_at_utc": _utc_now().isoformat().replace("+00:00", "Z"),
    }
    temporary.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def publish_rate_ready_snapshot(
    *,
    market_store_path: Path | str,
    snapshot_path: Path | str,
    as_of_utc: datetime | str | None = None,
    force: bool = False,
    watermark_path: Path | str | None = None,
) -> MarketSnapshotPublishResult:
    """Publish only a Snapshot with at least one usable canonical rate.

    The caller owns scheduling and error reporting. This function opens the
    Market Store read-only, never creates a store, and preserves a previous
    valid Snapshot if upstream evidence is empty or not rate-ready.

    Snapshot content is time-dependent even when the input rows are unchanged:
    source ages, freshness states, and ``generated_at_utc`` must advance on
    every scheduled invocation.  The input watermark is therefore audit
    metadata only; it must never suppress a rebuild.  ``force`` remains an
    accepted compatibility argument for older callers.
    """

    _distinct_paths(market_store_path, snapshot_path)
    snapshot_file = Path(snapshot_path).expanduser().resolve()
    mark_path = (
        Path(watermark_path).expanduser().resolve()
        if watermark_path is not None
        else _default_watermark_path(snapshot_file)
    )
    connection = None
    watermark: dict[str, int | str]
    try:
        connection = connect_market_store_read_only(market_store_path)
        verify_market_store_read_only(connection)
        watermark = snapshot_input_watermark(connection)
        snapshot = build_market_snapshot(
            connection,
            as_of_utc=as_of_utc or _utc_now(),
        )
    except (MarketStoreError, MarketStoreMigrationRequired) as exc:
        raise MarketSnapshotPublisherError("snapshot_publisher_store_unavailable") from exc
    except MarketSnapshotError as exc:
        raise MarketSnapshotPublisherError("snapshot_publisher_build_failed") from exc
    finally:
        if connection is not None:
            connection.close()

    rates = snapshot["rates"]
    estimated_count = int(rates["estimated_count"])
    no_data_count = int(rates["no_data_count"])
    if estimated_count <= 0:
        return MarketSnapshotPublishResult(
            status="NOT_RATE_READY",
            snapshot_digest=None,
            generated_at_utc=str(snapshot["generated_at_utc"]),
            estimated_rate_count=estimated_count,
            no_data_rate_count=no_data_count,
            reason="NO_ESTIMATED_COIN_RATES",
            input_watermark=watermark,
        )
    try:
        digest = publish_market_snapshot_atomically(snapshot_file, snapshot)
    except MarketSnapshotError as exc:
        raise MarketSnapshotPublisherError("snapshot_publisher_atomic_publish_failed") from exc
    _save_watermark(mark_path, watermark, snapshot_digest=digest)
    return MarketSnapshotPublishResult(
        status="PUBLISHED",
        snapshot_digest=digest,
        generated_at_utc=str(snapshot["generated_at_utc"]),
        estimated_rate_count=estimated_count,
        no_data_rate_count=no_data_count,
        reason=None,
        input_watermark=watermark,
    )


__all__ = [
    "SNAPSHOT_PUBLISHER_VERSION",
    "MarketSnapshotPublishResult",
    "MarketSnapshotPublisherError",
    "publish_rate_ready_snapshot",
]
