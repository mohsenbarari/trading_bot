import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AudioRecorder } from './audioRecorder'

type FakeRecorderListener = (event?: { data: Blob }) => void

describe('AudioRecorder', () => {
  const originalMediaDevices = navigator.mediaDevices
  const originalMediaRecorder = globalThis.MediaRecorder

  let trackStop: ReturnType<typeof vi.fn>
  let getUserMedia: ReturnType<typeof vi.fn>
  let fakeStream: MediaStream
  let lastRecorder: FakeMediaRecorder | null = null

  class FakeMediaRecorder {
    state: 'inactive' | 'recording' = 'inactive'
    mimeType = 'audio/webm'
    start = vi.fn((timeslice?: number) => {
      expect(timeslice).toBe(100)
      this.state = 'recording'
    })
    stop = vi.fn(() => {
      this.state = 'inactive'
      this.listeners.stop.forEach((listener) => listener())
    })
    private listeners: Record<'dataavailable' | 'stop', FakeRecorderListener[]> = {
      dataavailable: [],
      stop: [],
    }

    constructor(_stream: MediaStream) {
      lastRecorder = this
    }

    addEventListener(type: 'dataavailable' | 'stop', listener: FakeRecorderListener) {
      this.listeners[type].push(listener)
    }

    emitData(blob: Blob) {
      this.listeners.dataavailable.forEach((listener) => listener({ data: blob }))
    }
  }

  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2024-01-01T00:00:00Z'))
    vi.spyOn(console, 'error').mockImplementation(() => {})
    lastRecorder = null
    trackStop = vi.fn()
    fakeStream = {
      getTracks: vi.fn(() => [{ stop: trackStop }]),
    } as unknown as MediaStream
    getUserMedia = vi.fn(async () => fakeStream)

    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia },
    })
    Object.defineProperty(globalThis, 'MediaRecorder', {
      configurable: true,
      value: FakeMediaRecorder,
    })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()

    if (originalMediaDevices) {
      Object.defineProperty(navigator, 'mediaDevices', {
        configurable: true,
        value: originalMediaDevices,
      })
    }

    if (originalMediaRecorder) {
      Object.defineProperty(globalThis, 'MediaRecorder', {
        configurable: true,
        value: originalMediaRecorder,
      })
    }
  })

  it('starts recording, emits timer updates, and resolves a blob on stop', async () => {
    const onTimeUpdate = vi.fn()
    const recorder = new AudioRecorder(onTimeUpdate)

    await recorder.start()

    expect(getUserMedia).toHaveBeenCalledWith({ audio: true })
    expect(lastRecorder?.start).toHaveBeenCalledWith(100)
    expect(recorder.isRecording).toBe(true)

    vi.advanceTimersByTime(250)
    expect(onTimeUpdate).toHaveBeenNthCalledWith(1, 100)
    expect(onTimeUpdate).toHaveBeenNthCalledWith(2, 200)

    lastRecorder?.emitData(new Blob(['chunk-1'], { type: 'audio/webm' }))
    lastRecorder?.emitData(new Blob([], { type: 'audio/webm' }))

    const audioBlob = await recorder.stop()

    expect(audioBlob).toBeInstanceOf(Blob)
    expect(audioBlob?.type).toBe('audio/webm')
    expect(audioBlob?.size).toBeGreaterThan(0)
    expect(recorder.isRecording).toBe(false)
    expect(trackStop).toHaveBeenCalledTimes(1)
  })

  it('returns null from stop when recording never started or is already inactive', async () => {
    const recorder = new AudioRecorder(vi.fn())
    await expect(recorder.stop()).resolves.toBeNull()

    await recorder.start()
    lastRecorder!.state = 'inactive'
    await expect(recorder.stop()).resolves.toBeNull()
  })

  it('cancels an active recording, clears state, and stops the media track', async () => {
    const onTimeUpdate = vi.fn()
    const recorder = new AudioRecorder(onTimeUpdate)

    await recorder.start()
    recorder.cancel()
    vi.advanceTimersByTime(300)

    expect(lastRecorder?.stop).toHaveBeenCalledTimes(1)
    expect(trackStop).toHaveBeenCalledTimes(1)
    expect(recorder.isRecording).toBe(false)
    expect(onTimeUpdate).not.toHaveBeenCalled()
  })

  it('rethrows microphone access errors after logging them', async () => {
    const failure = new Error('permission denied')
    getUserMedia.mockRejectedValueOnce(failure)
    const recorder = new AudioRecorder(vi.fn())

    await expect(recorder.start()).rejects.toThrow('permission denied')
    expect(console.error).toHaveBeenCalledWith('Error accessing microphone:', failure)
  })
})