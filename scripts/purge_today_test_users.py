from __future__ import annotations

import argparse
import asyncio
import shutil
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Iterable

import redis.asyncio as redis
from sqlalchemy import delete, distinct, or_, select, update

from core.db import AsyncSessionLocal
from core.config import settings
from models.accountant_relation import AccountantRelation
from models.admin_message import AdminBroadcastMessage, AdminMarketMessage
from models.chat import Chat
from models.chat_file import ChatFile
from models.chat_member import ChatMember
from models.conversation import Conversation
from models.customer_relation import CustomerRelation
from models.invitation import Invitation
from models.market_schedule_override import MarketScheduleOverride
from models.message import Message
from models.notification import Notification
from models.offer import Offer
from models.session import SessionLoginRequest, SingleSessionRecoveryRequest, UserSession
from models.trade import Trade
from models.upload_session import UploadBatch, UploadSession
from models.user import User
from models.user_block import UserBlock


UPLOAD_ROOT = Path("uploads")
CHAT_FILES_ROOT = UPLOAD_ROOT / "chat_files"
CHAT_SESSIONS_ROOT = UPLOAD_ROOT / "chat_sessions"


def utc_day_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    current = now or datetime.now(timezone.utc)
    start = datetime.combine(current.date(), time.min, tzinfo=timezone.utc)
    end = current
    return start, end


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hard-delete benchmark/test users created today and all related data.",
    )
    parser.add_argument("--execute", action="store_true", help="Actually delete data. Without this flag the script only reports targets.")
    parser.add_argument(
        "--include-all-today",
        action="store_true",
        help="Delete all users created today, not just benchmark/dev accounts.",
    )
    parser.add_argument(
        "--prefix",
        action="append",
        default=["bench_", "dev_"],
        help="Account-name prefixes to match for test users. Can be passed multiple times.",
    )
    return parser.parse_args()


