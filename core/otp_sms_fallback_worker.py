"""Iran-only background-leader job for due Web-login OTP SMS fallback."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
import logging

from core.background_job_authority import JOB_OTP_SMS_FALLBACK, assert_background_job_authority
from core.config import settings
from core.redis import get_redis_client
from core.registration_contracts import OTPDeliveryStatus
from core.services.otp_delivery_state_service import (
    claim_sms_delivery,
    due_otp_request_ids,
    load_otp_delivery_state,
    record_sms_delivery_result,
)
from core.sms import SMSDeliveryOutcome, send_otp_sms_result_async
from core.utils import utc_now


logger = logging.getLogger(__name__)
_INTERVAL_SECONDS = 1.0
_BATCH_LIMIT = 100


@dataclass(frozen=True, slots=True)
class OTPFallbackCycleReport:
    due_count: int
    outcome_counts: dict[str, int]


def _delivery_status(outcome: SMSDeliveryOutcome) -> OTPDeliveryStatus:
    if outcome == SMSDeliveryOutcome.ACCEPTED:
        return OTPDeliveryStatus.ACCEPTED
    if outcome == SMSDeliveryOutcome.AMBIGUOUS:
        return OTPDeliveryStatus.AMBIGUOUS
    return OTPDeliveryStatus.FAILED


async def run_otp_sms_fallback_cycle(*, limit: int = _BATCH_LIMIT) -> OTPFallbackCycleReport:
    assert_background_job_authority(JOB_OTP_SMS_FALLBACK)
    if not (
        settings.telegram_login_otp_enabled
        and settings.otp_sms_auto_fallback_enabled
    ):
        raise RuntimeError("otp_sms_fallback_disabled")

    redis = get_redis_client()
    request_ids = await due_otp_request_ids(redis, now=utc_now(), limit=limit)
    semaphore = asyncio.Semaphore(
        min(20, max(1, int(settings.otp_sms_fallback_job_concurrency)))
    )

    async def deliver(request_id):
        async with semaphore:
            state = await load_otp_delivery_state(redis, request_id=request_id)
            if state is None:
                return "missing"
            claim = await claim_sms_delivery(
                redis,
                state=state,
                require_due=True,
            )
            if claim is None:
                return "not_claimed"
            try:
                outcome = await send_otp_sms_result_async(
                    claim.mobile_number,
                    claim.otp_code,
                )
            except Exception:
                outcome = SMSDeliveryOutcome.AMBIGUOUS
            await record_sms_delivery_result(
                redis,
                request_id=claim.request_id,
                outcome=_delivery_status(outcome),
            )
            logger.info(
                "OTP SMS fallback delivery completed",
                extra={
                    "event": "otp.sms_delivery_result",
                    "otp_request_id": str(claim.request_id),
                    "outcome": outcome.value,
                },
            )
            return outcome.value

    outcomes = await asyncio.gather(*(deliver(request_id) for request_id in request_ids))
    return OTPFallbackCycleReport(
        due_count=len(request_ids),
        outcome_counts=dict(Counter(outcomes)),
    )


async def otp_sms_fallback_loop() -> None:
    assert_background_job_authority(JOB_OTP_SMS_FALLBACK)
    while True:
        try:
            await run_otp_sms_fallback_cycle()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "OTP SMS fallback cycle failed",
                extra={
                    "event": "otp.sms_fallback_cycle_failed",
                    "error_type": type(exc).__name__,
                },
            )
        await asyncio.sleep(_INTERVAL_SECONDS)
