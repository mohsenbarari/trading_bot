import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import NotificationsView from './NotificationsView.vue'
import { useNotificationStore } from '../stores/notifications'

const routerPushMock = vi.fn()

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: routerPushMock,
  }),
}))

describe('NotificationsView.vue', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    routerPushMock.mockReset()
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

  it('routes back home and delegates clear/delete actions to the store', async () => {
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

    await wrapper.get('.back-button').trigger('click')
    expect(routerPushMock).toHaveBeenCalledWith('/')

    await wrapper.get('.clear-btn').trigger('click')
    expect(clearAllSpy).toHaveBeenCalledTimes(1)

    await wrapper.get('.delete-btn').trigger('click')
    expect(deleteSpy).toHaveBeenCalledWith(11)
    expect(wrapper.text()).toContain('اعلان')
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

    await wrapper.get('.notif-item').trigger('click')
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

    await wrapper.get('.toggle-read-btn').trigger('click')
    expect(toggleReadSpy).toHaveBeenCalledWith(14, true)
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