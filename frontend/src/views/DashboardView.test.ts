import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import DashboardView from './DashboardView.vue'

const dashboardViewMocks = vi.hoisted(() => ({
  routerPushMock: vi.fn(),
  apiFetchMock: vi.fn(),
  forceLogoutMock: vi.fn(),
  locationAssignMock: vi.fn(),
  notificationStore: {
    appNotifications: [] as Array<Record<string, unknown>>,
  },
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: dashboardViewMocks.routerPushMock,
  }),
}))

vi.mock('../stores/notifications', () => ({
  useNotificationStore: () => dashboardViewMocks.notificationStore,
}))

vi.mock('../utils/auth', () => ({
  apiFetch: dashboardViewMocks.apiFetchMock,
  forceLogout: dashboardViewMocks.forceLogoutMock,
}))

function makeJsonResponse(payload: unknown, ok = true) {
  return {
    ok,
    json: async () => payload,
  }
}

function makeToken(payload: Record<string, unknown>) {
  return `header.${window.btoa(JSON.stringify(payload))}.signature`
}

async function mountView() {
  const wrapper = mount(DashboardView)
  await flushPromises()
  return wrapper
}

describe('DashboardView.vue', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 4, 14, 5, 0, 0))
    dashboardViewMocks.routerPushMock.mockReset()
    dashboardViewMocks.apiFetchMock.mockReset()
    dashboardViewMocks.forceLogoutMock.mockReset()
    dashboardViewMocks.locationAssignMock.mockReset()
    dashboardViewMocks.notificationStore.appNotifications = []
    localStorage.clear()
    vi.stubGlobal('location', {
      ...window.location,
      assign: dashboardViewMocks.locationAssignMock,
    })
  })

  it('loads the current user, shows the unread notification dot, and routes the top-bar actions', async () => {
    dashboardViewMocks.notificationStore.appNotifications = [{ id: 1 }]
    dashboardViewMocks.apiFetchMock.mockResolvedValue(
      makeJsonResponse({
        id: 12,
        full_name: 'رضا محمدی',
        account_name: 'reza12',
        account_status: 'active',
        global_lock_grace_expires_at: null,
        global_web_locked_at: null,
        trading_restricted_until: null,
      }),
    )

    const wrapper = await mountView()

    expect(dashboardViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/auth/me')
    expect(wrapper.text()).toContain('صبح بخیر')
    expect(wrapper.text()).toContain('رضا محمدی')
    expect(wrapper.get('.avatar').text()).toContain('ر')
    expect(wrapper.find('.notif-dot').exists()).toBe(true)

    await wrapper.get('.notif-btn').trigger('click')
    await wrapper.get('.user-info-center').trigger('click')
    await wrapper.get('.hero-btn').trigger('click')

    expect(dashboardViewMocks.routerPushMock).toHaveBeenNthCalledWith(1, '/notifications')
    expect(dashboardViewMocks.routerPushMock).toHaveBeenNthCalledWith(2, '/profile')
    expect(dashboardViewMocks.routerPushMock).toHaveBeenNthCalledWith(3, '/market')
  })

  it('shows the inactive warning and blocks market navigation for inactive accounts', async () => {
    dashboardViewMocks.apiFetchMock.mockResolvedValue(
      makeJsonResponse({
        id: 13,
        full_name: 'کاربر مسدود',
        account_name: 'blocked13',
        account_status: 'inactive',
        global_lock_grace_expires_at: '2026-05-20T12:00:00Z',
        global_web_locked_at: null,
        trading_restricted_until: '2026-05-20T12:00:00Z',
      }),
    )

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('حساب کاربری غیرفعال شده است')
    expect(wrapper.text()).toContain('اگر حساب تا')
    expect(wrapper.text()).not.toContain('معاملات محدود شده')

    await wrapper.get('.hero-btn').trigger('click')
    expect(dashboardViewMocks.routerPushMock).not.toHaveBeenCalled()
  })

  it('hides the market entry button for accountants', async () => {
    dashboardViewMocks.apiFetchMock.mockResolvedValue(
      makeJsonResponse({
        id: 18,
        full_name: 'حسابدار وب',
        account_name: 'accountant18',
        account_status: 'active',
        is_accountant: true,
        global_lock_grace_expires_at: null,
        global_web_locked_at: null,
        trading_restricted_until: null,
      }),
    )

    const wrapper = await mountView()

    expect(wrapper.find('.hero-btn').exists()).toBe(false)
    expect(wrapper.find('.logout-btn').exists()).toBe(false)
    expect(dashboardViewMocks.routerPushMock).not.toHaveBeenCalledWith('/market')
  })

  it('shows the stronger lock copy when the account is already globally locked', async () => {
    dashboardViewMocks.apiFetchMock.mockResolvedValue(
      makeJsonResponse({
        id: 16,
        full_name: 'کاربر قفل‌شده',
        account_name: 'locked16',
        account_status: 'inactive',
        global_lock_grace_expires_at: '2026-05-16T12:00:00Z',
        global_web_locked_at: '2026-05-17T12:00:00Z',
        trading_restricted_until: null,
      }),
    )

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('حساب کاربری قفل شده است')
    expect(wrapper.text()).toContain('نشست‌های وب و پیام‌رسان این حساب')

    await wrapper.get('.hero-btn').trigger('click')
    expect(dashboardViewMocks.routerPushMock).not.toHaveBeenCalled()
  })

  it('shows the restricted trading warning with a formatted deadline when the user is temporarily restricted', async () => {
    dashboardViewMocks.apiFetchMock.mockResolvedValue(
      makeJsonResponse({
        id: 14,
        full_name: 'کاربر محدود',
        account_name: 'limited14',
        account_status: 'active',
        global_lock_grace_expires_at: null,
        global_web_locked_at: null,
        trading_restricted_until: '2026-05-20T12:00:00Z',
      }),
    )

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('معاملات محدود شده')
    expect(wrapper.text()).toContain('محدود شده است')
  })

  it('logs out by terminating the current session before forcing a local logout', async () => {
    dashboardViewMocks.apiFetchMock
      .mockResolvedValueOnce(makeJsonResponse({
        id: 15,
        full_name: 'کاربر خروج',
        account_name: 'logout15',
        account_status: 'active',
        global_lock_grace_expires_at: null,
        global_web_locked_at: null,
        trading_restricted_until: null,
      }))
      .mockResolvedValueOnce(makeJsonResponse([
        { id: 'session-a', is_current: false },
        { id: 'session-b', is_current: true },
      ]))
      .mockResolvedValueOnce(makeJsonResponse({ ok: true }))

    const wrapper = await mountView()
    await wrapper.get('.logout-btn').trigger('click')
    await flushPromises()

    expect(dashboardViewMocks.apiFetchMock).toHaveBeenNthCalledWith(2, '/api/sessions/active')
    expect(dashboardViewMocks.apiFetchMock).toHaveBeenNthCalledWith(3, '/api/sessions/session-b', { method: 'DELETE' })
    expect(dashboardViewMocks.forceLogoutMock).toHaveBeenCalledTimes(1)
  })

  it('renders the midday and evening greetings for later hours', async () => {
    const payload = {
      id: 21,
      full_name: 'کاربر زمان‌بندی',
      account_name: 'timed21',
      account_status: 'active',
      global_lock_grace_expires_at: null,
      global_web_locked_at: null,
      trading_restricted_until: null,
    }

    vi.setSystemTime(new Date(2026, 4, 14, 13, 0, 0))
    dashboardViewMocks.apiFetchMock.mockResolvedValueOnce(makeJsonResponse(payload))
    const middayWrapper = await mountView()
    expect(middayWrapper.text()).toContain('ظهر بخیر')
    middayWrapper.unmount()

    vi.setSystemTime(new Date(2026, 4, 14, 18, 0, 0))
    dashboardViewMocks.apiFetchMock.mockResolvedValueOnce(makeJsonResponse(payload))
    const eveningWrapper = await mountView()
    expect(eveningWrapper.text()).toContain('عصر بخیر')
    eveningWrapper.unmount()
  })

  it('shows the temporary account switcher for super admins and swaps tokens on selection', async () => {
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'مدیر ارشد' }))
    dashboardViewMocks.apiFetchMock
      .mockResolvedValueOnce(makeJsonResponse({
        id: 50,
        full_name: 'مدیر تست',
        account_name: 'root50',
        role: 'مدیر ارشد',
        account_status: 'active',
        global_lock_grace_expires_at: null,
        global_web_locked_at: null,
        trading_restricted_until: null,
      }))
      .mockResolvedValueOnce(makeJsonResponse([
        {
          id: 61,
          full_name: 'حسابدار تست',
          account_name: 'accountant61',
          mobile_number: '09120000061',
          role: 'عادی',
          is_accountant: true,
          is_customer: false,
          customer_tier: null,
        },
      ]))
      .mockResolvedValueOnce(makeJsonResponse({
        access_token: 'switched-access',
        refresh_token: 'switched-refresh',
        token_type: 'bearer',
      }))

    const wrapper = await mountView()
    await wrapper.get('.switcher-entry-btn').trigger('click')
    await flushPromises()

    expect(dashboardViewMocks.apiFetchMock).toHaveBeenNthCalledWith(2, '/api/auth/dev-switch/users')
    expect(wrapper.text()).toContain('سوییچ موقت حساب')
    expect(wrapper.text()).toContain('حسابدار تست')

    await wrapper.get('.switcher-user-row').trigger('click')
    await flushPromises()

    expect(dashboardViewMocks.apiFetchMock).toHaveBeenNthCalledWith(3, '/api/auth/dev-switch/61', { method: 'POST' })
    expect(localStorage.getItem('auth_token')).toBe('switched-access')
    expect(localStorage.getItem('refresh_token')).toBe('switched-refresh')
    expect(localStorage.getItem('current_user_summary')).toBeNull()
    expect(dashboardViewMocks.locationAssignMock).toHaveBeenCalledWith('/')
  })

  it('keeps the temporary account switcher visible for switched non-admin sessions via token claim', async () => {
    localStorage.setItem('auth_token', makeToken({ dev_account_switch: true }))
    dashboardViewMocks.apiFetchMock.mockResolvedValueOnce(makeJsonResponse({
      id: 71,
      full_name: 'مشتری تست',
      account_name: 'customer71',
      role: 'عادی',
      account_status: 'active',
      global_lock_grace_expires_at: null,
      global_web_locked_at: null,
      trading_restricted_until: null,
    }))

    const wrapper = await mountView()

    expect(wrapper.find('.switcher-entry-btn').exists()).toBe(true)
    expect(wrapper.text()).toContain('سوییچ موقت حساب')
  })

  it('hides the temporary account switcher when a non-admin session has an invalid token claim payload', async () => {
    localStorage.setItem('auth_token', 'broken.token.payload')
    dashboardViewMocks.apiFetchMock.mockResolvedValueOnce(makeJsonResponse({
      id: 72,
      full_name: 'کاربر عادی',
      account_name: 'user72',
      role: 'عادی',
      account_status: 'active',
      global_lock_grace_expires_at: null,
      global_web_locked_at: null,
      trading_restricted_until: null,
    }))

    const wrapper = await mountView()

    expect(wrapper.find('.switcher-entry-btn').exists()).toBe(false)
  })

  it('debounces account-switch search requests, shows the empty state, and clears the modal on close', async () => {
    dashboardViewMocks.apiFetchMock
      .mockResolvedValueOnce(makeJsonResponse({
        id: 80,
        full_name: 'مدیر جستجو',
        account_name: 'root80',
        role: 'مدیر ارشد',
        account_status: 'active',
        global_lock_grace_expires_at: null,
        global_web_locked_at: null,
        trading_restricted_until: null,
      }))
      .mockResolvedValueOnce(makeJsonResponse([
        {
          id: 81,
          full_name: 'کاربر اول',
          account_name: 'user81',
          mobile_number: '09120000081',
          role: 'عادی',
          is_accountant: false,
          is_customer: false,
          customer_tier: null,
        },
      ]))
      .mockResolvedValueOnce(makeJsonResponse([]))

    const wrapper = await mountView()
    await wrapper.get('.switcher-entry-btn').trigger('click')
    await flushPromises()

    await wrapper.get('.switcher-search-box input').setValue('ali')
    await vi.advanceTimersByTimeAsync(220)
    await flushPromises()

    expect(dashboardViewMocks.apiFetchMock).toHaveBeenNthCalledWith(3, '/api/auth/dev-switch/users?search=ali')
    expect(wrapper.text()).toContain('کاربری برای سوییچ پیدا نشد.')

    await wrapper.get('.switcher-close-btn').trigger('click')
    await flushPromises()

    expect(wrapper.find('.switcher-modal-backdrop').exists()).toBe(false)
  })

  it('shows switch errors when the target account cannot be activated', async () => {
    dashboardViewMocks.apiFetchMock
      .mockResolvedValueOnce(makeJsonResponse({
        id: 90,
        full_name: 'مدیر خطا',
        account_name: 'root90',
        role: 'مدیر ارشد',
        account_status: 'active',
        global_lock_grace_expires_at: null,
        global_web_locked_at: null,
        trading_restricted_until: null,
      }))
      .mockResolvedValueOnce(makeJsonResponse([
        {
          id: 91,
          full_name: 'هدف خطا',
          account_name: 'target91',
          mobile_number: '09120000091',
          role: 'عادی',
          is_accountant: false,
          is_customer: true,
          customer_tier: 'tier2',
        },
      ]))
      .mockResolvedValueOnce(makeJsonResponse({ detail: 'سوییچ حساب انجام نشد' }, false))

    const wrapper = await mountView()
    await wrapper.get('.switcher-entry-btn').trigger('click')
    await flushPromises()

    await wrapper.get('.switcher-user-row').trigger('click')
    await flushPromises()

    expect(wrapper.find('.switcher-error').text()).toBe('سوییچ حساب انجام نشد')
    expect(dashboardViewMocks.locationAssignMock).not.toHaveBeenCalled()
  })

  it('forces a local logout even when session lookup fails', async () => {
    dashboardViewMocks.apiFetchMock
      .mockResolvedValueOnce(makeJsonResponse({
        id: 95,
        full_name: 'کاربر خروج اجباری',
        account_name: 'logout95',
        role: 'عادی',
        account_status: 'active',
        global_lock_grace_expires_at: null,
        global_web_locked_at: null,
        trading_restricted_until: null,
      }))
      .mockRejectedValueOnce(new Error('session lookup failed'))

    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const wrapper = await mountView()

    await wrapper.get('.logout-btn').trigger('click')
    await flushPromises()

    expect(consoleErrorSpy).toHaveBeenCalled()
    expect(dashboardViewMocks.forceLogoutMock).toHaveBeenCalledTimes(1)

    consoleErrorSpy.mockRestore()
    wrapper.unmount()
  })
})
