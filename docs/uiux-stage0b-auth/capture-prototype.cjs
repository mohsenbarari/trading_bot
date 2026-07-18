const fs = require('node:fs')
const path = require('node:path')
const { pathToFileURL } = require('node:url')

let chromium
try {
  ({ chromium } = require('playwright'))
} catch {
  ({ chromium } = require(path.resolve(__dirname, '../../frontend/node_modules/playwright')))
}

const AUTHORING_WIDTH = 390
const RESPONSIVE_WIDTHS = [360, 375, 390, 414, 430]
const EXPECTED_STATE_CARD_COUNT = 41

const GROUP_CAPTURES = [
  { selector: '#mobile-scenarios', filename: 'stage0b-auth-mobile-scenarios.png' },
  { selector: '#keyboard-states', filename: 'stage0b-auth-keyboard-states.png' },
  { selector: '#state-atlas', filename: 'stage0b-auth-state-atlas.png' },
  { selector: '#adaptive-desktop', filename: 'stage0b-auth-adaptive-desktop.png' },
]

const FRAME_CAPTURES = [
  { selector: '#auth-login-mobile', filename: 'auth-01-login-mobile.png' },
  { selector: '#auth-login-otp', filename: 'auth-02-login-otp.png' },
  { selector: '#auth-device-approval', filename: 'auth-03-device-approval.png' },
  { selector: '#auth-recovery-waiting', filename: 'auth-04-recovery-waiting.png' },
  { selector: '#auth-invite-valid', filename: 'auth-05-invite-valid.png' },
  { selector: '#auth-register-review', filename: 'auth-06-register-review.png' },
  { selector: '#auth-register-otp', filename: 'auth-07-register-otp.png' },
  { selector: '#auth-register-address', filename: 'auth-08-register-address.png' },
  { selector: '#auth-register-telegram', filename: 'auth-09-register-telegram.png' },
  { selector: '#auth-recovery-identity', filename: 'auth-10-recovery-identity.png' },
  { selector: '#auth-setup-password', filename: 'auth-11-setup-password.png' },
  { selector: '#auth-invite-web-only', filename: 'auth-12-invite-web-only.png' },
  { selector: '#auth-recovery-unavailable', filename: 'auth-13-recovery-unavailable.png' },
  { selector: '#auth-register-direct', filename: 'auth-14-register-direct.png' },
  { selector: '#auth-setup-password-error', filename: 'auth-15-setup-password-error.png' },
]

const SEMANTIC_ACTION_SELECTOR = [
  'button',
  'a[href]',
  'input:not([type="hidden"])',
  'select',
  'textarea',
  'summary',
  '[contenteditable="true"]',
  '[role="button"]',
  '[role="link"]',
  '[role="checkbox"]',
  '[role="radio"]',
  '[role="switch"]',
  '[role="tab"]',
  '[tabindex]:not([tabindex="-1"])',
].join(', ')

const ACTIONABLE_SELECTOR = `${SEMANTIC_ACTION_SELECTOR}, [data-actionable="true"], .touch-target`

function promoteCaptureDirectory(stagedDir, destinationDir) {
  const parentDir = path.dirname(destinationDir)
  const backupDir = path.join(
    parentDir,
    `.stage0b-auth-assets-backup-${process.pid}-${Date.now()}`,
  )
  const destinationExists = fs.existsSync(destinationDir)

  if (destinationExists) fs.renameSync(destinationDir, backupDir)
  try {
    fs.renameSync(stagedDir, destinationDir)
  } catch (error) {
    if (destinationExists && fs.existsSync(backupDir)) {
      fs.renameSync(backupDir, destinationDir)
    }
    throw error
  }

  if (destinationExists) fs.rmSync(backupDir, { recursive: true, force: true })
}

function readPngDimensions(filePath) {
  const buffer = fs.readFileSync(filePath)
  const pngSignature = '89504e470d0a1a0a'
  if (buffer.length < 24 || buffer.subarray(0, 8).toString('hex') !== pngSignature) {
    throw new Error(`Expected a PNG file at ${filePath}`)
  }
  return {
    width: buffer.readUInt32BE(16),
    height: buffer.readUInt32BE(20),
  }
}

