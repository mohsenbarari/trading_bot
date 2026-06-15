from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import and_, select, or_
from sqlalchemy.orm import aliased, joinedload
from typing import List, Optional

from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from core.db import get_db
from core.services.accountant_relation_service import get_active_accountant_relation_for_accountant, list_active_accountants_for_owner
from core.services.customer_relation_service import (
    build_allowed_customer_chat_targets,
    get_active_customer_relation_for_customer,
    list_active_customers_for_owner,
)
from core.services.chat_role_badge_service import (
    CHAT_ROLE_ACCOUNTANT,
    CHAT_ROLE_COLLEAGUE,
    CHAT_ROLE_CUSTOMER,
    CHAT_ROLE_LABELS,
)
from models.customer_relation import CustomerRelation, CustomerRelationStatus
from models.user import User, UserRole
from api.deps import get_current_user

import schemas

router = APIRouter(
    tags=["Public Users"],
    dependencies=[Depends(get_current_user)]
)


PROJECT_DIRECTORY_ROLES = (
    UserRole.WATCH,
    UserRole.STANDARD,
    UserRole.MIDDLE_MANAGER,
    UserRole.SUPER_ADMIN,
)

_LAST_SEEN_UNSET = object()


def _serialize_public_user(
    user: User,
    *,
    resolved_from_accountant_id: int | None = None,
    chat_role_kind: str | None = None,
    chat_role_label: str | None = None,
    chat_accountant_owner_name: str | None = None,
    chat_accountant_owner_label: str | None = None,
    highlight_accountant_user_id: int | None = None,
    highlight_accountant_relation_display_name: str | None = None,
    accountant_relations: list[schemas.PublicAccountantRelationSummary] | None = None,
    customer_owner_user_id: int | None = None,
    customer_owner_account_name: str | None = None,
    customer_management_name: str | None = None,
    customer_tier=None,
    customer_relations: list[schemas.PublicCustomerRelationSummary] | None = None,
    last_seen_at=_LAST_SEEN_UNSET,
) -> schemas.UserPublicRead:
    public_user = schemas.UserPublicRead.model_validate(user, from_attributes=True)
    updates = {
        "resolved_from_accountant_id": resolved_from_accountant_id,
        "chat_role_kind": chat_role_kind,
        "chat_role_label": chat_role_label,
        "chat_accountant_owner_name": chat_accountant_owner_name,
        "chat_accountant_owner_label": chat_accountant_owner_label,
        "highlight_accountant_user_id": highlight_accountant_user_id,
        "highlight_accountant_relation_display_name": highlight_accountant_relation_display_name,
        "accountant_relations": accountant_relations or [],
        "customer_owner_user_id": customer_owner_user_id,
        "customer_owner_account_name": customer_owner_account_name,
        "customer_management_name": customer_management_name,
        "customer_tier": customer_tier,
        "customer_relations": customer_relations or [],
    }
    if last_seen_at is not _LAST_SEEN_UNSET:
        updates["last_seen_at"] = last_seen_at
    return public_user.model_copy(update=updates)


def _serialize_public_accountant_relation(
    relation: AccountantRelation,
) -> schemas.PublicAccountantRelationSummary | None:
    accountant_user = getattr(relation, "accountant_user", None)
    if accountant_user is None or accountant_user.is_deleted:
        return None
    return schemas.PublicAccountantRelationSummary(
        accountant_user_id=accountant_user.id,
        accountant_account_name=accountant_user.account_name,
        relation_display_name=relation.relation_display_name,
        duty_description=relation.duty_description,
    )


def _serialize_public_customer_relation(
    relation: CustomerRelation,
) -> schemas.PublicCustomerRelationSummary | None:
    customer_user = getattr(relation, "customer_user", None)
    if customer_user is None or customer_user.is_deleted:
        return None
    return schemas.PublicCustomerRelationSummary(
        customer_user_id=customer_user.id,
        customer_account_name=customer_user.account_name,
        management_name=relation.management_name,
        customer_tier=relation.customer_tier,
    )


def _serialize_project_user_directory_entry(user: User) -> schemas.ProjectUserDirectoryEntry:
    return schemas.ProjectUserDirectoryEntry.model_validate(user, from_attributes=True)


