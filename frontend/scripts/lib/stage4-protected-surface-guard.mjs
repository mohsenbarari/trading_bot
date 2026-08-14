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

// This is a one-purpose disposition, not a Stage 4 baseline rewrite. It permits
// only the Stage 6 replacement of the unprotected system-reset native confirm.
export const STAGE6_TRADING_SETTINGS_RESET_DIALOG_KIND =
  'stage6-trading-settings-reset-dialog'
export const STAGE6_TRADING_SETTINGS_RESET_DIALOG_SHA256 =
  'a3718e8beccbdd6eddcbcd72eebd1838fdf4584430f4ed8ba12c5ec95030eea0'
export const STAGE6_TRADING_SETTINGS_PROTECTED_CALENDAR_CONFIRM =
  "if (!confirm('آیا از حذف این استثنای تقویمی مطمئن هستید؟'))"
export const STAGE6_TRADING_SETTINGS_REMOVED_RESET_CONFIRM =
  "if (!confirm('آیا از بازنشانی تنظیمات به مقادیر پیش‌فرض مطمئن هستید؟'))"

export const STAGE4_SHARED_DEPENDENCY_ISOLATION_PATHS = Object.freeze([
  'frontend/src/App.vue',
  'frontend/src/assets/main.css',
  'frontend/src/components/JalaliDatePicker.vue',
  'frontend/src/components/ui/AppEmptyState.vue',
  TRADING_SETTINGS_PATH,
  'frontend/src/components/UserProfile.vue',
  'frontend/src/components/PublicProfile.vue',
  'frontend/src/views/MarketView.vue',
  'frontend/src/components/CreateChannelView.vue',
  'frontend/src/views/NotificationsView.vue',
  'frontend/src/views/CustomerWorkspaceView.vue',
  'frontend/src/views/SettingsView.vue',
  'frontend/src/views/OperationsView.vue',
  'frontend/src/views/AccountantWorkspaceView.vue',
  'frontend/src/components/CommodityManager.vue',
  'frontend/src/components/UserManager.vue',
  'frontend/src/components/CreateInvitationView.vue',
])

const STAGE7_JALALI_CONSUMER_PATHS = Object.freeze([
  'frontend/src/components/UserProfile.vue',
  'frontend/src/components/PublicProfile.vue',
])

const PROTECTED_EMPTY_STATE_CONSUMER_PATHS = Object.freeze([
  'frontend/src/views/MarketView.vue',
  'frontend/src/components/CreateChannelView.vue',
])

const STAGE7_EMPTY_STATE_CONSUMER_PATHS = Object.freeze([
  'frontend/src/views/NotificationsView.vue',
  'frontend/src/views/CustomerWorkspaceView.vue',
  'frontend/src/views/SettingsView.vue',
  'frontend/src/views/OperationsView.vue',
  'frontend/src/views/AccountantWorkspaceView.vue',
  'frontend/src/components/PublicProfile.vue',
  'frontend/src/components/CommodityManager.vue',
  'frontend/src/components/UserManager.vue',
  'frontend/src/components/CreateInvitationView.vue',
])

export const MARKET_RUNTIME_CONTRACT = 'stage4-market-owned-runtime-v1'
export const MESSENGER_RUNTIME_CONTRACT = 'stage4-messenger-owned-runtime-v1'

export const MARKET_RUNTIME_BASELINE = Object.freeze({
  count: 19,
  contentBytes: 137246,
  pathSetSha256: '37aa0b51e20f4ae86f7daf6c3c231d93b3d1f288ade1471490a1f843a57c9589',
  sha256: '162e9e618684a24f3db3298eb8ff2c62498b18753cd4e0b6d6b97650d0202058',
})

// This exact integration disposition preserves the immutable Stage 4
// baseline while admitting only the reviewed Market changes already present
// on main when UI/UX candidate fed8fa49 is integrated with main 443ea5a1.
export const MAIN_UIUX_INTEGRATION_MARKET_KIND =
  'main-443ea5a-uiux-fed8fa49-market-integration'

export const MAIN_UIUX_INTEGRATION_MARKET_ALLOWED_PATHS = Object.freeze([
  'frontend/src/components/OfferPreviewModal.vue',
  'frontend/src/components/OffersList.vue',
  'frontend/src/components/ui/AppOfferCard.vue',
  'frontend/src/composables/useOffers.ts',
  'frontend/src/utils/settlementType.ts',
  'frontend/src/views/MarketView.vue',
])

