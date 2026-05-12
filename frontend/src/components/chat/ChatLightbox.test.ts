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
})