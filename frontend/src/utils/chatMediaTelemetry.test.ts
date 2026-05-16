import { afterEach, describe, expect, it, vi } from 'vitest'

const STORAGE_KEY = 'chat_media_preprocess_telemetry'

const baseEvent = {
  userId: 41,
  mediaType: 'image' as const,
  path: 'image_worker' as const,
  status: 'success' as const,
  durationMs: 120,
  batchSize: 3,
  schedulerLimit: 2,
  usedWorker: true,
  timestamp: '2026-05-14T18:30:00.000Z',
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.resetModules()
  window.sessionStorage.clear()
  delete window.__chatMediaTelemetry
})

describe('chatMediaTelemetry', () => {
  it('records telemetry events, persists them, and updates summary aggregates', async () => {
    const telemetry = await import('./chatMediaTelemetry')

    telemetry.recordMediaPreprocessTelemetry(baseEvent)
    telemetry.recordMediaPreprocessTelemetry({
      ...baseEvent,
      mediaType: 'video',
      path: 'video_preview',
      status: 'failed',
      durationMs: 360,
      usedWorker: false,
      fallbackReason: 'worker_crash',
      errorMessage: 'preview failed',
      timestamp: '2026-05-14T18:30:03.000Z',
    })

    const stored = JSON.parse(window.sessionStorage.getItem(STORAGE_KEY) || '{}')
    expect(window.__chatMediaTelemetry).toEqual(stored)
    expect(stored.summary.total).toBe(2)
    expect(stored.summary.byPath.image_worker).toBe(1)
    expect(stored.summary.byPath.video_preview).toBe(1)
    expect(stored.summary.byStatus.success).toBe(1)
    expect(stored.summary.byStatus.failed).toBe(1)
    expect(stored.summary.fallbackReasons.worker_crash).toBe(1)
    expect(stored.summary.averageDurationMs).toBe(240)
    expect(stored.summary.slowestDurationMs).toBe(360)
    expect(stored.summary.lastEvent.path).toBe('video_preview')
  })

  it('caps recent events to the latest forty entries', async () => {
    const telemetry = await import('./chatMediaTelemetry')

    for (let index = 0; index < 45; index += 1) {
      telemetry.recordMediaPreprocessTelemetry({
        ...baseEvent,
        durationMs: index + 1,
        timestamp: `2026-05-14T18:30:${String(index).padStart(2, '0')}.000Z`,
      })
    }

    const stored = JSON.parse(window.sessionStorage.getItem(STORAGE_KEY) || '{}')
    expect(stored.summary.total).toBe(45)
    expect(stored.recentEvents).toHaveLength(40)
    expect(stored.recentEvents[0].timestamp).toBe('2026-05-14T18:30:05.000Z')
    expect(stored.recentEvents[39].timestamp).toBe('2026-05-14T18:30:44.000Z')
  })

  it('resets stored state when telemetry is recorded for a different user', async () => {
    const telemetry = await import('./chatMediaTelemetry')

    telemetry.recordMediaPreprocessTelemetry(baseEvent)
    telemetry.recordMediaPreprocessTelemetry({
      ...baseEvent,
      userId: 99,
      path: 'image_main_thread',
      timestamp: '2026-05-14T18:31:00.000Z',
    })

    const stored = JSON.parse(window.sessionStorage.getItem(STORAGE_KEY) || '{}')
    expect(stored.userId).toBe(99)
    expect(stored.summary.total).toBe(1)
    expect(stored.summary.byPath.image_main_thread).toBe(1)
    expect(stored.summary.byPath.image_worker).toBeUndefined()
  })

  it('primes valid stored telemetry and ignores invalid persisted JSON', async () => {
    const telemetry = await import('./chatMediaTelemetry')

    window.sessionStorage.setItem(STORAGE_KEY, '{bad json')
    expect(() => telemetry.primeMediaPreprocessTelemetry()).not.toThrow()
    expect(window.__chatMediaTelemetry).toBeUndefined()

    const validStore = {
      userId: baseEvent.userId,
      recentEvents: [baseEvent],
      summary: {
        total: 1,
        byPath: { image_worker: 1 },
        byStatus: { success: 1 },
        fallbackReasons: {},
        averageDurationMs: 120,
        slowestDurationMs: 120,
        lastEvent: baseEvent,
      },
    }
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(validStore))

    telemetry.primeMediaPreprocessTelemetry()

    expect(window.__chatMediaTelemetry).toEqual(validStore)
  })

  it('keeps the in-memory debug store updated when sessionStorage writes fail', async () => {
    const telemetry = await import('./chatMediaTelemetry')
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota exceeded')
    })

    expect(() => telemetry.recordMediaPreprocessTelemetry(baseEvent)).not.toThrow()
    expect(window.__chatMediaTelemetry?.summary.total).toBe(1)
    expect(window.__chatMediaTelemetry?.summary.lastEvent?.timestamp).toBe(baseEvent.timestamp)
  })
})