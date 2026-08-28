import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import PWAInstallOverlay from './PWAInstallOverlay.vue'
import {
  resetSecurityLayerStateForTests,
  setSecurityLayerActive,
} from '../utils/securityLayerState'

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
  ;(window as Window & { MSStream?: unknown }).MSStream = undefined
}

function setOnline(online: boolean) {
  Object.defineProperty(window.navigator, 'onLine', {
    configurable: true,
    value: online,
  })
}

function getInstallLayer() {
  return document.body.querySelector<HTMLElement>('.ui-v2-pwa-install')
}

function getInstallAction(selector: string) {
  const action = document.body.querySelector<HTMLButtonElement>(selector)
  if (!action) throw new Error(`missing ${selector}`)
  return action
}

function mountEligiblePrompt() {
  return mount(PWAInstallOverlay, {
    props: { eligible: true },
    attachTo: document.body,
  })
}

describe('PWAInstallOverlay.vue', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    document.body.replaceChildren()
    localStorage.clear()
    pwaOverlayMocks.isInstallable.value = false
    pwaOverlayMocks.isInstalled.value = false
    pwaOverlayMocks.installAppMock.mockReset()
    pwaOverlayMocks.installAppMock.mockResolvedValue(true)
    resetSecurityLayerStateForTests()
    setOnline(true)
    setUserAgent('Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/124 Safari/537.36')
  })

  it('shows the install sheet after the delay for an eligible healthy home and can be dismissed', async () => {
    pwaOverlayMocks.isInstallable.value = true

    const wrapper = mountEligiblePrompt()

    expect(getInstallLayer()).toBeNull()
    await vi.advanceTimersByTimeAsync(4000)
    await flushPromises()

    expect(getInstallLayer()).not.toBeNull()
    expect(document.body.querySelector('.ui-bottom-sheet')).not.toBeNull()
    getInstallAction('.pwa-action-dismiss').click()
    await flushPromises()

    expect(getInstallLayer()).toBeNull()
    expect(localStorage.getItem(PROMPT_DISMISSED_KEY)).toMatch(/^\d+$/)
    wrapper.unmount()
  })

  it('dismisses the install sheet with Escape and keeps the quiet period', async () => {
    pwaOverlayMocks.isInstallable.value = true

    const wrapper = mountEligiblePrompt()
    await vi.advanceTimersByTimeAsync(4000)
    await flushPromises()

    expect(getInstallLayer()).not.toBeNull()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()

    expect(getInstallLayer()).toBeNull()
    expect(localStorage.getItem(PROMPT_DISMISSED_KEY)).toMatch(/^\d+$/)
    wrapper.unmount()
  })

  it('does not reopen the prompt when it was dismissed less than a day ago', async () => {
    pwaOverlayMocks.isInstallable.value = true
    localStorage.setItem(PROMPT_DISMISSED_KEY, String(Date.now()))

    const wrapper = mountEligiblePrompt()
    await vi.advanceTimersByTimeAsync(4000)
    await flushPromises()

    expect(getInstallLayer()).toBeNull()
    wrapper.unmount()
  })

  it('shows the prompt when installability becomes available after the initial delay', async () => {
    const wrapper = mountEligiblePrompt()
    await vi.advanceTimersByTimeAsync(4000)
    await flushPromises()

    expect(getInstallLayer()).toBeNull()

    pwaOverlayMocks.isInstallable.value = true
    window.dispatchEvent(new Event('pwa-install-ready'))
    await flushPromises()

    expect(getInstallLayer()).not.toBeNull()
    wrapper.unmount()
  })

  it('shows the iOS guide inline instead of calling installApp', async () => {
    setUserAgent(
      'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1',
    )

    const wrapper = mountEligiblePrompt()
    await vi.advanceTimersByTimeAsync(4000)
    await flushPromises()

    expect(getInstallLayer()).not.toBeNull()
    expect(getInstallAction('.pwa-action-install').textContent).toContain('راهنما')

    getInstallAction('.pwa-action-install').click()
    await flushPromises()

    expect(pwaOverlayMocks.installAppMock).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('در سافاری دکمه اشتراک را بزنید')
    wrapper.unmount()
  })

  it('calls installApp on supported browsers and hides the overlay when installation succeeds', async () => {
    pwaOverlayMocks.isInstallable.value = true
    pwaOverlayMocks.installAppMock.mockResolvedValue(true)

    const wrapper = mountEligiblePrompt()
    await vi.advanceTimersByTimeAsync(4000)
    await flushPromises()

    getInstallAction('.pwa-action-install').click()
    await flushPromises()

    expect(pwaOverlayMocks.installAppMock).toHaveBeenCalledTimes(1)
    expect(getInstallLayer()).toBeNull()
    wrapper.unmount()
  })

  it('closes and applies the quiet period when the browser install prompt is dismissed', async () => {
    pwaOverlayMocks.isInstallable.value = true
    pwaOverlayMocks.installAppMock.mockResolvedValue(false)

    const wrapper = mountEligiblePrompt()
    await vi.advanceTimersByTimeAsync(4000)
    await flushPromises()

    getInstallAction('.pwa-action-install').click()
    await flushPromises()

    expect(pwaOverlayMocks.installAppMock).toHaveBeenCalledTimes(1)
    expect(getInstallLayer()).toBeNull()
    expect(localStorage.getItem(PROMPT_DISMISSED_KEY)).toMatch(/^\d+$/)
    wrapper.unmount()
  })

  it('fails safely without an unhandled error when the browser prompt rejects', async () => {
    pwaOverlayMocks.isInstallable.value = true
    pwaOverlayMocks.installAppMock.mockRejectedValue(new Error('browser prompt unavailable'))

    const wrapper = mountEligiblePrompt()
    await vi.advanceTimersByTimeAsync(4000)
    await flushPromises()

    getInstallAction('.pwa-action-install').click()
    await flushPromises()

    expect(getInstallLayer()).toBeNull()
    expect(localStorage.getItem(PROMPT_DISMISSED_KEY)).toMatch(/^\d+$/)
    wrapper.unmount()
  })

  it('keeps prompt gating safe when browser storage is unavailable', async () => {
    pwaOverlayMocks.isInstallable.value = true
    const getItemSpy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage blocked')
    })

    const wrapper = mountEligiblePrompt()
    await vi.advanceTimersByTimeAsync(4000)
    await flushPromises()
    expect(getInstallLayer()).not.toBeNull()
    getItemSpy.mockRestore()

    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage blocked')
    })
    getInstallAction('.pwa-action-dismiss').click()
    await flushPromises()
    expect(getInstallLayer()).toBeNull()
    setItemSpy.mockRestore()
    wrapper.unmount()
  })

  it('never opens when Home is ineligible, offline, or a security layer is active', async () => {
    pwaOverlayMocks.isInstallable.value = true

    const ineligible = mount(PWAInstallOverlay, { attachTo: document.body })
    await vi.advanceTimersByTimeAsync(4000)
    expect(getInstallLayer()).toBeNull()
    ineligible.unmount()

    setOnline(false)
    const offline = mountEligiblePrompt()
    await vi.advanceTimersByTimeAsync(4000)
    expect(getInstallLayer()).toBeNull()
    offline.unmount()

    setOnline(true)
    setSecurityLayerActive('session-approval', true)
    const blocked = mountEligiblePrompt()
    await vi.advanceTimersByTimeAsync(4000)
    expect(getInstallLayer()).toBeNull()
    blocked.unmount()
  })

  it('closes immediately when connectivity is lost or a security layer opens', async () => {
    pwaOverlayMocks.isInstallable.value = true
    const wrapper = mountEligiblePrompt()
    await vi.advanceTimersByTimeAsync(4000)
    await flushPromises()
    expect(getInstallLayer()).not.toBeNull()

    setOnline(false)
    window.dispatchEvent(new Event('offline'))
    await flushPromises()
    expect(getInstallLayer()).toBeNull()

    setOnline(true)
    window.dispatchEvent(new Event('online'))
    await flushPromises()
    expect(getInstallLayer()).not.toBeNull()

    setSecurityLayerActive('session-approval', true)
    await flushPromises()
    expect(getInstallLayer()).toBeNull()
    wrapper.unmount()
  })
})
