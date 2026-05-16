import { reactive } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ChatMessageItem from './ChatMessageItem.vue'

const chatMessageItemMocks = vi.hoisted(() => ({
  audioStore: null as {
    currentPlayingId: number | null
    setCurrentPlaying: ReturnType<typeof vi.fn>
  } | null,
  prewarmFileCacheMock: vi.fn(),
  observeVisibilityMock: vi.fn(() => () => {}),
  handleFileClickMock: vi.fn(),
  shareFileMock: vi.fn(),
  seedFileCacheMock: vi.fn(),
  canShareFilesMock: vi.fn(() => false),
  cachedFileRegistry: {} as Record<string, boolean>,
}))

vi.mock('../../stores/audio', async () => {
  const { reactive } = await import('vue')
  const audioStore = reactive({
    currentPlayingId: null as number | null,
    setCurrentPlaying: vi.fn((messageId: number | null) => {
      audioStore.currentPlayingId = messageId
    }),
  })
  chatMessageItemMocks.audioStore = audioStore
  return {
    useAudioStore: () => audioStore,
  }
})

vi.mock('../../utils/sharedVisibilityObserver', () => ({
  observeVisibility: chatMessageItemMocks.observeVisibilityMock,
}))

vi.mock('../../utils/messageReactions', () => ({
  MESSAGE_REACTION_ORDER: new Map(),
}))

vi.mock('../../composables/chat/useChatFileHandler', async () => {
  const { reactive } = await import('vue')
  return {
    handleFileClick: chatMessageItemMocks.handleFileClickMock,
    shareFile: chatMessageItemMocks.shareFileMock,
    canShareFiles: chatMessageItemMocks.canShareFilesMock,
    useChatFileHandler: () => ({ downloadingFiles: reactive({}) }),
    prewarmFileCache: chatMessageItemMocks.prewarmFileCacheMock,
    isFileCached: vi.fn((fileId: string) => Boolean(chatMessageItemMocks.cachedFileRegistry[fileId])),
    seedFileCache: chatMessageItemMocks.seedFileCacheMock,
    useFileCacheRegistry: () => chatMessageItemMocks.cachedFileRegistry,
  }
})

let nextPlayShouldReject = false

class FakeAudio extends EventTarget {
  src: string
  preload = ''
  duration = 8
  currentTime = 0
  readyState = 2

  constructor(src: string) {
    super()
    this.src = src
  }

  async play() {
    if (nextPlayShouldReject) {
      throw new Error('play failed')
    }
    this.dispatchEvent(new Event('loadedmetadata'))
    this.dispatchEvent(new Event('canplay'))
    this.dispatchEvent(new Event('play'))
  }

  pause() {
    this.dispatchEvent(new Event('pause'))
  }

  load() {}
}

function buildVoiceMessage(overrides: Record<string, unknown> = {}) {
  return {
    id: 51,
    sender_id: 9,
    content: JSON.stringify({ file_id: 'voice-51', durationMs: 8000 }),
    message_type: 'voice',
    created_at: '2026-05-12T10:00:00.000Z',
    is_deleted: false,
    reactions: [],
    ...overrides,
  }
}

function buildTextMessage(overrides: Record<string, unknown> = {}) {
  return {
    id: 61,
    sender_id: 9,
    content: 'forwarded text',
    message_type: 'text',
    created_at: '2026-05-12T10:00:00.000Z',
    is_deleted: false,
    reactions: [],
    ...overrides,
  }
}

function mountVoiceMessage(messageOverrides: Record<string, unknown> = {}) {
  return mount(ChatMessageItem, {
    props: {
      msg: buildVoiceMessage(messageOverrides),
      currentUserId: 7,
      selectedUserName: 'Ali',
      selectedMessages: [],
      imageCache: {},
      isSelectionMode: false,
    },
    global: {
      stubs: {
        ChatAlbumLayout: {
          template: '<div class="album-layout-stub"></div>',
        },
      },
    },
  })
}

function mountTextMessage(messageOverrides: Record<string, unknown> = {}) {
  return mount(ChatMessageItem, {
    props: {
      msg: buildTextMessage(messageOverrides),
      currentUserId: 7,
      selectedUserName: 'Ali',
      selectedMessages: [],
      imageCache: {},
      isSelectionMode: false,
    },
    global: {
      stubs: {
        ChatAlbumLayout: {
          template: '<div class="album-layout-stub"></div>',
        },
      },
    },
  })
}

