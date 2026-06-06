#!/usr/bin/env python3
"""Developer admin CLI for local/ops user and session maintenance.

Examples:
  python scripts/dev_admin.py create-superadmin 09120000000 "مدیر ارشد" --password 'TempPass123'
  python scripts/dev_admin.py create-admin 09120000001 "مدیر میانی" --password 'TempPass123'
  python scripts/dev_admin.py create-user 09120000002 "کاربر تست" --role standard
  python scripts/dev_admin.py change-password 09120000000 --password 'NewPass123'
  python scripts/dev_admin.py reset-sessions 09120000000
  python scripts/dev_admin.py show-user 09120000000
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path
from typing import Iterable

from sqlalchemy import delete, func, or_, select

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from core.db import AsyncSessionLocal, init_db
from core.enums import UserAccountStatus
from core.redis import pool
from core.security import get_password_hash
from core.services.chat_room_service import (
    ensure_mandatory_channel_membership,
    sync_mandatory_channel_for_user_state_change,
)
from core.services.session_service import force_clear_sessions
from models.session import (
    SessionLoginRequest,
    SingleSessionRecoveryRequest,
    UserSession,
)
from models.user import User, UserRole, set_legacy_has_bot_access_compatibility

try:
    import redis.asyncio as redis
except Exception:  # pragma: no cover - dependency exists in app runtime
    redis = None


ROLE_ALIASES = {
    "watch": UserRole.WATCH,
    "تماشا": UserRole.WATCH,
    "standard": UserRole.STANDARD,
    "user": UserRole.STANDARD,
    "normal": UserRole.STANDARD,
    "عادی": UserRole.STANDARD,
    "police": UserRole.POLICE,
    "پلیس": UserRole.POLICE,
    "middle": UserRole.MIDDLE_MANAGER,
    "middle-admin": UserRole.MIDDLE_MANAGER,
    "middle_manager": UserRole.MIDDLE_MANAGER,
    "مدیر میانی": UserRole.MIDDLE_MANAGER,
    "super": UserRole.SUPER_ADMIN,
    "superadmin": UserRole.SUPER_ADMIN,
    "super-admin": UserRole.SUPER_ADMIN,
    "مدیر ارشد": UserRole.SUPER_ADMIN,
}

ADMIN_ROLES = {UserRole.SUPER_ADMIN, UserRole.MIDDLE_MANAGER}


def prompt_text(label: str, current: str | None = None, *, required: bool = True, default: str | None = None) -> str | None:
    if current not in (None, ""):
        return current
    suffix = f" [{default}]" if default not in (None, "") else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default not in (None, ""):
            return default
        if not required:
            return None
        print("This value is required.")


def prompt_int(label: str, current: int | None = None, *, default: int | None = None, minimum: int | None = None, maximum: int | None = None) -> int:
    if current is not None:
        return current
    while True:
        raw = prompt_text(label, default=str(default) if default is not None else None)
        try:
            value = int(raw or "")
        except ValueError:
            print("Enter a valid number.")
            continue
        if minimum is not None and value < minimum:
            print(f"Value must be at least {minimum}.")
            continue
        if maximum is not None and value > maximum:
            print(f"Value must be at most {maximum}.")
            continue
        return value


def prompt_bool(label: str, current: bool | None = None, *, default: bool = False) -> bool:
    if current is not None:
        return current
    default_label = "Y/n" if default else "y/N"
    while True:
        value = input(f"{label} [{default_label}]: ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes", "1", "true", "بله"}:
            return True
        if value in {"n", "no", "0", "false", "خیر"}:
            return False
        print("Enter yes or no.")


def parse_role(value: str) -> UserRole:
    normalized = value.strip().lower()
    role = ROLE_ALIASES.get(normalized)
    if role is None:
        supported = ", ".join(sorted(ROLE_ALIASES))
        raise argparse.ArgumentTypeError(f"Unsupported role '{value}'. Supported: {supported}")
    return role


def parse_status(value: str) -> UserAccountStatus:
    normalized = value.strip().lower()
    if normalized in {"active", "فعال"}:
        return UserAccountStatus.ACTIVE
    if normalized in {"inactive", "disabled", "غیرفعال"}:
        return UserAccountStatus.INACTIVE
    raise argparse.ArgumentTypeError("Status must be active or inactive")


def prompt_password_if_needed(password: str | None, *, required: bool) -> str | None:
    if password:
        return password
    if not required:
        return None
    first = getpass.getpass("Temporary/admin password: ")
    second = getpass.getpass("Repeat password: ")
    if first != second:
        raise SystemExit("❌ Passwords do not match.")
    if len(first) < 6:
        raise SystemExit("❌ Password must be at least 6 characters.")
    return first


def prompt_role_if_needed(role: UserRole | None, *, default: UserRole = UserRole.STANDARD) -> UserRole:
    if role is not None:
        return role
    print("Role options: standard, middle, super, watch, police")
    while True:
        raw = prompt_text("Role", default=role_value(default))
        try:
            return parse_role(raw or role_value(default))
        except argparse.ArgumentTypeError as exc:
            print(exc)


def prompt_status_if_needed(status: UserAccountStatus | None) -> UserAccountStatus:
    if status is not None:
        return status
    while True:
        raw = prompt_text("Status", default="active")
        try:
            return parse_status(raw or "active")
        except argparse.ArgumentTypeError as exc:
            print(exc)


def account_name_from_name(name: str, mobile: str) -> str:
    base = "_".join(part for part in name.strip().split() if part)
    return base or f"user_{mobile}"


def role_value(role: UserRole | str | None) -> str:
    return getattr(role, "value", str(role or ""))


def print_user(user: User) -> None:
    print(
        " | ".join(
            [
                f"id={user.id}",
                f"mobile={user.mobile_number}",
                f"account={user.account_name}",
                f"name={user.full_name}",
                f"role={role_value(user.role)}",
                f"status={getattr(user.account_status, 'value', user.account_status)}",
                f"deleted={bool(user.is_deleted)}",
                f"must_change_password={bool(user.must_change_password)}",
                f"max_sessions={user.max_sessions}",
                f"home_server={user.home_server}",
            ]
        )
    )


async def find_user(db, identity: str) -> User | None:
    if identity.isdigit():
        user = await db.get(User, int(identity))
        if user:
            return user
    stmt = select(User).where(
        or_(
            User.mobile_number == identity,
            User.account_name == identity,
            User.username == identity,
        )
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def require_user(db, identity: str) -> User:
    user = await find_user(db, identity)
    if user is None:
        raise SystemExit(f"❌ User not found: {identity}")
    return user


async def create_user(args) -> None:
    interactive = args.mobile is None or args.name is None
    args.mobile = prompt_text("Mobile number", args.mobile)
    args.name = prompt_text("Full name", args.name)
    if args.role is None:
        args.role = prompt_role_if_needed(None)
    if interactive:
        args.account_name = prompt_text(
            "Account name",
            args.account_name,
            required=False,
            default=account_name_from_name(args.name, args.mobile),
        )
        args.home_server = prompt_text("Home server", None, default=args.home_server or "foreign")
        args.max_sessions = prompt_int("Max sessions", None, default=args.max_sessions, minimum=1, maximum=3)
        args.max_accountants = prompt_int("Max accountants", None, default=args.max_accountants, minimum=0)
        args.max_customers = prompt_int("Max customers", None, default=args.max_customers, minimum=0)
        args.bot_access = prompt_bool("Bot access", None, default=args.bot_access)

    role = args.role
    password_required = role in ADMIN_ROLES
    password = prompt_password_if_needed(args.password or args.password_arg, required=password_required)
    if interactive and role in ADMIN_ROLES:
        args.must_change_password = prompt_bool(
            "Force password change on next login",
            None,
            default=True,
        )
    if interactive and role == UserRole.SUPER_ADMIN:
        args.allow_multiple_superadmins = prompt_bool(
            "Allow creating another super admin if one already exists",
            None,
            default=False,
        )

    await init_db()
    async with AsyncSessionLocal() as db:
        existing_stmt = select(User).where(
            or_(
                User.mobile_number == args.mobile,
                User.account_name == (args.account_name or account_name_from_name(args.name, args.mobile)),
            )
        )
        existing = (await db.execute(existing_stmt)).scalar_one_or_none()
        if existing:
            print("❌ User already exists:")
            print_user(existing)
            return

        if role == UserRole.SUPER_ADMIN and not args.allow_multiple_superadmins:
            admin_exists = (
                await db.execute(select(func.count(User.id)).where(User.role == UserRole.SUPER_ADMIN))
            ).scalar_one()
            if admin_exists:
                print("❌ A SUPER_ADMIN already exists. Pass --allow-multiple-superadmins to override.")
                return

        must_change_password = args.must_change_password
        if must_change_password is None:
            must_change_password = role in ADMIN_ROLES

        user = User(
            account_name=args.account_name or account_name_from_name(args.name, args.mobile),
            mobile_number=args.mobile,
            telegram_id=args.telegram_id,
            username=args.username,
            full_name=args.name,
            address=args.address,
            role=role,
            account_status=UserAccountStatus.ACTIVE,
            admin_password_hash=get_password_hash(password) if password else None,
            must_change_password=bool(must_change_password and role in ADMIN_ROLES),
            max_sessions=args.max_sessions,
            max_accountants=args.max_accountants,
            max_customers=args.max_customers,
            home_server=args.home_server,
        )
        set_legacy_has_bot_access_compatibility(user, enabled=args.bot_access)

        db.add(user)
        await db.flush()
        await ensure_mandatory_channel_membership(db, user=user)
        await db.commit()
        await db.refresh(user)
        print("✅ User created.")
        print_user(user)


async def list_users(args) -> None:
    await init_db()
    async with AsyncSessionLocal() as db:
        stmt = select(User)
        if args.role:
            stmt = stmt.where(User.role == args.role)
        if args.status:
            stmt = stmt.where(User.account_status == args.status)
        if not args.include_deleted:
            stmt = stmt.where(User.is_deleted == False)
        if args.search:
            pattern = f"%{args.search}%"
            stmt = stmt.where(
                or_(
                    User.mobile_number.ilike(pattern),
                    User.account_name.ilike(pattern),
                    User.full_name.ilike(pattern),
                    User.username.ilike(pattern),
                )
            )
        stmt = stmt.order_by(User.id.asc()).limit(args.limit)
        users = list((await db.execute(stmt)).scalars().all())
        if not users:
            print("No users found.")
            return
        for user in users:
            print_user(user)


async def show_user(args) -> None:
    args.identity = prompt_text("User id/mobile/account_name/username", args.identity)
    await init_db()
    async with AsyncSessionLocal() as db:
        user = await require_user(db, args.identity)
        print_user(user)
        active_sessions = (
            await db.execute(
                select(func.count(UserSession.id)).where(
                    UserSession.user_id == user.id,
                    UserSession.is_active == True,
                )
            )
        ).scalar_one()
        pending_requests = (
            await db.execute(
                select(func.count(SessionLoginRequest.id)).where(
                    SessionLoginRequest.user_id == user.id,
                )
            )
        ).scalar_one()
        print(f"active_sessions={active_sessions} | login_requests={pending_requests}")


async def change_password(args) -> None:
    args.identity = prompt_text("User id/mobile/account_name/username", args.identity)
    password = prompt_password_if_needed(args.password, required=True)
    args.must_change_password = prompt_bool(
        "Force password change on next login",
        args.must_change_password,
        default=False,
    )
    await init_db()
    async with AsyncSessionLocal() as db:
        user = await require_user(db, args.identity)
        if user.role not in ADMIN_ROLES:
            print("❌ Local admin password exists only for SUPER_ADMIN and MIDDLE_MANAGER users.")
            return
        user.admin_password_hash = get_password_hash(password or "")
        user.must_change_password = args.must_change_password
        await db.commit()
        print("✅ Admin password updated.")
        print_user(user)


async def force_password_change(args) -> None:
    args.identity = prompt_text("User id/mobile/account_name/username", args.identity)
    await init_db()
    async with AsyncSessionLocal() as db:
        user = await require_user(db, args.identity)
        if user.role not in ADMIN_ROLES:
            print("❌ must_change_password applies only to admin users.")
            return
        user.must_change_password = True
        await db.commit()
        print("✅ User must change password on next login.")
        print_user(user)


async def set_role(args) -> None:
    interactive = args.identity is None or args.role is None
    args.identity = prompt_text("User id/mobile/account_name/username", args.identity)
    args.role = prompt_role_if_needed(args.role)
    if interactive and args.role in ADMIN_ROLES and not args.password:
        wants_password = prompt_bool("Set/update admin password now", default=True)
        if wants_password:
            args.password = prompt_password_if_needed(None, required=True)
            args.must_change_password = prompt_bool(
                "Force password change on next login",
                args.must_change_password,
                default=True,
            )
    await init_db()
    async with AsyncSessionLocal() as db:
        user = await require_user(db, args.identity)
        old_role = user.role
        user.role = args.role
        if args.role not in ADMIN_ROLES:
            user.must_change_password = False
            user.admin_password_hash = None
        elif args.password:
            user.admin_password_hash = get_password_hash(args.password)
            user.must_change_password = args.must_change_password
        await sync_mandatory_channel_for_user_state_change(db, user=user, previous_role=old_role)
        await db.commit()
        print(f"✅ Role updated: {role_value(old_role)} -> {role_value(user.role)}")
        print_user(user)


async def set_status(args) -> None:
    args.identity = prompt_text("User id/mobile/account_name/username", args.identity)
    args.status = prompt_status_if_needed(args.status)
    await init_db()
    async with AsyncSessionLocal() as db:
        user = await require_user(db, args.identity)
        user.account_status = args.status
        await db.commit()
        print("✅ Account status updated.")
        print_user(user)


async def set_max_sessions(args) -> None:
    args.identity = prompt_text("User id/mobile/account_name/username", args.identity)
    args.max_sessions = prompt_int("Max sessions", args.max_sessions, default=1, minimum=1, maximum=3)
    if args.max_sessions < 1 or args.max_sessions > 3:
        raise SystemExit("❌ max_sessions must be between 1 and 3.")
    await init_db()
    async with AsyncSessionLocal() as db:
        user = await require_user(db, args.identity)
        user.max_sessions = args.max_sessions
        await db.commit()
        print("✅ max_sessions updated.")
        print_user(user)


async def redis_delete_keys(keys: Iterable[str]) -> int:
    if redis is None:
        return 0
    client = redis.Redis(connection_pool=pool, decode_responses=True)
    try:
        actual = []
        for key in sorted(set(keys)):
            if await client.exists(key):
                actual.append(key)
        if actual:
            await client.delete(*actual)
        return len(actual)
    finally:
        await client.close()


async def collect_login_limit_keys(user: User) -> list[str]:
    keys = [
        f"otp_limit:{user.mobile_number}",
        f"banned:{user.mobile_number}",
    ]
    if redis is None:
        return keys
    client = redis.Redis(connection_pool=pool, decode_responses=True)
    try:
        async for key in client.scan_iter(f"session_req:{user.id}:*"):
            keys.append(key)
    finally:
        await client.close()
    return keys


async def reset_sessions(args) -> None:
    interactive = args.identity is None
    args.identity = prompt_text("User id/mobile/account_name/username", args.identity)
    if args.delete_session_rows is None:
        args.delete_session_rows = (
            prompt_bool("Delete session rows from database", default=True) if interactive else True
        )
    if args.clear_login_limits is None:
        args.clear_login_limits = (
            prompt_bool("Clear Redis login/OTP limits", default=True) if interactive else True
        )
    await init_db()
    async with AsyncSessionLocal() as db:
        user = await require_user(db, args.identity)
        print(f"Resetting sessions for {user.full_name or user.account_name} ({user.mobile_number})")
        cleared = await force_clear_sessions(db, user.id)

        async with AsyncSessionLocal() as cleanup_db:
            await cleanup_db.execute(delete(SessionLoginRequest).where(SessionLoginRequest.user_id == user.id))
            await cleanup_db.execute(delete(SingleSessionRecoveryRequest).where(SingleSessionRecoveryRequest.user_id == user.id))
            if args.delete_session_rows:
                await cleanup_db.execute(delete(UserSession).where(UserSession.user_id == user.id))
            await cleanup_db.commit()

        redis_deleted = 0
        if args.clear_login_limits:
            redis_deleted = await redis_delete_keys(await collect_login_limit_keys(user))

        print(f"✅ Revoked active sessions: {cleared}")
        print(f"✅ Deleted Redis login/OTP keys: {redis_deleted}")
        if args.delete_session_rows:
            print("✅ Deleted session rows from database.")


async def unlock_login(args) -> None:
    args.identity = prompt_text("User id/mobile/account_name/username", args.identity)
    await init_db()
    async with AsyncSessionLocal() as db:
        user = await require_user(db, args.identity)
        deleted = await redis_delete_keys(await collect_login_limit_keys(user))
        await db.execute(delete(SessionLoginRequest).where(SessionLoginRequest.user_id == user.id))
        await db.execute(delete(SingleSessionRecoveryRequest).where(SingleSessionRecoveryRequest.user_id == user.id))
        await db.commit()
        print(f"✅ Login limits and pending login/recovery requests cleared. Redis keys deleted: {deleted}")


def add_create_user_parser(subparsers, name: str, *, role: UserRole | None = None):
    parser = subparsers.add_parser(name)
    parser.add_argument("mobile", nargs="?")
    parser.add_argument("name", nargs="?")
    parser.add_argument("password_arg", nargs="?")
    parser.add_argument("--account-name")
    parser.add_argument("--username")
    parser.add_argument("--telegram-id", type=int)
    parser.add_argument("--address", default="System Default")
    parser.add_argument("--password")
    parser.add_argument("--home-server", default="foreign", choices=["foreign", "iran"])
    parser.add_argument("--max-sessions", type=int, default=1)
    parser.add_argument("--max-accountants", type=int, default=3)
    parser.add_argument("--max-customers", type=int, default=5)
    parser.add_argument("--allow-multiple-superadmins", action="store_true")
    parser.add_argument("--bot-access", dest="bot_access", action="store_true", default=True)
    parser.add_argument("--no-bot-access", dest="bot_access", action="store_false")
    parser.add_argument("--must-change-password", dest="must_change_password", action="store_true", default=None)
    parser.add_argument("--no-must-change-password", dest="must_change_password", action="store_false")
    if role is None:
        parser.add_argument("--role", type=parse_role, default=UserRole.STANDARD)
    else:
        parser.set_defaults(role=role)
    parser.set_defaults(func=create_user)
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Developer admin CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_create_user_parser(subparsers, "create-user")
    add_create_user_parser(subparsers, "create-superadmin", role=UserRole.SUPER_ADMIN)
    add_create_user_parser(subparsers, "create-admin", role=UserRole.MIDDLE_MANAGER)
    add_create_user_parser(subparsers, "create-middle-admin", role=UserRole.MIDDLE_MANAGER)

    list_parser = subparsers.add_parser("list-users")
    list_parser.add_argument("--role", type=parse_role)
    list_parser.add_argument("--status", type=parse_status)
    list_parser.add_argument("--search")
    list_parser.add_argument("--include-deleted", action="store_true")
    list_parser.add_argument("--limit", type=int, default=50)
    list_parser.set_defaults(func=list_users)

    show_parser = subparsers.add_parser("show-user")
    show_parser.add_argument("identity", nargs="?", help="id, mobile, account_name, or username")
    show_parser.set_defaults(func=show_user)

    pass_parser = subparsers.add_parser("change-password")
    pass_parser.add_argument("identity", nargs="?")
    pass_parser.add_argument("--password")
    pass_parser.add_argument("--must-change-password", action="store_true", default=False)
    pass_parser.set_defaults(func=change_password)

    force_pass_parser = subparsers.add_parser("force-password-change")
    force_pass_parser.add_argument("identity", nargs="?")
    force_pass_parser.set_defaults(func=force_password_change)

    role_parser = subparsers.add_parser("set-role")
    role_parser.add_argument("identity", nargs="?")
    role_parser.add_argument("role", nargs="?", type=parse_role)
    role_parser.add_argument("--password")
    role_parser.add_argument("--must-change-password", action="store_true", default=True)
    role_parser.set_defaults(func=set_role)

    status_parser = subparsers.add_parser("set-status")
    status_parser.add_argument("identity", nargs="?")
    status_parser.add_argument("status", nargs="?", type=parse_status)
    status_parser.set_defaults(func=set_status)

    max_sessions_parser = subparsers.add_parser("set-max-sessions")
    max_sessions_parser.add_argument("identity", nargs="?")
    max_sessions_parser.add_argument("max_sessions", nargs="?", type=int)
    max_sessions_parser.set_defaults(func=set_max_sessions)

    reset_parser = subparsers.add_parser("reset-sessions")
    reset_parser.add_argument("identity", nargs="?")
    reset_parser.add_argument("--keep-session-rows", dest="delete_session_rows", action="store_false", default=None)
    reset_parser.add_argument("--keep-login-limits", dest="clear_login_limits", action="store_false", default=None)
    reset_parser.set_defaults(func=reset_sessions)

    unlock_parser = subparsers.add_parser("unlock-login")
    unlock_parser.add_argument("identity", nargs="?")
    unlock_parser.set_defaults(func=unlock_login)

    return parser


async def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    await args.func(args)


if __name__ == "__main__":
    asyncio.run(main())
