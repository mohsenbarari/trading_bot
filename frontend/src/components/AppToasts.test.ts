import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { nextTick, TransitionGroup } from 'vue'
import AppToasts from './AppToasts.vue'
import { useNotificationStore } from '../stores/notifications'
import {
  resetSecurityLayerStateForTests,
  setSecurityLayerActive,
} from '../utils/securityLayerState'

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
    resetSecurityLayerStateForTests()
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
    store.activeToasts = [{ id: 7, title: 'هشدار', body: 'پیام', kind: 'app' }]

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

  it('renders live toasts through the AppToast tone classes', () => {
    const store = useNotificationStore()
    store.activeToasts = [
      { id: 5, title: 'خطا', body: 'پیام خطا', kind: 'app', level: 'error' },
      { id: 6, title: 'چت', body: 'پیام چت', kind: 'chat' },
    ]

    const wrapper = mount(AppToasts, {
      global: {
        stubs: {
          teleport: true,
        },
      },
    })

    const primitiveToasts = wrapper.findAll('.ui-toast')
    expect(primitiveToasts).toHaveLength(2)
    expect(primitiveToasts[0]!.classes()).toContain('ui-toast--danger')
    expect(primitiveToasts[1]!.classes()).toContain('ui-toast--info')
  })

  it('uses the canonical motion token and labelled dismiss action in the V2 shell', () => {
    const store = useNotificationStore()
    store.activeToasts = [{ id: 12, title: 'نتیجه', body: 'ثبت شد', kind: 'app' }]

    const wrapper = mount(AppToasts, { props: { v2Scope: true } })

    expect(wrapper.get('.ui-v2-toast-item').attributes('style')).toContain(
      'opacity var(--ui-v2-motion-state)',
    )
    expect(wrapper.findComponent(TransitionGroup).props('name')).toBe('ui-v2-toast')
    expect(wrapper.get('.ui-v2-toast-dismiss').attributes('aria-label')).toBe('بستن اعلان')
  })

  it('keeps legacy transition, semantics, and security-layer behavior outside the V2 shell', async () => {
    const store = useNotificationStore()
    store.activeToasts = [{ id: 13, title: 'اعلان قدیمی', body: 'متن', kind: 'app' }]

    const wrapper = mount(AppToasts)

    expect(wrapper.findComponent(TransitionGroup).props('name')).toBe('toast')
    expect(wrapper.get('.toast-card-floating').attributes('style')).toContain(
      'all 0.5s cubic-bezier',
    )
    expect(wrapper.get('.close-btn-minimal').attributes('aria-label')).toBeUndefined()
    expect(wrapper.get('.close-btn-minimal').attributes('type')).toBeUndefined()
    expect(wrapper.find('[class*="ui-v2-toast"]').exists()).toBe(false)

    setSecurityLayerActive('session-approval', true)
    await nextTick()
    expect(wrapper.attributes('aria-hidden')).toBeUndefined()
    expect(wrapper.attributes('inert')).toBeUndefined()
  })

  it('makes the toast layer inert and hidden from assistive technology during a security choice', async () => {
    const store = useNotificationStore()
    store.activeToasts = [{ id: 14, title: 'اعلان', body: 'نباید مزاحم شود', kind: 'app' }]
    const wrapper = mount(AppToasts, { props: { v2Scope: true } })

    setSecurityLayerActive('session-approval', true)
    await nextTick()

    const layer = wrapper.get('.ui-v2-toast-layer')
    expect(layer.attributes('aria-hidden')).toBe('true')
    expect(layer.attributes()).toHaveProperty('inert')
    expect(layer.classes()).toContain('ui-v2-toast-layer--blocked')
    expect(layer.classes()).toContain('z-[9999]')

    setSecurityLayerActive('session-approval', false)
    await nextTick()
    expect(layer.attributes('aria-hidden')).toBeUndefined()
    expect(layer.attributes('inert')).toBeUndefined()
  })

  it('does not apply the legacy swipe scale transform in the V2 shell', async () => {
    const store = useNotificationStore()
    store.activeToasts = [{ id: 15, title: 'پیام', body: 'سوایپ', kind: 'app' }]
    const wrapper = mount(AppToasts, { props: { v2Scope: true } })

    const toast = wrapper.get('.toast-card-floating')
    await toast.trigger('touchstart', { touches: [{ clientX: 20 }] })
    await toast.trigger('touchmove', { touches: [{ clientX: 70 }] })

    expect(toast.attributes('style')).toContain('translateX(50px)')
    expect(toast.attributes('style')).not.toContain('scale(')
  })

  it('ignores click navigation while the user is swiping and dismisses after a large swipe', async () => {
    const store = useNotificationStore()
    store.activeToasts = [{ id: 9, title: 'پیام', body: 'سوایپ', route: '/chat', kind: 'app' }]

    const removeToastSpy = vi.spyOn(store, 'removeToast')
    const wrapper = mount(AppToasts, {
      global: {
        stubs: {
          teleport: true,
        },
      },
    })

    const toast = wrapper.get('.toast-card-floating')
    await toast.trigger('touchstart', { touches: [{ clientX: 20 }] })
    await toast.trigger('touchmove', { touches: [{ clientX: 95 }] })

    expect(toast.attributes('style')).toContain('translateX(75px)')
    await toast.trigger('click')

    expect(routerPushMock).not.toHaveBeenCalled()
    expect(removeToastSpy).not.toHaveBeenCalled()

    await toast.trigger('touchend')
    expect(removeToastSpy).toHaveBeenCalledWith(9)
  })

  it('removes toasts without navigating when they have no route or only a tiny swipe gesture', async () => {
    const store = useNotificationStore()
    store.activeToasts = [{ id: 11, title: 'بدون مسیر', body: 'کلیک ساده', kind: 'app' }]

    const removeToastSpy = vi.spyOn(store, 'removeToast')
    const wrapper = mount(AppToasts, {
      global: {
        stubs: {
          teleport: true,
        },
      },
    })

    const toast = wrapper.get('.toast-card-floating')
    await toast.trigger('touchstart', { touches: [{ clientX: 40 }] })
    await toast.trigger('touchmove', { touches: [{ clientX: 44 }] })
    await toast.trigger('touchend')

    expect(removeToastSpy).not.toHaveBeenCalled()

    await toast.trigger('click')
    expect(routerPushMock).not.toHaveBeenCalled()
    expect(removeToastSpy).toHaveBeenCalledWith(11)
  })
})
