import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import DashboardView from './DashboardView.vue'
import PWAInstallOverlay from '../components/PWAInstallOverlay.vue'
import {
  applyMarketRuntimePatch,
  resetMarketRuntimeForTests,
} from '../composables/useMarketRuntime'
import {
  cacheCurrentUserSummary,
  clearCurrentUserSummary,
  currentUserSummary,
} from '../utils/currentUser'
// @ts-expect-error The production guard helper is intentionally shipped as plain ESM.
import {
  DASHBOARD_MARKET_REGION_SHA256,
  dashboardMarketRegionEvidence,
} from '../../scripts/lib/stage3-protected-region-guard.mjs'

const dashboardViewMocks = vi.hoisted(() => ({
  routerPushMock: vi.fn(),
  apiFetchMock: vi.fn(),
  forceLogoutMock: vi.fn(),
  isAppConnecting: { value: false },
  notificationStore: {
    appNotifications: [] as Array<Record<string, unknown>>,
    appUnreadCount: 0,
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
  isAppConnecting: dashboardViewMocks.isAppConnecting,
}))

const DashboardDailySectionsStub = {
  props: ['user'],
  template: `
    <section class="dashboard-daily-sections-stub">
      <h2>معاملات امروز</h2>
      <h2>لیست همکاران</h2>
      <h2>کالاهای مجاز برای معامله</h2>
    </section>
  `,
}

function makeJsonResponse(payload: unknown, ok = true) {
  return {
    ok,
    json: async () => payload,
  }
}

function mockIdentity(user: Record<string, unknown>) {
  dashboardViewMocks.apiFetchMock.mockImplementation(async (url: string) => {
    if (url === '/api/auth/me') {
      return makeJsonResponse({
        role: 'عادی',
        is_accountant: false,
        is_customer: false,
        ...user,
      })
    }
    return makeJsonResponse(null, false)
  })
}

function mountDashboard() {
  return mount(DashboardView, {
    global: {
      stubs: { DashboardDailySections: DashboardDailySectionsStub },
    },
  })
}

async function mountView() {
  const wrapper = mountDashboard()
  await flushPromises()
  return wrapper
}

function requestedUrls() {
  return dashboardViewMocks.apiFetchMock.mock.calls.map(([url]) => url)
}

describe('DashboardView.vue Stage 4 Home contract', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 4, 14, 5, 0, 0))
    dashboardViewMocks.routerPushMock.mockReset()
    dashboardViewMocks.apiFetchMock.mockReset()
    dashboardViewMocks.forceLogoutMock.mockReset()
    dashboardViewMocks.isAppConnecting.value = false
    dashboardViewMocks.notificationStore.appNotifications = []
    dashboardViewMocks.notificationStore.appUnreadCount = 0
    Object.defineProperty(window.navigator, 'onLine', {
      configurable: true,
      value: true,
    })
    resetMarketRuntimeForTests()
    clearCurrentUserSummary()
    localStorage.clear()
  })

  it('leaves loading for a cause-neutral identity error and retries the only Home request', async () => {
    dashboardViewMocks.apiFetchMock
      .mockRejectedValueOnce(new Error('private transport detail'))
      .mockResolvedValueOnce(
        makeJsonResponse({
          id: 73,
          role: 'عادی',
          is_accountant: false,
          is_customer: false,
          full_name: 'کاربر بازیابی‌شده',
          account_name: 'recovered73',
          account_status: 'active',
          global_lock_grace_expires_at: null,
          global_web_locked_at: null,
          trading_restricted_until: null,
        }),
      )

    const wrapper = await mountView()

    expect(wrapper.find('.ui-loading-state').exists()).toBe(false)
    expect(wrapper.get('.dashboard-identity-error').text()).toContain(
      'دریافت اطلاعات خانه انجام نشد',
    )
    expect(wrapper.get('main.dashboard-page').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('private transport detail')
    expect(wrapper.text()).not.toContain('اینترنت')
    expect(wrapper.findComponent(PWAInstallOverlay).exists()).toBe(false)

    await wrapper.get('.dashboard-identity-retry').trigger('click')
    await flushPromises()

    expect(requestedUrls()).toEqual(['/api/auth/me', '/api/auth/me'])
    expect(wrapper.find('.dashboard-identity-error').exists()).toBe(false)
    expect(wrapper.text()).toContain('کاربر بازیابی‌شده')
  })

  it('retains cached identity offline, shows freshness, removes retry, and suppresses PWA', async () => {
    cacheCurrentUserSummary({
      id: 70,
      role: 'عادی',
      is_accountant: false,
      is_customer: false,
      full_name: 'کاربر ذخیره‌شده',
      account_name: 'cached70',
      account_status: 'active',
      trading_restricted_until: null,
    })
    Object.defineProperty(window.navigator, 'onLine', {
      configurable: true,
      value: false,
    })
    dashboardViewMocks.apiFetchMock.mockRejectedValue(new Error('offline transport detail'))

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('کاربر ذخیره‌شده')
    expect(wrapper.get('.dashboard-connectivity-notice').text()).toContain(
      'اتصال اینترنت در دسترس نیست',
    )
    expect(wrapper.get('.dashboard-connectivity-notice').text()).toContain(
      'آخرین به‌روزرسانی ذخیره‌شده',
    )
    expect(wrapper.text()).not.toContain('offline transport detail')
    expect(wrapper.find('.dashboard-identity-retry').exists()).toBe(false)
    expect(wrapper.getComponent(PWAInstallOverlay).props('eligible')).toBe(false)
  })

  it('keeps cached Home visible while stale and retries without returning to loading', async () => {
    cacheCurrentUserSummary({
      id: 71,
      role: 'عادی',
      is_accountant: false,
      is_customer: false,
      full_name: 'کاربر قبلی',
      account_name: 'cached71',
      account_status: 'active',
      trading_restricted_until: null,
    })
    dashboardViewMocks.apiFetchMock
      .mockRejectedValueOnce(new Error('temporary refresh failure'))
      .mockResolvedValueOnce(
        makeJsonResponse({
          id: 71,
          role: 'عادی',
          is_accountant: false,
          is_customer: false,
          full_name: 'کاربر تازه',
          account_name: 'cached71',
          account_status: 'active',
          trading_restricted_until: null,
        }),
      )

    const wrapper = await mountView()

    expect(wrapper.find('.ui-loading-state').exists()).toBe(false)
    expect(wrapper.text()).toContain('کاربر قبلی')
    expect(wrapper.get('.dashboard-connectivity-notice').text()).toContain(
      'اطلاعات خانه به‌روز نشد',
    )
    expect(wrapper.getComponent(PWAInstallOverlay).props('eligible')).toBe(false)

    await wrapper.get('.dashboard-identity-retry').trigger('click')
    await flushPromises()

    expect(requestedUrls()).toEqual(['/api/auth/me', '/api/auth/me'])
    expect(wrapper.find('.dashboard-connectivity-notice').exists()).toBe(false)
    expect(wrapper.text()).toContain('کاربر تازه')
    expect(wrapper.getComponent(PWAInstallOverlay).props('eligible')).toBe(true)
  })

  it.each([
    ['empty object', {}],
    ['array payload', []],
    [
      'missing account status',
      { role: 'عادی', full_name: 'کاربر', is_accountant: false, is_customer: false },
    ],
    [
      'unknown account status',
      {
        role: 'عادی',
        full_name: 'کاربر',
        account_status: 'pending',
        is_accountant: false,
        is_customer: false,
      },
    ],
    [
      'missing accountant truth',
      { role: 'عادی', full_name: 'کاربر', account_status: 'active', is_customer: false },
    ],
    [
      'missing customer truth',
      { role: 'عادی', full_name: 'کاربر', account_status: 'active', is_accountant: false },
    ],
  ])('rejects %s before enabling Home or PWA', async (_label, payload) => {
    dashboardViewMocks.apiFetchMock.mockResolvedValue(makeJsonResponse(payload))

    const wrapper = await mountView()

    expect(requestedUrls()).toEqual(['/api/auth/me'])
    expect(wrapper.get('.dashboard-identity-error').text()).toContain(
      'دریافت اطلاعات خانه انجام نشد',
    )
    expect(wrapper.find('.dashboard-content').exists()).toBe(false)
    expect(wrapper.findComponent(PWAInstallOverlay).exists()).toBe(false)
    expect(currentUserSummary.value).toBeNull()
  })

  it('renders the quiet Home header, daily sections, and canonical routes', async () => {
    dashboardViewMocks.notificationStore.appNotifications = [
      { id: 1, is_read: true },
      { id: 2, is_read: false },
    ]
    mockIdentity({
      id: 12,
      role: 'owner',
      full_name: 'رضا محمدی',
      account_name: 'reza12',
      account_status: 'active',
      global_lock_grace_expires_at: null,
      global_web_locked_at: null,
      trading_restricted_until: null,
    })

    const wrapper = await mountView()

    expect(requestedUrls()).toEqual(['/api/auth/me'])
    expect(wrapper.get('#dashboard-page-title').text()).toBe('خانه')
    expect(wrapper.get('#dashboard-page-title').classes()).toContain('dashboard-page-title')
    expect(wrapper.find('.ui-v2-home-top').exists()).toBe(false)
    expect(wrapper.get('.dashboard-home-top').element.closest('[data-ui-system="v2"]')).toBeNull()
    expect(wrapper.get('.user-name').text()).toBe('رضا محمدی')
    expect(wrapper.get('.avatar').text()).toBe('ر')
    expect(wrapper.get('.user-info-center').attributes('aria-label')).toBe(
      'باز کردن منوی حساب رضا محمدی',
    )
    expect(wrapper.get('.user-info-center').attributes('aria-haspopup')).toBe('dialog')
    expect(wrapper.get('.user-info-center').attributes('aria-expanded')).toBe('false')
    expect(wrapper.findAll('.notif-dot')).toHaveLength(1)
    expect(wrapper.get('.notif-btn').attributes('aria-label')).toBe('اعلان‌های خوانده‌نشده')
    expect(wrapper.text()).not.toContain('۲ اعلان')
    expect(wrapper.text()).not.toContain('صبح بخیر')
    expect(wrapper.text()).not.toContain('حساب فعال')
    expect(wrapper.text()).not.toContain('آماده انجام عملیات روزانه')
    expect(wrapper.text()).toContain('معاملات امروز')
    expect(wrapper.text()).toContain('لیست همکاران')
    expect(wrapper.text()).toContain('کالاهای مجاز برای معامله')
    expect(wrapper.text()).not.toContain('اتصال تلگرام')
    expect(document.body.querySelector('.dashboard-account-sheet')).toBeNull()
    expect(wrapper.getComponent(PWAInstallOverlay).props('eligible')).toBe(true)

    const marketHero = wrapper.get('.hero-btn')
    expect(marketHero.element.closest('[data-ui-system="v2"]')).toBeNull()

    await wrapper.get('.notif-btn').trigger('click')
    await wrapper.get('.user-info-center').trigger('click')
    await flushPromises()
    expect(wrapper.get('.user-info-center').attributes('aria-expanded')).toBe('true')
    const profileItem = document.body.querySelector<HTMLElement>('[role="menuitem"]')
    expect(profileItem).not.toBeNull()
    profileItem!.click()
    await marketHero.trigger('click')

    expect(dashboardViewMocks.routerPushMock).toHaveBeenNthCalledWith(1, '/account/notifications')
    expect(dashboardViewMocks.routerPushMock).toHaveBeenNthCalledWith(2, '/profile')
    expect(dashboardViewMocks.routerPushMock).toHaveBeenNthCalledWith(3, '/market')
  })

  it('does not turn read notification history into an unread indicator', async () => {
    dashboardViewMocks.notificationStore.appNotifications = [
      { id: 1, is_read: true },
      { id: 2, is_read: true },
    ]
    mockIdentity({
      id: 19,
      full_name: 'کاربر بدون اعلان تازه',
      account_name: 'read19',
      account_status: 'active',
      global_lock_grace_expires_at: null,
      global_web_locked_at: null,
      trading_restricted_until: null,
    })

    const wrapper = await mountView()

    expect(wrapper.find('.notif-dot').exists()).toBe(false)
    expect(wrapper.get('.notif-btn').attributes('aria-label')).toBe('اعلان‌ها')
  })

  it('closes the account menu with Escape and logs out after best-effort session cleanup', async () => {
    dashboardViewMocks.apiFetchMock.mockImplementation(async (url: string) => {
      if (url === '/api/auth/me') {
        return makeJsonResponse({
          id: 55,
          role: 'owner',
          is_accountant: false,
          is_customer: false,
          full_name: 'مالک حساب',
          account_name: 'owner55',
          account_status: 'active',
          global_lock_grace_expires_at: null,
          global_web_locked_at: null,
          trading_restricted_until: null,
        })
      }
      if (url === '/api/sessions/active') {
        return makeJsonResponse([{ id: 901, is_current: true }, { id: 902, is_current: false }])
      }
      if (url === '/api/sessions/901') return makeJsonResponse({ ok: true })
      return makeJsonResponse(null, false)
    })

    const wrapper = await mountView()
    const trigger = wrapper.get<HTMLButtonElement>('.user-info-center')
    const focusSpy = vi.spyOn(trigger.element, 'focus')
    await trigger.trigger('click')
    await flushPromises()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()

    expect(document.body.querySelector('.dashboard-account-sheet')).toBeNull()
    expect(focusSpy).toHaveBeenCalledTimes(1)

    await trigger.trigger('click')
    await flushPromises()
    const logoutButton = Array.from(
      document.body.querySelectorAll<HTMLButtonElement>('[role="menuitem"]'),
    ).find((button) => button.textContent?.includes('خروج'))
    expect(logoutButton).toBeTruthy()
    logoutButton!.click()
    await flushPromises()

    expect(requestedUrls()).toEqual([
      '/api/auth/me',
      '/api/sessions/active',
      '/api/sessions/901',
    ])
    expect(dashboardViewMocks.apiFetchMock.mock.calls[2]?.[1]).toMatchObject({ method: 'DELETE' })
    expect(dashboardViewMocks.forceLogoutMock).toHaveBeenCalledTimes(1)
  })

  it('shows durable unread attention from the server count before history is opened', async () => {
    dashboardViewMocks.notificationStore.appUnreadCount = 4
    mockIdentity({
      id: 20,
      full_name: 'کاربر دارای اعلان',
      account_name: 'unread20',
      account_status: 'active',
      global_lock_grace_expires_at: null,
      global_web_locked_at: null,
      trading_restricted_until: null,
    })

    const wrapper = await mountView()

    expect(wrapper.findAll('.notif-dot')).toHaveLength(1)
    expect(wrapper.get('.notif-btn').attributes('aria-label')).toBe('اعلان‌های خوانده‌نشده')
    expect(wrapper.text()).not.toContain('۴ اعلان')
  })

  it('makes PWA eligibility depend on healthy Home identity, not a removed activity request', async () => {
    let resolveIdentity!: (value: ReturnType<typeof makeJsonResponse>) => void
    dashboardViewMocks.apiFetchMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveIdentity = resolve
        }),
    )

    const wrapper = mountDashboard()
    await flushPromises()

    expect(wrapper.findComponent(PWAInstallOverlay).exists()).toBe(false)

    resolveIdentity(
      makeJsonResponse({
        id: 91,
        role: 'عادی',
        is_accountant: false,
        is_customer: false,
        full_name: 'کاربر سالم',
        account_name: 'healthy91',
        account_status: 'active',
        global_lock_grace_expires_at: null,
        global_web_locked_at: null,
        trading_restricted_until: null,
      }),
    )
    await flushPromises()

    expect(requestedUrls()).toEqual(['/api/auth/me'])
    expect(wrapper.getComponent(PWAInstallOverlay).props('eligible')).toBe(true)
  })

  it('leaves shared reconnect feedback to App while keeping the Home PWA ineligible', async () => {
    dashboardViewMocks.isAppConnecting.value = true
    mockIdentity({
      id: 93,
      full_name: 'کاربر اتصال',
      account_name: 'connecting93',
      account_status: 'active',
      global_lock_grace_expires_at: null,
      global_web_locked_at: null,
      trading_restricted_until: null,
    })

    const wrapper = await mountView()

    expect(wrapper.getComponent(PWAInstallOverlay).props('eligible')).toBe(false)
    expect(wrapper.find('.dashboard-connectivity-notice').exists()).toBe(false)
    expect(requestedUrls()).toEqual(['/api/auth/me'])
  })

  it('does not duplicate the global reconnect banner when retained Home identity is stale', async () => {
    dashboardViewMocks.isAppConnecting.value = true
    cacheCurrentUserSummary({
      id: 94,
      role: 'عادی',
      full_name: 'کاربر ذخیره‌شده',
      account_name: 'cached94',
      account_status: 'active',
      is_accountant: false,
      is_customer: false,
      cached_at: '2026-08-09T12:00:00Z',
    })
    dashboardViewMocks.apiFetchMock.mockRejectedValueOnce(new Error('offline'))

    const wrapper = await mountView()

    expect(wrapper.find('.dashboard-connectivity-notice').exists()).toBe(false)
    expect(wrapper.getComponent(PWAInstallOverlay).props('eligible')).toBe(false)
  })

  it('shows one actionable inactive warning, removes dead Market entry, and routes follow-up to Account', async () => {
    mockIdentity({
      id: 13,
      full_name: 'کاربر غیرفعال',
      account_name: 'inactive13',
      account_status: 'inactive',
      global_lock_grace_expires_at: '2026-05-20T12:00:00Z',
      global_web_locked_at: null,
      trading_restricted_until: '2026-05-20T12:00:00Z',
    })

    const wrapper = await mountView()

    expect(wrapper.findAll('.dashboard-alert-card')).toHaveLength(1)
    expect(wrapper.get('.dashboard-alert-card').text()).toContain('حساب کاربری غیرفعال شده است')
    expect(wrapper.text()).not.toContain('معاملات موقتاً محدود است')
    expect(wrapper.get('.dashboard-account-follow-up').text()).toBe('پیگیری در حساب')
    expect(wrapper.find('.hero-btn').exists()).toBe(false)
    expect(wrapper.getComponent(PWAInstallOverlay).props('eligible')).toBe(false)
    expect(dashboardViewMocks.routerPushMock).not.toHaveBeenCalled()

    await wrapper.get('.dashboard-account-follow-up').trigger('click')
    expect(dashboardViewMocks.routerPushMock).toHaveBeenCalledWith({ name: 'account' })
  })

  it('uses the stronger inactive copy when the account is already globally locked', async () => {
    mockIdentity({
      id: 16,
      full_name: 'کاربر قفل‌شده',
      account_name: 'locked16',
      account_status: 'inactive',
      global_lock_grace_expires_at: '2026-05-16T12:00:00Z',
      global_web_locked_at: '2026-05-17T12:00:00Z',
      trading_restricted_until: null,
    })

    const wrapper = await mountView()

    expect(wrapper.get('.dashboard-alert-card').text()).toContain('حساب کاربری قفل شده است')
    expect(wrapper.get('.dashboard-alert-card').text()).toContain('نشست‌های وب و پیام‌رسان')
    expect(wrapper.getComponent(PWAInstallOverlay).props('eligible')).toBe(false)
  })

  it('shows one deadline-based restricted warning without inventing a second action', async () => {
    mockIdentity({
      id: 14,
      full_name: 'کاربر محدود',
      account_name: 'limited14',
      account_status: 'active',
      global_lock_grace_expires_at: null,
      global_web_locked_at: null,
      trading_restricted_until: '2026-05-20T12:00:00Z',
    })

    const wrapper = await mountView()

    expect(wrapper.findAll('.dashboard-alert-card')).toHaveLength(1)
    expect(wrapper.get('.dashboard-alert-card').text()).toContain('معاملات موقتاً محدود است')
    expect(wrapper.get('.dashboard-alert-card').text()).toContain('محدود شده است')
    expect(wrapper.find('.dashboard-account-follow-up').exists()).toBe(false)
    expect(wrapper.get('.hero-btn').attributes('disabled')).toBeUndefined()
    expect(wrapper.getComponent(PWAInstallOverlay).props('eligible')).toBe(false)
  })

  it('keeps accountant Home quiet without dead Market or owner-customer destinations', async () => {
    mockIdentity({
      id: 18,
      full_name: 'حسابدار امید',
      account_name: 'accountant18',
      account_status: 'active',
      is_accountant: true,
      global_lock_grace_expires_at: null,
      global_web_locked_at: null,
      trading_restricted_until: null,
    })

    const wrapper = await mountView()

    expect(wrapper.find('.hero-btn').exists()).toBe(false)
    expect(wrapper.find('.accountant-customers-action').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('مشتریان')
    expect(wrapper.text()).not.toContain('مشاهده محدود بازار')
    expect(requestedUrls()).toEqual(['/api/auth/me'])
    expect(wrapper.getComponent(PWAInstallOverlay).props('eligible')).toBe(true)

    await wrapper.get('.notif-btn').trigger('click')
    await wrapper.get('.user-info-center').trigger('click')
    await flushPromises()
    document.body.querySelector<HTMLElement>('[role="menuitem"]')!.click()

    expect(dashboardViewMocks.routerPushMock).toHaveBeenNthCalledWith(1, '/account/notifications')
    expect(dashboardViewMocks.routerPushMock).toHaveBeenNthCalledWith(2, '/profile')
    expect(dashboardViewMocks.routerPushMock).not.toHaveBeenCalledWith({
      name: 'operations-customers',
    })
  })

  it.each([
    ['owner', 'owner'],
    ['admin', 'admin'],
  ])('keeps the protected Market hero for an active %s identity', async (_label, role) => {
    mockIdentity({
      id: role === 'owner' ? 31 : 32,
      role,
      full_name: role === 'owner' ? 'مالک فعال' : 'مدیر فعال',
      account_name: `${role}31`,
      account_status: 'active',
      is_accountant: false,
      global_lock_grace_expires_at: null,
      global_web_locked_at: null,
      trading_restricted_until: null,
    })

    const wrapper = await mountView()

    expect(wrapper.findAll('.hero-btn')).toHaveLength(1)
    expect(wrapper.get('.hero-btn').text()).toContain('ورود به بازار')
  })

  it('keeps the protected closed-market behavior reachable for active users', async () => {
    applyMarketRuntimePatch({
      is_open: false,
      active_web_notice_visible: true,
      offers_since_last_open: 0,
      last_transition_at: '2026-06-12T10:00:00Z',
      next_transition_at: '2026-06-13T06:00:00Z',
    })
    mockIdentity({
      id: 17,
      full_name: 'کاربر بازار',
      account_name: 'market17',
      account_status: 'active',
      global_lock_grace_expires_at: null,
      global_web_locked_at: null,
      trading_restricted_until: null,
    })

    const wrapper = await mountView()
    const marketButton = wrapper.get('.hero-btn')

    expect(marketButton.classes()).toContain('hero-btn--closed')
    expect(marketButton.text()).toContain('بازار بسته')
    expect(marketButton.text()).toContain('فعلاً امکان ثبت لفظ جدید وجود ندارد')

    await marketButton.trigger('click')
    expect(dashboardViewMocks.routerPushMock).toHaveBeenCalledWith('/market')
  })

  it('keeps the new daily data implementation outside the protected Dashboard file', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/views/DashboardView.vue'), 'utf8')
    const styleStart = source.indexOf('<style scoped>')
    const runtimeSource = source.slice(0, styleStart)
    const styleSource = source.slice(styleStart)

    expect(runtimeSource).not.toMatch(/\/api\/trades\/my|\/api\/commodities|project-users/)
    expect(runtimeSource).not.toMatch(/telegramLink|TelegramConnectPanel/)
    expect(runtimeSource).toContain('DashboardDailySections')
    expect(runtimeSource).not.toMatch(/اتصال تلگرام|operations-customers/)
    expect(styleSource).not.toMatch(
      /\.today-trades-card|\.dashboard-project-users|\.dashboard-commodit|\.telegram-connect/,
    )
    expect(styleSource.match(/\.today-trades-refresh/g)).toHaveLength(1)
    expect(styleSource).toMatch(/\.dashboard-content\s*\{[\s\S]*?min-width:\s*0/)
    expect(styleSource).not.toMatch(/\.dashboard-content\s*\{[\s\S]*?padding:\s*var\(--ds-page-padding\)/)
    expect(runtimeSource).not.toMatch(/ui-v2-home-/)
  })

  it('retains the byte-locked six-section Market interior contract', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/views/DashboardView.vue'), 'utf8')
    const evidence = dashboardMarketRegionEvidence(source)

    expect(evidence.sections.map(({ id }: { id: string }) => id)).toEqual([
      'market-computed',
      'open-market',
      'template-hero',
      'hero-disabled-css',
      'hero-focus-css',
      'hero-css',
    ])
    expect(evidence.bytes).toBe(4553)
    expect(evidence.sha256).toBe(DASHBOARD_MARKET_REGION_SHA256)
    expect(evidence.sha256).toBe('f25c01dac38db208517047ffc0f2458e2c89868e988a6d7f68749221db106860')
  })
})
