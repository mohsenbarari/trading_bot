import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AdminView from './AdminView.vue'

const adminViewMocks = vi.hoisted(() => ({
  route: {
    query: {} as Record<string, string>,
  },
  routerPushMock: vi.fn(),
  routerReplaceMock: vi.fn(),
  pushBackStateMock: vi.fn(),
  popBackStateMock: vi.fn(),
  clearBackStackMock: vi.fn(),
  apiFetchMock: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => adminViewMocks.route,
  useRouter: () => ({
    push: adminViewMocks.routerPushMock,
    replace: adminViewMocks.routerReplaceMock,
  }),
}))

vi.mock('../utils/auth', () => ({
  apiFetch: adminViewMocks.apiFetchMock,
}))

vi.mock('../composables/useBackButton', () => ({
  pushBackState: adminViewMocks.pushBackStateMock,
  popBackState: adminViewMocks.popBackStateMock,
  clearBackStack: adminViewMocks.clearBackStackMock,
}))

describe('AdminView.vue', () => {
  beforeEach(() => {
    adminViewMocks.route.query = {}
    adminViewMocks.routerPushMock.mockReset()
    adminViewMocks.routerReplaceMock.mockReset()
    adminViewMocks.pushBackStateMock.mockReset()
    adminViewMocks.popBackStateMock.mockReset()
    adminViewMocks.clearBackStackMock.mockReset()
    adminViewMocks.apiFetchMock.mockReset()
    localStorage.clear()
    localStorage.setItem('auth_token', 'admin-jwt-token')
  })

  function mountView() {
    return mount(AdminView, {
      global: {
        stubs: {
          CreateInvitationView: {
            name: 'CreateInvitationView',
            props: ['apiBaseUrl', 'jwtToken'],
            template: '<div class="create-invitation-stub">{{ jwtToken }}</div>',
          },
          CommodityManager: {
            name: 'CommodityManager',
            props: ['apiBaseUrl', 'jwtToken'],
            template: '<div class="commodity-manager-stub">commodity</div>',
          },
          TradingSettings: {
            name: 'TradingSettings',
            props: ['apiBaseUrl', 'jwtToken'],
            template: '<div class="trading-settings-stub">settings</div>',
          },
          UserManager: {
            name: 'UserManager',
            props: ['apiBaseUrl', 'jwtToken'],
            emits: ['navigate'],
            template: '<button class="user-manager-open-profile" @click="$emit(\'navigate\', \'user_profile\', { id: 77, account_name: \'user-77\' })">open user profile</button>',
          },
          UserProfile: {
            name: 'UserProfile',
            props: ['user', 'isAdminView', 'apiBaseUrl', 'jwtToken'],
            template: '<div class="user-profile-stub">{{ user.account_name }}</div>',
          },
          CreateChannelView: {
            name: 'CreateChannelView',
            props: ['apiBaseUrl', 'jwtToken'],
            emits: ['open-public-profile'],
            template: '<button class="channel-open-public-profile" @click="$emit(\'open-public-profile\', { id: 88, account_name: \'owner-88\' })">open public profile</button>',
          },
        },
      },
    })
  }

  it('renders the real admin panel menu and opens the invitation section with the stored JWT token', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('پنل مدیریت')
    await wrapper.get('.admin-action-btn.primary').trigger('click')
    await flushPromises()

    expect(adminViewMocks.pushBackStateMock).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('ارسال دعوت‌نامه')
    expect(wrapper.get('.create-invitation-stub').text()).toBe('admin-jwt-token')

    await wrapper.get('.back-button').trigger('click')
    await flushPromises()

    expect(adminViewMocks.popBackStateMock).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('لطفاً بخش مورد نظر خود را انتخاب کنید:')
  })

  it('routes from the users section into the admin user profile view', async () => {
    const wrapper = mountView()
    await flushPromises()

    await wrapper.findAll('.admin-action-btn.secondary')[1]!.trigger('click')
    await flushPromises()
    await wrapper.get('.user-manager-open-profile').trigger('click')
    await flushPromises()

    expect(adminViewMocks.pushBackStateMock).toHaveBeenCalledTimes(2)
    expect(adminViewMocks.popBackStateMock).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('پروفایل کاربر')
    expect(wrapper.get('.user-profile-stub').text()).toBe('user-77')
  })

  it('opens the public profile route from the channel manager payload', async () => {
    const wrapper = mountView()
    await flushPromises()

    wrapper.getComponent({ name: 'AdminPanel' }).vm.$emit('navigate', 'create_channel')
    await flushPromises()
    await wrapper.get('.channel-open-public-profile').trigger('click')

    expect(adminViewMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'public-profile',
      params: { id: '88' },
      query: { account_name: 'owner-88' },
    })
  })

  it('loads the admin user profile directly from the route query handoff', async () => {
    adminViewMocks.route.query = {
      section: 'user_profile',
      user_id: '91',
      account_name: 'route-user',
    }
    adminViewMocks.apiFetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ id: 91, account_name: 'route-user' }),
    })

    const wrapper = mountView()
    await flushPromises()

    expect(adminViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/users/91')
    expect(wrapper.text()).toContain('پروفایل کاربر')
    expect(wrapper.get('.user-profile-stub').text()).toBe('route-user')
  })

  it('clears the custom back stack on unmount', async () => {
    const wrapper = mountView()
    await flushPromises()

    wrapper.unmount()

    expect(adminViewMocks.clearBackStackMock).toHaveBeenCalledTimes(1)
  })
})