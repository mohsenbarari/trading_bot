#!/usr/bin/env python3
"""Write a privacy-safe point-in-time coin-rate timeline from Market Store."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.coin_rate_engine import (  # noqa: E402
    COIN_RATE_ENGINE_VERSION,
    build_coin_rate_estimates,
)
from core.market_intelligence.market_contracts import normalize_utc  # noqa: E402


COMMAND_VERSION = "coin-rate-timeline-v1"


class TimelineError(RuntimeError):
    pass


def _external(value: str, *, field: str, exists: bool) -> Path:
    path = Path(value).expanduser().resolve()
    try:
        path.relative_to(REPO_ROOT.resolve())
    except ValueError:
        pass
    else:
        raise TimelineError(f"{field}_inside_repository")
    if path.is_symlink() or (exists and not path.is_file()):
        raise TimelineError(f"{field}_unavailable")
    if not exists and (not path.parent.is_dir() or path.parent.is_symlink()):
        raise TimelineError(f"{field}_parent_unavailable")
    return path


def _datetime(value: str, *, field: str) -> datetime:
    normalized = normalize_utc(value, field_name=field)
    return datetime.fromisoformat(normalized.replace("Z", "+00:00"))


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_timeline(
    connection: sqlite3.Connection,
    *,
    start_utc: datetime,
    end_utc: datetime,
    interval_seconds: int,
) -> dict[str, object]:
    points: list[dict[str, object]] = []
    current = start_utc
    while current <= end_utc:
        estimates = build_coin_rate_estimates(connection, as_of_utc=current)
        points.append(
            {
                "as_of_utc": _iso(current),
                "estimated_count": sum(item.status == "ESTIMATED" for item in estimates),
                "rates": [item.to_dict() for item in estimates],
            }
        )
        current += timedelta(seconds=interval_seconds)
    return {
        "schema": "coin_rate_timeline",
        "schema_version": "1.0",
        "command_version": COMMAND_VERSION,
        "engine_version": COIN_RATE_ENGINE_VERSION,
        "start_utc": _iso(start_utc),
        "end_utc": _iso(end_utc),
        "interval_seconds": interval_seconds,
        "point_count": len(points),
        "points": points,
    }


def write_atomic(path: Path, payload: dict[str, object]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _run(args: argparse.Namespace) -> int:
    store = _external(args.market_store, field="market_store", exists=True)
    output = _external(args.output, field="output", exists=False)
    start = _datetime(args.start_utc, field="timeline_start_utc")
    end = _datetime(args.end_utc, field="timeline_end_utc")
    interval = int(args.interval_seconds)
    if end < start or interval <= 0 or interval > 3600:
        raise TimelineError("timeline_range_invalid")
    connection = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        payload = build_timeline(
            connection,
            start_utc=start,
            end_utc=end,
            interval_seconds=interval,
        )
    finally:
        connection.close()
    write_atomic(output, payload)
    print(
        json.dumps(
            {
                "command": "timeline",
                "version": COMMAND_VERSION,
                "status": "WRITTEN",
                "point_count": payload["point_count"],
                "engine_version": COIN_RATE_ENGINE_VERSION,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-store", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--start-utc", required=True)
    parser.add_argument("--end-utc", required=True)
    parser.add_argument("--interval-seconds", type=int, default=600)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return _run(build_parser().parse_args(argv))
    except (TimelineError, OSError, sqlite3.Error, ValueError) as exc:
        print(json.dumps({"command": "timeline", "status": "FAILED", "reason": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
