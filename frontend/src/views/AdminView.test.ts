import { flushPromises, mount } from '@vue/test-utils'
import { nextTick, reactive } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AdminView from './AdminView.vue'

function responseOf(payload: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => 'application/json' },
    json: async () => payload,
  }
}

const adminViewMocks = vi.hoisted(() => ({
  route: {
    name: 'admin' as string,
    path: '/admin',
    params: {} as Record<string, string>,
    query: {} as Record<string, string>,
  },
  routerPushMock: vi.fn(),
  routerReplaceMock: vi.fn(),
  pushBackStateMock: vi.fn(),
  popBackStateMock: vi.fn(),
  clearBackStackMock: vi.fn(),
  apiFetchMock: vi.fn(),
  userManagerMountedMock: vi.fn(),
}))

type AdminViewTestVm = {
  currentSection: string
  isLoadingRouteUserProfile: boolean
  handleNavigate: (section: string, data?: unknown) => void
  syncRouteToSection: () => void
  handleOpenPublicProfile: (payload?: { id?: number; account_name?: string }) => void
}

function getAdminViewVm(wrapper: { vm: unknown }): AdminViewTestVm {
  return wrapper.vm as AdminViewTestVm
}

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
    adminViewMocks.route = reactive({
      name: 'admin' as string,
      path: '/admin',
      params: {} as Record<string, string>,
      query: {} as Record<string, string>,
    })
    adminViewMocks.routerPushMock.mockReset()
    adminViewMocks.routerReplaceMock.mockReset()
    adminViewMocks.routerReplaceMock.mockImplementation((location: unknown) => {
      const target = location as { name?: unknown }
      if (target?.name !== 'admin') return

      adminViewMocks.route.name = 'admin'
      adminViewMocks.route.path = '/admin'
      adminViewMocks.route.params = reactive({}) as Record<string, string>
      adminViewMocks.route.query = reactive({}) as Record<string, string>
    })
    adminViewMocks.pushBackStateMock.mockReset()
    adminViewMocks.popBackStateMock.mockReset()
    adminViewMocks.clearBackStackMock.mockReset()
    adminViewMocks.apiFetchMock.mockReset()
    adminViewMocks.userManagerMountedMock.mockReset()
    localStorage.clear()
    localStorage.setItem('auth_token', 'admin-jwt-token')
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'مدیر ارشد' }))
  })

  afterEach(() => {
    document.querySelector('.app-route-scroll')?.remove()
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
          AdminMessagesView: {
            name: 'AdminMessagesView',
            template: '<div class="admin-messages-stub">messages</div>',
          },
          UserManager: {
            name: 'UserManager',
            props: ['apiBaseUrl', 'jwtToken', 'query'],
            emits: ['navigate', 'query-change', 'loaded', 'settled'],
            mounted() {
              adminViewMocks.userManagerMountedMock()
            },
            template:
              "<div class=\"user-manager-stub\"><span class=\"user-manager-query\">{{ query }}</span><button class=\"user-manager-open-profile\" @click=\"$emit('navigate', 'user_profile', { id: 77, account_name: 'user-77' })\">open user profile</button></div>",
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
            template:
              '<button class="channel-open-public-profile" @click="$emit(\'open-public-profile\', { id: 88, account_name: \'owner-88\' })">open public profile</button>',
          },
        },
      },
    })
  }

  function mountRouteScroll(scrollTop = 0) {
    const routeScroll = document.createElement('div')
    routeScroll.className = 'app-route-scroll'
    routeScroll.scrollTop = scrollTop
    document.body.append(routeScroll)
    return routeScroll
  }

  function mountClampedRouteScroll(maximumScrollTop: number, scrollTop = 0) {
    const routeScroll = mountRouteScroll()
    let currentScrollTop = 0
    Object.defineProperty(routeScroll, 'scrollTop', {
      configurable: true,
      get: () => currentScrollTop,
      set: (value: unknown) => {
        const normalized = Number(value)
        currentScrollTop = Math.max(0, Math.min(maximumScrollTop, Number.isFinite(normalized) ? normalized : 0))
      },
    })
    routeScroll.scrollTop = scrollTop
    return routeScroll
  }

  function commitNativeUserDirectoryRoute(query: Record<string, string> = {}) {
    adminViewMocks.route.name = 'admin-users'
    adminViewMocks.route.path = '/admin/users'
    adminViewMocks.route.params = reactive({}) as Record<string, string>
    adminViewMocks.route.query = reactive(query) as Record<string, string>
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
    expect(wrapper.get('.admin-subview-return').classes()).toContain('ui-back-button')
    expect(wrapper.get('.admin-subview-return').attributes('aria-label')).toBe(
      'بازگشت به پنل مدیریت',
    )
    expect(wrapper.get('.create-invitation-stub').text()).toBe('admin-jwt-token')

    await wrapper.get('.admin-subview-return').trigger('click')
    await flushPromises()

    expect(adminViewMocks.popBackStateMock).toHaveBeenCalledTimes(1)
    expect(wrapper.get('.admin-panel-container').attributes('aria-label')).toBe('ابزارهای مدیریت')
  })

  it('routes from the users section into the authoritative admin user profile view', async () => {
    let resolveUserDirectoryPush: (() => void) | undefined
    const pendingUserDirectoryPush = new Promise<void>((resolve) => {
      resolveUserDirectoryPush = resolve
    })
    adminViewMocks.routerPushMock.mockImplementationOnce(() => pendingUserDirectoryPush)
    const wrapper = mountView()
    await flushPromises()

    const usersButton = wrapper
      .findAll('.admin-panel-action')
      .find((button) => button.text().includes('مدیریت کاربران'))
    expect(usersButton).toBeTruthy()
    await usersButton!.trigger('click')
    await flushPromises()
    expect(adminViewMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'admin-users',
      query: {},
    })

    commitNativeUserDirectoryRoute()
    resolveUserDirectoryPush?.()
    await nextTick()
    await flushPromises()

    expect(wrapper.findComponent({ name: 'UserManager' }).exists()).toBe(false)
    wrapper.unmount()

    const destinationWrapper = mountView()
    await flushPromises()
    await destinationWrapper.get('.user-manager-open-profile').trigger('click')
    await flushPromises()

    expect(adminViewMocks.pushBackStateMock).toHaveBeenCalledTimes(2)
    expect(adminViewMocks.popBackStateMock).toHaveBeenCalledTimes(1)
    expect(adminViewMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'admin-user-profile',
      params: { id: '77' },
      query: {},
    })
    expect(destinationWrapper.text()).toContain('پروفایل کاربر')
    expect(destinationWrapper.text()).toContain('در حال بارگذاری پروفایل کاربر')
    expect(destinationWrapper.find('.user-profile-stub').exists()).toBe(false)
  })

  it('waits for the native users route before mounting UserManager from the menu', async () => {
    let resolveUserDirectoryPush: (() => void) | undefined
    const pendingUserDirectoryPush = new Promise<void>((resolve) => {
      resolveUserDirectoryPush = resolve
    })
    adminViewMocks.routerPushMock.mockImplementationOnce(() => pendingUserDirectoryPush)

    const wrapper = mountView()
    await flushPromises()

    const usersButton = wrapper
      .findAll('.admin-panel-action')
      .find((button) => button.text().includes('مدیریت کاربران'))
    expect(usersButton).toBeTruthy()
    await usersButton!.trigger('click')
    await usersButton!.trigger('click')
    await nextTick()

    expect(adminViewMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'admin-users',
      query: {},
    })
    expect(adminViewMocks.routerPushMock).toHaveBeenCalledTimes(1)
    expect(adminViewMocks.pushBackStateMock).toHaveBeenCalledTimes(1)
    const backToMenu = adminViewMocks.pushBackStateMock.mock.calls[0]?.[0]
    expect(typeof backToMenu).toBe('function')
    expect(adminViewMocks.popBackStateMock).not.toHaveBeenCalled()
    expect(getAdminViewVm(wrapper).currentSection).toBe('menu')
    expect(wrapper.findComponent({ name: 'UserManager' }).exists()).toBe(false)
    expect(adminViewMocks.userManagerMountedMock).not.toHaveBeenCalled()

    commitNativeUserDirectoryRoute()
    resolveUserDirectoryPush?.()
    await nextTick()
    await flushPromises()

    expect(getAdminViewVm(wrapper).currentSection).toBe('menu')
    expect(wrapper.findComponent({ name: 'UserManager' }).exists()).toBe(false)
    expect(adminViewMocks.userManagerMountedMock).not.toHaveBeenCalled()

    backToMenu?.()
    await nextTick()
    expect(getAdminViewVm(wrapper).currentSection).toBe('menu')

    wrapper.unmount()
    adminViewMocks.userManagerMountedMock.mockClear()
    const destinationWrapper = mountView()
    await flushPromises()

    expect(getAdminViewVm(destinationWrapper).currentSection).toBe('manage_users')
    expect(destinationWrapper.findComponent({ name: 'UserManager' }).exists()).toBe(true)
    expect(adminViewMocks.userManagerMountedMock).toHaveBeenCalledTimes(1)
  })

  it('releases a rejected menu-to-directory navigation so a retry can await the native route', async () => {
    adminViewMocks.routerPushMock.mockRejectedValueOnce(new Error('navigation blocked'))
    const wrapper = mountView()
    await flushPromises()

    const usersButton = wrapper
      .findAll('.admin-panel-action')
      .find((button) => button.text().includes('مدیریت کاربران'))
    expect(usersButton).toBeTruthy()
    await usersButton!.trigger('click')
    await flushPromises()

    expect(adminViewMocks.routerPushMock).toHaveBeenCalledTimes(1)
    expect(adminViewMocks.routerPushMock).toHaveBeenLastCalledWith({
      name: 'admin-users',
      query: {},
    })
    expect(getAdminViewVm(wrapper).currentSection).toBe('menu')
    expect(wrapper.findComponent({ name: 'UserManager' }).exists()).toBe(false)
    expect(adminViewMocks.userManagerMountedMock).not.toHaveBeenCalled()

    let resolveUserDirectoryPush: (() => void) | undefined
    const pendingUserDirectoryPush = new Promise<void>((resolve) => {
      resolveUserDirectoryPush = resolve
    })
    adminViewMocks.routerPushMock.mockImplementationOnce(() => pendingUserDirectoryPush)
    await usersButton!.trigger('click')
    await nextTick()

    expect(adminViewMocks.routerPushMock).toHaveBeenCalledTimes(2)
    expect(adminViewMocks.routerPushMock).toHaveBeenLastCalledWith({
      name: 'admin-users',
      query: {},
    })
    expect(getAdminViewVm(wrapper).currentSection).toBe('menu')
    expect(wrapper.findComponent({ name: 'UserManager' }).exists()).toBe(false)

    commitNativeUserDirectoryRoute()
    resolveUserDirectoryPush?.()
    await nextTick()
    await flushPromises()

    expect(getAdminViewVm(wrapper).currentSection).toBe('menu')
    expect(wrapper.findComponent({ name: 'UserManager' }).exists()).toBe(false)
    expect(adminViewMocks.userManagerMountedMock).not.toHaveBeenCalled()

    wrapper.unmount()
    adminViewMocks.userManagerMountedMock.mockClear()
    const destinationWrapper = mountView()
    await flushPromises()

    expect(getAdminViewVm(destinationWrapper).currentSection).toBe('manage_users')
    expect(destinationWrapper.findComponent({ name: 'UserManager' }).exists()).toBe(true)
    expect(adminViewMocks.userManagerMountedMock).toHaveBeenCalledTimes(1)
  })

  it('keeps UserManager search state ephemeral and canonicalizes only list scroll in the URL', async () => {
    adminViewMocks.route.name = 'admin-users'
    adminViewMocks.route.query = reactive({
      scroll: '64.9',
      q: '09120000000',
      account_name: 'untrusted-route-name',
    }) as Record<string, string>
    const routeScroll = mountRouteScroll()

    const wrapper = mountView()
    await flushPromises()

    const userManager = wrapper.getComponent({ name: 'UserManager' })
    expect(userManager.props('query')).toBe('')
    expect(adminViewMocks.routerReplaceMock).toHaveBeenCalledWith({
      name: 'admin-users',
      query: { scroll: '64' },
    })

    adminViewMocks.routerPushMock.mockClear()
    adminViewMocks.routerReplaceMock.mockClear()
    userManager.vm.$emit('query-change', '  09120000000  ')
    await flushPromises()

    expect(userManager.props('query')).toBe('09120000000')
    expect(wrapper.get('.user-manager-query').text()).toBe('09120000000')
    expect(adminViewMocks.routerPushMock).not.toHaveBeenCalled()
    expect(adminViewMocks.routerReplaceMock).not.toHaveBeenCalled()

    userManager.vm.$emit('settled')
    await flushPromises()
    expect(routeScroll.scrollTop).toBe(64)

    routeScroll.scrollTop = 88.9
    routeScroll.dispatchEvent(new Event('scroll'))
    await flushPromises()
    expect(adminViewMocks.routerReplaceMock).toHaveBeenCalledWith({
      name: 'admin-users',
      query: { scroll: '88' },
    })
  })

  it('reads a legacy directory list once, then replaces it with the scroll-only native route', async () => {
    adminViewMocks.route.query = reactive({
      section: 'manage_users',
      scroll: '31.8',
      q: '09120000000',
      account_name: 'untrusted-route-name',
    }) as Record<string, string>

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.findComponent({ name: 'UserManager' }).exists()).toBe(true)
    expect(adminViewMocks.routerReplaceMock).toHaveBeenCalledWith({
      name: 'admin-users',
      query: { scroll: '31' },
    })
  })

  it('recanonicalizes an in-place raw query mutation even when its key set is unchanged', async () => {
    adminViewMocks.route.name = 'admin-users'
    adminViewMocks.route.query = reactive({
      scroll: '31',
      q: 'first-sensitive-value',
    }) as Record<string, string>
    mountRouteScroll()

    const wrapper = mountView()
    await flushPromises()
    expect(adminViewMocks.routerReplaceMock).toHaveBeenCalledWith({
      name: 'admin-users',
      query: { scroll: '31' },
    })

    adminViewMocks.routerReplaceMock.mockClear()
    adminViewMocks.route.query.q = 'second-sensitive-value'
    await nextTick()
    await flushPromises()

    expect(adminViewMocks.routerReplaceMock).toHaveBeenCalledWith({
      name: 'admin-users',
      query: { scroll: '31' },
    })
    wrapper.unmount()
  })

  it('returns from the user directory list to the management menu', async () => {
    adminViewMocks.route.name = 'admin-users'
    adminViewMocks.route.query = reactive({ scroll: '32' }) as Record<string, string>
    const routeScroll = mountRouteScroll()
    const wrapper = mountView()
    await flushPromises()

    routeScroll.scrollTop = 72.6
    adminViewMocks.routerPushMock.mockClear()
    adminViewMocks.routerReplaceMock.mockClear()

    expect(wrapper.get('.admin-subview-return').attributes('aria-label')).toBe(
      'بازگشت به پنل مدیریت',
    )
    await wrapper.get('.admin-subview-return').trigger('click')
    await flushPromises()

    expect(adminViewMocks.routerReplaceMock).toHaveBeenCalledWith({
      name: 'admin',
    })
    expect(adminViewMocks.routerPushMock).not.toHaveBeenCalled()
  })

  it('carries only normalized scroll context from the list to detail and back', async () => {
    adminViewMocks.route.name = 'admin-users'
    adminViewMocks.route.query = reactive({ scroll: '32' }) as Record<string, string>
    const routeScroll = mountRouteScroll()
    const wrapper = mountView()
    await flushPromises()

    const userManager = wrapper.getComponent({ name: 'UserManager' })
    userManager.vm.$emit('settled')
    await flushPromises()
    userManager.vm.$emit('query-change', '09120000000')
    routeScroll.scrollTop = 72.6
    adminViewMocks.routerPushMock.mockClear()

    await wrapper.get('.user-manager-open-profile').trigger('click')
    await flushPromises()

    expect(adminViewMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'admin-user-profile',
      params: { id: '77' },
      query: { scroll: '72' },
    })
    expect(JSON.stringify(adminViewMocks.routerPushMock.mock.calls)).not.toContain('09120000000')
    expect(JSON.stringify(adminViewMocks.routerPushMock.mock.calls)).not.toContain('account_name')

    const vm = getAdminViewVm(wrapper)
    vm.handleNavigate('manage_users')
    await flushPromises()

    expect(adminViewMocks.routerPushMock).toHaveBeenLastCalledWith({
      name: 'admin-users',
      query: { scroll: '72' },
    })
  })

  it('keeps captured list scroll authoritative while the detail transition emits an intermediate scroll', async () => {
    adminViewMocks.route.name = 'admin-users'
    adminViewMocks.route.query = reactive({ scroll: '640' }) as Record<string, string>
    const routeScroll = mountRouteScroll(640)
    const wrapper = mountView()
    await flushPromises()

    adminViewMocks.routerPushMock.mockClear()
    adminViewMocks.routerReplaceMock.mockClear()
    await wrapper.get('.user-manager-open-profile').trigger('click')
    routeScroll.scrollTop = 235
    routeScroll.dispatchEvent(new Event('scroll'))
    await nextTick()
    await flushPromises()

    expect(adminViewMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'admin-user-profile',
      params: { id: '77' },
      query: { scroll: '640' },
    })
    expect(adminViewMocks.routerReplaceMock).not.toHaveBeenCalledWith({
      name: 'admin-users',
      query: { scroll: '235' },
    })
  })

  it('holds UserProfile return scroll until the re-entered directory accepts its list response', async () => {
    adminViewMocks.route.name = 'admin-users'
    adminViewMocks.route.query = reactive({ scroll: '640' }) as Record<string, string>
    const routeScroll = mountRouteScroll(640)
    const wrapper = mountView()
    await flushPromises()

    wrapper.getComponent({ name: 'UserManager' }).vm.$emit('settled')
    await flushPromises()
    await wrapper.get('.user-manager-open-profile').trigger('click')
    await flushPromises()

    routeScroll.scrollTop = 235
    routeScroll.dispatchEvent(new Event('scroll'))
    await flushPromises()
    adminViewMocks.routerReplaceMock.mockClear()

    const vm = getAdminViewVm(wrapper)
    vm.handleNavigate('manage_users')
    vm.syncRouteToSection()
    await flushPromises()

    routeScroll.scrollTop = 235
    routeScroll.dispatchEvent(new Event('scroll'))
    await flushPromises()
    expect(adminViewMocks.routerReplaceMock).not.toHaveBeenCalledWith({
      name: 'admin-users',
      query: { scroll: '235' },
    })

    wrapper.getComponent({ name: 'UserManager' }).vm.$emit('settled')
    await flushPromises()
    expect(routeScroll.scrollTop).toBe(640)
  })

  it('releases a clamped directory restore and canonicalizes it to the rendered offset', async () => {
    adminViewMocks.route.name = 'admin-users'
    adminViewMocks.route.query = reactive({ scroll: '640' }) as Record<string, string>
    const routeScroll = mountClampedRouteScroll(235)
    const wrapper = mountView()
    await flushPromises()

    adminViewMocks.routerReplaceMock.mockClear()
    wrapper.getComponent({ name: 'UserManager' }).vm.$emit('settled')
    await flushPromises()

    expect(routeScroll.scrollTop).toBe(235)
    expect(adminViewMocks.routerReplaceMock).toHaveBeenCalledWith({
      name: 'admin-users',
      query: { scroll: '235' },
    })

    adminViewMocks.routerReplaceMock.mockClear()
    routeScroll.scrollTop = 120
    routeScroll.dispatchEvent(new Event('scroll'))
    await flushPromises()
    expect(adminViewMocks.routerReplaceMock).toHaveBeenCalledWith({
      name: 'admin-users',
      query: { scroll: '120' },
    })
    wrapper.unmount()
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
    })
  })

  it('reads a legacy profile handoff once, then canonicalizes to native scroll-only detail', async () => {
    adminViewMocks.route.query = reactive({
      section: 'user_profile',
      user_id: '91',
      account_name: 'untrusted-route-name',
      q: '09120000000',
      scroll: '27.4',
    }) as Record<string, string>
    adminViewMocks.apiFetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ id: 91, account_name: 'server-authoritative-user' }),
    })

    const wrapper = mountView()
    await flushPromises()

    expect(adminViewMocks.apiFetchMock).toHaveBeenCalledWith(
      '/api/users/91',
      expect.objectContaining({
        retryNetwork: false,
        trackConnectionState: false,
      }),
    )
    expect(wrapper.text()).toContain('پروفایل کاربر')
    expect(wrapper.get('.user-profile-stub').text()).toBe('server-authoritative-user')
    expect(wrapper.text()).not.toContain('untrusted-route-name')
    expect(adminViewMocks.routerPushMock).not.toHaveBeenCalled()
    expect(adminViewMocks.routerReplaceMock).toHaveBeenCalledWith({
      name: 'admin-user-profile',
      params: { id: '91' },
      query: { scroll: '27' },
    })
  })

  it('clears an invalid legacy profile context instead of retaining its query fields', async () => {
    adminViewMocks.route.query = reactive({
      section: 'user_profile',
      user_id: 'not-an-id',
      q: '09120000000',
      account_name: 'untrusted-route-name',
    }) as Record<string, string>

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.get('.admin-panel-container').attributes('aria-label')).toBe('ابزارهای مدیریت')
    expect(adminViewMocks.apiFetchMock).not.toHaveBeenCalled()
    expect(adminViewMocks.routerReplaceMock).toHaveBeenCalledWith({ name: 'admin' })
  })

  it('recovers an invalid native profile id to the scroll-only user directory', async () => {
    adminViewMocks.route.name = 'admin-user-profile'
    adminViewMocks.route.params = reactive({ id: 'not-an-id' }) as Record<string, string>
    adminViewMocks.route.query = reactive({
      scroll: '40.8',
      q: '09120000000',
      account_name: 'untrusted-route-name',
    }) as Record<string, string>

    const wrapper = mountView()
    await flushPromises()

    expect(adminViewMocks.apiFetchMock).not.toHaveBeenCalled()
    expect(wrapper.findComponent({ name: 'UserManager' }).exists()).toBe(true)
    expect(adminViewMocks.routerReplaceMock).toHaveBeenCalledWith({
      name: 'admin-users',
      query: { scroll: '40' },
    })
  })

  it('canonicalizes direct profile context to scroll only and loads the authoritative payload', async () => {
    adminViewMocks.route.name = 'admin-user-profile'
    adminViewMocks.route.params = reactive({ id: '93' }) as Record<string, string>
    adminViewMocks.route.query = reactive({
      scroll: '18.6',
      q: '09120000000',
      account_name: 'untrusted-route-name',
    }) as Record<string, string>
    adminViewMocks.apiFetchMock.mockResolvedValue(
      responseOf({ id: 93, account_name: 'server-authoritative-user' }),
    )

    const wrapper = mountView()
    await flushPromises()

    expect(adminViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/users/93', expect.any(Object))
    expect(adminViewMocks.routerReplaceMock).toHaveBeenCalledWith({
      name: 'admin-user-profile',
      params: { id: '93' },
      query: { scroll: '18' },
    })
    expect(wrapper.get('.user-profile-stub').text()).toBe('server-authoritative-user')
    expect(wrapper.text()).not.toContain('untrusted-route-name')
    expect(wrapper.text()).not.toContain('09120000000')

    adminViewMocks.routerPushMock.mockClear()
    expect(wrapper.get('.admin-subview-return').attributes('aria-label')).toBe(
      'بازگشت به فهرست کاربران',
    )
    await wrapper.get('.admin-subview-return').trigger('click')
    await flushPromises()
    expect(adminViewMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'admin-users',
      query: { scroll: '18' },
    })
  })

  it('returns direct-profile error fallback to the scroll-only user list', async () => {
    adminViewMocks.route.name = 'admin-user-profile'
    adminViewMocks.route.params = reactive({ id: '94' }) as Record<string, string>
    adminViewMocks.route.query = reactive({
      scroll: '24.7',
      q: '09120000000',
      account_name: 'untrusted-route-name',
    }) as Record<string, string>
    adminViewMocks.apiFetchMock.mockResolvedValue(responseOf({ detail: 'not found' }, 404))

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('کاربر پیدا نشد')
    expect(wrapper.get('.admin-subview-return').attributes('aria-label')).toBe(
      'بازگشت به فهرست کاربران',
    )
    adminViewMocks.routerPushMock.mockClear()
    await wrapper.get('.admin-subview-return').trigger('click')
    await flushPromises()

    expect(adminViewMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'admin-users',
      query: { scroll: '24' },
    })
    expect(JSON.stringify(adminViewMocks.routerPushMock.mock.calls)).not.toContain('09120000000')
    expect(JSON.stringify(adminViewMocks.routerPushMock.mock.calls)).not.toContain('account_name')
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

    expect(adminViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/users/91', expect.any(Object))
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

    const vm = getAdminViewVm(wrapper)
    vm.currentSection = 'user_profile'
    vm.isLoadingRouteUserProfile = true
    await flushPromises()

    expect(wrapper.text()).toContain('در حال بارگذاری پروفایل کاربر')
    expect(wrapper.find('.user-profile-stub').exists()).toBe(false)
  })

  it.each([
    [403, 'دسترسی به پروفایل مجاز نیست', 'مجوز مشاهده این پروفایل را ندارید.'],
    [404, 'کاربر پیدا نشد', 'این کاربر در دسترس نیست یا دیگر وجود ندارد.'],
    [500, 'پروفایل کاربر در دسترس نیست', 'دریافت اطلاعات کاربر انجام نشد. دوباره تلاش کنید.'],
  ])(
    'keeps the profile deep link in place for HTTP %s and renders its bounded error state',
    async (status, title, message) => {
      adminViewMocks.route.name = 'admin-user-profile'
      adminViewMocks.route.params = reactive({ id: '52' }) as Record<string, string>
      adminViewMocks.apiFetchMock.mockResolvedValueOnce(
        responseOf({ detail: 'backend detail' }, status),
      )

      const wrapper = mountView()
      await flushPromises()

      expect(wrapper.text()).toContain('پروفایل کاربر')
      expect(wrapper.text()).toContain(title)
      expect(wrapper.text()).toContain(message)
      expect(wrapper.text()).toContain('تلاش مجدد')
      expect(adminViewMocks.routerReplaceMock).not.toHaveBeenCalled()
    },
  )

  it('treats a mismatched successful profile payload as unavailable instead of rendering a blank detail', async () => {
    adminViewMocks.route.name = 'admin-user-profile'
    adminViewMocks.route.params = reactive({ id: '52' }) as Record<string, string>
    adminViewMocks.apiFetchMock.mockResolvedValueOnce(
      responseOf({ id: 99, account_name: 'wrong-user' }),
    )

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('پروفایل کاربر در دسترس نیست')
    expect(wrapper.text()).toContain('تلاش مجدد')
    expect(wrapper.find('.user-profile-stub').exists()).toBe(false)
    expect(adminViewMocks.routerReplaceMock).not.toHaveBeenCalled()
  })

  it('retries the same profile route after a transport failure without losing context', async () => {
    adminViewMocks.route.name = 'admin-user-profile'
    adminViewMocks.route.params = reactive({ id: '53' }) as Record<string, string>
    adminViewMocks.apiFetchMock
      .mockRejectedValueOnce(new Error('network failed'))
      .mockResolvedValueOnce(responseOf({ id: 53, account_name: 'recovered-user' }))

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('پروفایل کاربر در دسترس نیست')
    await wrapper.get('.admin-route-profile-error .ui-button').trigger('click')
    await flushPromises()

    expect(adminViewMocks.apiFetchMock).toHaveBeenCalledTimes(2)
    expect(wrapper.get('.user-profile-stub').text()).toBe('recovered-user')
    expect(adminViewMocks.routerReplaceMock).not.toHaveBeenCalled()
  })

  it('lets the latest route profile response win when requests settle out of order', async () => {
    let resolveFirst: ((value: ReturnType<typeof responseOf>) => void) | undefined
    const firstResponse = new Promise<ReturnType<typeof responseOf>>((resolve) => {
      resolveFirst = resolve
    })
    adminViewMocks.route.name = 'admin-user-profile'
    adminViewMocks.route.params = reactive({ id: '61' }) as Record<string, string>
    adminViewMocks.apiFetchMock
      .mockReturnValueOnce(firstResponse)
      .mockResolvedValueOnce(responseOf({ id: 62, account_name: 'latest-user' }))

    const wrapper = mountView()
    await flushPromises()

    adminViewMocks.route.params.id = '62'
    await nextTick()
    await flushPromises()
    expect(wrapper.get('.user-profile-stub').text()).toBe('latest-user')
    expect((adminViewMocks.apiFetchMock.mock.calls[0][1] as RequestInit).signal?.aborted).toBe(true)

    resolveFirst!(responseOf({ id: 61, account_name: 'stale-user' }))
    await flushPromises()

    expect(wrapper.get('.user-profile-stub').text()).toBe('latest-user')
    expect(wrapper.text()).not.toContain('stale-user')
  })

  it('aborts an in-flight detail request before returning to the user directory', async () => {
    adminViewMocks.route.name = 'admin-user-profile'
    adminViewMocks.route.params = reactive({ id: '63' }) as Record<string, string>
    adminViewMocks.apiFetchMock.mockReturnValue(new Promise(() => {}))

    const wrapper = mountView()
    await flushPromises()

    const detailSignal = (adminViewMocks.apiFetchMock.mock.calls[0][1] as RequestInit).signal
    const vm = getAdminViewVm(wrapper)
    vm.handleNavigate('manage_users')
    await flushPromises()

    expect(detailSignal?.aborted).toBe(true)
    expect(adminViewMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'admin-users',
      query: {},
    })
    expect(wrapper.find('.user-profile-stub').exists()).toBe(false)
  })

  it('ignores invalid public-profile payloads and invalid route profile ids', async () => {
    adminViewMocks.route.query = {
      section: 'user_profile',
      user_id: '0',
    }

    const wrapper = mountView()
    await flushPromises()

    expect(adminViewMocks.apiFetchMock).not.toHaveBeenCalled()
    expect(wrapper.get('.admin-panel-container').attributes('aria-label')).toBe('ابزارهای مدیریت')

    const vm = getAdminViewVm(wrapper)
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

    const vm = getAdminViewVm(wrapper)
    vm.handleNavigate('settings')
    await flushPromises()
    vm.handleNavigate('manage_commodities')
    await flushPromises()

    expect(adminViewMocks.pushBackStateMock).toHaveBeenCalledTimes(2)
    expect(adminViewMocks.popBackStateMock).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('مدیریت کالاها')

    vm.handleNavigate('admin_panel')
    await flushPromises()

    expect(wrapper.get('.admin-panel-container').attributes('aria-label')).toBe('ابزارهای مدیریت')
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

    const vm = getAdminViewVm(wrapper)
    vm.handleNavigate('settings')
    await flushPromises()

    expect(wrapper.get('.admin-panel-container').attributes('aria-label')).toBe('ابزارهای مدیریت')
    expect(wrapper.find('.trading-settings-stub').exists()).toBe(false)
  })

  it('blocks super-admin only route names for middle managers', async () => {
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'مدیر میانی' }))
    adminViewMocks.route.name = 'admin-channels'

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.get('.admin-panel-container').attributes('aria-label')).toBe('ابزارهای مدیریت')
    expect(wrapper.findComponent({ name: 'CreateChannelView' }).exists()).toBe(false)
    expect(adminViewMocks.routerReplaceMock).toHaveBeenCalledWith({ name: 'admin' })
  })

  it('canonicalizes denied admin messages routes to the menu for middle managers', async () => {
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'مدیر میانی' }))
    adminViewMocks.route.name = 'admin-messages'

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.get('.admin-panel-container').attributes('aria-label')).toBe('ابزارهای مدیریت')
    expect(wrapper.findComponent({ name: 'AdminMessagesView' }).exists()).toBe(false)
    expect(adminViewMocks.routerReplaceMock).toHaveBeenCalledWith({ name: 'admin' })
    expect(adminViewMocks.route.path).toBe('/admin')
  })

  it('canonicalizes a denied admin messages route exactly once after mount for middle managers', async () => {
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'مدیر میانی' }))
    const wrapper = mountView()
    await flushPromises()

    expect(adminViewMocks.routerReplaceMock).not.toHaveBeenCalled()

    adminViewMocks.route.name = 'admin-messages'
    adminViewMocks.route.path = '/admin/messages'
    await nextTick()
    await flushPromises()

    expect(wrapper.get('.admin-panel-container').attributes('aria-label')).toBe('ابزارهای مدیریت')
    expect(wrapper.findComponent({ name: 'AdminMessagesView' }).exists()).toBe(false)
    expect(adminViewMocks.routerReplaceMock).toHaveBeenCalledTimes(1)
    expect(adminViewMocks.routerReplaceMock).toHaveBeenCalledWith({ name: 'admin' })
    expect(adminViewMocks.route).toMatchObject({ name: 'admin', path: '/admin', query: {} })
  })

  it('keeps the admin messages route and content for senior admins', async () => {
    adminViewMocks.route.name = 'admin-messages'
    adminViewMocks.route.path = '/admin/messages'

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.findComponent({ name: 'AdminMessagesView' }).exists()).toBe(true)
    expect(adminViewMocks.routerReplaceMock).not.toHaveBeenCalled()
    expect(adminViewMocks.route).toMatchObject({ name: 'admin-messages', path: '/admin/messages' })
  })

  it('blocks legacy section query deep links that are not allowed for middle managers', async () => {
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'مدیر میانی' }))
    adminViewMocks.route.query = {
      section: 'create_channel',
    }

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.get('.admin-panel-container').attributes('aria-label')).toBe('ابزارهای مدیریت')
    expect(wrapper.findComponent({ name: 'CreateChannelView' }).exists()).toBe(false)
    expect(adminViewMocks.routerReplaceMock).toHaveBeenCalledWith({ name: 'admin' })
  })

  it('canonicalizes denied legacy admin messages queries to the menu for middle managers', async () => {
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'مدیر میانی' }))
    adminViewMocks.route.query = {
      section: 'admin_messages',
    }

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.get('.admin-panel-container').attributes('aria-label')).toBe('ابزارهای مدیریت')
    expect(wrapper.findComponent({ name: 'AdminMessagesView' }).exists()).toBe(false)
    expect(adminViewMocks.routerReplaceMock).toHaveBeenCalledWith({ name: 'admin' })
    expect(adminViewMocks.route.path).toBe('/admin')
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
    expect(adminViewMocks.apiFetchMock).toHaveBeenCalledWith('/api/users/92', expect.any(Object))
    expect(wrapper.get('.user-profile-stub').text()).toBe('route-reactive-user')

    const vm = getAdminViewVm(wrapper)
    vm.handleNavigate('settings')
    await flushPromises()
    const settingsBack = adminViewMocks.pushBackStateMock.mock.lastCall?.[0]
    expect(typeof settingsBack).toBe('function')
    settingsBack()
    await flushPromises()
    expect(wrapper.get('.admin-panel-container').attributes('aria-label')).toBe('ابزارهای مدیریت')

    const usersButton = wrapper
      .findAll('.admin-panel-action')
      .find((button) => button.text().includes('مدیریت کاربران'))
    expect(usersButton).toBeTruthy()
    let resolveUserDirectoryPush: (() => void) | undefined
    const pendingUserDirectoryPush = new Promise<void>((resolve) => {
      resolveUserDirectoryPush = resolve
    })
    adminViewMocks.routerPushMock.mockImplementationOnce(() => pendingUserDirectoryPush)
    await usersButton!.trigger('click')
    await flushPromises()
    commitNativeUserDirectoryRoute()
    resolveUserDirectoryPush?.()
    await nextTick()
    await flushPromises()
    expect(wrapper.findComponent({ name: 'UserManager' }).exists()).toBe(false)
    wrapper.unmount()

    const destinationWrapper = mountView()
    await flushPromises()
    await destinationWrapper.get('.user-manager-open-profile').trigger('click')
    await flushPromises()
    const profileBack = adminViewMocks.pushBackStateMock.mock.lastCall?.[0]
    expect(typeof profileBack).toBe('function')
    profileBack()
    await flushPromises()
    expect(destinationWrapper.get('.admin-panel-container').attributes('aria-label')).toBe('ابزارهای مدیریت')
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
