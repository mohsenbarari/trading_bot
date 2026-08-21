"""Administrator review surface for generic user flags."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

import schemas
from api.deps import verify_admin_user
from core.audit_logger import audit_log
from core.db import get_db
from core.services.user_flag_service import user_flag_labels
from core.services.user_management_context_service import (
    attach_user_management_relation_context,
    build_user_management_search_filter,
)
from core.utils import utc_now
from models.user import User, UserRole
from models.user_flag import UserFlag


router = APIRouter()


class UserFlagRead(BaseModel):
    id: int
    user_id: int
    flag_type: str
    flag_label: str
    reason_code: str
    reason_label: str
    status: str
    severity: str
    details: dict[str, Any]
    trigger_count: int
    first_flagged_at: datetime
    last_flagged_at: datetime
    resolved_at: datetime | None = None
    resolution_note: str | None = None
    user: schemas.UserRead


class ResolveUserFlagRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)


def _serialize_flag(flag: UserFlag, user: User) -> UserFlagRead:
    flag_label, reason_label = user_flag_labels(flag)
    return UserFlagRead(
        id=int(flag.id),
        user_id=int(flag.user_id),
        flag_type=flag.flag_type,
        flag_label=flag_label,
        reason_code=flag.reason_code,
        reason_label=reason_label,
        status=flag.status,
        severity=flag.severity,
        details=dict(flag.details or {}),
        trigger_count=int(flag.trigger_count or 1),
        first_flagged_at=flag.first_flagged_at,
        last_flagged_at=flag.last_flagged_at,
        resolved_at=flag.resolved_at,
        resolution_note=flag.resolution_note,
        user=schemas.UserRead.model_validate(user, from_attributes=True),
    )


def _assert_can_resolve_flag(actor: User, target: User) -> None:
    if int(actor.id) == int(target.id):
        raise HTTPException(status_code=403, detail="مدیر نمی‌تواند پرچم حساب خودش را تعیین تکلیف کند")
    if actor.role == UserRole.MIDDLE_MANAGER and target.role in (
        UserRole.SUPER_ADMIN,
        UserRole.MIDDLE_MANAGER,
    ):
        raise HTTPException(status_code=403, detail="مدیر میانی فقط می‌تواند کاربران غیرادمین را بررسی کند")


@router.get("/open", response_model=list[UserFlagRead])
async def list_open_user_flags(
    search: str | None = Query(default=None, min_length=1),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(verify_admin_user),
):
    stmt = (
        select(UserFlag, User)
        .join(User, User.id == UserFlag.user_id)
        .where(UserFlag.status == "open", User.is_deleted == False)
        .order_by(UserFlag.last_flagged_at.desc(), UserFlag.id.desc())
    )
    if actor.role == UserRole.MIDDLE_MANAGER:
        stmt = stmt.where(
            or_(
                User.id == actor.id,
                ~User.role.in_([UserRole.SUPER_ADMIN, UserRole.MIDDLE_MANAGER]),
            )
        )
    if search:
        stmt = stmt.where(build_user_management_search_filter(f"%{search.strip()}%"))

    rows = list((await db.execute(stmt)).all())
    users = [row[1] for row in rows]
    await attach_user_management_relation_context(db, users)
    return [_serialize_flag(flag, user) for flag, user in rows]


@router.post("/{flag_id}/resolve", response_model=UserFlagRead)
async def resolve_user_flag(
    flag_id: int,
    payload: ResolveUserFlagRequest,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(verify_admin_user),
):
    row = (
        await db.execute(
            select(UserFlag, User)
            .join(User, User.id == UserFlag.user_id)
            .where(and_(UserFlag.id == flag_id, UserFlag.status == "open"))
            .with_for_update()
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="پرچم باز یافت نشد")
    flag, target = row
    _assert_can_resolve_flag(actor, target)

    note = str(payload.note or "").strip() or None
    flag.status = "resolved"
    flag.resolved_at = utc_now()
    flag.resolved_by_user_id = int(actor.id)
    flag.resolution_note = note
    await db.commit()
    await db.refresh(flag)
    await attach_user_management_relation_context(db, [target])
    audit_log(
        "user_flag.resolve",
        target_type="user_flag",
        target_id=flag.id,
        actor_id=actor.id,
        actor_role=getattr(actor.role, "value", str(actor.role)),
        after_summary={
            "flag_type": flag.flag_type,
            "flagged_user_id": target.id,
            "status": flag.status,
            "has_note": note is not None,
        },
    )
    return _serialize_flag(flag, target)