function mountMediaMessage(messageOverrides: Record<string, unknown> = {}, extraProps: Record<string, unknown> = {}) {
  return mount(ChatMessageItem, {
    props: {
      msg: {
        id: 71,
        sender_id: 9,
        content: JSON.stringify({
          file_id: 'image-71',
          thumbnail: 'data:image/png;base64,thumb',
          caption: 'کپشن رسانه',
        }),
        message_type: 'image',
        created_at: '2026-05-12T10:00:00.000Z',
        is_deleted: false,
        reactions: [],
        ...messageOverrides,
      },
      currentUserId: 7,
      selectedUserName: 'Ali',
      selectedMessages: [],
      imageCache: {},
      isSelectionMode: false,
      ...extraProps,
    },
    global: {
      stubs: {
        ChatAlbumLayout: {
          template: '<div class="album-layout-stub"></div>',
        },
      },
    },
  })
}

function mountDocumentMessage(messageOverrides: Record<string, unknown> = {}) {
  return mount(ChatMessageItem, {
    props: {
      msg: {
        id: 91,
        sender_id: 9,
        content: JSON.stringify({
          file_id: 'doc-91',
          file_name: 'doc-91.pdf',
          mime_type: 'application/pdf',
          size: 2048,
        }),
        message_type: 'document',
        created_at: '2026-05-12T10:00:00.000Z',
        is_deleted: false,
        reactions: [],
        ...messageOverrides,
      },
      currentUserId: 7,
      selectedUserName: 'Ali',
      selectedMessages: [],
      imageCache: {},
      isSelectionMode: false,
    },
    global: {
      stubs: {
        ChatAlbumLayout: {
          template: '<div class="album-layout-stub"></div>',
        },
      },
    },
  })
}

function mountLocationMessage(messageOverrides: Record<string, unknown> = {}) {
  return mount(ChatMessageItem, {
    props: {
      msg: {
        id: 101,
        sender_id: 9,
        content: JSON.stringify({ lat: 35.7, lng: 51.4, snapshot_id: 'snapshot-101' }),
        message_type: 'location',
        created_at: '2026-05-12T10:00:00.000Z',
        is_deleted: false,
        reactions: [],
        ...messageOverrides,
      },
      currentUserId: 7,
      selectedUserName: 'Ali',
      selectedMessages: [],
      imageCache: {},
      isSelectionMode: false,
    },
    global: {
      stubs: {
        ChatAlbumLayout: {
          template: '<div class="album-layout-stub"></div>',
        },
      },
    },
  })
}

