import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

const shellMocks = vi.hoisted(() => ({
  on: vi.fn(),
  off: vi.fn(),
  connect: vi.fn(),
  useNotificationRuntime: vi.fn(),
  initChatUploadBackground: vi.fn(async () => {}),
  initChatDocumentDownloadBackground: vi.fn(async () => {}),
  initChatFileDebugOverlay: vi.fn(),
  setupExpiryTimer: vi.fn(),
  apiFetch: vi.fn(async () => ({})),
}))

vi.mock('../composables/useWebSocket', () => ({
  useWebSocket: () => ({ on: shellMocks.on, off: shellMocks.off, connect: shellMocks.connect }),
}))

vi.mock('../composables/useNotificationRuntime', () => ({
  useNotificationRuntime: shellMocks.useNotificationRuntime,
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
    shellMocks.useNotificationRuntime.mockReset()
    shellMocks.initChatUploadBackground.mockClear()
    shellMocks.initChatDocumentDownloadBackground.mockClear()
    shellMocks.initChatFileDebugOverlay.mockClear()
    shellMocks.setupExpiryTimer.mockClear()
    shellMocks.apiFetch.mockReset()
    shellMocks.apiFetch.mockResolvedValue({})
    localStorage.clear()
    installHandler = null
  })

  afterEach(() => {
    localStorage.clear()
    delete (window as any).deferredPrompt
  })

  it('boots background runtimes, forwards ensureSessionValidation, and handles the install prompt', async () => {
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

    expect(shellMocks.initChatUploadBackground).toHaveBeenCalled()
    expect(shellMocks.initChatDocumentDownloadBackground).toHaveBeenCalled()
    expect(shellMocks.useNotificationRuntime).toHaveBeenCalledWith({
      connect: shellMocks.connect,
      on: shellMocks.on,
      off: shellMocks.off,
      ensureSessionValidation: expect.any(Function),
    })
    expect(shellMocks.setupExpiryTimer).toHaveBeenCalledTimes(1)
    expect(shellMocks.initChatFileDebugOverlay).toHaveBeenCalledTimes(1)

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