from datetime import datetime
from types import SimpleNamespace
import unittest

from pydantic import ValidationError

from api.routers import chat_schemas
from core.enums import ChatType, MessageType


class ChatSchemasTests(unittest.TestCase):
    def test_message_read_from_orm_with_forwarding_and_reply_filter(self):
        now = datetime(2025, 1, 1, 12, 0, 0)
        deleted_reply = chat_schemas.MessageReplyRead(
            id=9,
            sender_id=2,
            content='old',
            message_type=MessageType.TEXT,
            is_deleted=True,
        )
        message = SimpleNamespace(
            id=1,
            sender_id=2,
            receiver_id=3,
            content='hello',
            message_type=MessageType.TEXT,
            is_read=True,
            is_deleted=False,
            updated_at=now,
            created_at=now,
            reply_to_message=deleted_reply,
            reactions=[{'emoji': '🔥', 'user_id': 7}],
            forwarded_from_id=4,
            forwarded_from=SimpleNamespace(account_name='origin'),
            sender=SimpleNamespace(account_name='sender-name'),
        )

        parsed = chat_schemas.MessageRead.from_orm_with_forwarding(message)

        self.assertEqual(parsed.forwarded_from_name, 'origin')
        self.assertEqual(parsed.sender_name, 'sender-name')
        self.assertIsNone(parsed.reply_to_message)
        self.assertEqual(parsed.reactions[0].emoji, '🔥')

        with_reply = chat_schemas.MessageRead(
            id=2,
            sender_id=2,
            receiver_id=3,
            content='reply',
            message_type=MessageType.TEXT,
            is_read=False,
            created_at=now,
            reply_to_message=chat_schemas.MessageReplyRead(
                id=10,
                sender_id=3,
                content='keep',
                message_type=MessageType.TEXT,
                is_deleted=False,
            ),
        )
        self.assertEqual(with_reply.reply_to_message.id, 10)

    def test_reaction_toggle_and_request_models(self):
        toggle = chat_schemas.MessageReactionToggle(emoji=' 🔥 ')
        self.assertEqual(toggle.emoji, '🔥')

        with self.assertRaises(ValidationError):
            chat_schemas.MessageReactionToggle(emoji='not-supported')

        self.assertEqual(chat_schemas.TypingSignal(receiver_id=5).receiver_id, 5)
        self.assertEqual(chat_schemas.MessageSend(receiver_id=5, content='hi').message_type, MessageType.TEXT)
        self.assertEqual(chat_schemas.RoomMessageSend(content='hi').message_type, MessageType.TEXT)
        self.assertEqual(chat_schemas.MessageUpdate(content='edit').content, 'edit')
        self.assertEqual(chat_schemas.GroupMemberAddRequest(user_id=4).user_id, 4)

    def test_group_request_normalization(self):
        request = chat_schemas.GroupCreateRequest(title='  My Group  ', description='  hello  ', member_ids=[0, 2, 2, 3, -1])
        self.assertEqual(request.title, 'My Group')
        self.assertEqual(request.description, 'hello')
        self.assertEqual(request.member_ids, [2, 3])

        update_request = chat_schemas.GroupUpdateRequest(title='  Updated  ', description='   ')
        self.assertEqual(update_request.title, 'Updated')
        self.assertIsNone(update_request.description)

        with self.assertRaises(ValidationError):
            chat_schemas.GroupCreateRequest(title='   ', member_ids=[])

        with self.assertRaises(ValidationError):
            chat_schemas.GroupUpdateRequest(title='   ')

    def test_channel_request_validators_and_response_models(self):
        create_request = chat_schemas.ChannelCreateRequest(title='  Channel  ', description='  desc  ')
        self.assertEqual(create_request.title, 'Channel')
        self.assertEqual(create_request.description, 'desc')

        update_request = chat_schemas.ChannelUpdateRequest(title='  New  ', description='   ')
        self.assertEqual(update_request.title, 'New')
        self.assertIsNone(update_request.description)

        bulk_request = chat_schemas.ChannelBulkMemberAddRequest(user_ids=[0, 3, 3, 5], select_all_active_users=False)
        self.assertEqual(bulk_request.user_ids, [3, 5])

        select_all_request = chat_schemas.ChannelBulkMemberAddRequest(select_all_active_users=True)
        self.assertTrue(select_all_request.select_all_active_users)

        with self.assertRaises(ValidationError):
            chat_schemas.ChannelBulkMemberAddRequest(user_ids=[1], select_all_active_users=True)
        with self.assertRaises(ValidationError):
            chat_schemas.ChannelBulkMemberAddRequest(user_ids=[], select_all_active_users=False)

        role_request = chat_schemas.ChannelMemberUpdateRequest(role=' ADMIN ')
        self.assertEqual(role_request.role, 'admin')

        remove_request = chat_schemas.ChannelMemberUpdateRequest(remove_member=True)
        self.assertTrue(remove_request.remove_member)

        with self.assertRaises(ValidationError):
            chat_schemas.ChannelMemberUpdateRequest(role='owner')
        with self.assertRaises(ValidationError):
            chat_schemas.ChannelMemberUpdateRequest(role='member', remove_member=True)
        with self.assertRaises(ValidationError):
            chat_schemas.ChannelMemberUpdateRequest()

        now = datetime(2025, 1, 1, 12, 0, 0)
        channel = chat_schemas.ChannelRoomRead(
            id=7,
            type=ChatType.CHANNEL,
            title='News',
            created_at=now,
        )
        group = chat_schemas.GroupRoomRead(
            id=8,
            type=ChatType.GROUP,
            title='Group',
            created_at=now,
        )
        conversation = chat_schemas.ConversationRead(id=-8, other_user_id=8, other_user_name='Group', room_kind='group', chat_id=8)
        poll = chat_schemas.PollResponse(total_unread=1, unread_chats_count=1, conversations_with_unread=[{'id': 1}])
        sticker_pack = chat_schemas.StickerPack(id='default', name='Pack', stickers=['🔥'])
        create_response = chat_schemas.ChannelCreateResponse(channel=channel)
        detail = chat_schemas.GroupDetailRead(
            group=group,
            members=[
                chat_schemas.GroupMemberRead(
                    user_id=1,
                    account_name='acct',
                    full_name='Full',
                    mobile_number='0912',
                    role='admin',
                    joined_at=now,
                )
            ],
        )
        candidates = chat_schemas.ChannelInviteCandidateListResponse(
            items=[
                chat_schemas.ChannelInviteCandidateRead(
                    user_id=1,
                    account_name='acct',
                    full_name='Full',
                    mobile_number='0912',
                )
            ],
            total=1,
            active_total=1,
        )
        bulk_response = chat_schemas.ChannelBulkMemberAddResponse(
            chat_id=7,
            processed_user_ids=[1],
            added_count=1,
            reactivated_count=0,
            already_member_count=0,
            member_count=1,
        )
        mutation = chat_schemas.ChannelMemberMutationResponse(chat_id=7, user_id=1, member_count=1)
        group_create_response = chat_schemas.GroupCreateResponse(group=group)
        group_mutation = chat_schemas.GroupMemberMutationResponse(chat_id=8, user_id=1, member_count=1)

        self.assertEqual(create_response.channel.title, 'News')
        self.assertEqual(group_create_response.group.title, 'Group')
        self.assertEqual(conversation.room_kind, 'group')
        self.assertEqual(poll.total_unread, 1)
        self.assertEqual(sticker_pack.stickers, ['🔥'])
        self.assertEqual(detail.members[0].account_name, 'acct')
        self.assertEqual(candidates.total, 1)
        self.assertEqual(bulk_response.chat_id, 7)
        self.assertEqual(mutation.user_id, 1)
        self.assertEqual(group_mutation.user_id, 1)


if __name__ == '__main__':
    unittest.main()