import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import NotificationsView from './NotificationsView.vue'
import { useNotificationStore } from '../stores/notifications'

const routerPushMock = vi.fn()
const webPushMocks = vi.hoisted(() => ({
  getWebPushStatus: vi.fn(),
  enableWebPushNotifications: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: routerPushMock,
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
    const openNotificationCenterSpy = vi.spyOn(store, 'openNotificationCenter').mockResolvedValue()

    const wrapper = mount(NotificationsView)
    await flushPromises()

    expect(openNotificationCenterSpy).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('هیچ اعلانی یافت نشد')
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

  it('routes back home and delegates clear/delete actions to the store after confirmation', async () => {
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
    const clearAllSpy = vi.spyOn(store, 'clearAllNotifications').mockResolvedValue()
    const deleteSpy = vi.spyOn(store, 'deleteNotification').mockResolvedValue()

    const wrapper = mount(NotificationsView)
    await flushPromises()

    await wrapper.get('.notifications-return').trigger('click')
    expect(routerPushMock).toHaveBeenCalledWith('/')

    await wrapper.get('.clear-btn').trigger('click')
    expect(wrapper.text()).toContain('پاک‌سازی همه اعلان‌ها')
    await wrapper.get('.ui-confirm-dialog .ui-button--danger').trigger('click')
    expect(clearAllSpy).toHaveBeenCalledTimes(1)

    await wrapper.find('.notification-category-tabs').findAll('[role="tab"]')[1]!.trigger('click')
    await wrapper.get('.delete-btn').trigger('click')
    expect(wrapper.text()).toContain('حذف اعلان')
    await wrapper.get('.ui-confirm-dialog .ui-button--danger').trigger('click')
    expect(deleteSpy).toHaveBeenCalledWith(11)
    expect(wrapper.text()).toContain('اعلان')
  })

  it('filters notification visibility by read state without changing store actions', async () => {
    const store = useNotificationStore()
    store.appNotifications = [
      {
        id: 21,
        title: 'خوانده نشده',
        body: 'بدنه',
        content: 'بدنه',
        message: 'بدنه',
        level: 'info',
        category: 'system',
        is_read: false,
      },
      {
        id: 22,
        title: 'خوانده شده',
        body: 'بدنه',
        content: 'بدنه',
        message: 'بدنه',
        level: 'info',
        category: 'system',
        is_read: true,
      },
    ]

    vi.spyOn(store, 'openNotificationCenter').mockResolvedValue()

    const wrapper = mount(NotificationsView)
    await flushPromises()

    const categoryTabs = wrapper.find('.notification-category-tabs').findAll('[role="tab"]')
    expect(categoryTabs).toHaveLength(2)
    expect(categoryTabs[1]!.attributes('aria-selected')).toBe('true')
    const filterTabs = wrapper.find('.notification-toolbar').findAll('[role="tab"]')
    expect(filterTabs).toHaveLength(3)
    expect(wrapper.text()).toContain('خوانده نشده')
    expect(wrapper.text()).toContain('خوانده شده')

    await filterTabs[1]!.trigger('click')
    expect(wrapper.text()).toContain('خوانده نشده')
    expect(wrapper.text()).not.toContain('خوانده شده')
    expect(wrapper.find('.notification-toolbar').findAll('[role="tab"]')[1]!.attributes('tabindex')).toBe('0')
    expect(wrapper.find('.notification-toolbar').findAll('[role="tab"]')[1]!.attributes('aria-selected')).toBe('true')

    await wrapper.find('.notification-toolbar').findAll('[role="tab"]')[2]!.trigger('click')
    expect(wrapper.text()).not.toContain('خوانده نشده')
    expect(wrapper.text()).toContain('خوانده شده')
  })

  it('supports keyboard navigation across notification filter tabs', async () => {
    const store = useNotificationStore()
    store.appNotifications = [
      {
        id: 31,
        title: 'خوانده نشده',
        body: 'بدنه',
        content: 'بدنه',
        message: 'بدنه',
        level: 'info',
        category: 'system',
        is_read: false,
      },
      {
        id: 32,
        title: 'خوانده شده',
        body: 'بدنه',
        content: 'بدنه',
        message: 'بدنه',
        level: 'info',
        category: 'system',
        is_read: true,
      },
    ]

    vi.spyOn(store, 'openNotificationCenter').mockResolvedValue()

    const wrapper = mount(NotificationsView)
    await flushPromises()

    const chips = () => wrapper.find('.notification-toolbar').findAll('[role="tab"]')
    expect(chips().map((chip) => chip.attributes('tabindex'))).toEqual(['0', '-1', '-1'])

    await chips()[0]!.trigger('keydown', { key: 'ArrowLeft' })
    expect(chips()[1]!.attributes('aria-selected')).toBe('true')
    expect(wrapper.text()).toContain('خوانده نشده')
    expect(wrapper.text()).not.toContain('خوانده شده')

    await chips()[1]!.trigger('keydown', { key: 'End' })
    expect(chips()[2]!.attributes('aria-selected')).toBe('true')
    expect(wrapper.text()).not.toContain('خوانده نشده')
    expect(wrapper.text()).toContain('خوانده شده')

    await chips()[2]!.trigger('keydown', { key: 'Home' })
    expect(chips()[0]!.attributes('aria-selected')).toBe('true')
    expect(wrapper.text()).toContain('خوانده نشده')
    expect(wrapper.text()).toContain('خوانده شده')
  })

  it('opens a notification route when the item carries one', async () => {
    const store = useNotificationStore()
    store.appNotifications = [
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
    ] as any

    vi.spyOn(store, 'openNotificationCenter').mockResolvedValue()

    const wrapper = mount(NotificationsView)
    await flushPromises()

    await wrapper.find('.notification-category-tabs').findAll('[role="tab"]')[0]!.trigger('click')

    await wrapper.get('.notif-item').trigger('click')
    expect(routerPushMock).toHaveBeenCalledWith('/users/19?account_name=owner-19')
    expect(wrapper.get('.notif-item').attributes('role')).toBe('button')
    expect(wrapper.get('.notif-item').attributes('tabindex')).toBe('0')

    routerPushMock.mockClear()
    await wrapper.get('.notif-item').trigger('keydown', { key: 'Enter' })
    expect(routerPushMock).toHaveBeenCalledWith('/users/19?account_name=owner-19')
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
    const toggleReadSpy = vi.spyOn(store, 'toggleReadStatus').mockResolvedValue()

    const wrapper = mount(NotificationsView)
    await flushPromises()

    expect(wrapper.find('.notif-lines').exists()).toBe(false)
    expect(wrapper.find('.notif-text').text()).toContain('یادآوری ساده')
    expect(wrapper.find('.notif-title').text()).toBe('اعلان جدید')
    expect(wrapper.find('.unread-dot').exists()).toBe(true)
    expect(wrapper.find('.notif-time').exists()).toBe(true)
    expect(wrapper.text()).toContain('جدید')

    await wrapper.get('.toggle-read-btn').trigger('click')
    expect(toggleReadSpy).toHaveBeenCalledWith(14, true)
    expect(wrapper.get('.toggle-read-btn').attributes('aria-label')).toContain('خوانده‌شده')
    expect(routerPushMock).not.toHaveBeenCalled()

    await wrapper.get('.notif-item').trigger('click')
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
    expect(wrapper.find('.notif-line-plain').text()).toContain('فروش')
    expect(wrapper.findAll('.notif-line-field')).toHaveLength(7)
    expect(wrapper.findAll('.notif-line-label').map((node) => node.text())).toEqual([
      'فی',
      'تعداد',
      'کالا',
      'طرف معامله',
      'شماره معامله',
      'زمان معامله',
      'مسیر',
    ])
    expect(wrapper.text()).toContain('مالک ↔ مشتری سطح ۱')
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

    expect(wrapper.find('.notif-text').exists()).toBe(false)
    expect(wrapper.find('.notif-lines.is-trade-lines').exists()).toBe(false)
    expect(wrapper.findAll('.notif-line')).toHaveLength(2)
    expect(wrapper.find('.notif-line-plain').text()).toContain('بروزرسانی سیستم')
    expect(wrapper.find('.notif-line-field .notif-line-label').text()).toBe('وضعیت')
    expect(wrapper.find('.unread-dot').exists()).toBe(false)
  })
})
