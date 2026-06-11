#!/usr/bin/env node
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'
import { performance } from 'node:perf_hooks'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const repoRoot = path.resolve(__dirname, '..')
const frontendRequire = createRequire(path.join(repoRoot, 'frontend', 'package.json'))
const { chromium } = frontendRequire('@playwright/test')

function parseArgs(argv) {
  const options = {
    baseUrl: '',
    context: '',
    output: '',
    headless: true,
    routeTimeoutMs: 60000,
  }
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index]
    if (token === '--base-url' && argv[index + 1]) {
      options.baseUrl = argv[++index]
    } else if (token === '--context' && argv[index + 1]) {
      options.context = argv[++index]
    } else if (token === '--output' && argv[index + 1]) {
      options.output = argv[++index]
    } else if (token === '--headed') {
      options.headless = false
    } else if (token === '--route-timeout-ms' && argv[index + 1]) {
      options.routeTimeoutMs = Number(argv[++index])
    }
  }
  if (!options.baseUrl) throw new Error('--base-url is required')
  if (!options.context) throw new Error('--context is required')
  if (!options.output) throw new Error('--output is required')
  return options
}

function readJson(filePath) {
  return JSON.parse(readFileSync(filePath, 'utf8'))
}

function cleanBaseUrl(raw) {
  return raw.replace(/\/+$/, '')
}

function percentile(values, pct) {
  if (!values.length) return 0
  const ordered = [...values].sort((a, b) => a - b)
  if (ordered.length === 1) return Number(ordered[0].toFixed(3))
  const rank = (ordered.length - 1) * pct
  const lower = Math.floor(rank)
  const upper = Math.min(lower + 1, ordered.length - 1)
  const weight = rank - lower
  return Number((ordered[lower] * (1 - weight) + ordered[upper] * weight).toFixed(3))
}

function summarizeDurations(values) {
  if (!values.length) {
    return { count: 0, p50_ms: 0, p95_ms: 0, p99_ms: 0, max_ms: 0 }
  }
  return {
    count: values.length,
    p50_ms: percentile(values, 0.5),
    p95_ms: percentile(values, 0.95),
    p99_ms: percentile(values, 0.99),
    max_ms: Number(Math.max(...values).toFixed(3)),
  }
}

function createNetworkTracker(page) {
  const requests = []
  const failedRequests = []
  const ignoredFailedRequests = []
  const failedResponses = []
  const consoleErrors = []

  page.on('request', (request) => {
    requests.push({
      method: request.method(),
      resourceType: request.resourceType(),
      url: request.url(),
    })
  })
  page.on('requestfailed', (request) => {
    const failedRequest = {
      resourceType: request.resourceType(),
      url: request.url(),
      failure: request.failure()?.errorText ?? 'unknown',
    }
    if (
      failedRequest.failure === 'net::ERR_ABORTED'
      && failedRequest.resourceType === 'fetch'
      && failedRequest.url.includes('/api/notifications/mark-all-read')
    ) {
      ignoredFailedRequests.push(failedRequest)
      return
    }
    failedRequests.push(failedRequest)
  })
  page.on('response', (response) => {
    const status = response.status()
    if (status >= 400 && !response.url().endsWith('/favicon.ico')) {
      failedResponses.push({
        status,
        url: response.url(),
      })
    }
  })
  page.on('console', (message) => {
    if (message.type() === 'error') {
      consoleErrors.push(String(message.text()).slice(0, 240))
    }
  })

  return {
    requests,
    failedRequests,
    failedResponses,
    consoleErrors,
    summary() {
      return {
        request_count: requests.length,
        failed_request_count: failedRequests.length,
        ignored_failed_request_count: ignoredFailedRequests.length,
        failed_response_count: failedResponses.length,
        console_error_count: consoleErrors.length,
        failed_requests: failedRequests.slice(0, 8),
        ignored_failed_requests: ignoredFailedRequests.slice(0, 8),
        failed_responses: failedResponses.slice(0, 8),
        console_errors: consoleErrors.slice(0, 8),
      }
    },
  }
}

