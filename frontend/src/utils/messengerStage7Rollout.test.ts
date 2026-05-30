import { describe, expect, it } from 'vitest'

import {
  evaluateMessengerRolloutGate,
  getMessengerRolloutSurface,
} from './messengerStage7Rollout'

describe('messengerStage7Rollout', () => {
  it('keeps legacy as the rollback surface and enables refactor only for explicit preview', () => {
    expect(getMessengerRolloutSurface('legacy')).toEqual({
      uiVersion: 'legacy',
      rolloutMode: 'legacy-default',
      isRefactorShellEnabled: false,
      rollbackVersion: 'legacy',
    })

    expect(getMessengerRolloutSurface('refactor')).toEqual({
      uiVersion: 'refactor',
      rolloutMode: 'refactor-preview',
      isRefactorShellEnabled: true,
      rollbackVersion: 'legacy',
    })
  })

  it('separates technical rollout readiness from legacy retirement approval', () => {
    expect(evaluateMessengerRolloutGate({
      legacyAvailable: true,
      rollbackVerified: true,
      focusedUnitPassed: true,
      productionBuildPassed: true,
      browserMatrixPassed: true,
      measurableImprovementRecorded: false,
      manualAcceptanceApproved: false,
    })).toEqual({
      technicalReady: true,
      canRetireLegacy: false,
      blockers: ['measured-improvement-missing', 'manual-acceptance-missing'],
    })
  })

  it('blocks technical rollout when any required safety gate is missing', () => {
    expect(evaluateMessengerRolloutGate({
      legacyAvailable: false,
      rollbackVerified: false,
      focusedUnitPassed: true,
      productionBuildPassed: false,
      browserMatrixPassed: true,
      measurableImprovementRecorded: true,
      manualAcceptanceApproved: true,
    })).toMatchObject({
      technicalReady: false,
      canRetireLegacy: false,
      blockers: ['legacy-unavailable', 'rollback-unverified', 'production-build-failed'],
    })
  })

  it('allows legacy retirement only after every roadmap gate is explicitly satisfied', () => {
    expect(evaluateMessengerRolloutGate({
      legacyAvailable: true,
      rollbackVerified: true,
      focusedUnitPassed: true,
      productionBuildPassed: true,
      browserMatrixPassed: true,
      measurableImprovementRecorded: true,
      manualAcceptanceApproved: true,
    })).toEqual({
      technicalReady: true,
      canRetireLegacy: true,
      blockers: [],
    })
  })
})