export const MAIN_UIUX_INTEGRATION_MARKET_ALLOWED_FILE_SHA256 = Object.freeze({
  'frontend/src/components/OfferPreviewModal.vue':
    '8a8aa129152070e192876eb9924e56d860c60b610cc4b2695a929d0c0dfa3e42',
  'frontend/src/components/OffersList.vue':
    '5e1d017e17f772e9a1621be54af16758128aaceb687942c123fc68bbfa21d6d9',
  'frontend/src/components/ui/AppOfferCard.vue':
    'edf2a78ed0a556b4b5e6ae2dbb81c6499da305ef5e36fc2de26c5271e1fff864',
  'frontend/src/composables/useOffers.ts':
    '4ce35b122ccfe94bcdac910663b9409211cac50eedd4bc0e08293e6067865bec',
  'frontend/src/utils/settlementType.ts':
    '4b1648a7310806d4d4bee7e5b241af663c6c998aaa7dde279ebee63a3dc6e5af',
  'frontend/src/views/MarketView.vue':
    'a03b608c63d2fc4ae397399ffb1bb5cf9d2b88adf201e4cf4dd4cd3a981a8d11',
})

export const MAIN_UIUX_INTEGRATION_MARKET_EVIDENCE = Object.freeze({
  count: 19,
  contentBytes: 147307,
  pathSetSha256: '37aa0b51e20f4ae86f7daf6c3c231d93b3d1f288ade1471490a1f843a57c9589',
  sha256: 'cff97c36d965737605b80c098918c517999fb11f2c66108c2dae4573aac07867',
})

export const MESSENGER_RUNTIME_BASELINE = Object.freeze({
  count: 85,
  contentBytes: 1312405,
  pathSetSha256: 'f6af1f961e45d785ba9c752ee670643571086c6a946843807fe6f581d11aea58',
  sha256: 'f66debf9809180d97b2bac98f5195ba24200d3b61b0d8e0e5cd423a8a7b97248',
})

// This is a one-purpose disposition, not a Stage 4 baseline update. It permits
// only the Stage 6 removal of profile labels from Messenger-originated URLs.
export const STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_PATHS = Object.freeze([
  'frontend/src/components/ChatView.vue',
  'frontend/src/components/CreateChannelView.vue',
  'frontend/src/views/MessengerView.vue',
])

export const STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_FILE_SHA256 = Object.freeze({
  'frontend/src/components/ChatView.vue':
    'e03ded196c369871f3ecd6763c09535c5a57efc5c0a767d848b2c5a94994273b',
  'frontend/src/components/CreateChannelView.vue':
    '708cabb84325114d03b35b5db8a0b4add64193f438c1a3375a5e66232034102c',
  'frontend/src/views/MessengerView.vue':
    '1cabee73dc161c456130f131f53274a5b546816ff0652d68a4e6ea290e0f83fb',
})

export const STAGE6_MESSENGER_URL_PRIVACY_EVIDENCE = Object.freeze({
  count: 85,
  contentBytes: 1311100,
  pathSetSha256: 'f6af1f961e45d785ba9c752ee670643571086c6a946843807fe6f581d11aea58',
  sha256: '3089210a77936d29754c9478fcdf40619acd08f35d1e8c64f6266fe8efb1699a',
})

// This is a one-purpose disposition, not a Stage 4 or Stage 6 rewrite. It
// permits only the Stage 8 CreateChannel HelpPopover placement remediation.
export const STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_KIND =
  'stage8-createchannel-helppopover-placement'

export const STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_ALLOWED_PATHS = Object.freeze([
  'frontend/src/components/CreateChannelView.vue',
])

export const STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_ALLOWED_FILE_SHA256 = Object.freeze({
  'frontend/src/components/CreateChannelView.vue':
    '2e92310e8c74150f9d94162405b68b4ed7bc36198bdfd3536faaae7b5568149a',
})

export const STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_LOCKED_STAGE6_PATHS = Object.freeze([
  'frontend/src/components/ChatView.vue',
  'frontend/src/views/MessengerView.vue',
])

