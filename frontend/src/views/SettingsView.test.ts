import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { currentUserSummary } from '../utils/currentUser'

const settingsViewMocks = vi.hoisted(() => ({
  route: {
    name: 'account-security' as string,
    query: {} as Record<string, string>,
  },
  routerPushMock: vi.fn(),
  apiFetchMock: vi.fn(),
  forceLogoutMock: vi.fn(),
  getCacheSizeMock: vi.fn(),
  clearStorageFileCacheMock: vi.fn(),
  reloadAfterStorageCacheClearMock: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => settingsViewMocks.route,
  useRouter: () => ({
    push: settingsViewMocks.routerPushMock,
  }),
}))

vi.mock('../utils/auth', () => ({
  apiFetch: settingsViewMocks.apiFetchMock,
  forceLogout: settingsViewMocks.forceLogoutMock,
}))

vi.mock('../composables/useStorageCacheMetrics', () => ({
  getStorageCacheSize: settingsViewMocks.getCacheSizeMock,
  clearStorageFileCache: settingsViewMocks.clearStorageFileCacheMock,
  reloadAfterStorageCacheClear: settingsViewMocks.reloadAfterStorageCacheClearMock,
}))

const sessionsFixture = [
  {
    id: 'session-current',
    device_name: 'Chrome',
    platform: 'Linux',
    device_ip: '10.0.0.1',
    home_server: 'foreign',
    is_primary: true,
    is_current: true,
    last_active_at: '2026-08-09T10:00:00Z',
  },
  {
    id: 'session-secondary',
    device_name: 'Android',
    platform: 'Android',
    device_ip: '10.0.0.2',
    home_server: 'iran',
    is_primary: false,
    is_current: false,
    last_active_at: '2026-08-08T12:30:00Z',
  },
]

function responseOf(data: unknown, ok = true, status = ok ? 200 : 400) {
  return {
    ok,
    status,
    json: async () => data,
  }
}

function authoritativeUser(overrides: Record<string, unknown> = {}) {
  return {
    id: 10,
    role: 'عادی',
    account_name: 'settings-user',
    account_status: 'active',
    is_accountant: false,
    is_customer: false,
    ...overrides,
  }
}

async function mountSettingsView(
  routeName: 'settings' | 'account-security' | 'account-storage' = 'account-security',
) {
  settingsViewMocks.route.name = routeName
  const SettingsView = (await import('./SettingsView.vue')).default
  const wrapper = mount(SettingsView, { attachTo: document.body })
  await flushPromises()
  return wrapper
}

function buttonWithText(wrapper: VueWrapper, text: string) {
  return wrapper.findAll('button').find((button) => button.text().includes(text))
}

function requestsFor(path: string) {
  return settingsViewMocks.apiFetchMock.mock.calls.filter(([requestPath]) => requestPath === path)
}

function bodyConfirmDialog() {
  const dialog = document.body.querySelector<HTMLElement>('.ui-confirm-dialog')
  if (!dialog) throw new Error('Expected the confirmation dialog to be teleported to document.body.')
  return dialog
}

function bodyConfirmButtons() {
  const buttons = bodyConfirmDialog().querySelectorAll<HTMLButtonElement>('button')
  if (buttons.length !== 2) {
    throw new Error('Expected exactly cancel and confirm buttons in the confirmation dialog.')
  }
  return { cancel: buttons[0]!, confirm: buttons[1]! }
}

async function confirmBodyDialog() {
  bodyConfirmButtons().confirm.click()
  await flushPromises()
}

async function cancelBodyDialog() {
  bodyConfirmButtons().cancel.click()
  await flushPromises()
}

async function pressEscape() {
  await flushPromises()
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
  await flushPromises()
}

