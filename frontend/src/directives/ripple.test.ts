import { afterEach, describe, expect, it, vi } from 'vitest'
import { vRipple } from './ripple'

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

describe('vRipple', () => {
  it('adds ripple markup, reuses a single ripple element, and removes it after the animation window', async () => {
    vi.useFakeTimers()
    const element = document.createElement('button')
    document.body.appendChild(element)
    vi.spyOn(element, 'getBoundingClientRect').mockReturnValue({
      width: 120,
      height: 48,
      left: 10,
      top: 20,
      right: 130,
      bottom: 68,
      x: 10,
      y: 20,
      toJSON: () => ({}),
    })

    vRipple.mounted(element, { value: 'rgba(10, 20, 30, 0.4)' } as never)
    expect(element.classList.contains('ripple-container')).toBe(true)

    element.dispatchEvent(new MouseEvent('mousedown', { clientX: 30, clientY: 40, bubbles: true }))
    let ripples = element.querySelectorAll('.ripple-effect')
    expect(ripples).toHaveLength(1)
    expect((ripples[0] as HTMLElement).style.width).toBe('120px')
    expect((ripples[0] as HTMLElement).style.backgroundColor).not.toBe('')

    element.dispatchEvent(new MouseEvent('mousedown', { clientX: 50, clientY: 50, bubbles: true }))
    ripples = element.querySelectorAll('.ripple-effect')
    expect(ripples).toHaveLength(1)

    await vi.advanceTimersByTimeAsync(600)
    expect(element.querySelectorAll('.ripple-effect')).toHaveLength(0)
  })
})