def _serialize_public_search_result(
    user: User,
    *,
    resolved_from_accountant_id: int | None = None,
    chat_role_kind: str | None = None,
    chat_role_label: str | None = None,
    chat_accountant_owner_name: str | None = None,
    chat_accountant_owner_label: str | None = None,
    highlight_accountant_user_id: int | None = None,
    highlight_accountant_relation_display_name: str | None = None,
    customer_owner_user_id: int | None = None,
    customer_owner_account_name: str | None = None,
    customer_management_name: str | None = None,
    customer_tier=None,
) -> schemas.PublicUserSearchResult:
    search_result = schemas.PublicUserSearchResult.model_validate(user, from_attributes=True)
    return search_result.model_copy(update={
        "resolved_from_accountant_id": resolved_from_accountant_id,
        "chat_role_kind": chat_role_kind,
        "chat_role_label": chat_role_label,
        "chat_accountant_owner_name": chat_accountant_owner_name,
        "chat_accountant_owner_label": chat_accountant_owner_label,
        "highlight_accountant_user_id": highlight_accountant_user_id,
        "highlight_accountant_relation_display_name": highlight_accountant_relation_display_name,
        "customer_owner_user_id": customer_owner_user_id,
        "customer_owner_account_name": customer_owner_account_name,
        "customer_management_name": customer_management_name,
        "customer_tier": customer_tier,
    })


def _is_super_admin(user: User) -> bool:
    return getattr(user, "role", None) == UserRole.SUPER_ADMIN


def _can_view_owner_customer_list(current_user: User, owner_user_id: int) -> bool:
    return current_user.id == owner_user_id or _is_super_admin(current_user)


def _can_view_project_users_directory(
    current_user: User,
    owner_user_id: int,
    *,
    viewer_accountant_relation: AccountantRelation | None,
) -> bool:
    if current_user.id == owner_user_id:
        return True
    return (
        viewer_accountant_relation is not None
        and getattr(viewer_accountant_relation, "owner_user_id", None) == owner_user_id
    )


def _can_view_customer_profile(
    current_user: User,
    relation: CustomerRelation,
    *,
    viewer_accountant_relation: AccountantRelation | None,
) -> bool:
    if current_user.id == relation.customer_user_id:
        return True
    if current_user.id == relation.owner_user_id:
        return True
    if _is_super_admin(current_user):
        return True
    return (
        viewer_accountant_relation is not None
        and getattr(viewer_accountant_relation, "owner_user_id", None) == relation.owner_user_id
    )


def _customer_profile_access_is_immediate(current_user: User, relation: CustomerRelation) -> bool:
    return (
        current_user.id == relation.customer_user_id
        or current_user.id == relation.owner_user_id
        or _is_super_admin(current_user)
    )


def _build_customer_public_visibility_filter(current_user: User):
    current_customer_owner_rel = aliased(CustomerRelation)
    current_customer_exists_rel = aliased(CustomerRelation)
    target_customer_rel = aliased(CustomerRelation)
    target_accountant_rel = aliased(AccountantRelation)

    current_customer_owner_id = (
        select(current_customer_owner_rel.owner_user_id)
        .where(
            current_customer_owner_rel.customer_user_id == current_user.id,
            current_customer_owner_rel.status == CustomerRelationStatus.ACTIVE,
            current_customer_owner_rel.deleted_at.is_(None),
        )
        .limit(1)
        .scalar_subquery()
    )
    current_customer_exists = (
        select(current_customer_exists_rel.id)
        .where(
            current_customer_exists_rel.customer_user_id == current_user.id,
            current_customer_exists_rel.status == CustomerRelationStatus.ACTIVE,
            current_customer_exists_rel.deleted_at.is_(None),
        )
        .exists()
    )
    target_customer_exists = (
        select(target_customer_rel.id)
        .where(
            target_customer_rel.customer_user_id == User.id,
            target_customer_rel.status == CustomerRelationStatus.ACTIVE,
            target_customer_rel.deleted_at.is_(None),
        )
        .exists()
    )
    target_same_owner_accountant_exists = (
        select(target_accountant_rel.id)
        .where(
            target_accountant_rel.accountant_user_id == User.id,
            target_accountant_rel.owner_user_id == current_customer_owner_id,
            target_accountant_rel.status == AccountantRelationStatus.ACTIVE,
            target_accountant_rel.deleted_at.is_(None),
        )
        .exists()
    )

    return or_(
        ~current_customer_exists,
        User.id == current_customer_owner_id,
        and_(User.role == UserRole.SUPER_ADMIN, ~target_customer_exists),
        target_same_owner_accountant_exists,
    )


