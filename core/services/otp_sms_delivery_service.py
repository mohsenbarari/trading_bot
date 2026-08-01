"""One crash-aware provider-attempt protocol shared by API and fallback job."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from core.db import require_external_effect_execution_authorization
from core.external_effect_execution_gate import EXTERNAL_EFFECT_SCOPE_SMS_PROVIDER_DELIVERY
from core.registration_contracts import OTPDeliveryStatus
from core.services.otp_delivery_state_service import (
    OTPDeliveryClaim,
    mark_sms_provider_attempt_started,
    record_sms_delivery_result,
)
from core.sms import SMSDeliveryOutcome, send_otp_sms_result_async


@dataclass(frozen=True, slots=True)
class OTPSMSAttemptResult:
    outcome: SMSDeliveryOutcome
    provider_attempted: bool
    result_recorded: bool


def delivery_status(outcome: SMSDeliveryOutcome) -> OTPDeliveryStatus:
    if outcome == SMSDeliveryOutcome.ACCEPTED:
        return OTPDeliveryStatus.ACCEPTED
    if outcome == SMSDeliveryOutcome.AMBIGUOUS:
        return OTPDeliveryStatus.AMBIGUOUS
    return OTPDeliveryStatus.FAILED


async def execute_claimed_otp_sms_delivery(
    redis,
    *,
    claim: OTPDeliveryClaim,
) -> OTPSMSAttemptResult:
    """Mark provider I/O before sending and finalize only the same claim generation."""

    # This runs before the durable provider-attempt marker.  A disabled gate
    # is a no-op; an enabled missing/expired receipt cannot turn a pending OTP
    # into a claimed/retryable external-effect attempt.
    require_external_effect_execution_authorization(
        EXTERNAL_EFFECT_SCOPE_SMS_PROVIDER_DELIVERY
    )

    try:
        provider_started = await mark_sms_provider_attempt_started(redis, claim=claim)
    except Exception:
        provider_started = False
    if not provider_started:
        return OTPSMSAttemptResult(
            outcome=SMSDeliveryOutcome.AMBIGUOUS,
            provider_attempted=False,
            result_recorded=False,
        )

    try:
        outcome = await send_otp_sms_result_async(claim.mobile_number, claim.otp_code)
    except asyncio.CancelledError:
        raise
    except Exception:
        outcome = SMSDeliveryOutcome.AMBIGUOUS

    try:
        recorded = await record_sms_delivery_result(
            redis,
            claim=claim,
            outcome=delivery_status(outcome),
        )
    except Exception:
        recorded = False
    return OTPSMSAttemptResult(
        outcome=outcome if recorded else SMSDeliveryOutcome.AMBIGUOUS,
        provider_attempted=True,
        result_recorded=recorded,
    )
