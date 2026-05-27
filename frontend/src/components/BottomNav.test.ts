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
})