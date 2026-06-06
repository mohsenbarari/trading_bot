"""Session management API endpoints."""
import uuid
import logging
import json
from inspect import isawaitable
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

from core.db import get_db
from core.config import settings
from core.enums import MessageType
from core.security import create_access_token, create_refresh_token
from core.sms import send_sms
from core.utils import publish_user_event
from api.deps import get_current_user
from api.routers.chat_schemas import MessageRead
from models.user import User, UserRole
from models.session import (
    UserSession,
    SessionLoginRequest,
    LoginRequestStatus,
    Platform,
    SingleSessionRecoveryAdminTarget,
    SingleSessionRecoveryRequest,
)
from core.services.chat_service import persist_sent_direct_message, publish_direct_message_event
from core.services.chat_upload_session_service import persist_chat_media_file_bytes
from core.services.session_service import (
    get_active_sessions,
    get_session_by_refresh_token,
    handle_login_session,
    approve_login_request,
    reject_login_request,
    logout_session,
    force_clear_sessions,
    hash_token,
    get_effective_max_sessions,
    provision_session_for_login_request,
)
from core.services.accountant_relation_service import is_user_accountant
from core.services.single_session_recovery_service import (
    approve_recovery_request,
    build_identity_requested_sms_text,
    build_identity_submitted_sms_text,
    build_identity_submitted_text,
    build_initial_recovery_request_text,
    build_recovery_approved_sms_text,
    build_recovery_expired_sms_text,
    build_recovery_message_action_payload,
    build_recovery_rejected_sms_text,
    cancel_recovery_request,
    create_recovery_request,
    get_recovery_admin_target,
    get_active_recovery_request_for_login_request,
    get_latest_recovery_request_for_login_request,
    expire_recovery_request,
    get_recovery_requester_display_name,
    is_active_recovery_status,
    list_pending_admin_recovery_targets,
    list_recovery_admin_targets,
    list_recovery_admin_users,
    reject_recovery_request,
    request_identity_verification,
    should_show_inline_recovery_prompt,
    submit_identity_material,
)

router = APIRouter()
logger = logging.getLogger(__name__)

ACCOUNTANT_SESSION_MANAGEMENT_DETAIL = "حسابداران به مدیریت نشست و خروج از حساب کاربری دسترسی ندارند."


# --- Schemas ---
class SessionOut(BaseModel):
    id: str
    device_name: str
    device_ip: Optional[str] = None
    platform: str
    home_server: str
    is_primary: bool
    is_active: bool
    created_at: datetime
    last_active_at: datetime

    class Config:
        from_attributes = True

class LoginRequestOut(BaseModel):
    id: str
    requester_device_name: str
    requester_ip: Optional[str] = None
    status: str
    created_at: datetime
    expires_at: datetime

    class Config:
        from_attributes = True

class LoginRequestAction(BaseModel):
    request_id: str

class MaxSessionsUpdate(BaseModel):
    max_sessions: int


def session_to_dict(s: UserSession) -> dict:
    return {
        "id": str(s.id),
        "device_name": s.device_name,
        "device_ip": s.device_ip,
        "platform": s.platform.value if hasattr(s.platform, 'value') else str(s.platform),
        "home_server": s.home_server or "foreign",
        "is_primary": s.is_primary,
        "is_active": s.is_active,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "last_active_at": s.last_active_at.isoformat() if s.last_active_at else None,
    }


async def ensure_non_accountant_session_management(db: AsyncSession, current_user: User) -> None:
    if getattr(current_user, "is_accountant", False) is True:
        raise HTTPException(status_code=403, detail=ACCOUNTANT_SESSION_MANAGEMENT_DETAIL)
    if isinstance(current_user, User) and await is_user_accountant(db, current_user.id):
        raise HTTPException(status_code=403, detail=ACCOUNTANT_SESSION_MANAGEMENT_DETAIL)


def login_request_to_dict(r: SessionLoginRequest) -> dict:
    return {
        "id": str(r.id),
        "requester_device_name": r.requester_device_name,
        "requester_ip": r.requester_ip,
        "status": r.status.value if hasattr(r.status, 'value') else str(r.status),
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "expires_at": r.expires_at.isoformat() if r.expires_at else None,
    }


def recovery_request_to_dict(r: SingleSessionRecoveryRequest) -> dict:
    return {
        "id": str(r.id),
        "status": r.status.value if hasattr(r.status, "value") else str(r.status),
        "requester_device_name": r.requester_device_name,
        "requester_ip": r.requester_ip,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "inline_action_expires_at": r.inline_action_expires_at.isoformat() if r.inline_action_expires_at else None,
        "chat_action_expires_at": r.chat_action_expires_at.isoformat() if r.chat_action_expires_at else None,
        "identity_requested_at": r.identity_requested_at.isoformat() if r.identity_requested_at else None,
        "identity_submitted_at": r.identity_submitted_at.isoformat() if r.identity_submitted_at else None,
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
        "cancelled_at": r.cancelled_at.isoformat() if r.cancelled_at else None,
    }


def _get_recovery_token_cache_key(recovery_id: uuid.UUID | str) -> str:
    return f"single_session_recovery_token:{recovery_id}"


