import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick, reactive } from 'vue'

const apiFetchMock = vi.fn()
const routeState = reactive({ name: 'home' })

vi.mock('vue-router', () => ({
  useRoute: () => routeState,
}))

vi.mock('../utils/auth', () => ({
  apiFetch: apiFetchMock,
}))

describe('BottomNav.vue', () => {
  beforeEach(() => {
    vi.resetModules()
    setActivePinia(createPinia())
    apiFetchMock.mockReset()
    localStorage.clear()
    routeState.name = 'home'
  })

  it('shows the admin entry immediately from the cached role on first mount', async () => {
    localStorage.setItem('current_user_summary', JSON.stringify({
      id: 1,
      role: 'مدیر ارشد',
      account_name: 'mohsen',
    }))
    apiFetchMock.mockRejectedValue(new Error('temporary network issue'))

    const BottomNav = (await import('./BottomNav.vue')).default
    const wrapper = mount(BottomNav, {
      global: {
        stubs: {
          'router-link': {
            template: '<a><slot /></a>',
          },
        },
      },
    })

    await flushPromises()

    expect(wrapper.text()).toContain('مدیریت')
    wrapper.unmount()
  })

  it('primes the current user, toggles the FAB menu, collapses on route changes, and renders disabled plus capped unread states', async () => {
    localStorage.setItem('auth_token', 'jwt-token')
    routeState.name = 'market'
    apiFetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ id: 2, role: 'عادی', account_name: 'normal-user' }),
    })

    const currentUserModule = await import('../utils/currentUser')
    currentUserModule.clearCurrentUserSummary()

    const BottomNav = (await import('./BottomNav.vue')).default
    const wrapper = mount(BottomNav, {
      global: {
        stubs: {
          'router-link': {
            props: ['to'],
            template: '<a :href="typeof to === \'string\' ? to : to.path"><slot /></a>',
          },
        },
      },
    })

    await flushPromises()
    expect(apiFetchMock).toHaveBeenCalledWith('/api/auth/me')

    await wrapper.get('.fab-btn').trigger('click')
    expect(wrapper.find('.fab-nav').exists()).toBe(true)

    ;(wrapper.vm as any).navItems[0].disabled = true
    const storeModule = await import('../stores/notifications')
    const notificationStore = storeModule.useNotificationStore()
    notificationStore.setChatUnreadCount(120)
    await nextTick()
    await flushPromises()

    expect(wrapper.find('.fab-unread-badge').text()).toBe('9+')

    routeState.name = 'home'
    await nextTick()
    await flushPromises()

    expect(wrapper.find('.fab-nav').exists()).toBe(false)
    expect(wrapper.find('.soon-dot').exists()).toBe(true)
    expect(wrapper.find('.nav-unread-badge').text()).toBe('99+')

    wrapper.unmount()
  })

  it('hides the market entry for accountant users from the cached summary', async () => {
    localStorage.setItem('current_user_summary', JSON.stringify({
      id: 9,
      role: 'عادی',
      account_name: 'accountant9',
      is_accountant: true,
    }))
    apiFetchMock.mockRejectedValue(new Error('temporary network issue'))

    const BottomNav = (await import('./BottomNav.vue')).default
    const wrapper = mount(BottomNav, {
      global: {
        stubs: {
          'router-link': {
            template: '<a><slot /></a>',
          },
        },
      },
    })

    await flushPromises()

    expect(wrapper.text()).not.toContain('بازار')
    wrapper.unmount()
  })

  it('shows a red closed marker on the market navigation item when the market is closed', async () => {
    localStorage.setItem('auth_token', 'jwt-token')
    apiFetchMock.mockImplementation(async (path: string) => {
      if (path === '/api/trading-settings/market-state') {
        return {
          ok: true,
          json: async () => ({
            is_open: false,
            active_web_notice_visible: true,
            offers_since_last_open: 0,
            last_transition_at: '2026-06-12T10:00:00Z',
            next_transition_at: '2026-06-13T06:00:00Z',
          }),
        }
      }
      if (path === '/api/auth/me') {
        return {
          ok: true,
          json: async () => ({ id: 3, role: 'عادی', account_name: 'market-user' }),
        }
      }
      return { ok: true, json: async () => null }
    })

    const currentUserModule = await import('../utils/currentUser')
    currentUserModule.clearCurrentUserSummary()

    const BottomNav = (await import('./BottomNav.vue')).default
    const wrapper = mount(BottomNav, {
      global: {
        stubs: {
          'router-link': {
            props: ['to'],
            template: '<a v-bind="$attrs" :href="typeof to === \'string\' ? to : to.path"><slot /></a>',
          },
        },
      },
    })

    await flushPromises()
    await nextTick()

    const marketItem = wrapper.get('.nav-item.market-closed')
    expect(marketItem.text()).toContain('بازار')
    expect(marketItem.get('.market-closed-text').text()).toBe('بسته')

    wrapper.unmount()
  })

  it('restores the persisted FAB position for messenger and ignores malformed stored coordinates', async () => {
    routeState.name = 'messenger'
    localStorage.setItem('fab_position', JSON.stringify({ x: 88, y: 144 }))

    const BottomNav = (await import('./BottomNav.vue')).default
    const wrapper = mount(BottomNav, {
      global: {
        stubs: {
          'router-link': {
            template: '<a><slot /></a>',
          },
        },
      },
    })

    await flushPromises()

    const fabContainer = wrapper.get('.fab-container')
    expect(fabContainer.attributes('style')).toContain('left: 88px;')
    expect(fabContainer.attributes('style')).toContain('top: 144px;')
    expect(fabContainer.attributes('style')).toContain('bottom: auto;')
    expect(fabContainer.attributes('style')).toContain('right: auto;')

    wrapper.unmount()

    localStorage.setItem('fab_position', '{bad json')
    const malformedWrapper = mount(BottomNav, {
      global: {
        stubs: {
          'router-link': {
            template: '<a><slot /></a>',
          },
        },
      },
    })

    await flushPromises()

    expect(malformedWrapper.get('.fab-container').attributes('style') || '').not.toContain('left:')
    malformedWrapper.unmount()
  })

  it('ignores non-numeric saved FAB coordinates and supports touch dragging with changedTouches fallback', async () => {
    vi.useFakeTimers()
    routeState.name = 'market'
    localStorage.setItem('fab_position', JSON.stringify({ x: '88', y: 144 }))

    const BottomNav = (await import('./BottomNav.vue')).default
    const wrapper = mount(BottomNav, {
      attachTo: document.body,
      global: {
        stubs: {
          'router-link': {
            template: '<a><slot /></a>',
          },
        },
      },
    })

    await flushPromises()
    expect(wrapper.get('.fab-container').attributes('style') || '').not.toContain('left:')

    await wrapper.get('.fab-btn').trigger('touchstart', {
      touches: [{ clientX: 10, clientY: 10 }],
    })

    const touchMove = new Event('touchmove', { bubbles: true, cancelable: true })
    Object.defineProperty(touchMove, 'touches', { value: [] })
    Object.defineProperty(touchMove, 'changedTouches', { value: [{ clientX: 170, clientY: 210 }] })
    document.dispatchEvent(touchMove)
    await nextTick()

    const touchEnd = new Event('touchend', { bubbles: true })
    Object.defineProperty(touchEnd, 'changedTouches', { value: [{ clientX: 170, clientY: 210 }] })
    document.dispatchEvent(touchEnd)
    await nextTick()

    const storedPosition = JSON.parse(localStorage.getItem('fab_position') || '{}')
    expect(storedPosition).toMatchObject({ x: 160, y: 200 })

    await vi.advanceTimersByTimeAsync(60)
    await wrapper.get('.fab-btn').trigger('click')
    expect(wrapper.find('.fab-nav').exists()).toBe(true)

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('renders disabled entries inside the expanded FAB menu when a nav item is flagged disabled', async () => {
    routeState.name = 'market'

    const BottomNav = (await import('./BottomNav.vue')).default
    const wrapper = mount(BottomNav, {
      global: {
        stubs: {
          'router-link': {
            template: '<a><slot /></a>',
          },
        },
      },
    })

    await flushPromises()

    ;(wrapper.vm as any).navItems[0].disabled = true
    await nextTick()

    await wrapper.get('.fab-btn').trigger('click')
    await nextTick()

    const disabledFabItem = wrapper.find('.fab-item.disabled')
    expect(disabledFabItem.exists()).toBe(true)
    expect(disabledFabItem.text()).toContain('خانه')

    wrapper.unmount()
  })

  it('drags the FAB with mouse events, persists the bounded position, and ignores toggle clicks while dragging', async () => {
    vi.useFakeTimers()
    routeState.name = 'market'

    const BottomNav = (await import('./BottomNav.vue')).default
    const wrapper = mount(BottomNav, {
      attachTo: document.body,
      global: {
        stubs: {
          'router-link': {
            props: ['to'],
            template: '<a :href="typeof to === \'string\' ? to : to.path"><slot /></a>',
          },
        },
      },
    })

    await flushPromises()

    const fabButton = wrapper.get('.fab-btn')
    await fabButton.trigger('mousedown', { clientX: 10, clientY: 10 })
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 4000, clientY: 4000, bubbles: true, cancelable: true }))
    await nextTick()

    expect(wrapper.find('.fab-nav').exists()).toBe(false)

    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await nextTick()

    const storedPosition = JSON.parse(localStorage.getItem('fab_position') || '{}')
    expect(storedPosition.x).toBe(window.innerWidth - 44)
    expect(storedPosition.y).toBe(window.innerHeight - 44)
    expect(wrapper.get('.fab-container').attributes('style')).toContain(`left: ${window.innerWidth - 44}px;`)
    expect(wrapper.get('.fab-container').attributes('style')).toContain(`top: ${window.innerHeight - 44}px;`)

    await vi.advanceTimersByTimeAsync(60)
    await fabButton.trigger('click')
    expect(wrapper.find('.fab-nav').exists()).toBe(true)

    wrapper.unmount()
    vi.useRealTimers()
  })
})