describe('SettingsView.vue', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    currentUserSummary.value = null
    settingsViewMocks.route.name = 'account-security'
    settingsViewMocks.route.query = {}
    settingsViewMocks.routerPushMock.mockReset()
    settingsViewMocks.apiFetchMock.mockReset()
    settingsViewMocks.forceLogoutMock.mockReset()
    settingsViewMocks.getCacheSizeMock.mockReset()
    settingsViewMocks.clearStorageFileCacheMock.mockReset()
    settingsViewMocks.reloadAfterStorageCacheClearMock.mockReset()

    settingsViewMocks.getCacheSizeMock.mockResolvedValue('12.50 MB')
    settingsViewMocks.clearStorageFileCacheMock.mockResolvedValue(undefined)
    settingsViewMocks.apiFetchMock.mockImplementation(
      async (path: string, options?: RequestInit) => {
        if (path === '/api/auth/me') {
          return responseOf(currentUserSummary.value ?? authoritativeUser())
        }
        if (path === '/api/sessions/active') return responseOf(sessionsFixture)
        if (path === '/api/sessions/session-secondary' && options?.method === 'DELETE') {
          return responseOf({ detail: 'نشست با موفقیت پایان یافت' })
        }
        if (path === '/api/sessions/logout-all' && options?.method === 'POST') {
          return responseOf({ detail: '1 نشست پایان یافت' })
        }
        if (path === '/api/sessions/session-current' && options?.method === 'DELETE') {
          return responseOf({ detail: 'نشست با موفقیت پایان یافت' })
        }
        return responseOf({})
      },
    )
  })

  afterEach(() => {
    vi.restoreAllMocks()
    document.body.innerHTML = ''
    document.body.classList.remove('ui-overlay-open')
    document.documentElement.classList.remove('ui-overlay-open')
    delete document.body.dataset.uiOverlayLockCount
  })

  it('renders only Security on its canonical route and keeps the session list server-local', async () => {
    const wrapper = await mountSettingsView('account-security')

    expect(wrapper.get('.settings-security-card').text()).toContain('نشست‌های این سرور')
    expect(wrapper.find('.settings-storage-card').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('اتصال تلگرام')
    expect(settingsViewMocks.getCacheSizeMock).not.toHaveBeenCalled()
    expect(requestsFor('/api/sessions/active')).toHaveLength(1)
    expect(wrapper.text()).toContain('آخرین فعالیت:')
    expect(wrapper.text()).not.toContain('10.0.0.1')
    expect(wrapper.text()).not.toContain('foreign')
    expect(wrapper.text()).not.toContain('iran')
  }, 15_000)

  it('renders an unbroken synthetic session name without changing its contents', async () => {
    const unbrokenDeviceName = 'x'.repeat(255)
    settingsViewMocks.apiFetchMock.mockImplementation(async (path: string) => {
      if (path === '/api/auth/me') return responseOf(authoritativeUser())
      if (path === '/api/sessions/active') {
        return responseOf([{ ...sessionsFixture[0], device_name: unbrokenDeviceName }])
      }
      return responseOf({})
    })

    const wrapper = await mountSettingsView()

    expect(wrapper.findAll('.session-name')).toHaveLength(1)
    expect(wrapper.get('.session-name').text()).toBe(unbrokenDeviceName)
  })

  it('keeps the session-name wrapping contract local to Settings', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/views/SettingsView.vue'), 'utf8')
    const styleSource = source.slice(source.indexOf('<style scoped>'))

    expect(styleSource).toMatch(/\.session-name\s*\{[^}]*overflow-wrap:\s*anywhere\s*;/)
    expect(styleSource).not.toMatch(/word-break:\s*break-all\s*;/)
  })

  it('renders only Storage on its canonical route without loading sessions or Telegram', async () => {
    const wrapper = await mountSettingsView('account-storage')

    expect(wrapper.get('.settings-storage-card').text()).toContain('فایل‌های پیام‌رسان این دستگاه')
    expect(wrapper.find('.settings-security-card').exists()).toBe(false)
    expect(wrapper.find('.settings-current-logout-card').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('اتصال تلگرام')
    expect(settingsViewMocks.getCacheSizeMock).toHaveBeenCalledTimes(1)
    expect(requestsFor('/api/sessions/active')).toHaveLength(0)
  })

  it('renders overtime only on the eligible general settings route', async () => {
    currentUserSummary.value = authoritativeUser({ offer_overtime_minutes: 3 })

    const wrapper = await mountSettingsView('settings')

    expect(wrapper.get('h1').text()).toBe('تنظیمات حساب')
    expect(wrapper.get('.settings-overtime-card').text()).toContain('وقت اضافه')
    expect(wrapper.find('.settings-security-card').exists()).toBe(false)
    expect(wrapper.find('.settings-storage-card').exists()).toBe(false)
    expect(requestsFor('/api/sessions/active')).toHaveLength(0)
    expect(settingsViewMocks.getCacheSizeMock).not.toHaveBeenCalled()
  })

  it('fails closed on the general settings route for an ineligible accountant', async () => {
    currentUserSummary.value = authoritativeUser({ is_accountant: true })

    const wrapper = await mountSettingsView('settings')

    expect(wrapper.find('.settings-overtime-card').exists()).toBe(false)
    expect(wrapper.get('.settings-role-notice').text()).toContain(
      'تنظیمی برای این نوع حساب فعال نیست',
    )
    expect(requestsFor('/api/sessions/active')).toHaveLength(0)
    expect(settingsViewMocks.getCacheSizeMock).not.toHaveBeenCalled()
  })

  it('returns deterministically to Account instead of browser history', async () => {
    const wrapper = await mountSettingsView('account-storage')

    await wrapper.get('.settings-return-control').trigger('click')

    expect(settingsViewMocks.routerPushMock).toHaveBeenCalledWith({ name: 'account' })
  })

  it('shows primary and current authority exactly as reported by the server', async () => {
    const wrapper = await mountSettingsView()

    expect(wrapper.get('.session-authority-notice').text()).toContain('این دستگاه، نشست اصلی است')
    expect(wrapper.get('.session-card').text()).toContain('اصلی')
    expect(wrapper.get('.session-card').text()).toContain('این دستگاه')
    expect(wrapper.findAll('.session-delete-btn')).toHaveLength(1)
    expect(wrapper.find('.logout-all-btn').exists()).toBe(true)
  })

  it('does not offer cross-session actions when the current session is not primary', async () => {
    settingsViewMocks.apiFetchMock.mockImplementation(async (path: string) => {
      if (path === '/api/auth/me') return responseOf(authoritativeUser())
      if (path === '/api/sessions/active') {
        return responseOf([
          { ...sessionsFixture[0], is_current: false, is_primary: true },
          { ...sessionsFixture[1], is_current: true, is_primary: false },
        ])
      }
      return responseOf({})
    })

    const wrapper = await mountSettingsView()

    expect(wrapper.get('.session-authority-notice').text()).toContain('این دستگاه، نشست فرعی است')
    expect(wrapper.find('.session-delete-btn').exists()).toBe(false)
    expect(wrapper.find('.logout-all-btn').exists()).toBe(false)
    expect(wrapper.find('.logout-btn').exists()).toBe(true)
  })

  it('fails closed when the current session cannot be identified', async () => {
    settingsViewMocks.apiFetchMock.mockImplementation(async (path: string) => {
      if (path === '/api/auth/me') return responseOf(authoritativeUser())
      if (path === '/api/sessions/active') {
        return responseOf(sessionsFixture.map((session) => ({ ...session, is_current: false })))
      }
      return responseOf({})
    })

    const wrapper = await mountSettingsView()

    expect(wrapper.get('.session-authority-notice').text()).toContain('اختیار این دستگاه مشخص نیست')
    expect(wrapper.find('.session-delete-btn').exists()).toBe(false)
    expect(wrapper.find('.logout-all-btn').exists()).toBe(false)
  })

  it('requires body-teleported confirmation before ending one session and keeps the receipt local to the list', async () => {
    const wrapper = await mountSettingsView()

    await wrapper.get('.session-delete-btn').trigger('click')
    const dialog = bodyConfirmDialog()
    expect(dialog.textContent).toContain('این نشست در همین سرور پایان یابد')
    expect(dialog.textContent).not.toContain('Android')
    expect(dialog.textContent).not.toContain('Chrome')
    expect(requestsFor('/api/sessions/session-secondary')).toHaveLength(0)

    await cancelBodyDialog()
    expect(document.body.querySelector('.ui-confirm-dialog')).toBeNull()
    expect(requestsFor('/api/sessions/session-secondary')).toHaveLength(0)
    expect(wrapper.text()).toContain('Android')

    await wrapper.get('.session-delete-btn').trigger('click')
    await confirmBodyDialog()

    expect(requestsFor('/api/sessions/session-secondary')).toHaveLength(1)
    expect(wrapper.text()).not.toContain('Android')
    expect(wrapper.get('.session-mutation-feedback').text()).toContain(
      'نشست انتخاب‌شده در همین سرور پایان یافت',
    )
    expect(wrapper.text()).not.toContain('نشست با موفقیت پایان یافت')
  })

  it('keeps a session row and shows a cause-neutral local failure when termination fails', async () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    settingsViewMocks.apiFetchMock.mockImplementation(
      async (path: string, options?: RequestInit) => {
        if (path === '/api/auth/me') return responseOf(authoritativeUser())
        if (path === '/api/sessions/active') return responseOf(sessionsFixture)
        if (path === '/api/sessions/session-secondary' && options?.method === 'DELETE') {
          throw new Error('raw-sensitive-cause')
        }
        return responseOf({})
      },
    )
    const wrapper = await mountSettingsView()

    await wrapper.get('.session-delete-btn').trigger('click')
    await confirmBodyDialog()

    expect(wrapper.text()).toContain('Android')
    expect(bodyConfirmDialog().querySelector('[role="alert"]')?.textContent).toContain(
      'پایان نشست تأیید نشد',
    )
    expect(wrapper.text()).not.toContain('raw-sensitive-cause')
    expect(consoleSpy).not.toHaveBeenCalled()
  })

  it('guards duplicate session termination while its local confirm action is busy', async () => {
    let resolveTerminate: ((value: ReturnType<typeof responseOf>) => void) | null = null
    settingsViewMocks.apiFetchMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/api/auth/me') return Promise.resolve(responseOf(authoritativeUser()))
      if (path === '/api/sessions/active') return Promise.resolve(responseOf(sessionsFixture))
      if (path === '/api/sessions/session-secondary' && options?.method === 'DELETE') {
        return new Promise((resolve) => {
          resolveTerminate = resolve
        })
      }
      return Promise.resolve(responseOf({}))
    })
    const wrapper = await mountSettingsView()

    await wrapper.get('.session-delete-btn').trigger('click')
    const confirmButton = bodyConfirmButtons().confirm
    confirmButton.click()
    confirmButton.click()

    expect(requestsFor('/api/sessions/session-secondary')).toHaveLength(1)
    if (!resolveTerminate) throw new Error('Expected terminate resolver')
    ;(resolveTerminate as (value: ReturnType<typeof responseOf>) => void)(
      responseOf({ detail: 'نشست پایان یافت' }),
    )
    await flushPromises()
  })

  it('uses other-session copy, body-teleported confirmation and preserves the current session after logout-all', async () => {
    let activeCalls = 0
    settingsViewMocks.apiFetchMock.mockImplementation(
      async (path: string, options?: RequestInit) => {
        if (path === '/api/auth/me') return responseOf(authoritativeUser())
        if (path === '/api/sessions/active') {
          activeCalls += 1
          return responseOf(activeCalls === 1 ? sessionsFixture : [sessionsFixture[0]])
        }
        if (path === '/api/sessions/logout-all' && options?.method === 'POST') {
          return responseOf({ detail: '1 نشست پایان یافت' })
        }
        return responseOf({})
      },
    )
    const wrapper = await mountSettingsView()

    expect(wrapper.get('.settings-other-sessions-card').text()).toContain(
      'نشست فعلی این دستگاه را حفظ می‌کند',
    )
    await wrapper.get('.logout-all-btn').trigger('click')
    expect(bodyConfirmDialog().textContent).toContain('نشست فعلی این دستگاه باز می‌ماند')
    expect(requestsFor('/api/sessions/logout-all')).toHaveLength(0)

    await confirmBodyDialog()

    expect(requestsFor('/api/sessions/logout-all')).toHaveLength(1)
    expect(wrapper.get('.logout-others-feedback').text()).toContain('نشست فعلی این دستگاه حفظ شد')
    expect(document.activeElement).toBe(wrapper.get('.logout-others-feedback').element)
    expect(wrapper.text()).toContain('Chrome')
    expect(wrapper.text()).not.toContain('Android')
  })

  it('reconciles to the current session immediately when the post-receipt refresh fails', async () => {
    let activeCalls = 0
    settingsViewMocks.apiFetchMock.mockImplementation(
      async (path: string, options?: RequestInit) => {
        if (path === '/api/auth/me') return responseOf(authoritativeUser())
        if (path === '/api/sessions/active') {
          activeCalls += 1
          if (activeCalls === 1) return responseOf(sessionsFixture)
          throw new Error('post-receipt-refresh-failed')
        }
        if (path === '/api/sessions/logout-all' && options?.method === 'POST') {
          return responseOf({ detail: '1 نشست پایان یافت' })
        }
        return responseOf({})
      },
    )
    const wrapper = await mountSettingsView()

    await wrapper.get('.logout-all-btn').trigger('click')
    await confirmBodyDialog()

    expect(requestsFor('/api/sessions/active')).toHaveLength(2)
    expect(wrapper.get('.logout-others-feedback').text()).toContain('نشست فعلی این دستگاه حفظ شد')
    expect(wrapper.text()).toContain('Chrome')
    expect(wrapper.text()).not.toContain('Android')
    expect(wrapper.get('.sessions-refresh-error').text()).toContain('فهرست قبلی حفظ شده است')
    expect(wrapper.text()).not.toContain('post-receipt-refresh-failed')
  })

  it('keeps the prior list and local error when logout of other sessions fails', async () => {
    settingsViewMocks.apiFetchMock.mockImplementation(
      async (path: string, options?: RequestInit) => {
        if (path === '/api/auth/me') return responseOf(authoritativeUser())
        if (path === '/api/sessions/active') return responseOf(sessionsFixture)
        if (path === '/api/sessions/logout-all' && options?.method === 'POST')
          throw new Error('raw-cause')
        return responseOf({})
      },
    )
    const wrapper = await mountSettingsView()

    await wrapper.get('.logout-all-btn').trigger('click')
    await confirmBodyDialog()

    expect(bodyConfirmDialog().querySelector('[role="alert"]')?.textContent).toContain(
      'خروج از نشست‌های دیگر تأیید نشد',
    )
    expect(wrapper.text()).toContain('Chrome')
    expect(wrapper.text()).toContain('Android')
    expect(wrapper.text()).not.toContain('raw-cause')
  })

  it('rejects hostile terminate and logout-others receipts without mutating the list', async () => {
    const hostileTerminateReceipt = 'server=iran route=/api/internal/session-secondary'
    const hostileLogoutOthersReceipt = 'backend=foreign api=/api/internal/logout-all'
    settingsViewMocks.apiFetchMock.mockImplementation(
      async (path: string, options?: RequestInit) => {
        if (path === '/api/auth/me') return responseOf(authoritativeUser())
        if (path === '/api/sessions/active') return responseOf(sessionsFixture)
        if (path === '/api/sessions/logout-all' && options?.method === 'POST') {
          return responseOf({ detail: hostileLogoutOthersReceipt })
        }
        if (path === '/api/sessions/session-secondary' && options?.method === 'DELETE') {
          return responseOf({ detail: hostileTerminateReceipt })
        }
        return responseOf({})
      },
    )
    const wrapper = await mountSettingsView()

    await wrapper.get('.logout-all-btn').trigger('click')
    await confirmBodyDialog()

    expect(bodyConfirmDialog().querySelector('[role="alert"]')?.textContent).toContain(
      'خروج از نشست‌های دیگر تأیید نشد',
    )
    expect(wrapper.text()).toContain('Android')
    expect(wrapper.text()).not.toContain(hostileLogoutOthersReceipt)

    await cancelBodyDialog()
    await wrapper.get('.session-delete-btn').trigger('click')
    await confirmBodyDialog()

    expect(bodyConfirmDialog().querySelector('[role="alert"]')?.textContent).toContain(
      'پایان نشست تأیید نشد',
    )
    expect(wrapper.text()).toContain('Android')
    expect(wrapper.text()).not.toContain(hostileTerminateReceipt)
    expect(wrapper.text()).not.toContain('server=iran')
    expect(wrapper.text()).not.toContain('/api/internal')
  })

  it('confirms logout in a body-teleported dialog, ends the reported current session, then clears local auth', async () => {
    const wrapper = await mountSettingsView()

    await wrapper.get('.logout-btn').trigger('click')
    expect(bodyConfirmDialog().textContent).toContain('نشست‌های دیگر تغییر نمی‌کنند')
    expect(settingsViewMocks.forceLogoutMock).not.toHaveBeenCalled()

    await confirmBodyDialog()

    expect(requestsFor('/api/sessions/session-current')).toHaveLength(1)
    expect(settingsViewMocks.forceLogoutMock).toHaveBeenCalledTimes(1)
    expect(wrapper.get('.local-logout-feedback').text()).toContain('خروج این دستگاه ثبت شد')
    expect(sessionStorage.getItem('stage4_local_logout_result_v1')).toBe('server-confirmed')
  })

  it('clears local auth without raw logging when current-session revocation is unavailable', async () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    settingsViewMocks.apiFetchMock.mockImplementation(
      async (path: string, options?: RequestInit) => {
        if (path === '/api/auth/me') return responseOf(authoritativeUser())
        if (path === '/api/sessions/active') return responseOf(sessionsFixture)
        if (path === '/api/sessions/session-current' && options?.method === 'DELETE')
          throw new Error('raw-logout-cause')
        return responseOf({})
      },
    )
    const wrapper = await mountSettingsView()

    await wrapper.get('.logout-btn').trigger('click')
    await confirmBodyDialog()

    expect(settingsViewMocks.forceLogoutMock).toHaveBeenCalledTimes(1)
    expect(wrapper.get('.local-logout-feedback').text()).toContain(
      'اطلاعات ورود این دستگاه به‌صورت محلی پاک می‌شود',
    )
    expect(wrapper.text()).not.toContain('raw-logout-cause')
    expect(consoleSpy).not.toHaveBeenCalled()
  })

  it('does not claim server-confirmed logout for an invalid successful receipt', async () => {
    settingsViewMocks.apiFetchMock.mockImplementation(
      async (path: string, options?: RequestInit) => {
        if (path === '/api/auth/me') return responseOf(authoritativeUser())
        if (path === '/api/sessions/active') return responseOf(sessionsFixture)
        if (path === '/api/sessions/session-current' && options?.method === 'DELETE')
          return responseOf({})
        return responseOf({})
      },
    )
    const wrapper = await mountSettingsView()

    await wrapper.get('.logout-btn').trigger('click')
    await confirmBodyDialog()

    expect(settingsViewMocks.forceLogoutMock).toHaveBeenCalledTimes(1)
    expect(wrapper.get('.local-logout-feedback').text()).toContain('تأیید سرور دریافت نشد')
    expect(sessionStorage.getItem('stage4_local_logout_result_v1')).toBe('local-only')
    expect(wrapper.get('.local-logout-feedback').text()).not.toContain('خروج این دستگاه ثبت شد')
  })

  it('does not claim server-confirmed logout for a hostile detail receipt', async () => {
    settingsViewMocks.apiFetchMock.mockImplementation(
      async (path: string, options?: RequestInit) => {
        if (path === '/api/auth/me') return responseOf(authoritativeUser())
        if (path === '/api/sessions/active') return responseOf(sessionsFixture)
        if (path === '/api/sessions/session-current' && options?.method === 'DELETE') {
          return responseOf({ detail: 'token=abc.def.ghi host=iran' })
        }
        return responseOf({})
      },
    )
    const wrapper = await mountSettingsView()

    await wrapper.get('.logout-btn').trigger('click')
    await confirmBodyDialog()

    expect(settingsViewMocks.forceLogoutMock).toHaveBeenCalledTimes(1)
    expect(sessionStorage.getItem('stage4_local_logout_result_v1')).toBe('local-only')
    expect(wrapper.text()).not.toContain('token=abc.def.ghi')
  })

  it('keeps accountant Security restricted while allowing the separate Storage route', async () => {
    currentUserSummary.value = {
      ...authoritativeUser({ id: 44, is_accountant: true }),
      accountant_owner_user_id: 20,
      accountant_owner_account_name: 'owner20',
    }

    const securityWrapper = await mountSettingsView('account-security')
    expect(securityWrapper.get('.settings-role-notice').text()).toContain(
      'مدیریت نشست برای حسابدار در دسترس نیست',
    )
    expect(securityWrapper.find('.settings-security-card').exists()).toBe(false)
    expect(securityWrapper.find('.logout-btn').exists()).toBe(false)
    expect(requestsFor('/api/sessions/active')).toHaveLength(0)

    const storageWrapper = await mountSettingsView('account-storage')
    expect(storageWrapper.find('.settings-storage-card').exists()).toBe(true)
    expect(settingsViewMocks.getCacheSizeMock).toHaveBeenCalledTimes(1)
  })

  it('keeps a cache-size error distinct from zero and supports a local retry', async () => {
    settingsViewMocks.getCacheSizeMock
      .mockRejectedValueOnce(new Error('raw-size-cause'))
      .mockResolvedValueOnce('7.25 MB')

    const wrapper = await mountSettingsView('account-storage')

    expect(wrapper.get('.storage-value').text()).toBe('نامشخص')
    expect(wrapper.get('.storage-size-error').text()).toContain('به معنی صفر بودن فضا نیست')
    expect(wrapper.text()).not.toContain('0.00 MB')

    await buttonWithText(wrapper, 'محاسبه دوباره')!.trigger('click')
    await flushPromises()

    expect(wrapper.get('.storage-value').text()).toBe('7.25 MB')
    expect(wrapper.find('.storage-size-error').exists()).toBe(false)
  })

  it('confirms the truthful local clear scope, guards duplicates and reports a local result', async () => {
    let resolveClear: (() => void) | null = null
    settingsViewMocks.clearStorageFileCacheMock.mockImplementationOnce(
      () =>
        new Promise<void>((resolve) => {
          resolveClear = resolve
        }),
    )
    const wrapper = await mountSettingsView('account-storage')

    expect(wrapper.get('.settings-storage-card').text()).toContain(
      'پیام‌ها، تنظیمات حساب و فایل‌های روی سرور تغییر نمی‌کنند',
    )
    await wrapper.get('.storage-clear-btn').trigger('click')
    expect(settingsViewMocks.clearStorageFileCacheMock).not.toHaveBeenCalled()
    const dialog = bodyConfirmDialog()
    expect(dialog.contains(document.activeElement)).toBe(true)
    expect(dialog.textContent).toContain('روی همین دستگاه')
    expect(dialog.textContent).toContain('لغو یا Escape هیچ تغییری ایجاد نمی‌کند')
    expect(dialog.textContent).not.toContain('trading-bot-chat-files')
    expect(dialog.textContent).not.toContain('localforage')

    await cancelBodyDialog()
    expect(document.body.querySelector('.ui-confirm-dialog')).toBeNull()
    expect(document.activeElement).toBe(wrapper.get('.storage-clear-btn').element)
    await wrapper.get('.storage-clear-btn').trigger('click')

    const confirmButton = bodyConfirmButtons().confirm
    confirmButton.click()
    confirmButton.click()
    await flushPromises()
    expect(settingsViewMocks.clearStorageFileCacheMock).toHaveBeenCalledTimes(1)

    if (!resolveClear) throw new Error('Expected clear-cache resolver')
    ;(resolveClear as () => void)()
    await flushPromises()

    expect(document.body.querySelector('.ui-confirm-dialog')).toBeNull()
    expect(wrapper.get('.storage-value').text()).toBe('0.00 MB')
    expect(wrapper.get('.storage-feedback').text()).toContain(
      'فقط فایل‌های ذخیره‌شده پیام‌رسان روی همین دستگاه حذف شدند',
    )
    expect(settingsViewMocks.reloadAfterStorageCacheClearMock).toHaveBeenCalledTimes(1)
  })

  it('keeps Escape and cancel at zero mutation for local cache clear', async () => {
    const wrapper = await mountSettingsView('account-storage')

    await wrapper.get('.storage-clear-btn').trigger('click')
    await pressEscape()
    expect(document.body.querySelector('.ui-confirm-dialog')).toBeNull()
    expect(settingsViewMocks.clearStorageFileCacheMock).not.toHaveBeenCalled()
    expect(settingsViewMocks.reloadAfterStorageCacheClearMock).not.toHaveBeenCalled()
    expect(wrapper.get('.storage-value').text()).toBe('12.50 MB')

    await wrapper.get('.storage-clear-btn').trigger('click')
    await cancelBodyDialog()
    expect(settingsViewMocks.clearStorageFileCacheMock).not.toHaveBeenCalled()
    expect(settingsViewMocks.reloadAfterStorageCacheClearMock).not.toHaveBeenCalled()
    expect(wrapper.get('.storage-value').text()).toBe('12.50 MB')
    expect(wrapper.find('.storage-feedback').exists()).toBe(false)
  })

  it('keeps cache-clear failure cause-neutral, retains the dialog, and skips reload', async () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    settingsViewMocks.clearStorageFileCacheMock.mockRejectedValueOnce(new Error('raw-clear-cause'))
    const wrapper = await mountSettingsView('account-storage')

    await wrapper.get('.storage-clear-btn').trigger('click')
    await confirmBodyDialog()

    expect(wrapper.get('.storage-value').text()).toBe('12.50 MB')
    expect(wrapper.find('.storage-feedback').exists()).toBe(false)
    expect(bodyConfirmDialog().querySelector('[role="alert"]')?.textContent).toContain(
      'پاک‌سازی تأیید نشد',
    )
    expect(bodyConfirmDialog().textContent).not.toContain('raw-clear-cause')
    expect(wrapper.text()).not.toContain('raw-clear-cause')
    expect(settingsViewMocks.reloadAfterStorageCacheClearMock).not.toHaveBeenCalled()
    expect(consoleSpy).not.toHaveBeenCalled()
  })

  it('does not request sessions for a partial cached identity until an authoritative payload arrives', async () => {
    let identityPayload: Record<string, unknown> = {
      id: 72,
      role: 'مدیر ارشد',
      account_name: 'partial-settings-user',
    }
    currentUserSummary.value = identityPayload as { role: string }
    settingsViewMocks.apiFetchMock.mockImplementation(async (path: string) => {
      if (path === '/api/auth/me') return responseOf(identityPayload)
      if (path === '/api/sessions/active') return responseOf(sessionsFixture)
      return responseOf({})
    })

    const wrapper = await mountSettingsView()

    expect(wrapper.find('.settings-identity-error').exists()).toBe(true)
    expect(wrapper.find('.settings-security-card').exists()).toBe(false)
    expect(requestsFor('/api/sessions/active')).toHaveLength(0)

    identityPayload = authoritativeUser({ id: 72, role: 'مدیر ارشد' })
    await wrapper.get('.settings-identity-retry').trigger('click')
    await flushPromises()

    expect(requestsFor('/api/sessions/active')).toHaveLength(1)
    expect(wrapper.get('.settings-security-card').text()).toContain('نشست‌های این سرور')
    expect(wrapper.text()).toContain('Chrome')
  })

  it('marks authoritative cached access as stale while the initial refresh is pending', async () => {
    let resolveIdentity: ((response: ReturnType<typeof responseOf>) => void) | null = null
    currentUserSummary.value = authoritativeUser()
    settingsViewMocks.apiFetchMock.mockImplementation((path: string) => {
      if (path === '/api/auth/me') {
        return new Promise((resolve) => {
          resolveIdentity = resolve
        })
      }
      if (path === '/api/sessions/active') return Promise.resolve(responseOf(sessionsFixture))
      return Promise.resolve(responseOf({}))
    })

    const wrapper = await mountSettingsView()

    expect(wrapper.get('.settings-identity-stale').text()).toContain('دسترسی‌های ذخیره‌شده قبلی')
    expect(wrapper.find('.settings-security-card').exists()).toBe(true)
    expect(requestsFor('/api/sessions/active')).toHaveLength(0)

    if (!resolveIdentity) throw new Error('Expected identity resolver')
    ;(resolveIdentity as (response: ReturnType<typeof responseOf>) => void)(
      responseOf(authoritativeUser()),
    )
    await flushPromises()

    expect(requestsFor('/api/sessions/active')).toHaveLength(1)
    expect(wrapper.find('.settings-identity-stale').exists()).toBe(false)
  })

  it('does not request or expose section data before current-user authority resolves', async () => {
    let resolveIdentity: ((response: ReturnType<typeof responseOf>) => void) | null = null
    settingsViewMocks.apiFetchMock.mockImplementation((path: string) => {
      if (path === '/api/auth/me') {
        return new Promise((resolve) => {
          resolveIdentity = resolve
        })
      }
      if (path === '/api/sessions/active') return Promise.resolve(responseOf(sessionsFixture))
      return Promise.resolve(responseOf({}))
    })

    const wrapper = await mountSettingsView()
    await Promise.resolve()
    await Promise.resolve()

    expect(wrapper.find('.settings-identity-loading').exists()).toBe(true)
    expect(wrapper.find('.settings-security-card').exists()).toBe(false)
    expect(requestsFor('/api/sessions/active')).toHaveLength(0)

    if (!resolveIdentity) throw new Error('Expected identity resolver')
    ;(resolveIdentity as (response: ReturnType<typeof responseOf>) => void)(
      responseOf(authoritativeUser()),
    )
    await flushPromises()

    expect(requestsFor('/api/sessions/active')).toHaveLength(1)
    expect(wrapper.text()).toContain('Chrome')
  })

  it('does not apply a successful status without an authoritative mutation receipt', async () => {
    settingsViewMocks.apiFetchMock.mockImplementation(
      async (path: string, options?: RequestInit) => {
        if (path === '/api/auth/me') return responseOf(authoritativeUser())
        if (path === '/api/sessions/active') return responseOf(sessionsFixture)
        if (path === '/api/sessions/session-secondary' && options?.method === 'DELETE')
          return responseOf({})
        return responseOf({})
      },
    )
    const wrapper = await mountSettingsView()

    await wrapper.get('.session-delete-btn').trigger('click')
    await confirmBodyDialog()

    expect(wrapper.text()).toContain('Android')
    expect(bodyConfirmDialog().querySelector('[role="alert"]')?.textContent).toContain(
      'پایان نشست تأیید نشد',
    )
  })

  it('keeps Escape and cancel at zero mutation for session termination', async () => {
    const wrapper = await mountSettingsView()

    await wrapper.get('.session-delete-btn').trigger('click')
    await pressEscape()
    expect(document.body.querySelector('.ui-confirm-dialog')).toBeNull()
    expect(requestsFor('/api/sessions/session-secondary')).toHaveLength(0)
    expect(wrapper.text()).toContain('Android')

    await wrapper.get('.session-delete-btn').trigger('click')
    await cancelBodyDialog()
    expect(requestsFor('/api/sessions/session-secondary')).toHaveLength(0)
    expect(wrapper.text()).toContain('Android')
  })

  it('rejects 403/404 terminate failures with fixed copy and retains the selected session', async () => {
    settingsViewMocks.apiFetchMock.mockImplementation(
      async (path: string, options?: RequestInit) => {
        if (path === '/api/auth/me') return responseOf(authoritativeUser())
        if (path === '/api/sessions/active') return responseOf(sessionsFixture)
        if (path === '/api/sessions/session-secondary' && options?.method === 'DELETE') {
          return responseOf({ detail: 'raw-forbidden-session' }, false, 403)
        }
        return responseOf({})
      },
    )
    const wrapper = await mountSettingsView()

    await wrapper.get('.session-delete-btn').trigger('click')
    await confirmBodyDialog()

    expect(wrapper.text()).toContain('Android')
    expect(bodyConfirmDialog().querySelector('[role="alert"]')?.textContent).toContain(
      'اجازه این اقدام را ندارید',
    )
    expect(wrapper.text()).not.toContain('raw-forbidden-session')
  })
})
