import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import {
  deriveOfficialCounts,
  getRouteDescriptor,
  STAGE8_ROUTE_NAMES,
  staleEndpointsFromDescriptors,
} from './stage8-full-acceptance-descriptors.mjs'

function sha256File(filePath) {
  return createHash('sha256').update(fs.readFileSync(filePath)).digest('hex')
}

function gitText(repo, args) {
  return execFileSync('git', args, {
    cwd: repo,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  }).replace(/\n$/u, '')
}

export const OFFICIAL_PHASES = Object.freeze([
  'access',
  'viewport',
  'state',
  'interaction',
  'environment',
])

export function officialCounts() {
  const derived = deriveOfficialCounts()
  return {
    accessExpected: derived.accessExpected,
    accessExecuted: derived.accessExecuted,
    accessPassed: derived.accessPassed,
    viewportExpected: derived.viewportExpected,
    viewportExecuted: derived.viewportExecuted,
    viewportPassed: derived.viewportPassed,
    stateTotal: derived.stateTotal,
    stateExecuted: derived.stateExecuted,
    stateNotApplicable: derived.stateNotApplicable,
    statePassed: derived.statePassed,
    interactionTotal: derived.interactionTotal,
    interactionExecuted: derived.interactionExecuted,
    interactionNotApplicable: derived.interactionNotApplicable,
    interactionPassed: derived.interactionPassed,
    environmentTotal: derived.environmentTotal,
    environmentExecuted: derived.environmentExecuted,
    environmentNotApplicable: derived.environmentNotApplicable,
    environmentPassed: derived.environmentPassed,
    uniqueScenarioIds: derived.uniqueScenarioIds,
  }
}

export const OFFICIAL_COUNTS = officialCounts()

export const BINDING_PATHS = Object.freeze([
  'frontend/scripts/stage8-full-acceptance-browser.mjs',
  'frontend/scripts/lib/stage8-full-acceptance-runtime.mjs',
  'frontend/scripts/lib/stage8-full-acceptance-contract.mjs',
  'frontend/scripts/lib/stage8-full-acceptance-descriptors.mjs',
  'frontend/scripts/lib/stage8-full-acceptance-constants.mjs',
  'frontend/scripts/stage8-full-acceptance-runtime.test.mjs',
  'frontend/scripts/stage8-acceptance-matrix-source-guard.test.mjs',
  'frontend/scripts/stage8-full-acceptance-contract.test.mjs',
  'docs/uiux-stage8-acceptance-rollout/ACCEPTANCE_MATRIX.json',
  'frontend/src/router/index.ts',
  'frontend/src/router/uiRouteContract.ts',
  'frontend/src/utils/auth.ts',
  'models/user.py',
  'frontend/src/views/AdminView.vue',
  'frontend/src/components/UserProfile.vue',
  'frontend/src/components/UserProfile.test.ts',
])

export const STALE_OLD_MARKER = 'کهنه-پذیرش'
export const STALE_FRESH_MARKER = 'تازه-پذیرش'

export function hashBindingFiles(repoRoot) {
  return Object.fromEntries(BINDING_PATHS.map((rel) => [rel, sha256File(path.join(repoRoot, rel))]))
}

export function captureOfficialBinding(repoRoot) {
  const porcelain = gitText(repoRoot, ['status', '--porcelain=v1', '--untracked-files=all'])
  const stagedDiff = gitText(repoRoot, ['diff', '--cached'])
  const worktreeDiff = gitText(repoRoot, ['diff'])
  return {
    branch: gitText(repoRoot, ['branch', '--show-current']).trim() || null,
    commit: gitText(repoRoot, ['rev-parse', 'HEAD']).trim(),
    tree: gitText(repoRoot, ['rev-parse', 'HEAD^{tree}']).trim(),
    porcelain,
    stagedDiff,
    worktreeDiff,
    hashes: hashBindingFiles(repoRoot),
  }
}

