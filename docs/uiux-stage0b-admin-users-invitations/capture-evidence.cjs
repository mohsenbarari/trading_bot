const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')
const { pathToFileURL } = require('node:url')

const RESPONSIVE_WIDTHS = [360, 375, 390, 414, 430]
const EXPECTED_SCENARIOS = ['M01', 'M02', 'M03', 'M04', 'M05', 'M06', 'M07', 'M08', 'M09', 'M10']
const EXPECTED_STATE_TYPES = ['loading', 'load-error', 'true-empty', 'search-empty', 'missing-detail', 'permission-protected', 'busy', 'success', 'failure', 'clipboard-failure', 'invite-not-pending']
const EXPECTED_PROTECTED_ACTIONS = ['role', 'restriction', 'quotas', 'deactivate', 'terminate-sessions', 'delete']
const EXPECTED_ACTION_COUNTS = { M01: 7, M02: 10, M03: 11, M04: 9, M05: 12, M06: 9, M07: 11, M08: 11, M09: 9, M10: 11 }
const EXPECTED_DIRECT_CHILD_COUNTS = { M01: 2, M02: 4, M03: 3, M04: 3, M05: 4, M06: 4, M07: 4, M08: 5, M09: 4, M10: 4 }
const TARGET_MIN = 44
const CTA_MIN = 48
const METRICS_FILENAME = 'local-admin-users-invitations-validation-metrics.json'
const EXPECTED_ASSERTION_IDS = [
  'font-vazirmatn-loaded',
  'ten-mobile-scenarios-complete',
  'mobile-roots-exact-390x844',
  'mobile-list-detail-xor',
  'no-product-overflow-or-clipping',
  'touch-targets-44',
  'cta-height-48',
  'responsive-width-sweep',
  'desktop-user-master-detail-1440x900',
  'minimal-content-contract',
  'admin-landing-action-focused',
  'user-directory-metadata-bounded',
  'user-filters-bounded-actionable',
  'role-permission-matrix-exact',
  'user-detail-progressive-disclosure',
  'sensitive-decision-review-complete',
  'account-deactivation-consequences-truthful',
  'action-feedback-in-context',
  'standard-invitation-form-bounded',
  'standard-invitation-result-truthful',
  'pending-invitation-queue-actionable',
  'invitation-revoke-confirmation-complete',
  'raw-invitation-urls-hidden-by-default',
  'recovery-state-atlas-complete',
  'protected-interiors-absent',
]

const CAPTURES = [
  { selector: '#admin-entry-directory', filename: 'local-admin-entry-user-directory.png' },
  { selector: '#user-decision-flow-capture', filename: 'local-user-decision-flow.png' },
  { selector: '#standard-invitations', filename: 'local-standard-invitation-flow.png' },
  { selector: '#admin-state-atlas', filename: 'local-admin-users-state-atlas.png' },
  { selector: '#admin-permission-matrix', filename: 'local-admin-users-permission-matrix.png' },
  { selector: '#admin-users-responsive-sweep', filename: 'local-admin-users-responsive-sweep.png' },
  { selector: '#desktop-admin-user-master-detail [data-desktop-proof]', filename: 'local-admin-user-master-detail-1440x900.png', exactSize: { width: 1440, height: 900 } },
]

const EXPECTED_OUTPUT_FILES = [...CAPTURES.map((item) => item.filename), METRICS_FILENAME].sort()

function hashBuffer(buffer) {
  return crypto.createHash('sha256').update(buffer).digest('hex')
}

function hashFile(filePath) {
  return hashBuffer(fs.readFileSync(filePath))
}

function readPngDimensions(filePath) {
  const buffer = fs.readFileSync(filePath)
  if (buffer.length < 24 || buffer.subarray(0, 8).toString('hex') !== '89504e470d0a1a0a') {
    throw new Error(`Expected a PNG file at ${filePath}`)
  }
  return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) }
}

function validateEvidenceDirectory(directory) {
  const problems = []
  if (!fs.existsSync(directory) || !fs.statSync(directory).isDirectory()) {
    return { valid: false, problems: ['directory is absent'] }
  }

  const files = fs.readdirSync(directory).filter((name) => !name.startsWith('.')).sort()
  if (JSON.stringify(files) !== JSON.stringify(EXPECTED_OUTPUT_FILES)) {
    problems.push(`file set drift: ${files.join(', ')}`)
  }

  const metricsPath = path.join(directory, METRICS_FILENAME)
  let report = null
  try {
    report = JSON.parse(fs.readFileSync(metricsPath, 'utf8'))
  } catch (error) {
    problems.push(`metrics unreadable: ${error.message}`)
  }

  if (report) {
    const assertionIds = Array.isArray(report.assertions) ? report.assertions.map((item) => item.id) : []
    if (JSON.stringify(assertionIds) !== JSON.stringify(EXPECTED_ASSERTION_IDS)) problems.push('assertion id set/order drift')
    if (report.summary?.passed !== true || report.summary?.failureCount !== 0 || report.summary?.pageErrorCount !== 0) problems.push('report is not a clean pass')
    const captureNames = Array.isArray(report.captures) ? report.captures.map((item) => item.filename).sort() : []
    const expectedCaptureNames = CAPTURES.map((item) => item.filename).sort()
    if (JSON.stringify(captureNames) !== JSON.stringify(expectedCaptureNames)) problems.push('capture set drift')
    for (const capture of report.captures || []) {
      const capturePath = path.join(directory, capture.filename)
      try {
        if (hashFile(capturePath) !== capture.sha256) problems.push(`hash mismatch: ${capture.filename}`)
        const dimensions = readPngDimensions(capturePath)
        if (dimensions.width !== capture.pixelDimensions?.width || dimensions.height !== capture.pixelDimensions?.height) problems.push(`dimension mismatch: ${capture.filename}`)
      } catch (error) {
        problems.push(`${capture.filename}: ${error.message}`)
      }
    }
  }

  return { valid: problems.length === 0, problems, report }
}

function recoverEvidenceBeforeDependencies(assetsDir, localEvidenceDir) {
  fs.mkdirSync(assetsDir, { recursive: true })
  for (const name of fs.readdirSync(assetsDir).filter((item) => item.startsWith('.local-evidence-staging-'))) {
    fs.rmSync(path.join(assetsDir, name), { recursive: true, force: true })
  }

  let backups = fs.readdirSync(assetsDir)
    .filter((name) => name.startsWith('.local-evidence-backup-'))
    .map((name) => path.join(assetsDir, name))
    .sort()
  if (backups.length > 1) throw new Error('Multiple interrupted local-evidence backups exist; refusing ambiguous recovery')

  if (fs.existsSync(localEvidenceDir) && fs.readdirSync(localEvidenceDir).length === 0) {
    fs.rmSync(localEvidenceDir, { recursive: true, force: true })
  }

  if (!fs.existsSync(localEvidenceDir) && backups.length === 1) {
    const backupValidation = validateEvidenceDirectory(backups[0])
    if (!backupValidation.valid) throw new Error(`Interrupted backup is invalid: ${backupValidation.problems.join('; ')}`)
    fs.renameSync(backups[0], localEvidenceDir)
    backups = []
  } else if (fs.existsSync(localEvidenceDir) && backups.length === 1) {
    const currentValidation = validateEvidenceDirectory(localEvidenceDir)
    const backupValidation = validateEvidenceDirectory(backups[0])
    if (currentValidation.valid) {
      fs.rmSync(backups[0], { recursive: true, force: true })
      backups = []
    } else if (backupValidation.valid) {
      fs.rmSync(localEvidenceDir, { recursive: true, force: true })
      fs.renameSync(backups[0], localEvidenceDir)
      backups = []
    } else {
      throw new Error(`Neither interrupted publication is valid. Current: ${currentValidation.problems.join('; ')}. Backup: ${backupValidation.problems.join('; ')}`)
    }
  }

  if (fs.existsSync(localEvidenceDir)) {
    const currentValidation = validateEvidenceDirectory(localEvidenceDir)
    if (!currentValidation.valid) throw new Error(`Published local evidence is incomplete; refusing overwrite: ${currentValidation.problems.join('; ')}`)
  }
}

function resolvePlaywright() {
  const candidates = [
    process.env.UIUX_PLAYWRIGHT_MODULE,
    'playwright',
    path.resolve(__dirname, '../../frontend/node_modules/playwright'),
    '/root/trading-bot/trading_bot/frontend/node_modules/playwright',
  ].filter(Boolean)
  const failures = []
  for (const candidate of candidates) {
    try { return { module: require(candidate), source: candidate } } catch (error) { failures.push(`${candidate}: ${error.code || error.message}`) }
  }
  throw new Error(`Playwright is unavailable. Tried:\n${failures.join('\n')}`)
}

function resolveSharp() {
  const candidates = [
    process.env.UIUX_SHARP_MODULE,
    'sharp',
    path.resolve(__dirname, '../../frontend/node_modules/sharp'),
    '/root/trading-bot/trading_bot/frontend/node_modules/sharp',
  ].filter(Boolean)
  const failures = []
  for (const candidate of candidates) {
    try { return require(candidate) } catch (error) { failures.push(`${candidate}: ${error.code || error.message}`) }
  }
  throw new Error(`Sharp is required only when an exact evidence crop is necessary. Tried:\n${failures.join('\n')}`)
}

