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
_MIN_QUEUE_SECRET_LENGTH = 32
_FERNET_DOMAIN = b"trading-bot:telegram-otp-eq:v1\x00"


class TelegramOTPQueueError(RuntimeError):
    """Raised when the ephemeral OTP queue cannot be used safely."""


@dataclass(frozen=True, slots=True)
class TelegramOTPEphemeralHealth:
    worker_present: bool
    pending_count: int
    poison_count: int
    oldest_command_age_seconds: float | None
    receipt_wait_seconds: float


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
    otp_request_id: UUID,
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


async def _quarantine(
    redis,
    *,
    message_id: str,
    request_id: str,
    reason: str,
) -> None:
    await redis.xadd(
        OTP_EQ_POISON_STREAM,
        {
            "request_id": request_id,
            "reason": reason,
            "quarantined_at": str(int(utc_now().timestamp())),
        },
    )
    await redis.xack(OTP_EQ_STREAM, OTP_EQ_GROUP, message_id)
    logger.warning(
        "Telegram OTP command quarantined",
        extra={"event": "otp.eq.poison", "reason": reason},
    )


async def process_telegram_otp_stream_message(
    redis,
    *,
    message_id: str,
    fields: dict[str, Any],
    deliveries: int = 1,
) -> TelegramOTPDeliveryOutcome:
    request_id = str(fields.get("request_id") or "")
    declared_hash = str(fields.get("command_hash") or "")
    ciphertext = str(fields.get("payload") or "")
    if deliveries > OTP_EQ_MAX_DELIVERIES:
        await _quarantine(redis, message_id=message_id, request_id=request_id, reason="max_deliveries")
        return TelegramOTPDeliveryOutcome.PROVIDER_ERROR
    try:
        command = decrypt_otp_command(ciphertext)
    except (TelegramOTPQueueError, InvalidToken, ValueError, TypeError, json.JSONDecodeError):
        await _quarantine(redis, message_id=message_id, request_id=request_id, reason="decrypt_or_contract")
        return TelegramOTPDeliveryOutcome.INVALID
    digest = command_hash(command)
    if request_id != str(command.otp_request_id) or (declared_hash and declared_hash != digest):
        await _quarantine(redis, message_id=message_id, request_id=request_id, reason="hash_mismatch")
        return TelegramOTPDeliveryOutcome.INVALID
    ttl_seconds = _receipt_ttl_seconds(command, now=utc_now())
    existing = await read_receipt_outcome(
        redis,
        otp_request_id=command.otp_request_id,
        expected_hash=digest,
    )
    if existing is not None:
        await redis.xack(OTP_EQ_STREAM, OTP_EQ_GROUP, message_id)
        return existing
    limiter_outcome = await _admit_central_bot(redis, command)
    if limiter_outcome is not None:
        await _write_receipt(
            redis,
            otp_request_id=command.otp_request_id,
            outcome=limiter_outcome,
            digest=digest,
            ttl_seconds=ttl_seconds,
        )
        await redis.xack(OTP_EQ_STREAM, OTP_EQ_GROUP, message_id)
        return limiter_outcome
    outcome = await execute_telegram_otp_via_gateway(command)
    await _write_receipt(
        redis,
        otp_request_id=command.otp_request_id,
        outcome=outcome,
        digest=digest,
        ttl_seconds=ttl_seconds,
    )
    await redis.xack(OTP_EQ_STREAM, OTP_EQ_GROUP, message_id)
    return outcome


async def _read_group_messages(redis, *, consumer: str, count: int = 1) -> list[tuple[str, dict[str, Any], int]]:
    claimed: list[tuple[str, dict[str, Any], int]] = []
    try:
        autoclaim = await redis.xautoclaim(
            OTP_EQ_STREAM,
            OTP_EQ_GROUP,
            consumer,
            OTP_EQ_CLAIM_IDLE_MS,
            "0-0",
            count=count,
        )
        for message_id, fields in _stream_entries(autoclaim):
            claimed.append((message_id, fields, 2))
    except Exception:
        logger.debug("Telegram OTP autoclaim skipped", extra={"event": "otp.eq.autoclaim_skipped"})
    if claimed:
        return claimed
    raw = await redis.xreadgroup(
        OTP_EQ_GROUP,
        consumer,
        streams={OTP_EQ_STREAM: ">"},
        count=count,
        block=1000,
    )
    for message_id, fields in _stream_entries(raw):
        claimed.append((message_id, fields, 1))
    return claimed


async def run_telegram_otp_ephemeral_once(
    redis,
    *,
    consumer: str = "bot",
) -> int:
    await ensure_otp_eq_group(redis)
    await redis.set(OTP_EQ_WORKER_KEY, "1", ex=OTP_EQ_WORKER_TTL_SECONDS)
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


async def inspect_telegram_otp_ephemeral_health(redis) -> TelegramOTPEphemeralHealth:
    worker_present = bool(await redis.get(OTP_EQ_WORKER_KEY))
    try:
        pending_count = int(await redis.xlen(OTP_EQ_STREAM) or 0)
    except Exception:
        pending_count = 0
    try:
        poison_count = int(await redis.xlen(OTP_EQ_POISON_STREAM) or 0)
    except Exception:
        poison_count = 0
    oldest_age: float | None = None
    try:
        first = await redis.xrange(OTP_EQ_STREAM, min="-", max="+", count=1)
        if first:
            _message_id, fields = first[0]
            enqueued_at = int(str((fields or {}).get("enqueued_at") or "0"))
            if enqueued_at > 0:
                oldest_age = max(0.0, utc_now().timestamp() - enqueued_at)
    except Exception:
        oldest_age = None
    return TelegramOTPEphemeralHealth(
        worker_present=worker_present,
        pending_count=pending_count,
        poison_count=poison_count,
        oldest_command_age_seconds=oldest_age,
        receipt_wait_seconds=OTP_EQ_WAIT_SECONDS,
    )


def telegram_otp_ephemeral_health_payload(health: TelegramOTPEphemeralHealth) -> dict[str, Any]:
    return {
        "worker_present": health.worker_present,
        "pending_count": health.pending_count,
        "poison_count": health.poison_count,
        "oldest_command_age_seconds": health.oldest_command_age_seconds,
        "receipt_wait_seconds": health.receipt_wait_seconds,
    }
