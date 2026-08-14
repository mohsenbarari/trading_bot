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
} from './lib/stage8-full-acceptance-runtime.mjs'

const HARNESS_PATH = fileURLToPath(import.meta.url)
const LIB_PATH = fileURLToPath(new URL('./lib/stage8-full-acceptance-runtime.mjs', import.meta.url))
const FRONTEND = path.resolve(path.dirname(HARNESS_PATH), '..')
const REPO = path.resolve(FRONTEND, '..')
const MATRIX_PATH = path.join(REPO, 'docs/uiux-stage8-acceptance-rollout/ACCEPTANCE_MATRIX.json')
const DIST = process.env.STAGE8_FULL_ACCEPTANCE_DIST || '/tmp/stage8-full-acceptance-dist'
const OUTPUT_ROOT = process.env.STAGE8_FULL_ACCEPTANCE_OUT || '/tmp/stage8-full-acceptance-runs'
const RUN_ID = `stage8-full-acceptance-${new Date().toISOString().replace(/[-:.]/gu, '')}`
const OUTPUT_DIR = path.join(OUTPUT_ROOT, RUN_ID)
const ALLOWED_DIRTY = new Set([
  'frontend/scripts/stage8-full-acceptance-browser.mjs',
  'frontend/scripts/lib/stage8-full-acceptance-runtime.mjs',
  'frontend/scripts/stage8-full-acceptance-runtime.test.mjs',
  'frontend/src/components/UserProfile.vue',
  'frontend/src/components/UserProfile.test.ts',
])
const PRODUCT_HASH_PATHS = [
  'frontend/src/router/index.ts',
  'frontend/src/router/uiRouteContract.ts',
  'frontend/src/utils/auth.ts',
  'frontend/src/views/AdminView.vue',
  'models/user.py',
]

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
  controller.profile = profile
  controller.mode = state === 'loading' ? 'loading' : state
  controller.delayMs = state === 'loading' ? 700 : state === 'slow' ? 1200 : 0
  const snapshotBefore = {
    unknown: serverState.unknownApiRequests,
    mutating: serverState.mutatingApiRequests,
  }
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
  let actual = null
  try {
    const target = `${baseUrl}${visitPathFor(route)}`
    await page.goto(target, { waitUntil: 'domcontentloaded', timeout: 30_000 })
    if (state === 'loading') {
      await page.waitForTimeout(180)
    }
    await waitForApp(page)
    if (zoom200 || interaction === 'zoom-200') {
      const session = await context.newCDPSession(page)
      await session.send('Emulation.setPageScaleFactor', { pageScaleFactor: 2 })
      await page.waitForTimeout(120)
    }
    if (interaction === 'keyboard') {
      await page.keyboard.press('Tab')
      await page.keyboard.press('Tab')
      await page.keyboard.press('Shift+Tab')
      await page.keyboard.press('Escape')
    }
    if (interaction === 'touch') {
      const candidate = page.locator('button, a, [role="button"], [role="tab"]').first()
      if ((await candidate.count()) > 0) {
        await candidate.click({ trial: true }).catch(() => {})
      }
    }
    actual = await readRuntimeRoute(page)
    probe = await collectUiProbe(page)
    failures.push(...assertOutcome(actual, expected))
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
      }
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
  const git = gitSnapshot(REPO)
  const dirty = git.status
    ? git.status
        .split('\n')
        .filter(Boolean)
        .map((line) => line.replace(/^.. /u, ''))
    : []
  const unexpectedDirty = dirty.filter((file) => !ALLOWED_DIRTY.has(file))
  assert(unexpectedDirty.length === 0, `Worktree is dirty beyond the Stage 8 harness: ${unexpectedDirty.join(', ')}`)
  assert(git.diffCheck === '', 'git diff --check failed')
  assert(git.branch === 'main', `Stage 8 full acceptance must run on main, found ${git.branch}`)
  const matrix = loadMatrix(MATRIX_PATH)
  const profiles = attachProfileRuntime(matrix.accessProfiles)
  const productHashes = Object.fromEntries(
    PRODUCT_HASH_PATHS.map((relative) => [relative, sha256File(path.join(REPO, relative))]),
  )
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

  const gitAfter = gitSnapshot(REPO)
  const distAfter = distFingerprint(DIST)
  assert(gitAfter.commit === git.commit, 'source commit drifted during the run')
  assert(gitAfter.tree === git.tree, 'source tree drifted during the run')
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
  const report = {
    schemaVersion: 1,
    stage: 8,
    runId: RUN_ID,
    claimBoundary:
      'Local synthetic production-build full-acceptance execution. It is not owner aesthetic acceptance, staging authority, Sites authority, or production authority.',
    git: {
      branch: git.branch,
      commit: git.commit,
      tree: git.tree,
      parents: git.parents,
    },
    productHashes,
    harnessSha256: sha256File(HARNESS_PATH),
    runtimeSha256: sha256File(LIB_PATH),
    dist: distBefore,
    matrixSourceSnapshot: matrix.sourceSnapshot,
    settingsSourceDrift: access.filter((item) => item.sourceDrift).length,
    counters: {
      accessCellsExpected: 270,
      accessCellsExecuted: access.length,
      accessCellsPassed: access.filter((item) => item.passed).length,
      routeViewportExpected: 240,
      routeViewportExecuted: viewport.length,
      routeViewportPassed: viewport.filter((item) => item.passed).length,
      stateExecuted: executedStates.length,
      stateNotApplicable: naStates.length,
      statePassed: executedStates.filter((item) => item.passed).length,
      interactionExecuted: executedInteractions.length,
      interactionPassed: executedInteractions.filter((item) => item.passed).length,
      environmentExecuted: executedEnvironments.length,
      environmentPassed: executedEnvironments.filter((item) => item.passed).length,
    },
    componentCanonicalizations: access.filter((item) => item.canonical).map((item) => ({
      id: item.id,
      passed: item.passed,
      actual: item.actual,
    })),
    failedScenarios: scenarios.filter((item) => !item.passed).map(redactScenario),
    scenarioDigests: scenarios.map((item) => ({ id: item.id, digest: item.digest, passed: item.passed })),
    server: {
      apiRequests: serverState.apiRequests,
      expectedApiRequests: serverState.expectedApiRequests,
      unknownApiRequests: serverState.unknownApiRequests,
      unknownApiPaths: [...new Set(serverState.unknownApiPaths)],
      mutatingApiRequests: serverState.mutatingApiRequests,
    },
    officialRun: ['access', 'viewport', 'state', 'interaction', 'environment'].every((phase) =>
      requestedPhases.has(phase),
    ),
    passed:
      failed === 0 &&
      ['access', 'viewport', 'state', 'interaction', 'environment'].every((phase) => requestedPhases.has(phase)) &&
      access.length === 270 &&
      viewport.length === 240,
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
