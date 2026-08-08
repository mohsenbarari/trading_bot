const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')
const { pathToFileURL } = require('node:url')

const RESPONSIVE_WIDTHS = [360, 375, 390, 414, 430]
const EXPECTED_SCENARIOS = ['M01', 'M02', 'M03', 'M04', 'M05', 'M06', 'M07', 'M08', 'M09', 'M10']
const TARGET_MIN = 44
const CTA_MIN = 48
const EXPECTED_ASSERTION_IDS = [
  'font-vazirmatn-loaded',
  'ten-mobile-scenarios-complete',
  'mobile-list-detail-xor',
  'mobile-roots-exact-390x844',
  'no-product-overflow-or-clipping',
  'touch-targets-44',
  'cta-height-48',
  'responsive-width-sweep',
  'desktop-true-master-detail-1440x900',
  'minimal-content-contract',
  'protected-interiors-absent',
  'operations-role-destinations-bounded',
  'customer-flow-decision-complete',
  'accountant-flow-not-duplicated',
  'session-decision-in-context',
  'maximum-risk-deletion-complete',
  'recovery-state-atlas-complete',
]

const CAPTURES = [
  { selector: '#operations-role-matrix', filename: 'local-operations-role-matrix.png' },
  { selector: '#customer-task-flow', filename: 'local-customer-task-flow.png' },
  { selector: '#accountant-task-flow', filename: 'local-accountant-task-flow.png' },
  { selector: '#workspace-state-atlas', filename: 'local-workspace-state-atlas.png' },
  { selector: '#workspace-action-feedback', filename: 'local-workspace-action-feedback.png' },
  { selector: '#workspaces-responsive-sweep', filename: 'local-workspaces-responsive-sweep.png' },
  { selector: '#desktop-customer-master-detail', filename: 'local-customer-master-detail-1440x900.png', exactSize: { width: 1440, height: 900 } },
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

  const required = [
    'Vazirmatn-Regular.woff2',
    'Vazirmatn-Medium.woff2',
    'Vazirmatn-SemiBold.woff2',
    'Vazirmatn-Bold.woff2',
  ]
  for (const candidate of candidates) {
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
  }).join('\n') + '\n:root,body,button,a,input,select,textarea{font-family:"Vazirmatn Evidence","Vazirmatn",Tahoma,Arial,sans-serif!important;}'
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

async function captureLocator(page, stagingDir, capture) {
  const locator = page.locator(capture.selector)
  const count = await locator.count()
  if (count !== 1) throw new Error(`Expected one ${capture.selector}; found ${count}`)

  const destination = path.join(stagingDir, capture.filename)
  if (capture.exactSize) {
    const rawTemporary = `${destination}.raw.png`
    await locator.screenshot({ path: rawTemporary, animations: 'disabled' })
    const rawDimensions = readPngDimensions(rawTemporary)
    if (rawDimensions.width < capture.exactSize.width || rawDimensions.height < capture.exactSize.height) {
      throw new Error(`${capture.selector} rendered ${rawDimensions.width}×${rawDimensions.height}; cannot crop to the required geometry`)
    }
    if (rawDimensions.width === capture.exactSize.width && rawDimensions.height === capture.exactSize.height) {
      fs.renameSync(rawTemporary, destination)
    } else {
      const sharp = resolveSharp()
      await sharp(rawTemporary).extract({
        left: Math.floor((rawDimensions.width - capture.exactSize.width) / 2),
        top: Math.floor((rawDimensions.height - capture.exactSize.height) / 2),
        width: capture.exactSize.width,
        height: capture.exactSize.height,
      }).toFile(destination)
      fs.rmSync(rawTemporary)
    }
  } else {
    await locator.screenshot({ path: destination, animations: 'disabled' })
  }
  const pixelDimensions = readPngDimensions(destination)

  if (capture.exactSize && (pixelDimensions.width !== capture.exactSize.width || pixelDimensions.height !== capture.exactSize.height)) {
    throw new Error(`${capture.selector} rendered ${pixelDimensions.width}×${pixelDimensions.height}; expected ${capture.exactSize.width}×${capture.exactSize.height}`)
  }

  return {
    selector: capture.selector,
    filename: capture.filename,
    pixelDimensions,
    sha256: crypto.createHash('sha256').update(fs.readFileSync(destination)).digest('hex'),
  }
}

