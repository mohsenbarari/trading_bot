import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import postcss from 'postcss'
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

const RouteComponentStub = defineComponent({
  name: 'RouteComponentStub',
  template: '<div data-test="route-component">route content</div>',
})

const RouterViewStub = defineComponent({
  name: 'RouterView',
  setup(_, { slots }) {
    return () => slots.default?.({ Component: RouteComponentStub })
  },
})

const AuthenticatedShellStub = defineComponent({
  name: 'AuthenticatedShell',
  props: { v2Scope: Boolean, showDailyNavigation: Boolean },
  template: '<div data-test="auth-shell">authenticated shell</div>',
})

const TransitionStub = defineComponent({
  name: 'TransitionStub',
  props: { name: String },
  template:
    '<div data-test="route-transition" :data-transition-name="name"><slot /></div>',
})

const appSource = readFileSync(resolve(process.cwd(), 'src/App.vue'), 'utf8')
const mainCssSource = readFileSync(resolve(process.cwd(), 'src/assets/main.css'), 'utf8')

function reducedMotionSelectors(source: string) {
  const selectors: string[] = []
  postcss.parse(source).walkAtRules('media', (rule) => {
    if (rule.params !== '(prefers-reduced-motion: reduce)') return
    rule.walkRules((nestedRule) => {
      selectors.push(...nestedRule.selectors)
    })
  })
  return selectors
}

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
        transition: TransitionStub,
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
    expect(wrapper.get('[data-test="route-transition"]').attributes('data-transition-name')).toBe(
      'ui-v2-route-fade',
    )
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
    expect(wrapper.get('.app-shell').classes()).not.toContain('app-copyable-info')
    expect(wrapper.get('[data-test="route-component"]').classes()).not.toContain(
      'app-route--persian-typography',
    )
    expect(wrapper.get('[data-test="route-transition"]').attributes('data-transition-name')).toBe(
      'fade',
    )
  })

  it('does not reserve daily navigation space on messenger', async () => {
    appMocks.route.name = 'messenger'
    appMocks.route.path = '/chat'
    appMocks.route.fullPath = '/chat'
    appMocks.route.meta = {
      uiShellClass: 'protected-legacy',
      uiV2Scope: 'off',
    }
    appMocks.isReadyMock.mockResolvedValueOnce()

    const wrapper = mountApp()
    await flushPromises()

    expect(wrapper.find('[data-test="auth-shell"]').exists()).toBe(true)
    expect(wrapper.get('.app-route-scroll').classes()).toContain('app-route-scroll--no-daily-nav')
  })

  it('applies the Persian typography marker only to NONE route vnodes', async () => {
    appMocks.isReadyMock.mockResolvedValue()

    const routeCases = [
      {
        name: 'login',
        path: '/login',
        fullPath: '/login',
        meta: { uiShellClass: 'public', uiV2Scope: 'route' },
        expected: true,
      },
      {
        name: 'admin-invitations',
        path: '/admin/invitations',
        fullPath: '/admin/invitations',
        meta: { uiShellClass: 'standard-authenticated', uiV2Scope: 'section' },
        expected: true,
      },
      {
        name: 'system-recovery',
        path: '/missing',
        fullPath: '/missing',
        meta: { uiShellClass: 'system-recovery', uiV2Scope: 'route' },
        expected: true,
      },
      {
        name: 'home',
        path: '/',
        fullPath: '/',
        meta: { uiShellClass: 'standard-authenticated', uiV2Scope: 'section' },
        expected: false,
      },
      {
        name: 'market',
        path: '/market',
        fullPath: '/market',
        meta: { uiShellClass: 'protected-legacy', uiV2Scope: 'off' },
        expected: false,
      },
      {
        name: 'messenger',
        path: '/chat',
        fullPath: '/chat',
        meta: { uiShellClass: 'protected-legacy', uiV2Scope: 'off' },
        expected: false,
      },
      {
        name: 'admin-channels',
        path: '/admin/channels',
        fullPath: '/admin/channels',
        meta: { uiShellClass: 'protected-legacy', uiV2Scope: 'off' },
        expected: false,
      },
      {
        name: 'share-receive',
        path: '/share-receive',
        fullPath: '/share-receive',
        meta: { uiShellClass: 'protected-legacy', uiV2Scope: 'off' },
        expected: false,
      },
      {
        name: 'admin-messages',
        path: '/admin/messages',
        fullPath: '/admin/messages',
        meta: { uiShellClass: 'standard-authenticated', uiV2Scope: 'section' },
        expected: false,
      },
      {
        name: 'admin-system',
        path: '/admin/system',
        fullPath: '/admin/system',
        meta: { uiShellClass: 'standard-authenticated', uiV2Scope: 'section' },
        expected: false,
      },
      {
        name: 'unknown-route',
        path: '/unknown',
        fullPath: '/unknown',
        meta: { uiShellClass: 'system-recovery', uiV2Scope: 'route' },
        expected: false,
      },
    ] as const

    for (const routeCase of routeCases) {
      appMocks.route.name = routeCase.name
      appMocks.route.path = routeCase.path
      appMocks.route.fullPath = routeCase.fullPath
      appMocks.route.meta = routeCase.meta

      const wrapper = mountApp()
      await flushPromises()

      expect(wrapper.get('.app-shell').classes()).toContain('font-sans')
      const routeRoot = routeCase.meta.uiV2Scope === 'route'
        ? wrapper.get('.app-route-v2-scope')
        : wrapper.get('[data-test="route-component"]')
      expect(routeRoot.classes().includes('app-route--persian-typography')).toBe(routeCase.expected)
      wrapper.unmount()
    }
  })

  it('keeps the typography marker with its route vnode across a protection boundary', async () => {
    appMocks.isReadyMock.mockResolvedValueOnce()
    appMocks.route.name = 'market'
    appMocks.route.path = '/market'
    appMocks.route.fullPath = '/market'
    appMocks.route.meta = { uiShellClass: 'protected-legacy', uiV2Scope: 'off' }

    const wrapper = mountApp()
    await flushPromises()
    const protectedVNode = wrapper.getComponent(RouteComponentStub).vm.$.vnode
    expect(protectedVNode.props?.class ?? '').not.toContain('app-route--persian-typography')

    appMocks.route.name = 'public-profile'
    appMocks.route.path = '/users/12'
    appMocks.route.fullPath = '/users/12'
    appMocks.route.meta = { uiShellClass: 'standard-authenticated', uiV2Scope: 'section' }
    await flushPromises()
    const allowedVNode = wrapper.getComponent(RouteComponentStub).vm.$.vnode

    expect(protectedVNode.props?.class ?? '').not.toContain('app-route--persian-typography')
    expect(allowedVNode.props?.class ?? '').toContain('app-route--persian-typography')

    appMocks.route.name = 'market'
    appMocks.route.path = '/market'
    appMocks.route.fullPath = '/market'
    appMocks.route.meta = { uiShellClass: 'protected-legacy', uiV2Scope: 'off' }
    await flushPromises()
    const returningProtectedVNode = wrapper.getComponent(RouteComponentStub).vm.$.vnode

    expect(allowedVNode.props?.class ?? '').toContain('app-route--persian-typography')
    expect(returningProtectedVNode.props?.class ?? '').not.toContain(
      'app-route--persian-typography',
    )
  })

  it('keeps Stage 5 workspaces mounted across canonical query changes and remounts on path changes', async () => {
    appMocks.route.name = 'operations-customers-detail'
    appMocks.route.path = '/operations/customers/11'
    appMocks.route.fullPath = '/operations/customers/11?section=sessions'
    appMocks.route.meta = {
      uiShellClass: 'standard-authenticated',
      uiV2Scope: 'section',
    }
    appMocks.isReadyMock.mockResolvedValueOnce()

    const wrapper = mountApp()
    await flushPromises()
    expect(wrapper.getComponent(RouteComponentStub).vm.$.vnode.key).toBe(
      'section:/operations/customers/11',
    )

    appMocks.route.fullPath = '/operations/customers/11?tab=sessions&scroll=96'
    await flushPromises()
    expect(wrapper.getComponent(RouteComponentStub).vm.$.vnode.key).toBe(
      'section:/operations/customers/11',
    )

    appMocks.route.name = 'operations-customers'
    appMocks.route.path = '/operations/customers'
    appMocks.route.fullPath = '/operations/customers?scroll=96'
    await flushPromises()
    expect(wrapper.getComponent(RouteComponentStub).vm.$.vnode.key).toBe(
      'section:/operations/customers',
    )
  })

  it('keeps the shared Stage 6 AdminView mounted across user list, detail, and scroll-only context changes', async () => {
    appMocks.route.name = 'admin-users'
    appMocks.route.path = '/admin/users'
    appMocks.route.fullPath = '/admin/users?scroll=96'
    appMocks.route.meta = {
      uiShellClass: 'standard-authenticated',
      uiV2Scope: 'section',
    }
    appMocks.isReadyMock.mockResolvedValueOnce()

    const usersWrapper = mountApp()
    await flushPromises()
    expect(usersWrapper.getComponent(RouteComponentStub).vm.$.vnode.key).toBe(
      'section:admin-user-directory',
    )

    appMocks.route.fullPath = '/admin/users?scroll=144'
    await flushPromises()
    expect(usersWrapper.getComponent(RouteComponentStub).vm.$.vnode.key).toBe(
      'section:admin-user-directory',
    )

    appMocks.route.name = 'admin-user-profile'
    appMocks.route.path = '/admin/users/11'
    appMocks.route.fullPath = '/admin/users/11?scroll=144'
    await flushPromises()
    expect(usersWrapper.getComponent(RouteComponentStub).vm.$.vnode.key).toBe(
      'section:admin-user-directory',
    )

    appMocks.route.fullPath = '/admin/users/11?scroll=208'
    await flushPromises()
    expect(usersWrapper.getComponent(RouteComponentStub).vm.$.vnode.key).toBe(
      'section:admin-user-directory',
    )
  })

  it('keeps unrelated section routes full-path keyed', async () => {
    appMocks.route.name = 'admin'
    appMocks.route.path = '/admin'
    appMocks.route.fullPath = '/admin?section=users'
    appMocks.route.meta = {
      uiShellClass: 'standard-authenticated',
      uiV2Scope: 'section',
    }
    appMocks.isReadyMock.mockResolvedValueOnce()

    const wrapper = mountApp()
    await flushPromises()
    expect(wrapper.getComponent(RouteComponentStub).vm.$.vnode.key).toBe(
      'legacy:/admin?section=users',
    )

    appMocks.route.fullPath = '/admin?section=users&scroll=96'
    await flushPromises()
    expect(wrapper.getComponent(RouteComponentStub).vm.$.vnode.key).toBe(
      'legacy:/admin?section=users&scroll=96',
    )
  })

  it('preserves full-path remounting for protected legacy routes', async () => {
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
    expect(wrapper.getComponent(RouteComponentStub).vm.$.vnode.key).toBe('legacy:/market')

    appMocks.route.fullPath = '/market?view=history'
    await flushPromises()
    expect(wrapper.getComponent(RouteComponentStub).vm.$.vnode.key).toBe(
      'legacy:/market?view=history',
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

  it('allows informational copy on V2 product routes and keeps protected shells unselectable', async () => {
    appMocks.isReadyMock.mockResolvedValue()

    const homeWrapper = mountApp()
    await flushPromises()
    expect(homeWrapper.get('.app-shell').classes()).toContain('app-copyable-info')
    expect(
      homeWrapper.get('[data-test="route-transition"]').attributes('data-transition-name'),
    ).toBe('fade')
    expect(homeWrapper.get('[data-test="route-component"]').classes()).not.toContain(
      'app-reduced-motion-route',
    )
    homeWrapper.unmount()

    appMocks.route.name = 'public-profile'
    appMocks.route.path = '/users/12'
    appMocks.route.fullPath = '/users/12'
    appMocks.route.meta = {
      uiShellClass: 'standard-authenticated',
      uiV2Scope: 'section',
    }
    const publicProfileWrapper = mountApp()
    await flushPromises()
    expect(publicProfileWrapper.get('.app-shell').classes()).toContain('app-copyable-info')
    expect(
      publicProfileWrapper
        .get('[data-test="route-transition"]')
        .attributes('data-transition-name'),
    ).toBe('fade')
    expect(publicProfileWrapper.get('[data-test="route-component"]').classes()).toContain(
      'app-reduced-motion-route',
    )
    publicProfileWrapper.unmount()

    appMocks.route.name = 'account'
    appMocks.route.path = '/account'
    appMocks.route.fullPath = '/account'
    appMocks.route.meta = {
      uiShellClass: 'standard-authenticated',
      uiV2Scope: 'route',
    }
    const accountWrapper = mountApp()
    await flushPromises()
    expect(accountWrapper.get('.app-shell').classes()).toContain('app-copyable-info')
    accountWrapper.unmount()

    appMocks.route.name = 'admin-messages'
    appMocks.route.path = '/admin/messages'
    appMocks.route.fullPath = '/admin/messages'
    appMocks.route.meta = {
      uiShellClass: 'standard-authenticated',
      uiV2Scope: 'section',
    }
    const messagesWrapper = mountApp()
    await flushPromises()
    expect(messagesWrapper.get('.app-shell').classes()).not.toContain('app-copyable-info')
    expect(
      messagesWrapper.get('[data-test="route-transition"]').attributes('data-transition-name'),
    ).toBe('fade')
    expect(messagesWrapper.get('[data-test="route-component"]').classes()).not.toContain(
      'app-reduced-motion-route',
    )
    messagesWrapper.unmount()

    appMocks.route.name = 'admin-system'
    appMocks.route.path = '/admin/system'
    appMocks.route.fullPath = '/admin/system'
    appMocks.route.meta = {
      uiShellClass: 'standard-authenticated',
      uiV2Scope: 'section',
    }
    const systemWrapper = mountApp()
    await flushPromises()
    expect(systemWrapper.get('.app-shell').classes()).not.toContain('app-copyable-info')
    expect(
      systemWrapper.get('[data-test="route-transition"]').attributes('data-transition-name'),
    ).toBe('fade')
    expect(systemWrapper.get('[data-test="route-component"]').classes()).not.toContain(
      'app-reduced-motion-route',
    )
    systemWrapper.unmount()
  })

  it('binds reduced-motion eligibility to each route vnode instead of a destination transition name', async () => {
    appMocks.isReadyMock.mockResolvedValueOnce()
    appMocks.route.name = 'market'
    appMocks.route.path = '/market'
    appMocks.route.fullPath = '/market'
    appMocks.route.meta = { uiShellClass: 'protected-legacy', uiV2Scope: 'off' }

    const wrapper = mountApp()
    await flushPromises()
    const protectedVNode = wrapper.getComponent(RouteComponentStub).vm.$.vnode
    expect(protectedVNode.props?.class ?? '').not.toContain('app-reduced-motion-route')

    appMocks.route.name = 'public-profile'
    appMocks.route.path = '/users/12'
    appMocks.route.fullPath = '/users/12'
    appMocks.route.meta = { uiShellClass: 'standard-authenticated', uiV2Scope: 'section' }
    await flushPromises()
    const allowedVNode = wrapper.getComponent(RouteComponentStub).vm.$.vnode

    expect(protectedVNode.props?.class ?? '').not.toContain('app-reduced-motion-route')
    expect(allowedVNode.props?.class ?? '').toContain('app-reduced-motion-route')
    expect(wrapper.get('[data-test="route-transition"]').attributes('data-transition-name')).toBe(
      'fade',
    )

    appMocks.route.name = 'market'
    appMocks.route.path = '/market'
    appMocks.route.fullPath = '/market'
    appMocks.route.meta = { uiShellClass: 'protected-legacy', uiV2Scope: 'off' }
    await flushPromises()
    const returningProtectedVNode = wrapper.getComponent(RouteComponentStub).vm.$.vnode

    expect(allowedVNode.props?.class ?? '').toContain('app-reduced-motion-route')
    expect(returningProtectedVNode.props?.class ?? '').not.toContain(
      'app-reduced-motion-route',
    )
  })

  it('collapses route fades only on the eligible route vnode marker', () => {
    const styleSource = appSource.match(/<style>([\s\S]*?)<\/style>/)?.[1]
    expect(styleSource).toBeDefined()

    const selectors = reducedMotionSelectors(`${styleSource}\n${mainCssSource}`)
    expect(selectors).toContain('.app-reduced-motion-route.fade-enter-active')
    expect(selectors).toContain('.app-reduced-motion-route.fade-leave-active')
    expect(selectors).not.toContain('.fade-enter-active')
    expect(selectors).not.toContain('.fade-leave-active')
  })
})
