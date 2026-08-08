const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')
const { pathToFileURL } = require('node:url')

const MOBILE_IDS = ['M01', 'M02', 'M03', 'M04', 'M05', 'M06', 'M07', 'M08', 'M09', 'M10']
const RESPONSIVE_WIDTHS = [360, 375, 390, 414, 430]
const NAV_DESTINATIONS = ['home', 'market', 'messenger', 'operations', 'account']
const NAV_LABELS = ['خانه', 'بازار', 'پیام‌رسان', 'عملیات', 'حساب']
const CANONICAL_ROUTES = ['/account', '/account/security', '/account/storage', '/account/notifications', '/profile', '/users/:id']
const LEGACY_REDIRECTS = [
  { from: '/settings', to: '/account/storage' },
  { from: '/notifications', to: '/account/notifications' },
]
const RECOVERY_GROUPS = [
  'account-loading',
  'account-error-retry',
  'account-inactive-restricted',
  'profile-loading',
  'profile-error-retry',
  'profile-unavailable',
  'profile-address-feedback',
  'session-loading',
  'session-true-empty',
  'session-error-retry',
  'session-termination-feedback',
  'session-permission',
  'storage-feedback',
  'notification-loading',
  'notification-empty-error',
]
const PUSH_STATES = ['checking', 'unsupported', 'insecure', 'server-disabled', 'permission-blocked', 'permission-default', 'subscribed', 'unsubscribed', 'error']
const PUSH_ACTION_STATES = ['permission-default', 'unsubscribed', 'error']
const VISIBILITY_ROWS = [
  { key: 'self', phone: 'full', address: 'full' },
  { key: 'normal', phone: 'masked', address: 'hidden' },
  { key: 'authorized-admin', phone: 'full', address: 'full' },
  { key: 'unavailable', phone: 'hidden', address: 'hidden' },
]
const SESSION_PERMISSION_ROWS = [
  { key: 'primary', view: 'yes', terminateOther: 'yes', terminateAllOther: 'yes' },
  { key: 'non-primary', view: 'yes', terminateOther: 'no', terminateAllOther: 'no' },
  { key: 'accountant', view: 'no', terminateOther: 'no', terminateAllOther: 'no' },
]
const TARGET_MIN = 44
const CTA_MIN = 48
const MIN_TEXT_CONTRAST = 4.5
const EXPECTED_ASSERTION_IDS = [
  'font-vazirmatn-loaded',
  'ten-mobile-scenarios-complete',
  'mobile-roots-exact-390x844',
  'no-product-overflow-or-clipping',
  'touch-targets-44',
  'cta-height-48',
  'responsive-width-sweep',
  'desktop-security-sessions-1440x900',
  'desktop-adds-no-facts',
  'shell-account-destination-invariant',
  'canonical-account-route-contract',
  'minimal-content-contract',
  'synthetic-identities-only',
  'account-hub-destinations-unique',
  'accountant-account-scope-bounded',
  'self-profile-progressive-disclosure',
  'profile-address-feedback-in-context',
  'public-profile-visibility-matrix-exact',
  'public-profile-actions-bounded',
  'session-list-metadata-bounded',
  'session-decision-feedback-in-context',
  'storage-action-feedback-in-context',
  'notification-center-metadata-bounded',
  'notification-empty-error-semantics-distinct',
  'push-state-matrix-complete-and-truthful',
  'recovery-state-atlas-complete',
  'protected-interiors-absent',
]
const METRICS_FILENAME = 'local-account-profile-security-notifications-validation-metrics.json'
const CAPTURES = [
  { selector: '#account-profile-scenarios', filename: 'local-account-profile-scenarios.png' },
  { selector: '#profile-visibility-matrix', filename: 'local-profile-visibility-matrix.png' },
  { selector: '#security-storage-scenarios', filename: 'local-security-storage-scenarios.png' },
  { selector: '#notification-center-scenarios', filename: 'local-notification-center-scenarios.png' },
  { selector: '#state-route-push-atlas', filename: 'local-state-route-push-atlas.png' },
  { selector: '#account-notifications-responsive-sweep', filename: 'local-account-notifications-responsive-sweep.png' },
  { selector: '[data-desktop-proof]', filename: 'local-desktop-security-sessions-1440x900.png', exactSize: { width: 1440, height: 900 } },
]
const EXPECTED_OUTPUT_FILES = [...CAPTURES.map((item) => item.filename), METRICS_FILENAME].sort()
const FIGMA_FREEZE = Object.freeze({
  fileKey: 'z8jgJxST4O2APzWnlyP9gv',
  pageId: '117:2',
  sectionIds: ['117:3', '117:4', '117:5', '117:6', '117:7', '117:8'],
  frozenAt: '2026-08-08T17:10:58.500Z',
  auditedAt: '2026-08-08T17:11:05.475Z',
  auditSchemaVersion: 2,
  auditAssertionCount: 27,
  auditAssertionsPassed: 27,
  sourceCommit: 'fa2a0a42934493752e7a7106e4dd10f168eb16d7',
  mobileRootIds: ['128:3', '128:64', '128:111', '129:132', '129:187', '131:823', '131:881', '131:935', '132:1627', '132:1683'],
  responsiveBoardId: '141:417',
  responsiveRootIds: ['141:456', '141:512', '141:568', '141:624', '141:680'],
  desktopBoardId: '143:632',
  desktopRootId: '143:668',
  recoveryBoardId: '136:409',
  visibilityRoutePermissionBoardId: '137:414',
  visibilityRowId: '149:687',
  pushBoardId: '138:414',
  nestedSubstateNodeIds: ['148:687', '148:694', '148:701', '148:708'],
  directAuditPath: 'assets/figma-stage0b5-audit-metrics.json',
  directAuditSha256: '351f6afafb0e2d3b1a08e908dcd88cb72d9d2fd4fed8110c3fb22c12c6658d94',
  auditMetrics: {
    designSystemInventory: {
      variableCount: 65,
      textStyleCount: 9,
      effectCount: 2,
      componentSetCount: 12,
      componentVariantCount: 54,
      boundInstanceCountOnStagePage: 77,
      detachedInstanceCountOnStagePage: 0,
    },
    geometry: {
      mobileRootCount: 10,
      mobileWidth: 390,
      mobileHeight: 844,
      responsiveRootCount: 5,
      responsiveWidths: [360, 375, 390, 414, 430],
      responsiveHeight: 844,
      desktopRootCount: 1,
      desktopWidth: 1440,
      desktopHeight: 900,
      desktopFactCount: 19,
      mobileReferenceFactCount: 19,
      overflowFailureCount: 0,
      textClipFailureCount: 0,
    },
    interaction: {
      semanticHitContainerCount: 142,
      minimumHitWidthPx: 44,
      minimumHitHeightPx: 44,
      boundButtonCount: 15,
      minimumButtonHeightPx: 48,
      minimumNavigationLabelFontSizePx: 11,
      detailHeaderHeightPx: 112,
      detailHeaderBackTargetWidthPx: 44,
      detailHeaderBackTargetHeightPx: 44,
    },
    stateCoverage: {
      recoveryGroupCount: 15,
      namedNestedSubstateCount: 14,
      pushStateCount: 9,
      pushStates: ['checking', 'unsupported', 'insecure', 'server-disabled', 'permission-blocked', 'permission-default', 'subscribed', 'unsubscribed', 'error'],
      semanticNames: ['Address/validation', 'Address/busy', 'Address/success', 'Address/failure', 'Session/busy', 'Session/success', 'Session/failure', 'Storage/busy', 'Storage/success', 'Storage/failure', 'Storage/valid-zero', 'Storage/size-error', 'Notification/true-empty', 'Notification/category-empty'],
    },
    typographyAndContrast: {
      fontFamilies: ['Vazirmatn'],
      missingFontCount: 0,
      visibleTextSampleCount: 401,
      textContrastFailureCount: 0,
      minimumTextContrastRatio: 4.548,
      minimumFocusContrastRatio: 3.972,
      focusStrokeWidthPx: 3,
      m04ContrastRatios: { label: 5.33, value: 15.84, privacy: 5.7, primaryCta: 4.55 },
    },
    contentAndScope: {
      syntheticIdentitiesOnly: true,
      rawRouteOrBackendMetadataHits: 0,
      bannedNoiseHits: 0,
      marketInteriorNodeCount: 0,
      messengerInteriorNodeCount: 0,
      protectedInteriors: ['market', 'messenger'],
      desktopAddsNewFacts: false,
      responsiveM09DataParity: true,
    },
    checks: {
      skipInvisibleInstanceChildren: false,
      allAssertionsPassed: true,
      assertionCount: 27,
      assertionFailureCount: 0,
      figmaDesignBlockerCount: 0,
    },
  },
})

function sha256Buffer(buffer) {
  return crypto.createHash('sha256').update(buffer).digest('hex')
}

function sha256File(filePath) {
  return sha256Buffer(fs.readFileSync(filePath))
}

