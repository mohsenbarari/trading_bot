import json
import uuid
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from api.routers.sessions import (
    _build_recovery_action_message_read,
    _build_identity_message_payload,
    _clear_recovery_admin_action_messages,
    _deliver_identity_submission_messages,
    _deliver_initial_recovery_messages,
    _ensure_recovery_admin_access,
    _enqueue_recovery_sms,
    _expire_recovery_if_needed,
    _get_recovery_token_cache_key,
    _pop_temporary_refresh_token,
    _publish_plain_direct_message,
    _publish_recovery_prompt_updates,
    _publish_recovery_action_message,
    _rollback_if_available,
    _send_legacy_recovery_sms,
    _store_temporary_refresh_token,
    approve_single_session_recovery,
    cancel_single_session_recovery,
    get_pending_single_session_recovery_prompts,
    get_single_session_recovery_status,
    login_request_to_dict,
    reject_single_session_recovery,
    request_single_session_recovery_identity,
    start_single_session_recovery,
    submit_single_session_recovery_identity,
)
from core.enums import MessageType
from models.session import LoginRequestStatus, Platform, SingleSessionRecoveryStatus
from models.user import UserRole


class FakeExecuteResult:
    def __init__(self, *, value=None):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)


class FakeUploadFile:
    def __init__(self, content: bytes, *, filename="identity.jpg", content_type="image/jpeg"):
        self._content = content
        self.filename = filename
        self.content_type = content_type
        self.close = AsyncMock()

    async def read(self):
        return self._content


