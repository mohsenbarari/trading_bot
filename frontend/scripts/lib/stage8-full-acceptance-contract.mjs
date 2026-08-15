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

export function sha256Hex(value) {
  return createHash('sha256').update(typeof value === 'string' ? value : JSON.stringify(value)).digest('hex')
}

export const MATRIX_PENDING_STATUS = 'full-local-synthetic-acceptance-executed-owner-signoff-pending'
export const MATRIX_CLOSED_STATUS = 'closed-owner-aesthetic-approved'
export const OWNER_APPROVAL_PHRASE = 'STAGE8 OWNER AESTHETIC SIGN-OFF — APPROVED'
export const STAGE8_CLOSURE_RELATIVE_PATH =
  'docs/uiux-stage8-acceptance-rollout/STAGE8_FINAL_ACCEPTANCE_CLOSURE.json'

export function loadStage8Closure(repoRoot) {
  const closurePath = path.join(repoRoot, STAGE8_CLOSURE_RELATIVE_PATH)
  if (!fs.existsSync(closurePath)) return null
  return JSON.parse(fs.readFileSync(closurePath, 'utf8'))
}
const SENSITIVE_ENDPOINT = /token|otp|secret|password|authorization|cookie|session_id/iu
const QUERY_OR_FRAGMENT = /[?#]/u
const ISO_TIMESTAMP = /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/u

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
  taxonomyCounts,
}) {
  const failures = []
  if (!officialRun) failures.push('official phases must be access,viewport,state,interaction,environment')
  if (failed !== 0) failures.push(`failed scenarios ${failed}`)
  if (sourceDriftCount !== 0) failures.push(`sourceDrift ${sourceDriftCount}`)
  if (duplicateIdCount !== 0) failures.push(`duplicate ids ${duplicateIdCount}`)
  const counts = officialCounts()
  const derived = deriveOfficialCounts()
  if (uniqueIdCount !== counts.uniqueScenarioIds) {
    failures.push(`unique ids ${uniqueIdCount} != ${counts.uniqueScenarioIds}`)
  }
  const harnessDeferred =
    taxonomyCounts?.harnessDeferred ?? derived.taxonomy?.harnessDeferred ?? 0
  if (harnessDeferred !== 0) failures.push(`harnessDeferred ${harnessDeferred}`)
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
  if ((diagnosticTotals?.unknownApiRequests || 0) !== 0) {
    failures.push(`unknownApi ${diagnosticTotals.unknownApiRequests}`)
  }
  if ((diagnosticTotals?.mutatingApiRequests || 0) !== 0) {
    failures.push(`mutatingApi ${diagnosticTotals.mutatingApiRequests}`)
  }
  if ((diagnosticTotals?.sourceDriftCount || 0) !== 0) {
    failures.push(`sourceDrift ${diagnosticTotals.sourceDriftCount}`)
  }
  return { passed: failures.length === 0, failures }
}

export function isRawIsoTimestamp(value) {
  return ISO_TIMESTAMP.test(String(value || ''))
}

export function endpointKey(pathname) {
  const raw = String(pathname || '')
  if (!raw || QUERY_OR_FRAGMENT.test(raw) || SENSITIVE_ENDPOINT.test(raw)) return ''
  return raw
}

export function assertEndpointKey(key) {
  const failures = []
  const value = String(key || '')
  if (QUERY_OR_FRAGMENT.test(value)) failures.push('endpoint key contains query')
  if (SENSITIVE_ENDPOINT.test(value)) failures.push('endpoint key contains sensitive data')
  return failures
}