export const STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_EVIDENCE = Object.freeze({
  count: 85,
  contentBytes: 1311122,
  pathSetSha256: 'f6af1f961e45d785ba9c752ee670643571086c6a946843807fe6f581d11aea58',
  sha256: '7659633875a604e75b925dcd9938ac71f74090b8b077d55ec9d4809107224124',
})

// This is a one-purpose disposition, not a Stage 4/6 rewrite. It permits only
// aria-label names on the four unnamed Messenger list controls found by Gate A.
export const STAGE8_MESSENGER_UNNAMED_CONTROL_KIND = 'stage8-messenger-unnamed-control-names'

export const STAGE8_MESSENGER_UNNAMED_CONTROL_ALLOWED_PATHS = Object.freeze([
  'frontend/src/components/chat/ChatHeader.vue',
  'frontend/src/components/chat/ChatConversationList.vue',
])

export const STAGE8_MESSENGER_UNNAMED_CONTROL_ALLOWED_FILE_SHA256 = Object.freeze({
  'frontend/src/components/chat/ChatHeader.vue':
    'a18d717f9823c262d2bbc9d3dc01cfca488be2a9cbc46d89a3dffb29429ad635',
  'frontend/src/components/chat/ChatConversationList.vue':
    '20359ff625de5faf7fcff7e739d181ee14e5d2262be8acaa559f2ba39f03f142',
})

export const STAGE8_MESSENGER_UNNAMED_CONTROL_LOCKED_STAGE8_PATHS = Object.freeze([
  'frontend/src/components/CreateChannelView.vue',
])

export const STAGE8_MESSENGER_UNNAMED_CONTROL_LOCKED_STAGE6_PATHS = Object.freeze([
  'frontend/src/components/ChatView.vue',
  'frontend/src/views/MessengerView.vue',
])

