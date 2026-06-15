# core/sms.py
"""SMS service using SMS.ir template/bulk APIs for OTP delivery and notifications."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from core.config import settings
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


def _configured_template_id(raw_template_id: str | int | None, *, setting_name: str) -> int | None:
    if raw_template_id is None or str(raw_template_id).strip() == "":
        return None

    try:
        return int(str(raw_template_id).strip())
    except ValueError as exc:
        raise RuntimeError(f"{setting_name} must be an integer") from exc


def _send_template_sms(
    mobile: str,
    *,
    template_id: int,
    parameters: list[dict[str, str]],
) -> bool:
    normalized_mobile = _normalize_mobile(mobile)
    payload = {
        "mobile": normalized_mobile,
        "templateId": template_id,
        "parameters": parameters,
    }
    data = _post_smsir("v1/send/verify", payload)
    if data is None:
        return False

    provider_data = data.get("data")
    if not isinstance(provider_data, dict) or provider_data.get("messageId") in (None, 0, "0"):
        logger.error("SMS.ir verify send returned no usable messageId for %s", mask_mobile(normalized_mobile))
        return False

    logger.info("SMS.ir template SMS sent to %s template_id=%s", mask_mobile(normalized_mobile), template_id)
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
    """Send OTP code strictly through the configured SMS.ir verify template."""
    try:
        template_id = _configured_template_id(
            settings.smsir_otp_template_id,
            setting_name="SMSIR_OTP_TEMPLATE_ID",
        )
    except RuntimeError as exc:
        logger.error("Invalid SMS.ir OTP configuration: %s", exc)
        return False

    if template_id is None:
        logger.error("SMSIR_OTP_TEMPLATE_ID is not configured")
        return False

    parameter_name = (settings.smsir_otp_template_parameter or "CODE").strip() or "CODE"
    return _send_template_sms(
        mobile,
        template_id=template_id,
        parameters=[{"name": parameter_name, "value": str(code)}],
    )


def send_invitation_sms(
    mobile: str,
    account_name: str,
    bot_link: str,
    web_link: str,
) -> bool:
    """Send general invitation SMS through the dedicated SMS.ir template."""
    del bot_link, web_link
    try:
        template_id = _configured_template_id(
            settings.smsir_invitation_template_id,
            setting_name="SMSIR_INVITATION_TEMPLATE_ID",
        )
    except RuntimeError as exc:
        logger.error("Invalid SMS.ir invitation configuration: %s", exc)
        return False

    if template_id is None:
        logger.error("SMSIR_INVITATION_TEMPLATE_ID is not configured")
        return False

    parameter_name = (settings.smsir_invitation_template_parameter or "NAME").strip() or "NAME"
    return _send_template_sms(
        mobile,
        template_id=template_id,
        parameters=[{"name": parameter_name, "value": str(account_name).strip() or "کاربر"}],
    )


def send_accountant_invitation_sms(
    mobile: str,
    relation_display_name: str,
    web_link: str,
) -> bool:
    """Send accountant invitation SMS through the dedicated SMS.ir template."""
    del web_link
    try:
        template_id = _configured_template_id(
            settings.smsir_accountant_invitation_template_id,
            setting_name="SMSIR_ACCOUNTANT_INVITATION_TEMPLATE_ID",
        )
    except RuntimeError as exc:
        logger.error("Invalid SMS.ir accountant invitation configuration: %s", exc)
        return False

    if template_id is None:
        logger.error("SMSIR_ACCOUNTANT_INVITATION_TEMPLATE_ID is not configured")
        return False

    parameter_name = (settings.smsir_invitation_template_parameter or "NAME").strip() or "NAME"
    return _send_template_sms(
        mobile,
        template_id=template_id,
        parameters=[{"name": parameter_name, "value": str(relation_display_name).strip() or "کاربر"}],
    )


def send_customer_invitation_sms(
    mobile: str,
    management_name: str,
    web_link: str,
) -> bool:
    """Send customer invitation SMS through the dedicated SMS.ir template."""
    del web_link
    try:
        template_id = _configured_template_id(
            settings.smsir_customer_invitation_template_id,
            setting_name="SMSIR_CUSTOMER_INVITATION_TEMPLATE_ID",
        )
    except RuntimeError as exc:
        logger.error("Invalid SMS.ir customer invitation configuration: %s", exc)
        return False

    if template_id is None:
        logger.error("SMSIR_CUSTOMER_INVITATION_TEMPLATE_ID is not configured")
        return False

    parameter_name = (settings.smsir_invitation_template_parameter or "NAME").strip() or "NAME"
    return _send_template_sms(
        mobile,
        template_id=template_id,
        parameters=[{"name": parameter_name, "value": str(management_name).strip() or "کاربر"}],
    )
