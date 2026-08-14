#!/usr/bin/env node
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'
import {
  VIEWPORTS,
  closeServer,
  collectMarketProbe,
  createFixtureServer,
  diagnosticCounts,
  distFingerprint,
  gitSnapshot,
  listen,
  newDiagnostics,
  newPage,
  ownerUser,
  sha256,
  waitForApp,
} from './lib/market-a-plus-c-browser.mjs'

const HARNESS_PATH = fileURLToPath(import.meta.url)
const FRONTEND = path.resolve(path.dirname(HARNESS_PATH), '..')
const REPO = path.resolve(FRONTEND, '..')
const DIST = process.env.MARKET_AC_DIST || '/tmp/market-a-plus-c-dist'
const OUTPUT_ROOT = process.env.MARKET_AC_OUT || '/tmp/market-a-plus-c-runs'
const PHASE = process.env.MARKET_AC_PHASE || 'baseline'
const RUN_ID = `market-ac-${PHASE}-${new Date().toISOString().replace(/[-:.]/gu, '')}`
const OUTPUT_DIR = path.join(OUTPUT_ROOT, RUN_ID)
const require = createRequire(import.meta.url)

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

function progress(stage, details = {}) {
  process.stdout.write(`${JSON.stringify({ event: 'market-a-plus-c-browser', runId: RUN_ID, stage, ...details })}\n`)
}