async function captureLocator(page, assetsDir, capture, captureKind) {
  const locator = page.locator(capture.selector)
  const count = await locator.count()
  if (count !== 1) {
    throw new Error(`Expected exactly one ${capture.selector}; found ${count}`)
  }
  if (captureKind.startsWith('full-scenario')) {
    const phoneCount = await locator.locator('.phone').count()
    const screenCount = await locator.locator('.phone .screen').count()
    if (phoneCount !== 1 || screenCount !== 1) {
      throw new Error(
        `Expected ${capture.selector} to contain one .phone and one .phone .screen; ` +
        `found ${phoneCount} phone(s) and ${screenCount} screen(s)`,
      )
    }
  }

  const outputPath = path.join(assetsDir, capture.filename)
  await locator.screenshot({ path: outputPath })
  return {
    id: capture.selector.slice(1),
    selector: capture.selector,
    filename: capture.filename,
    captureKind,
    pixelDimensions: readPngDimensions(outputPath),
  }
}

async function loadAndAssertVazirmatn(page) {
  return page.evaluate(async () => {
    const requestedWeights = [400, 500, 600, 700]
    await Promise.all(requestedWeights.map((weight) => document.fonts.load(`${weight} 16px "Vazirmatn"`)))
    await document.fonts.ready

    const checks = requestedWeights.map((weight) => ({
      weight,
      loaded: document.fonts.check(`${weight} 16px "Vazirmatn"`),
    }))
    const faces = [...document.fonts]
      .filter((face) => face.family.replace(/["']/g, '').trim().toLowerCase() === 'vazirmatn')
      .map((face) => ({ family: face.family, weight: face.weight, style: face.style, status: face.status }))
    const loaded = checks.every((item) => item.loaded) && faces.length > 0 && faces.every((face) => face.status === 'loaded')

    return {
      loaded,
      checks,
      faces,
      computedBodyFamily: getComputedStyle(document.body).fontFamily,
    }
  })
}

async function measureAtWidth(page, targetWidth) {
  return page.evaluate(async ({ width, semanticSelector, actionableSelector }) => {
    const phones = [...document.querySelectorAll('.phone')]
    phones.forEach((node) => { node.style.width = `${width}px` })
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))

    const isVisible = (node) => {
      const rect = node.getBoundingClientRect()
      const style = getComputedStyle(node)
      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden'
    }

    const contextFor = (node) => {
      if (node.closest('#keyboard-states')) return 'static-keyboard-illustration'
      if (node.closest('#state-atlas')) return 'static-state-atlas'
      if (node.closest('#adaptive-desktop')) return 'adaptive-desktop-proof'
      return node.closest('.scenario')?.id || 'document'
    }

    const describeTarget = (node, index) => {
      const rect = node.getBoundingClientRect()
      const label = (
        node.getAttribute('aria-label') ||
        node.textContent ||
        node.getAttribute('value') ||
        node.getAttribute('placeholder') ||
        ''
      ).trim().replace(/\s+/g, ' ').slice(0, 80)
      const context = contextFor(node)
      return {
        id: node.id || `${context}:${node.tagName.toLowerCase()}:${index}`,
        context,
        tag: node.tagName.toLowerCase(),
        role: node.getAttribute('role') || null,
        label,
        width: Math.round(rect.width),
        height: Math.round(rect.height),
        semantic: node.matches(semanticSelector),
        explicitlyDeclared: node.matches('[data-actionable="true"], .touch-target'),
        markedTouchTarget: node.classList.contains('touch-target'),
      }
    }

    const measureNaturalScreen = (screen) => {
      const screenRect = screen.getBoundingClientRect()
      const host = document.createElement('div')
      const clone = screen.cloneNode(true)
      const screenStyle = getComputedStyle(screen)

      Object.assign(host.style, {
        position: 'fixed',
        top: '0',
        left: '-10000px',
        width: `${screenRect.width}px`,
        height: 'auto',
        minHeight: '0',
        display: 'block',
        overflow: 'visible',
        margin: '0',
        padding: '0',
        border: '0',
        borderRadius: '0',
        boxShadow: 'none',
        isolation: 'auto',
        visibility: 'hidden',
        pointerEvents: 'none',
        direction: screenStyle.direction,
        writingMode: screenStyle.writingMode,
      })
      // Preserve any current or future `.phone .screen` descendant selectors
      // while removing the mock device's fixed-height layout constraints.
      host.className = 'phone'
      host.setAttribute('aria-hidden', 'true')
      clone.style.setProperty('width', '100%', 'important')
      clone.style.setProperty('height', 'auto', 'important')
      clone.style.setProperty('min-height', '0', 'important')
      clone.style.setProperty('flex', 'none', 'important')
      host.appendChild(clone)
      document.body.appendChild(host)

      const naturalHeight = Math.ceil(Math.max(clone.getBoundingClientRect().height, clone.scrollHeight))
      const availableHeight = screen.clientHeight
      host.remove()

      return {
        id: screen.closest('.scenario')?.id || '',
        availableHeight,
        naturalHeight,
        slack: availableHeight - naturalHeight,
        naturalContentClipped: naturalHeight > availableHeight,
        renderedClientHeight: screen.clientHeight,
        renderedScrollHeight: screen.scrollHeight,
        renderedOverflowDetected: screen.scrollHeight > screen.clientHeight,
      }
    }

    const screenNaturalFit = [...document.querySelectorAll('.phone .screen')].map(measureNaturalScreen)
    const actionableTargets = [...new Set([...document.querySelectorAll(actionableSelector)])]
      .filter(isVisible)
      .map(describeTarget)
    const ctaTargets = [...document.querySelectorAll('.button')]
      .filter(isVisible)
      .map(describeTarget)
    const atlasActionSamples = [...document.querySelectorAll('#state-atlas .state-card__example')].map((node, index) => ({
      id: `atlas-action-sample:${index}`,
      label: (node.textContent || '').trim().replace(/\s+/g, ' '),
      semanticOrDeclaredAction: node.matches(actionableSelector),
    }))

    return {
      width,
      phoneElements: phones.map((node) => {
        const rect = node.getBoundingClientRect()
        return {
          id: node.closest('.scenario')?.id || '',
          outerWidth: Math.round(rect.width),
          outerHeight: Math.round(rect.height),
          clientWidth: node.clientWidth,
          clientHeight: node.clientHeight,
          scrollWidth: node.scrollWidth,
          scrollHeight: node.scrollHeight,
        }
      }),
      horizontalOverflow: {
        frameIds: phones
          .filter((node) => node.scrollWidth > node.clientWidth)
          .map((node) => node.closest('.scenario')?.id || ''),
        maximumScrollWidth: Math.max(...phones.map((node) => node.scrollWidth)),
      },
      screenNaturalFit,
      naturalFitSummary: {
        minimumSlack: Math.min(...screenNaturalFit.map((item) => item.slack)),
        clippedFrameIds: screenNaturalFit.filter((item) => item.naturalContentClipped).map((item) => item.id),
      },
      actionableTargets: {
        count: actionableTargets.length,
        semanticCount: actionableTargets.filter((item) => item.semantic).length,
        minimumWidth: actionableTargets.length ? Math.min(...actionableTargets.map((item) => item.width)) : null,
        minimumHeight: actionableTargets.length ? Math.min(...actionableTargets.map((item) => item.height)) : null,
        below44: actionableTargets.filter((item) => item.width < 44 || item.height < 44),
        unmarked: actionableTargets.filter((item) => !item.markedTouchTarget),
        all: actionableTargets,
      },
      ctaTargets: {
        count: ctaTargets.length,
        minimumHeight: ctaTargets.length ? Math.min(...ctaTargets.map((item) => item.height)) : null,
        below48: ctaTargets.filter((item) => item.height < 48),
        all: ctaTargets,
      },
      atlasActionSamples: {
        count: atlasActionSamples.length,
        unexpectedlyInteractive: atlasActionSamples.filter((item) => item.semanticOrDeclaredAction),
        samples: atlasActionSamples,
        classification: 'Static visual/copy samples only; excluded from actionable-target acceptance unless made semantic or explicitly actionable.',
      },
    }
  }, {
    width: targetWidth,
    semanticSelector: SEMANTIC_ACTION_SELECTOR,
    actionableSelector: ACTIONABLE_SELECTOR,
  })
}

async function resetPhoneWidths(page) {
  await page.evaluate(() => {
    document.querySelectorAll('.phone').forEach((node) => { node.style.width = '' })
  })
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))))
}

