import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

import ChatLightbox from './ChatLightbox.vue'

type LightboxProps = InstanceType<typeof ChatLightbox>['$props']

function buildLightboxProps(overrides: Partial<NonNullable<LightboxProps['lightboxMedia']>> = {}): LightboxProps {
  return {
    lightboxMedia: {
      items: [
        {
          msgId: 101,
          fileId: 'image-file',
          type: 'image',
          url: 'https://example.com/image.jpg',
          thumbnail: 'https://example.com/image-thumb.jpg',
          senderId: 7,
          createdAt: new Date().toISOString(),
        },
        {
          msgId: 202,
          fileId: 'video-file',
          type: 'video',
          url: 'https://example.com/video.mp4',
          thumbnail: 'https://example.com/video-thumb.jpg',
          senderId: 8,
          createdAt: new Date().toISOString(),
        },
      ],
      currentIndex: 0,
      albumId: 'album-1',
      ...overrides,
    },
    currentUserId: 7,
  }
}

function buildLargeAlbumProps(currentIndex = 5): LightboxProps {
  return buildLightboxProps({
    items: Array.from({ length: 7 }, (_, index) => ({
      msgId: 1000 + index,
      fileId: index === 0 ? '' : `file-${index}`,
      type: index % 2 === 0 ? 'image' : 'video',
      url: `https://example.com/media-${index}.${index % 2 === 0 ? 'jpg' : 'mp4'}`,
      thumbnail: `https://example.com/thumb-${index}.jpg`,
      senderId: index % 2 === 0 ? 7 : 8,
      createdAt: new Date().toISOString(),
    })),
    currentIndex,
  })
}

function dispatchTouchEvent(
  element: Element,
  type: string,
  options: {
    touches?: Array<{ clientX: number; clientY: number }>
    changedTouches?: Array<{ clientX: number; clientY: number }>
    cancelable?: boolean
  },
) {
  const event = new Event(type, { bubbles: true, cancelable: options.cancelable ?? false })
  Object.defineProperty(event, 'touches', {
    configurable: true,
    value: options.touches ?? [],
  })
  Object.defineProperty(event, 'changedTouches', {
    configurable: true,
    value: options.changedTouches ?? options.touches ?? [],
  })
  element.dispatchEvent(event)
}

