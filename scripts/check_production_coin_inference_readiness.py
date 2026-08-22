#!/usr/bin/env python3
"""Read-only readiness gates for production coin inference consumers.

The command never collects Telegram data, writes a Snapshot, or touches a
product database.  It validates the already-published artifact, its effective
underlying freshness and useful model coverage; optional probes also verify
the canonical Market Store inputs and the read-only in-container mount.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# This read-only probe imports the pure offer-guard evaluator, whose module
# initializes application Settings as an import side effect.  Host-side
# release checks must not parse the operational compose .env (it legitimately
# contains orchestration-only keys).  Checked-in non-secret defaults satisfy
# unrelated required Settings fields, while real process environment values
# retain Pydantic's higher priority inside production containers.
os.environ.setdefault(
    "APP_ENV_FILE",
    str(REPO_ROOT / "config" / "unit-test.env.example"),
)

from core.market_intelligence.coin_inference import CANONICAL_COMMODITY_NAMES
from core.market_intelligence.market_contracts import normalize_utc
from core.market_intelligence.market_snapshot import (
    AtomicMarketSnapshotProvider,
    MarketSnapshotUnavailable,
)
from core.market_intelligence.market_store import (
    MarketStoreError,
    connect_market_store_read_only,
    verify_market_store_read_only,
)
from core.services.offer_model_price_guard import (
    OFFER_MODEL_PRICE_GUARD_MAXIMUM_ANCHOR_AGE_SECONDS,
    OFFER_MODEL_PRICE_GUARD_MAXIMUM_UNDERLYING_AGE_SECONDS,
    OFFER_MODEL_PRICE_TOLERANCE_BPS_BY_CODE,
    evaluate_offer_model_price_snapshot,
)


PRODUCTION_CONFIRMATION = "check-production-coin-inference-readiness"
CANONICAL_CONTAINER_DIR = Path("/app/runtime/coin-inference")
CANONICAL_CONTAINER_SNAPSHOT = CANONICAL_CONTAINER_DIR / "coin-rates.json"
MAXIMUM_SNAPSHOT_AGE_SECONDS = 120
MAXIMUM_GROUP_INPUT_AGE_SECONDS = 3 * 86_400
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SAFE_NO_DATA_CONTEXT_KEY = "production_safe_no_data"
SAFE_NO_DATA_CONTEXT_VERSION = "production-safe-no-data-v1"
SAFE_NO_DATA_SOURCE_REASON = "PRIVATE_GOLD_QUIET_OUTSIDE_HARD_AUTHORITY_WINDOW"


class ProductionInferenceReadinessError(RuntimeError):
    """One redacted readiness contract failed."""


def _exact_integer(value: object, expected: int) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value == expected


def _age_inside(value: object, maximum: float) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and 0 <= float(value) <= float(maximum)
    )


def safe_no_data_source_assessment(report: Mapping[str, object]) -> bool:
    """Return whether a source report can authorize a no-price artifact.

    This is deliberately narrower than a generic degraded source.  Group
    inputs must remain inside hot retention, all three durable collector
    checkpoints must exist, and the only degradation must be a quiet/private
    gold book outside the hard-authority freshness window.  It never grants
    price authority.
    """

    return (
        report.get("status") == "DEGRADED_GUARD_FAIL_OPEN"
        and report.get("degradation_reason") == SAFE_NO_DATA_SOURCE_REASON
        and report.get("group_inputs_within_hot_retention") is True
        and report.get("private_input_within_hot_retention") is True
        and report.get("private_gold_hard_authority_fresh") is False
        and _age_inside(
            report.get("group_1_age_seconds"),
            MAXIMUM_GROUP_INPUT_AGE_SECONDS,
        )
        and _age_inside(
            report.get("group_2_age_seconds"),
            MAXIMUM_GROUP_INPUT_AGE_SECONDS,
        )
        and _age_inside(
            report.get("private_gold_age_seconds"),
            MAXIMUM_GROUP_INPUT_AGE_SECONDS,
        )
        and _exact_integer(report.get("collector_checkpoint_count"), 3)
        and report.get("safe_no_data_snapshot_allowed") is True
    )


def safe_no_data_snapshot_assessment(snapshot: Mapping[str, Any]) -> bool:
    """Validate the bounded provenance carried by a production no-data file."""

    rates = snapshot.get("rates")
    context = snapshot.get(SAFE_NO_DATA_CONTEXT_KEY)
    return (
        snapshot.get("snapshot_status") == "NO_DATA_COIN_RATE_STATE"
        and isinstance(rates, Mapping)
        and _exact_integer(rates.get("estimated_count"), 0)
        and not isinstance(rates.get("no_data_count"), bool)
        and isinstance(rates.get("no_data_count"), int)
        and int(rates["no_data_count"]) > 0
        and isinstance(context, Mapping)
        and context.get("contract_version") == SAFE_NO_DATA_CONTEXT_VERSION
        and context.get("source_status") == "DEGRADED_GUARD_FAIL_OPEN"
        and context.get("source_reason") == SAFE_NO_DATA_SOURCE_REASON
        and context.get("group_inputs_within_hot_retention") is True
        and context.get("private_input_within_hot_retention") is True
        and _exact_integer(context.get("collector_checkpoint_count"), 3)
        and context.get("price_authority") is False
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _time(value: object, *, field: str) -> datetime:
    normalized = normalize_utc(value, field_name=field)
    return datetime.fromisoformat(normalized.replace("Z", "+00:00"))


def _emit(status: str, **payload: object) -> None:
    print(
        json.dumps(
            {"status": status, **payload, "secrets_disclosed": False},
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )


def _snapshot_assessment(
    path: Path,
    *,
    expected_sha256: str | None,
    now: datetime,
    require_hard_reject_coverage: bool = False,
) -> tuple[Mapping[str, Any], dict[str, object]]:
    digest = str(expected_sha256 or "").strip().lower() or None
    if digest is not None and not _DIGEST_PATTERN.fullmatch(digest):
        raise ProductionInferenceReadinessError("snapshot_expected_digest_invalid")
    try:
        snapshot = AtomicMarketSnapshotProvider(
            path,
            expected_sha256=digest,
        ).load()
    except (MarketSnapshotUnavailable, ValueError) as exc:
        raise ProductionInferenceReadinessError("snapshot_unavailable") from exc
    generated = _time(
        snapshot.get("generated_at_utc"),
        field="production_inference_snapshot_generated_at_utc",
    )
    snapshot_age = (now - generated).total_seconds()
    if snapshot_age < 0 or snapshot_age > MAXIMUM_SNAPSHOT_AGE_SECONDS:
        raise ProductionInferenceReadinessError("snapshot_stale_or_future")
    rates = snapshot.get("rates")
    items = rates.get("items") if isinstance(rates, Mapping) else None
    if not isinstance(items, list):
        raise ProductionInferenceReadinessError("snapshot_rates_unavailable")

    expected_codes = set(OFFER_MODEL_PRICE_TOLERANCE_BPS_BY_CODE)
    hard_eligible: set[tuple[str, str]] = set()
    estimated: set[tuple[str, str]] = set()
    per_settlement: dict[str, int] = {"CASH": 0, "TOMORROW": 0}
    for item in items:
        if not isinstance(item, Mapping) or item.get("status") != "ESTIMATED":
            continue
        code = str(item.get("commodity_code") or "")
        settlement = str(item.get("settlement_term") or "")
        if code not in expected_codes or settlement not in per_settlement:
            continue
        estimated.add((code, settlement))
        confidence = str(item.get("confidence") or "")
        underlying_age = item.get("underlying_age_seconds")
        if (
            confidence not in {"HIGH", "MEDIUM"}
            or isinstance(underlying_age, bool)
            or not isinstance(underlying_age, (int, float))
            or float(underlying_age) + max(0.0, snapshot_age)
            > OFFER_MODEL_PRICE_GUARD_MAXIMUM_UNDERLYING_AGE_SECONDS
        ):
            continue
        anchor_age = item.get("anchor_age_seconds")
        if confidence == "HIGH":
            if (
                isinstance(anchor_age, bool)
                or not isinstance(anchor_age, (int, float))
                or float(anchor_age) + max(0.0, snapshot_age)
                > OFFER_MODEL_PRICE_GUARD_MAXIMUM_ANCHOR_AGE_SECONDS
            ):
                continue
        elif anchor_age is not None:
            continue
        hard_eligible.add((code, settlement))
        per_settlement[settlement] += 1

    represented_codes = {code for code, _settlement in hard_eligible}
    safe_no_data = not estimated and safe_no_data_snapshot_assessment(snapshot)
    if not estimated and not safe_no_data:
        raise ProductionInferenceReadinessError("estimated_rate_coverage_unavailable")
    if require_hard_reject_coverage and safe_no_data:
        raise ProductionInferenceReadinessError("hard_reject_coverage_insufficient")
    hard_reject_ready = represented_codes == expected_codes and all(
        value > 0 for value in per_settlement.values()
    )
    if require_hard_reject_coverage and not hard_reject_ready:
        raise ProductionInferenceReadinessError("hard_reject_coverage_insufficient")
    pure_guard_probe = "ABSTAINED_FAIL_OPEN"
    if hard_eligible:
        sample_code, sample_settlement = sorted(hard_eligible)[0]
        sample = next(
            item
            for item in items
            if item.get("commodity_code") == sample_code
            and item.get("settlement_term") == sample_settlement
        )
        decision = evaluate_offer_model_price_snapshot(
            snapshot,
            commodity_name=CANONICAL_COMMODITY_NAMES[sample_code],
            settlement_type=("tomorrow" if sample_settlement == "TOMORROW" else "cash"),
            offer_type="sell",
            proposed_price=int(sample["estimated_project_price"]),
            now_utc=now,
            market_opened_at=None,
        )
        if decision.status != "ALLOWED":
            raise ProductionInferenceReadinessError("pure_guard_probe_failed")
        pure_guard_probe = "ALLOWED"
    elif safe_no_data:
        sample_code = sorted(expected_codes)[0]
        decision = evaluate_offer_model_price_snapshot(
            snapshot,
            commodity_name=CANONICAL_COMMODITY_NAMES[sample_code],
            settlement_type="cash",
            offer_type="sell",
            proposed_price=100_000,
            now_utc=now,
            market_opened_at=None,
        )
        if decision.status != "ABSTAINED" or decision.reason != "MODEL_RANGE_UNAVAILABLE":
            raise ProductionInferenceReadinessError("pure_guard_probe_failed")
    return snapshot, {
        "status": "READY" if hard_reject_ready else "DEGRADED_GUARD_FAIL_OPEN",
        "snapshot_mode": "SAFE_NO_DATA" if safe_no_data else "RATE_READY",
        "snapshot_age_seconds": round(snapshot_age, 3),
        "estimated_rate_count": len(estimated),
        "hard_reject_eligible_count": len(hard_eligible),
        "hard_reject_cash_count": per_settlement["CASH"],
        "hard_reject_tomorrow_count": per_settlement["TOMORROW"],
        "hard_reject_coverage_ready": hard_reject_ready,
        "pure_guard_probe": pure_guard_probe,
    }


def _read_only_store(path: Path) -> sqlite3.Connection:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ProductionInferenceReadinessError("market_store_invalid")
    lowered = tuple(part.lower() for part in path.parts)
    if any("staging" in part for part in lowered) or not any(
        "production" in part for part in lowered
    ):
        raise ProductionInferenceReadinessError("market_store_scope_invalid")
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ProductionInferenceReadinessError("market_store_unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise ProductionInferenceReadinessError("market_store_metadata_invalid")
    connection: sqlite3.Connection | None = None
    try:
        connection = connect_market_store_read_only(path)
        verify_market_store_read_only(connection)
        return connection
    except (MarketStoreError, sqlite3.Error) as exc:
        if connection is not None:
            connection.close()
        raise ProductionInferenceReadinessError("market_store_unavailable") from exc


def _latest_age(
    connection: sqlite3.Connection,
    *,
    source_codes: Sequence[str],
    now: datetime,
    instruments: Sequence[str] = (),
    trade_forms: Sequence[str] = (),
) -> float | None:
    source_placeholders = ",".join("?" for _ in source_codes)
    filters = [f"source_code IN ({source_placeholders})"]
    parameters: list[object] = list(source_codes)
    if instruments:
        filters.append(
            f"instrument IN ({','.join('?' for _ in instruments)})"
        )
        parameters.extend(instruments)
    if trade_forms:
        filters.append(
            f"trade_form IN ({','.join('?' for _ in trade_forms)})"
        )
        parameters.extend(trade_forms)
    where = " AND ".join(filters)
    cutoff = now.isoformat().replace("+00:00", "Z")
    row = connection.execute(
        f"""
        SELECT MAX(event_time_utc)
        FROM market_observations
        WHERE {where}
          AND quality_state = 'ELIGIBLE'
          AND event_time_utc <= ?
          AND available_at_utc <= ?
        """,
        (*parameters, cutoff, cutoff),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    # Ingestion/availability can be current for a historical backfill.  Model
    # freshness is therefore anchored to the economic event time, matching
    # the rate engine, while the WHERE clause still enforces knowledge-time.
    return max(
        0.0,
        (now - _time(row[0], field="production_input_event_time_utc")).total_seconds(),
    )


def _source_probe(path: Path, *, now: datetime) -> dict[str, object]:
    connection = _read_only_store(path)
    try:
        group_1_age = _latest_age(connection, source_codes=("GROUP_1",), now=now)
        group_2_age = _latest_age(connection, source_codes=("GROUP_2",), now=now)
        private_age = _latest_age(
            connection,
            source_codes=("PRIVATE_GOLD_CHANNEL", "PRIVATE_GOLD_PAPER_MINUTE"),
            now=now,
        )
        private_physical_age = _latest_age(
            connection,
            source_codes=("PRIVATE_GOLD_CHANNEL",),
            instruments=("MELTED_GOLD_PRIVATE",),
            trade_forms=("PHYSICAL",),
            now=now,
        )
        private_paper_age = _latest_age(
            connection,
            source_codes=("PRIVATE_GOLD_CHANNEL", "PRIVATE_GOLD_PAPER_MINUTE"),
            instruments=("MELTED_GOLD_PRIVATE",),
            trade_forms=("PAPER_NORMAL",),
            now=now,
        )
        checkpoints = dict(
            connection.execute(
                """
                SELECT source_code, last_message_id
                FROM market_source_checkpoints
                WHERE source_code IN (?, ?, ?)
                """,
                (
                    "COIN_GROUP_EVENT_CHANNEL",
                    "PRIVATE_GOLD_EVENT_OFFER",
                    "PRIVATE_GOLD_EVENT_TRADE",
                ),
            ).fetchall()
        )
    except sqlite3.Error as exc:
        raise ProductionInferenceReadinessError("market_store_contract_invalid") from exc
    finally:
        connection.close()
    if (
        group_1_age is None
        or group_2_age is None
        or private_age is None
        or set(checkpoints)
        != {
            "COIN_GROUP_EVENT_CHANNEL",
            "PRIVATE_GOLD_EVENT_OFFER",
            "PRIVATE_GOLD_EVENT_TRADE",
        }
        or any(int(value or 0) <= 0 for value in checkpoints.values())
    ):
        raise ProductionInferenceReadinessError("upstream_input_freshness_or_coverage_failed")
    group_inputs_within_hot_retention = (
        group_1_age <= MAXIMUM_GROUP_INPUT_AGE_SECONDS
        and group_2_age <= MAXIMUM_GROUP_INPUT_AGE_SECONDS
    )
    private_input_within_hot_retention = (
        private_age <= MAXIMUM_GROUP_INPUT_AGE_SECONDS
    )
    private_gold_engine_age_supported = (
        (private_physical_age is not None and private_physical_age <= 900)
        or (private_paper_age is not None and private_paper_age <= 180)
    )
    private_gold_hard_authority_fresh = (
        private_physical_age is not None
        and private_physical_age
        <= OFFER_MODEL_PRICE_GUARD_MAXIMUM_UNDERLYING_AGE_SECONDS
    )
    status = (
        "READY"
        if group_inputs_within_hot_retention
        and private_gold_hard_authority_fresh
        else "DEGRADED_GUARD_FAIL_OPEN"
    )
    if not group_inputs_within_hot_retention:
        degradation_reason = "GROUP_INPUTS_OUTSIDE_HOT_RETENTION"
    elif not private_gold_hard_authority_fresh:
        degradation_reason = SAFE_NO_DATA_SOURCE_REASON
    else:
        degradation_reason = None
    safe_no_data_allowed = (
        status == "DEGRADED_GUARD_FAIL_OPEN"
        and degradation_reason == SAFE_NO_DATA_SOURCE_REASON
        and group_inputs_within_hot_retention
        and private_input_within_hot_retention
        and len(checkpoints) == 3
    )
    return {
        "status": status,
        "degradation_reason": degradation_reason,
        "safe_no_data_snapshot_allowed": safe_no_data_allowed,
        "group_1_age_seconds": round(group_1_age, 3),
        "group_2_age_seconds": round(group_2_age, 3),
        "private_gold_age_seconds": round(private_age, 3),
        "private_gold_physical_age_seconds": (
            round(private_physical_age, 3)
            if private_physical_age is not None
            else None
        ),
        "private_gold_paper_age_seconds": (
            round(private_paper_age, 3) if private_paper_age is not None else None
        ),
        "freshness_basis": "ECONOMIC_EVENT_TIME",
        "group_inputs_within_hot_retention": group_inputs_within_hot_retention,
        "private_input_within_hot_retention": private_input_within_hot_retention,
        "private_gold_engine_age_supported": private_gold_engine_age_supported,
        "private_gold_hard_authority_fresh": private_gold_hard_authority_fresh,
        "collector_checkpoint_count": len(checkpoints),
    }


def _mount_is_read_only(mountinfo: Path, target: Path) -> bool:
    try:
        lines = mountinfo.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ProductionInferenceReadinessError("mountinfo_unavailable") from exc
    target_text = str(target)
    for line in lines:
        fields = line.split()
        if "-" not in fields or len(fields) < 7 or fields[4] != target_text:
            continue
        separator = fields.index("-")
        mount_options = set(fields[5].split(","))
        super_options = set(fields[separator + 3].split(",")) if len(fields) > separator + 3 else set()
        return "ro" in mount_options or "ro" in super_options
    return False


def _consumer_probe(args: argparse.Namespace, *, now: datetime) -> dict[str, object]:
    snapshot = Path(args.snapshot)
    if snapshot != CANONICAL_CONTAINER_SNAPSHOT:
        raise ProductionInferenceReadinessError("consumer_snapshot_path_invalid")
    if not _mount_is_read_only(Path(args.mountinfo), CANONICAL_CONTAINER_DIR):
        raise ProductionInferenceReadinessError("consumer_snapshot_mount_not_read_only")
    enabled = {
        "preview": os.getenv("COIN_INTELLIGENCE_INFERENCE_PREVIEW_ENABLED", "false").lower() == "true",
        "selection": os.getenv("COIN_INTELLIGENCE_INFERENCE_SELECTION_ENABLED", "false").lower() == "true",
        "guard": os.getenv("OFFER_MODEL_PRICE_GUARD_ENABLED", "false").lower() == "true",
    }
    if args.expect_enabled and not all(enabled.values()):
        raise ProductionInferenceReadinessError("consumer_inference_flags_not_enabled")
    if os.getenv("COIN_INTELLIGENCE_INFERENCE_AUTO_SELECTION_ENABLED", "false").lower() == "true":
        raise ProductionInferenceReadinessError("consumer_auto_selection_forbidden")
    _snapshot, assessment = _snapshot_assessment(
        snapshot,
        expected_sha256=args.expected_sha256,
        now=now,
        require_hard_reject_coverage=bool(
            getattr(args, "require_hard_reject_coverage", False)
        ),
    )
    return {**assessment, "mount_read_only": True, "enabled_flags": enabled}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True, choices=("production",))
    parser.add_argument("--production-confirmation", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("snapshot")
    snapshot.add_argument("--snapshot", required=True)
    snapshot.add_argument("--expected-sha256")
    snapshot.add_argument("--require-hard-reject-coverage", action="store_true")
    source = commands.add_parser("source")
    source.add_argument("--market-store", required=True)
    consumer = commands.add_parser("consumer")
    consumer.add_argument("--snapshot", required=True)
    consumer.add_argument("--expected-sha256", required=True)
    consumer.add_argument("--mountinfo", default="/proc/self/mountinfo")
    consumer.add_argument("--expect-enabled", action="store_true")
    consumer.add_argument("--require-hard-reject-coverage", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.production_confirmation != PRODUCTION_CONFIRMATION:
            raise ProductionInferenceReadinessError("production_confirmation_required")
        now = _now()
        if args.command == "snapshot":
            _snapshot, result = _snapshot_assessment(
                Path(args.snapshot),
                expected_sha256=args.expected_sha256,
                now=now,
                require_hard_reject_coverage=args.require_hard_reject_coverage,
            )
        elif args.command == "source":
            result = _source_probe(Path(args.market_store), now=now)
        else:
            result = _consumer_probe(args, now=now)
        result = dict(result)
        status = str(result.pop("status", "READY"))
        _emit(status, **result)
        return 0
    except (OSError, sqlite3.Error, ValueError, ProductionInferenceReadinessError) as exc:
        reason = str(exc) if isinstance(exc, ProductionInferenceReadinessError) else type(exc).__name__
        _emit("BLOCKED", reason=reason)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