export function assertCleanOfficialBinding(binding) {
  const failures = []
  if (binding.porcelain) failures.push(`official run requires a clean worktree: ${binding.porcelain}`)
  if (binding.stagedDiff) failures.push('official run requires an empty staged diff')
  if (binding.worktreeDiff) failures.push('official run requires an empty worktree diff')
  return failures
}

export function assertBindingUnchanged(before, after) {
  const keys = ['branch', 'commit', 'tree', 'porcelain', 'stagedDiff', 'worktreeDiff']
  const failures = []
  for (const key of keys) {
    if (before[key] !== after[key]) failures.push(`binding ${key} drifted during the run`)
  }
  const beforeHashes = before.hashes || {}
  const afterHashes = after.hashes || {}
  for (const rel of BINDING_PATHS) {
    if (beforeHashes[rel] !== afterHashes[rel]) failures.push(`binding hash drifted for ${rel}`)
  }
  return failures
}

export function evaluateOfficialPass({
  officialRun,
  failed,
  sourceDriftCount,
  uniqueIdCount,
  duplicateIdCount,
  counters,
  server,
  diagnosticTotals,
}) {
  const failures = []
  if (!officialRun) failures.push('official phases must be access,viewport,state,interaction,environment')
  if (failed !== 0) failures.push(`failed scenarios ${failed}`)
  if (sourceDriftCount !== 0) failures.push(`sourceDrift ${sourceDriftCount}`)
  if (duplicateIdCount !== 0) failures.push(`duplicate ids ${duplicateIdCount}`)
  const counts = officialCounts()
  if (uniqueIdCount !== counts.uniqueScenarioIds) {
    failures.push(`unique ids ${uniqueIdCount} != ${counts.uniqueScenarioIds}`)
  }
  const expected = {
    accessCellsExpected: counts.accessExpected,
    accessCellsExecuted: counts.accessExecuted,
    accessCellsPassed: counts.accessPassed,
    routeViewportExpected: counts.viewportExpected,
    routeViewportExecuted: counts.viewportExecuted,
    routeViewportPassed: counts.viewportPassed,
    stateTotal: counts.stateTotal,
    stateExecuted: counts.stateExecuted,
    stateNotApplicable: counts.stateNotApplicable,
    statePassed: counts.statePassed,
    interactionTotal: counts.interactionTotal,
    interactionExecuted: counts.interactionExecuted,
    interactionNotApplicable: counts.interactionNotApplicable,
    interactionPassed: counts.interactionPassed,
    environmentTotal: counts.environmentTotal,
    environmentExecuted: counts.environmentExecuted,
    environmentNotApplicable: counts.environmentNotApplicable,
    environmentPassed: counts.environmentPassed,
  }
  for (const [key, value] of Object.entries(expected)) {
    if (counters?.[key] !== value) failures.push(`counter ${key} ${counters?.[key]} != ${value}`)
  }
  if ((server?.unknownApiRequests || 0) !== 0) failures.push(`unknownApi ${server.unknownApiRequests}`)
  if ((server?.mutatingApiRequests || 0) !== 0) failures.push(`mutatingApi ${server.mutatingApiRequests}`)
  if ((diagnosticTotals?.unexpectedConsole || 0) !== 0) {
    failures.push(`unexpectedConsole ${diagnosticTotals.unexpectedConsole}`)
  }
  if ((diagnosticTotals?.pageErrors || 0) !== 0) failures.push(`pageErrors ${diagnosticTotals.pageErrors}`)
  if ((diagnosticTotals?.externalRequests || 0) !== 0) {
    failures.push(`externalRequests ${diagnosticTotals.externalRequests}`)
  }
  if ((diagnosticTotals?.requestFailuresOutsideOffline || 0) !== 0) {
    failures.push(`requestFailures ${diagnosticTotals.requestFailuresOutsideOffline}`)
  }
  return { passed: failures.length === 0, failures }
}

export function listStateRoutes() {
  return STAGE8_ROUTE_NAMES.filter((name) => {
    const descriptor = getRouteDescriptor(name)
    return descriptor.states.empty.applicable && descriptor.states.dense.applicable
  })
}

