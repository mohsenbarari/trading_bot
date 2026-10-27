"""Optional, explicit Telethon transport for routed private-gold event channels."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
from typing import Mapping

from .market_contracts import normalize_utc
from .market_store import (
    advance_source_checkpoint,
    connect_market_store,
    initialize_market_store,
    read_source_checkpoint,
)
from .private_gold_payloads import PrivateGoldPayloadEnvelope, stage_private_gold_payload
from .private_gold_pipeline import process_private_gold_payloads
from .private_gold_staging import connect_private_gold_staging, initialize_private_gold_staging
from .public_telegram.transport import PublicTelegramCredentials


PRIVATE_GOLD_TELEGRAM_TRANSPORT_VERSION = "private-gold-telegram-v1"
_MAXIMUM_DAYS = 3
_DEFAULT_MAXIMUM_MESSAGES_PER_STREAM = 500
_MAXIMUM_MESSAGES_PER_STREAM = 1_000
_SOURCES = (("OFFER", "PRIVATE_GOLD_EVENT_OFFER"), ("TRADE", "PRIVATE_GOLD_EVENT_TRADE"))


class PrivateGoldTelegramTransportError(RuntimeError):
    """Redacted private transport failure suitable for a command result."""


@dataclass(frozen=True, slots=True)
class PrivateGoldEventChannels:
    offer_channel_id: int
    trade_channel_id: int

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "PrivateGoldEventChannels":
        values = os.environ if environment is None else environment
        def read(name: str) -> int:
            value = str(values.get(name, "")).strip()
            if not re.fullmatch(r"-100\d{8,16}", value):
                raise ValueError("private_gold_event_channel_id_missing_or_invalid")
            return int(value)
        offer = read("COIN_INTELLIGENCE_PRIVATE_GOLD_OFFER_EVENT_CHANNEL_ID")
        trade = read("COIN_INTELLIGENCE_PRIVATE_GOLD_TRADE_EVENT_CHANNEL_ID")
        if offer == trade:
            raise ValueError("private_gold_event_channels_must_differ")
        return cls(offer_channel_id=offer, trade_channel_id=trade)


@dataclass(frozen=True, slots=True)
class PrivateGoldTelegramSettings:
    credentials: PublicTelegramCredentials
    channels: PrivateGoldEventChannels
    staging_path: Path
    market_store_path: Path
    session_path: Path
    allow_interactive_login: bool = False

    def validate_paths(self, *, repository_root: Path) -> None:
        repository = repository_root.resolve()
        candidates = (self.staging_path, self.market_store_path, self.session_path)
        for candidate in candidates:
            resolved = candidate.expanduser().resolve()
            if resolved == repository or repository in resolved.parents:
                raise ValueError("private_gold_telegram_runtime_path_inside_repository")
            if resolved.is_symlink() or resolved.parent.is_symlink():
                raise ValueError("private_gold_telegram_runtime_symlink_forbidden")
        if len({path.expanduser().resolve() for path in candidates}) != 3:
            raise ValueError("private_gold_telegram_runtime_paths_overlap")


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("private_gold_telegram_timestamp_timezone_required")
    return normalize_utc(value, field_name="private_gold_telegram_timestamp")


async def collect_private_gold_event_telegram(
    settings: PrivateGoldTelegramSettings,
    *,
    days: int,
    resume_from_checkpoint: bool,
    maximum_messages_per_stream: int = _DEFAULT_MAXIMUM_MESSAGES_PER_STREAM,
    as_of: datetime | None = None,
) -> dict[str, int]:
    """Read two explicitly configured private channels exactly once.

    The staging commit intentionally precedes Market Store/checkpoint commit;
    a failure can therefore be retried without loss or duplicate facts.
    """

    if days <= 0 or days > _MAXIMUM_DAYS:
        raise ValueError("private_gold_telegram_days_out_of_range")
    if not 0 < maximum_messages_per_stream <= _MAXIMUM_MESSAGES_PER_STREAM:
        raise ValueError("private_gold_telegram_maximum_messages_out_of_range")
    settings.validate_paths(repository_root=Path(__file__).resolve().parents[3])
    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise PrivateGoldTelegramTransportError("telethon_optional_dependency_not_installed") from exc
    now = as_of or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("private_gold_telegram_as_of_timezone_required")
    receipt_time = _iso(now)
    cutoff = now.astimezone(timezone.utc) - timedelta(days=days)
    previous_umask = os.umask(0o077)
    staging = market = client = None
    try:
        settings.session_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(settings.session_path.parent, 0o700)
        staging = connect_private_gold_staging(settings.staging_path, repository_root=Path(__file__).resolve().parents[3])
        market = connect_market_store(settings.market_store_path)
        initialize_private_gold_staging(staging)
        initialize_market_store(market)
        # This command is normally invoked by a short-interval timer.  A
        # Telegram network failure must therefore fail promptly and leave the
        # next timer tick free to retry; the collector must not block for the
        # library's long default connection-retry cycle.
        client = TelegramClient(
            str(settings.session_path),
            settings.credentials.api_id,
            settings.credentials.api_hash,
            sequential_updates=True,
            flood_sleep_threshold=60,
            timeout=10,
            connection_retries=1,
            retry_delay=1,
            request_retries=1,
            auto_reconnect=False,
        )
        await client.connect()
        if not await client.is_user_authorized():
            if not settings.allow_interactive_login:
                raise PrivateGoldTelegramTransportError("private_gold_telegram_session_authorization_required")
            await client.start(phone=settings.credentials.phone)
        messages: list[tuple[str, str, int, str]] = []
        pipeline_as_of = receipt_time
        counters = {"messages": 0, "invalid_items": 0, "staged_changes": 0, "offer_facts": 0, "trade_facts": 0, "paper_minutes": 0}
        for stream, checkpoint_code in _SOURCES:
            channel_id = settings.channels.offer_channel_id if stream == "OFFER" else settings.channels.trade_channel_id
            entity = await client.get_entity(channel_id)
            minimum_id = read_source_checkpoint(market, checkpoint_code) if resume_from_checkpoint else None
            # A brand-new live collector intentionally starts at the newest
            # message rather than replaying an unbounded channel history.
            # Historical ingestion is an explicit, separately reviewed job.
            # ``id - 1`` lets this run retain the current newest event while
            # creating a durable watermark for later timer runs.
            if minimum_id is None and resume_from_checkpoint:
                latest = [message async for message in client.iter_messages(entity, limit=1)]
                minimum_id = max(0, int(latest[0].id) - 1) if latest else 0
            batch = []
            async for message in client.iter_messages(
                entity,
                min_id=minimum_id or 0,
                reverse=True,
                limit=maximum_messages_per_stream,
            ):
                date = getattr(message, "date", None)
                if not isinstance(date, datetime):
                    continue
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                if date.astimezone(timezone.utc) < cutoff:
                    continue
                batch.append(message)
            for message in batch:
                payload = str(getattr(message, "message", None) or "")
                # ``now`` is captured before the Telegram read.  A post can
                # legitimately arrive while that read is in progress, so
                # snapshot availability per message instead of incorrectly
                # rejecting the just-arrived event as being in the future.
                if as_of is not None:
                    available_at = receipt_time
                else:
                    message_date = getattr(message, "date", None)
                    outer_available_at = (
                        _iso(message_date if message_date.tzinfo else message_date.replace(tzinfo=timezone.utc))
                        if isinstance(message_date, datetime)
                        else receipt_time
                    )
                    available_at = max(_iso(datetime.now(timezone.utc)), outer_available_at)
                report = stage_private_gold_payload(
                    staging,
                    PrivateGoldPayloadEnvelope(payload, available_at, stream),
                    staged_at_utc=available_at,
                )
                pipeline_as_of = available_at
                counters["messages"] += 1
                counters["invalid_items"] += report.invalid_items
                counters["staged_changes"] += report.inserted_or_updated_offers + report.inserted_or_updated_trade_updates
                messages.append((checkpoint_code, stream, int(message.id), _iso(message.date if message.date.tzinfo else message.date.replace(tzinfo=timezone.utc))))
        staging.commit()
        try:
            pipeline = process_private_gold_payloads(staging, market, (), as_of_utc=pipeline_as_of)
            for checkpoint_code, _stream, message_id, message_time in messages:
                advance_source_checkpoint(market, source_code=checkpoint_code, message_id=message_id, event_time_utc=message_time)
            market.commit()
            staging.commit()
        except BaseException:
            market.rollback(); staging.rollback(); raise
        counters["offer_facts"] = pipeline.promotion.offer_facts_upserted
        counters["trade_facts"] = pipeline.promotion.trade_facts_upserted
        counters["paper_minutes"] = pipeline.refreshed_paper_minutes
        return counters
    except PrivateGoldTelegramTransportError:
        raise
    except Exception as exc:
        raise PrivateGoldTelegramTransportError(f"private_gold_telegram_collect_failed:{type(exc).__name__}") from exc
    finally:
        if client is not None:
            try: await client.disconnect()
            except Exception: pass
        if market is not None: market.close()
        if staging is not None: staging.close()
        os.umask(previous_umask)
