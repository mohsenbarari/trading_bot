import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  clearMessengerMetricEntries,
  collectMessengerDomSnapshot,
  getMessengerMetricEntries,
  recordMessengerDomSnapshot,
  recordMessengerMetric,
  scheduleMessengerDiagnosticTask,
  startMessengerFrameBudgetProbe,
} from './messengerStage2Metrics'

describe('messengerStage2Metrics', () => {
  beforeEach(() => {
    clearMessengerMetricEntries()
    document.body.innerHTML = ''
  })

  afterEach(() => {
    clearMessengerMetricEntries()
    document.body.innerHTML = ''
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('records bounded numeric metric entries without affecting runtime callers', () => {
    const entry = recordMessengerMetric('chat-open-request', 42, 'ms', { roomKind: 'direct' })

    expect(entry).toMatchObject({ name: 'chat-open-request', value: 42, unit: 'ms' })
    expect(getMessengerMetricEntries()).toHaveLength(1)
    expect(recordMessengerMetric('bad', Number.NaN, 'ms')).toBeNull()
    expect(getMessengerMetricEntries()).toHaveLength(1)
  })

  it('captures Messenger DOM baseline counts for hot path selectors', () => {
    document.body.innerHTML = `
      <main class="chat-view">
        <div class="conversation-card"></div>
        <div class="message-bubble"><img alt="sample" /></div>
        <div class="lightbox-overlay"></div>
      </main>
    `

    const snapshot = collectMessengerDomSnapshot(document.body)
    expect(snapshot.conversationCards).toBe(1)
    expect(snapshot.messageBubbles).toBe(1)
    expect(snapshot.mediaNodes).toBe(1)
    expect(snapshot.overlayNodes).toBe(1)

    recordMessengerDomSnapshot('surface-ready', document.body)
    expect(getMessengerMetricEntries().map(entry => entry.name)).toContain('surface-ready:dom-total')
  })

  it('samples a small frame budget probe and records jank/fps metrics', () => {
    const callbacks: FrameRequestCallback[] = []
    const rafSpy = vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback: FrameRequestCallback) => {
      callbacks.push(callback)
      return callbacks.length
    })

    expect(startMessengerFrameBudgetProbe('messenger-scroll', { frameCount: 2, jankThresholdMs: 30 })).toBe(true)

    callbacks.shift()?.(0)
    callbacks.shift()?.(16)
    callbacks.shift()?.(64)

    expect(rafSpy).toHaveBeenCalled()
    const metricNames = getMessengerMetricEntries().map(entry => entry.name)
    expect(metricNames).toContain('messenger-scroll:frame-average')
    expect(metricNames).toContain('messenger-scroll:frame-janky')
  })

  it('schedules diagnostic tasks during idle when the browser supports it', () => {
    const callback = vi.fn()
    const requestIdleCallback = vi.fn((runner: () => void) => {
      expect(callback).not.toHaveBeenCalled()
      runner()
      return 1
    })
    vi.stubGlobal('requestIdleCallback', requestIdleCallback)

    expect(scheduleMessengerDiagnosticTask(callback, { timeoutMs: 250 })).toBe(true)

    expect(requestIdleCallback).toHaveBeenCalledWith(expect.any(Function), { timeout: 250 })
    expect(callback).toHaveBeenCalledTimes(1)
  })

  it('falls back to a short timeout for diagnostic tasks without idle callback', () => {
    vi.useFakeTimers()
    const callback = vi.fn()

    expect(scheduleMessengerDiagnosticTask(callback, { fallbackDelayMs: 32 })).toBe(true)
    expect(callback).not.toHaveBeenCalled()

    vi.advanceTimersByTime(31)
    expect(callback).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(callback).toHaveBeenCalledTimes(1)
    vi.useRealTimers()
  })

  it('defers diagnostic tasks before idle scheduling when requested', () => {
    vi.useFakeTimers()
    const callback = vi.fn()
    const requestIdleCallback = vi.fn((runner: () => void) => {
      runner()
      return 1
    })
    vi.stubGlobal('requestIdleCallback', requestIdleCallback)

    expect(scheduleMessengerDiagnosticTask(callback, { deferMs: 120, timeoutMs: 250 })).toBe(true)
    expect(requestIdleCallback).not.toHaveBeenCalled()
    expect(callback).not.toHaveBeenCalled()

    vi.advanceTimersByTime(119)
    expect(requestIdleCallback).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(requestIdleCallback).toHaveBeenCalledWith(expect.any(Function), { timeout: 250 })
    expect(callback).toHaveBeenCalledTimes(1)
    vi.useRealTimers()
  })
})
