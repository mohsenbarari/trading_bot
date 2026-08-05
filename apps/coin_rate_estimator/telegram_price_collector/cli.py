from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import sys

from .audit import build_audit_report
from .collector import collect_history
from .config import DEFAULT_CHANNELS, SOURCE_PARSER_CHANNELS, Settings
from .db import (
    connect,
    finish_external_collection_run,
    infer_naghdp_trade_sides,
    initialize,
    rebuild_minute_prices,
    replace_price_events,
    reset_database,
    start_external_collection_run,
    upsert_external_observations,
)
from .external_collectors import (
    ExternalSourceError,
    fetch_ime_live,
    fetch_wallex_history,
    fetch_wallex_live,
    iso_utc,
)
from .parsers import parse_message, should_ignore_message


def _settings_without_credentials() -> Settings:
    return Settings.from_environment(require_credentials=False)


def command_init_db() -> int:
    settings = _settings_without_credentials()
    connection = connect(settings.db_path)
    try:
        initialize(connection)
    finally:
        connection.close()
    print(f"Initialized {settings.db_path}")
    return 0


def command_reset_db(args: argparse.Namespace) -> int:
    if not args.yes:
        raise ValueError("reset-db requires --yes")
    settings = _settings_without_credentials()
    connection = connect(settings.db_path)
    try:
        reset_database(connection)
    finally:
        connection.close()
    print(f"Reset {settings.db_path}")
    return 0


