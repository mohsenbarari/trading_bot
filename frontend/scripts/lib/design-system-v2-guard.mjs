import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import postcss from 'postcss'
import ts from 'typescript'

const FROZEN_TOKEN_CONTRACT_FILE_SHA256 =
  'a0c3f3560acaa8c4fddc123ec042657d7db73d0599698e49eb172f647227cf66'
const frozenTokenContractSource = readFileSync(
  path.resolve(process.cwd(), 'src/design-system-v2/canonical-token-contract.json'),
  'utf8',
)
const frozenTokenContractSourceSha256 = createHash('sha256')
  .update(frozenTokenContractSource)
  .digest('hex')
const FROZEN_TOKEN_CONTRACT = Object.freeze(JSON.parse(frozenTokenContractSource))

const V2_SCOPE_ATTRIBUTE =
  '\\[\\s*data-ui-system\\s*=\\s*(?:"(v2(?:-portal)?)"|\'(v2(?:-portal)?)\'|(v2(?:-portal)?))\\s*\\]'
const V2_SCOPE_SELECTOR = new RegExp(V2_SCOPE_ATTRIBUTE, 'i')
const V2_SCOPE_SELECTOR_EXACT = new RegExp(`^${V2_SCOPE_ATTRIBUTE}$`, 'i')
const V2_SCOPE_SELECTOR_PREFIX = new RegExp(`^${V2_SCOPE_ATTRIBUTE}`, 'i')
const V2_TOKEN = /--ui-v2-[a-z0-9-]+/g
const V2_TOKEN_USAGE = /var\(\s*(--ui-v2-[a-z0-9-]+)/g
const CUSTOM_PROPERTY_USAGE = /var\(\s*(--[-_a-z0-9]+)/gi
const LEGACY_TOKEN = /--ds-[a-z0-9-]+/g
const RAW_COLOR =
  /#[0-9a-f]{3,8}\b|\b(?:color|color-mix|device-cmyk|hsl|hsla|hwb|lab|lch|oklab|oklch|rgb|rgba)\s*\(/gi
const CSS_COLOR_FUNCTIONS = new Set([
  'color',
  'color-mix',
  'device-cmyk',
  'hsl',
  'hsla',
  'hwb',
  'lab',
  'lch',
  'oklab',
  'oklch',
  'rgb',
  'rgba',
])
const CSS_NAMED_COLORS = new Set([
  'aliceblue',
  'antiquewhite',
  'aqua',
  'aquamarine',
  'azure',
  'beige',
  'bisque',
  'black',
  'blanchedalmond',
  'blue',
  'blueviolet',
  'brown',
  'burlywood',
  'cadetblue',
  'chartreuse',
  'chocolate',
  'coral',
  'cornflowerblue',
  'cornsilk',
  'crimson',
  'cyan',
  'darkblue',
  'darkcyan',
  'darkgoldenrod',
  'darkgray',
  'darkgreen',
  'darkgrey',
  'darkkhaki',
  'darkmagenta',
  'darkolivegreen',
  'darkorange',
  'darkorchid',
  'darkred',
  'darksalmon',
  'darkseagreen',
  'darkslateblue',
  'darkslategray',
  'darkslategrey',
  'darkturquoise',
  'darkviolet',
  'deeppink',
  'deepskyblue',
  'dimgray',
  'dimgrey',
  'dodgerblue',
  'firebrick',
  'floralwhite',
  'forestgreen',
  'fuchsia',
  'gainsboro',
  'ghostwhite',
  'gold',
  'goldenrod',
  'gray',
  'green',
  'greenyellow',
  'grey',
  'honeydew',
  'hotpink',
  'indianred',
  'indigo',
  'ivory',
  'khaki',
  'lavender',
  'lavenderblush',
  'lawngreen',
  'lemonchiffon',
  'lightblue',
  'lightcoral',
  'lightcyan',
  'lightgoldenrodyellow',
  'lightgray',
  'lightgreen',
  'lightgrey',
  'lightpink',
  'lightsalmon',
  'lightseagreen',
  'lightskyblue',
  'lightslategray',
  'lightslategrey',
  'lightsteelblue',
  'lightyellow',
  'lime',
  'limegreen',
  'linen',
  'magenta',
  'maroon',
  'mediumaquamarine',
  'mediumblue',
  'mediumorchid',
  'mediumpurple',
  'mediumseagreen',
  'mediumslateblue',
  'mediumspringgreen',
  'mediumturquoise',
  'mediumvioletred',
  'midnightblue',
  'mintcream',
  'mistyrose',
  'moccasin',
  'navajowhite',
  'navy',
  'oldlace',
  'olive',
  'olivedrab',
  'orange',
  'orangered',
  'orchid',
  'palegoldenrod',
  'palegreen',
  'paleturquoise',
  'palevioletred',
  'papayawhip',
  'peachpuff',
  'peru',
  'pink',
  'plum',
  'powderblue',
  'purple',
  'rebeccapurple',
  'red',
  'rosybrown',
  'royalblue',
  'saddlebrown',
  'salmon',
  'sandybrown',
  'seagreen',
  'seashell',
  'sienna',
  'silver',
  'skyblue',
  'slateblue',
  'slategray',
  'slategrey',
  'snow',
  'springgreen',
  'steelblue',
  'tan',
  'teal',
  'thistle',
  'tomato',
  'turquoise',
  'violet',
  'wheat',
  'white',
  'whitesmoke',
  'yellow',
  'yellowgreen',
])
const CSS_SYSTEM_COLORS = new Set([
  'accentcolor',
  'accentcolortext',
  'activeborder',
  'activecaption',
  'activetext',
  'appworkspace',
  'background',
  'buttonborder',
  'buttonface',
  'buttonhighlight',
  'buttonshadow',
  'buttontext',
  'canvas',
  'canvastext',
  'captiontext',
  'field',
  'fieldtext',
  'graytext',
  'highlight',
  'highlighttext',
  'inactiveborder',
  'inactivecaption',
  'inactivecaptiontext',
  'infobackground',
  'infotext',
  'linktext',
  'mark',
  'marktext',
  'menu',
  'menutext',
  'scrollbar',
  'selecteditem',
  'selecteditemtext',
  'threeddarkshadow',
  'threedface',
  'threedhighlight',
  'threedlightshadow',
  'threedshadow',
  'visitedtext',
  'window',
  'windowframe',
  'windowtext',
])
const DESIGN_LENGTH_LITERAL =
  /-?(?:\d+\.?\d*|\.\d+)(cap|ch|cm|dvh|dvw|em|ex|ic|in|lh|lvh|lvw|mm|pc|pt|px|q|rem|rlh|svh|svw|vmax|vmin|vh|vw|%)(?![a-z%])/gi
const DESIGN_TIME_LITERAL = /-?(?:\d+\.?\d*|\.\d+)(ms|s)\b/gi
const CANONICAL_V2_CLASS =
  /^(?:ui-v2-catalog(?:__[-_a-z0-9]+)?|ui-v2-motion-(?:micro|state)|ui-v2-scope)$/
const STAGE3_PRODUCT_CLASS =
  /^ui-v2-(?:auth|bottom-nav|connection|pwa|route|session|toast)-[-_a-z0-9]+$/
const STAGE4_PRODUCT_CLASS =
  /^ui-v2-(?:account|browser-push|daily|home|notifications|operations|settings|workspace)(?:[-_]|$)[-_a-z0-9]*$/
const ALLOWED_STAGE3_PRODUCT_CLASSES = new Set([
  'app-route-v2-scope',
  'ui-v2-bottom-nav',
  'ui-v2-public-header',
])
const ALLOWED_LEGACY_PRIMITIVE_CLASSES = new Set([
  'ui-button',
  'ui-button--primary',
  'ui-button--danger',
  'ui-button--block',
  'ui-button--ghost',
  'ui-button--secondary',
  'ui-button__spinner',
  'ui-button__icon',
  'ui-button__label',
  'ui-card',
  'ui-empty-state',
  'ui-empty-state--danger',
  'ui-form-field',
  'ui-form-field__error',
  'ui-form-field__label',
  'ui-input',
  'ui-loading-state',
  'ui-loading-state__spinner',
  'ui-list-item',
  'ui-list-item__copy',
  'ui-list-item__trailing',
  'ui-page',
  'ui-page--narrow',
  'ui-page-header',
  'ui-page-header__actions',
  'ui-page-header__copy',
  'ui-page-header__eyebrow',
  'ui-workspace',
  'ui-workspace--narrow',
  'ui-icon-button',
  'ui-icon-button--neutral',
  'ui-icon-button--md',
  'ui-icon-button--sm',
  'ui-icon-button--primary',
  'ui-icon-button--danger',
  'ui-action-card',
  'ui-action-card--neutral',
  'ui-action-card--primary',
  'ui-action-card--success',
  'ui-action-card--warning',
  'ui-action-card--danger',
  'ui-action-card--info',
  'ui-action-card__arrow',
  'ui-action-card__badge',
  'ui-action-card__copy',
  'ui-action-card__description',
  'ui-action-card__icon',
  'ui-action-card__title-row',
  'ui-filter-chips',
  'ui-filter-chip',
  'ui-section-card',
  'ui-section-card--neutral',
  'ui-section-card--primary',
  'ui-section-card--success',
  'ui-section-card--warning',
  'ui-section-card--danger',
  'ui-section-card--info',
  'ui-section-card__actions',
  'ui-section-card__body',
  'ui-section-card__copy',
  'ui-section-card__header',
  'ui-status-badge',
  'ui-status-badge--neutral',
  'ui-status-badge--primary',
  'ui-status-badge--warning',
  'ui-status-badge--info',
  'ui-status-badge--danger',
  'ui-status-badge--success',
  'ui-textarea',
  'ui-toast',
  'ui-toast--danger',
  'ui-toast--info',
  'ui-toast--success',
  'ui-toast--warning',
  'is-current',
  'is-active',
  'is-loading',
  'is-invalid',
  'slide-up-enter-active',
  'slide-up-enter-from',
  'slide-up-leave-active',
  'slide-up-leave-to',
])
const TOKEN_SOURCE_PATH = /^src\/styles\/design-system-v2\.tokens\.css$/
const ALLOWED_MEDIA_QUERIES = new Set([
  '(max-width: 430px)',
  '(min-width: 900px)',
  '(prefers-reduced-motion: reduce)',
])
const RESPONSIVE_OVERRIDE_TOKENS = new Set(['--ui-v2-motion-micro', '--ui-v2-motion-state'])

export function hasV2CssMarker(source) {
  const withoutComments = source.replace(/\/\*[\s\S]*?\*\//g, '')
  return /--ui-v2-|\[\s*data-ui-system\b|\.ui-v2-/i.test(withoutComments)
}

const CATALOG_ALLOWED_IMPORTS = [
  './AppButton.vue',
  './AppCard.vue',
  './AppDesignSystemScope.vue',
  './AppErrorState.vue',
  './AppFormField.vue',
  './AppInput.vue',
  './AppListItem.vue',
  './AppLoadingState.vue',
  './AppStatusBadge.vue',
  'vue',
]
const CATALOG_PROTECTED_REFERENCE =
  /\b(?:AdminMessagesView|ChatView|CreateChannelView|MarketView|MessengerView|OfferPreviewModal|OffersList|ShareReceiveView|TradeLotSuggestionAlert|TradingSettings)\b|بازار|پیام[\u200c\s-]?رسان/i
const CATALOG_PROTECTED_ROUTE =
  /['"`]\/(?:market|chat|admin\/channels|share-receive)(?:[/?#'"`]|$)/i
const CATALOG_PROTECTED_ROUTE_VALUE = /^\/(?:market|chat|admin\/channels|share-receive)(?:[/?#]|$)/i

export function checkCatalogBoundary({
  path = 'src/components/ui/AppDesignSystemCatalog.vue',
  source,
}) {
  const findings = []
  const scripts = [...source.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].map(
    (match) => match[1],
  )
  if (scripts.length === 0) {
    return [violation('invalid-catalog-source', path, 'Catalog must contain a script block')]
  }
  const imports = []
  const nonliteralDynamicImports = []
  const foldedStrings = new Set()
  for (const [scriptIndex, script] of scripts.entries()) {
    const sourceFile = ts.createSourceFile(
      `${path}#script-${scriptIndex + 1}`,
      script,
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TS,
    )
    function visit(node) {
      if (ts.isImportDeclaration(node) && ts.isStringLiteralLike(node.moduleSpecifier)) {
        imports.push(node.moduleSpecifier.text)
      } else if (
        ts.isCallExpression(node) &&
        node.expression.kind === ts.SyntaxKind.ImportKeyword
      ) {
        if (node.arguments.length === 1 && ts.isStringLiteralLike(node.arguments[0])) {
          imports.push(node.arguments[0].text)
        } else {
          nonliteralDynamicImports.push(node)
        }
      }
      if (
        ts.isTemplateExpression(node) ||
        (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.PlusToken)
      ) {
        const folded = staticStringValue(node)
        if (folded !== null) foldedStrings.add(folded)
      }
      ts.forEachChild(node, visit)
    }
    visit(sourceFile)
  }

  const actualImports = [...new Set(imports)].sort()
  const expectedImports = [...CATALOG_ALLOWED_IMPORTS].sort()
  if (JSON.stringify(actualImports) !== JSON.stringify(expectedImports)) {
    findings.push(
      violation(
        'catalog-import-boundary',
        path,
        `Catalog imports must equal the frozen public allowlist; received ${actualImports.join(', ')}`,
      ),
    )
  }
  if (nonliteralDynamicImports.length > 0) {
    findings.push(
      violation(
        'catalog-nonliteral-dynamic-import',
        path,
        'Catalog dynamic imports must use one literal module specifier from the frozen allowlist',
      ),
    )
  }
  const foldedProtected = [...foldedStrings].some(
    (value) => CATALOG_PROTECTED_REFERENCE.test(value) || CATALOG_PROTECTED_ROUTE_VALUE.test(value),
  )
  if (
    foldedProtected ||
    CATALOG_PROTECTED_REFERENCE.test(source) ||
    CATALOG_PROTECTED_ROUTE.test(source)
  ) {
    findings.push(
      violation(
        'catalog-protected-reference',
        path,
        'Catalog must not reference protected surfaces, routes, or interior components',
      ),
    )
  }

  return findings.sort(compareFindings)
}
const CATALOG_ROUTE =
  /(?:^|\/)(?:design-system(?:-v2)?|ui-v2(?:-catalog)?|component-catalog|catalog|storybook)(?:\/|$)/i

const REQUIRED_FULL_PROTECTED_ROUTES = ['/market', '/chat', '/admin/channels', '/share-receive']

const STAGE4_CATCH_ALL_PATH = '/:pathMatch(.*)*'

const REQUIRED_STAGE4_SCOPE_ROUTES = new Map([
  [
    'route',
    [
      '/setup-password',
      '/login',
      '/operations',
      '/account',
      '/account/security',
      '/account/storage',
      '/account/notifications',
      '/i/:code',
      '/register',
      STAGE4_CATCH_ALL_PATH,
    ],
  ],
  [
    'section',
    [
      '/',
      '/operations/customers',
      '/operations/customers/:relationId',
      '/operations/accountants',
      '/operations/accountants/:relationId',
      '/users/:id',
      '/profile',
      '/settings',
      '/admin',
      '/admin/invitations',
      '/admin/users',
      '/admin/users/:id',
      '/admin/commodities',
      '/admin/messages',
      '/admin/system',
      '/notifications',
    ],
  ],
  ['off', REQUIRED_FULL_PROTECTED_ROUTES],
])

const REQUIRED_STANDARD_AUTHENTICATED_ROUTES = [
  '/',
  '/operations',
  '/operations/customers',
  '/operations/customers/:relationId',
  '/operations/accountants',
  '/operations/accountants/:relationId',
  '/account',
  '/account/security',
  '/account/storage',
  '/account/notifications',
  '/users/:id',
  '/profile',
  '/settings',
  '/admin',
  '/admin/invitations',
  '/admin/users',
  '/admin/users/:id',
  '/admin/commodities',
  '/admin/messages',
  '/admin/system',
  '/notifications',
]

const REQUIRED_STAGE4_SHELL_ROUTES = new Map([
  ['public', ['/login', '/i/:code', '/register']],
  ['focused-authenticated', ['/setup-password']],
  ['standard-authenticated', REQUIRED_STANDARD_AUTHENTICATED_ROUTES],
  ['protected-legacy', REQUIRED_FULL_PROTECTED_ROUTES],
  ['system-recovery', [STAGE4_CATCH_ALL_PATH]],
])

const STAGE4_ACTIVATION_BOUNDARY_PATHS = new Set([
  'src/App.vue',
  'src/components/SessionApprovalModal.vue',
  'src/components/workspace/WorkspaceActionTile.vue',
  'src/components/workspace/WorkspaceAccountDeletionDialog.vue',
  'src/components/workspace/WorkspaceDangerZone.vue',
  'src/components/workspace/WorkspaceNotice.vue',
  'src/components/workspace/WorkspaceSection.vue',
  'src/components/workspace/WorkspaceShell.vue',
  'src/components/workspace/WorkspaceStatTile.vue',
  'src/views/DashboardView.vue',
])

const STAGE4_WORKSPACE_HELPER_BOUNDARY_PATHS = new Set([
  'src/components/workspace/WorkspaceActionTile.vue',
  'src/components/workspace/WorkspaceDangerZone.vue',
  'src/components/workspace/WorkspaceNotice.vue',
  'src/components/workspace/WorkspaceSection.vue',
  'src/components/workspace/WorkspaceStatTile.vue',
])

const REQUIRED_MIXED_INTERIORS = new Map([
  ['/', ['home-market-widget']],
  ['/admin/messages', ['admin-messages-market-delivery', 'admin-messages-messenger-delivery']],
  ['/admin/system', ['trading-settings-market-controls']],
])

function violation(code, file, detail, location) {
  return { code, file, detail, ...(location ? { location } : {}) }
}

function nodeLocation(node) {
  const start = node.source?.start
  return start ? `${start.line}:${start.column}` : undefined
}

function isInsideKeyframes(node) {
  let parent = node.parent
  while (parent) {
    if (parent.type === 'atrule' && /(?:^|-)keyframes$/i.test(parent.name)) return true
    parent = parent.parent
  }
  return false
}

function declarationContext(node) {
  const contexts = []
  let parent = node.parent
  while (parent) {
    if (parent.type === 'atrule') contexts.push(`@${parent.name} ${parent.params}`.trim())
    parent = parent.parent
  }
  return contexts.reverse().join(' > ') || 'base'
}

function tokenDefinitionEntries(root) {
  const entries = []
  root.walkDecls(/^--ui-v2-/, (declaration) => {
    entries.push({
      name: declaration.prop,
      value: declaration.value.trim(),
      context: declarationContext(declaration),
    })
  })
  return entries
}

function tokenDefinitionKey({ name, value, context }) {
  return `${name}\u0000${value}\u0000${context}`
}

function bannedGlobalSelector(selector) {
  if (/:root\b/i.test(selector)) return ':root'
  if (/(^|[\s>+~,(])html(?=$|[\s>+~.#[:),])/i.test(selector)) return 'html'
  if (/(^|[\s>+~,(])body(?=$|[\s>+~.#[:),])/i.test(selector)) return 'body'
  if (/(^|[\s>+~,(])\*(?=$|[\s>+~.#[:),])/i.test(selector)) return '*'
  return null
}

function scopeValue(selector) {
  const match = V2_SCOPE_SELECTOR_EXACT.exec(selector.trim())
  return match?.[1] ?? match?.[2] ?? match?.[3] ?? null
}

function scopedSelectorPrefixLength(selector) {
  const directMatch = V2_SCOPE_SELECTOR_PREFIX.exec(selector)
  if (directMatch) return directMatch[0].length

  const functionalMatch = /^:(?:is|where)\(([^()]*)\)/i.exec(selector)
  if (!functionalMatch) return null

  const alternatives = functionalMatch[1].split(',').map((value) => scopeValue(value))
  if (
    alternatives.length !== 2 ||
    alternatives.some((value) => value === null) ||
    new Set(alternatives).size !== 2 ||
    !alternatives.includes('v2') ||
    !alternatives.includes('v2-portal')
  ) {
    return null
  }
  return functionalMatch[0].length
}

function isStrictlyScopedSelector(selector) {
  const prefixLength = scopedSelectorPrefixLength(selector)
  if (prefixLength === null) return false
  return !['+', '~'].includes(firstCombinatorAfterScope(selector.slice(prefixLength)))
}

function firstCombinatorAfterScope(source) {
  let parentheses = 0
  let brackets = 0
  let quote = null
  let escaped = false

  for (let index = 0; index < source.length; index += 1) {
    const character = source[index]
    if (escaped) {
      escaped = false
      continue
    }
    if (character === '\\') {
      escaped = true
      continue
    }
    if (quote !== null) {
      if (character === quote) quote = null
      continue
    }
    if (character === '"' || character === "'") {
      quote = character
      continue
    }
    if (character === '[') brackets += 1
    else if (character === ']') brackets = Math.max(0, brackets - 1)
    else if (character === '(' && brackets === 0) parentheses += 1
    else if (character === ')' && brackets === 0) parentheses = Math.max(0, parentheses - 1)
    else if (parentheses === 0 && brackets === 0) {
      if (character === '+' || character === '~' || character === '>') return character
      if (/\s/.test(character)) {
        let next = index + 1
        while (next < source.length && /\s/.test(source[next])) next += 1
        if (source[next] === '+' || source[next] === '~' || source[next] === '>') {
          return source[next]
        }
        return ' '
      }
    }
  }
  return null
}

function tokenAliasCycles(aliasGraph) {
  const cycles = new Set()
  const visited = new Set()
  const active = []
  const activeSet = new Set()

  function visit(token) {
    if (activeSet.has(token)) {
      const cycleStart = active.indexOf(token)
      const cycle = active.slice(cycleStart)
      const canonical = cycle
        .map((_, index) => [...cycle.slice(index), ...cycle.slice(0, index)].join(' -> '))
        .sort()[0]
      cycles.add(`${canonical} -> ${canonical.split(' -> ')[0]}`)
      return
    }
    if (visited.has(token)) return
    visited.add(token)
    active.push(token)
    activeSet.add(token)
    for (const dependency of aliasGraph.get(token) ?? []) {
      if (aliasGraph.has(dependency)) visit(dependency)
    }
    active.pop()
    activeSet.delete(token)
  }

  for (const token of aliasGraph.keys()) visit(token)
  return [...cycles].sort()
}

function stripCssLiteralPayloads(value) {
  return value
    .replace(/url\(\s*(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^)])*\)/gi, '')
    .replace(/"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/g, '')
}

function stripCssFunctions(value, ignoredFunctions) {
  let result = ''
  let index = 0
  while (index < value.length) {
    const match = /^([a-z][-_a-z0-9]*)\(/i.exec(value.slice(index))
    const hasIdentifierBefore = index > 0 && /[-_a-z0-9]/i.test(value[index - 1])
    if (!match || hasIdentifierBefore || !ignoredFunctions.has(match[1].toLowerCase())) {
      result += value[index]
      index += 1
      continue
    }

    let cursor = index + match[0].length
    let depth = 1
    while (cursor < value.length && depth > 0) {
      if (value[cursor] === '(') depth += 1
      if (value[cursor] === ')') depth -= 1
      cursor += 1
    }
    result += ' '
    index = cursor
  }
  return result
}

function hasHardcodedDesignValue(property, value) {
  const searchable = stripCssFunctions(stripCssLiteralPayloads(value), CSS_COLOR_FUNCTIONS)
  DESIGN_LENGTH_LITERAL.lastIndex = 0
  for (const match of searchable.matchAll(DESIGN_LENGTH_LITERAL)) {
    const amount = Number.parseFloat(match[0])
    const unit = match[1]?.toLowerCase()
    if (amount === 0) continue
    if (
      amount === 100 &&
      unit === '%' &&
      /^(?:height|max-height|max-width|min-height|min-width|width)$/i.test(property)
    ) {
      continue
    }
    return true
  }
  if (/^line-height$/i.test(property) && !/var\(\s*--ui-v2-/i.test(searchable)) {
    return /(?:^|[^a-z0-9_.-])-?(?:\d+\.?\d*|\.\d+)(?:$|[^a-z0-9_.-])/i.test(searchable)
  }
  if (/^(?:transform|translate)$/i.test(property) && searchable.trim().toLowerCase() !== 'none') {
    return /-?(?:\d+\.?\d*|\.\d+)/.test(searchable)
  }
  return false
}

function isApprovedAuthFlowViewportFallback(declaration, stylePath) {
  if (
    stylePath !== 'src/styles/design-system-v2.components.css' ||
    declaration.prop.toLowerCase() !== 'min-height' ||
    !['100vh', '100dvh'].includes(declaration.value.trim())
  ) {
    return false
  }

  const parent = declaration.parent
  if (parent?.type !== 'rule' || parent.selectors?.length !== 1) return false

  return (
    parent.selectors[0]
      .replace(/\s+/g, ' ')
      .trim() ===
    ":where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-auth-flow--viewport-fill"
  )
}

function hasHardcodedMotionValue(value) {
  DESIGN_TIME_LITERAL.lastIndex = 0
  return [...value.matchAll(DESIGN_TIME_LITERAL)].some((match) => Number.parseFloat(match[0]) !== 0)
}

function containsRawNamedColor(value) {
  const searchable = stripCssLiteralPayloads(value).replace(/--ui-v2-[a-z0-9-]+/gi, '')
  return [...searchable.matchAll(/(?<![-_a-z0-9])[a-z]+(?![-_a-z0-9])/gi)].some((match) => {
    const color = match[0].toLowerCase()
    return CSS_NAMED_COLORS.has(color) || CSS_SYSTEM_COLORS.has(color)
  })
}

/**
 * Checks only explicitly supplied UIUX v2 styles. Legacy CSS is deliberately
 * outside this function so historical debt cannot fail the Stage 2 guard.
 */
export function checkV2Styles(styleFiles, { enforceFrozenTokenContract = true } = {}) {
  const findings = []
  const definitions = new Map()
  const aliasGraph = new Map()
  const parsedFiles = []

  if (styleFiles.length === 0) {
    findings.push(
      violation(
        'empty-v2-style-set',
        'src/styles',
        'Stage 2 requires at least one V2 style source',
      ),
    )
  }

  for (const styleFile of styleFiles) {
    let root
    try {
      root = postcss.parse(styleFile.source, { from: styleFile.path })
    } catch (error) {
      findings.push(
        violation(
          'css-parse-error',
          styleFile.path,
          error instanceof Error ? error.message : String(error),
        ),
      )
      continue
    }

    parsedFiles.push({ ...styleFile, root })

    root.walkAtRules((atRule) => {
      if (atRule.name.toLowerCase() === 'import') {
        findings.push(
          violation(
            'v2-css-import',
            styleFile.path,
            'V2 style sources must be self-contained and cannot use @import',
            nodeLocation(atRule),
          ),
        )
      }
      if (!['import', 'media'].includes(atRule.name.toLowerCase())) {
        findings.push(
          violation(
            'unsupported-v2-global-at-rule',
            styleFile.path,
            `V2 style sources cannot declare global @${atRule.name} rules`,
            nodeLocation(atRule),
          ),
        )
      }
      if (
        atRule.name.toLowerCase() === 'media' &&
        !ALLOWED_MEDIA_QUERIES.has(atRule.params.trim())
      ) {
        findings.push(
          violation(
            'unapproved-v2-media-query',
            styleFile.path,
            `V2 media query is outside the frozen breakpoint contract: ${atRule.params}`,
            nodeLocation(atRule),
          ),
        )
      }
    })

    root.walkRules((rule) => {
      if (isInsideKeyframes(rule)) return

      for (const selector of rule.selectors) {
        const trimmedSelector = selector.trim()
        const banned = bannedGlobalSelector(trimmedSelector)
        if (banned) {
          findings.push(
            violation(
              'global-v2-selector',
              styleFile.path,
              `V2 CSS cannot target ${banned}: ${trimmedSelector}`,
              nodeLocation(rule),
            ),
          )
        }
        if (!isStrictlyScopedSelector(trimmedSelector)) {
          findings.push(
            violation(
              'unscoped-v2-selector',
              styleFile.path,
              `Selector is missing [data-ui-system="v2"]: ${trimmedSelector}`,
              nodeLocation(rule),
            ),
          )
        }

        for (const match of trimmedSelector.matchAll(/\.([_a-z][-_a-z0-9]*)/gi)) {
          if (
            CANONICAL_V2_CLASS.test(match[1]) ||
            STAGE3_PRODUCT_CLASS.test(match[1]) ||
            STAGE4_PRODUCT_CLASS.test(match[1]) ||
            ALLOWED_STAGE3_PRODUCT_CLASSES.has(match[1]) ||
            ALLOWED_LEGACY_PRIMITIVE_CLASSES.has(match[1])
          )
            continue
          findings.push(
            violation(
              'noncanonical-v2-class',
              styleFile.path,
              `Local or duplicate component class is not allowed in V2 CSS: .${match[1]}`,
              nodeLocation(rule),
            ),
          )
        }
      }
    })

    root.walkDecls((declaration) => {
      const isV2TokenDefinition = declaration.prop.startsWith('--ui-v2-')
      const isCanonicalV2TokenDefinition =
        TOKEN_SOURCE_PATH.test(styleFile.path) && isV2TokenDefinition

      if (declaration.prop.startsWith('--') && !isV2TokenDefinition) {
        if (!declaration.prop.startsWith('--ds-')) {
          findings.push(
            violation(
              'noncanonical-v2-custom-property',
              styleFile.path,
              `V2 CSS custom properties must use the exact --ui-v2-* namespace: ${declaration.prop}`,
              nodeLocation(declaration),
            ),
          )
        }
      }

      if (isV2TokenDefinition) {
        const locations = definitions.get(declaration.prop) ?? []
        locations.push({
          file: styleFile.path,
          location: nodeLocation(declaration),
          context: declarationContext(declaration),
        })
        definitions.set(declaration.prop, locations)

        V2_TOKEN_USAGE.lastIndex = 0
        aliasGraph.set(
          declaration.prop,
          [...declaration.value.matchAll(V2_TOKEN_USAGE)].map((match) => match[1]),
        )
        if (!TOKEN_SOURCE_PATH.test(styleFile.path)) {
          findings.push(
            violation(
              'v2-token-definition-outside-source',
              styleFile.path,
              `${declaration.prop} must be defined only in the canonical V2 token source`,
              nodeLocation(declaration),
            ),
          )
        }
      }

      if (declaration.prop.startsWith('--ds-')) {
        findings.push(
          violation(
            'legacy-token-definition',
            styleFile.path,
            `V2 CSS cannot define or remap ${declaration.prop}`,
            nodeLocation(declaration),
          ),
        )
      }

      LEGACY_TOKEN.lastIndex = 0
      for (const match of declaration.value.matchAll(LEGACY_TOKEN)) {
        findings.push(
          violation(
            'legacy-token-remap',
            styleFile.path,
            `V2 CSS cannot depend on legacy token ${match[0]}`,
            nodeLocation(declaration),
          ),
        )
      }

      CUSTOM_PROPERTY_USAGE.lastIndex = 0
      for (const match of declaration.value.matchAll(CUSTOM_PROPERTY_USAGE)) {
        const customProperty = match[1]
        if (customProperty.startsWith('--ui-v2-') || customProperty.startsWith('--ds-')) continue
        findings.push(
          violation(
            'noncanonical-v2-custom-property-usage',
            styleFile.path,
            `V2 CSS cannot depend on a custom property outside --ui-v2-*: ${customProperty}`,
            nodeLocation(declaration),
          ),
        )
      }

      if (!isCanonicalV2TokenDefinition) {
        RAW_COLOR.lastIndex = 0
        if (RAW_COLOR.test(declaration.value) || containsRawNamedColor(declaration.value)) {
          findings.push(
            violation(
              'raw-v2-color',
              styleFile.path,
              `Raw colors are only allowed in the canonical V2 token source: ${declaration.value}`,
              nodeLocation(declaration),
            ),
          )
        }
        if (
          !declaration.prop.startsWith('--') &&
          hasHardcodedDesignValue(declaration.prop, declaration.value) &&
          !isApprovedAuthFlowViewportFallback(declaration, styleFile.path)
        ) {
          findings.push(
            violation(
              'hardcoded-v2-design-length',
              styleFile.path,
              `Use a --ui-v2-* token for ${declaration.prop}: ${declaration.value}`,
              nodeLocation(declaration),
            ),
          )
        }
        if (
          /^(?:animation|animation-delay|animation-duration|transition|transition-delay|transition-duration)$/i.test(
            declaration.prop,
          ) &&
          hasHardcodedMotionValue(declaration.value)
        ) {
          findings.push(
            violation(
              'hardcoded-v2-motion-duration',
              styleFile.path,
              `Use a --ui-v2-motion-* token for ${declaration.prop}: ${declaration.value}`,
              nodeLocation(declaration),
            ),
          )
        }
        if (
          /^font$/i.test(declaration.prop) &&
          declaration.value.trim().toLowerCase() !== 'inherit'
        ) {
          findings.push(
            violation(
              'hardcoded-v2-typography',
              styleFile.path,
              `Use individual approved V2 typography tokens instead of font shorthand: ${declaration.value}`,
              nodeLocation(declaration),
            ),
          )
        }
        if (
          /^font-family$/i.test(declaration.prop) &&
          declaration.value.trim().toLowerCase() !== 'inherit' &&
          !/^var\(\s*--ui-v2-font-family\s*\)$/.test(declaration.value.trim())
        ) {
          findings.push(
            violation(
              'hardcoded-v2-typography',
              styleFile.path,
              `Use the approved Vazirmatn family token: ${declaration.value}`,
              nodeLocation(declaration),
            ),
          )
        }
        if (
          /^font-weight$/i.test(declaration.prop) &&
          declaration.value.trim().toLowerCase() !== 'inherit' &&
          !/^var\(\s*--ui-v2-font-weight-(?:regular|medium|semibold|bold)\s*\)$/.test(
            declaration.value,
          )
        ) {
          findings.push(
            violation(
              'hardcoded-v2-typography',
              styleFile.path,
              `Use an approved V2 font-weight token: ${declaration.value}`,
              nodeLocation(declaration),
            ),
          )
        }
      }
    })
  }

  for (const cycle of tokenAliasCycles(aliasGraph)) {
    const firstToken = cycle.split(' -> ')[0]
    const firstLocation = definitions.get(firstToken)?.[0]
    findings.push(
      violation(
        'v2-token-alias-cycle',
        firstLocation?.file ?? 'src/styles',
        `V2 token aliases form a cycle: ${cycle}`,
        firstLocation?.location,
      ),
    )
  }

  for (const [token, locations] of definitions) {
    if (RESPONSIVE_OVERRIDE_TOKENS.has(token)) {
      const contexts = locations.map((location) => location.context).sort()
      const expectedContexts = ['@media (prefers-reduced-motion: reduce)', 'base']
      if (JSON.stringify(contexts) !== JSON.stringify(expectedContexts)) {
        findings.push(
          violation(
            'invalid-v2-token-override',
            locations.at(-1)?.file ?? 'src/styles',
            `${token} may be defined only once in base and once in the reduced-motion query`,
            locations.at(-1)?.location,
          ),
        )
      }
      continue
    }
    if (locations.length > 1) {
      findings.push(
        violation(
          'duplicate-v2-token',
          locations[1].file,
          `${token} is immutable and defined ${locations.length} times across V2 CSS`,
          locations[1].location,
        ),
      )
      continue
    }
    const byContext = new Map()
    for (const tokenLocation of locations) {
      const contextLocations = byContext.get(tokenLocation.context) ?? []
      contextLocations.push(tokenLocation)
      byContext.set(tokenLocation.context, contextLocations)
    }
    for (const [context, contextLocations] of byContext) {
      if (contextLocations.length < 2) continue
      findings.push(
        violation(
          'duplicate-v2-token',
          contextLocations[1].file,
          `${token} is defined ${contextLocations.length} times in ${context}`,
          contextLocations[1].location,
        ),
      )
    }
  }

  for (const styleFile of parsedFiles) {
    styleFile.root.walkDecls((declaration) => {
      V2_TOKEN_USAGE.lastIndex = 0
      for (const match of declaration.value.matchAll(V2_TOKEN_USAGE)) {
        if (definitions.has(match[1])) continue
        findings.push(
          violation(
            'undefined-v2-token',
            styleFile.path,
            `${match[1]} is used but never defined in V2 CSS`,
            nodeLocation(declaration),
          ),
        )
      }
    })
  }

  if (enforceFrozenTokenContract) {
    if (
      frozenTokenContractSourceSha256 !== FROZEN_TOKEN_CONTRACT_FILE_SHA256 ||
      FROZEN_TOKEN_CONTRACT.schemaVersion !== 1 ||
      FROZEN_TOKEN_CONTRACT.stage !== 2
    ) {
      findings.push(
        violation(
          'invalid-frozen-v2-token-contract',
          'src/design-system-v2/canonical-token-contract.json',
          'The machine-readable Stage 2 token contract hash or schema has changed',
        ),
      )
    }
    const tokenSources = parsedFiles.filter(({ path }) => TOKEN_SOURCE_PATH.test(path))
    if (tokenSources.length !== 1) {
      findings.push(
        violation(
          'frozen-v2-token-contract-drift',
          'src/styles/design-system-v2.tokens.css',
          `Expected exactly one canonical V2 token source, found ${tokenSources.length}`,
        ),
      )
    } else {
      const tokenSource = tokenSources[0]
      const canonicalMatch = tokenSource.source.match(
        /\/\* canonical-figma-variables:start \*\/([\s\S]*?)\/\* canonical-figma-variables:end \*\//,
      )
      let canonicalEntries = []
      if (canonicalMatch?.[1]) {
        try {
          canonicalEntries = tokenDefinitionEntries(postcss.parse(canonicalMatch[1]))
        } catch {
          canonicalEntries = []
        }
      }
      const expectedCanonical = Object.entries(FROZEN_TOKEN_CONTRACT.canonicalTokens).map(
        ([name, value]) => ({ name, value, context: 'base' }),
      )
      const expectedDefinitions = [
        ...expectedCanonical,
        ...FROZEN_TOKEN_CONTRACT.implementationDefinitions,
      ]
      const actualDefinitions = tokenDefinitionEntries(tokenSource.root)
      const canonicalMatches =
        canonicalEntries.length === FROZEN_TOKEN_CONTRACT.canonicalTokenCount &&
        JSON.stringify(canonicalEntries.map(tokenDefinitionKey).sort()) ===
          JSON.stringify(expectedCanonical.map(tokenDefinitionKey).sort())
      const definitionsMatch =
        actualDefinitions.length === FROZEN_TOKEN_CONTRACT.definitionCount &&
        JSON.stringify(actualDefinitions.map(tokenDefinitionKey).sort()) ===
          JSON.stringify(expectedDefinitions.map(tokenDefinitionKey).sort())
      if (!canonicalMatches || !definitionsMatch) {
        const expectedKeys = new Set(expectedDefinitions.map(tokenDefinitionKey))
        const actualKeys = new Set(actualDefinitions.map(tokenDefinitionKey))
        const missing = [...expectedKeys].filter((key) => !actualKeys.has(key)).length
        const extra = [...actualKeys].filter((key) => !expectedKeys.has(key)).length
        findings.push(
          violation(
            'frozen-v2-token-contract-drift',
            tokenSource.path,
            `Frozen token contract differs (canonical ${canonicalEntries.length}/${FROZEN_TOKEN_CONTRACT.canonicalTokenCount}; definitions ${actualDefinitions.length}/${FROZEN_TOKEN_CONTRACT.definitionCount}; missing ${missing}; extra ${extra})`,
          ),
        )
      }
    }
  }

  return findings.sort(compareFindings)
}

export function parseRouterRoutes(routerSource) {
  const sourceFile = ts.createSourceFile(
    'src/router/index.ts',
    routerSource,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TS,
  )
  const routerCalls = []

  function visit(node) {
    if (
      ts.isCallExpression(node) &&
      ts.isIdentifier(node.expression) &&
      node.expression.text === 'createRouter'
    ) {
      routerCalls.push(node)
    }
    ts.forEachChild(node, visit)
  }
  visit(sourceFile)
  if (routerCalls.length !== 1) return []

  const config = routerCalls[0].arguments[0]
  if (!config || !ts.isObjectLiteralExpression(config)) return []
  if (
    config.properties.some(
      (property) =>
        ts.isSpreadAssignment(property) ||
        ('name' in property && property.name && ts.isComputedPropertyName(property.name)),
    )
  ) {
    return [{ path: '__dynamic_route_config__', name: '__dynamic_route_config__' }]
  }
  if (
    config.properties.filter(
      (property) => ts.isPropertyAssignment(property) && propertyName(property.name) === 'routes',
    ).length !== 1
  ) {
    return [{ path: '__dynamic_route_config__', name: '__dynamic_route_config__' }]
  }
  const routesProperty = config.properties.find(
    (property) => ts.isPropertyAssignment(property) && propertyName(property.name) === 'routes',
  )
  if (!routesProperty || !ts.isPropertyAssignment(routesProperty)) return []
  if (!ts.isArrayLiteralExpression(routesProperty.initializer)) return []

  return parseRouteArray(routesProperty.initializer)
}

function parseRouteArray(arrayLiteral, parentPath = '') {
  return arrayLiteral.elements.flatMap((element) => {
    if (!ts.isObjectLiteralExpression(element)) {
      return [{ path: '__dynamic_route__', name: '__dynamic_route__' }]
    }
    if (
      element.properties.some(
        (property) =>
          ts.isSpreadAssignment(property) ||
          ('name' in property && property.name && ts.isComputedPropertyName(property.name)),
      )
    ) {
      return [{ path: '__dynamic_route__', name: '__dynamic_route__' }]
    }
    for (const key of ['path', 'name', 'children']) {
      if (
        element.properties.filter(
          (property) => ts.isPropertyAssignment(property) && propertyName(property.name) === key,
        ).length > 1
      ) {
        return [{ path: '__dynamic_route__', name: '__dynamic_route__' }]
      }
    }
    const path = literalPropertyValue(element, 'path')
    const name = literalPropertyValue(element, 'name')
    const resolvedPath =
      path === null
        ? null
        : path.startsWith('/') || parentPath === ''
          ? path
          : `${parentPath.replace(/\/$/, '')}/${path.replace(/^\//, '')}`
    const current =
      resolvedPath !== null && name !== null
        ? [{ path: resolvedPath, name }]
        : [{ path: '__dynamic_route__', name: '__dynamic_route__' }]
    const childrenProperty = element.properties.find(
      (candidate) =>
        ts.isPropertyAssignment(candidate) && propertyName(candidate.name) === 'children',
    )
    if (!childrenProperty || !ts.isPropertyAssignment(childrenProperty)) return current
    if (!ts.isArrayLiteralExpression(childrenProperty.initializer)) {
      return [...current, { path: '__dynamic_child_route__', name: '__dynamic_child_route__' }]
    }
    return [
      ...current,
      ...parseRouteArray(childrenProperty.initializer, resolvedPath ?? parentPath),
    ]
  })
}

function propertyName(name) {
  if (ts.isIdentifier(name) || ts.isStringLiteralLike(name)) return name.text
  return null
}

function literalPropertyValue(objectLiteral, key) {
  const property = objectLiteral.properties.find(
    (candidate) => ts.isPropertyAssignment(candidate) && propertyName(candidate.name) === key,
  )
  if (!property || !ts.isPropertyAssignment(property)) return null
  return ts.isStringLiteralLike(property.initializer) ? property.initializer.text : null
}

export function isProductActivationSourcePath(repoPath) {
  const normalized = repoPath.split('\\').join('/')
  if (normalized === 'index.html') return true
  if (!/^src\/.*\.(?:vue|[cm]?[jt]sx?)$/.test(normalized)) return false
  if (/(?:^|\/)[^/]+\.(?:test|spec)\.(?:vue|[cm]?[jt]sx?)$/.test(normalized)) return false
  return !new Set([
    'src/components/ui/AppDesignSystemScope.vue',
    'src/components/ui/AppDesignSystemCatalog.vue',
    'src/components/ui/uiDesignSystemScope.ts',
    'src/components/ui/index.ts',
    'src/router/uiRouteContract.ts',
  ]).has(normalized)
}

function duplicateValues(items, key) {
  const counts = new Map()
  for (const item of items) {
    const value = item?.[key]
    counts.set(value, (counts.get(value) ?? 0) + 1)
  }
  return [...counts.entries()].filter(([, count]) => count > 1).map(([value]) => value)
}

function sorted(values) {
  return [...values].sort((left, right) => String(left).localeCompare(String(right)))
}

function sameValues(left, right) {
  return JSON.stringify(sorted(left)) === JSON.stringify(sorted(right))
}

const ROUTE_REGISTRY_MUTATION_METHODS = new Set(['addRoute', 'clearRoutes', 'removeRoute'])

function unwrapExpression(expression) {
  let current = expression
  while (
    ts.isParenthesizedExpression(current) ||
    ts.isAsExpression(current) ||
    ts.isNonNullExpression(current) ||
    ts.isSatisfiesExpression(current) ||
    ts.isTypeAssertionExpression(current)
  ) {
    current = current.expression
  }
  return current
}

function staticStringValue(expression) {
  const current = unwrapExpression(expression)
  if (ts.isStringLiteralLike(current)) return current.text
  if (ts.isNumericLiteral(current)) return current.text
  if (ts.isTemplateExpression(current)) {
    let value = current.head.text
    for (const span of current.templateSpans) {
      const expressionValue = staticStringValue(span.expression)
      if (expressionValue === null) return null
      value += expressionValue + span.literal.text
    }
    return value
  }
  if (ts.isBinaryExpression(current) && current.operatorToken.kind === ts.SyntaxKind.PlusToken) {
    const left = staticStringValue(current.left)
    const right = staticStringValue(current.right)
    return left === null || right === null ? null : `${left}${right}`
  }
  return null
}

function sourceScriptBodies(sourcePath, source) {
  if (!sourcePath.endsWith('.vue')) return [source]
  return [...source.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].map((match) => match[1])
}

function sourceContainsRouteRegistryMutation(sourcePath, source) {
  const scriptSources = sourceScriptBodies(sourcePath, source)

  return scriptSources.some((scriptSource) => {
    const sourceFile = ts.createSourceFile(
      sourcePath,
      scriptSource,
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TSX,
    )
    let found = false
    function visit(node) {
      if (found) return
      if (ts.isCallExpression(node)) {
        const callee = unwrapExpression(node.expression)
        let method = null
        if (ts.isPropertyAccessExpression(callee)) {
          method = callee.name.text
        } else if (ts.isElementAccessExpression(callee) && callee.argumentExpression) {
          method = staticStringValue(callee.argumentExpression)
          const target = unwrapExpression(callee.expression)
          if (method === null && ts.isIdentifier(target) && /router/i.test(target.text)) {
            found = true
            return
          }
        }
        if (method !== null && ROUTE_REGISTRY_MUTATION_METHODS.has(method)) {
          found = true
          return
        }
      }
      ts.forEachChild(node, visit)
    }
    visit(sourceFile)
    return found
  })
}

function datasetUiSystemAssignment(left, right) {
  const value = staticStringValue(right)
  if (value !== 'v2' && value !== 'v2-portal') return false
  const target = unwrapExpression(left)
  if (ts.isPropertyAccessExpression(target) && target.name.text === 'uiSystem') {
    const dataset = unwrapExpression(target.expression)
    return ts.isPropertyAccessExpression(dataset) && dataset.name.text === 'dataset'
  }
  if (ts.isElementAccessExpression(target) && target.argumentExpression) {
    const key = staticStringValue(target.argumentExpression)
    const dataset = unwrapExpression(target.expression)
    return (
      key === 'uiSystem' &&
      ts.isPropertyAccessExpression(dataset) &&
      dataset.name.text === 'dataset'
    )
  }
  return false
}

function isDatasetExpression(expression) {
  const target = unwrapExpression(expression)
  if (ts.isPropertyAccessExpression(target)) return target.name.text === 'dataset'
  return (
    ts.isElementAccessExpression(target) &&
    !!target.argumentExpression &&
    staticStringValue(target.argumentExpression) === 'dataset'
  )
}

function memberMethodName(expression) {
  const target = unwrapExpression(expression)
  if (ts.isPropertyAccessExpression(target)) return target.name.text
  if (ts.isElementAccessExpression(target) && target.argumentExpression) {
    return staticStringValue(target.argumentExpression)
  }
  if (ts.isIdentifier(target)) return target.text
  return null
}

function objectPropertyStaticName(property) {
  if (!('name' in property) || !property.name) return null
  if (ts.isComputedPropertyName(property.name)) return staticStringValue(property.name.expression)
  return propertyName(property.name)
}

function sourceActivationEvidence(sourcePath, source) {
  const activationPattern =
    /\bdata-ui-system\b|\bdataset\s*(?:\.\s*uiSystem|\[\s*['"]uiSystem['"]\s*\])|\bAppDesignSystem(?:Scope|Catalog)\b|\b(?:attach|get)UiDesignSystem(?:Portal)?Scope(?:Attributes)?\b|\bUI_DESIGN_SYSTEM_(?:PORTAL_)?SCOPE_(?:ATTRIBUTE|VALUE)\b|\bui-v2-scope\b|\bv2Scope\s*:\s*['"](?:section|route)['"]/i
  const direct = activationPattern.exec(source)
  if (direct) return direct[0]

  for (const scriptSource of sourceScriptBodies(sourcePath, source)) {
    const sourceFile = ts.createSourceFile(
      sourcePath,
      scriptSource,
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TSX,
    )
    let evidence = null
    function visit(node) {
      if (evidence !== null) return
      if (ts.isCallExpression(node)) {
        const callee = unwrapExpression(node.expression)
        const method = memberMethodName(callee)
        if (
          method === 'setAttribute' &&
          node.arguments.length >= 2 &&
          staticStringValue(node.arguments[0])?.toLowerCase() === 'data-ui-system' &&
          ['v2', 'v2-portal'].includes(staticStringValue(node.arguments[1]))
        ) {
          evidence = 'setAttribute(data-ui-system)'
          return
        }
        if (
          method === 'setAttributeNS' &&
          node.arguments.length >= 3 &&
          staticStringValue(node.arguments[1])?.toLowerCase() === 'data-ui-system' &&
          ['v2', 'v2-portal'].includes(staticStringValue(node.arguments[2]))
        ) {
          evidence = 'setAttributeNS(data-ui-system)'
          return
        }
        if (callee.kind === ts.SyntaxKind.ImportKeyword && node.arguments.length === 1) {
          const specifier = staticStringValue(node.arguments[0])
          if (
            specifier &&
            /(?:AppDesignSystem(?:Scope|Catalog)\.vue|uiDesignSystemScope(?:\.[cm]?[jt]s)?)/i.test(
              specifier,
            )
          ) {
            evidence = `import(${specifier})`
            return
          }
        }
        if (/^(?:attach|get)UiDesignSystem(?:Portal)?Scope(?:Attributes)?$/.test(method ?? '')) {
          evidence = `helper call ${method}`
          return
        }
        if (
          method === 'resolveComponent' &&
          node.arguments.length >= 1 &&
          staticStringValue(node.arguments[0]) === 'AppDesignSystemCatalog'
        ) {
          evidence = 'resolveComponent(AppDesignSystemCatalog)'
          return
        }
        if (
          ts.isPropertyAccessExpression(callee) &&
          ts.isIdentifier(callee.expression) &&
          callee.expression.text === 'Reflect' &&
          method === 'set' &&
          node.arguments.length >= 3 &&
          isDatasetExpression(node.arguments[0]) &&
          staticStringValue(node.arguments[1]) === 'uiSystem' &&
          ['v2', 'v2-portal'].includes(staticStringValue(node.arguments[2]))
        ) {
          evidence = 'Reflect.set(dataset.uiSystem)'
          return
        }
        if (
          ts.isPropertyAccessExpression(callee) &&
          ts.isIdentifier(callee.expression) &&
          callee.expression.text === 'Object' &&
          method === 'assign' &&
          node.arguments.length >= 2 &&
          isDatasetExpression(node.arguments[0]) &&
          ts.isObjectLiteralExpression(unwrapExpression(node.arguments[1]))
        ) {
          const objectLiteral = unwrapExpression(node.arguments[1])
          const activates = objectLiteral.properties.some(
            (property) =>
              ts.isPropertyAssignment(property) &&
              objectPropertyStaticName(property) === 'uiSystem' &&
              ['v2', 'v2-portal'].includes(staticStringValue(property.initializer)),
          )
          if (activates) {
            evidence = 'Object.assign(dataset.uiSystem)'
            return
          }
        }
      }
      if (
        ts.isBinaryExpression(node) &&
        [
          ts.SyntaxKind.EqualsToken,
          ts.SyntaxKind.BarBarEqualsToken,
          ts.SyntaxKind.AmpersandAmpersandEqualsToken,
          ts.SyntaxKind.QuestionQuestionEqualsToken,
        ].includes(node.operatorToken.kind) &&
        datasetUiSystemAssignment(node.left, node.right)
      ) {
        evidence = 'dataset.uiSystem assignment'
        return
      }
      ts.forEachChild(node, visit)
    }
    visit(sourceFile)
    if (evidence !== null) return evidence
  }
  return null
}

function isApprovedStage4WorkspaceBoundary(sourcePath, source) {
  const hasOptInContract =
    /\bv2Scope\??\s*:\s*boolean\b/.test(source) &&
    /\bv2Scope\s*:\s*false\b/.test(source) &&
    /\bprops\.v2Scope\b/.test(source)
  if (!hasOptInContract) return false

  if (sourcePath === 'src/components/workspace/WorkspaceShell.vue') {
    if (
      !/\bprops\.v2Scope\s*\?\s*AppDesignSystemScope\s*:\s*['"]section['"]/.test(source) ||
      !/:is\s*=\s*["']workspaceRoot["']/.test(source)
    ) {
      return false
    }
    const withoutApprovedScope = source.replace(/\bAppDesignSystemScope\b/g, '')
    return sourceActivationEvidence(sourcePath, withoutApprovedScope) === null
  }

  if (!STAGE4_WORKSPACE_HELPER_BOUNDARY_PATHS.has(sourcePath)) return false
  const helperCalls = [...source.matchAll(/\bgetUiDesignSystemScopeAttributes\s*\(\s*\)/g)]
  if (
    helperCalls.length !== 1 ||
    !/\bprops\.v2Scope\s*\?\s*getUiDesignSystemScopeAttributes\s*\(\s*\)\s*:\s*\{\s*\}/.test(
      source,
    ) ||
    !/\bv-bind\s*=\s*["']scopeAttributes["']/.test(source) ||
    !/["']ui-v2-scope["']\s*:\s*v2Scope\b/.test(source)
  ) {
    return false
  }
  const withoutApprovedHelper = source
    .replace(/\bgetUiDesignSystemScopeAttributes\b/g, '')
    .replace(/["']ui-v2-scope["']\s*:\s*v2Scope\b/g, '')
  return sourceActivationEvidence(sourcePath, withoutApprovedHelper) === null
}

function isApprovedStage4ActivationBoundary({ path: sourcePath, source }) {
  if (!STAGE4_ACTIVATION_BOUNDARY_PATHS.has(sourcePath)) return false

  if (sourcePath === 'src/components/workspace/WorkspaceAccountDeletionDialog.vue') {
    const scopeBindings = [...source.matchAll(/:data-ui-system\s*=\s*["']portalScopeValue["']/g)]
    const portalRoot =
      /<Teleport\b(?=[^>]*\bto\s*=\s*["']body["'])[^>]*>[\s\S]*?<div\b(?=[^>]*\bv-if\s*=\s*["']open["'])(?=[^>]*:data-ui-system\s*=\s*["']portalScopeValue["'])/.test(
        source,
      )
    const hasRawScope = /\bdata-ui-system\s*=\s*["']v2(?:-portal)?["']/.test(source)
    if (
      scopeBindings.length !== 1 ||
      !portalRoot ||
      hasRawScope ||
      !/\bportalScopeValue\s*=\s*computed\s*\(\s*\(\s*\)\s*=>\s*UI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE\s*\)/.test(
        source,
      ) ||
      !/\buseOverlayA11y\b/.test(source) ||
      !/\bcontainerRef\b/.test(source) ||
      !/\bUI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE\b/.test(source) ||
      !/from\s+["'][^"']*uiDesignSystemScope["']/.test(source)
    ) {
      return false
    }

    const withoutApprovedPortalBinding = source
      .replace(/:data-ui-system\s*=\s*["']portalScopeValue["']/g, '')
      .replace(/\bUI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE\b/g, '')
    return sourceActivationEvidence(sourcePath, withoutApprovedPortalBinding) === null
  }

  if (sourcePath.startsWith('src/components/workspace/')) {
    return isApprovedStage4WorkspaceBoundary(sourcePath, source)
  }

  if (sourcePath === 'src/components/SessionApprovalModal.vue') {
    const scopeBindings = [...source.matchAll(/:data-ui-system\s*=\s*["']portalScopeValue["']/g)]
    const modalRoot =
      /<div\b(?=[^>]*\bv-if\s*=\s*["']showModal["'])(?=[^>]*:data-ui-system\s*=\s*["']portalScopeValue["'])[^>]*>/.test(
        source,
      )
    const hasRawScope = /\bdata-ui-system\s*=\s*["']v2(?:-portal)?["']/.test(source)
    if (
      scopeBindings.length !== 1 ||
      !modalRoot ||
      hasRawScope ||
      !/\bv2Portal\??\s*:\s*boolean\b/.test(source) ||
      !/\bv2Portal\s*:\s*false\b/.test(source) ||
      !/\bportalScopeValue\b/.test(source) ||
      !/\bprops\.v2Portal\b/.test(source) ||
      !/\bUI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE\b/.test(source) ||
      !/from\s+["'][^"']*uiDesignSystemScope["']/.test(source)
    ) {
      return false
    }

    const withoutApprovedPortalBinding = source
      .replace(/:data-ui-system\s*=\s*["']portalScopeValue["']/g, '')
      .replace(/\bUI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE\b/g, '')
    return sourceActivationEvidence(sourcePath, withoutApprovedPortalBinding) === null
  }

  if (!/\bAppDesignSystemScope\b/.test(source)) return false

  if (sourcePath === 'src/App.vue') {
    const openingTags = [...source.matchAll(/<AppDesignSystemScope\b[^>]*>/g)]
    const scopedBlocks = [
      ...source.matchAll(/<AppDesignSystemScope\b([^>]*)>([\s\S]*?)<\/AppDesignSystemScope>/g),
    ]
    const usesContractResolver =
      /\broute\.meta\.uiShellClass\b/.test(source) &&
      /\broute\.meta\.uiV2Scope\b/.test(source) &&
      /\bUI_ROUTE_SHELL\b/.test(source)
    if (
      !usesContractResolver ||
      !/\bUI_V2_SCOPE\b/.test(source) ||
      openingTags.length === 0 ||
      openingTags.length !== scopedBlocks.length ||
      scopedBlocks.some(
        (match) => !/\bv-(?:else-)?if\s*=/.test(match[1]) || /<RouterView\b/.test(match[2]),
      )
    ) {
      return false
    }
  } else if (sourcePath === 'src/views/DashboardView.vue') {
    const scopedSections = [
      ...source.matchAll(/<AppDesignSystemScope\b([^>]*)>([\s\S]*?)<\/AppDesignSystemScope>/g),
    ]
    const homeSections = scopedSections.filter((section) =>
      /\bclass\s*=\s*["'][^"']*\bui-v2-home-top\b[^"']*["']/.test(section[1]),
    )
    const pwaSections = scopedSections.filter((section) =>
      /\bclass\s*=\s*["'][^"']*\bui-v2-pwa-section\b[^"']*["']/.test(section[1]),
    )
    const homeContent = homeSections[0]?.[2] ?? ''
    const pwaContent = pwaSections[0]?.[2] ?? ''
    if (
      scopedSections.length !== 2 ||
      homeSections.length !== 1 ||
      pwaSections.length !== 1 ||
      !/<header\b[^>]*\bui-v2-home-header\b/.test(homeContent) ||
      !/\bui-v2-home-header__main\b/.test(homeContent) ||
      !/\bui-v2-home-identity\b/.test(homeContent) ||
      !/\bui-v2-home-notifications\b/.test(homeContent) ||
      !/\bui-v2-home-title\b/.test(homeContent) ||
      !/\bui-v2-home-alert\b/.test(homeContent) ||
      /\bhero-btn\b|Market Entry|<MarketHero\b|<PWAInstallOverlay\b/.test(homeContent) ||
      !/^\s*<PWAInstallOverlay\b[^>]*\/>\s*$/.test(pwaContent) ||
      /\bhero-btn\b|Market Entry|<MarketHero\b/.test(pwaContent)
    ) {
      return false
    }
  }

  // Approved boundaries may compose the audited scope component, but may not
  // reproduce the raw attribute/helper protocol or activate catalog/portal scope.
  const withoutScopeComponent = source.replace(/\bAppDesignSystemScope\b/g, '')
  return sourceActivationEvidence(sourcePath, withoutScopeComponent) === null
}

export function checkRoutePolicy({ manifest, routerSource, activationSources = [] }) {
  const findings = []
  const routes = Array.isArray(manifest?.routes) ? manifest.routes : []
  const routerRoutes = parseRouterRoutes(routerSource)

  const routeMutationSources = new Map([
    ['src/router/index.ts', routerSource],
    ...activationSources.map((source) => [source.path, source.source]),
  ])
  for (const [sourcePath, source] of routeMutationSources) {
    if (!sourceContainsRouteRegistryMutation(sourcePath, source)) continue
    findings.push(
      violation(
        'runtime-route-registry-mutation',
        sourcePath,
        'Stage 4 requires the exact static 30-route registry; addRoute/removeRoute/clearRoutes are forbidden',
      ),
    )
  }

  if (manifest?.schemaVersion !== 3 || manifest?.stage !== 4 || manifest?.mode !== 'opt-in') {
    findings.push(
      violation(
        'invalid-scope-manifest',
        'src/design-system-v2/scope-manifest.json',
        'Expected schemaVersion 3, Stage 4, opt-in scope manifest',
      ),
    )
  }
  if (manifest?.scopeSelector !== '[data-ui-system="v2"]' || manifest?.tokenPrefix !== '--ui-v2-') {
    findings.push(
      violation(
        'invalid-scope-contract',
        'src/design-system-v2/scope-manifest.json',
        'Scope selector and V2 token prefix are immutable in Stage 4',
      ),
    )
  }
  if (manifest?.legacyTokenPrefix !== '--ds-') {
    findings.push(
      violation(
        'invalid-legacy-boundary',
        'src/design-system-v2/scope-manifest.json',
        'The legacy boundary must remain explicit as --ds-',
      ),
    )
  }
  if (manifest?.productionCatalogRoute !== null) {
    findings.push(
      violation(
        'production-catalog-route',
        'src/design-system-v2/scope-manifest.json',
        'The component catalog must not have a production route',
      ),
    )
  }
  if (routes.length !== 30) {
    findings.push(
      violation(
        'route-contract-count',
        'src/design-system-v2/scope-manifest.json',
        `Expected exactly 30 product routes, found ${routes.length}`,
      ),
    )
  }

  for (const key of ['path', 'name', 'testId']) {
    for (const value of duplicateValues(routes, key)) {
      findings.push(
        violation(
          'duplicate-route-contract-value',
          'src/design-system-v2/scope-manifest.json',
          `Duplicate ${key}: ${String(value)}`,
        ),
      )
    }
  }

  for (const route of routes) {
    if (
      typeof route.path !== 'string' ||
      typeof route.name !== 'string' ||
      typeof route.testId !== 'string' ||
      !/^route-[a-z0-9-]+$/.test(route.testId) ||
      !REQUIRED_STAGE4_SHELL_ROUTES.has(route.shellClass) ||
      !REQUIRED_STAGE4_SCOPE_ROUTES.has(route.v2Scope) ||
      !['none', 'full', 'mixed'].includes(route.protection) ||
      !Array.isArray(route.protectedInteriors)
    ) {
      findings.push(
        violation(
          'invalid-route-contract-entry',
          'src/design-system-v2/scope-manifest.json',
          `Route contract fields are missing or invalid for ${String(route.path)}`,
        ),
      )
    }
  }

  const manifestPairs = routes.map((route) => `${route.path}\u0000${route.name}`)
  const routerPairs = routerRoutes.map((route) => `${route.path}\u0000${route.name}`)
  if (!sameValues(manifestPairs, routerPairs)) {
    findings.push(
      violation(
        'route-registry-drift',
        'src/router/index.ts',
        'Router path/name pairs differ from the Stage 4 scope manifest',
      ),
    )
  }

  if (
    routes.at(-1)?.path !== STAGE4_CATCH_ALL_PATH ||
    routes.at(-1)?.name !== 'system-recovery' ||
    routerRoutes.at(-1)?.path !== STAGE4_CATCH_ALL_PATH ||
    routerRoutes.at(-1)?.name !== 'system-recovery'
  ) {
    findings.push(
      violation(
        'recovery-catch-all-order',
        'src/router/index.ts',
        'The eager system-recovery catch-all must be the final manifest and router record',
      ),
    )
  }

  for (const route of routerRoutes) {
    if (CATALOG_ROUTE.test(route.path) || CATALOG_ROUTE.test(route.name)) {
      findings.push(
        violation(
          'production-catalog-route',
          'src/router/index.ts',
          `Production router exposes catalog route ${route.path}`,
        ),
      )
    }
  }

  const fullProtectedPaths = routes
    .filter((route) => route.protection === 'full')
    .map((route) => route.path)
  if (!sameValues(fullProtectedPaths, REQUIRED_FULL_PROTECTED_ROUTES)) {
    findings.push(
      violation(
        'protected-route-contract-drift',
        'src/design-system-v2/scope-manifest.json',
        'Full protection must remain on /market, /chat, /admin/channels, and /share-receive',
      ),
    )
  }

  const mixedPaths = routes
    .filter((route) => route.protection === 'mixed')
    .map((route) => route.path)
  if (!sameValues(mixedPaths, REQUIRED_MIXED_INTERIORS.keys())) {
    findings.push(
      violation(
        'mixed-route-contract-drift',
        'src/design-system-v2/scope-manifest.json',
        'Mixed protection must remain on /, /admin/messages, and /admin/system',
      ),
    )
  }

  for (const route of routes) {
    const expectedInteriors = REQUIRED_MIXED_INTERIORS.get(route.path)
    if (expectedInteriors && !sameValues(route.protectedInteriors ?? [], expectedInteriors)) {
      findings.push(
        violation(
          'protected-interior-contract-drift',
          'src/design-system-v2/scope-manifest.json',
          `Protected interiors changed for ${route.path}`,
        ),
      )
    }
    if (route.protection !== 'mixed' && (route.protectedInteriors?.length ?? 0) > 0) {
      findings.push(
        violation(
          'protected-interior-contract-drift',
          'src/design-system-v2/scope-manifest.json',
          `Only mixed routes may declare protected interiors: ${route.path}`,
        ),
      )
    }
    if (route.protection === 'full' && route.v2Scope !== 'off') {
      findings.push(
        violation(
          'protected-route-activation',
          'src/design-system-v2/scope-manifest.json',
          `V2 cannot activate on fully protected route ${route.path}`,
        ),
      )
    }
    if (route.protection === 'mixed' && route.v2Scope === 'route') {
      findings.push(
        violation(
          'mixed-route-whole-scope',
          'src/design-system-v2/scope-manifest.json',
          `Mixed route ${route.path} cannot receive whole-route V2 scope`,
        ),
      )
    }
  }

  for (const [scope, expectedPaths] of REQUIRED_STAGE4_SCOPE_ROUTES) {
    const actualPaths = routes.filter((route) => route.v2Scope === scope).map((route) => route.path)
    if (sameValues(actualPaths, expectedPaths)) continue
    findings.push(
      violation(
        'stage4-v2-scope-contract-drift',
        'src/design-system-v2/scope-manifest.json',
        `Stage 4 ${scope} scope routes changed; expected ${expectedPaths.length}, found ${actualPaths.length}`,
      ),
    )
  }

  for (const [shellClass, expectedPaths] of REQUIRED_STAGE4_SHELL_ROUTES) {
    const actualPaths = routes
      .filter((route) => route.shellClass === shellClass)
      .map((route) => route.path)
    if (sameValues(actualPaths, expectedPaths)) continue
    findings.push(
      violation(
        'stage4-shell-contract-drift',
        'src/design-system-v2/scope-manifest.json',
        `Stage 4 ${shellClass} shell routes changed; expected ${expectedPaths.length}, found ${actualPaths.length}`,
      ),
    )
  }

  for (const activationSource of activationSources) {
    const evidence = sourceActivationEvidence(activationSource.path, activationSource.source)
    if (!evidence) continue
    if (isApprovedStage4ActivationBoundary(activationSource)) continue
    findings.push(
      violation(
        'stage4-product-source-activation',
        activationSource.path,
        `Product source activates V2 outside an approved Stage 4 boundary with ${evidence}`,
      ),
    )
  }

  return findings.sort(compareFindings)
}

export function compareFindings(left, right) {
  return (
    left.file.localeCompare(right.file) ||
    left.code.localeCompare(right.code) ||
    (left.location ?? '').localeCompare(right.location ?? '') ||
    left.detail.localeCompare(right.detail)
  )
}

export const designSystemV2GuardConstants = Object.freeze({
  scopeSelector: '[data-ui-system="v2"]',
  tokenPattern: V2_TOKEN.source,
  fullProtectedRoutes: [...REQUIRED_FULL_PROTECTED_ROUTES],
  mixedProtectedRoutes: [...REQUIRED_MIXED_INTERIORS.keys()],
})
