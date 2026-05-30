export type MessengerUiVersion = 'legacy' | 'refactor'

export const MESSENGER_UI_VERSION_STORAGE_KEY = 'messenger_ui_version'

const TRUTHY_ENV_VALUES = new Set(['1', 'true', 'yes', 'on', 'refactor'])
const PERF_PREFIX = 'messenger:'

export function normalizeMessengerUiVersion(value: unknown): MessengerUiVersion | null {
  if (typeof value !== 'string') {
    return null
  }

  const normalized = value.trim().toLowerCase()
  if (normalized === 'legacy' || normalized === 'refactor') {
    return normalized
  }

  return null
}

function readStoredMessengerUiVersion(): MessengerUiVersion | null {
  if (typeof window === 'undefined') {
    return null
  }

  try {
    return normalizeMessengerUiVersion(window.localStorage.getItem(MESSENGER_UI_VERSION_STORAGE_KEY))
  } catch {
    return null
  }
}

function isEnvRefactorEnabled(): boolean {
  const envValue = String(import.meta.env.VITE_MESSENGER_REFACTOR_ENABLED ?? '').trim().toLowerCase()
  return TRUTHY_ENV_VALUES.has(envValue)
}

export function resolveMessengerUiVersion(): MessengerUiVersion {
  return readStoredMessengerUiVersion() ?? (isEnvRefactorEnabled() ? 'refactor' : 'legacy')
}

export function isMessengerRefactorEnabled(): boolean {
  return resolveMessengerUiVersion() === 'refactor'
}

export function getMessengerPerformanceName(name: string): string {
  return `${PERF_PREFIX}${name}`
}

export function markMessengerPerformance(name: string): void {
  if (typeof performance === 'undefined' || typeof performance.mark !== 'function') {
    return
  }

  try {
    performance.mark(getMessengerPerformanceName(name))
  } catch {
    // Performance marks are diagnostic only and must never affect Messenger runtime.
  }
}

export function measureMessengerPerformance(name: string, startMark: string, endMark: string): number | null {
  if (typeof performance === 'undefined' || typeof performance.measure !== 'function') {
    return null
  }

  const measureName = getMessengerPerformanceName(name)
  try {
    performance.measure(
      measureName,
      getMessengerPerformanceName(startMark),
      getMessengerPerformanceName(endMark),
    )
    const entries = performance.getEntriesByName(measureName, 'measure')
    const latestEntry = entries.length > 0 ? entries[entries.length - 1] : null
    return typeof latestEntry?.duration === 'number' ? latestEntry.duration : null
  } catch {
    return null
  }
}