import type { MessengerUiVersion } from './messengerRefactor'

export type MessengerRolloutMode = 'legacy-default' | 'refactor-preview'

export interface MessengerRolloutSurface {
  uiVersion: MessengerUiVersion
  rolloutMode: MessengerRolloutMode
  isRefactorShellEnabled: boolean
  rollbackVersion: MessengerUiVersion
}

export interface MessengerRolloutGateInput {
  legacyAvailable: boolean
  rollbackVerified: boolean
  focusedUnitPassed: boolean
  productionBuildPassed: boolean
  browserMatrixPassed: boolean
  measurableImprovementRecorded: boolean
  manualAcceptanceApproved: boolean
}

export interface MessengerRolloutGateResult {
  technicalReady: boolean
  canRetireLegacy: boolean
  blockers: string[]
}

export function getMessengerRolloutSurface(uiVersion: MessengerUiVersion): MessengerRolloutSurface {
  return {
    uiVersion,
    rolloutMode: uiVersion === 'refactor' ? 'refactor-preview' : 'legacy-default',
    isRefactorShellEnabled: uiVersion === 'refactor',
    rollbackVersion: 'legacy',
  }
}

export function evaluateMessengerRolloutGate(input: MessengerRolloutGateInput): MessengerRolloutGateResult {
  const blockers: string[] = []

  if (!input.legacyAvailable) blockers.push('legacy-unavailable')
  if (!input.rollbackVerified) blockers.push('rollback-unverified')
  if (!input.focusedUnitPassed) blockers.push('focused-unit-failed')
  if (!input.productionBuildPassed) blockers.push('production-build-failed')
  if (!input.browserMatrixPassed) blockers.push('browser-matrix-missing')
  if (!input.measurableImprovementRecorded) blockers.push('measured-improvement-missing')
  if (!input.manualAcceptanceApproved) blockers.push('manual-acceptance-missing')

  const technicalReady = !blockers.some((blocker) => [
    'legacy-unavailable',
    'rollback-unverified',
    'focused-unit-failed',
    'production-build-failed',
    'browser-matrix-missing',
  ].includes(blocker))

  return {
    technicalReady,
    canRetireLegacy: blockers.length === 0,
    blockers,
  }
}
