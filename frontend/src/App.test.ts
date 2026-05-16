import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App.vue'

const appMocks = vi.hoisted(() => ({
  route: { name: 'dashboard' as string | undefined },
  isReadyMock: vi.fn<() => Promise<void>>(),
  isAppConnecting: null as any,
}))

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
  template: '<div data-test="auth-shell">authenticated shell</div>',
})

const PWAInstallOverlayStub = defineComponent({
  name: 'PWAInstallOverlay',
  template: '<div data-test="pwa-overlay">pwa overlay</div>',
})

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
        PWAInstallOverlay: PWAInstallOverlayStub,
        transition: false,
      },
    },
  })
}

describe('App.vue', () => {
  beforeEach(() => {
    appMocks.route.name = 'dashboard'
    appMocks.isReadyMock.mockReset()
    if (appMocks.isAppConnecting) {
      appMocks.isAppConnecting.value = false
    }
    document.documentElement.removeAttribute('data-app-mounted')
    document.documentElement.removeAttribute('data-app-boot-timeout')
    delete (window as any).__appBootTimeoutId
  })

  it('shows the initial spinner until the first route is ready, then renders the route, shell, and PWA overlay', async () => {
    const deferred = createDeferred()
    appMocks.isReadyMock.mockReturnValueOnce(deferred.promise)
    document.documentElement.setAttribute('data-app-boot-timeout', '1')
    ;(window as any).__appBootTimeoutId = 123
    const clearTimeoutSpy = vi.spyOn(window, 'clearTimeout')

    const wrapper = mountApp()

    expect(wrapper.find('.animate-spin').exists()).toBe(true)
    expect(wrapper.find('[data-test="route-component"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="auth-shell"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="pwa-overlay"]').exists()).toBe(false)

    deferred.resolve()
    await flushPromises()

    expect(wrapper.find('.animate-spin').exists()).toBe(false)
    expect(wrapper.find('[data-test="route-component"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="auth-shell"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="pwa-overlay"]').exists()).toBe(true)
    expect(document.documentElement.getAttribute('data-app-mounted')).toBe('1')
    expect(document.documentElement.hasAttribute('data-app-boot-timeout')).toBe(false)
    expect(clearTimeoutSpy).toHaveBeenCalledWith(123)
    expect((window as any).__appBootTimeoutId).toBeUndefined()
  })

  it('renders the connection banner and omits the authenticated shell on the login route', async () => {
    appMocks.route.name = 'login'
    appMocks.isReadyMock.mockResolvedValueOnce()
    appMocks.isAppConnecting.value = true

    const wrapper = mountApp()
    await flushPromises()

    expect(wrapper.text()).toContain('در حال اتصال...')
    expect(wrapper.find('[data-test="route-component"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="auth-shell"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="pwa-overlay"]').exists()).toBe(true)
  })
})