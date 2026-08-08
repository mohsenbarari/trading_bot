const fs = require('node:fs')
const path = require('node:path')
const { pathToFileURL } = require('node:url')

const RESPONSIVE_WIDTHS = [360, 375, 390, 414, 430]
const TARGET_MIN = 44
const CTA_MIN = 48
const NAV_LABEL_MIN_FONT_SIZE = 11

const CAPTURES = [
  { selector: '#responsive-sweep', filename: 'local-home-shell-responsive-sweep.png' },
  { selector: '#quiet-home-390', filename: 'local-home-shell-quiet-390.png', exactSize: { width: 390, height: 844 } },
  { selector: '#route-shell-matrix', filename: 'local-home-shell-route-matrix.png' },
  { selector: '#state-atlas', filename: 'local-home-shell-state-atlas.png' },
  { selector: '#security-modal-device', filename: 'local-home-shell-security-session-modal.png', exactSize: { width: 390, height: 844 } },
  { selector: '#adaptive-desktop', filename: 'local-home-shell-adaptive-desktop.png' },
  { selector: '#desktop-viewport', filename: 'local-home-shell-desktop-1440x900.png', exactSize: { width: 1440, height: 900 } },
]

function resolvePlaywright() {
  const moduleCandidates = [
    process.env.UIUX_PLAYWRIGHT_MODULE,
    'playwright',
    path.resolve(__dirname, '../../frontend/node_modules/playwright'),
    '/root/trading-bot/trading_bot/frontend/node_modules/playwright',
  ].filter(Boolean)

  const failures = []
  for (const candidate of moduleCandidates) {
    try {
      return { module: require(candidate), source: candidate }
    } catch (error) {
      failures.push(`${candidate}: ${error.code || error.message}`)
    }
  }

  throw new Error(`Playwright is unavailable. Tried:\n${failures.join('\n')}`)
}

function resolveSharp() {
  const moduleCandidates = [
    process.env.UIUX_SHARP_MODULE,
    'sharp',
    path.resolve(__dirname, '../../frontend/node_modules/sharp'),
    '/root/trading-bot/trading_bot/frontend/node_modules/sharp',
  ].filter(Boolean)

  const failures = []
  for (const candidate of moduleCandidates) {
    try {
      return require(candidate)
    } catch (error) {
      failures.push(`${candidate}: ${error.code || error.message}`)
    }
  }

  throw new Error(`Sharp is required for exact-size evidence crops. Tried:\n${failures.join('\n')}`)
}

function findFontRoot() {
  const candidates = [
    process.env.UIUX_VAZIRMATN_FONT_ROOT,
    path.resolve(__dirname, '../../frontend/node_modules/vazirmatn/fonts/webfonts'),
    '/root/trading-bot/trading_bot/frontend/node_modules/vazirmatn/fonts/webfonts',
  ].filter(Boolean)

  for (const candidate of candidates) {
    const required = [
      'Vazirmatn-Regular.woff2',
      'Vazirmatn-Medium.woff2',
      'Vazirmatn-SemiBold.woff2',
      'Vazirmatn-Bold.woff2',
    ]
    if (required.every((filename) => fs.existsSync(path.join(candidate, filename)))) return candidate
  }

  throw new Error(`Vazirmatn webfonts are unavailable. Tried: ${candidates.join(', ')}`)
}

function buildEmbeddedFontCss(fontRoot) {
  const faces = [
    { filename: 'Vazirmatn-Regular.woff2', weight: 400 },
    { filename: 'Vazirmatn-Medium.woff2', weight: 500 },
    { filename: 'Vazirmatn-SemiBold.woff2', weight: 600 },
    { filename: 'Vazirmatn-Bold.woff2', weight: 700 },
  ]

  return faces.map(({ filename, weight }) => {
    const base64 = fs.readFileSync(path.join(fontRoot, filename)).toString('base64')
    return `@font-face{font-family:"Vazirmatn Evidence";src:url(data:font/woff2;base64,${base64}) format("woff2");font-style:normal;font-weight:${weight};font-display:block;}`
  }).join('\n') + '\n:root,body,button,a{font-family:"Vazirmatn Evidence","Vazirmatn",Tahoma,Arial,sans-serif!important;}'
}

