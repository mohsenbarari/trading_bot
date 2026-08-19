#!/usr/bin/env node
/**
 * Phase 11 local production-build matrix for WebApp UIUX Unification V3.
 * Reuses the existing fixture server. Does not write Stage 8 receipts.
 */
import { createHash } from 'node:crypto'
import { mkdir, writeFile } from 'node:fs/promises'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium, firefox, webkit } from 'playwright'
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
  recoverIdentityPageDataAfterHold,
  visitPathFor,
  waitForApp,
  waitForNetworkSettle,
} from './lib/stage8-full-acceptance-runtime.mjs'
import { allowedProfileForRoute } from './lib/stage8-full-acceptance-runtime.mjs'
import { getRouteDescriptor } from './lib/stage8-full-acceptance-descriptors.mjs'
import { assertRequestedState } from './lib/uiux-unification-v3-state-evidence.mjs'

const SCRIPT_PATH = fileURLToPath(import.meta.url)
const FRONTEND = path.resolve(path.dirname(SCRIPT_PATH), '..')
const REPO = path.resolve(FRONTEND, '..')
const MATRIX_PATH = path.join(REPO, 'docs/uiux-stage8-acceptance-rollout/ACCEPTANCE_MATRIX.json')
const DIST = process.env.UIUX_V3_DIST || '/tmp/uiux-unification-v3-dist-phase11'
const OUTPUT_ROOT = process.env.UIUX_V3_PHASE11_OUT || '/tmp/uiux-unification-v3-phase11'
const RUN_ID = `uiux-v3-phase11-${new Date().toISOString().replace(/[-:.]/gu, '')}`
const OUTPUT_DIR = path.join(OUTPUT_ROOT, RUN_ID)
const CORE_VIEWPORTS = [
  { id: '390x844', width: 390, height: 844 },
  { id: '1440x900', width: 1440, height: 900 },
]
const EXTRA_VIEWPORTS = [
  { id: '360x740', width: 360, height: 740 },
  { id: '430x932', width: 430, height: 932 },
  { id: '768x1024', width: 768, height: 1024 },
]
const EXTRA_STATES = ['loading', 'empty', 'error']
const SENSITIVE_ROUTES = [
  'home',
  'profile',
  'public-profile',
  'operations-customers',
  'operations-accountants',
  'account',
  'admin',
  'login',
  'market',
  'messenger',
  'share-receive',
  'system-recovery',
]
const FAMILY_ROUTES = [
  'home',
  'profile',
  'operations-customers',
  'account',
  'admin',
  'login',
]
const KEYBOARD_ROUTES = ['home', 'login', 'profile', 'operations-customers', 'account']
const ZOOM_ROUTES = ['home', 'profile', 'operations-customers', 'account', 'admin', 'login']
const REDUCED_MOTION_ROUTES = ['market', 'home', 'profile']
const PWA_ROUTES = ['home', 'account', 'profile']
const ONLY = process.env.UIUX_V3_PHASE11_ONLY || ''

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

function routeByName(matrix, name) {
  const route = matrix.routes.find((item) => item.name === name)
  if (!route) throw new Error(`missing route ${name}`)
  return route
}

async function visibleSelectorCount(page, selector) {
  return page.locator(selector).evaluateAll((elements) => elements.filter((element) => {
    if (!(element instanceof HTMLElement)) return false
    const style = getComputedStyle(element)
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false
    const rect = element.getBoundingClientRect()
    return rect.width > 0 && rect.height > 0
  }).length)
}

async function waitForVisibleSelector(page, selector, timeout = 5_000) {
  await page.locator(selector).first().waitFor({ state: 'visible', timeout }).catch(() => {})
  return visibleSelectorCount(page, selector)
}

async function waitForHiddenSelector(page, selector, timeout = 5_000) {
  try {
    await page.waitForFunction((candidate) => {
      const visible = (element) => {
        if (!(element instanceof HTMLElement)) return false
        const style = getComputedStyle(element)
        if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false
        const rect = element.getBoundingClientRect()
        return rect.width > 0 && rect.height > 0
      }
      return [...document.querySelectorAll(candidate)].every((element) => !visible(element))
    }, selector, { timeout })
    return true
  } catch {
    return false
  }
}

