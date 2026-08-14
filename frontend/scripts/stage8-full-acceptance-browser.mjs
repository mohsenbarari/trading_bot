#!/usr/bin/env node

import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  ACCESS_VIEWPORT,
  ENVIRONMENTS,
  RUN_AUTHORIZATION,
  VIEWPORTS,
  allowedProfileForRoute,
  assertCommonUi,
  assertOutcome,
  attachProfileRuntime,
  closeServer,
  collectUiProbe,
  createFixtureServer,
  diagnosticCounts,
  distFingerprint,
  environmentApplicability,
  finalRouteExpectation,
  gitSnapshot,
  interactionApplicability,
  listen,
  loadMatrix,
  newDiagnostics,
  newPage,
  readRuntimeRoute,
  redactScenario,
  sha256,
  sha256File,
  stateApplicability,
  visitPathFor,
  waitForApp,
  waitForMountedPendingMidProbe,
  waitForNetworkSettle,
  waitForPendingRequest,
} from './lib/stage8-full-acceptance-runtime.mjs'
import {
  BINDING_PATHS,
  OFFICIAL_PHASES,
  assertBindingUnchanged,
  assertCleanOfficialBinding,
  assertEnvironmentSemantics,
  assertInteractionSemantics,
  assertStateSemantics,
  captureOfficialBinding,
  evaluateOfficialPass,
} from './lib/stage8-full-acceptance-contract.mjs'
import {
  classifyScenarioFailure,
  deriveOfficialCounts,
  getRouteDescriptor,
} from './lib/stage8-full-acceptance-descriptors.mjs'

const HARNESS_PATH = fileURLToPath(import.meta.url)
const LIB_PATH = fileURLToPath(new URL('./lib/stage8-full-acceptance-runtime.mjs', import.meta.url))
const DESCRIPTOR_PATH = fileURLToPath(new URL('./lib/stage8-full-acceptance-descriptors.mjs', import.meta.url))
const CONTRACT_PATH = fileURLToPath(new URL('./lib/stage8-full-acceptance-contract.mjs', import.meta.url))
const FRONTEND = path.resolve(path.dirname(HARNESS_PATH), '..')
const REPO = path.resolve(FRONTEND, '..')
const MATRIX_PATH = path.join(REPO, 'docs/uiux-stage8-acceptance-rollout/ACCEPTANCE_MATRIX.json')
const DIST = process.env.STAGE8_FULL_ACCEPTANCE_DIST || '/tmp/stage8-full-acceptance-dist'
const OUTPUT_ROOT = process.env.STAGE8_FULL_ACCEPTANCE_OUT || '/tmp/stage8-full-acceptance-runs'
const RUN_ID = `stage8-full-acceptance-${new Date().toISOString().replace(/[-:.]/gu, '')}`
const OUTPUT_DIR = path.join(OUTPUT_ROOT, RUN_ID)
const screenshotDigests = []

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

function progress(stage, details = {}) {
  process.stdout.write(`${JSON.stringify({ event: 'stage8-full-acceptance', runId: RUN_ID, stage, ...details })}\n`)
}

function scenarioDigest(scenario) {
  return sha256(
    JSON.stringify({
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
    }),
  )
}

