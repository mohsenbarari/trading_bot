import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { MESSENGER_UI_VERSION_STORAGE_KEY } from '../utils/messengerRefactor'
import MessengerView from './MessengerView.vue'

const {
  apiFetchMock,
  clearBackStackMock,
  popBackStateMock,
  pushBackStateMock,
  routerBackMock,
  routerPushMock,
  routerReplaceMock,
  routeState,
} = vi.hoisted(() => ({
  apiFetchMock: vi.fn(),
  clearBackStackMock: vi.fn(),
  popBackStateMock: vi.fn(),
  pushBackStateMock: vi.fn(),
  routerBackMock: vi.fn(),
  routerPushMock: vi.fn(),
  routerReplaceMock: vi.fn(),
  routeState: {
    query: {
      user_id: '18',
      user_name: 'peer-user',
    },
  },
}))

vi.mock('../utils/auth', () => ({
  apiFetch: apiFetchMock,
}))

vi.mock('../composables/useBackButton', () => ({
  clearBackStack: clearBackStackMock,
  popBackState: popBackStateMock,
  pushBackState: pushBackStateMock,
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({
    back: routerBackMock,
    push: routerPushMock,
    replace: routerReplaceMock,
  }),
  useRoute: () => routeState,
}))

vi.mock('../components/ChatView.vue', () => ({
  default: {
    props: ['currentUserId', 'currentUserRole', 'currentUserIsAccountant', 'currentUserIsCustomer', 'targetUserId', 'targetUserName'],
    template: `
      <div class="chat-view-stub">
        <span class="stub-user-id">{{ currentUserId }}</span>
        <span class="stub-role">{{ currentUserRole }}</span>
        <span class="stub-accountant">{{ String(currentUserIsAccountant) }}</span>
        <span class="stub-customer">{{ String(currentUserIsCustomer) }}</span>
        <span class="stub-target-id">{{ targetUserId }}</span>
        <span class="stub-target-name">{{ targetUserName }}</span>
        <button class="emit-public-profile" @click="$emit('navigate', 'public_profile', { id: 88, account_name: 'owner-88' })">public-profile</button>
        <button class="emit-profile-user-id" @click="$emit('navigate', 'profile', { user_id: 77 })">profile-user-id</button>
        <button class="emit-invalid-navigate" @click="$emit('navigate', 'dashboard', { id: 11 })">invalid-navigate</button>
        <button class="emit-back" @click="$emit('back')">back</button>
      </div>
    `,
  },
}))

function makeResponse(payload: unknown, ok = true) {
  return {
    ok,
    json: async () => payload,
  }
}

