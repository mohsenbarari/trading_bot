import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

type FakeOscillator = {
  type: string
  frequency: { setValueAtTime: ReturnType<typeof vi.fn> }
  connect: ReturnType<typeof vi.fn>
  start: ReturnType<typeof vi.fn>
  stop: ReturnType<typeof vi.fn>
}

type FakeGain = {
  gain: {
    setValueAtTime: ReturnType<typeof vi.fn>
    linearRampToValueAtTime: ReturnType<typeof vi.fn>
    exponentialRampToValueAtTime: ReturnType<typeof vi.fn>
  }
  connect: ReturnType<typeof vi.fn>
}

describe('audio utilities', () => {
  const originalAudioContext = window.AudioContext
  const originalWebkitAudioContext = (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext

  let lastContext: FakeAudioContext | null = null

  class FakeAudioContext {
    state: 'running' | 'suspended' = 'suspended'
    currentTime = 1
    destination = { kind: 'destination' }
    createdSources: Array<{ buffer: AudioBuffer | null; connect: ReturnType<typeof vi.fn>; start: ReturnType<typeof vi.fn> }> = []
    createdOscillators: FakeOscillator[] = []
    createdGains: FakeGain[] = []
    resume = vi.fn(async () => {
      this.state = 'running'
    })
    createBuffer = vi.fn(() => ({}) as AudioBuffer)
    createBufferSource = vi.fn(() => {
      const source = {
        buffer: null as AudioBuffer | null,
        connect: vi.fn(),
        start: vi.fn(),
      }
      this.createdSources.push(source)
      return source
    })
    createOscillator = vi.fn(() => {
      const oscillator: FakeOscillator = {
        type: 'sine',
        frequency: { setValueAtTime: vi.fn() },
        connect: vi.fn(),
        start: vi.fn(),
        stop: vi.fn(),
      }
      this.createdOscillators.push(oscillator)
      return oscillator as unknown as OscillatorNode
    })
    createGain = vi.fn(() => {
      const gain: FakeGain = {
        gain: {
          setValueAtTime: vi.fn(),
          linearRampToValueAtTime: vi.fn(),
          exponentialRampToValueAtTime: vi.fn(),
        },
        connect: vi.fn(),
      }
      this.createdGains.push(gain)
      return gain as unknown as GainNode
    })

    constructor() {
      lastContext = this
    }
  }

  const installAudioContext = (mode: 'standard' | 'webkit') => {
    lastContext = null
    if (mode === 'standard') {
      Object.defineProperty(window, 'AudioContext', {
        configurable: true,
        value: FakeAudioContext,
      })
      Object.defineProperty(window, 'webkitAudioContext', {
        configurable: true,
        value: undefined,
      })
      return
    }

    Object.defineProperty(window, 'AudioContext', {
      configurable: true,
      value: undefined,
    })
    Object.defineProperty(window, 'webkitAudioContext', {
      configurable: true,
      value: FakeAudioContext,
    })
  }

  beforeEach(() => {
    vi.resetModules()
    vi.spyOn(console, 'log').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.restoreAllMocks()
    Object.defineProperty(window, 'AudioContext', {
      configurable: true,
      value: originalAudioContext,
    })
    Object.defineProperty(window, 'webkitAudioContext', {
      configurable: true,
      value: originalWebkitAudioContext,
    })
    lastContext = null
  })

  it('does nothing before unlock, then unlocks the standard AudioContext with a silent buffer', async () => {
    installAudioContext('standard')
    const audioModule = await import('./audio')

    expect(() => audioModule.playNotificationSound()).not.toThrow()
    await audioModule.unlockAudioContext()

    expect(lastContext).not.toBeNull()
    expect(lastContext?.resume).toHaveBeenCalledTimes(1)
    expect(lastContext?.createBuffer).toHaveBeenCalledWith(1, 1, 22050)
    expect(lastContext?.createBufferSource).toHaveBeenCalledTimes(1)
    expect(lastContext?.createdSources[0]?.connect).toHaveBeenCalledWith(lastContext?.destination)
    expect(lastContext?.createdSources[0]?.start).toHaveBeenCalledWith(0)
    expect(audioModule.resumeAudioContext).toBe(audioModule.unlockAudioContext)
    expect(console.log).toHaveBeenCalledWith('AudioContext unlocked. State:', 'running')
  })

  it('uses the webkit fallback and plays both notification notes with the expected timing envelope', async () => {
    installAudioContext('webkit')
    const audioModule = await import('./audio')

    await audioModule.unlockAudioContext()
    expect(lastContext).not.toBeNull()

    lastContext!.state = 'suspended'
    audioModule.playNotificationSound()

    expect(lastContext?.resume).toHaveBeenCalledTimes(2)
    expect(lastContext?.createOscillator).toHaveBeenCalledTimes(2)
    expect(lastContext?.createGain).toHaveBeenCalledTimes(2)

    const [firstOscillator, secondOscillator] = lastContext!.createdOscillators
    const [firstGain, secondGain] = lastContext!.createdGains
    if (!firstOscillator || !secondOscillator || !firstGain || !secondGain) throw new Error('mock setup failed')

    expect(firstOscillator.frequency.setValueAtTime).toHaveBeenCalledWith(783.99, 1.05)
    expect(firstOscillator.start).toHaveBeenCalledWith(1.05)
    expect(firstOscillator.stop).toHaveBeenCalledWith(1.2)
    expect(firstGain.gain.setValueAtTime).toHaveBeenCalledWith(0, 1.05)
    expect(firstGain.gain.linearRampToValueAtTime).toHaveBeenCalledWith(0.1, 1.06)
    expect(firstGain.gain.exponentialRampToValueAtTime).toHaveBeenCalledWith(0.001, 1.2)

    expect(secondOscillator.frequency.setValueAtTime).toHaveBeenCalledWith(1046.5, 1.1500000000000001)
    expect(secondOscillator.start).toHaveBeenCalledWith(1.1500000000000001)
    expect(secondOscillator.stop).toHaveBeenCalledWith(1.4500000000000002)
    expect(secondGain.gain.setValueAtTime).toHaveBeenCalledWith(0, 1.1500000000000001)
    expect(secondGain.gain.linearRampToValueAtTime).toHaveBeenCalledWith(0.1, 1.1600000000000001)
    expect(secondGain.gain.exponentialRampToValueAtTime).toHaveBeenCalledWith(0.001, 1.4500000000000002)
  })
})