export const STAGE8_MESSENGER_UNNAMED_CONTROL_EVIDENCE = Object.freeze({
  count: 85,
  contentBytes: 1311357,
  pathSetSha256: 'f6af1f961e45d785ba9c752ee670643571086c6a946843807fe6f581d11aea58',
  sha256: '32dde68767fbcf6dfd070e25547ca5c2d69199aaf9d1999fff26bfcac05bedbb',
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

function assertMainUiuxIntegrationMarketAllowedFiles(entries) {
  const entriesByPath = new Map(entries.map((entry) => [entry.path, entry]))
  for (const repoPath of MAIN_UIUX_INTEGRATION_MARKET_ALLOWED_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) {
      throw new Error(`main/UIUX Market integration allowed file is missing: ${repoPath}`)
    }
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = MAIN_UIUX_INTEGRATION_MARKET_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `main/UIUX Market integration allowed file drift: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
  }
}

export function assertMainUiuxIntegrationMarketDisposition(entries) {
  assertMainUiuxIntegrationMarketAllowedFiles(entries)
  return assertProtectedFileSetEvidence(
    'main/UIUX Market integration disposition',
    protectedFileSetEvidence(entries, MARKET_RUNTIME_CONTRACT),
    MAIN_UIUX_INTEGRATION_MARKET_EVIDENCE,
  )
}

export function resolveMarketRuntimeDisposition(entries) {
  const actual = protectedFileSetEvidence(entries, MARKET_RUNTIME_CONTRACT)
  try {
    return {
      kind: 'stage4-baseline',
      evidence: assertProtectedFileSetEvidence(
        'Market runtime',
        actual,
        MARKET_RUNTIME_BASELINE,
      ),
    }
  } catch (baselineError) {
    try {
      return {
        kind: MAIN_UIUX_INTEGRATION_MARKET_KIND,
        evidence: assertMainUiuxIntegrationMarketDisposition(entries),
      }
    } catch (integrationError) {
      const baselineMessage =
        baselineError instanceof Error ? baselineError.message : String(baselineError)
      const integrationMessage =
        integrationError instanceof Error ? integrationError.message : String(integrationError)
      throw new Error(
        `Market runtime rejected after Stage 4 baseline drift (${baselineMessage}); main/UIUX integration disposition rejected (${integrationMessage})`,
      )
    }
  }
}

export function fileSha256(value) {
  return sha256(value)
}

function requiredTextSource(sources, repoPath) {
  const value = sources instanceof Map ? sources.get(repoPath) : sources?.[repoPath]
  if (typeof value !== 'string') {
    throw new Error(`shared dependency source is missing: ${repoPath}`)
  }
  return value
}

function componentTags(source, componentName) {
  const expression = new RegExp(`<${componentName}\\b[^>]*>`, 'g')
  return [...source.matchAll(expression)].map((match) => match[0])
}

function styleBlocks(source) {
  const blocks = [...source.matchAll(/<style(?:\s[^>]*)?>([\s\S]*?)<\/style>/g)].map(
    (match) => match[1],
  )
  return blocks.length ? blocks.join('\n') : source
}

function mediaBlockBodies(source, params) {
  const bodies = []
  let searchFrom = 0
  while (searchFrom < source.length) {
    const mediaIndex = source.indexOf(`@media ${params}`, searchFrom)
    if (mediaIndex === -1) break
    const openBrace = source.indexOf('{', mediaIndex)
    if (openBrace === -1) throw new Error(`${params}: media block is malformed`)

    let depth = 1
    let cursor = openBrace + 1
    while (cursor < source.length && depth > 0) {
      if (source[cursor] === '{') depth += 1
      else if (source[cursor] === '}') depth -= 1
      cursor += 1
    }
    if (depth !== 0) throw new Error(`${params}: media block is malformed`)
    bodies.push(source.slice(openBrace + 1, cursor - 1))
    searchFrom = cursor
  }
  return bodies
}

function assertNoBareFadeReducedMotion(label, source) {
  const reducedMotionBlocks = mediaBlockBodies(
    styleBlocks(source),
    '(prefers-reduced-motion: reduce)',
  )
  for (const block of reducedMotionBlocks) {
    if (/(?:^|[},])\s*\.fade-(?:enter|leave)-active(?=\s*[,\{])/m.test(block)) {
      throw new Error(`${label}: bare fade reduced-motion selector bypasses protected routes`)
    }
  }
  return reducedMotionBlocks
}

/**
 * Protects shared dependencies by behavior instead of rebasing immutable
 * whole-file hashes. Defaults must remain inert for protected consumers;
 * Stage 7 behavior is allowed only through explicit call-site opt-ins.
 */
export function assertStage4SharedDependencyIsolation(sources) {
  const appSource = requiredTextSource(sources, 'frontend/src/App.vue')
  const mainCssSource = requiredTextSource(sources, 'frontend/src/assets/main.css')
  const appStyleSource = styleBlocks(appSource)
  const reducedMotionEligibilityBlock = appSource.match(
    /const allowsReducedMotionRouteTransition = computed\(([\s\S]*?)\n\)/,
  )?.[1]
  if (
    !appSource.includes('getUiRouteContractByName,') ||
    !appSource.includes('UI_ROUTE_PROTECTION,') ||
    !reducedMotionEligibilityBlock?.includes('getUiRouteContractByName(route.name)?.protection') ||
    !reducedMotionEligibilityBlock.includes('UI_ROUTE_PROTECTION.NONE') ||
    !reducedMotionEligibilityBlock.includes('v2Scope.value === UI_V2_SCOPE.SECTION') ||
    !appSource.includes("shouldScopeRoute.value ? 'ui-v2-route-fade' : 'fade'") ||
    !appSource.includes(
      "allowsReducedMotionRouteTransition.value ? 'app-reduced-motion-route' : undefined",
    ) ||
    !appSource.includes('<transition :name="routeTransitionName">') ||
    !appSource.includes(':class="[reducedMotionRouteClass, persianTypographyRouteClass]"')
  ) {
    throw new Error(
      'App route transition is not isolated behind the unprotected-section opt-in contract',
    )
  }

  for (const [label, source] of [
    ['App.vue', appSource],
    ['main.css', mainCssSource],
  ]) {
    assertNoBareFadeReducedMotion(label, source)
  }
  const appReducedMotion = mediaBlockBodies(appStyleSource, '(prefers-reduced-motion: reduce)')
  if (
    !appReducedMotion.some(
      (block) =>
        block.includes('.app-reduced-motion-route.fade-enter-active') &&
        block.includes('.app-reduced-motion-route.fade-leave-active') &&
        block.includes('transition: none;'),
    )
  ) {
    throw new Error('App V2 route reduced-motion opt-in is missing')
  }

  const jalaliSource = requiredTextSource(sources, 'frontend/src/components/JalaliDatePicker.vue')
  if (
    !jalaliSource.includes('arrowKeyNavigation?: boolean') ||
    !jalaliSource.includes('arrowKeyNavigation: false') ||
    !jalaliSource.includes('if (!props.arrowKeyNavigation || !date || props.disabled) return')
  ) {
    throw new Error('JalaliDatePicker arrow navigation must remain default-off and guarded')
  }

  const protectedJalaliTags = componentTags(
    requiredTextSource(sources, TRADING_SETTINGS_PATH),
    'JalaliDatePicker',
  )
  if (!protectedJalaliTags.length) {
    throw new Error('TradingSettings JalaliDatePicker consumer is missing')
  }
  if (protectedJalaliTags.some((tag) => tag.includes('arrow-key-navigation'))) {
    throw new Error('TradingSettings must not opt in to Jalali arrow navigation')
  }

  let stage7JalaliOptIns = 0
  for (const repoPath of STAGE7_JALALI_CONSUMER_PATHS) {
    const tags = componentTags(requiredTextSource(sources, repoPath), 'JalaliDatePicker')
    if (!tags.length || tags.some((tag) => !tag.includes('arrow-key-navigation'))) {
      throw new Error(`${repoPath}: Stage 7 Jalali consumer lacks explicit arrow opt-in`)
    }
    stage7JalaliOptIns += tags.length
  }

  const emptyStateSource = requiredTextSource(
    sources,
    'frontend/src/components/ui/AppEmptyState.vue',
  )
  if (
    !emptyStateSource.includes("role?: 'status' | 'alert'") ||
    !emptyStateSource.includes(':role="role"') ||
    /\brole\s*:\s*['"]status['"]/.test(emptyStateSource) ||
    /<section\b[^>]*\srole=['"]status['"]/.test(emptyStateSource)
  ) {
    throw new Error('AppEmptyState role must remain opt-in with no default status semantics')
  }

  let protectedEmptyStateConsumers = 0
  for (const repoPath of PROTECTED_EMPTY_STATE_CONSUMER_PATHS) {
    const tags = componentTags(requiredTextSource(sources, repoPath), 'AppEmptyState')
    if (!tags.length) throw new Error(`${repoPath}: protected AppEmptyState consumer is missing`)
    if (tags.some((tag) => /\s:?role\s*=/.test(tag))) {
      throw new Error(`${repoPath}: protected AppEmptyState consumer must use the inert default`)
    }
    protectedEmptyStateConsumers += tags.length
  }

  let stage7EmptyStateOptIns = 0
  for (const repoPath of STAGE7_EMPTY_STATE_CONSUMER_PATHS) {
    const tags = componentTags(requiredTextSource(sources, repoPath), 'AppEmptyState')
    if (!tags.length || tags.some((tag) => !/\srole="(?:status|alert)"/.test(tag))) {
      throw new Error(`${repoPath}: Stage 7 empty state lacks an explicit semantic role`)
    }
    stage7EmptyStateOptIns += tags.length
  }

  return {
    reducedMotionSources: 2,
    protectedJalaliConsumers: protectedJalaliTags.length,
    stage7JalaliOptIns,
    protectedEmptyStateConsumers,
    stage7EmptyStateOptIns,
  }
}

function assertStage6MessengerUrlPrivacyAllowedFiles(entries) {
  const entriesByPath = new Map(entries.map((entry) => [entry.path, entry]))
  for (const repoPath of STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) {
      throw new Error(`Stage 6 Messenger URL-privacy allowed file is missing: ${repoPath}`)
    }
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `Stage 6 Messenger URL-privacy allowed file drift: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
  }
}

/**
 * Accepts only the one reviewed Stage 6 Messenger URL-privacy remediation.
 * The aggregate evidence freezes every other Messenger-owned file, while the
 * per-file hashes make the three permitted paths independently auditable.
 */
export function assertStage6MessengerUrlPrivacyDisposition(entries) {
  assertStage6MessengerUrlPrivacyAllowedFiles(entries)
  return assertProtectedFileSetEvidence(
    'Stage 6 Messenger URL-privacy disposition',
    protectedFileSetEvidence(entries, MESSENGER_RUNTIME_CONTRACT),
    STAGE6_MESSENGER_URL_PRIVACY_EVIDENCE,
  )
}

function assertStage8CreateChannelHelpPopoverPlacementAllowedFiles(entries) {
  const entriesByPath = new Map(entries.map((entry) => [entry.path, entry]))
  for (const repoPath of STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_ALLOWED_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) {
      throw new Error(
        `Stage 8 CreateChannel HelpPopover placement allowed file is missing: ${repoPath}`,
      )
    }
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `Stage 8 CreateChannel HelpPopover placement allowed file drift: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
  }
  for (const repoPath of STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_LOCKED_STAGE6_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) {
      throw new Error(
        `Stage 8 CreateChannel HelpPopover placement locked Stage 6 file is missing: ${repoPath}`,
      )
    }
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `Stage 8 CreateChannel HelpPopover placement requires unchanged Stage 6 file: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
  }
}