function readPngDimensions(filePath) {
  const buffer = fs.readFileSync(filePath)
  if (buffer.length < 24 || buffer.subarray(0, 8).toString('hex') !== '89504e470d0a1a0a') {
    throw new Error(`Expected PNG: ${filePath}`)
  }
  return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) }
}

function assertExactArray(actual, expected, label) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${label} drift: ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`)
  }
}

function readAndValidateDirectFigmaAudit(stageDir) {
  const auditPath = path.join(stageDir, FIGMA_FREEZE.directAuditPath)
  if (!fs.existsSync(auditPath)) throw new Error(`Direct Figma audit is absent: ${auditPath}`)
  const actualSha256 = sha256File(auditPath)
  if (actualSha256 !== FIGMA_FREEZE.directAuditSha256) throw new Error(`Direct Figma audit SHA drift: ${actualSha256}`)
  const audit = JSON.parse(fs.readFileSync(auditPath, 'utf8'))
  const failures = []
  const same = (actual, expected, label) => {
    if (JSON.stringify(actual) !== JSON.stringify(expected)) failures.push(`${label}: ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`)
  }
  same(audit.schema, FIGMA_FREEZE.auditSchemaVersion, 'schema')
  same(audit.status, 'passed', 'status')
  same(audit.source?.fileKey, FIGMA_FREEZE.fileKey, 'fileKey')
  same(audit.source?.pageId, FIGMA_FREEZE.pageId, 'pageId')
  same(audit.source?.frozenAt, FIGMA_FREEZE.frozenAt, 'frozenAt')
  same(audit.source?.auditedAt, FIGMA_FREEZE.auditedAt, 'auditedAt')
  same(audit.source?.sourceCommit, FIGMA_FREEZE.sourceCommit, 'sourceCommit')
  same(Object.values(audit.sections || {}), FIGMA_FREEZE.sectionIds, 'sectionIds')
  same(audit.productRoots, FIGMA_FREEZE.mobileRootIds, 'mobileRootIds')
  same((audit.responsiveRoots || []).map((item) => item.id), FIGMA_FREEZE.responsiveRootIds, 'responsiveRootIds')
  same((audit.responsiveRoots || []).map((item) => item.width), FIGMA_FREEZE.auditMetrics.geometry.responsiveWidths, 'responsiveWidths')
  same((audit.responsiveRoots || []).map((item) => item.height), Array(5).fill(FIGMA_FREEZE.auditMetrics.geometry.responsiveHeight), 'responsiveHeights')
  same(audit.desktopRoot?.id, FIGMA_FREEZE.desktopRootId, 'desktopRootId')
  same(audit.desktopRoot?.boardId, FIGMA_FREEZE.desktopBoardId, 'desktopBoardId')
  same(audit.supplementalNodes?.recoveryAtlas, FIGMA_FREEZE.recoveryBoardId, 'recoveryBoardId')
  same(audit.supplementalNodes?.visibilityRoutePermissionMatrix, FIGMA_FREEZE.visibilityRoutePermissionBoardId, 'visibilityRoutePermissionBoardId')
  same(audit.supplementalNodes?.pushMatrix, FIGMA_FREEZE.pushBoardId, 'pushBoardId')
  same(audit.supplementalNodes?.visibilityForbiddenRow, FIGMA_FREEZE.visibilityRowId, 'visibilityRowId')
  same([audit.supplementalNodes?.addressSubstates, audit.supplementalNodes?.sessionSubstates, audit.supplementalNodes?.storageSubstates, audit.supplementalNodes?.notificationEmptySubstates], FIGMA_FREEZE.nestedSubstateNodeIds, 'nestedSubstateNodeIds')
  same(audit.designSystemInventory, FIGMA_FREEZE.auditMetrics.designSystemInventory, 'designSystemInventory')
  same(audit.geometry, FIGMA_FREEZE.auditMetrics.geometry, 'geometry')
  same(audit.interaction, FIGMA_FREEZE.auditMetrics.interaction, 'interaction')
  same(audit.stateCoverage, FIGMA_FREEZE.auditMetrics.stateCoverage, 'stateCoverage')
  same(audit.typographyAndContrast, FIGMA_FREEZE.auditMetrics.typographyAndContrast, 'typographyAndContrast')
  same(audit.contentAndScope, FIGMA_FREEZE.auditMetrics.contentAndScope, 'contentAndScope')
  same(audit.checks, FIGMA_FREEZE.auditMetrics.checks, 'checks')
  same((audit.assertions || []).map((item) => item.id), EXPECTED_ASSERTION_IDS, 'assertionIds')
  same((audit.assertions || []).map((item) => item.status), Array(27).fill('passed'), 'assertionStatuses')
  if (failures.length > 0) throw new Error(`Direct Figma audit contract mismatch:\n${failures.join('\n')}`)
  return {
    passed: true,
    path: FIGMA_FREEZE.directAuditPath,
    sha256: actualSha256,
    semanticHitContainerCount: audit.interaction.semanticHitContainerCount,
    canonicalMetricGroupsCompared: 7,
    assertionCount: audit.assertions.length,
    assertionFailures: audit.assertions.filter((item) => item.status !== 'passed').length,
  }
}

function validateEvidenceDirectory(directory, { allowSupersededCanonical = false } = {}) {
  const problems = []
  if (!fs.existsSync(directory) || !fs.statSync(directory).isDirectory()) {
    return { valid: false, problems: ['directory absent'], report: null }
  }
  const files = fs.readdirSync(directory).filter((name) => !name.startsWith('.')).sort()
  if (JSON.stringify(files) !== JSON.stringify(EXPECTED_OUTPUT_FILES)) problems.push(`file set drift: ${files.join(', ')}`)
  const metricsPath = path.join(directory, METRICS_FILENAME)
  let report = null
  try {
    report = JSON.parse(fs.readFileSync(metricsPath, 'utf8'))
  } catch (error) {
    problems.push(`metrics unreadable: ${error.message}`)
  }
  if (report) {
    const ids = Array.isArray(report.assertions) ? report.assertions.map((item) => item.id) : []
    if (JSON.stringify(ids) !== JSON.stringify(EXPECTED_ASSERTION_IDS)) problems.push('assertion set/order drift')
    if (report.summary?.passed !== true || report.summary?.assertionCount !== 27 || report.summary?.failureCount !== 0 || report.summary?.pageErrorCount !== 0) problems.push('report is not a clean 27/27 pass')
    if (!allowSupersededCanonical && JSON.stringify(report.canonicalFigma) !== JSON.stringify(FIGMA_FREEZE)) problems.push('canonical Figma freeze provenance drift')
    if (!allowSupersededCanonical && (report.directFigmaAuditValidation?.passed !== true || report.directFigmaAuditValidation?.sha256 !== FIGMA_FREEZE.directAuditSha256 || report.directFigmaAuditValidation?.semanticHitContainerCount !== 142)) problems.push('direct Figma audit reconciliation is absent or stale')
    const captureNames = Array.isArray(report.captures) ? report.captures.map((item) => item.filename).sort() : []
    const expectedNames = CAPTURES.map((item) => item.filename).sort()
    if (JSON.stringify(captureNames) !== JSON.stringify(expectedNames)) problems.push('capture set drift')
    for (const capture of report.captures || []) {
      const capturePath = path.join(directory, capture.filename)
      try {
        if (sha256File(capturePath) !== capture.sha256) problems.push(`hash mismatch: ${capture.filename}`)
        const dimensions = readPngDimensions(capturePath)
        if (dimensions.width !== capture.pixelDimensions?.width || dimensions.height !== capture.pixelDimensions?.height) problems.push(`dimension mismatch: ${capture.filename}`)
      } catch (error) {
        problems.push(`${capture.filename}: ${error.message}`)
      }
    }
  }
  return { valid: problems.length === 0, problems, report }
}

function recoverEvidenceBeforeDependencyResolution(assetsDir, localEvidenceDir) {
  fs.mkdirSync(assetsDir, { recursive: true })
  for (const name of fs.readdirSync(assetsDir).filter((item) => item.startsWith('.local-evidence-staging-'))) {
    fs.rmSync(path.join(assetsDir, name), { recursive: true, force: true })
  }
  let backups = fs.readdirSync(assetsDir).filter((item) => item.startsWith('.local-evidence-backup-')).map((item) => path.join(assetsDir, item)).sort()
  if (backups.length > 1) throw new Error('Ambiguous recovery: multiple local-evidence backups')
  if (fs.existsSync(localEvidenceDir) && fs.readdirSync(localEvidenceDir).length === 0) fs.rmSync(localEvidenceDir, { recursive: true, force: true })
  if (!fs.existsSync(localEvidenceDir) && backups.length === 1) {
    const validation = validateEvidenceDirectory(backups[0], { allowSupersededCanonical: true })
    if (!validation.valid) throw new Error(`Interrupted backup invalid: ${validation.problems.join('; ')}`)
    fs.renameSync(backups[0], localEvidenceDir)
    backups = []
  } else if (fs.existsSync(localEvidenceDir) && backups.length === 1) {
    const current = validateEvidenceDirectory(localEvidenceDir, { allowSupersededCanonical: true })
    const backup = validateEvidenceDirectory(backups[0], { allowSupersededCanonical: true })
    if (current.valid) {
      fs.rmSync(backups[0], { recursive: true, force: true })
      backups = []
    } else if (backup.valid) {
      fs.rmSync(localEvidenceDir, { recursive: true, force: true })
      fs.renameSync(backups[0], localEvidenceDir)
      backups = []
    } else {
      throw new Error(`Recovery refused: current and backup invalid (${current.problems.join('; ')} / ${backup.problems.join('; ')})`)
    }
  }
  if (fs.existsSync(localEvidenceDir)) {
    const current = validateEvidenceDirectory(localEvidenceDir, { allowSupersededCanonical: true })
    if (!current.valid) throw new Error(`Published local evidence is invalid; refusing overwrite: ${current.problems.join('; ')}`)
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
  throw new Error(`Playwright unavailable. Tried:\n${failures.join('\n')}`)
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
  throw new Error(`Sharp unavailable for browser fractional-pixel normalization. Tried:\n${failures.join('\n')}`)
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
  throw new Error(`Vazirmatn webfonts unavailable. Tried: ${candidates.join(', ')}`)
}

function embeddedFontCss(fontRoot) {
  const faces = [
    ['Vazirmatn-Regular.woff2', 400],
    ['Vazirmatn-Medium.woff2', 500],
    ['Vazirmatn-SemiBold.woff2', 600],
    ['Vazirmatn-Bold.woff2', 700],
  ]
  return `${faces.map(([filename, weight]) => `@font-face{font-family:"Vazirmatn Evidence";src:url(data:font/woff2;base64,${fs.readFileSync(path.join(fontRoot, filename)).toString('base64')}) format("woff2");font-style:normal;font-weight:${weight};font-display:block;}`).join('\n')}\nbody,body *{font-family:"Vazirmatn Evidence","Vazirmatn",Tahoma,Arial,sans-serif!important}`
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
  if (capture.exactSize) {
    const box = await locator.boundingBox()
    if (!box || Math.abs(box.width - capture.exactSize.width) > .01 || Math.abs(box.height - capture.exactSize.height) > .01) {
      throw new Error(`${capture.selector} DOM root is ${box?.width}×${box?.height}; expected exact ${capture.exactSize.width}×${capture.exactSize.height}`)
    }
    const rawPath = `${destination}.raw.png`
    await locator.screenshot({ path: rawPath, animations: 'disabled' })
    const raw = readPngDimensions(rawPath)
    if (raw.width < capture.exactSize.width || raw.height < capture.exactSize.height || raw.width - capture.exactSize.width > 1 || raw.height - capture.exactSize.height > 1) {
      throw new Error(`${capture.selector} browser raster is ${raw.width}×${raw.height}; expected exact root plus at most one fractional-pixel edge`)
    }
    if (raw.width === capture.exactSize.width && raw.height === capture.exactSize.height) {
      fs.renameSync(rawPath, destination)
    } else {
      const sharp = resolveSharp()
      await sharp(rawPath).extract({ left: 0, top: 0, width: capture.exactSize.width, height: capture.exactSize.height }).toFile(destination)
      fs.rmSync(rawPath)
    }
  } else {
    await locator.screenshot({ path: destination, animations: 'disabled' })
  }
  const pixelDimensions = readPngDimensions(destination)
  if (capture.exactSize && (pixelDimensions.width !== capture.exactSize.width || pixelDimensions.height !== capture.exactSize.height)) {
    throw new Error(`${capture.filename} is ${pixelDimensions.width}×${pixelDimensions.height}; expected exact ${capture.exactSize.width}×${capture.exactSize.height}`)
  }
  return { selector: capture.selector, filename: capture.filename, pixelDimensions, sha256: sha256File(destination) }
}

async function measureEvidence(page) {
  return page.evaluate(({ mobileIds, responsiveWidths, navDestinations, canonicalRoutes, recoveryGroups, pushStates, pushActionStates, targetMin, ctaMin, minTextContrast }) => {
    const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim()
    const visible = (element) => {
      if (!element || !(element instanceof Element) || element.hidden) return false
      const rect = element.getBoundingClientRect()
      const style = getComputedStyle(element)
      return rect.width > 0 && rect.height > 0 && element.getClientRects().length > 0 && style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity) > .01
    }
    const rect = (element) => {
      const value = element.getBoundingClientRect()
      return { width: Number(value.width.toFixed(3)), height: Number(value.height.toFixed(3)), left: Number(value.left.toFixed(3)), top: Number(value.top.toFixed(3)), right: Number(value.right.toFixed(3)), bottom: Number(value.bottom.toFixed(3)) }
    }
    const inside = (inner, outer, tolerance = 1.1) => inner.left >= outer.left - tolerance && inner.top >= outer.top - tolerance && inner.right <= outer.right + tolerance && inner.bottom <= outer.bottom + tolerance
    const textOf = (element) => normalize(element?.innerText || element?.textContent)
    const elements = (root, selector) => [...root.querySelectorAll(selector)].filter(visible)
    const navContract = (navigation, context) => {
      const links = elements(navigation, 'a[data-nav-destination]')
      return {
        context,
        destinations: links.map((item) => item.dataset.navDestination),
        labels: links.map((item) => normalize(item.getAttribute('aria-label') || item.textContent)),
        active: links.filter((item) => item.classList.contains('is-active')).map((item) => item.dataset.navDestination),
        svgCount: elements(navigation, 'svg').length,
        minimumWidth: links.length ? Math.min(...links.map((item) => item.getBoundingClientRect().width)) : 0,
        minimumHeight: links.length ? Math.min(...links.map((item) => item.getBoundingClientRect().height)) : 0,
        minimumLabelFontSize: links.length ? Math.min(...links.map((item) => Number.parseFloat(getComputedStyle(item.querySelector('span')).fontSize))) : 0,
        labelBoundsViolations: links.filter((item) => !inside(item.querySelector('span').getBoundingClientRect(), item.getBoundingClientRect())).length,
        navigationHeight: navigation.getBoundingClientRect().height,
      }
    }
    const notificationFacts = (root) => elements(root, '[data-notification-id]').map((item) => ({
      id: item.dataset.notificationId,
      content: item.dataset.content,
      time: item.dataset.time,
      routeState: item.dataset.routeState,
      isNew: item.dataset.new,
      interactive: item.matches('button,a[href]') || elements(item, 'button,a[href]').length > 0,
      newDotCount: elements(item, '.new-dot').length,
    }))
    const sessionFacts = (root) => elements(root, '[data-session-id]').map((item) => ({
      id: item.dataset.sessionId,
      device: item.dataset.device,
      platform: item.dataset.platform,
      activity: item.dataset.activity,
      signal: item.dataset.signal,
    }))

    const surfaces = [...document.querySelectorAll('[data-product-surface]')].filter(visible)
    const geometry = surfaces.map((surface, index) => {
      const surfaceRect = surface.getBoundingClientRect()
      const descendantsOutside = [...surface.querySelectorAll('*')].filter(visible).filter((item) => !inside(item.getBoundingClientRect(), surfaceRect)).map((item) => ({ tag: item.tagName.toLowerCase(), className: String(item.className || ''), text: textOf(item).slice(0, 70) }))
      const clippedText = [...surface.querySelectorAll('h1,h2,h3,h4,h5,p,strong,small,label,span,button,a,code')].filter(visible).filter((item) => {
        if (![...item.childNodes].some((node) => node.nodeType === Node.TEXT_NODE && normalize(node.textContent))) return false
        const style = getComputedStyle(item)
        if (style.textOverflow === 'ellipsis' || style.webkitLineClamp !== 'none') return false
        return item.scrollWidth > item.clientWidth + .6 || item.scrollHeight > item.clientHeight + .6
      }).map((item) => ({ tag: item.tagName.toLowerCase(), text: textOf(item).slice(0, 80), client: `${item.clientWidth}×${item.clientHeight}`, scroll: `${item.scrollWidth}×${item.scrollHeight}` }))
      return {
        id: surface.dataset.productScreen || surface.dataset.recoveryGroup || surface.dataset.pushState || (surface.hasAttribute('data-responsive-proof') ? `responsive-${surface.style.getPropertyValue('--responsive-width')}` : surface.hasAttribute('data-desktop-proof') ? 'desktop' : `surface-${index}`),
        rect: rect(surface),
        overflowX: surface.scrollWidth > surface.clientWidth + 1,
        overflowY: surface.scrollHeight > surface.clientHeight + 1,
        descendantsOutside,
        clippedText,
      }
    })
    const screenContentOverflow = elements(document, '[data-mobile-scenario] .screen-content,[data-responsive-proof] .screen-content').map((item) => ({ overflowX: item.scrollWidth > item.clientWidth + 1, overflowY: item.scrollHeight > item.clientHeight + 1, rect: rect(item), scroll: { width: item.scrollWidth, height: item.scrollHeight } }))

    const actions = surfaces.flatMap((surface) => elements(surface, 'button,a[href],input,select,textarea')).map((item, index) => ({
      id: item.id || `${item.closest('[data-product-screen]')?.dataset.productScreen || item.closest('[data-recovery-group]')?.dataset.recoveryGroup || 'surface'}-${index}`,
      label: normalize(item.getAttribute('aria-label') || item.value || item.textContent).slice(0, 90),
      rect: rect(item),
      disabled: Boolean(item.disabled),
    }))
    const ctas = surfaces.flatMap((surface) => elements(surface, '.cta')).map((item) => ({ label: textOf(item).slice(0, 90), rect: rect(item) }))

    const mobile = elements(document, '[data-mobile-scenario]').map((root) => {
      const navigation = root.querySelector('[data-shell-navigation]')
      return {
        id: root.dataset.productScreen,
        rect: rect(root),
        synthetic: root.dataset.synthetic,
        navigation: navigation ? navContract(navigation, 'mobile') : null,
        directContentChildren: root.querySelector('.screen-content')?.firstElementChild?.children.length || 0,
      }
    })
    const m09 = document.querySelector('[data-product-screen="M09"]')
    const m09Facts = notificationFacts(m09)
    const responsive = responsiveWidths.map((width) => {
      const wrapper = document.querySelector(`[data-responsive-width="${width}"]`)
      const root = wrapper?.querySelector('[data-responsive-proof]')
      return {
        requestedWidth: width,
        found: Boolean(root),
        rect: root ? rect(root) : null,
        signature: root?.dataset.m09Signature || null,
        synthetic: root?.dataset.synthetic || null,
        facts: root ? notificationFacts(root) : [],
        navigation: root?.querySelector('[data-shell-navigation]') ? navContract(root.querySelector('[data-shell-navigation]'), 'responsive') : null,
        overflowX: Boolean(root && root.scrollWidth > root.clientWidth + 1),
        overflowY: Boolean(root && root.scrollHeight > root.clientHeight + 1),
      }
    })
    const desktopRoot = document.querySelector('[data-desktop-proof]')
    const m06 = document.querySelector('[data-product-screen="M06"]')
    const desktop = {
      rect: rect(desktopRoot),
      synthetic: desktopRoot.dataset.synthetic,
      navigation: navContract(desktopRoot.querySelector('[data-shell-navigation]'), 'desktop'),
      sessionFacts: sessionFacts(desktopRoot),
      mobileSessionFacts: sessionFacts(m06),
      currentSessionSignalCount: elements(desktopRoot, '[data-current-session-signal]').length,
      kpiCount: elements(desktopRoot, '[data-kpi],.kpi,[data-summary-count]').length,
      extraFactCount: elements(desktopRoot, '[data-extra-fact],[data-server-metadata],[data-current-role]').length,
      overflowX: desktopRoot.scrollWidth > desktopRoot.clientWidth + 1,
      overflowY: desktopRoot.scrollHeight > desktopRoot.clientHeight + 1,
    }

    const textParents = []
    const textSeen = new Set()
    for (const surface of surfaces) {
      const walker = document.createTreeWalker(surface, NodeFilter.SHOW_TEXT)
      let node
      while ((node = walker.nextNode())) {
        if (!normalize(node.textContent) || !visible(node.parentElement) || textSeen.has(node.parentElement)) continue
        textSeen.add(node.parentElement)
        textParents.push(node.parentElement)
      }
    }
    const parseRgb = (value) => {
      const match = String(value).match(/rgba?\((\d+(?:\.\d+)?)[, ]+(\d+(?:\.\d+)?)[, ]+(\d+(?:\.\d+)?)(?:[, /]+(\d+(?:\.\d+)?))?\)/)
      return match ? { r: Number(match[1]), g: Number(match[2]), b: Number(match[3]), a: match[4] === undefined ? 1 : Number(match[4]) } : null
    }
    const luminance = ({ r, g, b }) => {
      const channel = (value) => { const normalized = value / 255; return normalized <= .03928 ? normalized / 12.92 : ((normalized + .055) / 1.055) ** 2.4 }
      return .2126 * channel(r) + .7152 * channel(g) + .0722 * channel(b)
    }
    const contrast = (first, second) => { const a = luminance(first); const b = luminance(second); return (Math.max(a, b) + .05) / (Math.min(a, b) + .05) }
    const background = (element) => {
      for (let current = element; current; current = current.parentElement) {
        const parsed = parseRgb(getComputedStyle(current).backgroundColor)
        if (parsed && parsed.a >= .98) return parsed
      }
      return { r: 255, g: 255, b: 255, a: 1 }
    }
    const contrastRows = textParents.map((item) => {
      const color = parseRgb(getComputedStyle(item).color)
      const backgroundColor = background(item)
      return { text: normalize(item.textContent).slice(0, 70), ratio: color ? Number(contrast(color, backgroundColor).toFixed(3)) : 0, fontSize: Number.parseFloat(getComputedStyle(item).fontSize), fontWeight: Number.parseInt(getComputedStyle(item).fontWeight, 10), color: getComputedStyle(item).color, background: getComputedStyle(item).backgroundColor }
    })
    const fontViolations = textParents.map((item) => ({ text: normalize(item.textContent).slice(0, 60), family: getComputedStyle(item).fontFamily })).filter((item) => !item.family.includes('Vazirmatn Evidence'))

    const focusTarget = document.querySelector('[data-product-screen="M01"] [data-account-destination="profile"]')
    focusTarget.focus()
    const focusStyle = getComputedStyle(focusTarget)
    const focusColor = parseRgb(focusStyle.outlineColor)
    const focusBackground = background(focusTarget)
    const focus = { width: Number.parseFloat(focusStyle.outlineWidth), style: focusStyle.outlineStyle, color: focusStyle.outlineColor, contrast: focusColor ? Number(contrast(focusColor, focusBackground).toFixed(3)) : 0 }
    focusTarget.blur()

    const allProductText = normalize(surfaces.map(textOf).join(' '))
    const entireTextAndValues = normalize(`${document.body.innerText} ${[...document.querySelectorAll('input,textarea')].map((item) => item.value).join(' ')}`)
    const forbiddenMinimalTerms = ['تعداد روابط', 'تعداد رابطه', 'کل روابط', 'نقش فعلی', 'وضعیت دسترسی', 'خلاصه دسترسی', 'home_server', 'backend', 'API']
    const knownRealData = ['محمدعلی همتی', 'امین تکبیری', 'امین روحی', 'مریم حسینی', 'mohammad.hemmati', 'amin.takbiri', '09123456789', '09356421180']
    const phoneViolations = entireTextAndValues.match(/(?:\+98|0098|09)\d{9}/g) || []
    const defaultUnits = elements(document, '[data-default-unit]').map((item) => ({ necessity: item.dataset.necessity || '', text: textOf(item).slice(0, 80) }))

    const accountDestinations = elements(document.querySelector('[data-product-screen="M01"]'), '[data-account-destination]').map((item) => item.dataset.accountDestination)
    const accountantRoot = document.querySelector('[data-product-screen="M02"]')
    const accountantDestinations = elements(accountantRoot, '[data-account-destination]').map((item) => item.dataset.accountDestination)
    const accountantText = textOf(accountantRoot)
    const m03 = document.querySelector('[data-product-screen="M03"]')
    const m04 = document.querySelector('[data-product-screen="M04"]')
    const m05 = document.querySelector('[data-product-screen="M05"]')
    const m07 = document.querySelector('[data-product-screen="M07"]')
    const m08 = document.querySelector('[data-product-screen="M08"]')
    const m10 = document.querySelector('[data-product-screen="M10"]')

    const visibility = elements(document, '[data-visibility-row]').map((item) => ({ key: item.dataset.visibilityRow, phone: item.dataset.phone, address: item.dataset.address }))
    const canonical = elements(document, '[data-route-row="canonical"]').map((item) => normalize(item.querySelector('code').textContent))
    const legacy = elements(document, '[data-route-row="legacy-redirect"]').map((item) => ({ from: normalize(item.querySelector('code').textContent), to: normalize(item.lastElementChild.textContent) }))
    const sessionPermissions = elements(document, '[data-session-permission-row]').map((item) => ({ key: item.dataset.sessionPermissionRow, view: item.dataset.view, terminateOther: item.dataset.terminateOther, terminateAllOther: item.dataset.terminateAllOther }))

    const recovery = elements(document, '[data-recovery-group]').map((group) => ({
      key: group.dataset.recoveryGroup,
      nested: elements(group, '[data-nested-state]').map((item) => item.dataset.nestedState),
      actionCount: elements(group, 'button,a[href]').length,
      preservedInput: group.dataset.preservedInput || null,
      failurePreserved: group.querySelector('[data-nested-state="failure"]')?.dataset.inputPreserved || null,
      text: textOf(group),
    }))
    const push = elements(document, '[data-push-state]').map((state) => ({ key: state.dataset.pushState, actions: elements(state, '[data-push-state-action]').map((item) => item.dataset.pushStateAction), text: textOf(state) }))
    const navigationContracts = [
      ...mobile.map((item) => item.navigation),
      ...responsive.map((item) => item.navigation),
      desktop.navigation,
    ].filter(Boolean)

    const protectedLexicon = ['ثبت آفر', 'آفر خرید', 'آفر فروش', 'قیمت خرید', 'قیمت فروش', 'گفت‌وگو', 'گفتگو', 'ارسال فایل', 'نوشتن پیام']
    const protectedTextViolations = []
    for (const surface of surfaces) {
      const walker = document.createTreeWalker(surface, NodeFilter.SHOW_TEXT)
      let node
      while ((node = walker.nextNode())) {
        const value = normalize(node.textContent)
        if (!value || !visible(node.parentElement)) continue
        if (protectedLexicon.some((term) => value.includes(term))) protectedTextViolations.push(value)
        if ((value.includes('بازار') || value.includes('پیام‌رسان')) && !node.parentElement.closest('[data-shell-navigation]')) protectedTextViolations.push(value)
      }
    }

    return {
      geometry,
      screenContentOverflow,
      actions: { count: actions.length, minimumWidth: Math.min(...actions.map((item) => item.rect.width)), minimumHeight: Math.min(...actions.map((item) => item.rect.height)), violations: actions.filter((item) => item.rect.width < targetMin || item.rect.height < targetMin) },
      ctas: { count: ctas.length, minimumHeight: Math.min(...ctas.map((item) => item.rect.height)), violations: ctas.filter((item) => item.rect.height < ctaMin) },
      mobile,
      responsive,
      m09Facts,
      desktop,
      typography: { textNodeCount: textParents.length, fontViolations, contrastRows, minimumContrast: Math.min(...contrastRows.map((item) => item.ratio)), contrastViolations: contrastRows.filter((item) => item.ratio < minTextContrast) },
      focus,
      minimalism: { defaultUnits, forbiddenTerms: forbiddenMinimalTerms.filter((term) => allProductText.toLocaleLowerCase('en-US').includes(term.toLocaleLowerCase('en-US'))), summaryCount: elements(document, '[data-product-surface] [data-summary-count],[data-product-surface] [data-kpi]').length },
      synthetic: { rootFlags: [...mobile.map((item) => item.synthetic), ...responsive.map((item) => item.synthetic), desktop.synthetic], realDataViolations: knownRealData.filter((term) => entireTextAndValues.toLocaleLowerCase('en-US').includes(term.toLocaleLowerCase('en-US'))), phoneViolations },
      shell: { navigationContracts },
      routes: { canonical, legacy, expectedCanonicalCount: canonicalRoutes.length },
      accountHub: {
        destinations: accountDestinations,
        headerBackCount: elements(document.querySelector('[data-product-screen="M01"] .mobile-header'), 'button[aria-label="بازگشت"],a[aria-label="بازگشت"]').length,
        positiveStatusCount: elements(document.querySelector('[data-product-screen="M01"] .mobile-header'), '.status,.mini-status,[data-positive-status]').length,
        headerSubtitle: textOf(document.querySelector('[data-product-screen="M01"] .mobile-title p')),
      },
      accountant: {
        destinations: accountantDestinations,
        text: accountantText,
        forbiddenElements: elements(accountantRoot, '[data-session],[data-logout],[data-telegram],[data-account-destination="security"],[data-account-destination="telegram"]').length,
        headerBackCount: elements(accountantRoot.querySelector('.mobile-header'), 'button[aria-label="بازگشت"],a[aria-label="بازگشت"]').length,
        roleChipCount: elements(accountantRoot.querySelector('.mobile-header'), '.status,.mini-status,[data-role-chip]').length,
        headerSubtitle: textOf(accountantRoot.querySelector('.mobile-title p')),
      },
      selfProfile: {
        onDemandCount: elements(m03, '[data-profile-secondary="on-demand"]').length,
        defaultSecondaryCount: elements(m03, '[data-profile-secondary="default"]').length,
        phoneMode: m03.querySelector('[data-self-phone]')?.dataset.selfPhone || '',
        phoneText: textOf(m03.querySelector('[data-self-phone]')),
        addressMode: m03.querySelector('[data-self-address]')?.dataset.selfAddress || '',
        addressText: textOf(m03.querySelector('[data-self-address]')),
      },
      profileAddress: { inputValue: m04.querySelector('[data-address-input]')?.value || '', contextFeedbackCount: elements(m04, '[data-address-feedback="context"]').length },
      visibility,
      publicProfile: { phone: m05.querySelector('[data-public-phone]')?.dataset.publicPhone || '', address: m05.querySelector('[data-public-address]')?.dataset.publicAddress || '', actionMode: m05.querySelector('[data-public-actions]')?.dataset.publicActions || '', relationSurfaceCount: elements(m05, '[data-relation-surface],[data-history-surface]').length },
      sessions: { facts: sessionFacts(m06), inventoryScope: m06.querySelector('[data-session-inventory]')?.dataset.sessionInventory || '', currentSessionSignalCount: elements(m06, '[data-current-session-signal]').length, forbiddenMetadataCount: elements(m06, '[data-ip],[data-server-metadata],[data-token]').length, forbiddenMetadataTerms: ['IP', 'آی‌پی', 'سرور', 'توکن'].filter((term) => textOf(m06).includes(term)) },
      sessionDecision: { ackCount: elements(m07, '[data-session-ack][aria-pressed="true"]').length, confirmCount: elements(m07, '[data-session-confirm]').length, outcomeCount: elements(m07, '[data-session-inline-outcome="failure-preserved"]').length, text: textOf(m07) },
      storage: { scope: m08.querySelector('[data-storage-scope]')?.dataset.storageScope || '', size: m08.querySelector('[data-storage-size]')?.dataset.storageSize || '', clearActionCount: elements(m08, '[data-clear-local-storage]').length, outcomeCount: elements(m08, '[data-storage-inline-outcome="failure"]').length, text: textOf(m08) },
      notifications: { tabs: elements(m09, '[role="tab"]').map(textOf), facts: m09Facts, countElements: elements(m09, '[data-count],[data-total],.count').length, rawRouteElements: elements(m09, '[data-raw-route]').length, newSignalCount: elements(m09, '.new-dot').length },
      pushMobile: { state: m10.dataset.pushMobileState, actions: elements(m10, '[data-push-action]').map((item) => item.dataset.pushAction) },
      recovery,
      push,
      visibilityRows: visibility,
      sessionPermissions,
      protectedScope: { marketInteriorCount: elements(document, '[data-market-interior]').length, messengerInteriorCount: elements(document, '[data-messenger-interior]').length, protectedTextViolations },
    }
  }, { mobileIds: MOBILE_IDS, responsiveWidths: RESPONSIVE_WIDTHS, navDestinations: NAV_DESTINATIONS, canonicalRoutes: CANONICAL_ROUTES, recoveryGroups: RECOVERY_GROUPS, pushStates: PUSH_STATES, pushActionStates: PUSH_ACTION_STATES, targetMin: TARGET_MIN, ctaMin: CTA_MIN, minTextContrast: MIN_TEXT_CONTRAST })
}

function buildAssertions(metrics, font) {
  const scenarioIds = metrics.mobile.map((item) => item.id)
  const geometryViolations = metrics.geometry.filter((item) => item.overflowX || item.overflowY || item.descendantsOutside.length > 0 || item.clippedText.length > 0)
  const contentOverflow = metrics.screenContentOverflow.filter((item) => item.overflowX || item.overflowY)
  const expectedM09Signature = 'trade-request|اکنون|actionable|new;trade-final|۱۰ دقیقه پیش|actionable|read'
  const expectedNotificationFacts = [
    { id: 'trade-request', content: 'درخواست معامله تازه‌ای ثبت شد.', time: 'اکنون', routeState: 'actionable', isNew: 'true', interactive: true, newDotCount: 1 },
    { id: 'trade-final', content: 'معامله شما نهایی شد؛ نتیجه را مشاهده کنید.', time: '۱۰ دقیقه پیش', routeState: 'actionable', isNew: 'false', interactive: true, newDotCount: 0 },
  ]
  const expectedSessionFacts = [
    { id: 'pixel-current', device: 'Pixel 8', platform: 'Chrome · Android', activity: 'اکنون', signal: 'current' },
    { id: 'iphone-other', device: 'iPhone 15', platform: 'Safari · iOS', activity: '۱ ساعت پیش', signal: 'other' },
  ]
  const recoveryByKey = Object.fromEntries(metrics.recovery.map((item) => [item.key, item]))
  const pushByKey = Object.fromEntries(metrics.push.map((item) => [item.key, item]))
  const expectedAddressStates = ['validation', 'busy', 'success', 'failure']
  const expectedSessionTriad = ['busy', 'success', 'failure']
  const expectedStorageStates = ['busy', 'success', 'failure', 'valid-zero', 'size-error']
  const expectedNotificationStates = ['true-empty', 'category-empty', 'error-retry', 'route-less', 'realtime-new']
  const expectedNavigation = (item) => JSON.stringify(item.destinations) === JSON.stringify(NAV_DESTINATIONS) && JSON.stringify(item.labels) === JSON.stringify(NAV_LABELS) && JSON.stringify(item.active) === JSON.stringify(['account']) && item.svgCount === 5 && item.labelBoundsViolations === 0 && item.minimumWidth >= (item.context === 'desktop' ? 48 : 44) && item.minimumHeight >= (item.context === 'desktop' ? 48 : 52) && item.minimumLabelFontSize >= 11 && (item.context === 'desktop' || item.navigationHeight >= 78)
  const routeContractPass = JSON.stringify(metrics.routes.canonical) === JSON.stringify(CANONICAL_ROUTES) && JSON.stringify(metrics.routes.legacy) === JSON.stringify(LEGACY_REDIRECTS)
  const publicVisibilityPass = JSON.stringify(metrics.visibility) === JSON.stringify(VISIBILITY_ROWS)
  const sessionPermissionsPass = JSON.stringify(metrics.sessionPermissions) === JSON.stringify(SESSION_PERMISSION_ROWS)
  const pushActionTruth = PUSH_STATES.every((key) => {
    const actions = pushByKey[key]?.actions || []
    return PUSH_ACTION_STATES.includes(key) ? actions.length === 1 : actions.length === 0
  })
  return [
    { id: 'font-vazirmatn-loaded', passed: font.loaded && font.faces.length >= 4 && metrics.typography.fontViolations.length === 0, detail: `${font.faces.length} loaded faces; family violations=${metrics.typography.fontViolations.length}` },
    { id: 'ten-mobile-scenarios-complete', passed: JSON.stringify(scenarioIds) === JSON.stringify(MOBILE_IDS), detail: scenarioIds.join(', ') },
    { id: 'mobile-roots-exact-390x844', passed: metrics.mobile.every((item) => item.rect.width === 390 && item.rect.height === 844), detail: metrics.mobile.map((item) => `${item.id}:${item.rect.width}×${item.rect.height}`).join(', ') },
    { id: 'no-product-overflow-or-clipping', passed: geometryViolations.length === 0 && contentOverflow.length === 0, detail: `surface=${geometryViolations.map((item) => `${item.id}[overflow=${item.overflowX}/${item.overflowY},bounds=${item.descendantsOutside.length},text=${item.clippedText.length}]`).join('; ') || 'none'}; content=${contentOverflow.length}` },
    { id: 'touch-targets-44', passed: metrics.actions.count > 0 && metrics.actions.violations.length === 0, detail: `count=${metrics.actions.count}; minimum=${metrics.actions.minimumWidth}×${metrics.actions.minimumHeight}; violations=${metrics.actions.violations.length}` },
    { id: 'cta-height-48', passed: metrics.ctas.count > 0 && metrics.ctas.violations.length === 0, detail: `count=${metrics.ctas.count}; minimum=${metrics.ctas.minimumHeight}; violations=${metrics.ctas.violations.length}` },
    { id: 'responsive-width-sweep', passed: metrics.responsive.length === 5 && metrics.responsive.every((item) => item.found && item.rect.width === item.requestedWidth && item.rect.height === 844 && item.signature === expectedM09Signature && JSON.stringify(item.facts) === JSON.stringify(expectedNotificationFacts) && item.synthetic === 'true' && !item.overflowX && !item.overflowY), detail: metrics.responsive.map((item) => `${item.requestedWidth}:${item.rect?.width}×${item.rect?.height}/same=${JSON.stringify(item.facts) === JSON.stringify(expectedNotificationFacts)}`).join(', ') },
    { id: 'desktop-security-sessions-1440x900', passed: metrics.desktop.rect.width === 1440 && metrics.desktop.rect.height === 900 && !metrics.desktop.overflowX && !metrics.desktop.overflowY, detail: `${metrics.desktop.rect.width}×${metrics.desktop.rect.height}; overflow=${metrics.desktop.overflowX}/${metrics.desktop.overflowY}` },
    { id: 'desktop-adds-no-facts', passed: JSON.stringify(metrics.desktop.sessionFacts) === JSON.stringify(expectedSessionFacts) && JSON.stringify(metrics.desktop.mobileSessionFacts) === JSON.stringify(expectedSessionFacts) && metrics.desktop.currentSessionSignalCount === 1 && metrics.desktop.kpiCount === 0 && metrics.desktop.extraFactCount === 0, detail: `same-session-facts=${JSON.stringify(metrics.desktop.sessionFacts) === JSON.stringify(metrics.desktop.mobileSessionFacts)}; current-signal=${metrics.desktop.currentSessionSignalCount}; kpi=${metrics.desktop.kpiCount}; extra=${metrics.desktop.extraFactCount}` },
    { id: 'shell-account-destination-invariant', passed: metrics.shell.navigationContracts.length === 16 && metrics.shell.navigationContracts.every(expectedNavigation) && metrics.focus.width >= 3 && metrics.focus.style === 'solid' && metrics.focus.contrast >= 3, detail: `nav=${metrics.shell.navigationContracts.length}; valid=${metrics.shell.navigationContracts.filter(expectedNavigation).length}; focus=${metrics.focus.width}px/${metrics.focus.contrast}:1` },
    { id: 'canonical-account-route-contract', passed: routeContractPass, detail: `canonical=${metrics.routes.canonical.join(',')}; legacy=${metrics.routes.legacy.map((item) => `${item.from}->${item.to}`).join(',')}` },
    { id: 'minimal-content-contract', passed: metrics.minimalism.defaultUnits.length > 0 && metrics.minimalism.defaultUnits.every((item) => item.necessity === 'keep') && metrics.minimalism.forbiddenTerms.length === 0 && metrics.minimalism.summaryCount === 0 && metrics.typography.contrastViolations.length === 0 && metrics.accountHub.headerBackCount === 0 && metrics.accountHub.positiveStatusCount === 0 && metrics.accountHub.headerSubtitle === 'نگار پارسا' && metrics.accountant.headerBackCount === 0 && metrics.accountant.roleChipCount === 0 && metrics.accountant.headerSubtitle === 'رها نیکویی' && metrics.selfProfile.phoneMode === 'full' && metrics.selfProfile.phoneText === 'sample.user · ۰۹۱۲ ۰۰۰ ۰۰۰۰' && metrics.selfProfile.addressMode === 'full' && metrics.selfProfile.addressText === 'تهران، خیابان نمونه، پلاک ۱۲', detail: `units=${metrics.minimalism.defaultUnits.length}; unjustified=${metrics.minimalism.defaultUnits.filter((item) => item.necessity !== 'keep').length}; forbidden=${metrics.minimalism.forbiddenTerms.join('|') || 'none'}; top-level-back=${metrics.accountHub.headerBackCount}/${metrics.accountant.headerBackCount}; positive-status=${metrics.accountHub.positiveStatusCount}; role-chip=${metrics.accountant.roleChipCount}; self=${metrics.selfProfile.phoneMode}/${metrics.selfProfile.addressMode}; contrast-min=${metrics.typography.minimumContrast}; contrast-violations=${metrics.typography.contrastViolations.length}` },
    { id: 'synthetic-identities-only', passed: metrics.synthetic.rootFlags.length === 16 && metrics.synthetic.rootFlags.every((item) => item === 'true') && metrics.synthetic.realDataViolations.length === 0 && metrics.synthetic.phoneViolations.length === 0, detail: `flags=${metrics.synthetic.rootFlags.length}; real=${metrics.synthetic.realDataViolations.join('|') || 'none'}; phone=${metrics.synthetic.phoneViolations.join('|') || 'none'}` },
    { id: 'account-hub-destinations-unique', passed: JSON.stringify(metrics.accountHub.destinations) === JSON.stringify(['profile', 'security', 'storage', 'notifications', 'telegram']) && new Set(metrics.accountHub.destinations).size === 5 && metrics.accountHub.headerBackCount === 0 && metrics.accountHub.positiveStatusCount === 0 && metrics.accountHub.headerSubtitle === 'نگار پارسا', detail: `${metrics.accountHub.destinations.join(', ')}; back=${metrics.accountHub.headerBackCount}; status=${metrics.accountHub.positiveStatusCount}; identity=${metrics.accountHub.headerSubtitle}` },
    { id: 'accountant-account-scope-bounded', passed: JSON.stringify(metrics.accountant.destinations) === JSON.stringify(['profile', 'storage', 'notifications']) && metrics.accountant.forbiddenElements === 0 && !/نشست|خروج|تلگرام/.test(metrics.accountant.text) && metrics.accountant.headerBackCount === 0 && metrics.accountant.roleChipCount === 0 && metrics.accountant.headerSubtitle === 'رها نیکویی', detail: `destinations=${metrics.accountant.destinations.join(',')}; forbidden-elements=${metrics.accountant.forbiddenElements}; back=${metrics.accountant.headerBackCount}; role-chip=${metrics.accountant.roleChipCount}; identity=${metrics.accountant.headerSubtitle}` },
    { id: 'self-profile-progressive-disclosure', passed: metrics.selfProfile.onDemandCount === 1 && metrics.selfProfile.defaultSecondaryCount === 0 && metrics.selfProfile.phoneMode === 'full' && metrics.selfProfile.phoneText === 'sample.user · ۰۹۱۲ ۰۰۰ ۰۰۰۰' && metrics.selfProfile.addressMode === 'full' && metrics.selfProfile.addressText === 'تهران، خیابان نمونه، پلاک ۱۲', detail: `on-demand=${metrics.selfProfile.onDemandCount}; default-secondary=${metrics.selfProfile.defaultSecondaryCount}; phone=${metrics.selfProfile.phoneMode}:${metrics.selfProfile.phoneText}; address=${metrics.selfProfile.addressMode}:${metrics.selfProfile.addressText}` },
    { id: 'profile-address-feedback-in-context', passed: metrics.profileAddress.inputValue === 'تهران، خیابان نمونه، پلاک ۱۲' && metrics.profileAddress.contextFeedbackCount === 1 && JSON.stringify(recoveryByKey['profile-address-feedback']?.nested) === JSON.stringify(expectedAddressStates) && recoveryByKey['profile-address-feedback']?.preservedInput === 'تهران، خیابان نمونه، پلاک ۱۲' && recoveryByKey['profile-address-feedback']?.failurePreserved === 'true' && recoveryByKey['profile-address-feedback']?.text.includes('«تهران، خیابان نمونه، پلاک ۱۲» حفظ شد'), detail: `input=${metrics.profileAddress.inputValue}; preserved=${recoveryByKey['profile-address-feedback']?.preservedInput}; context=${metrics.profileAddress.contextFeedbackCount}; states=${recoveryByKey['profile-address-feedback']?.nested.join(',')}` },
    { id: 'public-profile-visibility-matrix-exact', passed: publicVisibilityPass && metrics.publicProfile.phone === 'masked' && metrics.publicProfile.address === 'hidden', detail: JSON.stringify(metrics.visibility) },
    { id: 'public-profile-actions-bounded', passed: metrics.publicProfile.actionMode === 'none' && metrics.publicProfile.relationSurfaceCount === 0, detail: `mode=${metrics.publicProfile.actionMode}; relation/history=${metrics.publicProfile.relationSurfaceCount}` },
    { id: 'session-list-metadata-bounded', passed: JSON.stringify(metrics.sessions.facts) === JSON.stringify(expectedSessionFacts) && metrics.sessions.inventoryScope === 'local-per-server' && metrics.sessions.currentSessionSignalCount === 1 && metrics.sessions.forbiddenMetadataCount === 0 && metrics.sessions.forbiddenMetadataTerms.length === 0, detail: `facts=${JSON.stringify(metrics.sessions.facts)}; current-signal=${metrics.sessions.currentSessionSignalCount}; forbidden=${metrics.sessions.forbiddenMetadataCount}/${metrics.sessions.forbiddenMetadataTerms.join('|') || 'none'}` },
    { id: 'session-decision-feedback-in-context', passed: metrics.sessionDecision.ackCount === 1 && metrics.sessionDecision.confirmCount === 1 && metrics.sessionDecision.outcomeCount === 1 && metrics.sessionDecision.text.includes('همه نشست‌های دیگر') && metrics.sessionDecision.text.includes('نشست جاری') && metrics.sessionDecision.text.includes('حفظ می‌شود') && JSON.stringify(recoveryByKey['session-termination-feedback']?.nested) === JSON.stringify(expectedSessionTriad) && sessionPermissionsPass, detail: `ack=${metrics.sessionDecision.ackCount}; confirm=${metrics.sessionDecision.confirmCount}; outcome=${metrics.sessionDecision.outcomeCount}; triad=${recoveryByKey['session-termination-feedback']?.nested.join(',')}; permissions=${sessionPermissionsPass}` },
    { id: 'storage-action-feedback-in-context', passed: metrics.storage.scope === 'current-browser-device' && metrics.storage.size === '12.4 MB' && metrics.storage.clearActionCount === 1 && metrics.storage.outcomeCount === 1 && metrics.storage.text.includes('۱۲٫۴ مگابایت') && metrics.storage.text.includes('همین مرورگر') && metrics.storage.text.includes('حساب، پیام‌ها و نشست‌های فعال بدون تغییر می‌مانند') && JSON.stringify(recoveryByKey['storage-feedback']?.nested) === JSON.stringify(expectedStorageStates), detail: `scope=${metrics.storage.scope}; size=${metrics.storage.size}; action=${metrics.storage.clearActionCount}; outcome=${metrics.storage.outcomeCount}; states=${recoveryByKey['storage-feedback']?.nested.join(',')}` },
    { id: 'notification-center-metadata-bounded', passed: JSON.stringify(metrics.notifications.tabs) === JSON.stringify(['معاملات', 'سایر']) && JSON.stringify(metrics.notifications.facts) === JSON.stringify(expectedNotificationFacts) && metrics.notifications.countElements === 0 && metrics.notifications.rawRouteElements === 0 && metrics.notifications.newSignalCount === 1, detail: `tabs=${metrics.notifications.tabs.join(',')}; rows=${metrics.notifications.facts.length}; counts=${metrics.notifications.countElements}; new=${metrics.notifications.newSignalCount}` },
    { id: 'notification-empty-error-semantics-distinct', passed: JSON.stringify(recoveryByKey['notification-empty-error']?.nested) === JSON.stringify(expectedNotificationStates) && recoveryByKey['notification-empty-error']?.text.includes('هنوز اعلانی نیست') && recoveryByKey['notification-empty-error']?.text.includes('در این دسته اعلانی نیست'), detail: recoveryByKey['notification-empty-error']?.nested.join(',') || 'missing' },
    { id: 'push-state-matrix-complete-and-truthful', passed: JSON.stringify(metrics.push.map((item) => item.key)) === JSON.stringify(PUSH_STATES) && pushActionTruth && metrics.pushMobile.state === 'permission-default' && JSON.stringify(metrics.pushMobile.actions) === JSON.stringify(['enable']) && !metrics.push.some((item) => /آزمایشی|غیرفعال‌سازی|تضمین تحویل/.test(item.text)), detail: `${metrics.push.map((item) => `${item.key}:${item.actions.join('|') || 'none'}`).join(', ')}; mobile=${metrics.pushMobile.state}` },
    { id: 'recovery-state-atlas-complete', passed: JSON.stringify(metrics.recovery.map((item) => item.key)) === JSON.stringify(RECOVERY_GROUPS) && JSON.stringify(recoveryByKey['profile-address-feedback']?.nested) === JSON.stringify(expectedAddressStates) && JSON.stringify(recoveryByKey['session-termination-feedback']?.nested) === JSON.stringify(expectedSessionTriad) && JSON.stringify(recoveryByKey['storage-feedback']?.nested) === JSON.stringify(expectedStorageStates) && JSON.stringify(recoveryByKey['notification-empty-error']?.nested) === JSON.stringify(expectedNotificationStates) && JSON.stringify(recoveryByKey['session-permission']?.nested) === JSON.stringify(['forbidden', 'accountant-forbidden']), detail: metrics.recovery.map((item) => `${item.key}[${item.nested.join(',')}]`).join('; ') },
    { id: 'protected-interiors-absent', passed: metrics.protectedScope.marketInteriorCount === 0 && metrics.protectedScope.messengerInteriorCount === 0 && metrics.protectedScope.protectedTextViolations.length === 0, detail: `market=${metrics.protectedScope.marketInteriorCount}; messenger=${metrics.protectedScope.messengerInteriorCount}; text=${metrics.protectedScope.protectedTextViolations.join('|') || 'none'}` },
  ]
}

function assertAssertionContract(assertions) {
  const ids = assertions.map((item) => item.id)
  if (new Set(ids).size !== ids.length || JSON.stringify(ids) !== JSON.stringify(EXPECTED_ASSERTION_IDS)) {
    throw new Error(`Assertion contract drift: ${ids.join(', ')}`)
  }
}

function assertNoResidue(assetsDir) {
  const residue = fs.readdirSync(assetsDir).filter((item) => item.startsWith('.local-evidence-staging-') || item.startsWith('.local-evidence-backup-'))
  if (residue.length > 0) throw new Error(`Residual publication directories: ${residue.join(', ')}`)
}

async function main() {
  const preflightOnly = process.argv.includes('--preflight')
  const htmlPath = path.join(__dirname, 'account-profile-security-notifications-evidence.html')
  const assetsDir = path.join(__dirname, 'assets')
  const localEvidenceDir = path.join(assetsDir, 'local-evidence')

  recoverEvidenceBeforeDependencyResolution(assetsDir, localEvidenceDir)
  const directFigmaAuditValidation = readAndValidateDirectFigmaAudit(__dirname)

  const { module: playwright, source: playwrightSource } = resolvePlaywright()
  const fontRoot = findFontRoot()
  const runToken = `${process.pid}-${Date.now()}`
  const stagingDir = path.join(assetsDir, `.local-evidence-staging-${runToken}`)
  const backupDir = path.join(assetsDir, `.local-evidence-backup-${runToken}`)
  fs.mkdirSync(stagingDir)

  let browser = null
  let backupCreated = false
  let newDirectoryInstalled = false
  let publicationValidated = false
  let completed = false
  let output = null
  const pageErrors = []

  try {
    browser = await playwright.chromium.launch({ headless: true })
    const page = await browser.newPage({ viewport: { width: 3200, height: 2000 }, deviceScaleFactor: 1 })
    page.on('pageerror', (error) => pageErrors.push(error.message))
    await page.goto(pathToFileURL(htmlPath).href, { waitUntil: 'load' })
    await page.addStyleTag({ content: embeddedFontCss(fontRoot) })
    await page.evaluate(async () => {
      await Promise.all([400, 500, 600, 700].map((weight) => document.fonts.load(`${weight} 16px "Vazirmatn Evidence"`)))
      await document.fonts.ready
    })
    await page.locator('[data-product-screen="M10"]').waitFor({ state: 'visible' })
    await page.locator('[data-responsive-width="430"] [data-responsive-proof]').waitFor({ state: 'visible' })

    const font = await page.evaluate(() => {
      const checks = [400, 500, 600, 700].map((weight) => ({ weight, loaded: document.fonts.check(`${weight} 16px "Vazirmatn Evidence"`) }))
      const faces = [...document.fonts].filter((face) => face.family.replace(/["']/g, '').trim() === 'Vazirmatn Evidence').map((face) => ({ family: face.family, weight: face.weight, style: face.style, status: face.status }))
      return { loaded: checks.every((item) => item.loaded) && faces.length >= 4 && faces.every((face) => face.status === 'loaded'), checks, faces, computedBodyFamily: getComputedStyle(document.body).fontFamily }
    })

    const preflightCanonicalDom = await canonicalDomSnapshot(page)
    const preflightDomHash = sha256Buffer(Buffer.from(preflightCanonicalDom))
    const preflightRawDomHash = sha256Buffer(Buffer.from(await page.content()))
    const preflightMeasurements = await measureEvidence(page)
    const preflightAssertions = buildAssertions(preflightMeasurements, font)
    assertAssertionContract(preflightAssertions)
    const preflightFailures = preflightAssertions.filter((item) => !item.passed)
    if (preflightFailures.length > 0 || pageErrors.length > 0) {
      throw new Error(`Preflight failed:\n${preflightFailures.map((item) => `${item.id}: ${item.detail}`).join('\n') || pageErrors.join('\n')}`)
    }

    const captures = []
    for (const capture of CAPTURES) captures.push(await captureLocator(page, stagingDir, capture))
    if (pageErrors.length > 0) throw new Error(`Page error during capture: ${pageErrors.join('; ')}`)

    const postCaptureCanonicalDom = await canonicalDomSnapshot(page)
    const postCaptureDomHash = sha256Buffer(Buffer.from(postCaptureCanonicalDom))
    const postCaptureRawDomHash = sha256Buffer(Buffer.from(await page.content()))
    if (preflightDomHash !== postCaptureDomHash) throw new Error(`Canonical DOM changed during capture: ${preflightDomHash} -> ${postCaptureDomHash}`)

    const measurements = await measureEvidence(page)
    const assertions = buildAssertions(measurements, font)
    assertAssertionContract(assertions)
    const failures = assertions.filter((item) => !item.passed)
    const assertionOutcomeDrift = assertions.some((item, index) => item.id !== preflightAssertions[index]?.id || item.passed !== preflightAssertions[index]?.passed || item.detail !== preflightAssertions[index]?.detail)
    if (failures.length > 0 || pageErrors.length > 0 || assertionOutcomeDrift) {
      throw new Error(`Post-capture validation failed:\n${failures.map((item) => `${item.id}: ${item.detail}`).join('\n') || (assertionOutcomeDrift ? 'pre/post assertion drift' : pageErrors.join('\n'))}`)
    }

    const report = {
      schemaVersion: 3,
      runId: runToken,
      runMode: preflightOnly ? 'preflight' : 'publish',
      evidenceRole: 'secondary-local-derivative',
      scope: 'Stage 0B-5 account, profile, security, storage, notifications and Push; Market and Messenger interiors excluded',
      generatedAt: new Date().toISOString(),
      canonicalFigma: FIGMA_FREEZE,
      directFigmaAuditValidation,
      environment: { playwrightSource, browser: await browser.version(), viewport: { width: 3200, height: 2000, deviceScaleFactor: 1 }, fontSourceRoot: fontRoot },
      integrity: {
        preflightCanonicalDomSha256: preflightDomHash,
        postCaptureCanonicalDomSha256: postCaptureDomHash,
        canonicalDomUnchangedDuringCapture: preflightDomHash === postCaptureDomHash,
        preflightRawDomSha256: preflightRawDomHash,
        postCaptureRawDomSha256: postCaptureRawDomHash,
        preAndPostAssertionsIdentical: !assertionOutcomeDrift,
        postCaptureRemeasurementCompleted: true,
        atomicDirectorySwap: !preflightOnly,
        recoveryRanBeforeDependencyResolution: true,
      },
      font,
      captures,
      measurements,
      preflightAssertions,
      assertions,
      summary: { passed: failures.length === 0 && pageErrors.length === 0 && !assertionOutcomeDrift, assertionCount: assertions.length, passedCount: assertions.filter((item) => item.passed).length, failureCount: failures.length, pageErrorCount: pageErrors.length },
      failures,
      pageErrors,
    }
    fs.writeFileSync(path.join(stagingDir, METRICS_FILENAME), `${JSON.stringify(report, null, 2)}\n`)
    const staged = validateEvidenceDirectory(stagingDir)
    if (!staged.valid) throw new Error(`Staging validation failed: ${staged.problems.join('; ')}`)

    await browser.close()
    browser = null

    if (!preflightOnly) {
      if (fs.existsSync(localEvidenceDir)) {
        const current = validateEvidenceDirectory(localEvidenceDir, { allowSupersededCanonical: true })
        if (!current.valid) throw new Error(`Current evidence invalid before swap: ${current.problems.join('; ')}`)
        fs.renameSync(localEvidenceDir, backupDir)
        backupCreated = true
      }
      fs.renameSync(stagingDir, localEvidenceDir)
      newDirectoryInstalled = true
      const published = validateEvidenceDirectory(localEvidenceDir)
      if (!published.valid) throw new Error(`Published evidence invalid after swap: ${published.problems.join('; ')}`)
      publicationValidated = true
      if (backupCreated) fs.rmSync(backupDir, { recursive: true, force: true })
      backupCreated = false
    }

    completed = true
    output = {
      passed: true,
      mode: preflightOnly ? 'preflight-no-publication' : 'atomic-publication',
      runId: runToken,
      canonicalFigmaFrozenAt: FIGMA_FREEZE.frozenAt,
      directFigmaSemanticHitContainerCount: directFigmaAuditValidation.semanticHitContainerCount,
      assertions: `${assertions.filter((item) => item.passed).length}/${assertions.length}`,
      pageErrors: pageErrors.length,
      captures: captures.map((item) => ({ filename: item.filename, dimensions: `${item.pixelDimensions.width}×${item.pixelDimensions.height}`, sha256: item.sha256 })),
      mobileRoots: measurements.mobile.map((item) => `${item.id}:${item.rect.width}×${item.rect.height}`),
      responsive: measurements.responsive.map((item) => `${item.rect.width}×${item.rect.height}`),
      desktop: `${measurements.desktop.rect.width}×${measurements.desktop.rect.height}`,
      minimumTarget: `${measurements.actions.minimumWidth}×${measurements.actions.minimumHeight}`,
      minimumCtaHeight: measurements.ctas.minimumHeight,
      minimumTextContrast: measurements.typography.minimumContrast,
      focus: `${measurements.focus.width}px/${measurements.focus.contrast}:1`,
      canonicalDomSha256: preflightDomHash,
    }
  } finally {
    if (browser) {
      try { await browser.close() } catch {}
    }
    try { fs.rmSync(stagingDir, { recursive: true, force: true }) } catch {}
    if (!publicationValidated && !preflightOnly) {
      try {
        if (newDirectoryInstalled && fs.existsSync(localEvidenceDir)) fs.rmSync(localEvidenceDir, { recursive: true, force: true })
        if (backupCreated && fs.existsSync(backupDir) && !fs.existsSync(localEvidenceDir)) fs.renameSync(backupDir, localEvidenceDir)
      } catch (recoveryError) {
        throw new Error(`Publication failed and rollback failed: ${recoveryError.message}`)
      }
    }
  }

  assertNoResidue(assetsDir)
  if (!completed) throw new Error('Evidence run did not complete')
  process.stdout.write(`${JSON.stringify(output, null, 2)}\n`)
}

main().catch((error) => {
  console.error(error.stack || error.message)
  process.exitCode = 1
})
