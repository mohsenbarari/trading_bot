import { describe, expect, it } from 'vitest'
import {
  getConversationPreviewText,
  getMediaCaptionText,
  parseStructuredMessageContent,
} from './chatMessagePreview'

describe('chatMessagePreview', () => {
  it('parses structured payloads and returns empty captions for non-media messages', () => {
    expect(parseStructuredMessageContent('{"caption":"  hello  "}')).toEqual({ caption: '  hello  ' })
    expect(parseStructuredMessageContent('not-json')).toBeNull()
    expect(parseStructuredMessageContent('42')).toBeNull()

    expect(getMediaCaptionText('text', '{"caption":"ignored"}')).toBe('')
    expect(getMediaCaptionText('image', '{"caption":"  hello  "}')).toBe('hello')
  })

  it('builds preview text for empty, video, and fallback message types', () => {
    expect(getConversationPreviewText(null, 'ignored')).toBe('')
    expect(getConversationPreviewText('video', '{"caption":"  clip  "}')).toBe('ویدئو · clip')
    expect(getConversationPreviewText('video', '{"caption":123}')).toBe('ویدئو')
    expect(getConversationPreviewText('voice', 'ignored')).toBe('پیام صوتی')
    expect(getConversationPreviewText('sticker', 'ignored')).toBe('استیکر')
    expect(getConversationPreviewText('location', 'ignored')).toBe('موقعیت')
    expect(getConversationPreviewText('document', 'ignored')).toBe('فایل')
    expect(getConversationPreviewText('unknown', null)).toBe('...')
  })
})