def _build_direct_chat_target_visibility_filter(current_user: User):
    viewer_accountant_rel = aliased(AccountantRelation)
    target_customer_rel = aliased(CustomerRelation)
    target_owned_customer_rel = aliased(CustomerRelation)
    target_same_owner_customer_rel = aliased(CustomerRelation)

    viewer_accountant_owner_id = (
        select(viewer_accountant_rel.owner_user_id)
        .where(
            viewer_accountant_rel.accountant_user_id == current_user.id,
            viewer_accountant_rel.status == AccountantRelationStatus.ACTIVE,
            viewer_accountant_rel.deleted_at.is_(None),
        )
        .limit(1)
        .scalar_subquery()
    )

    target_customer_exists = (
        select(target_customer_rel.id)
        .where(
            target_customer_rel.customer_user_id == User.id,
            target_customer_rel.status == CustomerRelationStatus.ACTIVE,
            target_customer_rel.deleted_at.is_(None),
        )
        .exists()
    )
    target_owned_customer_exists = (
        select(target_owned_customer_rel.id)
        .where(
            target_owned_customer_rel.customer_user_id == User.id,
            target_owned_customer_rel.owner_user_id == current_user.id,
            target_owned_customer_rel.status == CustomerRelationStatus.ACTIVE,
            target_owned_customer_rel.deleted_at.is_(None),
        )
        .exists()
    )
    target_same_owner_customer_exists = (
        select(target_same_owner_customer_rel.id)
        .where(
            target_same_owner_customer_rel.customer_user_id == User.id,
            target_same_owner_customer_rel.owner_user_id == viewer_accountant_owner_id,
            target_same_owner_customer_rel.status == CustomerRelationStatus.ACTIVE,
            target_same_owner_customer_rel.deleted_at.is_(None),
        )
        .exists()
    )

    return or_(
        ~target_customer_exists,
        target_owned_customer_exists,
        target_same_owner_customer_exists,
    )


async def _ensure_customer_viewer_can_access_public_user(
    db: AsyncSession,
    current_user: User,
    target_user_id: int,
    *,
    current_customer_relation: CustomerRelation | None = None,
    allowed_target_ids: set[int] | None = None,
) -> None:
    if target_user_id == current_user.id:
        return

    if current_customer_relation is None:
        current_customer_relation = await get_active_customer_relation_for_customer(db, current_user.id)
    if current_customer_relation is None:
        return

    if allowed_target_ids is None:
        allowed_target_ids = set(await build_allowed_customer_chat_targets(db, current_user.id))
    if target_user_id not in allowed_target_ids:
        raise HTTPException(status_code=404, detail="User not found")


def _build_project_user_directory_stmt(
    *,
    current_user_id: int,
    q: str | None,
    limit: int,
    offset: int,
):
    active_accountant_exists = (
        select(AccountantRelation.id)
        .where(
            AccountantRelation.accountant_user_id == User.id,
            AccountantRelation.status == AccountantRelationStatus.ACTIVE,
            AccountantRelation.deleted_at.is_(None),
        )
        .exists()
    )
    active_customer_exists = (
        select(CustomerRelation.id)
        .where(
            CustomerRelation.customer_user_id == User.id,
            CustomerRelation.status == CustomerRelationStatus.ACTIVE,
            CustomerRelation.deleted_at.is_(None),
        )
        .exists()
    )

    stmt = select(User).where(
        User.is_deleted == False,
        User.role.in_(PROJECT_DIRECTORY_ROLES),
        User.id != current_user_id,
        ~active_accountant_exists,
        ~active_customer_exists,
    )

    normalized_query = (q or "").strip()
    if normalized_query:
        search_pattern = f"%{normalized_query}%"
        stmt = stmt.where(
            or_(
                User.account_name.ilike(search_pattern),
                User.mobile_number.ilike(search_pattern),
            )
        )

    return stmt.order_by(User.account_name.asc(), User.id.asc()).offset(offset).limit(limit)


async def _load_public_accountant_relation_summaries(
    db: AsyncSession,
    owner_user_id: int,
) -> list[schemas.PublicAccountantRelationSummary]:
    relations = await list_active_accountants_for_owner(db, owner_user_id)
    serialized = [_serialize_public_accountant_relation(relation) for relation in relations]
    return [relation for relation in serialized if relation is not None]


