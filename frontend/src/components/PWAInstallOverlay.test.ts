import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import PWAInstallOverlay from './PWAInstallOverlay.vue'

const pwaOverlayMocks = vi.hoisted(() => ({
  isInstallable: { value: false },
  isInstalled: { value: false },
  installAppMock: vi.fn(),
}))

const PROMPT_DISMISSED_KEY = 'pwa_install_prompt_dismissed_at_v2'

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
    setUserAgent('Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/124 Safari/537.36')
  })

  it('shows the install overlay after the delay for installable non-iOS browsers and can be dismissed', async () => {
    pwaOverlayMocks.isInstallable.value = true

    const wrapper = mount(PWAInstallOverlay)

    expect(wrapper.find('.pwa-install-overlay').exists()).toBe(false)
    await vi.advanceTimersByTimeAsync(4000)
    await flushPromises()

    expect(wrapper.find('.pwa-install-overlay').exists()).toBe(true)
    await wrapper.get('.pwa-action-dismiss').trigger('click')

    expect(wrapper.find('.pwa-install-overlay').exists()).toBe(false)
    expect(localStorage.getItem(PROMPT_DISMISSED_KEY)).toMatch(/^\d+$/)
  })

  it('does not reopen the prompt when it was dismissed less than a day ago', async () => {
    pwaOverlayMocks.isInstallable.value = true
    localStorage.setItem(PROMPT_DISMISSED_KEY, String(Date.now()))

    const wrapper = mount(PWAInstallOverlay)
    await vi.advanceTimersByTimeAsync(4000)
    await flushPromises()

    expect(wrapper.find('.pwa-install-overlay').exists()).toBe(false)
  })

  it('shows the prompt when installability becomes available after the initial delay', async () => {
    const wrapper = mount(PWAInstallOverlay)
    await vi.advanceTimersByTimeAsync(4000)
    await flushPromises()

    expect(wrapper.find('.pwa-install-overlay').exists()).toBe(false)

    pwaOverlayMocks.isInstallable.value = true
    window.dispatchEvent(new Event('pwa-install-ready'))
    await flushPromises()

    expect(wrapper.find('.pwa-install-overlay').exists()).toBe(true)
  })

  it('shows the iOS guide inline instead of calling installApp', async () => {
    setUserAgent('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1')

    const wrapper = mount(PWAInstallOverlay)
    await vi.advanceTimersByTimeAsync(4000)
    await flushPromises()

    expect(wrapper.find('.pwa-install-overlay').exists()).toBe(true)
    expect(wrapper.get('.pwa-action-install').text()).toBe('راهنما')

    await wrapper.get('.pwa-action-install').trigger('click')

    expect(pwaOverlayMocks.installAppMock).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('در Safari دکمه Share را بزنید')
  })

  it('calls installApp on supported browsers and hides the overlay when installation succeeds', async () => {
    pwaOverlayMocks.isInstallable.value = true
    pwaOverlayMocks.installAppMock.mockResolvedValue(true)

    const wrapper = mount(PWAInstallOverlay)
    await vi.advanceTimersByTimeAsync(4000)
    await flushPromises()

    await wrapper.get('.pwa-action-install').trigger('click')
    await flushPromises()

    expect(pwaOverlayMocks.installAppMock).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.pwa-install-overlay').exists()).toBe(false)
  })
})