export function buildPreSettleEvidence({
  state,
  kind,
  midProbe = null,
  probe = null,
  holdEndpoint = '',
  recovered = false,
  errorProbe = null,
} = {}) {
  const loadingOrSlow = state === 'loading' || state === 'slow'
  const isError = state === 'error'
  const applicable = kind === 'state' && (loadingOrSlow || isError)
  return {
    applicable,
    observedBeforeRelease: Boolean(applicable && loadingOrSlow && midProbe),
    pendingRequest: Boolean(midProbe?.pendingRequest),
    loadingVisible: Boolean(midProbe?.loadingVisible),
    identityRequestCount: Number(midProbe?.identityRequestCount || 0),
    endpointKey: endpointKey(holdEndpoint),
    state: state || null,
    settledAfterRelease: Boolean(probe?.settledVisible),
    settledLoadingVisible: Boolean(probe?.loadingVisible),
    recovered: Boolean(recovered),
    retryVisibleBeforeRecovery: isError ? Boolean(errorProbe?.retryVisible) : null,
    errorClearedAfterRetry: isError
      ? Boolean(errorProbe?.errorVisible && probe && !probe.errorVisible)
      : null,
  }
}

export function assertPreSettleEvidence(evidence, { state } = {}) {
  const failures = []
  if ((state === 'loading' || state === 'slow') && !evidence) {
    failures.push('pre-settle evidence missing')
    return failures
  }
  if (!evidence) return failures
  failures.push(...assertEndpointKey(evidence.endpointKey || ''))
  if (evidence.applicable && (evidence.state === 'loading' || evidence.state === 'slow')) {
    if (!evidence.pendingRequest) failures.push('loading/slow without pendingRequest')
    if (!evidence.loadingVisible) failures.push('loading/slow without loadingVisible before release')
    if (!evidence.observedBeforeRelease) failures.push('loading/slow not observed before release')
    if (evidence.settledLoadingVisible === true && evidence.loadingVisible === true && !evidence.pendingRequest) {
      failures.push('settled state recorded as pre-settle evidence')
    }
  }
  return failures
}

export function digestScenario(scenario) {
  return sha256Hex({
    id: scenario.id,
    kind: scenario.kind,
    route: scenario.route,
    profile: scenario.profile,
    viewport: scenario.viewport,
    state: scenario.state || null,
    interaction: scenario.interaction || null,
    environment: scenario.environment || null,
    passed: scenario.passed,
    failures: scenario.failures || [],
    preSettleEvidence: scenario.preSettleEvidence || null,
  })
}

export function collectKeyAssertions(scenario) {
  const probe = scenario.probe || {}
  const mid = scenario.preSettleEvidence || {}
  const assertions = []
  if (!probe.documentOverflow && !probe.appOverflow) assertions.push('common-ui-no-overflow')
  if (!Number(probe.unnamedInteractive || 0)) assertions.push('no-unnamed-interactive')
  if (!Number(probe.nestedInteractive || 0)) assertions.push('no-nested-interactive')
  if (scenario.state === 'loading' && mid.loadingVisible && mid.observedBeforeRelease) {
    assertions.push('loading-observed-before-release')
  }
  if (scenario.state === 'slow' && mid.loadingVisible && mid.observedBeforeRelease) {
    assertions.push('slow-loading-observed-before-release')
  }
  if ((scenario.state === 'loading' || scenario.state === 'slow') && mid.settledAfterRelease) {
    assertions.push('settled-after-release')
  }
  if (scenario.state === 'error' && (mid.retryVisibleBeforeRecovery || scenario.errorProbe?.errorVisible)) {
    assertions.push('error-visible-before-retry')
  }
  if (scenario.state === 'error' && mid.errorClearedAfterRetry) assertions.push('retry-recovered')
  if (scenario.state === 'offline' && (probe.offlineVisible || probe.errorVisible)) {
    assertions.push('offline-state-visible')
  }
  if (scenario.interaction === 'keyboard' && probe.focusVisible) assertions.push('keyboard-focus-visible')
  if (scenario.interaction === 'touch' && probe.touchActivated) assertions.push('touch-activation-observed')
  if (scenario.interaction === 'reduced-motion' && probe.reducedMotion !== false) {
    assertions.push('reduced-motion-respected')
  }
  if (scenario.interaction === 'zoom-200') assertions.push('zoom-content-preserved')
  if (
    scenario.route === 'market' &&
    scenario.expectedKind === 'render-route' &&
    !['loading', 'empty', 'error', 'offline', 'stale'].includes(scenario.state) &&
    probe.marketLifecycle?.deadlineMeterPresent
  ) {
    assertions.push('market-lifecycle-contract-passed')
  }
  return assertions
}