def command_collect(args: argparse.Namespace) -> int:
    settings = Settings.with_interactive_credentials()
    channels = tuple(args.channel or DEFAULT_CHANNELS)
    result = asyncio.run(
        collect_history(
            settings,
            channels=channels,
            days=args.days,
            batch_size=args.batch_size,
            request_wait_seconds=args.request_wait_seconds,
            start_before_days=args.start_before_days,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_reparse() -> int:
    settings = _settings_without_credentials()
    connection = connect(settings.db_path)
    initialize(connection)
    parsed_posts = 0
    parsed_events = 0
    side_links = {"examined": 0, "matched": 0, "unresolved": 0}
    try:
        rows = connection.execute(
            """
            SELECT id, raw_text, published_at_utc, source_code
            FROM raw_posts
            ORDER BY id
            """
        )
        for row in rows:
            parser_channel = SOURCE_PARSER_CHANNELS[row["source_code"]]
            events = parse_message(parser_channel, row["raw_text"])
            empty_status = (
                "IGNORED"
                if should_ignore_message(
                    parser_channel,
                    row["raw_text"],
                )
                else "UNMATCHED"
            )
            parsed_events += replace_price_events(
                connection,
                raw_post_id=int(row["id"]),
                event_time_utc=row["published_at_utc"],
                events=events,
                empty_status=empty_status,
            )
            parsed_posts += 1
            if parsed_posts % 1_000 == 0:
                connection.commit()
        side_links = infer_naghdp_trade_sides(connection)
        connection.commit()
    finally:
        connection.close()
    print(
        json.dumps(
            {"posts": parsed_posts, "events": parsed_events, "trade_side_links": side_links},
            indent=2,
        )
    )
    return 0


def command_aggregate() -> int:
    settings = _settings_without_credentials()
    connection = connect(settings.db_path)
    initialize(connection)
    try:
        count = rebuild_minute_prices(connection)
    finally:
        connection.close()
    print(json.dumps({"minute_rows": count}, indent=2))
    return 0


def command_stats() -> int:
    settings = _settings_without_credentials()
    connection = connect(settings.db_path)
    initialize(connection)
    try:
        payload = {
            "telegram_sources": connection.execute(
                "SELECT COUNT(DISTINCT source_code) FROM raw_posts"
            ).fetchone()[0],
            "raw_posts": connection.execute("SELECT COUNT(*) FROM raw_posts").fetchone()[0],
            "price_events": connection.execute("SELECT COUNT(*) FROM price_events").fetchone()[0],
            "minute_prices": connection.execute("SELECT COUNT(*) FROM minute_prices").fetchone()[0],
            "external_market_observations": connection.execute(
                "SELECT COUNT(*) FROM external_market_observations"
            ).fetchone()[0],
            "external_observations_by_source": {
                row["source"]: row["count"]
                for row in connection.execute(
                    """
                    SELECT external_instruments.source, COUNT(*) AS count
                    FROM external_market_observations
                    JOIN external_instruments
                      ON external_instruments.code = external_market_observations.instrument_code
                    GROUP BY external_instruments.source
                    ORDER BY external_instruments.source
                    """
                )
            },
        }
    finally:
        connection.close()
    print(json.dumps(payload, indent=2))
    return 0


def _run_external_source(
    *,
    source: str,
    mode: str,
    loader: object,
    requested_from: datetime | None = None,
    requested_to: datetime | None = None,
) -> dict[str, object]:
    settings = _settings_without_credentials()
    connection = connect(settings.db_path)
    initialize(connection)
    run_id = start_external_collection_run(
        connection,
        source=source,
        mode=mode,
        requested_from_utc=iso_utc(requested_from) if requested_from else None,
        requested_to_utc=iso_utc(requested_to) if requested_to else None,
    )
    try:
        observations = loader()  # type: ignore[operator]
        count = upsert_external_observations(connection, observations)
        finish_external_collection_run(
            connection,
            run_id,
            status="COMPLETED",
            observation_count=count,
        )
        return {"source": source, "status": "COMPLETED", "observations": count}
    except Exception as exc:
        finish_external_collection_run(
            connection,
            run_id,
            status="FAILED",
            observation_count=0,
            error_text=str(exc),
        )
        return {"source": source, "status": "FAILED", "error": str(exc)}
    finally:
        connection.close()


def command_collect_external(args: argparse.Namespace) -> int:
    results: list[dict[str, object]] = []
    if args.source in {"wallex", "all"}:
        results.append(
            _run_external_source(
                source="WALLEX_PUBLIC_API",
                mode="LIVE",
                loader=fetch_wallex_live,
            )
        )
    if args.source in {"ime", "all"}:
        results.append(
            _run_external_source(
                source="IME_REALTIME_BOARD",
                mode="LIVE",
                loader=lambda: asyncio.run(
                    fetch_ime_live(
                        timeout=args.ime_timeout,
                        handshake_attempts=args.ime_handshake_attempts,
                    )
                ),
            )
        )
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return 0 if all(item["status"] == "COMPLETED" for item in results) else 1


def command_backfill_external(args: argparse.Namespace) -> int:
    if args.days <= 0:
        raise ValueError("days must be positive")
    end = datetime.now(timezone.utc).replace(microsecond=0)
    start = end - timedelta(days=args.days)
    results: list[dict[str, object]] = []
    cursor = start
    total = 0
    failed = False
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=1), end)
        result = _run_external_source(
            source="WALLEX_PUBLIC_API",
            mode=f"HISTORY_{args.resolution_minutes}M",
            requested_from=cursor,
            requested_to=chunk_end,
            loader=lambda chunk_start=cursor, chunk_stop=chunk_end: fetch_wallex_history(
                start=chunk_start,
                end=chunk_stop,
                resolution_minutes=args.resolution_minutes,
            ),
        )
        results.append(result)
        if result["status"] == "COMPLETED":
            total += int(result["observations"])
            print(
                json.dumps(
                    {
                        "event": "external_backfill_day",
                        "source": "WALLEX_PUBLIC_API",
                        "from_utc": iso_utc(cursor),
                        "to_utc": iso_utc(chunk_end),
                        "observations": result["observations"],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                flush=True,
            )
        else:
            failed = True
        cursor = chunk_end
    print(
        json.dumps(
            {
                "source": "WALLEX_PUBLIC_API",
                "status": "PARTIAL" if failed else "COMPLETED",
                "from_utc": iso_utc(start),
                "to_utc": iso_utc(end),
                "observations_processed": total,
                "chunks": len(results),
                "failed_chunks": sum(item["status"] != "COMPLETED" for item in results),
                "note": (
                    "The official IME live board does not expose a verified public historical "
                    "endpoint, so this command does not manufacture IME history."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if failed else 0


def command_audit() -> int:
    settings = _settings_without_credentials()
    connection = connect(settings.db_path)
    initialize(connection)
    try:
        payload = build_audit_report(connection)
    finally:
        connection.close()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["passed"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telegram-price-poc",
        description="Read-only Telegram price history collector",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Create or migrate the SQLite schema")
    reset_parser = subparsers.add_parser("reset-db", help="Delete all collected data")
    reset_parser.add_argument("--yes", action="store_true")

    collect_parser = subparsers.add_parser("collect", help="Collect recent Telegram history")
    collect_parser.add_argument("--days", type=int, default=7)
    collect_parser.add_argument("--batch-size", type=int, default=1_000)
    collect_parser.add_argument("--request-wait-seconds", type=float)
    collect_parser.add_argument(
        "--start-before-days",
        type=float,
        help="Start before this many recent days (for resuming an older window)",
    )
    collect_parser.add_argument(
        "--channel",
        action="append",
        help="Channel username; repeat to collect more than one channel",
    )

    subparsers.add_parser("reparse", help="Re-run local parsers without Telegram access")
    subparsers.add_parser("aggregate", help="Rebuild one-minute OHLC rows")
    subparsers.add_parser("stats", help="Show local database counts")
    subparsers.add_parser("audit", help="Validate extracted data and timestamps")
    external_parser = subparsers.add_parser(
        "collect-external", help="Collect current Wallex and/or IME public quotes"
    )
    external_parser.add_argument(
        "--source", choices=("all", "wallex", "ime"), default="all"
    )
    external_parser.add_argument("--ime-timeout", type=float, default=12.0)
    external_parser.add_argument(
        "--ime-handshake-attempts",
        type=int,
        default=4,
    )

    backfill_parser = subparsers.add_parser(
        "backfill-external", help="Backfill public historical sources that expose timestamps"
    )
    backfill_parser.add_argument("--days", type=int, default=30)
    backfill_parser.add_argument("--resolution-minutes", type=int, default=1)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "init-db":
            exit_code = command_init_db()
        elif args.command == "reset-db":
            exit_code = command_reset_db(args)
        elif args.command == "collect":
            exit_code = command_collect(args)
        elif args.command == "reparse":
            exit_code = command_reparse()
        elif args.command == "aggregate":
            exit_code = command_aggregate()
        elif args.command == "stats":
            exit_code = command_stats()
        elif args.command == "audit":
            exit_code = command_audit()
        elif args.command == "collect-external":
            exit_code = command_collect_external(args)
        elif args.command == "backfill-external":
            exit_code = command_backfill_external(args)
        else:
            parser.error(f"Unknown command: {args.command}")
            return
    except (ExternalSourceError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    raise SystemExit(exit_code)
