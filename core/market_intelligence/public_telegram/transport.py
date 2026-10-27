"""Explicit optional Telethon transport for the public-source adapter.

Importing this module makes no network request.  Calling the collector requires
an injected settings object and the optional ``telethon`` package; it is never
registered in FastAPI startup and therefore cannot start accidentally.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
from typing import Iterable, Mapping

from core.market_intelligence.market_store import (
    connect_market_store,
    initialize_market_store,
    read_source_checkpoint,
)

from .ingest import PublicTelegramMessage, ingest_public_message
from .sources import PUBLIC_TELEGRAM_SOURCES, PublicTelegramSource


MAXIMUM_BACKFILL_DAYS = 31


class PublicTelegramTransportError(RuntimeError):
    """Redacted transport failure safe for command/health output."""


@dataclass(frozen=True, slots=True)
class PublicTelegramCredentials:
    api_id: int
    api_hash: str = field(repr=False)
    phone: str = field(repr=False)

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "PublicTelegramCredentials":
        values = os.environ if environment is None else environment
        raw_api_id = str(values.get("COIN_MARKET_TELEGRAM_API_ID", "")).strip()
        api_hash = str(values.get("COIN_MARKET_TELEGRAM_API_HASH", "")).strip()
        phone = str(values.get("COIN_MARKET_TELEGRAM_PHONE", "")).strip()
        if not raw_api_id.isdigit() or int(raw_api_id) <= 0:
            raise ValueError("public_telegram_api_id_missing_or_invalid")
        if not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
            raise ValueError("public_telegram_api_hash_missing_or_invalid")
        if not re.fullmatch(r"\+[1-9][0-9]{7,14}", phone):
            raise ValueError("public_telegram_phone_missing_or_invalid")
        return cls(api_id=int(raw_api_id), api_hash=api_hash, phone=phone)


@dataclass(frozen=True, slots=True)
class PublicTelegramTransportSettings:
    credentials: PublicTelegramCredentials = field(repr=False)
    market_store_path: Path
    session_path: Path
    # A scheduled/non-interactive run must never pause while Telethon requests
    # a login code.  Bootstrap is a separate, explicitly authorized console
    # operation that persists the session under the protected runtime root.
    allow_interactive_login: bool = False

    def validate_paths(self, *, repository_root: Path) -> None:
        database = self.market_store_path.expanduser().resolve()
        session = self.session_path.expanduser().resolve()
        repository = repository_root.resolve()
        if database == session or database == session.with_suffix(".session"):
            raise ValueError("public_telegram_database_and_session_paths_overlap")
        for candidate in (database, session, session.with_suffix(".session")):
            if candidate == repository or repository in candidate.parents:
                raise ValueError("public_telegram_runtime_path_inside_repository")
        if session.is_symlink() or session.parent.is_symlink():
            raise ValueError("public_telegram_session_symlink_forbidden")


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("public_telegram_timestamp_timezone_required")
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _validate_request(
    *,
    sources: Iterable[PublicTelegramSource],
    days: int,
    batch_size: int,
    request_wait_seconds: float | None,
) -> tuple[PublicTelegramSource, ...]:
    selected = tuple(sources)
    if not selected:
        raise ValueError("public_telegram_source_required")
    if len({source.code for source in selected}) != len(selected):
        raise ValueError("public_telegram_source_duplicate")
    if days <= 0 or days > MAXIMUM_BACKFILL_DAYS:
        raise ValueError("public_telegram_days_out_of_range")
    if batch_size <= 0:
        raise ValueError("public_telegram_batch_size_invalid")
    if request_wait_seconds is not None and request_wait_seconds < 0:
        raise ValueError("public_telegram_wait_invalid")
    return selected


async def collect_public_market_telegram(
    settings: PublicTelegramTransportSettings,
    *,
    sources: Iterable[PublicTelegramSource] = PUBLIC_TELEGRAM_SOURCES,
    days: int,
    resume_from_checkpoint: bool,
    batch_size: int = 500,
    request_wait_seconds: float | None = None,
    as_of: datetime | None = None,
) -> dict[str, dict[str, int]]:
    """Explicitly read public channels into the canonical local Market Store."""

    selected = _validate_request(
        sources=sources,
        days=days,
        batch_size=batch_size,
        request_wait_seconds=request_wait_seconds,
    )
    settings.validate_paths(repository_root=Path(__file__).resolve().parents[3])
    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise PublicTelegramTransportError(
            "telethon_optional_dependency_not_installed"
        ) from exc

    settings.session_path.expanduser().parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(settings.session_path.expanduser().parent, 0o700)
    collection_time = as_of or datetime.now(timezone.utc)
    if collection_time.tzinfo is None or collection_time.utcoffset() is None:
        raise ValueError("public_telegram_as_of_timezone_required")
    cutoff = collection_time.astimezone(timezone.utc) - timedelta(days=days)
    previous_umask = os.umask(0o077)
    connection = None
    client = None
    results: dict[str, dict[str, int]] = {}
    try:
        connection = connect_market_store(settings.market_store_path)
        initialize_market_store(connection)
        client = TelegramClient(
            str(settings.session_path.expanduser()),
            settings.credentials.api_id,
            settings.credentials.api_hash,
            flood_sleep_threshold=60,
            sequential_updates=True,
        )
        await client.connect()
        if not await client.is_user_authorized():
            if not settings.allow_interactive_login:
                raise PublicTelegramTransportError(
                    "public_telegram_session_authorization_required"
                )
            await client.start(phone=settings.credentials.phone)
        for source in selected:
            entity = await client.get_entity(source.public_username)
            minimum_id = (
                read_source_checkpoint(connection, source.code) or 0
                if resume_from_checkpoint
                else 0
            )
            message_count = event_count = ignored_count = linked_count = 0
            try:
                pending_messages: list[PublicTelegramMessage] = []
                async for message in client.iter_messages(
                    entity,
                    min_id=minimum_id,
                    wait_time=request_wait_seconds,
                ):
                    published = getattr(message, "date", None)
                    if not isinstance(published, datetime):
                        continue
                    if published.tzinfo is None:
                        published = published.replace(tzinfo=timezone.utc)
                    if published.astimezone(timezone.utc) < cutoff:
                        break
                    pending_messages.append(
                        PublicTelegramMessage(
                            message_id=int(message.id),
                            published_at_utc=_iso_utc(published),
                            available_at_utc=datetime.now(timezone.utc),
                            text=str(getattr(message, "message", None) or ""),
                            is_forwarded=getattr(message, "fwd_from", None) is not None,
                        )
                    )
                # Telethon history is newest-first.  Causal range decisions and
                # checkpoints must be built oldest-first; future quotes cannot
                # repair an older message.
                for public_message in reversed(pending_messages):
                    result = ingest_public_message(
                        connection,
                        source_code=source.code,
                        message=public_message,
                    )
                    message_count += 1
                    event_count += result.event_count
                    ignored_count += int(result.ignored)
                    linked_count += result.linked_melted_flow_trades
                    if message_count % batch_size == 0:
                        connection.commit()
                connection.commit()
                results[source.code] = {
                    "messages": message_count,
                    "events": event_count,
                    "ignored": ignored_count,
                    "linked_melted_flow_trades": linked_count,
                }
            except BaseException as exc:
                connection.rollback()
                if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
                    raise
                raise PublicTelegramTransportError(
                    f"public_telegram_channel_read_failed:{source.code}:{type(exc).__name__}"
                ) from exc
    except PublicTelegramTransportError:
        raise
    except Exception as exc:
        raise PublicTelegramTransportError(
            f"public_telegram_transport_failed:{type(exc).__name__}"
        ) from exc
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass
        if connection is not None:
            connection.close()
        os.umask(previous_umask)
        session_file = settings.session_path.with_suffix(".session")
        if session_file.exists():
            os.chmod(session_file, 0o600)
    return results