function findFontRoot() {
  const candidates = [
    process.env.UIUX_VAZIRMATN_FONT_ROOT,
    path.resolve(__dirname, '../../frontend/node_modules/vazirmatn/fonts/webfonts'),
    '/root/trading-bot/trading_bot/frontend/node_modules/vazirmatn/fonts/webfonts',
  ].filter(Boolean)
  const required = ['Vazirmatn-Regular.woff2', 'Vazirmatn-Medium.woff2', 'Vazirmatn-SemiBold.woff2', 'Vazirmatn-Bold.woff2']
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

async function canonicalDomSnapshot(page) {
  return page.evaluate(() => {
    const clone = document.documentElement.cloneNode(true)
    clone.querySelectorAll('[style]').forEach((element) => {
      if (!(element.getAttribute('style') || '').trim()) element.removeAttribute('style')
    })
    return `<!doctype html>\n${clone.outerHTML}`
  })
}

async function captureLocator(page, stagingDir, capture) {
  const locator = page.locator(capture.selector)
  const count = await locator.count()
  if (count !== 1) throw new Error(`Expected one ${capture.selector}; found ${count}`)
  if (!await locator.isVisible()) throw new Error(`${capture.selector} is not visible`)

  const destination = path.join(stagingDir, capture.filename)
  const temporary = capture.exactSize ? `${destination}.raw.png` : destination
  await locator.screenshot({ path: temporary, animations: 'disabled' })
  const rawDimensions = readPngDimensions(temporary)

  if (capture.exactSize && (rawDimensions.width !== capture.exactSize.width || rawDimensions.height !== capture.exactSize.height)) {
    if (rawDimensions.width < capture.exactSize.width || rawDimensions.height < capture.exactSize.height) {
      throw new Error(`${capture.selector} rendered ${rawDimensions.width}×${rawDimensions.height}; cannot crop to ${capture.exactSize.width}×${capture.exactSize.height}`)
    }
    const sharp = resolveSharp()
    await sharp(temporary).extract({
      left: Math.floor((rawDimensions.width - capture.exactSize.width) / 2),
      top: Math.floor((rawDimensions.height - capture.exactSize.height) / 2),
      width: capture.exactSize.width,
      height: capture.exactSize.height,
    }).toFile(destination)
    fs.rmSync(temporary)
  } else if (capture.exactSize) {
    fs.renameSync(temporary, destination)
  }

  const pixelDimensions = readPngDimensions(destination)
  if (capture.exactSize && (pixelDimensions.width !== capture.exactSize.width || pixelDimensions.height !== capture.exactSize.height)) {
    throw new Error(`${capture.filename} is ${pixelDimensions.width}×${pixelDimensions.height}; expected ${capture.exactSize.width}×${capture.exactSize.height}`)
  }
  return { selector: capture.selector, filename: capture.filename, pixelDimensions, sha256: hashFile(destination) }
}

async function measureEvidence(page) {
  return page.evaluate(({ responsiveWidths, targetMin, ctaMin, expectedStateTypes }) => {
    const normalize = (value) => (value || '').replace(/\s+/g, ' ').trim()
    const rawRect = (element) => {
      const rect = element.getBoundingClientRect()
      return { width: rect.width, height: rect.height, x: rect.x + window.scrollX, y: rect.y + window.scrollY, right: rect.right + window.scrollX, bottom: rect.bottom + window.scrollY }
    }
    const roundedRect = (element) => Object.fromEntries(Object.entries(rawRect(element)).map(([key, value]) => [key, Number(value.toFixed(3))]))
    const inside = (inner, outer, tolerance = 1.1) => inner.left >= outer.left - tolerance && inner.top >= outer.top - tolerance && inner.right <= outer.right + tolerance && inner.bottom <= outer.bottom + tolerance
    const visibilityOf = (element) => {
      const reasons = []
      if (!element || !(element instanceof Element)) return { visible: false, reasons: ['missing element'] }
      if (element.hidden) reasons.push('hidden attribute')
      const rect = element.getBoundingClientRect()
      if (rect.width <= 0 || rect.height <= 0 || element.getClientRects().length === 0) reasons.push('zero geometry')
      for (let current = element; current; current = current.parentElement) {
        const style = getComputedStyle(current)
        if (style.display === 'none') reasons.push(`${current.tagName}:display-none`)
        if (style.visibility === 'hidden' || style.visibility === 'collapse') reasons.push(`${current.tagName}:visibility-hidden`)
        if (Number.parseFloat(style.opacity) <= 0.01) reasons.push(`${current.tagName}:opacity-zero`)
        if (current !== element && ['hidden', 'clip', 'auto', 'scroll'].includes(style.overflowX)) {
          const ancestorRect = current.getBoundingClientRect()
          if (rect.left < ancestorRect.left - 1.1 || rect.right > ancestorRect.right + 1.1) reasons.push(`${current.tagName}:clipped-x`)
        }
        if (current !== element && ['hidden', 'clip', 'auto', 'scroll'].includes(style.overflowY)) {
          const ancestorRect = current.getBoundingClientRect()
          if (rect.top < ancestorRect.top - 1.1 || rect.bottom > ancestorRect.bottom + 1.1) reasons.push(`${current.tagName}:clipped-y`)
        }
      }
      return { visible: reasons.length === 0, reasons: [...new Set(reasons)] }
    }
    const visible = (element) => visibilityOf(element).visible
    const visibleText = (root) => {
      const fragments = []
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
      let node
      while ((node = walker.nextNode())) {
        if (visible(node.parentElement)) fragments.push(normalize(node.textContent))
      }
      return normalize(fragments.filter(Boolean).join(' '))
    }
    const visibleLiteralOccurrences = (root, literal) => {
      let count = 0
      const countIn = (value) => {
        let offset = 0
        while ((offset = String(value || '').indexOf(literal, offset)) !== -1) {
          count += 1
          offset += literal.length
        }
      }
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
      let node
      while ((node = walker.nextNode())) {
        if (visible(node.parentElement)) countIn(node.textContent)
      }
      for (const field of visibleElements(root, 'input,textarea,select')) countIn(field.value)
      return count
    }
    const visibleElements = (root, selector) => [...root.querySelectorAll(selector)].filter(visible)
    const actionSelector = 'button,a[href],input,select,textarea'
    const describeAction = (element, index) => {
      const surface = element.closest('[data-product-surface]')
      const visibility = visibilityOf(element)
      return {
        id: element.id || `${element.closest('[data-product-screen]')?.dataset.productScreen || element.closest('[data-state-contract]')?.dataset.stateContract || 'surface'}:${element.tagName.toLowerCase()}:${index}`,
        screen: element.closest('[data-product-screen]')?.dataset.productScreen || null,
        label: normalize(element.getAttribute('aria-label') || element.textContent || element.value).slice(0, 140),
        tag: element.tagName.toLowerCase(),
        disabled: Boolean(element.disabled),
        insideShellNavigation: Boolean(element.closest('[data-shell-navigation]')),
        insideSurface: Boolean(surface && inside(element.getBoundingClientRect(), surface.getBoundingClientRect())),
        ancestorVisibilityReasons: visibility.reasons,
        raw: rawRect(element),
        rounded: roundedRect(element),
      }
    }

    const surfaces = [...document.querySelectorAll('[data-product-surface]')]
    const surfaceGeometry = surfaces.map((surface, index) => {
      const surfaceRect = surface.getBoundingClientRect()
      const descendantBoundsViolations = [...surface.querySelectorAll('*')].filter(visible).filter((element) => !inside(element.getBoundingClientRect(), surfaceRect)).map((element) => ({ tag: element.tagName.toLowerCase(), className: element.className?.baseVal || element.className || '', text: normalize(element.textContent).slice(0, 80), raw: rawRect(element) }))
      const clippedTextViolations = [...surface.querySelectorAll('h1,h2,h3,h4,h5,p,strong,small,label,li,span,button,a')].filter(visible).filter((element) => {
        const style = getComputedStyle(element)
        if (style.textOverflow === 'ellipsis') return false
        if (element.tagName === 'SPAN' && element.closest('[data-shell-navigation]')) return false
        return element.scrollWidth > element.clientWidth + 0.5 || element.scrollHeight > element.clientHeight + 0.5
      }).map((element) => ({ tag: element.tagName.toLowerCase(), text: normalize(element.textContent).slice(0, 90) }))
      return {
        id: surface.dataset.productScreen || surface.dataset.stateContract || (surface.hasAttribute('data-responsive-proof') ? `responsive-${index}` : surface.hasAttribute('data-desktop-proof') ? 'desktop' : `surface-${index}`),
        raw: rawRect(surface),
        rounded: roundedRect(surface),
        overflowX: surface.scrollWidth > surface.clientWidth + 1,
        overflowY: surface.scrollHeight > surface.clientHeight + 1,
        descendantBoundsViolations,
        clippedTextViolations,
      }
    })

    const actions = surfaces.flatMap((surface) => [...surface.querySelectorAll(actionSelector)].filter(visible)).map(describeAction)
    const ctas = surfaces.flatMap((surface) => [...surface.querySelectorAll('.cta')].filter(visible)).map(describeAction)

    const mobileScenarios = [...document.querySelectorAll('[data-mobile-scenario]')].map((screen) => {
      const main = screen.querySelector('.screen-content')
      const visibleMainChildren = [...main.children].filter(visible)
      const contractRoots = visibleElements(screen, '[data-contract-content]')
      const contractRoot = contractRoots[0]
      const lists = visibleElements(screen, '[data-master-list]')
      const details = visibleElements(screen, '[data-detail-view]')
      const nestedContractCount = contractRoot ? visibleElements(contractRoot, '[data-contract-content],[data-master-list],[data-detail-view]').length : 0
      const unmarkedMainChildCount = visibleMainChildren.filter((item) => !item.matches('[data-contract-content][data-master-list],[data-contract-content][data-detail-view]')).length
      const unexpectedTaskPaneCount = contractRoot ? visibleElements(contractRoot, '[data-task-pane],[data-unmarked-pane],[role="dialog"],.drawer,.sheet,.modal,.task-pane').length : 0
      const unmarkedInlinePanelCount = contractRoot ? visibleElements(contractRoot, '.card,.decision-panel,.confirmation-panel,.session-control,.queue-context').filter((item) => !item.hasAttribute('data-inline-section')).length : 0
      const actionCount = visibleElements(screen, actionSelector).length
      return {
        id: screen.dataset.productScreen,
        mode: screen.dataset.screenMode,
        raw: rawRect(screen),
        rounded: roundedRect(screen),
        listCount: lists.length,
        detailCount: details.length,
        xor: (lists.length === 1) !== (details.length === 1),
        visibleMainChildCount: visibleMainChildren.length,
        unmarkedMainChildCount,
        contractRootCount: contractRoots.length,
        contractDirectChildCount: contractRoot ? [...contractRoot.children].filter(visible).length : 0,
        nestedContractCount,
        unexpectedTaskPaneCount,
        unmarkedInlinePanelCount,
        actionCount,
        mainOverflowX: main.scrollWidth > main.clientWidth + 1,
        mainOverflowY: main.scrollHeight > main.clientHeight + 1,
      }
    })

    const responsive = responsiveWidths.map((width) => {
      const wrapper = document.querySelector(`[data-responsive-width="${width}"]`)
      const device = wrapper?.querySelector('[data-responsive-proof]')
      return {
        requestedWidth: width,
        found: Boolean(device),
        ...(device ? { raw: rawRect(device), rounded: roundedRect(device) } : { raw: null, rounded: null }),
        exactWidth: Boolean(device && Math.abs(device.getBoundingClientRect().width - width) < 0.01),
        exactHeight: Boolean(device && Math.abs(device.getBoundingClientRect().height - 844) < 0.01),
        overflowX: Boolean(device && device.scrollWidth > device.clientWidth + 1),
        overflowY: Boolean(device && device.scrollHeight > device.clientHeight + 1),
        listCount: device ? visibleElements(device, '[data-master-list]').length : 0,
        detailCount: device ? visibleElements(device, '[data-detail-view]').length : 0,
        navigationCount: device ? visibleElements(device, '[data-shell-navigation]').length : 0,
        searchCount: device ? visibleElements(device, '[data-user-search][data-search-persistent]').length : 0,
        filterCount: device ? visibleElements(device, '[data-user-filter]').length : 0,
        actionCount: device ? visibleElements(device, actionSelector).length : 0,
        rowIdentities: device ? visibleElements(device, '[data-user-row] strong').map((item) => normalize(item.textContent)) : [],
      }
    })

    const desktopNode = document.querySelector('[data-desktop-proof]')
    const desktopWorkspace = desktopNode.querySelector('.desktop-workspace')
    const desktopLists = visibleElements(desktopNode, '[data-master-list]')
    const desktopDetails = visibleElements(desktopNode, '[data-detail-view]')
    const desktopChildren = [...desktopWorkspace.children].filter(visible)
    const desktopListRect = desktopLists[0]?.getBoundingClientRect()
    const desktopDetailRect = desktopDetails[0]?.getBoundingClientRect()
    const desktopWorkspaceRect = desktopWorkspace.getBoundingClientRect()
    const selectedRow = desktopNode.querySelector('[data-selected-row]')
    const detailIdentity = desktopNode.querySelector('[data-detail-identity]')
    const desktop = {
      raw: rawRect(desktopNode),
      rounded: roundedRect(desktopNode),
      listCount: desktopLists.length,
      detailCount: desktopDetails.length,
      navigationCount: visibleElements(desktopNode, '[data-shell-navigation]').length,
      sidebarCount: visibleElements(desktopNode, '[data-sidebar],.sidebar').length,
      kpiCount: visibleElements(desktopNode, '[data-kpi],.kpi').length,
      summaryCountCount: visibleElements(desktopNode, '[data-summary-count]').length,
      currentAdminMetadataCount: visibleElements(desktopNode, '[data-current-admin-metadata],.desktop-account').length,
      extraPaneCount: desktopChildren.filter((element) => !element.matches('[data-master-list],[data-detail-view]')).length,
      sideBySide: Boolean(desktopListRect && desktopDetailRect && Math.abs(desktopListRect.top - desktopDetailRect.top) < 0.5 && (desktopListRect.right <= desktopDetailRect.left || desktopDetailRect.right <= desktopListRect.left) && inside(desktopListRect, desktopWorkspaceRect) && inside(desktopDetailRect, desktopWorkspaceRect)),
      selectedRowCount: selectedRow && visible(selectedRow) ? 1 : 0,
      sameIdentity: Boolean(selectedRow && detailIdentity && visible(selectedRow) && visible(detailIdentity) && normalize(selectedRow.querySelector('strong')?.textContent) === normalize(detailIdentity.textContent)),
      rowIdentities: visibleElements(desktopNode, '[data-master-list] [data-user-row] strong').map((item) => normalize(item.textContent)),
      actionLabels: visibleElements(desktopNode, '.desktop-action-list button').map((item) => normalize(item.textContent)),
      detailText: visibleText(desktopDetails[0]),
      detailBodyChildCount: visibleElements(desktopDetails[0], '.desktop-detail-body > *').length,
      overflowX: desktopNode.scrollWidth > desktopNode.clientWidth + 1,
      overflowY: desktopNode.scrollHeight > desktopNode.clientHeight + 1,
    }

    const bodyTextAndValues = normalize(`${document.body.innerText} ${[...document.querySelectorAll('input,textarea')].map((item) => item.value).join(' ')}`)
    const productText = normalize(surfaces.map(visibleText).join(' '))
    const forbiddenTerms = ['تعداد روابط', 'تعداد رابطه', 'کل روابط', 'کل کاربران', 'مجموع کاربران', 'مسیر', 'ابزار', 'سرور', 'بک‌اند', 'backend', 'home_server', 'API', 'نقش فعلی', 'وضعیت دسترسی', 'خلاصه دسترسی']
    const forbiddenTextChecks = forbiddenTerms.map((term) => ({ term, present: productText.toLocaleLowerCase('en-US').includes(term.toLocaleLowerCase('en-US')) }))
    const obsoleteCanonicalTerms = ['۱۴ مرداد', '۱۶ مرداد', '۲۳ مرداد', 'ساعت ۱۸:۰۰', 'ساعت ۱۲:۳۰', 'گرم', 'حجم معامله', 'درخواست لفظ کانال']
    const obsoleteCanonicalViolations = obsoleteCanonicalTerms.filter((term) => productText.includes(term))
    const knownIdentityTerms = ['محمدعلی همتی', 'امین تکبیری', 'مریم حسینی', 'mohammad.hemmati', 'amin.takbiri', 'maryam.hosseini', '0912 345 6789', '0935 642 1180', '0991 720 4635']
    const syntheticDataViolations = knownIdentityTerms.filter((term) => bodyTextAndValues.toLocaleLowerCase('en-US').includes(term.toLocaleLowerCase('en-US')))
    const numericPendingViolations = []
    for (const surface of surfaces) {
      const walker = document.createTreeWalker(surface, NodeFilter.SHOW_TEXT)
      let node
      while ((node = walker.nextNode())) {
        const text = normalize(node.textContent)
        if (visible(node.parentElement) && /[0-9۰-۹]+\s*(دعوت(?:‌نامه)?(?:‌های)?\s*(?:فعال|در انتظار)|دعوت در انتظار)/.test(text)) numericPendingViolations.push(text)
      }
    }

    const rawUrlViolations = []
    for (const element of surfaces.flatMap((surface) => [surface, ...surface.querySelectorAll('*')]).filter(visible)) {
      const candidate = normalize(`${element.childElementCount === 0 ? element.textContent : ''} ${'value' in element ? element.value : ''}`)
      if (/(?:https?:\/\/|tg:\/\/|t\.me\/|\bINV-[A-Za-z0-9_-]+|\/invite\b)/i.test(candidate)) rawUrlViolations.push({ tag: element.tagName.toLowerCase(), text: candidate.slice(0, 150) })
    }

    const protectedTextViolations = []
    const marketInteriorPhrases = ['ثبت آفر', 'آفر خرید', 'آفر فروش', 'قیمت خرید', 'قیمت فروش', 'سفارش خرید', 'سفارش فروش']
    const messengerInteriorPhrases = ['پیام جدید', 'گفت‌وگو', 'گفتگو', 'چت', 'ارسال فایل', 'پیوست پیام', 'نوشتن پیام', 'مکالمه']
    for (const surface of surfaces) {
      const walker = document.createTreeWalker(surface, NodeFilter.SHOW_TEXT)
      let node
      while ((node = walker.nextNode())) {
        const text = normalize(node.textContent)
        const parent = node.parentElement
        if (!text || !visible(parent)) continue
        if (marketInteriorPhrases.some((phrase) => text.includes(phrase)) || messengerInteriorPhrases.some((phrase) => text.includes(phrase))) {
          protectedTextViolations.push({ kind: 'interior-lexicon', text, surface: surface.dataset.productScreen || surface.dataset.stateContract || 'other' })
        }
        if (text.includes('بازار') && !parent.closest('[data-shell-navigation]') && !parent.closest('[data-protected-reference="market-consequence"]')) {
          protectedTextViolations.push({ kind: 'market-reference', text, surface: surface.dataset.productScreen || surface.dataset.stateContract || 'other' })
        }
        if (text.includes('پیام‌رسان') && !parent.closest('[data-shell-navigation]') && !parent.closest('[data-protected-reference="messenger-consequence"]')) {
          protectedTextViolations.push({ kind: 'messenger-reference', text, surface: surface.dataset.productScreen || surface.dataset.stateContract || 'other' })
        }
      }
    }
    const protectedInteractiveViolations = actions.filter((action) => {
      const protectedLabel = action.label.includes('بازار') || action.label.includes('پیام‌رسان') || marketInteriorPhrases.some((phrase) => action.label.includes(phrase)) || messengerInteriorPhrases.some((phrase) => action.label.includes(phrase))
      if (!protectedLabel) return false
      return !(action.insideShellNavigation && ['بازار', 'پیام‌رسان'].includes(action.label))
    }).map((action) => ({ screen: action.screen, label: action.label }))
    const navigationContracts = visibleElements(document, '[data-mobile-scenario] [data-shell-navigation], [data-responsive-proof] [data-shell-navigation], [data-desktop-proof] [data-shell-navigation]').map((navigation) => {
      const links = visibleElements(navigation, 'a[data-nav-destination]')
      return {
        context: navigation.closest('[data-mobile-scenario]') ? 'mobile-root' : navigation.closest('[data-responsive-proof]') ? 'responsive' : 'desktop',
        navigationHeight: navigation.getBoundingClientRect().height,
        destinations: links.map((item) => item.dataset.navDestination),
        labels: links.map((item) => normalize(item.getAttribute('aria-label') || item.textContent)),
        activeDestinations: links.filter((item) => item.classList.contains('is-active')).map((item) => item.dataset.navDestination),
        svgCount: visibleElements(navigation, 'a[data-nav-destination] svg').length,
        labelBoundsViolationCount: links.filter((item) => {
          const label = item.querySelector('span')
          return !label || !inside(label.getBoundingClientRect(), item.getBoundingClientRect()) || item.scrollWidth > item.clientWidth + 1 || item.scrollHeight > item.clientHeight + 1
        }).length,
        minimumWidth: Math.min(...links.map((item) => item.getBoundingClientRect().width)),
        minimumHeight: Math.min(...links.map((item) => item.getBoundingClientRect().height)),
        minimumLabelFontSize: Math.min(...links.map((item) => Number.parseFloat(getComputedStyle(item.querySelector('span') || item).fontSize))),
      }
    })

    const requiredSelectors = [
      ['[data-mobile-scenario]', 10], ['[data-responsive-proof]', 5], ['[data-desktop-proof]', 1], ['[data-permission-matrix]', 1], ['[data-state-contract]', expectedStateTypes.length],
      ['[data-product-screen="M01"] [data-admin-destination]', 2], ['[data-product-screen="M02"] [data-user-search]', 1], ['[data-product-screen="M02"] [data-user-row]', 3],
      ['[data-product-screen="M03"] [data-user-action]', 5], ['[data-product-screen="M04"] [data-sensitive-review="trading"]', 1], ['[data-product-screen="M04"] [data-finite-deadline="trading"]', 1],
      ['[data-product-screen="M05"] [data-quota]', 3], ['[data-product-screen="M05"] [data-counter-reset-disclosure]', 1], ['[data-product-screen="M06"] [data-consequence]', 2],
      ['[data-product-screen="M07"] [data-advanced-group]', 3], ['[data-product-screen="M07"] [data-session-control]', 1], ['[data-product-screen="M08"] [data-standard-invitation-form]', 1],
      ['[data-product-screen="M09"] [data-invitation-result]', 1], ['[data-product-screen="M10"] [data-pending-invitation-row]', 1], ['[data-product-screen="M10"] [data-revoke-confirmation]', 1],
    ].map(([selector, expected]) => {
      const nodes = [...document.querySelectorAll(selector)]
      const reports = nodes.map(visibilityOf)
      return { selector, expected, total: nodes.length, visible: reports.filter((item) => item.visible).length, hiddenReasons: reports.flatMap((item) => item.reasons) }
    })

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

    const m02Rows = visibleElements(m02, '[data-user-row]').map((row) => ({
      identity: normalize(row.querySelector('strong')?.textContent),
      primaryCount: visibleElements(row, '.row-button__main > strong').length,
      phoneCount: visibleElements(row, '.meta-line > bdi').length,
      metadataPartCount: visibleElements(row, '.meta-line > *').length,
      badgeCount: visibleElements(row, '.status,[data-role-chip],[data-summary-count]').length,
    }))

    const permissionCards = visibleElements(document, '[data-permission-matrix] [data-admin-role]').map((card) => ({
      role: card.dataset.adminRole,
      targets: visibleElements(card, '[data-target]').map((row) => ({
        key: row.dataset.target,
        text: visibleText(row),
        protectedActions: (row.dataset.protectedActions || '').split('|').filter(Boolean),
        permissions: {
          listed: row.dataset.listed === 'true',
          view: row.dataset.viewPermission || '',
          role: row.dataset.rolePermission || '',
          restriction: row.dataset.restrictionPermission || '',
          quotas: row.dataset.quotasPermission || '',
          deactivate: row.dataset.deactivatePermission || '',
          terminateSessions: row.dataset.terminateSessionsPermission || '',
          delete: row.dataset.deletePermission || '',
        },
      })),
      invitableRoles: (card.querySelector('[data-invitable-roles]')?.dataset.invitableRoles || '').split('|').filter(Boolean),
      pendingQueueScope: card.querySelector('[data-pending-queue-scope]')?.dataset.pendingQueueScope || '',
      pendingQueueScopeCopy: visibleText(card.querySelector('[data-pending-queue-scope]')),
      protectionCopyCount: visibleElements(card, '[data-protection-copy]').length,
    }))

    const feedbackStates = ['busy', 'success', 'failure', 'clipboard-failure'].map((type) => {
      const node = document.querySelector(`[data-state-contract="${type}"]`)
      const feedbackActions = visibleElements(node, 'button,a[href]')
      return { type, context: node.dataset.feedbackContext || '', actionCount: feedbackActions.length, disabledActionCount: feedbackActions.filter((item) => item.disabled).length, text: visibleText(node) }
    })

    const stateContracts = visibleElements(document, '[data-state-contract]').map((node) => ({
      type: node.dataset.stateContract,
      actionCount: visibleElements(node, 'button,a[href]').length,
      actionLabels: visibleElements(node, 'button,a[href]').map((item) => normalize(item.getAttribute('aria-label') || item.textContent)),
      queryCount: visibleElements(node, '[data-search-query]').length,
      skeletonCount: visibleElements(node, '[data-loading-skeleton]').length,
      text: visibleText(node),
    }))

    const fontNodes = surfaces.flatMap((surface) => [surface, ...surface.querySelectorAll('button,a,input,select,textarea,h3,h4,h5,p,strong,small,label,li,span')]).filter(visible)
    const fontViolations = fontNodes.map((element) => ({ family: getComputedStyle(element).fontFamily, label: normalize(element.textContent || element.getAttribute('aria-label')).slice(0, 60) })).filter((item) => !item.family.includes('Vazirmatn Evidence'))

    return {
      surfaceGeometry,
      requiredSelectors,
      actions: {
        count: actions.length,
        minimumWidth: Math.min(...actions.map((item) => item.raw.width)),
        minimumHeight: Math.min(...actions.map((item) => item.raw.height)),
        violations: actions.filter((item) => item.raw.width < targetMin || item.raw.height < targetMin || !item.insideSurface || item.ancestorVisibilityReasons.length > 0),
      },
      ctas: {
        count: ctas.length,
        minimumHeight: Math.min(...ctas.map((item) => item.raw.height)),
        violations: ctas.filter((item) => item.raw.height < ctaMin || item.ancestorVisibilityReasons.length > 0),
      },
      mobileScenarios,
      responsive,
      desktop,
      typography: { nodeCount: fontNodes.length, violations: fontViolations },
      minimalism: {
        forbiddenTextChecks,
        syntheticDataViolations,
        obsoleteCanonicalViolations,
        numericPendingViolations,
        summaryCountCount: visibleElements(document, '[data-product-surface] [data-summary-count]').length,
        roleChipCount: visibleElements(document, '[data-product-surface] [data-role-chip], [data-product-surface] .role-chip').length,
        inventedRestrictionReasonCount: visibleElements(m04, 'textarea,[data-restriction-reason]').length,
      },
      protectedScope: {
        marketInteriorCount: visibleElements(document, '[data-product-surface] [data-market-interior]').length,
        messengerInteriorCount: visibleElements(document, '[data-product-surface] [data-messenger-interior]').length,
        protectedTextViolations,
        protectedInteractiveViolations,
        marketConsequenceReferenceCount: visibleElements(m06, '[data-protected-reference="market-consequence"]').length,
        messengerConsequenceReferenceCount: visibleElements(m06, '[data-protected-reference="messenger-consequence"]').length,
        navigationContracts,
      },
      landing: {
        destinations: visibleElements(m01, '[data-admin-destination]').map((item) => item.dataset.adminDestination),
        helperCardCount: visibleElements(m01, '[data-inline-section]').length,
        actionableCountCount: visibleElements(m01, '[data-actionable-count]').length,
        actionableCountText: normalize(m01.querySelector('[data-actionable-count]')?.textContent),
      },
      directory: {
        searchCount: visibleElements(m02, '[data-user-search][data-search-persistent]').length,
        filterCount: visibleElements(m02, '[data-user-filter]').length,
        selectCount: visibleElements(m02, 'select').length,
        instructionalHintCount: visibleElements(m02, '[data-directory-hint]').length,
        rows: m02Rows,
      },
      permissions: { cards: permissionCards },
      detail: {
        actions: visibleElements(m03, '[data-user-action]').map((item) => item.dataset.userAction),
        standaloneWarningActionCount: visibleElements(m03, '[data-user-action]').filter((item) => /هشدار|اخطار/.test(visibleText(item))).length,
        identity: normalize(m03.querySelector('[data-user-identity]')?.textContent),
        protectedNoticeCount: visibleElements(m03, '[data-protected-target-state]').length + visibleElements(m07, '[data-protected-target-state]').length,
        sessionControlCount: visibleElements(m07, '[data-session-control]').length,
        terminateAllCount: visibleElements(m07, '[data-terminate-all-sessions]').length,
        sessionConsequenceCopy: normalize(m07.querySelector('[data-session-control] .hint')?.textContent),
        sessionTokenJargonPresent: /توکن|token/i.test(visibleText(m07)),
      },
      sensitive: {
        tradingReviewCount: visibleElements(m04, '[data-sensitive-review="trading"]').length,
        tradingBeforeCount: visibleElements(m04, '[data-before-value]').length,
        tradingAfterCount: visibleElements(m04, '[data-after-value]').length,
        tradingDeadlineCount: visibleElements(m04, '[data-finite-deadline="trading"]').length,
        tradingConfirmCount: visibleElements(m04, '[data-confirm-sensitive="trading"]').length,
        tradingStatusCopy: visibleText(m04.querySelector('[data-trading-status]')),
        tradingDeadlineValue: m04.querySelector('[data-finite-deadline="trading"]')?.value || '',
        tradingDeadlineLiteralOccurrences: visibleLiteralOccurrences(m04, '۲۲ مرداد ۱۴۰۵'),
        tradingEffectText: visibleText(m04.querySelector('[data-trading-effect]')),
        quotaKeys: visibleElements(m05, '[data-quota]').map((item) => item.dataset.quota),
        quotaReviewLines: visibleElements(m05, '[data-quota]').map((item) => normalize(`${visibleText(item.querySelector('[data-quota-line]'))} ${item.querySelector('input')?.value || ''}`)),
        quotaCurrentValues: visibleElements(m05, '[data-quota]').map((item) => item.dataset.currentValue || ''),
        quotaProposedValues: visibleElements(m05, '[data-quota]').map((item) => item.dataset.proposedValue || ''),
        proposedQuotaValues: visibleElements(m05, '[data-quota] input').map((item) => item.value),
        quotaDeadlineCount: visibleElements(m05, '[data-finite-deadline="quotas"]').length,
        quotaDeadlineValue: m05.querySelector('[data-finite-deadline="quotas"]')?.value || '',
        quotaConfirmCount: visibleElements(m05, '[data-confirm-sensitive="quotas"]').length,
        noPermanentCopy: visibleText(m05.querySelector('[data-no-permanent-limit]')),
        quotaUsageConsequenceCopy: visibleText(m05.querySelector('[data-quota-usage-consequence]')),
        finiteOnlyCopy: visibleText(m05.querySelector('[data-finite-only-copy]')),
        counterResetCount: visibleElements(m05, '[data-counter-reset-disclosure]').length,
        permanentLimitTermPresent: /دائم|نامحدود/.test(visibleText(m05)),
        permanentLimitMarkerCount: m05.querySelectorAll('[data-permanent-option],[data-unlimited-option],[data-limit-mode="permanent"],[data-limit-mode="unlimited"],[value="permanent"],[value="unlimited"]').length,
      },
      deactivation: {
        consequenceKeys: visibleElements(m06, '[data-consequence]').map((item) => item.dataset.consequence),
        consequenceCopy: visibleElements(m06, '[data-consequence]').map(visibleText),
        acknowledgementCount: visibleElements(m06, '[data-deactivation-ack][aria-pressed="true"]').length,
        confirmCount: visibleElements(m06, '[data-confirm-sensitive="deactivate"]').length,
        forbiddenOfferEffect: /پیشنهادهای فعال|آفرهای فعال|منقضی/.test(visibleText(m06)),
        forbiddenInitiationTerms: ['تلاش می‌کند', 'آغاز', 'ثبت درخواست'].filter((term) => visibleText(m06).includes(term)),
        delayedConditional: visibleText(m06.querySelector('[data-consequence="delayed-global"]')).startsWith('اگر تا پایان دو روز فعال نشود'),
        deadlineCopy: visibleText(m06.querySelector('[data-deactivation-deadline]')),
        successSideEffectClaimCount: visibleElements(document, '[data-state-contract="success"]').filter((item) => /تلگرام|کانال|پیام‌رسان|خارج/.test(visibleText(item))).length,
        successProof: (() => {
          const proof = document.querySelector('[data-deactivation-success-proof]')
          return {
            count: proof && visible(proof) ? 1 : 0,
            statusCopy: visibleText(proof?.querySelector('[data-observable-account-status]')),
            deadlineCopy: visibleText(proof?.querySelector('[data-grace-deadline]')),
            factualFieldCount: proof ? visibleElements(proof, '[data-observable-account-status],[data-grace-deadline]').length : 0,
            sideEffectInitiationCount: proof ? visibleElements(proof, '[data-side-effect-initiation]').length : 0,
            actionCount: proof ? visibleElements(proof, 'button,a[href]').length : 0,
            forbiddenOutcomeClaim: proof ? /تلگرام|کانال|پیام‌رسان|اعلان|پیامک|خارج شد|با موفقیت|درخواست.*آغاز|درخواست پیامد/.test(visibleText(proof)) : true,
          }
        })(),
      },
      feedback: feedbackStates,
      invitationForm: {
        formCount: visibleElements(m08, '[data-standard-invitation-form]').length,
        fieldKeys: visibleElements(m08, '[data-invite-field]').map((item) => item.dataset.inviteField),
        roleSelectCount: visibleElements(m08, '[data-invite-role]').length,
        roleOptions: [...m08.querySelectorAll('[data-invite-role] option')].map((item) => normalize(item.textContent)),
        createCount: visibleElements(m08, '[data-create-standard-invite]').length,
        actorCopy: normalize(m08.querySelector('.mobile-title p')?.textContent),
        mixedActorHintCount: visibleElements(m08, '.field-wrap .hint').length,
      },
      invitationResult: {
        resultCount: visibleElements(m09, '[data-invitation-result]').length,
        created: m09.querySelector('[data-invitation-result]')?.dataset.created,
        kind: m09.querySelector('[data-result-kind]')?.dataset.resultKind,
        copySurfaces: visibleElements(m09, '[data-copy-surface]').map((item) => item.dataset.copySurface),
        expiryCount: visibleElements(m09, '[data-invite-expiry]').length,
        smsState: m09.querySelector('[data-sms-state]')?.dataset.smsState || '',
        telegramDeliveryClaim: m09.querySelector('[data-telegram-delivery-claim]')?.dataset.telegramDeliveryClaim || '',
        expiryCopies: visibleElements(m09, '[data-invite-expiry]').map(visibleText),
        text: visibleText(m09),
        variants: visibleElements(document, '[data-invitation-result]').map((result) => ({
          created: result.dataset.created,
          kind: result.dataset.resultKind || result.querySelector('[data-result-kind]')?.dataset.resultKind || '',
          availability: result.dataset.availability || '',
          copySurfaces: visibleElements(result, '[data-copy-surface]').map((item) => item.dataset.copySurface),
          smsState: result.querySelector('[data-sms-state]')?.dataset.smsState || '',
          expiryCount: visibleElements(result, '[data-invite-expiry]').length,
          expiryCopy: visibleText(result.querySelector('[data-invite-expiry]')),
          telegramDeliveryClaim: result.querySelector('[data-telegram-delivery-claim]')?.dataset.telegramDeliveryClaim || '',
          outsideMobileRoot: !result.closest('[data-mobile-scenario]'),
          text: visibleText(result),
        })),
      },
      pendingQueue: {
        rowCount: visibleElements(m10, '[data-pending-invitation-row]').length,
        copySurfaces: visibleElements(m10, '[data-pending-invitation-row] [data-copy-surface]').map((item) => item.dataset.copySurface),
        expiryCount: visibleElements(m10, '[data-pending-invitation-row] [data-invite-expiry]').length,
        queueTotalCount: visibleElements(m10, '[data-queue-total]').length,
        actorRole: m10.dataset.actorRole || '',
        scope: m10.dataset.pendingScope || '',
        scopeCopy: normalize(m10.querySelector('.mobile-title p')?.textContent),
        pendingMetaCopy: visibleText(m10.querySelector('[data-pending-invitation-row] [data-invite-expiry]')),
      },
      revoke: {
        confirmationCount: visibleElements(m10, '[data-revoke-confirmation]').length,
        acknowledgementCount: visibleElements(m10, '[data-revoke-ack][aria-pressed="true"]').length,
        actionCount: visibleElements(m10, '[data-revoke-invitation].danger-button.cta').length,
        confirmationText: visibleText(m10.querySelector('[data-revoke-confirmation]')),
      },
      rawUrls: { violations: rawUrlViolations },
      states: stateContracts,
    }
  }, { responsiveWidths: RESPONSIVE_WIDTHS, targetMin: TARGET_MIN, ctaMin: CTA_MIN, expectedStateTypes: EXPECTED_STATE_TYPES })
}

function buildAssertions(metrics, font) {
  const actualScenarioIds = metrics.mobileScenarios.map((item) => item.id)
  const actualStateTypes = metrics.states.map((item) => item.type)
  const requiredNodeViolations = metrics.requiredSelectors.filter((item) => item.total !== item.expected || item.visible !== item.expected || item.hiddenReasons.length > 0)
  const surfaceViolations = metrics.surfaceGeometry.filter((item) => item.overflowX || item.overflowY || item.descendantBoundsViolations.length > 0 || item.clippedTextViolations.length > 0)
  const mobileStructureViolations = metrics.mobileScenarios.filter((item) => {
    const expectedMode = item.mode === 'list' ? item.listCount === 1 && item.detailCount === 0 : item.mode === 'detail' ? item.detailCount === 1 && item.listCount === 0 : false
    return !item.xor || !expectedMode || item.visibleMainChildCount !== 1 || item.unmarkedMainChildCount !== 0 || item.contractRootCount !== 1 || item.nestedContractCount !== 0 || item.unexpectedTaskPaneCount !== 0 || item.unmarkedInlinePanelCount !== 0 || item.actionCount !== EXPECTED_ACTION_COUNTS[item.id] || item.contractDirectChildCount !== EXPECTED_DIRECT_CHILD_COUNTS[item.id] || item.mainOverflowX || item.mainOverflowY
  })
  const middle = metrics.permissions.cards.find((item) => item.role === 'middle')
  const superAdmin = metrics.permissions.cards.find((item) => item.role === 'super')
  const expectedProtected = EXPECTED_PROTECTED_ACTIONS.join('|')
  const protectedRows = metrics.permissions.cards.flatMap((card) => card.targets.filter((target) => target.protectedActions.length > 0))
  const expectedDesktopDetailText = 'سارا نادری sara.demo · 0912 ••• 2468 فعال هویت حساب عادی اقدامات کاربر هر اقدام در مرور مستقل با پیامد و تأیید نهایی باز می‌شود. محدودیت معاملاتی محدودیت تعدادی غیرفعال‌سازی حساب تنظیمات پیشرفته بررسی حذف کاربر'
  const feedbackByType = Object.fromEntries(metrics.feedback.map((item) => [item.type, item]))
  const stateByType = Object.fromEntries(metrics.states.map((item) => [item.type, item]))
  const expectedNavigationDestinations = ['home', 'market', 'messenger', 'operations', 'account']
  const expectedNavigationLabels = ['خانه', 'بازار', 'پیام‌رسان', 'عملیات', 'حساب']
  const desktopNavigationContracts = metrics.protectedScope.navigationContracts.filter((item) => item.context === 'desktop')
  const desktopNavigation = desktopNavigationContracts[0]
  const expectedPermissionMatrix = {
    middle: {
      self: { listed: true, view: 'yes', role: 'no', restriction: 'no', quotas: 'no', deactivate: 'no', terminateSessions: 'no', delete: 'no' },
      lower: { listed: true, view: 'yes', role: 'no', restriction: 'yes', quotas: 'yes', deactivate: 'yes', terminateSessions: 'yes', delete: 'yes' },
      admin: { listed: false, view: 'no', role: 'no', restriction: 'no', quotas: 'no', deactivate: 'no', terminateSessions: 'no', delete: 'no' },
    },
    super: {
      self: { listed: true, view: 'yes', role: 'no', restriction: 'no', quotas: 'no', deactivate: 'no', terminateSessions: 'no', delete: 'no' },
      lower: { listed: true, view: 'yes', role: 'yes', restriction: 'yes', quotas: 'yes', deactivate: 'yes', terminateSessions: 'yes', delete: 'yes' },
      peer: { listed: true, view: 'yes', role: 'no', restriction: 'no', quotas: 'no', deactivate: 'no', terminateSessions: 'no', delete: 'no' },
    },
  }
  const actualPermissionMatrix = Object.fromEntries(metrics.permissions.cards.map((card) => [card.role, Object.fromEntries(card.targets.map((target) => [target.key, target.permissions]))]))

  return [
    { id: 'font-vazirmatn-loaded', passed: font.loaded && font.faces.length >= 4 && metrics.typography.violations.length === 0, detail: `${font.faces.length} faces; ${metrics.typography.violations.length} computed-family violation(s)` },
    { id: 'ten-mobile-scenarios-complete', passed: JSON.stringify(actualScenarioIds) === JSON.stringify(EXPECTED_SCENARIOS) && requiredNodeViolations.length === 0, detail: `${actualScenarioIds.join(', ')}; required-node violations=${requiredNodeViolations.map((item) => item.selector).join(', ') || 'none'}` },
    { id: 'mobile-roots-exact-390x844', passed: metrics.mobileScenarios.every((item) => Math.abs(item.raw.width - 390) < 0.01 && Math.abs(item.raw.height - 844) < 0.01), detail: metrics.mobileScenarios.map((item) => `${item.id}:${item.raw.width}×${item.raw.height}`).join(', ') },
    { id: 'mobile-list-detail-xor', passed: mobileStructureViolations.length === 0, detail: mobileStructureViolations.map((item) => `${item.id}[actions=${item.actionCount}/${EXPECTED_ACTION_COUNTS[item.id]},children=${item.contractDirectChildCount}/${EXPECTED_DIRECT_CHILD_COUNTS[item.id]},nested=${item.nestedContractCount},panes=${item.unexpectedTaskPaneCount + item.unmarkedInlinePanelCount}]`).join('; ') || 'exact roots/actions; no nested or unmarked task panes' },
    { id: 'no-product-overflow-or-clipping', passed: surfaceViolations.length === 0 && requiredNodeViolations.length === 0, detail: `${surfaceViolations.length} geometry violation(s): ${surfaceViolations.map((item) => `${item.id}[overflow=${item.overflowX}/${item.overflowY},bounds=${item.descendantBoundsViolations.length}:${item.descendantBoundsViolations.slice(0, 2).map((node) => `${node.tag}.${node.className}`).join('|')},text=${item.clippedTextViolations.length}:${item.clippedTextViolations.slice(0, 2).map((node) => `${node.tag}:${node.text}`).join('|')}]`).join('; ') || 'none'}; ${requiredNodeViolations.length} required-node visibility violation(s)` },
    { id: 'touch-targets-44', passed: metrics.actions.count > 0 && metrics.actions.violations.length === 0, detail: `minimum raw ${metrics.actions.minimumWidth}×${metrics.actions.minimumHeight}px across ${metrics.actions.count}; violations=${metrics.actions.violations.length}` },
    { id: 'cta-height-48', passed: metrics.ctas.count > 0 && metrics.ctas.violations.length === 0, detail: `minimum raw ${metrics.ctas.minimumHeight}px across ${metrics.ctas.count}; violations=${metrics.ctas.violations.length}` },
    { id: 'responsive-width-sweep', passed: metrics.responsive.length === 5 && metrics.responsive.every((item) => item.found && item.exactWidth && item.exactHeight && !item.overflowX && !item.overflowY && item.listCount === 1 && item.detailCount === 0 && item.navigationCount === 1 && item.searchCount === 1 && item.filterCount === 0 && item.actionCount === 10 && JSON.stringify(item.rowIdentities) === JSON.stringify(['سارا نادری', 'کیان مرادی', 'نرگس آقایی'])), detail: metrics.responsive.map((item) => `${item.requestedWidth}:${item.raw?.width}×${item.raw?.height}/actions=${item.actionCount}/rows=${item.rowIdentities.join('|')}`).join(', ') },
    { id: 'desktop-user-master-detail-1440x900', passed: Math.abs(metrics.desktop.raw.width - 1440) < 0.01 && Math.abs(metrics.desktop.raw.height - 900) < 0.01 && metrics.desktop.listCount === 1 && metrics.desktop.detailCount === 1 && metrics.desktop.navigationCount === 1 && metrics.desktop.sidebarCount === 0 && metrics.desktop.kpiCount === 0 && metrics.desktop.summaryCountCount === 0 && metrics.desktop.currentAdminMetadataCount === 0 && metrics.desktop.extraPaneCount === 0 && metrics.desktop.sideBySide && metrics.desktop.selectedRowCount === 1 && metrics.desktop.sameIdentity && !metrics.desktop.overflowX && !metrics.desktop.overflowY && JSON.stringify(metrics.desktop.rowIdentities) === JSON.stringify(['سارا نادری', 'کیان مرادی', 'نرگس آقایی']) && JSON.stringify(metrics.desktop.actionLabels) === JSON.stringify(['محدودیت معاملاتی', 'محدودیت تعدادی', 'غیرفعال‌سازی حساب', 'تنظیمات پیشرفته', 'بررسی حذف کاربر']) && metrics.desktop.detailBodyChildCount === 2 && metrics.desktop.detailText === expectedDesktopDetailText, detail: `${metrics.desktop.raw.width}×${metrics.desktop.raw.height}; side-by-side=${metrics.desktop.sideBySide}; same-identity=${metrics.desktop.sameIdentity}; current-admin-metadata=${metrics.desktop.currentAdminMetadataCount}; exact-detail-copy=${metrics.desktop.detailText === expectedDesktopDetailText}` },
    { id: 'minimal-content-contract', passed: metrics.minimalism.forbiddenTextChecks.every((item) => !item.present) && metrics.minimalism.syntheticDataViolations.length === 0 && metrics.minimalism.obsoleteCanonicalViolations.length === 0 && metrics.minimalism.numericPendingViolations.length === 0 && metrics.minimalism.summaryCountCount === 0 && metrics.minimalism.roleChipCount === 0 && metrics.minimalism.inventedRestrictionReasonCount === 0, detail: `forbidden=${metrics.minimalism.forbiddenTextChecks.filter((item) => item.present).map((item) => item.term).join('|') || 'none'}; real-data=${metrics.minimalism.syntheticDataViolations.join('|') || 'none'}; obsolete-canonical=${metrics.minimalism.obsoleteCanonicalViolations.join('|') || 'none'}; numeric-pending=${metrics.minimalism.numericPendingViolations.length}; invented-reason=${metrics.minimalism.inventedRestrictionReasonCount}` },
    { id: 'admin-landing-action-focused', passed: JSON.stringify(metrics.landing.destinations) === JSON.stringify(['users', 'standard-invitations']) && metrics.landing.helperCardCount === 0 && metrics.landing.actionableCountCount === 1 && metrics.landing.actionableCountText === 'نیازمند رسیدگی' && !/[0-9۰-۹]/.test(metrics.landing.actionableCountText), detail: `destinations=${metrics.landing.destinations.join(',')}; helper=${metrics.landing.helperCardCount}; actionable=${metrics.landing.actionableCountText}` },
    { id: 'user-directory-metadata-bounded', passed: JSON.stringify(metrics.directory.rows.map((item) => item.identity)) === JSON.stringify(['سارا نادری', 'کیان مرادی', 'نرگس آقایی']) && metrics.directory.rows.every((item) => item.primaryCount === 1 && item.phoneCount === 1 && item.metadataPartCount === 2 && item.badgeCount === 0) && metrics.directory.instructionalHintCount === 0, detail: `${metrics.directory.rows.length} rows; metadata=${metrics.directory.rows.map((item) => item.metadataPartCount).join(',')}; hints=${metrics.directory.instructionalHintCount}` },
    { id: 'user-filters-bounded-actionable', passed: metrics.directory.searchCount === 1 && metrics.directory.filterCount === 0 && metrics.directory.selectCount === 0, detail: `persistent-search=${metrics.directory.searchCount}; filters=${metrics.directory.filterCount}; selects=${metrics.directory.selectCount}` },
    { id: 'role-permission-matrix-exact', passed: JSON.stringify(metrics.permissions.cards.map((item) => item.role)) === JSON.stringify(['middle', 'super']) && JSON.stringify(middle?.targets.map((item) => item.key)) === JSON.stringify(['self', 'lower', 'admin']) && JSON.stringify(superAdmin?.targets.map((item) => item.key)) === JSON.stringify(['self', 'lower', 'peer']) && JSON.stringify(actualPermissionMatrix) === JSON.stringify(expectedPermissionMatrix) && middle?.targets.find((item) => item.key === 'self')?.text.includes('فقط مشاهده') && middle?.targets.find((item) => item.key === 'admin')?.text.startsWith('هر مدیر دیگر') && middle?.targets.find((item) => item.key === 'admin')?.text.includes('در فهرست نیست') && superAdmin?.targets.find((item) => item.key === 'peer')?.text.includes('فقط مشاهده') && JSON.stringify(middle?.invitableRoles) === JSON.stringify(['تماشا', 'عادی']) && JSON.stringify(superAdmin?.invitableRoles) === JSON.stringify(['تماشا', 'عادی', 'پلیس', 'مدیر میانی']) && middle?.pendingQueueScope === 'own' && middle?.pendingQueueScopeCopy === 'دعوت‌های در انتظار: فقط دعوت‌های ساخته‌شده توسط خود مدیر' && superAdmin?.pendingQueueScope === 'all' && superAdmin?.pendingQueueScopeCopy === 'دامنه صف: همه دعوت‌های در انتظار' && protectedRows.length === 4 && protectedRows.every((item) => item.protectedActions.join('|') === expectedProtected) && metrics.permissions.cards.every((item) => item.protectionCopyCount === 1), detail: `roles=${metrics.permissions.cards.map((item) => item.role).join(',')}; exact-matrix=${JSON.stringify(actualPermissionMatrix) === JSON.stringify(expectedPermissionMatrix)}; middle-self=${middle?.targets.find((item) => item.key === 'self')?.text}; middle-other-admin=${middle?.targets.find((item) => item.key === 'admin')?.text}; queue-scopes=${metrics.permissions.cards.map((item) => `${item.role}:${item.pendingQueueScope}`).join(',')}; protected-rows=${protectedRows.length}; protected-actions=${expectedProtected}` },
    { id: 'user-detail-progressive-disclosure', passed: JSON.stringify(metrics.detail.actions) === JSON.stringify(['trading', 'quotas', 'deactivate', 'advanced', 'delete']) && metrics.detail.standaloneWarningActionCount === 0 && metrics.detail.identity === 'sara.demo' && metrics.detail.protectedNoticeCount === 0 && metrics.detail.sessionControlCount === 1 && metrics.detail.terminateAllCount === 1 && metrics.detail.sessionConsequenceCopy === 'با پایان همه نشست‌ها، کاربر فوراً از همه دستگاه‌ها خارج می‌شود.' && !metrics.detail.sessionTokenJargonPresent, detail: `actions=${metrics.detail.actions.join(',')}; warning=${metrics.detail.standaloneWarningActionCount}; generic-protection-notice=${metrics.detail.protectedNoticeCount}; terminate-all=${metrics.detail.terminateAllCount}; session-copy=${metrics.detail.sessionConsequenceCopy}; token-jargon=${metrics.detail.sessionTokenJargonPresent}` },
    { id: 'sensitive-decision-review-complete', passed: metrics.sensitive.tradingReviewCount === 1 && metrics.sensitive.tradingBeforeCount === 1 && metrics.sensitive.tradingAfterCount === 1 && metrics.sensitive.tradingDeadlineCount === 1 && metrics.sensitive.tradingConfirmCount === 1 && metrics.sensitive.tradingStatusCopy === 'وضعیت: فعال ← محدود' && metrics.sensitive.tradingDeadlineValue === 'پایان: ۲۲ مرداد ۱۴۰۵، ساعت ۱۴:۳۰' && metrics.sensitive.tradingDeadlineLiteralOccurrences === 1 && metrics.sensitive.tradingEffectText === 'کاربر نمی‌تواند پیشنهاد معاملاتی ایجاد کند یا معامله انجام دهد؛ ورود او به حساب برقرار می‌ماند.' && JSON.stringify(metrics.sensitive.quotaKeys) === JSON.stringify(['daily-trades', 'traded-quantity', 'channel-offer-requests']) && JSON.stringify(metrics.sensitive.quotaReviewLines) === JSON.stringify(['معامله روزانه: ۲۰ ← ۱۰', 'تعداد کالای معامله‌شده: ۵۰۰ ← ۳۰۰', 'درخواست آفر کانال: ۶ ← ۴']) && JSON.stringify(metrics.sensitive.quotaCurrentValues) === JSON.stringify(['20', '500', '6']) && JSON.stringify(metrics.sensitive.quotaProposedValues) === JSON.stringify(['10', '300', '4']) && JSON.stringify(metrics.sensitive.proposedQuotaValues) === JSON.stringify(['۱۰', '۳۰۰', '۴']) && metrics.sensitive.quotaUsageConsequenceCopy === 'مصرف فعلی: ۳ معامله، ۱۲۰ عدد کالا و ۲ درخواست. با تأیید، هر سه شمارنده از صفر آغاز می‌شوند.' && metrics.sensitive.quotaDeadlineCount === 1 && metrics.sensitive.quotaDeadlineValue === '۲۲ مرداد ۱۴۰۵، ۱۴:۳۰' && metrics.sensitive.quotaConfirmCount === 1 && metrics.sensitive.counterResetCount === 1 && metrics.sensitive.finiteOnlyCopy === 'این محدودیت فقط با موعد پایان مشخص اعمال می‌شود.' && !metrics.sensitive.permanentLimitTermPresent && metrics.sensitive.permanentLimitMarkerCount === 0, detail: `trading=${metrics.sensitive.tradingStatusCopy}; trading-deadline=${metrics.sensitive.tradingDeadlineValue}; trading-deadline-occurrences=${metrics.sensitive.tradingDeadlineLiteralOccurrences}; exact-effect=${metrics.sensitive.tradingEffectText}; quotas=${metrics.sensitive.quotaReviewLines.join('|')}; proposed-inputs=${metrics.sensitive.proposedQuotaValues.join(',')}; usage=${metrics.sensitive.quotaUsageConsequenceCopy}; deadline=${metrics.sensitive.quotaDeadlineValue}; reset=${metrics.sensitive.counterResetCount}; permanent-term=${metrics.sensitive.permanentLimitTermPresent}; permanent-markers=${metrics.sensitive.permanentLimitMarkerCount}` },
    { id: 'account-deactivation-consequences-truthful', passed: JSON.stringify(metrics.deactivation.consequenceKeys) === JSON.stringify(['immediate', 'delayed-global']) && JSON.stringify(metrics.deactivation.consequenceCopy) === JSON.stringify(['فوری: بازار بسته می‌شود. خروج کاربر از کانال تلگرام مورد انتظار است؛ نتیجه آن در این صفحه قابل‌تأیید نیست.', 'اگر تا پایان دو روز فعال نشود: ورود وب‌اپ و پیام‌رسان قفل و همه نشست‌ها پایان می‌یابد.']) && metrics.deactivation.forbiddenInitiationTerms.length === 0 && metrics.deactivation.delayedConditional && metrics.deactivation.deadlineCopy === 'غیرفعال؛ مهلت تا ۱۹ مرداد ۱۴۰۵، ۱۴:۳۰' && metrics.deactivation.acknowledgementCount === 1 && metrics.deactivation.confirmCount === 1 && !metrics.deactivation.forbiddenOfferEffect && metrics.deactivation.successSideEffectClaimCount === 0 && metrics.deactivation.successProof.count === 1 && metrics.deactivation.successProof.statusCopy === 'وضعیت حساب: غیرفعال' && metrics.deactivation.successProof.deadlineCopy === 'مهلت فعال‌سازی تا ۱۹ مرداد ۱۴۰۵، ۱۴:۳۰' && metrics.deactivation.successProof.factualFieldCount === 2 && metrics.deactivation.successProof.sideEffectInitiationCount === 0 && metrics.deactivation.successProof.actionCount === 1 && !metrics.deactivation.successProof.forbiddenOutcomeClaim, detail: `${metrics.deactivation.consequenceKeys.join(',')}; exact-consequences=${JSON.stringify(metrics.deactivation.consequenceCopy)}; forbidden-initiation=${metrics.deactivation.forbiddenInitiationTerms.join('|') || 'none'}; deadline=${metrics.deactivation.deadlineCopy}; conditional=${metrics.deactivation.delayedConditional}; forbidden-offer-effect=${metrics.deactivation.forbiddenOfferEffect}; success-proof=${metrics.deactivation.successProof.count}/facts=${metrics.deactivation.successProof.factualFieldCount}/deadline=${metrics.deactivation.successProof.deadlineCopy}/initiation=${metrics.deactivation.successProof.sideEffectInitiationCount}/forbidden=${metrics.deactivation.successProof.forbiddenOutcomeClaim}; success-side-effect-claims=${metrics.deactivation.successSideEffectClaimCount}` },
    { id: 'action-feedback-in-context', passed: metrics.feedback.length === 4 && metrics.feedback.every((item) => item.context.includes('|') && item.actionCount === 1) && feedbackByType.busy?.context === feedbackByType.success?.context && feedbackByType.success?.context === feedbackByType.failure?.context && feedbackByType.busy?.disabledActionCount === 1 && feedbackByType.success?.text.includes('سارا نادری') && feedbackByType.failure?.text.includes('موعد انتخاب‌شده حفظ شده است') && feedbackByType['clipboard-failure']?.text.includes('دعوت همچنان فعال است'), detail: `${metrics.feedback.map((item) => `${item.type}:${item.context}/actions=${item.actionCount}`).join(', ')}; decision-context-equal=${feedbackByType.busy?.context === feedbackByType.success?.context && feedbackByType.success?.context === feedbackByType.failure?.context}` },
    { id: 'standard-invitation-form-bounded', passed: metrics.invitationForm.formCount === 1 && JSON.stringify(metrics.invitationForm.fieldKeys) === JSON.stringify(['account', 'mobile']) && metrics.invitationForm.roleSelectCount === 1 && JSON.stringify(metrics.invitationForm.roleOptions) === JSON.stringify(['تماشا', 'عادی']) && !metrics.invitationForm.roleOptions.includes('مدیر ارشد') && metrics.invitationForm.createCount === 1 && metrics.invitationForm.actorCopy === 'مدیر میانی · حساب جدید' && metrics.invitationForm.mixedActorHintCount === 0, detail: `actor=${metrics.invitationForm.actorCopy}; fields=${metrics.invitationForm.fieldKeys.join(',')}+role; roles=${metrics.invitationForm.roleOptions.join(',')}; mixed-hints=${metrics.invitationForm.mixedActorHintCount}` },
    { id: 'standard-invitation-result-truthful', passed: metrics.invitationResult.resultCount === 1 && metrics.invitationResult.created === 'false' && metrics.invitationResult.kind === 'reused' && JSON.stringify(metrics.invitationResult.copySurfaces) === JSON.stringify(['web', 'telegram']) && metrics.invitationResult.expiryCount === 1 && JSON.stringify(metrics.invitationResult.expiryCopies) === JSON.stringify(['تا ۲۲ مرداد ۱۴۰۵، ۱۴:۳۰']) && metrics.invitationResult.smsState === 'accepted' && metrics.invitationResult.telegramDeliveryClaim === 'none' && metrics.invitationResult.text.includes('دعوت تازه‌ای ساخته نشد') && metrics.invitationResult.text.includes('این وضعیت، تحویل پیام در تلگرام را تأیید نمی‌کند') && !/با موفقیت.*تلگرام|تلگرام.*موفقیت/.test(metrics.invitationResult.text) && metrics.invitationResult.variants.length === 2 && JSON.stringify(metrics.invitationResult.variants.map((item) => `${item.created}:${item.kind}:${item.availability}`)) === JSON.stringify(['false:reused:web-telegram', 'true:fresh:web-only']) && JSON.stringify(metrics.invitationResult.variants[1].copySurfaces) === JSON.stringify(['web']) && metrics.invitationResult.variants[1].smsState === 'pending' && metrics.invitationResult.variants[1].expiryCount === 1 && metrics.invitationResult.variants[1].expiryCopy === 'اعتبار تا ۲۲ مرداد ۱۴۰۵، ۱۴:۳۰' && metrics.invitationResult.variants[1].telegramDeliveryClaim === 'none' && metrics.invitationResult.variants[1].outsideMobileRoot && metrics.invitationResult.variants[1].text.includes('دعوت تازه ساخته شد') && metrics.invitationResult.variants[1].text.includes('لینک تلگرام در دسترس نیست'), detail: `variants=${metrics.invitationResult.variants.map((item) => `${item.created}:${item.kind}:${item.availability}[${item.copySurfaces.join(',')}]/expiry=${item.expiryCount}:${item.expiryCopy}/outside=${item.outsideMobileRoot}`).join('; ')}; reused-expiry=${metrics.invitationResult.expiryCopies.join('|')}; reused-sms=${metrics.invitationResult.smsState}` },
    { id: 'pending-invitation-queue-actionable', passed: metrics.pendingQueue.rowCount === 1 && JSON.stringify(metrics.pendingQueue.copySurfaces) === JSON.stringify(['web', 'telegram']) && metrics.pendingQueue.expiryCount === 1 && metrics.pendingQueue.pendingMetaCopy === 'تماشا · تا ۲۲ مرداد ۱۴۰۵، ۱۴:۳۰' && metrics.pendingQueue.queueTotalCount === 0 && metrics.pendingQueue.actorRole === 'middle' && metrics.pendingQueue.scope === 'own' && metrics.pendingQueue.scopeCopy === 'نرگس آقایی · دعوت خود شما' && metrics.minimalism.numericPendingViolations.length === 0, detail: `rows=${metrics.pendingQueue.rowCount}; meta=${metrics.pendingQueue.pendingMetaCopy}; links=${metrics.pendingQueue.copySurfaces.join(',')}; totals=${metrics.pendingQueue.queueTotalCount}; actor/scope=${metrics.pendingQueue.actorRole}/${metrics.pendingQueue.scope}` },
    { id: 'invitation-revoke-confirmation-complete', passed: metrics.revoke.confirmationCount === 1 && metrics.revoke.acknowledgementCount === 1 && metrics.revoke.actionCount === 1 && metrics.revoke.confirmationText.includes('هر دو لینک همین دعوت از کار می‌افتد') && metrics.revoke.confirmationText.includes('دعوت تازه‌ای بسازی'), detail: `confirmation=${metrics.revoke.confirmationCount}; acknowledgement=${metrics.revoke.acknowledgementCount}; danger-action=${metrics.revoke.actionCount}` },
    { id: 'raw-invitation-urls-hidden-by-default', passed: metrics.rawUrls.violations.length === 0, detail: `${metrics.rawUrls.violations.length} visible raw URL/token violation(s)` },
    { id: 'recovery-state-atlas-complete', passed: JSON.stringify(actualStateTypes) === JSON.stringify(EXPECTED_STATE_TYPES) && stateByType.loading?.actionCount === 0 && stateByType.loading?.skeletonCount === 1 && !/اتصال|اینترنت|شبکه/.test(stateByType['load-error']?.text || '') && metrics.states.filter((item) => item.type !== 'loading').every((item) => item.actionCount === 1) && stateByType['search-empty']?.queryCount === 1 && stateByType['permission-protected']?.text.includes('خود حساب یا مدیر هم‌سطح') && JSON.stringify(stateByType['permission-protected']?.actionLabels) === JSON.stringify(['بازگشت به جزئیات']), detail: `${actualStateTypes.map((type) => `${type}:actions=${stateByType[type]?.actionCount}/skeleton=${stateByType[type]?.skeletonCount}`).join(', ')}; protected-action=${stateByType['permission-protected']?.actionLabels.join('|') || 'missing'}` },
    { id: 'protected-interiors-absent', passed: metrics.protectedScope.marketInteriorCount === 0 && metrics.protectedScope.messengerInteriorCount === 0 && metrics.protectedScope.protectedTextViolations.length === 0 && metrics.protectedScope.protectedInteractiveViolations.length === 0 && metrics.protectedScope.marketConsequenceReferenceCount === 1 && metrics.protectedScope.messengerConsequenceReferenceCount === 2 && metrics.protectedScope.navigationContracts.length === 16 && metrics.protectedScope.navigationContracts.every((item) => JSON.stringify(item.destinations) === JSON.stringify(expectedNavigationDestinations) && JSON.stringify(item.labels) === JSON.stringify(expectedNavigationLabels) && JSON.stringify(item.activeDestinations) === JSON.stringify(['operations']) && item.svgCount === 5 && item.labelBoundsViolationCount === 0 && item.minimumLabelFontSize >= 11 && item.minimumWidth >= (item.context === 'desktop' ? 48 : 44) && item.minimumHeight >= (item.context === 'desktop' ? 48 : 52) && (item.context === 'desktop' || item.navigationHeight >= 78)) && desktopNavigationContracts.length === 1 && JSON.stringify(desktopNavigation.destinations) === JSON.stringify(expectedNavigationDestinations) && JSON.stringify(desktopNavigation.labels) === JSON.stringify(expectedNavigationLabels) && JSON.stringify(desktopNavigation.activeDestinations) === JSON.stringify(['operations']) && desktopNavigation.svgCount === 5 && desktopNavigation.labelBoundsViolationCount === 0 && desktopNavigation.minimumWidth >= 48 && desktopNavigation.minimumHeight >= 48, detail: `market=${metrics.protectedScope.marketInteriorCount}; messenger=${metrics.protectedScope.messengerInteriorCount}; consequence-refs=${metrics.protectedScope.marketConsequenceReferenceCount}/${metrics.protectedScope.messengerConsequenceReferenceCount}; lexicon=${JSON.stringify(metrics.protectedScope.protectedTextViolations)}; interactive=${JSON.stringify(metrics.protectedScope.protectedInteractiveViolations)}; desktop-shell=${desktopNavigationContracts.length}:${desktopNavigation?.destinations.join('>')}/${desktopNavigation?.labels.join('>')}/active=${desktopNavigation?.activeDestinations.join('|')}/svg=${desktopNavigation?.svgCount}/item=${desktopNavigation?.minimumWidth}×${desktopNavigation?.minimumHeight}; nav=${metrics.protectedScope.navigationContracts.map((item) => `${item.context}:${item.destinations.join('>')}/${item.labels.join('>')}/active=${item.activeDestinations.join('|')}/svg=${item.svgCount}/label-bounds=${item.labelBoundsViolationCount}/nav-h=${item.navigationHeight}/item=${item.minimumWidth}×${item.minimumHeight}/font=${item.minimumLabelFontSize}`).join('; ')}` },
  ]
}

function assertAssertionContract(assertions) {
  const ids = assertions.map((item) => item.id)
  if (new Set(ids).size !== ids.length || JSON.stringify(ids) !== JSON.stringify(EXPECTED_ASSERTION_IDS)) {
    throw new Error(`Assertion contract drifted. Expected ${EXPECTED_ASSERTION_IDS.join(', ')}; received ${ids.join(', ')}`)
  }
}

async function main() {
  const htmlPath = path.join(__dirname, 'admin-users-invitations-evidence.html')
  const assetsDir = path.join(__dirname, 'assets')
  const localEvidenceDir = path.join(assetsDir, 'local-evidence')

  recoverEvidenceBeforeDependencies(assetsDir, localEvidenceDir)

  const { module: playwright, source: playwrightSource } = resolvePlaywright()
  const { chromium } = playwright
  const fontRoot = findFontRoot()
  const runToken = `${process.pid}-${Date.now()}`
  const stagingDir = path.join(assetsDir, `.local-evidence-staging-${runToken}`)
  const backupDir = path.join(assetsDir, `.local-evidence-backup-${runToken}`)
  fs.mkdirSync(stagingDir)

  let browser = null
  let backupCreated = false
  let newDirectoryInstalled = false
  let publicationValidated = false
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
      const checks = [400, 500, 600, 700].map((weight) => ({ weight, loaded: document.fonts.check(`${weight} 16px "Vazirmatn Evidence"`) }))
      const faces = [...document.fonts].filter((face) => face.family.replace(/["']/g, '').trim() === 'Vazirmatn Evidence').map((face) => ({ family: face.family, weight: face.weight, style: face.style, status: face.status }))
      return { loaded: checks.every((item) => item.loaded) && faces.length >= 4 && faces.every((face) => face.status === 'loaded'), checks, faces, computedBodyFamily: getComputedStyle(document.body).fontFamily }
    })

    const preflightDom = await page.content()
    const preflightRawDomHash = hashBuffer(Buffer.from(preflightDom))
    const preflightCanonicalDom = await canonicalDomSnapshot(page)
    const preflightDomHash = hashBuffer(Buffer.from(preflightCanonicalDom))
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

    const postCaptureDom = await page.content()
    const postCaptureRawDomHash = hashBuffer(Buffer.from(postCaptureDom))
    const postCaptureCanonicalDom = await canonicalDomSnapshot(page)
    const postCaptureDomHash = hashBuffer(Buffer.from(postCaptureCanonicalDom))
    if (postCaptureDomHash !== preflightDomHash) {
      let firstDifference = 0
      const sharedLength = Math.min(preflightCanonicalDom.length, postCaptureCanonicalDom.length)
      while (firstDifference < sharedLength && preflightCanonicalDom[firstDifference] === postCaptureCanonicalDom[firstDifference]) firstDifference += 1
      throw new Error(`Canonical DOM mutated during capture: ${preflightDomHash} -> ${postCaptureDomHash}; lengths=${preflightCanonicalDom.length}/${postCaptureCanonicalDom.length}; firstDifference=${firstDifference}; before=${JSON.stringify(preflightCanonicalDom.slice(Math.max(0, firstDifference - 80), firstDifference + 160))}; after=${JSON.stringify(postCaptureCanonicalDom.slice(Math.max(0, firstDifference - 80), firstDifference + 160))}`)
    }

    const measurements = await measureEvidence(page)
    const assertions = buildAssertions(measurements, font)
    assertAssertionContract(assertions)
    const failures = assertions.filter((item) => !item.passed)
    const assertionOutcomeDrift = assertions.some((item, index) => item.id !== preflightAssertions[index]?.id || item.passed !== preflightAssertions[index]?.passed || item.detail !== preflightAssertions[index]?.detail)
    if (failures.length > 0 || pageErrors.length > 0 || assertionOutcomeDrift) {
      throw new Error(`Evidence post-capture validation failed: ${failures.map((item) => `${item.id} (${item.detail})`).join(', ') || (assertionOutcomeDrift ? 'pre/post assertion outcome drift' : pageErrors.join(', '))}`)
    }

    const browserVersion = await browser.version()
    const report = {
      schemaVersion: 2,
      runId: runToken,
      evidenceRole: 'secondary-derivative-evidence',
      primaryDesignSource: 'Figma file z8jgJxST4O2APzWnlyP9gv',
      scope: 'Stage 0B-4 admin users and standard invitations; Market and Messenger interiors excluded',
      generatedAt: new Date().toISOString(),
      environment: { playwrightSource, browser: browserVersion, viewport: { width: 2400, height: 1800, deviceScaleFactor: 1 }, fontSourceRoot: fontRoot },
      integrity: { preflightCanonicalDomSha256: preflightDomHash, postCaptureCanonicalDomSha256: postCaptureDomHash, canonicalDomUnchangedDuringCapture: preflightDomHash === postCaptureDomHash, preflightRawDomSha256: preflightRawDomHash, postCaptureRawDomSha256: postCaptureRawDomHash, rawDomOnlyBrowserNormalization: preflightRawDomHash !== postCaptureRawDomHash && preflightDomHash === postCaptureDomHash, ignoredNormalization: 'empty style attributes added by Chromium screenshot caret handling', preAndPostAssertionsIdentical: !assertionOutcomeDrift },
      font,
      captures,
      measurements,
      preflightAssertions,
      assertions,
      summary: { passed: failures.length === 0 && pageErrors.length === 0 && !assertionOutcomeDrift, assertionCount: assertions.length, failureCount: failures.length, pageErrorCount: pageErrors.length },
      failures,
      pageErrors,
    }
    if (!report.summary.passed) throw new Error('Evidence validation failed before staging publication')
    fs.writeFileSync(path.join(stagingDir, METRICS_FILENAME), `${JSON.stringify(report, null, 2)}\n`)

    const stagedValidation = validateEvidenceDirectory(stagingDir)
    if (!stagedValidation.valid) throw new Error(`Evidence staging is incomplete: ${stagedValidation.problems.join('; ')}`)

    await browser.close()
    browser = null

    if (fs.existsSync(localEvidenceDir)) {
      const currentValidation = validateEvidenceDirectory(localEvidenceDir)
      if (!currentValidation.valid) throw new Error(`Current evidence failed validation immediately before swap: ${currentValidation.problems.join('; ')}`)
      fs.renameSync(localEvidenceDir, backupDir)
      backupCreated = true
    }
    fs.renameSync(stagingDir, localEvidenceDir)
    newDirectoryInstalled = true

    const publishedValidation = validateEvidenceDirectory(localEvidenceDir)
    if (!publishedValidation.valid) throw new Error(`Published evidence failed post-swap validation: ${publishedValidation.problems.join('; ')}`)
    publicationValidated = true
    if (backupCreated) fs.rmSync(backupDir, { recursive: true, force: true })
    backupCreated = false

    process.stdout.write(`${JSON.stringify({
      passed: true,
      captures: captures.map((item) => `${item.filename} (${item.pixelDimensions.width}×${item.pixelDimensions.height})`),
      mobileScenarios: measurements.mobileScenarios.map((item) => item.id),
      responsiveWidths: measurements.responsive.map((item) => `${item.raw.width}×${item.raw.height}`),
      desktop: `${measurements.desktop.raw.width}×${measurements.desktop.raw.height}`,
      minimumTarget: `${measurements.actions.minimumWidth}×${measurements.actions.minimumHeight}`,
      minimumCtaHeight: measurements.ctas.minimumHeight,
      fontFaces: font.faces.length,
      assertionCount: assertions.length,
      domSha256: preflightDomHash,
    }, null, 2)}\n`)
  } finally {
    if (browser) {
      try { await browser.close() } catch {}
    }
    try { fs.rmSync(stagingDir, { recursive: true, force: true }) } catch {}
    if (!publicationValidated) {
      try {
        if (newDirectoryInstalled && fs.existsSync(localEvidenceDir)) fs.rmSync(localEvidenceDir, { recursive: true, force: true })
        if (backupCreated && fs.existsSync(backupDir) && !fs.existsSync(localEvidenceDir)) fs.renameSync(backupDir, localEvidenceDir)
      } catch (recoveryError) {
        throw new Error(`Publication failed and rollback also failed: ${recoveryError.message}`)
      }
    }
  }
}

main().catch((error) => {
  console.error(error.stack || error.message)
  process.exitCode = 1
})
