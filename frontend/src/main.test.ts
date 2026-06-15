import { flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mainMocks = vi.hoisted(() => ({
  appInstance: {
    use: vi.fn(),
    directive: vi.fn(),
    mount: vi.fn(),
  },
  createApp: vi.fn(),
  createPinia: vi.fn(),
  registerSW: vi.fn(),
  router: { name: 'router' },
  vRipple: Symbol('ripple'),
  listeners: new Map<string, Array<(event?: any) => void>>(),
  timeouts: [] as Array<{ fn: (...args: any[]) => any; delay?: number }>,
  reloadSpy: vi.fn(),
  getRegistrationsMock: vi.fn(),
  unregisterMock: vi.fn(),
  telegram: {
    ready: vi.fn(),
    expand: vi.fn(),
    onEvent: vi.fn(),
  },
}))

mainMocks.appInstance.use.mockReturnValue(mainMocks.appInstance)
mainMocks.appInstance.directive.mockReturnValue(mainMocks.appInstance)
mainMocks.createApp.mockImplementation(() => mainMocks.appInstance)
mainMocks.createPinia.mockReturnValue({ pinia: true })

vi.mock('vue', () => ({
  createApp: mainMocks.createApp,
  ref: (value: any) => ({ value }),
}))

vi.mock('pinia', () => ({
  createPinia: mainMocks.createPinia,
}))

vi.mock('./App.vue', () => ({
  default: { name: 'AppStub' },
}))

vi.mock('./router', () => ({
  default: mainMocks.router,
}))

vi.mock('./directives/ripple', () => ({
  vRipple: mainMocks.vRipple,
}))

vi.mock('virtual:pwa-register', () => ({
  registerSW: mainMocks.registerSW,
}))

vi.mock('./assets/main.css', () => ({}))
vi.mock('vazirmatn/Vazirmatn-font-face.css', () => ({}))

function setReadyState(state: DocumentReadyState) {
  Object.defineProperty(document, 'readyState', {
    configurable: true,
    value: state,
  })
}

function setTelegram(enabled: boolean) {
  ;(window as any).Telegram = enabled
    ? { WebApp: mainMocks.telegram }
    : undefined
}

function getFirstListener(type: string) {
  return mainMocks.listeners.get(type)?.[0]
}

function dispatchStoredListeners(type: string) {
  for (const listener of mainMocks.listeners.get(type) ?? []) {
    listener()
  }
}

function getTimeoutByDelay(delay: number) {
  return mainMocks.timeouts.find((entry) => entry.delay === delay)
}

async function importFreshMain() {
  vi.resetModules()
  await import('./main')
}

describe('main.ts', () => {
  beforeEach(() => {
    mainMocks.createApp.mockClear()
    mainMocks.createPinia.mockClear()
    mainMocks.registerSW.mockClear()
    mainMocks.appInstance.use.mockClear()
    mainMocks.appInstance.directive.mockClear()
    mainMocks.appInstance.mount.mockClear()
    mainMocks.reloadSpy.mockReset()
    mainMocks.unregisterMock.mockReset()
    mainMocks.getRegistrationsMock.mockReset()
    mainMocks.getRegistrationsMock.mockResolvedValue([{ unregister: mainMocks.unregisterMock }])
    mainMocks.telegram.ready.mockReset()
    mainMocks.telegram.expand.mockReset()
    mainMocks.telegram.onEvent.mockReset()
    mainMocks.listeners = new Map()
    mainMocks.timeouts = []

    vi.spyOn(window, 'addEventListener').mockImplementation(((type: string, listener: EventListenerOrEventListenerObject) => {
      const handlers = mainMocks.listeners.get(type) ?? []
      const normalized = typeof listener === 'function'
        ? listener
        : listener.handleEvent.bind(listener)
      handlers.push(normalized)
      mainMocks.listeners.set(type, handlers)
    }) as typeof window.addEventListener)

    const timeoutImpl = vi.fn(((fn: (...args: any[]) => any, delay?: number) => {
      mainMocks.timeouts.push({ fn, delay })
      return mainMocks.timeouts.length
    }) as typeof window.setTimeout)
    vi.stubGlobal('setTimeout', timeoutImpl)
    Object.defineProperty(window, 'setTimeout', {
      configurable: true,
      value: timeoutImpl,
    })

    Object.defineProperty(window, 'location', {
      configurable: true,
      value: {
        ...window.location,
        reload: mainMocks.reloadSpy,
      },
    })

    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: {
        getRegistrations: mainMocks.getRegistrationsMock,
      },
    })

    document.documentElement.setAttribute('data-app-boot-recovering', '1')
    vi.spyOn(window.sessionStorage.__proto__, 'removeItem').mockImplementation(() => undefined)
    document.body.style.backgroundColor = ''
    document.body.style.color = ''
    setTelegram(false)
  })

  it('bootstraps the app, handles preload failures, and registers the service worker on stable complete pages', async () => {
    setReadyState('complete')
    setTelegram(true)

    await importFreshMain()

    expect(mainMocks.createApp).toHaveBeenCalled()
    expect(mainMocks.appInstance.use).toHaveBeenCalledWith({ pinia: true })
    expect(mainMocks.appInstance.use).toHaveBeenCalledWith(mainMocks.router)
    expect(mainMocks.appInstance.directive).toHaveBeenCalledWith('ripple', mainMocks.vRipple)
    expect(mainMocks.appInstance.mount).toHaveBeenCalledWith('#app')
    expect(sessionStorage.removeItem).toHaveBeenCalledWith('app_boot_recovery_attempted')
    expect(document.documentElement.hasAttribute('data-app-boot-recovering')).toBe(false)
    expect(mainMocks.telegram.ready).toHaveBeenCalled()
    expect(mainMocks.telegram.expand).toHaveBeenCalled()
    expect(mainMocks.telegram.onEvent).toHaveBeenCalledWith('themeChanged', expect.any(Function))
    expect(document.body.style.backgroundColor).toBe('rgb(249, 250, 251)')
    expect(document.body.style.color).toBe('rgb(17, 24, 39)')

    const preloadHandler = getFirstListener('vite:preloadError')
    const preloadEvent = { preventDefault: vi.fn() }
    preloadHandler?.(preloadEvent)
    expect(preloadEvent.preventDefault).toHaveBeenCalled()
    expect(mainMocks.reloadSpy).toHaveBeenCalledTimes(1)

    getTimeoutByDelay(250)?.fn()
    expect(mainMocks.registerSW).toHaveBeenCalledTimes(1)

    const registerOptions = mainMocks.registerSW.mock.calls[0]?.[0]
    registerOptions.onNeedRefresh()
    expect(mainMocks.reloadSpy).toHaveBeenCalledTimes(1)

    registerOptions.onRegisterError(new Error('broken sw'))
    await flushPromises()

    expect(mainMocks.getRegistrationsMock).toHaveBeenCalled()
    expect(mainMocks.unregisterMock).toHaveBeenCalled()
    expect(mainMocks.reloadSpy).toHaveBeenCalledTimes(2)
  })

  it('retries Telegram init later and still registers the service worker without waiting for window load', async () => {
    setReadyState('loading')

    await importFreshMain()

    expect(mainMocks.telegram.ready).not.toHaveBeenCalled()
    expect(mainMocks.registerSW).not.toHaveBeenCalled()

    setTelegram(true)
    getTimeoutByDelay(500)?.fn()

    expect(mainMocks.telegram.ready).toHaveBeenCalled()
    expect(mainMocks.telegram.expand).toHaveBeenCalled()
    expect(mainMocks.registerSW).not.toHaveBeenCalled()

    getTimeoutByDelay(250)?.fn()
    getTimeoutByDelay(250)?.fn()

    expect(mainMocks.registerSW).toHaveBeenCalledTimes(1)

    dispatchStoredListeners('load')
    expect(mainMocks.registerSW).toHaveBeenCalledTimes(1)
  })

  it('covers offline-ready logging, setup failures, Telegram init warnings, and storage-cleanup failures', async () => {
    setReadyState('complete')
    setTelegram(true)

    const logSpy = vi.spyOn(console, 'log').mockImplementation(() => undefined)
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined)

    mainMocks.telegram.ready.mockImplementationOnce(() => {
      throw new Error('telegram init failed')
    })
    mainMocks.registerSW.mockImplementationOnce((options: any) => {
      options.onOfflineReady()
      throw new Error('sw setup failed')
    })
    vi.spyOn(window.sessionStorage.__proto__, 'removeItem').mockImplementationOnce(() => {
      throw new Error('blocked storage')
    })

    await importFreshMain()
    getTimeoutByDelay(250)?.fn()

    expect(warnSpy).toHaveBeenCalledWith('Telegram WebApp not initialized', expect.any(Error))
    expect(logSpy).toHaveBeenCalledWith('App ready to work offline')
    expect(errorSpy).toHaveBeenCalledWith('SW setup error:', expect.any(Error))

    logSpy.mockRestore()
    warnSpy.mockRestore()
    errorSpy.mockRestore()
  })
})
