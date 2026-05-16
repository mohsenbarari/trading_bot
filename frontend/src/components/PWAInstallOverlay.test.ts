import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import PWAInstallOverlay from './PWAInstallOverlay.vue'

const pwaOverlayMocks = vi.hoisted(() => ({
  isInstallable: { value: false },
  isInstalled: { value: false },
  installAppMock: vi.fn(),
}))

vi.mock('../utils/pwaInstall', () => ({
  usePWAInstall: () => ({
    isInstallable: pwaOverlayMocks.isInstallable,
    isInstalled: pwaOverlayMocks.isInstalled,
    installApp: pwaOverlayMocks.installAppMock,
  }),
}))

function setUserAgent(userAgent: string, standalone = false) {
  Object.defineProperty(window.navigator, 'userAgent', {
    configurable: true,
    value: userAgent,
  })
  Object.defineProperty(window.navigator, 'standalone', {
    configurable: true,
    value: standalone,
  })
  ;(window as any).MSStream = undefined
}

describe('PWAInstallOverlay.vue', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    localStorage.clear()
    pwaOverlayMocks.isInstallable.value = false
    pwaOverlayMocks.isInstalled.value = false
    pwaOverlayMocks.installAppMock.mockReset()
    pwaOverlayMocks.installAppMock.mockResolvedValue(true)
    vi.stubGlobal('alert', vi.fn())
    setUserAgent('Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/124 Safari/537.36')
  })

  it('shows the install overlay after the delay for installable non-iOS browsers and can be dismissed', async () => {
    pwaOverlayMocks.isInstallable.value = true

    const wrapper = mount(PWAInstallOverlay)

    expect(wrapper.find('.pwa-install-overlay').exists()).toBe(false)
    await vi.advanceTimersByTimeAsync(3000)
    await flushPromises()

    expect(wrapper.find('.pwa-install-overlay').exists()).toBe(true)
    await wrapper.get('.btn-dismiss').trigger('click')

    expect(wrapper.find('.pwa-install-overlay').exists()).toBe(false)
    expect(localStorage.getItem('pwa_prompt_dismissed')).toMatch(/^\d+$/)
  })

  it('does not reopen the prompt when it was dismissed less than a day ago', async () => {
    pwaOverlayMocks.isInstallable.value = true
    localStorage.setItem('pwa_prompt_dismissed', String(Date.now()))

    const wrapper = mount(PWAInstallOverlay)
    await vi.advanceTimersByTimeAsync(3000)
    await flushPromises()

    expect(wrapper.find('.pwa-install-overlay').exists()).toBe(false)
  })

  it('shows the iOS guide flow and alerts instead of calling installApp', async () => {
    setUserAgent('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1')

    const wrapper = mount(PWAInstallOverlay)
    await vi.advanceTimersByTimeAsync(3000)
    await flushPromises()

    expect(wrapper.find('.pwa-install-overlay').exists()).toBe(true)
    expect(wrapper.get('.btn-install').text()).toBe('راهنما')

    await wrapper.get('.btn-install').trigger('click')

    expect(pwaOverlayMocks.installAppMock).not.toHaveBeenCalled()
    expect(vi.mocked(alert)).toHaveBeenCalledTimes(1)
  })

  it('calls installApp on supported browsers and hides the overlay when installation succeeds', async () => {
    pwaOverlayMocks.isInstallable.value = true
    pwaOverlayMocks.installAppMock.mockResolvedValue(true)

    const wrapper = mount(PWAInstallOverlay)
    await vi.advanceTimersByTimeAsync(3000)
    await flushPromises()

    await wrapper.get('.btn-install').trigger('click')
    await flushPromises()

    expect(pwaOverlayMocks.installAppMock).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.pwa-install-overlay').exists()).toBe(false)
  })
})