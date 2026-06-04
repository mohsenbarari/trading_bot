import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createChatRoomLifecycleRuntime } from './chatRoomLifecycle'

describe('chatRoomLifecycle', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('runs room-specific cleanup when the active room changes', () => {
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn(),
    })
    const revokeSpy = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
    const clearTimeoutSpy = vi.spyOn(window, 'clearTimeout')
    const clearIntervalSpy = vi.spyOn(window, 'clearInterval')
    const cleanup = vi.fn()
    const lifecycle = createChatRoomLifecycleRuntime()

    lifecycle.enterRoom(10)
    lifecycle.trackRoomObjectUrl(10, 'blob:room-10')
    lifecycle.trackRoomTimer(10, 123)
    lifecycle.registerRoomCleanup(10, cleanup)

    lifecycle.enterRoom(11)

    expect(lifecycle.getActiveRoomKey()).toBe(11)
    expect(revokeSpy).toHaveBeenCalledWith('blob:room-10')
    expect(clearTimeoutSpy).toHaveBeenCalledWith(123)
    expect(clearIntervalSpy).toHaveBeenCalledWith(123)
    expect(cleanup).toHaveBeenCalledOnce()
  })
})