async def _load_public_customer_relation_summaries(
    db: AsyncSession,
    owner_user_id: int,
) -> list[schemas.PublicCustomerRelationSummary]:
    relations = await list_active_customers_for_owner(db, owner_user_id)
    serialized = [_serialize_public_customer_relation(relation) for relation in relations]
    return [relation for relation in serialized if relation is not None]


async def _resolve_public_search_rows(
    db: AsyncSession,
    rows: list[User],
    *,
    current_user: User,
    preserve_chat_target_identity: bool = False,
) -> list[schemas.PublicUserSearchResult]:
    user_ids = [user.id for user in rows if getattr(user, "id", None) is not None]
    if not user_ids:
        return []

    relation_stmt = (
        select(AccountantRelation)
        .options(joinedload(AccountantRelation.owner_user))
        .where(
            AccountantRelation.accountant_user_id.in_(user_ids),
            AccountantRelation.status == AccountantRelationStatus.ACTIVE,
            AccountantRelation.deleted_at.is_(None),
        )
    )
    relations = list((await db.execute(relation_stmt)).scalars().all())
    relation_by_accountant_id = {
        relation.accountant_user_id: relation
        for relation in relations
        if relation.accountant_user_id is not None
    }

    customer_relation_stmt = (
        select(CustomerRelation)
        .options(joinedload(CustomerRelation.owner_user))
        .where(
            CustomerRelation.customer_user_id.in_(user_ids),
            CustomerRelation.status == CustomerRelationStatus.ACTIVE,
            CustomerRelation.deleted_at.is_(None),
        )
    )
    customer_relations = list((await db.execute(customer_relation_stmt)).scalars().all())
    relation_by_customer_id = {
        relation.customer_user_id: relation
        for relation in customer_relations
        if relation.customer_user_id is not None
    }
    viewer_accountant_relation: AccountantRelation | None = None
    if any(
        not _customer_profile_access_is_immediate(current_user, relation)
        for relation in customer_relations
    ):
        viewer_accountant_relation = await get_active_accountant_relation_for_accountant(db, current_user.id)

    serialized_rows: list[schemas.PublicUserSearchResult] = []
    seen_user_ids: set[int] = set()
    for user in rows:
        relation = relation_by_accountant_id.get(user.id)
        if relation and relation.owner_user and not relation.owner_user.is_deleted:
            if preserve_chat_target_identity:
                if user.id == current_user.id or user.id in seen_user_ids:
                    continue
                serialized_rows.append(
                    _serialize_public_search_result(
                        user,
                        chat_role_kind=CHAT_ROLE_ACCOUNTANT,
                        chat_role_label=CHAT_ROLE_LABELS[CHAT_ROLE_ACCOUNTANT],
                        chat_accountant_owner_name=relation.owner_user.account_name,
                        chat_accountant_owner_label=f"سرگروه: {relation.owner_user.account_name}",
                    )
                )
                seen_user_ids.add(user.id)
                continue

            owner_user = relation.owner_user
            if owner_user.id == current_user.id or owner_user.id in seen_user_ids:
                continue
            serialized_rows.append(
                _serialize_public_search_result(
                    owner_user,
                    resolved_from_accountant_id=user.id,
                    chat_role_kind=CHAT_ROLE_COLLEAGUE,
                    chat_role_label=CHAT_ROLE_LABELS[CHAT_ROLE_COLLEAGUE],
                    highlight_accountant_user_id=user.id,
                    highlight_accountant_relation_display_name=relation.relation_display_name,
                )
            )
            seen_user_ids.add(owner_user.id)
            continue

        customer_relation = relation_by_customer_id.get(user.id)
        if customer_relation:
            if not _can_view_customer_profile(
                current_user,
                customer_relation,
                viewer_accountant_relation=viewer_accountant_relation,
            ):
                continue

            owner_user = customer_relation.owner_user
            serialized_rows.append(
                _serialize_public_search_result(
                    user,
                    chat_role_kind=CHAT_ROLE_CUSTOMER,
                    chat_role_label=CHAT_ROLE_LABELS[CHAT_ROLE_CUSTOMER],
                    customer_owner_user_id=owner_user.id if owner_user and not owner_user.is_deleted else None,
                    customer_owner_account_name=owner_user.account_name if owner_user and not owner_user.is_deleted else None,
                    customer_management_name=customer_relation.management_name,
                    customer_tier=customer_relation.customer_tier,
                )
            )
            seen_user_ids.add(user.id)
            continue

        if user.id == current_user.id or user.id in seen_user_ids:
            continue
        serialized_rows.append(
            _serialize_public_search_result(
                user,
                chat_role_kind=CHAT_ROLE_COLLEAGUE,
                chat_role_label=CHAT_ROLE_LABELS[CHAT_ROLE_COLLEAGUE],
            )
        )
        seen_user_ids.add(user.id)

    return serialized_rows

