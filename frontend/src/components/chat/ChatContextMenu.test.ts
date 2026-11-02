import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

vi.mock('../../composables/chat/useChatFileHandler', () => ({
  canShareFiles: () => true,
}))

vi.mock('../../utils/messageReactions', () => ({
  buildQuickMessageReactions: (availableReactions: string[]) => availableReactions.slice(0, 6),
}))

async function waitForReactionShell() {
  await new Promise<void>((resolve) => setTimeout(() => resolve(), 25))
}

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

    await waitForReactionShell()

    expect(wrapper.text()).toContain('کپی کردن')
    expect(wrapper.text()).toContain('سنجاق کردن پیام')
    expect(wrapper.text()).toContain('اقدام اصلی')
    expect(wrapper.text()).toContain('ارتباط و پیام')

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
        canViewSeenList: true,
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
    expect(wrapper.text()).toContain('بازدیدها')
    expect(wrapper.text()).toContain('رسانه و فایل')
    expect(wrapper.text()).not.toContain('ذخیره در گالری')

    const shareAlbumButton = wrapper.findAll('.menu-item').find((item) => item.text().includes('اشتراک‌گذاری آلبوم'))
    expect(shareAlbumButton).toBeTruthy()
    await shareAlbumButton!.trigger('click')
    expect(wrapper.emitted('share-album')).toHaveLength(1)

    const seenButton = wrapper.findAll('.menu-item').find((item) => item.text().includes('بازدیدها'))
    expect(seenButton).toBeTruthy()
    await seenButton!.trigger('click')
    expect(wrapper.emitted('seen-list')).toHaveLength(1)
  })

  it('expands overflow reactions and resets the expanded picker when the menu closes', async () => {
    const ChatContextMenu = (await import('./ChatContextMenu.vue')).default
    const wrapper = mount(ChatContextMenu, {
      props: {
        menuState: {
          x: 12,
          y: 18,
          visible: true,
          message: {
            id: 3,
            message_type: 'text',
            is_deleted: false,
            reactions: [{ emoji: '🎯', user_id: 4 }],
          },
          messageIds: [3],
        },
        isAlbumSelection: false,
        currentUserId: 4,
        canEdit: false,
        canDelete: false,
        canPin: false,
        isPinnedMessage: false,
        availableReactions: ['🔥', '👍', '😂', '❤️', '👏', '🎉', '🎯', '😮'],
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

    await waitForReactionShell()

    expect(wrapper.find('.reaction-dropdown-toggle').exists()).toBe(true)
    expect(wrapper.find('.reaction-dropdown-list').exists()).toBe(false)

    await wrapper.get('.reaction-dropdown-toggle').trigger('click')
    expect(wrapper.find('.reaction-dropdown-list').exists()).toBe(true)

    const overflowButton = wrapper.findAll('.reaction-dropdown-list .reaction-btn').find((button) => button.text().includes('🎯'))
    expect(overflowButton).toBeTruthy()
    await overflowButton!.trigger('click')
    expect(wrapper.emitted('react')).toContainEqual(['🎯'])

    await wrapper.setProps({
      menuState: {
        x: 12,
        y: 18,
        visible: false,
        message: {
          id: 3,
          message_type: 'text',
          is_deleted: false,
          reactions: [{ emoji: '🎯', user_id: 4 }],
        },
        messageIds: [3],
      },
    })
    await wrapper.setProps({
      menuState: {
        x: 12,
        y: 18,
        visible: true,
        message: {
          id: 3,
          message_type: 'text',
          is_deleted: false,
          reactions: [{ emoji: '🎯', user_id: 4 }],
        },
        messageIds: [3],
      },
    })
    await waitForReactionShell()

    expect(wrapper.find('.reaction-dropdown-list').exists()).toBe(false)
  })

  it('hides reactions for deleted messages and shows the unpin label for pinned messages', async () => {
    const ChatContextMenu = (await import('./ChatContextMenu.vue')).default
    const wrapper = mount(ChatContextMenu, {
      props: {
        menuState: {
          x: 20,
          y: 30,
          visible: true,
          message: {
            id: 4,
            message_type: 'document',
            is_deleted: true,
            reactions: [{ emoji: '👍', user_id: 7 }],
          },
          messageIds: [4],
        },
        isAlbumSelection: false,
        currentUserId: 7,
        canEdit: false,
        canDelete: true,
        canPin: true,
        isPinnedMessage: true,
        availableReactions: ['👍', '🔥'],
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

    expect(wrapper.find('.reaction-picker-shell').exists()).toBe(false)
    expect(wrapper.findAll('.reaction-btn')).toHaveLength(0)
    expect(wrapper.text()).toContain('برداشتن پیام سنجاق‌شده')
    expect(wrapper.text()).toContain('اشتراک‌گذاری')
  })

  it('shows and emits the seen-list action when it is allowed', async () => {
    const ChatContextMenu = (await import('./ChatContextMenu.vue')).default
    const wrapper = mount(ChatContextMenu, {
      props: {
        menuState: {
          x: 20,
          y: 30,
          visible: true,
          message: {
            id: 5,
            message_type: 'text',
            is_deleted: false,
            reactions: [],
          },
          messageIds: [5],
        },
        isAlbumSelection: false,
        currentUserId: 7,
        canEdit: false,
        canDelete: false,
        canPin: false,
        canViewSeenList: true,
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

    const seenButton = wrapper.findAll('.menu-item').find((item) => item.text().includes('بازدیدها'))
    expect(seenButton).toBeTruthy()
    await seenButton!.trigger('click')
    expect(wrapper.emitted('seen-list')).toHaveLength(1)
  })

  it('keeps menu action clicks from bubbling into navigation/back handlers', async () => {
    const ChatContextMenu = (await import('./ChatContextMenu.vue')).default
    const wrapper = mount(ChatContextMenu, {
      props: {
        menuState: {
          x: 20,
          y: 30,
          visible: true,
          message: {
            id: 6,
            message_type: 'text',
            is_deleted: false,
            reactions: [],
          },
          messageIds: [6],
        },
        isAlbumSelection: false,
        currentUserId: 7,
        canEdit: false,
        canDelete: false,
        canPin: false,
        canViewSeenList: true,
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

    const bubbled = vi.fn()
    wrapper.element.addEventListener('click', bubbled)
    const seenButton = wrapper.findAll('.menu-item').find((item) => item.text().includes('بازدیدها'))
    expect(seenButton).toBeTruthy()

    const actionClick = new MouseEvent('click', { bubbles: true, cancelable: true })
    seenButton!.element.dispatchEvent(actionClick)

    expect(wrapper.emitted('seen-list')).toHaveLength(1)
    expect(actionClick.defaultPrevented).toBe(true)
    expect(bubbled).not.toHaveBeenCalled()

    await wrapper.get('.context-overlay').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)
  })
  it('places the measured DOM bounds inside the viewport instead of trusting the model estimate', async () => {
    const originalRect = HTMLElement.prototype.getBoundingClientRect
    const geometry = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function () {
      if (this.getAttribute('role') !== 'menu') return originalRect.call(this)
      // Owned DOM geometry: real fonts/touch targets can exceed the initial estimate.
      return new DOMRect(0, 0, 296, Math.min(640, Number.parseFloat(this.style.maxHeight) || 640))
    })
    let wrapper: ReturnType<typeof mount> | undefined
    try {
      const ChatContextMenu = (await import('./ChatContextMenu.vue')).default
      wrapper = mount(ChatContextMenu, {
        props: {
          menuState: { x: 20, y: 700, visible: true, message: { id: 9, message_type: 'text' },
            style: { left: '20px', top: '500px', width: '296px' } },
          isAlbumSelection: false, currentUserId: 7, canEdit: true, canDelete: true,
          canPin: true, isPinnedMessage: false, availableReactions: [],
        },
        attachTo: document.body,
        global: { directives: { ripple: {} }, stubs: { teleport: true, transition: false } },
      })
      await waitForReactionShell()
      const menu = wrapper.get('[role="menu"]').element as HTMLElement
      expect(Number.parseFloat(menu.style.top)).toBeGreaterThanOrEqual(8)
      expect(Number.parseFloat(menu.style.top) + menu.getBoundingClientRect().height)
        .toBeLessThanOrEqual(window.innerHeight - 8)
      const deletion = wrapper.findAll('[role="menuitem"]').find((item) => item.text().includes('حذف'))
      expect(deletion).toBeTruthy()
      await deletion!.trigger('click')
      expect(wrapper.emitted('delete')).toHaveLength(1)
    } finally {
      wrapper?.unmount()
      geometry.mockRestore()
    }
  })

  it('re-measures on viewport resize and disconnects the actual observer on unmount', async () => {
    const originalRect = HTMLElement.prototype.getBoundingClientRect
    const geometry = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function () {
      if (this.getAttribute('role') !== 'menu') return originalRect.call(this)
      return new DOMRect(0, 0, 220, Math.min(600, Number.parseFloat(this.style.maxHeight) || 600))
    })
    const observers: Array<{ observe: ReturnType<typeof vi.fn>; disconnect: ReturnType<typeof vi.fn> }> = []
    vi.stubGlobal('ResizeObserver', class {
      observe = vi.fn()
      disconnect = vi.fn()
      constructor() { observers.push(this) }
    })
    const originalHeight = window.innerHeight
    let wrapper: ReturnType<typeof mount> | undefined
    try {
      const ChatContextMenu = (await import('./ChatContextMenu.vue')).default
      wrapper = mount(ChatContextMenu, {
        props: {
          menuState: { x: 20, y: 600, visible: true, message: { id: 10, message_type: 'text' } },
          isAlbumSelection: false, currentUserId: 7, canEdit: false, canDelete: true,
          canPin: false, isPinnedMessage: false, availableReactions: [],
        },
        global: { directives: { ripple: {} }, stubs: { teleport: true, transition: false } },
      })
      await waitForReactionShell()
      Object.defineProperty(window, 'innerHeight', { configurable: true, value: 240 })
      window.dispatchEvent(new Event('resize'))
      await waitForReactionShell()
      const menu = wrapper.get('[role="menu"]').element as HTMLElement
      expect(menu.style.maxHeight).toBe('224px')
      expect(Number.parseFloat(menu.style.top)).toBe(8)
      expect(Number.parseFloat(menu.style.top) + menu.getBoundingClientRect().height).toBe(232)
      const originalObservers = observers.filter((observer) =>
        observer.observe.mock.calls.some(([element]) => element === menu))
      expect(originalObservers.length).toBeGreaterThan(0)
      wrapper.unmount();wrapper = undefined
      // Verify each observer belonging to this menu, rather than unrelated mounted menus.
      for (const observer of originalObservers) expect(observer.disconnect).toHaveBeenCalledOnce()
    } finally {
      wrapper?.unmount()
      Object.defineProperty(window, 'innerHeight', { configurable: true, value: originalHeight })
      geometry.mockRestore()
      vi.unstubAllGlobals()
    }
  })

})
