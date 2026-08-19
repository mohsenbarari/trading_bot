import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ProfileWorkspaceView from './ProfileWorkspaceView.vue'

const {
  apiFetchMock,
  routeRequestJsonMock,
  routeMock,
  routerPushMock,
  routerReplaceMock,
  routerBackMock,
} = vi.hoisted(() => {
  const { reactive } = require('vue') as typeof import('vue')
  return {
    apiFetchMock: vi.fn(),
    routeRequestJsonMock: vi.fn(),
    routeMock: reactive({
      name: 'profile' as string,
      params: {} as Record<string, string>,
      query: {} as Record<string, string>,
    }),
    routerPushMock: vi.fn(),
    routerReplaceMock: vi.fn(),
    routerBackMock: vi.fn(),
  }
})

vi.mock('../utils/auth', () => ({
  apiFetch: apiFetchMock,
}))

vi.mock('../utils/routeRequest', () => ({
  routeRequestJson: routeRequestJsonMock,
}))

vi.mock('vue-router', () => ({
  useRoute: () => routeMock,
  useRouter: () => ({
    push: routerPushMock,
    replace: routerReplaceMock,
    back: routerBackMock,
  }),
}))

describe('ProfileWorkspaceView.vue', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    routeRequestJsonMock.mockReset()
    routerPushMock.mockReset()
    routerReplaceMock.mockReset()
    routerBackMock.mockReset()
    routeMock.name = 'profile'
    routeMock.params = {}
    routeMock.query = {}
    localStorage.clear()
    localStorage.setItem('auth_token', 'jwt-token')
  })

  it('keeps one PublicProfile root when the same workspace handles self and public routes', async () => {
    routeRequestJsonMock.mockResolvedValue({
      id: 42,
      account_name: 'self-user',
    })

    const wrapper = mount(ProfileWorkspaceView, {
      global: {
        stubs: {
          PublicProfile: {
            name: 'PublicProfile',
            props: ['user', 'viewerUserId'],
            template: '<div class="public-profile-stub">{{ user.id }}</div>',
          },
        },
      },
    })
    await flushPromises()

    const first = wrapper.getComponent({ name: 'PublicProfile' })
    expect(first.props('user')).toMatchObject({ id: 42 })
    expect(wrapper.get('[data-test="profile-workspace-root"]').classes()).toContain('profile-view')

    routeMock.name = 'public-profile'
    routeMock.params = { id: '99' }
    await flushPromises()

    expect(wrapper.findComponent({ name: 'PublicProfile' }).exists()).toBe(true)
    expect(wrapper.getComponent({ name: 'PublicProfile' }).props('user')).toMatchObject({ id: 99 })
    expect(wrapper.get('[data-test="profile-workspace-root"]').classes()).toContain('public-profile-view')
  })
})
