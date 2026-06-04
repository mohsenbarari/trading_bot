import {
  getMessengerPerformanceName,
  markMessengerPerformance,
  measureMessengerPerformance,
} from './messengerRefactor'

export type MessengerMetricUnit = 'count' | 'ms' | 'fps' | 'bytes'

export type MessengerMetricMetadata = Record<string, string | number | boolean | null | undefined>

export interface MessengerMetricEntry {
  name: string
  value: number
  unit: MessengerMetricUnit
  timestamp: number
  metadata?: MessengerMetricMetadata
}

export interface MessengerDomSnapshot {
  totalNodes: number
  conversationCards: number
  messageBubbles: number
  mediaNodes: number
  overlayNodes: number
}

export interface MessengerFrameBudgetOptions {
  frameCount?: number
  jankThresholdMs?: number
}

export interface MessengerDiagnosticTaskOptions {
  timeoutMs?: number
  fallbackDelayMs?: number
  deferMs?: number
}

declare global {
  interface Window {
    __messengerDiagnosticsMetrics?: MessengerMetricEntry[]
  }
}

const MAX_METRIC_ENTRIES = 500

function now() {
  if (typeof performance !== 'undefined' && typeof performance.now === 'function') {
    return performance.now()
  }

  return Date.now()
}

function getMetricStore() {
  if (typeof window === 'undefined') {
    return null
  }

  window.__messengerDiagnosticsMetrics ||= []
  return window.__messengerDiagnosticsMetrics
}

function trimMetricStore(store: MessengerMetricEntry[]) {
  if (store.length <= MAX_METRIC_ENTRIES) {
    return
  }

  store.splice(0, store.length - MAX_METRIC_ENTRIES)
}

function runDiagnosticTask(callback: () => void) {
  try {
    callback()
  } catch {
    // Diagnostics only; scheduled probes must never affect Messenger runtime.
  }
}

export function scheduleMessengerDiagnosticTask(
  callback: () => void,
  options: MessengerDiagnosticTaskOptions = {},
) {
  if (typeof window === 'undefined') {
    runDiagnosticTask(callback)
    return false
  }

  const deferMs = Number(options.deferMs ?? 0)
  if (deferMs > 0) {
    window.setTimeout(() => {
      scheduleMessengerDiagnosticTask(callback, {
        ...options,
        deferMs: 0,
      })
    }, deferMs)
    return true
  }

  const win = window as Window & {
    requestIdleCallback?: (
      callback: () => void,
      options?: { timeout?: number },
    ) => number
  }

  if (typeof win.requestIdleCallback === 'function') {
    win.requestIdleCallback(() => runDiagnosticTask(callback), {
      timeout: options.timeoutMs ?? 750,
    })
    return true
  }

  window.setTimeout(() => runDiagnosticTask(callback), options.fallbackDelayMs ?? 96)
  return true
}

export function recordMessengerMetric(
  name: string,
  value: number,
  unit: MessengerMetricUnit = 'count',
  metadata?: MessengerMetricMetadata,
) {
  if (!Number.isFinite(value)) {
    return null
  }

  const entry: MessengerMetricEntry = {
    name,
    value,
    unit,
    timestamp: now(),
    metadata,
  }

  const store = getMetricStore()
  if (store) {
    store.push(entry)
    trimMetricStore(store)
    try {
      window.dispatchEvent(new CustomEvent('messenger:diagnostic-metric', { detail: entry }))
    } catch {
      // Diagnostics only; metric dispatch must never affect Messenger runtime.
    }
  }

  return entry
}

export function getMessengerMetricEntries() {
  return [...(getMetricStore() || [])]
}

export function clearMessengerMetricEntries() {
  const store = getMetricStore()
  if (store) {
    store.splice(0, store.length)
  }
}

export function markMessengerDiagnostic(name: string) {
  markMessengerPerformance(name)
  recordMessengerMetric(`${name}:mark`, now(), 'ms')
}