async function measureStaticKeyboardIllustrations(page) {
  return page.evaluate(() => ({
    classification: 'Static CSS illustrations only; not an operating-system keyboard, browser keyboard, or runtime focus/viewport test.',
    cases: [...document.querySelectorAll('#keyboard-states .keyboard-case')].map((node, index) => {
      const viewport = node.querySelector('.keyboard-viewport')
      const app = node.querySelector('.keyboard-app')
      const panel = node.querySelector('.keyboard-panel')
      const dimensions = (element) => {
        if (!element) return null
        const rect = element.getBoundingClientRect()
        return { width: Math.round(rect.width), height: Math.round(rect.height) }
      }
      return {
        id: node.id || `keyboard-illustration:${index}`,
        label: node.querySelector('h3')?.textContent?.trim() || '',
        viewportElementDimensions: dimensions(viewport),
        simulatedAppDimensions: dimensions(app),
        simulatedKeyboardPanelDimensions: dimensions(panel),
      }
    }),
  }))
}

async function measureKeyboardFocusTraversal(page) {
  const focusableSelector = [
    'button:not([disabled])',
    'a[href]',
    'input:not([type="hidden"]):not([disabled])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    'summary',
    '[contenteditable="true"]',
    '[tabindex]:not([tabindex="-1"])',
  ].join(', ')

  const expectedOutlineColor = await page.evaluate(() => {
    const probe = document.createElement('span')
    probe.style.color = getComputedStyle(document.documentElement).getPropertyValue('--focus-ring').trim()
    probe.style.position = 'fixed'
    probe.style.visibility = 'hidden'
    document.body.appendChild(probe)
    const normalized = getComputedStyle(probe).color
    probe.remove()
    return normalized
  })

  const expected = await page.evaluate((selector) => {
    const isVisible = (node) => {
      const rect = node.getBoundingClientRect()
      const style = getComputedStyle(node)
      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden'
    }
    const nodes = [...document.querySelectorAll(selector)].filter(isVisible)
    nodes.forEach((node, index) => { node.dataset.focusAuditId = `focus-${index}` })
    document.activeElement?.blur()
    window.scrollTo(0, 0)
    return nodes.length
  }, focusableSelector)

  const visited = []
  const visitedIds = new Set()
  for (let index = 0; index < expected + 4; index += 1) {
    await page.keyboard.press('Tab')
    const item = await page.evaluate(() => {
      const node = document.activeElement
      if (!(node instanceof HTMLElement) || !node.dataset.focusAuditId) return null
      const style = getComputedStyle(node)
      const context = node.closest('#keyboard-states')
        ? 'static-keyboard-illustration'
        : node.closest('#adaptive-desktop')
          ? 'adaptive-desktop-proof'
          : node.closest('.scenario')?.id || 'document'
      return {
        id: node.dataset.focusAuditId,
        context,
        tag: node.tagName.toLowerCase(),
        label: (node.getAttribute('aria-label') || node.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 80),
        focusVisible: node.matches(':focus-visible'),
        outlineStyle: style.outlineStyle,
        outlineWidth: Number.parseFloat(style.outlineWidth) || 0,
        outlineColor: style.outlineColor,
        outlineOffset: Number.parseFloat(style.outlineOffset) || 0,
      }
    })
    if (item && !visitedIds.has(item.id)) {
      visitedIds.add(item.id)
      visited.push(item)
    }
    if (visited.length === expected) break
  }

  await page.evaluate(() => {
    document.activeElement?.blur()
    document.querySelectorAll('[data-focus-audit-id]').forEach((node) => delete node.dataset.focusAuditId)
  })

  const failures = visited.filter((item) => (
    !item.focusVisible ||
    item.outlineStyle === 'none' ||
    item.outlineWidth < 3 ||
    item.outlineColor !== expectedOutlineColor
  ))

  return {
    classification: 'Synthetic keyboard Tab traversal of the static artifact; not a runtime, WebView, or assistive-technology test.',
    expectedFocusableCount: expected,
    visitedFocusableCount: visited.length,
    allExpectedVisited: visited.length === expected,
    expectedOutlineColor,
    minimumOutlineWidth: visited.length ? Math.min(...visited.map((item) => item.outlineWidth)) : null,
    failures,
    visited,
  }
}

