import { flushPromises, mount } from '@vue/test-utils'
import { nextTick, reactive } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AdminView from './AdminView.vue'

const adminViewMocks = vi.hoisted(() => ({
  route: {
    name: 'admin' as string,
    params: {} as Record<string, string>,
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
    adminViewMocks.route.name = 'admin'
    adminViewMocks.route.params = reactive({}) as Record<string, string>
    adminViewMocks.route.query = reactive({}) as Record<string, string>
    adminViewMocks.routerPushMock.mockReset()
    adminViewMocks.routerReplaceMock.mockReset()
    adminViewMocks.pushBackStateMock.mockReset()
    adminViewMocks.popBackStateMock.mockReset()
    adminViewMocks.clearBackStackMock.mockReset()
    adminViewMocks.apiFetchMock.mockReset()
    localStorage.clear()
    localStorage.setItem('auth_token', 'admin-jwt-token')
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'مدیر ارشد' }))
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

    expect(wrapper.text()).toContain('مرکز مدیریت')
    await wrapper.get('.admin-panel-action.primary').trigger('click')
    await flushPromises()

    expect(adminViewMocks.pushBackStateMock).toHaveBeenCalledTimes(1)
    expect(adminViewMocks.routerPushMock).toHaveBeenCalledWith({ name: 'admin-invitations' })
    expect(wrapper.text()).toContain('ارسال دعوت‌نامه')
    expect(wrapper.find('.admin-subview-card.ui-section-card').exists()).toBe(true)
    expect(wrapper.get('.admin-subview-return').classes()).toContain('ui-icon-button')
    expect(wrapper.get('.admin-subview-return').attributes('aria-label')).toBe('بازگشت به پنل مدیریت')
    expect(wrapper.get('.create-invitation-stub').text()).toBe('admin-jwt-token')

    await wrapper.get('.admin-subview-return').trigger('click')
    await flushPromises()

    expect(adminViewMocks.popBackStateMock).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('بخش مورد نظر خود را انتخاب کنید')
  })

  it('routes from the users section into the admin user profile view', async () => {
    const wrapper = mountView()
    await flushPromises()

    const usersButton = wrapper.findAll('.admin-panel-action').find((button) => button.text().includes('مدیریت کاربران'))
    expect(usersButton).toBeTruthy()
    await usersButton!.trigger('click')
    await flushPromises()
    expect(adminViewMocks.routerPushMock).toHaveBeenCalledWith({ name: 'admin-users' })

    await wrapper.get('.user-manager-open-profile').trigger('click')
    await flushPromises()

    expect(adminViewMocks.pushBackStateMock).toHaveBeenCalledTimes(2)
    expect(adminViewMocks.popBackStateMock).toHaveBeenCalledTimes(1)
    expect(adminViewMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'admin-user-profile',
      params: { id: '77' },
      query: { account_name: 'user-77' },
    })
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

  it('opens admin sections directly from route names and params', async () => {
    adminViewMocks.route.name = 'admin-system'
    const systemWrapper = mountView()
    await flushPromises()

    expect(systemWrapper.text()).toContain('تنظیمات سیستم')
    expect(systemWrapper.find('.trading-settings-stub').exists()).toBe(true)

    adminViewMocks.route.name = 'admin-user-profile'
    adminViewMocks.route.params = {
      id: '91',
    }
    adminViewMocks.apiFetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ id: 91, account_name: 'route-param-user' }),
    })

    const profileWrapper = mountView()
    await flushPromises()

    expect(adminViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/users/91')
    expect(profileWrapper.text()).toContain('پروفایل کاربر')
    expect(profileWrapper.get('.user-profile-stub').text()).toBe('route-param-user')
  })

  it('keeps legacy section query deep links working for allowed admin tools', async () => {
    adminViewMocks.route.query = {
      section: 'create_channel',
    }

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('ساخت کانال')
    expect(wrapper.findComponent({ name: 'CreateChannelView' }).exists()).toBe(true)
  })

  it('renders the route-profile loading state when the profile section is awaiting route data', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as any
    vm.currentSection = 'user_profile'
    vm.isLoadingRouteUserProfile = true
    await flushPromises()

    expect(wrapper.text()).toContain('در حال بارگذاری پروفایل کاربر')
    expect(wrapper.find('.user-profile-stub').exists()).toBe(false)
  })

  it('falls back to the admin menu when the route profile request is rejected or not found', async () => {
    adminViewMocks.route.query = {
      section: 'user_profile',
      user_id: '52',
    }
    adminViewMocks.apiFetchMock.mockResolvedValueOnce({
      ok: false,
      json: async () => ({ message: 'missing' }),
    })

    const notFoundWrapper = mountView()
    await flushPromises()

    expect(notFoundWrapper.text()).toContain('بخش مورد نظر خود را انتخاب کنید')
    expect(adminViewMocks.routerReplaceMock).toHaveBeenCalledWith({ name: 'admin' })

    adminViewMocks.route.query = {
      section: 'user_profile',
      user_id: '53',
    }
    adminViewMocks.apiFetchMock.mockRejectedValueOnce(new Error('network failed'))

    const rejectedWrapper = mountView()
    await flushPromises()

    expect(rejectedWrapper.text()).toContain('بخش مورد نظر خود را انتخاب کنید')
    expect(adminViewMocks.routerReplaceMock).toHaveBeenCalledWith({ name: 'admin' })
  })

  it('ignores invalid public-profile payloads and invalid route profile ids', async () => {
    adminViewMocks.route.query = {
      section: 'user_profile',
      user_id: '0',
    }

    const wrapper = mountView()
    await flushPromises()

    expect(adminViewMocks.apiFetchMock).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('بخش مورد نظر خود را انتخاب کنید')

    const vm = wrapper.vm as any
    vm.handleOpenPublicProfile()
    vm.handleOpenPublicProfile({ id: 0, account_name: 'bad-user' })
    vm.handleOpenPublicProfile({ id: Number.NaN, account_name: 'bad-user' })

    expect(adminViewMocks.routerPushMock).not.toHaveBeenCalled()
  })

  it('replaces prior back state when switching sub-pages and clears route handoff on admin-panel navigation', async () => {
    adminViewMocks.route.query = {
      user_id: '44',
    }
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as any
    vm.handleNavigate('settings')
    await flushPromises()
    vm.handleNavigate('manage_commodities')
    await flushPromises()

    expect(adminViewMocks.pushBackStateMock).toHaveBeenCalledTimes(2)
    expect(adminViewMocks.popBackStateMock).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('مدیریت کالاها')

    vm.handleNavigate('admin_panel')
    await flushPromises()

    expect(wrapper.text()).toContain('بخش مورد نظر خود را انتخاب کنید')
    expect(adminViewMocks.popBackStateMock).toHaveBeenCalledTimes(2)
    expect(adminViewMocks.routerReplaceMock).toHaveBeenCalledWith({ name: 'admin' })
  })

  it('clears the custom back stack on unmount', async () => {
    const wrapper = mountView()
    await flushPromises()

    wrapper.unmount()

    expect(adminViewMocks.clearBackStackMock).toHaveBeenCalledTimes(1)
  })

  it('blocks system settings navigation for middle managers', async () => {
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'مدیر میانی' }))
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as any
    vm.handleNavigate('settings')
    await flushPromises()

    expect(wrapper.text()).toContain('بخش مورد نظر خود را انتخاب کنید')
    expect(wrapper.find('.trading-settings-stub').exists()).toBe(false)
  })

  it('blocks super-admin only route names for middle managers', async () => {
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'مدیر میانی' }))
    adminViewMocks.route.name = 'admin-channels'

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('بخش مورد نظر خود را انتخاب کنید')
    expect(wrapper.findComponent({ name: 'CreateChannelView' }).exists()).toBe(false)
  })

  it('blocks legacy section query deep links that are not allowed for middle managers', async () => {
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'مدیر میانی' }))
    adminViewMocks.route.query = {
      section: 'create_channel',
    }

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('بخش مورد نظر خود را انتخاب کنید')
    expect(wrapper.findComponent({ name: 'CreateChannelView' }).exists()).toBe(false)
  })

  it('reacts to route query changes after mount and executes stored back callbacks', async () => {
    adminViewMocks.apiFetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ id: 92, account_name: 'route-reactive-user' }),
    })

    const wrapper = mountView()
    await flushPromises()

    adminViewMocks.route.query.section = 'settings'
    adminViewMocks.route.query.user_id = '92'
    await nextTick()
    await flushPromises()
    expect(adminViewMocks.apiFetchMock).not.toHaveBeenCalled()

    adminViewMocks.route.query.section = 'user_profile'
    await nextTick()
    await flushPromises()
    expect(adminViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/users/92')
    expect(wrapper.get('.user-profile-stub').text()).toBe('route-reactive-user')

    const vm = wrapper.vm as any
    vm.handleNavigate('settings')
    await flushPromises()
    const settingsBack = adminViewMocks.pushBackStateMock.mock.lastCall?.[0]
    expect(typeof settingsBack).toBe('function')
    settingsBack()
    await flushPromises()
    expect(wrapper.text()).toContain('بخش مورد نظر خود را انتخاب کنید')

    const usersButton = wrapper.findAll('.admin-panel-action').find((button) => button.text().includes('مدیریت کاربران'))
    expect(usersButton).toBeTruthy()
    await usersButton!.trigger('click')
    await flushPromises()
    await wrapper.get('.user-manager-open-profile').trigger('click')
    await flushPromises()
    const profileBack = adminViewMocks.pushBackStateMock.mock.lastCall?.[0]
    expect(typeof profileBack).toBe('function')
    profileBack()
    await flushPromises()
    expect(wrapper.text()).toContain('بخش مورد نظر خود را انتخاب کنید')
  })

  it('keeps legacy system_settings query deep links mapped to the system route', async () => {
    adminViewMocks.route.query = {
      section: 'system_settings',
    }

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('تنظیمات سیستم')
    expect(wrapper.find('.trading-settings-stub').exists()).toBe(true)
  })
})
