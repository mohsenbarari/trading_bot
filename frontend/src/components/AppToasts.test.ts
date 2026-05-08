import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import AppToasts from './AppToasts.vue'
import { useNotificationStore } from '../stores/notifications'

const routerPushMock = vi.fn()

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: routerPushMock,
  }),
}))

describe('AppToasts.vue', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    routerPushMock.mockReset()
  })

  it('navigates to the toast route and removes the toast on click', async () => {
    const store = useNotificationStore()
    store.activeToasts = [
      { id: 1, title: 'اعلان', body: 'متن اعلان', route: '/notifications', kind: 'app' },
    ]

    const removeToastSpy = vi.spyOn(store, 'removeToast')
    const wrapper = mount(AppToasts, {
      global: {
        stubs: {
          teleport: true,
        },
      },
    })

    await wrapper.get('.toast-card-floating').trigger('click')

    expect(routerPushMock).toHaveBeenCalledWith('/notifications')
    expect(removeToastSpy).toHaveBeenCalledWith(1)
  })

  it('dismisses the toast when the close button is pressed', async () => {
    const store = useNotificationStore()
    store.activeToasts = [
      { id: 7, title: 'هشدار', body: 'پیام', kind: 'app' },
    ]

    const removeToastSpy = vi.spyOn(store, 'removeToast')
    const wrapper = mount(AppToasts, {
      global: {
        stubs: {
          teleport: true,
        },
      },
    })

    await wrapper.get('.close-btn-minimal').trigger('click')
    expect(removeToastSpy).toHaveBeenCalledWith(7)
  })
})