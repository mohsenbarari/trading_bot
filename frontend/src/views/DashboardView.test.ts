import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import DashboardView from './DashboardView.vue'
import { applyMarketRuntimePatch, resetMarketRuntimeForTests } from '../composables/useMarketRuntime'

const dashboardViewMocks = vi.hoisted(() => ({
  routerPushMock: vi.fn(),
  apiFetchMock: vi.fn(),
  forceLogoutMock: vi.fn(),
  locationAssignMock: vi.fn(),
  requestTelegramLinkMock: vi.fn(),
  openTelegramLinkMock: vi.fn(),
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

vi.mock('../services/telegramLink', () => ({
  requestTelegramLink: dashboardViewMocks.requestTelegramLinkMock,
  openTelegramLink: dashboardViewMocks.openTelegramLinkMock,
}))

function makeJsonResponse(payload: unknown, ok = true) {
  return {
    ok,
    json: async () => payload,
  }
}

function mockDashboardApi(options: {
  user: Record<string, unknown>
  trades?: unknown[]
  commodities?: unknown[]
  projectUsers?: unknown[]
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
    if (url === '/api/commodities/') {
      return makeJsonResponse(options.commodities || [])
    }
    if (url.startsWith('/api/users-public/') && url.includes('/project-users?')) {
      return makeJsonResponse(options.projectUsers || [])
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
    dashboardViewMocks.requestTelegramLinkMock.mockReset()
    dashboardViewMocks.openTelegramLinkMock.mockReset()
    dashboardViewMocks.notificationStore.appNotifications = []
    resetMarketRuntimeForTests()
    localStorage.clear()
    vi.stubGlobal('location', {
      ...window.location,
      assign: dashboardViewMocks.locationAssignMock,
    })
  })

  it('shows the Telegram connect panel only before linking and opens the generated link', async () => {
    mockDashboardApi({
      user: {
        id: 41,
        full_name: 'کاربر تلگرام',
        account_name: 'telegram41',
        account_status: 'active',
        can_connect_telegram: true,
        telegram_linked: false,
        global_lock_grace_expires_at: null,
        global_web_locked_at: null,
        trading_restricted_until: null,
      },
    })
    dashboardViewMocks.requestTelegramLinkMock.mockResolvedValue({
      telegram_linked: false,
      can_connect_telegram: true,
      telegram_url: 'https://t.me/example_bot?start=link_token',
    })

    const wrapper = await mountView()

    expect(wrapper.get('.telegram-connect-section').text()).toContain('برای استفاده از امکانات اپ در بستر تلگرام ضربه بزنید!')

    await wrapper.get('.telegram-connect-panel').trigger('click')
    await flushPromises()

    expect(dashboardViewMocks.requestTelegramLinkMock).toHaveBeenCalledTimes(1)
    expect(dashboardViewMocks.openTelegramLinkMock).toHaveBeenCalledWith('https://t.me/example_bot?start=link_token')

    wrapper.unmount()

    mockDashboardApi({
      user: {
        id: 42,
        full_name: 'کاربر متصل',
        account_name: 'telegram42',
        account_status: 'active',
        can_connect_telegram: true,
        telegram_linked: true,
        global_lock_grace_expires_at: null,
        global_web_locked_at: null,
        trading_restricted_until: null,
      },
    })

    const connectedWrapper = await mountView()
    expect(connectedWrapper.find('.telegram-connect-section').exists()).toBe(false)
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
      commodities: [
        {
          id: 1,
          name: 'سکه',
          aliases: [{ alias: 'امامی' }, { alias: 'طرح جدید' }],
        },
        {
          id: 2,
          name: 'طلای آب‌شده',
          aliases: [],
        },
      ],
      projectUsers: [
        { id: 31, account_name: 'ali31', mobile_number: '09120000031', created_at: '2026-05-12T07:30:00Z' },
        { id: 32, account_name: 'zahra32', mobile_number: '09120000032', created_at: '2026-04-20T07:30:00Z' },
      ],
    })

    const wrapper = await mountView()

    expect(dashboardViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/auth/me')
    expect(dashboardViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/trades/my?from_date=2026-05-14&to_date=2026-05-14&limit=20')
    expect(dashboardViewMocks.apiFetchMock).not.toHaveBeenCalledWith('/api/commodities/')
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
    expect(wrapper.find('.dashboard-shortcuts').exists()).toBe(false)
    expect(wrapper.findAll('.dashboard-action-card')).toHaveLength(0)
    expect(wrapper.get('.dashboard-header-summary').text()).toContain('حساب فعال')
    expect(wrapper.get('.dashboard-header-summary').text()).toContain('آماده انجام عملیات روزانه')
    expect(wrapper.get('.dashboard-header-summary').text()).toContain('بازار باز')
    expect(wrapper.get('.dashboard-header-summary').text()).toContain('کار امروز ۱ معامله')
    expect(wrapper.get('.dashboard-header-summary').text()).toContain('۱ اعلان')
    expect(wrapper.get('.dashboard-project-users-card').text()).toContain('لیست همکاران')
    expect(wrapper.get('.dashboard-project-users-card').text()).toContain('باز کنید')
    expect(wrapper.get('.dashboard-commodities-card').text()).toContain('کالاهای مجاز برای معامله')
    expect(wrapper.get('.dashboard-commodities-card').text()).toContain('باز کنید')
    expect(wrapper.get('.dashboard-commodities-card').text()).not.toContain('امامی')

    await wrapper.get('.dashboard-accordion-toggle--project-users').trigger('click')
    await flushPromises()
    expect(dashboardViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/users-public/12/project-users?limit=25&offset=0')
    expect(wrapper.get('.dashboard-project-users-card').text()).toContain('ali31')
    expect(wrapper.get('.dashboard-project-users-card').text()).toContain('09120000031')
    const projectUserCards = wrapper.findAll('.dashboard-project-user-card')
    expect(projectUserCards[0]!.text()).toContain('جدید')
    expect(projectUserCards[1]!.text()).not.toContain('جدید')

    await wrapper.get('.dashboard-accordion-toggle--commodities').trigger('click')
    await flushPromises()
    expect(dashboardViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/commodities/')
    expect(wrapper.get('.dashboard-commodities-card').text()).toContain('سکه')
    expect(wrapper.get('.dashboard-commodities-card').text()).toContain('امامی')
    expect(wrapper.get('.dashboard-commodities-card').text()).toContain('طرح جدید')
    expect(wrapper.get('.dashboard-commodities-card').text()).toContain('طلای آب‌شده')
    expect(wrapper.get('.dashboard-commodities-card').text()).toContain('برای این کالا هنوز نام مستعار جداگانه‌ای ثبت نشده است')

    await wrapper.get('.notif-btn').trigger('click')
    await wrapper.get('.user-info-center').trigger('click')
    await wrapper.get('.hero-btn').trigger('click')
    await wrapper.findAll('.dashboard-project-user-card')[0]!.trigger('click')

    expect(dashboardViewMocks.routerPushMock).toHaveBeenNthCalledWith(1, '/notifications')
    expect(dashboardViewMocks.routerPushMock).toHaveBeenNthCalledWith(2, '/profile')
    expect(dashboardViewMocks.routerPushMock).toHaveBeenNthCalledWith(3, '/market')
    expect(dashboardViewMocks.routerPushMock).toHaveBeenNthCalledWith(4, {
      name: 'public-profile',
      params: { id: 31 },
      query: { account_name: 'ali31' },
    })
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
      projectUsers: [
        { id: 20, account_name: 'owner-peer', mobile_number: '09120000020' },
      ],
    })

    const wrapper = await mountView()

    expect(wrapper.find('.hero-btn').exists()).toBe(false)
    expect(wrapper.find('.logout-btn').exists()).toBe(false)
    expect(wrapper.find('.dashboard-commodities-card').exists()).toBe(false)
    expect(wrapper.find('.dashboard-project-users-card').exists()).toBe(true)
    expect(wrapper.get('.today-trades-card').text()).toContain('طرف مالک')
    expect(wrapper.get('.today-trades-card').text()).toContain('فروش')

    await wrapper.get('.dashboard-accordion-toggle--project-users').trigger('click')
    await flushPromises()

    expect(dashboardViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/users-public/44/project-users?limit=25&offset=0')
    expect(wrapper.get('.dashboard-project-users-card').text()).toContain('owner-peer')
    expect(dashboardViewMocks.routerPushMock).not.toHaveBeenCalledWith('/market')
  })

  it('hides the commodities section for tier-2 customers', async () => {
    mockDashboardApi({
      user: {
        id: 22,
        full_name: 'customer_09120000022',
        account_name: 'customer_09120000022',
        customer_management_name: 'محسن',
        customer_tier: 'tier2',
        is_customer: true,
        account_status: 'active',
        global_lock_grace_expires_at: null,
        global_web_locked_at: null,
        trading_restricted_until: null,
      },
      trades: [],
      commodities: [
        { id: 1, name: 'سکه', aliases: [{ alias: 'امامی' }] },
      ],
    })

    const wrapper = await mountView()

    expect(wrapper.get('.user-name').text()).toBe('محسن')
    expect(wrapper.get('.avatar').text()).toContain('م')
    expect(wrapper.get('.user-info-center').attributes('aria-label')).toBe('مشاهده پروفایل محسن')
    expect(wrapper.find('.dashboard-commodities-card').exists()).toBe(false)
    expect(wrapper.find('.dashboard-project-users-card').exists()).toBe(false)
    expect(dashboardViewMocks.apiFetchMock).not.toHaveBeenCalledWith('/api/commodities/')
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
