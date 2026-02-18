# core/sms.py
"""
SMS service using SMS.ir for OTP delivery and invitation notifications.
"""
import logging
from sms_ir import SmsIr
from core.config import settings

logger = logging.getLogger(__name__)

_sms_client: SmsIr | None = None


def _get_client() -> SmsIr:
    global _sms_client
    if _sms_client is None:
        if not settings.smsir_api_key:
            raise RuntimeError("SMSIR_API_KEY is not configured")
        _sms_client = SmsIr(
            api_key=settings.smsir_api_key,
            linenumber=settings.smsir_line_number,
        )
    return _sms_client


def send_sms(mobile: str, message: str) -> bool:
    """
    Send a plain text SMS to a mobile number.
    Returns True on success, False on failure.
    """
    try:
        client = _get_client()
        resp = client.send_sms(number=mobile, message=message)

        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == 1:
                logger.info(f"✅ SMS sent to {mobile}")
                return True
            else:
                logger.error(f"❌ SMS.ir error: {data}")
                return False
        else:
            logger.error(f"❌ SMS HTTP {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        logger.error(f"❌ SMS send failed: {e}")
        return False


def send_otp_sms(mobile: str, code: str) -> bool:
    """Send OTP code via SMS."""
    message = (
        f"کد تایید شما: {code}\n"
        f"این کد تا ۲ دقیقه معتبر است.\n"
        f"\n"
        f"@coin.gold-trade.ir #{code}"
    )
    return send_sms(mobile, message)


def send_invitation_sms(
    mobile: str,
    account_name: str,
    bot_link: str,
    web_link: str,
) -> bool:
    """
    Send invitation SMS with both Telegram bot and web registration links.
    Text encourages Telegram registration as the primary method.
    """
    message = (
        f"سلام {account_name} عزیز!\n"
        f"شما به سامانه معاملاتی دعوت شدید.\n"
        f"\n"
        f"ثبت‌نام از طریق تلگرام (توصیه شده):\n"
        f"{bot_link}\n"
        f"\n"
        f"ثبت‌نام از طریق وب:\n"
        f"{web_link}"
    )
    return send_sms(mobile, message)