function readPngDimensions(filePath) {
  const buffer = fs.readFileSync(filePath)
  if (buffer.length < 24 || buffer.subarray(0, 8).toString('hex') !== '89504e470d0a1a0a') {
    throw new Error(`Expected a PNG file at ${filePath}`)
  }
  return {
    width: buffer.readUInt32BE(16),
    height: buffer.readUInt32BE(20),
  }
}

function writeJsonAtomic(filePath, value) {
  const temporary = `${filePath}.tmp-${process.pid}-${Date.now()}`
  fs.writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`)
  fs.renameSync(temporary, filePath)
}

async function captureLocator(page, assetsDir, capture) {
  const locator = page.locator(capture.selector)
  const count = await locator.count()
  if (count !== 1) throw new Error(`Expected one ${capture.selector}; found ${count}`)

  const destination = path.join(assetsDir, capture.filename)
  const stamp = `${process.pid}-${Date.now()}`
  const rawTemporary = path.join(assetsDir, `.${capture.filename}.raw-${stamp}.png`)
  let finalTemporary = rawTemporary
  await locator.screenshot({ path: rawTemporary, animations: 'disabled' })

  if (capture.exactSize) {
    const rawDimensions = readPngDimensions(rawTemporary)
    const { width, height } = capture.exactSize
    if (rawDimensions.width < width || rawDimensions.height < height) {
      throw new Error(`${capture.selector} rendered ${rawDimensions.width}×${rawDimensions.height}; cannot crop to ${width}×${height}`)
    }
    if (rawDimensions.width !== width || rawDimensions.height !== height) {
      const adjustedTemporary = path.join(assetsDir, `.${capture.filename}.exact-${stamp}.png`)
      const sharp = resolveSharp()
      await sharp(rawTemporary)
        .extract({
          left: Math.floor((rawDimensions.width - width) / 2),
          top: Math.floor((rawDimensions.height - height) / 2),
          width,
          height,
        })
        .toFile(adjustedTemporary)
      fs.rmSync(rawTemporary)
      finalTemporary = adjustedTemporary
    }
  }
  fs.renameSync(finalTemporary, destination)

  return {
    selector: capture.selector,
    filename: capture.filename,
    pixelDimensions: readPngDimensions(destination),
  }
}

async function measureEvidence(page) {
  return page.evaluate(({ widths, targetMin, ctaMin, navLabelMinFontSize }) => {
    const visible = (element) => {
      const style = getComputedStyle(element)
      const rect = element.getBoundingClientRect()
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0
    }

    const roundedRect = (element) => {
      const rect = element.getBoundingClientRect()
      return {
        x: Math.round(rect.x + window.scrollX),
        y: Math.round(rect.y + window.scrollY),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
        right: Math.round(rect.right + window.scrollX),
        bottom: Math.round(rect.bottom + window.scrollY),
      }
    }

    const describeAction = (element, index) => {
      const rect = roundedRect(element)
      const screen = element.closest('[data-product-screen]')
      return {
        id: element.id || `${screen?.dataset.productScreen || 'screen'}:${element.tagName.toLowerCase()}:${index}`,
        screen: screen?.dataset.productScreen || null,
        label: (element.getAttribute('aria-label') || element.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 90),
        tag: element.tagName.toLowerCase(),
        disabled: element.matches(':disabled'),
        ...rect,
      }
    }

    const actions = [...document.querySelectorAll('.product-screen button, .product-screen a[href], .product-screen [role="button"]')]
      .filter(visible)
      .map(describeAction)

    const ctas = [...document.querySelectorAll('.product-screen .cta')]
      .filter(visible)
      .map(describeAction)

    const navigationLabels = [...document.querySelectorAll('.product-screen .nav-item span')]
      .filter(visible)
      .map((element) => ({
        label: (element.textContent || '').trim(),
        fontSize: Number.parseFloat(getComputedStyle(element).fontSize),
        screen: element.closest('[data-product-screen]')?.dataset.productScreen || null,
      }))

    const productScreens = [...document.querySelectorAll('[data-product-screen]')].map((screen) => {
      const rect = roundedRect(screen)
      return {
        id: screen.dataset.productScreen,
        ...rect,
        clientWidth: screen.clientWidth,
        clientHeight: screen.clientHeight,
        scrollWidth: screen.scrollWidth,
        scrollHeight: screen.scrollHeight,
        overflowX: screen.scrollWidth > screen.clientWidth + 1,
        overflowY: screen.scrollHeight > screen.clientHeight + 1,
      }
    })

    const responsive = widths.map((width) => {
      const scenario = document.querySelector(`[data-responsive-width="${width}"]`)
      const device = scenario.querySelector('.device')
      const content = device.querySelector('.screen-content')
      const nav = device.querySelector('.bottom-nav')
      const contentBlocks = [...content.children].filter(visible)
      const lastContentBottom = Math.max(...contentBlocks.map((element) => element.getBoundingClientRect().bottom))
      const navTop = nav.getBoundingClientRect().top

      return {
        requestedWidth: width,
        device: roundedRect(device),
        exactWidth: Math.round(device.getBoundingClientRect().width) === width,
        overflowX: device.scrollWidth > device.clientWidth + 1,
        overflowY: device.scrollHeight > device.clientHeight + 1,
        bottomNavClearance: Math.round(navTop - lastContentBottom),
        identityCount: device.querySelectorAll('.identity').length,
        notificationCount: device.querySelectorAll('[data-header-notification]').length,
        protectedMarketSlotCount: device.querySelectorAll('[data-protected-market-slot]').length,
        navigationCount: device.querySelectorAll('.bottom-nav').length,
        navDestinationCount: device.querySelectorAll('[data-nav-destination]').length,
      }
    })

    const routeShells = [...document.querySelectorAll('[data-route-shell]')].map((scenario) => {
      const screen = scenario.querySelector('[data-product-screen]')
      return {
        id: scenario.dataset.routeShell,
        mode: scenario.dataset.shellMode,
        bottomNavigationCount: screen.querySelectorAll('.bottom-nav').length,
        floatingNavigationCount: screen.querySelectorAll('.floating-nav').length,
        notificationCount: screen.querySelectorAll('[data-header-notification]').length,
        activeDestinationCount: screen.querySelectorAll('.nav-item.is-active').length,
        destinations: [...screen.querySelectorAll('[data-nav-destination]')].map((item) => item.dataset.navDestination),
      }
    })

    const modal = document.querySelector('#security-modal-device')
    const modalActions = [...modal.querySelectorAll('.security-actions button')].filter(visible).map(describeAction)
    const desktop = document.querySelector('#desktop-viewport')
    const desktopRect = roundedRect(desktop)
    const pwaIcon = document.querySelector('#state-pwa [data-pwa-app-icon]')

    const productText = [...document.querySelectorAll('[data-product-screen]')]
      .map((screen) => screen.textContent || '')
      .join(' ')
      .replace(/\s+/g, ' ')

    const forbiddenTextChecks = [
      'حساب فعال',
      'حساب جاری',
      'تعداد روابط',
      'تعداد مسیر',
      'تعداد ابزار',
      'تعداد معامله',
      'معاملات امروز',
      'همکاران',
      'کالاها',
      'خوش آمدید',
    ].map((term) => ({ term, present: productText.includes(term) }))

    const structuralExclusions = {
      accountActiveBadgeCount: document.querySelectorAll('.product-screen .account-active-badge').length,
      roleChipCount: document.querySelectorAll('.product-screen .role-chip').length,
      kpiCount: document.querySelectorAll('.product-screen .kpi, .product-screen [data-kpi]').length,
      dailyEmptyCardCount: document.querySelectorAll('.product-screen .daily-empty-card').length,
      collaboratorSectionCount: document.querySelectorAll('.product-screen .collaborator-section').length,
      commoditySectionCount: document.querySelectorAll('.product-screen .commodity-section').length,
      sidebarCount: document.querySelectorAll('.product-screen .sidebar, .product-screen [data-sidebar]').length,
    }

    const protectedSlots = [...document.querySelectorAll('.product-screen [data-protected-market-slot]')]
    const inactiveState = document.querySelector('#state-inactive')
    const loadingState = document.querySelector('#state-loading')
    const errorState = document.querySelector('#state-error')
    const offlineState = document.querySelector('#state-offline')
    const staleState = document.querySelector('#state-stale')
    const errorText = (errorState.textContent || '').replace(/\s+/g, ' ')
    const futureRetryTerms = ['بعداً دوباره تلاش', 'کمی بعد تلاش', 'خودکار دوباره تلاش', 'به‌زودی دوباره تلاش']

    return {
      responsive,
      productScreens,
      actions: {
        count: actions.length,
        minimumWidth: Math.min(...actions.map((item) => item.width)),
        minimumHeight: Math.min(...actions.map((item) => item.height)),
        violations: actions.filter((item) => item.width < targetMin || item.height < targetMin),
        items: actions,
      },
      ctas: {
        count: ctas.length,
        minimumHeight: Math.min(...ctas.map((item) => item.height)),
        violations: ctas.filter((item) => item.height < ctaMin),
        items: ctas,
      },
      navigationTypography: {
        count: navigationLabels.length,
        minimumFontSize: Math.min(...navigationLabels.map((item) => item.fontSize)),
        requiredMinimumFontSize: navLabelMinFontSize,
        violations: navigationLabels.filter((item) => item.fontSize < navLabelMinFontSize),
        items: navigationLabels,
      },
      routeShells,
      securityModal: {
        dialogCount: modal.querySelectorAll('[role="dialog"][aria-modal="true"]').length,
        actionCount: modalActions.length,
        actions: modalActions,
        notificationCount: modal.querySelectorAll('[data-header-notification]').length,
        requestTimeCount: modal.querySelectorAll('[data-session-request-time]').length,
        countdownCount: modal.querySelectorAll('[data-session-countdown]').length,
        overflowX: modal.scrollWidth > modal.clientWidth + 1,
        overflowY: modal.scrollHeight > modal.clientHeight + 1,
      },
      desktop: {
        ...desktopRect,
        navigationCount: desktop.querySelectorAll('.desktop-nav').length,
        identityCount: desktop.querySelectorAll('.identity').length,
        notificationCount: desktop.querySelectorAll('[data-header-notification]').length,
        protectedMarketSlotCount: desktop.querySelectorAll('[data-protected-market-slot]').length,
        sidebarCount: desktop.querySelectorAll('.sidebar, [data-sidebar]').length,
        kpiCount: desktop.querySelectorAll('.kpi, [data-kpi]').length,
        overflowX: desktop.scrollWidth > desktop.clientWidth + 1,
        overflowY: desktop.scrollHeight > desktop.clientHeight + 1,
      },
      contentMinimalism: {
        forbiddenTextChecks,
        structuralExclusions,
      },
      scopeGuards: {
        protectedMarketSlotCount: protectedSlots.length,
        nonNormativePlaceholderCount: protectedSlots.filter((slot) => slot.querySelector('[data-non-normative-market-placeholder]')).length,
        protectedMarketInteractiveDescendantCount: protectedSlots.reduce((total, slot) => total + slot.querySelectorAll('button, a[href], [role="button"]').length, 0),
        protectedMarketStateBadgeCount: protectedSlots.reduce((total, slot) => total + slot.querySelectorAll('.market-state, [data-market-state]').length, 0),
      },
      stateContracts: {
        inactive: {
          accountFollowupCount: inactiveState.querySelectorAll('[data-follow-up-destination="account"]').length,
          accountFollowupLabel: (inactiveState.querySelector('[data-follow-up-destination="account"]')?.textContent || '').trim(),
          protectedMarketSlotCount: inactiveState.querySelectorAll('[data-protected-market-slot]').length,
          disabledActionCount: inactiveState.querySelectorAll('button:disabled, a[aria-disabled="true"]').length,
        },
        loading: {
          permissionDestinationCount: loadingState.querySelectorAll('[data-nav-destination="market"], [data-nav-destination="operations"], [data-protected-market-slot]').length,
        },
        error: {
          permissionDestinationCount: errorState.querySelectorAll('[data-nav-destination="market"], [data-nav-destination="operations"], [data-protected-market-slot]').length,
          presumedCauseTerms: ['اینترنت', 'اتصال', 'شبکه'].filter((term) => errorText.includes(term)),
        },
        offline: {
          connectionSignalCount: offlineState.querySelectorAll('[data-connection-signal]').length,
          disabledActionCount: offlineState.querySelectorAll('button:disabled, a[aria-disabled="true"]').length,
          stateBadgeCount: offlineState.querySelectorAll('.market-state, [data-market-state]').length,
        },
        stale: {
          connectionSignalCount: staleState.querySelectorAll('[data-connection-signal]').length,
          disabledActionCount: staleState.querySelectorAll('button:disabled, a[aria-disabled="true"]').length,
          stateBadgeCount: staleState.querySelectorAll('.market-state, [data-market-state]').length,
        },
        pwa: {
          realIconCount: pwaIcon ? 1 : 0,
          realIconLoaded: Boolean(pwaIcon?.complete && pwaIcon.naturalWidth > 0 && pwaIcon.naturalHeight > 0),
          realIconSource: pwaIcon?.getAttribute('src') || null,
        },
        futureRetryPromises: futureRetryTerms.filter((term) => productText.includes(term)),
      },
    }
  }, {
    widths: RESPONSIVE_WIDTHS,
    targetMin: TARGET_MIN,
    ctaMin: CTA_MIN,
    navLabelMinFontSize: NAV_LABEL_MIN_FONT_SIZE,
  })
}

function buildAssertions(metrics, font) {
  const bottomShells = metrics.routeShells.filter((item) => item.mode === 'bottom-nav')
  const floatingShells = metrics.routeShells.filter((item) => item.mode === 'floating')
  const accountantShell = metrics.routeShells.find((item) => item.id === 'route-accountant-home')
  const structuralValues = Object.values(metrics.contentMinimalism.structuralExclusions)
  const guards = metrics.scopeGuards
  const states = metrics.stateContracts

  return [
    { id: 'font-vazirmatn-loaded', passed: font.loaded && font.faces.length >= 4, detail: `${font.faces.length} loaded faces` },
    { id: 'responsive-widths-exact', passed: metrics.responsive.length === 5 && metrics.responsive.every((item) => item.exactWidth), detail: metrics.responsive.map((item) => item.device.width).join(', ') },
    { id: 'quiet-home-core-contract', passed: metrics.responsive.every((item) => item.identityCount === 1 && item.notificationCount === 1 && item.protectedMarketSlotCount === 1 && item.navigationCount === 1), detail: 'identity=1, notification=1, market-slot=1, navigation=1 per width' },
    { id: 'quiet-home-nav-clearance', passed: metrics.responsive.every((item) => item.bottomNavClearance >= 24), detail: `minimum ${Math.min(...metrics.responsive.map((item) => item.bottomNavClearance))}px` },
    { id: 'no-product-screen-overflow', passed: metrics.productScreens.every((item) => !item.overflowX && !item.overflowY), detail: `${metrics.productScreens.filter((item) => item.overflowX || item.overflowY).length} violation(s)` },
    { id: 'touch-targets-44', passed: metrics.actions.violations.length === 0, detail: `minimum ${metrics.actions.minimumWidth}×${metrics.actions.minimumHeight}px across ${metrics.actions.count}` },
    { id: 'cta-height-48', passed: metrics.ctas.violations.length === 0, detail: `minimum ${metrics.ctas.minimumHeight}px across ${metrics.ctas.count}` },
    { id: 'navigation-label-font-11', passed: metrics.navigationTypography.count > 0 && metrics.navigationTypography.violations.length === 0, detail: `minimum ${metrics.navigationTypography.minimumFontSize}px across ${metrics.navigationTypography.count}` },
    { id: 'route-shell-matrix-complete', passed: metrics.routeShells.length === 6 && bottomShells.length === 4 && floatingShells.length === 2, detail: `${bottomShells.length} bottom + ${floatingShells.length} floating` },
    { id: 'route-shell-active-destination', passed: bottomShells.every((item) => item.bottomNavigationCount === 1 && item.notificationCount === 1 && item.activeDestinationCount === 1) && floatingShells.every((item) => item.floatingNavigationCount === 1 && item.bottomNavigationCount === 0 && item.notificationCount === 0), detail: 'standard shell has one notification and active destination; protected shell has one floating control' },
    { id: 'accountant-market-omitted', passed: accountantShell && !accountantShell.destinations.includes('market') && accountantShell.destinations.length === 4, detail: accountantShell ? accountantShell.destinations.join(', ') : 'missing shell' },
    { id: 'security-modal-decision-complete', passed: metrics.securityModal.dialogCount === 1 && metrics.securityModal.actionCount === 2 && metrics.securityModal.notificationCount === 1 && metrics.securityModal.requestTimeCount === 1 && metrics.securityModal.countdownCount === 1 && !metrics.securityModal.overflowX && !metrics.securityModal.overflowY, detail: `${metrics.securityModal.actionCount} actions; time=1; countdown=1` },
    { id: 'desktop-exact-1440x900', passed: metrics.desktop.width === 1440 && metrics.desktop.height === 900, detail: `${metrics.desktop.width}×${metrics.desktop.height}` },
    { id: 'desktop-content-economy', passed: metrics.desktop.navigationCount === 1 && metrics.desktop.identityCount === 1 && metrics.desktop.notificationCount === 1 && metrics.desktop.protectedMarketSlotCount === 1 && metrics.desktop.sidebarCount === 0 && metrics.desktop.kpiCount === 0, detail: 'nav=1, identity=1, notification=1, market-slot=1, sidebar=0, KPI=0' },
    { id: 'forbidden-content-absent', passed: metrics.contentMinimalism.forbiddenTextChecks.every((item) => !item.present) && structuralValues.every((value) => value === 0), detail: 'all forbidden text and structures absent' },
    { id: 'protected-market-placeholder-only', passed: guards.protectedMarketSlotCount > 0 && guards.nonNormativePlaceholderCount === guards.protectedMarketSlotCount && guards.protectedMarketInteractiveDescendantCount === 0 && guards.protectedMarketStateBadgeCount === 0, detail: `${guards.nonNormativePlaceholderCount}/${guards.protectedMarketSlotCount} locked placeholders; interactive=${guards.protectedMarketInteractiveDescendantCount}; states=${guards.protectedMarketStateBadgeCount}` },
    { id: 'pwa-real-icon-loaded', passed: states.pwa.realIconCount === 1 && states.pwa.realIconLoaded && states.pwa.realIconSource.endsWith('/frontend/public/pwa-192x192.png'), detail: `${states.pwa.realIconSource || 'missing'}; loaded=${states.pwa.realIconLoaded}` },
    { id: 'draft-state-blockers-resolved', passed: states.inactive.accountFollowupCount === 1 && states.inactive.accountFollowupLabel === 'پیگیری در حساب' && states.inactive.protectedMarketSlotCount === 0 && states.inactive.disabledActionCount === 0 && states.loading.permissionDestinationCount === 0 && states.error.permissionDestinationCount === 0 && states.error.presumedCauseTerms.length === 0 && states.offline.connectionSignalCount === 1 && states.offline.disabledActionCount === 0 && states.offline.stateBadgeCount === 0 && states.stale.connectionSignalCount === 1 && states.stale.disabledActionCount === 0 && states.stale.stateBadgeCount === 0 && states.futureRetryPromises.length === 0, detail: 'canonical account follow-up; loading/error neutral; offline/stale single-signal; no future retry promise' },
  ]
}

async function main() {
  const { module: playwright, source: playwrightSource } = resolvePlaywright()
  const { chromium } = playwright
  const fontRoot = findFontRoot()
  const htmlPath = path.join(__dirname, 'home-shell-evidence.html')
  const assetsDir = path.join(__dirname, 'assets')
  fs.mkdirSync(assetsDir, { recursive: true })

  const browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 2400, height: 1600 }, deviceScaleFactor: 1 })
  const stagingDir = path.join(assetsDir, `.evidence-run-${process.pid}-${Date.now()}`)
  fs.mkdirSync(stagingDir)
  const pageErrors = []
  page.on('pageerror', (error) => pageErrors.push(error.message))

  try {
    await page.goto(pathToFileURL(htmlPath).href, { waitUntil: 'load' })
    await page.addStyleTag({ content: buildEmbeddedFontCss(fontRoot) })
    await page.evaluate(async () => {
      await Promise.all([400, 500, 600, 700].map((weight) => document.fonts.load(`${weight} 16px "Vazirmatn Evidence"`)))
      await document.fonts.ready
    })
    await page.locator('#quiet-home-390').waitFor({ state: 'visible' })

    const font = await page.evaluate(() => {
      const checks = [400, 500, 600, 700].map((weight) => ({
        weight,
        loaded: document.fonts.check(`${weight} 16px "Vazirmatn Evidence"`),
      }))
      const faces = [...document.fonts]
        .filter((face) => face.family.replace(/["']/g, '').trim() === 'Vazirmatn Evidence')
        .map((face) => ({ family: face.family, weight: face.weight, style: face.style, status: face.status }))
      return {
        loaded: checks.every((item) => item.loaded) && faces.length >= 4 && faces.every((face) => face.status === 'loaded'),
        checks,
        faces,
        computedBodyFamily: getComputedStyle(document.body).fontFamily,
      }
    })

    const measurements = await measureEvidence(page)
    const assertions = buildAssertions(measurements, font)
    const failures = assertions.filter((item) => !item.passed)

    if (failures.length > 0 || pageErrors.length > 0) {
      throw new Error(`Evidence preflight failed: ${failures.map((item) => item.id).join(', ') || pageErrors.join(', ')}`)
    }

    const captures = []
    for (const capture of CAPTURES) captures.push(await captureLocator(page, stagingDir, capture))

    if (pageErrors.length > 0) {
      throw new Error(`Evidence capture failed: ${pageErrors.join(', ')}`)
    }

    const report = {
      schemaVersion: 1,
      evidenceRole: 'secondary-derivative-evidence',
      primaryDesignSource: 'Figma file z8jgJxST4O2APzWnlyP9gv',
      scope: 'Stage 0B-2 home and authenticated shell; Market and Messenger internals excluded',
      generatedAt: new Date().toISOString(),
      environment: {
        playwrightSource,
        browser: await browser.version(),
        viewport: { width: 2400, height: 1600, deviceScaleFactor: 1 },
        fontSourceRoot: fontRoot,
      },
      font,
      captures,
      measurements,
      assertions,
      summary: {
        passed: failures.length === 0 && pageErrors.length === 0,
        assertionCount: assertions.length,
        failureCount: failures.length,
        pageErrorCount: pageErrors.length,
      },
      failures,
      pageErrors,
    }

    if (!report.summary.passed) {
      throw new Error(`Evidence validation failed: ${failures.map((item) => item.id).join(', ') || pageErrors.join(', ')}`)
    }

    const metricsFilename = 'local-home-shell-validation-metrics.json'
    writeJsonAtomic(path.join(stagingDir, metricsFilename), report)
    for (const capture of captures) {
      fs.renameSync(path.join(stagingDir, capture.filename), path.join(assetsDir, capture.filename))
    }
    fs.renameSync(path.join(stagingDir, metricsFilename), path.join(assetsDir, metricsFilename))

    process.stdout.write(`${JSON.stringify({
      passed: true,
      captures: captures.map((item) => `${item.filename} (${item.pixelDimensions.width}×${item.pixelDimensions.height})`),
      responsiveWidths: measurements.responsive.map((item) => item.device.width),
      minimumBottomNavClearance: Math.min(...measurements.responsive.map((item) => item.bottomNavClearance)),
      minimumTarget: `${measurements.actions.minimumWidth}×${measurements.actions.minimumHeight}`,
      minimumCtaHeight: measurements.ctas.minimumHeight,
      fontFaces: font.faces.length,
      assertionCount: assertions.length,
    }, null, 2)}\n`)
  } finally {
    await browser.close()
    fs.rmSync(stagingDir, { recursive: true, force: true })
  }
}

main().catch((error) => {
  console.error(error.stack || error.message)
  process.exitCode = 1
})
