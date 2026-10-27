"""Fail-closed Product reader for legacy and private estimator snapshots.

The reader keeps Product authority on the legacy artifact by default.  Shadow
mode validates both artifacts but still returns legacy; primary mode has no
legacy fallback.  Private snapshots are projected without recalculation into
the existing Product market-snapshot vocabulary so inference and safety guards
can share one immutable input.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import json
import os
from pathlib import Path
import stat
from typing import Any, Callable, Mapping

from .coin_rate_engine import COIN_RATE_ENGINE_VERSION
from .market_contracts import MARKET_STORE_CONTRACT_VERSION, normalize_utc
from .market_snapshot import (
    AtomicMarketSnapshotProvider,
    MARKET_SNAPSHOT_SCHEMA_VERSION,
    MarketSnapshotUnavailable,
    validate_market_snapshot,
)
from .private_pipeline_contracts import EstimatorSnapshotV2


PRODUCT_SNAPSHOT_MODES = frozenset(
    {"LEGACY", "PRIVATE_SHADOW", "PRIVATE_PRIMARY"}
)
PRODUCT_PRIVATE_SNAPSHOT_BUILDER_VERSION = "product-private-snapshot-adapter-v1"
PRODUCT_PRIVATE_SNAPSHOT_MAXIMUM_BYTES = 2 * 1024 * 1024
PRODUCT_PRIVATE_SNAPSHOT_PUBLISHER_UID = 10001


class ProductSnapshotUnavailable(RuntimeError):
    """Content-free failure that prevents an unsafe Product snapshot read."""

    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class ProductSnapshotReadResult:
    snapshot: Mapping[str, Any]
    configured_mode: str
    authority: str
    comparison_status: str
    private_snapshot_hash: str | None = None
    private_snapshot_version: int | None = None
    private_reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class PrivateShadowReadinessResult:
    """Machine-readable validation result independent of legacy authority."""

    ready: bool
    reason_code: str
    private_snapshot_hash: str | None = None
    private_snapshot_version: int | None = None
    projected_snapshot: Mapping[str, Any] | None = None


def normalize_product_snapshot_mode(value: str | None) -> str:
    mode = str(value or "LEGACY").strip().upper()
    if mode not in PRODUCT_SNAPSHOT_MODES:
        raise ProductSnapshotUnavailable("PRODUCT_SNAPSHOT_MODE_INVALID")
    return mode


def _utc(value: datetime | str, *, field_name: str) -> datetime:
    normalized = normalize_utc(value, field_name=field_name)
    return datetime.fromisoformat(normalized.replace("Z", "+00:00"))


def _require_fresh(
    generated_at_utc: object,
    *,
    now_utc: datetime,
    maximum_age_seconds: int,
) -> None:
    try:
        generated = _utc(
            str(generated_at_utc or ""),
            field_name="product_snapshot_generated_at_utc",
        )
    except (TypeError, ValueError) as exc:
        raise ProductSnapshotUnavailable("PRODUCT_SNAPSHOT_TIME_INVALID") from exc
    age = (now_utc - generated).total_seconds()
    if age < 0:
        raise ProductSnapshotUnavailable("PRODUCT_SNAPSHOT_FUTURE")
    if age > maximum_age_seconds:
        raise ProductSnapshotUnavailable("PRODUCT_SNAPSHOT_STALE")


def _private_snapshot_owner_is_allowed(
    owner_uid: int, *, effective_uid: int | None = None
) -> bool:
    reader_uid = os.geteuid() if effective_uid is None else int(effective_uid)
    return int(owner_uid) in {
        0,
        reader_uid,
        PRODUCT_PRIVATE_SNAPSHOT_PUBLISHER_UID,
    }


def _read_private_document(path: Path) -> Mapping[str, Any]:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ProductSnapshotUnavailable("PRIVATE_SNAPSHOT_UNAVAILABLE") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > PRODUCT_PRIVATE_SNAPSHOT_MAXIMUM_BYTES
            or before.st_nlink != 1
            or not _private_snapshot_owner_is_allowed(before.st_uid)
            or stat.S_IMODE(before.st_mode) & 0o022
        ):
            raise ProductSnapshotUnavailable("PRIVATE_SNAPSHOT_FILE_INVALID")
        chunks: list[bytes] = []
        remaining = before.st_size + 1
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        if len(payload) != before.st_size or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ProductSnapshotUnavailable("PRIVATE_SNAPSHOT_CHANGED_DURING_READ")
    except ProductSnapshotUnavailable:
        raise
    except OSError as exc:
        raise ProductSnapshotUnavailable("PRIVATE_SNAPSHOT_READ_FAILED") from exc
    finally:
        os.close(descriptor)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductSnapshotUnavailable("PRIVATE_SNAPSHOT_JSON_INVALID") from exc
    if not isinstance(value, Mapping):
        raise ProductSnapshotUnavailable("PRIVATE_SNAPSHOT_DOCUMENT_INVALID")
    return value


def _product_regime_label(label: str) -> str | None:
    return {
        "RANGE": "NORMAL",
        "UP": "UP",
        "DOWN": "DOWN",
        "SHOCK": "VOLATILE",
    }.get(label)


def _product_regime(label: str) -> dict[str, object]:
    product_label = _product_regime_label(label)
    if product_label is None:
        return {
            "status": "ABSTAIN",
            "reason": "MARKET_REGIME_UNKNOWN",
            "inputs": [],
            "method": "PRIVATE_SNAPSHOT_PRESERVED",
        }
    return {
        "status": "OBSERVED",
        "label": product_label,
        "inputs": [],
        "method": "PRIVATE_SNAPSHOT_PRESERVED",
    }


def project_private_snapshot_for_product(
    snapshot: EstimatorSnapshotV2,
) -> dict[str, Any]:
    """Translate fields one-to-one; never estimate or fill a missing cell."""

    items: list[dict[str, Any]] = []
    regimes: dict[str, str] = {}
    for rate in snapshot.rates:
        if rate.unit != "PROJECT_THOUSAND_TOMAN":
            raise ProductSnapshotUnavailable(
                "PRIVATE_SNAPSHOT_RATE_UNIT_UNSUPPORTED"
            )
        code = rate.instrument.removeprefix("COIN_")
        regimes.setdefault(rate.settlement, rate.market_regime)
        if regimes[rate.settlement] != rate.market_regime:
            raise ProductSnapshotUnavailable("PRIVATE_SNAPSHOT_REGIME_CONFLICT")
        items.append(
            {
                "commodity_code": code,
                "settlement_term": rate.settlement,
                "status": rate.status,
                "estimated_project_price": (
                    int(rate.value) if rate.value is not None else None
                ),
                "lower_project_price": (
                    int(rate.lower_bound) if rate.lower_bound is not None else None
                ),
                "upper_project_price": (
                    int(rate.upper_bound) if rate.upper_bound is not None else None
                ),
                "confidence": rate.confidence,
                "method": rate.method,
                "underlying_source": rate.underlying_source,
                "underlying_age_seconds": rate.underlying_age_seconds,
                "anchor_age_seconds": rate.anchor_age_seconds,
                "market_regime": _product_regime_label(rate.market_regime)
                or "UNKNOWN",
                "reason": rate.reason_code,
            }
        )
    market_regimes = {
        settlement: _product_regime(regimes.get(settlement, "UNKNOWN"))
        for settlement in ("CASH", "TOMORROW")
    }
    estimated_count = sum(item["status"] == "ESTIMATED" for item in items)
    result = {
        "schema_version": MARKET_SNAPSHOT_SCHEMA_VERSION,
        "market_store_contract_version": MARKET_STORE_CONTRACT_VERSION,
        "builder_version": PRODUCT_PRIVATE_SNAPSHOT_BUILDER_VERSION,
        "generated_at_utc": snapshot.generated_at_utc.isoformat().replace(
            "+00:00", "Z"
        ),
        "signals": {},
        "market_regime": market_regimes["TOMORROW"],
        "market_regimes": market_regimes,
        "rates": {
            "engine_version": snapshot.model_version,
            "items": items,
            "estimated_count": estimated_count,
            "no_data_count": len(items) - estimated_count,
        },
        "snapshot_status": (
            "PARTIAL_COIN_RATE_STATE"
            if estimated_count
            else "NO_DATA_COIN_RATE_STATE"
        ),
    }
    try:
        validate_market_snapshot(result)
    except Exception as exc:
        raise ProductSnapshotUnavailable(
            "PRIVATE_PRODUCT_PROJECTION_INVALID"
        ) from exc
    return result


def _rate_fingerprint(snapshot: Mapping[str, Any]) -> tuple[tuple[object, ...], ...]:
    rates = snapshot.get("rates")
    if not isinstance(rates, Mapping):
        return ()
    return tuple(
        (
            item.get("commodity_code"),
            item.get("settlement_term"),
            item.get("status"),
            item.get("estimated_project_price"),
            item.get("lower_project_price"),
            item.get("upper_project_price"),
            item.get("confidence"),
            item.get("underlying_source"),
            item.get("underlying_age_seconds"),
            item.get("anchor_age_seconds"),
            item.get("market_regime"),
        )
        for item in rates.get("items") or ()
        if isinstance(item, Mapping)
    )


class ProductSnapshotReader:
    """Read one configured authority with explicit shadow comparison state."""

    def __init__(
        self,
        *,
        legacy_path: Path | str,
        private_shadow_path: Path | str | None = None,
        private_primary_path: Path | str | None = None,
        mode: str = "LEGACY",
        maximum_age_seconds: int = 120,
        legacy_provider_factory: Callable[
            [Path | str], AtomicMarketSnapshotProvider
        ] = AtomicMarketSnapshotProvider,
    ) -> None:
        self.legacy_path = Path(legacy_path)
        self.private_shadow_path = (
            Path(private_shadow_path) if private_shadow_path else None
        )
        self.private_primary_path = (
            Path(private_primary_path) if private_primary_path else None
        )
        self.mode = normalize_product_snapshot_mode(mode)
        self.maximum_age_seconds = int(maximum_age_seconds)
        self.legacy_provider_factory = legacy_provider_factory
        if self.maximum_age_seconds <= 0:
            raise ProductSnapshotUnavailable("PRODUCT_SNAPSHOT_MAXIMUM_AGE_INVALID")
        self._seen_private: dict[str, tuple[int, str]] = {}

    def inspect_private_shadow(
        self,
        *,
        now_utc: datetime | None = None,
    ) -> PrivateShadowReadinessResult:
        """Validate only the private Shadow artifact, never the legacy source.

        This inspection is deliberately separate from :meth:`load`: a valid
        private artifact can therefore produce readiness evidence while a
        missing or stale legacy artifact still makes Product reads fail closed.
        """

        if self.mode != "PRIVATE_SHADOW":
            return PrivateShadowReadinessResult(
                ready=False,
                reason_code="PRIVATE_SHADOW_MODE_NOT_CONFIGURED",
            )
        now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
        try:
            product, private = self._private(
                lane="PRIVATE_SHADOW",
                path=self.private_shadow_path,
                now_utc=now,
            )
        except ProductSnapshotUnavailable as exc:
            return PrivateShadowReadinessResult(
                ready=False,
                reason_code=exc.reason_code,
            )
        if private.status != "OK":
            return PrivateShadowReadinessResult(
                ready=False,
                reason_code="PRIVATE_SHADOW_NOT_RATE_READY",
                private_snapshot_hash=private.snapshot_id,
                private_snapshot_version=private.snapshot_version,
                projected_snapshot=product,
            )
        return PrivateShadowReadinessResult(
            ready=True,
            reason_code="PRIVATE_SHADOW_VALID",
            private_snapshot_hash=private.snapshot_id,
            private_snapshot_version=private.snapshot_version,
            projected_snapshot=product,
        )

    def _legacy(self, now_utc: datetime) -> Mapping[str, Any]:
        try:
            snapshot = self.legacy_provider_factory(self.legacy_path).load()
        except MarketSnapshotUnavailable as exc:
            raise ProductSnapshotUnavailable("LEGACY_SNAPSHOT_UNAVAILABLE") from exc
        _require_fresh(
            snapshot.get("generated_at_utc"),
            now_utc=now_utc,
            maximum_age_seconds=self.maximum_age_seconds,
        )
        return snapshot

    def _private(
        self,
        *,
        lane: str,
        path: Path | None,
        now_utc: datetime,
    ) -> tuple[Mapping[str, Any], EstimatorSnapshotV2]:
        if path is None:
            raise ProductSnapshotUnavailable("PRIVATE_SNAPSHOT_PATH_UNCONFIGURED")
        document = _read_private_document(path)
        contract = document.get("contract")
        if contract != "estimator_snapshot_web_view/1.0":
            raise ProductSnapshotUnavailable("PRIVATE_SNAPSHOT_CONTRACT_UNSUPPORTED")
        payload = document.get("snapshot")
        if not isinstance(payload, Mapping):
            raise ProductSnapshotUnavailable("PRIVATE_WEB_VIEW_SNAPSHOT_INVALID")
        try:
            snapshot = EstimatorSnapshotV2.model_validate(payload)
        except (TypeError, ValueError) as exc:
            raise ProductSnapshotUnavailable("PRIVATE_SNAPSHOT_CONTRACT_INVALID") from exc
        if snapshot.feed_mode != lane:
            raise ProductSnapshotUnavailable("PRIVATE_SNAPSHOT_LANE_MISMATCH")
        if document.get("feed_mode") != lane:
            raise ProductSnapshotUnavailable("PRIVATE_SNAPSHOT_LANE_MISMATCH")
        if (
            document.get("snapshot_hash") != snapshot.snapshot_id
            or document.get("snapshot_version") != snapshot.snapshot_version
        ):
            raise ProductSnapshotUnavailable("PRIVATE_WEB_VIEW_IDENTITY_MISMATCH")
        if snapshot.model_version != COIN_RATE_ENGINE_VERSION:
            raise ProductSnapshotUnavailable("PRIVATE_SNAPSHOT_MODEL_VERSION_INVALID")
        if snapshot.status == "FAILURE":
            raise ProductSnapshotUnavailable("PRIVATE_SNAPSHOT_FAILURE")
        _require_fresh(
            snapshot.generated_at_utc,
            now_utc=now_utc,
            maximum_age_seconds=self.maximum_age_seconds,
        )
        prior = self._seen_private.get(lane)
        current = (snapshot.snapshot_version, snapshot.snapshot_id)
        if prior is not None and current[0] < prior[0]:
            raise ProductSnapshotUnavailable("PRIVATE_SNAPSHOT_VERSION_REGRESSION")
        if prior is not None and current[0] == prior[0] and current[1] != prior[1]:
            raise ProductSnapshotUnavailable("PRIVATE_SNAPSHOT_VERSION_CONFLICT")
        self._seen_private[lane] = current
        return project_private_snapshot_for_product(snapshot), snapshot

    def load(self, *, now_utc: datetime | None = None) -> ProductSnapshotReadResult:
        now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if self.mode == "LEGACY":
            return ProductSnapshotReadResult(
                snapshot=self._legacy(now),
                configured_mode=self.mode,
                authority="LEGACY",
                comparison_status="NOT_REQUESTED",
            )
        if self.mode == "PRIVATE_PRIMARY":
            product, private = self._private(
                lane="PRIVATE_PRIMARY",
                path=self.private_primary_path,
                now_utc=now,
            )
            return ProductSnapshotReadResult(
                snapshot=product,
                configured_mode=self.mode,
                authority="PRIVATE_PRIMARY",
                comparison_status="AUTHORITATIVE",
                private_snapshot_hash=private.snapshot_id,
                private_snapshot_version=private.snapshot_version,
            )

        legacy = self._legacy(now)
        try:
            product, private = self._private(
                lane="PRIVATE_SHADOW",
                path=self.private_shadow_path,
                now_utc=now,
            )
        except ProductSnapshotUnavailable as exc:
            return ProductSnapshotReadResult(
                snapshot=legacy,
                configured_mode=self.mode,
                authority="LEGACY",
                comparison_status="PRIVATE_REJECTED",
                private_reason_code=exc.reason_code,
            )
        return ProductSnapshotReadResult(
            snapshot=legacy,
            configured_mode=self.mode,
            authority="LEGACY",
            comparison_status=(
                "MATCH"
                if _rate_fingerprint(legacy) == _rate_fingerprint(product)
                else "MISMATCH"
            ),
            private_snapshot_hash=private.snapshot_id,
            private_snapshot_version=private.snapshot_version,
        )


@lru_cache(maxsize=16)
def _configured_reader(
    legacy_path: str,
    private_shadow_path: str,
    private_primary_path: str,
    mode: str,
    maximum_age_seconds: int,
) -> ProductSnapshotReader:
    return ProductSnapshotReader(
        legacy_path=legacy_path,
        private_shadow_path=private_shadow_path or None,
        private_primary_path=private_primary_path or None,
        mode=mode,
        maximum_age_seconds=maximum_age_seconds,
    )


def product_snapshot_reader_from_settings(
    settings: object,
    *,
    maximum_age_seconds: int,
) -> ProductSnapshotReader:
    """Build one process-local reader without importing the settings module."""

    return _configured_reader(
        str(getattr(settings, "coin_intelligence_inference_snapshot_path", "") or "").strip(),
        str(
            getattr(settings, "product_estimator_private_shadow_snapshot_path", "")
            or ""
        ).strip(),
        str(
            getattr(settings, "product_estimator_private_primary_snapshot_path", "")
            or ""
        ).strip(),
        str(getattr(settings, "product_estimator_snapshot_mode", "LEGACY") or "LEGACY"),
        int(maximum_age_seconds),
    )


def configured_product_snapshot_authority_path(settings: object) -> str:
    """Return the path required by the configured Product authority lane."""

    mode = normalize_product_snapshot_mode(
        str(getattr(settings, "product_estimator_snapshot_mode", "LEGACY") or "LEGACY")
    )
    if mode == "PRIVATE_PRIMARY":
        return str(
            getattr(settings, "product_estimator_private_primary_snapshot_path", "")
            or ""
        ).strip()
    return str(
        getattr(settings, "coin_intelligence_inference_snapshot_path", "") or ""
    ).strip()


__all__ = [
    "PRODUCT_SNAPSHOT_MODES",
    "PRODUCT_PRIVATE_SNAPSHOT_PUBLISHER_UID",
    "PrivateShadowReadinessResult",
    "ProductSnapshotReadResult",
    "ProductSnapshotReader",
    "ProductSnapshotUnavailable",
    "configured_product_snapshot_authority_path",
    "normalize_product_snapshot_mode",
    "product_snapshot_reader_from_settings",
    "project_private_snapshot_for_product",
]