async function collectBrowserCounters(page, client) {
  await client.send('Performance.enable').catch(() => null)
  await client.send('HeapProfiler.collectGarbage').catch(() => null)
  const metrics = await client.send('Performance.getMetrics').catch(() => ({ metrics: [] }))
  const map = new Map(metrics.metrics.map((item) => [item.name, item.value]))
  const dom = await page.evaluate(() => ({
    totalNodes: document.querySelectorAll('*').length,
    bodyTextLength: (document.body?.innerText ?? '').length,
    projectUserCards: document.querySelectorAll('.project-user-card').length,
    publicAccountantCards: document.querySelectorAll('.public-accountant-card').length,
    publicCustomerCards: document.querySelectorAll('.public-customer-card').length,
    offerCards: document.querySelectorAll('.offer-card-wrap, .offer-card').length,
    tradeCards: document.querySelectorAll('.trade-card, .mini-trade-card').length,
    userItems: document.querySelectorAll('.user-item').length,
    notifications: document.querySelectorAll('.notif-item').length,
    loadingStates: document.querySelectorAll('.loading-state, .ds-loading-state, .loading-container').length,
    errorStates: document.querySelectorAll('.error-state, .ds-message.danger, .admin-user-error, .error-text').length,
  }))
  return {
    js_heap_used_mb: Number(((map.get('JSHeapUsedSize') ?? 0) / (1024 * 1024)).toFixed(2)),
    dom_nodes_metric: Math.round(map.get('Nodes') ?? dom.totalNodes),
    layout_count: Math.round(map.get('LayoutCount') ?? 0),
    recalc_style_count: Math.round(map.get('RecalcStyleCount') ?? 0),
    dom,
  }
}

async function waitForAnySelector(page, selectors, timeout) {
  const selector = Array.isArray(selectors) ? selectors.join(', ') : selectors
  await page.waitForSelector(selector, { timeout, state: 'visible' })
}

async function withAuthPage(browser, contextPayload) {
  const context = await browser.newContext({
    locale: 'fa-IR',
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
  })
  await context.addInitScript(({ token, summary }) => {
    window.localStorage.setItem('auth_token', token)
    window.localStorage.removeItem('refresh_token')
    window.localStorage.removeItem('suspended_refresh_token')
    window.localStorage.setItem('current_user_summary', JSON.stringify(summary))
  }, {
    token: contextPayload.token,
    summary: contextPayload.current_user_summary,
  })
  const page = await context.newPage()
  return { context, page }
}

async function withPlainPage(browser) {
  const context = await browser.newContext({
    locale: 'fa-IR',
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
  })
  const page = await context.newPage()
  return { context, page }
}

async function timedAction(label, fn) {
  const started = performance.now()
  const payload = await fn()
  return {
    label,
    duration_ms: Number((performance.now() - started).toFixed(3)),
    ...(payload && typeof payload === 'object' ? payload : {}),
  }
}

async function measureRoute(browser, options, contextPayload, spec) {
  const pageBundle = spec.auth === false ? await withPlainPage(browser) : await withAuthPage(browser, contextPayload)
  const { context, page } = pageBundle
  const tracker = createNetworkTracker(page)
  const client = await context.newCDPSession(page).catch(() => null)
  const actions = []
  let status = 'passed'
  let error = null
  const started = performance.now()

  try {
    await page.goto(`${options.baseUrl}${spec.path}`, { waitUntil: 'domcontentloaded', timeout: options.routeTimeoutMs })
    await waitForAnySelector(page, spec.ready, options.routeTimeoutMs)
    if (spec.afterReady) {
      const nextActions = await spec.afterReady(page)
      if (Array.isArray(nextActions)) actions.push(...nextActions)
    }
    await page.waitForTimeout(250)
  } catch (caught) {
    status = 'failed'
    error = String(caught?.message ?? caught).slice(0, 500)
  }

  const firstUsableMs = Number((performance.now() - started).toFixed(3))
  const counters = client ? await collectBrowserCounters(page, client) : await collectBrowserCounters(page, { send: async () => ({ metrics: [] }) })
  const network = tracker.summary()
  if (network.failed_request_count > 0 || network.failed_response_count > 0 || counters.dom.errorStates > 0) {
    status = status === 'failed' ? status : 'warning'
  }

  await context.close().catch(() => null)
  return {
    route_id: spec.id,
    path: spec.path,
    label: spec.label,
    status,
    error,
    first_usable_ms: firstUsableMs,
    actions,
    network,
    counters,
  }
}

