"""One crash-aware provider-attempt protocol shared by API and fallback job."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from core.registration_contracts import OTPDeliveryStatus
from core.dr_effects import execute_claimed_inline_effect
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
        outcome = await execute_claimed_inline_effect(
            effect_type="otp_sms",
            provider="smsir",
            idempotency_key=f"otp-sms:{claim.request_id}",
            handler=lambda: send_otp_sms_result_async(claim.mobile_number, claim.otp_code),
        )
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
