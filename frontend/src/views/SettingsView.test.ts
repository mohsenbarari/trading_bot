import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const settingsViewMocks = vi.hoisted(() => ({
  backMock: vi.fn(),
  apiFetchMock: vi.fn(),
  forceLogoutMock: vi.fn(),
  getCacheSizeMock: vi.fn(),
  clearFileCacheMock: vi.fn(),
}))

vi.mock('vue-router', () => ({
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

const blockedUsersFixture = [
  {
    id: 88,
    full_name: 'Blocked User',
    account_name: 'blocked-user',
    mobile_number: '09120000000',
  },
]

const searchResultsFixture = [
  {
    id: 99,
    full_name: 'Search User',
    account_name: 'search-user',
    mobile_number: '09125550000',
    is_blocked: false,
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
      if (path === '/api/blocks/') return responseOf(blockedUsersFixture)
      if (path.startsWith('/api/blocks/search?')) return responseOf(searchResultsFixture)
      if (path === '/api/blocks/99' && options?.method === 'POST') return responseOf({})
      if (path === '/api/blocks/88' && options?.method === 'DELETE') return responseOf({})
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

    const accordions = wrapper.findAll('.ds-accordion-header')
    await accordions[0]!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Chrome')
    expect(wrapper.text()).toContain('Android')

    await wrapper.find('.logout-all-btn').trigger('click')
    await flushPromises()
    expect(settingsViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/sessions/logout-all', { method: 'POST' })

    await wrapper.find('.session-delete-btn').trigger('click')
    await flushPromises()
    expect(settingsViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/sessions/session-secondary', { method: 'DELETE' })
    expect(wrapper.text()).not.toContain('Android')

    await wrapper.find('.back-button').trigger('click')
    expect(settingsViewMocks.backMock).toHaveBeenCalled()

    wrapper.unmount()
  })

  it('clears cached files from the storage accordion and shows feedback', async () => {
    const wrapper = await mountSettingsView()
    await flushPromises()

    const accordions = wrapper.findAll('.ds-accordion-header')
    await accordions[1]!.trigger('click')
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

  it('loads blocked users, searches users, blocks, and unblocks through the blocks accordion', async () => {
    const wrapper = await mountSettingsView()
    await flushPromises()

    const accordions = wrapper.findAll('.ds-accordion-header')
    await accordions[2]!.trigger('click')
    await flushPromises()

    expect(settingsViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/blocks/')
    expect(wrapper.text()).toContain('Blocked User')

    const searchInput = wrapper.find('.search-input-wrapper input')
    await searchInput.setValue('se')
    await flushPromises()

    expect(settingsViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/blocks/search?q=se&limit=5')
    expect(wrapper.text()).toContain('Search User')

    await wrapper.find('.btn-block').trigger('click')
    await flushPromises()

    expect(settingsViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/blocks/99', { method: 'POST' })
    expect(window.alert).toHaveBeenCalledWith('کاربر با موفقیت مسدود شد.')

    await wrapper.find('.btn-unblock').trigger('click')
    await flushPromises()

    expect(window.confirm).toHaveBeenCalled()
    expect(settingsViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/blocks/88', { method: 'DELETE' })

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

    const accordions = wrapper.findAll('.ds-accordion-header')
    await accordions[1]!.trigger('click')
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

  it('handles short block searches, renders already-blocked results, and surfaces block API failures', async () => {
    settingsViewMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/sessions/active') return responseOf(sessionsFixture)
      if (path === '/api/blocks/') return responseOf(blockedUsersFixture)
      if (path === '/api/blocks/search?q=ab&limit=5') {
        return responseOf([
          {
            id: 77,
            full_name: 'Already Blocked',
            account_name: 'already-blocked',
            mobile_number: '09121111111',
            is_blocked: true,
          },
          {
            id: 88,
            full_name: 'Blocked User',
            account_name: 'blocked-user',
            mobile_number: '09120000000',
            is_blocked: true,
          },
        ])
      }
      if (path === '/api/blocks/search?q=xy&limit=5') return responseOf(searchResultsFixture)
      if (path === '/api/blocks/99' && options?.method === 'POST') {
        return responseOf({ detail: 'limit reached' }, false)
      }
      return responseOf({})
    })

    const wrapper = await mountSettingsView()
    await flushPromises()

    const accordions = wrapper.findAll('.ds-accordion-header')
    await accordions[2]!.trigger('click')
    await flushPromises()

    const searchInput = wrapper.find('.search-input-wrapper input')
    await searchInput.setValue('a')
    await flushPromises()
    expect(wrapper.find('.search-results').exists()).toBe(false)

    await searchInput.setValue('ab')
    await flushPromises()
    expect(wrapper.find('.already-blocked').text()).toBe('مسدود شده')

    await searchInput.setValue('xy')
    await flushPromises()
    await wrapper.find('.btn-block').trigger('click')
    await flushPromises()

    expect(window.alert).toHaveBeenCalledWith('limit reached')
  })

  it('surfaces unblock network failures and still force-logs out when no current session exists', async () => {
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
      if (path === '/api/blocks/') return responseOf(blockedUsersFixture)
      if (path === '/api/blocks/88' && options?.method === 'DELETE') {
        throw new Error('network failed')
      }
      return responseOf({})
    })

    const wrapper = await mountSettingsView()
    await flushPromises()

    const accordions = wrapper.findAll('.ds-accordion-header')
    await accordions[2]!.trigger('click')
    await flushPromises()

    await wrapper.find('.btn-unblock').trigger('click')
    await flushPromises()
    expect(window.alert).toHaveBeenCalledWith('خطا در برقراری ارتباط')

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

    const accordions = wrapper.findAll('.ds-accordion-header')
    await accordions[0]!.trigger('click')
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