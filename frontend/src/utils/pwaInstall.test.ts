import { beforeEach, describe, expect, it, vi } from 'vitest'

function stubMatchMedia(matches: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn().mockReturnValue({
      matches,
      media: '(display-mode: standalone)',
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }),
  })
}

function setStandalone(standalone: boolean) {
  Object.defineProperty(window.navigator, 'standalone', {
    configurable: true,
    value: standalone,
  })
}

function makeBeforeInstallPromptEvent(outcome: 'accepted' | 'dismissed') {
  const event = new Event('beforeinstallprompt') as Event & {
    prompt: ReturnType<typeof vi.fn>
    userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>
    preventDefault: ReturnType<typeof vi.fn>
  }
  event.prompt = vi.fn()
  event.userChoice = Promise.resolve({ outcome })
  event.preventDefault = vi.fn()
  return event
}

async function importFreshModule() {
  vi.resetModules()
  return import('./pwaInstall')
}

describe('pwaInstall', () => {
  beforeEach(() => {
    stubMatchMedia(false)
    setStandalone(false)
  })

  it('marks the app as installed on load when the browser is already standalone', async () => {
    setStandalone(true)
    const module = await importFreshModule()
    const pwa = module.usePWAInstall()

    window.dispatchEvent(new Event('load'))

    expect(pwa.isInstalled.value).toBe(true)
  })

  it('stores beforeinstallprompt, prompts the user, and reacts to appinstalled', async () => {
    const module = await importFreshModule()
    const pwa = module.usePWAInstall()
    const installEvent = makeBeforeInstallPromptEvent('accepted')

    window.dispatchEvent(installEvent)
    expect(installEvent.preventDefault).toHaveBeenCalled()
    expect(pwa.isInstallable.value).toBe(true)

    await expect(pwa.installApp()).resolves.toBe(true)
    expect(installEvent.prompt).toHaveBeenCalledTimes(1)
    expect(pwa.isInstallable.value).toBe(false)

    window.dispatchEvent(new Event('appinstalled'))
    expect(pwa.isInstalled.value).toBe(true)
  })

  it('returns false when there is no deferred prompt or the user dismisses it', async () => {
    const module = await importFreshModule()
    const pwa = module.usePWAInstall()

    await expect(pwa.installApp()).resolves.toBe(false)

    const installEvent = makeBeforeInstallPromptEvent('dismissed')
    window.dispatchEvent(installEvent)

    await expect(pwa.installApp()).resolves.toBe(false)
    expect(installEvent.prompt).toHaveBeenCalledTimes(1)
  })
})