async function runScenario({
  browser,
  baseUrl,
  controller,
  serverState,
  viewport,
  state = 'normal',
  interaction = 'none',
  zoom200 = false,
  reducedMotion = false,
}) {
  const diagnostics = newDiagnostics()
  controller.mode = state === 'loading' ? 'loading' : state
  controller.delayMs = state === 'loading' ? 800 : state === 'slow' ? 900 : 0
  controller.marketOpen = state !== 'closed'
  controller.noticeVisible = state === 'notice' || state === 'closed'
  controller.adminMessage = state === 'admin'
  controller.notificationEnabled = state !== 'notify-off'
  const snapshotBefore = {
    unknown: serverState.unknownApiRequests,
    mutating: serverState.mutatingApiRequests,
  }
  const { context, page } = await newPage(browser, baseUrl, viewport, diagnostics, ownerUser(), {
    reducedMotion: reducedMotion || interaction === 'reduced-motion',
    deviceScaleFactor: zoom200 || interaction === 'zoom-200' ? 2 : 1,
  })
  const failures = []
  let probe = null
  try {
    await page.goto(`${baseUrl}/market`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
    if (state === 'loading' || state === 'slow') {
      await page.waitForTimeout(180)
    }
    await waitForApp(page)
    if (state === 'stale') {
      await page.waitForTimeout(200)
    }
    if (interaction === 'first-tap') {
      const button = page.locator('[data-test="trade-action-button"]').first()
      if ((await button.count()) > 0) {
        await button.click()
        await page.waitForTimeout(80)
      }
    }
    if (interaction === 'escape-pending') {
      const button = page.locator('[data-test="trade-action-button"]').first()
      if ((await button.count()) > 0) {
        await button.click()
        await page.keyboard.press('Escape')
        await page.waitForTimeout(80)
      }
    }
    if (interaction === 'recent-offers') {
      const toggle = page.locator('[data-test="recent-offers-toggle"]')
      if ((await toggle.count()) > 0) {
        await toggle.click()
        await page.waitForTimeout(120)
      }
    }
    if (interaction === 'preview') {
      const input = page.locator('[data-test="market-text-offer-input"]')
      if ((await input.count()) > 0) {
        await input.fill('خرید ۲ امامی نقدی ۷۸۴۵۰۰۰۰')
        await page.locator('[data-test="market-send-button"]').click()
        await page.waitForTimeout(200)
      }
    }
    if (interaction === 'keyboard') {
      await page.keyboard.press('Tab')
      await page.keyboard.press('Tab')
    }
    probe = await collectMarketProbe(page)
    if (probe.overtimeInMarket) failures.push('overtime-rendered-in-market')
    if (probe.persianMarker) failures.push('stage8b-marker-on-market')
    if (probe.docOverflow) failures.push('document-overflow')
    if (probe.nestedInteractiveCount > 0) failures.push('nested-interactive')
    if (probe.dir && probe.dir !== 'rtl') failures.push('missing-rtl')
    if (state === 'empty' && !probe.emptyVisible && probe.offerCount > 0) {
      failures.push('empty-state-missing')
    }
    if (state === 'normal' && probe.offerCount < 1 && !probe.loadingVisible && !probe.emptyVisible) {
      failures.push('offers-missing')
    }
    if (interaction === 'first-tap' && probe.pendingCount < 1) {
      failures.push('pending-confirm-missing')
    }
    if (interaction === 'escape-pending' && probe.pendingCount > 0) {
      failures.push('escape-did-not-clear-pending')
    }
    if (interaction === 'preview' && !probe.previewVisible) {
      failures.push('preview-missing')
    }
    if (PHASE === 'candidate') {
      if (state === 'normal' && !probe.marketTitleVisible) {
        failures.push('market-title-missing')
      }
      if (interaction === 'first-tap' && !probe.decisionPanelVisible) {
        failures.push('decision-panel-missing')
      }
      if (interaction === 'preview' && !probe.previewRecapVisible) {
        failures.push('preview-recap-missing')
      }
      if (
        ['normal', 'dense', 'admin', 'notice', 'notify-off'].includes(state)
        && interaction === 'none'
        && probe.tradeButtonCount > 0
        && probe.smallTradeTargetCount > 0
      ) {
        failures.push('trade-target-below-44')
      }
    }
  } catch (error) {
    failures.push(error instanceof Error ? error.message : String(error))
  } finally {
    await context.close()
  }
  const counts = diagnosticCounts(diagnostics)
  if (counts.externalRequests > 0) failures.push('external-request')
  if (counts.pageErrors > 0) failures.push('page-error')
  if (serverState.unknownApiRequests > snapshotBefore.unknown) failures.push('unknown-api')
  if (serverState.mutatingApiRequests > snapshotBefore.mutating) failures.push('blocked-product-mutation')
  return {
    id: `${state}:${viewport.id}:${interaction}`,
    state,
    viewport: viewport.id,
    interaction,
    passed: failures.length === 0,
    failures,
    probe,
    diagnostics: {
      ...counts,
      externalOrigins: diagnostics.externalRequests.slice(0, 8),
    },
  }
}

async function main() {
  assert(PHASE === 'baseline' || PHASE === 'candidate', 'MARKET_AC_PHASE must be baseline or candidate')
  const snapshot = gitSnapshot(REPO)
  const dist = distFingerprint(DIST)
  await mkdir(OUTPUT_DIR, { recursive: true })
  const { chromium } = require('playwright')
  const controller = {
    mode: 'normal',
    delayMs: 0,
    marketOpen: true,
    noticeVisible: false,
    adminMessage: false,
    notificationEnabled: true,
    userOverrides: {},
  }
  const serverState = {
    apiRequests: 0,
    knownApiRequests: 0,
    unknownApiRequests: 0,
    mutatingApiRequests: 0,
    injectedErrorResponses: 0,
    unknownPaths: [],
  }
  const server = await createFixtureServer(DIST, controller, serverState)
  const baseUrl = await listen(server)
  const browser = await chromium.launch({ headless: true })
  const scenarios = []
  const screenshotDigests = []
  try {
    const states = [
      'normal',
      'empty',
      'dense',
      'error',
      'offline',
      'closed',
      'admin',
      'notify-off',
      'notice',
    ]
    const viewports = process.env.MARKET_AC_QUICK === '1'
      ? VIEWPORTS.filter((item) => item.id === '390x844')
      : VIEWPORTS
    for (const viewport of viewports) {
      for (const state of states) {
        progress('scenario', { viewport: viewport.id, state })
        scenarios.push(await runScenario({
          browser,
          baseUrl,
          controller,
          serverState,
          viewport,
          state,
        }))
      }
    }
    const focusViewport = VIEWPORTS.find((item) => item.id === '390x844')
    const interactionCases = [
      { interaction: 'first-tap' },
      { interaction: 'escape-pending' },
      { interaction: 'recent-offers' },
      { interaction: 'preview' },
      { interaction: 'keyboard' },
      { interaction: 'zoom-200', zoom200: true },
      { interaction: 'reduced-motion', reducedMotion: true },
    ]
    for (const item of interactionCases) {
      progress('interaction', { interaction: item.interaction })
      scenarios.push(await runScenario({
        browser,
        baseUrl,
        controller,
        serverState,
        viewport: focusViewport,
        state: 'normal',
        ...item,
      }))
    }
    const homeDiagnostics = newDiagnostics()
    const { context, page } = await newPage(browser, baseUrl, focusViewport, homeDiagnostics, ownerUser())
    await page.goto(`${baseUrl}/`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
    await waitForApp(page)
    const homeHasMarketList = await page.locator('[data-test="offer-card"]').count()
    const homeHasPersian = await page.locator('.app-route--persian-typography').count()
    await context.close()
    scenarios.push({
      id: 'home-shared-consumer',
      state: 'home',
      viewport: focusViewport.id,
      interaction: 'none',
      passed: homeHasMarketList === 0,
      failures: homeHasMarketList > 0 ? ['home-rendered-market-offer-cards'] : [],
      probe: { offerCount: homeHasMarketList, persianMarker: homeHasPersian > 0 },
      diagnostics: diagnosticCounts(homeDiagnostics),
    })
  } finally {
    await browser.close()
    await closeServer(server)
  }

  const passed = scenarios.filter((item) => item.passed).length
  const failed = scenarios.filter((item) => !item.passed).length
  const report = {
    schemaVersion: 1,
    phase: PHASE,
    runId: RUN_ID,
    source: snapshot,
    dist,
    counts: {
      scenarios: scenarios.length,
      passed,
      failed,
      viewports: VIEWPORTS.length,
      apiRequests: serverState.apiRequests,
      knownApiRequests: serverState.knownApiRequests,
      unknownApiRequests: serverState.unknownApiRequests,
      mutatingApiRequests: serverState.mutatingApiRequests,
      unknownPaths: serverState.unknownPaths.slice(0, 20),
      screenshots: screenshotDigests.length,
    },
    scenarios: scenarios.map((item) => ({
      id: item.id,
      passed: item.passed,
      failures: item.failures,
      viewport: item.viewport,
      state: item.state,
      interaction: item.interaction,
      offerCount: item.probe?.offerCount ?? null,
      pendingCount: item.probe?.pendingCount ?? null,
      decisionPanelVisible: item.probe?.decisionPanelVisible ?? null,
      previewVisible: item.probe?.previewVisible ?? null,
      previewRecapVisible: item.probe?.previewRecapVisible ?? null,
      marketTitleVisible: item.probe?.marketTitleVisible ?? null,
      docOverflow: item.probe?.docOverflow ?? null,
      smallTradeTargetCount: item.probe?.smallTradeTargetCount ?? null,
      overtimeInMarket: item.probe?.overtimeInMarket ?? null,
      persianMarker: item.probe?.persianMarker ?? null,
      externalOrigins: item.diagnostics?.externalOrigins || [],
    })),
  }
  const reportPath = path.join(OUTPUT_DIR, 'report.json')
  await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`)
  progress('done', {
    passed,
    failed,
    digest: sha256(JSON.stringify(report.counts)),
  })
  if (failed > 0 && PHASE === 'candidate') {
    process.exitCode = 1
  }
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`)
  process.exitCode = 1
})
