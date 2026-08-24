#!/usr/bin/env python3
"""Causally replay new capture spools into a protected shadow Market Store."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.capture_event_adapter import (  # noqa: E402
    CAPTURE_ADAPTER_VERSION,
    CaptureEvent,
    CaptureEventContractError,
    decode_capture_event,
    initialize_capture_adapter,
    project_capture_changes,
    stage_capture_event,
)
from core.market_intelligence.coin_group_staging import connect_coin_group_staging  # noqa: E402
from core.market_intelligence.coin_rate_engine import (  # noqa: E402
    COIN_RATE_ENGINE_VERSION,
    build_coin_rate_estimates,
)
from core.market_intelligence.market_contracts import normalize_utc  # noqa: E402
from core.market_intelligence.market_store import connect_market_store, initialize_market_store  # noqa: E402


COMMAND_VERSION = "capture-shadow-replay-v1"
_SPOOL_NAME = re.compile(r"^events-\d{4}-\d{2}-\d{2}\.jsonl$")
_MAX_RECORD_BYTES = 256 * 1024


class CaptureReplayError(RuntimeError):
    pass


def _path(value: str, *, field: str, kind: str, must_not_exist: bool = False) -> Path:
    path = Path(value).expanduser().resolve()
    try:
        path.relative_to(REPO_ROOT.resolve())
    except ValueError:
        pass
    else:
        raise CaptureReplayError(f"{field}_inside_repository")
    if path.is_symlink():
        raise CaptureReplayError(f"{field}_symlink_rejected")
    if kind == "file" and not path.is_file():
        raise CaptureReplayError(f"{field}_unavailable")
    if kind == "dir" and not path.is_dir():
        raise CaptureReplayError(f"{field}_unavailable")
    if kind == "output" and (not path.parent.is_dir() or path.parent.is_symlink()):
        raise CaptureReplayError(f"{field}_parent_unavailable")
    if must_not_exist and path.exists():
        raise CaptureReplayError(f"{field}_already_exists")
    return path


def _dt(value: str, *, field: str) -> datetime:
    return datetime.fromisoformat(
        normalize_utc(value, field_name=field).replace("Z", "+00:00")
    )


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_events(
    directories: tuple[tuple[str, Path], ...],
    *,
    maximum_records: int,
) -> tuple[list[CaptureEvent], int, int]:
    events: list[CaptureEvent] = []
    rejected = records = 0
    for stream, directory in directories:
        files = [
            path
            for path in sorted(directory.iterdir())
            if _SPOOL_NAME.fullmatch(path.name) and path.is_file() and not path.is_symlink()
        ]
        for path in files:
            with path.open("rb") as handle:
                for raw in handle:
                    records += 1
                    if records > maximum_records:
                        raise CaptureReplayError("capture_replay_record_limit_exceeded")
                    if len(raw) > _MAX_RECORD_BYTES or not raw.endswith(b"\n"):
                        rejected += 1
                        continue
                    try:
                        record = json.loads(raw.decode("utf-8"))
                        events.append(decode_capture_event(record, stream=stream))
                    except (UnicodeDecodeError, json.JSONDecodeError, CaptureEventContractError):
                        rejected += 1
    events.sort(key=lambda item: (item.available_at_utc, item.stream, item.event_id))
    return events, records, rejected


def _write_report(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _run(args: argparse.Namespace) -> int:
    seed = _path(args.seed_market_store, field="seed_market_store", kind="file")
    market_path = _path(args.shadow_market_store, field="shadow_market_store", kind="output", must_not_exist=True)
    staging_path = _path(args.shadow_staging_store, field="shadow_staging_store", kind="output", must_not_exist=True)
    report_path = _path(args.output_report, field="output_report", kind="output", must_not_exist=True)
    market_spool = _path(args.market_spool_dir, field="market_spool_dir", kind="dir")
    coin_spool = _path(args.coin_spool_dir, field="coin_spool_dir", kind="dir")
    start = _dt(args.start_utc, field="capture_replay_start_utc")
    end = _dt(args.end_utc, field="capture_replay_end_utc")
    interval = int(args.interval_seconds)
    if end < start or interval <= 0 or interval > 3600:
        raise CaptureReplayError("capture_replay_range_invalid")
    events, records, rejected = _load_events(
        (("market", market_spool), ("coin", coin_spool)),
        maximum_records=int(args.maximum_records),
    )
    shutil.copy2(seed, market_path)
    os.chmod(market_path, 0o600)
    staging = connect_coin_group_staging(staging_path, repository_root=REPO_ROOT)
    market = connect_market_store(market_path)
    points: list[dict[str, object]] = []
    index = accepted = duplicates = changes = tombstones = 0
    projection_totals = {
        "market_messages_reprojected": 0,
        "market_facts_upserted": 0,
        "market_facts_retracted": 0,
        "private_paper_minutes_refreshed": 0,
        "group_pipeline_runs": 0,
        "group_eligible_offers_last": 0,
        "group_eligible_trades_last": 0,
    }
    try:
        initialize_capture_adapter(staging)
        initialize_market_store(market)
        checkpoint = start
        while checkpoint <= end:
            checkpoint_iso = _iso(checkpoint)
            while index < len(events) and events[index].available_at_utc <= checkpoint_iso:
                stage = stage_capture_event(staging, events[index])
                accepted += int(stage.accepted)
                duplicates += int(stage.duplicate)
                changes += int(stage.staged_change)
                tombstones += int(stage.tombstone_applied)
                index += 1
            staging.commit()
            projection = project_capture_changes(staging, market, as_of_utc=checkpoint_iso)
            market.commit()
            staging.commit()
            projection_totals["market_messages_reprojected"] += projection.market_messages_reprojected
            projection_totals["market_facts_upserted"] += projection.market_facts_upserted
            projection_totals["market_facts_retracted"] += projection.market_facts_retracted
            projection_totals["private_paper_minutes_refreshed"] += projection.private_paper_minutes_refreshed
            if projection.group_pipeline is not None:
                projection_totals["group_pipeline_runs"] += 1
                projection_totals["group_eligible_offers_last"] = projection.group_pipeline.eligible_offers
                projection_totals["group_eligible_trades_last"] = projection.group_pipeline.eligible_trades
            rates = build_coin_rate_estimates(market, as_of_utc=checkpoint)
            points.append(
                {
                    "as_of_utc": checkpoint_iso,
                    "estimated_count": sum(item.status == "ESTIMATED" for item in rates),
                    "rates": [item.to_dict() for item in rates],
                }
            )
            checkpoint += timedelta(seconds=interval)
        integrity = market.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise CaptureReplayError("capture_shadow_market_integrity_failed")
        source_counts = {
            str(row["source_code"]): int(row["count"])
            for row in market.execute(
                "SELECT source_code,COUNT(*) AS count FROM market_observations WHERE quality_state='ELIGIBLE' GROUP BY source_code"
            ).fetchall()
        }
    finally:
        market.close()
        staging.close()
    payload = {
        "schema": "capture_shadow_replay",
        "schema_version": "1.0",
        "command_version": COMMAND_VERSION,
        "adapter_version": CAPTURE_ADAPTER_VERSION,
        "engine_version": COIN_RATE_ENGINE_VERSION,
        "start_utc": _iso(start),
        "end_utc": _iso(end),
        "interval_seconds": interval,
        "point_count": len(points),
        "input": {
            "records_seen": records,
            "records_rejected": rejected,
            "events_within_replay_horizon": index,
            "accepted": accepted,
            "duplicates": duplicates,
            "staged_changes": changes,
            "tombstones": tombstones,
        },
        "projection": projection_totals,
        "eligible_source_counts": source_counts,
        "points": points,
    }
    _write_report(report_path, payload)
    print(
        json.dumps(
            {
                "command": "replay",
                "version": COMMAND_VERSION,
                "status": "WRITTEN",
                "point_count": len(points),
                "records_seen": records,
                "records_rejected": rejected,
                "events_replayed": index,
                **projection_totals,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-market-store", required=True)
    parser.add_argument("--shadow-market-store", required=True)
    parser.add_argument("--shadow-staging-store", required=True)
    parser.add_argument("--output-report", required=True)
    parser.add_argument("--market-spool-dir", required=True)
    parser.add_argument("--coin-spool-dir", required=True)
    parser.add_argument("--start-utc", required=True)
    parser.add_argument("--end-utc", required=True)
    parser.add_argument("--interval-seconds", type=int, default=600)
    parser.add_argument("--maximum-records", type=int, default=250_000)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    previous_umask = os.umask(0o077)
    try:
        return _run(build_parser().parse_args(argv))
    except (CaptureReplayError, CaptureEventContractError, OSError, sqlite3.Error, ValueError) as exc:
        print(json.dumps({"command": "replay", "status": "FAILED", "reason": str(exc)}, sort_keys=True))
        return 2
    finally:
        os.umask(previous_umask)


if __name__ == "__main__":
    raise SystemExit(main())
