#!/usr/bin/env python3
"""Run one bounded WebApp Messenger upload/download regression on the Writer.

The probe deliberately uses the application's upload, direct-message and
download authorization paths.  It never calls Telegram, browser push, or an
external realtime provider.  The only fixture is three short-lived synthetic
WebApp users and one content-addressed blob.  After the rows are removed the
encrypted, versioned blob is left to the normal DR retention workflow: this
probe must never delete an Object Storage version or a host volume directly.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import hashlib
import io
import json
from pathlib import Path
import re
import sys
from typing import Any

from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers
from sqlalchemy import func, or_, select, text


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.routers import chat as chat_router  # noqa: E402
from api.routers.chat_schemas import MessageSend  # noqa: E402
from core.config import settings  # noqa: E402
from core.enums import MessageType  # noqa: E402
from core.runtime_identity import resolve_runtime_identity  # noqa: E402
from core.server_routing import SERVER_IRAN  # noqa: E402
from core.security import create_access_token  # noqa: E402
from core.webapp_writer_control import load_writer_snapshot, snapshot_is_local_active  # noqa: E402
from core.writer_fencing import writer_fence_scope  # noqa: E402
from models.chat import Chat  # noqa: E402
from models.chat_file import ChatFile  # noqa: E402
from models.chat_member import ChatMember  # noqa: E402
from models.conversation import Conversation  # noqa: E402
from models.dr_event import DrBlobDelivery, DrBlobManifest, DrEffectFanout, DrFileIntent  # noqa: E402
from models.message import Message  # noqa: E402
from models.notification import Notification  # noqa: E402
from models.upload_session import UploadBatch, UploadSession  # noqa: E402
from models.user import User  # noqa: E402
from scripts import trading_core_probe_worker as worker  # noqa: E402
from core.utils import create_user_notification  # noqa: E402


SCHEMA = "three-site-full-matrix-messenger-regression-probe-v1"
PREFIX_RE = re.compile(r"FMX_[A-Za-z0-9_]{12,96}")
SCENARIO_IDS = frozenset(
    {
        "messenger_upload_download_regression",
        "notifications_webpush_messenger_files",
    }
)


class MessengerRegressionProbeError(RuntimeError):
    """The bounded Messenger regression could not prove its live contract."""


def _json(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def _statement(statement):
    """Use the production-approved, explicitly scoped cleanup capability."""

    return worker.cleanup_mutating_statement(statement)


def _safe_prefix(prefix: str) -> str:
    normalized = str(prefix or "").strip()
    if PREFIX_RE.fullmatch(normalized) is None:
        raise MessengerRegressionProbeError("messenger fixture prefix is unsafe")
    return normalized


@asynccontextmanager
async def _writer_capability():
    identity = resolve_runtime_identity(settings)
    if not identity.is_webapp_authority or identity.physical_site != "webapp_fi":
        raise MessengerRegressionProbeError("messenger regression must run on WebApp-FI")
    # Both physical WebApp replicas deliberately retain the logical WebApp
    # authority (the legacy ``iran`` server mode).  Physical location is
    # carried by ``PHYSICAL_SITE`` and the Witness-bound writer state, not by
    # changing the business authority to Bot-FI/``foreign``.
    if str(getattr(settings, "server_mode", "") or "") != SERVER_IRAN:
        raise MessengerRegressionProbeError("messenger regression server mode differs from WebApp authority")
    async with worker.AsyncSessionLocal() as db:
        snapshot = await load_writer_snapshot(db)
        await db.rollback()
    active, reasons = snapshot_is_local_active(
        identity,
        snapshot,
        require_witness_lease=True,
    )
    if not active:
        raise MessengerRegressionProbeError(
            "messenger regression requires the Witness-leased WebApp-FI Writer"
        )
    with writer_fence_scope(
        identity,
        snapshot,
        source="three_site_full_matrix_messenger_regression",
        require_witness_lease=True,
    ):
        yield {
            "writer_epoch": int(snapshot.writer_epoch),
            "transition_id": str(snapshot.transition_id),
        }


async def _prefixed_user_ids(db, prefix: str) -> list[int]:
    rows = await db.execute(
        select(User.id).where(User.account_name.like(f"{prefix}%"))
    )
    return [int(value) for value in rows.scalars().all()]


async def _cleanup(prefix: str) -> dict[str, Any]:
    """Remove only the synthetic relational graph; retain the DR blob version.

    Deleting a ``ChatFile`` cascades its file intent.  Its immutable blob
    manifest/delivery are intentionally *not* deleted, because another worker
    may already have published the encrypted version or be acknowledging it.
    Normal tombstone/retention processing owns that lifecycle.
    """

    deleted: dict[str, int] = {}
    async with worker.AsyncSessionLocal() as db:
        user_ids = await _prefixed_user_ids(db, prefix)
        if not user_ids:
            return {
                "deleted": deleted,
                "residue_zero": True,
                "retained_blob_manifests": 0,
            }

        chat_ids = {
            int(value)
            for value in (
                await db.execute(
                    select(Chat.id).where(Chat.created_by_id.in_(user_ids))
                )
            ).scalars().all()
        }
        chat_ids.update(
            int(value)
            for value in (
                await db.execute(
                    select(ChatMember.chat_id).where(ChatMember.user_id.in_(user_ids))
                )
            ).scalars().all()
        )
        ordered_chat_ids = sorted(chat_ids)
        message_ids = {
            int(value)
            for value in (
                await db.execute(
                    select(Message.id).where(
                        or_(
                            Message.sender_id.in_(user_ids),
                            Message.receiver_id.in_(user_ids),
                            Message.actor_user_id.in_(user_ids),
                            Message.forwarded_from_id.in_(user_ids),
                            Message.chat_id.in_(ordered_chat_ids or [-1]),
                        )
                    )
                )
            ).scalars().all()
        }
        ordered_message_ids = sorted(message_ids)
        chat_file_rows = list(
            (
                await db.execute(
                    select(ChatFile).where(
                        ChatFile.uploader_id.in_(user_ids)
                    )
                )
            ).scalars().all()
        )
        chat_file_ids = [str(row.id) for row in chat_file_rows]
        content_hashes = sorted(
            {str(row.content_hash) for row in chat_file_rows if row.content_hash}
        )
        notification_rows = list(
            (
                await db.execute(
                    select(Notification).where(
                        or_(
                            Notification.user_id.in_(user_ids),
                            Notification.message.contains(prefix),
                            Notification.dedupe_key.contains(prefix),
                        )
                    )
                )
            ).scalars().all()
        )
        notification_ids = [int(row.id) for row in notification_rows]
        users = list(
            (
                await db.execute(select(User).where(User.id.in_(user_ids)))
            ).scalars().all()
        )
        chats = list(
            (
                await db.execute(select(Chat).where(Chat.id.in_(ordered_chat_ids or [-1])))
            ).scalars().all()
        )
        conversations = list(
            (
                await db.execute(
                    select(Conversation).where(
                        or_(
                            Conversation.user1_id.in_(user_ids),
                            Conversation.user2_id.in_(user_ids),
                        )
                    )
                )
            ).scalars().all()
        )
        chat_members = list(
            (
                await db.execute(
                    select(ChatMember).where(
                        or_(
                            ChatMember.chat_id.in_(ordered_chat_ids or [-1]),
                            ChatMember.user_id.in_(user_ids),
                        )
                    )
                )
            ).scalars().all()
        )
        messages = list(
            (
                await db.execute(select(Message).where(Message.id.in_(ordered_message_ids or [-1])))
            ).scalars().all()
        )
        upload_sessions = list(
            (
                await db.execute(
                    select(UploadSession).where(
                        or_(
                            UploadSession.owner_user_id.in_(user_ids),
                            UploadSession.actor_user_id.in_(user_ids),
                        )
                    )
                )
            ).scalars().all()
        )
        upload_batches = list(
            (
                await db.execute(
                    select(UploadBatch).where(
                        or_(
                            UploadBatch.owner_user_id.in_(user_ids),
                            UploadBatch.actor_user_id.in_(user_ids),
                        )
                    )
                )
            ).scalars().all()
        )
        foreign_file_reference = await db.scalar(
            select(Chat.id).where(
                Chat.avatar_file_id.in_(chat_file_ids or [""]),
                ~Chat.id.in_(ordered_chat_ids or [-1]),
            ).limit(1)
        )
        if foreign_file_reference is not None:
            raise MessengerRegressionProbeError("synthetic chat file has an unexpected foreign reference")
        foreign_user_file_reference = await db.scalar(
            select(User.id).where(
                User.avatar_file_id.in_(chat_file_ids or [""]),
                ~User.id.in_(user_ids),
            ).limit(1)
        )
        if foreign_user_file_reference is not None:
            raise MessengerRegressionProbeError("synthetic chat file has an unexpected foreign user reference")

        message_id_set = set(ordered_message_ids)
        file_id_set = set(chat_file_ids)
        for conversation in conversations:
            if conversation.last_message_id in message_id_set:
                conversation.last_message_id = None
                conversation.last_message_at = None
        for chat in chats:
            if chat.last_message_id in message_id_set:
                chat.last_message_id = None
                chat.last_message_at = None
            if chat.pinned_message_id in message_id_set:
                chat.pinned_message_id = None
                chat.pinned_message_at = None
                chat.pinned_message_by_id = None
            if chat.avatar_file_id in file_id_set:
                chat.avatar_file_id = None
        for member in chat_members:
            if member.last_read_message_id in message_id_set:
                member.last_read_message_id = None
                member.last_read_at = None
        for user in users:
            if user.avatar_file_id in file_id_set:
                user.avatar_file_id = None

        for name, rows in (
            ("notifications", notification_rows),
            ("upload_sessions", upload_sessions),
            ("upload_batches", upload_batches),
            ("chat_members", chat_members),
            ("messages", messages),
            ("conversations", conversations),
            ("chats", chats),
            ("chat_files", chat_file_rows),
            ("users", users),
        ):
            for row in rows:
                await db.delete(row)
            deleted[name] = len(rows)

        change_logs = await db.execute(
            _statement(
                text(
                    """
                    DELETE FROM change_log
                    WHERE strpos(data::text, :prefix) > 0
                       OR (table_name = 'messages' AND record_id = ANY(:message_ids))
                       OR (table_name = 'chats' AND record_id = ANY(:chat_ids))
                       OR (table_name = 'notifications' AND record_id = ANY(:notification_ids))
                       OR (table_name = 'users' AND record_id = ANY(:user_ids))
                    """
                )
            ),
            {
                "prefix": prefix,
                "message_ids": ordered_message_ids or [-1],
                "chat_ids": ordered_chat_ids or [-1],
                "notification_ids": notification_ids or [-1],
                "user_ids": user_ids or [-1],
            },
        )
        deleted["change_logs"] = int(change_logs.rowcount or 0)
        retained_manifests = int(
            await db.scalar(
                select(func.count())
                .select_from(DrBlobManifest)
                .where(DrBlobManifest.content_hash.in_(content_hashes or [""]))
            )
            or 0
        )
        await db.commit()

    await worker.cleanup_redis_for_user_ids(user_ids)
    async with worker.AsyncSessionLocal() as db:
        residue = {
            "users": int(
                await db.scalar(
                    select(func.count()).select_from(User).where(User.account_name.like(f"{prefix}%"))
                )
                or 0
            ),
            "messages": int(
                await db.scalar(
                    select(func.count())
                    .select_from(Message)
                    .where(
                        or_(
                            Message.content.contains(prefix),
                            Message.sender_id.in_(user_ids or [-1]),
                            Message.receiver_id.in_(user_ids or [-1]),
                        )
                    )
                )
                or 0
            ),
            "chat_files": int(
                await db.scalar(
                    select(func.count())
                    .select_from(ChatFile)
                    .where(ChatFile.uploader_id.in_(user_ids or [-1]))
                )
                or 0
            ),
            "notifications": int(
                await db.scalar(
                    select(func.count())
                    .select_from(Notification)
                    .where(
                        or_(
                            Notification.message.contains(prefix),
                            Notification.dedupe_key.contains(prefix),
                        )
                    )
                )
                or 0
            ),
            "upload_sessions": int(
                await db.scalar(
                    select(func.count())
                    .select_from(UploadSession)
                    .where(UploadSession.owner_user_id.in_(user_ids or [-1]))
                )
                or 0
            ),
            "upload_batches": int(
                await db.scalar(
                    select(func.count())
                    .select_from(UploadBatch)
                    .where(UploadBatch.owner_user_id.in_(user_ids or [-1]))
                )
                or 0
            ),
        }
    if any(value != 0 for value in residue.values()):
        raise MessengerRegressionProbeError("messenger cleanup left active fixture rows")
    return {
        "deleted": deleted,
        "residue_zero": True,
        "retained_blob_manifests": retained_manifests,
    }


async def _response_bytes(response: Any) -> bytes:
    chunks: list[bytes] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        if message.get("type") == "http.response.body":
            chunks.append(bytes(message.get("body") or b""))

    await response(
        {"type": "http", "http_version": "1.1", "method": "GET", "path": "/", "headers": []},
        receive,
        send,
    )
    return b"".join(chunks)


async def _run(prefix: str, *, include_notification: bool) -> dict[str, Any]:
    if not bool(getattr(settings, "three_site_dr_enabled", False)) or not bool(
        getattr(settings, "dr_event_protocol_enabled", False)
    ):
        raise MessengerRegressionProbeError("messenger regression requires the strict three-site blob plane")
    users = await worker.create_load_fixture_users(prefix, user_count=3)
    content = hashlib.sha256(f"{prefix}:messenger-content".encode("utf-8")).digest()
    filename = f"{prefix}proof.bin"
    original_publisher = chat_router.publish_user_event
    import core.utils as core_utils

    original_notification_publisher = core_utils.publish_user_event

    async def no_external_realtime(*_args: Any, **_kwargs: Any) -> None:
        return None

    try:
        async with worker.AsyncSessionLocal() as db:
            sender = await db.get(User, users[0].user_id)
            recipient = await db.get(User, users[1].user_id)
            intruder = await db.get(User, users[2].user_id)
            if sender is None or recipient is None or intruder is None:
                raise MessengerRegressionProbeError("messenger fixture users disappeared")
            notification_fanout_created = False
            if include_notification:
                core_utils.publish_user_event = no_external_realtime
                notification = await create_user_notification(
                    db,
                    int(sender.id),
                    f"{prefix}webpush-notification",
                    dedupe_key=f"{prefix}webpush",
                    extra_payload={"route": "/notifications", "kind": "full_matrix"},
                )
                fanout = await db.scalar(
                    select(DrEffectFanout).where(
                        DrEffectFanout.aggregate_type == "notifications",
                        DrEffectFanout.aggregate_db_id == str(notification.id),
                        DrEffectFanout.fanout_type == "notification_webpush",
                    )
                )
                if fanout is None or fanout.status != "pending":
                    raise MessengerRegressionProbeError(
                        "notification did not create a durable Web Push fanout"
                    )
                notification_fanout_created = True

            upload = UploadFile(
                file=io.BytesIO(content),
                filename=filename,
                headers=Headers({"content-type": "application/octet-stream"}),
            )
            uploaded = await chat_router.upload_chat_media(
                file=upload,
                thumbnail=None,
                current_user=sender,
                db=db,
            )
            file_id = str(uploaded.get("file_id") or "")
            if not file_id:
                raise MessengerRegressionProbeError("messenger upload did not return a file id")
            chat_file = await db.get(ChatFile, file_id)
            if (
                chat_file is None
                or chat_file.content_hash != hashlib.sha256(content).hexdigest()
                or int(chat_file.size) != len(content)
            ):
                raise MessengerRegressionProbeError("messenger upload did not bind an immutable blob")
            manifest = await db.get(DrBlobManifest, chat_file.content_hash)
            intent = await db.scalar(
                select(DrFileIntent.intent_id).where(DrFileIntent.chat_file_id == file_id)
            )
            delivery = await db.scalar(
                select(DrBlobDelivery.destination_site).where(
                    DrBlobDelivery.content_hash == chat_file.content_hash
                )
            )
            if manifest is None or not intent or not delivery:
                raise MessengerRegressionProbeError("messenger upload lacks the required DR blob intent")

            chat_router.publish_user_event = no_external_realtime
            sent = await chat_router.send_message(
                data=MessageSend(
                    receiver_id=int(recipient.id),
                    content=json.dumps({"file_id": file_id, "file_name": filename}, sort_keys=True),
                    message_type=MessageType.DOCUMENT,
                ),
                current_user=sender,
                db=db,
            )
            if int(getattr(sent, "id", 0) or 0) <= 0:
                raise MessengerRegressionProbeError("messenger direct file message was not persisted")
            sender_token = create_access_token(subject=sender.id)
            recipient_token = create_access_token(subject=recipient.id)
            intruder_token = create_access_token(subject=intruder.id)
            sender_response = await chat_router.get_chat_file(file_id=file_id, db=db, token=sender_token)
            recipient_response = await chat_router.get_chat_file(file_id=file_id, db=db, token=recipient_token)
            if await _response_bytes(sender_response) != content or await _response_bytes(recipient_response) != content:
                raise MessengerRegressionProbeError("messenger download bytes differ from the immutable upload")
            intruder_denied = False
            try:
                await chat_router.get_chat_file(file_id=file_id, db=db, token=intruder_token)
            except HTTPException as exc:
                intruder_denied = int(exc.status_code) == 403
            if not intruder_denied:
                raise MessengerRegressionProbeError("unrelated user received a messenger file")
    finally:
        chat_router.publish_user_event = original_publisher
        core_utils.publish_user_event = original_notification_publisher
    result = {
        "uploaded_immutable_blob": True,
        "dr_file_intent_and_delivery_created": True,
        "sender_download_authorized": True,
        "recipient_download_authorized": True,
        "unrelated_user_denied": True,
    }
    if include_notification:
        result["notification_persisted_with_durable_webpush_fanout"] = notification_fanout_created
    return result


async def run_probe(
    *,
    scenario_id: str,
    prefix: str,
    allow_production: bool,
    allow_cleanup: bool,
) -> dict[str, Any]:
    if scenario_id not in SCENARIO_IDS:
        raise MessengerRegressionProbeError("messenger regression scenario is unsupported")
    normalized = _safe_prefix(prefix)
    worker.assert_production_full_matrix_allowed(normalized, allow_flag=allow_production)
    if worker.is_production_runtime():
        worker.allow_production_cleanup_hard_delete(normalized, allow_flag=allow_cleanup)
    worker.setup_event_listeners()
    async with _writer_capability() as writer_state:
        await _cleanup(normalized)
        cleanup: dict[str, Any] | None = None
        failure: Exception | None = None
        observation: dict[str, Any] | None = None
        try:
            observation = await _run(
                normalized,
                include_notification=scenario_id == "notifications_webpush_messenger_files",
            )
        except Exception as exc:
            failure = exc
        finally:
            cleanup = await _cleanup(normalized)
    if failure is not None:
        raise MessengerRegressionProbeError("messenger regression execution raised") from failure
    if observation is None or cleanup is None:
        raise MessengerRegressionProbeError("messenger regression has no result")
    return {
        "schema": SCHEMA,
        "status": "passed",
        "scenario_id": scenario_id,
        "role": "webapp_fi",
        "prefix": normalized,
        "writer_epoch": writer_state["writer_epoch"],
        "observation": observation,
        "cleanup": {
            "active_fixture_rows_removed": cleanup["residue_zero"],
            "encrypted_blob_retention_owned_by_dr": cleanup["retained_blob_manifests"] >= 1,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--scenario-id", choices=sorted(SCENARIO_IDS), required=True)
    parser.add_argument("--allow-production-execution", action="store_true")
    parser.add_argument("--allow-production-cleanup", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = asyncio.run(
            run_probe(
                scenario_id=args.scenario_id,
                prefix=args.prefix,
                allow_production=bool(args.allow_production_execution),
                allow_cleanup=bool(args.allow_production_cleanup),
            )
        )
    except Exception as exc:
        _json({"schema": SCHEMA, "status": "failed", "error_class": type(exc).__name__})
        return 1
    _json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
