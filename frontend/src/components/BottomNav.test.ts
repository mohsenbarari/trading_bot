import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const apiFetchMock = vi.fn()
const routeState = { name: 'home' }

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
})