export function measureMessengerDiagnostic(
  name: string,
  startMark: string,
  endMark: string,
  metadata?: MessengerMetricMetadata,
) {
  const duration = measureMessengerPerformance(name, startMark, endMark)
  if (duration !== null) {
    recordMessengerMetric(name, duration, 'ms', metadata)
  }

  return duration
}

function queryCount(root: ParentNode, selector: string) {
  try {
    return root.querySelectorAll(selector).length
  } catch {
    return 0
  }
}

export function collectMessengerDomSnapshot(root: ParentNode = document): MessengerDomSnapshot {
  return {
    totalNodes: queryCount(root, '*'),
    conversationCards: queryCount(root, '.conversation-card, .conversation-item'),
    messageBubbles: queryCount(root, '.message-bubble, .message-row'),
    mediaNodes: queryCount(root, 'img, video, audio, canvas'),
    overlayNodes: queryCount(root, '.context-menu, .lightbox-overlay, .modal-overlay, .attachment-menu, .emoji-sticker-picker'),
  }
}

export function recordMessengerDomSnapshot(
  name: string,
  root: ParentNode = document,
  metadata?: MessengerMetricMetadata,
) {
  const snapshot = collectMessengerDomSnapshot(root)
  const metricMetadata = { ...metadata }

  recordMessengerMetric(`${name}:dom-total`, snapshot.totalNodes, 'count', metricMetadata)
  recordMessengerMetric(`${name}:conversation-cards`, snapshot.conversationCards, 'count', metricMetadata)
  recordMessengerMetric(`${name}:message-bubbles`, snapshot.messageBubbles, 'count', metricMetadata)
  recordMessengerMetric(`${name}:media-nodes`, snapshot.mediaNodes, 'count', metricMetadata)
  recordMessengerMetric(`${name}:overlay-nodes`, snapshot.overlayNodes, 'count', metricMetadata)

  return snapshot
}

export function startMessengerFrameBudgetProbe(
  name: string,
  options: MessengerFrameBudgetOptions = {},
) {
  if (typeof window === 'undefined' || typeof window.requestAnimationFrame !== 'function') {
    return false
  }

  const targetFrameCount = Math.max(2, Math.min(options.frameCount ?? 60, 240))
  const jankThresholdMs = options.jankThresholdMs ?? 50
  const frameDurations: number[] = []
  let previousTimestamp: number | null = null

  const step = (timestamp: number) => {
    if (previousTimestamp !== null) {
      frameDurations.push(timestamp - previousTimestamp)
    }
    previousTimestamp = timestamp

    if (frameDurations.length < targetFrameCount) {
      window.requestAnimationFrame(step)
      return
    }

    const total = frameDurations.reduce((sum, value) => sum + value, 0)
    const average = total / frameDurations.length
    const worst = Math.max(...frameDurations)
    const jankyFrames = frameDurations.filter(value => value >= jankThresholdMs).length
    const approximateFps = average > 0 ? 1000 / average : 0

    recordMessengerMetric(`${name}:frame-average`, average, 'ms', { frameCount: frameDurations.length })
    recordMessengerMetric(`${name}:frame-worst`, worst, 'ms', { frameCount: frameDurations.length })
    recordMessengerMetric(`${name}:frame-janky`, jankyFrames, 'count', { frameCount: frameDurations.length, jankThresholdMs })
    recordMessengerMetric(`${name}:frame-fps`, approximateFps, 'fps', { frameCount: frameDurations.length })
  }

  window.requestAnimationFrame(step)
  return true
}

export function getMessengerPerformanceEntries(name: string, type?: PerformanceEntry['entryType']) {
  if (typeof performance === 'undefined' || typeof performance.getEntriesByName !== 'function') {
    return []
  }

  return performance.getEntriesByName(getMessengerPerformanceName(name), type)
}
