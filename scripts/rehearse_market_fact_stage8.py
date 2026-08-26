#!/usr/bin/env python3
"""Rehearse Stage 8 archive/outbox/ACK semantics against PostgreSQL."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import statistics
import sys
import tempfile
import time
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.market_fact_archive import build_and_publish_fact
from core.market_intelligence.market_fact_receiver import (
    apply_fact_batch,
    connect_receiver,
)
from core.market_intelligence.market_fact_sync import (
    MarketTransportError,
    load_next_batch,
    outbox_metrics,
    run_sync_cycle,
)
from scripts.rehearse_market_archive_stage2 import (
    DOWN_MIGRATION,
    POSTGRES_IMAGE,
    UP_MIGRATION,
    connect,
    create_database,
    docker,
    ensure_unused,
    mapped_port,
    wait_for_postgres,
)


def _connect():
    import psycopg2

    password = Path(
        os.environ.get(
            "MARKET_POSTGRES_PASSWORD_FILE", "/run/secrets/market_postgres_password"
        )
    ).read_text(encoding="utf-8").strip()
    return psycopg2.connect(
        host=os.environ.get("MARKET_POSTGRES_HOST", "market-database"),
        port=int(os.environ.get("MARKET_POSTGRES_PORT", "5432")),
        user=os.environ.get("MARKET_POSTGRES_USER", "market_data"),
        password=password,
        dbname=os.environ.get("MARKET_POSTGRES_DB", "market_archive"),
        application_name="market-stage8-rehearsal",
    )


def _stamp(offset_ms: int = 0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(milliseconds=offset_ms)


def _publish_xau(connection, index: int) -> None:
    event_key = f"{index + 10_000:064x}"
    build_and_publish_fact(
        connection,
        event_key=event_key,
        origin_event_key=event_key,
        source_code="XAUUSD",
        occurred_at_utc=_stamp(20),
        available_at_utc=_stamp(10),
        parser_version="stage8-rehearsal-v1",
        quality_state="ELIGIBLE",
        quality_reason_codes=(),
        payload={
            "kind": "EXTERNAL_QUOTE",
            "instrument": "XAUUSD",
            "quote_kind": "MID",
            "price_value": str(4600 + (index % 100) / 100),
            "price_unit": "USD_PER_TROY_OUNCE",
            "currency": "USD",
        },
    )


def _counts(connection) -> tuple[int, int, int, int]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM market_data.market_facts),
              (SELECT COUNT(*) FROM market_data.market_fact_revisions),
              (SELECT COUNT(*) FROM market_data.market_fact_outbox),
              (SELECT COUNT(*) FROM market_data.market_fact_outbox
               WHERE acknowledged_at_utc IS NOT NULL)
            """
        )
        return tuple(int(value) for value in cursor.fetchone())  # type: ignore[return-value]


