import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ChatAlbumLayout from './ChatAlbumLayout.vue'

function buildVideoItem(messageOverrides: Record<string, unknown> = {}) {
  const msg = {
    id: 301,
    sender_id: 7,
    content: JSON.stringify({ file_id: 'album-video-301' }),
    message_type: 'video',
    created_at: '2026-05-31T08:00:00.000Z',
    is_deleted: false,
    reactions: [],
    ...messageOverrides,
  }

  return {
    msg,
    url: '',
    previewUrl: 'blob:preview-video',
    hasResolvedMedia: false,
    type: 'video' as const,
    width: 1080,
    height: 1080,
  }
}

describe('ChatAlbumLayout.vue', () => {
  it('shows a processing badge and clamps upload progress for album cells', async () => {
    const item = buildVideoItem({
      is_sending: true,
      upload_progress: 140,
      upload_loaded: 1024,
      upload_total: 1024,
    })
    const wrapper = mount(ChatAlbumLayout, {
      props: {
        items: [item],
        currentUserId: 7,
      },
    })

    expect(wrapper.find('.album-upload-badge').text()).toContain('در حال پردازش...')
    expect(wrapper.find('.album-progress-ring .ring-fg').attributes('stroke-dasharray')).toBe('100, 100')

    await wrapper.get('.album-upload-overlay').trigger('click')
    expect(wrapper.emitted('cancel-send')).toEqual([[item.msg]])
  })

  it('shows clamped download progress and emits cancel for active album downloads', async () => {
    const item = buildVideoItem({
      is_downloading: true,
      download_progress: 145,
    })
    const wrapper = mount(ChatAlbumLayout, {
      props: {
        items: [item],
        currentUserId: 7,
      },
    })

    expect(wrapper.find('.album-download-progress-ring .ring-fg').attributes('stroke-dasharray')).toBe('100, 100')

    await wrapper.get('.album-download-progress-overlay').trigger('click')
    expect(wrapper.emitted('cancel-download')).toEqual([[item.msg]])
  })
})