import { describe, expect, it } from 'vitest'

import {
  buildMessengerActivityLabel,
  getMessengerMediaDownloadPatch,
  isMessengerRuntimeEventForConversation,
  normalizeMessengerRealtimeActivityPayload,
  resolveMessengerRealtimeConversationKey,
} from './messengerStage6MediaRealtime'

describe('messengerStage6MediaRealtime', () => {
  it('resolves direct and room conversation keys from realtime payloads', () => {
    expect(resolveMessengerRealtimeConversationKey({ sender_id: '42' })).toBe(42)
    expect(resolveMessengerRealtimeConversationKey({ room_kind: 'group', chat_id: '17', sender_id: 42 })).toBe(-17)
    expect(resolveMessengerRealtimeConversationKey({ room_kind: 'channel', chat_id: 9, sender_id: 42 })).toBe(-9)
    expect(resolveMessengerRealtimeConversationKey({ sender_id: 'bad-id' })).toBeNull()
  })

  it('normalizes activity payloads while preserving safe defaults', () => {
    expect(normalizeMessengerRealtimeActivityPayload({
      room_kind: 'group',
      chat_id: 5,
      sender_id: '31',
      sender_name: '  Sara  ',
      activity: 'uploading_file',
      active: false,
    })).toEqual({
      conversationKey: -5,
      senderId: 31,
      senderName: 'Sara',
      activity: 'uploading_file',
      active: false,
    })

    expect(normalizeMessengerRealtimeActivityPayload({
      sender_id: 7,
      sender_name: '   ',
      activity: 'typing',
    })).toMatchObject({
      conversationKey: 7,
      senderId: 7,
      senderName: 'کاربر',
      active: true,
    })

    expect(normalizeMessengerRealtimeActivityPayload({ sender_id: 7, activity: 'unknown' })).toBeNull()
  })

  it('builds direct and room activity labels consistently', () => {
    expect(buildMessengerActivityLabel(42, 'typing', ['Ali'])).toBe('در حال نوشتن...')
    expect(buildMessengerActivityLabel(-5, 'typing', ['Ali'])).toBe('Ali در حال نوشتن...')
    expect(buildMessengerActivityLabel(-5, 'uploading_file', ['Ali', 'Ali', 'Sara'])).toBe('۲ نفر در حال ارسال فایل...')
    expect(buildMessengerActivityLabel(-5, 'uploading_file', ['   '])).toBe('یک نفر در حال ارسال فایل...')
  })

  it('guards runtime events by the visible conversation key', () => {
    expect(isMessengerRuntimeEventForConversation(-7, -7)).toBe(true)
    expect(isMessengerRuntimeEventForConversation(12, 12)).toBe(true)
    expect(isMessengerRuntimeEventForConversation(12, -12)).toBe(false)
    expect(isMessengerRuntimeEventForConversation(null, null)).toBe(false)
  })

  it('normalizes media download patches and clamps progress', () => {
    expect(getMessengerMediaDownloadPatch('active', 42.4)).toEqual({ is_downloading: true, download_progress: 42 })
    expect(getMessengerMediaDownloadPatch('active', 160)).toEqual({ is_downloading: true, download_progress: 100 })
    expect(getMessengerMediaDownloadPatch('active', -5)).toEqual({ is_downloading: true, download_progress: 0 })
    expect(getMessengerMediaDownloadPatch('active', Number.NaN)).toEqual({ is_downloading: true, download_progress: 0 })
    expect(getMessengerMediaDownloadPatch('completed')).toEqual({ is_downloading: false, download_progress: 100 })
    expect(getMessengerMediaDownloadPatch('reset')).toEqual({ is_downloading: false, download_progress: 0 })
  })
})