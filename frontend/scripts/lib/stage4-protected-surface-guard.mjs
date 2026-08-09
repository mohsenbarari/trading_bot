import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'

export const STAGE4_BASE_COMMIT = '9dfa961000832c830729ce67e8a54357915c716a'
export const STAGE4_BASE_TREE = '1540c2534d8052a3a8cfcffcdc2f65e4b85fc874'

export const STAGE4_SCOPE_MANIFEST_PATH = 'frontend/src/design-system-v2/scope-manifest.json'
export const STAGE4_ROUTE_CONTRACT_PATH = 'frontend/src/router/uiRouteContract.ts'
export const ADMIN_MESSAGES_PATH = 'frontend/src/components/AdminMessagesView.vue'
export const ADMIN_MESSAGES_SHA256 =
  '5572589b83a8a07776d5b983777a14a91e2104f9577fa76960df5a54562a431a'
export const TRADING_SETTINGS_PATH = 'frontend/src/components/TradingSettings.vue'
export const TRADING_SETTINGS_SHA256 =
  '509dd32235e1cb98aa164940cf7722604f16b6518f7387699554bf3a828ecfaa'

export const MARKET_RUNTIME_CONTRACT = 'stage4-market-owned-runtime-v1'
export const MESSENGER_RUNTIME_CONTRACT = 'stage4-messenger-owned-runtime-v1'

export const MARKET_RUNTIME_BASELINE = Object.freeze({
  count: 19,
  contentBytes: 137246,
  pathSetSha256: '37aa0b51e20f4ae86f7daf6c3c231d93b3d1f288ade1471490a1f843a57c9589',
  sha256: '162e9e618684a24f3db3298eb8ff2c62498b18753cd4e0b6d6b97650d0202058',
})

export const MESSENGER_RUNTIME_BASELINE = Object.freeze({
  count: 85,
  contentBytes: 1312405,
  pathSetSha256: 'f6af1f961e45d785ba9c752ee670643571086c6a946843807fe6f581d11aea58',
  sha256: 'f66debf9809180d97b2bac98f5195ba24200d3b61b0d8e0e5cd423a8a7b97248',
})

const RUNTIME_SOURCE_EXTENSION = /\.(?:css|[cm]?[jt]sx?|vue)$/
const TEST_SOURCE = /(?:^|\/)[^/]+\.(?:spec|test)\.[^/]+$/

const MARKET_EXACT_RUNTIME_PATHS = new Set([
  'frontend/src/components/OfferPreviewModal.vue',
  'frontend/src/components/OffersList.vue',
  'frontend/src/components/TradeLotSuggestionAlert.vue',
  'frontend/src/components/ui/AppSettlementBadge.vue',
  'frontend/src/components/ui/AppTradeActionButton.vue',
  'frontend/src/composables/useMarketRuntime.ts',
  'frontend/src/composables/useOffers.ts',
  'frontend/src/utils/offerDraftText.ts',
  // A direct Market dependency omitted from the Stage 3 glob expansion.
  'frontend/src/utils/settlementType.ts',
  'frontend/src/views/MarketView.vue',
])

export const MESSENGER_OMITTED_DIRECT_RUNTIME_PATHS = Object.freeze([
  'frontend/src/components/AdminBroadcastModal.vue',
  'frontend/src/stores/audio.ts',
  'frontend/src/utils/accountantChatIdentity.ts',
  'frontend/src/utils/audioRecorder.ts',
  'frontend/src/utils/composerOverlayState.ts',
  'frontend/src/utils/conversationListModel.ts',
  'frontend/src/utils/emojiStickerCatalog.ts',
  'frontend/src/utils/imagePreprocessClient.ts',
  'frontend/src/utils/messageContextMenuModel.ts',
  'frontend/src/utils/messageReactions.ts',
  'frontend/src/utils/shareTargetStore.ts',
  'frontend/src/utils/sharedVisibilityObserver.ts',
  'frontend/src/workers/imagePreprocess.worker.ts',
])

const MESSENGER_EXACT_RUNTIME_PATHS = new Set([
  'frontend/src/components/ChatView.vue',
  'frontend/src/components/CreateChannelView.vue',
  'frontend/src/styles/messenger-design-tokens.css',
  'frontend/src/types/chat.ts',
  'frontend/src/views/MessengerView.vue',
  'frontend/src/views/ShareReceiveView.vue',
  ...MESSENGER_OMITTED_DIRECT_RUNTIME_PATHS,
])