async function measureEvidence(page) {
  return page.evaluate(({ responsiveWidths, targetMin, ctaMin }) => {
    const visible = (element) => {
      if (!element) return false
      const rect = element.getBoundingClientRect()
      if (rect.width <= 0 || rect.height <= 0 || element.hidden) return false
      for (let current = element; current; current = current.parentElement) {
        const style = getComputedStyle(current)
        if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse' || Number.parseFloat(style.opacity) <= 0.01) return false
      }
      return true
    }

    const rectOf = (element) => {
      const rect = element.getBoundingClientRect()
      const precise = (value) => Number(value.toFixed(3))
      return {
        width: precise(rect.width),
        height: precise(rect.height),
        x: precise(rect.x + window.scrollX),
        y: precise(rect.y + window.scrollY),
        right: precise(rect.right + window.scrollX),
        bottom: precise(rect.bottom + window.scrollY),
      }
    }

    const inside = (inner, outer, tolerance = 0.5) => inner.left >= outer.left - tolerance && inner.top >= outer.top - tolerance && inner.right <= outer.right + tolerance && inner.bottom <= outer.bottom + tolerance

    const normalize = (value) => (value || '').replace(/\s+/g, ' ').trim()
    const visibleText = (root) => {
      const fragments = []
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
      let node
      while ((node = walker.nextNode())) {
        if (visible(node.parentElement)) fragments.push(normalize(node.textContent))
      }
      return normalize(fragments.filter(Boolean).join(' '))
    }
    const describeAction = (element, index) => ({
      id: element.id || `${element.closest('[data-product-screen]')?.dataset.productScreen || element.closest('[data-state-contract]')?.dataset.stateContract || 'surface'}:${element.tagName.toLowerCase()}:${index}`,
      screen: element.closest('[data-product-screen]')?.dataset.productScreen || null,
      label: normalize(element.getAttribute('aria-label') || element.textContent).slice(0, 100),
      tag: element.tagName.toLowerCase(),
      insideShellNavigation: Boolean(element.closest('[data-shell-navigation]')),
      insideSurface: inside(element.getBoundingClientRect(), element.closest('[data-product-surface]').getBoundingClientRect()),
      ...rectOf(element),
    })

    const surfaces = [...document.querySelectorAll('[data-product-surface]')]
    const productScreens = [...document.querySelectorAll('[data-product-screen]')].map((screen) => {
      const main = screen.querySelector('.screen-content, .desktop-workspace')
      const screenRect = screen.getBoundingClientRect()
      const contentContracts = [...screen.querySelectorAll('[data-contract-content]')].map((content) => {
        const contentRect = content.getBoundingClientRect()
        const mainRect = main?.getBoundingClientRect()
        return {
          insideMain: Boolean(mainRect && contentRect.top >= mainRect.top - 1 && contentRect.left >= mainRect.left - 1 && contentRect.right <= mainRect.right + 1 && contentRect.bottom <= mainRect.bottom + 1),
          ...rectOf(content),
        }
      })
      const descendantBoundsViolations = [...screen.querySelectorAll('*')]
        .filter(visible)
        .filter((element) => !inside(element.getBoundingClientRect(), screenRect))
        .map((element) => ({ tag: element.tagName.toLowerCase(), className: element.className?.baseVal || element.className || '', text: normalize(element.textContent).slice(0, 80) }))
      const clippedTextViolations = [...screen.querySelectorAll('h1,h2,h3,h4,h5,p,strong,small,label,li,span,button,a')]
        .filter(visible)
        .filter((element) => {
          const style = getComputedStyle(element)
          if (style.textOverflow === 'ellipsis') return false
          return element.scrollWidth > element.clientWidth + 0.5 || element.scrollHeight > element.clientHeight + 0.5
        })
        .map((element) => ({ tag: element.tagName.toLowerCase(), text: normalize(element.textContent).slice(0, 80) }))
      const visibleMainChildren = main ? [...main.children].filter(visible) : []
      const unmarkedMainChildren = visibleMainChildren.filter((element) => !element.matches('[data-master-list],[data-detail-view]') && !element.querySelector('[data-master-list],[data-detail-view]'))
      return {
        id: screen.dataset.productScreen,
        ...rectOf(screen),
        clientWidth: screen.clientWidth,
        clientHeight: screen.clientHeight,
        scrollWidth: screen.scrollWidth,
        scrollHeight: screen.scrollHeight,
        overflowX: screen.scrollWidth > screen.clientWidth + 1,
        overflowY: screen.scrollHeight > screen.clientHeight + 1,
        mainOverflowX: Boolean(main && main.scrollWidth > main.clientWidth + 1),
        mainOverflowY: Boolean(main && main.scrollHeight > main.clientHeight + 1),
        contentContracts,
        descendantBoundsViolations,
        clippedTextViolations,
        visibleMainChildCount: visibleMainChildren.length,
        unmarkedMainChildCount: unmarkedMainChildren.length,
      }
    })

    const surfaceOverflow = surfaces.map((surface) => ({
      id: surface.dataset.productScreen || surface.dataset.stateContract,
      overflowX: surface.scrollWidth > surface.clientWidth + 1,
      overflowY: surface.scrollHeight > surface.clientHeight + 1,
    }))

    const actions = [...document.querySelectorAll('[data-product-surface] button, [data-product-surface] a[href], [data-product-surface] input, [data-product-surface] select, [data-product-surface] textarea')]
      .filter(visible)
      .map(describeAction)
    const ctas = [...document.querySelectorAll('[data-product-surface] .cta')]
      .filter(visible)
      .map(describeAction)

    const mobileScenarios = [...document.querySelectorAll('[data-mobile-scenario]')].map((screen) => {
      const main = screen.querySelector('.screen-content')
      const listCount = [...screen.querySelectorAll('[data-master-list]')].filter(visible).length
      const detailCount = [...screen.querySelectorAll('[data-detail-view]')].filter(visible).length
      const visibleMainChildren = [...main.children].filter(visible)
      const unmarkedMainChildCount = visibleMainChildren.filter((element) => !element.matches('[data-master-list],[data-detail-view]') && !element.querySelector('[data-master-list],[data-detail-view]')).length
      const contractRoots = [...screen.querySelectorAll('[data-contract-content]')].filter(visible)
      const contractRoot = contractRoots[0]
      const actionCount = [...screen.querySelectorAll('button, a[href], input, select, textarea')].filter(visible).length
      const nestedContractCount = contractRoot ? [...contractRoot.querySelectorAll('[data-contract-content],[data-master-list],[data-detail-view]')].filter(visible).length : 0
      const unexpectedTaskPaneCount = screen.dataset.screenMode === 'list' && contractRoot
        ? [...contractRoot.querySelectorAll('[role="dialog"],.confirmation-sheet,.detail-card,.comparison-card,.session-card,.cascade-list,[data-before-after-review],[data-session-card]')].filter(visible).length
        : 0
      return {
        id: screen.dataset.productScreen,
        mode: screen.dataset.screenMode,
        ...rectOf(screen),
        listCount,
        detailCount,
        visibleMainChildCount: visibleMainChildren.length,
        unmarkedMainChildCount,
        contractRootCount: contractRoots.length,
        contractDirectChildCount: contractRoot ? [...contractRoot.children].filter(visible).length : 0,
        nestedContractCount,
        unexpectedTaskPaneCount,
        actionCount,
        xor: (listCount === 1) !== (detailCount === 1),
        expectedMode: ((screen.dataset.screenMode === 'list' && listCount === 1 && detailCount === 0) || (screen.dataset.screenMode === 'detail' && detailCount === 1 && listCount === 0)) && visibleMainChildren.length === 1 && unmarkedMainChildCount === 0 && contractRoots.length === 1 && nestedContractCount === 0 && unexpectedTaskPaneCount === 0,
      }
    })

    const responsive = responsiveWidths.map((width) => {
      const scenario = document.querySelector(`[data-responsive-width="${width}"]`)
      const device = scenario?.querySelector('[data-responsive-proof]')
      return {
        requestedWidth: width,
        found: Boolean(device),
        ...(device ? rectOf(device) : { width: 0, height: 0 }),
        exactWidth: Boolean(device && Math.abs(device.getBoundingClientRect().width - width) < 0.01),
        overflowX: Boolean(device && device.scrollWidth > device.clientWidth + 1),
        overflowY: Boolean(device && device.scrollHeight > device.clientHeight + 1),
        listCount: device ? [...device.querySelectorAll('[data-master-list]')].filter(visible).length : 0,
        detailCount: device ? [...device.querySelectorAll('[data-detail-view]')].filter(visible).length : 0,
        navigationCount: device ? [...device.querySelectorAll('[data-shell-navigation]')].filter(visible).length : 0,
        actionableCountCount: device ? [...device.querySelectorAll('[data-actionable-count]')].filter(visible).length : 0,
      }
    })

    const desktopNode = document.querySelector('[data-desktop-proof]')
    const desktopWorkspace = desktopNode.querySelector('.desktop-workspace')
    const desktopLists = [...desktopNode.querySelectorAll('[data-master-list]')].filter(visible)
    const desktopDetails = [...desktopNode.querySelectorAll('[data-detail-view]')].filter(visible)
    const desktopListRect = desktopLists[0]?.getBoundingClientRect()
    const desktopDetailRect = desktopDetails[0]?.getBoundingClientRect()
    const desktopWorkspaceRect = desktopWorkspace.getBoundingClientRect()
    const selectedRow = desktopNode.querySelector('[data-selected-row]')
    const detailIdentity = desktopNode.querySelector('[data-detail-identity]')
    const desktopChildren = [...desktopWorkspace.children].filter(visible)
    const desktop = {
      ...rectOf(desktopNode),
      listCount: desktopLists.length,
      detailCount: desktopDetails.length,
      navigationCount: [...desktopNode.querySelectorAll('[data-shell-navigation]')].filter(visible).length,
      sidebarCount: [...desktopNode.querySelectorAll('[data-sidebar], .sidebar')].filter(visible).length,
      kpiCount: [...desktopNode.querySelectorAll('[data-kpi], .kpi')].filter(visible).length,
      extraPaneCount: desktopChildren.filter((element) => !element.matches('[data-master-list],[data-detail-view]')).length,
      sideBySide: Boolean(desktopListRect && desktopDetailRect && Math.abs(desktopListRect.top - desktopDetailRect.top) < 0.5 && (desktopListRect.right <= desktopDetailRect.left || desktopDetailRect.right <= desktopListRect.left) && inside(desktopListRect, desktopWorkspaceRect) && inside(desktopDetailRect, desktopWorkspaceRect)),
      selectedRowCount: selectedRow && visible(selectedRow) ? 1 : 0,
      sameIdentity: Boolean(selectedRow && detailIdentity && visible(selectedRow) && visible(detailIdentity) && normalize(selectedRow.querySelector('strong')?.textContent) === normalize(detailIdentity.textContent)),
      overflowX: desktopNode.scrollWidth > desktopNode.clientWidth + 1,
      overflowY: desktopNode.scrollHeight > desktopNode.clientHeight + 1,
    }

    const productText = normalize(surfaces.map(visibleText).join(' '))
    const forbiddenTerms = [
      'تعداد روابط',
      'تعداد رابطه',
      'کل روابط',
      'مسیر',
      'ابزار',
      'سرور',
      'بک‌اند',
      'backend',
      'home_server',
      'API',
      'نقش فعلی',
      'وضعیت دسترسی',
      'خلاصه دسترسی',
    ]
    const forbiddenTextChecks = forbiddenTerms.map((term) => ({ term, present: productText.toLocaleLowerCase('en-US').includes(term.toLocaleLowerCase('en-US')) }))

    const protectedWordViolations = []
    const protectedInteriorPhrases = ['ثبت آفر', 'آفر خرید', 'آفر فروش', 'قیمت خرید', 'قیمت فروش', 'سفارش خرید', 'سفارش فروش', 'پیام جدید', 'گفت‌وگو', 'چت', 'ارسال فایل', 'پیوست پیام']
    const protectedInteractivePhrases = [...protectedInteriorPhrases, 'ارسال پیام']
    for (const surface of surfaces) {
      const walker = document.createTreeWalker(surface, NodeFilter.SHOW_TEXT)
      let node
      while ((node = walker.nextNode())) {
        const text = normalize(node.textContent)
        if (!text || (!text.includes('بازار') && !text.includes('پیام‌رسان') && !protectedInteriorPhrases.some((phrase) => text.includes(phrase)))) continue
        const parent = node.parentElement
        if (!visible(parent)) continue
        if (!parent?.closest('[data-shell-navigation]')) {
          protectedWordViolations.push({ screen: surface.dataset.productScreen || surface.dataset.stateContract, text })
        }
      }
    }

    const protectedInteractiveViolations = actions
      .filter((action) => !action.insideShellNavigation)
      .filter((action) => protectedInteractivePhrases.some((phrase) => action.label.includes(phrase)))
      .map((action) => ({ screen: action.screen, label: action.label }))

    const countTextViolations = []
    for (const surface of surfaces) {
      const walker = document.createTreeWalker(surface, NodeFilter.SHOW_TEXT)
      let node
      while ((node = walker.nextNode())) {
        const text = normalize(node.textContent)
        if (!/[0-9۰-۹]+\s*(دعوت|مشتری|حسابدار|رابطه)/.test(text)) continue
        if (!visible(node.parentElement)) continue
        if (!node.parentElement?.closest('[data-actionable-count]')) {
          countTextViolations.push({ screen: surface.dataset.productScreen || surface.dataset.stateContract, text })
        }
      }
    }

    const fontNodes = surfaces.flatMap((surface) => [surface, ...surface.querySelectorAll('button, a, input, select, textarea, h3, h4, h5, p, strong, small, label, li, span')])
      .filter(visible)
    const fontViolations = fontNodes.map((element) => ({
      family: getComputedStyle(element).fontFamily,
      label: normalize(element.textContent || element.getAttribute('aria-label')).slice(0, 60),
    })).filter((item) => !item.family.includes('Vazirmatn Evidence'))

    const m01 = document.querySelector('[data-product-screen="M01"]')
    const m02 = document.querySelector('[data-product-screen="M02"]')
    const m03 = document.querySelector('[data-product-screen="M03"]')
    const m04 = document.querySelector('[data-product-screen="M04"]')
    const m05 = document.querySelector('[data-product-screen="M05"]')
    const m06 = document.querySelector('[data-product-screen="M06"]')
    const m07 = document.querySelector('[data-product-screen="M07"]')
    const m08 = document.querySelector('[data-product-screen="M08"]')
    const m09 = document.querySelector('[data-product-screen="M09"]')
    const m10 = document.querySelector('[data-product-screen="M10"]')

    const cascadeItems = [...m10.querySelectorAll('[data-cascade]')]
      .filter(visible)
      .map((item) => ({ key: item.dataset.cascade, text: visibleText(item) }))
    const cascadeKeys = cascadeItems.map((item) => item.key)
    const stateContracts = [...document.querySelectorAll('[data-state-contract]')].filter(visible).map((state) => ({
      type: state.dataset.stateContract,
      actionCount: [...state.querySelectorAll('button, a[href]')].filter(visible).length,
      queryCount: [...state.querySelectorAll('[data-search-query]')].filter(visible).length,
    }))

    return {
      productScreens,
      surfaceOverflow,
      actions: {
        count: actions.length,
        minimumWidth: Math.min(...actions.map((item) => item.width)),
        minimumHeight: Math.min(...actions.map((item) => item.height)),
        violations: actions.filter((item) => item.width < targetMin || item.height < targetMin || !item.insideSurface),
      },
      ctas: {
        count: ctas.length,
        minimumHeight: Math.min(...ctas.map((item) => item.height)),
        violations: ctas.filter((item) => item.height < ctaMin),
      },
      mobileScenarios,
      responsive,
      desktop,
      typography: {
        nodeCount: fontNodes.length,
        violations: fontViolations,
      },
      minimalism: {
        forbiddenTextChecks,
        countTextViolations,
        summaryCountCount: [...document.querySelectorAll('[data-product-surface] [data-summary-count]')].filter(visible).length,
        roleChipCount: [...document.querySelectorAll('[data-product-surface] .role-chip, [data-product-surface] [data-role-chip]')].filter(visible).length,
      },
      protectedScope: {
        marketInteriorCount: [...document.querySelectorAll('[data-product-surface] [data-market-interior]')].filter(visible).length,
        messengerInteriorCount: [...document.querySelectorAll('[data-product-surface] [data-messenger-interior]')].filter(visible).length,
        protectedWordViolations,
        protectedInteractiveViolations,
      },
      operations: {
        ownerActions: [...m01.querySelectorAll('[data-operation-action]')].filter(visible).map((item) => item.dataset.operationAction),
        adminActionCount: [...m02.querySelectorAll('[data-operation-action]')].filter(visible).length,
        accountantOwnerActionCount: [...m03.querySelectorAll('[data-operation-action]')].filter(visible).length,
        accountantFollowupCount: [...m03.querySelectorAll('[data-account-followup]')].filter(visible).length,
      },
      customer: {
        listActionableCount: [...m04.querySelectorAll('[data-actionable-count]')].filter(visible).length,
        relationRowCount: [...m04.querySelectorAll('.relation-row')].filter(visible).length,
        inviteDeadlineCount: [...m05.querySelectorAll('[data-invite-deadline]')].filter(visible).length,
        smsFailureCount: [...m05.querySelectorAll('[data-sms-failure]')].filter(visible).length,
        inviteCopyCount: [...m05.querySelectorAll('[data-copy-invite]')].filter(visible).length,
        futureOnlyCount: [...m06.querySelectorAll('[data-future-only]')].filter(visible).length,
        completedHistoryUnchanged: visibleText(m06).includes('سوابق نهایی‌شده تغییر نمی‌کند'),
        reviewCount: [...m06.querySelectorAll('[data-before-after-review]')].filter(visible).length,
        changeRowCount: [...m06.querySelectorAll('[data-change-row]')].filter(visible).length,
        beforeValueCount: [...m06.querySelectorAll('[data-before-value]')].filter(visible).length,
        afterValueCount: [...m06.querySelectorAll('[data-after-value]')].filter(visible).length,
        confirmCount: [...m06.querySelectorAll('[data-confirm-financial]')].filter(visible).length,
        returnToEditCount: [...m06.querySelectorAll('[data-return-financial-edit]')].filter(visible).length,
      },
      accountant: {
        listActionableCount: [...m07.querySelectorAll('[data-actionable-count]')].filter(visible).length,
        relationRowCount: [...m07.querySelectorAll('.relation-row')].filter(visible).length,
        dutyEditorCount: [...m08.querySelectorAll('[data-duty-editor]')].filter(visible).length,
        duplicateCurrentDutyCount: [...m08.querySelectorAll('[data-current-duty]')].filter(visible).length,
        inlineSuccessCount: [...m08.querySelectorAll('[data-inline-success]')].filter(visible).length,
      },
      session: {
        cardCount: [...m09.querySelectorAll('[data-session-card]')].filter(visible).length,
        confirmationCount: [...m09.querySelectorAll('[data-session-confirmation][role="dialog"][aria-modal="true"]')].filter(visible).length,
        confirmationActionCount: [...m09.querySelectorAll('[data-session-confirmation] button')].filter(visible).length,
        immediateEffectCount: [...m09.querySelectorAll('[data-session-immediate-effect]')].filter(visible).length,
        primaryStatusCount: [...m09.querySelectorAll('[data-session-primary]')].filter(visible).length,
        deviceFactCount: [...m09.querySelectorAll('[data-session-device]')].filter(visible).length,
        lastActivityCount: [...m09.querySelectorAll('[data-session-last-activity]')].filter(visible).length,
        homeServerCount: [...m09.querySelectorAll('[data-home-server]')].filter(visible).length,
        forbiddenHomeServerText: /home_server|سرور/i.test(visibleText(m09)),
      },
      deletion: {
        cascadeKeys,
        cascadeCopy: cascadeItems.map((item) => item.text),
        confirmationInputCount: [...m10.querySelectorAll('[data-delete-confirmation-input]')].filter(visible).length,
        confirmationValue: [...m10.querySelectorAll('[data-delete-confirmation-input]')].find(visible)?.value || '',
        acknowledgementCount: [...m10.querySelectorAll('[data-delete-acknowledgement][aria-pressed="true"]')].filter(visible).length,
        dangerActionCount: [...m10.querySelectorAll('[data-delete-account].danger-action.cta')].filter(visible).length,
      },
      states: stateContracts,
    }
  }, {
    responsiveWidths: RESPONSIVE_WIDTHS,
    targetMin: TARGET_MIN,
    ctaMin: CTA_MIN,
  })
}

