import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import MessengerView from './MessengerView.vue'

const {
  apiFetchMock,
  routerPushMock,
  routeState,
} = vi.hoisted(() => ({
  apiFetchMock: vi.fn(),
  routerPushMock: vi.fn(),
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

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: routerPushMock,
  }),
  useRoute: () => routeState,
}))

vi.mock('../components/ChatView.vue', () => ({
  default: {
    props: ['currentUserId', 'currentUserRole', 'currentUserIsAccountant', 'targetUserId', 'targetUserName'],
    template: `
      <div class="chat-view-stub">
        <span class="stub-user-id">{{ currentUserId }}</span>
        <span class="stub-role">{{ currentUserRole }}</span>
        <span class="stub-accountant">{{ String(currentUserIsAccountant) }}</span>
        <span class="stub-target-id">{{ targetUserId }}</span>
        <span class="stub-target-name">{{ targetUserName }}</span>
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
    routerPushMock.mockReset()
    localStorage.clear()
    localStorage.setItem('auth_token', 'jwt-token')
    routeState.query = {
      user_id: '18',
      user_name: 'peer-user',
    }
  })

  it('passes the authenticated accountant state into ChatView', async () => {
    apiFetchMock.mockResolvedValue(makeResponse({
      id: 42,
      role: 'عادی',
      is_accountant: true,
    }))

    const wrapper = mount(MessengerView)
    await flushPromises()

    expect(apiFetchMock).toHaveBeenCalledWith('/api/auth/me')
    expect(wrapper.find('.chat-view-stub').exists()).toBe(true)
    expect(wrapper.get('.stub-user-id').text()).toBe('42')
    expect(wrapper.get('.stub-role').text()).toBe('عادی')
    expect(wrapper.get('.stub-accountant').text()).toBe('true')
    expect(wrapper.get('.stub-target-id').text()).toBe('18')
    expect(wrapper.get('.stub-target-name').text()).toBe('peer-user')
  })
})