async def _rollback_if_available(db: AsyncSession) -> None:
    rollback = getattr(db, "rollback", None)
    if rollback is None:
        return
    result = rollback()
    if isawaitable(result):
        await result


async def _store_temporary_refresh_token(cache_key: str, refresh_token: str, *, ttl_seconds: int = 300) -> None:
    try:
        from bot.utils.redis_helpers import get_redis

        redis_client = await get_redis()
        await redis_client.setex(cache_key, ttl_seconds, refresh_token)
    except Exception as exc:
        logger.warning("Failed to store temporary refresh token %s: %s", cache_key, exc)


async def _pop_temporary_refresh_token(cache_key: str) -> Optional[str]:
    try:
        from bot.utils.redis_helpers import get_redis

        redis_client = await get_redis()
        refresh_token = await redis_client.get(cache_key)
        if refresh_token:
            await redis_client.delete(cache_key)
        return refresh_token
    except Exception as exc:
        logger.warning("Failed to pop temporary refresh token %s: %s", cache_key, exc)
        return None


def _serialize_admin_recovery_prompt(
    recovery_request: SingleSessionRecoveryRequest,
    requester_user: User,
    admin_target: SingleSessionRecoveryAdminTarget,
) -> dict:
    requester_name = get_recovery_requester_display_name(requester_user)
    prompt_type = "identity_submitted"
    message_text = build_identity_submitted_text(requester_name)
    can_request_identity = False
    if recovery_request.status.value == "pending_admin_review":
        prompt_type = "initial_request"
        message_text = build_initial_recovery_request_text(requester_name)
        can_request_identity = True

    return {
        "recovery_id": str(recovery_request.id),
        "status": recovery_request.status.value if hasattr(recovery_request.status, "value") else str(recovery_request.status),
        "prompt_type": prompt_type,
        "user_id": requester_user.id,
        "user_name": requester_name,
        "requester_device_name": recovery_request.requester_device_name,
        "requester_ip": recovery_request.requester_ip,
        "inline_action_expires_at": recovery_request.inline_action_expires_at.isoformat() if recovery_request.inline_action_expires_at else None,
        "chat_action_expires_at": recovery_request.chat_action_expires_at.isoformat() if recovery_request.chat_action_expires_at else None,
        "current_action_message_id": admin_target.current_action_message_id,
        "can_approve": True,
        "can_reject": True,
        "can_request_identity": can_request_identity,
        "visible": should_show_inline_recovery_prompt(recovery_request, admin_target),
        "message": message_text,
    }


def _build_recovery_action_message_read(
    message,
    recovery_request: SingleSessionRecoveryRequest,
    requester_user: User,
) -> MessageRead:
    payload = MessageRead.from_orm_with_forwarding(message).model_dump()
    payload["recovery_action"] = build_recovery_message_action_payload(
        recovery_request=recovery_request,
        requester_user=requester_user,
        current_action_message_id=message.id,
    )
    return MessageRead(**payload)


async def _publish_plain_direct_message(message, *, sender_name: str) -> None:
    await publish_direct_message_event(
        receiver_id=message.receiver_id,
        message=message,
        serializer=MessageRead.from_orm_with_forwarding,
        publisher=publish_user_event,
        sender_name=sender_name,
    )


async def _publish_recovery_action_message(
    message,
    *,
    recovery_request: SingleSessionRecoveryRequest,
    requester_user: User,
) -> None:
    response_message = _build_recovery_action_message_read(message, recovery_request, requester_user)
    await publish_direct_message_event(
        receiver_id=message.receiver_id,
        message=message,
        serializer=lambda _message: response_message,
        publisher=publish_user_event,
        sender_name=get_recovery_requester_display_name(requester_user),
    )


async def _clear_recovery_admin_action_messages(db: AsyncSession, recovery_request_id: uuid.UUID) -> None:
    admin_targets = await list_recovery_admin_targets(db, recovery_request_id)
    for admin_target in admin_targets:
        admin_target.current_action_message_id = None


async def _publish_recovery_prompt_updates(
    db: AsyncSession,
    recovery_request: SingleSessionRecoveryRequest,
    requester_user: Optional[User] = None,
) -> None:
    requester = requester_user or getattr(recovery_request, "user", None)
    if requester is None:
        stmt_user = select(User).where(User.id == recovery_request.user_id)
        requester = (await db.execute(stmt_user)).scalar_one_or_none()
    if requester is None:
        return

    admin_targets = await list_recovery_admin_targets(db, recovery_request.id)
    for admin_target in admin_targets:
        await publish_user_event(
            admin_target.admin_user_id,
            "session:single_session_recovery",
            _serialize_admin_recovery_prompt(recovery_request, requester, admin_target),
        )