def _normalize_prefixes(prefixes: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for prefix in prefixes:
        value = prefix.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


async def _redis_delete_keys(keys: Iterable[str]) -> int:
    if redis is None:
        return 0
    client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        unique = [key for key in sorted(set(keys)) if key]
        if not unique:
            return 0
        existing: list[str] = []
        for key in unique:
            if await client.exists(key):
                existing.append(key)
        if existing:
            await client.delete(*existing)
        return len(existing)
    finally:
        await client.aclose()


def _user_test_match_clauses(prefixes: list[str], include_all_today: bool):
    if include_all_today:
        return []
    return [User.account_name.startswith(prefix) for prefix in prefixes]


async def _collect_targets(db, start: datetime, end: datetime, prefixes: list[str], include_all_today: bool):
    clauses = [User.created_at >= start, User.created_at <= end]
    prefix_clauses = _user_test_match_clauses(prefixes, include_all_today)
    if prefix_clauses:
        clauses.append(or_(*prefix_clauses))
    stmt = select(User).where(*clauses).order_by(User.id.asc())
    rows = (await db.execute(stmt)).scalars().all()
    return rows


async def _collect_chat_ids(db, target_ids: list[int]) -> list[int]:
    if not target_ids:
        return []
    created_rows = await db.execute(select(Chat.id).where(Chat.created_by_id.in_(target_ids)))
    member_rows = await db.execute(select(distinct(ChatMember.chat_id)).where(ChatMember.user_id.in_(target_ids)))
    chat_ids = {int(chat_id) for chat_id in created_rows.scalars().all() if chat_id is not None}
    chat_ids.update(int(chat_id) for chat_id in member_rows.scalars().all() if chat_id is not None)
    return sorted(chat_ids)


async def _collect_message_ids(db, target_ids: list[int], chat_ids: list[int]) -> list[int]:
    if not target_ids:
        target_message_ids = []
    else:
        stmt = select(Message.id).where(
            or_(
                Message.sender_id.in_(target_ids),
                Message.receiver_id.in_(target_ids),
                Message.actor_user_id.in_(target_ids),
                Message.forwarded_from_id.in_(target_ids),
            )
        )
        rows = (await db.execute(stmt)).scalars().all()
        target_message_ids = [int(message_id) for message_id in rows if message_id is not None]

    chat_message_ids: list[int] = []
    if chat_ids:
        rows = await db.execute(select(Message.id).where(Message.chat_id.in_(chat_ids)))
        chat_message_ids = [int(message_id) for message_id in rows.scalars().all() if message_id is not None]

    return sorted(set(target_message_ids).union(chat_message_ids))


async def _collect_chat_file_rows(db, target_ids: list[int]):
    if not target_ids:
        return []
    stmt = select(ChatFile).where(ChatFile.uploader_id.in_(target_ids)).order_by(ChatFile.id.asc())
    return list((await db.execute(stmt)).scalars().all())


async def _collect_upload_paths(db, target_ids: list[int]) -> list[str]:
    if not target_ids:
        return []
    rows = await db.execute(
        select(UploadSession.temp_storage_path).where(
            or_(
                UploadSession.owner_user_id.in_(target_ids),
                UploadSession.actor_user_id.in_(target_ids),
            )
        )
    )
    paths = [str(path) for path in rows.scalars().all() if path]
    return paths


async def _collect_redis_keys(target_users: list[User]) -> list[str]:
    keys: list[str] = []
    if redis is None:
        return keys
    client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        for user in target_users:
            keys.extend([
                f"otp_limit:{user.mobile_number}",
                f"banned:{user.mobile_number}",
            ])
            async for key in client.scan_iter(f"session_req:{user.id}:*"):
                keys.append(key)
    finally:
        await client.aclose()
    return keys


async def _delete_rows(db, target_ids: list[int], chat_ids: list[int], message_ids: list[int], chat_file_ids: list[str]) -> None:
    if target_ids:
        await db.execute(delete(Notification).where(Notification.user_id.in_(target_ids)))
        await db.execute(delete(UserBlock).where(or_(UserBlock.blocker_id.in_(target_ids), UserBlock.blocked_id.in_(target_ids))))
        await db.execute(delete(SessionLoginRequest).where(SessionLoginRequest.user_id.in_(target_ids)))
        await db.execute(delete(SingleSessionRecoveryRequest).where(SingleSessionRecoveryRequest.user_id.in_(target_ids)))
        await db.execute(delete(UserSession).where(UserSession.user_id.in_(target_ids)))
        await db.execute(delete(Invitation).where(Invitation.created_by_id.in_(target_ids)))
        await db.execute(delete(AdminMarketMessage).where(AdminMarketMessage.created_by_id.in_(target_ids)))
        await db.execute(delete(AdminBroadcastMessage).where(AdminBroadcastMessage.created_by_id.in_(target_ids)))
        await db.execute(delete(MarketScheduleOverride).where(MarketScheduleOverride.created_by_user_id.in_(target_ids)))
        await db.execute(
            delete(AccountantRelation).where(
                or_(
                    AccountantRelation.owner_user_id.in_(target_ids),
                    AccountantRelation.accountant_user_id.in_(target_ids),
                    AccountantRelation.created_by_user_id.in_(target_ids),
                )
            )
        )
        await db.execute(
            delete(CustomerRelation).where(
                or_(
                    CustomerRelation.owner_user_id.in_(target_ids),
                    CustomerRelation.customer_user_id.in_(target_ids),
                    CustomerRelation.created_by_user_id.in_(target_ids),
                )
            )
        )
        await db.execute(
            delete(Offer).where(or_(Offer.user_id.in_(target_ids), Offer.actor_user_id.in_(target_ids)))
        )
        await db.execute(
            delete(Trade).where(
                or_(
                    Trade.offer_user_id.in_(target_ids),
                    Trade.responder_user_id.in_(target_ids),
                    Trade.actor_user_id.in_(target_ids),
                )
            )
        )
        await db.execute(delete(UploadBatch).where(or_(UploadBatch.owner_user_id.in_(target_ids), UploadBatch.actor_user_id.in_(target_ids))))
        await db.execute(delete(UploadSession).where(or_(UploadSession.owner_user_id.in_(target_ids), UploadSession.actor_user_id.in_(target_ids))))
        await db.execute(delete(Conversation).where(or_(Conversation.user1_id.in_(target_ids), Conversation.user2_id.in_(target_ids))))
        if message_ids:
            await db.execute(update(Conversation).where(Conversation.last_message_id.in_(message_ids)).values(last_message_id=None))
            await db.execute(
                update(Chat).where(Chat.last_message_id.in_(message_ids)).values(last_message_id=None)
            )
            await db.execute(
                update(Chat).where(Chat.pinned_message_id.in_(message_ids)).values(
                    pinned_message_id=None,
                    pinned_message_at=None,
                )
            )
            await db.execute(
                update(ChatMember).where(ChatMember.last_read_message_id.in_(message_ids)).values(
                    last_read_message_id=None,
                    last_read_at=None,
                )
            )
        if chat_file_ids:
            await db.execute(delete(ChatFile).where(ChatFile.id.in_(chat_file_ids)))
        if message_ids:
            await db.execute(delete(Message).where(Message.id.in_(message_ids)))
        if chat_ids:
            await db.execute(delete(ChatMember).where(or_(ChatMember.chat_id.in_(chat_ids), ChatMember.user_id.in_(target_ids))))
            await db.execute(delete(Chat).where(Chat.id.in_(chat_ids)))
        await db.execute(delete(Notification).where(Notification.user_id.in_(target_ids)))
        await db.execute(delete(User).where(User.id.in_(target_ids)))


def _remove_path(path: str) -> bool:
    try:
        p = Path(path)
        if p.is_file() or p.is_symlink():
            p.unlink(missing_ok=True)
            return True
        if p.is_dir():
            shutil.rmtree(p)
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return False


def _cleanup_upload_dirs(user_ids: Iterable[int]) -> int:
    removed = 0
    for user_id in user_ids:
        for base in (CHAT_FILES_ROOT / str(user_id), CHAT_SESSIONS_ROOT / str(user_id)):
            if _remove_path(str(base)):
                removed += 1
    return removed


async def main() -> None:
    args = parse_args()
    prefixes = _normalize_prefixes(args.prefix)
    start, end = utc_day_bounds()

    async with AsyncSessionLocal() as db:
        target_users = await _collect_targets(db, start, end, prefixes, args.include_all_today)
        if not target_users:
            print("No matching users found.")
            return

        target_ids = [user.id for user in target_users]
        chat_ids = await _collect_chat_ids(db, target_ids)
        message_ids = await _collect_message_ids(db, target_ids, chat_ids)
        chat_file_rows = await _collect_chat_file_rows(db, target_ids)
        chat_file_ids = [row.id for row in chat_file_rows]
        upload_paths = await _collect_upload_paths(db, target_ids)
        redis_keys = await _collect_redis_keys(target_users)

        print(f"Targets: {len(target_users)} users")
        for user in target_users[:20]:
            print(f"  - {user.id} {user.account_name} {user.mobile_number}")
        if len(target_users) > 20:
            print(f"  ... and {len(target_users) - 20} more")
        print(f"Chats to delete: {len(chat_ids)}")
        print(f"Messages to delete: {len(message_ids)}")
        print(f"Chat files to delete: {len(chat_file_ids)}")
        print(f"Upload temp files to delete: {len(upload_paths)}")
        print(f"Redis keys to delete: {len(set(redis_keys))}")

        if not args.execute:
            print("Dry run only. Re-run with --execute to perform the purge.")
            return

        await _delete_rows(db, target_ids, chat_ids, message_ids, chat_file_ids)
        await db.commit()

        removed_files = 0
        for path in chat_file_rows:
            removed_files += 1 if _remove_path(path.s3_key) else 0
        for path in upload_paths:
            removed_files += 1 if _remove_path(path) else 0
        removed_dirs = _cleanup_upload_dirs(target_ids)
        redis_deleted = await _redis_delete_keys(redis_keys)

        print(f"✅ Hard-deleted users: {len(target_users)}")
        print(f"✅ Removed file paths: {removed_files}")
        print(f"✅ Removed upload dirs: {removed_dirs}")
        print(f"✅ Deleted Redis keys: {redis_deleted}")


if __name__ == "__main__":
    asyncio.run(main())
