import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import NotificationsView from './NotificationsView.vue'
import { useNotificationStore } from '../stores/notifications'
import type { NormalizedAppNotification } from '../types/notifications'

const routerPushMock = vi.fn()
const routerReplaceMock = vi.fn()
const routerResolveMock = vi.fn()
const routerCurrentRouteMock = {
  value: { name: 'account-notifications', fullPath: '/account/notifications' },
}
const webPushMocks = vi.hoisted(() => ({
  getWebPushStatus: vi.fn(),
  enableWebPushNotifications: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: routerPushMock,
    replace: routerReplaceMock,
    resolve: routerResolveMock,
    currentRoute: routerCurrentRouteMock,
  }),
}))

vi.mock('../services/webPush', () => ({
  getWebPushStatus: webPushMocks.getWebPushStatus,
  enableWebPushNotifications: webPushMocks.enableWebPushNotifications,
}))

describe('NotificationsView.vue', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    routerPushMock.mockReset()
    routerReplaceMock.mockReset()
    routerResolveMock.mockReset()
    routerCurrentRouteMock.value = {
      name: 'account-notifications',
      fullPath: '/account/notifications',
    }
    routerResolveMock.mockImplementation((path: string) => ({
      name: path.startsWith('/missing') ? 'system-recovery' : 'resolved-notification',
      fullPath: path,
      matched: path.startsWith('/missing') ? [] : [{ name: 'resolved-notification' }],
    }))
    webPushMocks.getWebPushStatus.mockReset()
    webPushMocks.enableWebPushNotifications.mockReset()
    webPushMocks.getWebPushStatus.mockResolvedValue({ state: 'subscribed' })
    webPushMocks.enableWebPushNotifications.mockResolvedValue({ state: 'subscribed' })
  })

  it('renders the loading state while the notification history is still fetching', async () => {
    const store = useNotificationStore()
    store.isLoadingHistory = true
    vi.spyOn(store, 'openNotificationCenter').mockImplementation(async () => {})

    const wrapper = mount(NotificationsView)

    expect(wrapper.find('.ds-loading-state').exists()).toBe(true)
    expect(wrapper.find('.ds-empty-state').exists()).toBe(false)
    expect(wrapper.find('.clear-btn').exists()).toBe(false)
  })

  it('opens the notification center on mount and renders the empty state when there are no notifications', async () => {
    const store = useNotificationStore()
    store.hasLoadedHistory = true
    store.historyStatus = 'success'
    const openNotificationCenterSpy = vi.spyOn(store, 'openNotificationCenter').mockResolvedValue()

    const wrapper = mount(NotificationsView)
    await flushPromises()

    expect(openNotificationCenterSpy).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('هیچ اعلانی یافت نشد')
    expect(wrapper.text()).toContain('در آخرین اعلان‌های دریافت‌شده')
    expect(wrapper.findAll('main')).toHaveLength(1)
    expect(wrapper.get('[role="tabpanel"]').attributes('id')).toBe(
      'notifications-category-trade-panel',
    )
    expect(wrapper.get('[role="tabpanel"]').attributes('aria-labelledby')).toBe(
      'notifications-category-trade-tab',
    )
  })

  it('shows a retryable error instead of a false empty state when the initial history request fails', async () => {
    const store = useNotificationStore()
    store.historyStatus = 'error'
    store.historyError = 'دریافت اعلان‌ها انجام نشد.'
    const openNotificationCenterSpy = vi.spyOn(store, 'openNotificationCenter').mockResolvedValue({
      ok: false,
      error: 'دریافت اعلان‌ها انجام نشد.',
    })

    const wrapper = mount(NotificationsView)
    await flushPromises()

    expect(wrapper.text()).toContain('اعلان‌ها دریافت نشدند')
    expect(wrapper.text()).toContain('دریافت اعلان‌ها انجام نشد. دوباره تلاش کنید.')
    expect(wrapper.text()).not.toContain('هیچ اعلانی یافت نشد')
    expect(wrapper.find('.notification-category-tabs').text()).not.toContain('۰')

    await wrapper.get('.notification-history-retry').trigger('click')
    await flushPromises()
    expect(openNotificationCenterSpy).toHaveBeenCalledTimes(2)
  })

  it('keeps retained notifications visible with compact refresh and retry feedback', async () => {
    const store = useNotificationStore()
    store.hasLoadedHistory = true
    store.historyStatus = 'error'
    store.historyError = 'دریافت اعلان‌ها انجام نشد.'
    store.appNotifications = [
      {
        id: 71,
        title: 'اعلان باقی‌مانده',
        body: 'این مورد از دریافت قبلی حفظ شده است',
        content: 'این مورد از دریافت قبلی حفظ شده است',
        message: 'این مورد از دریافت قبلی حفظ شده است',
        level: 'info',
        category: 'system',
        is_read: true,
      },
    ]
    const openNotificationCenterSpy = vi.spyOn(store, 'openNotificationCenter').mockResolvedValue({
      ok: false,
      error: 'دریافت اعلان‌ها انجام نشد.',
    })

    const wrapper = mount(NotificationsView)
    await flushPromises()
    await wrapper.find('.notification-category-tabs').findAll('[role="tab"]')[1]!.trigger('click')

    expect(wrapper.find('.notif-item').exists()).toBe(true)
    expect(wrapper.text()).toContain('موارد قبلی همچنان نمایش داده می‌شوند')
    expect(wrapper.text()).not.toContain('هیچ اعلانی یافت نشد')

    store.historyStatus = 'loading'
    store.isRefreshingHistory = true
    await flushPromises()
    expect(wrapper.find('.notif-item').exists()).toBe(true)
    expect(wrapper.text()).toContain('در حال به‌روزرسانی اعلان‌ها')
  })

  it('shows only the enable action when push notifications are inactive', async () => {
    webPushMocks.getWebPushStatus.mockResolvedValueOnce({ state: 'unsubscribed' })
    const store = useNotificationStore()
    vi.spyOn(store, 'openNotificationCenter').mockResolvedValue()

    const wrapper = mount(NotificationsView)
    await flushPromises()

    expect(wrapper.find('.push-enable-btn').exists()).toBe(true)
    expect(wrapper.find('.push-test-btn').exists()).toBe(false)
    expect(wrapper.find('.push-disable-btn').exists()).toBe(false)

    await wrapper.get('.push-enable-btn').trigger('click')
    await flushPromises()

    expect(webPushMocks.enableWebPushNotifications).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('فعال شد')
    expect(wrapper.find('.push-test-btn').exists()).toBe(false)
    expect(wrapper.find('.push-disable-btn').exists()).toBe(false)
  })

  it.each([
    ['checking', 'در حال بررسی'],
    ['unsupported', 'پشتیبانی نمی‌شود'],
    ['insecure', 'نیازمند HTTPS'],
    ['server-disabled', 'غیرفعال در سرور'],
    ['permission-blocked', 'مسدود در مرورگر'],
    ['permission-default', 'آماده فعال‌سازی'],
    ['subscribed', 'فعال'],
    ['unsubscribed', 'غیرفعال'],
    ['error', 'خطا'],
  ] as const)('renders the truthful browser Push state %s', async (state, label) => {
    webPushMocks.getWebPushStatus.mockResolvedValueOnce({ state })
    const store = useNotificationStore()
    vi.spyOn(store, 'openNotificationCenter').mockResolvedValue()

    const wrapper = mount(NotificationsView)
    await flushPromises()

    expect(wrapper.get('.ui-v2-browser-push').text()).toContain(label)
    expect(wrapper.get('.push-device-scope').text()).toContain('همین مرورگر و دستگاه')
    expect(wrapper.find('.push-enable-btn').exists()).toBe(
      state === 'permission-default' || state === 'unsubscribed',
    )
    expect(wrapper.find('.push-status-retry').exists()).toBe(state === 'error')
  })

  it('routes back to the canonical account hub and does not render per-notification action buttons', async () => {
    const store = useNotificationStore()
    store.appNotifications = [
      {
        id: 11,
        title: 'اعلان',
        body: 'بدنه',
        content: 'بدنه',
        message: 'بدنه',
        level: 'warning',
        category: 'system',
        is_read: false,
      },
    ]

    vi.spyOn(store, 'openNotificationCenter').mockResolvedValue()

    const wrapper = mount(NotificationsView)
    await flushPromises()

    await wrapper.get('.notifications-return').trigger('click')
    expect(routerPushMock).toHaveBeenCalledWith({ name: 'account' })
    expect(wrapper.find('.clear-btn').exists()).toBe(false)
    expect(wrapper.find('.notification-toolbar').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('صندوق ورودی')
    expect(wrapper.get('h1').text()).toBe('اعلان‌ها')
    expect(wrapper.find('.delete-btn').exists()).toBe(false)
    expect(wrapper.find('.toggle-read-btn').exists()).toBe(false)
    expect(wrapper.find('.notif-actions').exists()).toBe(false)
  })

  it('switches notification visibility by category without rendering read-count filters', async () => {
    const store = useNotificationStore()
    store.appNotifications = [
      {
        id: 21,
        title: 'پیام مدیریتی',
        body: 'بدنه',
        content: 'بدنه',
        message: 'بدنه',
        level: 'info',
        category: 'system',
        is_read: false,
      },
      {
        id: 22,
        title: 'اعلان معامله',
        body: 'بدنه',
        content: 'بدنه',
        message: 'بدنه',
        level: 'info',
        category: 'trade',
        is_read: true,
      },
    ]

    vi.spyOn(store, 'openNotificationCenter').mockResolvedValue()

    const wrapper = mount(NotificationsView)
    await flushPromises()

    const categoryTabs = wrapper.find('.notification-category-tabs').findAll('[role="tab"]')
    expect(categoryTabs).toHaveLength(2)
    expect(categoryTabs[0]!.attributes('aria-selected')).toBe('true')
    expect(categoryTabs[0]!.text()).toBe('معاملات')
    expect(categoryTabs[1]!.text()).toBe('سایر')
    expect(wrapper.find('.notification-toolbar').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('پیام مدیریتی')
    expect(wrapper.find('.notif-item.category-trade').exists()).toBe(true)
    expect(wrapper.find('.notif-title').exists()).toBe(false)

    await categoryTabs[1]!.trigger('click')
    expect(wrapper.text()).toContain('پیام مدیریتی')
    expect(wrapper.text()).not.toContain('اعلان معامله')
  })

  it('supports keyboard navigation across notification category tabs', async () => {
    const store = useNotificationStore()
    store.appNotifications = [
      {
        id: 31,
        title: 'پیام مدیریتی',
        body: 'بدنه',
        content: 'بدنه',
        message: 'بدنه',
        level: 'info',
        category: 'system',
        is_read: false,
      },
      {
        id: 32,
        title: 'اعلان معامله',
        body: 'بدنه',
        content: 'بدنه',
        message: 'بدنه',
        level: 'info',
        category: 'trade',
        is_read: true,
      },
    ]

    vi.spyOn(store, 'openNotificationCenter').mockResolvedValue()

    const wrapper = mount(NotificationsView, { attachTo: document.body })
    await flushPromises()

    const chips = () => wrapper.find('.notification-category-tabs').findAll('[role="tab"]')
    expect(chips().map((chip) => chip.attributes('tabindex'))).toEqual(['0', '-1'])
    expect(chips()[0]!.attributes('aria-selected')).toBe('true')
    expect(wrapper.text()).not.toContain('پیام مدیریتی')
    expect(wrapper.find('.notif-item.category-trade').exists()).toBe(true)
    expect(chips()[0]!.attributes('id')).toBe('notifications-category-trade-tab')
    expect(chips()[0]!.attributes('aria-controls')).toBe('notifications-category-trade-panel')

    chips()[0]!.element.focus()
    await chips()[0]!.trigger('keydown', { key: 'ArrowLeft' })
    await flushPromises()
    expect(document.activeElement).toBe(chips()[1]!.element)
    expect(wrapper.get('[role="tabpanel"]').attributes('id')).toBe(
      'notifications-category-management-panel',
    )

    await chips()[1]!.trigger('keydown', { key: 'ArrowRight' })
    await flushPromises()
    expect(document.activeElement).toBe(chips()[0]!.element)

    await chips()[0]!.trigger('keydown', { key: 'Home' })
    expect(chips()[0]!.attributes('aria-selected')).toBe('true')

    await chips()[0]!.trigger('keydown', { key: 'End' })
    expect(chips()[1]!.attributes('aria-selected')).toBe('true')
    expect(wrapper.text()).toContain('پیام مدیریتی')
    expect(wrapper.text()).not.toContain('اعلان معامله')

    await chips()[1]!.trigger('keydown', { key: 'Home' })
    expect(chips()[0]!.attributes('aria-selected')).toBe('true')
    expect(wrapper.text()).not.toContain('پیام مدیریتی')
    expect(wrapper.find('.notif-item.category-trade').exists()).toBe(true)
    expect(wrapper.find('.notif-title').exists()).toBe(false)
    wrapper.unmount()
  })

  it('canonicalizes a query-bearing public-profile notification route before opening it', async () => {
    const store = useNotificationStore()
    const hostileNotifications: NormalizedAppNotification[] = [
      {
        id: 12,
        title: 'معامله',
        body: 'بدنه',
        content: 'بدنه',
        message: 'بدنه',
        level: 'success',
        category: 'trade',
        is_read: false,
        route: '/users/19?account_name=owner-19',
      },
    ]
    store.appNotifications = hostileNotifications

    vi.spyOn(store, 'openNotificationCenter').mockResolvedValue()

    const wrapper = mount(NotificationsView)
    await flushPromises()

    await wrapper.find('.notification-category-tabs').findAll('[role="tab"]')[0]!.trigger('click')

    await wrapper.get('.notif-item').trigger('click')
    expect(routerPushMock).toHaveBeenCalledWith('/users/19')
    expect(wrapper.get('.notif-item').element.tagName).toBe('BUTTON')
    expect(wrapper.get('.notif-item').attributes('type')).toBe('button')
  })

  it('shows a decorative route affordance only for safely routable non-trade notifications', async () => {
    const store = useNotificationStore()
    store.appNotifications = [
      {
        id: 91,
        title: 'اعلان قابل‌مشاهده',
        body: 'بدنه',
        content: 'بدنه',
        message: 'بدنه',
        level: 'info',
        category: 'system',
        is_read: false,
        route: '/market',
      },
      {
        id: 92,
        title: 'اعلان بدون مسیر امن',
        body: 'بدنه',
        content: 'بدنه',
        message: 'بدنه',
        level: 'warning',
        category: 'system',
        is_read: true,
        route: '/missing/notification-target',
      },
      {
        id: 93,
        title: 'اعلان معامله قابل‌مشاهده',
        body: 'بدنه معامله',
        content: 'بدنه معامله',
        message: 'بدنه معامله',
        level: 'success',
        category: 'trade',
        is_read: true,
        route: '/market',
      },
      {
        id: 94,
        title: 'اعلان بازیابی‌شده',
        body: 'بدنه',
        content: 'بدنه',
        message: 'بدنه',
        level: 'warning',
        category: 'system',
        is_read: true,
        route: '/recovery-target',
      },
    ]
    vi.spyOn(store, 'openNotificationCenter').mockResolvedValue()
    routerResolveMock.mockImplementation((path: string) => {
      if (path === '/recovery-target') {
        return {
          name: 'system-recovery',
          fullPath: '/system/permission-denied',
          matched: [{ name: 'system-recovery' }],
        }
      }
      return {
        name: path.startsWith('/missing') ? 'system-recovery' : 'resolved-notification',
        fullPath: path,
        matched: path.startsWith('/missing') ? [] : [{ name: 'resolved-notification' }],
      }
    })

    const wrapper = mount(NotificationsView)
    await flushPromises()

    const source = readFileSync(resolve(process.cwd(), 'src/views/NotificationsView.vue'), 'utf8')
    const styleSource = source.slice(source.indexOf('<style scoped>'))
    expect(styleSource).toMatch(/\.notif-route-affordance\s*\{[^}]*flex:\s*0 0 auto\s*;/)
    expect(styleSource).toMatch(/\.notif-route-affordance\s*\{[^}]*pointer-events:\s*none\s*;/)

    const tradeItem = wrapper.get('.notif-item.category-trade')
    expect(tradeItem.element.tagName).toBe('BUTTON')
    expect(tradeItem.attributes('type')).toBe('button')
    expect(tradeItem.attributes('aria-label')).toBe('باز کردن اعلان اعلان معامله قابل‌مشاهده')
    expect(tradeItem.find('.notif-route-affordance').exists()).toBe(false)

    await wrapper.find('.notification-category-tabs').findAll('[role="tab"]')[1]!.trigger('click')

    const [eligibleItem, ineligibleItem, recoveryItem] = wrapper.findAll('.notif-item')
    expect(eligibleItem!.element.tagName).toBe('BUTTON')
    expect(eligibleItem!.attributes('type')).toBe('button')
    expect(eligibleItem!.attributes('aria-label')).toBe('باز کردن اعلان اعلان قابل‌مشاهده')
    expect(eligibleItem!.findAll('.notif-route-affordance')).toHaveLength(1)
    expect(eligibleItem!.get('.notif-route-affordance').attributes('aria-hidden')).toBe('true')
    expect(eligibleItem!.get('.notif-main').element.lastElementChild).toBe(
      eligibleItem!.get('.notif-route-affordance').element,
    )
    expect(wrapper.findAll('.notif-route-affordance')).toHaveLength(1)

    await eligibleItem!.trigger('click')
    expect(routerPushMock).toHaveBeenCalledWith('/market')

    expect(ineligibleItem!.element.tagName).toBe('ARTICLE')
    expect(ineligibleItem!.attributes('aria-label')).toBeUndefined()
    expect(ineligibleItem!.find('.notif-route-affordance').exists()).toBe(false)

    expect(recoveryItem!.element.tagName).toBe('ARTICLE')
    expect(recoveryItem!.attributes('aria-label')).toBeUndefined()
    expect(recoveryItem!.find('.notif-route-affordance').exists()).toBe(false)
  })

  it('restores the notification center when an auth guard redirects a target to recovery', async () => {
    const store = useNotificationStore()
    store.appNotifications = [
      {
        id: 16,
        title: 'بازار',
        body: 'اعلان بازار',
        content: 'اعلان بازار',
        message: 'اعلان بازار',
        level: 'info',
        category: 'trade',
        is_read: true,
        route: '/market',
      },
    ]
    vi.spyOn(store, 'openNotificationCenter').mockResolvedValue()
    routerPushMock.mockImplementationOnce(async () => {
      routerCurrentRouteMock.value = {
        name: 'system-recovery',
        fullPath: '/system/permission-denied',
      }
    })
    routerReplaceMock.mockImplementationOnce(async () => {
      routerCurrentRouteMock.value = {
        name: 'account-notifications',
        fullPath: '/account/notifications',
      }
    })

    const wrapper = mount(NotificationsView)
    await flushPromises()
    await wrapper.get('.notif-item').trigger('click')
    await flushPromises()

    expect(routerPushMock).toHaveBeenCalledWith('/market')
    expect(routerReplaceMock).toHaveBeenCalledWith({ name: 'account-notifications' })
    expect(routerCurrentRouteMock.value.name).toBe('account-notifications')
    expect(wrapper.text()).toContain('اعلان بازار')
  })

  it('renders plain notifications with fallback title and ignores route-less item clicks', async () => {
    const store = useNotificationStore()
    store.appNotifications = [
      {
        id: 14,
        title: '',
        body: 'یادآوری ساده',
        content: '',
        message: 'یادآوری ساده',
        level: 'info',
        category: 'system',
        is_read: false,
        route: '   ',
        created_at: '2026-05-28T06:00:00Z',
      },
    ] as any

    vi.spyOn(store, 'openNotificationCenter').mockResolvedValue()

    const wrapper = mount(NotificationsView)
    await flushPromises()
    await wrapper.find('.notification-category-tabs').findAll('[role="tab"]')[1]!.trigger('click')

    expect(wrapper.find('.notif-lines').exists()).toBe(false)
    expect(wrapper.find('.notif-text').text()).toContain('یادآوری ساده')
    expect(wrapper.find('.notif-title').text()).toBe('اعلان جدید')
    expect(wrapper.find('.unread-dot').exists()).toBe(true)
    expect(wrapper.find('.notif-time').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('خوانده‌شده')
    expect(wrapper.find('.toggle-read-btn').exists()).toBe(false)
    expect(wrapper.find('.delete-btn').exists()).toBe(false)
    expect(routerPushMock).not.toHaveBeenCalled()

    await wrapper.get('.notif-item').trigger('click')
    expect(routerPushMock).not.toHaveBeenCalled()
  })

  it('renders unbroken synthetic notification text in plain and structured local targets', async () => {
    const unbrokenText = `token-${'x'.repeat(384)}`
    const tradeBody = [unbrokenText, `${unbrokenText}: ${unbrokenText}`].join('\n')
    const store = useNotificationStore()
    store.appNotifications = [
      {
        id: 81,
        title: `عنوان-${unbrokenText}`,
        body: unbrokenText,
        content: unbrokenText,
        message: unbrokenText,
        level: 'info',
        category: 'system',
        is_read: true,
      },
      {
        id: 82,
        title: 'اعلان معامله',
        body: tradeBody,
        content: tradeBody,
        message: tradeBody,
        level: 'info',
        category: 'trade',
        is_read: true,
      },
    ]
    vi.spyOn(store, 'openNotificationCenter').mockResolvedValue()

    const wrapper = mount(NotificationsView)
    await flushPromises()

    await wrapper.find('.notification-category-tabs').findAll('[role="tab"]')[0]!.trigger('click')
    expect(wrapper.get('.notif-line-text').text()).toBe(unbrokenText)
    expect(wrapper.get('.notif-line-label').text()).toBe(unbrokenText)
    expect(wrapper.get('.notif-line-value').text()).toBe(unbrokenText)

    await wrapper.find('.notification-category-tabs').findAll('[role="tab"]')[1]!.trigger('click')
    expect(wrapper.get('.notif-title').text()).toBe(`عنوان-${unbrokenText}`)
    expect(wrapper.get('.notif-text').text()).toBe(unbrokenText)
  })

  it('keeps long-text wrapping contracts scoped to notification content', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/views/NotificationsView.vue'), 'utf8')
    const styleSource = source.slice(source.indexOf('<style scoped>'))

    expect(styleSource).toMatch(/\.notif-title\s*\{[^}]*overflow-wrap:\s*anywhere\s*;/)
    expect(styleSource).toMatch(/\.notif-text\s*\{[^}]*overflow-wrap:\s*anywhere\s*;/)
    expect(styleSource).toMatch(/\.notif-line-label\s*\{[^}]*overflow-wrap:\s*anywhere\s*;/)
    expect(styleSource).toMatch(
      /\.notif-line-value,\s*\.notif-line-text\s*\{[^}]*overflow-wrap:\s*anywhere\s*;/,
    )
    expect(styleSource).not.toMatch(/word-break:\s*break-all\s*;/)
  })

  it('fails closed for external, sensitive, and unmatched notification routes', async () => {
    const store = useNotificationStore()
    const hostileRouteNotifications: NormalizedAppNotification[] = [
      {
        id: 41,
        title: 'مسیر نامعتبر',
        body: 'بدنه',
        content: 'بدنه',
        message: 'بدنه',
        level: 'warning',
        category: 'system',
        is_read: true,
        route: 'https://evil.example/collect',
      },
      {
        id: 42,
        title: 'مسیر حساس',
        body: 'بدنه',
        content: 'بدنه',
        message: 'بدنه',
        level: 'warning',
        category: 'system',
        is_read: true,
        route: '/account?token=raw-secret',
      },
      {
        id: 43,
        title: 'مسیر ناشناخته',
        body: 'بدنه',
        content: 'بدنه',
        message: 'بدنه',
        level: 'warning',
        category: 'system',
        is_read: true,
        route: '/missing/notification-target',
      },
    ]
    store.appNotifications = hostileRouteNotifications
    vi.spyOn(store, 'openNotificationCenter').mockResolvedValue()

    const wrapper = mount(NotificationsView)
    await flushPromises()
    await wrapper.find('.notification-category-tabs').findAll('[role="tab"]')[1]!.trigger('click')

    expect(wrapper.findAll('.notif-item')).toHaveLength(3)
    expect(wrapper.findAll('.notif-item').every((item) => item.element.tagName === 'ARTICLE')).toBe(
      true,
    )
    for (const item of wrapper.findAll('.notif-item')) await item.trigger('click')
    expect(routerPushMock).not.toHaveBeenCalled()
  })

  it('renders multiline trade notifications as separate structured rows', async () => {
    const store = useNotificationStore()
    const tradeBody = [
      '🔴 فروش',
      '💰 فی: 189,000',
      '📦 تعداد: 10',
      '🏷️ کالا: امام',
      '👤 طرف معامله: bahar',
      '🔢 شماره معامله: 10005',
      '🕐 زمان معامله: 1405/03/06 11:20',
      '🧭 مسیر: مالک ↔ مشتری سطح ۱',
      '📝 توضیحات: تحویل حضوری',
    ].join('\n')

    store.appNotifications = [
      {
        id: 13,
        title: 'اعلان معامله',
        body: tradeBody,
        content: tradeBody,
        message: tradeBody,
        level: 'success',
        category: 'trade',
        is_read: false,
      },
    ]

    vi.spyOn(store, 'openNotificationCenter').mockResolvedValue()

    const wrapper = mount(NotificationsView)
    await flushPromises()

    await wrapper.find('.notification-category-tabs').findAll('[role="tab"]')[0]!.trigger('click')

    expect(wrapper.find('.notif-text').exists()).toBe(false)
    expect(wrapper.find('.notif-item').classes()).toContain('category-trade')
    expect(wrapper.find('.notif-title').exists()).toBe(false)
    expect(wrapper.find('.notif-badges').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('اعلان معامله')
    expect(wrapper.find('.notif-line-plain').text()).toContain('فروش')
    expect(wrapper.findAll('.notif-line-field')).toHaveLength(7)
    expect(wrapper.findAll('.notif-line-label').map((node) => node.text())).toEqual([
      'فی',
      'تعداد',
      'کالا',
      'طرف معامله',
      'شماره معامله',
      'زمان معامله',
      'توضیحات',
    ])
    expect(wrapper.text()).not.toContain('مالک ↔ مشتری سطح ۱')
    expect(wrapper.text()).not.toContain('مسیر')
    expect(wrapper.text()).toContain('تحویل حضوری')
  })

  it('filters raw route and backend metadata before interpreting a leading token as an icon', async () => {
    const store = useNotificationStore()
    const tradeBody =
      [
        'route: /market',
        'route=/admin',
        'مسیر: /account',
        'مسیر：/market',
        'backend: iran',
        'backend＝foreign',
        'server: api-01',
        '🏷️ کالا: امام',
        '📝 توضیحات: سالم',
      ].join('\n') + '\rserver=secondary\u2028route=/hidden\u2029backend: hidden'

    store.appNotifications = [
      {
        id: 14,
        title: 'اعلان پالایش‌شده',
        body: tradeBody,
        content: tradeBody,
        message: tradeBody,
        level: 'success',
        category: 'trade',
        is_read: true,
      },
    ]

    vi.spyOn(store, 'openNotificationCenter').mockResolvedValue()

    const wrapper = mount(NotificationsView)
    await flushPromises()
    await wrapper.find('.notification-category-tabs').findAll('[role="tab"]')[0]!.trigger('click')

    expect(wrapper.findAll('.notif-line-field')).toHaveLength(2)
    expect(wrapper.findAll('.notif-line-label').map((node) => node.text())).toEqual([
      'کالا',
      'توضیحات',
    ])
    expect(wrapper.text()).toContain('امام')
    expect(wrapper.text()).toContain('سالم')
    expect(wrapper.text()).not.toContain('/market')
    expect(wrapper.text()).not.toContain('/account')
    expect(wrapper.text()).not.toContain('/admin')
    expect(wrapper.text()).not.toContain('iran')
    expect(wrapper.text()).not.toContain('secondary')
    expect(wrapper.text()).not.toContain('/hidden')
    expect(wrapper.text()).not.toContain('foreign')
    expect(wrapper.text()).not.toContain('api-01')
  })

  it('filters blank structured lines and keeps non-trade multiline notifications in structured mode', async () => {
    const store = useNotificationStore()
    const messageBody = ['ℹ️ بروزرسانی سیستم', '', '🧪 وضعیت: پایدار'].join('\n')

    store.appNotifications = [
      {
        id: 15,
        title: 'وضعیت',
        body: messageBody,
        content: messageBody,
        message: messageBody,
        level: 'info',
        category: 'system',
        is_read: true,
      },
    ]

    vi.spyOn(store, 'openNotificationCenter').mockResolvedValue()

    const wrapper = mount(NotificationsView)
    await flushPromises()
    await wrapper.find('.notification-category-tabs').findAll('[role="tab"]')[1]!.trigger('click')

    expect(wrapper.find('.notif-text').exists()).toBe(false)
    expect(wrapper.find('.notif-lines.is-trade-lines').exists()).toBe(false)
    expect(wrapper.findAll('.notif-line')).toHaveLength(2)
    expect(wrapper.find('.notif-line-plain').text()).toContain('بروزرسانی سیستم')
    expect(wrapper.find('.notif-line-field .notif-line-label').text()).toBe('وضعیت')
    expect(wrapper.find('.unread-dot').exists()).toBe(false)
  })
})