function buildAssertions(metrics, font) {
  const expectedCascadeKeys = ['web-bot', 'sessions', 'offers', 'invites-relations']
  const expectedCascadeCopy = [
    'ورود وب‌اپ و بات غیرفعال می‌شود',
    'همه نشست‌ها پایان می‌یابد',
    'آفرهای فعال منقضی می‌شود',
    'دعوت‌های در انتظار و روابط وابسته بسته می‌شوند',
  ]
  const expectedActionCounts = { M01: 8, M02: 11, M03: 6, M04: 12, M05: 10, M06: 9, M07: 12, M08: 9, M09: 9, M10: 10 }
  const expectedDirectChildCounts = { M01: 2, M02: 5, M03: 2, M04: 5, M05: 4, M06: 5, M07: 5, M08: 3, M09: 2, M10: 5 }
  const actualScenarioIds = metrics.mobileScenarios.map((item) => item.id)
  const expectedStateTypes = ['loading', 'error', 'true-empty', 'filter-empty', 'missing-detail']
  const actualStateTypes = metrics.states.map((item) => item.type)
  const screensWithOverflow = metrics.productScreens.filter((item) => item.overflowX || item.overflowY || item.mainOverflowX || item.mainOverflowY || item.contentContracts.some((content) => !content.insideMain) || item.descendantBoundsViolations.length > 0 || item.clippedTextViolations.length > 0 || item.unmarkedMainChildCount > 0)
  const mobileStructureViolations = metrics.mobileScenarios.filter((item) => item.contractRootCount !== 1 || item.nestedContractCount !== 0 || item.unexpectedTaskPaneCount !== 0 || item.actionCount !== expectedActionCounts[item.id] || item.contractDirectChildCount !== expectedDirectChildCounts[item.id])

  return [
    { id: 'font-vazirmatn-loaded', passed: font.loaded && font.faces.length >= 4 && metrics.typography.violations.length === 0, detail: `${font.faces.length} faces; ${metrics.typography.violations.length} computed-family violation(s)` },
    { id: 'ten-mobile-scenarios-complete', passed: JSON.stringify(actualScenarioIds) === JSON.stringify(EXPECTED_SCENARIOS), detail: actualScenarioIds.join(', ') },
    { id: 'mobile-list-detail-xor', passed: metrics.mobileScenarios.every((item) => item.xor && item.expectedMode) && mobileStructureViolations.length === 0, detail: `${metrics.mobileScenarios.filter((item) => !item.xor || !item.expectedMode).length} mode violation(s); ${mobileStructureViolations.map((item) => `${item.id}[actions=${item.actionCount}/${expectedActionCounts[item.id]},children=${item.contractDirectChildCount}/${expectedDirectChildCounts[item.id]},nested=${item.nestedContractCount},unexpected=${item.unexpectedTaskPaneCount}]`).join('; ') || '0 content/action structure violation(s)'}` },
    { id: 'mobile-roots-exact-390x844', passed: metrics.mobileScenarios.every((item) => Math.abs(item.width - 390) < 0.01 && Math.abs(item.height - 844) < 0.01), detail: `${metrics.mobileScenarios.filter((item) => Math.abs(item.width - 390) >= 0.01 || Math.abs(item.height - 844) >= 0.01).length} violation(s)` },
    { id: 'no-product-overflow-or-clipping', passed: screensWithOverflow.length === 0 && metrics.surfaceOverflow.every((item) => !item.overflowX && !item.overflowY), detail: `${screensWithOverflow.length} screen violation(s)` },
    { id: 'touch-targets-44', passed: metrics.actions.count > 0 && metrics.actions.violations.length === 0, detail: `minimum ${metrics.actions.minimumWidth}×${metrics.actions.minimumHeight}px across ${metrics.actions.count}` },
    { id: 'cta-height-48', passed: metrics.ctas.count > 0 && metrics.ctas.violations.length === 0, detail: `minimum ${metrics.ctas.minimumHeight}px across ${metrics.ctas.count}` },
    { id: 'responsive-width-sweep', passed: metrics.responsive.length === 5 && metrics.responsive.every((item) => item.found && item.exactWidth && !item.overflowX && !item.overflowY && item.listCount === 1 && item.detailCount === 0 && item.navigationCount === 1 && item.actionableCountCount === 1), detail: metrics.responsive.map((item) => item.width).join(', ') },
    { id: 'desktop-true-master-detail-1440x900', passed: Math.abs(metrics.desktop.width - 1440) < 0.01 && Math.abs(metrics.desktop.height - 900) < 0.01 && metrics.desktop.listCount === 1 && metrics.desktop.detailCount === 1 && metrics.desktop.navigationCount === 1 && metrics.desktop.sidebarCount === 0 && metrics.desktop.kpiCount === 0 && metrics.desktop.extraPaneCount === 0 && metrics.desktop.sideBySide && metrics.desktop.selectedRowCount === 1 && metrics.desktop.sameIdentity && !metrics.desktop.overflowX && !metrics.desktop.overflowY, detail: `${metrics.desktop.width}×${metrics.desktop.height}; list=${metrics.desktop.listCount}; detail=${metrics.desktop.detailCount}; side-by-side=${metrics.desktop.sideBySide}; same-identity=${metrics.desktop.sameIdentity}` },
    { id: 'minimal-content-contract', passed: metrics.minimalism.forbiddenTextChecks.every((item) => !item.present) && metrics.minimalism.countTextViolations.length === 0 && metrics.minimalism.summaryCountCount === 0 && metrics.minimalism.roleChipCount === 0, detail: `${metrics.minimalism.forbiddenTextChecks.filter((item) => item.present).length} forbidden term(s); ${metrics.minimalism.countTextViolations.length} non-actionable count(s)` },
    { id: 'protected-interiors-absent', passed: metrics.protectedScope.marketInteriorCount === 0 && metrics.protectedScope.messengerInteriorCount === 0 && metrics.protectedScope.protectedWordViolations.length === 0 && metrics.protectedScope.protectedInteractiveViolations.length === 0, detail: `market=${metrics.protectedScope.marketInteriorCount}; messenger=${metrics.protectedScope.messengerInteriorCount}; text=${JSON.stringify(metrics.protectedScope.protectedWordViolations)}; interactive=${JSON.stringify(metrics.protectedScope.protectedInteractiveViolations)}` },
    { id: 'operations-role-destinations-bounded', passed: JSON.stringify(metrics.operations.ownerActions) === JSON.stringify(['customers', 'accountants']) && metrics.operations.adminActionCount === 5 && metrics.operations.accountantOwnerActionCount === 0 && metrics.operations.accountantFollowupCount === 1, detail: `owner=${metrics.operations.ownerActions.join(', ')}; admin=${metrics.operations.adminActionCount}; accountant-dead=${metrics.operations.accountantOwnerActionCount}` },
    { id: 'customer-flow-decision-complete', passed: metrics.customer.listActionableCount === 1 && metrics.customer.relationRowCount === 3 && metrics.customer.inviteDeadlineCount === 1 && metrics.customer.smsFailureCount === 1 && metrics.customer.inviteCopyCount === 2 && metrics.customer.futureOnlyCount === 1 && metrics.customer.completedHistoryUnchanged && metrics.customer.reviewCount === 1 && metrics.customer.changeRowCount === 4 && metrics.customer.beforeValueCount === 4 && metrics.customer.afterValueCount === 4 && metrics.customer.confirmCount === 1 && metrics.customer.returnToEditCount === 1, detail: `pending=${metrics.customer.listActionableCount}; rows=${metrics.customer.relationRowCount}; deadline=${metrics.customer.inviteDeadlineCount}; copies=${metrics.customer.inviteCopyCount}; review=${metrics.customer.changeRowCount} before/after row(s); future-only=${metrics.customer.futureOnlyCount}` },
    { id: 'accountant-flow-not-duplicated', passed: metrics.accountant.listActionableCount === 1 && metrics.accountant.relationRowCount === 3 && metrics.accountant.dutyEditorCount === 1 && metrics.accountant.duplicateCurrentDutyCount === 0 && metrics.accountant.inlineSuccessCount === 1, detail: `rows=${metrics.accountant.relationRowCount}; editor=${metrics.accountant.dutyEditorCount}; duplicate=${metrics.accountant.duplicateCurrentDutyCount}; success=${metrics.accountant.inlineSuccessCount}` },
    { id: 'session-decision-in-context', passed: metrics.session.cardCount === 1 && metrics.session.confirmationCount === 1 && metrics.session.confirmationActionCount === 2 && metrics.session.immediateEffectCount === 1 && metrics.session.primaryStatusCount === 1 && metrics.session.deviceFactCount === 1 && metrics.session.lastActivityCount === 1 && metrics.session.homeServerCount === 0 && !metrics.session.forbiddenHomeServerText, detail: `card=${metrics.session.cardCount}; dialog=${metrics.session.confirmationCount}; primary=${metrics.session.primaryStatusCount}; device=${metrics.session.deviceFactCount}; home-server=${metrics.session.homeServerCount}` },
    { id: 'maximum-risk-deletion-complete', passed: JSON.stringify(metrics.deletion.cascadeKeys) === JSON.stringify(expectedCascadeKeys) && JSON.stringify(metrics.deletion.cascadeCopy) === JSON.stringify(expectedCascadeCopy) && metrics.deletion.confirmationInputCount === 1 && metrics.deletion.confirmationValue === 'محمد همتی' && metrics.deletion.acknowledgementCount === 1 && metrics.deletion.dangerActionCount === 1, detail: `${metrics.deletion.cascadeKeys.join(', ')}; visible-copy=${metrics.deletion.cascadeCopy.length}; confirmation=${metrics.deletion.confirmationValue}` },
    { id: 'recovery-state-atlas-complete', passed: JSON.stringify(actualStateTypes) === JSON.stringify(expectedStateTypes) && metrics.states.find((item) => item.type === 'loading')?.actionCount === 0 && metrics.states.filter((item) => item.type !== 'loading').every((item) => item.actionCount === 1) && metrics.states.find((item) => item.type === 'filter-empty')?.queryCount === 1, detail: actualStateTypes.map((type) => `${type}:${metrics.states.find((item) => item.type === type).actionCount}${type === 'filter-empty' ? `/query=${metrics.states.find((item) => item.type === type).queryCount}` : ''}`).join(', ') },
  ]
}