@router.get("/search", response_model=List[schemas.PublicUserSearchResult])
async def search_public_users(
    q: Optional[str] = Query(None, min_length=1),
    limit: int = 50,
    chat_targets: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """جستجوی عمومی بین کاربران سیستم بر اساس نام، نام کاربری یا شماره فیلتر شده"""
    direct_chat_targets = chat_targets is True
    current_customer_relation = await get_active_customer_relation_for_customer(db, current_user.id)

    query = select(User).where(User.is_deleted == False, User.id != current_user.id)
    if current_customer_relation is not None:
        allowed_target_ids = await build_allowed_customer_chat_targets(db, current_user.id)
        if not allowed_target_ids:
            return []
        query = query.where(User.id.in_(allowed_target_ids))
    elif direct_chat_targets:
        query = query.where(_build_direct_chat_target_visibility_filter(current_user))
    else:
        query = query.where(_build_customer_public_visibility_filter(current_user))

    if q:
        search_pattern = f"%{q}%"
        search_terms = [
            User.full_name.ilike(search_pattern),
            User.account_name.ilike(search_pattern),
            User.username.ilike(search_pattern),
            User.mobile_number.ilike(search_pattern),
        ]
        if direct_chat_targets:
            accountant_relation_search_alias = aliased(AccountantRelation)
            accountant_owner_search_alias = aliased(User)
            customer_relation_search_alias = aliased(CustomerRelation)
            accountant_owner_match_exists = (
                select(accountant_relation_search_alias.id)
                .join(
                    accountant_owner_search_alias,
                    accountant_owner_search_alias.id == accountant_relation_search_alias.owner_user_id,
                )
                .where(
                    accountant_relation_search_alias.accountant_user_id == User.id,
                    accountant_relation_search_alias.status == AccountantRelationStatus.ACTIVE,
                    accountant_relation_search_alias.deleted_at.is_(None),
                    accountant_owner_search_alias.is_deleted.is_(False),
                    or_(
                        accountant_owner_search_alias.full_name.ilike(search_pattern),
                        accountant_owner_search_alias.account_name.ilike(search_pattern),
                        accountant_owner_search_alias.username.ilike(search_pattern),
                        accountant_owner_search_alias.mobile_number.ilike(search_pattern),
                    ),
                )
                .exists()
            )
            customer_management_match_exists = (
                select(customer_relation_search_alias.id)
                .where(
                    customer_relation_search_alias.customer_user_id == User.id,
                    customer_relation_search_alias.status == CustomerRelationStatus.ACTIVE,
                    customer_relation_search_alias.deleted_at.is_(None),
                    customer_relation_search_alias.management_name.ilike(search_pattern),
                )
                .exists()
            )
            search_terms.append(accountant_owner_match_exists)
            search_terms.append(customer_management_match_exists)
        query = query.where(
            or_(
                *search_terms,
            )
        )
        
    query = query.order_by(User.id.desc()).limit(limit)
    result = await db.execute(query)
    users = result.scalars().all()
    return await _resolve_public_search_rows(
        db,
        users,
        current_user=current_user,
        preserve_chat_target_identity=direct_chat_targets,
    )


@router.get("/{user_id}/project-users", response_model=List[schemas.ProjectUserDirectoryEntry])
async def list_project_users_directory(
    user_id: int,
    q: Optional[str] = Query(None, min_length=1),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """لیست کاربران پروژه برای self public profile یا accountant-resolved owner profile."""
    current_customer_relation = await get_active_customer_relation_for_customer(db, current_user.id)
    if current_customer_relation is not None:
        raise HTTPException(status_code=403, detail="Customers cannot view the project users directory")

    if not _can_view_project_users_directory(
        current_user,
        user_id,
        viewer_accountant_relation=(
            await get_active_accountant_relation_for_accountant(db, current_user.id)
            if current_user.id != user_id
            else None
        ),
    ):
        raise HTTPException(status_code=403, detail="Access denied")

    normalized_limit = limit if isinstance(limit, int) else 25
    normalized_offset = offset if isinstance(offset, int) else 0
    stmt = _build_project_user_directory_stmt(
        current_user_id=current_user.id,
        q=q,
        limit=normalized_limit,
        offset=normalized_offset,
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [_serialize_project_user_directory_entry(user) for user in rows]

@router.get("/{user_id}", response_model=schemas.UserPublicRead)
async def read_public_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """دریافت اطلاعات عمومی یک کاربر (قابل دسترسی برای همه کاربران لاگین شده)"""
    current_customer_relation_loaded = False
    current_customer_relation: CustomerRelation | None = None
    allowed_target_ids_loaded = False
    allowed_target_ids: set[int] | None = None

    async def get_current_customer_relation() -> CustomerRelation | None:
        nonlocal current_customer_relation_loaded, current_customer_relation
        if not current_customer_relation_loaded:
            current_customer_relation = await get_active_customer_relation_for_customer(db, current_user.id)
            current_customer_relation_loaded = True
        return current_customer_relation

    async def get_customer_allowed_target_ids() -> set[int] | None:
        nonlocal allowed_target_ids_loaded, allowed_target_ids
        current_relation = await get_current_customer_relation()
        if current_relation is None:
            return None
        if not allowed_target_ids_loaded:
            allowed_target_ids = set(await build_allowed_customer_chat_targets(db, current_user.id))
            allowed_target_ids_loaded = True
        return allowed_target_ids

    async def ensure_customer_viewer_can_access_target(target_user_id: int) -> None:
        if target_user_id == current_user.id:
            return
        current_relation = await get_current_customer_relation()
        if current_relation is None:
            return
        await _ensure_customer_viewer_can_access_public_user(
            db,
            current_user,
            target_user_id,
            current_customer_relation=current_relation,
            allowed_target_ids=await get_customer_allowed_target_ids(),
        )

    relation = await get_active_accountant_relation_for_accountant(db, user_id)
    if relation and relation.owner_user and not relation.owner_user.is_deleted:
        await ensure_customer_viewer_can_access_target(user_id)
        accountant_relations = await _load_public_accountant_relation_summaries(db, relation.owner_user.id)
        customer_relations = []
        if _can_view_owner_customer_list(current_user, relation.owner_user.id):
            customer_relations = await _load_public_customer_relation_summaries(db, relation.owner_user.id)
        return _serialize_public_user(
            relation.owner_user,
            resolved_from_accountant_id=user_id,
            highlight_accountant_user_id=user_id,
            highlight_accountant_relation_display_name=relation.relation_display_name,
            accountant_relations=accountant_relations,
            customer_relations=customer_relations,
            last_seen_at=getattr(getattr(relation, "accountant_user", None), "last_seen_at", None),
        )

    customer_relation = await get_active_customer_relation_for_customer(db, user_id)
    if customer_relation:
        customer_user = customer_relation.customer_user
        if customer_user is None or customer_user.is_deleted:
            raise HTTPException(status_code=404, detail="User not found")
        viewer_accountant_relation = (
            await get_active_accountant_relation_for_accountant(db, current_user.id)
            if not _customer_profile_access_is_immediate(current_user, customer_relation)
            else None
        )
        if not _can_view_customer_profile(
            current_user,
            customer_relation,
            viewer_accountant_relation=viewer_accountant_relation,
        ):
            raise HTTPException(status_code=404, detail="User not found")

        owner_user = customer_relation.owner_user
        return _serialize_public_user(
            customer_user,
            customer_owner_user_id=owner_user.id if owner_user and not owner_user.is_deleted else None,
            customer_owner_account_name=owner_user.account_name if owner_user and not owner_user.is_deleted else None,
            customer_management_name=customer_relation.management_name,
            customer_tier=customer_relation.customer_tier,
        )

    user = await db.get(User, user_id)
    if not user or user.is_deleted:
        raise HTTPException(status_code=404, detail="User not found")
    await ensure_customer_viewer_can_access_target(user_id)
    accountant_relations = await _load_public_accountant_relation_summaries(db, user.id)
    customer_relations = []
    if _can_view_owner_customer_list(current_user, user.id):
        customer_relations = await _load_public_customer_relation_summaries(db, user.id)
    return _serialize_public_user(user, accountant_relations=accountant_relations, customer_relations=customer_relations)
