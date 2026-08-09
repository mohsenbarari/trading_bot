import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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
const routerReplaceMock = vi.fn()
const routerResolveMock = vi.fn()
const routerCurrentRouteMock = {
  value: { name: 'home', fullPath: '/' },
}

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: routerPushMock,
    replace: routerReplaceMock,
    resolve: routerResolveMock,
    currentRoute: routerCurrentRouteMock,
  }),
}))

describe('AppToasts.vue', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    routerPushMock.mockReset()
    routerReplaceMock.mockReset()
    routerResolveMock.mockReset()
    routerResolveMock.mockImplementation((path: string) => ({
      name: path.startsWith('/missing') ? 'system-recovery' : 'resolved-toast',
      matched: path.startsWith('/missing') ? [] : [{ name: 'resolved-toast' }],
    }))
    routerCurrentRouteMock.value = { name: 'home', fullPath: '/' }
    resetSecurityLayerStateForTests()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('uses a named native route button and removes the toast after activation', async () => {
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

    const routeAction = wrapper.get('.toast-card-floating__action--interactive')
    const dismissAction = wrapper.get('.close-btn-minimal')

    expect(routeAction.element.tagName).toBe('BUTTON')
    expect(routeAction.attributes('type')).toBe('button')
    expect(routeAction.attributes('aria-label')).toBe('باز کردن اعلان «اعلان»')
    expect(routeAction.element.contains(dismissAction.element)).toBe(false)

    await routeAction.trigger('click')

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
    const routeAction = wrapper.get('.toast-card-floating__action--interactive')
    await toast.trigger('touchstart', { touches: [{ clientX: 20 }] })
    await toast.trigger('touchmove', { touches: [{ clientX: 95 }] })

    expect(toast.attributes('style')).toContain('translateX(75px)')
    await routeAction.trigger('click')

    expect(routerPushMock).not.toHaveBeenCalled()
    expect(removeToastSpy).not.toHaveBeenCalled()

    await toast.trigger('touchend')
    expect(removeToastSpy).toHaveBeenCalledWith(9)
  })

  it('keeps a route-less toast non-interactive and leaves dismissal to the separate control', async () => {
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

    const surface = wrapper.get('.toast-card-floating__action')
    expect(surface.element.tagName).toBe('DIV')
    expect(wrapper.find('.toast-card-floating__action--interactive').exists()).toBe(false)

    await surface.trigger('click')
    expect(routerPushMock).not.toHaveBeenCalled()
    expect(removeToastSpy).not.toHaveBeenCalled()

    await wrapper.get('.close-btn-minimal').trigger('click')
    expect(removeToastSpy).toHaveBeenCalledWith(11)
  })

  it('keeps toast context for unsafe, failed, and recovery-redirected navigation', async () => {
    const store = useNotificationStore()
    store.activeToasts = [
      { id: 20, title: 'بیرونی', body: 'متن', route: 'https://evil.example', kind: 'app' },
      { id: 21, title: 'نامعتبر', body: 'متن', route: '/missing/target', kind: 'app' },
      { id: 22, title: 'خطا', body: 'متن', route: '/account', kind: 'app' },
      { id: 23, title: 'محدود', body: 'متن', route: '/market', kind: 'app' },
    ]
    const removeToastSpy = vi.spyOn(store, 'removeToast')
    const wrapper = mount(AppToasts)

    const routeActions = wrapper.findAll('.toast-card-floating__action--interactive')
    expect(routeActions).toHaveLength(2)
    routerPushMock.mockRejectedValueOnce(new Error('chunk unavailable'))
    await routeActions[0]!.trigger('click')
    routerPushMock.mockImplementationOnce(async () => {
      routerCurrentRouteMock.value = {
        name: 'system-recovery',
        fullPath: '/system/permission-denied',
      }
    })
    routerReplaceMock.mockImplementationOnce(async () => {
      routerCurrentRouteMock.value = { name: 'home', fullPath: '/' }
    })
    await routeActions[1]!.trigger('click')

    expect(routerReplaceMock).toHaveBeenCalledWith('/')
    expect(removeToastSpy).not.toHaveBeenCalled()
    expect(store.activeToasts).toHaveLength(4)
  })

  it('pauses auto-dismiss for focus, hover, and a blocking security layer', async () => {
    vi.useFakeTimers()
    const store = useNotificationStore()
    store.addToast({ title: 'قابل مکث', body: 'پیام', route: '/account', kind: 'app' })
    const wrapper = mount(AppToasts, { props: { v2Scope: true }, attachTo: document.body })
    const toast = wrapper.get('.toast-card-floating')

    vi.advanceTimersByTime(3000)
    await toast.trigger('focusin')
    await toast.trigger('mouseenter')
    await toast.trigger('focusout', { relatedTarget: document.body })
    vi.advanceTimersByTime(10_000)
    expect(store.activeToasts).toHaveLength(1)

    await toast.trigger('mouseleave', { relatedTarget: document.body })
    vi.advanceTimersByTime(1999)
    expect(store.activeToasts).toHaveLength(1)
    vi.advanceTimersByTime(1)
    expect(store.activeToasts).toHaveLength(0)

    store.addToast({ title: 'پشت لایه امنیتی', body: 'پیام', kind: 'app' })
    setSecurityLayerActive('session-approval', true)
    await nextTick()
    vi.advanceTimersByTime(10_000)
    expect(store.activeToasts).toHaveLength(1)

    setSecurityLayerActive('session-approval', false)
    await nextTick()
    vi.advanceTimersByTime(5000)
    expect(store.activeToasts).toHaveLength(0)
    wrapper.unmount()
  })
})