export const LIST_STATE_ROUTES = Object.freeze(listStateRoutes())

export function hasListStateSurface(routeName) {
  return listStateRoutes().includes(routeName)
}

export function assertStateSemantics(probe, midProbe, state, protection, expectedKind, routeName = '') {
  const failures = []
  if (expectedKind !== 'render-route') return failures
  const listSurface = hasListStateSurface(routeName)
  if (state === 'loading') {
    if (!midProbe?.pendingRequest) failures.push('loading mid-probe ran without a pending request')
    if (!midProbe?.loadingVisible) failures.push('loading UI not observed before settle')
    if (probe.loadingVisible && !probe.settledVisible) failures.push('loading did not settle')
    if ((midProbe?.identityRequestCount || 0) > 3) {
      failures.push(`loading remounted identity ${midProbe.identityRequestCount} times`)
    }
  }
  if (state === 'empty') {
    if (!probe.emptyVisible) failures.push('empty state UI not observed')
    if (!probe.emptyNamed) failures.push('empty state missing accessible name')
    if (probe.listItemCount > 0) failures.push(`empty state still shows ${probe.listItemCount} items`)
    if (probe.denseVisible) failures.push('empty state still shows dense inventory')
  }
  if (state === 'dense') {
    if (probe.listItemCount < 8) failures.push(`dense list rendered ${probe.listItemCount} items`)
    if (!probe.lastItemAccessible) failures.push('dense last item is not accessible')
    if (probe.emptyVisible) failures.push('dense state still shows empty UI')
    if (listSurface && probe.documentOverflow && !probe.internalStripOverflow) {
      failures.push('dense document overflow is not confined to an internal strip')
    }
  }
  if (state === 'error') {
    if (protection === 'none' && !probe.errorVisible) failures.push('error UI not observed')
    if (probe.landedRecovery) failures.push('error state left the route for system recovery')
    if (probe.identityBootstrapBroken) failures.push('error state broke identity bootstrap')
  }
  if (state === 'slow') {
    if (!midProbe?.pendingRequest) failures.push('slow mid-probe ran without a pending request')
    if (!midProbe?.loadingVisible) failures.push('slow state never showed loading')
    if (probe.emptyVisible && !probe.listItemCount) failures.push('slow settled into premature empty')
    if (probe.errorVisible && !probe.settledVisible) failures.push('slow settled into premature error')
  }
  if (state === 'offline') {
    if (protection === 'none' && !probe.offlineVisible && !probe.errorVisible) {
      failures.push('offline/fallback UI not observed')
    }
    if (probe.landedRecovery) failures.push('offline state left the route for system recovery')
  }
  if (state === 'stale') {
    if ((probe.staleTargetHits || 0) < 2) {
      failures.push(`stale endpoint requested ${probe.staleTargetHits || 0} times`)
    }
    if (probe.staleOldVisible) failures.push('stale response overwrote newer state')
    if (!probe.staleFreshVisible) {
      failures.push('fresh stale-race marker not observed')
    }
    if ((probe.staleNewCompletedAt || 0) > 0 && (probe.staleOldCompletedAt || 0) > 0) {
      if (probe.staleOldCompletedAt <= probe.staleNewCompletedAt) {
        failures.push('stale race did not deliver the old response after the fresh one')
      }
    }
  }
  return failures
}

