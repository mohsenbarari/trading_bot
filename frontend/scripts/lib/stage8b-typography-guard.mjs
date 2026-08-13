import postcss from 'postcss'

export const STAGE8B_TYPOGRAPHY_PATHS = Object.freeze({
  app: 'frontend/src/App.vue',
  mainCss: 'frontend/src/assets/main.css',
  indexHtml: 'frontend/index.html',
  mainTs: 'frontend/src/main.ts',
})

export const STAGE8B_APPROVED_PERSIAN_FAMILY = 'Vazirmatn, Tahoma, Arial, sans-serif'
export const STAGE8B_BOOTSTRAP_FAMILY =
  'Vazirmatn, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'

const APP_SHELL_STATIC_CLASS_TOKENS = Object.freeze([
  'app-shell',
  'h-full',
  'flex',
  'flex-col',
  'font-sans',
  'text-gray-900',
  'antialiased',
  'selection:bg-primary-500',
  'selection:text-white',
  'overflow-hidden',
])

const MAIN_CSS_ROOT_FONT_TOKENS = Object.freeze({
  '--ds-font-xs': '0.7rem',
  '--ds-font-sm': '0.75rem',
  '--ds-font-base': '0.85rem',
  '--ds-font-md': '0.9rem',
  '--ds-font-lg': '1rem',
  '--ds-font-xl': '1.1rem',
  '--ds-font-2xl': '1.2rem',
  '--ds-font-mono':
    'ui-monospace, SFMono-Regular, "SF Mono", Consolas, "Liberation Mono", monospace',
  '--ds-font-eyebrow': 'var(--ds-font-xs)',
  '--ds-font-badge': 'var(--ds-font-xs)',
  '--ds-font-meta': 'var(--ds-font-xs)',
  '--ds-font-helper': 'var(--ds-font-xs)',
})

const MAIN_CSS_GLOBAL_TYPOGRAPHY_DECLARATIONS = Object.freeze([
  Object.freeze({ selector: 'html', prop: 'font-size', value: '16px', important: true }),
  Object.freeze({ selector: 'html', prop: '-webkit-text-size-adjust', value: '100%', important: false }),
  Object.freeze({ selector: 'html', prop: 'text-size-adjust', value: '100%', important: false }),
])

