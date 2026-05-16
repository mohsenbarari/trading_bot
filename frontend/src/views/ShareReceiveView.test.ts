import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChatForwardTarget, Conversation } from '../types/chat'
import ShareReceiveView from './ShareReceiveView.vue'

const shareReceiveViewMocks = vi.hoisted(() => ({
  routeState: {
    query: {} as Record<string, string>,
  },
  routerBackMock: vi.fn(),
  routerReplaceMock: vi.fn(),
  apiFetchJsonMock: vi.fn(),
  readSharedPayloadMock: vi.fn(),
  deleteSharedPayloadMock: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => shareReceiveViewMocks.routeState,
  useRouter: () => ({
    back: shareReceiveViewMocks.routerBackMock,
    replace: shareReceiveViewMocks.routerReplaceMock,
  }),
}))

vi.mock('../utils/auth', () => ({
  apiFetchJson: shareReceiveViewMocks.apiFetchJsonMock,
}))

vi.mock('../utils/shareTargetStore', () => ({
  readSharedPayload: shareReceiveViewMocks.readSharedPayloadMock,
  deleteSharedPayload: shareReceiveViewMocks.deleteSharedPayloadMock,
}))

vi.mock('../components/chat/ChatForwardModal.vue', () => ({
  default: {
    name: 'ChatForwardModal',
    props: ['showForwardModal', 'sortedConversations', 'includeChannels', 'includeGroups'],
    emits: ['close', 'forward-to'],
    template: `
      <div class="chat-forward-modal-stub">
        <span class="show-forward-modal">{{ String(showForwardModal) }}</span>
        <span class="conversation-count">{{ sortedConversations.length }}</span>
        <span class="include-channels">{{ String(includeChannels) }}</span>
        <span class="include-groups">{{ String(includeGroups) }}</span>
      </div>
    `,
  },
}))

function makeJsonResponse(payload: unknown, ok = true, status = ok ? 200 : 500) {
  return {
    ok,
    status,
    json: async () => payload,
  }
}

function makeConversation(overrides: Partial<Conversation> = {}): Conversation {
  return {
    id: 1,
    other_user_id: 7,
    other_user_name: 'peer-user',
    last_message_content: null,
    last_message_type: null,
    last_message_at: null,
    unread_count: 0,
    ...overrides,
  }
}

function makeSharedPayload(overrides: Record<string, unknown> = {}) {
  return {
    key: 'share-key-1',
    createdAt: Date.now(),
    title: 'عنوان اشتراک',
    text: 'متن اشتراک',
    url: 'https://example.test/share',
    files: [],
    ...overrides,
  }
}

function makeSharedFile(name = 'photo.png', type = 'image/png', contents = 'image-bytes') {
  const blob = new Blob([contents], { type })
  return {
    name,
    type,
    size: blob.size,
    blob,
  }
}

async function settle() {
  await flushPromises()
  await flushPromises()
}

async function mountView() {
  const wrapper = mount(ShareReceiveView)
  await settle()
  return wrapper
}

