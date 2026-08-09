import { defineComponent, reactive } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App.vue'
import AppDesignSystemScope from './components/ui/AppDesignSystemScope.vue'

const appMocks = vi.hoisted(() => ({
  route: {
    name: 'home' as string | undefined,
    path: '/',
    fullPath: '/',
    meta: {
      uiShellClass: 'standard-authenticated',
      uiV2Scope: 'section',
    } as Record<string, unknown>,
  },
  isReadyMock: vi.fn<() => Promise<void>>(),
  isAppConnecting: null as { value: boolean } | null,
  usePWAInstallMock: vi.fn(() => ({
    isInstallable: { value: false },
    isInstalled: { value: false },
    installApp: vi.fn(),
  })),
}))

appMocks.route = reactive(appMocks.route)

vi.mock('vue-router', () => ({
  useRoute: () => appMocks.route,
  useRouter: () => ({
    isReady: appMocks.isReadyMock,
  }),
}))

vi.mock('./utils/auth', async () => {
  const vue = await import('vue')
  const isAppConnecting = vue.ref(false)
  appMocks.isAppConnecting = isAppConnecting
  return { isAppConnecting }
})

vi.mock('./utils/pwaInstall', () => ({
  usePWAInstall: appMocks.usePWAInstallMock,
}))

const RouterViewStub = defineComponent({
  name: 'RouterView',
  setup(_, { slots }) {
    const RouteComponent = defineComponent({
      name: 'RouteComponentStub',
      template: '<div data-test="route-component">route content</div>',
    })
    return () => slots.default?.({ Component: RouteComponent })
  },
})

const AuthenticatedShellStub = defineComponent({
  name: 'AuthenticatedShell',
  props: { v2Scope: Boolean, showDailyNavigation: Boolean },
  template: '<div data-test="auth-shell">authenticated shell</div>',
})

type AppBootTestWindow = Window & { __appBootTimeoutId?: unknown }

function createDeferred() {
  let resolve!: () => void
  const promise = new Promise<void>((resolver) => {
    resolve = resolver
  })
  return { promise, resolve }
}

function mountApp() {
  return mount(App, {
    global: {
      stubs: {
        RouterView: RouterViewStub,
        AuthenticatedShell: AuthenticatedShellStub,
        transition: false,
      },
    },
  })
}

