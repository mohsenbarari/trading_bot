# core/sms.py
"""
SMS service using SMS.ir REST APIs for OTP delivery and notifications.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from core.config import settings
from core.deployment_surface import sms_public_host
from core.log_redaction import mask_mobile
from core.utils import normalize_persian_numerals

logger = logging.getLogger(__name__)


def _api_url(path: str) -> str:
    base_url = (settings.smsir_base_url or "https://api.sms.ir").rstrip("/")
    return f"{base_url}/{path.lstrip('/')}"


def _smsir_headers() -> dict[str, str]:
    if not settings.smsir_api_key:
        raise RuntimeError("SMSIR_API_KEY is not configured")
    return {
        "X-API-KEY": settings.smsir_api_key,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _normalize_mobile(mobile: str) -> str:
    value = normalize_persian_numerals(str(mobile or "")).strip()
    if value.startswith("+98"):
        return "0" + value[3:]
    if value.startswith("0098"):
        return "0" + value[4:]
    return value


def _response_is_success(data: dict[str, Any]) -> bool:
    return data.get("status") in (1, "1", True)


def _post_smsir(path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    try:
        response = httpx.post(
            _api_url(path),
            headers=_smsir_headers(),
            json=payload,
            timeout=settings.smsir_timeout_seconds,
        )
    except Exception as exc:
        logger.error("SMS.ir request failed for %s: %s", path, exc)
        return None

    try:
        data = response.json()
    except ValueError:
        logger.error(
            "SMS.ir returned non-JSON response for %s: http_status=%s",
            path,
            response.status_code,
        )
        return None

    if response.status_code < 200 or response.status_code >= 300:
        logger.error(
            "SMS.ir HTTP error for %s: http_status=%s provider_status=%s message=%s",
            path,
            response.status_code,
            data.get("status"),
            data.get("message"),
        )
        return None

    if not _response_is_success(data):
        logger.error(
            "SMS.ir rejected request for %s: provider_status=%s message=%s",
            path,
            data.get("status"),
            data.get("message"),
        )
        return None

    return data


def _configured_otp_template_id() -> int | None:
    raw_template_id = settings.smsir_otp_template_id
    if raw_template_id is None or str(raw_template_id).strip() == "":
        return None

    try:
        return int(str(raw_template_id).strip())
    except ValueError as exc:
        raise RuntimeError("SMSIR_OTP_TEMPLATE_ID must be an integer") from exc


def _send_verify_sms(mobile: str, code: str, template_id: int) -> bool:
    parameter_name = (settings.smsir_otp_template_parameter or "Code").strip() or "Code"
    normalized_mobile = _normalize_mobile(mobile)
    payload = {
        "mobile": normalized_mobile,
        "templateId": template_id,
        "parameters": [
            {
                "name": parameter_name,
                "value": str(code),
            }
        ],
    }
    data = _post_smsir("v1/send/verify", payload)
    if data is None:
        return False

    provider_data = data.get("data")
    if not isinstance(provider_data, dict) or provider_data.get("messageId") in (None, 0, "0"):
        logger.error("SMS.ir verify send returned no usable messageId for %s", mask_mobile(normalized_mobile))
        return False

    logger.info("SMS.ir verify SMS sent to %s", mask_mobile(normalized_mobile))
    return True


def send_sms(mobile: str, message: str) -> bool:
    """
    Send a plain text SMS to a mobile number through SMS.ir bulk endpoint.
    Returns True on accepted delivery request, False on configuration/provider failure.
    """
    normalized_mobile = _normalize_mobile(mobile)
    if not settings.smsir_line_number:
        logger.error("SMSIR_LINE_NUMBER is not configured")
        return False

    payload = {
        "lineNumber": settings.smsir_line_number,
        "messageText": message,
        "mobiles": [normalized_mobile],
    }
    data = _post_smsir("v1/send/bulk", payload)
    if data is None:
        return False

    provider_data = data.get("data")
    message_ids = provider_data.get("messageIds") if isinstance(provider_data, dict) else None
    if not isinstance(message_ids, list) or not message_ids:
        logger.error("SMS.ir bulk send returned no messageIds for %s", mask_mobile(normalized_mobile))
        return False

    first_message_id = message_ids[0]
    if first_message_id in (None, 0, "0"):
        logger.warning(
            "SMS.ir did not accept SMS for %s: message_id=%s",
            mask_mobile(normalized_mobile),
            first_message_id,
        )
        return False

    logger.info("SMS.ir SMS sent to %s", mask_mobile(normalized_mobile))
    return True


def send_otp_sms(mobile: str, code: str) -> bool:
    """Send OTP code via SMS.ir verify template when configured, otherwise via plain SMS."""
    try:
        template_id = _configured_otp_template_id()
    except RuntimeError as exc:
        logger.error("Invalid SMS.ir OTP configuration: %s", exc)
        return False

    if template_id is not None:
        return _send_verify_sms(mobile, code, template_id)

    public_host = sms_public_host(settings)
    message = (
        f"کد تایید شما: {code}\n"
        f"این کد تا ۲ دقیقه معتبر است.\n"
        f"\n"
        f"@{public_host} #{code}"
    )
    return send_sms(mobile, message)


def send_invitation_sms(
    mobile: str,
    account_name: str,
    bot_link: str,
    web_link: str,
) -> bool:
    """
    Send invitation SMS with the web registration link only.

    bot_link is kept in the signature for caller compatibility while Telegram
    registration links are temporarily paused in SMS content.
    """
    message = (
        f"کاربر گرامی شما بعنوان معامله گر به سامانه معاملاتی دعوت شدین.\n"
        f"برای تکمیل ثبت نام از طریق لینک زیر اقدام فرمایید:\n"
        f"{web_link}"
    )
    return send_sms(mobile, message)


def send_accountant_invitation_sms(
    mobile: str,
    relation_display_name: str,
    web_link: str,
) -> bool:
    """Send accountant invitation SMS with a web-only registration link."""
    message = (
        f"کاربر گرامی شما بعنوان حسابدار به سامانه معاملاتی دعوت شدین\n"
        f"برای تکمیل ثبت نام از طریق لینک زیر اقدام فرمایید:\n"
        f"{web_link}"
    )
    return send_sms(mobile, message)


def send_customer_invitation_sms(
    mobile: str,
    management_name: str,
    web_link: str,
) -> bool:
    """Send customer invitation SMS with a web-only registration link."""
    message = (
        f"کاربر گرامی شما بعنوان مشتری به سامانه معاملاتی دعوت شدین\n"
        f"برای تکمیل ثبت نام از طریق لینک زیر اقدام فرمایید:\n"
        f"{web_link}"
    )
    return send_sms(mobile, message)