describe('MessengerView.vue', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    clearBackStackMock.mockReset()
    popBackStateMock.mockReset()
    pushBackStateMock.mockReset()
    routerBackMock.mockReset()
    routerPushMock.mockReset()
    routerReplaceMock.mockReset()
    vi.unstubAllEnvs()
    localStorage.clear()
    localStorage.setItem('auth_token', 'jwt-token')
    routeState.query = {
      user_id: '18',
      user_name: 'peer-user',
    }
    window.history.replaceState({ back: '/' }, '', '/chat')
  })

  it('passes the authenticated accountant state into ChatView', async () => {
    apiFetchMock.mockResolvedValue(makeResponse({
      id: 42,
      role: 'عادی',
      is_accountant: true,
      is_customer: false,
    }))

    const wrapper = mount(MessengerView)
    await flushPromises()

    expect(apiFetchMock).toHaveBeenCalledWith('/api/auth/me')
    expect(wrapper.get('.messenger-page').attributes('data-messenger-ui-version')).toBe('legacy')
    expect(wrapper.get('.messenger-page').attributes('data-messenger-rollout-mode')).toBe('legacy-default')
    expect(wrapper.find('.chat-view-stub').exists()).toBe(true)
    expect(wrapper.find('[data-testid="messenger-refactor-shell"]').exists()).toBe(false)
    expect(wrapper.get('.stub-user-id').text()).toBe('42')
    expect(wrapper.get('.stub-role').text()).toBe('عادی')
    expect(wrapper.get('.stub-accountant').text()).toBe('true')
    expect(wrapper.get('.stub-customer').text()).toBe('false')
    expect(wrapper.get('.stub-target-id').text()).toBe('18')
    expect(wrapper.get('.stub-target-name').text()).toBe('peer-user')
  })

  it('mounts the reversible refactor shell only when explicitly enabled', async () => {
    localStorage.setItem(MESSENGER_UI_VERSION_STORAGE_KEY, 'refactor')
    apiFetchMock.mockResolvedValue(makeResponse({
      id: 42,
      role: 'عادی',
      is_accountant: false,
      is_customer: false,
    }))

    const wrapper = mount(MessengerView)
    await flushPromises()

    expect(wrapper.get('.messenger-page').attributes('data-messenger-ui-version')).toBe('refactor')
    expect(wrapper.get('.messenger-page').attributes('data-messenger-rollout-mode')).toBe('refactor-preview')
    expect(wrapper.find('.chat-view-stub').exists()).toBe(false)
    expect(wrapper.find('[data-testid="messenger-refactor-shell"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('peer-user')
    expect(pushBackStateMock).not.toHaveBeenCalled()

    await wrapper.get('.shell-back').trigger('click')
    expect(routerBackMock).toHaveBeenCalledTimes(1)
  })

  it('keeps direct-target props undefined and does not mount ChatView when auth me is not ok', async () => {
    routeState.query = {} as any
    apiFetchMock.mockResolvedValue(makeResponse({ detail: 'unauthorized' }, false))

    const wrapper = mount(MessengerView)
    expect(wrapper.find('.loading-spinner').exists()).toBe(true)

    await flushPromises()

    expect(wrapper.find('.loading-spinner').exists()).toBe(false)
    expect(wrapper.find('.chat-view-stub').exists()).toBe(false)
  })

  it('logs fetch failures and still clears the loading state', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    apiFetchMock.mockRejectedValue(new Error('network exploded'))

    const wrapper = mount(MessengerView)
    await flushPromises()

    expect(errorSpy).toHaveBeenCalledWith(expect.any(Error))
    expect(wrapper.find('.loading-spinner').exists()).toBe(false)
    expect(wrapper.find('.chat-view-stub').exists()).toBe(false)
  })

  it('routes ChatView navigate and back events through the messenger view handlers', async () => {
    apiFetchMock.mockResolvedValue(makeResponse({
      id: 42,
      role: 'عادی',
      is_accountant: false,
      is_customer: true,
    }))

    const wrapper = mount(MessengerView)
    await flushPromises()

    await wrapper.get('.emit-public-profile').trigger('click')
    expect(routerPushMock).toHaveBeenNthCalledWith(1, {
      name: 'public-profile',
      params: { id: '88' },
      query: { account_name: 'owner-88' },
    })

    await wrapper.get('.emit-invalid-navigate').trigger('click')
    expect(routerPushMock).toHaveBeenCalledTimes(1)

    await wrapper.get('.emit-profile-user-id').trigger('click')
    expect(routerPushMock).toHaveBeenNthCalledWith(2, {
      name: 'public-profile',
      params: { id: '77' },
      query: undefined,
    })

    await wrapper.get('.emit-back').trigger('click')
    expect(routerBackMock).toHaveBeenCalledTimes(1)
  })

  it('installs a route-level base back state when messenger is opened without dashboard behind it', async () => {
    window.history.replaceState({ back: '/users/88' }, '', '/chat?user_id=18')
    apiFetchMock.mockResolvedValue(makeResponse({
      id: 42,
      role: 'عادی',
      is_accountant: false,
      is_customer: false,
    }))

    const wrapper = mount(MessengerView)
    await flushPromises()

    expect(pushBackStateMock).toHaveBeenCalledTimes(1)
    const baseBackCallback = pushBackStateMock.mock.calls[0][0]
    expect(baseBackCallback).toBeTypeOf('function')

    baseBackCallback()
    expect(routerReplaceMock).toHaveBeenCalledWith('/')

    routerReplaceMock.mockClear()
    await wrapper.get('.emit-back').trigger('click')
    expect(popBackStateMock).not.toHaveBeenCalled()
    expect(routerReplaceMock).toHaveBeenCalledWith('/')
  })
})