export function buildSuccessSummary(scenario) {
  const probe = scenario.probe || {}
  return {
    id: scenario.id,
    digest: scenario.digest,
    route: scenario.route,
    profile: scenario.profile,
    viewport: scenario.viewport || null,
    state: scenario.state || null,
    interaction: scenario.interaction || null,
    environment: scenario.environment || null,
    keyAssertions: collectKeyAssertions(scenario),
    preSettleEvidence: scenario.preSettleEvidence || null,
    documentOverflow: Boolean(probe.documentOverflow),
    appOverflow: Boolean(probe.appOverflow),
    unnamedInteractive: Number(probe.unnamedInteractive || 0),
    nestedInteractive: Number(probe.nestedInteractive || 0),
    focusInViewport: probe.focusInViewport !== false,
    focusVisible: Boolean(probe.focusVisible),
    touchActivated: Boolean(probe.touchActivated),
    ctaAboveNav: probe.ctaAboveNav !== false,
    stateMarker: {
      loadingVisible: Boolean(probe.loadingVisible),
      emptyVisible: Boolean(probe.emptyVisible),
      errorVisible: Boolean(probe.errorVisible),
      offlineVisible: Boolean(probe.offlineVisible),
      settledVisible: Boolean(probe.settledVisible),
      listItemCount: Number(probe.listItemCount || 0),
    },
    lifecycleMarker: probe.marketLifecycle || null,
    diagnosticSummary: scenario.diagnostics || null,
  }
}

export function classifyMarketLifecycleScenario(scenario) {
  if (scenario?.route !== 'market') return null
  const state = scenario.state || 'normal'
  const profileId = typeof scenario.profile === 'object' ? scenario.profile?.id : scenario.profile
  if (scenario.applicable === false || scenario.expectedKind !== 'render-route') {
    return 'nonLifecycleOrNotApplicable'
  }
  if (state === 'loading' || state === 'slow') return 'loadingOrSlow'
  if (state === 'error' || state === 'offline') return 'errorOrOffline'
  if (state === 'empty' || state === 'stale') return 'nonLifecycleOrNotApplicable'
  if (profileId === 'customer' || profileId === 'accountant') return 'historyHiddenByProfile'
  const marker = scenario.probe?.marketLifecycle || scenario.lifecycleMarker || {}
  if (
    marker.deadlineMeterPresent
    && marker.expiredDistinct
    && marker.tradedDistinct
    && marker.partialTradedDistinct
    && marker.fullTradedDistinct
  ) {
    return 'fullLifecycleVisible'
  }
  return 'nonLifecycleOrNotApplicable'
}

export function summarizePreSettleEvidence(scenarios) {
  const items = (scenarios || []).map((scenario) => scenario.preSettleEvidence).filter(Boolean)
  const applicable = items.filter((item) => item.applicable)
  return {
    applicableCount: applicable.length,
    observedBeforeRelease: applicable.filter((item) => item.observedBeforeRelease).length,
    pendingRequest: applicable.filter((item) => item.pendingRequest).length,
    loadingVisibleBeforeRelease: applicable.filter((item) => item.loadingVisible).length,
    settledAfterRelease: applicable.filter((item) => item.settledAfterRelease).length,
    recovered: applicable.filter((item) => item.recovered).length,
  }
}