export const STAGE4_PROTECTED_ROUTE_CONTRACT = Object.freeze([
  Object.freeze({
    path: '/',
    shellClass: 'standard-authenticated',
    protection: 'mixed',
    protectedInteriors: Object.freeze(['home-market-widget']),
    v2Scope: 'section',
  }),
  Object.freeze({
    path: '/admin/channels',
    shellClass: 'protected-legacy',
    protection: 'full',
    protectedInteriors: Object.freeze([]),
    v2Scope: 'off',
  }),
  Object.freeze({
    path: '/admin/messages',
    shellClass: 'standard-authenticated',
    protection: 'mixed',
    protectedInteriors: Object.freeze([
      'admin-messages-market-delivery',
      'admin-messages-messenger-delivery',
    ]),
    v2Scope: 'section',
  }),
  Object.freeze({
    path: '/admin/system',
    shellClass: 'standard-authenticated',
    protection: 'mixed',
    protectedInteriors: Object.freeze(['trading-settings-market-controls']),
    v2Scope: 'section',
  }),
  Object.freeze({
    path: '/chat',
    shellClass: 'protected-legacy',
    protection: 'full',
    protectedInteriors: Object.freeze([]),
    v2Scope: 'off',
  }),
  Object.freeze({
    path: '/market',
    shellClass: 'protected-legacy',
    protection: 'full',
    protectedInteriors: Object.freeze([]),
    v2Scope: 'off',
  }),
  Object.freeze({
    path: '/share-receive',
    shellClass: 'protected-legacy',
    protection: 'full',
    protectedInteriors: Object.freeze([]),
    v2Scope: 'off',
  }),
])

function sha256(value) {
  return crypto.createHash('sha256').update(value).digest('hex')
}

function posixPath(value) {
  return value.split(path.sep).join('/')
}

function isRuntimeSource(repoPath) {
  return RUNTIME_SOURCE_EXTENSION.test(repoPath) && !TEST_SOURCE.test(repoPath)
}

export function isMarketOwnedRuntimePath(repoPath) {
  const normalized = posixPath(repoPath)
  if (!isRuntimeSource(normalized)) return false
  return (
    MARKET_EXACT_RUNTIME_PATHS.has(normalized) ||
    /^frontend\/src\/components\/ui\/AppOffer[^/]*\.vue$/.test(normalized)
  )
}

export function isMessengerOwnedRuntimePath(repoPath) {
  const normalized = posixPath(repoPath)
  if (!isRuntimeSource(normalized)) return false
  return (
    MESSENGER_EXACT_RUNTIME_PATHS.has(normalized) ||
    normalized.startsWith('frontend/src/components/chat/') ||
    normalized.startsWith('frontend/src/components/messenger-v2/') ||
    normalized.startsWith('frontend/src/composables/chat/') ||
    normalized.startsWith('frontend/src/services/chat/') ||
    /^frontend\/src\/services\/chat[^/]*\.[cm]?[jt]sx?$/.test(normalized) ||
    normalized.startsWith('frontend/src/stores/chat/') ||
    /^frontend\/src\/utils\/(?:chat|messenger)[^/]*\.[cm]?[jt]sx?$/.test(normalized)
  )
}

function walkFiles(directory) {
  if (!fs.existsSync(directory)) return []
  const files = []
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const entryPath = path.join(directory, entry.name)
    if (entry.isDirectory()) files.push(...walkFiles(entryPath))
    else if (entry.isFile()) files.push(entryPath)
  }
  return files
}

export function discoverStage4OwnedRuntimePaths(repoRoot) {
  const sourceRoot = path.join(repoRoot, 'frontend', 'src')
  const allSourcePaths = walkFiles(sourceRoot).map((filePath) =>
    posixPath(path.relative(repoRoot, filePath)),
  )
  return {
    market: allSourcePaths.filter(isMarketOwnedRuntimePath).sort(),
    messenger: allSourcePaths.filter(isMessengerOwnedRuntimePath).sort(),
  }
}

export function readFileEntries(repoRoot, repoPaths) {
  return repoPaths.map((repoPath) => ({
    path: repoPath,
    content: fs.readFileSync(path.join(repoRoot, repoPath)),
  }))
}

export function pathSetSha256(repoPaths) {
  const paths = [...repoPaths].sort()
  if (new Set(paths).size !== paths.length)
    throw new Error('protected path set contains duplicates')
  return sha256(Buffer.from(`${paths.join('\n')}\n`, 'utf8'))
}

export function protectedFileSetEvidence(entries, contract) {
  if (!Array.isArray(entries) || typeof contract !== 'string' || contract.length === 0) {
    throw new TypeError('protected file-set evidence requires entries and a contract')
  }

  const sorted = [...entries].sort((left, right) => left.path.localeCompare(right.path))
  const paths = sorted.map((entry) => entry.path)
  if (new Set(paths).size !== paths.length)
    throw new Error('protected file set contains duplicates')

  let contentBytes = 0
  const chunks = [Buffer.from(`${contract}\0`, 'utf8')]
  for (const entry of sorted) {
    if (typeof entry.path !== 'string' || entry.path.length === 0) {
      throw new TypeError('protected file-set entry path must be a non-empty string')
    }
    const content = Buffer.isBuffer(entry.content)
      ? entry.content
      : Buffer.from(entry.content, 'utf8')
    contentBytes += content.byteLength
    chunks.push(Buffer.from(`${entry.path}\0${content.byteLength}\0`, 'utf8'), content)
  }

  return {
    contract,
    count: sorted.length,
    contentBytes,
    pathSetSha256: pathSetSha256(paths),
    sha256: sha256(Buffer.concat(chunks)),
  }
}

