from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from datetime import datetime

from core.db import get_db
from core.audit_logger import audit_log
from core.utils import utc_now_naive
from core.services.accountant_relation_service import (
    get_pending_accountant_relation_by_invitation_token,
    is_accountant_invitation_token,
)
from core.services.customer_relation_service import (
    get_pending_customer_relation_by_invitation_token,
    is_customer_invitation_token,
)
from models.invitation import Invitation, InvitationKind
from models.user import User, UserRole
from core.config import settings
from api.deps import verify_admin_user
from core.sms import send_invitation_sms
from core.invitation_contract_service import build_invitation_contract_v2
from core.public_webapp_url import public_webapp_url_for_links
from core.invitation_creation_contracts import (
    InternalInvitationCreateRequest,
    build_standard_invitation_idempotency_key,
)
from core.invitation_sms_policy import invitation_sms_enabled, invitation_sms_status
from core.registration_contracts import InvitationDerivedState, InvitationSMSStatus
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, current_server, normalize_server
from core.services.canonical_invitation_creation_service import (
    CanonicalInvitationCreationError,
    create_or_reuse_canonical_invitation,
    generate_invitation_short_code,
    generate_invitation_token,
)
from core.trade_forwarding import verify_internal_signature
from core.services.invitation_identity_reservation_service import release_invitation_identity
from core.services.invitation_lifecycle_service import (
    get_new_invitation_expiry,
    soft_revoke_invitation,
)
from core.services.invitation_transition_lock_service import (
    lock_invitation_for_transition,
)
from core.log_redaction import mask_mobile
from core.services.invitation_public_access_service import (
    enforce_public_invitation_access,
    public_invitation_http_exception,
)

router = APIRouter()

class InvitationCreate(BaseModel):
    account_name: str
    mobile_number: str
    role: UserRole = UserRole.WATCH

class InvitationResponse(BaseModel):
    token: str
    link: str | None
    short_link: str | None
    bot_link: str | None
    web_link: str
    web_short_link: str | None
    bot_available: bool
    web_available: bool
    state: InvitationDerivedState
    kind: str
    sms_status: InvitationSMSStatus
    expires_at: datetime
    created: bool


class PendingInvitationResponse(BaseModel):
    id: int
    account_name: str
    mobile_number: str
    role: UserRole
    token: str
    short_code: str | None
    bot_link: str | None
    web_link: str
    short_link: str | None
    expires_at: datetime
    created_at: datetime | None
    created_by_id: int | None

def generate_token():
    return generate_invitation_token(InvitationKind.STANDARD)


def generate_short_code():
    return generate_invitation_short_code()


def build_invitation_links(invitation: Invitation) -> tuple[str, str, str | None]:
    contract = build_invitation_contract_v2(
        invitation,
        bot_username=settings.bot_username,
        sms_status=InvitationSMSStatus.DISABLED,
    )
    return contract.bot_link or "", contract.web_link, contract.web_short_link


def _creation_error_http_exception(exc: CanonicalInvitationCreationError) -> HTTPException:
    if exc.code == "iran_authority_required":
        return HTTPException(status_code=403, detail="Invitation creation is Iran-authoritative")
    if exc.code == "user_identity_exists":
        return HTTPException(
            status_code=400,
            detail="کاربری با این نام کاربری یا موبایل قبلاً ثبت شده است",
        )
    if exc.code == "invalid_identity":
        return HTTPException(status_code=400, detail="شماره موبایل یا نام کاربری نامعتبر است")
    if exc.code in {
        "identity_reserved",
        "mobile_reserved",
        "account_name_reserved",
        "identity_split_reserved",
        "invitation_identity_conflict",
    }:
        return HTTPException(
            status_code=409,
            detail="دعوت‌نامه فعال دیگری با این نام کاربری یا موبایل وجود دارد",
        )
    return HTTPException(status_code=409, detail="ساخت دعوت‌نامه با وضعیت فعلی ممکن نیست")


def _validate_standard_inviter(admin: User, role: UserRole) -> None:
    if (
        getattr(admin, "role", UserRole.SUPER_ADMIN) == UserRole.MIDDLE_MANAGER
        and role not in (UserRole.WATCH, UserRole.STANDARD)
    ):
        raise HTTPException(
            status_code=403,
            detail="مدیر میانی فقط می‌تواند کاربران عادی یا تماشا را دعوت کند",
        )