export function summarizeMarketLifecycle(scenarios) {
  const buckets = {
    fullLifecycleVisible: [],
    historyHiddenByProfile: [],
    loadingOrSlow: [],
    errorOrOffline: [],
    nonLifecycleOrNotApplicable: [],
  }
  for (const scenario of scenarios || []) {
    const bucket = classifyMarketLifecycleScenario(scenario)
    if (!bucket) continue
    buckets[bucket].push(scenario.id)
  }
  return {
    fullLifecycleVisible: buckets.fullLifecycleVisible.length,
    historyHiddenByProfile: buckets.historyHiddenByProfile.length,
    loadingOrSlow: buckets.loadingOrSlow.length,
    errorOrOffline: buckets.errorOrOffline.length,
    nonLifecycleOrNotApplicable: buckets.nonLifecycleOrNotApplicable.length,
    historyHiddenIds: buckets.historyHiddenByProfile,
  }
}

export function evaluateMatrixAcceptanceTransition(matrix, options = {}) {
  const failures = []
  const accounting = matrix?.cellAccounting || {}
  const taxonomy = accounting.naTaxonomy || {}
  const official = accounting.officialFullAcceptance || {}
  const catalog = matrix?.evidenceCatalog || {}
  const receiptId = official.receiptId
  const receiptEntry = receiptId ? catalog[receiptId] : null
  const snapshot = matrix?.fullAcceptanceSourceSnapshot || {}
  const closure = options.closure || null
  const required = [
    ['executedFullMatrixCellCount', 270],
    ['viewportStateInteractionEnvironmentExpansionPerformed', true],
    ['plannedScenarioCount', 960],
    ['applicableExecutedCount', 830],
    ['applicablePassedCount', 830],
    ['notApplicableCount', 130],
    ['partialSyntheticBrowserSliceCount', 12],
    ['partialSyntheticBrowserScenarioCellCount', 163],
    ['partialSyntheticBrowserCellsCountTowardFullMatrix', false],
  ]
  for (const [key, value] of required) {
    if (accounting[key] !== value) failures.push(`counter ${key} ${accounting[key]} != ${value}`)
  }
  if ((accounting.applicablePassedCount || 0) < (accounting.applicableExecutedCount || 0)) {
    failures.push('applicablePassedCount below applicableExecutedCount')
  }
  if (taxonomy.productNotApplicable !== 118) failures.push('productNotApplicable drifted')
  if (taxonomy.canonicalAlias !== 12) failures.push('canonicalAlias drifted')
  if (taxonomy.harnessDeferred !== 0) failures.push(`harnessDeferred ${taxonomy.harnessDeferred}`)
  if ((matrix?.partialSyntheticBrowserSlices || []).length !== 12) {
    failures.push('partial slice list drifted')
  }
  if (!receiptId || !receiptEntry?.path || !receiptEntry?.sha256) {
    failures.push('receipt/report reference incomplete')
  }
  if (!official.runId) failures.push('official runId missing')
  if (!/^[0-9a-f]{64}$/u.test(String(snapshot.reportSha256 || ''))) {
    failures.push('report hash missing or invalid')
  }
  if (options.repoRoot && receiptEntry?.path) {
    const receiptPath = path.join(options.repoRoot, receiptEntry.path)
    if (!fs.existsSync(receiptPath)) failures.push('receipt file missing')
    else if (sha256File(receiptPath) !== receiptEntry.sha256) failures.push('receipt hash mismatch')
  }
  const ownerApproved = Boolean(
    closure?.ownerSignoff?.status === 'approved' &&
      closure?.ownerSignoff?.approvalPhrase === OWNER_APPROVAL_PHRASE,
  )
  const pending = matrix?.status === MATRIX_PENDING_STATUS
  const closed = matrix?.status === MATRIX_CLOSED_STATUS
  if (pending) {
    if (matrix.acceptanceAuthority !== false) failures.push('pending requires acceptanceAuthority=false')
    if (ownerApproved || closure?.status === MATRIX_CLOSED_STATUS) {
      failures.push('pending must not record owner closure')
    }
  } else if (closed) {
    if (matrix.acceptanceAuthority !== true) failures.push('closed requires acceptanceAuthority=true')
    if (!ownerApproved) failures.push('closed requires explicit owner sign-off')
    if (closure?.status !== MATRIX_CLOSED_STATUS) failures.push('closure record missing')
    if (closure?.technicalReceiptSha256 && receiptEntry?.sha256 !== closure.technicalReceiptSha256) {
      failures.push('closure receipt hash mismatch')
    }
  } else {
    failures.push(`invalid matrix status ${matrix?.status || 'missing'}`)
  }
  if (matrix?.acceptanceAuthority === true && !closed) {
    failures.push('acceptanceAuthority=true without valid closure')
  }
  return {
    passed: failures.length === 0,
    failures,
    state: pending ? 'pending' : closed ? 'closed' : 'invalid',
  }
}