export function assertProtectedFileSetEvidence(label, actual, expected) {
  for (const field of ['count', 'contentBytes', 'pathSetSha256', 'sha256']) {
    if (actual[field] !== expected[field]) {
      throw new Error(`${label} ${field} drift: ${expected[field]} -> ${actual[field]}`)
    }
  }
  return actual
}

export function fileSha256(value) {
  return sha256(value)
}

function sameArray(left, right) {
  return (
    Array.isArray(left) &&
    Array.isArray(right) &&
    left.length === right.length &&
    left.every((value, index) => value === right[index])
  )
}

function routeShape(row) {
  return {
    path: row.path,
    shellClass: row.shellClass,
    protection: row.protection,
    protectedInteriors: row.protectedInteriors,
    v2Scope: row.v2Scope,
  }
}

export function assertStage4RouteProtection(routes) {
  if (!Array.isArray(routes)) throw new TypeError('route protection requires a routes array')

  const paths = routes.map((route) => route?.path)
  if (paths.some((routePath) => typeof routePath !== 'string')) {
    throw new Error('route protection found an invalid path')
  }
  if (new Set(paths).size !== paths.length)
    throw new Error('route protection found duplicate paths')

  const expectedPaths = STAGE4_PROTECTED_ROUTE_CONTRACT.map(({ path: routePath }) => routePath)
  const actualProtectedPaths = routes
    .filter(({ protection }) => protection === 'full' || protection === 'mixed')
    .map(({ path: routePath }) => routePath)
    .sort()
  if (!sameArray(actualProtectedPaths, expectedPaths)) {
    throw new Error(
      `protected route set drift: ${JSON.stringify(expectedPaths)} -> ${JSON.stringify(actualProtectedPaths)}`,
    )
  }

  for (const expected of STAGE4_PROTECTED_ROUTE_CONTRACT) {
    const actual = routes.find(({ path: routePath }) => routePath === expected.path)
    if (!actual) throw new Error(`protected route is missing: ${expected.path}`)
    const actualShape = routeShape(actual)
    for (const field of ['shellClass', 'protection', 'v2Scope']) {
      if (actualShape[field] !== expected[field]) {
        throw new Error(
          `${expected.path} ${field} drift: ${expected[field]} -> ${String(actualShape[field])}`,
        )
      }
    }
    if (!sameArray(actualShape.protectedInteriors, expected.protectedInteriors)) {
      throw new Error(`${expected.path} protected interiors drift`)
    }
  }

  return {
    count: STAGE4_PROTECTED_ROUTE_CONTRACT.length,
    full: STAGE4_PROTECTED_ROUTE_CONTRACT.filter(({ protection }) => protection === 'full').length,
    mixed: STAGE4_PROTECTED_ROUTE_CONTRACT.filter(({ protection }) => protection === 'mixed')
      .length,
  }
}

function uniqueRouteBlock(source, routePath) {
  const escapedPath = routePath.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const expression = new RegExp(`  \\{\\n    path: '${escapedPath}',[\\s\\S]*?\\n  \\},`, 'g')
  const matches = [...source.matchAll(expression)]
  if (matches.length !== 1) {
    throw new Error(`${routePath}: expected exactly one runtime route block`)
  }
  return matches[0][0]
}

function parseQuotedArray(block, property) {
  const match = block.match(new RegExp(`^    ${property}: \\[(.*?)\\],$`, 'm'))
  if (!match) throw new Error(`${property}: runtime route array is missing`)
  if (match[1].trim() === '') return []
  const values = [...match[1].matchAll(/'([^']+)'/g)].map((item) => item[1])
  const residue = match[1].replace(/'[^']+'/g, '').replace(/[\s,]/g, '')
  if (residue !== '') throw new Error(`${property}: runtime route array is invalid`)
  return values
}

function parseEnumProperty(block, property, enumName) {
  const match = block.match(new RegExp(`^    ${property}: ${enumName}\\.([A-Z_]+),$`, 'm'))
  if (!match) throw new Error(`${property}: runtime route enum is missing`)
  return match[1].toLowerCase().replaceAll('_', '-')
}

export function assertStage4RuntimeRouteProtection(source) {
  if (typeof source !== 'string') throw new TypeError('runtime route contract must be text')
  const parsed = STAGE4_PROTECTED_ROUTE_CONTRACT.map((expected) => {
    const block = uniqueRouteBlock(source, expected.path)
    return {
      path: expected.path,
      shellClass: parseEnumProperty(block, 'shellClass', 'UI_ROUTE_SHELL'),
      protection: parseEnumProperty(block, 'protection', 'UI_ROUTE_PROTECTION'),
      protectedInteriors: parseQuotedArray(block, 'protectedInteriors'),
      v2Scope: parseEnumProperty(block, 'v2Scope', 'UI_V2_SCOPE'),
    }
  })
  return assertStage4RouteProtection(parsed)
}
