#!/usr/bin/env python3
"""Export a bounded, privacy-minimized parser calibration seed.

The command reads active SQLite databases query-only.  It does not copy raw
database files: only the feedback contract and the bounded MAIN_ONLINE columns
needed by the new parser are written into atomically published SQLite files.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Callable


FEEDBACK_COLUMNS = (
    "event_key",
    "event_type",
    "group_number",
    "source_event_time_utc",
    "ambiguous_fields_json",
    "event_confirmed",
    "commodity_code",
    "side",
    "price_project_thousand_toman",
    "quantity",
    "settlement_term",
    "trade_form",
    "is_conditional",
    "reviewer_digest",
    "review_revision",
    "reviewed_at_utc",
    "applied_revision",
    "applied_at_utc",
    "application_count",
)
PREDICTION_COLUMNS = (
    "id",
    "prediction_time_utc",
    "created_at_utc",
    "model_id",
    "commodity",
    "settlement",
    "estimated_price_toman",
)


class CalibrationSeedError(RuntimeError):
    pass


def _utc(value: datetime | None = None) -> datetime:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(
        microsecond=0
    )


def _staging_root(value: Path) -> Path:
    if not value.is_absolute():
        raise CalibrationSeedError("calibration_seed_root_must_be_absolute")
    resolved = value.resolve(strict=False)
    if len(resolved.parts) < 4 or not any(
        "staging" in part.lower() for part in resolved.parts
    ):
        raise CalibrationSeedError("calibration_seed_root_must_be_staging_scoped")
    if resolved.exists() and resolved.is_symlink():
        raise CalibrationSeedError("calibration_seed_root_symlink_forbidden")
    resolved.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(resolved, 0o700)
    return resolved


def _source(path: Path) -> sqlite3.Connection:
    database = path.resolve(strict=True)
    connection = sqlite3.connect(
        f"file:{database}?mode=ro", uri=True, timeout=30, isolation_level=None
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    return connection


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _atomic_database(
    destination: Path,
    writer: Callable[[sqlite3.Connection], int],
) -> int:
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.pending")
    temporary.unlink(missing_ok=True)
    output = sqlite3.connect(temporary)
    try:
        output.execute("PRAGMA journal_mode=DELETE")
        output.execute("PRAGMA synchronous=FULL")
        count = writer(output)
        output.commit()
        check = output.execute("PRAGMA quick_check").fetchone()
        if check is None or check[0] != "ok":
            raise CalibrationSeedError("calibration_seed_integrity_failed")
    except BaseException:
        output.close()
        temporary.unlink(missing_ok=True)
        raise
    output.close()
    os.chmod(temporary, 0o600)
    descriptor = os.open(temporary, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, destination)
    parent = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    return count


def export_feedback(source: sqlite3.Connection, destination: Path) -> int:
    if not set(FEEDBACK_COLUMNS).issubset(
        _columns(source, "coin_group_parser_feedback")
    ):
        raise CalibrationSeedError("calibration_feedback_schema_invalid")
    state = source.execute(
        "SELECT schema_version,calibration_revision,updated_at_utc "
        "FROM coin_group_parser_feedback_state WHERE singleton=1"
    ).fetchone()
    if state is None or int(state["schema_version"]) != 1:
        raise CalibrationSeedError("calibration_feedback_state_invalid")
    rows = source.execute(
        f"SELECT {','.join(FEEDBACK_COLUMNS)} FROM coin_group_parser_feedback "
        "ORDER BY reviewed_at_utc,event_key"
    ).fetchall()

    def write(output: sqlite3.Connection) -> int:
        from core.market_intelligence.coin_group_feedback import _SCHEMA

        output.executescript(_SCHEMA)
        output.execute(
            "INSERT OR REPLACE INTO coin_group_parser_feedback_state "
            "VALUES(1,?,?,?)",
            (
                int(state["schema_version"]),
                int(state["calibration_revision"]),
                str(state["updated_at_utc"]),
            ),
        )
        placeholders = ",".join("?" for _ in FEEDBACK_COLUMNS)
        output.executemany(
            f"INSERT INTO coin_group_parser_feedback({','.join(FEEDBACK_COLUMNS)}) "
            f"VALUES({placeholders})",
            (tuple(row[column] for column in FEEDBACK_COLUMNS) for row in rows),
        )
        return len(rows)

    return _atomic_database(destination, write)


def export_predictions(
    source: sqlite3.Connection,
    destination: Path,
    *,
    cutoff_utc: str,
    as_of_utc: str,
) -> int:
    if not set(PREDICTION_COLUMNS).issubset(
        _columns(source, "coin_estimate_predictions")
    ):
        raise CalibrationSeedError("calibration_prediction_schema_invalid")
    rows = source.execute(
        f"SELECT {','.join(PREDICTION_COLUMNS)} FROM coin_estimate_predictions "
        "WHERE model_id='MAIN_ONLINE' AND prediction_time_utc>=? "
        "AND prediction_time_utc<=? AND created_at_utc<=? "
        "ORDER BY prediction_time_utc,id",
        (cutoff_utc, as_of_utc, as_of_utc),
    ).fetchall()

    def write(output: sqlite3.Connection) -> int:
        output.executescript(
            """
            CREATE TABLE coin_estimate_predictions(
              id INTEGER PRIMARY KEY,
              prediction_time_utc TEXT NOT NULL,
              created_at_utc TEXT NOT NULL,
              model_id TEXT NOT NULL,
              commodity TEXT NOT NULL,
              settlement TEXT NOT NULL,
              estimated_price_toman INTEGER NOT NULL
            );
            CREATE INDEX coin_estimate_predictions_causal_idx
            ON coin_estimate_predictions(model_id,prediction_time_utc,created_at_utc);
            """
        )
        placeholders = ",".join("?" for _ in PREDICTION_COLUMNS)
        output.executemany(
            f"INSERT INTO coin_estimate_predictions({','.join(PREDICTION_COLUMNS)}) "
            f"VALUES({placeholders})",
            (tuple(row[column] for column in PREDICTION_COLUMNS) for row in rows),
        )
        return len(rows)

    return _atomic_database(destination, write)


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def export_seed(
    *,
    feedback_source: Path,
    prediction_source: Path,
    destination_root: Path,
    window_hours: int = 12,
    as_of: datetime | None = None,
) -> dict[str, object]:
    if not 8 <= int(window_hours) <= 72:
        raise CalibrationSeedError("calibration_seed_window_invalid")
    root = _staging_root(destination_root)
    now = _utc(as_of)
    as_of_utc = now.isoformat().replace("+00:00", "Z")
    cutoff_utc = (now - timedelta(hours=window_hours)).isoformat().replace(
        "+00:00", "Z"
    )
    feedback = _source(feedback_source)
    predictions = _source(prediction_source)
    try:
        feedback_path = root / "review-decisions.sqlite3"
        prediction_path = root / "prediction-ledger.sqlite3"
        feedback_count = export_feedback(feedback, feedback_path)
        prediction_count = export_predictions(
            predictions,
            prediction_path,
            cutoff_utc=cutoff_utc,
            as_of_utc=as_of_utc,
        )
    finally:
        feedback.rollback()
        feedback.close()
        predictions.rollback()
        predictions.close()
    return {
        "schema": "market_calibration_seed_receipt/1.0",
        "as_of_utc": as_of_utc,
        "window_hours": int(window_hours),
        "feedback_rows": feedback_count,
        "prediction_rows": prediction_count,
        "feedback_sha256": _digest(feedback_path),
        "prediction_sha256": _digest(prediction_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feedback-source", type=Path, required=True)
    parser.add_argument("--prediction-source", type=Path, required=True)
    parser.add_argument("--destination-root", type=Path, required=True)
    parser.add_argument("--window-hours", type=int, default=12)
    args = parser.parse_args()
    try:
        receipt = export_seed(
            feedback_source=args.feedback_source,
            prediction_source=args.prediction_source,
            destination_root=args.destination_root,
            window_hours=args.window_hours,
        )
    except (CalibrationSeedError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"status": "fail", "reason_code": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"status": "pass", **receipt}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
