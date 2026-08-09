import crypto from 'node:crypto'

export const STAGE3_COMPARISON_BASE = '3822df67a48e7ee3197bc6d67c79aa7ee84a7905'
export const DASHBOARD_MARKET_REGION_PATH = 'frontend/src/views/DashboardView.vue'
export const DASHBOARD_MARKET_REGION_CONTRACT = 'stage3-dashboard-market-region-v1'
export const DASHBOARD_MARKET_REGION_SHA256 =
  'f25c01dac38db208517047ffc0f2458e2c89868e988a6d7f68749221db106860'

const SECTION_IDS = [
  'market-computed',
  'open-market',
  'template-hero',
  'hero-disabled-css',
  'hero-focus-css',
  'hero-css',
]

function sha256(value) {
  return crypto.createHash('sha256').update(value).digest('hex')
}

function uniqueIndex(source, marker, label) {
  const first = source.indexOf(marker)
  const second = first === -1 ? -1 : source.indexOf(marker, first + marker.length)
  if (first === -1 || second !== -1) {
    throw new Error(`${label}: expected exactly one anchor`)
  }
  return first
}

function anchoredSlice(source, startMarker, endMarker, label) {
  const start = uniqueIndex(source, startMarker, `${label}:start`)
  const end = uniqueIndex(source, endMarker, `${label}:end`)
  if (end <= start) throw new Error(`${label}: anchors are out of order`)
  return source.slice(start, end)
}

function balancedCssBlock(source, lead, label) {
  const start = uniqueIndex(source, lead, `${label}:start`)
  const open = source.indexOf('{', start)
  if (open === -1) throw new Error(`${label}: opening brace is missing`)

  let depth = 0
  let quote = null
  let escaped = false
  for (let index = open; index < source.length; index += 1) {
    const character = source[index]
    if (quote) {
      if (escaped) escaped = false
      else if (character === '\\') escaped = true
      else if (character === quote) quote = null
      continue
    }
    if (character === '"' || character === "'") {
      quote = character
      continue
    }
    if (character === '{') depth += 1
    if (character === '}') {
      depth -= 1
      if (depth === 0) return source.slice(start, index + 1)
      if (depth < 0) break
    }
  }
  throw new Error(`${label}: CSS block is not balanced`)
}

function uniqueRegexMatch(source, expression, label) {
  const matches = [...source.matchAll(expression)]
  if (matches.length !== 1 || typeof matches[0]?.[0] !== 'string') {
    throw new Error(`${label}: expected exactly one match`)
  }
  return matches[0][0]
}

export function extractDashboardMarketSections(source) {
  if (typeof source !== 'string') throw new TypeError('Dashboard source must be UTF-8 text')

  const sections = new Map([
    [
      'market-computed',
      anchoredSlice(
        source,
        'const isMarketOpen = computed(() => marketRuntime.value.is_open)',
        '\n\nconst isGloballyLockedAccount = computed',
        'market-computed',
      ),
    ],
    [
      'open-market',
      anchoredSlice(
        source,
        'function openMarket() {',
        '\n\nasync function connectTelegram() {',
        'open-market',
      ),
    ],
    [
      'template-hero',
      uniqueRegexMatch(
        source,
        /        <!-- Market Entry — Hero Button -->[\s\S]*?\n          <div class="hero-cta-tail">ورود<\/div>\n        <\/button>/g,
        'template-hero',
      ),
    ],
    ['hero-disabled-css', balancedCssBlock(source, '.hero-btn:disabled {', 'hero-disabled-css')],
    [
      'hero-focus-css',
      balancedCssBlock(source, '.user-info-center:focus-visible,', 'hero-focus-css'),
    ],
    ['hero-css', anchoredSlice(source, '/* ═══ Hero Button ═══ */', '\n\n</style>', 'hero-css')],
  ])

  const focusSource = sections.get('hero-focus-css')
  if (!focusSource?.includes('.hero-btn:focus-visible,')) {
    throw new Error('hero-focus-css: protected hero focus selector is missing')
  }
  return sections
}

export function dashboardMarketRegionEvidence(source) {
  const sections = extractDashboardMarketSections(source)
  const chunks = [Buffer.from(`${DASHBOARD_MARKET_REGION_CONTRACT}\0`, 'utf8')]
  const sectionEvidence = []

  for (const id of SECTION_IDS) {
    const section = sections.get(id)
    if (typeof section !== 'string') throw new Error(`${id}: section is missing`)
    const bytes = Buffer.from(section, 'utf8')
    chunks.push(Buffer.from(`${id}\0${bytes.byteLength}\0`, 'utf8'), bytes)
    sectionEvidence.push({ id, bytes: bytes.byteLength, sha256: sha256(bytes) })
  }

  const composite = Buffer.concat(chunks)
  return {
    contract: DASHBOARD_MARKET_REGION_CONTRACT,
    bytes: composite.byteLength,
    sha256: sha256(composite),
    sections: sectionEvidence,
  }
}
