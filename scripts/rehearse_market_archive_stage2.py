#!/usr/bin/env python3
"""Rehearse the isolated PostgreSQL market archive on disposable Docker state.

The command never connects to the product database.  It creates names scoped
to its own process, refuses pre-existing targets, binds PostgreSQL to loopback
on a random port, restores a logical backup into a second database, applies
the down migration, and removes both the container and volume in ``finally``.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time
from typing import Any, Sequence

import psycopg2


REPO_ROOT = Path(__file__).resolve().parents[1]
UP_MIGRATION = (
    REPO_ROOT / "deploy" / "market-data" / "migrations" / "0001_market_archive.up.sql"
)
DOWN_MIGRATION = (
    REPO_ROOT / "deploy" / "market-data" / "migrations" / "0001_market_archive.down.sql"
)
SOURCE_REGISTRY = REPO_ROOT / "config" / "market_data_sources.v1.json"
POSTGRES_IMAGE = "postgres:15-alpine"
SAFE_NAME = re.compile(r"^market-stage2-rehearsal-[0-9]+$")


class RehearsalError(RuntimeError):
    pass


def percentile(values: Sequence[float], ratio: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * ratio))]


def docker(
    arguments: Sequence[str],
    *,
    label: str,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["docker", *arguments],
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode:
        raise RehearsalError(f"{label}_failed_rc_{result.returncode}")
    return result


def ensure_unused(container: str, volume: str) -> None:
    if not SAFE_NAME.fullmatch(container) or not SAFE_NAME.fullmatch(volume):
        raise RehearsalError("unsafe_rehearsal_name")
    if docker(["container", "inspect", container], label="inspect_container", check=False).returncode == 0:
        raise RehearsalError("rehearsal_container_already_exists")
    if docker(["volume", "inspect", volume], label="inspect_volume", check=False).returncode == 0:
        raise RehearsalError("rehearsal_volume_already_exists")


def wait_for_postgres(container: str, timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = docker(
            ["exec", container, "pg_isready", "-U", "postgres"],
            label="postgres_ready",
            check=False,
        )
        if result.returncode == 0:
            return
        time.sleep(0.25)
    raise RehearsalError("postgres_readiness_timeout")


def mapped_port(container: str) -> int:
    output = docker(
        ["port", container, "5432/tcp"], label="inspect_postgres_port"
    ).stdout.strip()
    match = re.fullmatch(r"127\.0\.0\.1:([0-9]{1,5})", output)
    if not match:
        raise RehearsalError("postgres_not_loopback_bound")
    port = int(match.group(1))
    if not 1024 <= port <= 65535:
        raise RehearsalError("postgres_mapped_port_invalid")
    return port


def create_database(container: str, database: str, timeout_seconds: float = 10.0) -> None:
    if not database.startswith("market_archive_rehearsal"):
        raise RehearsalError("unsafe_database_name")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = docker(
            ["exec", container, "createdb", "-U", "postgres", database],
            label=f"create_{database}",
            check=False,
        )
        if result.returncode == 0:
            return
        time.sleep(0.25)
    raise RehearsalError(f"create_{database}_timeout")


def connect(port: int, database: str):
    if not database.startswith("market_archive_rehearsal"):
        raise RehearsalError("unsafe_database_name")
    return psycopg2.connect(
        host="127.0.0.1",
        port=port,
        user="postgres",
        dbname=database,
        connect_timeout=5,
    )


def seed_sql(rows: int) -> str:
    return f"""
    INSERT INTO market_data.capture_events (
        event_key, upstream_event_id, source_code, stream_id, source_sequence,
        upstream_schema, upstream_schema_version, event_type, occurred_at_utc,
        available_at_utc, persisted_at_utc, payload_hash, raw_payload,
        contains_pii, purge_after_utc
    )
    SELECT
        decode(md5('capture-event-' || n::text) || md5('capture-event-b-' || n::text), 'hex'),
        'fixture-' || n::text,
        'GROUP_1',
        'capture.coin.group.1',
        n,
        'coin_group_event',
        '2.0',
        'MESSAGE_CREATED',
        clock_timestamp() - make_interval(secs => (n % 600)) - interval '1 second',
        clock_timestamp() - make_interval(secs => (n % 600)) - interval '900 milliseconds',
        clock_timestamp(),
        decode(md5('capture-payload-' || n::text) || md5('capture-payload-b-' || n::text), 'hex'),
        jsonb_build_object('fixture', n),
        TRUE,
        clock_timestamp() + interval '3 days'
    FROM generate_series(1, {rows}) AS n;

    INSERT INTO market_data.market_facts (
        fact_id, event_key, origin_event_key, source_code, stream_id,
        source_sequence, occurred_at_utc, available_at_utc, persisted_at_utc,
        parser_version, fact_revision, fact_kind, quality_state,
        quality_reason_codes, payload_hash, payload, retention_class
    )
    SELECT
        decode(md5('fact-' || n::text) || md5('fact-b-' || n::text), 'hex'),
        decode(md5('event-' || n::text) || md5('event-b-' || n::text), 'hex'),
        decode(md5('capture-event-' || n::text) || md5('capture-event-b-' || n::text), 'hex'),
        'GROUP_1',
        'market.fact.coin.group.1',
        n,
        clock_timestamp() - make_interval(secs => (n % 600)) - interval '1 second',
        clock_timestamp() - make_interval(secs => (n % 600)) - interval '900 milliseconds',
        clock_timestamp(),
        'stage2-benchmark-v1',
        1,
        'COIN_OFFER',
        'ELIGIBLE',
        '{{}}',
        decode(md5('fact-payload-' || n::text) || md5('fact-payload-b-' || n::text), 'hex'),
        jsonb_build_object(
            'kind', 'COIN_OFFER',
            'instrument', 'COIN_IMAM',
            'price_value', (187000 + (n % 2000))::text,
            'price_unit', 'PROJECT_THOUSAND_TOMAN'
        ),
        'PERMANENT'
    FROM generate_series(1, {rows}) AS n;

    INSERT INTO market_data.market_fact_outbox (
        stream_id, delivery_sequence, fact_id, envelope, envelope_hash
    )
    SELECT
        'market.fact.coin.group.1',
        n,
        decode(md5('fact-' || n::text) || md5('fact-b-' || n::text), 'hex'),
        jsonb_build_object('contract', 'market_fact/1.0', 'sequence', n),
        decode(md5('outbox-' || n::text) || md5('outbox-b-' || n::text), 'hex')
    FROM generate_series(1, {rows}) AS n;
    """


def registry_rows() -> list[tuple[Any, ...]]:
    document = json.loads(SOURCE_REGISTRY.read_text(encoding="utf-8"))
    return sorted(
        (
            item["source_code"],
            item["capture_stream_id"],
            item["fact_stream_id"],
            item["capture_enabled"],
            item["permanent_archive"],
            item["raw_retention_seconds"],
            item["transfer_to_bot"],
            item["allowed_fact_kinds"],
        )
        for item in document["sources"]
    )


def benchmark(connection, iterations: int) -> dict[str, float]:
    query = """
        SELECT fact_id, available_at_utc, payload
        FROM market_data.market_facts
        WHERE quality_state = 'ELIGIBLE'
          AND source_code = 'GROUP_1'
          AND available_at_utc >= clock_timestamp() - interval '90 seconds'
        ORDER BY available_at_utc DESC
        LIMIT 100
    """
    outbox_query = """
        SELECT stream_id, delivery_sequence, fact_id
        FROM market_data.market_fact_outbox
        WHERE acknowledged_at_utc IS NULL
          AND stream_id = 'market.fact.coin.group.1'
          AND next_attempt_at_utc <= clock_timestamp()
        ORDER BY delivery_sequence
        LIMIT 100
    """
    snapshot_ms: list[float] = []
    outbox_ms: list[float] = []
    with connection.cursor() as cursor:
        for _ in range(iterations):
            started = time.perf_counter()
            cursor.execute(query)
            cursor.fetchall()
            snapshot_ms.append((time.perf_counter() - started) * 1000)
            started = time.perf_counter()
            cursor.execute(outbox_query)
            cursor.fetchall()
            outbox_ms.append((time.perf_counter() - started) * 1000)
    return {
        "snapshot_p50_ms": round(statistics.median(snapshot_ms), 3),
        "snapshot_p95_ms": round(percentile(snapshot_ms, 0.95), 3),
        "outbox_p50_ms": round(statistics.median(outbox_ms), 3),
        "outbox_p95_ms": round(percentile(outbox_ms, 0.95), 3),
    }


def run(rows: int, iterations: int) -> dict[str, Any]:
    suffix = str(os.getpid())
    container = f"market-stage2-rehearsal-{suffix}"
    volume = f"market-stage2-rehearsal-{suffix}"
    ensure_unused(container, volume)
    cleanup = {"container_removed": False, "volume_removed": False}
    result: dict[str, Any] = {}
    try:
        docker(["volume", "create", volume], label="create_volume")
        docker(
            [
                "run",
                "-d",
                "--name",
                container,
                "--mount",
                f"type=volume,source={volume},target=/var/lib/postgresql/data",
                "-p",
                "127.0.0.1::5432",
                "-e",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                POSTGRES_IMAGE,
            ],
            label="start_postgres",
        )
        wait_for_postgres(container)
        port = mapped_port(container)
        for database in ("market_archive_rehearsal", "market_archive_rehearsal_restore"):
            create_database(container, database)

        connection = connect(port, "market_archive_rehearsal")
        connection.autocommit = True
        try:
            with connection.cursor() as cursor:
                cursor.execute(UP_MIGRATION.read_text(encoding="utf-8"))
                cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = 'market_data'"
                )
                table_count = int(cursor.fetchone()[0])
                cursor.execute(
                    "SELECT source_code,capture_stream_id,fact_stream_id,capture_enabled,"
                    "permanent_archive,raw_retention_seconds,transfer_to_bot,allowed_fact_kinds "
                    "FROM market_data.source_registry ORDER BY source_code"
                )
                database_registry = cursor.fetchall()
            if database_registry != registry_rows():
                raise RehearsalError("migration_source_registry_mismatch")

            started = time.perf_counter()
            with connection.cursor() as cursor:
                cursor.execute(seed_sql(rows))
            insert_seconds = time.perf_counter() - started
            query_metrics = benchmark(connection, iterations)
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_database_size(current_database())")
                database_bytes = int(cursor.fetchone()[0])
        finally:
            connection.close()

        backup_path = "/var/lib/postgresql/data/market_archive_rehearsal.dump"
        docker(
            [
                "exec",
                container,
                "pg_dump",
                "-U",
                "postgres",
                "-Fc",
                "-d",
                "market_archive_rehearsal",
                "-f",
                backup_path,
            ],
            label="logical_backup",
        )
        backup_bytes = int(
            docker(
                ["exec", container, "stat", "-c", "%s", backup_path],
                label="backup_size",
            ).stdout.strip()
        )
        restore_started = time.perf_counter()
        docker(
            [
                "exec",
                container,
                "pg_restore",
                "-U",
                "postgres",
                "-d",
                "market_archive_rehearsal_restore",
                backup_path,
            ],
            label="logical_restore",
        )
        restore_seconds = time.perf_counter() - restore_started

        restored = connect(port, "market_archive_rehearsal_restore")
        restored.autocommit = True
        try:
            with restored.cursor() as cursor:
                cursor.execute(
                    "SELECT (SELECT COUNT(*) FROM market_data.market_facts),"
                    "(SELECT COUNT(*) FROM market_data.capture_events),"
                    "(SELECT COUNT(*) FROM market_data.market_fact_outbox),"
                    "(SELECT COUNT(*) FROM market_data.source_registry)"
                )
                restored_counts = tuple(int(value) for value in cursor.fetchone())
        finally:
            restored.close()
        if restored_counts != (rows, rows, rows, len(registry_rows())):
            raise RehearsalError("restore_count_mismatch")

        original = connect(port, "market_archive_rehearsal")
        original.autocommit = True
        try:
            with original.cursor() as cursor:
                cursor.execute(DOWN_MIGRATION.read_text(encoding="utf-8"))
                cursor.execute("SELECT to_regnamespace('market_data')")
                if cursor.fetchone()[0] is not None:
                    raise RehearsalError("down_migration_left_schema")
        finally:
            original.close()

        result = {
            "status": "pass",
            "postgres_major": 15,
            "rows_per_primary_table": rows,
            "market_table_count": table_count,
            "insert_rows_per_second": round((rows * 3) / max(insert_seconds, 0.000001), 1),
            "database_mib": round(database_bytes / (1024 * 1024), 3),
            "backup_mib": round(backup_bytes / (1024 * 1024), 3),
            "restore_seconds": round(restore_seconds, 3),
            "restore_counts_match": True,
            "source_registry_match": True,
            "down_migration_clean": True,
            "query": query_metrics,
        }
    finally:
        removed = docker(
            ["rm", "-f", container], label="remove_container", check=False
        )
        cleanup["container_removed"] = removed.returncode == 0
        removed = docker(
            ["volume", "rm", volume], label="remove_volume", check=False
        )
        cleanup["volume_removed"] = removed.returncode == 0
    result["cleanup"] = cleanup
    if not all(cleanup.values()):
        raise RehearsalError("rehearsal_cleanup_failed")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=50_000)
    parser.add_argument("--iterations", type=int, default=200)
    args = parser.parse_args(argv)
    if not 1_000 <= args.rows <= 500_000:
        parser.error("--rows must be between 1000 and 500000")
    if not 10 <= args.iterations <= 2_000:
        parser.error("--iterations must be between 10 and 2000")
    try:
        result = run(args.rows, args.iterations)
    except (OSError, ValueError, RehearsalError, psycopg2.Error) as exc:
        print(
            json.dumps(
                {"status": "fail", "reason": str(exc)},
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
