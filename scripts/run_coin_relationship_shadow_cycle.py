#!/usr/bin/env python3
"""Run one serial, non-authoritative durable relationship-research cycle."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.relationship_ledger import (
    append_labels,
    append_melted_features,
)


def _outside_repository(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise ValueError("relationship_cycle_runtime_path_inside_repository")
    return resolved


def _load_jsonl(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _run(command: list[str]) -> dict:
    try:
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=1800,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("relationship_cycle_child_timeout") from exc
    if result.returncode != 0:
        raise ValueError("relationship_cycle_child_failed")
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise ValueError("relationship_cycle_child_output_invalid") from exc


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _earliest_confirmed_trade_with_lookback(path: Path) -> str:
    """Bound discovery to current label coverage plus its longest feature window."""

    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    try:
        value = connection.execute(
            "SELECT MIN(occurred_at_utc) FROM confirmed_trade_training_examples"
        ).fetchone()[0]
    except sqlite3.OperationalError as exc:
        raise ValueError("relationship_cycle_coin_trade_schema_invalid") from exc
    finally:
        connection.close()
    if value is None:
        raise ValueError("relationship_cycle_no_confirmed_coin_trade")
    from datetime import datetime, timedelta, timezone

    earliest = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if earliest.tzinfo is None or earliest.utcoffset() is None:
        raise ValueError("relationship_cycle_coin_trade_time_invalid")
    bounded = earliest.astimezone(timezone.utc) - timedelta(minutes=20)
    return bounded.isoformat().replace("+00:00", "Z")


def _earliest_canonical_trade_with_lookback(path: Path) -> str:
    """Use Market Store availability time; offers are never label evidence."""

    from core.market_intelligence.market_store import (
        connect_market_store_read_only,
        verify_market_store_read_only,
    )

    connection = connect_market_store_read_only(path)
    verify_market_store_read_only(connection)
    try:
        value = connection.execute(
            """
            SELECT MIN(available_at_utc)
            FROM (
                SELECT available_at_utc, instrument, event_type, price_unit,
                       quality_state, is_conditional
                FROM market_observations
                UNION ALL
                SELECT available_at_utc, instrument, event_type, price_unit,
                       quality_state, is_conditional
                FROM market_observations_archive
            )
            WHERE event_type = 'TRADE'
              AND quality_state = 'ELIGIBLE'
              AND is_conditional = 0
              AND instrument LIKE 'COIN_%'
              AND instrument != 'COIN_PUBLIC_CHANNEL'
              AND price_unit = 'PROJECT_THOUSAND_TOMAN'
            """
        ).fetchone()[0]
    finally:
        connection.close()
    if value is None:
        raise ValueError("relationship_cycle_no_canonical_confirmed_coin_trade")
    from datetime import datetime, timedelta, timezone

    earliest = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if earliest.tzinfo is None or earliest.utcoffset() is None:
        raise ValueError("relationship_cycle_coin_trade_time_invalid")
    bounded = earliest.astimezone(timezone.utc) - timedelta(minutes=20)
    return bounded.isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acknowledge-shadow-only", action="store_true")
    parser.add_argument("--market-store", type=Path, required=True)
    parser.add_argument("--coin-trade-target-db", type=Path)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--retention-days", type=int, default=180)
    args = parser.parse_args()
    if not args.acknowledge_shadow_only:
        raise SystemExit("--acknowledge-shadow-only is required")
    if args.retention_days <= 0:
        raise SystemExit("--retention-days must be positive")
    market_store = _outside_repository(args.market_store)
    coin_trade_db = (
        _outside_repository(args.coin_trade_target_db)
        if args.coin_trade_target_db is not None
        else None
    )
    runtime_root = _outside_repository(args.runtime_root)
    if not market_store.exists() or (
        coin_trade_db is not None and not coin_trade_db.exists()
    ):
        raise SystemExit("relationship_cycle_input_database_missing")
    try:
        since_utc = _earliest_canonical_trade_with_lookback(market_store)
    except ValueError as exc:
        if (
            str(exc) != "relationship_cycle_no_canonical_confirmed_coin_trade"
            or coin_trade_db is None
        ):
            raise
        since_utc = _earliest_confirmed_trade_with_lookback(coin_trade_db)
    runtime_root.mkdir(parents=True, exist_ok=True)
    lock_path = runtime_root / "relationship_cycle.lock"
    labels_path = runtime_root / "current_coin_intrinsic_labels.jsonl"
    melted_features_path = runtime_root / "current_melted_relationship_features.jsonl"
    discovery_report = runtime_root / "latest_discovery_report.json"
    ledger_path = runtime_root / "coin_intrinsic_labels.sqlite3"
    append_report = runtime_root / "latest_ledger_append_report.json"
    melted_append_report = runtime_root / "latest_melted_ledger_append_report.json"
    challenger_report = runtime_root / "latest_challenger_report.json"
    melted_challenger_report = runtime_root / "latest_melted_challenger_report.json"
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"status": "SHADOW_RELATIONSHIP_CYCLE_ALREADY_RUNNING"}))
            return 0
        discovery_command = [
            sys.executable,
            str(
                REPO_ROOT
                / "scripts"
                / "discover_melted_market_relationships_shadow.py"
            ),
            "--acknowledge-shadow-only",
            "--market-store",
            str(market_store),
            "--since",
            since_utc,
            "--report",
            str(discovery_report),
            "--dataset",
            str(melted_features_path),
            "--coin-intrinsic-dataset",
            str(labels_path),
        ]
        if coin_trade_db is not None:
            discovery_command.extend(
                ["--coin-trade-target-db", str(coin_trade_db)]
            )
        discovery = _run(discovery_command)
        ledger = append_labels(
            ledger_path,
            _load_jsonl(labels_path),
            retention_days=args.retention_days,
        )
        ledger.update({"status": "SHADOW_LEDGER_UPDATED", "automatic_promotion": False})
        _write_json_atomic(append_report, ledger)
        melted_ledger = append_melted_features(
            ledger_path,
            _load_jsonl(melted_features_path),
            retention_days=args.retention_days,
        )
        melted_ledger.update(
            {"status": "SHADOW_MELTED_LEDGER_UPDATED", "automatic_promotion": False}
        )
        _write_json_atomic(melted_append_report, melted_ledger)
        melted_challenger = _run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "train_melted_relationship_challenger_shadow.py"),
                "--acknowledge-shadow-only",
                "--ledger", str(ledger_path),
                "--report", str(melted_challenger_report),
            ]
        )
        challenger = _run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "train_coin_bubble_relationship_challenger_shadow.py"),
                "--acknowledge-shadow-only",
                "--ledger", str(ledger_path),
                "--report", str(challenger_report),
            ]
        )
        # These are rebuildable transport files.  Both datasets have already
        # committed atomically to the durable ledger before this cleanup.
        labels_path.unlink(missing_ok=True)
        melted_features_path.unlink(missing_ok=True)
    result = {
        "status": "SHADOW_RELATIONSHIP_CYCLE_COMPLETE",
        "automatic_promotion": False,
        "discovery": discovery,
        "ledger": ledger,
        "melted_ledger": melted_ledger,
        "melted_challenger": melted_challenger,
        "challenger": challenger,
        "runtime_artifacts": "outside_repository",
        "discovery_since_utc": since_utc,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
