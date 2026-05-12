import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

vi.mock('../../composables/chat/useChatFileHandler', () => ({
  canShareFiles: () => true,
}))

vi.mock('../../utils/messageReactions', () => ({
  buildQuickMessageReactions: (availableReactions: string[]) => availableReactions.slice(0, 6),
}))

describe('ChatContextMenu.vue', () => {
  it('renders reaction and text actions, then emits reaction and copy events', async () => {
    const ChatContextMenu = (await import('./ChatContextMenu.vue')).default
    const wrapper = mount(ChatContextMenu, {
      props: {
        menuState: {
          x: 40,
          y: 60,
          visible: true,
          message: {
            id: 1,
            message_type: 'text',
            is_deleted: false,
            reactions: [{ emoji: '🔥', user_id: 7 }],
          },
          messageIds: [1],
        },
        isAlbumSelection: false,
        currentUserId: 7,
        canEdit: true,
        canDelete: true,
        canPin: true,
        isPinnedMessage: false,
        availableReactions: ['🔥', '👍'],
      },
      global: {
        directives: {
          ripple: {},
        },
        stubs: {
          teleport: true,
          transition: false,
        },
      },
    })

    expect(wrapper.text()).toContain('کپی کردن')
    expect(wrapper.text()).toContain('سنجاق کردن پیام')

    const reactionButton = wrapper.findAll('.reaction-btn').find((button) => button.text().includes('🔥'))
    expect(reactionButton).toBeTruthy()
    await reactionButton!.trigger('click')
    expect(wrapper.emitted('react')).toEqual([['🔥']])

    const copyButton = wrapper.findAll('.menu-item').find((item) => item.text().includes('کپی کردن'))
    expect(copyButton).toBeTruthy()
    await copyButton!.trigger('click')
    expect(wrapper.emitted('copy')).toHaveLength(1)
  })

  it('switches to album-specific save and share actions when album mode is active', async () => {
    const ChatContextMenu = (await import('./ChatContextMenu.vue')).default
    const wrapper = mount(ChatContextMenu, {
      props: {
        menuState: {
          x: 16,
          y: 24,
          visible: true,
          message: {
            id: 2,
            message_type: 'image',
            is_deleted: false,
            reactions: [],
          },
          messageIds: [2, 3],
        },
        isAlbumSelection: true,
        currentUserId: 9,
        canEdit: false,
        canDelete: true,
        canPin: false,
        isPinnedMessage: false,
        availableReactions: [],
      },
      global: {
        directives: {
          ripple: {},
        },
        stubs: {
          teleport: true,
          transition: false,
        },
      },
    })

    expect(wrapper.text()).toContain('دانلود آلبوم')
    expect(wrapper.text()).toContain('اشتراک‌گذاری آلبوم')
    expect(wrapper.text()).not.toContain('ذخیره در گالری')

    const shareAlbumButton = wrapper.findAll('.menu-item').find((item) => item.text().includes('اشتراک‌گذاری آلبوم'))
    expect(shareAlbumButton).toBeTruthy()
    await shareAlbumButton!.trigger('click')
    expect(wrapper.emitted('share-album')).toHaveLength(1)
  })
})