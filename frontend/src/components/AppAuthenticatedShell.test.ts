import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

const shellMocks = vi.hoisted(() => ({
  on: vi.fn(),
  off: vi.fn(),
  connect: vi.fn(),
  sendPresenceUpdate: vi.fn(),
  useNotificationRuntime: vi.fn(),
  initChatUploadBackground: vi.fn(async () => {}),
  hasPendingUploadResumeHint: vi.fn(() => false),
  initChatDocumentDownloadBackground: vi.fn(async () => {}),
  hasPendingDocumentDownloadResumeHint: vi.fn(() => false),
  initChatFileDebugOverlay: vi.fn(),
  setupExpiryTimer: vi.fn(),
  apiFetch: vi.fn(async () => ({})),
  route: { path: '/' },
}))

vi.mock('../composables/useWebSocket', () => ({
  useWebSocket: () => ({
    on: shellMocks.on,
    off: shellMocks.off,
    connect: shellMocks.connect,
    sendPresenceUpdate: shellMocks.sendPresenceUpdate,
  }),
}))

vi.mock('vue-router', () => ({
  useRoute: () => shellMocks.route,
}))

vi.mock('../composables/useNotificationRuntime', () => ({
  useNotificationRuntime: shellMocks.useNotificationRuntime,
}))

vi.mock('../services/chatTransferResumeHints', () => ({
  hasPendingDocumentDownloadResumeHint: shellMocks.hasPendingDocumentDownloadResumeHint,
  hasPendingUploadResumeHint: shellMocks.hasPendingUploadResumeHint,
}))

vi.mock('../services/chatUploadBackground', () => ({
  initChatUploadBackground: shellMocks.initChatUploadBackground,
}))

vi.mock('../services/chatDocumentDownloadBackground', () => ({
  initChatDocumentDownloadBackground: shellMocks.initChatDocumentDownloadBackground,
}))

vi.mock('../composables/chat/useChatFileHandler', () => ({
  initChatFileDebugOverlay: shellMocks.initChatFileDebugOverlay,
}))

vi.mock('../utils/auth', () => ({
  setupExpiryTimer: shellMocks.setupExpiryTimer,
  apiFetch: shellMocks.apiFetch,
}))

