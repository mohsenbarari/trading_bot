import { describe, expect, it } from 'vitest'
import {
  buildChatActivityBody,
  buildChatActivityEndpoint,
  buildChatMessagesEndpoint,
  buildChatReadEndpoint,
  buildChatSendBody,
  buildChatSendEndpoint,
  isChannelConversationKey,
  isNamedRoomKind,
  isRoomConversationKey,
  resolveChannelChatId,
  resolveRoomConversationKey,
} from './chatRoomRouting'

describe('chatRoomRouting', () => {
  it('resolves room keys and endpoints for direct and room conversations', () => {
    expect(isRoomConversationKey(-12)).toBe(true)
    expect(isChannelConversationKey(-12)).toBe(true)
    expect(resolveChannelChatId(-12)).toBe(12)
    expect(isNamedRoomKind('group')).toBe(true)
    expect(resolveRoomConversationKey('channel', '15')).toBe(-15)
    expect(resolveRoomConversationKey('other', 15)).toBeNull()
    expect(resolveRoomConversationKey('group', 'broken')).toBeNull()

    expect(buildChatMessagesEndpoint(-15, 'limit=20')).toBe('/chat/rooms/15/messages?limit=20')
    expect(buildChatMessagesEndpoint(8, 'limit=20')).toBe('/chat/messages/8?limit=20')
    expect(buildChatReadEndpoint(-15)).toBe('/chat/rooms/15/read')
    expect(buildChatReadEndpoint(8)).toBe('/chat/read/8')
    expect(buildChatSendEndpoint(-15)).toBe('/chat/rooms/15/send')
    expect(buildChatSendEndpoint(8)).toBe('/chat/send')
    expect(buildChatActivityEndpoint(-15)).toBe('/chat/rooms/15/activity')
  })

  it('builds send and activity bodies for direct and room messages', () => {
    expect(buildChatSendBody(9, {
      content: 'hello',
      message_type: 'text',
      reply_to_message_id: 55,
    })).toEqual({
      receiver_id: 9,
      content: 'hello',
      message_type: 'text',
      reply_to_message_id: 55,
    })

    expect(buildChatSendBody(-9, {
      content: 'hello',
      message_type: 'text',
    })).toEqual({
      content: 'hello',
      message_type: 'text',
    })

    expect(buildChatActivityBody(9, { activity: 'typing', active: false })).toEqual({
      receiver_id: 9,
      activity: 'typing',
      active: false,
    })
    expect(buildChatActivityBody(-9, { activity: 'uploading_file' })).toEqual({
      activity: 'uploading_file',
      active: true,
    })
  })
})