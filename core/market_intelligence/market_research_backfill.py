"""Idempotent raw-text consolidation from bounded capture staging into PostgreSQL.

The command never prints message text, Telegram IDs, names, or source message
IDs.  It can safely consume overlapping legacy/new staging databases because
both raw-message identity and fact linkage are deterministic.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
from typing import Iterable, Iterator, Sequence

from .coin_groups import CoinGroupMessageInput, parse_coin_group_offers
from .market_contracts import derive_event_key
from .research_archive import (
    ResearchActor,
    ResearchArchiveKey,
    ResearchFactContext,
    archive_research_actors,
    archive_research_message,
    link_research_message_to_fact,
)


_CHANNEL_SOURCES = frozenset(
    {"MELTED_PRIMARY_FLOW", "MELTED_AGGREGATE", "MELTED_FLOW"}
)
_PRIMARY = "MELTED_PRIMARY_FLOW"


class MarketResearchBackfillError(RuntimeError):
    """A content-free backfill failure."""


@dataclass(frozen=True, slots=True)
class Candidate:
    context: ResearchFactContext
    event_keys: tuple[bytes, ...]


def _table(connection: sqlite3.Connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def _columns(connection: sqlite3.Connection, name: str) -> frozenset[str]:
    return frozenset(str(row[1]) for row in connection.execute(f"PRAGMA table_info({name})"))


def _coin_candidates(connection: sqlite3.Connection) -> Iterator[Candidate]:
    if not _table(connection, "coin_group_staged_messages"):
        return
    columns = _columns(connection, "coin_group_staged_messages")
    has_identity = {"sender_telegram_id", "sender_display_name"} <= columns
    identity_select = (
        "sender_telegram_id,sender_display_name"
        if has_identity
        else "NULL AS sender_telegram_id,NULL AS sender_display_name"
    )
    rows = connection.execute(
        "SELECT group_number,message_id,event_time_utc,available_at_utc,"
        f"message_text,{identity_select} FROM coin_group_staged_messages "
        "ORDER BY event_time_utc,group_number,message_id"
    )
    for row in rows:
        group = int(row["group_number"])
        message_id = int(row["message_id"])
        try:
            offers = parse_coin_group_offers(
                CoinGroupMessageInput(
                    group_number=group,
                    source_event_id=message_id,
                    published_at_utc=str(row["event_time_utc"]),
                    available_at_utc=str(row["available_at_utc"]),
                    text=str(row["message_text"]),
                )
            )
        except (TypeError, ValueError):
            continue
        if not offers:
            continue
        actors = {}
        if row["sender_telegram_id"] is not None:
            actors["OFFERER"] = ResearchActor(
                str(row["sender_telegram_id"]),
                (
                    str(row["sender_display_name"])
                    if row["sender_display_name"] is not None
                    else None
                ),
            )
        yield Candidate(
            context=ResearchFactContext(
                source_code=f"GROUP_{group}",
                source_message_id=message_id,
                occurred_at_utc=str(row["event_time_utc"]),
                available_at_utc=str(row["available_at_utc"]),
                raw_text=str(row["message_text"]),
                raw_kind="OFFER_TEXT",
                actors=actors,
            ),
            event_keys=tuple(
                derive_event_key("coin-group-offer-v1", group, message_id, index)
                for index in range(len(offers))
            ),
        )


def _channel_candidates(connection: sqlite3.Connection) -> Iterator[Candidate]:
    if not _table(connection, "capture_market_messages"):
        return
    projections: dict[tuple[str, int], list[bytes]] = defaultdict(list)
    if _table(connection, "capture_projection_keys"):
        for row in connection.execute(
            "SELECT source_id,message_id,event_key FROM capture_projection_keys "
            "WHERE source_id IN (?,?,?)",
            (_PRIMARY, "MELTED_AGGREGATE", "MELTED_FLOW"),
        ):
            projections[(str(row["source_id"]), int(row["message_id"]))].append(
                bytes(row["event_key"])
            )
    originals: dict[tuple[str, int], sqlite3.Row] = {}
    if _table(connection, "capture_market_message_revisions"):
        for row in connection.execute(
            """
            SELECT source_id,message_id,event_time_utc,available_at_utc,message_text
            FROM capture_market_message_revisions
            WHERE source_id=?
            ORDER BY source_id,message_id,event_time_utc,available_at_utc,event_id
            """,
            (_PRIMARY,),
        ):
            originals.setdefault(
                (str(row["source_id"]), int(row["message_id"])), row
            )
    for row in connection.execute(
        """
        SELECT source_id,message_id,event_time_utc,available_at_utc,message_text
        FROM capture_market_messages
        WHERE source_id IN (?,?,?)
        ORDER BY event_time_utc,source_id,message_id
        """,
        (_PRIMARY, "MELTED_AGGREGATE", "MELTED_FLOW"),
    ):
        capture_source = str(row["source_id"])
        message_id = int(row["message_id"])
        selected = originals.get((capture_source, message_id), row)
        yield Candidate(
            context=ResearchFactContext(
                source_code=(
                    "PRIVATE_GOLD_CHANNEL"
                    if capture_source == _PRIMARY
                    else capture_source
                ),
                source_message_id=message_id,
                occurred_at_utc=str(selected["event_time_utc"]),
                available_at_utc=str(selected["available_at_utc"]),
                raw_text=str(selected["message_text"]),
                raw_kind=(
                    "OFFER_TEXT" if capture_source == _PRIMARY else "SOURCE_TEXT"
                ),
                actors={},
            ),
            event_keys=tuple(projections.get((capture_source, message_id), ())),
        )


def candidates(path: Path) -> Iterator[Candidate]:
    if path.is_symlink() or not path.is_file():
        raise MarketResearchBackfillError("research_backfill_capture_db_invalid")
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=10000")
    try:
        yield from _coin_candidates(connection)
        yield from _channel_candidates(connection)
    finally:
        connection.close()


def _archive_connection():
    import psycopg2

    password_path = Path(
        os.environ.get(
            "MARKET_POSTGRES_PASSWORD_FILE",
            "/run/secrets/market_postgres_password",
        )
    )
    try:
        password = password_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise MarketResearchBackfillError("research_backfill_database_secret_invalid") from exc
    try:
        return psycopg2.connect(
            host=os.environ.get("MARKET_POSTGRES_HOST", "market-database"),
            port=int(os.environ.get("MARKET_POSTGRES_PORT", "5432")),
            user=os.environ.get("MARKET_POSTGRES_USER", "market_data"),
            password=password,
            dbname=os.environ.get("MARKET_POSTGRES_DB", "market_archive"),
            connect_timeout=5,
            application_name="market-research-backfill",
        )
    except psycopg2.Error as exc:
        raise MarketResearchBackfillError("research_backfill_database_unavailable") from exc


def run(paths: Iterable[Path], *, apply: bool) -> dict[str, object]:
    counts: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    if not apply:
        for path in paths:
            for item in candidates(path):
                counts["candidate_messages"] += 1
                counts["candidate_fact_links"] += len(item.event_keys)
                sources[item.context.source_code] += 1
        return {
            "status": "dry-run",
            **counts,
            "source_messages": dict(sorted(sources.items())),
        }

    key = ResearchArchiveKey.from_file(
        Path(
            os.environ.get(
                "MARKET_RESEARCH_ENCRYPTION_KEY_FILE",
                "/run/secrets/market_research_encryption_key",
            )
        ),
        key_id=os.environ.get(
            "MARKET_RESEARCH_ENCRYPTION_KEY_ID", "market-research:v1"
        ),
    )
    archive = _archive_connection()
    try:
        for path in paths:
            processed = 0
            with archive.cursor() as cursor:
                for item in candidates(path):
                    message_key, plaintext_hash = archive_research_message(
                        cursor, context=item.context, key=key
                    )
                    counts["archived_messages"] += 1
                    sources[item.context.source_code] += 1
                    for event_key in item.event_keys:
                        cursor.execute(
                            """
                            SELECT encode(fact_id,'hex'),fact_revision
                            FROM market_data.market_facts
                            WHERE event_key=%s AND source_code=%s
                            """,
                            (event_key, item.context.source_code),
                        )
                        fact = cursor.fetchone()
                        if fact is None:
                            counts["unlinked_event_keys"] += 1
                            continue
                        link_research_message_to_fact(
                            cursor,
                            fact_id=str(fact[0]),
                            fact_revision=int(fact[1]),
                            raw_message_key=message_key,
                            plaintext_hash=plaintext_hash,
                            raw_role=item.context.raw_kind,
                        )
                        archive_research_actors(
                            cursor,
                            fact_id=str(fact[0]),
                            actors=item.context.actors,
                            key=key,
                        )
                        counts["linked_facts"] += 1
                    processed += 1
                    if processed % 1_000 == 0:
                        archive.commit()
                archive.commit()
    except BaseException:
        archive.rollback()
        raise
    finally:
        archive.close()
    return {
        "status": "applied",
        **counts,
        "source_messages": dict(sorted(sources.items())),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-db", action="append", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = run(tuple(args.capture_db), apply=bool(args.apply))
    except (MarketResearchBackfillError, sqlite3.Error, ValueError) as exc:
        print(json.dumps({"status": "fail", "reason_code": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