async def _expire_recovery_if_needed(
    db: AsyncSession,
    recovery_request: SingleSessionRecoveryRequest,
    requester_user: Optional[User] = None,
) -> bool:
    if not is_active_recovery_status(recovery_request.status):
        return False
    if recovery_request.chat_action_expires_at.replace(tzinfo=None) >= datetime.utcnow():
        return False

    expire_recovery_request(recovery_request)
    login_req = getattr(recovery_request, "session_login_request", None)
    if login_req is None:
        recovery_login_request_id = getattr(recovery_request, "session_login_request_id", None)
        if recovery_login_request_id is not None:
            stmt_login_req = select(SessionLoginRequest).where(SessionLoginRequest.id == recovery_login_request_id)
            login_req = (await db.execute(stmt_login_req)).scalar_one_or_none()
    if login_req is not None and login_req.status == LoginRequestStatus.PENDING:
        login_req.status = LoginRequestStatus.EXPIRED

    await _clear_recovery_admin_action_messages(db, recovery_request.id)
    await db.commit()

    requester = requester_user or getattr(recovery_request, "user", None)
    if requester is None:
        stmt_user = select(User).where(User.id == recovery_request.user_id)
        requester = (await db.execute(stmt_user)).scalar_one_or_none()
    requester_mobile_number = getattr(requester, "mobile_number", None)
    if requester and requester_mobile_number:
        try:
            send_sms(requester_mobile_number, build_recovery_expired_sms_text())
        except Exception as exc:
            logger.warning("Failed to send recovery-expired SMS for user %s: %s", requester.id, exc)

    await _publish_recovery_prompt_updates(db, recovery_request, requester)
    return True


async def _deliver_initial_recovery_messages(
    db: AsyncSession,
    recovery_request: SingleSessionRecoveryRequest,
    requester_user: User,
    admin_users: List[User],
) -> None:
    for admin_user in admin_users:
        db.add(
            SingleSessionRecoveryAdminTarget(
                recovery_request_id=recovery_request.id,
                admin_user_id=admin_user.id,
            )
        )
    await db.commit()

    delivery_text = build_initial_recovery_request_text(get_recovery_requester_display_name(requester_user))
    for admin_user in admin_users:
        try:
            admin_target = await get_recovery_admin_target(
                db,
                recovery_id=recovery_request.id,
                admin_user_id=admin_user.id,
            )
            if admin_target is None:
                continue
            message = await persist_sent_direct_message(
                db,
                sender=requester_user,
                receiver=admin_user,
                content=delivery_text,
                message_type=MessageType.TEXT,
            )
            admin_target.current_action_message_id = message.id
            await db.commit()
            await _publish_recovery_action_message(
                message,
                recovery_request=recovery_request,
                requester_user=requester_user,
            )
        except Exception as exc:
            logger.warning(
                "Failed to deliver initial recovery message recovery=%s admin=%s: %s",
                recovery_request.id,
                admin_user.id,
                exc,
            )
            await _rollback_if_available(db)

    await _publish_recovery_prompt_updates(db, recovery_request, requester_user)


async def _build_identity_message_payload(
    *,
    file_name: str,
    declared_content_type: str,
    contents: bytes,
    uploader_id: int,
    caption: str | None,
    db: AsyncSession,
) -> tuple[MessageType, str, str | None]:
    file_result = await persist_chat_media_file_bytes(
        db,
        uploader_id=uploader_id,
        file_name=file_name,
        declared_content_type=declared_content_type,
        contents=contents,
    )

    normalized_caption = caption.strip() if isinstance(caption, str) and caption.strip() else None
    chat_file = file_result.chat_file
    if file_result.mime_type.startswith("image/"):
        payload = {
            "file_id": str(chat_file.id),
            "file_name": chat_file.file_name,
            "mime_type": file_result.mime_type,
            "thumbnail": chat_file.thumbnail,
        }
        if file_result.width:
            payload["width"] = file_result.width
        if file_result.height:
            payload["height"] = file_result.height
        if normalized_caption:
            payload["caption"] = normalized_caption
        return MessageType.IMAGE, json.dumps(payload, ensure_ascii=False), normalized_caption

    payload = {
        "file_id": str(chat_file.id),
        "file_name": chat_file.file_name,
        "mime_type": file_result.mime_type,
        "size": file_result.size,
    }
    return MessageType.DOCUMENT, json.dumps(payload, ensure_ascii=False), normalized_caption


async def _deliver_identity_submission_messages(
    db: AsyncSession,
    *,
    recovery_request: SingleSessionRecoveryRequest,
    requester_user: User,
    message_type: MessageType,
    message_content: str,
    caption_text: str | None,
) -> None:
    admin_targets = await list_recovery_admin_targets(db, recovery_request.id)
    sender_name = get_recovery_requester_display_name(requester_user)

    for admin_target in admin_targets:
        try:
            stmt_admin = select(User).where(User.id == admin_target.admin_user_id)
            admin_user = (await db.execute(stmt_admin)).scalar_one_or_none()
            if admin_user is None:
                continue

            if caption_text and message_type == MessageType.DOCUMENT:
                text_message = await persist_sent_direct_message(
                    db,
                    sender=requester_user,
                    receiver=admin_user,
                    content=caption_text,
                    message_type=MessageType.TEXT,
                )
                await _publish_plain_direct_message(text_message, sender_name=sender_name)

            action_message = await persist_sent_direct_message(
                db,
                sender=requester_user,
                receiver=admin_user,
                content=message_content,
                message_type=message_type,
            )
            admin_target.current_action_message_id = action_message.id
            await db.commit()
            await _publish_recovery_action_message(
                action_message,
                recovery_request=recovery_request,
                requester_user=requester_user,
            )
        except Exception as exc:
            logger.warning(
                "Failed to deliver recovery identity submission recovery=%s admin=%s: %s",
                recovery_request.id,
                admin_target.admin_user_id,
                exc,
            )
            await _rollback_if_available(db)

    await _publish_recovery_prompt_updates(db, recovery_request, requester_user)


