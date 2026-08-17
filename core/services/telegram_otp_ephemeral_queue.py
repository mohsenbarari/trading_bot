"""Short-lived Redis Stream transport for Telegram login OTP.

The foreign API only enqueues an encrypted command. The foreign bot is the
sole consumer and the only process that may call Telegram. Plaintext OTP
never appears in Redis keys, receipts, health labels, or logs.
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken

from core import telegram_gateway
from core.config import settings
from core.registration_contracts import (
    TelegramOTPDeliveryCommand,
    TelegramOTPDeliveryOutcome,
    TelegramOTPDeliveryResponse,
)
from core.server_routing import SERVER_FOREIGN, current_server
from core.telegram_delivery_queue_limiter import configured_redis_telegram_delivery_limiter
from core.telegram_delivery_runtime_policy import assert_telegram_provider_execution_authority
from core.utils import utc_now


logger = logging.getLogger(__name__)

OTP_EQ_STREAM = "telegram_otp_eq:commands"
OTP_EQ_GROUP = "telegram_otp_eq"
OTP_EQ_POISON_STREAM = "telegram_otp_eq:poison"
OTP_EQ_WORKER_KEY = "telegram_otp_eq:worker"
OTP_EQ_RECEIPT_PREFIX = "telegram_otp_eq:receipt:"
OTP_EQ_WAIT_SECONDS = 4.0
OTP_EQ_POLL_SECONDS = 0.05
OTP_EQ_WORKER_TTL_SECONDS = 30
OTP_EQ_CLAIM_IDLE_MS = 15_000
OTP_EQ_MAX_DELIVERIES = 3
# OTP itself is two minutes; receipts keep at most five minutes plus a
# one-minute waiter margin. Unused commands may not outlive that window.
OTP_EQ_COMMAND_RETENTION_SECONDS = 360
OTP_EQ_RECEIPT_FALLBACK_TTL_SECONDS = 180
OTP_EQ_POISON_MAXLEN = 256
OTP_EQ_POISON_TTL_SECONDS = 86_400
_MIN_QUEUE_SECRET_LENGTH = 32
_FERNET_DOMAIN = b"trading-bot:telegram-otp-eq:v1\x00"


class TelegramOTPQueueError(RuntimeError):
    """Raised when the ephemeral OTP queue cannot be used safely."""


@dataclass(frozen=True, slots=True)
class TelegramOTPEphemeralHealth:
    worker_present: bool
    pending_count: int | None
    poison_count: int
    oldest_command_age_seconds: float | None
    receipt_wait_seconds: float
    error: str | None = None


def _text(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value)


def command_hash(command: TelegramOTPDeliveryCommand) -> str:
    body = json.dumps(
        command.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def receipt_key(otp_request_id: UUID | str) -> str:
    return f"{OTP_EQ_RECEIPT_PREFIX}{otp_request_id}"


def _queue_secret() -> bytes:
    secret = str(getattr(settings, "telegram_otp_queue_secret", "") or "")
    if len(secret) < _MIN_QUEUE_SECRET_LENGTH:
        raise TelegramOTPQueueError("telegram_otp_queue_secret_missing")
    return secret.encode("utf-8")


def _fernet() -> Fernet:
    key = hashlib.sha256(_FERNET_DOMAIN + _queue_secret()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_otp_command(command: TelegramOTPDeliveryCommand) -> str:
    payload = json.dumps(
        command.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _fernet().encrypt(payload.encode("utf-8")).decode("ascii")


def decrypt_otp_command(ciphertext: str) -> TelegramOTPDeliveryCommand:
    raw = _fernet().decrypt(str(ciphertext).encode("ascii"))
    return TelegramOTPDeliveryCommand.model_validate(json.loads(raw.decode("utf-8")))


def _receipt_ttl_seconds(command: TelegramOTPDeliveryCommand, *, now: datetime) -> int:
    expires_at = command.expires_at.astimezone(timezone.utc)
    return max(1, min(300, int((expires_at - now).total_seconds()) + 60))


def parse_receipt(raw: object | None) -> tuple[str, str] | None:
    text = _text(raw)
    if not text or ":" not in text:
        return None
    status, _, digest = text.partition(":")
    if not status or not digest:
        return None
    return status, digest


def outcome_from_receipt(
    raw: object | None,
    *,
    expected_hash: str,
) -> TelegramOTPDeliveryOutcome | None:
    parsed = parse_receipt(raw)
    if parsed is None:
        return None
    status, digest = parsed
    if status == "processing":
        return None
    if digest != expected_hash:
        return TelegramOTPDeliveryOutcome.INVALID
    try:
        return TelegramOTPDeliveryOutcome(status)
    except ValueError:
        return TelegramOTPDeliveryOutcome.INVALID


async def _write_receipt(
    redis,
    *,
    otp_request_id: UUID | str,
    outcome: TelegramOTPDeliveryOutcome,
    digest: str,
    ttl_seconds: int,
) -> None:
    await redis.set(
        receipt_key(otp_request_id),
        f"{outcome.value}:{digest}",
        ex=ttl_seconds,
    )


async def read_receipt_outcome(
    redis,
    *,
    otp_request_id: UUID,
    expected_hash: str,
) -> TelegramOTPDeliveryOutcome | None:
    return outcome_from_receipt(
        await redis.get(receipt_key(otp_request_id)),
        expected_hash=expected_hash,
    )


async def ensure_otp_eq_group(redis) -> None:
    try:
        await redis.xgroup_create(OTP_EQ_STREAM, OTP_EQ_GROUP, id="0", mkstream=True)
    except Exception as exc:
        message = str(exc).lower()
        if "busygroup" in message or "already exists" in message:
            return
        raise


async def enqueue_telegram_otp_command(
    redis,
    *,
    command: TelegramOTPDeliveryCommand,
    now: datetime | None = None,
) -> str:
    moment = now or utc_now()
    fields = {
        "payload": encrypt_otp_command(command),
        "request_id": str(command.otp_request_id),
        "command_hash": command_hash(command),
        "enqueued_at": str(int(moment.timestamp())),
    }
    message_id = await redis.xadd(OTP_EQ_STREAM, fields)
    await retain_telegram_otp_command_stream(redis)
    return str(message_id)


async def wait_telegram_otp_receipt(
    redis,
    *,
    command: TelegramOTPDeliveryCommand,
    timeout_seconds: float = OTP_EQ_WAIT_SECONDS,
) -> TelegramOTPDeliveryOutcome:
    digest = command_hash(command)
    deadline = asyncio.get_running_loop().time() + max(0.05, float(timeout_seconds))
    saw_worker = False
    while asyncio.get_running_loop().time() < deadline:
        outcome = await read_receipt_outcome(
            redis,
            otp_request_id=command.otp_request_id,
            expected_hash=digest,
        )
        if outcome is not None:
            return outcome
        if await redis.get(OTP_EQ_WORKER_KEY):
            saw_worker = True
        await asyncio.sleep(OTP_EQ_POLL_SECONDS)
    if not saw_worker and not await redis.get(OTP_EQ_WORKER_KEY):
        return TelegramOTPDeliveryOutcome.WORKER_UNAVAILABLE
    return TelegramOTPDeliveryOutcome.TIMEOUT


async def enqueue_telegram_otp_and_wait(
    redis,
    *,
    command: TelegramOTPDeliveryCommand,
    timeout_seconds: float = OTP_EQ_WAIT_SECONDS,
) -> TelegramOTPDeliveryResponse:
    if current_server() != SERVER_FOREIGN:
        raise RuntimeError("telegram_otp_delivery_requires_foreign")
    now = utc_now()
    expires_at = command.expires_at.astimezone(timezone.utc)
    if expires_at <= now:
        return TelegramOTPDeliveryResponse(
            otp_request_id=command.otp_request_id,
            outcome=TelegramOTPDeliveryOutcome.INVALID,
        )
    digest = command_hash(command)
    existing = await read_receipt_outcome(
        redis,
        otp_request_id=command.otp_request_id,
        expected_hash=digest,
    )
    if existing is not None:
        return TelegramOTPDeliveryResponse(
            otp_request_id=command.otp_request_id,
            outcome=existing,
        )
    try:
        await enqueue_telegram_otp_command(redis, command=command, now=now)
    except TelegramOTPQueueError:
        logger.warning(
            "Telegram OTP queue secret is not configured",
            extra={"event": "otp.eq.secret_missing"},
        )
        return TelegramOTPDeliveryResponse(
            otp_request_id=command.otp_request_id,
            outcome=TelegramOTPDeliveryOutcome.WORKER_UNAVAILABLE,
        )
    outcome = await wait_telegram_otp_receipt(
        redis,
        command=command,
        timeout_seconds=timeout_seconds,
    )
    return TelegramOTPDeliveryResponse(
        otp_request_id=command.otp_request_id,
        outcome=outcome,
    )


def classify_telegram_otp_provider_result(result: Any) -> TelegramOTPDeliveryOutcome:
    if result is None:
        return TelegramOTPDeliveryOutcome.PROVIDER_ERROR
    if getattr(result, "ok", False):
        return TelegramOTPDeliveryOutcome.SENT
    status_code = getattr(result, "status_code", None)
    if status_code == 429:
        return TelegramOTPDeliveryOutcome.RATE_LIMITED
    if status_code in {400, 403}:
        return TelegramOTPDeliveryOutcome.UNREACHABLE
    return TelegramOTPDeliveryOutcome.PROVIDER_ERROR


async def execute_telegram_otp_via_gateway(
    command: TelegramOTPDeliveryCommand,
) -> TelegramOTPDeliveryOutcome:
    assert_telegram_provider_execution_authority()
    now = utc_now()
    if command.expires_at.astimezone(timezone.utc) <= now:
        return TelegramOTPDeliveryOutcome.INVALID
    text = f"🔐 کد ورود شما: `{command.otp_code}`\n\nاین کد تا ۲ دقیقه معتبر است."
    try:
        result = await telegram_gateway.send_message(
            command.telegram_id,
            text,
            parse_mode="Markdown",
            timeout=5,
            idempotency_key=f"web-login-otp:{command.otp_request_id}",
        )
    except Exception:
        logger.warning(
            "Telegram OTP provider call failed",
            extra={"event": "otp.eq.provider_exception"},
        )
        return TelegramOTPDeliveryOutcome.PROVIDER_ERROR
    return classify_telegram_otp_provider_result(result)


async def _admit_central_bot(redis, command: TelegramOTPDeliveryCommand) -> TelegramOTPDeliveryOutcome | None:
    limiter = configured_redis_telegram_delivery_limiter(redis, settings=settings)
    job = SimpleNamespace(
        id=f"otp:{command.otp_request_id}",
        destination_key=f"private:telegram:{command.telegram_id}",
        bot_identity="primary",
    )
    try:
        admission = await limiter.acquire(job, now=utc_now())
    except Exception:
        logger.warning(
            "Telegram OTP limiter unavailable",
            extra={"event": "otp.eq.limiter_unavailable"},
        )
        return TelegramOTPDeliveryOutcome.PROVIDER_ERROR
    if admission.allowed:
        return None
    wait = float(getattr(admission, "retry_after_seconds", 0) or 0)
    if 0 < wait <= 2.5:
        await asyncio.sleep(wait)
        try:
            retry = await limiter.acquire(job, now=utc_now())
        except Exception:
            return TelegramOTPDeliveryOutcome.PROVIDER_ERROR
        if retry.allowed:
            return None
    return TelegramOTPDeliveryOutcome.RATE_LIMITED


def _message_pairs(raw: Any) -> list[tuple[str, dict[str, Any]]]:
    entries: list[tuple[str, dict[str, Any]]] = []
    for item in raw or ():
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        message_id, fields = item[0], item[1]
        if isinstance(fields, dict):
            entries.append((str(message_id), fields))
    return entries


def _stream_entries(raw: Any) -> list[tuple[str, dict[str, Any]]]:
    if not raw:
        return []
    if isinstance(raw, dict):
        pairs: list[tuple[str, dict[str, Any]]] = []
        for messages in raw.values():
            pairs.extend(_message_pairs(messages))
        return pairs
    if not isinstance(raw, (list, tuple)):
        return []
    first = raw[0]
    if isinstance(first, (bytes, str)):
        # xautoclaim: [next_id, [(id, fields), ...], optional deleted]
        if len(raw) >= 2:
            return _message_pairs(raw[1])
        return []
    pairs: list[tuple[str, dict[str, Any]]] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        stream_or_id, maybe_fields = item[0], item[1]
        if isinstance(maybe_fields, dict):
            pairs.append((str(stream_or_id), maybe_fields))
            continue
        pairs.extend(_message_pairs(maybe_fields))
    return pairs


def _stream_id_ms(message_id: str) -> int | None:
    text = str(message_id or "")
    head, separator, _tail = text.partition("-")
    if not separator or not head.isdigit():
        return None
    return int(head)


def _mapping(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return {str(key): raw[key] for key in raw}
    if not isinstance(raw, (list, tuple)):
        return {}
    mapped: dict[str, Any] = {}
    for index in range(0, len(raw) - 1, 2):
        mapped[str(raw[index])] = raw[index + 1]
    return mapped


def _pending_delivery_count(entry: Any) -> tuple[str, int] | None:
    if isinstance(entry, dict):
        message_id = str(entry.get("message_id") or entry.get("msgid") or "")
        raw_count = entry.get("times_delivered", entry.get("deliveries"))
        if not message_id or raw_count is None:
            return None
        try:
            return message_id, int(raw_count)
        except (TypeError, ValueError):
            return None
    if isinstance(entry, (list, tuple)) and len(entry) >= 4:
        try:
            return str(entry[0]), int(entry[3])
        except (TypeError, ValueError):
            return None
    return None


def _group_info(raw: Any) -> dict[str, Any] | None:
    rows = raw or []
    if isinstance(rows, dict):
        rows = [rows]
    for item in rows:
        info = _mapping(item)
        name = str(info.get("name") or "")
        if name == OTP_EQ_GROUP:
            return info
    return None


async def _ack_and_delete(redis, message_id: str) -> None:
    """ACK then delete one command. Idempotent; never sends OTP."""
    try:
        pipeline = redis.pipeline(transaction=True)
        if hasattr(pipeline, "__aenter__"):
            async with pipeline as pipe:
                pipe.xack(OTP_EQ_STREAM, OTP_EQ_GROUP, message_id)
                pipe.xdel(OTP_EQ_STREAM, message_id)
                await pipe.execute()
        else:
            pipeline.xack(OTP_EQ_STREAM, OTP_EQ_GROUP, message_id)
            pipeline.xdel(OTP_EQ_STREAM, message_id)
            await pipeline.execute()
        return
    except Exception:
        logger.warning(
            "Telegram OTP finalize pipeline failed",
            extra={"event": "otp.eq.finalize_pipeline_failed"},
        )
    try:
        await redis.xack(OTP_EQ_STREAM, OTP_EQ_GROUP, message_id)
        await redis.xdel(OTP_EQ_STREAM, message_id)
    except Exception:
        logger.warning(
            "Telegram OTP finalize recovery failed",
            extra={"event": "otp.eq.finalize_ack_delete_retry_failed"},
        )
        raise


async def finalize_telegram_otp_command(
    redis,
    *,
    message_id: str,
    otp_request_id: UUID | str | None = None,
    outcome: TelegramOTPDeliveryOutcome | None = None,
    digest: str | None = None,
    ttl_seconds: int | None = None,
) -> None:
    """Persist a terminal receipt first, then ACK+DELETE the command.

    Re-running this helper is safe. A crash after the receipt write leaves a
    reclaim that observes the receipt, does not resend, and deletes the body.
    """
    if (
        otp_request_id is not None
        and outcome is not None
        and digest
    ):
        await _write_receipt(
            redis,
            otp_request_id=otp_request_id,
            outcome=outcome,
            digest=digest,
            ttl_seconds=max(1, int(ttl_seconds or OTP_EQ_RECEIPT_FALLBACK_TTL_SECONDS)),
        )
    await _ack_and_delete(redis, message_id)
    await retain_telegram_otp_command_stream(redis)


async def _bound_poison_stream(redis) -> None:
    try:
        await redis.xtrim(
            OTP_EQ_POISON_STREAM,
            maxlen=OTP_EQ_POISON_MAXLEN,
            approximate=False,
        )
    except Exception:
        logger.warning(
            "Telegram OTP poison trim failed",
            extra={"event": "otp.eq.poison_trim_failed"},
        )
    try:
        await redis.expire(OTP_EQ_POISON_STREAM, OTP_EQ_POISON_TTL_SECONDS)
    except Exception:
        logger.warning(
            "Telegram OTP poison expire failed",
            extra={"event": "otp.eq.poison_expire_failed"},
        )


async def _record_poison(redis, *, request_id: str, reason: str) -> None:
    await redis.xadd(
        OTP_EQ_POISON_STREAM,
        {
            "request_id": request_id,
            "reason": reason,
            "quarantined_at": str(int(utc_now().timestamp())),
        },
    )
    await _bound_poison_stream(redis)
    logger.warning(
        "Telegram OTP command quarantined",
        extra={"event": "otp.eq.poison", "reason": reason},
    )


async def retain_telegram_otp_command_stream(redis) -> None:
    """Drop only commands older than OTP lifetime plus operational margin.

    Pending entries are never trimmed. A small MAXLEN is intentionally not
    used because it can evict a still-valid in-flight command.
    """
    cutoff_ms = int((utc_now().timestamp() - OTP_EQ_COMMAND_RETENTION_SECONDS) * 1000)
    cutoff_id = f"{max(0, cutoff_ms)}-0"
    threshold = cutoff_id
    try:
        pending_ids = await _pending_message_ids(redis)
    except Exception:
        logger.warning(
            "Telegram OTP retention skipped pending lookup",
            extra={"event": "otp.eq.retention_pending_unavailable"},
        )
        return
    if pending_ids:
        oldest_pending = min(
            pending_ids,
            key=lambda item: _stream_id_ms(item) if _stream_id_ms(item) is not None else 2**63,
        )
        oldest_ms = _stream_id_ms(oldest_pending)
        if oldest_ms is not None and oldest_ms < cutoff_ms:
            threshold = oldest_pending
    try:
        await redis.xtrim(OTP_EQ_STREAM, minid=threshold, approximate=False)
    except TypeError:
        try:
            await redis.xtrim(OTP_EQ_STREAM, maxlen=0, minid=threshold, approximate=False)
        except Exception:
            logger.warning(
                "Telegram OTP command trim failed",
                extra={"event": "otp.eq.command_trim_failed"},
            )
    except Exception:
        logger.warning(
            "Telegram OTP command trim failed",
            extra={"event": "otp.eq.command_trim_failed"},
        )


async def _pending_message_ids(redis) -> list[str]:
    try:
        rows = await redis.xpending_range(
            OTP_EQ_STREAM,
            OTP_EQ_GROUP,
            min="-",
            max="+",
            count=1024,
        )
    except Exception as exc:
        message = str(exc).lower()
        if "nogroup" in message or "no such key" in message:
            return []
        raise
    ids: list[str] = []
    for entry in rows or ():
        parsed = _pending_delivery_count(entry)
        if parsed is None:
            if isinstance(entry, dict) and entry.get("message_id"):
                ids.append(str(entry["message_id"]))
            elif isinstance(entry, (list, tuple)) and entry:
                ids.append(str(entry[0]))
            continue
        ids.append(parsed[0])
    return ids


async def _times_delivered(redis, message_ids: list[str]) -> dict[str, int | None]:
    wanted = {str(item) for item in message_ids}
    found: dict[str, int] = {}
    if not wanted:
        return {}
    try:
        rows = await redis.xpending_range(
            OTP_EQ_STREAM,
            OTP_EQ_GROUP,
            min="-",
            max="+",
            count=max(len(wanted) * 4, 16),
        )
    except Exception:
        logger.warning(
            "Telegram OTP delivery metadata unavailable",
            extra={"event": "otp.eq.delivery_count_unavailable"},
        )
        return {item: None for item in wanted}
    for entry in rows or ():
        parsed = _pending_delivery_count(entry)
        if parsed is None:
            continue
        message_id, deliveries = parsed
        if message_id in wanted:
            found[message_id] = deliveries
    return {item: found.get(item) for item in wanted}


async def _finalize_without_send(
    redis,
    *,
    message_id: str,
    request_id: str,
    digest: str,
    outcome: TelegramOTPDeliveryOutcome,
    reason: str | None = None,
    ttl_seconds: int = OTP_EQ_RECEIPT_FALLBACK_TTL_SECONDS,
) -> TelegramOTPDeliveryOutcome:
    existing = None
    if request_id and digest:
        existing = outcome_from_receipt(
            await redis.get(receipt_key(request_id)),
            expected_hash=digest,
        )
    if existing is None:
        if reason:
            await _record_poison(redis, request_id=request_id, reason=reason)
        if request_id and digest:
            await finalize_telegram_otp_command(
                redis,
                message_id=message_id,
                otp_request_id=request_id,
                outcome=outcome,
                digest=digest,
                ttl_seconds=ttl_seconds,
            )
            return outcome
        await finalize_telegram_otp_command(redis, message_id=message_id)
        return outcome
    await finalize_telegram_otp_command(redis, message_id=message_id)
    return existing


async def process_telegram_otp_stream_message(
    redis,
    *,
    message_id: str,
    fields: dict[str, Any],
    deliveries: int | None = 1,
) -> TelegramOTPDeliveryOutcome:
    request_id = str(fields.get("request_id") or "")
    declared_hash = str(fields.get("command_hash") or "")
    ciphertext = str(fields.get("payload") or "")
    if deliveries is None:
        return await _finalize_without_send(
            redis,
            message_id=message_id,
            request_id=request_id,
            digest=declared_hash,
            outcome=TelegramOTPDeliveryOutcome.PROVIDER_ERROR,
            reason="delivery_count_unknown",
        )
    if deliveries > OTP_EQ_MAX_DELIVERIES:
        logger.warning(
            "Telegram OTP max deliveries exceeded",
            extra={"event": "otp.eq.max_deliveries"},
        )
        return await _finalize_without_send(
            redis,
            message_id=message_id,
            request_id=request_id,
            digest=declared_hash,
            outcome=TelegramOTPDeliveryOutcome.PROVIDER_ERROR,
            reason="max_deliveries",
        )
    try:
        command = decrypt_otp_command(ciphertext)
    except (TelegramOTPQueueError, InvalidToken, ValueError, TypeError, json.JSONDecodeError):
        return await _finalize_without_send(
            redis,
            message_id=message_id,
            request_id=request_id,
            digest=declared_hash,
            outcome=TelegramOTPDeliveryOutcome.INVALID,
            reason="decrypt_or_contract",
        )
    digest = command_hash(command)
    if request_id != str(command.otp_request_id) or (declared_hash and declared_hash != digest):
        return await _finalize_without_send(
            redis,
            message_id=message_id,
            request_id=request_id,
            digest=declared_hash or digest,
            outcome=TelegramOTPDeliveryOutcome.INVALID,
            reason="hash_mismatch",
        )
    ttl_seconds = _receipt_ttl_seconds(command, now=utc_now())
    existing = await read_receipt_outcome(
        redis,
        otp_request_id=command.otp_request_id,
        expected_hash=digest,
    )
    if existing is not None:
        await finalize_telegram_otp_command(redis, message_id=message_id)
        return existing
    limiter_outcome = await _admit_central_bot(redis, command)
    if limiter_outcome is not None:
        await finalize_telegram_otp_command(
            redis,
            message_id=message_id,
            otp_request_id=command.otp_request_id,
            outcome=limiter_outcome,
            digest=digest,
            ttl_seconds=ttl_seconds,
        )
        return limiter_outcome
    outcome = await execute_telegram_otp_via_gateway(command)
    await finalize_telegram_otp_command(
        redis,
        message_id=message_id,
        otp_request_id=command.otp_request_id,
        outcome=outcome,
        digest=digest,
        ttl_seconds=ttl_seconds,
    )
    return outcome


async def _read_group_messages(redis, *, consumer: str, count: int = 1) -> list[tuple[str, dict[str, Any], int | None]]:
    claimed: list[tuple[str, dict[str, Any]]] = []
    try:
        autoclaim = await redis.xautoclaim(
            OTP_EQ_STREAM,
            OTP_EQ_GROUP,
            consumer,
            OTP_EQ_CLAIM_IDLE_MS,
            "0-0",
            count=count,
        )
        claimed.extend(_stream_entries(autoclaim))
    except Exception:
        logger.debug("Telegram OTP autoclaim skipped", extra={"event": "otp.eq.autoclaim_skipped"})
    if not claimed:
        raw = await redis.xreadgroup(
            OTP_EQ_GROUP,
            consumer,
            streams={OTP_EQ_STREAM: ">"},
            count=count,
            block=1000,
        )
        claimed.extend(_stream_entries(raw))
    if not claimed:
        return []
    deliveries = await _times_delivered(redis, [message_id for message_id, _fields in claimed])
    return [
        (message_id, fields, deliveries.get(message_id))
        for message_id, fields in claimed
    ]


async def run_telegram_otp_ephemeral_once(
    redis,
    *,
    consumer: str = "bot",
) -> int:
    await ensure_otp_eq_group(redis)
    await redis.set(OTP_EQ_WORKER_KEY, "1", ex=OTP_EQ_WORKER_TTL_SECONDS)
    await retain_telegram_otp_command_stream(redis)
    messages = await _read_group_messages(redis, consumer=consumer, count=1)
    processed = 0
    for message_id, fields, deliveries in messages:
        await process_telegram_otp_stream_message(
            redis,
            message_id=message_id,
            fields=fields,
            deliveries=deliveries,
        )
        processed += 1
    return processed


async def telegram_otp_ephemeral_loop(redis, *, consumer: str = "bot") -> None:
    await ensure_otp_eq_group(redis)
    while True:
        try:
            processed = await run_telegram_otp_ephemeral_once(redis, consumer=consumer)
            if processed == 0:
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Telegram OTP ephemeral worker cycle failed",
                extra={"event": "otp.eq.worker_cycle_failed"},
            )
            await asyncio.sleep(0.5)


def configured_telegram_otp_ephemeral_worker_factory(settings_obj=settings):
    async def run_telegram_otp_ephemeral_worker() -> None:
        import redis.asyncio as redis_async

        redis_client = redis_async.Redis.from_url(
            str(getattr(settings_obj, "redis_url", "") or ""),
            decode_responses=True,
        )
        try:
            await telegram_otp_ephemeral_loop(redis_client, consumer="bot")
        finally:
            await redis_client.aclose()

    run_telegram_otp_ephemeral_worker.__name__ = "run_telegram_otp_ephemeral_worker"
    return run_telegram_otp_ephemeral_worker


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def inspect_telegram_otp_ephemeral_health(redis) -> TelegramOTPEphemeralHealth:
    worker_present = bool(await redis.get(OTP_EQ_WORKER_KEY))
    try:
        poison_count = int(await redis.xlen(OTP_EQ_POISON_STREAM) or 0)
    except Exception:
        poison_count = 0
    try:
        return await _inspect_outstanding_health(
            redis,
            worker_present=worker_present,
            poison_count=poison_count,
        )
    except TelegramOTPQueueError as exc:
        logger.warning(
            "Telegram OTP health metadata unavailable",
            extra={"event": "otp.eq.health_metadata_unavailable", "reason": str(exc)},
        )
        return TelegramOTPEphemeralHealth(
            worker_present=worker_present,
            pending_count=None,
            poison_count=poison_count,
            oldest_command_age_seconds=None,
            receipt_wait_seconds=OTP_EQ_WAIT_SECONDS,
            error=str(exc) or "otp_eq_health_metadata_unavailable",
        )
    except Exception:
        logger.warning(
            "Telegram OTP health inspection failed",
            extra={"event": "otp.eq.health_inspect_failed"},
        )
        return TelegramOTPEphemeralHealth(
            worker_present=worker_present,
            pending_count=None,
            poison_count=poison_count,
            oldest_command_age_seconds=None,
            receipt_wait_seconds=OTP_EQ_WAIT_SECONDS,
            error="otp_eq_health_metadata_unavailable",
        )


async def _inspect_outstanding_health(
    redis,
    *,
    worker_present: bool,
    poison_count: int,
) -> TelegramOTPEphemeralHealth:
    try:
        stream_length = int(await redis.xlen(OTP_EQ_STREAM) or 0)
    except Exception as exc:
        raise TelegramOTPQueueError("otp_eq_health_stream_unavailable") from exc
    try:
        groups = await redis.xinfo_groups(OTP_EQ_STREAM)
    except Exception as exc:
        message = str(exc).lower()
        if "no such key" in message or "no such file" in message:
            groups = []
        else:
            raise TelegramOTPQueueError("otp_eq_health_groups_unavailable") from exc
    info = _group_info(groups)
    if info is None:
        if stream_length == 0:
            return TelegramOTPEphemeralHealth(
                worker_present=worker_present,
                pending_count=0,
                poison_count=poison_count,
                oldest_command_age_seconds=None,
                receipt_wait_seconds=OTP_EQ_WAIT_SECONDS,
            )
        outstanding_ids = [
            str(message_id)
            for message_id, _fields in await redis.xrange(OTP_EQ_STREAM, min="-", max="+")
        ]
        return TelegramOTPEphemeralHealth(
            worker_present=worker_present,
            pending_count=len(outstanding_ids),
            poison_count=poison_count,
            oldest_command_age_seconds=_oldest_age_seconds(outstanding_ids, {}),
            receipt_wait_seconds=OTP_EQ_WAIT_SECONDS,
        )
    pending = _int_or_none(info.get("pending"))
    lag = _int_or_none(info.get("lag"))
    entries_read = _int_or_none(info.get("entries-read", info.get("entries_read")))
    if lag is None:
        if entries_read is None:
            raise TelegramOTPQueueError("otp_eq_health_lag_unavailable")
        lag = max(0, stream_length - entries_read)
    if pending is None:
        raise TelegramOTPQueueError("otp_eq_health_pending_unavailable")
    try:
        summary = await redis.xpending(OTP_EQ_STREAM, OTP_EQ_GROUP)
    except Exception as exc:
        raise TelegramOTPQueueError("otp_eq_health_xpending_unavailable") from exc
    summary_pending = 0
    if isinstance(summary, dict):
        summary_pending = _int_or_none(summary.get("pending")) or 0
    elif isinstance(summary, (list, tuple)) and summary:
        summary_pending = _int_or_none(summary[0]) or 0
    if summary_pending != pending:
        raise TelegramOTPQueueError("otp_eq_health_pending_inconsistent")
    outstanding = pending + lag
    if outstanding == 0:
        return TelegramOTPEphemeralHealth(
            worker_present=worker_present,
            pending_count=0,
            poison_count=poison_count,
            oldest_command_age_seconds=None,
            receipt_wait_seconds=OTP_EQ_WAIT_SECONDS,
        )
    pending_ids = await _pending_message_ids(redis)
    last_delivered = str(info.get("last-delivered-id") or info.get("last_delivered_id") or "0-0")
    unread_ids = await _unread_message_ids(redis, last_delivered_id=last_delivered, pending_ids=set(pending_ids))
    outstanding_ids = list(dict.fromkeys([*pending_ids, *unread_ids]))
    if outstanding > 0 and not outstanding_ids:
        raise TelegramOTPQueueError("otp_eq_health_outstanding_inconsistent")
    fields_by_id = {
        str(message_id): fields
        for message_id, fields in await redis.xrange(OTP_EQ_STREAM, min="-", max="+")
        if str(message_id) in set(outstanding_ids)
    }
    return TelegramOTPEphemeralHealth(
        worker_present=worker_present,
        pending_count=outstanding,
        poison_count=poison_count,
        oldest_command_age_seconds=_oldest_age_seconds(outstanding_ids, fields_by_id),
        receipt_wait_seconds=OTP_EQ_WAIT_SECONDS,
    )


async def _unread_message_ids(
    redis,
    *,
    last_delivered_id: str,
    pending_ids: set[str],
) -> list[str]:
    minimum = "-" if last_delivered_id in {"", "0-0"} else f"({last_delivered_id}"
    try:
        rows = await redis.xrange(OTP_EQ_STREAM, min=minimum, max="+")
    except Exception as exc:
        raise TelegramOTPQueueError("otp_eq_health_unread_unavailable") from exc
    unread: list[str] = []
    for message_id, _fields in rows or ():
        text_id = str(message_id)
        if text_id in pending_ids:
            continue
        unread.append(text_id)
    return unread


def _oldest_age_seconds(
    message_ids: list[str],
    fields_by_id: dict[str, dict[str, Any]],
) -> float | None:
    now = utc_now().timestamp()
    ages: list[float] = []
    for message_id in message_ids:
        fields = fields_by_id.get(message_id) or {}
        enqueued_raw = str(fields.get("enqueued_at") or "")
        if enqueued_raw.isdigit():
            ages.append(max(0.0, now - int(enqueued_raw)))
            continue
        stream_ms = _stream_id_ms(message_id)
        if stream_ms is not None:
            ages.append(max(0.0, now - (stream_ms / 1000.0)))
    if not ages:
        return None
    return max(ages)


def telegram_otp_ephemeral_health_payload(health: TelegramOTPEphemeralHealth) -> dict[str, Any]:
    return {
        "worker_present": health.worker_present,
        "pending_count": health.pending_count,
        "poison_count": health.poison_count,
        "oldest_command_age_seconds": health.oldest_command_age_seconds,
        "receipt_wait_seconds": health.receipt_wait_seconds,
        "error": health.error,
    }
