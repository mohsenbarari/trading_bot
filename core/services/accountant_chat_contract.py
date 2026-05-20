from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from models.accountant_relation import AccountantRelation, AccountantRelationStatus


@dataclass(frozen=True)
class AccountantChatIdentity:
    raw_user_id: int
    display_name: str
    profile_user_id: int
    profile_account_name: str
    profile_avatar_file_id: str | None = None
    resolved_from_accountant_id: int | None = None
    highlight_accountant_user_id: int | None = None
    highlight_accountant_relation_display_name: str | None = None


def build_relation_aware_display_name(
    relation_display_name: str | None,
    fallback_account_name: str | None,
) -> str:
    normalized_relation_display_name = (relation_display_name or "").strip()
    if normalized_relation_display_name:
        return normalized_relation_display_name
    return (fallback_account_name or "").strip()


async def load_accountant_chat_identity_map(
    db: AsyncSession,
    user_ids: Iterable[int],
) -> dict[int, AccountantChatIdentity]:
    normalized_user_ids = sorted(
        {
            normalized_user_id
            for user_id in user_ids
            for normalized_user_id in [_coerce_positive_int(user_id)]
            if normalized_user_id is not None
        }
    )
    if not normalized_user_ids:
        return {}

    stmt = (
        select(AccountantRelation)
        .options(joinedload(AccountantRelation.owner_user))
        .where(
            AccountantRelation.accountant_user_id.in_(normalized_user_ids),
            AccountantRelation.status == AccountantRelationStatus.ACTIVE,
            AccountantRelation.deleted_at.is_(None),
        )
    )
    relations = list((await db.execute(stmt)).scalars().all())
    identity_map: dict[int, AccountantChatIdentity] = {}
    for relation in relations:
        if relation.accountant_user_id is None or relation.owner_user is None or relation.owner_user.is_deleted:
            continue
        owner_user = relation.owner_user
        identity_map[relation.accountant_user_id] = AccountantChatIdentity(
            raw_user_id=relation.accountant_user_id,
            display_name=build_relation_aware_display_name(
                relation.relation_display_name,
                owner_user.account_name,
            ),
            profile_user_id=owner_user.id,
            profile_account_name=owner_user.account_name,
            profile_avatar_file_id=getattr(owner_user, "avatar_file_id", None),
            resolved_from_accountant_id=relation.accountant_user_id,
            highlight_accountant_user_id=relation.accountant_user_id,
            highlight_accountant_relation_display_name=relation.relation_display_name,
        )
    return identity_map


async def resolve_direct_sender_display_name(
    db: AsyncSession | object,
    *,
    user: object,
) -> str | None:
    # Compatibility alias for older direct-only call sites and tests.
    return await resolve_relation_aware_sender_display_name(db, user=user)


async def resolve_relation_aware_sender_display_name(
    db: AsyncSession | object,
    *,
    user: object,
) -> str | None:
    if not hasattr(db, "execute"):
        return getattr(user, "account_name", None)

    sender_id = _coerce_positive_int(getattr(user, "id", None))
    if sender_id is None:
        return getattr(user, "account_name", None)

    identity = (await load_accountant_chat_identity_map(db, [sender_id])).get(sender_id)
    if identity is not None:
        return identity.display_name

    return getattr(user, "account_name", None)


def _coerce_positive_int(value: Any) -> int | None:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def apply_accountant_identity_to_direct_conversation_row(
    row: Mapping[str, Any],
    identity_map: Mapping[int, AccountantChatIdentity],
) -> dict[str, Any]:
    enriched_row = dict(row)
    raw_user_id = _coerce_positive_int(enriched_row.get("other_user_id"))
    fallback_name = (enriched_row.get("other_user_name") or "").strip()
    if raw_user_id is None:
        return enriched_row

    identity = identity_map.get(raw_user_id)
    if identity is None:
        enriched_row["profile_user_id"] = raw_user_id
        enriched_row["profile_account_name"] = fallback_name or None
        return enriched_row

    enriched_row["other_user_name"] = identity.display_name
    enriched_row["avatar_file_id"] = identity.profile_avatar_file_id
    enriched_row["profile_user_id"] = identity.profile_user_id
    enriched_row["profile_account_name"] = identity.profile_account_name
    enriched_row["resolved_from_accountant_id"] = identity.resolved_from_accountant_id
    enriched_row["highlight_accountant_user_id"] = identity.highlight_accountant_user_id
    enriched_row["highlight_accountant_relation_display_name"] = identity.highlight_accountant_relation_display_name
    return enriched_row


def collect_message_identity_user_ids(items: Iterable[Mapping[str, Any] | Any]) -> set[int]:
    user_ids: set[int] = set()
    for item in items:
        for field_name in ("sender_id", "forwarded_from_id"):
            value = item.get(field_name) if isinstance(item, Mapping) else getattr(item, field_name, None)
            normalized = _coerce_positive_int(value)
            if normalized is not None:
                user_ids.add(normalized)
    return user_ids


def apply_accountant_identity_to_message_payload(
    payload: Mapping[str, Any],
    identity_map: Mapping[int, AccountantChatIdentity],
) -> dict[str, Any]:
    enriched_payload = dict(payload)

    def apply_identity(field_prefix: str, id_field: str, name_field: str) -> None:
        raw_user_id = _coerce_positive_int(enriched_payload.get(id_field))
        fallback_name = (enriched_payload.get(name_field) or "").strip()
        if raw_user_id is None:
            return

        identity = identity_map.get(raw_user_id)
        if identity is None:
            enriched_payload[f"{field_prefix}_profile_user_id"] = raw_user_id
            enriched_payload[f"{field_prefix}_profile_account_name"] = fallback_name or None
            return

        enriched_payload[name_field] = identity.display_name
        enriched_payload[f"{field_prefix}_profile_user_id"] = identity.profile_user_id
        enriched_payload[f"{field_prefix}_profile_account_name"] = identity.profile_account_name
        enriched_payload[f"{field_prefix}_resolved_from_accountant_id"] = identity.resolved_from_accountant_id
        enriched_payload[f"{field_prefix}_highlight_accountant_user_id"] = identity.highlight_accountant_user_id
        enriched_payload[f"{field_prefix}_highlight_accountant_relation_display_name"] = identity.highlight_accountant_relation_display_name

    apply_identity("sender", "sender_id", "sender_name")
    apply_identity("forwarded_from", "forwarded_from_id", "forwarded_from_name")
    return enriched_payload