describe('AppAuthenticatedShell.vue', () => {
  let installHandler: EventListener | null

  beforeEach(() => {
    shellMocks.on.mockReset()
    shellMocks.off.mockReset()
    shellMocks.connect.mockReset()
    shellMocks.sendPresenceUpdate.mockReset()
    shellMocks.useNotificationRuntime.mockReset()
    shellMocks.initChatUploadBackground.mockClear()
    shellMocks.hasPendingUploadResumeHint.mockReset()
    shellMocks.hasPendingUploadResumeHint.mockReturnValue(false)
    shellMocks.initChatDocumentDownloadBackground.mockClear()
    shellMocks.hasPendingDocumentDownloadResumeHint.mockReset()
    shellMocks.hasPendingDocumentDownloadResumeHint.mockReturnValue(false)
    shellMocks.initChatFileDebugOverlay.mockClear()
    shellMocks.setupExpiryTimer.mockClear()
    shellMocks.apiFetch.mockReset()
    shellMocks.apiFetch.mockResolvedValue({})
    shellMocks.route.path = '/'
    Object.defineProperty(document, 'hidden', { configurable: true, value: false })
    localStorage.clear()
    installHandler = null
  })

  afterEach(() => {
    localStorage.clear()
    delete (window as any).deferredPrompt
  })

  it('skips eager background recovery when no pending transfer hint exists, forwards ensureSessionValidation, and handles the install prompt', async () => {
    localStorage.setItem('auth_token', 'jwt')
    localStorage.setItem('refresh_token', 'refresh-token')
    shellMocks.useNotificationRuntime.mockImplementation(({ ensureSessionValidation }) => {
      ;(shellMocks.useNotificationRuntime as any).capturedEnsure = ensureSessionValidation
    })

    const AppAuthenticatedShell = (await import('./AppAuthenticatedShell.vue')).default
    const wrapper = mount(AppAuthenticatedShell, {
      global: {
        stubs: {
          BottomNav: true,
          SessionApprovalModal: true,
          AppToasts: true,
        },
      },
    })

    expect(shellMocks.initChatUploadBackground).not.toHaveBeenCalled()
    expect(shellMocks.initChatDocumentDownloadBackground).not.toHaveBeenCalled()
    expect(shellMocks.useNotificationRuntime).toHaveBeenCalledWith({
      connect: shellMocks.connect,
      on: shellMocks.on,
      off: shellMocks.off,
      ensureSessionValidation: expect.any(Function),
    })
    expect(shellMocks.setupExpiryTimer).toHaveBeenCalledTimes(1)
    expect(shellMocks.initChatFileDebugOverlay).toHaveBeenCalledTimes(1)
    expect(shellMocks.on).toHaveBeenCalledWith('ws:reconnect', expect.any(Function))
    expect(shellMocks.sendPresenceUpdate).toHaveBeenCalledWith('/', true)

    const ensureSessionValidation = (shellMocks.useNotificationRuntime as any).capturedEnsure
    await ensureSessionValidation()
    expect(shellMocks.apiFetch).toHaveBeenCalledWith('/api/sessions/verify', {
      method: 'POST',
      body: JSON.stringify({ refresh_token: 'refresh-token' }),
    })

    const readyListener = vi.fn()
    window.addEventListener('pwa-install-ready', readyListener, { once: true })
    const preventDefault = vi.fn()
    const event = new Event('beforeinstallprompt')
    ;(event as any).preventDefault = preventDefault
    installHandler = wrapper.vm.$.appContext.app._instance?.vnode.el ? null : null
    window.dispatchEvent(event)

    expect(preventDefault).toHaveBeenCalled()
    expect((window as any).deferredPrompt).toBe(event)
    expect(readyListener).toHaveBeenCalledTimes(1)
    wrapper.unmount()
    expect(shellMocks.off).toHaveBeenCalledWith('ws:reconnect', expect.any(Function))
    expect(shellMocks.sendPresenceUpdate).toHaveBeenLastCalledWith('/', false)
  })

  it('starts background recovery immediately when a pending transfer hint exists', async () => {
    shellMocks.hasPendingUploadResumeHint.mockReturnValue(true)
    shellMocks.hasPendingDocumentDownloadResumeHint.mockReturnValue(true)

    const AppAuthenticatedShell = (await import('./AppAuthenticatedShell.vue')).default
    mount(AppAuthenticatedShell, {
      global: {
        stubs: {
          BottomNav: true,
          SessionApprovalModal: true,
          AppToasts: true,
        },
      },
    })

    await vi.waitFor(() => {
      expect(shellMocks.initChatUploadBackground).toHaveBeenCalledTimes(1)
      expect(shellMocks.initChatDocumentDownloadBackground).toHaveBeenCalledTimes(1)
    })
  })

  it('skips session verification without a refresh token and swallows verification failures', async () => {
    shellMocks.useNotificationRuntime.mockImplementation(({ ensureSessionValidation }) => {
      ;(shellMocks.useNotificationRuntime as any).capturedEnsure = ensureSessionValidation
    })
    shellMocks.apiFetch.mockRejectedValueOnce(new Error('unauthorized'))

    const AppAuthenticatedShell = (await import('./AppAuthenticatedShell.vue')).default
    mount(AppAuthenticatedShell, {
      global: {
        stubs: {
          BottomNav: true,
          SessionApprovalModal: true,
          AppToasts: true,
        },
      },
    })

    const ensureSessionValidation = (shellMocks.useNotificationRuntime as any).capturedEnsure
    await ensureSessionValidation()
    expect(shellMocks.apiFetch).not.toHaveBeenCalled()

    localStorage.setItem('refresh_token', 'refresh-token')
    await expect(ensureSessionValidation()).resolves.toBeUndefined()
  })
})