export function canViewExpiredMarketHistory(profile) {
  if (!profile) return true
  if (profile.authenticated === false) return false
  if (profile.isCustomer === true) return false
  if (profile.isAccountant === true) return false
  return true
}

export function assertMarketLifecycle(probe, options = {}) {
  const failures = []
  const { routeName, state, expectedKind, interaction, profile } = options
  if (routeName !== 'market' || expectedKind !== 'render-route') return failures
  if (['loading', 'empty', 'error', 'offline', 'stale'].includes(state)) return failures
  const market = probe?.marketLifecycle || {}
  if (!market.deadlineMeterPresent) failures.push('market linear deadline meter missing')
  if (market.deadlineMeterRole !== 'progressbar') {
    failures.push('market deadline meter lacks progressbar semantics')
  }
  if ((market.legacyDeadlineVisualCount || 0) > 0) {
    failures.push('market legacy deadline visual returned')
  }
  if ((market.tradeRailCount || 0) > 0) {
    failures.push('market overlapping vertical trade rail returned')
  }
  if ((market.overtimeStickerCount || 0) !== 1) {
    failures.push(`market hourglass count ${market.overtimeStickerCount || 0} != 1`)
  }
  if (market.overtimeStickerName !== 'وقت اضافه') {
    failures.push(`market hourglass name ${market.overtimeStickerName || 'missing'}`)
  }
  if (interaction === 'reduced-motion') {
    if (market.overtimeStickerAnimated !== false) {
      failures.push('market hourglass is not static under reduced motion')
    }
  } else if (market.overtimeStickerAnimated !== true) {
    failures.push('market hourglass motion is not the gentle turn')
  }
  if (!market.overtimeProgressBound) {
    failures.push('market overtime progress is not bound to final_deadline')
  }
  if (canViewExpiredMarketHistory(profile)) {
    if (!market.expiredReadOnly || !market.expiredDistinct) {
      failures.push('market expired is not read-only and distinct')
    }
    if (!market.tradedReadOnly || !market.tradedDistinct) {
      failures.push('market traded is not read-only and distinct')
    }
    if (!market.partialTradedDistinct || !market.fullTradedDistinct) {
      failures.push('market partial and full trade histories are not visually distinct')
    }
  }
  if (market.sideActionInverted) {
    failures.push('market offer side is inverted against user action')
  }
  if (market.displayCreatedAtIso) {
    failures.push('market created_at shows raw ISO')
  }
  if (probe.documentOverflow) failures.push('market document overflow')
  if (probe.ctaAboveNav === false) failures.push('market CTA obscured')
  return failures
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
    const identityPageData = STAGE8_ROUTE_NAMES.includes(routeName)
      ? Boolean(getRouteDescriptor(routeName).states.slow?.identityPageData)
      : false
    if (probe.emptyVisible && !probe.listItemCount && !identityPageData) {
      failures.push('slow settled into premature empty')
    }
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
