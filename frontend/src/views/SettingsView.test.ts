import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { currentUserSummary } from '../utils/currentUser'

const settingsViewMocks = vi.hoisted(() => ({
  route: {
    name: 'settings' as string,
    query: {} as Record<string, string>,
  },
  backMock: vi.fn(),
  apiFetchMock: vi.fn(),
  forceLogoutMock: vi.fn(),
  getCacheSizeMock: vi.fn(),
  clearFileCacheMock: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => settingsViewMocks.route,
  useRouter: () => ({
    back: settingsViewMocks.backMock,
  }),
}))

vi.mock('../utils/auth', () => ({
  apiFetch: settingsViewMocks.apiFetchMock,
  forceLogout: settingsViewMocks.forceLogoutMock,
}))

vi.mock('../composables/chat/useChatFileHandler', () => ({
  useChatFileHandler: () => ({
    getCacheSize: settingsViewMocks.getCacheSizeMock,
    clearFileCache: settingsViewMocks.clearFileCacheMock,
  }),
}))

const sessionsFixture = [
  {
    id: 'session-current',
    device_name: 'Chrome',
    platform: 'Linux',
    device_ip: '10.0.0.1',
    is_primary: true,
    is_current: true,
  },
  {
    id: 'session-secondary',
    device_name: 'Android',
    platform: 'Android',
    device_ip: '10.0.0.2',
    is_primary: false,
    is_current: false,
  },
]

function responseOf(data: unknown, ok = true) {
  return {
    ok,
    json: async () => data,
  }
}

async function mountSettingsView() {
  const SettingsView = (await import('./SettingsView.vue')).default
  return mount(SettingsView)
}