async function restoreInitialView(page) {
  await page.evaluate(() => {
    window.scrollTo(0, 0)
    if (document.scrollingElement) document.scrollingElement.scrollTop = 0
    const routeScroller = document.querySelector('.app-route-scroll')
    if (routeScroller instanceof HTMLElement) routeScroller.scrollTop = 0
  })
  await page.waitForTimeout(50)
}

async function launchBrowser(engine) {
  try {
    const browser = await engine.launch({ headless: true })
    return { browser, skipped: null }
  } catch (error) {
    return {
      browser: null,
      skipped: error instanceof Error ? error.message : String(error),
    }
  }
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
  engineName,
  reducedMotion = false,
  zoom = 1,
  environment,
  keyboard = false,
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
  const envName =
    environment || (viewport.width >= 1024 ? 'desktop-browser' : 'mobile-browser')
  const { context, page } = await newPage(browser, baseUrl, viewport, diagnostics, profile, {
    environment: envName,
    seedCurrentUserSummary: !descriptor.states?.[state]?.identityPageData,
    reducedMotion,
  })
  const expected = finalRouteExpectation(route, profile)
  const failures = []
  let probe = null
  let requestedStateProbe = null
  let requestedStateSelectorCount = null
  let postReleaseProbe = null
  let postReleaseSelectorCount = null
  let actual = null
  let keyboardReport = null
  const suffix = [
    engineName,
    viewport.id,
    state,
    reducedMotion ? 'reduced-motion' : null,
    zoom !== 1 ? `zoom${zoom}` : null,
    envName === 'pwa-simulation' ? 'pwa' : null,
    keyboard ? 'keyboard' : null,
  ]
    .filter(Boolean)
    .join('__')
  const screenshotName = `${route.name}__${suffix}.png`
  const screenshotPath = path.join(OUTPUT_DIR, 'screenshots', screenshotName)
  try {
    await page.goto(`${baseUrl}${visitPathFor(route)}`, {
      waitUntil: 'domcontentloaded',
      timeout: 30_000,
    })
    if (state === 'loading') {
      await page.waitForTimeout(180)
      requestedStateSelectorCount = await waitForVisibleSelector(page, descriptor.states[state].selector)
      requestedStateProbe = await collectUiProbe(page)
      failures.push(...assertRequestedState(
        state,
        descriptor.states[state],
        requestedStateSelectorCount,
      ))
      await restoreInitialView(page)
      await page.screenshot({ path: screenshotPath, fullPage: false })
      controller.releaseHeldRequest = true
    }
    await waitForApp(page)
    await waitForNetworkSettle(page).catch(() => {})
    if (state === 'loading') {
      if (descriptor.states[state]?.identityPageData) {
        await recoverIdentityPageDataAfterHold(page)
      }
      await waitForHiddenSelector(page, descriptor.states[state].selector)
    }
    if (zoom !== 1) {
      const session = await context.newCDPSession(page)
      await session.send('Emulation.setPageScaleFactor', { pageScaleFactor: zoom })
      await page.waitForTimeout(120)
    }
    actual = await readRuntimeRoute(page)
    probe = await collectUiProbe(page)
    failures.push(...assertCommonUi(probe, expected, route, route))
    if (state === 'loading') {
      postReleaseProbe = probe
      postReleaseSelectorCount = await visibleSelectorCount(page, descriptor.states[state].selector)
      if (postReleaseSelectorCount > 0) failures.push('loading state did not settle after request release')
    } else if (state === 'empty' || state === 'error') {
      requestedStateProbe = probe
      requestedStateSelectorCount = await visibleSelectorCount(page, descriptor.states[state].selector)
      failures.push(...assertRequestedState(
        state,
        descriptor.states[state],
        requestedStateSelectorCount,
      ))
    }
    if (keyboard) {
      let identityMenu = null
      if (route.name === 'home') {
        await restoreInitialView(page)
        const trigger = page.locator('.dashboard-account-menu__trigger')
        await trigger.focus()
        await page.keyboard.press('ArrowDown')
        const menu = page.locator('#dashboard-account-menu[role="menu"]')
        const firstItem = menu.locator('[role="menuitem"]').first()
        const menuOpened = await menu.isVisible().catch(() => false)
        const firstItemFocused = await firstItem.evaluate(
          (element) => document.activeElement === element,
        ).catch(() => false)
        await page.keyboard.press('Escape')
        const menuClosed = !(await menu.isVisible().catch(() => false))
        const focusRestored = await trigger.evaluate(
          (element) => document.activeElement === element,
        ).catch(() => false)
        identityMenu = { menuOpened, firstItemFocused, menuClosed, focusRestored }
        if (!menuOpened) failures.push('home identity menu did not open from ArrowDown')
        if (!firstItemFocused) failures.push('home identity menu did not focus its first item')
        if (!menuClosed) failures.push('home identity menu did not close with Escape')
        if (!focusRestored) failures.push('home identity menu did not restore trigger focus')
      }
      const names = []
      for (let index = 0; index < 10; index += 1) {
        await page.keyboard.press('Tab')
        names.push(
          await page.evaluate(() => {
            const active = document.activeElement
            if (!(active instanceof HTMLElement)) return null
            const style = getComputedStyle(active)
            return {
              tag: active.tagName.toLowerCase(),
              testId: active.getAttribute('data-test') || active.getAttribute('data-testid'),
              outline: style.outlineStyle,
              outlineWidth: style.outlineWidth,
            }
          }),
        )
      }
      await page.keyboard.press('Escape')
      keyboardReport = {
        tabStops: names.filter(Boolean).length,
        firstNamed: names.find((item) => item?.testId || item?.tag === 'button' || item?.tag === 'a') || null,
        identityMenu,
      }
      if (keyboardReport.tabStops < 1) failures.push('keyboard produced no tab stop')
    }
    if (state !== 'loading') {
      await restoreInitialView(page)
      await page.screenshot({ path: screenshotPath, fullPage: false })
    }
  } catch (error) {
    failures.push(error instanceof Error ? error.message : String(error))
  } finally {
    controller.releaseHeldRequest = true
    await context.close()
  }
  const counts = diagnosticCounts(diagnostics)
  const classified = classifyFinding(failures)
  return {
    id: `${route.name}/${suffix}`,
    route: route.name,
    path: route.path,
    viewport: viewport.id,
    state,
    engine: engineName,
    profile: profile.id,
    environment: envName,
    reducedMotion,
    zoom,
    keyboard,
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
    requestedStateEvidence: requestedStateProbe
      ? {
          selector: descriptor.states?.[state]?.selector || null,
          visibleSelectorCount: requestedStateSelectorCount,
          loadingVisible: requestedStateProbe.loadingVisible,
          emptyVisible: requestedStateProbe.emptyVisible,
          errorVisible: requestedStateProbe.errorVisible,
        }
      : null,
    postReleaseEvidence: postReleaseProbe
      ? {
          loadingVisible: postReleaseProbe.loadingVisible,
          emptyVisible: postReleaseProbe.emptyVisible,
          errorVisible: postReleaseProbe.errorVisible,
          visibleLoadingSelectorCount: postReleaseSelectorCount,
        }
      : null,
    keyboardReport,
    diagnostics: counts,
    unknownApiDelta: serverState.unknownApiRequests - snapshotBefore.unknown,
    mutatingDelta: serverState.mutatingApiRequests - snapshotBefore.mutating,
    screenshot: screenshotName,
    screenshotSha256: fs.existsSync(screenshotPath) ? sha256File(screenshotPath) : null,
    failures: classified.confirmed,
    falsePositives: classified.falsePositive,
    passed: classified.confirmed.length === 0,
    status: classified.confirmed.length === 0 ? 'pass' : 'fail',
  }
}