async def _ensure_recovery_admin_access(
    db: AsyncSession,
    *,
    recovery_id: uuid.UUID,
    current_user: User,
) -> tuple[SingleSessionRecoveryAdminTarget, SingleSessionRecoveryRequest, User]:
    if current_user.role not in (UserRole.SUPER_ADMIN, UserRole.MIDDLE_MANAGER):
        raise HTTPException(status_code=403, detail="این عملیات فقط برای مدیران مجاز است")

    admin_target = await get_recovery_admin_target(
        db,
        recovery_id=recovery_id,
        admin_user_id=current_user.id,
    )
    if admin_target is None or admin_target.recovery_request is None:
        raise HTTPException(status_code=404, detail="درخواست بازیابی برای شما یافت نشد")

    recovery_request = admin_target.recovery_request
    requester_user = recovery_request.user
    if requester_user is None:
        stmt_user = select(User).where(User.id == recovery_request.user_id)
        requester_user = (await db.execute(stmt_user)).scalar_one_or_none()
    if requester_user is None:
        raise HTTPException(status_code=404, detail="کاربر درخواست‌دهنده یافت نشد")

    if await _expire_recovery_if_needed(db, recovery_request, requester_user):
        raise HTTPException(status_code=400, detail="مهلت این درخواست به پایان رسیده است")

    return admin_target, recovery_request, requester_user


# --- Endpoints ---

