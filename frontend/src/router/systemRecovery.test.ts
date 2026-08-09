import { describe, expect, it } from 'vitest'
import {
  createSystemRecoveryLocation,
  resolveSystemRecoveryOutcome,
  SYSTEM_RECOVERY_OUTCOME,
} from './systemRecovery'

describe('system recovery contract', () => {
  it('accepts only the three owned outcomes and defaults unknown input to not-found', () => {
    expect(resolveSystemRecoveryOutcome('not-found')).toBe(SYSTEM_RECOVERY_OUTCOME.NOT_FOUND)
    expect(resolveSystemRecoveryOutcome(['forbidden'])).toBe(SYSTEM_RECOVERY_OUTCOME.FORBIDDEN)
    expect(resolveSystemRecoveryOutcome('deep-link-failure')).toBe(
      SYSTEM_RECOVERY_OUTCOME.DEEP_LINK_FAILURE,
    )
    expect(resolveSystemRecoveryOutcome('raw-secret-token')).toBe(SYSTEM_RECOVERY_OUTCOME.NOT_FOUND)
    expect(resolveSystemRecoveryOutcome(undefined)).toBe(SYSTEM_RECOVERY_OUTCOME.NOT_FOUND)
  })

  it('builds the stable eager-recovery location without carrying the failed URL', () => {
    expect(createSystemRecoveryLocation(SYSTEM_RECOVERY_OUTCOME.DEEP_LINK_FAILURE)).toEqual({
      name: 'system-recovery',
      params: { pathMatch: ['__system', 'recovery'] },
      query: { outcome: 'deep-link-failure' },
    })
  })
})
