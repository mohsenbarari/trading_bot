type MediaPreprocessPath =
  | 'image_worker'
  | 'image_main_thread'
  | 'image_legacy_fallback'
  | 'image_edited_passthrough'
  | 'video_preview'
  | 'video_metadata_fallback'
  | 'voice_passthrough'
  | 'document_passthrough'

type MediaPreprocessStatus = 'success' | 'failed' | 'cancelled'

export type MediaPreprocessTelemetryEvent = {
  userId: number
  mediaType: 'image' | 'video' | 'voice'
  path: MediaPreprocessPath
  status: MediaPreprocessStatus
  durationMs: number
  batchSize: number
  schedulerLimit: number
  usedWorker: boolean
  fallbackReason?: string
  width?: number
  height?: number
  errorMessage?: string
  timestamp: string
}

type MediaPreprocessTelemetrySummary = {
  total: number
  byPath: Record<string, number>
  byStatus: Record<string, number>
  fallbackReasons: Record<string, number>
  averageDurationMs: number
  slowestDurationMs: number
  lastEvent: MediaPreprocessTelemetryEvent | null
}

type MediaPreprocessTelemetryStore = {
  userId: number
  recentEvents: MediaPreprocessTelemetryEvent[]
  summary: MediaPreprocessTelemetrySummary
}

declare global {
  interface Window {
    __chatMediaTelemetry?: MediaPreprocessTelemetryStore
  }
}

const STORAGE_KEY = 'chat_media_preprocess_telemetry'
const MAX_EVENTS = 40

function createEmptySummary(): MediaPreprocessTelemetrySummary {
  return {
    total: 0,
    byPath: {},
    byStatus: {},
    fallbackReasons: {},
    averageDurationMs: 0,
    slowestDurationMs: 0,
    lastEvent: null,
  }
}

function safeReadStore(): MediaPreprocessTelemetryStore | null {
  if (typeof window === 'undefined') return null

  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    return JSON.parse(raw) as MediaPreprocessTelemetryStore
  } catch {
    return null
  }
}

function safeWriteStore(store: MediaPreprocessTelemetryStore) {
  if (typeof window === 'undefined') return

  window.__chatMediaTelemetry = store

  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(store))
  } catch {
    // Ignore storage failures on low-storage/private contexts.
  }
}

function nextAverage(previousAverage: number, count: number, nextValue: number) {
  if (count <= 1) return nextValue
  return ((previousAverage * (count - 1)) + nextValue) / count
}

export function recordMediaPreprocessTelemetry(event: MediaPreprocessTelemetryEvent) {
  const previous = safeReadStore()
  const baseStore: MediaPreprocessTelemetryStore = previous && previous.userId === event.userId
    ? previous
    : {
        userId: event.userId,
        recentEvents: [],
        summary: createEmptySummary(),
      }

  const total = baseStore.summary.total + 1
  const nextStore: MediaPreprocessTelemetryStore = {
    userId: event.userId,
    recentEvents: [...baseStore.recentEvents, event].slice(-MAX_EVENTS),
    summary: {
      total,
      byPath: {
        ...baseStore.summary.byPath,
        [event.path]: (baseStore.summary.byPath[event.path] || 0) + 1,
      },
      byStatus: {
        ...baseStore.summary.byStatus,
        [event.status]: (baseStore.summary.byStatus[event.status] || 0) + 1,
      },
      fallbackReasons: event.fallbackReason
        ? {
            ...baseStore.summary.fallbackReasons,
            [event.fallbackReason]: (baseStore.summary.fallbackReasons[event.fallbackReason] || 0) + 1,
          }
        : baseStore.summary.fallbackReasons,
      averageDurationMs: nextAverage(baseStore.summary.averageDurationMs, total, event.durationMs),
      slowestDurationMs: Math.max(baseStore.summary.slowestDurationMs, event.durationMs),
      lastEvent: event,
    },
  }

  safeWriteStore(nextStore)
}

export function primeMediaPreprocessTelemetry() {
  const existing = safeReadStore()
  if (existing) {
    window.__chatMediaTelemetry = existing
  }
}