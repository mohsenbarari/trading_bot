#!/usr/bin/env node
/**
 * Phase 1 runtime montage for WebApp UIUX Unification V3.
 * Reuses the existing local fixture server. Does not write Stage 8 receipts.
 */
import { createHash } from 'node:crypto'
import { mkdir, writeFile } from 'node:fs/promises'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import {
  assertCommonUi,
  attachProfileRuntime,
  closeServer,
  collectUiProbe,
  createFixtureServer,
  diagnosticCounts,
  distFingerprint,
  finalRouteExpectation,
  gitSnapshot,
  listen,
  loadMatrix,
  newDiagnostics,
  newPage,
  readRuntimeRoute,
  visitPathFor,
  waitForApp,
  waitForNetworkSettle,
} from './lib/stage8-full-acceptance-runtime.mjs'
import { allowedProfileForRoute } from './lib/stage8-full-acceptance-runtime.mjs'
import { getRouteDescriptor } from './lib/stage8-full-acceptance-descriptors.mjs'

const SCRIPT_PATH = fileURLToPath(import.meta.url)
const FRONTEND = path.resolve(path.dirname(SCRIPT_PATH), '..')
const REPO = path.resolve(FRONTEND, '..')
const MATRIX_PATH = path.join(REPO, 'docs/uiux-stage8-acceptance-rollout/ACCEPTANCE_MATRIX.json')
const DIST = process.env.UIUX_V3_DIST || '/tmp/uiux-unification-v3-dist'
const OUTPUT_ROOT = process.env.UIUX_V3_PHASE1_OUT || '/tmp/uiux-unification-v3-phase1'
const RUN_ID = `uiux-v3-phase1-${new Date().toISOString().replace(/[-:.]/gu, '')}`
const OUTPUT_DIR = path.join(OUTPUT_ROOT, RUN_ID)
const VIEWPORTS = [
  { id: '390x844', width: 390, height: 844 },
  { id: '1440x900', width: 1440, height: 900 },
]
const EXTRA_STATES = ['loading', 'empty', 'error']

function sha256File(filePath) {
  return createHash('sha256').update(fs.readFileSync(filePath)).digest('hex')
}

function classifyFinding(failures) {
  const confirmed = []
  const falsePositive = []
  for (const failure of failures) {
    if (/FULL route unexpectedly used Vazirmatn/.test(failure)) {
      falsePositive.push({
        failure,
        reason: 'protected-legacy-primary-family-is-not-v2-scope',
      })
      continue
    }
    confirmed.push(failure)
  }
  return { confirmed, falsePositive }
}

async function runOne({
  browser,
  baseUrl,
  controller,
  serverState,
  route,
  profile,
  viewport,
  state,
}) {
  const diagnostics = newDiagnostics()
  const descriptor = getRouteDescriptor(route.name)
  controller.profile = profile
  controller.mode = state === 'loading' ? 'loading' : state
  controller.delayMs = 0
  controller.holdEndpoint =
    state === 'loading' ? descriptor.states?.[state]?.endpoint || '' : ''
  controller.releaseHeldRequest = state !== 'loading'
  controller.staleEndpoint = ''
  controller.allowIdentityPageData = Boolean(descriptor.states?.[state]?.identityPageData)
  const snapshotBefore = {
    unknown: serverState.unknownApiRequests,
    mutating: serverState.mutatingApiRequests,
  }
  const { context, page } = await newPage(browser, baseUrl, viewport, diagnostics, profile, {
    environment: viewport.width >= 1024 ? 'desktop-browser' : 'mobile-browser',
    seedCurrentUserSummary: !descriptor.states?.[state]?.identityPageData,
  })
  const expected = finalRouteExpectation(route, profile)
  const failures = []
  let probe = null
  let actual = null
  const screenshotName = `${route.name}__${viewport.id}__${state}.png`
  const screenshotPath = path.join(OUTPUT_DIR, 'screenshots', screenshotName)
  try {
    await page.goto(`${baseUrl}${visitPathFor(route)}`, {
      waitUntil: 'domcontentloaded',
      timeout: 30_000,
    })
    if (state === 'loading') {
      await page.waitForTimeout(180)
      controller.releaseHeldRequest = true
    }
    await waitForApp(page)
    await waitForNetworkSettle(page).catch(() => {})
    actual = await readRuntimeRoute(page)
    probe = await collectUiProbe(page)
    failures.push(...assertCommonUi(probe, expected, route, route))
    await page.screenshot({ path: screenshotPath, fullPage: false })
  } catch (error) {
    failures.push(error instanceof Error ? error.message : String(error))
  } finally {
    await context.close()
  }
  const counts = diagnosticCounts(diagnostics)
  const classified = classifyFinding(failures)
  return {
    id: `${route.name}/${viewport.id}/${state}`,
    route: route.name,
    path: route.path,
    viewport: viewport.id,
    state,
    profile: profile.id,
    expectedKind: expected.kind,
    actual,
    probe: probe
      ? {
          documentOverflow: probe.documentOverflow,
          appOverflow: probe.appOverflow,
          visibleMainCount: probe.visibleMainCount,
          headingCount: probe.headingCount,
          unnamedInteractive: probe.unnamedInteractive,
          nestedInteractive: probe.nestedInteractive,
          ctaAboveNav: probe.ctaAboveNav,
          dir: probe.dir,
          vazirmatn: probe.vazirmatn,
          fontFamily: probe.fontFamily,
          loadingVisible: probe.loadingVisible,
          emptyVisible: probe.emptyVisible,
          errorVisible: probe.errorVisible,
          clippedControlCount: probe.clippedControlCount,
          clippedTextCount: probe.clippedTextCount,
          minTarget: probe.minTarget,
          navVisible: probe.navVisible,
        }
      : null,
    diagnostics: counts,
    unknownApiDelta: serverState.unknownApiRequests - snapshotBefore.unknown,
    mutatingDelta: serverState.mutatingApiRequests - snapshotBefore.mutating,
    screenshot: screenshotName,
    screenshotSha256: fs.existsSync(screenshotPath) ? sha256File(screenshotPath) : null,
    failures: classified.confirmed,
    falsePositives: classified.falsePositive,
    passed: classified.confirmed.length === 0,
  }
}

