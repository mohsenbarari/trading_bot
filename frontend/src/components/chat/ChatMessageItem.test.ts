import { reactive } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ChatMessageItem from './ChatMessageItem.vue'
import { cacheCurrentUserSummary, clearCurrentUserSummary } from '../../utils/currentUser'

const chatMessageItemMocks = vi.hoisted(() => ({
  audioStore: null as {
    currentPlayingId: number | null
    setCurrentPlaying: ReturnType<typeof vi.fn>
  } | null,
  prewarmFileCacheMock: vi.fn(),
  observeVisibilityMock: vi.fn(() => () => {}),
  handleFileClickMock: vi.fn(),
  ensureFileCachedMock: vi.fn(),
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
    ensureFileCached: chatMessageItemMocks.ensureFileCachedMock,
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

function flushDocumentDownloadEmit() {
  return new Promise(resolve => setTimeout(resolve, 0))
}

class FakeAudio extends EventTarget {
  static instances: FakeAudio[] = []
  src: string
  preload = ''
  duration = 8
  currentTime = 0
  readyState = 2

  constructor(src: string) {
    super()
    this.src = src
    FakeAudio.instances.push(this)
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

function makeTouch(clientX: number, clientY: number) {
  return { clientX, clientY }
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
    chatMessageItemMocks.ensureFileCachedMock.mockReset()
    chatMessageItemMocks.shareFileMock.mockReset()
    chatMessageItemMocks.shareFileMock.mockResolvedValue(true)
    chatMessageItemMocks.seedFileCacheMock.mockReset()
    chatMessageItemMocks.canShareFilesMock.mockReset()
    chatMessageItemMocks.canShareFilesMock.mockReturnValue(false)
    Object.keys(chatMessageItemMocks.cachedFileRegistry).forEach((key) => delete chatMessageItemMocks.cachedFileRegistry[key])
    nextPlayShouldReject = false
    FakeAudio.instances = []
    localStorage.setItem('auth_token', 'test-token')
    cacheCurrentUserSummary({ id: 7, role: 'عادی', account_name: 'ali' })
    vi.stubGlobal('Audio', FakeAudio)
    vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  afterEach(() => {
    clearCurrentUserSummary()
    localStorage.clear()
    if (originalAudio) {
      vi.stubGlobal('Audio', originalAudio)
    }
  })

  it('keeps regular text and voice bubbles content-sized instead of full width', async () => {
    const textWrapper = mountTextMessage({ content: 'سلام مامان' })
    const voiceWrapper = mountVoiceMessage()

    await flushPromises()

    const textBubble = textWrapper.find('.message-bubble')
    const voiceBubble = voiceWrapper.find('.message-bubble')

    expect(textBubble.classes()).toContain('type-text')
    expect(textBubble.classes()).not.toContain('full-width-bubble')
    expect(voiceBubble.classes()).toContain('type-voice')
    expect(voiceBubble.classes()).not.toContain('full-width-bubble')
  })

  it('highlights recognized own and non-own mentions while ignoring unrelated handles', async () => {
    const wrapper = mountTextMessage({
      content: 'سلام @ali @reza @ghost @all',
      mentions: [7, 9],
      mention_all: true,
      mention_details: [
        { user_id: 7, account_name: 'ali' },
        { user_id: 9, account_name: 'reza' },
      ],
    })

    await flushPromises()

    const html = wrapper.html()
    expect(html).toContain('<span class="message-mention own-mention clickable" data-mention-user-id="7">@ali</span>')
    expect(html).toContain('<span class="message-mention clickable" data-mention-user-id="9">@reza</span>')
    expect(html).toContain('<span class="message-mention own-mention">@all</span>')
    expect(html).not.toContain('<span class="message-mention">@ghost</span>')
    expect(html).not.toContain('<span class="message-mention own-mention">@ghost</span>')
  })

  it('emits open-public-profile when a mention is clicked', async () => {
    const wrapper = mountTextMessage({
      content: 'hello @ali',
      mention_details: [
        { user_id: 9, account_name: 'ali' }
      ]
    })
    
    await flushPromises()
    
    const mention = wrapper.find('.message-mention.clickable')
    expect(mention.exists()).toBe(true)
    
    await mention.trigger('click')
    
    const events = wrapper.emitted('open-public-profile')
    expect(events).toBeTruthy()
    expect(events![0]![0]).toEqual({ id: 9, account_name: 'ali' })
    expect(wrapper.emitted('click-message')).toBeFalsy()
  })

  it('opens public profile from single media caption mentions too', async () => {
    const wrapper = mountMediaMessage({
      content: JSON.stringify({
        file_id: 'image-71',
        thumbnail: 'data:image/png;base64,thumb',
        caption: 'caption @ali',
      }),
      mention_details: [
        { user_id: 9, account_name: 'ali' },
      ],
    })

    await flushPromises()

    const mention = wrapper.find('.media-caption .message-mention.clickable')
    expect(mention.exists()).toBe(true)

    await mention.trigger('click')

    const events = wrapper.emitted('open-public-profile')
    expect(events).toBeTruthy()
    expect(events![0]![0]).toEqual({ id: 9, account_name: 'ali' })
  })

  it('renders the voice waveform and toggles playback state', async () => {
    const wrapper = mountVoiceMessage()

    expect(wrapper.findAll('.voice-wave-bar')).toHaveLength(40)
    expect(chatMessageItemMocks.prewarmFileCacheMock).not.toHaveBeenCalled()

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

  it('renders forwarded fallback text, video download badges, voice upload state, and read receipts', async () => {
    const forwardedWrapper = mountTextMessage({
      forwarded_from_name: 'حساب بدون پروفایل',
    })
    expect(forwardedWrapper.find('.forward-link').exists()).toBe(false)
    expect(forwardedWrapper.find('.forward-text').text()).toContain('حساب بدون پروفایل')

    const videoWrapper = mountMediaMessage({
      message_type: 'video',
      content: JSON.stringify({ file_id: 'video-uncached' }),
      local_blob_url: '',
    })
    expect(videoWrapper.find('.media-type-badge').text()).toContain('ویدئو')

    const voiceUploadingWrapper = mountVoiceMessage({
      is_sending: true,
      upload_progress: 55,
      upload_loaded: 550,
      upload_total: 1000,
    })
    expect(voiceUploadingWrapper.find('.msg-voice-uploading').exists()).toBe(true)
    expect(voiceUploadingWrapper.find('.voice-upload-status').text()).toContain('550 Bytes / 1000 Bytes')
    await voiceUploadingWrapper.get('.msg-voice-uploading').trigger('click')
    expect(voiceUploadingWrapper.emitted('cancel-send')).toHaveLength(1)

    const voiceProcessingWrapper = mountVoiceMessage({
      is_sending: true,
      upload_progress: 140,
      upload_loaded: 2048,
      upload_total: 2048,
    })
    expect(voiceProcessingWrapper.find('.voice-upload-status').text()).toContain('در حال پردازش...')
    expect(voiceProcessingWrapper.find('.progress-ring-small .ring-fg').attributes('stroke-dasharray')).toBe('100, 100')

    const readWrapper = mountTextMessage({
      sender_id: 7,
      is_read: true,
    })
    expect(readWrapper.find('.icon-read').exists()).toBe(true)
    expect(readWrapper.find('.icon-unread').exists()).toBe(false)

    const groupReadWrapper = mountTextMessage({
      sender_id: 7,
      is_read: true,
    })
    await groupReadWrapper.setProps({ roomKind: 'group' })
    expect(groupReadWrapper.find('.icon-read').exists()).toBe(false)
    expect(groupReadWrapper.find('.icon-unread').exists()).toBe(true)
  })

  it('renders captions for single media bubbles', () => {
    const wrapper = mountMediaMessage()

    expect(wrapper.find('.media-caption').text()).toContain('کپشن رسانه')
  })

  it('shows the sender name for received group messages like Telegram', async () => {
    const wrapper = mountTextMessage({
      sender_id: 9,
      sender_name: 'کامران',
      sender_profile_user_id: 9,
      sender_profile_account_name: 'کامران',
    })
    await wrapper.setProps({ roomKind: 'group' })

    const senderName = wrapper.get('.group-sender-name')
    expect(senderName.text()).toBe('کامران')

    await senderName.trigger('click')
    expect(wrapper.emitted('open-public-profile')?.[0]).toEqual([{
      id: 9,
      account_name: 'کامران',
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
    }])
  })

  it('does not repeat the current user name above sent group messages', async () => {
    const wrapper = mountTextMessage({
      sender_id: 7,
      sender_name: 'علی',
    })
    await wrapper.setProps({ roomKind: 'group' })

    expect(wrapper.find('.group-sender-name').exists()).toBe(false)
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

    const fallbackLocationWrapper = mountLocationMessage({
      content: JSON.stringify({ lat: 35.7, lng: 51.4 }),
    })
    expect(fallbackLocationWrapper.find('.location-preview.fallback').exists()).toBe(true)
  })

  it('routes uncached documents through the persistent download flow and opens cached files directly', async () => {
    const wrapper = mountDocumentMessage()

    expect(chatMessageItemMocks.prewarmFileCacheMock).toHaveBeenCalledWith('doc-91')

    await wrapper.get('.msg-document').trigger('click')
    await flushDocumentDownloadEmit()
    await flushPromises()
    expect(wrapper.emitted('download')).toHaveLength(1)
    expect(chatMessageItemMocks.handleFileClickMock).not.toHaveBeenCalled()

    chatMessageItemMocks.cachedFileRegistry['doc-91'] = true
    const cachedWrapper = mountDocumentMessage()
    await cachedWrapper.get('.msg-document').trigger('click')
    expect(chatMessageItemMocks.handleFileClickMock).toHaveBeenCalledWith(
      'doc-91',
      expect.stringContaining('/api/chat/files/doc-91?token=test-token'),
      'doc-91.pdf',
    )

    chatMessageItemMocks.handleFileClickMock.mockRejectedValueOnce(new Error('open failed'))
    await cachedWrapper.get('.msg-document').trigger('click')
    expect(cachedWrapper.emitted('download')).toHaveLength(1)

    const legacyWrapper = mountDocumentMessage({
      content: JSON.stringify({ file_name: 'legacy.pdf', mime_type: 'application/pdf' }),
    })
    await legacyWrapper.get('.msg-document').trigger('click')
    expect(legacyWrapper.emitted('download')).toHaveLength(1)

    const busyWrapper = mountDocumentMessage({
      is_downloading: true,
      download_progress: 12,
    })
    await busyWrapper.get('.msg-document').trigger('click')
    expect(chatMessageItemMocks.handleFileClickMock).toHaveBeenCalledTimes(2)
  })

  it('shows document busy state before persistent download state arrives', async () => {
    const wrapper = mountDocumentMessage()

    await wrapper.get('.msg-document').trigger('click')
    await flushPromises()

    expect(wrapper.find('.msg-document').classes()).toContain('is-busy')
    expect(wrapper.find('.doc-icon.doc-uploading').exists()).toBe(true)
  })

  it('formats document variants, busy progress text, and missing file ids', async () => {
    const noExtensionWrapper = mountDocumentMessage({
      content: JSON.stringify({ file_id: 'doc-json', file_name: 'payload', mime_type: 'application/json', size: 4096 }),
    })
    expect(noExtensionWrapper.find('.doc-extension-badge').text()).toBe('JSON')
    expect(noExtensionWrapper.find('.doc-icon').classes()).toContain('doc-generic')
    expect(noExtensionWrapper.find('.doc-size').text()).toContain('4 KB')

    const archiveWrapper = mountDocumentMessage({
      content: JSON.stringify({ file_id: 'doc-zip', file_name: 'archive.7z', mime_type: 'application/x-7z-compressed' }),
    })
    expect(archiveWrapper.find('.doc-icon').classes()).toContain('doc-archive')

    const sendingWrapper = mountDocumentMessage({
      is_sending: true,
      upload_progress: 25,
      upload_loaded: 512,
      upload_total: 2048,
    })
    expect(sendingWrapper.find('.doc-size').text()).toContain('512 Bytes')

    const processingWrapper = mountDocumentMessage({
      is_sending: true,
      upload_progress: 100,
      upload_loaded: 2048,
      upload_total: 2048,
    })
    expect(processingWrapper.find('.doc-size').text()).toContain('در حال پردازش...')

    const downloadingWrapper = mountDocumentMessage({
      is_downloading: true,
      download_progress: 73,
    })
    expect(downloadingWrapper.find('.doc-size').text()).toContain('73%')

    const missingFileIdWrapper = mountDocumentMessage({
      content: JSON.stringify({ file_name: 'legacy.bin', mime_type: 'application/octet-stream' }),
    })
    await missingFileIdWrapper.get('.msg-document').trigger('click')
    expect(missingFileIdWrapper.emitted('download')).toHaveLength(1)
    await (missingFileIdWrapper.vm as any).handleDocumentShareClick(new Event('click'))
    expect(chatMessageItemMocks.shareFileMock).not.toHaveBeenCalledWith('', expect.anything(), expect.anything(), expect.anything())
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
      upload_progress: 140,
      upload_total: 100,
      upload_loaded: 100,
    })
    expect(uploadingMediaWrapper.find('.telegram-size-badge').text()).toContain('در حال پردازش...')
    expect(uploadingMediaWrapper.find('.msg-media-overlay .ring-fg').attributes('stroke-dasharray')).toBe('100, 100')
    await uploadingMediaWrapper.get('.msg-media-overlay.cancelable-overlay').trigger('click')
    expect(uploadingMediaWrapper.emitted('cancel-send')).toHaveLength(1)
  })

  it('prefers the current media cache over a stale local blob URL after upload hydration', () => {
    const wrapper = mountMediaMessage({
      local_blob_url: 'blob:revoked-local-preview',
    }, {
      imageCache: { 'image-71': 'blob:fresh-cached-media' },
    })

    expect(wrapper.get('img.msg-media-content').attributes('src')).toBe('blob:fresh-cached-media')

    const albumWrapper = mount(ChatMessageItem, {
      props: {
        msg: {
          id: 72,
          sender_id: 7,
          content: JSON.stringify({ file_id: 'image-72', album_id: 'album-72', album_index: 0 }),
          message_type: 'image',
          created_at: '2026-05-12T10:00:00.000Z',
          is_deleted: false,
          reactions: [],
          local_blob_url: 'blob:revoked-album-preview',
        },
        currentUserId: 7,
        selectedUserName: 'Ali',
        selectedMessages: [],
        imageCache: { 'image-72': 'blob:fresh-album-cache' },
        isSelectionMode: false,
        isAlbum: true,
        albumItems: [{
          id: 72,
          sender_id: 7,
          content: JSON.stringify({ file_id: 'image-72', album_id: 'album-72', album_index: 0 }),
          message_type: 'image',
          created_at: '2026-05-12T10:00:00.000Z',
          is_deleted: false,
          reactions: [],
          local_blob_url: 'blob:revoked-album-preview',
        }],
      },
      global: {
        stubs: {
          ChatAlbumLayout: {
            props: ['items'],
            template: '<div class="album-layout-stub">{{ items[0].url }}</div>',
          },
        },
      },
    })

    expect(albumWrapper.text()).toContain('blob:fresh-album-cache')
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
      { msg: (reactiveWrapper.props() as unknown as Record<string, unknown>).msg, emoji: '🔥' },
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
    expect(selectionWrapper.emitted('select')).toEqual([[(selectionWrapper.props() as unknown as Record<string, unknown>).msg]])
    expect(selectionWrapper.emitted('toggle-reaction')).toBeUndefined()
  })

  it('covers reaction tie sorting, media size fallbacks, and generic media helper fallbacks', async () => {
    const { MESSAGE_REACTION_ORDER } = await import('../../utils/messageReactions')
    MESSAGE_REACTION_ORDER.clear()
    MESSAGE_REACTION_ORDER.set('😀', 10)
    MESSAGE_REACTION_ORDER.set('👍', 1)

    const sortedWrapper = mountTextMessage({
      reactions: [
        { emoji: '😀', user_id: 18 },
        { emoji: '😀', user_id: 19 },
        { emoji: '👍', user_id: 20 },
        { emoji: '👍', user_id: 21 },
      ],
    })
    const reactionTexts = sortedWrapper.findAll('.reaction-chip').map((chip) => chip.text())
    expect(reactionTexts[0]).toContain('👍')
    MESSAGE_REACTION_ORDER.clear()

    const invalidSizeWrapper = mountMediaMessage({
      content: JSON.stringify({ file_id: 'invalid-size', width: 0, height: 120 }),
      local_blob_url: 'blob:invalid-size',
    })
    expect(invalidSizeWrapper.get('.msg-media-link').attributes('style')).toContain('min-height: 200px')

    const missingSizeWrapper = mountMediaMessage({
      content: JSON.stringify({ file_id: 'missing-size' }),
      local_blob_url: 'blob:missing-size',
    })
    expect(missingSizeWrapper.get('.msg-media-link').attributes('style')).toContain('min-height: 200px')

    const genericWrapper = mountTextMessage({
      id: 62,
      message_type: 'system',
      content: '',
    })
    expect((genericWrapper.vm as any).getMediaMimeFallback()).toBe('application/octet-stream')
    expect((genericWrapper.vm as any).getMediaFileNameFallback()).toBe('file-media-62')
    expect((genericWrapper.vm as any).getImageThumbnail('thumb::raw-base64-thumbnail')).toBe('data:image/jpeg;base64,raw-base64-thumbnail')
    expect((genericWrapper.vm as any).getImageThumbnail('thumb::data:image/png;base64,abc')).toBe('data:image/png;base64,abc')
  })

  it('renders recovery action buttons and emits the selected recovery action payload', async () => {
    const wrapper = mountTextMessage({
      recovery_action: {
        recovery_id: 'rec-1',
        user_id: 55,
        can_approve: true,
        can_reject: true,
        can_request_identity: true,
      },
    })

    await wrapper.get('button').trigger('click')
    await wrapper.get('button:nth-of-type(2)').trigger('click')
    await wrapper.get('button:nth-of-type(3)').trigger('click')

    expect(wrapper.text()).toContain('درخواست مدرک')
    expect(wrapper.text()).toContain('رد درخواست')
    expect(wrapper.text()).toContain('تایید درخواست')
    expect(wrapper.emitted('recovery-action')).toEqual([
      [{ action: 'request_identity', recoveryId: 'rec-1', msg: (wrapper.props() as unknown as Record<string, unknown>).msg, userId: 55 }],
      [{ action: 'reject', recoveryId: 'rec-1', msg: (wrapper.props() as unknown as Record<string, unknown>).msg, userId: 55 }],
      [{ action: 'approve', recoveryId: 'rec-1', msg: (wrapper.props() as unknown as Record<string, unknown>).msg, userId: 55 }],
    ])
  })

  it('shares media by seeding the unified file cache from an existing local blob URL', async () => {
    chatMessageItemMocks.canShareFilesMock.mockReturnValue(true)
    chatMessageItemMocks.ensureFileCachedMock.mockResolvedValue({ fileName: 'image-image-71.jpg' })

    const wrapper = mountMediaMessage({}, {
      imageCache: { 'image-71': 'blob:cached-image' },
    })

    await wrapper.get('.media-share-btn').trigger('click')
    await flushPromises()

    expect(chatMessageItemMocks.ensureFileCachedMock).toHaveBeenCalledWith(
      'image-71',
      'image-image-71.jpg',
      {
        mimeType: 'image/jpeg',
        localUrl: 'blob:cached-image',
        fileUrl: expect.stringContaining('/api/chat/files/image-71?token=test-token'),
      },
    )
    expect(chatMessageItemMocks.shareFileMock).toHaveBeenCalledWith(
      'image-71',
      'image-image-71.jpg',
      'image/jpeg',
      expect.stringContaining('/api/chat/files/image-71?token=test-token'),
    )
  })

  it('handles wrapper click, context menu, long press selection, and swipe-to-reply gestures', async () => {
    vi.useFakeTimers()
    Object.defineProperty(navigator, 'vibrate', {
      configurable: true,
      value: vi.fn(),
    })

    const wrapper = mountTextMessage()
    await wrapper.get('.message-wrapper').trigger('click')
    expect(wrapper.emitted('click-message')).toHaveLength(1)

    await wrapper.get('.message-wrapper').trigger('contextmenu')
    expect(wrapper.emitted('context-menu')).toHaveLength(1)

    await wrapper.get('.message-wrapper').trigger('touchstart', { touches: [makeTouch(10, 20)] })
    await vi.advanceTimersByTimeAsync(500)
    expect(navigator.vibrate).toHaveBeenCalledWith(50)
    expect(wrapper.emitted('select')).toHaveLength(1)
    await wrapper.get('.message-wrapper').trigger('touchend')

    const receivedSwipeWrapper = mountTextMessage({ id: 62, sender_id: 9 })
    await receivedSwipeWrapper.get('.message-bubble').trigger('touchstart', { touches: [makeTouch(0, 0)] })
    await receivedSwipeWrapper.get('.message-bubble').trigger('touchmove', { touches: [makeTouch(160, 4)] })
    expect(receivedSwipeWrapper.find('.swipe-reply-icon').exists()).toBe(true)
    await receivedSwipeWrapper.get('.message-bubble').trigger('touchend')
    expect(receivedSwipeWrapper.emitted('swipe-reply')).toHaveLength(1)

    const sentSwipeWrapper = mountTextMessage({ id: 63, sender_id: 7 })
    await sentSwipeWrapper.get('.message-bubble').trigger('touchstart', { touches: [makeTouch(160, 0)] })
    await sentSwipeWrapper.get('.message-bubble').trigger('touchmove', { touches: [makeTouch(0, 4)] })
    await sentSwipeWrapper.get('.message-bubble').trigger('touchend')
    expect(sentSwipeWrapper.emitted('swipe-reply')).toHaveLength(1)

    const verticalSwipeWrapper = mountTextMessage({ id: 64, sender_id: 9 })
    await verticalSwipeWrapper.get('.message-bubble').trigger('touchstart', { touches: [makeTouch(0, 0)] })
    await verticalSwipeWrapper.get('.message-bubble').trigger('touchmove', { touches: [makeTouch(10, 80)] })
    await verticalSwipeWrapper.get('.message-bubble').trigger('touchend')
    expect(verticalSwipeWrapper.emitted('swipe-reply')).toBeUndefined()

    wrapper.unmount()
    receivedSwipeWrapper.unmount()
    sentSwipeWrapper.unmount()
    verticalSwipeWrapper.unmount()
    vi.useRealTimers()
  })

  it('renders highlighted text safely plus sticker, video, cached document, and deferred hydration variants', async () => {
    const highlightedWrapper = mountTextMessage({
      content: '<b>needle?</b>',
    })
    await highlightedWrapper.setProps({ searchQuery: 'needle?' })
    expect(highlightedWrapper.html()).toContain('&lt;b&gt;')
    expect(highlightedWrapper.find('mark.in-bubble-highlight').text()).toBe('needle?')

    const stickerWrapper = mountTextMessage({
      message_type: 'sticker',
      content: '🙂',
    })
    expect(stickerWrapper.find('.msg-sticker').text()).toBe('🙂')

    const videoWrapper = mountMediaMessage({
      message_type: 'video',
      content: JSON.stringify({ file_id: 'video-1', file_name: 'clip.mp4', thumbnail: 'data:image/jpeg;base64,thumb', width: 1920, height: 1080 }),
      local_blob_url: 'blob:video',
    })
    expect(videoWrapper.find('video').attributes('src')).toBe('blob:video')
    expect(videoWrapper.find('.video-play-indicator').exists()).toBe(true)

    chatMessageItemMocks.cachedFileRegistry['doc-91'] = true
    const cachedDocWrapper = mountDocumentMessage({
      content: JSON.stringify({ file_id: 'doc-91', file_name: 'sheet.xlsx', mime_type: 'application/vnd.ms-excel', size: 0 }),
    })
    expect(cachedDocWrapper.find('.doc-icon').classes()).toContain('doc-excel')
    expect(cachedDocWrapper.find('.doc-download-icon').exists()).toBe(false)
    expect(cachedDocWrapper.find('.doc-extension-badge').text()).toBe('XLSX')

    let visibilityCallback: (() => void) | undefined
    const onLoad = vi.fn()
    chatMessageItemMocks.observeVisibilityMock.mockImplementationOnce(((
      _element: Element,
      callback: () => void,
    ) => {
      visibilityCallback = callback
      return vi.fn()
    }) as unknown as () => () => void)
    const hydratedWrapper = mountMediaMessage({}, { onLoad })
    visibilityCallback?.()
    await flushPromises()
    expect(onLoad).toHaveBeenCalledTimes(1)
    expect(chatMessageItemMocks.observeVisibilityMock).toHaveBeenCalled()
    hydratedWrapper.unmount()
  })

  it('falls back from local media share seeding to the authenticated API and shows a toast on share rejection', async () => {
    chatMessageItemMocks.canShareFilesMock.mockReturnValue(true)
    chatMessageItemMocks.ensureFileCachedMock.mockResolvedValue({ fileName: 'image-image-71.jpg' })
    chatMessageItemMocks.shareFileMock.mockResolvedValue(false)

    const wrapper = mountMediaMessage({}, {
      imageCache: { 'image-71': 'blob:broken-local' },
    })

    await wrapper.get('.media-share-btn').trigger('click')
    await flushPromises()

    expect(chatMessageItemMocks.ensureFileCachedMock).toHaveBeenCalledWith(
      'image-71',
      'image-image-71.jpg',
      {
        mimeType: 'image/jpeg',
        localUrl: 'blob:broken-local',
        fileUrl: expect.stringContaining('/api/chat/files/image-71?token=test-token'),
      },
    )
    expect(chatMessageItemMocks.shareFileMock).toHaveBeenCalledWith(
      'image-71',
      'image-image-71.jpg',
      'image/jpeg',
      expect.stringContaining('/api/chat/files/image-71?token=test-token'),
    )
    expect(document.getElementById('chat-file-share-toast')?.textContent).toContain('اشتراک‌گذاری این فایل در این مرورگر پشتیبانی نمی‌شود')
    document.getElementById('chat-file-share-toast')?.remove()
  })

  it('falls back when media cache seeding fails and tears down voice audio when the source changes', async () => {
    chatMessageItemMocks.canShareFilesMock.mockReturnValue(true)
    chatMessageItemMocks.ensureFileCachedMock.mockResolvedValue(null)

    const mediaWrapper = mountMediaMessage()
    await mediaWrapper.get('.media-share-btn').trigger('click')
    await flushPromises()

    expect(chatMessageItemMocks.shareFileMock).not.toHaveBeenCalled()
    expect(document.getElementById('chat-file-share-toast')?.textContent).toContain('اشتراک‌گذاری این فایل در این مرورگر پشتیبانی نمی‌شود')
    document.getElementById('chat-file-share-toast')?.remove()

    const helperWrapper = mountTextMessage({
      id: 63,
      message_type: 'system',
      content: '',
    })
    expect((helperWrapper.vm as any).normalizeComparableUrl('http://[')).toBe('http://[')
    expect((helperWrapper.vm as any).ensureVoiceAudio()).toBeNull()

    const voiceWrapper = mountVoiceMessage()
    await voiceWrapper.find('.voice-play-btn').trigger('click')
    await flushPromises()

    const firstAudio = FakeAudio.instances.at(-1)!
    const pauseSpy = vi.spyOn(firstAudio, 'pause')
    const loadSpy = vi.spyOn(firstAudio, 'load')

    firstAudio.duration = 10
    firstAudio.currentTime = 9.99
    firstAudio.dispatchEvent(new Event('timeupdate'))
    firstAudio.dispatchEvent(new Event('pause'))
    await voiceWrapper.find('.voice-play-btn').trigger('click')
    await flushPromises()
    expect(firstAudio.currentTime).toBe(0)

    await voiceWrapper.setProps({
      msg: buildVoiceMessage({
        content: JSON.stringify({ file_id: 'voice-52', durationMs: 8000 }),
      }),
    })
    await flushPromises()

    expect(pauseSpy).toHaveBeenCalled()
    expect(loadSpy).toHaveBeenCalled()
  })

  it('covers selection and touch guard branches for forwarded profiles, recovery actions, clicks, and context menus', async () => {
    vi.useFakeTimers()

    const selectionForwardWrapper = mount(ChatMessageItem, {
      props: {
        msg: buildTextMessage({
          forwarded_from_id: 12,
          forwarded_from_name: 'دفتر حسابدار',
          forwarded_from_profile_user_id: 88,
          forwarded_from_profile_account_name: 'owner-88',
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
    await selectionForwardWrapper.get('.forward-link').trigger('click')
    expect(selectionForwardWrapper.emitted('select')).toHaveLength(1)
    expect(selectionForwardWrapper.emitted('open-public-profile')).toBeUndefined()

    const missingForwardTargetWrapper = mountTextMessage({
      forwarded_from_name: 'بدون هدف',
    })
    ;(missingForwardTargetWrapper.vm as any).handleForwardedProfileClick()
    expect(missingForwardTargetWrapper.emitted('open-public-profile')).toBeUndefined()

    const missingRecoveryWrapper = mountTextMessage({
      recovery_action: {
        can_approve: true,
      },
    })
    ;(missingRecoveryWrapper.vm as any).handleRecoveryActionClick('approve')
    expect(missingRecoveryWrapper.emitted('recovery-action')).toBeUndefined()

    const selectionClickWrapper = mountTextMessage()
    await selectionClickWrapper.setProps({ isSelectionMode: true })
    await selectionClickWrapper.get('.message-wrapper').trigger('click')
    expect(selectionClickWrapper.emitted('select')).toHaveLength(1)
    expect(selectionClickWrapper.emitted('click-message')).toBeUndefined()

    const longPressWrapper = mountTextMessage()
    await longPressWrapper.get('.message-wrapper').trigger('touchstart', { touches: [makeTouch(1, 1)] })
    await vi.advanceTimersByTimeAsync(500)
    await longPressWrapper.get('.message-wrapper').trigger('contextmenu')
    expect(longPressWrapper.emitted('context-menu')).toBeUndefined()
    await longPressWrapper.get('.message-wrapper').trigger('touchcancel')

    const guardedContextWrapper = mountDocumentMessage()
    await guardedContextWrapper.get('.doc-download-icon').trigger('contextmenu')
    expect(guardedContextWrapper.emitted('context-menu')).toBeUndefined()

    const tinySwipeWrapper = mountTextMessage()
    await tinySwipeWrapper.get('.message-bubble').trigger('touchstart', { touches: [makeTouch(0, 0)] })
    await tinySwipeWrapper.get('.message-bubble').trigger('touchmove', { touches: [makeTouch(5, 5)] })
    await tinySwipeWrapper.get('.message-bubble').trigger('touchend')
    expect(tinySwipeWrapper.emitted('swipe-reply')).toBeUndefined()
    expect((tinySwipeWrapper.vm as any).getIconStyle()).toEqual({ opacity: 0, transform: 'scale(0.7)' })

    const shortSwipeWrapper = mountTextMessage({ sender_id: 9 })
    await shortSwipeWrapper.get('.message-bubble').trigger('touchstart', { touches: [makeTouch(0, 0)] })
    await shortSwipeWrapper.get('.message-bubble').trigger('touchmove', { touches: [makeTouch(20, 1)] })
    await shortSwipeWrapper.get('.message-bubble').trigger('touchend')
    expect(shortSwipeWrapper.emitted('swipe-reply')).toBeUndefined()

    vi.useRealTimers()
  })

  it('seeks voice playback from the waveform and pauses when another voice becomes active', async () => {
    const wrapper = mountVoiceMessage()
    const waveform = wrapper.get('.voice-waveform')
    Object.defineProperty(waveform.element, 'getBoundingClientRect', {
      configurable: true,
      value: () => ({ left: 10, width: 100 }),
    })

    await waveform.trigger('click', { clientX: 60 })
    await flushPromises()

    expect(chatMessageItemMocks.audioStore!.currentPlayingId).toBe(51)
    expect(wrapper.findAll('.voice-wave-bar.is-played').length).toBeGreaterThan(0)

    chatMessageItemMocks.audioStore!.setCurrentPlaying(999)
    await flushPromises()

    expect(wrapper.find('.voice-play-btn').classes()).not.toContain('is-active')
  })

  it('updates voice loading, progress, ended, and error states from native audio events', async () => {
    const wrapper = mountVoiceMessage()

    await wrapper.find('.voice-play-btn').trigger('click')
    await flushPromises()

    const audio = FakeAudio.instances.at(-1)!
    audio.duration = 10
    audio.currentTime = 4
    audio.dispatchEvent(new Event('timeupdate'))
    await flushPromises()
    expect(wrapper.find('.voice-time').text()).toBe('0:04')
    expect(wrapper.findAll('.voice-wave-bar.is-played').length).toBeGreaterThan(0)

    audio.dispatchEvent(new Event('waiting'))
    await flushPromises()
    expect(wrapper.find('.voice-state-dot.is-loading').exists()).toBe(true)

    audio.dispatchEvent(new Event('canplay'))
    await flushPromises()
    expect(wrapper.find('.voice-state-dot.is-loading').exists()).toBe(false)

    audio.dispatchEvent(new Event('ended'))
    await flushPromises()
    expect(chatMessageItemMocks.audioStore!.currentPlayingId).toBeNull()
    expect(wrapper.find('.voice-time').text()).toBe('0:10')

    await wrapper.find('.voice-play-btn').trigger('click')
    await flushPromises()
    const nextAudio = FakeAudio.instances.at(-1)!
    nextAudio.dispatchEvent(new Event('error'))
    await flushPromises()
    expect(wrapper.find('.msg-voice').classes()).toContain('is-error')
  })

  it('covers media fallback helpers and failed API seeding paths', async () => {
    chatMessageItemMocks.canShareFilesMock.mockReturnValue(true)
    localStorage.removeItem('auth_token')
    chatMessageItemMocks.ensureFileCachedMock.mockResolvedValueOnce(null)
    const pngWrapper = mountMediaMessage({
      content: JSON.stringify({ file_id: 'png-1', file_name: 'photo.png' }),
    })
    expect((pngWrapper.vm as any).getMediaMimeFallback()).toBe('image/png')
    expect((pngWrapper.vm as any).getMediaFileNameFallback()).toBe('photo.png')
    expect((pngWrapper.vm as any).getMediaApiUrl()).toBe('')
    expect(await (pngWrapper.vm as any).ensureMediaInFileCache()).toBe(false)
    expect(chatMessageItemMocks.ensureFileCachedMock).toHaveBeenCalledWith(
      'png-1',
      'photo.png',
      {
        mimeType: 'image/png',
        localUrl: undefined,
        fileUrl: undefined,
      },
    )

    localStorage.setItem('auth_token', 'test-token')
    const heicWrapper = mountMediaMessage({
      content: JSON.stringify({ file_id: 'heic-1', file_name: 'capture.heic' }),
    })
    expect((heicWrapper.vm as any).getMediaMimeFallback()).toBe('image/heic')

    const voiceWrapper = mountVoiceMessage({
      content: JSON.stringify({ file_id: 'voice-api' }),
    })
    expect((voiceWrapper.vm as any).getMediaMimeFallback()).toBe('audio/webm')
    expect((voiceWrapper.vm as any).getMediaFileNameFallback()).toBe('voice-voice-api.webm')

    chatMessageItemMocks.ensureFileCachedMock.mockResolvedValueOnce(null)
    expect(await (voiceWrapper.vm as any).ensureMediaInFileCache()).toBe(false)
    expect(chatMessageItemMocks.ensureFileCachedMock).toHaveBeenLastCalledWith(
      'voice-api',
      'voice-voice-api.webm',
      {
        mimeType: 'audio/webm',
        localUrl: undefined,
        fileUrl: expect.stringContaining('/api/chat/files/voice-api?token=test-token'),
      },
    )
  })
})
