import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import CircleTimer from './CircleTimer.vue'

describe('CircleTimer.vue', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-05-14T12:00:00Z'))
  })

  it('renders the remaining time, computed dash array, and requested size', () => {
    const now = Math.floor(Date.now() / 1000)
    const wrapper = mount(CircleTimer, {
      props: {
        expiresAt: now + 65,
        totalDuration: 120,
        size: 40,
        showText: true,
      },
    })

    expect(wrapper.attributes('title')).toBe('1:05')
    expect(wrapper.get('.timer-text').text()).toBe('1:05')
    expect(wrapper.get('.timer-svg').attributes('width')).toBe('40')
    expect(wrapper.get('.circle').attributes('stroke-dasharray')).toContain('54.166')
  })

  it('emits expired once the countdown reaches zero and clears its interval on unmount', async () => {
    const now = Math.floor(Date.now() / 1000)
    const clearIntervalSpy = vi.spyOn(globalThis, 'clearInterval')
    const wrapper = mount(CircleTimer, {
      props: {
        expiresAt: now + 1,
      },
    })

    await vi.advanceTimersByTimeAsync(1000)

    expect(wrapper.emitted('expired')).toHaveLength(1)

    wrapper.unmount()
    expect(clearIntervalSpy).toHaveBeenCalled()
  })
})