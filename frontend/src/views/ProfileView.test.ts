import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ProfileView from './ProfileView.vue'

const {
  apiFetchMock,
  routerPushMock,
} = vi.hoisted(() => ({
  apiFetchMock: vi.fn(),
  routerPushMock: vi.fn(),
}))

vi.mock('../utils/auth', () => ({
  apiFetch: apiFetchMock,
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: routerPushMock,
  }),
}))

function makeResponse(payload: unknown, ok = true) {
  return {
    ok,
    json: async () => payload,
  }
}

describe('ProfileView.vue', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    routerPushMock.mockReset()
    localStorage.clear()
  })

  it('loads the current user and passes the resolved profile props into PublicProfile', async () => {
    localStorage.setItem('auth_token', 'jwt-token')
    apiFetchMock.mockResolvedValue(makeResponse({
      id: 42,
      full_name: 'علی رضایی',
      account_name: 'ali',
    }))

    const wrapper = mount(ProfileView, {
      global: {
        stubs: {
          PublicProfile: {
            props: ['user', 'viewerUserId', 'apiBaseUrl', 'jwtToken'],
            template: `
              <div class="public-profile-stub">
                <span class="stub-user">{{ user.account_name }}</span>
                <span class="stub-viewer">{{ viewerUserId }}</span>
                <span class="stub-token">{{ jwtToken }}</span>
              </div>
            `,
          },
        },
      },
    })
    await flushPromises()

    expect(apiFetchMock).toHaveBeenCalledWith('/api/auth/me')
    expect(wrapper.find('.public-profile-stub').exists()).toBe(true)
    expect(wrapper.get('.stub-user').text()).toBe('ali')
    expect(wrapper.get('.stub-viewer').text()).toBe('42')
    expect(wrapper.get('.stub-token').text()).toBe('jwt-token')
  })

  it('routes navigate events from PublicProfile to the current router targets', async () => {
    localStorage.setItem('auth_token', 'jwt-token')
    apiFetchMock.mockResolvedValue(makeResponse({
      id: 77,
      full_name: 'مینا',
      account_name: 'mina',
    }))

    const wrapper = mount(ProfileView, {
      global: {
        stubs: {
          PublicProfile: {
            props: ['user'],
            emits: ['navigate'],
            template: `
              <div>
                <button class="go-settings" @click="$emit('navigate', 'settings')">settings</button>
                <button class="go-home" @click="$emit('navigate', 'home')">home</button>
                <button class="go-chat" @click="$emit('navigate', 'chat', { userId: 88, userName: 'peer-user' })">chat</button>
              </div>
            `,
          },
        },
      },
    })
    await flushPromises()

    await wrapper.get('.go-settings').trigger('click')
    await wrapper.get('.go-chat').trigger('click')
    await wrapper.get('.go-home').trigger('click')

    expect(routerPushMock).toHaveBeenNthCalledWith(1, { name: 'settings' })
    expect(routerPushMock).toHaveBeenNthCalledWith(2, {
      name: 'messenger',
      query: { user_id: '88', user_name: 'peer-user' },
    })
    expect(routerPushMock).toHaveBeenNthCalledWith(3, '/')
  })

  it('keeps the loading fallback and logs when loading the current profile fails', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    apiFetchMock.mockRejectedValue(new Error('profile fetch failed'))

    const wrapper = mount(ProfileView, {
      global: {
        stubs: {
          PublicProfile: {
            template: '<div class="public-profile-stub" />',
          },
        },
      },
    })
    await flushPromises()

    expect(wrapper.find('.public-profile-stub').exists()).toBe(false)
    expect(wrapper.find('.loading-container').exists()).toBe(true)
    expect(errorSpy).toHaveBeenCalledWith(expect.any(Error))

    errorSpy.mockRestore()
  })
})