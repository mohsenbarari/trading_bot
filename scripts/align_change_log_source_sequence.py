#!/usr/bin/env python3
"""Preserve sync source-sequence monotonicity across server replacement."""

from __future__ import annotations

import argparse
import json
import os

import psycopg2
from psycopg2 import sql


CHANGE_LOG_TABLE = "change_log"
CHANGE_LOG_ID_COLUMN = "id"


def normalize_database_url(raw_url: str) -> str:
    return raw_url.replace("postgresql+psycopg2://", "postgresql://").replace(
        "postgresql+asyncpg://", "postgresql://"
    )


def plan_change_log_sequence_alignment(
    *,
    last_value: int,
    is_called: bool,
    max_change_id: int,
    required_floor: int,
) -> tuple[int | None, int]:
    """Return ``(setval_target, effective_next_value)`` for a locked sequence."""

    floor = int(required_floor)
    if floor < 0:
        raise ValueError("required_floor cannot be negative")

    current_last = int(last_value)
    current_next = current_last + 1 if is_called else current_last
    consumed_bound = current_last if is_called else current_last - 1
    required_bound = max(floor, int(max_change_id), consumed_bound)
    if current_next > required_bound:
        return None, current_next

    # The receiver has already observed every source sequence up to ``floor``.
    # Mark the bound as consumed so the next producer value is strictly newer.
    return required_bound, required_bound + 1


def read_source_watermark_floor(database_url: str, source_server: str) -> int:
    normalized_source = str(source_server or "").strip().lower()
    if normalized_source not in {"iran", "foreign"}:
        raise ValueError("source_server must be either iran or foreign")

    with psycopg2.connect(normalize_database_url(database_url), connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COALESCE(MAX(last_source_sequence), 0) "
                "FROM sync_apply_watermarks WHERE source_server = %s",
                (normalized_source,),
            )
            return int(cursor.fetchone()[0] or 0)


def align_change_log_sequence(database_url: str, required_floor: int) -> dict[str, int | str]:
    floor = int(required_floor)
    if floor < 0:
        raise ValueError("required_floor cannot be negative")

    with psycopg2.connect(normalize_database_url(database_url), connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_get_serial_sequence(%s, %s)",
                (CHANGE_LOG_TABLE, CHANGE_LOG_ID_COLUMN),
            )
            sequence_name = cursor.fetchone()[0]
            if not sequence_name:
                raise RuntimeError("change_log.id has no PostgreSQL sequence")

            sequence_identifier = sql.Identifier(*str(sequence_name).split("."))
            # Lock the sequence while reading its state and deciding whether it
            # must move forward. Gaps are safe; moving backward is forbidden.
            cursor.execute(
                sql.SQL("ALTER SEQUENCE {} INCREMENT BY 1").format(sequence_identifier)
            )
            cursor.execute(
                sql.SQL("SELECT last_value, is_called FROM {}").format(sequence_identifier)
            )
            last_value, is_called = cursor.fetchone()
            cursor.execute("SELECT COALESCE(MAX(id), 0) FROM change_log")
            max_change_id = int(cursor.fetchone()[0] or 0)
            target, next_value = plan_change_log_sequence_alignment(
                last_value=int(last_value),
                is_called=bool(is_called),
                max_change_id=max_change_id,
                required_floor=floor,
            )
            if target is not None:
                cursor.execute(
                    "SELECT setval(%s::regclass, %s, true)",
                    (sequence_name, target),
                )

    return {
        "status": "realigned" if target is not None else "verified",
        "required_floor": floor,
        "max_change_id": max_change_id,
        "next_value": next_value,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect receiver watermarks or align the local change_log sequence."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    floor = subparsers.add_parser(
        "watermark-floor",
        help="read the highest receiver watermark for one producer role",
    )
    floor.add_argument("--source-server", choices=("iran", "foreign"), required=True)
    floor.add_argument("--format", choices=("json", "value"), default="json")

    align = subparsers.add_parser(
        "align",
        help="ensure the next local change_log id is above a receiver watermark floor",
    )
    align.add_argument("--floor", type=int, required=True)
    return parser.parse_args()


def main() -> None:
    database_url = (os.environ.get("SYNC_DATABASE_URL") or "").strip()
    if not database_url:
        raise RuntimeError("SYNC_DATABASE_URL is required")
    args = parse_args()

    if args.command == "watermark-floor":
        watermark_floor = read_source_watermark_floor(database_url, args.source_server)
        if args.format == "value":
            print(watermark_floor)
        else:
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "source_server": args.source_server,
                        "watermark_floor": watermark_floor,
                    },
                    sort_keys=True,
                )
            )
        return

    if args.command == "align":
        print(json.dumps(align_change_log_sequence(database_url, args.floor), sort_keys=True))
        return

    raise RuntimeError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    main()