function assertAssertionContract(assertions) {
  const assertionIds = assertions.map((item) => item.id)
  if (new Set(assertionIds).size !== assertionIds.length || JSON.stringify(assertionIds) !== JSON.stringify(EXPECTED_ASSERTION_IDS)) {
    throw new Error(`Assertion contract drifted. Expected ${EXPECTED_ASSERTION_IDS.join(', ')}; received ${assertionIds.join(', ')}`)
  }
}

async function main() {
  const htmlPath = path.join(__dirname, 'operations-workspaces-evidence.html')
  const assetsDir = path.join(__dirname, 'assets')
  fs.mkdirSync(assetsDir, { recursive: true })

  const localEvidenceDir = path.join(assetsDir, 'local-evidence')
  const staleBackups = fs.readdirSync(assetsDir)
    .filter((name) => name.startsWith('.local-evidence-backup-'))
    .map((name) => path.join(assetsDir, name))
  if (!fs.existsSync(localEvidenceDir) && staleBackups.length === 1) {
    fs.renameSync(staleBackups[0], localEvidenceDir)
  } else if (!fs.existsSync(localEvidenceDir) && staleBackups.length > 1) {
    throw new Error('Multiple interrupted local-evidence backups exist; refusing an ambiguous recovery')
  } else if (fs.existsSync(localEvidenceDir)) {
    for (const staleBackup of staleBackups) fs.rmSync(staleBackup, { recursive: true, force: true })
  }
  for (const name of fs.readdirSync(assetsDir).filter((item) => item.startsWith('.local-evidence-staging-'))) {
    fs.rmSync(path.join(assetsDir, name), { recursive: true, force: true })
  }

  const { module: playwright, source: playwrightSource } = resolvePlaywright()
  const { chromium } = playwright
  const fontRoot = findFontRoot()

  const runToken = `${process.pid}-${Date.now()}`
  const stagingDir = path.join(assetsDir, `.local-evidence-staging-${runToken}`)
  const backupDir = path.join(assetsDir, `.local-evidence-backup-${runToken}`)
  fs.mkdirSync(stagingDir)
  let browser = null
  let promoted = false
  const pageErrors = []

  try {
    browser = await chromium.launch({ headless: true })
    const page = await browser.newPage({ viewport: { width: 2400, height: 1800 }, deviceScaleFactor: 1 })
    page.on('pageerror', (error) => pageErrors.push(error.message))
    await page.goto(pathToFileURL(htmlPath).href, { waitUntil: 'load' })
    await page.addStyleTag({ content: buildEmbeddedFontCss(fontRoot) })
    await page.evaluate(async () => {
      await Promise.all([400, 500, 600, 700].map((weight) => document.fonts.load(`${weight} 16px "Vazirmatn Evidence"`)))
      await document.fonts.ready
    })
    await page.locator('[data-product-screen="M10"]').waitFor({ state: 'visible' })

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

    const preflightMeasurements = await measureEvidence(page)
    const preflightAssertions = buildAssertions(preflightMeasurements, font)
    assertAssertionContract(preflightAssertions)
    const preflightFailures = preflightAssertions.filter((item) => !item.passed)

    if (preflightFailures.length > 0 || pageErrors.length > 0) {
      throw new Error(`Evidence preflight failed: ${preflightFailures.map((item) => `${item.id} (${item.detail})`).join(', ') || pageErrors.join(', ')}`)
    }

    const captures = []
    for (const capture of CAPTURES) captures.push(await captureLocator(page, stagingDir, capture))

    if (pageErrors.length > 0) throw new Error(`Evidence capture failed: ${pageErrors.join(', ')}`)

    const measurements = await measureEvidence(page)
    const assertions = buildAssertions(measurements, font)
    assertAssertionContract(assertions)
    const failures = assertions.filter((item) => !item.passed)
    if (failures.length > 0 || pageErrors.length > 0) {
      throw new Error(`Evidence post-capture validation failed: ${failures.map((item) => `${item.id} (${item.detail})`).join(', ') || pageErrors.join(', ')}`)
    }

    const browserVersion = await browser.version()

    const report = {
      schemaVersion: 1,
      runId: runToken,
      evidenceRole: 'secondary-derivative-evidence',
      primaryDesignSource: 'Figma file z8jgJxST4O2APzWnlyP9gv',
      scope: 'Stage 0B-3 operations and customer/accountant workspaces; Market and Messenger interiors excluded',
      generatedAt: new Date().toISOString(),
      environment: {
        playwrightSource,
        browser: browserVersion,
        viewport: { width: 2400, height: 1800, deviceScaleFactor: 1 },
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

    if (!report.summary.passed) throw new Error('Evidence validation failed before promotion')

    const metricsFilename = 'local-workspaces-validation-metrics.json'
    fs.writeFileSync(path.join(stagingDir, metricsFilename), `${JSON.stringify(report, null, 2)}\n`)

    const stagedFiles = [...captures.map((item) => item.filename), metricsFilename]
    if (!stagedFiles.every((filename) => fs.existsSync(path.join(stagingDir, filename)))) {
      throw new Error('Evidence staging is incomplete; no output was promoted')
    }

    for (const capture of captures) {
      const stagedHash = crypto.createHash('sha256').update(fs.readFileSync(path.join(stagingDir, capture.filename))).digest('hex')
      if (stagedHash !== capture.sha256) throw new Error(`Evidence hash drifted before promotion: ${capture.filename}`)
    }

    await browser.close()
    browser = null

    if (fs.existsSync(localEvidenceDir)) fs.renameSync(localEvidenceDir, backupDir)
    fs.renameSync(stagingDir, localEvidenceDir)
    promoted = true
    try {
      if (fs.existsSync(backupDir)) fs.rmSync(backupDir, { recursive: true, force: true })
    } catch {}

    try {
      process.stdout.write(`${JSON.stringify({
        passed: true,
        captures: captures.map((item) => `${item.filename} (${item.pixelDimensions.width}×${item.pixelDimensions.height})`),
        mobileScenarios: measurements.mobileScenarios.map((item) => item.id),
        responsiveWidths: measurements.responsive.map((item) => item.width),
        desktop: `${measurements.desktop.width}×${measurements.desktop.height}`,
        minimumTarget: `${measurements.actions.minimumWidth}×${measurements.actions.minimumHeight}`,
        minimumCtaHeight: measurements.ctas.minimumHeight,
        fontFaces: font.faces.length,
        assertionCount: assertions.length,
      }, null, 2)}\n`)
    } catch {}
  } finally {
    if (browser) {
      try { await browser.close() } catch {}
    }
    try { fs.rmSync(stagingDir, { recursive: true, force: true }) } catch {}
    try {
      if (!promoted && !fs.existsSync(localEvidenceDir) && fs.existsSync(backupDir)) {
        fs.renameSync(backupDir, localEvidenceDir)
      } else if (promoted && fs.existsSync(backupDir)) {
        fs.rmSync(backupDir, { recursive: true, force: true })
      }
    } catch {}
  }
}

main().catch((error) => {
  console.error(error.stack || error.message)
  process.exitCode = 1
})