async def _create_standard_invitation(
    *,
    invite: InvitationCreate,
    db: AsyncSession,
    admin: User,
) -> dict:
    public_webapp_url_for_links()
    _validate_standard_inviter(admin, invite.role)
    try:
        creation = await create_or_reuse_canonical_invitation(
            db,
            creator_user_id=admin.id,
            account_name=invite.account_name,
            mobile_number=invite.mobile_number,
            role=invite.role,
            kind=InvitationKind.STANDARD,
        )
    except CanonicalInvitationCreationError as exc:
        await db.rollback()
        raise _creation_error_http_exception(exc) from exc

    await db.commit()
    if creation.created:
        await db.refresh(creation.invitation)

    sms_enabled = invitation_sms_enabled(InvitationKind.STANDARD)
    sms_accepted: bool | None = None
    if creation.created and sms_enabled:
        pre_send_contract = build_invitation_contract_v2(
            creation.invitation,
            bot_username=settings.bot_username,
            sms_status=InvitationSMSStatus.DISABLED,
        )
        sms_accepted = bool(
            send_invitation_sms(
                mobile=creation.invitation.mobile_number,
                account_name=creation.invitation.account_name,
                bot_link=pre_send_contract.bot_link or "",
                web_link=pre_send_contract.web_link,
            )
        )

    contract = build_invitation_contract_v2(
        creation.invitation,
        bot_username=settings.bot_username,
        sms_status=invitation_sms_status(
            enabled=bool(creation.created and sms_enabled),
            accepted=sms_accepted,
        ),
    )
    return {**contract.model_dump(), "created": creation.created}


def serialize_pending_invitation(invitation: Invitation) -> dict:
    bot_link, web_link, short_link = build_invitation_links(invitation)
    return {
        "id": invitation.id,
        "account_name": invitation.account_name,
        "mobile_number": invitation.mobile_number,
        "role": invitation.role,
        "token": invitation.token,
        "short_code": invitation.short_code,
        "bot_link": bot_link or None,
        "web_link": web_link,
        "short_link": short_link,
        "expires_at": invitation.expires_at,
        "created_at": invitation.created_at,
        "created_by_id": invitation.created_by_id,
    }


def pending_invitation_select(now: datetime):
    return select(Invitation).where(
        Invitation.token.like("INV-%"),
        Invitation.is_used == False,
        Invitation.revoked_at.is_(None),
        Invitation.kind != InvitationKind.LEGACY_UNKNOWN,
        Invitation.expires_at > now,
    )


def can_manage_invitation(admin: User, invitation: Invitation) -> bool:
    if getattr(admin, "role", None) == UserRole.SUPER_ADMIN:
        return True
    return invitation.created_by_id == admin.id


@router.get("/pending", response_model=list[PendingInvitationResponse])
async def list_pending_invitations(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(verify_admin_user),
):
    now = utc_now_naive()
    stmt = (
        pending_invitation_select(now)
        .order_by(Invitation.created_at.desc(), Invitation.id.desc())
        .limit(100)
    )
    if getattr(admin, "role", None) != UserRole.SUPER_ADMIN:
        stmt = stmt.where(Invitation.created_by_id == admin.id)

    invitations = list((await db.execute(stmt)).scalars().all())
    return [serialize_pending_invitation(invitation) for invitation in invitations]


@router.delete("/pending/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pending_invitation(
    invitation_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(verify_admin_user),
):
    invitation = await lock_invitation_for_transition(
        db,
        invitation_id=invitation_id,
    )
    if (
        not invitation
        or not str(invitation.token or "").startswith("INV-")
        or not can_manage_invitation(admin, invitation)
    ):
        raise HTTPException(status_code=404, detail="دعوت‌نامه پیدا نشد")

    if invitation.is_used or getattr(invitation, "revoked_at", None) is not None or invitation.expires_at <= utc_now_naive():
        raise HTTPException(status_code=400, detail="دعوت‌نامه pending نیست")

    soft_revoke_invitation(invitation)
    if getattr(invitation, "id", None) is not None:
        await release_invitation_identity(db, invitation_id=invitation.id)
    await db.commit()
    return None

@router.post("/", response_model=InvitationResponse)
async def create_invitation(
    invite: InvitationCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(verify_admin_user)
):
    return await _create_standard_invitation(invite=invite, db=db, admin=admin)