/**
 * Accepts only the one reviewed Stage 8 CreateChannel HelpPopover placement
 * remediation. ChatView and MessengerView stay on their Stage 6 hashes; only
 * CreateChannelView may carry the new exact file hash.
 */
export function assertStage8CreateChannelHelpPopoverPlacementDisposition(entries) {
  assertStage8CreateChannelHelpPopoverPlacementAllowedFiles(entries)
  return assertProtectedFileSetEvidence(
    'Stage 8 CreateChannel HelpPopover placement remediation',
    protectedFileSetEvidence(entries, MESSENGER_RUNTIME_CONTRACT),
    STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_EVIDENCE,
  )
}

function assertStage8MessengerUnnamedControlAllowedFiles(entries) {
  const entriesByPath = new Map(entries.map((entry) => [entry.path, entry]))
  for (const repoPath of STAGE8_MESSENGER_UNNAMED_CONTROL_ALLOWED_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) {
      throw new Error(`Stage 8 Messenger unnamed-control allowed file is missing: ${repoPath}`)
    }
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = STAGE8_MESSENGER_UNNAMED_CONTROL_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `Stage 8 Messenger unnamed-control allowed file drift: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
    const text = entry.content.toString('utf8')
    if (repoPath.endsWith('ChatHeader.vue')) {
      if (!text.includes('aria-label="بازگشت"') || !text.includes('aria-label="جستجو"') || !text.includes('aria-label="گزینه‌های بیشتر"')) {
        throw new Error('Stage 8 Messenger unnamed-control disposition lost ChatHeader accessible names')
      }
    }
    if (repoPath.endsWith('ChatConversationList.vue') && !text.includes('aria-label="شروع گفتگوی جدید"')) {
      throw new Error('Stage 8 Messenger unnamed-control disposition lost the new-chat accessible name')
    }
  }
  for (const repoPath of STAGE8_MESSENGER_UNNAMED_CONTROL_LOCKED_STAGE8_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) {
      throw new Error(`Stage 8 Messenger unnamed-control locked Stage 8 file is missing: ${repoPath}`)
    }
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `Stage 8 Messenger unnamed-control requires unchanged CreateChannel file: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
  }
  for (const repoPath of STAGE8_MESSENGER_UNNAMED_CONTROL_LOCKED_STAGE6_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) {
      throw new Error(`Stage 8 Messenger unnamed-control locked Stage 6 file is missing: ${repoPath}`)
    }
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `Stage 8 Messenger unnamed-control requires unchanged Stage 6 file: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
  }
}

export function assertStage8MessengerUnnamedControlDisposition(entries) {
  assertStage8MessengerUnnamedControlAllowedFiles(entries)
  return assertProtectedFileSetEvidence(
    'Stage 8 Messenger unnamed-control names',
    protectedFileSetEvidence(entries, MESSENGER_RUNTIME_CONTRACT),
    STAGE8_MESSENGER_UNNAMED_CONTROL_EVIDENCE,
  )
}

/**
 * Stage 4 remains immutable. If it no longer matches, the exact Stage 6
 * URL-privacy disposition is tried next, then the exact Stage 8
 * CreateChannel HelpPopover placement remediation. All other drift fails.
 */
function tradingSettingsSourceText(source) {
  return Buffer.isBuffer(source) ? source.toString('utf8') : String(source)
}

/**
 * Accepts only the one reviewed Stage 6 TradingSettings reset-dialog change.
 * The protected market-calendar native confirm stays exactly as Stage 4 left it.
 */
export function assertStage6TradingSettingsResetDialogDisposition(source) {
  const text = tradingSettingsSourceText(source)
  if (!text.includes(STAGE6_TRADING_SETTINGS_PROTECTED_CALENDAR_CONFIRM)) {
    throw new Error(
      'Stage 6 TradingSettings reset-dialog disposition lost the protected calendar confirm',
    )
  }
  if (text.includes(STAGE6_TRADING_SETTINGS_REMOVED_RESET_CONFIRM)) {
    throw new Error(
      'Stage 6 TradingSettings reset-dialog disposition must not keep the native reset confirm',
    )
  }
  if (!text.includes('<AppConfirmDialog') || !text.includes('requestResetConfirmation')) {
    throw new Error(
      'Stage 6 TradingSettings reset-dialog disposition is missing the shared reset dialog',
    )
  }
  if (text.includes('arrow-key-navigation')) {
    throw new Error(
      'Stage 6 TradingSettings reset-dialog disposition must not opt in Jalali arrows',
    )
  }
  const actualSha256 = fileSha256(source)
  if (actualSha256 !== STAGE6_TRADING_SETTINGS_RESET_DIALOG_SHA256) {
    throw new Error(
      `Stage 6 TradingSettings reset-dialog allowed file drift: ${STAGE6_TRADING_SETTINGS_RESET_DIALOG_SHA256} -> ${actualSha256}`,
    )
  }
  return actualSha256
}

/**
 * Stage 4 remains the immutable whole-file baseline. If it no longer matches,
 * only the exact Stage 6 reset-dialog disposition may accept TradingSettings.
 */
export function resolveTradingSettingsDisposition(source) {
  const actualSha256 = fileSha256(source)
  if (actualSha256 === TRADING_SETTINGS_SHA256) {
    return {
      kind: 'stage4-baseline',
      sha256: actualSha256,
    }
  }
  try {
    return {
      kind: STAGE6_TRADING_SETTINGS_RESET_DIALOG_KIND,
      sha256: assertStage6TradingSettingsResetDialogDisposition(source),
    }
  } catch (stage6Error) {
    const stage6Message = stage6Error instanceof Error ? stage6Error.message : String(stage6Error)
    throw new Error(
      `TradingSettings rejected after Stage 4 whole-file drift (${TRADING_SETTINGS_SHA256} -> ${actualSha256}); Stage 6 reset-dialog disposition rejected (${stage6Message})`,
    )
  }
}

export function resolveMessengerRuntimeDisposition(entries) {
  const actual = protectedFileSetEvidence(entries, MESSENGER_RUNTIME_CONTRACT)
  try {
    return {
      kind: 'stage4-baseline',
      evidence: assertProtectedFileSetEvidence(
        'Messenger runtime',
        actual,
        MESSENGER_RUNTIME_BASELINE,
      ),
    }
  } catch (baselineError) {
    try {
      return {
        kind: 'stage6-url-privacy',
        evidence: assertStage6MessengerUrlPrivacyDisposition(entries),
      }
    } catch (stage6Error) {
      try {
        return {
          kind: STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_KIND,
          evidence: assertStage8CreateChannelHelpPopoverPlacementDisposition(entries),
        }
      } catch (stage8Error) {
        try {
          return {
            kind: STAGE8_MESSENGER_UNNAMED_CONTROL_KIND,
            evidence: assertStage8MessengerUnnamedControlDisposition(entries),
          }
        } catch (stage8NamesError) {
          const baselineMessage =
            baselineError instanceof Error ? baselineError.message : String(baselineError)
          const stage6Message =
            stage6Error instanceof Error ? stage6Error.message : String(stage6Error)
          const stage8Message =
            stage8Error instanceof Error ? stage8Error.message : String(stage8Error)
          const stage8NamesMessage =
            stage8NamesError instanceof Error ? stage8NamesError.message : String(stage8NamesError)
          throw new Error(
            `Messenger runtime rejected after Stage 4 baseline drift (${baselineMessage}); Stage 6 URL-privacy disposition rejected (${stage6Message}); Stage 8 CreateChannel HelpPopover placement remediation rejected (${stage8Message}); Stage 8 Messenger unnamed-control names rejected (${stage8NamesMessage})`,
          )
        }
      }
    }
  }
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