describe('App.vue', () => {
  beforeEach(() => {
    localStorage.clear()
    appMocks.route.name = 'home'
    appMocks.route.path = '/'
    appMocks.route.fullPath = '/'
    appMocks.route.meta = {
      uiShellClass: 'standard-authenticated',
      uiV2Scope: 'section',
    }
    appMocks.isReadyMock.mockReset()
    appMocks.usePWAInstallMock.mockClear()
    if (appMocks.isAppConnecting) {
      appMocks.isAppConnecting.value = false
    }
    document.documentElement.removeAttribute('data-app-mounted')
    document.documentElement.removeAttribute('data-app-boot-timeout')
    delete (window as AppBootTestWindow).__appBootTimeoutId
  })

  it('shows the initial spinner until the first route is ready, then renders the route and shell', async () => {
    const deferred = createDeferred()
    appMocks.isReadyMock.mockReturnValueOnce(deferred.promise)
    document.documentElement.setAttribute('data-app-boot-timeout', '1')
    ;(window as AppBootTestWindow).__appBootTimeoutId = 123
    const clearTimeoutSpy = vi.spyOn(window, 'clearTimeout')

    const wrapper = mountApp()

    expect(appMocks.usePWAInstallMock).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.animate-spin').exists()).toBe(true)
    expect(wrapper.find('[data-test="route-component"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="auth-shell"]').exists()).toBe(false)

    deferred.resolve()
    await flushPromises()

    expect(wrapper.find('.animate-spin').exists()).toBe(false)
    expect(wrapper.find('[data-test="route-component"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="auth-shell"]').exists()).toBe(true)
    expect(wrapper.get('.app-authenticated-shell-v2').attributes('data-ui-system')).toBe('v2')
    expect(wrapper.get('.app-route-scroll').classes()).not.toContain(
      'app-route-scroll--no-daily-nav',
    )
    expect(document.documentElement.getAttribute('data-app-mounted')).toBe('1')
    expect(document.documentElement.hasAttribute('data-app-boot-timeout')).toBe(false)
    expect(clearTimeoutSpy).toHaveBeenCalledWith(123)
    expect((window as AppBootTestWindow).__appBootTimeoutId).toBeUndefined()
  })

  it('isolates a public route from authenticated shell, connection state, and nav spacing', async () => {
    appMocks.route.name = 'login'
    appMocks.route.path = '/login'
    appMocks.route.fullPath = '/login'
    appMocks.route.meta = {
      uiShellClass: 'public',
      uiV2Scope: 'route',
    }
    appMocks.isReadyMock.mockResolvedValueOnce()
    appMocks.isAppConnecting!.value = true

    const wrapper = mountApp()
    await flushPromises()

    expect(appMocks.usePWAInstallMock).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).not.toContain('در حال اتصال...')
    expect(wrapper.find('[data-test="route-component"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="auth-shell"]').exists()).toBe(false)
    expect(wrapper.get('.app-route-v2-scope').attributes('data-ui-system')).toBe('v2')
    expect(wrapper.getComponent(AppDesignSystemScope).vm.$.vnode.key).toBe('v2:/login')
    expect(wrapper.get('.app-route-scroll').classes()).toContain('app-route-scroll--no-daily-nav')
  })

  it('keeps security layers but omits daily navigation for the focused authenticated shell', async () => {
    appMocks.route.name = 'setup-password'
    appMocks.route.path = '/setup-password'
    appMocks.route.fullPath = '/setup-password'
    appMocks.route.meta = {
      uiShellClass: 'focused-authenticated',
      uiV2Scope: 'route',
    }
    appMocks.isReadyMock.mockResolvedValueOnce()

    const wrapper = mountApp()
    await flushPromises()

    const shell = wrapper.getComponent(AuthenticatedShellStub)
    expect(shell.props('v2Scope')).toBe(true)
    expect(shell.props('showDailyNavigation')).toBe(false)
    expect(wrapper.get('.app-route-scroll').classes()).toContain('app-route-scroll--no-daily-nav')
    expect(wrapper.get('.app-authenticated-shell-v2').attributes('data-ui-system')).toBe('v2')
  })

  it('keeps a full-protected route on the unscoped legacy shell branch', async () => {
    appMocks.route.name = 'market'
    appMocks.route.path = '/market'
    appMocks.route.fullPath = '/market'
    appMocks.route.meta = {
      uiShellClass: 'protected-legacy',
      uiV2Scope: 'off',
    }
    appMocks.isReadyMock.mockResolvedValueOnce()

    const wrapper = mountApp()
    await flushPromises()

    expect(wrapper.find('.app-route-v2-scope').exists()).toBe(false)
    expect(wrapper.find('.app-authenticated-shell-v2').exists()).toBe(false)
    expect(wrapper.find('[data-test="auth-shell"]').exists()).toBe(true)
    expect(wrapper.get('.app-route-scroll').classes()).not.toContain(
      'app-route-scroll--no-daily-nav',
    )
  })

  it('renders scoped reconnecting feedback on the standard shell and legacy feedback on protected routes', async () => {
    appMocks.isReadyMock.mockResolvedValueOnce()
    appMocks.isAppConnecting!.value = true

    const standard = mountApp()
    await flushPromises()

    expect(standard.get('.ui-v2-connection-banner').attributes('data-ui-system')).toBe('v2')
    expect(standard.text()).toContain('ارتباط در حال بازیابی است')
    standard.unmount()

    appMocks.route.name = 'market'
    appMocks.route.path = '/market'
    appMocks.route.fullPath = '/market'
    appMocks.route.meta = {
      uiShellClass: 'protected-legacy',
      uiV2Scope: 'off',
    }
    appMocks.isReadyMock.mockResolvedValueOnce()

    const protectedRoute = mountApp()
    await flushPromises()

    expect(protectedRoute.find('.ui-v2-connection-banner').exists()).toBe(false)
    expect(protectedRoute.text()).toContain('در حال اتصال...')
  })

  it('fails closed for missing route metadata without leaking an authenticated shell', async () => {
    appMocks.route.name = undefined
    appMocks.route.path = '/unknown'
    appMocks.route.fullPath = '/unknown'
    appMocks.route.meta = {}
    appMocks.isReadyMock.mockResolvedValueOnce()

    const wrapper = mountApp()
    await flushPromises()

    expect(wrapper.find('[data-test="route-component"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="auth-shell"]').exists()).toBe(false)
    expect(wrapper.get('.app-route-scroll').classes()).toContain('app-route-scroll--no-daily-nav')
  })

  it('uses an authenticated recovery shell only when local session credentials exist', async () => {
    appMocks.route.name = 'system-recovery'
    appMocks.route.path = '/missing'
    appMocks.route.fullPath = '/missing'
    appMocks.route.meta = {
      uiShellClass: 'system-recovery',
      uiV2Scope: 'route',
    }
    appMocks.isReadyMock.mockResolvedValueOnce()

    const guest = mountApp()
    await flushPromises()
    expect(guest.find('[data-test="auth-shell"]').exists()).toBe(false)
    guest.unmount()

    localStorage.setItem('refresh_token', 'retained-session')
    appMocks.isReadyMock.mockResolvedValueOnce()
    const authenticated = mountApp()
    await flushPromises()

    const shell = authenticated.getComponent(AuthenticatedShellStub)
    expect(shell.props('v2Scope')).toBe(true)
    expect(shell.props('showDailyNavigation')).toBe(true)
    expect(authenticated.get('.app-route-scroll').classes()).not.toContain(
      'app-route-scroll--no-daily-nav',
    )
  })

  it('re-evaluates recovery shell authority after same-tab login and logout transitions', async () => {
    appMocks.route.name = 'system-recovery'
    appMocks.route.path = '/missing-before-login'
    appMocks.route.fullPath = '/missing-before-login'
    appMocks.route.meta = {
      uiShellClass: 'system-recovery',
      uiV2Scope: 'route',
    }
    appMocks.isReadyMock.mockResolvedValueOnce()

    const wrapper = mountApp()
    await flushPromises()
    expect(wrapper.find('[data-test="auth-shell"]').exists()).toBe(false)

    localStorage.setItem('refresh_token', 'same-tab-session')
    appMocks.route.name = 'login'
    appMocks.route.path = '/login'
    appMocks.route.fullPath = '/login'
    appMocks.route.meta = { uiShellClass: 'public', uiV2Scope: 'route' }
    await flushPromises()
    appMocks.route.name = 'system-recovery'
    appMocks.route.path = '/missing-after-login'
    appMocks.route.fullPath = '/missing-after-login'
    appMocks.route.meta = { uiShellClass: 'system-recovery', uiV2Scope: 'route' }
    await flushPromises()

    expect(wrapper.getComponent(AuthenticatedShellStub).props('showDailyNavigation')).toBe(true)

    localStorage.clear()
    appMocks.route.name = 'login'
    appMocks.route.path = '/login'
    appMocks.route.fullPath = '/login'
    appMocks.route.meta = { uiShellClass: 'public', uiV2Scope: 'route' }
    await flushPromises()
    appMocks.route.name = 'system-recovery'
    appMocks.route.path = '/missing-after-logout'
    appMocks.route.fullPath = '/missing-after-logout'
    appMocks.route.meta = { uiShellClass: 'system-recovery', uiV2Scope: 'route' }
    await flushPromises()

    expect(wrapper.find('[data-test="auth-shell"]').exists()).toBe(false)
    expect(wrapper.get('.app-route-scroll').classes()).toContain('app-route-scroll--no-daily-nav')
  })

  it('fails closed when session storage cannot be read for a recovery route', async () => {
    appMocks.route.name = 'system-recovery'
    appMocks.route.path = '/missing'
    appMocks.route.fullPath = '/missing'
    appMocks.route.meta = {
      uiShellClass: 'system-recovery',
      uiV2Scope: 'route',
    }
    appMocks.isReadyMock.mockResolvedValueOnce()
    const getItemSpy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage blocked')
    })

    const wrapper = mountApp()
    await flushPromises()

    expect(wrapper.find('[data-test="auth-shell"]').exists()).toBe(false)
    getItemSpy.mockRestore()
  })

  it('keys a scoped registration view by path so secret-query scrubbing does not remount it', async () => {
    appMocks.route.name = 'web-register'
    appMocks.route.path = '/register'
    appMocks.route.fullPath = '/register?registration_token=legacy-secret'
    appMocks.route.meta = {
      uiShellClass: 'public',
      uiV2Scope: 'route',
    }
    appMocks.isReadyMock.mockResolvedValueOnce()

    const wrapper = mountApp()
    await flushPromises()

    expect(wrapper.getComponent(AppDesignSystemScope).vm.$.vnode.key).toBe('v2:/register')
    expect(wrapper.html()).not.toContain('legacy-secret')
  })
})