describe('ShareReceiveView.vue', () => {
  beforeEach(() => {
    shareReceiveViewMocks.routeState.query = { share_key: 'share-key-1' }
    shareReceiveViewMocks.routerBackMock.mockReset()
    shareReceiveViewMocks.routerReplaceMock.mockReset()
    shareReceiveViewMocks.apiFetchJsonMock.mockReset()
    shareReceiveViewMocks.readSharedPayloadMock.mockReset()
    shareReceiveViewMocks.deleteSharedPayloadMock.mockReset()
    shareReceiveViewMocks.deleteSharedPayloadMock.mockResolvedValue(undefined)
    vi.stubGlobal('fetch', vi.fn())
    localStorage.clear()
    localStorage.setItem('auth_token', 'jwt-token')
  })

  it('shows a receive error state when the service worker redirects with share_error', async () => {
    shareReceiveViewMocks.routeState.query = { share_error: '1' }

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('دریافت محتوای اشتراک‌گذاری شده با خطا مواجه شد.')
    expect(shareReceiveViewMocks.readSharedPayloadMock).not.toHaveBeenCalled()
    expect(shareReceiveViewMocks.apiFetchJsonMock).not.toHaveBeenCalled()
  })

  it('shows an invalid-link error when no share_key is present', async () => {
    shareReceiveViewMocks.routeState.query = {}

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('لینک اشتراک‌گذاری نامعتبر است.')
    expect(shareReceiveViewMocks.readSharedPayloadMock).not.toHaveBeenCalled()
  })

  it('rejects payloads that contain neither text nor files', async () => {
    shareReceiveViewMocks.readSharedPayloadMock.mockResolvedValue(
      makeSharedPayload({
        title: '',
        text: '',
        url: '',
        files: [],
      }),
    )

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('محتوایی برای اشتراک‌گذاری دریافت نشد.')
    expect(shareReceiveViewMocks.apiFetchJsonMock).not.toHaveBeenCalled()
  })

  it('loads conversations into the full-screen picker and deletes the payload on close', async () => {
    shareReceiveViewMocks.readSharedPayloadMock.mockResolvedValue(
      makeSharedPayload({
        files: [makeSharedFile()],
      }),
    )
    shareReceiveViewMocks.apiFetchJsonMock.mockResolvedValue([
      makeConversation({ id: 11, other_user_name: 'receiver-11' }),
    ])
    window.history.pushState({ from: '/chat' }, '', '/share-receive?share_key=share-key-1')

    const wrapper = await mountView()
    const modal = wrapper.getComponent({ name: 'ChatForwardModal' })

    expect(modal.props('showForwardModal')).toBe(true)
    expect(modal.props('sortedConversations')).toHaveLength(1)
    expect(modal.props('includeChannels')).toBe(true)
    expect(modal.props('includeGroups')).toBe(true)

    modal.vm.$emit('close')
    await settle()

    expect(shareReceiveViewMocks.deleteSharedPayloadMock).toHaveBeenCalledWith('share-key-1')
    expect(shareReceiveViewMocks.routerBackMock).toHaveBeenCalledTimes(1)
    expect(shareReceiveViewMocks.routerReplaceMock).not.toHaveBeenCalled()
  })

  it('uploads files once, reuses their file ids, and redirects to a single room target', async () => {
    const fetchMock = vi.mocked(fetch)
    shareReceiveViewMocks.readSharedPayloadMock.mockResolvedValue(
      makeSharedPayload({
        files: [makeSharedFile('photo.png', 'image/png', 'abcd')],
      }),
    )
    shareReceiveViewMocks.apiFetchJsonMock.mockResolvedValue([])
    fetchMock
      .mockResolvedValueOnce(makeJsonResponse({
        file_id: 'file-1',
        file_name: 'server-photo.png',
        mime_type: 'image/png',
        size: 4,
      }) as any)
      .mockResolvedValueOnce(makeJsonResponse({ id: 101 }) as any)
      .mockResolvedValueOnce(makeJsonResponse({ id: 102 }) as any)

    const wrapper = await mountView()
    const modal = wrapper.getComponent({ name: 'ChatForwardModal' })
    const target: ChatForwardTarget = { kind: 'group', id: 9, title: 'اتاق گروهی' }

    modal.vm.$emit('forward-to', [target])
    await settle()

    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/chat/upload-media')
    expect(fetchMock.mock.calls[0]?.[1]).toEqual(expect.objectContaining({
      method: 'POST',
      headers: { Authorization: 'Bearer jwt-token' },
      body: expect.any(FormData),
    }))

    const uploadedSendBody = JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/chat/rooms/9/send')
    expect(uploadedSendBody).toMatchObject({
      message_type: 'image',
      content: JSON.stringify({
        file_id: 'file-1',
        file_name: 'server-photo.png',
        mime_type: 'image/png',
        size: 4,
      }),
    })
    expect(uploadedSendBody).not.toHaveProperty('receiver_id')

    const mergedTextSendBody = JSON.parse(String(fetchMock.mock.calls[2]?.[1]?.body))
    expect(fetchMock.mock.calls[2]?.[0]).toBe('/api/chat/rooms/9/send')
    expect(mergedTextSendBody).toMatchObject({
      message_type: 'text',
      content: 'عنوان اشتراک\nمتن اشتراک\nhttps://example.test/share',
    })

    expect(shareReceiveViewMocks.deleteSharedPayloadMock).toHaveBeenCalledWith('share-key-1')
    expect(shareReceiveViewMocks.routerReplaceMock).toHaveBeenCalledWith({
      path: '/chat',
      query: { user_id: '-9' },
    })
  })

  it('fans one upload out to multiple direct and channel targets before returning to messenger home', async () => {
    const fetchMock = vi.mocked(fetch)
    shareReceiveViewMocks.readSharedPayloadMock.mockResolvedValue(
      makeSharedPayload({
        files: [makeSharedFile('document.pdf', 'application/pdf', 'file-payload')],
      }),
    )
    shareReceiveViewMocks.apiFetchJsonMock.mockResolvedValue([])
    fetchMock
      .mockResolvedValueOnce(makeJsonResponse({
        file_id: 'file-2',
        file_name: 'document.pdf',
        mime_type: 'application/pdf',
        size: 12,
      }) as any)
      .mockResolvedValueOnce(makeJsonResponse({ id: 201 }) as any)
      .mockResolvedValueOnce(makeJsonResponse({ id: 202 }) as any)
      .mockResolvedValueOnce(makeJsonResponse({ id: 203 }) as any)
      .mockResolvedValueOnce(makeJsonResponse({ id: 204 }) as any)

    const wrapper = await mountView()
    const modal = wrapper.getComponent({ name: 'ChatForwardModal' })
    const targets: ChatForwardTarget[] = [
      { kind: 'user', id: 4, title: 'کاربر مستقیم' },
      { kind: 'channel', id: 11, title: 'کانال خبر' },
    ]

    modal.vm.$emit('forward-to', targets)
    await settle()

    expect(fetchMock).toHaveBeenCalledTimes(5)
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/chat/upload-media')

    const directFileSend = JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/chat/send')
    expect(directFileSend).toMatchObject({
      receiver_id: 4,
      message_type: 'document',
    })

    const directTextSend = JSON.parse(String(fetchMock.mock.calls[2]?.[1]?.body))
    expect(fetchMock.mock.calls[2]?.[0]).toBe('/api/chat/send')
    expect(directTextSend).toMatchObject({
      receiver_id: 4,
      message_type: 'text',
    })

    const channelFileSend = JSON.parse(String(fetchMock.mock.calls[3]?.[1]?.body))
    expect(fetchMock.mock.calls[3]?.[0]).toBe('/api/chat/rooms/11/send')
    expect(channelFileSend).toMatchObject({
      message_type: 'document',
    })
    expect(channelFileSend).not.toHaveProperty('receiver_id')

    const channelTextSend = JSON.parse(String(fetchMock.mock.calls[4]?.[1]?.body))
    expect(fetchMock.mock.calls[4]?.[0]).toBe('/api/chat/rooms/11/send')
    expect(channelTextSend).toMatchObject({
      message_type: 'text',
      content: 'عنوان اشتراک\nمتن اشتراک\nhttps://example.test/share',
    })

    expect(shareReceiveViewMocks.routerReplaceMock).toHaveBeenCalledWith({ path: '/chat' })
  })

  it('shows a result panel with collected errors when upload or send fails', async () => {
    const fetchMock = vi.mocked(fetch)
    shareReceiveViewMocks.readSharedPayloadMock.mockResolvedValue(
      makeSharedPayload({
        files: [makeSharedFile('photo.png', 'image/png', 'abcd')],
      }),
    )
    shareReceiveViewMocks.apiFetchJsonMock.mockResolvedValue([])
    fetchMock
      .mockResolvedValueOnce(makeJsonResponse({ message: 'upload failed' }, false, 500) as any)
      .mockResolvedValueOnce(makeJsonResponse({ message: 'send failed' }, false, 500) as any)

    const wrapper = await mountView()
    const modal = wrapper.getComponent({ name: 'ChatForwardModal' })
    modal.vm.$emit('forward-to', [{ kind: 'user', id: 15, title: 'مخاطب خطادار' }])
    await settle()

    expect(wrapper.text()).toContain('ارسال با 2 خطا انجام شد')
    expect(wrapper.text()).toContain('آپلود فایل ناموفق: photo.png')
    expect(wrapper.text()).toContain('ارسال متن به مخاطب خطادار ناموفق')
    expect(shareReceiveViewMocks.routerReplaceMock).not.toHaveBeenCalledWith({
      path: '/chat',
      query: { user_id: '15' },
    })

    await wrapper.get('.primary-btn').trigger('click')
    expect(shareReceiveViewMocks.routerReplaceMock).toHaveBeenCalledWith('/')
  })
})