@router.post(
    "/internal/create",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_invitation_internal_from_bot(
    payload: InternalInvitationCreateRequest,
    raw_request: Request,
    db: AsyncSession = Depends(get_db),
):
    body = await raw_request.body()
    if not verify_internal_signature(
        body,
        raw_request.headers.get("x-timestamp"),
        raw_request.headers.get("x-signature"),
        raw_request.headers.get("x-api-key"),
    ):
        raise HTTPException(status_code=401, detail="Invalid internal invitation signature")
    header_source = normalize_server(raw_request.headers.get("x-source-server"), default="")
    if current_server() != SERVER_IRAN:
        raise HTTPException(status_code=403, detail="Invitation creation is Iran-authoritative")
    if payload.source_server != SERVER_FOREIGN or header_source != SERVER_FOREIGN:
        raise HTTPException(status_code=401, detail="Invalid internal invitation source")
    expected_key = build_standard_invitation_idempotency_key(
        requester_user_id=payload.requester_user_id,
        account_name=payload.account_name,
        mobile_number=payload.mobile_number,
        role=payload.role,
    )
    if payload.idempotency_key != expected_key:
        raise HTTPException(status_code=400, detail="کلید تکرار دعوت‌نامه نامعتبر است")

    admin = await db.get(User, payload.requester_user_id)
    if not admin or getattr(admin, "is_deleted", False):
        raise HTTPException(status_code=404, detail="مدیر دعوت‌کننده یافت نشد")
    if getattr(admin, "role", None) not in (UserRole.SUPER_ADMIN, UserRole.MIDDLE_MANAGER):
        raise HTTPException(status_code=403, detail="کاربر اجازه ساخت دعوت‌نامه ندارد")
    return await _create_standard_invitation(
        invite=InvitationCreate(
            account_name=payload.account_name,
            mobile_number=payload.mobile_number,
            role=payload.role,
        ),
        db=db,
        admin=admin,
    )

@router.get(
    "/lookup/{short_code}",
    dependencies=[Depends(enforce_public_invitation_access)],
)
async def lookup_invitation(
    short_code: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Lookup full token from short code.
    """
    stmt = select(Invitation).where(Invitation.short_code == short_code)
    inv = (await db.execute(stmt)).scalar_one_or_none()
    
    if not inv:
        raise public_invitation_http_exception(status_code=404, detail="Invalid short code")
        
    if inv.is_used:
        raise public_invitation_http_exception(status_code=400, detail="Invitation already used")
    if getattr(inv, "revoked_at", None) is not None:
        raise public_invitation_http_exception(status_code=400, detail="Invitation revoked")
    if getattr(inv, "kind", None) == InvitationKind.LEGACY_UNKNOWN:
        raise public_invitation_http_exception(status_code=400, detail="Invitation state is ambiguous")

    if is_accountant_invitation_token(inv.token):
        relation = await get_pending_accountant_relation_by_invitation_token(db, inv.token)
        if not relation:
            raise public_invitation_http_exception(status_code=400, detail="Invitation expired")
    elif is_customer_invitation_token(inv.token):
        relation = await get_pending_customer_relation_by_invitation_token(db, inv.token)
        if not relation:
            raise public_invitation_http_exception(status_code=400, detail="Invitation expired")
        
    if inv.expires_at < utc_now_naive():
        raise public_invitation_http_exception(status_code=400, detail="Invitation expired")

    audit_log(
        "invitation.opened",
        target_type="invitation",
        target_id=getattr(inv, "id", None),
        extra={"surface": "web", "entry": "short_link"},
    )
    return {"token": inv.token}

@router.get(
    "/validate/{token}",
    dependencies=[Depends(enforce_public_invitation_access)],
)
async def validate_invitation(
    token: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Validate invitation token for web registration.
    """
    stmt = select(Invitation).where(Invitation.token == token)
    inv = (await db.execute(stmt)).scalar_one_or_none()
    
    if not inv:
        raise public_invitation_http_exception(status_code=404, detail="Invalid token")
        
    if inv.is_used:
        raise public_invitation_http_exception(status_code=400, detail="Invitation already used")
    if getattr(inv, "revoked_at", None) is not None:
        raise public_invitation_http_exception(status_code=400, detail="Invitation revoked")
    if getattr(inv, "kind", None) == InvitationKind.LEGACY_UNKNOWN:
        raise public_invitation_http_exception(status_code=400, detail="Invitation state is ambiguous")

    if is_accountant_invitation_token(token):
        relation = await get_pending_accountant_relation_by_invitation_token(db, token)
        if not relation:
            raise public_invitation_http_exception(status_code=400, detail="Invitation expired")
    elif is_customer_invitation_token(token):
        relation = await get_pending_customer_relation_by_invitation_token(db, token)
        if not relation:
            raise public_invitation_http_exception(status_code=400, detail="Invitation expired")
        
    if inv.expires_at < utc_now_naive():
        raise public_invitation_http_exception(status_code=400, detail="Invitation expired")

    audit_log(
        "invitation.opened",
        target_type="invitation",
        target_id=getattr(inv, "id", None),
        extra={"surface": "web", "entry": "token"},
    )
    return {
        "valid": True,
        "account_name": inv.account_name,
        "mobile_number": mask_mobile(inv.mobile_number),
        "role": inv.role
    }