async function measureDecorativeSemantics(page) {
  return page.evaluate(() => ({
    pickerGlyphCount: document.querySelectorAll('.picker b').length,
    pickerGlyphsExposedToAccessibilityTree: document.querySelectorAll('.picker b:not([aria-hidden="true"])').length,
    noticeMarkerCount: document.querySelectorAll('.notice__mark').length,
    noticeMarkersExposedToAccessibilityTree: document.querySelectorAll('.notice__mark:not([aria-hidden="true"])').length,
    classification: 'Decorative glyph source audit in the static artifact; runtime accessible names require implementation tests.',
  }))
}

async function measureContrastChecks(page) {
  return page.evaluate(() => {
    const rootStyle = getComputedStyle(document.documentElement)
    const token = (name) => rootStyle.getPropertyValue(name).trim()
    const pairs = [
      ['muted-on-page', '--text-muted', '--page', 4.5, 'normal-text'],
      ['muted-on-surface', '--text-muted', '--surface', 4.5, 'normal-text'],
      ['placeholder-on-surface', '--text-placeholder', '--surface', 4.5, 'normal-text'],
      ['secondary-on-page', '--text-secondary', '--page', 4.5, 'normal-text'],
      ['secondary-on-surface', '--text-secondary', '--surface', 4.5, 'normal-text'],
      ['action-on-action-surface', '--blue-700', '--blue-50', 4.5, 'normal-text'],
      ['success-on-success-surface', '--success', '--success-bg', 4.5, 'normal-text'],
      ['warning-on-warning-surface', '--warning', '--warning-bg', 4.5, 'normal-text'],
      ['danger-on-danger-surface', '--danger', '--danger-bg', 4.5, 'normal-text'],
      ['neutral-state-tag', '--neutral-state', '--neutral-state-bg', 4.5, 'normal-text'],
      ['warning-marker', '--warning', '--warning-marker-bg', 4.5, 'decorative-visible-glyph'],
      ['focus-ring-on-page', '--focus-ring', '--page', 3, 'focus-indicator'],
      ['focus-ring-on-surface', '--focus-ring', '--surface', 3, 'focus-indicator'],
      ['focus-ring-on-action-surface', '--focus-ring', '--blue-50', 3, 'focus-indicator'],
    ]

    const parseHex = (value) => {
      const match = value.match(/^#([0-9a-f]{6})$/i)
      if (!match) throw new Error(`Contrast check requires a six-digit hex token; received ${value}`)
      return match[1].match(/../g).map((part) => Number.parseInt(part, 16) / 255)
    }
    const luminance = (value) => {
      const [red, green, blue] = parseHex(value).map((channel) => (
        channel <= 0.04045
          ? channel / 12.92
          : ((channel + 0.055) / 1.055) ** 2.4
      ))
      return (0.2126 * red) + (0.7152 * green) + (0.0722 * blue)
    }

    const checks = pairs.map(([id, foregroundToken, backgroundToken, requiredRatio, category]) => {
      const foreground = token(foregroundToken)
      const background = token(backgroundToken)
      const foregroundLuminance = luminance(foreground)
      const backgroundLuminance = luminance(background)
      const ratio = (
        (Math.max(foregroundLuminance, backgroundLuminance) + 0.05) /
        (Math.min(foregroundLuminance, backgroundLuminance) + 0.05)
      )
      return {
        id,
        foregroundToken,
        foreground,
        backgroundToken,
        background,
        ratio: Number(ratio.toFixed(3)),
        requiredRatio,
        category,
        passesRequiredRatio: ratio >= requiredRatio,
      }
    })

    return {
      standards: {
        normalText: '4.5:1 for scoped normal-text and visible-glyph pairs.',
        focusIndicator: '3:1 against adjacent scoped surfaces for the focus indicator.',
      },
      checks,
      failures: checks.filter((item) => !item.passesRequiredRatio),
    }
  })
}

async function main() {
  const root = __dirname
  const htmlPath = path.join(root, 'auth-scenarios.html')
  const assetsDir = path.join(root, 'assets')
  const stagedAssetsDir = fs.mkdtempSync(path.join(root, '.stage0b-auth-assets-staged-'))

  let browser
  try {
    browser = await chromium.launch({ headless: true })
    const page = await browser.newPage({
      viewport: { width: 2000, height: 1200 },
      deviceScaleFactor: 1,
    })

    await page.goto(pathToFileURL(htmlPath).href, { waitUntil: 'load' })
    const fontLoad = await loadAndAssertVazirmatn(page)
    if (!fontLoad.loaded) {
      throw new Error(`Vazirmatn did not load for every required weight: ${JSON.stringify(fontLoad)}`)
    }

    const stateAtlas = await page.evaluate(() => ({
      stateCardCount: document.querySelectorAll('#state-atlas .state-card').length,
      exampleActionCount: document.querySelectorAll('#state-atlas .state-card__example').length,
    }))
    if (stateAtlas.stateCardCount !== EXPECTED_STATE_CARD_COUNT) {
      throw new Error(
        `Expected ${EXPECTED_STATE_CARD_COUNT} state cards; found ${stateAtlas.stateCardCount}`,
      )
    }

    const overviewPath = path.join(stagedAssetsDir, 'stage0b-auth-overview.png')
    await page.screenshot({ path: overviewPath, fullPage: true })
    const overviewCapture = {
      filename: path.basename(overviewPath),
      captureKind: 'full-design-board',
      pixelDimensions: readPngDimensions(overviewPath),
    }

    const groupCaptures = []
    for (const capture of GROUP_CAPTURES) {
      groupCaptures.push(await captureLocator(page, stagedAssetsDir, capture, 'group-section'))
    }

    const scenarioPngCaptures = []
    for (const capture of FRAME_CAPTURES) {
      scenarioPngCaptures.push(await captureLocator(
        page,
        stagedAssetsDir,
        capture,
        'full-scenario-article-including-label-and-390x844-phone-element',
      ))
    }

    const responsiveWidths = []
    for (const width of RESPONSIVE_WIDTHS) {
      responsiveWidths.push(await measureAtWidth(page, width))
    }
    await resetPhoneWidths(page)

    const authoringMetrics = responsiveWidths.find((item) => item.width === AUTHORING_WIDTH)
    if (!authoringMetrics) throw new Error(`Missing authoring-width metrics for ${AUTHORING_WIDTH}px`)

    const staticKeyboardIllustrations = await measureStaticKeyboardIllustrations(page)
    const contrast = await measureContrastChecks(page)
    const focusTraversal = await measureKeyboardFocusTraversal(page)
    const decorativeSemantics = await measureDecorativeSemantics(page)
    const metrics = {
      schemaVersion: 4,
      generatedAt: new Date().toISOString(),
      authoringWidth: AUTHORING_WIDTH,
      captureSemantics: {
        phoneElement: 'The mock device element itself; expected outer dimensions are 390x844 at the authoring width.',
        scenarioPng: 'A locator screenshot of the complete scenario article, including its label and the phone element; its PNG dimensions are therefore larger than 390x844.',
        keyboardIllustration: staticKeyboardIllustrations.classification,
        artifactPromotion: 'All PNGs and metrics are generated in a sibling staging directory and promoted together only after every hard assertion passes.',
      },
      captures: {
        overview: overviewCapture,
        groups: groupCaptures,
        scenarioPngs: scenarioPngCaptures,
      },
      stateAtlas,
      phoneElementsAtAuthoringWidth: authoringMetrics.phoneElements,
      screensAtAuthoringWidth: authoringMetrics.screenNaturalFit,
      actionableTargetsAtAuthoringWidth: authoringMetrics.actionableTargets,
      ctaTargetsAtAuthoringWidth: authoringMetrics.ctaTargets,
      atlasActionSamples: authoringMetrics.atlasActionSamples,
      staticKeyboardIllustrations,
      fontLoad,
      contrast,
      focusTraversal,
      decorativeSemantics,
      responsiveWidths,
      summary: {
        scenarioCaptureCount: scenarioPngCaptures.length,
        stateCardCount: stateAtlas.stateCardCount,
        phoneElementCount: authoringMetrics.phoneElements.length,
        phoneElementDimensionMismatches: authoringMetrics.phoneElements.filter(
          (item) => item.outerWidth !== AUTHORING_WIDTH || item.outerHeight !== 844,
        ),
        minimumNaturalSlackAtAuthoringWidth: authoringMetrics.naturalFitSummary.minimumSlack,
        naturallyClippedAtAuthoringWidth: authoringMetrics.naturalFitSummary.clippedFrameIds,
        widthsWithHorizontalOverflow: responsiveWidths
          .filter((item) => item.horizontalOverflow.frameIds.length > 0)
          .map((item) => ({ width: item.width, frameIds: item.horizontalOverflow.frameIds })),
        naturalFitByWidth: responsiveWidths.map((item) => ({
          width: item.width,
          minimumSlack: item.naturalFitSummary.minimumSlack,
          clippedFrameIds: item.naturalFitSummary.clippedFrameIds,
        })),
        actionableBelow44AtAuthoringWidth: authoringMetrics.actionableTargets.below44,
        actionableUnmarkedAtAuthoringWidth: authoringMetrics.actionableTargets.unmarked,
        ctaBelow48AtAuthoringWidth: authoringMetrics.ctaTargets.below48,
        focusTraversalFailures: focusTraversal.failures,
        focusTraversalComplete: focusTraversal.allExpectedVisited,
        exposedDecorativeGlyphCount:
          decorativeSemantics.pickerGlyphsExposedToAccessibilityTree +
          decorativeSemantics.noticeMarkersExposedToAccessibilityTree,
        vazirmatnLoaded: fontLoad.loaded,
        contrastFailures: contrast.failures,
      },
    }

    const hardFailures = []
    if (metrics.summary.phoneElementDimensionMismatches.length) hardFailures.push('phone element dimensions')
    if (metrics.summary.naturallyClippedAtAuthoringWidth.length) hardFailures.push('authoring-width natural fit')
    if (responsiveWidths.some((item) => item.naturalFitSummary.clippedFrameIds.length)) hardFailures.push('responsive natural fit')
    if (metrics.summary.widthsWithHorizontalOverflow.length) hardFailures.push('horizontal overflow')
    if (metrics.summary.actionableBelow44AtAuthoringWidth.length) hardFailures.push('44px actionable targets')
    if (metrics.summary.actionableUnmarkedAtAuthoringWidth.length) hardFailures.push('unmarked actionable targets')
    if (metrics.summary.ctaBelow48AtAuthoringWidth.length) hardFailures.push('48px CTA targets')
    if (!metrics.summary.focusTraversalComplete || metrics.summary.focusTraversalFailures.length) hardFailures.push('keyboard focus traversal')
    if (metrics.summary.exposedDecorativeGlyphCount) hardFailures.push('decorative glyph semantics')
    if (metrics.summary.contrastFailures.length) hardFailures.push('scoped contrast')
    if (hardFailures.length) {
      throw new Error(`Stage 0B-1 capture acceptance failed: ${hardFailures.join(', ')}`)
    }

    fs.writeFileSync(
      path.join(stagedAssetsDir, 'stage0b-auth-validation-metrics.json'),
      `${JSON.stringify(metrics, null, 2)}\n`,
      'utf8',
    )

    promoteCaptureDirectory(stagedAssetsDir, assetsDir)

    process.stdout.write(`${JSON.stringify(metrics, null, 2)}\n`)
  } finally {
    try {
      if (browser) await browser.close()
    } finally {
      if (fs.existsSync(stagedAssetsDir)) {
        fs.rmSync(stagedAssetsDir, { recursive: true, force: true })
      }
    }
  }
}

main().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
