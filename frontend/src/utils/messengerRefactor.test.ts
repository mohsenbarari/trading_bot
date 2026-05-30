import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  MESSENGER_UI_VERSION_STORAGE_KEY,
  getMessengerPerformanceName,
  markMessengerPerformance,
  measureMessengerPerformance,
  normalizeMessengerUiVersion,
  resolveMessengerUiVersion,
} from './messengerRefactor'

describe('messengerRefactor utilities', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.unstubAllEnvs()
    performance.clearMarks()
    performance.clearMeasures()
  })

  afterEach(() => {
    vi.unstubAllEnvs()
    performance.clearMarks()
    performance.clearMeasures()
  })

  it('normalizes only supported messenger UI versions', () => {
    expect(normalizeMessengerUiVersion('legacy')).toBe('legacy')
    expect(normalizeMessengerUiVersion(' REFACTOR ')).toBe('refactor')
    expect(normalizeMessengerUiVersion('next')).toBeNull()
    expect(normalizeMessengerUiVersion(null)).toBeNull()
  })

  it('keeps legacy as the default and lets localStorage override the env flag', () => {
    expect(resolveMessengerUiVersion()).toBe('legacy')

    vi.stubEnv('VITE_MESSENGER_REFACTOR_ENABLED', 'true')
    expect(resolveMessengerUiVersion()).toBe('refactor')

    localStorage.setItem(MESSENGER_UI_VERSION_STORAGE_KEY, 'legacy')
    expect(resolveMessengerUiVersion()).toBe('legacy')
  })

  it('records diagnostic performance marks and measures without changing runtime behavior', () => {
    markMessengerPerformance('route-mounted')
    markMessengerPerformance('surface-ready')

    const duration = measureMessengerPerformance('route-to-surface', 'route-mounted', 'surface-ready')

    expect(performance.getEntriesByName(getMessengerPerformanceName('route-mounted'), 'mark')).toHaveLength(1)
    expect(typeof duration === 'number' || duration === null).toBe(true)
  })
})