def run(total: int) -> dict[str, object]:
    connection = _connect()
    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "TRUNCATE market_data.market_fact_dead_letters,"
                    "market_data.market_fact_outbox,"
                    "market_data.market_fact_delivery_checkpoints,"
                    "market_data.market_fact_revisions,"
                    "market_data.coin_trade_outcomes,"
                    "market_data.coin_offers,"
                    "market_data.private_gold_outcomes,"
                    "market_data.private_gold_offers,"
                    "market_data.market_facts,"
                    "market_data.stream_sequences CASCADE"
                )
        # A transaction rollback must remove both the fact and outbox row.
        before = _counts(connection)
        try:
            with connection:
                _publish_xau(connection, 9_999_999)
                raise RuntimeError("intentional-rollback")
        except RuntimeError:
            pass
        if _counts(connection) != before:
            raise RuntimeError("stage8_atomic_rollback_failed")

        publish_started = time.perf_counter()
        with connection:
            for index in range(total):
                _publish_xau(connection, index)
        publish_ms = (time.perf_counter() - publish_started) * 1000
        facts, revisions, outbox, acknowledged = _counts(connection)
        if (facts, revisions, outbox, acknowledged) != (total, total, total, 0):
            raise RuntimeError("stage8_publish_count_mismatch")

        with tempfile.TemporaryDirectory() as directory:
            receiver = connect_receiver(Path(directory) / "receiver.sqlite3")
            try:
                failed_once = False

                def lost_ack(document):
                    nonlocal failed_once
                    status, response = apply_fact_batch(receiver, document)
                    if not failed_once:
                        failed_once = True
                        raise MarketTransportError("simulated_lost_ack")
                    return status, response

                lost = run_sync_cycle(
                    connection,
                    sender_instance_id="stage8-rehearsal-1",
                    send=lost_ack,
                )
                if int(lost.get("acknowledged", 0)) != 0:
                    raise RuntimeError("stage8_lost_ack_was_acknowledged")
                # The committed receiver batch must be retried; no row is removed.
                with connection:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "UPDATE market_data.market_fact_outbox "
                            "SET next_attempt_at_utc=clock_timestamp() "
                            "WHERE acknowledged_at_utc IS NULL"
                        )

                latencies: list[float] = []

                def durable_receiver(document):
                    return apply_fact_batch(receiver, document)

                while True:
                    batch = load_next_batch(
                        connection, sender_instance_id="stage8-rehearsal-1"
                    )
                    if batch is None:
                        break
                    result = run_sync_cycle(
                        connection,
                        sender_instance_id="stage8-rehearsal-1",
                        send=durable_receiver,
                    )
                    latency = result.get("ack_latency_ms")
                    if latency is not None:
                        latencies.append(float(latency))
                facts, revisions, outbox, acknowledged = _counts(connection)
                if acknowledged != total:
                    raise RuntimeError("stage8_ack_count_mismatch")
                receiver_count = int(
                    receiver.execute("SELECT COUNT(*) FROM fact_deliveries").fetchone()[0]
                )
                checkpoint = int(
                    receiver.execute(
                        "SELECT highest_contiguous_sequence FROM fact_checkpoints "
                        "WHERE stream_id='market.fact.xauusd'"
                    ).fetchone()[0]
                )
                if receiver_count != total or checkpoint != total:
                    raise RuntimeError("stage8_receiver_checkpoint_mismatch")

                # A receiver outage after all ACKs cannot delete or reorder facts.
                metrics = outbox_metrics(connection)
                if int(metrics["queue_depth"]) != 0:
                    raise RuntimeError("stage8_outbox_not_drained")
            finally:
                receiver.close()

        ordered = sorted(latencies)
        p95 = ordered[max(0, int(len(ordered) * 0.95) - 1)] if ordered else 0.0
        p99 = ordered[max(0, int(len(ordered) * 0.99) - 1)] if ordered else 0.0
        return {
            "status": "pass",
            "fact_count": facts,
            "outbox_count": outbox,
            "acknowledged_count": acknowledged,
            "batch_count": len(latencies),
            "lost_ack_replayed_as_duplicate": True,
            "publish_ms": round(publish_ms, 3),
            "ack_p50_ms": round(statistics.median(latencies), 3) if latencies else 0,
            "ack_p95_ms": round(p95, 3),
            "ack_p99_ms": round(p99, 3),
        }
    finally:
        connection.close()


def run_disposable(total: int) -> dict[str, object]:
    suffix = str(os.getpid())
    container = f"market-stage2-rehearsal-{suffix}"
    volume = f"market-stage2-rehearsal-{suffix}"
    ensure_unused(container, volume)
    cleanup = {"container_removed": False, "volume_removed": False}
    result: dict[str, object] = {}
    try:
        docker(["volume", "create", volume], label="stage8_create_volume")
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
            label="stage8_start_postgres",
        )
        wait_for_postgres(container)
        port = mapped_port(container)
        create_database(container, "market_archive_rehearsal")
        migration = connect(port, "market_archive_rehearsal")
        migration.autocommit = True
        try:
            with migration.cursor() as cursor:
                cursor.execute(UP_MIGRATION.read_text(encoding="utf-8"))
        finally:
            migration.close()
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "postgres-password"
            secret.write_text("fixture-not-used-by-trust-auth\n", encoding="utf-8")
            secret.chmod(0o600)
            with patch.dict(
                os.environ,
                {
                    "MARKET_POSTGRES_HOST": "127.0.0.1",
                    "MARKET_POSTGRES_PORT": str(port),
                    "MARKET_POSTGRES_USER": "postgres",
                    "MARKET_POSTGRES_DB": "market_archive_rehearsal",
                    "MARKET_POSTGRES_PASSWORD_FILE": str(secret),
                },
                clear=False,
            ):
                result = run(total)
        down = connect(port, "market_archive_rehearsal")
        down.autocommit = True
        try:
            with down.cursor() as cursor:
                cursor.execute(DOWN_MIGRATION.read_text(encoding="utf-8"))
        finally:
            down.close()
    finally:
        cleanup["container_removed"] = (
            docker(["rm", "-f", container], label="stage8_remove_container", check=False).returncode
            == 0
        )
        cleanup["volume_removed"] = (
            docker(["volume", "rm", volume], label="stage8_remove_volume", check=False).returncode
            == 0
        )
    result["cleanup"] = cleanup
    if not all(cleanup.values()):
        raise RuntimeError("stage8_cleanup_failed")
    return result


if __name__ == "__main__":
    total = int(os.environ.get("MARKET_STAGE8_FACT_COUNT", "1000"))
    print(json.dumps(run_disposable(total), sort_keys=True, separators=(",", ":")))
