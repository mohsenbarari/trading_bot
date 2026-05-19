import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import DashboardView from './DashboardView.vue'

const dashboardViewMocks = vi.hoisted(() => ({
  routerPushMock: vi.fn(),
  apiFetchMock: vi.fn(),
  forceLogoutMock: vi.fn(),
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

async function mountView() {
  const wrapper = mount(DashboardView)
  await flushPromises()
  return wrapper
}

describe('DashboardView.vue', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 4, 14, 9, 0, 0))
    dashboardViewMocks.routerPushMock.mockReset()
    dashboardViewMocks.apiFetchMock.mockReset()
    dashboardViewMocks.forceLogoutMock.mockReset()
    dashboardViewMocks.notificationStore.appNotifications = []
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
})