describe('ChatLightbox.vue', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('emits toolbar, navigation, and close events for the current album item', async () => {
    const wrapper = mount(ChatLightbox, {
      props: buildLightboxProps(),
      global: {
        stubs: {
          teleport: true,
          transition: false,
        },
      },
    })

    const buttons = wrapper.findAll('.lightbox-btn-labeled')
    await buttons[0]!.trigger('click')
    await buttons[1]!.trigger('click')
    await buttons[2]!.trigger('click')

    expect(wrapper.emitted('reply')).toEqual([[101]])
    expect(wrapper.emitted('forward')).toEqual([[101]])
    expect(wrapper.emitted('share')).toEqual([[101]])

    await wrapper.findAll('.lightbox-thumb')[1]!.trigger('click')
    expect(wrapper.emitted('navigate')).toEqual([[1]])

    await wrapper.find('.lightbox-overlay').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  it('downloads selected album items from the action menu and emits delete for the active owner item', async () => {
    vi.useFakeTimers()
    const anchorClickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const originalCreateElement = document.createElement.bind(document)
    const createdAnchors: HTMLAnchorElement[] = []

    vi.spyOn(document, 'createElement').mockImplementation(((tagName: string, options?: ElementCreationOptions) => {
      const element = originalCreateElement(tagName, options)
      if (tagName.toLowerCase() === 'a') {
        createdAnchors.push(element as HTMLAnchorElement)
      }
      return element
    }) as typeof document.createElement)

    const wrapper = mount(ChatLightbox, {
      props: buildLightboxProps(),
      global: {
        stubs: {
          teleport: true,
          transition: false,
        },
      },
    })

    await wrapper.find('.lightbox-menu-wrap .lightbox-btn').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('دانلود آلبوم')
    expect(wrapper.text()).toContain('حذف')

    const deleteButton = wrapper.findAll('.lightbox-menu-item').find((item) => item.text().includes('حذف'))
    expect(deleteButton).toBeTruthy()
    await deleteButton!.trigger('click')
    expect(wrapper.emitted('delete')).toEqual([[101]])

    await wrapper.find('.lightbox-menu-wrap .lightbox-btn').trigger('click')
    await flushPromises()
    const downloadAlbumButton = wrapper.findAll('.lightbox-menu-item').find((item) => item.text().includes('دانلود آلبوم'))
    expect(downloadAlbumButton).toBeTruthy()
    await downloadAlbumButton!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('2 از 2 مدیا انتخاب شده')
    await wrapper.find('.album-download-primary').trigger('click')
    await vi.runAllTimersAsync()

    expect(anchorClickSpy).toHaveBeenCalledTimes(2)
    expect(createdAnchors.map((anchor) => anchor.download)).toEqual(['01_image-file.jpg', '02_video-file.mp4'])
  })

  it('hides delete when the current item is not deletable', async () => {
    const wrapper = mount(ChatLightbox, {
      props: buildLightboxProps({
        items: [
          {
            msgId: 303,
            fileId: 'foreign-image',
            type: 'image',
            url: 'https://example.com/foreign.jpg',
            thumbnail: 'https://example.com/foreign-thumb.jpg',
            senderId: 999,
            createdAt: '2024-01-01T00:00:00.000Z',
          },
        ],
        albumId: null,
      }),
      currentUserId: 7,
      global: {
        stubs: {
          teleport: true,
          transition: false,
        },
      },
    })

    await wrapper.find('.lightbox-menu-wrap .lightbox-btn').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('دانلود')
    expect(wrapper.text()).not.toContain('حذف')
    expect(wrapper.text()).not.toContain('دانلود آلبوم')
  })

  it('closes the action menu and album sheet before closing the full overlay', async () => {
    const wrapper = mount(ChatLightbox, {
      props: buildLightboxProps(),
      global: {
        stubs: {
          teleport: true,
          transition: false,
        },
      },
    })

    await wrapper.find('.lightbox-menu-wrap .lightbox-btn').trigger('click')
    await flushPromises()
    expect(wrapper.find('.lightbox-menu-panel').exists()).toBe(true)

    await wrapper.find('.lightbox-overlay').trigger('click')
    await flushPromises()
    expect(wrapper.find('.lightbox-menu-panel').exists()).toBe(false)
    expect(wrapper.emitted('close')).toBeUndefined()

    await wrapper.find('.lightbox-menu-wrap .lightbox-btn').trigger('click')
    await flushPromises()
    const downloadAlbumButton = wrapper.findAll('.lightbox-menu-item').find((item) => item.text().includes('دانلود آلبوم'))
    await downloadAlbumButton!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.album-download-sheet').exists()).toBe(true)

    await wrapper.find('.lightbox-overlay').trigger('click')
    await flushPromises()
    expect(wrapper.find('.album-download-sheet').exists()).toBe(false)
    expect(wrapper.emitted('close')).toBeUndefined()

    await wrapper.find('.lightbox-overlay').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  it('supports selective album download controls after deselecting and reselecting items', async () => {
    vi.useFakeTimers()
    const anchorClickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const originalCreateElement = document.createElement.bind(document)
    const createdAnchors: HTMLAnchorElement[] = []

    vi.spyOn(document, 'createElement').mockImplementation(((tagName: string, options?: ElementCreationOptions) => {
      const element = originalCreateElement(tagName, options)
      if (tagName.toLowerCase() === 'a') {
        createdAnchors.push(element as HTMLAnchorElement)
      }
      return element
    }) as typeof document.createElement)

    const wrapper = mount(ChatLightbox, {
      props: buildLightboxProps(),
      global: {
        stubs: {
          teleport: true,
          transition: false,
        },
      },
    })

    await wrapper.find('.lightbox-menu-wrap .lightbox-btn').trigger('click')
    await flushPromises()
    const downloadAlbumButton = wrapper.findAll('.lightbox-menu-item').find((item) => item.text().includes('دانلود آلبوم'))
    await downloadAlbumButton!.trigger('click')
    await flushPromises()

    await wrapper.findAll('.album-download-chip')[1]!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('0 از 2 مدیا انتخاب شده')
    expect(wrapper.get('.album-download-primary').attributes('disabled')).toBeDefined()

    await wrapper.findAll('.album-download-checkbox')[0]!.trigger('change')
    await flushPromises()
    expect(wrapper.text()).toContain('1 از 2 مدیا انتخاب شده')
    expect(wrapper.get('.album-download-primary').attributes('disabled')).toBeUndefined()

    await wrapper.get('.album-download-primary').trigger('click')
    await vi.runAllTimersAsync()

    expect(anchorClickSpy).toHaveBeenCalledTimes(1)
    expect(createdAnchors.map((anchor) => anchor.download)).toEqual(['01_image-file.jpg'])
  })

  it('dismisses the menu on media tap, toggles image zoom on double click, and emits swipe navigation and close gestures', async () => {
    const wrapper = mount(ChatLightbox, {
      props: buildLightboxProps(),
      global: {
        stubs: {
          teleport: true,
          transition: false,
        },
      },
    })

    await wrapper.find('.lightbox-menu-wrap .lightbox-btn').trigger('click')
    await flushPromises()
    expect(wrapper.find('.lightbox-menu-panel').exists()).toBe(true)

    const image = wrapper.get('.lightbox-stage-card.active img.lightbox-media')
    await image.trigger('click')
    await flushPromises()
    expect(wrapper.find('.lightbox-menu-panel').exists()).toBe(false)

    await image.trigger('dblclick', { clientX: 120, clientY: 80 })
    await flushPromises()
    expect(wrapper.get('.lightbox-stage-card.active img.lightbox-media').classes()).toContain('is-zoomed')

    await image.trigger('dblclick', { clientX: 120, clientY: 80 })
    await flushPromises()
    expect(wrapper.get('.lightbox-stage-card.active img.lightbox-media').classes()).not.toContain('is-zoomed')

    const stage = wrapper.get('.lightbox-stage')
    await stage.trigger('touchstart', { touches: [{ clientX: 240, clientY: 120 }] })
    await stage.trigger('touchmove', { touches: [{ clientX: 120, clientY: 124 }] })
    await stage.trigger('touchend', { changedTouches: [{ clientX: 120, clientY: 124 }], touches: [] })

    expect(wrapper.emitted('navigate')).toContainEqual([1])

    await stage.trigger('touchstart', { touches: [{ clientX: 120, clientY: 80 }] })
    await stage.trigger('touchmove', { touches: [{ clientX: 126, clientY: 210 }] })
    await stage.trigger('touchend', { changedTouches: [{ clientX: 126, clientY: 210 }], touches: [] })

    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  it('downloads the current item from the menu and resets menu, sheet, and zoom state when the album context changes', async () => {
    vi.useFakeTimers()
    const anchorClickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const originalCreateElement = document.createElement.bind(document)
    const createdAnchors: HTMLAnchorElement[] = []

    vi.spyOn(document, 'createElement').mockImplementation(((tagName: string, options?: ElementCreationOptions) => {
      const element = originalCreateElement(tagName, options)
      if (tagName.toLowerCase() === 'a') {
        createdAnchors.push(element as HTMLAnchorElement)
      }
      return element
    }) as typeof document.createElement)

    const wrapper = mount(ChatLightbox, {
      props: buildLightboxProps(),
      global: {
        stubs: {
          teleport: true,
          transition: false,
        },
      },
    })

    const image = wrapper.get('.lightbox-stage-card.active img.lightbox-media')
    await image.trigger('dblclick', { clientX: 132, clientY: 84 })
    await flushPromises()
    expect(image.classes()).toContain('is-zoomed')

    await wrapper.find('.lightbox-menu-wrap .lightbox-btn').trigger('click')
    await flushPromises()
    const downloadCurrentButton = wrapper.findAll('.lightbox-menu-item').find((item) => item.text().includes('دانلود'))
    expect(downloadCurrentButton).toBeTruthy()
    await downloadCurrentButton!.trigger('click')
    await flushPromises()

    expect(anchorClickSpy).toHaveBeenCalledTimes(1)
    expect(createdAnchors.map((anchor) => anchor.download)).toEqual(['01_image-file.jpg'])

    await wrapper.find('.lightbox-menu-wrap .lightbox-btn').trigger('click')
    await flushPromises()
    const downloadAlbumButton = wrapper.findAll('.lightbox-menu-item').find((item) => item.text().includes('دانلود آلبوم'))
    await downloadAlbumButton!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.album-download-sheet').exists()).toBe(true)

    await wrapper.setProps(buildLightboxProps({ currentIndex: 1, albumId: 'album-2' }))
    await flushPromises()

    expect(wrapper.find('.lightbox-menu-panel').exists()).toBe(false)
    expect(wrapper.find('.album-download-sheet').exists()).toBe(false)
    expect(wrapper.get('.lightbox-stage-card.active .lightbox-media').classes()).not.toContain('is-zoomed')
  })

  it('renders distant strip thumbnails from thumbnails, suppresses strip clicks after a swipe, and clears the suppression timer on unmount', async () => {
    vi.useFakeTimers()
    const clearTimeoutSpy = vi.spyOn(window, 'clearTimeout')

    const wrapper = mount(ChatLightbox, {
      props: buildLargeAlbumProps(),
      global: {
        stubs: {
          teleport: true,
          transition: false,
        },
      },
    })

    const thumbs = wrapper.findAll('.lightbox-thumb')
    expect(thumbs).toHaveLength(7)
    expect(thumbs[0]!.find('.lightbox-thumb-image').attributes('src')).toBe('https://example.com/thumb-0.jpg')
    expect(thumbs[0]!.attributes('style')).toContain('blur(6px)')
    expect(wrapper.findAll('.thumb-video-badge')).toHaveLength(3)

    const strip = wrapper.get('.lightbox-strip')
    await strip.trigger('touchstart', { touches: [{ clientX: 240, clientY: 120 }] })
    await strip.trigger('touchmove', { touches: [{ clientX: 180, clientY: 124 }] })
    await flushPromises()

    await thumbs[1]!.trigger('click')
    expect(wrapper.emitted('navigate')).toBeUndefined()

    wrapper.unmount()
    expect(clearTimeoutSpy).toHaveBeenCalled()
  })

  it('supports pinch zoom, pan gestures, and touch double-tap reset for the active image', async () => {
    vi.useFakeTimers()
    const wrapper = mount(ChatLightbox, {
      props: buildLightboxProps(),
      global: {
        stubs: {
          teleport: true,
          transition: false,
        },
      },
    })

    const stage = wrapper.get('.lightbox-stage')

    dispatchTouchEvent(stage.element, 'touchstart', {
      touches: [
        { clientX: 110, clientY: 100 },
        { clientX: 210, clientY: 100 },
      ],
    })
    dispatchTouchEvent(stage.element, 'touchmove', {
      cancelable: true,
      touches: [
        { clientX: 90, clientY: 100 },
        { clientX: 250, clientY: 100 },
      ],
    })
    await flushPromises()
    expect(wrapper.get('.lightbox-stage-card.active img.lightbox-media').classes()).toContain('is-zoomed')

    dispatchTouchEvent(stage.element, 'touchend', {
      touches: [{ clientX: 90, clientY: 100 }],
      changedTouches: [{ clientX: 250, clientY: 100 }],
    })
    dispatchTouchEvent(stage.element, 'touchmove', {
      cancelable: true,
      touches: [{ clientX: 130, clientY: 126 }],
    })
    await flushPromises()
    expect(wrapper.get('.lightbox-stage-card.active img.lightbox-media').attributes('style')).toContain('translate3d(')

    dispatchTouchEvent(stage.element, 'touchend', {
      touches: [],
      changedTouches: [{ clientX: 130, clientY: 126 }],
    })

    const image = wrapper.get('.lightbox-stage-card.active img.lightbox-media')
    await image.trigger('dblclick', { clientX: 120, clientY: 90 })
    await flushPromises()
    expect(image.classes()).not.toContain('is-zoomed')

    dispatchTouchEvent(stage.element, 'touchstart', { touches: [{ clientX: 120, clientY: 90 }] })
    dispatchTouchEvent(stage.element, 'touchend', { touches: [], changedTouches: [{ clientX: 120, clientY: 90 }] })
    vi.advanceTimersByTime(120)
    dispatchTouchEvent(stage.element, 'touchstart', { touches: [{ clientX: 122, clientY: 92 }] })
    dispatchTouchEvent(stage.element, 'touchend', { touches: [], changedTouches: [{ clientX: 122, clientY: 92 }] })
    await flushPromises()

    expect(image.classes()).toContain('is-zoomed')
  })
})