async function runScenario({
  browser,
  baseUrl,
  controller,
  serverState,
  route,
  profile,
  viewport,
  kind,
  state = 'normal',
  interaction = null,
  environment = 'mobile-browser',
  reducedMotion = false,
  zoom200 = false,
}) {
  const diagnostics = newDiagnostics()
  const descriptor = getRouteDescriptor(route.name)
  controller.profile = profile
  controller.mode = state === 'loading' ? 'loading' : state
  controller.delayMs = state === 'loading' || state === 'slow' ? 2500 : 0
  controller.staleEndpoint = state === 'stale' ? descriptor.states.stale.endpoint || '' : ''
  const snapshotBefore = {
    unknown: serverState.unknownApiRequests,
    mutating: serverState.mutatingApiRequests,
  }
  if (state === 'stale') {
    serverState.staleHits = {}
    serverState.staleCompletions = []
  }
  serverState.identityRequestCount = 0
  const { context, page } = await newPage(browser, baseUrl, viewport, diagnostics, profile, {
    reducedMotion: reducedMotion || interaction === 'reduced-motion',
    environment,
  })
  const expected = finalRouteExpectation(route, profile)
  const failures = []
  if (expected.sourceDrift) {
    failures.push(expected.driftReason || 'sourceDrift')
  }
  let probe = null
  let midProbe = null
  let errorProbe = null
  let actual = null
  try {
    const target = `${baseUrl}${visitPathFor(route)}`
    await page.goto(target, { waitUntil: 'domcontentloaded', timeout: 30_000 })
    if (state === 'loading' || state === 'slow') {
      const pending = await waitForMountedPendingMidProbe(
        page,
        descriptor.states[state].endpoint || '',
        8000,
      )
      midProbe = await collectUiProbe(page)
      midProbe.identityRequestCount = serverState.identityRequestCount
      midProbe.pendingRequest = pending.pendingRequest
      await waitForNetworkSettle(page)
      await waitForApp(page)
    } else if (state === 'stale' && expected.kind === 'render-route') {
      const staleEndpoint = descriptor.states.stale.endpoint || ''
      await waitForPendingRequest(page, staleEndpoint, 8000)
      const refreshSelector = descriptor.states.stale.refreshSelector || ''
      if (refreshSelector) {
        const refresh = page.locator(refreshSelector)
        if ((await refresh.count()) > 0) {
          await refresh.first().click({ timeout: 1500 }).catch(() => {})
        }
      }
      const staleWaitStarted = Date.now()
      while (Date.now() - staleWaitStarted < 4000) {
        const hits = Object.values(serverState.staleHits || {}).reduce((sum, value) => sum + value, 0)
        if (hits >= 2) break
        await page.waitForTimeout(50)
      }
      await waitForNetworkSettle(page)
      await waitForApp(page)
    } else {
      await waitForApp(page)
      await waitForNetworkSettle(page).catch(() => {})
    }
    if (zoom200 || interaction === 'zoom-200') {
      const session = await context.newCDPSession(page)
      await session.send('Emulation.setPageScaleFactor', { pageScaleFactor: 2 })
      await page.waitForTimeout(120)
    }
    if (interaction === 'keyboard') {
      const before = await page.evaluate(() => document.activeElement?.outerHTML?.slice(0, 80) || '')
      await page.keyboard.press('Tab')
      await page.keyboard.press('Tab')
      await page.keyboard.press('Shift+Tab')
      const afterTab = await page.evaluate(() => ({
        html: document.activeElement?.outerHTML?.slice(0, 80) || '',
        focusVisible: document.activeElement?.matches?.(':focus-visible') === true,
        safe:
          document.activeElement instanceof HTMLAnchorElement ||
          document.activeElement?.getAttribute('role') === 'tab',
      }))
      if (afterTab.safe) {
        await page.keyboard.press('Enter')
        await page.keyboard.press('Space')
      }
      await page.keyboard.press('Escape')
      midProbe = midProbe || {}
      midProbe.tabChanged = before !== afterTab.html
      midProbe.focusVisible = afterTab.focusVisible
    }
    if (interaction === 'touch') {
      const candidate = page.locator(descriptor.touch.selector).first()
      if ((await candidate.count()) > 0) {
        await candidate.click({ timeout: 2000 })
        midProbe = midProbe || {}
        midProbe.touchClicked = true
        const expectedNames = [
          descriptor.touch.expectedName,
          ...(descriptor.touch.expectedNameAny || []),
        ].filter(Boolean)
        if (expectedNames.length) {
          await page
            .waitForFunction(
              (names) => {
                const app = document.querySelector('#app')?.__vue_app__
                const name = app?.config?.globalProperties?.$router?.currentRoute?.value?.name
                return names.includes(name)
              },
              expectedNames,
              { timeout: 4000 },
            )
            .catch(() => {})
        }
        await waitForApp(page)
        await waitForNetworkSettle(page).catch(() => {})
      }
    }
    if (state === 'error' && expected.kind === 'render-route') {
      errorProbe = await collectUiProbe(page)
      const retry = page.getByRole('button', { name: /تلاش|دوباره|retry/i })
      if ((await retry.count()) > 0) {
        controller.mode = 'normal'
        controller.delayMs = 0
        await retry.first().click({ timeout: 1500 }).catch(() => {})
        await waitForNetworkSettle(page).catch(() => {})
        await waitForApp(page)
      }
    }
    await waitForApp(page)
    actual = await readRuntimeRoute(page)
    probe = await collectUiProbe(page)
    if (interaction === 'keyboard') {
      probe.tabCycleObserved = Boolean(midProbe?.tabChanged)
      probe.focusVisible = Boolean(midProbe?.focusVisible || probe.focusVisible)
    }
    if (interaction === 'touch') {
      probe.touchActivated = Boolean(midProbe?.touchClicked)
    }
    if ((zoom200 || interaction === 'zoom-200') && descriptor.zoom?.internalStrip) {
      const revealed = await page.evaluate((stripSel) => {
        const focused =
          document.activeElement instanceof HTMLElement && document.activeElement !== document.body
            ? document.activeElement
            : null
        const strip = document.querySelector(stripSel)
        if (!focused || !strip) {
          return { expected: false, selectedControlInStrip: true, hitTestPassed: true }
        }
        focused.scrollIntoView({ block: 'nearest', inline: 'nearest' })
        const rect = focused.getBoundingClientRect()
        const stripRect = strip.getBoundingClientRect()
        const inside =
          rect.left >= stripRect.left - 2 &&
          rect.right <= stripRect.right + 2 &&
          rect.top >= stripRect.top - 2 &&
          rect.bottom <= stripRect.bottom + 2
        const cx = (rect.left + rect.right) / 2
        const cy = (rect.top + rect.bottom) / 2
        const hit = document.elementFromPoint(cx, cy)
        return {
          expected: true,
          selectedControlInStrip: inside,
          hitTestPassed: Boolean(hit && (hit === focused || focused.contains(hit) || hit.contains(focused))),
        }
      }, descriptor.zoom.internalStrip)
      probe.zoomStripExpected = revealed.expected
      probe.selectedControlInStrip = revealed.selectedControlInStrip
      probe.hitTestPassed = revealed.hitTestPassed
    }
    const staleOld = (serverState.staleCompletions || []).find((item) => item.mode === 'stale-old')
    const staleNew = (serverState.staleCompletions || []).find((item) => item.mode === 'stale-new')
    if (probe) {
      probe.staleOldCompletedAt = staleOld?.completedAt || 0
      probe.staleNewCompletedAt = staleNew?.completedAt || 0
      probe.staleTargetHits = Object.values(serverState.staleHits || {}).reduce((sum, value) => sum + value, 0)
      probe.identityBootstrapBroken = false
      probe.mutatingProductRequest = serverState.mutatingApiRequests > snapshotBefore.mutating
    }
    const stateProbe = state === 'error' && errorProbe ? errorProbe : probe
    if (state === 'error' && errorProbe && probe) {
      errorProbe.landedRecovery = errorProbe.landedRecovery || probe.landedRecovery
      errorProbe.identityBootstrapBroken = probe.identityBootstrapBroken
    }
    const skipRouteAssert =
      (interaction === 'touch' && descriptor.touch.applicable) ||
      (interaction === 'keyboard' && route.name === 'system-recovery' && actual?.name === 'login')
    if (interaction === 'touch' && descriptor.touch.applicable) {
      const landed = actual?.name
      const expectedNames = [
        descriptor.touch.expectedName,
        ...(descriptor.touch.expectedNameAny || []),
      ].filter(Boolean)
      if (expectedNames.length && !expectedNames.includes(landed)) {
        failures.push(`touch landed ${landed} != ${expectedNames.join('|')}`)
      }
    }
    if (!skipRouteAssert) {
      failures.push(...assertOutcome(actual, expected))
    }
    if (['access', 'viewport', 'state', 'interaction', 'environment'].includes(kind)) {
      if (
        expected.kind === 'render-route' ||
        expected.kind === 'redirect-canonical' ||
        expected.kind === 'redirect-home' ||
        expected.kind === 'redirect-login' ||
        expected.kind === 'redirect-forbidden-recovery' ||
        expected.canonical
      ) {
        const landedRoute =
          (actual?.name && controller.routesByName?.get(actual.name)) ||
          (expected.finalName && controller.routesByName?.get(expected.finalName)) ||
          route
        failures.push(...assertCommonUi(probe, expected, route, landedRoute))
        if (kind === 'state') {
          failures.push(
            ...assertStateSemantics(
              stateProbe,
              midProbe,
              state,
              landedRoute?.uiContract?.protection || route.uiContract?.protection,
              expected.kind,
              route.name,
            ),
          )
          if (state === 'error' && errorProbe?.retryVisible && probe.errorVisible) {
            failures.push('error retry did not recover in a separate probe')
          }
        }
        if (kind === 'interaction') {
          failures.push(
            ...assertInteractionSemantics(
              probe,
              interaction,
              landedRoute?.uiContract?.protection || route.uiContract?.protection,
            ),
          )
        }
        if (kind === 'environment') {
          failures.push(...assertEnvironmentSemantics(probe, environment))
        }
      }
    }
    const shouldShot =
      (kind === 'viewport' && (viewport.width === 390 || viewport.width === 1440)) ||
      (kind === 'state' && ['loading', 'empty', 'error', 'offline', 'stale'].includes(state))
    if (shouldShot) {
      const shotDir = path.join(OUTPUT_DIR, 'screenshots')
      await mkdir(shotDir, { recursive: true })
      const shotName = `${kind}-${route.name}-${viewport.width}-${state}-${interaction || 'none'}-${environment}.png`
      const shotPath = path.join(shotDir, shotName)
      await page.screenshot({ path: shotPath, fullPage: false })
      screenshotDigests.push({ id: shotName, sha256: sha256File(shotPath) })
    }
    const counts = diagnosticCounts(diagnostics, {
      allowInjected: state === 'error' || state === 'offline',
    })
    if (counts.pageErrors > 0) failures.push(`pageErrors ${counts.pageErrors}`)
    if (counts.unexpectedConsole > 0) failures.push(`unexpectedConsole ${counts.unexpectedConsole}`)
    if (counts.requestFailures > 0 && state !== 'offline') failures.push(`requestFailures ${counts.requestFailures}`)
    if (counts.externalRequests > 0) failures.push(`externalRequests ${counts.externalRequests}`)
    if (counts.mutatingRequests > 0) failures.push(`mutatingRequests ${counts.mutatingRequests}`)
    const unknownDelta = serverState.unknownApiRequests - snapshotBefore.unknown
    if (unknownDelta > 0) {
      failures.push(`unknownApi ${serverState.unknownApiPaths.slice(-unknownDelta).join(',')}`)
    }
  } catch (error) {
    failures.push(error instanceof Error ? error.message : String(error))
  } finally {
    await context.close()
  }
  const scenario = {
    id: [
      kind,
      route.name,
      profile.id,
      `${viewport.width}x${viewport.height}`,
      state,
      interaction || 'none',
      environment,
    ].join('/'),
    kind,
    route: route.name,
    path: visitPathFor(route),
    protection: route.uiContract?.protection || null,
    uiAssertionScope:
      route.uiContract?.protection === 'full'
        ? 'protected-legacy-access-overflow-diagnostics'
        : 'common-ui',
    profile: profile.id,
    viewport: { width: viewport.width, height: viewport.height },
    state,
    interaction,
    environment,
    expectedKind: expected.kind,
    matrixKind: expected.matrixKind,
    sourceDrift: expected.sourceDrift || false,
    canonical: expected.canonical || null,
    actual,
    probe,
    failures,
    passed: failures.length === 0,
    diagnostics: diagnosticCounts(diagnostics, {
      allowInjected: state === 'error' || state === 'offline',
    }),
  }
  scenario.digest = scenarioDigest(scenario)
  return scenario
}