async function writeContactSheet(scenarios) {
  const passedShots = scenarios
    .filter((item) => item.status === 'pass' && item.screenshot)
    .slice(0, 72)
  const cards = passedShots
    .map((item) => {
      return `<figure><img src="screenshots/${item.screenshot}" alt=""><figcaption>${item.id}</figcaption></figure>`
    })
    .join('\n')
  const html = `<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8"><title>UIUX V3 contact sheet</title>
<style>body{font-family:Vazirmatn,Tahoma,sans-serif;margin:16px}section{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}img{width:100%;height:auto;border:1px solid #ddd}figcaption{font-size:11px;word-break:break-all}</style>
<h1>برگ تماس مسیرها — پیش‌نویس داخلی</h1>
<p>این فایل پذیرش محصول نیست.</p>
<section>${cards}</section>`
  const sheetPath = path.join(OUTPUT_DIR, 'CONTACT_SHEET.html')
  await writeFile(sheetPath, html)
  return sheetPath
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
  const engines = [
    { name: 'chromium', impl: chromium },
    { name: 'firefox', impl: firefox },
    { name: 'webkit', impl: webkit },
  ]
  const launched = {}
  const scenarios = []
  const na = []
  try {
    for (const engine of engines) {
      launched[engine.name] = await launchBrowser(engine.impl)
    }

    const chromiumBrowser = launched.chromium.browser
    if (!chromiumBrowser) {
      throw new Error(`chromium unavailable: ${launched.chromium.skipped}`)
    }

    if (!ONLY || ONLY === 'all' || ONLY === 'states') for (const route of matrix.routes) {
      const profile = allowedProfileForRoute(route, profiles)
      if (ONLY !== 'states') for (const viewport of CORE_VIEWPORTS) {
        scenarios.push(
          await runOne({
            browser: chromiumBrowser,
            baseUrl,
            controller,
            serverState: state,
            route,
            profile,
            viewport,
            state: 'normal',
            engineName: 'chromium',
          }),
        )
      }
      const descriptor = getRouteDescriptor(route.name)
      for (const extra of EXTRA_STATES) {
        const spec = descriptor.states?.[extra]
        if (!spec || spec.applicable === false) {
          na.push({
            route: route.name,
            state: extra,
            viewport: '390x844',
            engine: 'chromium',
            status: 'n-a',
            reason: spec?.reason || 'descriptor marks this state not applicable',
          })
          continue
        }
        scenarios.push(
          await runOne({
            browser: chromiumBrowser,
            baseUrl,
            controller,
            serverState: state,
            route,
            profile,
            viewport: CORE_VIEWPORTS[0],
            state: extra,
            engineName: 'chromium',
          }),
        )
      }
    }

    if (!ONLY || ONLY === 'all') for (const name of FAMILY_ROUTES) {
      const route = routeByName(matrix, name)
      const profile = allowedProfileForRoute(route, profiles)
      for (const viewport of EXTRA_VIEWPORTS) {
        scenarios.push(
          await runOne({
            browser: chromiumBrowser,
            baseUrl,
            controller,
            serverState: state,
            route,
            profile,
            viewport,
            state: 'normal',
            engineName: 'chromium',
          }),
        )
      }
    }

    if (!ONLY || ONLY === 'all') for (const engine of engines.filter((item) => item.name !== 'chromium')) {
      const browser = launched[engine.name].browser
      if (!browser) {
        for (const name of SENSITIVE_ROUTES) {
          na.push({
            route: name,
            engine: engine.name,
            status: 'n-a',
            reason: `browser launch failed: ${launched[engine.name].skipped}`,
          })
        }
        continue
      }
      for (const name of SENSITIVE_ROUTES) {
        const route = routeByName(matrix, name)
        const profile = allowedProfileForRoute(route, profiles)
        scenarios.push(
          await runOne({
            browser,
            baseUrl,
            controller,
            serverState: state,
            route,
            profile,
            viewport: CORE_VIEWPORTS[0],
            state: 'normal',
            engineName: engine.name,
          }),
        )
      }
    }

    if (!ONLY || ONLY === 'zoom' || ONLY === 'all') for (const name of ZOOM_ROUTES) {
      const route = routeByName(matrix, name)
      const profile = allowedProfileForRoute(route, profiles)
      scenarios.push(
        await runOne({
          browser: chromiumBrowser,
          baseUrl,
          controller,
          serverState: state,
          route,
          profile,
          viewport: CORE_VIEWPORTS[0],
          state: 'normal',
          engineName: 'chromium',
          zoom: 2,
        }),
      )
    }

    if (!ONLY || ONLY === 'all') for (const name of REDUCED_MOTION_ROUTES) {
      const route = routeByName(matrix, name)
      const profile = allowedProfileForRoute(route, profiles)
      scenarios.push(
        await runOne({
          browser: chromiumBrowser,
          baseUrl,
          controller,
          serverState: state,
          route,
          profile,
          viewport: CORE_VIEWPORTS[0],
          state: 'normal',
          engineName: 'chromium',
          reducedMotion: true,
        }),
      )
    }

    if (!ONLY || ONLY === 'all') for (const name of KEYBOARD_ROUTES) {
      const route = routeByName(matrix, name)
      const profile = allowedProfileForRoute(route, profiles)
      scenarios.push(
        await runOne({
          browser: chromiumBrowser,
          baseUrl,
          controller,
          serverState: state,
          route,
          profile,
          viewport: CORE_VIEWPORTS[0],
          state: 'normal',
          engineName: 'chromium',
          keyboard: true,
        }),
      )
    }

    if (!ONLY || ONLY === 'all') for (const name of PWA_ROUTES) {
      const route = routeByName(matrix, name)
      const profile = allowedProfileForRoute(route, profiles)
      scenarios.push(
        await runOne({
          browser: chromiumBrowser,
          baseUrl,
          controller,
          serverState: state,
          route,
          profile,
          viewport: CORE_VIEWPORTS[0],
          state: 'normal',
          engineName: 'chromium',
          environment: 'pwa-simulation',
        }),
      )
    }
  } finally {
    for (const engine of Object.values(launched)) {
      if (engine.browser) await engine.browser.close()
    }
    await closeServer(server)
  }

  const sheetPath = await writeContactSheet(scenarios)
  const summary = {
    track: 'webapp-uiux-unification-v3',
    phase: 11,
    independent_of_stage8: true,
    not_acceptance_authority: true,
    runId: RUN_ID,
    git: gitSnapshot(REPO),
    dist: distFingerprint(DIST),
    counts: {
      routes: matrix.routes.length,
      scenarios: scenarios.length,
      passed: scenarios.filter((item) => item.status === 'pass').length,
      failed: scenarios.filter((item) => item.status === 'fail').length,
      na: na.length,
      falsePositiveFindings: scenarios.reduce((sum, item) => sum + item.falsePositives.length, 0),
      unknownApiRequests: state.unknownApiRequests,
      mutatingApiRequests: state.mutatingApiRequests,
      pageErrors: scenarios.reduce((sum, item) => sum + (item.diagnostics?.pageErrors || 0), 0),
      externalRequests: scenarios.reduce((sum, item) => sum + (item.diagnostics?.externalRequests || 0), 0),
    },
    failedIds: scenarios.filter((item) => item.status === 'fail').map((item) => item.id),
    na,
    contactSheet: path.basename(sheetPath),
    scenarios,
  }
  const summaryPath = path.join(OUTPUT_DIR, 'RUNTIME_AUDIT.json')
  await writeFile(summaryPath, `${JSON.stringify(summary, null, 2)}\n`)
  await writeFile(
    path.join(OUTPUT_DIR, 'RUNTIME_AUDIT.sha256'),
    `${sha256File(summaryPath)}  RUNTIME_AUDIT.json\n`,
  )
  process.stdout.write(`${JSON.stringify({ event: 'uiux-v3-phase11-done', output: OUTPUT_DIR, ...summary.counts })}\n`)
  if (summary.counts.failed) process.exitCode = 2
}

await main()
