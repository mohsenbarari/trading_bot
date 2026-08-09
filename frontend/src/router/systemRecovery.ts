import type { RouteLocationNamedRaw } from 'vue-router'

export const SYSTEM_RECOVERY_OUTCOME = {
  NOT_FOUND: 'not-found',
  FORBIDDEN: 'forbidden',
  DEEP_LINK_FAILURE: 'deep-link-failure',
} as const

export type SystemRecoveryOutcome =
  (typeof SYSTEM_RECOVERY_OUTCOME)[keyof typeof SYSTEM_RECOVERY_OUTCOME]

export const SYSTEM_RECOVERY_PATH_MATCH = ['__system', 'recovery'] as const
export const SYSTEM_RECOVERY_FALLBACK_HREF = '/__system/recovery?outcome=deep-link-failure'

export function resolveSystemRecoveryOutcome(value: unknown): SystemRecoveryOutcome {
  const candidate = Array.isArray(value) ? value[0] : value
  return Object.values(SYSTEM_RECOVERY_OUTCOME).includes(candidate as SystemRecoveryOutcome)
    ? (candidate as SystemRecoveryOutcome)
    : SYSTEM_RECOVERY_OUTCOME.NOT_FOUND
}

export function createSystemRecoveryLocation(
  outcome: SystemRecoveryOutcome,
): RouteLocationNamedRaw {
  return {
    name: 'system-recovery',
    params: { pathMatch: [...SYSTEM_RECOVERY_PATH_MATCH] },
    query: { outcome },
  }
}