async function main() {
  assert(
    process.env.STAGE8_FULL_ACCEPTANCE_AUTHORIZATION === RUN_AUTHORIZATION,
    'Stage 8 full acceptance is locked behind the exact authorization value.',
  )
  const bindingBefore = captureOfficialBinding(REPO)
  const cleanFailures = assertCleanOfficialBinding(bindingBefore)
  assert(cleanFailures.length === 0, cleanFailures.join('; '))
  assert(bindingBefore.branch === 'main', `Stage 8 full acceptance must run on main, found ${bindingBefore.branch}`)
  const git = gitSnapshot(REPO)
  assert(git.diffCheck === '', 'git diff --check failed')
  const matrix = loadMatrix(MATRIX_PATH)
  const profiles = attachProfileRuntime(matrix.accessProfiles)
  const productHashes = bindingBefore.hashes
  assert(fs.existsSync(path.join(DIST, 'index.html')), `Production dist missing at ${DIST}`)
  const distBefore = distFingerprint(DIST)
  const requireFromFrontend = createRequire(path.join(FRONTEND, 'package.json'))
  const { chromium } = requireFromFrontend('playwright')
  const browserExecutable =
    process.env.STAGE8_PLAYWRIGHT_EXECUTABLE ||
    '/tmp/stage8b-pw-browsers/chromium_headless_shell-1217/chrome-headless-shell-linux64/chrome-headless-shell'
  await mkdir(OUTPUT_DIR, { recursive: true })

  const controller = {
    profile: profiles[0],
    mode: 'normal',
    delayMs: 0,
    staleEndpoint: '',
    routesByName: new Map(matrix.routes.map((item) => [item.name, item])),
  }
  const { server, state: serverState } = createFixtureServer(DIST, controller)
  const baseUrl = await listen(server)
  const browser = await chromium.launch({ headless: true, executablePath: browserExecutable })
  const scenarios = []
  let failed = 0

  const requestedPhases = new Set(
    String(process.env.STAGE8_FULL_ACCEPTANCE_PHASES || 'access,viewport,state,interaction,environment')
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean),
  )
  progress('start', {
    commit: git.commit,
    tree: git.tree,
    dist: distBefore.sha256,
    phases: [...requestedPhases],
  })

  try {
    if (!requestedPhases.has('access')) {
      progress('access-skipped', { reason: 'phase-filter' })
    }
    for (const route of requestedPhases.has('access') ? matrix.routes : []) {
      for (const profile of profiles) {
        const scenario = await runScenario({
          browser,
          baseUrl,
          controller,
          serverState,
          route,
          profile,
          viewport: ACCESS_VIEWPORT,
          kind: 'access',
        })
        scenarios.push(scenario)
        if (!scenario.passed) failed += 1
        progress('access', {
          id: scenario.id,
          passed: scenario.passed,
          failures: scenario.failures,
          done: scenarios.filter((item) => item.kind === 'access').length,
          total: 270,
        })
      }
    }

    if (!requestedPhases.has('viewport')) {
      progress('viewport-skipped', { reason: 'phase-filter' })
    }
    for (const route of requestedPhases.has('viewport') ? matrix.routes : []) {
      const profile = allowedProfileForRoute(route, profiles)
      for (const viewport of VIEWPORTS) {
        const scenario = await runScenario({
          browser,
          baseUrl,
          controller,
          serverState,
          route,
          profile,
          viewport,
          kind: 'viewport',
        })
        scenarios.push(scenario)
        if (!scenario.passed) failed += 1
      }
      progress('viewport-route', {
        route: route.name,
        done: scenarios.filter((item) => item.kind === 'viewport').length,
        total: 240,
      })
    }

    if (!requestedPhases.has('state')) {
      progress('states-skipped', { reason: 'phase-filter' })
    }
    for (const route of requestedPhases.has('state') ? matrix.routes : []) {
      const profile = allowedProfileForRoute(route, profiles)
      for (const item of stateApplicability(route.name)) {
        if (!item.applicable) {
          const scenario = {
            id: `state/${route.name}/${profile.id}/${item.state}/na`,
            kind: 'state',
            route: route.name,
            profile: profile.id,
            state: item.state,
            applicable: false,
            reason: item.reason,
            passed: true,
            failures: [],
          }
          scenario.digest = scenarioDigest(scenario)
          scenarios.push(scenario)
          continue
        }
        const scenario = await runScenario({
          browser,
          baseUrl,
          controller,
          serverState,
          route,
          profile,
          viewport: ACCESS_VIEWPORT,
          kind: 'state',
          state: item.state,
        })
        scenarios.push(scenario)
        if (!scenario.passed) {
          failed += 1
          progress('state', { id: scenario.id, passed: false, failures: scenario.failures })
        }
      }
    }
    progress('states-done', { count: scenarios.filter((item) => item.kind === 'state').length })

    if (!requestedPhases.has('interaction')) {
      progress('interactions-skipped', { reason: 'phase-filter' })
    }
    for (const route of requestedPhases.has('interaction') ? matrix.routes : []) {
      const profile = allowedProfileForRoute(route, profiles)
      for (const item of interactionApplicability(route.name)) {
        if (!item.applicable) {
          const scenario = {
            id: `interaction/${route.name}/${item.interaction}/na`,
            kind: 'interaction',
            route: route.name,
            profile: profile.id,
            interaction: item.interaction,
            applicable: false,
            reason: item.reason,
            passed: true,
            failures: [],
          }
          scenario.digest = scenarioDigest(scenario)
          scenarios.push(scenario)
          continue
        }
        const scenario = await runScenario({
          browser,
          baseUrl,
          controller,
          serverState,
          route,
          profile,
          viewport: ACCESS_VIEWPORT,
          kind: 'interaction',
          interaction: item.interaction,
          reducedMotion: item.interaction === 'reduced-motion',
          zoom200: item.interaction === 'zoom-200',
        })
        scenarios.push(scenario)
        if (!scenario.passed) failed += 1
      }
    }
    progress('interactions-done', { count: scenarios.filter((item) => item.kind === 'interaction').length })

    if (!requestedPhases.has('environment')) {
      progress('environments-skipped', { reason: 'phase-filter' })
    }
    for (const route of requestedPhases.has('environment') ? matrix.routes : []) {
      const profile = allowedProfileForRoute(route, profiles)
      for (const environment of ENVIRONMENTS) {
        const applicability = environmentApplicability(route.name, environment)
        if (!applicability.applicable) {
          const scenario = {
            id: `environment/${route.name}/${environment}/na`,
            kind: 'environment',
            route: route.name,
            profile: profile.id,
            environment,
            applicable: false,
            reason: applicability.reason,
            passed: true,
            failures: [],
          }
          scenario.digest = scenarioDigest(scenario)
          scenarios.push(scenario)
          continue
        }
        const scenario = await runScenario({
          browser,
          baseUrl,
          controller,
          serverState,
          route,
          profile,
          viewport: environment === 'mobile-browser' ? ACCESS_VIEWPORT : VIEWPORTS[2],
          kind: 'environment',
          environment,
        })
        scenarios.push(scenario)
        if (!scenario.passed) failed += 1
      }
    }
    progress('environments-done', { count: scenarios.filter((item) => item.kind === 'environment').length })
  } finally {
    await browser.close()
    await closeServer(server)
  }

  const bindingAfter = captureOfficialBinding(REPO)
  const distAfter = distFingerprint(DIST)
  const bindingDrift = assertBindingUnchanged(bindingBefore, bindingAfter)
  assert(bindingDrift.length === 0, bindingDrift.join('; '))
  assert(distAfter.sha256 === distBefore.sha256, 'dist fingerprint drifted during the run')

  const access = scenarios.filter((item) => item.kind === 'access')
  const viewport = scenarios.filter((item) => item.kind === 'viewport')
  const states = scenarios.filter((item) => item.kind === 'state')
  const interactions = scenarios.filter((item) => item.kind === 'interaction')
  const environments = scenarios.filter((item) => item.kind === 'environment')
  const executedStates = states.filter((item) => item.applicable !== false)
  const executedInteractions = interactions.filter((item) => item.applicable !== false)
  const executedEnvironments = environments.filter((item) => item.applicable !== false)
  const naStates = states.filter((item) => item.applicable === false)
  const naInteractions = interactions.filter((item) => item.applicable === false)
  const naEnvironments = environments.filter((item) => item.applicable === false)
  const ids = scenarios.map((item) => item.id)
  const uniqueIdCount = new Set(ids).size
  const duplicateIdCount = ids.length - uniqueIdCount
  const sourceDriftCount = scenarios.filter((item) => item.sourceDrift).length
  const officialRun = OFFICIAL_PHASES.every((phase) => requestedPhases.has(phase))
  const diagnosticTotals = scenarios.reduce(
    (acc, item) => {
      const counts = item.diagnostics || {}
      acc.unexpectedConsole += counts.unexpectedConsole || 0
      acc.pageErrors += counts.pageErrors || 0
      acc.externalRequests += counts.externalRequests || 0
      if (item.state !== 'offline') acc.requestFailuresOutsideOffline += counts.requestFailures || 0
      return acc
    },
    { unexpectedConsole: 0, pageErrors: 0, externalRequests: 0, requestFailuresOutsideOffline: 0 },
  )
  const counters = {
    accessCellsExpected: 270,
    accessCellsExecuted: access.length,
    accessCellsPassed: access.filter((item) => item.passed).length,
    routeViewportExpected: 240,
    routeViewportExecuted: viewport.length,
    routeViewportPassed: viewport.filter((item) => item.passed).length,
    stateTotal: states.length,
    stateExecuted: executedStates.length,
    stateNotApplicable: naStates.length,
    statePassed: executedStates.filter((item) => item.passed).length,
    interactionTotal: interactions.length,
    interactionExecuted: executedInteractions.length,
    interactionNotApplicable: naInteractions.length,
    interactionPassed: executedInteractions.filter((item) => item.passed).length,
    environmentTotal: environments.length,
    environmentExecuted: executedEnvironments.length,
    environmentNotApplicable: naEnvironments.length,
    environmentPassed: executedEnvironments.filter((item) => item.passed).length,
  }
  const derivedCounts = deriveOfficialCounts()
  const failedItems = scenarios.filter((item) => !item.passed)
  const classifiedFailures = failedItems.map((item) => ({
    id: item.id,
    route: item.route,
    kind: item.kind,
    state: item.state || null,
    interaction: item.interaction || null,
    ...classifyScenarioFailure(item),
    failures: item.failures,
    unnamedFingerprints: item.probe?.unnamedFingerprints || [],
  }))
  const failureByRootCause = {}
  const failureByBucket = {
    'confirmed-product': 0,
    'harness-fixture': 0,
    'source-derived-na': 0,
  }
  for (const item of classifiedFailures) {
    failureByBucket[item.bucket] = (failureByBucket[item.bucket] || 0) + 1
    failureByRootCause[item.rootCause] = (failureByRootCause[item.rootCause] || 0) + 1
  }
  const sourceDerivedNa = [...naStates, ...naInteractions, ...naEnvironments].map((item) => ({
    id: item.id,
    kind: item.kind,
    route: item.route,
    key: item.state || item.interaction || item.environment,
    reason: item.reason,
  }))
  failureByBucket['source-derived-na'] = sourceDerivedNa.length
  const verdict = evaluateOfficialPass({
    officialRun,
    failed,
    sourceDriftCount,
    uniqueIdCount,
    duplicateIdCount,
    counters,
    server: serverState,
    diagnosticTotals,
  })
  const contactSheetPath = path.join(OUTPUT_DIR, 'CONTACT_SHEET.html')
  await writeFile(
    contactSheetPath,
    `<!doctype html><html lang="fa" dir="rtl"><body>${screenshotDigests
      .map((item) => `<figure><img alt="${item.id}" src="screenshots/${item.id}"><figcaption>${item.id}</figcaption></figure>`)
      .join('')}</body></html>\n`,
  )
  const report = {
    schemaVersion: 3,
    stage: 8,
    runId: RUN_ID,
    claimBoundary:
      'Local synthetic production-build full-acceptance execution. It is not owner aesthetic acceptance, staging authority, Sites authority, or production authority. Previous report fb69a47b is provisional and non-promotable.',
    git: {
      branch: bindingAfter.branch,
      commit: bindingAfter.commit,
      tree: bindingAfter.tree,
      parents: git.parents,
      porcelain: bindingAfter.porcelain,
    },
    productHashes,
    bindingPaths: BINDING_PATHS,
    harnessSha256: sha256File(HARNESS_PATH),
    runtimeSha256: sha256File(LIB_PATH),
    descriptorSha256: sha256File(DESCRIPTOR_PATH),
    contractSha256: sha256File(CONTRACT_PATH),
    derivedCounts,
    dist: distBefore,
    matrixSourceSnapshot: matrix.sourceSnapshot,
    sourceDriftCount,
    counters,
    componentCanonicalizations: access.filter((item) => item.canonical).map((item) => ({
      id: item.id,
      passed: item.passed,
      actual: item.actual,
    })),
    classification: {
      scenarioFailureCount: failedItems.length,
      rootCauseCount: Object.keys(failureByRootCause).length,
      byBucket: failureByBucket,
      byRootCause: failureByRootCause,
      items: classifiedFailures,
    },
    sourceDerivedNa: {
      count: sourceDerivedNa.length,
      items: sourceDerivedNa,
    },
    failedScenarios: failedItems.map(redactScenario),
    scenarioDigests: scenarios.map((item) => ({ id: item.id, digest: item.digest, passed: item.passed })),
    screenshotCount: screenshotDigests.length,
    screenshotDigests,
    contactSheet: { path: contactSheetPath, count: screenshotDigests.length },
    server: {
      apiRequests: serverState.apiRequests,
      expectedApiRequests: serverState.expectedApiRequests,
      unknownApiRequests: serverState.unknownApiRequests,
      unknownApiPaths: [...new Set(serverState.unknownApiPaths)],
      mutatingApiRequests: serverState.mutatingApiRequests,
    },
    officialRun,
    passFailures: verdict.failures,
    passed: verdict.passed,
  }

  const reportPath = path.join(OUTPUT_DIR, 'STAGE8_FULL_ACCEPTANCE_REPORT.json')
  await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`)
  progress('complete', {
    passed: report.passed,
    failed,
    reportPath,
    counters: report.counters,
  })
  if (!report.passed) {
    process.exitCode = 1
  }
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.stack || error.message : String(error)}\n`)
  process.exit(1)
})