function profileActions(expected) {
  return async (page) => {
    const actions = []
    actions.push(await timedAction('open_project_users_first_page', async () => {
      await page.locator('.project-users-section .ds-accordion-header').click({ timeout: 15000 })
      await page.waitForFunction(
        () => document.querySelectorAll('.project-user-card').length > 0 || document.body.innerText.includes('همکاری برای نمایش وجود ندارد'),
        null,
        { timeout: 15000 },
      )
      const firstPageCount = await page.locator('.project-user-card').count()
      return {
        item_count: firstPageCount,
        bounded_page_size_ok: firstPageCount <= Number(expected.project_users_page_size ?? 25) + 2,
      }
    }))

    const loadMore = page.locator('.project-users-load-more').first()
    if (await loadMore.count().catch(() => 0)) {
      const before = await page.locator('.project-user-card').count()
      actions.push(await timedAction('project_users_load_more', async () => {
        await loadMore.click({ timeout: 15000 })
        await page.waitForFunction(
          (previous) => document.querySelectorAll('.project-user-card').length > previous,
          before,
          { timeout: 15000 },
        ).catch(() => null)
        const after = await page.locator('.project-user-card').count()
        return {
          item_count_before: before,
          item_count_after: after,
          bounded_increment_ok: after <= before + Number(expected.project_users_page_size ?? 25) + 2,
        }
      }))
    }

    actions.push(await timedAction('open_accountants_section', async () => {
      await page.locator('.accountant-relations-section .ds-accordion-header').click({ timeout: 15000 }).catch(() => null)
      await page.waitForTimeout(150)
      return { item_count: await page.locator('.public-accountant-card').count() }
    }))
    actions.push(await timedAction('open_customers_section', async () => {
      await page.locator('.customer-relations-section .ds-accordion-header').click({ timeout: 15000 }).catch(() => null)
      await page.waitForTimeout(150)
      return { item_count: await page.locator('.public-customer-card').count() }
    }))
    actions.push(await timedAction('open_trade_history_section', async () => {
      await page.locator('.profile-section .ds-accordion-header').filter({ hasText: 'تاریخچه' }).first().click({ timeout: 15000 })
      await page.waitForFunction(
        () => document.querySelectorAll('.mini-trade-card').length > 0 || document.body.innerText.includes('هیچ معامله'),
        null,
        { timeout: 15000 },
      )
      return { item_count: await page.locator('.mini-trade-card').count() }
    }))
    return actions
  }
}

function adminActions() {
  return async (page) => {
    const actions = []
    actions.push(await timedAction('open_user_manager', async () => {
      await page.getByRole('button', { name: /مدیریت کاربران/ }).click({ timeout: 15000 })
      await page.waitForFunction(
        () => document.querySelectorAll('.user-item').length > 0 || document.body.innerText.includes('کاربری یافت نشد'),
        null,
        { timeout: 15000 },
      )
      const count = await page.locator('.user-item').count()
      return {
        item_count: count,
        bounded_list_ok: count <= 120,
      }
    }))
    return actions
  }
}

function buildSpecs(contextPayload) {
  const publicProfile = contextPayload.public_profile || {}
  const customerProfile = contextPayload.customer_profile || {}
  const expected = contextPayload.expected || {}
  const specs = [
    {
      id: 'login',
      label: 'Login page first usable paint',
      path: '/login',
      ready: 'button:has-text("دریافت کد تایید")',
      auth: false,
    },
    {
      id: 'dashboard',
      label: 'Dashboard route',
      path: '/',
      ready: '.dashboard-content .hero-btn',
    },
    {
      id: 'profile_self',
      label: 'Own profile plus bounded coworkers/customers/accountants/history',
      path: '/profile',
      ready: '.profile-content',
      afterReady: profileActions(expected),
    },
    {
      id: 'market',
      label: 'Market offers route',
      path: '/market',
      ready: '.market-page .market-content',
    },
    {
      id: 'notifications',
      label: 'Notifications center route',
      path: '/notifications',
      ready: ['.notifications-list', '.ds-empty-state'],
      afterReady: async (page) => {
        await page.waitForResponse((response) => response.url().includes('/api/notifications/mark-all-read'), { timeout: 5000 }).catch(() => null)
        await page.waitForTimeout(500)
        return []
      },
    },
    {
      id: 'admin_users',
      label: 'Admin menu to user manager route',
      path: '/admin',
      ready: '.admin-panel-container',
      afterReady: adminActions(),
    },
  ]
  if (publicProfile.id) {
    specs.push({
      id: 'public_profile',
      label: 'Public user profile route',
      path: `/users/${publicProfile.id}?account_name=${encodeURIComponent(publicProfile.account_name ?? '')}`,
      ready: '.profile-content',
    })
  }
  if (customerProfile.id) {
    specs.push({
      id: 'customer_profile',
      label: 'Customer public profile route',
      path: `/users/${customerProfile.id}?account_name=${encodeURIComponent(customerProfile.account_name ?? '')}`,
      ready: '.profile-content .customer-context-banner',
    })
  }
  return specs
}

