from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import Settings, source_code_for_channel
from .db import (
    connect,
    finish_scrape_run,
    infer_naghdp_trade_sides,
    initialize,
    replace_price_events,
    start_scrape_run,
    upsert_raw_post,
)
from .models import RawPost
from .parsers import parse_message


def _emit_progress(
    event: str,
    *,
    channel_username: str,
    channel_title: str,
    source_time: datetime | None = None,
    **details: object,
) -> None:
    payload: dict[str, object] = {
        "event": event,
        "logged_at_utc": _iso_utc(datetime.now(timezone.utc)),
        "channel_username": f"@{channel_username.lstrip('@')}",
        "channel_title": channel_title,
    }
    if source_time is not None:
        if source_time.tzinfo is None:
            source_time = source_time.replace(tzinfo=timezone.utc)
        source_utc = source_time.astimezone(timezone.utc)
        source_tehran = source_utc.astimezone(ZoneInfo("Asia/Tehran"))
        payload.update(
            {
                "source_datetime_utc": _iso_utc(source_utc),
                "source_datetime_tehran": source_tehran.isoformat(timespec="seconds"),
                "source_date_tehran": source_tehran.date().isoformat(),
            }
        )
    payload.update(details)
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


async def collect_history(
    settings: Settings,
    *,
    channels: tuple[str, ...],
    days: int = 7,
    batch_size: int = 1_000,
    request_wait_seconds: float | None = None,
    start_before_days: float | None = None,
) -> dict[str, dict[str, int]]:
    if days <= 0:
        raise ValueError("days must be positive")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if request_wait_seconds is not None and request_wait_seconds < 0:
        raise ValueError("request_wait_seconds must be non-negative")
    if start_before_days is not None and not (0 < start_before_days < days):
        raise ValueError("start_before_days must be greater than zero and less than days")

    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise RuntimeError(
            "Telethon is not installed. Run: python -m pip install -e ."
        ) from exc

    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.session_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(settings.session_path.parent, 0o700)
    previous_umask = os.umask(0o077)

    connection = connect(settings.db_path)
    initialize(connection)
    collection_now = datetime.now(timezone.utc)
    cutoff = collection_now - timedelta(days=days)
    offset_date = (
        collection_now - timedelta(days=start_before_days)
        if start_before_days is not None
        else None
    )
    cutoff_iso = _iso_utc(cutoff)
    results: dict[str, dict[str, int]] = {}

    try:
        client = TelegramClient(
            str(settings.session_path),
            settings.api_id,
            settings.api_hash,
            flood_sleep_threshold=60,
            sequential_updates=True,
        )
        await client.start(phone=settings.phone)
        try:
            for requested_username in channels:
                entity = await client.get_entity(requested_username)
                canonical_username = getattr(entity, "username", None) or requested_username
                channel_title = getattr(entity, "title", None) or canonical_username
                source_code = source_code_for_channel(canonical_username)
                run_id = start_scrape_run(connection, source_code, cutoff_iso or "")
                message_count = 0
                event_count = 0
                side_links = {"examined": 0, "matched": 0, "unresolved": 0}
                newest_message_id: int | None = None
                oldest_message_id: int | None = None
                last_reported_source_date: str | None = None
                compact_minutes_seen: set[str] = set()

                _emit_progress(
                    "channel_start",
                    channel_username=canonical_username,
                    channel_title=channel_title,
                    cutoff_utc=cutoff_iso,
                )

                try:
                    async for message in client.iter_messages(
                        entity,
                        wait_time=request_wait_seconds,
                        offset_date=offset_date,
                    ):
                        published = getattr(message, "date", None)
                        if published is None:
                            continue
                        if published.tzinfo is None:
                            published = published.replace(tzinfo=timezone.utc)
                        if published < cutoff:
                            break

                        source_date = published.astimezone(ZoneInfo("Asia/Tehran")).date().isoformat()
                        if source_date != last_reported_source_date:
                            _emit_progress(
                                "source_date_changed",
                                channel_username=canonical_username,
                                channel_title=channel_title,
                                source_time=published,
                                messages=message_count,
                                price_events=event_count,
                            )
                            last_reported_source_date = source_date

                        message_id = int(message.id)
                        newest_message_id = newest_message_id or message_id
                        oldest_message_id = message_id
                        raw_text = getattr(message, "message", None) or ""
                        published_at = _iso_utc(published)
                        if published_at is None:
                            continue
                        is_forwarded = getattr(message, "fwd_from", None) is not None
                        events = [] if is_forwarded else parse_message(canonical_username, raw_text)
                        message_count += 1
                        if not events:
                            if message_count % batch_size == 0:
                                connection.commit()
                                _emit_progress(
                                    "channel_progress",
                                    channel_username=canonical_username,
                                    channel_title=channel_title,
                                    source_time=published,
                                    messages=message_count,
                                    price_events=event_count,
                                )
                            continue
                        if source_code == "XAUUSD":
                            compact_minute = published_at[:16]
                            if compact_minute in compact_minutes_seen:
                                if message_count % batch_size == 0:
                                    connection.commit()
                                    _emit_progress(
                                        "channel_progress",
                                        channel_username=canonical_username,
                                        channel_title=channel_title,
                                        source_time=published,
                                        messages=message_count,
                                        price_events=event_count,
                                        current_message_id=message_id,
                                    )
                                continue
                            compact_minutes_seen.add(compact_minute)
                        post = RawPost(
                            message_id=message_id,
                            published_at_utc=published_at,
                            raw_text=raw_text,
                        )
                        raw_post_id = upsert_raw_post(
                            connection,
                            source_code=source_code,
                            post=post,
                        )
                        event_count += replace_price_events(
                            connection,
                            raw_post_id=raw_post_id,
                            event_time_utc=published_at,
                            events=events,
                        )

                        if message_count % batch_size == 0:
                            connection.commit()
                            _emit_progress(
                                "channel_progress",
                                channel_username=canonical_username,
                                channel_title=channel_title,
                                source_time=published,
                                messages=message_count,
                                price_events=event_count,
                                current_message_id=message_id,
                            )

                    connection.commit()
                    if canonical_username.lower() == "naghdp":
                        side_links = infer_naghdp_trade_sides(connection)
                        connection.commit()
                    finish_scrape_run(
                        connection,
                        run_id,
                        status="COMPLETE",
                        message_count=message_count,
                        parsed_event_count=event_count,
                        newest_message_id=newest_message_id,
                        oldest_message_id=oldest_message_id,
                    )
                    results[canonical_username] = {
                        "messages": message_count,
                        "events": event_count,
                    }
                    _emit_progress(
                        "channel_complete",
                        channel_username=canonical_username,
                        channel_title=channel_title,
                        messages=message_count,
                        price_events=event_count,
                        newest_message_id=newest_message_id,
                        oldest_message_id=oldest_message_id,
                        trade_side_links=side_links,
                    )
                except BaseException as exc:
                    interrupted = isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError))
                    if interrupted:
                        connection.commit()
                    else:
                        connection.rollback()
                    finish_scrape_run(
                        connection,
                        run_id,
                        status="INTERRUPTED" if interrupted else "FAILED",
                        message_count=message_count,
                        parsed_event_count=event_count,
                        newest_message_id=newest_message_id,
                        oldest_message_id=oldest_message_id,
                        error_text=f"{type(exc).__name__}: {exc}"[:1_000],
                    )
                    _emit_progress(
                        "channel_interrupted" if interrupted else "channel_failed",
                        channel_username=canonical_username,
                        channel_title=channel_title,
                        messages=message_count,
                        price_events=event_count,
                        newest_message_id=newest_message_id,
                        oldest_message_id=oldest_message_id,
                        error_type=type(exc).__name__,
                    )
                    raise
        finally:
            await client.disconnect()
    finally:
        connection.close()
        os.umask(previous_umask)

    session_file = settings.session_path.with_suffix(".session")
    if session_file.exists():
        os.chmod(session_file, 0o600)
    return results
