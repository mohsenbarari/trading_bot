import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import DashboardView from './DashboardView.vue'
import { applyMarketRuntimePatch, resetMarketRuntimeForTests } from '../composables/useMarketRuntime'

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

function mockDashboardApi(options: {
  user: Record<string, unknown>
  trades?: unknown[]
  switchUsers?: unknown[]
  searchedSwitchUsers?: unknown[]
  switchResponse?: { payload: unknown; ok?: boolean }
  activeSessions?: unknown[]
  failSessionLookup?: boolean
}) {
  dashboardViewMocks.apiFetchMock.mockImplementation(async (url: string, requestOptions?: RequestInit) => {
    if (url === '/api/auth/me') {
      return makeJsonResponse(options.user)
    }
    if (url.startsWith('/api/trades/my?')) {
      return makeJsonResponse(options.trades || [])
    }
    if (url === '/api/auth/dev-switch/users') {
      return makeJsonResponse(options.switchUsers || [])
    }
    if (url.startsWith('/api/auth/dev-switch/users?search=')) {
      return makeJsonResponse(options.searchedSwitchUsers || [])
    }
    if (url.startsWith('/api/auth/dev-switch/')) {
      return makeJsonResponse(
        options.switchResponse?.payload || {},
        options.switchResponse?.ok ?? true,
      )
    }
    if (url === '/api/sessions/active') {
      if (options.failSessionLookup) {
        throw new Error('session lookup failed')
      }
      return makeJsonResponse(options.activeSessions || [])
    }
    if (url.startsWith('/api/sessions/') && requestOptions?.method === 'DELETE') {
      return makeJsonResponse({ ok: true })
    }
    return makeJsonResponse(null)
  })
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
    resetMarketRuntimeForTests()
    localStorage.clear()
    vi.stubGlobal('location', {
      ...window.location,
      assign: dashboardViewMocks.locationAssignMock,
    })
  })

  it('loads the current user, shows the unread notification dot, and routes the top-bar actions', async () => {
    dashboardViewMocks.notificationStore.appNotifications = [{ id: 1 }]
    mockDashboardApi({
      user: {
        id: 12,
        full_name: 'رضا محمدی',
        account_name: 'reza12',
        account_status: 'active',
        global_lock_grace_expires_at: null,
        global_web_locked_at: null,
        trading_restricted_until: null,
      },
      trades: [
        {
          id: 101,
          trade_type: 'buy',
          offer_user_id: 44,
          responder_user_id: 12,
          counterparty_name: 'حسین رضایی',
          commodity_name: 'سکه',
          quantity: 3,
          price: 123000,
        },
        {
          id: 102,
          trade_type: 'sell',
          offer_user_id: 77,
          responder_user_id: 88,
          counterparty_name: 'نباید دیده شود',
          commodity_name: 'امام',
          quantity: 40,
          price: 170000,
        },
      ],
    })

    const wrapper = await mountView()

    expect(dashboardViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/auth/me')
    expect(dashboardViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/trades/my?from_date=2026-05-14&to_date=2026-05-14&limit=20')
    expect(wrapper.text()).toContain('صبح بخیر')
    expect(wrapper.text()).toContain('رضا محمدی')
    expect(wrapper.get('.avatar').text()).toContain('ر')
    expect(wrapper.get('.user-info-center').element.tagName).toBe('BUTTON')
    expect(wrapper.get('.user-info-center').attributes('aria-label')).toBe('مشاهده پروفایل رضا محمدی')
    expect(wrapper.find('.notif-dot').exists()).toBe(true)
    expect(wrapper.get('.today-trades-card').text()).toContain('طرف مقابل معامله')
    expect(wrapper.get('.today-trades-card').text()).toContain('حسین رضایی')
    expect(wrapper.get('.today-trades-card').text()).toContain('خرید')
    expect(wrapper.get('.today-trades-card').text()).toContain('سکه')
    expect(wrapper.get('.today-trades-card').text()).not.toContain('نباید دیده شود')
    expect(wrapper.get('.dashboard-shortcuts').text()).toContain('عملیات')
    expect(wrapper.get('.dashboard-shortcuts').text()).toContain('حساب')
    expect(wrapper.get('.dashboard-overview').text()).toContain('وضعیت حساب')
    expect(wrapper.get('.dashboard-overview').text()).toContain('حساب فعال')
    expect(wrapper.get('.dashboard-overview').text()).toContain('وضعیت بازار')
    expect(wrapper.get('.dashboard-overview').text()).toContain('کار امروز')
    expect(wrapper.get('.dashboard-overview').text()).toContain('۱ معامله')
    expect(wrapper.get('.dashboard-overview').text()).toContain('۱ اعلان')

    await wrapper.get('.notif-btn').trigger('click')
    await wrapper.get('.user-info-center').trigger('click')
    await wrapper.get('.hero-btn').trigger('click')
    await wrapper.findAll('.dashboard-shortcut-card')[0]!.trigger('click')
    await wrapper.findAll('.dashboard-shortcut-card')[1]!.trigger('click')

    expect(dashboardViewMocks.routerPushMock).toHaveBeenNthCalledWith(1, '/notifications')
    expect(dashboardViewMocks.routerPushMock).toHaveBeenNthCalledWith(2, '/profile')
    expect(dashboardViewMocks.routerPushMock).toHaveBeenNthCalledWith(3, '/market')
    expect(dashboardViewMocks.routerPushMock).toHaveBeenNthCalledWith(4, '/operations')
    expect(dashboardViewMocks.routerPushMock).toHaveBeenNthCalledWith(5, '/account')
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

  it('styles the market entry for closed hours while keeping the market page reachable', async () => {
    applyMarketRuntimePatch({
      is_open: false,
      active_web_notice_visible: true,
      offers_since_last_open: 0,
      last_transition_at: '2026-06-12T10:00:00Z',
      next_transition_at: '2026-06-13T06:00:00Z',
    })
    dashboardViewMocks.apiFetchMock.mockResolvedValue(
      makeJsonResponse({
        id: 17,
        full_name: 'کاربر بازار',
        account_name: 'market17',
        account_status: 'active',
        global_lock_grace_expires_at: null,
        global_web_locked_at: null,
        trading_restricted_until: null,
      }),
    )

    const wrapper = await mountView()
    const marketButton = wrapper.get('.hero-btn')

    expect(marketButton.classes()).toContain('hero-btn--closed')
    expect(marketButton.text()).toContain('بازار بسته')
    expect(marketButton.text()).toContain('فعلاً امکان ثبت لفظ جدید وجود ندارد')

    await marketButton.trigger('click')
    expect(dashboardViewMocks.routerPushMock).toHaveBeenCalledWith('/market')
  })

  it('hides the market entry button for accountants', async () => {
    mockDashboardApi({
      user: {
        id: 18,
        full_name: 'حسابدار وب',
        account_name: 'accountant18',
        account_status: 'active',
        is_accountant: true,
        accountant_owner_user_id: 44,
        global_lock_grace_expires_at: null,
        global_web_locked_at: null,
        trading_restricted_until: null,
      },
      trades: [
        {
          id: 181,
          trade_type: 'sell',
          offer_user_id: 19,
          responder_user_id: 44,
          counterparty_name: 'طرف مالک',
          commodity_name: 'طلای آب‌شده',
          quantity: 7,
          price: 456000,
        },
      ],
    })

    const wrapper = await mountView()

    expect(wrapper.find('.hero-btn').exists()).toBe(false)
    expect(wrapper.find('.logout-btn').exists()).toBe(false)
    expect(wrapper.get('.today-trades-card').text()).toContain('طرف مالک')
    expect(wrapper.get('.today-trades-card').text()).toContain('فروش')
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
    mockDashboardApi({
      user: {
        id: 15,
        full_name: 'کاربر خروج',
        account_name: 'logout15',
        account_status: 'active',
        global_lock_grace_expires_at: null,
        global_web_locked_at: null,
        trading_restricted_until: null,
      },
      activeSessions: [
        { id: 'session-a', is_current: false },
        { id: 'session-b', is_current: true },
      ],
    })

    const wrapper = await mountView()
    await wrapper.get('.logout-btn').trigger('click')
    await flushPromises()

    expect(dashboardViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/sessions/active')
    expect(dashboardViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/sessions/session-b', { method: 'DELETE' })
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
    mockDashboardApi({
      user: {
        id: 50,
        full_name: 'مدیر تست',
        account_name: 'root50',
        role: 'مدیر ارشد',
        account_status: 'active',
        global_lock_grace_expires_at: null,
        global_web_locked_at: null,
        trading_restricted_until: null,
      },
      switchUsers: [
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
      ],
      switchResponse: {
        payload: {
        access_token: 'switched-access',
        refresh_token: 'switched-refresh',
        token_type: 'bearer',
        },
      },
    })

    const wrapper = await mountView()
    await wrapper.get('.switcher-entry-btn').trigger('click')
    await flushPromises()

    expect(dashboardViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/auth/dev-switch/users')
    expect(wrapper.text()).toContain('سوییچ موقت حساب')
    expect(wrapper.text()).toContain('حسابدار تست')

    await wrapper.get('.switcher-user-row').trigger('click')
    await flushPromises()

    expect(dashboardViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/auth/dev-switch/61', { method: 'POST' })
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
    mockDashboardApi({
      user: {
        id: 80,
        full_name: 'مدیر جستجو',
        account_name: 'root80',
        role: 'مدیر ارشد',
        account_status: 'active',
        global_lock_grace_expires_at: null,
        global_web_locked_at: null,
        trading_restricted_until: null,
      },
      switchUsers: [
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
      ],
      searchedSwitchUsers: [],
    })

    const wrapper = await mountView()
    await wrapper.get('.switcher-entry-btn').trigger('click')
    await flushPromises()

    await wrapper.get('.switcher-search-box input').setValue('ali')
    await vi.advanceTimersByTimeAsync(220)
    await flushPromises()

    expect(dashboardViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/auth/dev-switch/users?search=ali')
    expect(wrapper.text()).toContain('کاربری برای سوییچ پیدا نشد.')

    await wrapper.get('.switcher-close-btn').trigger('click')
    await flushPromises()

    expect(wrapper.find('.switcher-modal-backdrop').exists()).toBe(false)
  })

  it('shows switch errors when the target account cannot be activated', async () => {
    mockDashboardApi({
      user: {
        id: 90,
        full_name: 'مدیر خطا',
        account_name: 'root90',
        role: 'مدیر ارشد',
        account_status: 'active',
        global_lock_grace_expires_at: null,
        global_web_locked_at: null,
        trading_restricted_until: null,
      },
      switchUsers: [
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
      ],
      switchResponse: {
        payload: { detail: 'سوییچ حساب انجام نشد' },
        ok: false,
      },
    })

    const wrapper = await mountView()
    await wrapper.get('.switcher-entry-btn').trigger('click')
    await flushPromises()

    await wrapper.get('.switcher-user-row').trigger('click')
    await flushPromises()

    expect(wrapper.find('.switcher-error').text()).toBe('سوییچ حساب انجام نشد')
    expect(dashboardViewMocks.locationAssignMock).not.toHaveBeenCalled()
  })

  it('forces a local logout even when session lookup fails', async () => {
    mockDashboardApi({
      user: {
        id: 95,
        full_name: 'کاربر خروج اجباری',
        account_name: 'logout95',
        role: 'عادی',
        account_status: 'active',
        global_lock_grace_expires_at: null,
        global_web_locked_at: null,
        trading_restricted_until: null,
      },
      failSessionLookup: true,
    })

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