def make_login_request(request_id=None, **overrides):
    data = {
        "id": request_id or uuid.uuid4(),
        "user_id": 7,
        "requester_device_name": "Chrome on Windows",
        "requester_ip": "8.8.8.8",
        "requester_home_server": "foreign",
        "status": LoginRequestStatus.PENDING,
        "expires_at": datetime.utcnow() + timedelta(minutes=1),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_user(**overrides):
    data = {
        "id": 7,
        "role": UserRole.STANDARD,
        "max_sessions": 1,
        "is_deleted": False,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_recovery(**overrides):
    now = datetime.utcnow()
    data = {
        "id": uuid.uuid4(),
        "user_id": 7,
        "user": make_user(),
        "session_login_request_id": uuid.uuid4(),
        "session_login_request": SimpleNamespace(status=LoginRequestStatus.PENDING),
        "status": SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
        "requester_device_name": "Chrome on Windows",
        "requester_ip": "8.8.8.8",
        "created_at": now,
        "inline_action_expires_at": now + timedelta(seconds=30),
        "chat_action_expires_at": now + timedelta(hours=2),
        "identity_requested_at": None,
        "identity_submitted_at": None,
        "decided_at": None,
        "cancelled_at": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class SessionsRouterSingleSessionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_strict_recovery_sms_is_staged_in_the_callers_transaction(self):
        recovery = make_recovery()
        event = SimpleNamespace(event_id=str(uuid.uuid4()))
        db = SimpleNamespace(flush=AsyncMock(), scalar=AsyncMock(return_value=event))
        fence = SimpleNamespace(physical_site="webapp-fi", writer_epoch=17)

        with patch("api.routers.sessions.settings.three_site_dr_enabled", True), patch(
            "api.routers.sessions.settings.dr_event_protocol_strict", True
        ), patch(
            "api.routers.sessions.current_writer_fence_context", return_value=fence
        ), patch(
            "api.routers.sessions.current_dr_transaction_event_ids", return_value=(event.event_id,)
        ), patch(
            "api.routers.sessions.enqueue_epoch_bound_effect", new=AsyncMock()
        ) as enqueue_mock, patch(
            "api.routers.sessions.send_sms"
        ) as send_mock:
            queued = await _enqueue_recovery_sms(
                db,
                recovery,
                mobile="09120000000",
                message="recovery state changed",
                action="approved",
            )

        self.assertTrue(queued)
        db.flush.assert_awaited_once()
        db.scalar.assert_awaited_once()
        enqueue_mock.assert_awaited_once_with(
            db,
            event_id=event.event_id,
            effect_type="recovery_sms",
            provider="smsir",
            destination_key="09120000000",
            idempotency_key=f"recovery-sms:{recovery.id}:approved",
            payload={"mobile": "09120000000", "message": "recovery state changed"},
        )
        send_mock.assert_not_called()

    async def test_strict_recovery_sms_fails_closed_without_causation_event(self):
        recovery = make_recovery()
        db = SimpleNamespace(flush=AsyncMock(), scalar=AsyncMock(return_value=None))
        fence = SimpleNamespace(physical_site="webapp-fi", writer_epoch=17)
        with patch("api.routers.sessions.settings.three_site_dr_enabled", True), patch(
            "api.routers.sessions.settings.dr_event_protocol_strict", True
        ), patch(
            "api.routers.sessions.current_writer_fence_context", return_value=fence
        ), patch(
            "api.routers.sessions.current_dr_transaction_event_ids", return_value=(str(uuid.uuid4()),)
        ):
            with self.assertRaisesRegex(RuntimeError, "no immutable causation event"):
                await _enqueue_recovery_sms(
                    db,
                    recovery,
                    mobile="09120000000",
                    message="recovery state changed",
                    action="approved",
                )

    def test_legacy_recovery_sms_only_sends_when_no_durable_intent_exists(self):
        with patch("api.routers.sessions.send_sms") as send_mock:
            _send_legacy_recovery_sms(
                durably_queued=True,
                mobile="09120000000",
                message="queued",
            )
            _send_legacy_recovery_sms(
                durably_queued=False,
                mobile="09120000000",
                message="legacy",
            )
        send_mock.assert_called_once_with("09120000000", "legacy")

    async def test_internal_cache_prompt_and_expiry_helpers_cover_fallback_paths(self):
        login_req = make_login_request(status="pending", created_at=None, expires_at=None)
        self.assertEqual(login_request_to_dict(login_req)["created_at"], None)

        await _rollback_if_available(SimpleNamespace())
        sync_db = SimpleNamespace(rollback=lambda: None)
        await _rollback_if_available(sync_db)
        async_db = FakeDB()
        await _rollback_if_available(async_db)
        async_db.rollback.assert_awaited_once()

        redis_client = SimpleNamespace(setex=AsyncMock(), get=AsyncMock(return_value="refresh-1"), delete=AsyncMock())
        with patch("bot.utils.redis_helpers.get_redis", new=AsyncMock(return_value=redis_client)):
            await _store_temporary_refresh_token("cache-key", "refresh-1", ttl_seconds=12)
            popped = await _pop_temporary_refresh_token("cache-key")
        redis_client.setex.assert_awaited_once_with("cache-key", 12, "refresh-1")
        redis_client.delete.assert_awaited_once_with("cache-key")
        self.assertEqual(popped, "refresh-1")

        with patch("bot.utils.redis_helpers.get_redis", new=AsyncMock(side_effect=RuntimeError("redis down"))):
            await _store_temporary_refresh_token("cache-key", "refresh-2")
            self.assertIsNone(await _pop_temporary_refresh_token("cache-key"))

        recovery_without_user = make_recovery(user=None)
        await _publish_recovery_prompt_updates(
            FakeDB([FakeExecuteResult(value=None)]),
            recovery_without_user,
        )

        target = SimpleNamespace(admin_user_id=90, current_action_message_id=44)
        requester = make_user(id=7, full_name="Requester")
        with patch("api.routers.sessions.list_recovery_admin_targets", new=AsyncMock(return_value=[target])), patch(
            "api.routers.sessions.publish_user_event",
            new=AsyncMock(),
        ) as publish_mock:
            await _publish_recovery_prompt_updates(FakeDB(), make_recovery(user=requester), requester)
        publish_mock.assert_awaited_once()

        inactive = make_recovery(status=SingleSessionRecoveryStatus.APPROVED)
        self.assertFalse(await _expire_recovery_if_needed(FakeDB(), inactive))
        not_expired = make_recovery(chat_action_expires_at=datetime.utcnow() + timedelta(minutes=5))
        self.assertFalse(await _expire_recovery_if_needed(FakeDB(), not_expired))

        expired_login_req = make_login_request(status=LoginRequestStatus.PENDING)
        expired_requester = make_user(mobile_number="09120000000")
        expired = make_recovery(
            user=None,
            session_login_request=None,
            session_login_request_id=uuid.uuid4(),
            chat_action_expires_at=datetime.utcnow() - timedelta(minutes=1),
        )
        db = FakeDB([FakeExecuteResult(value=expired_login_req), FakeExecuteResult(value=expired_requester)])
        with patch("api.routers.sessions._clear_recovery_admin_action_messages", new=AsyncMock()) as clear_mock, patch(
            "api.routers.sessions._publish_recovery_prompt_updates",
            new=AsyncMock(),
        ) as publish_mock, patch("api.routers.sessions.send_sms", side_effect=RuntimeError("sms down")):
            self.assertTrue(await _expire_recovery_if_needed(db, expired))
        self.assertEqual(expired.status, SingleSessionRecoveryStatus.EXPIRED)
        self.assertEqual(expired_login_req.status, LoginRequestStatus.EXPIRED)
        clear_mock.assert_awaited_once_with(db, expired.id)
        publish_mock.assert_awaited_once_with(db, expired, expired_requester)

    async def test_recovery_action_helpers_cover_serialization_publish_and_clear_paths(self):
        recovery = make_recovery()
        requester = make_user(id=7, full_name="Requester")
        message = SimpleNamespace(id=55, receiver_id=90)

        class FakeMessageRead:
            from_orm_with_forwarding = Mock(return_value=SimpleNamespace(model_dump=lambda: {"id": 55, "kind": "base"}))

            def __init__(self, **payload):
                self.payload = payload

        with patch("api.routers.sessions.MessageRead", FakeMessageRead), patch(
            "api.routers.sessions.build_recovery_message_action_payload",
            return_value={"kind": "recovery", "message_id": 55},
        ):
            response = _build_recovery_action_message_read(message, recovery, requester)
        self.assertEqual(response.payload["recovery_action"], {"kind": "recovery", "message_id": 55})

        with patch("api.routers.sessions.publish_direct_message_event", new=AsyncMock()) as publish_mock:
            await _publish_plain_direct_message(message, sender_name="Requester")
        publish_mock.assert_awaited_once_with(
            receiver_id=90,
            message=message,
            serializer=unittest.mock.ANY,
            publisher=unittest.mock.ANY,
            sender_name="Requester",
        )

        fake_response = SimpleNamespace(id=55)
        with patch("api.routers.sessions._build_recovery_action_message_read", return_value=fake_response) as build_mock, patch(
            "api.routers.sessions.publish_direct_message_event",
            new=AsyncMock(),
        ) as publish_mock:
            await _publish_recovery_action_message(message, recovery_request=recovery, requester_user=requester)
        build_mock.assert_called_once_with(message, recovery, requester)
        publish_kwargs = publish_mock.await_args.kwargs
        self.assertIs(publish_kwargs["serializer"](message), fake_response)

        first_target = SimpleNamespace(current_action_message_id=101)
        second_target = SimpleNamespace(current_action_message_id=202)
        with patch("api.routers.sessions.list_recovery_admin_targets", new=AsyncMock(return_value=[first_target, second_target])):
            await _clear_recovery_admin_action_messages(FakeDB(), recovery.id)
        self.assertIsNone(first_target.current_action_message_id)
        self.assertIsNone(second_target.current_action_message_id)

    async def test_identity_payload_delivery_and_admin_access_helpers_cover_edge_paths(self):
        image_file = SimpleNamespace(
            id=uuid.uuid4(),
            file_name="id.jpg",
            thumbnail="thumb",
        )
        document_file = SimpleNamespace(
            id=uuid.uuid4(),
            file_name="id.pdf",
            thumbnail=None,
        )
        with patch(
            "api.routers.sessions.persist_chat_media_file_bytes",
            new=AsyncMock(side_effect=[
                SimpleNamespace(chat_file=image_file, mime_type="image/jpeg", width=640, height=480, size=5),
                SimpleNamespace(chat_file=document_file, mime_type="application/pdf", width=None, height=None, size=7),
            ]),
        ):
            image_type, image_content, image_caption = await _build_identity_message_payload(
                file_name="id.jpg",
                declared_content_type="image/jpeg",
                contents=b"img",
                uploader_id=7,
                caption="  توضیح  ",
                db=FakeDB(),
            )
            doc_type, doc_content, doc_caption = await _build_identity_message_payload(
                file_name="id.pdf",
                declared_content_type="application/pdf",
                contents=b"pdf",
                uploader_id=7,
                caption="  متن مدرک  ",
                db=FakeDB(),
            )
        self.assertEqual(image_type, MessageType.IMAGE)
        self.assertEqual(json.loads(image_content)["caption"], "توضیح")
        self.assertEqual(image_caption, "توضیح")
        self.assertEqual(doc_type, MessageType.DOCUMENT)
        self.assertEqual(json.loads(doc_content)["size"], 7)
        self.assertEqual(doc_caption, "متن مدرک")

        recovery = make_recovery()
        requester = make_user(id=7)
        admin_target_missing = SimpleNamespace(admin_user_id=80, current_action_message_id=None)
        admin_target_ok = SimpleNamespace(admin_user_id=81, current_action_message_id=None)
        text_message = SimpleNamespace(id=1001, receiver_id=81)
        action_message = SimpleNamespace(id=1002, receiver_id=81)
        db = FakeDB([FakeExecuteResult(value=None), FakeExecuteResult(value=make_user(id=81))])
        with patch("api.routers.sessions.list_recovery_admin_targets", new=AsyncMock(return_value=[admin_target_missing, admin_target_ok])), patch(
            "api.routers.sessions.persist_sent_direct_message",
            new=AsyncMock(side_effect=[text_message, action_message]),
        ) as persist_mock, patch(
            "api.routers.sessions._publish_plain_direct_message",
            new=AsyncMock(),
        ) as plain_publish, patch(
            "api.routers.sessions._publish_recovery_action_message",
            new=AsyncMock(),
        ) as action_publish, patch(
            "api.routers.sessions._publish_recovery_prompt_updates",
            new=AsyncMock(),
        ) as prompt_publish:
            await _deliver_identity_submission_messages(
                db,
                recovery_request=recovery,
                requester_user=requester,
                message_type=MessageType.DOCUMENT,
                message_content=doc_content,
                caption_text="متن مدرک",
            )
        self.assertEqual(persist_mock.await_count, 2)
        plain_publish.assert_awaited_once_with(text_message, sender_name=unittest.mock.ANY)
        action_publish.assert_awaited_once_with(action_message, recovery_request=recovery, requester_user=requester)
        prompt_publish.assert_awaited_once_with(db, recovery, requester)
        self.assertEqual(admin_target_ok.current_action_message_id, action_message.id)

        admin = make_user(id=99, role=UserRole.SUPER_ADMIN)
        with self.assertRaises(HTTPException) as exc_info:
            await _ensure_recovery_admin_access(FakeDB(), recovery_id=uuid.uuid4(), current_user=make_user(role=UserRole.STANDARD))
        self.assertEqual(exc_info.exception.status_code, 403)

        with patch("api.routers.sessions.get_recovery_admin_target", new=AsyncMock(return_value=None)):
            with self.assertRaises(HTTPException) as exc_info:
                await _ensure_recovery_admin_access(FakeDB(), recovery_id=uuid.uuid4(), current_user=admin)
        self.assertEqual(exc_info.exception.status_code, 404)

        recovery_without_requester = make_recovery(user=None)
        admin_target = SimpleNamespace(recovery_request=recovery_without_requester)
        with patch("api.routers.sessions.get_recovery_admin_target", new=AsyncMock(return_value=admin_target)), patch(
            "api.routers.sessions._expire_recovery_if_needed",
            new=AsyncMock(return_value=False),
        ):
            resolved_target, resolved_recovery, resolved_user = await _ensure_recovery_admin_access(
                FakeDB([FakeExecuteResult(value=requester)]),
                recovery_id=uuid.uuid4(),
                current_user=admin,
            )
        self.assertIs(resolved_target, admin_target)
        self.assertIs(resolved_recovery, recovery_without_requester)
        self.assertIs(resolved_user, requester)

        with patch("api.routers.sessions.get_recovery_admin_target", new=AsyncMock(return_value=admin_target)), patch(
            "api.routers.sessions._expire_recovery_if_needed",
            new=AsyncMock(return_value=True),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await _ensure_recovery_admin_access(
                    FakeDB([FakeExecuteResult(value=requester)]),
                    recovery_id=uuid.uuid4(),
                    current_user=admin,
                )
        self.assertEqual(exc_info.exception.status_code, 400)

    async def test_identity_delivery_exception_and_missing_requester_admin_access_paths(self):
        recovery = make_recovery()
        requester = make_user(id=7)
        admin_target = SimpleNamespace(admin_user_id=80, current_action_message_id=None)
        action_message = SimpleNamespace(id=1200, receiver_id=80)
        db = FakeDB([FakeExecuteResult(value=make_user(id=80))])

        with patch("api.routers.sessions.list_recovery_admin_targets", new=AsyncMock(return_value=[admin_target])), patch(
            "api.routers.sessions.persist_sent_direct_message",
            new=AsyncMock(return_value=action_message),
        ), patch(
            "api.routers.sessions._publish_recovery_action_message",
            new=AsyncMock(side_effect=RuntimeError("publish down")),
        ), patch(
            "api.routers.sessions._publish_recovery_prompt_updates",
            new=AsyncMock(),
        ) as prompt_publish, patch(
            "api.routers.sessions._rollback_if_available",
            new=AsyncMock(),
        ) as rollback_mock:
            await _deliver_identity_submission_messages(
                db,
                recovery_request=recovery,
                requester_user=requester,
                message_type=MessageType.IMAGE,
                message_content='{"file_id":"1"}',
                caption_text=None,
            )
        rollback_mock.assert_awaited_once_with(db)
        prompt_publish.assert_awaited_once_with(db, recovery, requester)

        admin = make_user(id=99, role=UserRole.SUPER_ADMIN)
        missing_requester_recovery = make_recovery(user=None)
        missing_requester_target = SimpleNamespace(recovery_request=missing_requester_recovery)
        with patch("api.routers.sessions.get_recovery_admin_target", new=AsyncMock(return_value=missing_requester_target)):
            with self.assertRaises(HTTPException) as exc_info:
                await _ensure_recovery_admin_access(
                    FakeDB([FakeExecuteResult(value=None)]),
                    recovery_id=uuid.uuid4(),
                    current_user=admin,
                )
        self.assertEqual(exc_info.exception.status_code, 404)

    async def test_initial_recovery_message_delivery_handles_missing_targets_and_failures(self):
        requester = make_user(id=7)
        admins = [make_user(id=90), make_user(id=91)]
        recovery = make_recovery()
        db = FakeDB()
        db.rollback = AsyncMock()
        message = SimpleNamespace(id=501, receiver_id=91)
        admin_target = SimpleNamespace(current_action_message_id=None)
        with patch(
            "api.routers.sessions.get_recovery_admin_target",
            new=AsyncMock(side_effect=[None, admin_target]),
        ), patch(
            "api.routers.sessions.persist_sent_direct_message",
            new=AsyncMock(return_value=message),
        ) as persist_mock, patch(
            "api.routers.sessions._publish_recovery_action_message",
            new=AsyncMock(side_effect=RuntimeError("publish down")),
        ), patch(
            "api.routers.sessions._publish_recovery_prompt_updates",
            new=AsyncMock(),
        ) as prompt_publish:
            await _deliver_initial_recovery_messages(db, recovery, requester, admins)

        self.assertEqual(len(db.added), 2)
        self.assertEqual(persist_mock.await_count, 1)
        self.assertEqual(admin_target.current_action_message_id, 501)
        db.rollback.assert_awaited_once()
        prompt_publish.assert_awaited_once_with(db, recovery, requester)

    async def test_start_recovery_validates_request_and_eligibility(self):
        with self.assertRaises(HTTPException) as exc_info:
            await start_single_session_recovery("bad-id", db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 400)

        rid = str(uuid.uuid4())
        with self.assertRaises(HTTPException) as exc_info:
            await start_single_session_recovery(rid, db=FakeDB([FakeExecuteResult(value=None)]))
        self.assertEqual(exc_info.exception.status_code, 404)

        login_req = make_login_request(request_id=uuid.UUID(rid), status=LoginRequestStatus.APPROVED)
        db = FakeDB([FakeExecuteResult(value=login_req), FakeExecuteResult(value=make_user())])
        with patch(
            "api.routers.sessions.get_latest_recovery_request_for_login_request",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await start_single_session_recovery(rid, db=db)
        self.assertEqual(exc_info.exception.status_code, 400)

        login_req = make_login_request(request_id=uuid.UUID(rid))
        db = FakeDB([FakeExecuteResult(value=login_req), FakeExecuteResult(value=make_user(role=UserRole.SUPER_ADMIN))])
        with patch(
            "api.routers.sessions.get_latest_recovery_request_for_login_request",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await start_single_session_recovery(rid, db=db)
        self.assertEqual(exc_info.exception.status_code, 403)

        db = FakeDB([FakeExecuteResult(value=login_req), FakeExecuteResult(value=make_user(max_sessions=2))])
        with patch(
            "api.routers.sessions.get_latest_recovery_request_for_login_request",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await start_single_session_recovery(rid, db=db)
        self.assertEqual(exc_info.exception.status_code, 400)

    async def test_start_recovery_reuses_active_or_creates_new(self):
        rid_uuid = uuid.uuid4()
        login_req = make_login_request(request_id=rid_uuid)
        active = make_recovery()

        with patch(
            "api.routers.sessions.get_latest_recovery_request_for_login_request",
            new=AsyncMock(return_value=active),
        ):
            result = await start_single_session_recovery(
                str(rid_uuid),
                db=FakeDB([FakeExecuteResult(value=login_req)]),
            )
        self.assertEqual(result["id"], str(active.id))

        created = make_recovery()
        db = FakeDB([FakeExecuteResult(value=login_req), FakeExecuteResult(value=make_user())])
        with patch(
            "api.routers.sessions.get_latest_recovery_request_for_login_request",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.sessions.create_recovery_request",
            new=AsyncMock(return_value=created),
        ) as create_mock:
            with patch(
                "api.routers.sessions.list_recovery_admin_users",
                new=AsyncMock(return_value=[make_user(id=90, role=UserRole.SUPER_ADMIN)]),
            ), patch(
                "api.routers.sessions._deliver_initial_recovery_messages",
                new=AsyncMock(),
            ) as deliver_mock:
                result = await start_single_session_recovery(str(rid_uuid), db=db)

        create_mock.assert_awaited_once_with(db, login_req)
        deliver_mock.assert_awaited_once_with(
            db,
            created,
            unittest.mock.ANY,
            unittest.mock.ANY,
        )
        db.commit.assert_awaited_once()
        self.assertEqual(result["status"], SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW.value)

    async def test_start_cancel_and_status_cover_additional_recovery_edges(self):
        rid_uuid = uuid.uuid4()
        expired_login_req = make_login_request(
            request_id=rid_uuid,
            expires_at=datetime.utcnow() - timedelta(minutes=1),
        )
        with patch(
            "api.routers.sessions.get_latest_recovery_request_for_login_request",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as expired_exc:
                await start_single_session_recovery(str(rid_uuid), db=FakeDB([FakeExecuteResult(value=expired_login_req)]))
        self.assertEqual(expired_exc.exception.status_code, 400)

        valid_login_req = make_login_request(request_id=rid_uuid)
        with patch(
            "api.routers.sessions.get_latest_recovery_request_for_login_request",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as deleted_exc:
                await start_single_session_recovery(
                    str(rid_uuid),
                    db=FakeDB([FakeExecuteResult(value=valid_login_req), FakeExecuteResult(value=make_user(is_deleted=True))]),
                )
        self.assertEqual(deleted_exc.exception.status_code, 404)

        with patch(
            "api.routers.sessions.get_latest_recovery_request_for_login_request",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.sessions.list_recovery_admin_users",
            new=AsyncMock(return_value=[]),
        ):
            with self.assertRaises(HTTPException) as no_admin_exc:
                await start_single_session_recovery(
                    str(rid_uuid),
                    db=FakeDB([FakeExecuteResult(value=valid_login_req), FakeExecuteResult(value=make_user())]),
                )
        self.assertEqual(no_admin_exc.exception.status_code, 503)

        recovery = make_recovery(session_login_request=None, session_login_request_id=uuid.uuid4())
        login_req = make_login_request(status=LoginRequestStatus.PENDING)
        db = FakeDB([FakeExecuteResult(value=login_req)])
        with patch(
            "api.routers.sessions.get_active_recovery_request_for_login_request",
            new=AsyncMock(return_value=recovery),
        ), patch("api.routers.sessions.cancel_recovery_request") as cancel_mock, patch(
            "api.routers.sessions._clear_recovery_admin_action_messages",
            new=AsyncMock(),
        ), patch(
            "api.routers.sessions._publish_recovery_prompt_updates",
            new=AsyncMock(),
        ):
            result = await cancel_single_session_recovery(str(rid_uuid), db=db)
        cancel_mock.assert_called_once_with(recovery)
        self.assertEqual(login_req.status, LoginRequestStatus.REJECTED)
        self.assertEqual(result["detail"], "درخواست بازیابی لغو شد")

        approved_recovery = make_recovery(status=SingleSessionRecoveryStatus.APPROVED)
        approved_recovery.id = rid_uuid
        approved_recovery.user_id = 7
        new_session = SimpleNamespace(
            id=uuid.uuid4(),
            home_server="foreign",
            user_id=7,
            is_active=True,
            created_at=datetime.utcnow(),
        )
        with patch(
            "api.routers.sessions.get_latest_recovery_request_for_login_request",
            new=AsyncMock(return_value=approved_recovery),
        ), patch(
            "api.routers.sessions._expire_recovery_if_needed",
            new=AsyncMock(return_value=False),
        ), patch("api.routers.sessions.create_access_token", return_value="access-token"), patch(
            "api.routers.sessions._pop_temporary_refresh_token",
            new=AsyncMock(return_value="refresh-token"),
        ):
            status_result = await get_single_session_recovery_status(
                str(rid_uuid),
                db=FakeDB([FakeExecuteResult(value=new_session)]),
            )
        self.assertEqual(status_result["access_token"], "access-token")
        self.assertEqual(status_result["refresh_token"], "refresh-token")
        self.assertEqual(status_result["token_type"], "bearer")

    async def test_cancel_recovery_validates_and_cancels(self):
        with self.assertRaises(HTTPException) as exc_info:
            await cancel_single_session_recovery("bad-id", db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 400)

        rid = uuid.uuid4()
        with patch(
            "api.routers.sessions.get_active_recovery_request_for_login_request",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await cancel_single_session_recovery(str(rid), db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 404)

        recovery = make_recovery()
        db = FakeDB()
        with patch(
            "api.routers.sessions.get_active_recovery_request_for_login_request",
            new=AsyncMock(return_value=recovery),
        ), patch("api.routers.sessions.cancel_recovery_request") as cancel_mock, patch(
            "api.routers.sessions._clear_recovery_admin_action_messages",
            new=AsyncMock(),
        ), patch(
            "api.routers.sessions._publish_recovery_prompt_updates",
            new=AsyncMock(),
        ):
            result = await cancel_single_session_recovery(str(rid), db=db)

        cancel_mock.assert_called_once_with(recovery)
        db.commit.assert_awaited_once()
        self.assertEqual(result["detail"], "درخواست بازیابی لغو شد")

    async def test_recovery_status_returns_not_started_and_lazily_expires(self):
        with self.assertRaises(HTTPException) as exc_info:
            await get_single_session_recovery_status("bad-id", db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 400)

        rid = uuid.uuid4()
        with patch(
            "api.routers.sessions.get_latest_recovery_request_for_login_request",
            new=AsyncMock(return_value=None),
        ):
            result = await get_single_session_recovery_status(str(rid), db=FakeDB())
        self.assertEqual(result, {"status": "not_started"})

        expired_candidate = make_recovery(chat_action_expires_at=datetime.utcnow() - timedelta(minutes=1))
        db = FakeDB()
        with patch(
            "api.routers.sessions.get_latest_recovery_request_for_login_request",
            new=AsyncMock(return_value=expired_candidate),
        ), patch("api.routers.sessions.expire_recovery_request") as expire_mock, patch(
            "api.routers.sessions._clear_recovery_admin_action_messages",
            new=AsyncMock(),
        ), patch(
            "api.routers.sessions._publish_recovery_prompt_updates",
            new=AsyncMock(),
        ):
            result = await get_single_session_recovery_status(str(rid), db=db)

        expire_mock.assert_called_once_with(expired_candidate)
        db.commit.assert_awaited_once()
        self.assertEqual(result["id"], str(expired_candidate.id))

    async def test_pending_recovery_prompts_are_admin_only_and_serialized(self):
        non_admin = make_user(role=UserRole.STANDARD)
        self.assertEqual(await get_pending_single_session_recovery_prompts(db=FakeDB(), current_user=non_admin), [])

        admin = make_user(id=99, role=UserRole.MIDDLE_MANAGER)
        recovery = make_recovery()
        requester = make_user(id=7)
        target = SimpleNamespace(current_action_message_id=123)
        with patch(
            "api.routers.sessions.list_pending_admin_recovery_targets",
            new=AsyncMock(return_value=[(target, recovery, requester)]),
        ) as list_mock:
            result = await get_pending_single_session_recovery_prompts(db=FakeDB(), current_user=admin)

        list_mock.assert_awaited_once_with(unittest.mock.ANY, admin_user_id=99)
        self.assertEqual(result[0]["recovery_id"], str(recovery.id))
        self.assertTrue(result[0]["can_request_identity"])

    async def test_request_identity_validates_state_and_sends_sms(self):
        recovery_id = uuid.uuid4()
        admin = make_user(id=99, role=UserRole.SUPER_ADMIN)

        with self.assertRaises(HTTPException) as exc_info:
            await request_single_session_recovery_identity("bad-id", db=FakeDB(), current_user=admin)
        self.assertEqual(exc_info.exception.status_code, 400)

        invalid_state = make_recovery(status=SingleSessionRecoveryStatus.IDENTITY_SUBMITTED)
        requester = make_user(mobile_number="09120000000")
        with patch(
            "api.routers.sessions._ensure_recovery_admin_access",
            new=AsyncMock(return_value=(SimpleNamespace(), invalid_state, requester)),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await request_single_session_recovery_identity(str(recovery_id), db=FakeDB(), current_user=admin)
        self.assertEqual(exc_info.exception.status_code, 400)

        recovery = make_recovery(status=SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW)
        db = FakeDB()
        with patch(
            "api.routers.sessions._ensure_recovery_admin_access",
            new=AsyncMock(return_value=(SimpleNamespace(), recovery, requester)),
        ), patch(
            "api.routers.sessions._clear_recovery_admin_action_messages",
            new=AsyncMock(),
        ) as clear_mock, patch(
            "api.routers.sessions._publish_recovery_prompt_updates",
            new=AsyncMock(),
        ) as publish_mock, patch("api.routers.sessions.send_sms", side_effect=RuntimeError("sms down")):
            result = await request_single_session_recovery_identity(str(recovery_id), db=db, current_user=admin)

        self.assertEqual(recovery.status, SingleSessionRecoveryStatus.IDENTITY_VERIFICATION_REQUESTED)
        clear_mock.assert_awaited_once_with(db, recovery.id)
        publish_mock.assert_awaited_once_with(db, recovery, requester)
        db.commit.assert_awaited_once()
        self.assertEqual(result["detail"], "درخواست ارسال مدرک برای کاربر ثبت شد")

    async def test_approve_and_reject_recovery_flows_update_login_request_and_notify(self):
        recovery_id = uuid.uuid4()
        admin = make_user(id=99, role=UserRole.SUPER_ADMIN)
        login_req = make_login_request(status=LoginRequestStatus.PENDING)
        requester = make_user(mobile_number="09120000000")
        recovery = make_recovery(
            status=SingleSessionRecoveryStatus.IDENTITY_SUBMITTED,
            session_login_request=login_req,
        )
        new_session = SimpleNamespace(
            id=uuid.uuid4(),
            device_name="Chrome",
            device_ip="1.2.3.4",
            platform=Platform.WEB,
            home_server="foreign",
            is_primary=True,
            is_active=True,
            created_at=datetime.utcnow(),
            last_active_at=datetime.utcnow(),
        )

        with patch(
            "api.routers.sessions._ensure_recovery_admin_access",
            new=AsyncMock(return_value=(SimpleNamespace(), recovery, requester)),
        ), patch("api.routers.sessions.create_refresh_token", return_value="refresh-token"), patch(
            "api.routers.sessions.provision_session_for_login_request",
            new=AsyncMock(return_value=new_session),
        ) as provision_mock, patch(
            "api.routers.sessions._clear_recovery_admin_action_messages",
            new=AsyncMock(),
        ), patch(
            "api.routers.sessions._store_temporary_refresh_token",
            new=AsyncMock(),
        ) as store_mock, patch(
            "api.routers.sessions._publish_recovery_prompt_updates",
            new=AsyncMock(),
        ), patch("api.routers.sessions.send_sms"):
            result = await approve_single_session_recovery(
                str(recovery_id),
                request=SimpleNamespace(client=SimpleNamespace(host="1.2.3.4")),
                db=FakeDB(),
                current_user=admin,
            )

        self.assertEqual(login_req.status, LoginRequestStatus.APPROVED)
        self.assertEqual(recovery.status, SingleSessionRecoveryStatus.APPROVED)
        provision_mock.assert_awaited_once()
        store_mock.assert_awaited_once()
        self.assertEqual(result["detail"], "درخواست بازیابی تایید شد")

        reject_login_req = make_login_request(status=LoginRequestStatus.PENDING)
        reject_recovery = make_recovery(
            status=SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
            session_login_request=reject_login_req,
        )
        with patch(
            "api.routers.sessions._ensure_recovery_admin_access",
            new=AsyncMock(return_value=(SimpleNamespace(), reject_recovery, requester)),
        ), patch(
            "api.routers.sessions._clear_recovery_admin_action_messages",
            new=AsyncMock(),
        ), patch(
            "api.routers.sessions._publish_recovery_prompt_updates",
            new=AsyncMock(),
        ), patch("api.routers.sessions.send_sms"):
            result = await reject_single_session_recovery(str(recovery_id), db=FakeDB(), current_user=admin)

        self.assertEqual(reject_login_req.status, LoginRequestStatus.REJECTED)
        self.assertEqual(reject_recovery.status, SingleSessionRecoveryStatus.REJECTED)
        self.assertEqual(result["detail"], "درخواست بازیابی رد شد")

    async def test_approve_reject_and_submit_cover_remaining_router_error_and_warning_paths(self):
        admin = make_user(id=99, role=UserRole.SUPER_ADMIN)
        requester = make_user(mobile_number="09120000000")

        with self.assertRaises(HTTPException) as exc_info:
            await approve_single_session_recovery("bad-id", request=SimpleNamespace(), db=FakeDB(), current_user=admin)
        self.assertEqual(exc_info.exception.status_code, 400)

        blocked_approve = make_recovery(status=SingleSessionRecoveryStatus.APPROVED)
        with patch(
            "api.routers.sessions._ensure_recovery_admin_access",
            new=AsyncMock(return_value=(SimpleNamespace(), blocked_approve, requester)),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await approve_single_session_recovery(str(uuid.uuid4()), request=SimpleNamespace(), db=FakeDB(), current_user=admin)
        self.assertEqual(exc_info.exception.status_code, 400)

        missing_login_req_recovery = make_recovery(
            status=SingleSessionRecoveryStatus.IDENTITY_SUBMITTED,
            session_login_request=None,
            session_login_request_id=uuid.uuid4(),
        )
        with patch(
            "api.routers.sessions._ensure_recovery_admin_access",
            new=AsyncMock(return_value=(SimpleNamespace(), missing_login_req_recovery, requester)),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await approve_single_session_recovery(
                    str(uuid.uuid4()),
                    request=SimpleNamespace(),
                    db=FakeDB([FakeExecuteResult(value=None)]),
                    current_user=admin,
                )
        self.assertEqual(exc_info.exception.status_code, 404)

        login_req = make_login_request(status=LoginRequestStatus.PENDING)
        approve_recovery = make_recovery(
            status=SingleSessionRecoveryStatus.IDENTITY_SUBMITTED,
            session_login_request=login_req,
        )
        new_session = SimpleNamespace(
            id=uuid.uuid4(),
            device_name="Chrome",
            device_ip="1.2.3.4",
            platform=Platform.WEB,
            home_server="foreign",
            is_primary=True,
            is_active=True,
            created_at=datetime.utcnow(),
            last_active_at=datetime.utcnow(),
        )
        with patch(
            "api.routers.sessions._ensure_recovery_admin_access",
            new=AsyncMock(return_value=(SimpleNamespace(), approve_recovery, requester)),
        ), patch("api.routers.sessions.create_refresh_token", return_value="refresh-token"), patch(
            "api.routers.sessions.provision_session_for_login_request",
            new=AsyncMock(return_value=new_session),
        ), patch(
            "api.routers.sessions._clear_recovery_admin_action_messages",
            new=AsyncMock(),
        ), patch(
            "api.routers.sessions._store_temporary_refresh_token",
            new=AsyncMock(),
        ), patch(
            "api.routers.sessions._publish_recovery_prompt_updates",
            new=AsyncMock(),
        ), patch("api.routers.sessions.send_sms", side_effect=RuntimeError("sms down")):
            approve_result = await approve_single_session_recovery(
                str(uuid.uuid4()),
                request=SimpleNamespace(client=SimpleNamespace(host="1.2.3.4")),
                db=FakeDB(),
                current_user=admin,
            )
        self.assertEqual(approve_result["detail"], "درخواست بازیابی تایید شد")

        with self.assertRaises(HTTPException) as exc_info:
            await reject_single_session_recovery("bad-id", db=FakeDB(), current_user=admin)
        self.assertEqual(exc_info.exception.status_code, 400)

        blocked_reject = make_recovery(status=SingleSessionRecoveryStatus.REJECTED)
        with patch(
            "api.routers.sessions._ensure_recovery_admin_access",
            new=AsyncMock(return_value=(SimpleNamespace(), blocked_reject, requester)),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await reject_single_session_recovery(str(uuid.uuid4()), db=FakeDB(), current_user=admin)
        self.assertEqual(exc_info.exception.status_code, 400)

        reject_login_req = make_login_request(status=LoginRequestStatus.PENDING)
        reject_recovery = make_recovery(
            status=SingleSessionRecoveryStatus.IDENTITY_SUBMITTED,
            session_login_request=None,
            session_login_request_id=uuid.uuid4(),
        )
        with patch(
            "api.routers.sessions._ensure_recovery_admin_access",
            new=AsyncMock(return_value=(SimpleNamespace(), reject_recovery, requester)),
        ), patch(
            "api.routers.sessions._clear_recovery_admin_action_messages",
            new=AsyncMock(),
        ), patch(
            "api.routers.sessions._publish_recovery_prompt_updates",
            new=AsyncMock(),
        ), patch("api.routers.sessions.send_sms", side_effect=RuntimeError("sms down")):
            reject_result = await reject_single_session_recovery(
                str(uuid.uuid4()),
                db=FakeDB([FakeExecuteResult(value=reject_login_req)]),
                current_user=admin,
            )
        self.assertEqual(reject_login_req.status, LoginRequestStatus.REJECTED)
        self.assertEqual(reject_result["detail"], "درخواست بازیابی رد شد")

        with self.assertRaises(HTTPException) as exc_info:
            await submit_single_session_recovery_identity("bad-id", file=FakeUploadFile(b"x"), db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 400)

        wrong_state_recovery = make_recovery(status=SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW)
        with patch(
            "api.routers.sessions.get_active_recovery_request_for_login_request",
            new=AsyncMock(return_value=wrong_state_recovery),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await submit_single_session_recovery_identity(str(uuid.uuid4()), file=FakeUploadFile(b"x"), db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 400)

        identity_recovery = make_recovery(status=SingleSessionRecoveryStatus.IDENTITY_VERIFICATION_REQUESTED)
        with patch(
            "api.routers.sessions.get_active_recovery_request_for_login_request",
            new=AsyncMock(return_value=identity_recovery),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await submit_single_session_recovery_identity(
                    str(uuid.uuid4()),
                    file=FakeUploadFile(b"x"),
                    db=FakeDB([FakeExecuteResult(value=None)]),
                )
        self.assertEqual(exc_info.exception.status_code, 404)

        with patch(
            "api.routers.sessions.get_active_recovery_request_for_login_request",
            new=AsyncMock(return_value=identity_recovery),
        ), patch(
            "api.routers.sessions._expire_recovery_if_needed",
            new=AsyncMock(return_value=True),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await submit_single_session_recovery_identity(
                    str(uuid.uuid4()),
                    file=FakeUploadFile(b"x"),
                    db=FakeDB([FakeExecuteResult(value=requester)]),
                )
        self.assertEqual(exc_info.exception.status_code, 400)

        ok_file = FakeUploadFile(b"img", filename="id.jpg", content_type="image/jpeg")
        with patch(
            "api.routers.sessions.get_active_recovery_request_for_login_request",
            new=AsyncMock(return_value=identity_recovery),
        ), patch(
            "api.routers.sessions._expire_recovery_if_needed",
            new=AsyncMock(return_value=False),
        ), patch(
            "api.routers.sessions._build_identity_message_payload",
            new=AsyncMock(return_value=(MessageType.IMAGE, '{"file_id":"1"}', None)),
        ), patch(
            "api.routers.sessions._clear_recovery_admin_action_messages",
            new=AsyncMock(),
        ), patch(
            "api.routers.sessions._deliver_identity_submission_messages",
            new=AsyncMock(),
        ), patch("api.routers.sessions.send_sms", side_effect=RuntimeError("sms down")):
            submit_result = await submit_single_session_recovery_identity(
                str(uuid.uuid4()),
                file=ok_file,
                db=FakeDB([FakeExecuteResult(value=requester)]),
            )
        ok_file.close.assert_awaited_once()
        self.assertEqual(submit_result["detail"], "مدرک برای بررسی ارسال شد")

    async def test_submit_identity_validates_type_builds_payload_and_closes_upload(self):
        rid = uuid.uuid4()
        recovery = make_recovery(status=SingleSessionRecoveryStatus.IDENTITY_VERIFICATION_REQUESTED)
        requester = make_user(mobile_number="09120000000")

        with patch(
            "api.routers.sessions.get_active_recovery_request_for_login_request",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await submit_single_session_recovery_identity(str(rid), file=FakeUploadFile(b"x"), db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 404)

        bad_file = FakeUploadFile(b"x", content_type="application/x-msdownload")
        with patch(
            "api.routers.sessions.get_active_recovery_request_for_login_request",
            new=AsyncMock(return_value=recovery),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await submit_single_session_recovery_identity(
                    str(rid),
                    file=bad_file,
                    db=FakeDB([FakeExecuteResult(value=requester)]),
                )
        self.assertEqual(exc_info.exception.status_code, 400)

        ok_file = FakeUploadFile(b"img", filename="id.jpg", content_type="image/jpeg")
        db = FakeDB([FakeExecuteResult(value=requester)])
        with patch(
            "api.routers.sessions.get_active_recovery_request_for_login_request",
            new=AsyncMock(return_value=recovery),
        ), patch(
            "api.routers.sessions._expire_recovery_if_needed",
            new=AsyncMock(return_value=False),
        ), patch(
            "api.routers.sessions._build_identity_message_payload",
            new=AsyncMock(return_value=(MessageType.IMAGE, '{"file_id":"1"}', "caption")),
        ) as payload_mock, patch(
            "api.routers.sessions._clear_recovery_admin_action_messages",
            new=AsyncMock(),
        ), patch(
            "api.routers.sessions._deliver_identity_submission_messages",
            new=AsyncMock(),
        ) as deliver_mock, patch("api.routers.sessions.send_sms"):
            result = await submit_single_session_recovery_identity(
                str(rid),
                file=ok_file,
                caption=" caption ",
                db=db,
            )

        payload_mock.assert_awaited_once()
        deliver_mock.assert_awaited_once()
        ok_file.close.assert_awaited_once()
        self.assertEqual(recovery.status, SingleSessionRecoveryStatus.IDENTITY_SUBMITTED)
        self.assertEqual(result["detail"], "مدرک برای بررسی ارسال شد")
