import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ProfileView from './ProfileView.vue'

const {
  apiFetchMock,
  forceLogoutMock,
  getCacheSizeMock,
  clearFileCacheMock,
} = vi.hoisted(() => ({
  apiFetchMock: vi.fn(),
  forceLogoutMock: vi.fn(),
  getCacheSizeMock: vi.fn(),
  clearFileCacheMock: vi.fn(),
}))

vi.mock('../utils/auth', () => ({
  apiFetch: apiFetchMock,
  forceLogout: forceLogoutMock,
}))

vi.mock('../composables/chat/useChatFileHandler', () => ({
  useChatFileHandler: () => ({
    getCacheSize: getCacheSizeMock,
    clearFileCache: clearFileCacheMock,
  }),
}))

function makeResponse(payload: unknown, ok = true) {
  return {
    ok,
    json: async () => payload,
  }
}

describe('ProfileView.vue', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    forceLogoutMock.mockReset()
    getCacheSizeMock.mockReset()
    clearFileCacheMock.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('loads user, sessions, and cache size on mount and removes a terminated secondary session', async () => {
    apiFetchMock.mockImplementation((url: string) => {
      if (url === '/api/auth/me') {
        return Promise.resolve(makeResponse({
          full_name: 'علی رضایی',
          account_name: 'ali',
          mobile_number: '09123456789',
          role: 'user',
          created_at: '2026-05-01T00:00:00.000Z',
        }))
      }
      if (url === '/api/sessions/active') {
        return Promise.resolve(makeResponse([
          { id: 'primary', device_name: 'iPhone', platform: 'iOS', device_ip: '1.1.1.1', is_current: true, is_primary: true },
          { id: 'secondary', device_name: 'Chrome', platform: 'Android', device_ip: '2.2.2.2', is_current: false, is_primary: false },
        ]))
      }
      if (url === '/api/sessions/secondary') {
        return Promise.resolve(makeResponse({}, true))
      }
      return Promise.resolve(makeResponse({}, true))
    })
    getCacheSizeMock.mockResolvedValue('12.50 MB')

    const wrapper = mount(ProfileView)
    await flushPromises()

    expect(wrapper.text()).toContain('علی رضایی')
    expect(wrapper.text()).toContain('09123456789')
    expect(wrapper.text()).toContain('12.50 MB')
    expect(wrapper.text()).toContain('Chrome')

    apiFetchMock.mockResolvedValueOnce(makeResponse({}, true))
    await wrapper.get('button[title="پایان نشست"]').trigger('click')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenCalledWith('/api/sessions/secondary', { method: 'DELETE' })
    expect(wrapper.text()).not.toContain('Chrome')
  })

  it('clears cached files and logs out the current session', async () => {
    vi.useFakeTimers()
    apiFetchMock.mockImplementation((url: string) => {
      if (url === '/api/auth/me') {
        return Promise.resolve(makeResponse({
          full_name: 'مینا',
          account_name: 'mina',
          mobile_number: '09999999999',
          role: 'user',
          created_at: '2026-05-01T00:00:00.000Z',
        }))
      }
      if (url === '/api/sessions/active') {
        return Promise.resolve(makeResponse([
          { id: 'current', device_name: 'Pixel', platform: 'Android', device_ip: '3.3.3.3', is_current: true, is_primary: true },
        ]))
      }
      return Promise.resolve(makeResponse({}, true))
    })
    getCacheSizeMock.mockResolvedValue('4.00 MB')
    clearFileCacheMock.mockResolvedValue(undefined)

    const wrapper = mount(ProfileView)
    await flushPromises()

    await wrapper.get('.storage-clear-btn').trigger('click')
    await flushPromises()
    expect(clearFileCacheMock).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('حافظه با موفقیت پاک شد.')
    expect(wrapper.text()).toContain('0.00 MB')

    apiFetchMock.mockClear()
    await wrapper.get('.logout-btn').trigger('click')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenCalledWith('/api/sessions/current', { method: 'DELETE' })
    expect(forceLogoutMock).toHaveBeenCalledTimes(1)

    await vi.runAllTimersAsync()
  })
})