@router.get("/login-requests/pending", response_model=List[dict])
async def get_pending_requests(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    لیست درخواست‌های ورود در انتظار تایید (فقط دستگاه اصلی)
    """
    await ensure_non_accountant_session_management(db, current_user)
    current_session_id = None
    try:
        from jose import jwt as jose_jwt
        from core.config import settings
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            payload = jose_jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
            current_session_id = payload.get("sid")
    except Exception:
        pass
    
    if current_session_id:
        # Check if the current session is primary
        stmt_sess = select(UserSession).where(
            and_(
                UserSession.id == uuid.UUID(current_session_id),
                UserSession.is_active == True,
            )
        )
        session = (await db.execute(stmt_sess)).scalar_one_or_none()
        if not session or not session.is_primary:
            return [] # Empty list for non-primary devices

    stmt = select(SessionLoginRequest).where(
        and_(
            SessionLoginRequest.user_id == current_user.id,
            SessionLoginRequest.status == LoginRequestStatus.PENDING,
            SessionLoginRequest.expires_at > datetime.utcnow(),
        )
    ).order_by(SessionLoginRequest.created_at.desc())
    requests = (await db.execute(stmt)).scalars().all()
    
    res = []
    for r in requests:
        res.append({
            "request_id": str(r.id),
            "device_name": r.requester_device_name,
            "device_ip": r.requester_ip,
            "expires_at": r.expires_at.isoformat() + "Z" if r.expires_at else None,
        })
    return res


@router.post("/login-requests/{request_id}/recovery")
async def start_single_session_recovery(
    request_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Start the web-only single-session recovery flow for a pending login request."""
    try:
        rid = uuid.UUID(request_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="شناسه درخواست نامعتبر است")

    stmt = select(SessionLoginRequest).where(SessionLoginRequest.id == rid)
    login_req = (await db.execute(stmt)).scalar_one_or_none()
    if not login_req:
        raise HTTPException(status_code=404, detail="درخواست یافت نشد")

    latest_recovery = await get_latest_recovery_request_for_login_request(db, rid)
    if latest_recovery is not None and is_active_recovery_status(latest_recovery.status):
        return recovery_request_to_dict(latest_recovery)

    if login_req.status != LoginRequestStatus.PENDING:
        raise HTTPException(status_code=400, detail="برای این درخواست دیگر امکان شروع بازیابی وجود ندارد")
    if login_req.expires_at.replace(tzinfo=None) < datetime.utcnow():
        raise HTTPException(status_code=400, detail="مهلت درخواست اولیه تمام شده است")

    stmt_user = select(User).where(User.id == login_req.user_id)
    user = (await db.execute(stmt_user)).scalar_one_or_none()
    if not user or user.is_deleted:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")
    if user.role in (UserRole.SUPER_ADMIN, UserRole.MIDDLE_MANAGER):
        raise HTTPException(status_code=403, detail="مسیر بازیابی نشست برای مدیران از این endpoint فعال نشده است")
    if get_effective_max_sessions(user) != 1:
        raise HTTPException(status_code=400, detail="این مسیر فقط برای کاربران تک‌نشستی فعال است")

    admin_users = await list_recovery_admin_users(db)
    if not admin_users:
        raise HTTPException(status_code=503, detail="در حال حاضر مدیری برای بررسی درخواست در دسترس نیست")

    recovery_request = await create_recovery_request(db, login_req)
    await db.commit()
    await _deliver_initial_recovery_messages(db, recovery_request, user, admin_users)
    return recovery_request_to_dict(recovery_request)


@router.post("/login-requests/{request_id}/recovery/cancel")
async def cancel_single_session_recovery(
    request_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Cancel an active single-session recovery flow tied to a login request."""
    try:
        rid = uuid.UUID(request_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="شناسه درخواست نامعتبر است")

    recovery_request = await get_active_recovery_request_for_login_request(db, rid)
    if recovery_request is None:
        raise HTTPException(status_code=404, detail="درخواست بازیابی فعالی یافت نشد")

    cancel_recovery_request(recovery_request)
    login_req = getattr(recovery_request, "session_login_request", None)
    recovery_login_request_id = getattr(recovery_request, "session_login_request_id", None)
    if login_req is None and recovery_login_request_id is not None:
        stmt_login_req = select(SessionLoginRequest).where(SessionLoginRequest.id == recovery_login_request_id)
        login_req = (await db.execute(stmt_login_req)).scalar_one_or_none()
    if login_req is not None and login_req.status == LoginRequestStatus.PENDING:
        login_req.status = LoginRequestStatus.REJECTED
    await _clear_recovery_admin_action_messages(db, recovery_request.id)
    await db.commit()
    await _publish_recovery_prompt_updates(db, recovery_request)
    return {
        "detail": "درخواست بازیابی لغو شد",
        "recovery": recovery_request_to_dict(recovery_request),
    }


@router.get("/login-requests/{request_id}/recovery/status")
async def get_single_session_recovery_status(
    request_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Poll the latest single-session recovery status for a login request."""
    try:
        rid = uuid.UUID(request_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="شناسه درخواست نامعتبر است")

    recovery_request = await get_latest_recovery_request_for_login_request(db, rid)
    if recovery_request is None:
        return {"status": "not_started"}

    await _expire_recovery_if_needed(db, recovery_request)
    response = recovery_request_to_dict(recovery_request)
    if recovery_request.status.value == "approved":
        stmt_session = select(UserSession).where(
            and_(
                UserSession.user_id == recovery_request.user_id,
                UserSession.is_active == True,
            )
        ).order_by(UserSession.created_at.desc()).limit(1)
        new_session = (await db.execute(stmt_session)).scalar_one_or_none()
        if new_session:
            response["access_token"] = create_access_token(
                subject=recovery_request.user_id,
                session_id=str(new_session.id),
                server_id=new_session.home_server or "foreign",
            )
            refresh_token = await _pop_temporary_refresh_token(_get_recovery_token_cache_key(recovery_request.id))
            if refresh_token:
                response["refresh_token"] = refresh_token
                response["token_type"] = "bearer"

    return response


@router.get("/recovery/pending", response_model=List[dict])
async def get_pending_single_session_recovery_prompts(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await ensure_non_accountant_session_management(db, current_user)
    if current_user.role not in (UserRole.SUPER_ADMIN, UserRole.MIDDLE_MANAGER):
        return []

    pending_rows = await list_pending_admin_recovery_targets(
        db,
        admin_user_id=current_user.id,
    )
    return [
        _serialize_admin_recovery_prompt(recovery_request, requester_user, admin_target)
        for admin_target, recovery_request, requester_user in pending_rows
    ]


@router.post("/recovery/{recovery_id}/request-identity")
async def request_single_session_recovery_identity(
    recovery_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await ensure_non_accountant_session_management(db, current_user)
    try:
        recovery_uuid = uuid.UUID(recovery_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="شناسه بازیابی نامعتبر است")

    _admin_target, recovery_request, requester_user = await _ensure_recovery_admin_access(
        db,
        recovery_id=recovery_uuid,
        current_user=current_user,
    )
    if recovery_request.status.value != "pending_admin_review":
        raise HTTPException(status_code=400, detail="در وضعیت فعلی امکان درخواست مدرک وجود ندارد")

    request_identity_verification(recovery_request)
    await _clear_recovery_admin_action_messages(db, recovery_request.id)
    await db.commit()

    requester_mobile_number = getattr(requester_user, "mobile_number", None)
    if requester_mobile_number:
        try:
            send_sms(requester_mobile_number, build_identity_requested_sms_text())
        except Exception as exc:
            logger.warning("Failed to send recovery identity-request SMS user=%s: %s", requester_user.id, exc)

    await _publish_recovery_prompt_updates(db, recovery_request, requester_user)
    return {
        "detail": "درخواست ارسال مدرک برای کاربر ثبت شد",
        "recovery": recovery_request_to_dict(recovery_request),
    }


@router.post("/recovery/{recovery_id}/approve")
async def approve_single_session_recovery(
    recovery_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await ensure_non_accountant_session_management(db, current_user)
    try:
        recovery_uuid = uuid.UUID(recovery_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="شناسه بازیابی نامعتبر است")

    _admin_target, recovery_request, requester_user = await _ensure_recovery_admin_access(
        db,
        recovery_id=recovery_uuid,
        current_user=current_user,
    )
    if recovery_request.status.value not in {"pending_admin_review", "identity_submitted"}:
        raise HTTPException(status_code=400, detail="در وضعیت فعلی امکان تایید این درخواست وجود ندارد")

    login_req = recovery_request.session_login_request
    if login_req is None:
        stmt_login_req = select(SessionLoginRequest).where(SessionLoginRequest.id == recovery_request.session_login_request_id)
        login_req = (await db.execute(stmt_login_req)).scalar_one_or_none()
    if login_req is None:
        raise HTTPException(status_code=404, detail="درخواست ورود اولیه یافت نشد")

    refresh_token = create_refresh_token(subject=recovery_request.user_id)
    approved_after_identity_review = recovery_request.status.value == "identity_submitted"
    approve_recovery_request(recovery_request, decided_by_user_id=current_user.id)
    login_req.status = LoginRequestStatus.APPROVED
    login_req.resolved_by_session_id = None
    await _clear_recovery_admin_action_messages(db, recovery_request.id)
    new_session = await provision_session_for_login_request(
        db,
        login_request=login_req,
        refresh_token=refresh_token,
        platform=Platform.WEB,
        home_server=login_req.requester_home_server,
    )
    await db.commit()
    await _store_temporary_refresh_token(_get_recovery_token_cache_key(recovery_request.id), refresh_token)

    requester_mobile_number = getattr(requester_user, "mobile_number", None)
    if requester_mobile_number:
        try:
            send_sms(
                requester_mobile_number,
                build_recovery_approved_sms_text(after_identity_review=approved_after_identity_review),
            )
        except Exception as exc:
            logger.warning("Failed to send recovery-approved SMS user=%s: %s", requester_user.id, exc)

    await _publish_recovery_prompt_updates(db, recovery_request, requester_user)
    return {
        "detail": "درخواست بازیابی تایید شد",
        "session": session_to_dict(new_session),
        "recovery": recovery_request_to_dict(recovery_request),
    }


@router.post("/recovery/{recovery_id}/reject")
async def reject_single_session_recovery(
    recovery_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await ensure_non_accountant_session_management(db, current_user)
    try:
        recovery_uuid = uuid.UUID(recovery_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="شناسه بازیابی نامعتبر است")

    _admin_target, recovery_request, requester_user = await _ensure_recovery_admin_access(
        db,
        recovery_id=recovery_uuid,
        current_user=current_user,
    )
    if recovery_request.status.value not in {"pending_admin_review", "identity_submitted"}:
        raise HTTPException(status_code=400, detail="در وضعیت فعلی امکان رد این درخواست وجود ندارد")

    rejected_after_identity_review = recovery_request.status.value == "identity_submitted"
    reject_recovery_request(recovery_request, decided_by_user_id=current_user.id)
    login_req = recovery_request.session_login_request
    if login_req is None:
        stmt_login_req = select(SessionLoginRequest).where(SessionLoginRequest.id == recovery_request.session_login_request_id)
        login_req = (await db.execute(stmt_login_req)).scalar_one_or_none()
    if login_req is not None and login_req.status == LoginRequestStatus.PENDING:
        login_req.status = LoginRequestStatus.REJECTED
    await _clear_recovery_admin_action_messages(db, recovery_request.id)
    await db.commit()

    requester_mobile_number = getattr(requester_user, "mobile_number", None)
    if requester_mobile_number:
        try:
            send_sms(
                requester_mobile_number,
                build_recovery_rejected_sms_text(after_identity_review=rejected_after_identity_review),
            )
        except Exception as exc:
            logger.warning("Failed to send recovery-rejected SMS user=%s: %s", requester_user.id, exc)

    await _publish_recovery_prompt_updates(db, recovery_request, requester_user)
    return {
        "detail": "درخواست بازیابی رد شد",
        "recovery": recovery_request_to_dict(recovery_request),
    }


@router.post("/login-requests/{request_id}/recovery/identity")
async def submit_single_session_recovery_identity(
    request_id: str,
    file: UploadFile = File(...),
    caption: Optional[str] = Form(default=None),
    db: AsyncSession = Depends(get_db),
):
    try:
        rid = uuid.UUID(request_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="شناسه درخواست نامعتبر است")

    recovery_request = await get_active_recovery_request_for_login_request(db, rid)
    if recovery_request is None:
        raise HTTPException(status_code=404, detail="درخواست بازیابی فعالی یافت نشد")
    if recovery_request.status.value != "identity_verification_requested":
        raise HTTPException(status_code=400, detail="در وضعیت فعلی امکان ارسال مدرک وجود ندارد")

    stmt_user = select(User).where(User.id == recovery_request.user_id)
    requester_user = (await db.execute(stmt_user)).scalar_one_or_none()
    if requester_user is None or requester_user.is_deleted:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")
    if await _expire_recovery_if_needed(db, recovery_request, requester_user):
        raise HTTPException(status_code=400, detail="مهلت این درخواست به پایان رسیده است")

    declared_content_type = (file.content_type or "application/octet-stream").split(";")[0].strip().lower()
    if not (
        declared_content_type.startswith("image/")
        or declared_content_type in {
            "application/pdf",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/octet-stream",
        }
    ):
        raise HTTPException(status_code=400, detail="فقط تصویر یا فایل مدرک قابل ارسال است")

    contents = await file.read()
    try:
        message_type, message_content, caption_text = await _build_identity_message_payload(
            file_name=file.filename or "identity-proof",
            declared_content_type=declared_content_type,
            contents=contents,
            uploader_id=requester_user.id,
            caption=caption,
            db=db,
        )
        submit_identity_material(recovery_request)
        await _clear_recovery_admin_action_messages(db, recovery_request.id)
        await db.commit()
        await _deliver_identity_submission_messages(
            db,
            recovery_request=recovery_request,
            requester_user=requester_user,
            message_type=message_type,
            message_content=message_content,
            caption_text=caption_text if message_type == MessageType.DOCUMENT else None,
        )
    finally:
        await file.close()

    requester_mobile_number = getattr(requester_user, "mobile_number", None)
    if requester_mobile_number:
        try:
            send_sms(requester_mobile_number, build_identity_submitted_sms_text())
        except Exception as exc:
            logger.warning("Failed to send recovery identity-submitted SMS user=%s: %s", requester_user.id, exc)

    return {
        "detail": "مدرک برای بررسی ارسال شد",
        "recovery": recovery_request_to_dict(recovery_request),
    }

class VerifySessionRequest(BaseModel):
    refresh_token: str

@router.post("/verify")
async def verify_my_session(
    req: VerifySessionRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    بررسی اینکه آیا نشست برای یک refresh_token خاص هنوز فعال است یا خیر.
    (استفاده در فرانت‌اند هنگام دریافت نوتیفیکیشن لغو نشست)
    """
    from core.services.session_service import get_session_by_refresh_token
    session = await get_session_by_refresh_token(db, req.refresh_token)
    if not session:
        raise HTTPException(status_code=401, detail="نشست شما باطل شده است")
    return {"status": "active"}

@router.get("/active", response_model=List[dict])
async def list_active_sessions(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """لیست نشست‌های فعال کاربر جاری"""
    await ensure_non_accountant_session_management(db, current_user)
    # Extract session_id from JWT to mark current session
    current_session_id = None
    try:
        from jose import jwt as jose_jwt
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            payload = jose_jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
            current_session_id = payload.get("sid")
    except Exception:
        pass
    
    sessions = await get_active_sessions(db, current_user.id)
    result = []
    for s in sessions:
        d = session_to_dict(s)
        d["is_current"] = (str(s.id) == current_session_id) if current_session_id else False
        result.append(d)
    return result


@router.delete("/{session_id}")
async def terminate_session(
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    پایان دادن به یک نشست.
    - دستگاه غیراصلی: فقط نشست خودش
    - نشست اصلی: می‌تواند هر نشستی را پایان دهد اما خودش را اگر نشست‌های دیگر باشند نمی‌تواند پایان دهد
    """
    await ensure_non_accountant_session_management(db, current_user)
    current_session_id_str = None
    try:
        from jose import jwt as jose_jwt
        from core.config import settings
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            payload = jose_jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
            current_session_id_str = payload.get("sid")
    except Exception:
        pass

    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="شناسه نشست نامعتبر است")

    stmt = select(UserSession).where(
        and_(UserSession.id == sid, UserSession.user_id == current_user.id, UserSession.is_active == True)
    )
    session = (await db.execute(stmt)).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="نشست یافت نشد")

    # Access control: If terminating another session, caller must be primary
    is_trying_to_delete_other = current_session_id_str is None or str(sid) != current_session_id_str
    if is_trying_to_delete_other:
        if not current_session_id_str:
            raise HTTPException(status_code=403, detail="شناسه نشست شما مشخص نیست")
        caller_stmt = select(UserSession).where(UserSession.id == uuid.UUID(current_session_id_str))
        caller_session = (await db.execute(caller_stmt)).scalar_one_or_none()
        if not caller_session or not caller_session.is_primary:
            raise HTTPException(
                status_code=403, 
                detail="شما دسترسی برای حذف نشست دستگاه‌های دیگر را ندارید"
            )

    # Don't allow terminating primary if other sessions exist
    if session.is_primary:
        active_sessions = await get_active_sessions(db, current_user.id)
        if len(active_sessions) > 1:
            raise HTTPException(
                status_code=400,
                detail="نشست اصلی را نمی‌توان حذف کرد. ابتدا نشست‌های دیگر را حذف کنید."
            )

    await logout_session(db, session)
    return {"detail": "نشست با موفقیت پایان یافت"}


@router.post("/logout-all")
async def logout_all_sessions(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """خروج از تمام نشست‌ها به جز نشست جاری دستگاه (فقط برای دستگاه اصلی)"""
    await ensure_non_accountant_session_management(db, current_user)
    current_session_id_str = None
    try:
        from jose import jwt as jose_jwt
        from core.config import settings
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            payload = jose_jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
            current_session_id_str = payload.get("sid")
    except Exception:
        pass

    if not current_session_id_str:
        raise HTTPException(status_code=403, detail="شناسه نشست شما مشخص نیست")

    caller_sid = uuid.UUID(current_session_id_str)
    caller_stmt = select(UserSession).where(UserSession.id == caller_sid)
    caller_session = (await db.execute(caller_stmt)).scalar_one_or_none()
    
    if not caller_session or not caller_session.is_primary:
        raise HTTPException(
            status_code=403, 
            detail="شما اجازه خروج از سایر نشست‌ها را ندارید. (فقط دستگاه اصلی مجاز است)"
        )

    count = await force_clear_sessions(db, current_user.id, exclude_session_id=caller_sid)
    return {"detail": f"{count} نشست پایان یافت"}


@router.get("/login-requests/pending", response_model=List[dict])
async def get_pending_login_requests(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """دریافت درخواست‌های ورود در انتظار تایید"""
    await ensure_non_accountant_session_management(db, current_user)
    stmt = select(SessionLoginRequest).where(
        and_(
            SessionLoginRequest.user_id == current_user.id,
            SessionLoginRequest.status == LoginRequestStatus.PENDING,
            SessionLoginRequest.expires_at > datetime.utcnow(),
        )
    )
    result = await db.execute(stmt)
    requests = result.scalars().all()
    return [login_request_to_dict(r) for r in requests]


@router.post("/login-requests/{request_id}/approve")
async def approve_request(
    request_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    تایید درخواست ورود از دستگاه جدید.
    فقط از نشست primary مجاز است.
    """
    await ensure_non_accountant_session_management(db, current_user)
    try:
        rid = uuid.UUID(request_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="شناسه درخواست نامعتبر است")

    # Find caller's primary session
    stmt = select(UserSession).where(
        and_(
            UserSession.user_id == current_user.id,
            UserSession.is_primary == True,
            UserSession.is_active == True,
        )
    )
    primary_session = (await db.execute(stmt)).scalar_one_or_none()
    if not primary_session:
        raise HTTPException(status_code=403, detail="فقط از نشست اصلی مجاز به تایید هستید")

    # Generate refresh token for new session
    stmt_req = select(SessionLoginRequest).where(SessionLoginRequest.id == rid)
    login_req = (await db.execute(stmt_req)).scalar_one_or_none()
    if not login_req:
        raise HTTPException(status_code=404, detail="درخواست یافت نشد")

    new_refresh = create_refresh_token(subject=current_user.id)

    result = await approve_login_request(
        db, rid, primary_session, new_refresh,
        device_ip=request.client.host if request.client else None,
        home_server=login_req.requester_home_server,
    )

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    await _store_temporary_refresh_token(f"login_req_token:{request_id}", new_refresh)

    return {"detail": "درخواست ورود تایید شد", "session": session_to_dict(result["session"])}


@router.post("/login-requests/{request_id}/reject")
async def reject_request(
    request_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """رد درخواست ورود"""
    await ensure_non_accountant_session_management(db, current_user)
    try:
        rid = uuid.UUID(request_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="شناسه درخواست نامعتبر است")

    # Find caller's primary session
    stmt = select(UserSession).where(
        and_(
            UserSession.user_id == current_user.id,
            UserSession.is_primary == True,
            UserSession.is_active == True,
        )
    )
    primary_session = (await db.execute(stmt)).scalar_one_or_none()
    if not primary_session:
        raise HTTPException(status_code=403, detail="فقط از نشست اصلی مجاز به رد هستید")

    result = await reject_login_request(db, rid, primary_session)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    return {"detail": "درخواست ورود رد شد"}


@router.get("/login-requests/{request_id}/status")
async def poll_login_request_status(
    request_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Polling endpoint for new device waiting for approval.
    No auth required — uses request ID as temporary token.
    """
    try:
        rid = uuid.UUID(request_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="شناسه نامعتبر است")

    stmt = select(SessionLoginRequest).where(SessionLoginRequest.id == rid)
    login_req = (await db.execute(stmt)).scalar_one_or_none()

    if not login_req:
        raise HTTPException(status_code=404, detail="درخواست یافت نشد")

    status = login_req.status.value if hasattr(login_req.status, 'value') else str(login_req.status)
    
    response = {"status": status}
    
    # If approved, include the session tokens
    if login_req.status == LoginRequestStatus.APPROVED:
        # Find the new session by user + newest
        stmt2 = select(UserSession).where(
            and_(
                UserSession.user_id == login_req.user_id,
                UserSession.is_active == True,
            )
        ).order_by(UserSession.created_at.desc()).limit(1)
        new_session = (await db.execute(stmt2)).scalar_one_or_none()
        if new_session:
            response["access_token"] = create_access_token(
                subject=login_req.user_id,
                session_id=str(new_session.id),
                server_id=new_session.home_server or "foreign",
            )
            actual_refresh = await _pop_temporary_refresh_token(f"login_req_token:{request_id}")
            if actual_refresh:
                response["refresh_token"] = actual_refresh
            response["token_type"] = "bearer"
    elif login_req.expires_at.replace(tzinfo=None) < datetime.utcnow():
        response["status"] = "expired"

    return response