function evaluateGates(routeResults) {
  const failures = []
  const warnings = []
  for (const route of routeResults) {
    if (route.status === 'failed') {
      failures.push(`${route.route_id}: ${route.error || 'route failed'}`)
      continue
    }
    if (route.first_usable_ms > 10000) {
      failures.push(`${route.route_id}: first usable ${route.first_usable_ms}ms exceeds 10000ms`)
    } else if (route.first_usable_ms > 6000) {
      warnings.push(`${route.route_id}: first usable ${route.first_usable_ms}ms exceeds 6000ms`)
    }
    if ((route.counters?.dom?.totalNodes ?? 0) > 8500) {
      failures.push(`${route.route_id}: DOM nodes ${(route.counters?.dom?.totalNodes ?? 0)} exceeds 8500`)
    }
    for (const action of route.actions || []) {
      if (action.bounded_page_size_ok === false) {
        failures.push(`${route.route_id}/${action.label}: project users first page is not bounded`)
      }
      if (action.bounded_increment_ok === false) {
        failures.push(`${route.route_id}/${action.label}: project users load-more increment is not bounded`)
      }
      if (action.bounded_list_ok === false) {
        failures.push(`${route.route_id}/${action.label}: admin user list is not bounded`)
      }
    }
    if ((route.network?.failed_request_count ?? 0) > 0 || (route.network?.failed_response_count ?? 0) > 0) {
      warnings.push(`${route.route_id}: network failures detected`)
    }
    if ((route.counters?.dom?.errorStates ?? 0) > 0) {
      warnings.push(`${route.route_id}: error state selector detected`)
    }
  }
  return {
    status: failures.length ? 'failed' : 'passed',
    failures,
    warnings,
  }
}

async function main() {
  const options = parseArgs(process.argv.slice(2))
  const outputPath = path.isAbsolute(options.output) ? options.output : path.resolve(repoRoot, options.output)
  mkdirSync(path.dirname(outputPath), { recursive: true })
  if (!existsSync(options.context)) {
    throw new Error(`Context file does not exist: ${options.context}`)
  }
  const contextPayload = readJson(options.context)
  options.baseUrl = cleanBaseUrl(options.baseUrl)
  const browser = await chromium.launch({ headless: options.headless })
  const startedAt = new Date().toISOString()
  const started = performance.now()
  const routeResults = []
  try {
    for (const spec of buildSpecs(contextPayload)) {
      routeResults.push(await measureRoute(browser, options, contextPayload, spec))
    }
  } finally {
    await browser.close().catch(() => null)
  }

  const firstUsableValues = routeResults.map((route) => route.first_usable_ms)
  const gates = evaluateGates(routeResults)
  const payload = {
    status: gates.status,
    started_at: startedAt,
    finished_at: new Date().toISOString(),
    duration_seconds: Number(((performance.now() - started) / 1000).toFixed(3)),
    base_url: options.baseUrl,
    route_count: routeResults.length,
    summary: {
      first_usable_ms: summarizeDurations(firstUsableValues),
      failed_routes: routeResults.filter((route) => route.status === 'failed').map((route) => route.route_id),
      warning_routes: routeResults.filter((route) => route.status === 'warning').map((route) => route.route_id),
      max_dom_nodes: Math.max(...routeResults.map((route) => route.counters?.dom?.totalNodes ?? 0)),
      max_js_heap_used_mb: Math.max(...routeResults.map((route) => route.counters?.js_heap_used_mb ?? 0)),
      total_network_requests: routeResults.reduce((sum, route) => sum + (route.network?.request_count ?? 0), 0),
    },
    gates,
    routes: routeResults,
  }
  writeFileSync(outputPath, JSON.stringify(payload, null, 2) + '\n', 'utf8')
  console.log(JSON.stringify({
    status: payload.status,
    output: path.relative(repoRoot, outputPath),
    route_count: payload.route_count,
    summary: payload.summary,
    failures: gates.failures,
    warnings: gates.warnings,
  }, null, 2))
  return payload.status === 'passed' ? 0 : 1
}

main()
  .then((code) => {
    process.exitCode = code
  })
  .catch((error) => {
    console.error(error?.stack || String(error))
    process.exitCode = 1
  })