const TYPOGRAPHY_PROPERTY = /^(?:font(?:-[a-z-]+)?|line-height|letter-spacing|word-spacing|text-(?:size-adjust|transform|rendering)|-webkit-text-size-adjust|direction|unicode-bidi|hyphens|overflow-wrap|word-break|white-space|tab-size)$/i
const TYPOGRAPHY_CUSTOM_PROPERTY = /(?:^--(?:font|default-font|tw-font)|font|typography)/i
const ROOT_SELECTOR = /(?:^|[\s>+~,(])(?::root|html|body|#app|\.app-shell|\.app-route-scroll)(?=$|[\s>+~,.#[:])/i
const UNIVERSAL_SELECTOR = /(?:^|[\s>+~,(])\*(?=$|[\s>+~,.#[:])/i

function normalizeWhitespace(value) {
  return String(value).trim().replace(/\s+/g, ' ')
}

function normalizedSelector(selector) {
  return normalizeWhitespace(selector)
}

function sameOrderedValues(left, right) {
  return (
    Array.isArray(left) &&
    Array.isArray(right) &&
    left.length === right.length &&
    left.every((value, index) => value === right[index])
  )
}

function sourceValue(sources, repoPath) {
  const value = sources instanceof Map ? sources.get(repoPath) : sources?.[repoPath]
  if (typeof value !== 'string') {
    throw new Error(`Stage 8B typography source is missing: ${repoPath}`)
  }
  return value
}

function parseCss(label, source) {
  try {
    return postcss.parse(source)
  } catch (error) {
    throw new Error(
      `${label}: CSS is malformed (${error instanceof Error ? error.message : String(error)})`,
    )
  }
}

function extractStyleBlocks(source, label) {
  const blocks = [...source.matchAll(/<style(?:\s[^>]*)?>([\s\S]*?)<\/style>/gi)].map(
    (match) => match[1],
  )
  if (!blocks.length) throw new Error(`${label}: expected an inline style block`)
  return blocks
}

function declarations(rule) {
  return (rule.nodes ?? []).filter((node) => node.type === 'decl')
}

function hasSharedRootSelector(selector) {
  return ROOT_SELECTOR.test(selector) || UNIVERSAL_SELECTOR.test(selector)
}

function isTypographyCustomProperty(property) {
  return property.startsWith('--') && TYPOGRAPHY_CUSTOM_PROPERTY.test(property)
}

function declarationRecord(selector, declaration) {
  return {
    selector: normalizedSelector(selector),
    prop: declaration.prop.toLowerCase(),
    value: normalizeWhitespace(declaration.value),
    important: Boolean(declaration.important),
  }
}

function sameDeclaration(left, right) {
  return (
    left.selector === right.selector &&
    left.prop === right.prop &&
    left.value === right.value &&
    left.important === right.important
  )
}

function assertSingleAppShellTag(appSource) {
  const tags = [...appSource.matchAll(/<div\b[\s\S]*?>/g)].map((match) => match[0])
  const shellTags = tags.filter((tag) => /(?:^|\s)class\s*=\s*(["'])[^"']*\bapp-shell\b[^"']*\1/.test(tag))
  if (shellTags.length !== 1) {
    throw new Error(`App root shell must have exactly one static app-shell div; found ${shellTags.length}`)
  }
  return shellTags[0]
}

function assertAppComputedContract(appSource) {
  const declarations = [...appSource.matchAll(/\bconst\s+usesApprovedPersianTypography\b/g)]
  if (declarations.length !== 1) {
    throw new Error(
      `App Persian typography eligibility must have exactly one computed declaration; found ${declarations.length}`,
    )
  }

  const routerImport = appSource.match(
    /import\s*\{([\s\S]*?)\}\s*from\s*['"]\.\/router\/uiRouteContract['"]/m,
  )?.[1]
  if (
    !routerImport ||
    !/\bgetUiRouteContractByName\b/.test(routerImport) ||
    !/\bUI_ROUTE_PROTECTION\b/.test(routerImport)
  ) {
    throw new Error('App Persian typography eligibility must use the route-contract imports')
  }

  const approvedComputed =
    /const\s+usesApprovedPersianTypography\s*=\s*computed\(\s*\(\s*\)\s*=>\s*getUiRouteContractByName\(\s*route\.name\s*\)\?\.protection\s*===\s*UI_ROUTE_PROTECTION\.NONE\s*,?\s*\)/g
  if ([...appSource.matchAll(approvedComputed)].length !== 1) {
    throw new Error(
      'App Persian typography eligibility must be the strict NONE-only route-contract expression',
    )
  }
}

function assertLegacyShellBaseClass(appSource) {
  const rootTag = assertSingleAppShellTag(appSource)
  const staticClass = rootTag.match(/(?:^|\s)class\s*=\s*(["'])([^"']*)\1/)?.[2]
  const tokens = staticClass ? staticClass.split(/\s+/).filter(Boolean) : []
  if (!sameOrderedValues(tokens, APP_SHELL_STATIC_CLASS_TOKENS)) {
    throw new Error(
      'App root shell static class contract drifted; its legacy font/layout boundary must remain exact',
    )
  }
  if (/app-(?:shell|route)--persian-typography/.test(rootTag)) {
    throw new Error('App root shell must not receive the Stage 8B typography marker')
  }
  const classBindings = [...rootTag.matchAll(/:class\s*=\s*(["'])([\s\S]*?)\1/g)]
  if (classBindings.length !== 1) {
    throw new Error('App root shell must retain exactly one dynamic class binding')
  }
  const binding = normalizeWhitespace(classBindings[0]?.[2])
  if (binding !== "{ 'app-copyable-info': allowsInformationalCopy }") {
    throw new Error('App root shell dynamic class must remain limited to app-copyable-info')
  }
}

function openingTags(source, componentName) {
  return [...source.matchAll(new RegExp(`<${componentName}\\b[\\s\\S]*?>`, 'g'))].map(
    (match) => match[0],
  )
}

function classBinding(tag, label) {
  const bindings = [...tag.matchAll(/:class\s*=\s*(["'])([\s\S]*?)\1/g)]
  if (bindings.length !== 1) throw new Error(`${label} must have exactly one dynamic class binding`)
  return normalizeWhitespace(bindings[0]?.[2])
}

function assertRouteLocalClassContract(appSource) {
  const markerComputed =
    /const\s+persianTypographyRouteClass\s*=\s*computed\(\s*\(\s*\)\s*=>\s*usesApprovedPersianTypography\.value\s*\?\s*['"]app-route--persian-typography['"]\s*:\s*undefined\s*,?\s*\)/g
  if ([...appSource.matchAll(markerComputed)].length !== 1) {
    throw new Error(
      'App route-local typography marker must be derived only from the NONE-only eligibility computed',
    )
  }

  const scopedTags = openingTags(appSource, 'AppDesignSystemScope').filter((tag) =>
    /\bv-if\s*=\s*(["'])\s*shouldScopeRoute\s*\1/.test(tag),
  )
  if (scopedTags.length !== 1) {
    throw new Error(`App route-scoped branch must occur exactly once; found ${scopedTags.length}`)
  }
  if (
    classBinding(scopedTags[0], 'App route-scoped branch') !==
    "['app-route-v2-scope', persianTypographyRouteClass]"
  ) {
    throw new Error(
      'App route-scoped branch must bind app-route--persian-typography through persianTypographyRouteClass',
    )
  }

  const unscopedTags = openingTags(appSource, 'component').filter(
    (tag) => /\bv-else(?:\s|=|>|$)/.test(tag) && /:is\s*=\s*(["'])Component\1/.test(tag),
  )
  if (unscopedTags.length !== 1) {
    throw new Error(`App unscoped route vnode branch must occur exactly once; found ${unscopedTags.length}`)
  }
  if (
    classBinding(unscopedTags[0], 'App unscoped route vnode branch') !==
    '[reducedMotionRouteClass, persianTypographyRouteClass]'
  ) {
    throw new Error(
      'App unscoped route vnode branch must bind app-route--persian-typography through persianTypographyRouteClass',
    )
  }
}

function sourceEntries(productSources) {
  if (productSources instanceof Map) return [...productSources.entries()]
  if (productSources && typeof productSources === 'object') return Object.entries(productSources)
  throw new TypeError('Stage 8B product source boundary requires a source map')
}

export function assertStage8bTypographyProductSourceBoundary(productSources) {
  const entries = sourceEntries(productSources)
  const byPath = new Map(entries)
  const appSource = byPath.get(STAGE8B_TYPOGRAPHY_PATHS.app)
  if (typeof appSource !== 'string') {
    throw new Error('Stage 8B product source boundary is missing frontend/src/App.vue')
  }

  const marker = /\bapp-route--persian-typography\b/g
  const appMatches = [...appSource.matchAll(marker)]
  if (appMatches.length !== 2) {
    throw new Error('App.vue must contain exactly the computed and CSS route-local typography marker')
  }
  for (const [repoPath, source] of entries) {
    if (repoPath === STAGE8B_TYPOGRAPHY_PATHS.app) continue
    if (typeof source !== 'string') {
      throw new Error(`Stage 8B product source is not text: ${repoPath}`)
    }
    marker.lastIndex = 0
    if (marker.test(source)) {
      throw new Error(`Stage 8B route-local typography marker must not appear outside App.vue: ${repoPath}`)
    }
  }
}

function assertAppLocalCssContract(appSource) {
  const root = parseCss('App.vue', extractStyleBlocks(appSource, 'App.vue').join('\n'))
  root.walkRules((rule) => {
    const selectors = rule.selectors.map(normalizedSelector)
    if (!selectors.some((selector) => /\.app-shell(?:\b|--)/.test(selector))) return
    for (const declaration of declarations(rule)) {
      if (TYPOGRAPHY_PROPERTY.test(declaration.prop) || isTypographyCustomProperty(declaration.prop)) {
        throw new Error('App root shell must not receive a Stage 8B typography/cascade declaration')
      }
    }
  })

  const typographyRules = []
  root.walkRules((rule) => {
    const selectors = rule.selectors.map(normalizedSelector)
    if (!selectors.some((selector) => selector.includes('app-route--persian-typography'))) return
    if (selectors.length !== 1 || selectors[0] !== '.app-route--persian-typography') {
      throw new Error(`App route-local typography selector drift: ${selectors.join(', ')}`)
    }
    typographyRules.push(rule)
  })
  if (typographyRules.length !== 1) {
    throw new Error('App route-local Persian typography style rule is missing or duplicated')
  }
  const typographyDeclarations = declarations(typographyRules[0]).map((declaration) => ({
    prop: declaration.prop.toLowerCase(),
    value: normalizeWhitespace(declaration.value),
    important: Boolean(declaration.important),
  }))
  const expected = [
    { prop: 'font-family', value: STAGE8B_APPROVED_PERSIAN_FAMILY, important: false },
    { prop: 'font-synthesis', value: 'none', important: false },
  ]
  if (JSON.stringify(typographyDeclarations) !== JSON.stringify(expected)) {
    throw new Error(
      'App route-local Persian typography rule may contain only the approved literal family and font-synthesis:none',
    )
  }

  const appTypographyDeclarations = []
  root.walkDecls((declaration) => {
    if (!/^font-(?:family|synthesis)$/i.test(declaration.prop)) return
    appTypographyDeclarations.push({
      prop: declaration.prop.toLowerCase(),
      value: normalizeWhitespace(declaration.value),
    })
  })
  if (
    JSON.stringify(appTypographyDeclarations) !==
    JSON.stringify([
      { prop: 'font-family', value: STAGE8B_APPROVED_PERSIAN_FAMILY },
      { prop: 'font-synthesis', value: 'none' },
    ])
  ) {
    throw new Error('App typography declarations escaped the approved route-local bridge')
  }
}

function assertMainCssGlobalTypographyContract(mainCssSource) {
  const root = parseCss('main.css', mainCssSource)
  const rootTokens = []
  const globalDeclarations = []

  root.walkDecls((declaration) => {
    const parentRule = declaration.parent?.type === 'rule' ? declaration.parent : null
    const selectors = parentRule?.selectors?.map(normalizedSelector) ?? []
    const targetsSharedRoot = selectors.some(hasSharedRootSelector)
    const exactRoot = selectors.length === 1 && selectors[0] === ':root'

    if (isTypographyCustomProperty(declaration.prop) && (targetsSharedRoot || !parentRule)) {
      if (!exactRoot) {
        throw new Error(
          `main.css must not add a global typography custom property outside :root: ${declaration.prop}`,
        )
      }
      rootTokens.push({
        prop: declaration.prop,
        value: normalizeWhitespace(declaration.value),
      })
    }

    if (!TYPOGRAPHY_PROPERTY.test(declaration.prop) || !targetsSharedRoot) return
    for (const selector of selectors.filter(hasSharedRootSelector)) {
      globalDeclarations.push(declarationRecord(selector, declaration))
    }
  })

  const expectedRootTokens = Object.entries(MAIN_CSS_ROOT_FONT_TOKENS).map(([prop, value]) => ({
    prop,
    value,
  }))
  if (JSON.stringify(rootTokens) !== JSON.stringify(expectedRootTokens)) {
    throw new Error('main.css global typography token baseline drifted')
  }

  if (
    globalDeclarations.length !== MAIN_CSS_GLOBAL_TYPOGRAPHY_DECLARATIONS.length ||
    !globalDeclarations.every((record, index) =>
      sameDeclaration(record, MAIN_CSS_GLOBAL_TYPOGRAPHY_DECLARATIONS[index]),
    )
  ) {
    throw new Error('main.css must not add or alter global typography/layout cascade declarations')
  }
}

function assertIndexBootstrapTypographyContract(indexHtmlSource) {
  const styleBlocks = extractStyleBlocks(indexHtmlSource, 'index.html')
  if (styleBlocks.length !== 1) {
    throw new Error(`index.html must retain exactly one bootstrap style block; found ${styleBlocks.length}`)
  }
  if (/@import\b/i.test(styleBlocks[0])) {
    throw new Error('index.html bootstrap typography must not load a stylesheet through @import')
  }
  if (/<link\b[^>]*\brel\s*=\s*(['"]?)stylesheet\1/i.test(indexHtmlSource)) {
    throw new Error('index.html bootstrap typography must not add a stylesheet link')
  }

  const root = parseCss('index.html bootstrap style', styleBlocks[0])
  const globalDeclarations = []
  root.walkDecls((declaration) => {
    const parentRule = declaration.parent?.type === 'rule' ? declaration.parent : null
    const selectors = parentRule?.selectors?.map(normalizedSelector) ?? []
    const targetsSharedRoot = selectors.some(hasSharedRootSelector)
    if (!targetsSharedRoot || !TYPOGRAPHY_PROPERTY.test(declaration.prop)) return
    for (const selector of selectors.filter(hasSharedRootSelector)) {
      globalDeclarations.push(declarationRecord(selector, declaration))
    }
  })

  const expected = [
    {
      selector: 'body',
      prop: 'font-family',
      value: STAGE8B_BOOTSTRAP_FAMILY,
      important: false,
    },
  ]
  if (
    globalDeclarations.length !== expected.length ||
    !globalDeclarations.every((record, index) => sameDeclaration(record, expected[index]))
  ) {
    throw new Error('index.html bootstrap typography baseline drifted')
  }
}

function assertMainTsBootstrapContract(mainTsSource) {
  const staticImports = [
    ...mainTsSource.matchAll(/\bimport\s*(?:[^'"()]*?\s+from\s*)?['"]([^'"]+)['"]/g),
  ].map((match) => match[1])
  const cssImports = staticImports.filter((specifier) => specifier.endsWith('.css'))
  const expectedCssImports = ['./assets/main.css', 'vazirmatn/Vazirmatn-font-face.css']
  if (!sameOrderedValues(cssImports, expectedCssImports)) {
    throw new Error('main.ts bootstrap CSS imports drifted from the approved local Vazirmatn boundary')
  }
  if (/\bimport\s*\(\s*['"][^'"]+\.css['"]\s*\)/.test(mainTsSource)) {
    throw new Error('main.ts must not dynamically import a bootstrap stylesheet')
  }

  const rootStyleMutation =
    /document\.(?:body|documentElement)\.style\.(?:font(?:[A-Z][A-Za-z]*)?|lineHeight|letterSpacing|wordSpacing|textTransform|textRendering|direction|whiteSpace)\s*=/
  const rootStyleCustomProperty =
    /document\.(?:body|documentElement)\.style\.setProperty\(\s*['"][^'"]*(?:font|typography|line-height|letter-spacing|word-spacing|text-size)[^'"]*['"]/i
  const rootStyleText = /document\.(?:body|documentElement)\.style\.cssText\s*=/
  const rootStyleAttribute = /document\.(?:body|documentElement)\.setAttribute\(\s*['"]style['"]/i
  const rootFontClass =
    /document\.(?:body|documentElement)\.classList\.(?:add|remove|toggle|replace)\([^)]*['"][^'"]*\b(?:font|leading|tracking)-/i
  if (
    rootStyleMutation.test(mainTsSource) ||
    rootStyleCustomProperty.test(mainTsSource) ||
    rootStyleText.test(mainTsSource) ||
    rootStyleAttribute.test(mainTsSource) ||
    rootFontClass.test(mainTsSource)
  ) {
    throw new Error('main.ts must not mutate root typography/cascade at runtime')
  }
  if (
    /document\.createElement\(\s*['"]style['"]\s*\)/i.test(mainTsSource) ||
    /\bnew\s+CSSStyleSheet\b/.test(mainTsSource) ||
    /\badoptedStyleSheets\b/.test(mainTsSource)
  ) {
    throw new Error('main.ts must not inject a bootstrap stylesheet at runtime')
  }
}

/**
 * Stage 8B permits a single, route-contract-gated Vazirmatn bridge. The
 * marker lives on each route vnode so a FULL/MIXED outgoing page cannot inherit
 * the new family during Vue's simultaneous default route fade.
 */
export function assertStage8bTypographyContract(sources, productSources = sources) {
  const appSource = sourceValue(sources, STAGE8B_TYPOGRAPHY_PATHS.app)
  const mainCssSource = sourceValue(sources, STAGE8B_TYPOGRAPHY_PATHS.mainCss)
  const indexHtmlSource = sourceValue(sources, STAGE8B_TYPOGRAPHY_PATHS.indexHtml)
  const mainTsSource = sourceValue(sources, STAGE8B_TYPOGRAPHY_PATHS.mainTs)

  assertAppComputedContract(appSource)
  assertLegacyShellBaseClass(appSource)
  assertRouteLocalClassContract(appSource)
  assertAppLocalCssContract(appSource)
  assertStage8bTypographyProductSourceBoundary(productSources)
  assertMainCssGlobalTypographyContract(mainCssSource)
  assertIndexBootstrapTypographyContract(indexHtmlSource)
  assertMainTsBootstrapContract(mainTsSource)

  return Object.freeze({
    eligibility: 'route-contract-none-only',
    legacyBaseClass: 'font-sans',
    scopedClass: 'app-route--persian-typography',
    family: STAGE8B_APPROVED_PERSIAN_FAMILY,
    fontSynthesis: 'none',
    bootstrap: 'local-vazirmatn-font-face',
  })
}
