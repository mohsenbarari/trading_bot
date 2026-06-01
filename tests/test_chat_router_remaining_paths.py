import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

from fastapi import BackgroundTasks, HTTPException
from jose import JWTError

from api.routers.chat import (
    _build_direct_message_accountant_serializer,
    _build_upload_session_event_payload,
    _enrich_direct_message_reads,
    _has_active_upload_sessions_for_room,
    _increment_upload_session_observability_counters,
    _list_batch_upload_sessions,
    _publish_upload_session_runtime_event,
    _serialize_pinned_message_state,
    _serialize_direct_message_with_accountant_contract,
    _serialize_direct_messages_with_accountant_contract,
    commit_upload_batch_endpoint,
    create_channel,
    create_group,
    get_chat_file,
    get_direct_pinned_message,
    get_room_pinned_message,
    get_stickers,
    mark_room_conversation_unread,
    mute_room_conversation,
    poll_messages,
    patch_group,
    pin_room_conversation,
    reorder_direct_conversation_pin,
    reorder_room_conversation_pin,
    toggle_message_pin,
    upload_chat_media,
    update_channel,
)
from api.routers.chat_schemas import MessageRead
from core.enums import ChatType
from models.upload_session import UploadBatchStatus


def make_message_read(message_id: int, *, content: str = '{"file_id":"f"}', message_type: str = 'text') -> MessageRead:
    return MessageRead(
        id=message_id,
        sender_id=5,
        receiver_id=9,
        content=content,
        message_type=message_type,
        is_read=False,
        created_at=datetime.now(timezone.utc),
    )


class FakeExecuteResult:
    def __init__(self, scalar_value):
        self._scalar_value = scalar_value

    def scalar_one_or_none(self):
        return self._scalar_value


class FakeScalarsExecuteResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return SimpleNamespace(all=lambda: self._values)


class FakeMappingsExecuteResult:
    def __init__(self, values):
        self._values = values

    def mappings(self):
        return SimpleNamespace(all=lambda: self._values)


class FakeDB:
    def __init__(self, *, execute_results=None, get_values=None):
        self.execute_results = list(execute_results or [])
        self.get_values = dict(get_values or {})

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError('Unexpected execute() call')
        return self.execute_results.pop(0)

    async def get(self, _model, primary_key):
        return self.get_values.get(primary_key)


class ChatRouterRemainingPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_session_helper_paths_cover_payload_counters_and_room_lookup(self):
        session = SimpleNamespace(
            id='sess-1',
            batch_id='batch-1',
            room_kind=SimpleNamespace(value='group'),
            target_id=77,
            media_type=SimpleNamespace(value='image'),
            status=SimpleNamespace(value='uploading'),
            received_bytes=10,
            total_bytes=20,
            next_offset=10,
            final_chat_file_id='file-1',
            retry_count=2,
            last_error='boom',
            preview_metadata={'thumbnail': 'thumb'},
        )

        payload = _build_upload_session_event_payload(
            event_name='progress',
            session=session,
            batch_status=UploadBatchStatus.COMMITTED,
        )

        self.assertEqual(payload['batch_status'], UploadBatchStatus.COMMITTED.value)
        self.assertEqual(payload['last_error'], 'boom')
        self.assertEqual(payload['preview_metadata'], {'thumbnail': 'thumb'})

        redis = SimpleNamespace(hincrby=AsyncMock())
        with patch('api.routers.chat.get_redis_client', return_value=redis):
            await _increment_upload_session_observability_counters(
                event_name='progress',
                room_kind=SimpleNamespace(value='group'),
                media_type=SimpleNamespace(value='image'),
            )

        self.assertEqual(redis.hincrby.await_count, 3)

        with patch('api.routers.chat.get_redis_client', side_effect=RuntimeError('redis down')), patch(
            'api.routers.chat.logger.debug'
        ) as debug_mock:
            await _increment_upload_session_observability_counters(
                event_name='failed',
                room_kind=None,
                media_type=None,
            )

        debug_mock.assert_called_once()

        db = FakeDB(execute_results=[FakeExecuteResult(object()), FakeExecuteResult(None)])
        self.assertTrue(
            await _has_active_upload_sessions_for_room(
                db,
                owner_user_id=5,
                room_kind='direct',
                target_id=9,
            )
        )
        self.assertFalse(
            await _has_active_upload_sessions_for_room(
                db,
                owner_user_id=5,
                room_kind='group',
                target_id=77,
            )
        )

    async def test_direct_message_serializer_helpers_cover_empty_and_enriched_paths(self):
        db = SimpleNamespace(execute=AsyncMock())
        empty: list[MessageRead] = []
        self.assertIs(await _enrich_direct_message_reads(db, empty), empty)

        base_message = make_message_read(10)
        enriched_payload = {
            **base_message.model_dump(),
            'sender_resolved_from_accountant_id': 44,
            'sender_profile_user_id': 101,
            'sender_profile_account_name': 'owner-101',
            'recovery_action': None,
        }
        with patch('api.routers.chat.load_accountant_chat_identity_map', new=AsyncMock(return_value={10: 'x'})) as identity_mock, patch(
            'api.routers.chat.collect_message_identity_user_ids',
            return_value={5, 9},
        ) as collect_mock, patch(
            'api.routers.chat.apply_accountant_identity_to_message_payload',
            side_effect=lambda payload, _identity_map: {**payload, **{k: v for k, v in enriched_payload.items() if k not in payload or payload[k] != v}},
        ) as apply_mock, patch(
            'api.routers.chat.build_recovery_action_map_for_admin_messages',
            new=AsyncMock(return_value={
                10: {
                    'recovery_id': 'recovery-10',
                    'status': 'pending',
                    'prompt_type': 'initial_request',
                }
            }),
        ) as recovery_mock:
            enriched = await _enrich_direct_message_reads(db, [base_message], viewer_user_id=7)

        collect_mock.assert_called_once_with([base_message])
        identity_mock.assert_awaited_once_with(db, {5, 9})
        apply_mock.assert_called_once()
        recovery_mock.assert_awaited_once_with(db, admin_user_id=7, message_ids=[10])
        self.assertEqual(enriched[0].sender_resolved_from_accountant_id, 44)
        self.assertEqual(enriched[0].sender_profile_user_id, 101)
        self.assertEqual(enriched[0].recovery_action.recovery_id, 'recovery-10')
        self.assertEqual(enriched[0].recovery_action.status, 'pending')

        sentinel_one = make_message_read(11)
        sentinel_many = [make_message_read(12)]
        with patch(
            'api.routers.chat.serialize_direct_message_for_response',
            return_value=base_message,
        ) as serialize_one_mock, patch(
            'api.routers.chat._enrich_direct_message_reads',
            new=AsyncMock(return_value=[sentinel_one]),
        ) as enrich_mock:
            result = await _serialize_direct_message_with_accountant_contract(db, SimpleNamespace(id=10), viewer_user_id=8)

        serialize_one_mock.assert_called_once()
        enrich_mock.assert_awaited_once()
        self.assertIs(result, sentinel_one)

        with patch(
            'api.routers.chat.serialize_direct_messages_for_response',
            return_value=[base_message],
        ) as serialize_many_mock, patch(
            'api.routers.chat._enrich_direct_message_reads',
            new=AsyncMock(return_value=sentinel_many),
        ) as enrich_many_mock:
            result_many = await _serialize_direct_messages_with_accountant_contract(db, [SimpleNamespace(id=10)], viewer_user_id=9)

        serialize_many_mock.assert_called_once()
        enrich_many_mock.assert_awaited_once_with(db, [base_message], viewer_user_id=9)
        self.assertIs(result_many, sentinel_many)

        serializer_payload = {
            **base_message.model_dump(),
            'sender_resolved_from_accountant_id': 91,
            'sender_profile_user_id': 202,
            'sender_profile_account_name': 'owner-202',
        }
        with patch.object(MessageRead, 'from_orm_with_forwarding', return_value=base_message) as from_orm_mock, patch(
            'api.routers.chat.apply_accountant_identity_to_message_payload',
            return_value=serializer_payload,
        ) as apply_serializer_mock:
            serializer = _build_direct_message_accountant_serializer({})
            serialized = serializer(SimpleNamespace(id=99))

        from_orm_mock.assert_called_once()
        apply_serializer_mock.assert_called_once()
        self.assertEqual(serialized.sender_resolved_from_accountant_id, 91)
        self.assertEqual(serialized.sender_profile_user_id, 202)

    async def test_direct_pinned_routes_cover_reorder_missing_user_and_existing_chat_paths(self):
        current_user = SimpleNamespace(id=5)
        db = object()
        member = SimpleNamespace(chat_id=44)
        reordered_member = SimpleNamespace(chat_id=44, pin_order=3)

        with patch('api.routers.chat.set_direct_chat_pin_state', new=AsyncMock(return_value=member)) as pin_mock, patch(
            'api.routers.chat.reorder_chat_member_pin_order',
            new=AsyncMock(return_value=reordered_member),
        ) as reorder_mock:
            reordered = await reorder_direct_conversation_pin(
                user_id=9,
                data=SimpleNamespace(direction='up'),
                current_user=current_user,
                db=db,
            )

        pin_mock.assert_awaited_once_with(db, actor=current_user, other_user_id=9, pinned=True)
        reorder_mock.assert_awaited_once_with(db, user_id=5, chat_id=44, direction='up')
        self.assertEqual(reordered.pin_order, 3)

        with self.assertRaises(HTTPException) as exc_info:
            await get_direct_pinned_message(
                user_id=9,
                current_user=current_user,
                db=FakeDB(get_values={}),
            )
        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, 'User not found')

        target = SimpleNamespace(id=9)
        db = FakeDB(get_values={9: target})
        serialized_none = SimpleNamespace(kind='none')
        with patch('api.routers.chat.get_existing_direct_chat', new=AsyncMock(return_value=None)) as chat_mock, patch(
            'api.routers.chat.get_pinned_message_for_chat',
            new=AsyncMock(),
        ) as pinned_mock, patch(
            'api.routers.chat._serialize_pinned_message_state',
            new=AsyncMock(return_value=serialized_none),
        ) as serialize_mock:
            result_none = await get_direct_pinned_message(user_id=9, current_user=current_user, db=db)

        chat_mock.assert_awaited_once_with(db, 5, 9)
        pinned_mock.assert_not_awaited()
        serialize_mock.assert_awaited_once_with(db=db, room_kind='direct', chat=None, message=None)
        self.assertIs(result_none, serialized_none)

        chat = SimpleNamespace(id=55, type=ChatType.DIRECT)
        pinned_message = SimpleNamespace(id=88)
        serialized = SimpleNamespace(kind='chat')
        with patch('api.routers.chat.get_existing_direct_chat', new=AsyncMock(return_value=chat)), patch(
            'api.routers.chat.get_pinned_message_for_chat',
            new=AsyncMock(return_value=pinned_message),
        ) as pinned_mock, patch(
            'api.routers.chat._serialize_pinned_message_state',
            new=AsyncMock(return_value=serialized),
        ) as serialize_mock:
            result = await get_direct_pinned_message(user_id=9, current_user=current_user, db=db)

        pinned_mock.assert_awaited_once_with(db, chat)
        serialize_mock.assert_awaited_once_with(db=db, room_kind='direct', chat=chat, message=pinned_message)
        self.assertIs(result, serialized)

    async def test_room_pin_and_pinned_message_routes_cover_guards_and_success_branches(self):
        current_user = SimpleNamespace(id=5)
        direct_room = SimpleNamespace(id=70, type=ChatType.DIRECT)
        group_room = SimpleNamespace(id=71, type=ChatType.GROUP)
        channel_room = SimpleNamespace(id=72, type=ChatType.CHANNEL)

        with patch('api.routers.chat.get_room_or_404', new=AsyncMock(return_value=direct_room)):
            with self.assertRaises(HTTPException) as exc_info:
                await pin_room_conversation(
                    chat_id=70,
                    data=SimpleNamespace(pinned=True),
                    current_user=current_user,
                    db=object(),
                )
        self.assertEqual(exc_info.exception.detail, 'Use the direct pin endpoint for personal chats')

        with patch('api.routers.chat.get_room_or_404', new=AsyncMock(return_value=direct_room)):
            with self.assertRaises(HTTPException) as exc_info:
                await reorder_room_conversation_pin(
                    chat_id=70,
                    data=SimpleNamespace(direction='down'),
                    current_user=current_user,
                    db=object(),
                )
        self.assertEqual(exc_info.exception.detail, 'Use the direct pin-order endpoint for personal chats')

        pinned_member = SimpleNamespace(chat_id=71, pin_order=8)
        reordered_member = SimpleNamespace(chat_id=71, pin_order=2)
        with patch('api.routers.chat.get_room_or_404', new=AsyncMock(return_value=group_room)), patch(
            'api.routers.chat.set_room_pin_state',
            new=AsyncMock(return_value=pinned_member),
        ) as pin_mock, patch(
            'api.routers.chat.reorder_chat_member_pin_order',
            new=AsyncMock(return_value=reordered_member),
        ) as reorder_mock:
            reordered = await reorder_room_conversation_pin(
                chat_id=71,
                data=SimpleNamespace(direction='up'),
                current_user=current_user,
                db=object(),
            )

        pin_mock.assert_awaited_once_with(ANY, chat=group_room, user_id=5, pinned=True)
        reorder_mock.assert_awaited_once_with(ANY, user_id=5, chat_id=71, direction='up')
        self.assertEqual(reordered.pin_order, 2)

        with patch('api.routers.chat.get_room_or_404', new=AsyncMock(return_value=direct_room)):
            with self.assertRaises(HTTPException) as exc_info:
                await get_room_pinned_message(chat_id=70, current_user=current_user, db=object())
        self.assertEqual(exc_info.exception.detail, 'Use the direct pinned-message endpoint for personal chats')

        group_serialized = SimpleNamespace(kind='group')
        with patch('api.routers.chat.get_room_or_404', new=AsyncMock(return_value=group_room)), patch(
            'api.routers.chat.get_active_group_member_or_403',
            new=AsyncMock(),
        ) as group_member_mock, patch(
            'api.routers.chat.get_pinned_message_for_chat',
            new=AsyncMock(return_value=SimpleNamespace(id=1)),
        ) as group_pin_mock, patch(
            'api.routers.chat._serialize_pinned_message_state',
            new=AsyncMock(return_value=group_serialized),
        ) as group_serialize_mock:
            group_result = await get_room_pinned_message(chat_id=71, current_user=current_user, db=object())

        group_member_mock.assert_awaited_once_with(ANY, chat=group_room, user_id=5)
        group_pin_mock.assert_awaited_once_with(ANY, group_room)
        group_serialize_mock.assert_awaited_once_with(db=ANY, room_kind='group', chat=group_room, message=ANY)
        self.assertIs(group_result, group_serialized)

        channel_serialized = SimpleNamespace(kind='channel')
        with patch('api.routers.chat.get_room_or_404', new=AsyncMock(return_value=channel_room)), patch(
            'api.routers.chat.get_active_channel_member_or_403',
            new=AsyncMock(),
        ) as channel_member_mock, patch(
            'api.routers.chat.get_pinned_message_for_chat',
            new=AsyncMock(return_value=SimpleNamespace(id=2)),
        ) as channel_pin_mock, patch(
            'api.routers.chat._serialize_pinned_message_state',
            new=AsyncMock(return_value=channel_serialized),
        ) as channel_serialize_mock:
            channel_result = await get_room_pinned_message(chat_id=72, current_user=current_user, db=object())

        channel_member_mock.assert_awaited_once_with(ANY, chat=channel_room, user_id=5)
        channel_pin_mock.assert_awaited_once_with(ANY, channel_room)
        channel_serialize_mock.assert_awaited_once_with(db=ANY, room_kind='channel', chat=channel_room, message=ANY)
        self.assertIs(channel_result, channel_serialized)

    async def test_room_mute_channel_avatar_toggle_pin_and_commit_fallback_paths(self):
        current_user = SimpleNamespace(id=5, account_name='owner')
        direct_room = SimpleNamespace(id=70, type=ChatType.DIRECT)
        with patch('api.routers.chat.get_room_or_404', new=AsyncMock(return_value=direct_room)):
            with self.assertRaises(HTTPException) as exc_info:
                await mute_room_conversation(
                    chat_id=70,
                    data=SimpleNamespace(muted=True),
                    current_user=current_user,
                    db=object(),
                )
        self.assertEqual(exc_info.exception.detail, 'Use the direct mute endpoint for personal chats')

        with patch('api.routers.chat.get_room_or_404', new=AsyncMock(return_value=direct_room)):
            with self.assertRaises(HTTPException) as exc_info:
                await mark_room_conversation_unread(
                    chat_id=70,
                    data=SimpleNamespace(unread=True),
                    current_user=current_user,
                    db=object(),
                )
        self.assertEqual(exc_info.exception.detail, 'Use the direct unread endpoint for personal chats')

        channel = SimpleNamespace(
            id=88,
            title='VIP',
            description='alerts',
            avatar_file_id='avatar-55',
            created_by_id=5,
            is_system=False,
            is_mandatory=False,
            created_at=datetime(2026, 5, 1, 8, 0, 0),
            type=ChatType.CHANNEL,
        )
        with patch('api.routers.chat.resolve_owned_avatar_file_id', new=AsyncMock(return_value='avatar-55')) as resolve_mock, patch(
            'api.routers.chat.create_optional_channel',
            new=AsyncMock(return_value=channel),
        ) as create_mock, patch(
            'api.routers.chat.count_active_chat_members',
            new=AsyncMock(return_value=4),
        ):
            created = await create_channel(
                data=SimpleNamespace(title='VIP', description='alerts', avatar_file_id='avatar-55'),
                current_user=current_user,
                db=object(),
            )

        resolve_mock.assert_awaited_once_with(ANY, actor_id=5, avatar_file_id='avatar-55')
        create_mock.assert_awaited_once_with(
            ANY,
            creator=current_user,
            title='VIP',
            description='alerts',
            avatar_file_id='avatar-55',
        )
        self.assertEqual(created.channel.avatar_file_id, 'avatar-55')

        with patch('api.routers.chat.get_channel_or_404', new=AsyncMock(return_value=channel)), patch(
            'api.routers.chat.resolve_owned_avatar_file_id',
            new=AsyncMock(return_value='avatar-77'),
        ) as update_resolve_mock, patch(
            'api.routers.chat.update_manageable_channel_metadata',
            new=AsyncMock(return_value=channel),
        ) as update_mock, patch(
            'api.routers.chat.count_active_chat_members',
            new=AsyncMock(return_value=6),
        ):
            updated = await update_channel(
                chat_id=88,
                data=SimpleNamespace(title='VIP', description='alerts', avatar_file_id='avatar-77'),
                current_user=current_user,
                db=object(),
            )

        update_resolve_mock.assert_awaited_once_with(ANY, actor_id=5, avatar_file_id='avatar-77')
        update_mock.assert_awaited_once_with(
            ANY,
            chat=channel,
            title='VIP',
            description='alerts',
            avatar_file_id='avatar-77',
        )
        self.assertEqual(updated.member_count, 6)

        chat = SimpleNamespace(type=ChatType.GROUP)
        pinned_state = SimpleNamespace(kind='pinned')
        with patch('api.routers.chat.apply_message_pin_state', new=AsyncMock(return_value=(chat, SimpleNamespace(id=9)))) as pin_state_mock, patch(
            'api.routers.chat._serialize_pinned_message_state',
            new=AsyncMock(return_value=pinned_state),
        ) as serialize_mock:
            pin_result = await toggle_message_pin(
                message_id=9,
                data=SimpleNamespace(pinned=True),
                current_user=current_user,
                db=object(),
            )

        pin_state_mock.assert_awaited_once_with(ANY, message_id=9, actor_id=5, pinned=True)
        serialize_mock.assert_awaited_once_with(db=ANY, room_kind='group', chat=chat, message=ANY)
        self.assertIs(pin_result, pinned_state)

        batch = SimpleNamespace(id='batch-1')
        commit_result = SimpleNamespace(
            batch=SimpleNamespace(id='batch-1', status=UploadBatchStatus.COMMITTED, committed_items=2),
            target=SimpleNamespace(target_id=9, receiver=SimpleNamespace(id=9), chat=None),
            messages=[SimpleNamespace(id=101), SimpleNamespace(id=102)],
        )
        serialized_messages = [make_message_read(101, message_type='document')]
        fallback_message = make_message_read(102, message_type='document')
        published = []
        published_sender_names = []

        async def capture_publish_direct_message_event(*, message, serializer, **_kwargs):
            published.append(serializer(message))
            published_sender_names.append(_kwargs.get('sender_name'))

        background_tasks = BackgroundTasks()
        with patch('api.routers.chat.get_upload_batch_for_current_user', new=AsyncMock(return_value=batch)), patch(
            'api.routers.chat.commit_upload_batch',
            new=AsyncMock(return_value=commit_result),
        ), patch(
            'api.routers.chat._serialize_direct_messages_with_accountant_contract',
            new=AsyncMock(return_value=serialized_messages),
        ), patch(
            'api.routers.chat.publish_direct_message_event',
            new=AsyncMock(side_effect=capture_publish_direct_message_event),
        ), patch(
            'api.routers.chat._list_batch_upload_sessions',
            new=AsyncMock(return_value=[]),
        ), patch(
            'api.routers.chat._publish_upload_session_runtime_event',
            new=AsyncMock(),
        ), patch.object(
            MessageRead,
            'from_orm_with_forwarding',
            return_value=fallback_message,
        ) as fallback_mock:
            result = await commit_upload_batch_endpoint(
                batch_id='batch-1',
                background_tasks=background_tasks,
                current_user=current_user,
                db=object(),
            )

            self.assertEqual(published, [])
            self.assertEqual(len(background_tasks.tasks), 2)
            await background_tasks.tasks[0]()
            fallback_mock.assert_called_once_with(commit_result.messages[1])
        self.assertEqual([message.id for message in published], [101, 102])
        self.assertEqual(published_sender_names, ['owner', 'owner'])
        self.assertEqual(result.batch_id, 'batch-1')

    async def test_upload_runtime_and_pinned_state_helpers_cover_remaining_chat_router_branches(self):
        current_user = SimpleNamespace(id=5, account_name='owner')
        session = SimpleNamespace(
            id='upload-session-1',
            batch_id='upload-batch-1',
            room_kind=SimpleNamespace(value='group'),
            target_id=77,
            media_type=SimpleNamespace(value='image'),
            status=SimpleNamespace(value='uploading'),
        )

        with patch('api.routers.chat.publish_user_event', new=AsyncMock()) as publish_user_event_mock, patch(
            'api.routers.chat._increment_upload_session_observability_counters',
            new=AsyncMock(),
        ) as increment_mock, patch(
            'api.routers.chat._has_active_upload_sessions_for_room',
            new=AsyncMock(return_value=False),
        ) as active_mock, patch(
            'api.routers.chat.publish_room_activity_event',
            new=AsyncMock(),
        ) as publish_room_mock:
            db = FakeDB(get_values={77: None})
            await _publish_upload_session_runtime_event(
                db=db,
                current_user=current_user,
                session=session,
                event_name='progress',
            )

        publish_user_event_mock.assert_awaited_once()
        increment_mock.assert_awaited_once()
        active_mock.assert_awaited_once_with(db, owner_user_id=5, room_kind='group', target_id=77)
        publish_room_mock.assert_not_awaited()

        listed_sessions = [SimpleNamespace(id='s1'), SimpleNamespace(id='s2')]
        batch_sessions = await _list_batch_upload_sessions(
            FakeDB(execute_results=[FakeScalarsExecuteResult(listed_sessions)]),
            batch_id='upload-batch-1',
        )
        self.assertEqual(batch_sessions, listed_sessions)

        serialized_message = make_message_read(17)
        chat = SimpleNamespace(id=71, pinned_message_at=datetime.now(timezone.utc), pinned_message_by_id=3)
        with patch(
            'api.routers.chat._serialize_direct_message_with_accountant_contract',
            new=AsyncMock(return_value=serialized_message),
        ) as serialize_message_mock:
            pinned_state = await _serialize_pinned_message_state(
                db=object(),
                room_kind='group',
                chat=chat,
                message=SimpleNamespace(id=17),
            )

        serialize_message_mock.assert_awaited_once()
        self.assertEqual(pinned_state.chat_id, 71)
        self.assertEqual(pinned_state.message.id, 17)

    async def test_group_avatar_poll_and_sticker_paths_cover_remaining_chat_router_branches(self):
        current_user = SimpleNamespace(id=5)
        group = SimpleNamespace(
            id=77,
            title='Desk',
            description='ops',
            avatar_file_id='avatar-1',
            created_by_id=5,
            max_members=50,
            created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            type=ChatType.GROUP,
        )

        with patch('api.routers.chat.is_user_accountant', new=AsyncMock(return_value=False)), patch(
            'api.routers.chat.is_user_customer',
            new=AsyncMock(return_value=False),
        ), patch(
            'api.routers.chat.resolve_owned_avatar_file_id',
            new=AsyncMock(return_value='avatar-1'),
        ) as resolve_create_mock, patch(
            'api.routers.chat.create_group_chat',
            new=AsyncMock(return_value=group),
        ) as create_group_mock, patch(
            'api.routers.chat.count_active_chat_members',
            new=AsyncMock(return_value=3),
        ):
            created = await create_group(
                data=SimpleNamespace(title='Desk', member_ids=[9], description='ops', avatar_file_id='avatar-1'),
                current_user=current_user,
                db=object(),
            )

        resolve_create_mock.assert_awaited_once_with(ANY, actor_id=5, avatar_file_id='avatar-1')
        create_group_mock.assert_awaited_once_with(
            ANY,
            creator=current_user,
            title='Desk',
            member_ids=[9],
            description='ops',
            avatar_file_id='avatar-1',
        )
        self.assertEqual(created.group.avatar_file_id, 'avatar-1')

        with patch('api.routers.chat.get_group_or_404', new=AsyncMock(return_value=group)), patch(
            'api.routers.chat.get_active_group_admin_or_403',
            new=AsyncMock(return_value=SimpleNamespace(role=SimpleNamespace(value='admin'))),
        ), patch(
            'api.routers.chat.resolve_owned_avatar_file_id',
            new=AsyncMock(return_value='avatar-2'),
        ) as resolve_update_mock, patch(
            'api.routers.chat.update_group_chat',
            new=AsyncMock(return_value=group),
        ) as update_group_mock, patch(
            'api.routers.chat.count_active_chat_members',
            new=AsyncMock(return_value=4),
        ):
            updated = await patch_group(
                chat_id=77,
                data=SimpleNamespace(title='Desk', description='ops', avatar_file_id='avatar-2'),
                current_user=current_user,
                db=object(),
            )

        resolve_update_mock.assert_awaited_once_with(ANY, actor_id=5, avatar_file_id='avatar-2')
        update_group_mock.assert_awaited_once_with(
            ANY,
            chat=group,
            title='Desk',
            description='ops',
            avatar_file_id='avatar-2',
        )
        self.assertEqual(updated.avatar_file_id, 'avatar-1')

        poll_db = FakeDB(execute_results=[FakeMappingsExecuteResult([
            object(),
            {
                'other_user_id': 9,
                'other_user_name': 'Ali',
                'unread_count': 2,
                'is_muted': True,
                'other_user_is_deleted': False,
            },
        ])])
        with patch(
            'api.routers.chat.get_active_customer_relation_for_customer',
            new=AsyncMock(return_value=None),
        ), patch('api.routers.chat.build_direct_conversation_list_stmt', return_value=object()), patch(
            'api.routers.chat.list_group_conversations',
            new=AsyncMock(return_value=[SimpleNamespace(other_user_id=-10, other_user_name='Desk', unread_count=1, is_muted=False, other_user_is_deleted=False)]),
        ), patch(
            'api.routers.chat.list_channel_conversations',
            new=AsyncMock(return_value=[]),
        ):
            poll_response = await poll_messages(current_user=current_user, db=poll_db)

        self.assertEqual(poll_response.total_unread, 3)
        self.assertEqual(poll_response.unread_chats_count, 2)
        self.assertEqual(poll_response.muted_conversation_ids, [9])

        stickers = await get_stickers()
        self.assertEqual([pack.id for pack in stickers], ['emotions', 'actions', 'trade'])

    async def test_chat_media_upload_and_secure_file_paths_cover_remaining_chat_router_branches(self):
        db = SimpleNamespace(commit=AsyncMock(), get=AsyncMock())
        upload_file = SimpleNamespace(
            read=AsyncMock(return_value=b'abc'),
            filename='pic.png',
            content_type='image/png',
            close=AsyncMock(),
        )
        file_result = SimpleNamespace(
            chat_file=SimpleNamespace(
                id='file-1',
                thumbnail='thumb',
                file_name='pic.png',
                mime_type='image/png',
                size=3,
            ),
            width=100,
            height=60,
        )
        with patch('api.routers.chat.persist_chat_media_file_bytes', new=AsyncMock(return_value=file_result)) as persist_mock:
            uploaded = await upload_chat_media(
                file=upload_file,
                thumbnail='thumb',
                current_user=SimpleNamespace(id=5),
                db=db,
            )

        persist_mock.assert_awaited_once()
        db.commit.assert_awaited_once()
        upload_file.close.assert_awaited_once()
        self.assertEqual(uploaded['width'], 100)
        self.assertEqual(uploaded['height'], 60)

        with self.assertRaises(HTTPException) as exc_info:
            await get_chat_file(file_id='file-1', db=FakeDB(), token=None)
        self.assertEqual(exc_info.exception.detail, 'Token is missing')

        with patch('api.routers.chat.jwt.decode', side_effect=JWTError('bad token')):
            with self.assertRaises(HTTPException) as exc_info:
                await get_chat_file(file_id='file-1', db=FakeDB(), token='bad')
        self.assertEqual(exc_info.exception.detail, 'Invalid token')

        with patch('api.routers.chat.jwt.decode', return_value={'sub': '5'}):
            with self.assertRaises(HTTPException) as exc_info:
                await get_chat_file(file_id='missing', db=FakeDB(get_values={}), token='good')
        self.assertEqual(exc_info.exception.detail, 'File not found')

        missing_disk_file = SimpleNamespace(s3_key='/tmp/missing-chat-file', mime_type='image/png', file_name='pic.png')
        with patch('api.routers.chat.jwt.decode', return_value={'sub': '5'}), patch('api.routers.chat.os.path.exists', return_value=False):
            with self.assertRaises(HTTPException) as exc_info:
                await get_chat_file(file_id='file-1', db=FakeDB(get_values={'file-1': missing_disk_file}), token='good')
        self.assertEqual(exc_info.exception.detail, 'File not found on disk')

        chat_file = SimpleNamespace(s3_key='/tmp/chat-file', mime_type='image/png', file_name='pic.png')
        sentinel_response = object()
        with patch('api.routers.chat.jwt.decode', return_value={'sub': '5'}), patch(
            'api.routers.chat.os.path.exists',
            return_value=True,
        ), patch('fastapi.responses.FileResponse', return_value=sentinel_response) as file_response_mock:
            response = await get_chat_file(
                file_id='file-1',
                db=FakeDB(get_values={'file-1': chat_file}),
                token='good',
            )

        file_response_mock.assert_called_once_with(
            path='/tmp/chat-file',
            media_type='image/png',
            filename='pic.png',
        )
        self.assertIs(response, sentinel_response)


if __name__ == '__main__':
    unittest.main()
