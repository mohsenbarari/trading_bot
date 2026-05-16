import { defineComponent, h } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearBackStack, discardBackState, popBackState, pushBackState, useBackButton } from './useBackButton'

describe('useBackButton', () => {
  let pushStateSpy: ReturnType<typeof vi.spyOn>
  let historyBackSpy: ReturnType<typeof vi.spyOn>
  const telegramBackButton = {
    show: vi.fn(),
    hide: vi.fn(),
    onClick: vi.fn(),
  }

  beforeEach(() => {
    pushStateSpy = vi.spyOn(window.history, 'pushState')
    historyBackSpy = vi.spyOn(window.history, 'back').mockImplementation(() => undefined)
    ;(window as any).Telegram = { WebApp: { BackButton: telegramBackButton } }
    telegramBackButton.show.mockClear()
    telegramBackButton.hide.mockClear()
    telegramBackButton.onClick.mockClear()
    clearBackStack()
  })

  afterEach(() => {
    clearBackStack()
    pushStateSpy.mockRestore()
    historyBackSpy.mockRestore()
    delete (window as any).Telegram
  })

  it('pushes back states, mirrors them into history, and handles browser popstate', () => {
    const onBack = vi.fn()

    pushBackState(onBack)

    expect(pushStateSpy).toHaveBeenCalled()
    expect(telegramBackButton.show).toHaveBeenCalled()
    expect(telegramBackButton.onClick).toHaveBeenCalled()

    window.dispatchEvent(new PopStateEvent('popstate'))

    expect(onBack).toHaveBeenCalledTimes(1)
    expect(telegramBackButton.hide).toHaveBeenCalled()
  })

  it('drops the top back state without running its callback when the UI already closed itself', () => {
    const onBack = vi.fn()
    pushBackState(onBack)

    popBackState()
    expect(historyBackSpy).toHaveBeenCalledTimes(1)

    window.dispatchEvent(new PopStateEvent('popstate'))
    expect(onBack).not.toHaveBeenCalled()
  })

  it('supports discarding and clearing internal states without touching browser history', () => {
    const first = vi.fn()
    const second = vi.fn()
    pushBackState(first)
    pushBackState(second)

    discardBackState()
    window.dispatchEvent(new PopStateEvent('popstate'))
    expect(second).not.toHaveBeenCalled()
    expect(first).toHaveBeenCalledTimes(1)

    pushBackState(first)
    clearBackStack()
    expect(telegramBackButton.hide).toHaveBeenCalled()
  })

  it('starts the shared listener when the composable is mounted', () => {
    const Harness = defineComponent({
      setup() {
        useBackButton()
        return () => h('div')
      },
    })

    const wrapper = mount(Harness)
    pushBackState(vi.fn())

    expect(wrapper.html()).toBe('<div></div>')
    expect(telegramBackButton.show).toHaveBeenCalled()
  })
})