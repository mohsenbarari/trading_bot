"""Explicit, read-only Telethon transport for normalized market collection."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from typing import Iterable
from zoneinfo import ZoneInfo

from core.market_intelligence.telegram_collector.config import (
    TelegramCollectorSettings,
    TelegramSource,
)
from core.market_intelligence.telegram_collector.models import (
    CollectedMessage,
)
from core.market_intelligence.telegram_collector.storage import (
    connect,
    infer_melted_flow_trade_sides,
    ingest_message,
    initialize,
    read_checkpoint,
)


MAXIMUM_BACKFILL_DAYS = 31


class TelegramCollectionError(RuntimeError):
    """A redacted transport failure safe for operator output."""


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("collector_timestamp_timezone_required")
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _emit_progress(
    event: str,
    *,
    source: TelegramSource,
    source_time: datetime | None = None,
    **details: object,
) -> None:
    payload: dict[str, object] = {
        "event": event,
        "logged_at_utc": _iso_utc(datetime.now(timezone.utc)),
        "source_code": source.code,
        "channel": f"@{source.username}",
    }
    if source_time is not None:
        source_utc = source_time.astimezone(timezone.utc)
        source_tehran = source_utc.astimezone(ZoneInfo("Asia/Tehran"))
        payload["source_datetime_utc"] = _iso_utc(source_time)
        payload["source_date_utc"] = source_utc.date().isoformat()
        payload["source_datetime_tehran"] = source_tehran.isoformat(
            timespec="seconds"
        )
        payload["source_date_tehran"] = (
            source_tehran.date().isoformat()
        )
    payload.update(details)
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        flush=True,
    )


def _validate_collection_request(
    *,
    sources: Iterable[TelegramSource],
    days: int,
    batch_size: int,
    request_wait_seconds: float | None,
) -> tuple[TelegramSource, ...]:
    selected = tuple(sources)
    if not selected:
        raise ValueError("telegram_collection_source_required")
    if len({source.code for source in selected}) != len(selected):
        raise ValueError("telegram_collection_source_duplicate")
    if days <= 0 or days > MAXIMUM_BACKFILL_DAYS:
        raise ValueError("telegram_collection_days_out_of_range")
    if batch_size <= 0:
        raise ValueError("telegram_collection_batch_size_invalid")
    if request_wait_seconds is not None and request_wait_seconds < 0:
        raise ValueError("telegram_collection_wait_invalid")
    return selected


async def collect_telegram_market(
    settings: TelegramCollectorSettings,
    *,
    sources: Iterable[TelegramSource],
    days: int,
    resume_from_checkpoint: bool,
    batch_size: int = 500,
    request_wait_seconds: float | None = None,
    as_of: datetime | None = None,
) -> dict[str, dict[str, int]]:
    """Read public channels and write only normalized price observations."""
    selected = _validate_collection_request(
        sources=sources,
        days=days,
        batch_size=batch_size,
        request_wait_seconds=request_wait_seconds,
    )
    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise RuntimeError(
            "telethon_optional_dependency_not_installed"
        ) from exc

    session_parent = settings.session_path.expanduser().parent
    if session_parent.is_symlink():
        raise ValueError("telegram_session_parent_symlink_forbidden")
    session_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(session_parent, 0o700)
    collection_time = as_of or datetime.now(timezone.utc)
    if (
        collection_time.tzinfo is None
        or collection_time.utcoffset() is None
    ):
        raise ValueError("telegram_collection_as_of_timezone_required")
    cutoff = collection_time.astimezone(timezone.utc) - timedelta(days=days)
    results: dict[str, dict[str, int]] = {}

    previous_umask = os.umask(0o077)
    connection = None
    client = None
    try:
        connection = connect(settings.database_path)
        initialize(connection)
        client = TelegramClient(
            str(settings.session_path.expanduser()),
            settings.credentials.api_id,
            settings.credentials.api_hash,
            flood_sleep_threshold=60,
            sequential_updates=True,
        )
        try:
            await client.start(phone=settings.credentials.phone)
            for source in selected:
                entity = await client.get_entity(source.username)
                minimum_id = (
                    read_checkpoint(connection, source.code) or 0
                    if resume_from_checkpoint
                    else 0
                )
                message_count = 0
                event_count = 0
                ignored_count = 0
                newest_source_time: datetime | None = None
                _emit_progress(
                    "channel_start",
                    source=source,
                    cutoff_utc=_iso_utc(cutoff),
                    resume_from_checkpoint=resume_from_checkpoint,
                )
                try:
                    async for message in client.iter_messages(
                        entity,
                        min_id=minimum_id,
                        wait_time=request_wait_seconds,
                    ):
                        published = getattr(message, "date", None)
                        if not isinstance(published, datetime):
                            continue
                        if published.tzinfo is None:
                            published = published.replace(
                                tzinfo=timezone.utc
                            )
                        published = published.astimezone(timezone.utc)
                        if published < cutoff:
                            break
                        newest_source_time = newest_source_time or published
                        raw_text = getattr(message, "message", None) or ""
                        result = ingest_message(
                            connection,
                            source_code=source.code,
                            message=CollectedMessage(
                                message_id=int(message.id),
                                published_at_utc=_iso_utc(published),
                                text=str(raw_text),
                                is_forwarded=(
                                    getattr(message, "fwd_from", None)
                                    is not None
                                ),
                            ),
                        )
                        message_count += 1
                        event_count += result.parsed_event_count
                        ignored_count += int(result.ignored)
                        if message_count % batch_size == 0:
                            connection.commit()
                            _emit_progress(
                                "channel_progress",
                                source=source,
                                source_time=published,
                                messages=message_count,
                                price_events=event_count,
                                ignored_messages=ignored_count,
                            )
                    if source.code == "MELTED_FLOW":
                        link_result = infer_melted_flow_trade_sides(
                            connection
                        )
                    else:
                        link_result = {
                            "examined": 0,
                            "matched": 0,
                            "unresolved": 0,
                        }
                    connection.commit()
                    results[source.code] = {
                        "messages": message_count,
                        "events": event_count,
                        "ignored": ignored_count,
                        "linked_trade_sides": int(
                            link_result["matched"]
                        ),
                    }
                    _emit_progress(
                        "channel_complete",
                        source=source,
                        source_time=newest_source_time,
                        messages=message_count,
                        price_events=event_count,
                        ignored_messages=ignored_count,
                        linked_trade_sides=link_result,
                    )
                except BaseException as exc:
                    connection.rollback()
                    interrupted = isinstance(
                        exc,
                        (KeyboardInterrupt, asyncio.CancelledError),
                    )
                    _emit_progress(
                        (
                            "channel_interrupted"
                            if interrupted
                            else "channel_failed"
                        ),
                        source=source,
                        messages=message_count,
                        price_events=event_count,
                        error_type=type(exc).__name__,
                    )
                    if interrupted:
                        raise
                    raise TelegramCollectionError(
                        "telegram_channel_read_failed:"
                        f"{source.code}:{type(exc).__name__}"
                    ) from exc
        finally:
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    pass
    except TelegramCollectionError:
        raise
    except Exception as exc:
        raise TelegramCollectionError(
            f"telegram_transport_failed:{type(exc).__name__}"
        ) from exc
    finally:
        if connection is not None:
            connection.close()
        os.umask(previous_umask)
        session_file = settings.session_path.with_suffix(".session")
        if session_file.exists():
            os.chmod(session_file, 0o600)
    return results