async function main() {
  if (!fs.existsSync(path.join(DIST, 'index.html'))) {
    throw new Error(`missing production dist at ${DIST}`)
  }
  await mkdir(path.join(OUTPUT_DIR, 'screenshots'), { recursive: true })
  const matrix = loadMatrix(MATRIX_PATH)
  const profiles = attachProfileRuntime(matrix.accessProfiles)
  const controller = {
    profile: profiles.find((item) => item.id === 'senior-admin'),
    mode: 'normal',
    delayMs: 0,
    holdEndpoint: '',
    releaseHeldRequest: true,
    staleEndpoint: '',
    allowIdentityPageData: false,
  }
  const { server, state } = createFixtureServer(DIST, controller)
  const baseUrl = await listen(server)
  const browser = await chromium.launch({ headless: true })
  const scenarios = []
  try {
    for (const route of matrix.routes) {
      const profile = allowedProfileForRoute(route, profiles)
      for (const viewport of VIEWPORTS) {
        scenarios.push(
          await runOne({
            browser,
            baseUrl,
            controller,
            serverState: state,
            route,
            profile,
            viewport,
            state: 'normal',
          }),
        )
        process.stdout.write(
          `${JSON.stringify({ event: 'uiux-v3-phase1', id: `${route.name}/${viewport.id}/normal` })}\n`,
        )
      }
      const descriptor = getRouteDescriptor(route.name)
      for (const extra of EXTRA_STATES) {
        const spec = descriptor.states?.[extra]
        if (!spec || spec.applicable === false) continue
        scenarios.push(
          await runOne({
            browser,
            baseUrl,
            controller,
            serverState: state,
            route,
            profile,
            viewport: VIEWPORTS[0],
            state: extra,
          }),
        )
        process.stdout.write(
          `${JSON.stringify({ event: 'uiux-v3-phase1', id: `${route.name}/390x844/${extra}` })}\n`,
        )
      }
    }
  } finally {
    await browser.close()
    await closeServer(server)
  }

  const summary = {
    track: 'webapp-uiux-unification-v3',
    phase: 1,
    independent_of_stage8: true,
    not_acceptance_authority: true,
    runId: RUN_ID,
    git: gitSnapshot(REPO),
    dist: distFingerprint(DIST),
    counts: {
      routes: matrix.routes.length,
      scenarios: scenarios.length,
      passed: scenarios.filter((item) => item.passed).length,
      failed: scenarios.filter((item) => !item.passed).length,
      falsePositiveFindings: scenarios.reduce((sum, item) => sum + item.falsePositives.length, 0),
      unknownApiRequests: state.unknownApiRequests,
      mutatingApiRequests: state.mutatingApiRequests,
      pageErrors: scenarios.reduce((sum, item) => sum + (item.diagnostics?.pageErrors || 0), 0),
      externalRequests: scenarios.reduce((sum, item) => sum + (item.diagnostics?.externalRequests || 0), 0),
    },
    failedIds: scenarios.filter((item) => !item.passed).map((item) => item.id),
    scenarios,
  }
  const summaryPath = path.join(OUTPUT_DIR, 'RUNTIME_AUDIT.json')
  await writeFile(summaryPath, `${JSON.stringify(summary, null, 2)}\n`)
  await writeFile(
    path.join(OUTPUT_DIR, 'RUNTIME_AUDIT.sha256'),
    `${sha256File(summaryPath)}  RUNTIME_AUDIT.json\n`,
  )
  process.stdout.write(`${JSON.stringify({ event: 'uiux-v3-phase1-done', output: OUTPUT_DIR, ...summary.counts })}\n`)
  if (summary.counts.failed) process.exitCode = 2
}

await main()
