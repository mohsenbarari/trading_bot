import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SystemRecoveryView from './SystemRecoveryView.vue'

const routeState = vi.hoisted(() => ({
  query: {} as Record<string, unknown>,
  params: { pathMatch: ['__system', 'recovery'] } as Record<string, unknown>,
}))
const forceLogoutMock = vi.hoisted(() => vi.fn())

vi.mock('vue-router', () => ({
  useRoute: () => routeState,
}))

vi.mock('../utils/auth', () => ({
  forceLogout: forceLogoutMock,
}))

function mountView() {
  return mount(SystemRecoveryView, {
    global: {
      stubs: {
        RouterLink: {
          props: ['to'],
          template: '<a :href="to"><slot /></a>',
        },
      },
    },
  })
}

describe('SystemRecoveryView', () => {
  beforeEach(() => {
    routeState.query = {}
    routeState.params = { pathMatch: ['__system', 'recovery'] }
    forceLogoutMock.mockReset()
  })

  it.each([
    ['not-found', 'این صفحه پیدا نشد'],
    ['forbidden', 'دسترسی به این بخش مجاز نیست'],
    ['deep-link-failure', 'باز کردن این صفحه ممکن نشد'],
  ])('renders the owned %s outcome without leaking a failed target', (outcome, title) => {
    routeState.query = { outcome, target: '/register?registration_token=secret' }

    const wrapper = mountView()

    expect(wrapper.get('[data-test="route-system-recovery"]').attributes('data-outcome')).toBe(
      outcome,
    )
    expect(wrapper.get('h1').text()).toBe(title)
    expect(wrapper.text()).not.toContain('registration_token')
    expect(wrapper.text()).not.toContain('secret')
    expect(wrapper.get('a').attributes('href')).toBe('/')
  })

  it('fails closed to not-found for an unknown outcome', () => {
    routeState.query = { outcome: 'backend-detail' }

    const wrapper = mountView()

    expect(wrapper.attributes('data-outcome')).toBe('not-found')
    expect(wrapper.text()).toContain('این صفحه پیدا نشد')
    expect(wrapper.text()).not.toContain('backend-detail')
  })

  it('does not trust an outcome query on an ordinary unknown path', () => {
    routeState.params = { pathMatch: ['some', 'unknown', 'page'] }
    routeState.query = { outcome: 'forbidden' }

    const wrapper = mountView()

    expect(wrapper.attributes('data-outcome')).toBe('not-found')
    expect(wrapper.text()).toContain('این صفحه پیدا نشد')
    expect(wrapper.text()).not.toContain('دسترسی به این بخش مجاز نیست')
  })

  it('offers a loop-free authentication reset for an unavailable deep link', async () => {
    routeState.query = { outcome: 'deep-link-failure' }
    const wrapper = mountView()

    await wrapper.get('[data-test="restart-authentication"]').trigger('click')

    expect(forceLogoutMock).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).not.toMatch(/target|registration_token|secret/i)
  })
})
