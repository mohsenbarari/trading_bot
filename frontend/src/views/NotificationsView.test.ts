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
})