export function assertInteractionSemantics(probe, interaction, protection) {
  const failures = []
  if (interaction === 'keyboard') {
    if (!probe.focusInViewport) failures.push('keyboard focus outside viewport')
    if (!probe.tabCycleObserved) failures.push('Tab/Shift+Tab cycle not observed')
    if (!probe.focusVisible) failures.push('keyboard focus-visible was not observed')
    if (probe.escapeOpenedMutation) failures.push('Escape performed a product mutation')
    if (probe.modalOpen && !probe.focusInsideModal) failures.push('modal focus was not contained')
    if (probe.modalClosedAfterEscape && probe.modalOpen) {
      failures.push('Escape did not close the observed modal')
    }
  }
  if (interaction === 'touch') {
    if (!probe.touchActivated) failures.push('touch/pointer did not activate a safe control')
    if (probe.mutatingProductRequest) failures.push('touch activated a mutating product endpoint')
  }
  if (interaction === 'zoom-200') {
    if (Number(probe.visualScale || 0) < 1.9) failures.push(`zoom scale ${probe.visualScale} != 2`)
    if (probe.documentOverflow && !probe.internalStripOverflow) {
      failures.push('document overflow at 200% zoom')
    }
    if (probe.appOverflow && !probe.internalStripOverflow) {
      failures.push('app overflow at 200% zoom')
    }
    if (probe.clippedControlCount > 0) failures.push(`clipped controls at zoom ${probe.clippedControlCount}`)
    if (probe.clippedTextCount > 0) failures.push(`clipped text at zoom ${probe.clippedTextCount}`)
    if (!probe.ctaAboveNav) failures.push('CTA obscured at 200% zoom')
    if (probe.bottomNavClipped) failures.push('BottomNav clipped at 200% zoom')
    if (probe.modalOpen && probe.modalOutOfBounds) failures.push('modal/sheet exceeded viewport at 200% zoom')
    if (probe.zoomStripExpected && !probe.selectedControlInStrip) {
      failures.push('selected control is not inside the internal strip after reveal')
    }
    if (probe.zoomStripExpected && !probe.hitTestPassed) {
      failures.push('selected control failed hit-test after reveal')
    }
  }
  if (interaction === 'reduced-motion') {
    if (!probe.reducedMotion) failures.push('prefers-reduced-motion is not reduce')
    if (protection === 'none' && probe.v2MotionMs != null && probe.v2MotionMs > 20) {
      failures.push(`NONE reduced-motion token ${probe.v2MotionMs}ms`)
    }
    if ((protection === 'full' || protection === 'mixed') && probe.protectedFadeMs != null && probe.protectedFadeMs < 180) {
      failures.push(`protected fade changed to ${probe.protectedFadeMs}ms`)
    }
  }
  return failures
}

export function assertEnvironmentSemantics(probe, environment) {
  const failures = []
  if (probe.environmentName && probe.environmentName !== environment) {
    failures.push(`environment label ${probe.environmentName} != ${environment}`)
  }
  if (environment === 'mobile-browser') {
    if (probe.hasTelegramBridge) failures.push('mobile-browser injected a Telegram bridge')
    if (probe.standalone) failures.push('mobile-browser reported standalone display-mode')
    if (probe.serviceWorkerControlled) failures.push('mobile-browser claimed a service worker')
  }
  if (environment === 'pwa-simulation') {
    if (!probe.standalone) failures.push('pwa-simulation standalone display-mode was not asserted')
    if (probe.serviceWorkerControlled) {
      failures.push('pwa-simulation cannot claim an installed service worker while workers are blocked')
    }
    if (probe.hasTelegramBridge) failures.push('pwa-simulation injected a Telegram bridge')
  }
  if (environment === 'telegram-webview-non-messenger') {
    if (!probe.hasTelegramBridge) failures.push('Telegram WebView missing window.Telegram.WebApp')
    if (!probe.telegramReadyCalled) failures.push('Telegram WebApp.ready was not observed')
    if (!String(probe.userAgent || '').includes('Telegram WebView')) {
      failures.push('Telegram WebView user agent was not applied')
    }
    if (probe.standalone) failures.push('Telegram WebView reported standalone display-mode')
  }
  return failures
}

export function matchesStaleEndpoint(pathname, endpoint) {
  if (!pathname || !endpoint) return false
  return pathname.replace(/\/$/u, '') === endpoint.replace(/\/$/u, '')
}

export function isStaleTargetPath(pathname, endpoint = '') {
  if (endpoint) return matchesStaleEndpoint(pathname, endpoint)
  return staleEndpointsFromDescriptors().some((item) => matchesStaleEndpoint(pathname, item))
}
