#!/usr/bin/env node
import { mkdir, writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
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
const SCREEN_DIR = path.join(OUTPUT_DIR, 'screenshots')
const require = createRequire(import.meta.url)

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

function progress(stage, details = {}) {
  process.stdout.write(`${JSON.stringify({ event: 'market-a-plus-c-browser', runId: RUN_ID, stage, ...details })}\n`)
}

function aligned(a, b, tolerance = 2) {
  if (!a || !b) return false
  return Math.abs(a.left - b.left) <= tolerance && Math.abs(a.width - b.width) <= tolerance
}

function near(value, expected, tolerance = 1) {
  return Number.isFinite(value) && Math.abs(value - expected) <= tolerance
}

async function digestFile(filePath) {
  const { readFile } = await import('node:fs/promises')
  return createHash('sha256').update(await readFile(filePath)).digest('hex')
}

async function runScenario({
  browser,
  baseUrl,
  controller,
  serverState,
  viewport,
  state = 'normal',
  interaction = 'none',
  reducedMotion = false,
  deviceScaleFactor = 1,
  cssZoom = null,
  screenshotName = null,
}) {
  const diagnostics = newDiagnostics()
  controller.mode = state === 'loading' ? 'loading' : state
  controller.delayMs = state === 'loading' ? 800 : 0
  controller.marketOpen = state !== 'closed'
  controller.noticeVisible = state === 'notice' || state === 'closed'
  controller.adminMessage = state === 'admin'
  controller.notificationEnabled = state !== 'notify-off'
  const snapshotBefore = {
    unknown: serverState.unknownApiRequests,
    mutating: serverState.mutatingApiRequests,
    known: serverState.knownApiRequests,
  }
  const useReducedMotion = reducedMotion || interaction === 'reduced-motion'
  const { context, page } = await newPage(browser, baseUrl, viewport, diagnostics, ownerUser(), {
    reducedMotion: useReducedMotion,
    deviceScaleFactor,
  })
  if (useReducedMotion) {
    await page.emulateMedia({ reducedMotion: 'reduce' })
  }
  const failures = []
  let probe = null
  let screenshot = null
  try {
    await page.goto(`${baseUrl}/market`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
    if (state === 'loading') {
      await page.waitForTimeout(180)
    }
    await waitForApp(page)
    if (cssZoom) {
      await page.evaluate((zoom) => {
        document.documentElement.style.zoom = String(zoom)
      }, cssZoom)
      await page.waitForTimeout(80)
    }
    if (interaction === 'first-tap' || interaction === 'decision') {
      const button = page.locator('[data-test="trade-action-button"]').first()
      if ((await button.count()) > 0) {
        await button.click()
        await page.waitForTimeout(80)
      }
    }
    if (interaction === 'second-tap') {
      const button = page.locator('[data-test="trade-action-button"]').first()
      if ((await button.count()) > 0) {
        await button.click()
        await page.waitForTimeout(60)
        await button.click()
        await page.waitForTimeout(120)
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
    if (interaction === 'cancel-pending') {
      const button = page.locator('[data-test="trade-action-button"]').first()
      if ((await button.count()) > 0) {
        await button.click()
        const cancel = page.locator('[data-test="offer-decision-cancel"]')
        if ((await cancel.count()) > 0) await cancel.click()
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
      await page.locator('[data-test="trade-action-button"]').first().focus()
      await page.waitForTimeout(40)
    }
    if (interaction === 'scroll-end') {
      await page.evaluate(() => {
        const scroller = document.querySelector('.market-content')
        if (scroller instanceof HTMLElement) scroller.scrollTop = scroller.scrollHeight
      })
      await page.waitForTimeout(80)
    }
    if (interaction === 'monotonic-deadline') {
      const first = await page.evaluate(() => {
        const card = document.querySelector('[data-test="offer-card"]')
        return card instanceof HTMLElement ? Number(getComputedStyle(card).getPropertyValue('--t-pct')) : null
      })
      await page.waitForTimeout(1100)
      const second = await page.evaluate(() => {
        const card = document.querySelector('[data-test="offer-card"]')
        return card instanceof HTMLElement ? Number(getComputedStyle(card).getPropertyValue('--t-pct')) : null
      })
      if (!(Number.isFinite(first) && Number.isFinite(second) && second <= first + 0.01)) {
        failures.push(`deadline-not-monotonic:${first}->${second}`)
      }
    }
    probe = await collectMarketProbe(page)
    if (probe.overtimeInMarket) failures.push('overtime-preference-rendered-in-market')
    if (probe.persianMarker) failures.push('stage8b-marker-on-market')
    if (probe.docOverflow) failures.push('document-overflow')
    if (probe.nestedInteractiveCount > 0) failures.push('nested-interactive')
    if (probe.unnamedCount > 0) failures.push('unnamed-control')
    if (probe.dir && probe.dir !== 'rtl') failures.push('missing-rtl')
    if (state === 'empty' && !probe.emptyVisible && probe.offerCount > 0) {
      failures.push('empty-state-missing')
    }
    if (state === 'loading' && !probe.loadingVisible && probe.offerCount > 0) {
      failures.push('loading-state-missing')
    }
    if (state === 'normal' && probe.offerCount < 1 && !probe.loadingVisible && !probe.emptyVisible) {
      failures.push('offers-missing')
    }
    if (interaction === 'first-tap' && probe.pendingCount < 1) {
      failures.push('pending-confirm-missing')
    }
    if ((interaction === 'escape-pending' || interaction === 'cancel-pending') && probe.pendingCount > 0) {
      failures.push('pending-did-not-clear')
    }
    if (interaction === 'preview' && !probe.previewVisible) {
      failures.push('preview-missing')
    }
    if (interaction === 'scroll-end' && probe.lastActionHit && probe.lastActionHit.hit === false) {
      failures.push('final-action-not-hittable')
    }
    if (PHASE === 'candidate') {
      if (state === 'normal' && !probe.marketTitleVisible) {
        failures.push('market-title-missing')
      }
      if ((interaction === 'first-tap' || interaction === 'decision') && !probe.decisionPanelVisible) {
        failures.push('decision-panel-missing')
      }
      if (interaction === 'preview' && !probe.previewRecapVisible) {
        failures.push('preview-recap-missing')
      }
      if (interaction === 'preview' && probe.previewText && !probe.previewText.includes('نوع لفظ شما')) {
        failures.push('preview-direction-inverted')
      }
      if ((interaction === 'first-tap' || interaction === 'decision') && probe.decisionText) {
        const firstOfferIsBuy = state === 'normal' || state === 'normal-buy'
        if (firstOfferIsBuy && state !== 'normal-sell') {
          if (!probe.decisionText.includes('نوع لفظ: خرید') || !probe.decisionText.includes('اقدام شما: فروش')) {
            failures.push('offer-side-user-action-mismatch')
          }
        }
        if (state === 'normal-sell') {
          if (!probe.decisionText.includes('نوع لفظ: فروش') || !probe.decisionText.includes('اقدام شما: خرید')) {
            failures.push('offer-side-user-action-mismatch')
          }
        }
        if (probe.decisionText.includes('مقدار معامله را انتخاب کنید')) {
          failures.push('stale-decision-prompt')
        }
      }
      if (
        ['normal', 'dense', 'normal-buy', 'normal-sell', 'overtime-buy', 'overtime-sell', 'own-offer'].includes(state)
        && interaction === 'none'
        && probe.tradeButtonCount > 0
        && probe.smallTradeTargetCount > 0
      ) {
        failures.push('trade-target-below-44')
      }
      if (viewport.id === '1440x900' && ['normal', 'normal-buy', 'normal-sell', 'overtime-sell'].includes(state)) {
        const { title, header, content, composer, firstCard } = probe.geometry || {}
        if (!near(title?.width, 960) || !near(header?.width, 960) || !near(content?.width, 960) || !near(composer?.width, 960)) {
          failures.push(`desktop-rail-not-960:${JSON.stringify({
            title: title?.width,
            header: header?.width,
            content: content?.width,
            composer: composer?.width,
          })}`)
        }
        if (!aligned(title, header) || !aligned(title, content) || !aligned(title, composer)) {
          failures.push('desktop-rail-not-aligned')
        }
        if (!(firstCard?.width > 700)) {
          failures.push(`desktop-card-still-narrow:${firstCard?.width}`)
        }
      }
      if (viewport.id === '1024x768' && probe.docOverflow) {
        failures.push('desktop-1024-overflow')
      }
      if (['overtime-buy', 'overtime-sell', 'critical-overtime'].includes(state)) {
        if (!probe.overtimeStickerVisible) failures.push('overtime-sticker-missing')
        if (probe.deadline?.phase !== 'overtime') failures.push(`overtime-phase-mismatch:${probe.deadline?.phase}`)
        if (!String(probe.deadline?.label || '').includes('باقی‌مانده')) failures.push('overtime-countdown-label-missing')
        if (state !== 'critical-overtime' && !(probe.deadline?.pct > 50)) {
          failures.push(`overtime-progress-not-reset:${probe.deadline?.pct}`)
        }
        if (state === 'critical-overtime' && probe.deadline?.critical !== 'true') {
          failures.push('critical-overtime-not-marked')
        }
      }
      if (state === 'critical-normal') {
        if (probe.deadline?.phase !== 'critical' && probe.deadline?.critical !== 'true') {
          failures.push('critical-normal-not-marked')
        }
        if (!String(probe.deadline?.label || '').includes('مهلت اصلی')) {
          failures.push('critical-normal-label-missing')
        }
      }
      if (
        ['normal', 'dense', 'normal-buy', 'normal-sell', 'critical-normal', 'overtime-buy', 'overtime-sell', 'critical-overtime', 'own-offer'].includes(state)
        && probe.deadline?.present
      ) {
        if (!probe.deadline.perimeterMatchesCard) failures.push('deadline-perimeter-does-not-follow-card')
        if (!probe.deadline.strokeDasharray || probe.deadline.strokeDasharray === 'none') {
          failures.push('deadline-perimeter-progress-missing')
        }
      }
      if (state === 'final-tail') {
        if (!probe.finalTailBadgeVisible) failures.push('final-tail-badge-missing')
        if (probe.deadline?.present) failures.push('final-tail-has-timer')
        if (probe.tradeButtonCount > 0) failures.push('final-tail-has-action')
      }
      if (['expired', 'traded', 'partially-traded', 'traded-overtime', 'terminal-mix'].includes(state)) {
        if (!(probe.offerCount > 0)) failures.push('terminal-offer-missing')
        if (probe.deadline?.present) failures.push('terminal-has-timer')
        if (probe.tradeButtonCount > 0) failures.push('terminal-has-action')
      }
      if (state === 'traded-overtime' && !probe.overtimeTradeBadgeVisible) {
        failures.push('overtime-trade-badge-missing')
      }
      if ((reducedMotion || interaction === 'reduced-motion') && Array.isArray(probe.animationNames)) {
        if (probe.animationNames.some((name) => name && name !== 'none')) {
          failures.push(`reduced-motion-animation:${probe.animationNames.join(',')}`)
        }
      }
      if (interaction === 'keyboard' && probe.focusContrast?.ratio != null && probe.focusContrast.ratio < 3) {
        failures.push(`focus-contrast-below-3:${probe.focusContrast.ratio}`)
      }
    }
    if (screenshotName) {
      await mkdir(SCREEN_DIR, { recursive: true })
      const filePath = path.join(SCREEN_DIR, `${screenshotName}.png`)
      await page.screenshot({ path: filePath, fullPage: false })
      screenshot = {
        name: screenshotName,
        file: filePath,
        sha256: await digestFile(filePath),
        viewport: viewport.id,
        state,
        interaction,
        cssZoom,
        deviceScaleFactor,
        reducedMotion: reducedMotion || interaction === 'reduced-motion',
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
    id: `${state}:${viewport.id}:${interaction}${cssZoom ? `:css-zoom-${cssZoom}` : ''}${deviceScaleFactor !== 1 ? `:dpr-${deviceScaleFactor}` : ''}`,
    state,
    viewport: viewport.id,
    interaction,
    cssZoom,
    deviceScaleFactor,
    passed: failures.length === 0,
    failures,
    probe,
    screenshot,
    diagnostics: {
      ...counts,
      externalOrigins: diagnostics.externalRequests.slice(0, 8),
      knownDelta: serverState.knownApiRequests - snapshotBefore.known,
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
  const screenshots = []
  const byId = (id) => VIEWPORTS.find((item) => item.id === id)
  try {
    const coreStates = ['normal', 'empty', 'dense', 'error', 'offline', 'closed', 'loading']
    const viewports = process.env.MARKET_AC_QUICK === '1'
      ? VIEWPORTS.filter((item) => item.id === '390x844')
      : VIEWPORTS
    for (const viewport of viewports) {
      for (const state of coreStates) {
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

    const lifecycleViewports = process.env.MARKET_AC_QUICK === '1'
      ? [byId('390x844')]
      : [byId('390x844'), byId('1440x900')]
    const lifecycleStates = [
      'normal-buy',
      'normal-sell',
      'critical-normal',
      'overtime-buy',
      'overtime-sell',
      'critical-overtime',
      'final-tail',
      'expired',
      'traded',
      'partially-traded',
      'traded-overtime',
      'own-offer',
      'terminal-mix',
      'notice',
      'admin',
      'notify-off',
    ]
    for (const viewport of lifecycleViewports) {
      for (const state of lifecycleStates) {
        progress('lifecycle', { viewport: viewport.id, state })
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

    const focus390 = byId('390x844')
    const focus1440 = byId('1440x900')
    const interactionCases = [
      { viewport: focus390, interaction: 'first-tap', screenshotName: '08-mobile-decision' },
      { viewport: focus1440, interaction: 'first-tap', screenshotName: '09-desktop-decision' },
      { viewport: focus390, state: 'normal-sell', interaction: 'first-tap' },
      { viewport: focus390, interaction: 'second-tap' },
      { viewport: focus390, interaction: 'escape-pending' },
      { viewport: focus390, interaction: 'cancel-pending' },
      { viewport: focus390, interaction: 'recent-offers' },
      { viewport: focus390, interaction: 'preview', screenshotName: '10-preview-modal' },
      { viewport: focus390, interaction: 'keyboard' },
      { viewport: focus390, interaction: 'scroll-end' },
      { viewport: focus390, interaction: 'monotonic-deadline' },
      { viewport: focus390, state: 'overtime-sell', interaction: 'reduced-motion', reducedMotion: true, screenshotName: '11-reduced-motion-overtime' },
      { viewport: focus390, interaction: 'none', cssZoom: 2, screenshotName: '12-page-css-zoom' },
      { viewport: { id: '320x740', width: 320, height: 740 }, interaction: 'none', screenshotName: '12b-reflow-320' },
      { viewport: focus390, interaction: 'dpr-2-resolution', deviceScaleFactor: 2 },
    ]
    for (const item of interactionCases) {
      progress('interaction', { interaction: item.interaction, viewport: item.viewport.id })
      scenarios.push(await runScenario({
        browser,
        baseUrl,
        controller,
        serverState,
        state: item.state || 'normal',
        ...item,
      }))
    }

    const screenshotCases = [
      { name: '01-mobile-normal', viewport: focus390, state: 'normal' },
      { name: '02-desktop-normal', viewport: focus1440, state: 'normal' },
      { name: '03-mobile-critical-main', viewport: focus390, state: 'critical-normal' },
      { name: '04-mobile-overtime', viewport: focus390, state: 'overtime-sell' },
      { name: '05-desktop-overtime', viewport: focus1440, state: 'overtime-sell' },
      { name: '06-mobile-expired-traded', viewport: focus390, state: 'terminal-mix' },
      { name: '07-desktop-expired-traded', viewport: focus1440, state: 'terminal-mix' },
    ]
    for (const item of screenshotCases) {
      progress('screenshot', { name: item.name })
      scenarios.push(await runScenario({
        browser,
        baseUrl,
        controller,
        serverState,
        viewport: item.viewport,
        state: item.state,
        screenshotName: item.name,
      }))
    }

    const homeDiagnostics = newDiagnostics()
    const { context, page } = await newPage(browser, baseUrl, focus390, homeDiagnostics, ownerUser())
    await page.goto(`${baseUrl}/`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
    await waitForApp(page)
    const homeHasMarketList = await page.locator('[data-test="offer-card"]').count()
    const homeHasPersian = await page.locator('.app-route--persian-typography').count()
    await context.close()
    scenarios.push({
      id: 'home-shared-consumer',
      state: 'home',
      viewport: focus390.id,
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

  for (const item of scenarios) {
    if (item.screenshot) screenshots.push(item.screenshot)
  }
  const passed = scenarios.filter((item) => item.passed).length
  const failed = scenarios.filter((item) => item.passed === false).length
  const report = {
    schemaVersion: 2,
    phase: PHASE,
    runId: RUN_ID,
    source: snapshot,
    dist,
    zoomMethodology: {
      pageZoom: 'Chromium CSS zoom=2 on documentElement; layout-equivalent page zoom, not deviceScaleFactor',
      reflow: 'viewport 320x740 effective CSS width',
      dpr2: 'Playwright deviceScaleFactor=2 named dpr-2-resolution only; not called 200% zoom',
    },
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
      screenshots: screenshots.length,
    },
    screenshots,
    scenarios: scenarios.map((item) => ({
      id: item.id,
      passed: item.passed,
      failures: item.failures,
      viewport: item.viewport,
      state: item.state,
      interaction: item.interaction,
      cssZoom: item.cssZoom || null,
      deviceScaleFactor: item.deviceScaleFactor || 1,
      offerCount: item.probe?.offerCount ?? null,
      pendingCount: item.probe?.pendingCount ?? null,
      decisionPanelVisible: item.probe?.decisionPanelVisible ?? null,
      decisionText: item.probe?.decisionText || null,
      previewVisible: item.probe?.previewVisible ?? null,
      previewRecapVisible: item.probe?.previewRecapVisible ?? null,
      previewText: item.probe?.previewText || null,
      marketTitleVisible: item.probe?.marketTitleVisible ?? null,
      docOverflow: item.probe?.docOverflow ?? null,
      smallTradeTargetCount: item.probe?.smallTradeTargetCount ?? null,
      smallTargetCount: item.probe?.smallTargetCount ?? null,
      geometry: item.probe?.geometry || null,
      deadline: item.probe?.deadline || null,
      focusContrast: item.probe?.focusContrast || null,
      lastActionHit: item.probe?.lastActionHit || null,
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
    screenshots: screenshots.length,
    outputDir: OUTPUT_DIR,
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
