import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  clearMessengerMetricEntries,
  collectMessengerDomSnapshot,
  getMessengerMetricEntries,
  recordMessengerDomSnapshot,
  recordMessengerMetric,
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
})