describe('ChatMessageItem.vue', () => {
  const originalAudio = globalThis.Audio

  beforeEach(() => {
    chatMessageItemMocks.audioStore!.currentPlayingId = null
    chatMessageItemMocks.audioStore!.setCurrentPlaying.mockClear()
    chatMessageItemMocks.prewarmFileCacheMock.mockClear()
    chatMessageItemMocks.observeVisibilityMock.mockClear()
    chatMessageItemMocks.handleFileClickMock.mockReset()
    chatMessageItemMocks.handleFileClickMock.mockResolvedValue(undefined)
    chatMessageItemMocks.shareFileMock.mockReset()
    chatMessageItemMocks.shareFileMock.mockResolvedValue(true)
    chatMessageItemMocks.seedFileCacheMock.mockReset()
    chatMessageItemMocks.canShareFilesMock.mockReset()
    chatMessageItemMocks.canShareFilesMock.mockReturnValue(false)
    Object.keys(chatMessageItemMocks.cachedFileRegistry).forEach((key) => delete chatMessageItemMocks.cachedFileRegistry[key])
    nextPlayShouldReject = false
    localStorage.setItem('auth_token', 'test-token')
    vi.stubGlobal('Audio', FakeAudio)
    vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  afterEach(() => {
    localStorage.clear()
    if (originalAudio) {
      vi.stubGlobal('Audio', originalAudio)
    }
  })

  it('renders the voice waveform and toggles playback state', async () => {
    const wrapper = mountVoiceMessage()

    expect(wrapper.findAll('.voice-wave-bar')).toHaveLength(40)
    expect(chatMessageItemMocks.prewarmFileCacheMock).toHaveBeenCalledWith('voice-51')

    await wrapper.find('.voice-play-btn').trigger('click')
    await flushPromises()

    expect(wrapper.find('.voice-play-btn').classes()).toContain('is-active')
    expect(chatMessageItemMocks.audioStore!.currentPlayingId).toBe(51)
    expect(chatMessageItemMocks.audioStore!.setCurrentPlaying).toHaveBeenCalledWith(51)

    await wrapper.find('.voice-play-btn').trigger('click')
    await flushPromises()

    expect(wrapper.find('.voice-play-btn').classes()).not.toContain('is-active')
    expect(chatMessageItemMocks.audioStore!.currentPlayingId).toBeNull()
    expect(chatMessageItemMocks.audioStore!.setCurrentPlaying).toHaveBeenCalledWith(null)
  })

  it('marks the bubble as errored when voice playback fails', async () => {
    nextPlayShouldReject = true
    const wrapper = mountVoiceMessage()

    await wrapper.find('.voice-play-btn').trigger('click')
    await flushPromises()

    expect(wrapper.find('.msg-voice').classes()).toContain('is-error')
    expect(chatMessageItemMocks.audioStore!.currentPlayingId).toBeNull()
    expect(chatMessageItemMocks.audioStore!.setCurrentPlaying).toHaveBeenCalledWith(null)
  })

  it('emits owner profile targets for forwarded accountant messages', async () => {
    const wrapper = mountTextMessage({
      forwarded_from_id: 12,
      forwarded_from_name: 'دفتر حسابدار',
      forwarded_from_profile_user_id: 88,
      forwarded_from_profile_account_name: 'owner-88',
      forwarded_from_highlight_accountant_user_id: 12,
      forwarded_from_highlight_accountant_relation_display_name: 'حسابدار فروش',
    })

    await wrapper.get('.forward-link').trigger('click')

    expect(wrapper.emitted('open-public-profile')).toEqual([
      [{
        id: 88,
        account_name: 'owner-88',
        highlight_accountant_user_id: 12,
        highlight_accountant_relation_display_name: 'حسابدار فروش',
      }],
    ])
  })

  it('renders captions for single media bubbles', () => {
    const wrapper = mountMediaMessage()

    expect(wrapper.find('.media-caption').text()).toContain('کپشن رسانه')
  })

  it('renders the first album item caption under album bubbles', () => {
    const albumLeadMessage = {
      id: 81,
      sender_id: 9,
      content: JSON.stringify({
        file_id: 'image-81',
        thumbnail: 'data:image/png;base64,thumb',
        caption: 'کپشن آلبوم',
        album_id: 'album-1',
        album_index: 0,
      }),
      message_type: 'image',
      created_at: '2026-05-12T10:00:00.000Z',
      is_deleted: false,
      reactions: [],
    }
    const wrapper = mountMediaMessage(albumLeadMessage, {
      isAlbum: true,
      albumItems: [
        albumLeadMessage,
        {
          ...albumLeadMessage,
          id: 82,
          content: JSON.stringify({
            file_id: 'image-82',
            thumbnail: 'data:image/png;base64,thumb',
            album_id: 'album-1',
            album_index: 1,
          }),
        },
      ],
    })

    expect(wrapper.find('.album-layout-stub').exists()).toBe(true)
    expect(wrapper.find('.media-caption').text()).toContain('کپشن آلبوم')
  })

  it('emits scroll and location events for reply contexts and location bubbles', async () => {
    const replyWrapper = mountTextMessage({
      reply_to_message: {
        id: 44,
        sender_id: 7,
        content: 'reply',
        message_type: 'image',
      },
    })

    await replyWrapper.get('.reply-context').trigger('click')
    expect(replyWrapper.emitted('scroll-to')).toEqual([[44]])

    const locationWrapper = mountLocationMessage()
    expect(locationWrapper.find('.location-snapshot').attributes('style')).toContain('/api/chat/files/snapshot-101?token=test-token')

    await locationWrapper.get('.msg-location').trigger('click')
    expect(locationWrapper.emitted('location-click')).toHaveLength(1)
  })

  it('opens document messages through the shared file handler and falls back to legacy download when needed', async () => {
    const wrapper = mountDocumentMessage()

    await wrapper.get('.msg-document').trigger('click')
    expect(chatMessageItemMocks.handleFileClickMock).toHaveBeenCalledWith(
      'doc-91',
      expect.stringContaining('/api/chat/files/doc-91?token=test-token'),
      'doc-91.pdf',
    )

    chatMessageItemMocks.handleFileClickMock.mockRejectedValueOnce(new Error('open failed'))
    await wrapper.get('.msg-document').trigger('click')
    expect(wrapper.emitted('download')).toHaveLength(1)

    const legacyWrapper = mountDocumentMessage({
      content: JSON.stringify({ file_name: 'legacy.pdf', mime_type: 'application/pdf' }),
    })
    await legacyWrapper.get('.msg-document').trigger('click')
    expect(legacyWrapper.emitted('download')).toHaveLength(1)
  })

  it('emits the correct cancel event for busy document bubbles', async () => {
    const sendingWrapper = mountDocumentMessage({
      is_sending: true,
      upload_progress: 33,
    })
    await sendingWrapper.get('.doc-icon.doc-uploading').trigger('click')
    expect(sendingWrapper.emitted('cancel-send')).toHaveLength(1)

    const downloadingWrapper = mountDocumentMessage({
      is_downloading: true,
      download_progress: 45,
    })
    await downloadingWrapper.get('.doc-icon.doc-uploading').trigger('click')
    expect(downloadingWrapper.emitted('cancel-download')).toHaveLength(1)
  })

  it('shows a lightweight toast when document sharing is unavailable', async () => {
    chatMessageItemMocks.shareFileMock.mockResolvedValue(false)
    const wrapper = mountDocumentMessage()

    await (wrapper.vm as any).handleDocumentShareClick(new Event('click'))

    expect(chatMessageItemMocks.shareFileMock).toHaveBeenCalledWith(
      'doc-91',
      'doc-91.pdf',
      'application/pdf',
      expect.stringContaining('/api/chat/files/doc-91?token=test-token'),
    )
    expect(document.getElementById('chat-file-share-toast')?.textContent).toContain('اشتراک‌گذاری این فایل در این مرورگر پشتیبانی نمی‌شود')
    document.getElementById('chat-file-share-toast')?.remove()
  })

  it('forwards album child events to the parent emits', async () => {
    const albumLeadMessage = {
      id: 121,
      sender_id: 9,
      content: JSON.stringify({
        file_id: 'image-121',
        thumbnail: 'data:image/png;base64,thumb',
        caption: 'album lead',
        album_id: 'album-forward',
        album_index: 0,
      }),
      message_type: 'image',
      created_at: '2026-05-12T10:00:00.000Z',
      is_deleted: false,
      reactions: [],
    }
    const wrapper = mount(ChatMessageItem, {
      props: {
        msg: albumLeadMessage,
        currentUserId: 7,
        selectedUserName: 'Ali',
        selectedMessages: [],
        imageCache: {},
        isSelectionMode: false,
        isAlbum: true,
        albumItems: [albumLeadMessage],
      },
      global: {
        stubs: {
          ChatAlbumLayout: {
            template: `
              <div>
                <button class="album-download" @click="$emit('download', 1)">d</button>
                <button class="album-cancel-send" @click="$emit('cancel-send', 2)">cs</button>
                <button class="album-cancel-download" @click="$emit('cancel-download', 3)">cd</button>
                <button class="album-reply" @click="$emit('reply-item', 4)">r</button>
                <button class="album-forward" @click="$emit('forward-item', 5)">f</button>
                <button class="album-delete" @click="$emit('delete-item', 6)">del</button>
                <button class="album-toggle" @click="$emit('toggle-download-item', 7)">t</button>
              </div>
            `,
          },
        },
      },
    })

    await wrapper.get('.album-download').trigger('click')
    await wrapper.get('.album-cancel-send').trigger('click')
    await wrapper.get('.album-cancel-download').trigger('click')
    await wrapper.get('.album-reply').trigger('click')
    await wrapper.get('.album-forward').trigger('click')
    await wrapper.get('.album-delete').trigger('click')
    await wrapper.get('.album-toggle').trigger('click')

    expect(wrapper.emitted('download')).toEqual([[1]])
    expect(wrapper.emitted('cancel-send')).toEqual([[2]])
    expect(wrapper.emitted('cancel-download')).toEqual([[3]])
    expect(wrapper.emitted('reply-album-item')).toEqual([[4]])
    expect(wrapper.emitted('forward-album-item')).toEqual([[5]])
    expect(wrapper.emitted('delete-album-item')).toEqual([[6]])
    expect(wrapper.emitted('toggle-album-download-item')).toEqual([[7]])
  })

  it('emits media and text action events for download and cancel states', async () => {
    const textWrapper = mountTextMessage({
      sender_id: 7,
      is_sending: true,
    })
    await textWrapper.get('.cancel-text-btn').trigger('click')
    expect(textWrapper.emitted('cancel-send')).toHaveLength(1)

    const downloadingMediaWrapper = mountMediaMessage({
      content: JSON.stringify({ file_id: 'image-71' }),
      is_downloading: true,
      download_progress: 45,
      local_blob_url: '',
    })
    await downloadingMediaWrapper.get('.msg-media-link .progress-container.cancelable').trigger('click')
    expect(downloadingMediaWrapper.emitted('cancel-download')).toHaveLength(1)

    const downloadableMediaWrapper = mountMediaMessage({
      content: JSON.stringify({ file_id: 'image-71' }),
      local_blob_url: '',
    })
    await downloadableMediaWrapper.get('.msg-media-link .download-btn').trigger('click')
    expect(downloadableMediaWrapper.emitted('download')).toHaveLength(1)

    const uploadingMediaWrapper = mountMediaMessage({
      local_blob_url: 'blob:uploading-image',
      is_sending: true,
      upload_progress: 40,
      upload_total: 100,
      upload_loaded: 40,
    })
    await uploadingMediaWrapper.get('.msg-media-overlay.cancelable-overlay').trigger('click')
    expect(uploadingMediaWrapper.emitted('cancel-send')).toHaveLength(1)
  })

  it('treats optimistic sent messages as sending only until upload handoff is marked pending', () => {
    const sendingWrapper = mountTextMessage({
      id: -111,
      sender_id: 7,
      is_error: false,
      upload_handoff_pending: false,
    })

    expect(sendingWrapper.get('.message-bubble').classes()).toContain('sending')
    expect(sendingWrapper.find('.cancel-text-btn').exists()).toBe(true)

    const handedOffWrapper = mountTextMessage({
      id: -112,
      sender_id: 7,
      is_error: false,
      upload_handoff_pending: true,
      is_read: false,
    })

    expect(handedOffWrapper.get('.message-bubble').classes()).not.toContain('sending')
    expect(handedOffWrapper.find('.cancel-text-btn').exists()).toBe(false)
    expect(handedOffWrapper.find('.icon-unread').exists()).toBe(true)
  })

  it('groups reactions, prioritizes the current user reaction, and switches to selection mode behavior when needed', async () => {
    const reactiveWrapper = mountTextMessage({
      reactions: [
        { emoji: '👍', user_id: 12 },
        { emoji: '🔥', user_id: 7 },
        { emoji: '🔥', user_id: 13 },
        { emoji: '', user_id: 14 },
      ],
    })

    const chips = reactiveWrapper.findAll('.reaction-chip')
    expect(chips).toHaveLength(2)
    expect(chips[0]?.text()).toContain('🔥')
    expect(chips[0]?.text()).toContain('2')
    expect(chips[0]?.classes()).toContain('is-own')

    await chips[0]!.trigger('click')
    expect(reactiveWrapper.emitted('toggle-reaction')).toEqual([[
      { msg: (reactiveWrapper.props() as Record<string, unknown>).msg, emoji: '🔥' },
    ]])

    const selectionWrapper = mount(ChatMessageItem, {
      props: {
        msg: buildTextMessage({
          reactions: [{ emoji: '🔥', user_id: 7 }],
        }),
        currentUserId: 7,
        selectedUserName: 'Ali',
        selectedMessages: [],
        imageCache: {},
        isSelectionMode: true,
      },
      global: {
        stubs: {
          ChatAlbumLayout: {
            template: '<div class="album-layout-stub"></div>',
          },
        },
      },
    })

    await selectionWrapper.get('.reaction-chip').trigger('click')
    expect(selectionWrapper.emitted('select')).toEqual([[(selectionWrapper.props() as Record<string, unknown>).msg]])
    expect(selectionWrapper.emitted('toggle-reaction')).toBeUndefined()
  })

  it('shares media by seeding the unified file cache from an existing local blob URL', async () => {
    chatMessageItemMocks.canShareFilesMock.mockReturnValue(true)
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === 'blob:cached-image') {
        return new Response(new Blob(['image-cache'], { type: 'image/png' }), {
          status: 200,
        })
      }
      throw new Error(`Unexpected fetch ${String(input)}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mountMediaMessage({}, {
      imageCache: { 'image-71': 'blob:cached-image' },
    })

    await wrapper.get('.media-share-btn').trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledWith('blob:cached-image')
    expect(chatMessageItemMocks.seedFileCacheMock).toHaveBeenCalledTimes(1)
    const [seededFileId, seededBlob, seededFileName, seededMime] = chatMessageItemMocks.seedFileCacheMock.mock.calls[0]!
    expect(seededFileId).toBe('image-71')
    expect(seededBlob).toMatchObject({
      size: expect.any(Number),
      type: expect.any(String),
    })
    expect(seededFileName).toBe('image-image-71.jpg')
    expect(seededMime).toBeTruthy()
    expect(chatMessageItemMocks.shareFileMock).toHaveBeenCalledWith(
      'image-71',
      'image-image-71.jpg',
      'image/jpeg',
      expect.stringContaining('/api/chat/files/image-71?token=test-token'),
    )
  })
})