import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const publicProfileViewMocks = vi.hoisted(() => ({
  route: {
    params: { id: '0' },
    query: {} as Record<string, string>,
  },
  routerPushMock: vi.fn(),
  routerBackMock: vi.fn(),
  apiFetchMock: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => publicProfileViewMocks.route,
  useRouter: () => ({
    push: publicProfileViewMocks.routerPushMock,
    back: publicProfileViewMocks.routerBackMock,
  }),
}))

vi.mock('../utils/auth', () => ({
  apiFetch: publicProfileViewMocks.apiFetchMock,
}))

describe('PublicProfileView.vue', () => {
  beforeEach(() => {
    publicProfileViewMocks.route.params = { id: '44' }
    publicProfileViewMocks.route.query = {}
    publicProfileViewMocks.routerPushMock.mockReset()
    publicProfileViewMocks.routerBackMock.mockReset()
    publicProfileViewMocks.apiFetchMock.mockReset()
    localStorage.setItem('auth_token', 'header.eyJzdWIiOiI3NyJ9.signature')
  })

  it('passes highlight accountant query state into PublicProfile', async () => {
    publicProfileViewMocks.route.query = {
      account_name: 'owner-44',
      highlight_accountant_user_id: '91',
      highlight_accountant_relation_display_name: 'حسابدار فروش',
    }

    const PublicProfileView = (await import('./PublicProfileView.vue')).default
    const wrapper = mount(PublicProfileView, {
      global: {
        stubs: {
          PublicProfile: {
            name: 'PublicProfile',
            template: '<div class="public-profile-stub"></div>',
            props: ['user', 'viewerUserId', 'apiBaseUrl', 'jwtToken', 'highlightAccountantUserId', 'highlightAccountantRelationDisplayName'],
          },
        },
      },
    })

    await flushPromises()

    const stub = wrapper.getComponent({ name: 'PublicProfile' })
    expect(stub.props('user')).toEqual({ id: 44, account_name: 'owner-44' })
    expect(stub.props('viewerUserId')).toBe(77)
    expect(stub.props('highlightAccountantUserId')).toBe(91)
    expect(stub.props('highlightAccountantRelationDisplayName')).toBe('حسابدار فروش')
  })

  it('loads viewer id from /api/auth/me when the token does not contain a valid numeric subject', async () => {
    localStorage.setItem('auth_token', 'bad.token.value')
    publicProfileViewMocks.apiFetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ id: 88 }),
    })

    const PublicProfileView = (await import('./PublicProfileView.vue')).default
    const wrapper = mount(PublicProfileView, {
      global: {
        stubs: {
          PublicProfile: {
            name: 'PublicProfile',
            template: '<div class="public-profile-stub"></div>',
            props: ['viewerUserId'],
          },
        },
      },
    })

    await flushPromises()

    const stub = wrapper.getComponent({ name: 'PublicProfile' })
    expect(publicProfileViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/auth/me')
    expect(stub.props('viewerUserId')).toBe(88)
  })

  it('passes a null user when the route id is invalid and ignores invalid highlight query values', async () => {
    publicProfileViewMocks.route.params = { id: 'not-a-number' }
    publicProfileViewMocks.route.query = {
      account_name: 'ignored',
      highlight_accountant_user_id: 'bad-value',
      highlight_accountant_relation_display_name: 'ignored-title',
    }

    const PublicProfileView = (await import('./PublicProfileView.vue')).default
    const wrapper = mount(PublicProfileView, {
      global: {
        stubs: {
          PublicProfile: {
            name: 'PublicProfile',
            template: '<div class="public-profile-stub"></div>',
            props: ['user', 'highlightAccountantUserId', 'highlightAccountantRelationDisplayName'],
          },
        },
      },
    })

    await flushPromises()

    const stub = wrapper.getComponent({ name: 'PublicProfile' })
    expect(stub.props('user')).toBeNull()
    expect(stub.props('highlightAccountantUserId')).toBeNull()
    expect(stub.props('highlightAccountantRelationDisplayName')).toBe('ignored-title')
  })

  it('routes chat navigation requests through the messenger query contract', async () => {
    const PublicProfileView = (await import('./PublicProfileView.vue')).default
    const wrapper = mount(PublicProfileView, {
      global: {
        stubs: {
          PublicProfile: {
            name: 'PublicProfile',
            template: '<button class="chat-nav" @click="$emit(\'navigate\', \'chat\', { userId: 55, userName: \'target55\' })">chat</button>',
          },
        },
      },
    })

    await wrapper.get('.chat-nav').trigger('click')

    expect(publicProfileViewMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'messenger',
      query: {
        user_id: '55',
        user_name: 'target55',
      },
    })
  })

  it('routes owner workspace navigation requests to the operations workspaces', async () => {
    const PublicProfileView = (await import('./PublicProfileView.vue')).default
    const wrapper = mount(PublicProfileView, {
      global: {
        stubs: {
          PublicProfile: {
            name: 'PublicProfile',
            template: `
              <div>
                <button class="customers-nav" @click="$emit('navigate', 'operations_customers')">customers</button>
                <button class="accountants-nav" @click="$emit('navigate', 'operations_accountants')">accountants</button>
              </div>
            `,
          },
        },
      },
    })

    await wrapper.get('.customers-nav').trigger('click')
    await wrapper.get('.accountants-nav').trigger('click')

    expect(publicProfileViewMocks.routerPushMock).toHaveBeenNthCalledWith(1, { name: 'operations-customers' })
    expect(publicProfileViewMocks.routerPushMock).toHaveBeenNthCalledWith(2, { name: 'operations-accountants' })
  })

  it('routes admin settings navigation requests through the admin user-profile route', async () => {
    const PublicProfileView = (await import('./PublicProfileView.vue')).default
    const wrapper = mount(PublicProfileView, {
      global: {
        stubs: {
          PublicProfile: {
            name: 'PublicProfile',
            template: '<button class="settings-nav" @click="$emit(\'navigate\', \'settings\', { userId: 66, userName: \'managed66\' })">settings</button>',
          },
        },
      },
    })

    await wrapper.get('.settings-nav').trigger('click')

    expect(publicProfileViewMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'admin-user-profile',
      params: { id: '66' },
      query: {
        account_name: 'managed66',
      },
    })
  })

  it('routes nested public-profile navigation requests with accountant highlight query metadata', async () => {
    const PublicProfileView = (await import('./PublicProfileView.vue')).default
    const wrapper = mount(PublicProfileView, {
      global: {
        stubs: {
          PublicProfile: {
            name: 'PublicProfile',
            template: '<button class="profile-nav" @click="$emit(\'navigate\', \'public_profile\', { id: 71, account_name: \'owner71\', highlight_accountant_user_id: 19, highlight_accountant_relation_display_name: \'حسابدار فروش\' })">profile</button>',
          },
        },
      },
    })

    await wrapper.get('.profile-nav').trigger('click')

    expect(publicProfileViewMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'public-profile',
      params: { id: '71' },
      query: {
        account_name: 'owner71',
        highlight_accountant_user_id: '19',
        highlight_accountant_relation_display_name: 'حسابدار فروش',
      },
    })
  })

  it('uses router.back for non-chat navigation when browser history has a back entry', async () => {
    window.history.replaceState({ back: '/chat' }, '', '/users/44')

    const PublicProfileView = (await import('./PublicProfileView.vue')).default
    const wrapper = mount(PublicProfileView, {
      global: {
        stubs: {
          PublicProfile: {
            name: 'PublicProfile',
            template: '<button class="go-home" @click="$emit(\'navigate\', \'home\')">home</button>',
          },
        },
      },
    })

    await wrapper.get('.go-home').trigger('click')

    expect(publicProfileViewMocks.routerBackMock).toHaveBeenCalledTimes(1)
    expect(publicProfileViewMocks.routerPushMock).not.toHaveBeenCalled()
  })

  it('falls back to pushing the dashboard when there is no browser back entry', async () => {
    window.history.replaceState({}, '', '/users/44')

    const PublicProfileView = (await import('./PublicProfileView.vue')).default
    const wrapper = mount(PublicProfileView, {
      global: {
        stubs: {
          PublicProfile: {
            name: 'PublicProfile',
            template: '<button class="go-home" @click="$emit(\'navigate\', \'home\')">home</button>',
          },
        },
      },
    })

    await wrapper.get('.go-home').trigger('click')

    expect(publicProfileViewMocks.routerBackMock).not.toHaveBeenCalled()
    expect(publicProfileViewMocks.routerPushMock).toHaveBeenCalledWith('/')
  })

  it('keeps viewerUserId null when /api/auth/me is non-ok or throws', async () => {
    localStorage.setItem('auth_token', 'broken.token.value')
    publicProfileViewMocks.apiFetchMock.mockResolvedValueOnce({
      ok: false,
      json: async () => ({ id: 99 }),
    })

    const PublicProfileView = (await import('./PublicProfileView.vue')).default
    const wrapper = mount(PublicProfileView, {
      global: {
        stubs: {
          PublicProfile: {
            name: 'PublicProfile',
            template: '<div class="public-profile-stub"></div>',
            props: ['viewerUserId'],
          },
        },
      },
    })

    await flushPromises()
    expect(wrapper.getComponent({ name: 'PublicProfile' }).props('viewerUserId')).toBeNull()

    publicProfileViewMocks.apiFetchMock.mockReset()
    publicProfileViewMocks.apiFetchMock.mockRejectedValueOnce(new Error('network'))

    const secondWrapper = mount(PublicProfileView, {
      global: {
        stubs: {
          PublicProfile: {
            name: 'PublicProfile',
            template: '<div class="public-profile-stub"></div>',
            props: ['viewerUserId'],
          },
        },
      },
    })

    await flushPromises()
    expect(secondWrapper.getComponent({ name: 'PublicProfile' }).props('viewerUserId')).toBeNull()
  })
})