describe('SettingsView.vue', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    localStorage.clear()
    currentUserSummary.value = null
    settingsViewMocks.route.name = 'settings'
    settingsViewMocks.route.query = {}
    settingsViewMocks.backMock.mockReset()
    settingsViewMocks.apiFetchMock.mockReset()
    settingsViewMocks.forceLogoutMock.mockReset()
    settingsViewMocks.getCacheSizeMock.mockReset()
    settingsViewMocks.clearFileCacheMock.mockReset()

    settingsViewMocks.getCacheSizeMock.mockResolvedValue('12.50 MB')
    settingsViewMocks.clearFileCacheMock.mockResolvedValue(undefined)

    settingsViewMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/sessions/active') return responseOf(sessionsFixture)
      if (path === '/api/sessions/session-secondary' && options?.method === 'DELETE') return responseOf({})
      if (path === '/api/sessions/logout-all' && options?.method === 'POST') return responseOf({})
      if (path === '/api/sessions/session-current' && options?.method === 'DELETE') return responseOf({})
      return responseOf({})
    })

    vi.spyOn(window, 'alert').mockImplementation(() => {})
    vi.spyOn(window, 'confirm').mockReturnValue(true)
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('loads sessions and cache size on mount, manages sessions, and handles back navigation', async () => {
    const wrapper = await mountSettingsView()
    await flushPromises()

    expect(settingsViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/sessions/active')
    expect(settingsViewMocks.getCacheSizeMock).toHaveBeenCalled()

    expect(wrapper.text()).toContain('Chrome')
    expect(wrapper.text()).toContain('Android')

    await wrapper.find('.logout-all-btn').trigger('click')
    await flushPromises()
    expect(settingsViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/sessions/logout-all', { method: 'POST' })

    await wrapper.find('.session-delete-btn').trigger('click')
    await flushPromises()
    expect(settingsViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/sessions/session-secondary', { method: 'DELETE' })
    expect(wrapper.text()).not.toContain('Android')

    await wrapper.find('.settings-back-button').trigger('click')
    expect(settingsViewMocks.backMock).toHaveBeenCalled()

    wrapper.unmount()
  }, 15000)

  it('clears cached files from the storage accordion and shows feedback', async () => {
    const wrapper = await mountSettingsView()
    await flushPromises()

    expect(wrapper.find('.storage-value').text()).toBe('12.50 MB')
    await wrapper.find('.storage-clear-btn').trigger('click')
    await flushPromises()

    expect(settingsViewMocks.clearFileCacheMock).toHaveBeenCalled()
    expect(wrapper.find('.storage-value').text()).toBe('0.00 MB')
    expect(wrapper.find('.storage-feedback').text()).toContain('حافظه با موفقیت پاک شد.')

    vi.runAllTimers()
    await flushPromises()
    expect(wrapper.find('.storage-feedback').exists()).toBe(false)

    wrapper.unmount()
  })

  it('opens account route sections from route names without legacy query handoff', async () => {
    settingsViewMocks.route.name = 'account-storage'
    const storageWrapper = await mountSettingsView()
    await flushPromises()

    expect(storageWrapper.text()).toContain('حافظه و داده‌ها')
    expect(storageWrapper.find('.settings-page').exists()).toBe(true)
    expect(storageWrapper.findAll('.settings-section-card').length).toBeGreaterThanOrEqual(2)

    settingsViewMocks.route.name = 'account-security'
    const securityWrapper = await mountSettingsView()
    await flushPromises()

    expect(securityWrapper.text()).toContain('امنیت حساب')
    expect(securityWrapper.find('.settings-page').exists()).toBe(true)
    expect(securityWrapper.findAll('.settings-section-card').length).toBeGreaterThanOrEqual(3)
  })

  it('does not render the blocked-users management section or call block APIs', async () => {
    const wrapper = await mountSettingsView()
    await flushPromises()

    expect(wrapper.text()).not.toContain('لیست مسدودشدگان')
    expect(wrapper.find('.search-input-wrapper').exists()).toBe(false)
    expect(settingsViewMocks.apiFetchMock.mock.calls.some(([path]) => String(path).startsWith('/api/blocks'))).toBe(false)

    wrapper.unmount()
  })

  it('hides session management and logout for accountant users without loading sessions', async () => {
    currentUserSummary.value = {
      id: 44,
      role: 'عادی',
      is_accountant: true,
      accountant_owner_user_id: 20,
      accountant_owner_account_name: 'owner20',
    }

    const wrapper = await mountSettingsView()
    await flushPromises()

    expect(wrapper.text()).not.toContain('نشست‌های فعال')
    expect(wrapper.text()).toContain('نشست و خروج برای حسابدار محدود است')
    expect(wrapper.find('.logout-btn').exists()).toBe(false)
    expect(settingsViewMocks.apiFetchMock.mock.calls.some(([path]) => path === '/api/sessions/active')).toBe(false)
    expect(settingsViewMocks.getCacheSizeMock).toHaveBeenCalled()

    currentUserSummary.value = null
    wrapper.unmount()
  })

  it('logs out the current session and forces a local logout', async () => {
    const wrapper = await mountSettingsView()
    await flushPromises()

    await wrapper.find('.logout-btn').trigger('click')
    await flushPromises()

    expect(settingsViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/sessions/session-current', { method: 'DELETE' })
    expect(settingsViewMocks.forceLogoutMock).toHaveBeenCalled()

    wrapper.unmount()
  })

  it('falls back when cache refresh or cache clearing fails and ignores duplicate clear requests while busy', async () => {
    let rejectClear: ((reason?: unknown) => void) | null = null
    settingsViewMocks.getCacheSizeMock.mockRejectedValueOnce(new Error('cache-size-failed'))
    settingsViewMocks.clearFileCacheMock.mockImplementationOnce(() => new Promise((_, reject) => {
      rejectClear = reject
    }))

    const wrapper = await mountSettingsView()
    await flushPromises()

    expect(wrapper.find('.storage-value').text()).toBe('0.00 MB')

    const clearButton = wrapper.find('.storage-clear-btn')
    await clearButton.trigger('click')
    await clearButton.trigger('click')
    expect(settingsViewMocks.clearFileCacheMock).toHaveBeenCalledTimes(1)

    if (!rejectClear) {
      throw new Error('Expected clear-cache rejection handler')
    }
    ;(rejectClear as (e: Error) => void)(new Error('clear-cache-failed'))
    await flushPromises()

    expect(wrapper.find('.storage-feedback').text()).toContain('پاک‌سازی حافظه ناموفق بود.')

    await vi.advanceTimersByTimeAsync(3500)
    expect(wrapper.find('.storage-feedback').exists()).toBe(false)
  })

  it('still force-logs out when no current session exists', async () => {
    settingsViewMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/sessions/active') {
        return responseOf([
          {
            id: 'session-secondary-only',
            device_name: 'Android',
            platform: 'Android',
            device_ip: '10.0.0.3',
            is_primary: false,
            is_current: false,
          },
        ])
      }
      return responseOf({})
    })

    const wrapper = await mountSettingsView()
    await flushPromises()

    await wrapper.find('.logout-btn').trigger('click')
    await flushPromises()

    expect(settingsViewMocks.apiFetchMock).not.toHaveBeenCalledWith('/api/sessions/session-current', { method: 'DELETE' })
    expect(settingsViewMocks.forceLogoutMock).toHaveBeenCalled()
  })

  it('logs session loading failures on mount without breaking the page shell', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    settingsViewMocks.apiFetchMock.mockImplementation(async (path: string) => {
      if (path === '/api/sessions/active') throw new Error('sessions-load-failed')
      return responseOf({})
    })

    const wrapper = await mountSettingsView()
    await flushPromises()

    expect(errorSpy).toHaveBeenCalledWith(expect.any(Error))
    expect(wrapper.find('.settings-page').exists()).toBe(true)
    errorSpy.mockRestore()
  })

  it('logs terminate/logout failures but still forces local logout for the current session', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    settingsViewMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/sessions/active') return responseOf(sessionsFixture)
      if (path === '/api/sessions/logout-all' && options?.method === 'POST') throw new Error('logout-all-failed')
      if (path === '/api/sessions/session-secondary' && options?.method === 'DELETE') throw new Error('terminate-failed')
      if (path === '/api/sessions/session-current' && options?.method === 'DELETE') throw new Error('logout-current-failed')
      return responseOf({})
    })

    const wrapper = await mountSettingsView()
    await flushPromises()

    await wrapper.find('.logout-all-btn').trigger('click')
    await flushPromises()

    await wrapper.find('.session-delete-btn').trigger('click')
    await flushPromises()

    await wrapper.find('.logout-btn').trigger('click')
    await flushPromises()

    expect(errorSpy).toHaveBeenCalledTimes(3)
    expect(settingsViewMocks.forceLogoutMock).toHaveBeenCalledTimes(1)
    errorSpy.mockRestore()
  })
})
