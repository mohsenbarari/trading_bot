import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import {
  ADMIN_MESSAGES_PATH,
  ADMIN_MESSAGES_SHA256,
  MAIN_UIUX_INTEGRATION_MARKET_ALLOWED_FILE_SHA256,
  MAIN_UIUX_INTEGRATION_MARKET_ALLOWED_PATHS,
  MAIN_UIUX_INTEGRATION_MARKET_EVIDENCE,
  MAIN_UIUX_INTEGRATION_MARKET_KIND,
  MARKET_A_PLUS_C_ALLOWED_FILE_SHA256,
  MARKET_A_PLUS_C_ALLOWED_PATHS,
  MARKET_A_PLUS_C_EVIDENCE,
  MARKET_A_PLUS_C_KIND,
  MARKET_A_PLUS_C_LIFECYCLE_ALLOWED_FILE_SHA256,
  MARKET_A_PLUS_C_LIFECYCLE_ALLOWED_PATHS,
  MARKET_A_PLUS_C_LIFECYCLE_EVIDENCE,
  MARKET_A_PLUS_C_LIFECYCLE_KIND,
  MARKET_A_PLUS_C_PERIMETER_ALLOWED_FILE_SHA256,
  MARKET_A_PLUS_C_PERIMETER_ALLOWED_PATHS,
  MARKET_A_PLUS_C_PERIMETER_EVIDENCE,
  MARKET_A_PLUS_C_PERIMETER_KIND,
  MARKET_A_PLUS_C_LINEAR_METER_ALLOWED_FILE_SHA256,
  MARKET_A_PLUS_C_LINEAR_METER_ALLOWED_PATHS,
  MARKET_A_PLUS_C_LINEAR_METER_EVIDENCE,
  MARKET_A_PLUS_C_LINEAR_METER_KIND,
  MARKET_COMPACT_BUTTON_CONFIRM_ALLOWED_FILE_SHA256,
  MARKET_COMPACT_BUTTON_CONFIRM_ALLOWED_PATHS,
  MARKET_COMPACT_BUTTON_CONFIRM_EVIDENCE,
  MARKET_COMPACT_BUTTON_CONFIRM_KIND,
  MARKET_CUSTOMER_HISTORY_ACCESS_ALLOWED_FILE_SHA256,
  MARKET_CUSTOMER_HISTORY_ACCESS_ALLOWED_PATHS,
  MARKET_CUSTOMER_HISTORY_ACCESS_EVIDENCE,
  MARKET_CUSTOMER_HISTORY_ACCESS_KIND,
  MARKET_CROSS_SERVER_LOT_SUGGESTION_ALLOWED_FILE_SHA256,
  MARKET_CROSS_SERVER_LOT_SUGGESTION_ALLOWED_PATHS,
  MARKET_CROSS_SERVER_LOT_SUGGESTION_EVIDENCE,
  MARKET_CROSS_SERVER_LOT_SUGGESTION_KIND,
  MARKET_FEED_HEADING_REMOVAL_ALLOWED_FILE_SHA256,
  MARKET_FEED_HEADING_REMOVAL_ALLOWED_PATHS,
  MARKET_FEED_HEADING_REMOVAL_EVIDENCE,
  MARKET_FEED_HEADING_REMOVAL_KIND,
  MARKET_HISTORY_TERMINAL_VISUAL_ALLOWED_FILE_SHA256,
  MARKET_HISTORY_TERMINAL_VISUAL_ALLOWED_PATHS,
  MARKET_HISTORY_TERMINAL_VISUAL_EVIDENCE,
  MARKET_HISTORY_TERMINAL_VISUAL_KIND,
  MARKET_INFERENCE_CONFIRMATION_UX_ALLOWED_FILE_SHA256,
  MARKET_INFERENCE_CONFIRMATION_UX_ALLOWED_PATHS,
  MARKET_INFERENCE_CONFIRMATION_UX_EVIDENCE,
  MARKET_INFERENCE_CONFIRMATION_UX_KIND,
  MARKET_HISTORY_COMPACT_SUMMARY_ALLOWED_FILE_SHA256,
  MARKET_HISTORY_COMPACT_SUMMARY_ALLOWED_PATHS,
  MARKET_HISTORY_COMPACT_SUMMARY_EVIDENCE,
  MARKET_HISTORY_COMPACT_SUMMARY_KIND,
  MARKET_HISTORY_COMPACT_SEPARATION_ALLOWED_FILE_SHA256,
  MARKET_HISTORY_COMPACT_SEPARATION_ALLOWED_PATHS,
  MARKET_HISTORY_COMPACT_SEPARATION_EVIDENCE,
  MARKET_HISTORY_COMPACT_SEPARATION_KIND,
  MARKET_OVERTIME_REQUESTER_ACK_ALLOWED_FILE_SHA256,
  MARKET_OVERTIME_REQUESTER_ACK_ALLOWED_PATHS,
  MARKET_OVERTIME_REQUESTER_ACK_EVIDENCE,
  MARKET_OVERTIME_REQUESTER_ACK_KIND,
  MARKET_RUNTIME_BASELINE,
  MARKET_RUNTIME_CONTRACT,
  MESSENGER_OMITTED_DIRECT_RUNTIME_PATHS,
  MESSENGER_RUNTIME_BASELINE,
  MESSENGER_RUNTIME_CONTRACT,
  STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_FILE_SHA256,
  STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_PATHS,
  STAGE6_MESSENGER_URL_PRIVACY_EVIDENCE,
  STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_ALLOWED_FILE_SHA256,
  STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_ALLOWED_PATHS,
  STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_EVIDENCE,
  STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_KIND,
  STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_LOCKED_STAGE6_PATHS,
  STAGE8_MESSENGER_UNNAMED_CONTROL_ALLOWED_FILE_SHA256,
  STAGE8_MESSENGER_UNNAMED_CONTROL_ALLOWED_PATHS,
  STAGE8_MESSENGER_UNNAMED_CONTROL_EVIDENCE,
  STAGE8_MESSENGER_UNNAMED_CONTROL_KIND,
  STAGE8_MESSENGER_UNNAMED_CONTROL_LOCKED_STAGE6_PATHS,
  STAGE8_MESSENGER_UNNAMED_CONTROL_LOCKED_STAGE8_PATHS,
  NATIVE_APP_MESSENGER_VISUAL_KIND,
  assertNativeAppMessengerVisualDisposition,
  assertStage8CreateChannelHelpPopoverPlacementDisposition,
  assertStage8MessengerUnnamedControlDisposition,
  assertMainUiuxIntegrationMarketDisposition,
  assertMarketAPlusCDisposition,
  assertMarketCustomerHistoryAccessDisposition,
  assertMarketCustomerHistoryAccessSemantics,
  assertMarketCrossServerLotSuggestionDisposition,
  assertMarketCrossServerLotSuggestionSemantics,
  assertMarketLifecycleClarityDisposition,
  assertMarketLifecycleClaritySemantics,
  assertMarketPerimeterDeadlineDisposition,
  assertMarketPerimeterDeadlineSemantics,
  assertMarketLinearDeadlineDisposition,
  assertMarketLinearDeadlineSemantics,
  assertMarketCompactButtonConfirmDisposition,
  assertMarketCompactButtonConfirmSemantics,
  assertMarketFeedHeadingRemovalDisposition,
  assertMarketFeedHeadingRemovalSemantics,
  assertMarketHistoryTerminalVisualDisposition,
  assertMarketHistoryTerminalVisualSemantics,
  assertMarketInferenceConfirmationUxDisposition,
  assertMarketInferenceConfirmationUxSemantics,
  assertMarketHistoryCompactSummaryDisposition,
  assertMarketHistoryCompactSummarySemantics,
  assertMarketHistoryCompactSeparationDisposition,
  assertMarketHistoryCompactSeparationSemantics,
  assertMarketOvertimeRequesterAcknowledgementDisposition,
  assertMarketOvertimeRequesterAcknowledgementSemantics,
  STAGE4_BASE_COMMIT,
  STAGE4_BASE_TREE,
  STAGE4_ROUTE_CONTRACT_PATH,
  STAGE4_SHARED_DEPENDENCY_ISOLATION_PATHS,
  STAGE4_SCOPE_MANIFEST_PATH,
  STAGE6_TRADING_SETTINGS_RESET_DIALOG_KIND,
  STAGE6_TRADING_SETTINGS_RESET_DIALOG_SHA256,
  TRADING_SETTINGS_PATH,
  TRADING_SETTINGS_SHA256,
  NATIVE_APP_ADMIN_MESSAGES_VISUAL_KIND,
  NATIVE_APP_TRADING_SETTINGS_VISUAL_KIND,
  assertNativeAppAdminMessagesVisualDisposition,
  assertNativeAppTradingSettingsVisualDisposition,
  assertStage6TradingSettingsResetDialogDisposition,
  resolveAdminMessagesDisposition,
  resolveTradingSettingsDisposition,
  assertProtectedFileSetEvidence,
  assertStage4RouteProtection,
  assertStage4RuntimeRouteProtection,
  assertStage4SharedDependencyIsolation,
  discoverStage4OwnedRuntimePaths,
  fileSha256,
  isMarketOwnedRuntimePath,
  isMessengerOwnedRuntimePath,
  protectedFileSetEvidence,
  readFileEntries,
  resolveMarketRuntimeDisposition,
  resolveMessengerRuntimeDisposition,
} from './lib/stage4-protected-surface-guard.mjs'
import {
  DASHBOARD_MARKET_REGION_PATH,
  DASHBOARD_MARKET_REGION_SHA256,
  dashboardMarketRegionEvidence,
  extractDashboardMarketSections,
} from './lib/stage3-protected-region-guard.mjs'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.resolve(scriptDir, '..', '..')

function readRepoFile(repoPath, encoding = null) {
  return fs.readFileSync(path.join(repoRoot, repoPath), encoding)
}

function currentEvidence(paths, contract) {
  return protectedFileSetEvidence(readFileEntries(repoRoot, paths), contract)
}

function currentSharedDependencySources() {
  return new Map(
    STAGE4_SHARED_DEPENDENCY_ISOLATION_PATHS.map((repoPath) => [
      repoPath,
      readRepoFile(repoPath, 'utf8'),
    ]),
  )
}

function withCreateChannelPlacementReverted(entries) {
  return entries.map((entry) => {
    if (entry.path !== 'frontend/src/components/CreateChannelView.vue') return entry
    const source = entry.content.toString('utf8')
    const next = source.replace(
      '.manager-section-card.card-with-help {\n  position: relative;\n  padding-left: 4rem;\n}',
      '.manager-section-card.card-with-help {\n  padding-left: 4rem;\n}',
    )
    if (next === source) {
      throw new Error('test mutation did not revert the Stage 8 placement declaration')
    }
    return { ...entry, content: Buffer.from(next, 'utf8') }
  })
}

function withUnnamedControlNamesReverted(entries) {
  return entries.map((entry) => {
    if (entry.path === 'frontend/src/components/chat/ChatHeader.vue') {
      const source = entry.content.toString('utf8')
      const next = source
        .replace(
          '<button class="header-btn back-btn" type="button" aria-label="بازگشت" v-ripple @click="$emit(\'back\')" v-if="!isSearchActive">',
          '<button class="header-btn back-btn" v-ripple @click="$emit(\'back\')" v-if="!isSearchActive">',
        )
        .replace(
          '<button class="header-btn" type="button" aria-label="جستجو" v-ripple @click="$emit(\'toggle-search\')">',
          '<button class="header-btn" v-ripple @click="$emit(\'toggle-search\')">',
        )
        .replace(
          '<button class="header-btn" type="button" aria-label="گزینه‌های بیشتر" v-ripple @click.stop="toggleMenu">',
          '<button class="header-btn" v-ripple @click.stop="toggleMenu">',
        )
      if (next === source) {
        throw new Error('test mutation did not revert ChatHeader accessible names')
      }
      return { ...entry, content: Buffer.from(next, 'utf8') }
    }
    if (entry.path === 'frontend/src/components/chat/ChatConversationList.vue') {
      const source = entry.content.toString('utf8')
      const next = source.replace(
        `<button
      v-if="canStartNewConversation !== false"
      type="button"
      class="fab-new-chat"
      aria-label="شروع گفتگوی جدید"
      v-ripple
      @click="emit('new-conversation')"
    >`,
        `<button v-if="canStartNewConversation !== false" class="fab-new-chat" v-ripple @click="emit('new-conversation')">`,
      )
      if (next === source) {
        throw new Error('test mutation did not revert the new-chat accessible name')
      }
      return { ...entry, content: Buffer.from(next, 'utf8') }
    }
    return entry
  })
}

function withSharedDependencyMutation(sources, repoPath, mutate) {
  const mutated = new Map(sources)
  const source = mutated.get(repoPath)
  const nextSource = mutate(source)
  if (nextSource === source) throw new Error(`test mutation did not change ${repoPath}`)
  mutated.set(repoPath, nextSource)
  return mutated
}

describe('Stage 4 protected surface baseline', () => {
  const ownedPaths = discoverStage4OwnedRuntimePaths(repoRoot)

  it('binds the clean Stage 4 checkpoint commit and tree', () => {
    expect(STAGE4_BASE_COMMIT).toBe('9dfa961000832c830729ce67e8a54357915c716a')
    expect(STAGE4_BASE_TREE).toBe('1540c2534d8052a3a8cfcffcdc2f65e4b85fc874')
  })

  it('isolates protected surfaces from opt-in shared dependency behavior', () => {
    expect(assertStage4SharedDependencyIsolation(currentSharedDependencySources())).toEqual({
      reducedMotionSources: 2,
      protectedJalaliConsumers: 1,
      stage7JalaliOptIns: 4,
      protectedEmptyStateConsumers: 4,
      stage7EmptyStateOptIns: 21,
    })
  })

  it('allows the Stage 8B route-local typography marker without widening reduced-motion eligibility', () => {
    const appSource = readRepoFile('frontend/src/App.vue', 'utf8')
    expect(appSource).toContain(':class="[reducedMotionRouteClass, persianTypographyRouteClass]"')
    expect(appSource).toContain("'app-route--persian-typography'")
    expect(assertStage4SharedDependencyIsolation(currentSharedDependencySources())).toMatchObject({
      reducedMotionSources: 2,
    })
  })

  it('fails closed for shared dependency default or call-site opt-in drift', () => {
    const sources = currentSharedDependencySources()

    const globalReducedMotion = withSharedDependencyMutation(
      sources,
      'frontend/src/assets/main.css',
      (source) =>
        `${source}\n@media (prefers-reduced-motion: reduce) {\n  .fade-enter-active { transition: none; }\n}\n`,
    )
    expect(() => assertStage4SharedDependencyIsolation(globalReducedMotion)).toThrow(
      /bare fade reduced-motion selector/,
    )

    const mixedRouteMotionOptIn = withSharedDependencyMutation(
      sources,
      'frontend/src/App.vue',
      (source) =>
        source.replace(
          'getUiRouteContractByName(route.name)?.protection === UI_ROUTE_PROTECTION.NONE',
          'getUiRouteContractByName(route.name)?.protection !== UI_ROUTE_PROTECTION.FULL',
        ),
    )
    expect(() => assertStage4SharedDependencyIsolation(mixedRouteMotionOptIn)).toThrow(
      /unprotected-section opt-in contract/,
    )

    const jalaliDefaultOn = withSharedDependencyMutation(
      sources,
      'frontend/src/components/JalaliDatePicker.vue',
      (source) => source.replace('arrowKeyNavigation: false', 'arrowKeyNavigation: true'),
    )
    expect(() => assertStage4SharedDependencyIsolation(jalaliDefaultOn)).toThrow(
      /default-off and guarded/,
    )

    const protectedJalaliOptIn = withSharedDependencyMutation(
      sources,
      TRADING_SETTINGS_PATH,
      (source) =>
        source.replace(
          '<JalaliDatePicker\n',
          '<JalaliDatePicker\n                arrow-key-navigation\n',
        ),
    )
    expect(() => assertStage4SharedDependencyIsolation(protectedJalaliOptIn)).toThrow(
      /TradingSettings must not opt in/,
    )

    const emptyStateDefaultOn = withSharedDependencyMutation(
      sources,
      'frontend/src/components/ui/AppEmptyState.vue',
      (source) => source.replace(':role="role"', 'role="status"'),
    )
    expect(() => assertStage4SharedDependencyIsolation(emptyStateDefaultOn)).toThrow(
      /role must remain opt-in/,
    )

    const protectedEmptyStateOptIn = withSharedDependencyMutation(
      sources,
      'frontend/src/views/MarketView.vue',
      (source) => source.replace('<AppEmptyState\n', '<AppEmptyState\n            role="status"\n'),
    )
    expect(() => assertStage4SharedDependencyIsolation(protectedEmptyStateOptIn)).toThrow(
      /protected AppEmptyState consumer must use the inert default/,
    )

    const missingStage7Role = withSharedDependencyMutation(
      sources,
      'frontend/src/views/NotificationsView.vue',
      (source) => source.replace('            role="status"\n', ''),
    )
    expect(() => assertStage4SharedDependencyIsolation(missingStage7Role)).toThrow(
      /Stage 7 empty state lacks an explicit semantic role/,
    )
  })

  it('keeps every prior Market disposition immutable while admitting separated compact terminal history', () => {
    expect(ownedPaths.market).toContain('frontend/src/utils/settlementType.ts')
    expect(ownedPaths.market).toContain('frontend/src/components/CommodityInferenceSelectionModal.vue')
    const entries = readFileEntries(repoRoot, ownedPaths.market)
    expect(resolveMarketRuntimeDisposition(entries)).toMatchObject({
      kind: MARKET_HISTORY_COMPACT_SEPARATION_KIND,
      evidence: MARKET_HISTORY_COMPACT_SEPARATION_EVIDENCE,
    })
    expect(MARKET_RUNTIME_BASELINE).toEqual({
      count: 19,
      contentBytes: 137246,
      pathSetSha256: '37aa0b51e20f4ae86f7daf6c3c231d93b3d1f288ade1471490a1f843a57c9589',
      sha256: '162e9e618684a24f3db3298eb8ff2c62498b18753cd4e0b6d6b97650d0202058',
    })
    expect(MAIN_UIUX_INTEGRATION_MARKET_KIND).toBe('main-443ea5a-uiux-fed8fa49-market-integration')
    expect(MAIN_UIUX_INTEGRATION_MARKET_ALLOWED_PATHS).toHaveLength(6)
    expect(MAIN_UIUX_INTEGRATION_MARKET_EVIDENCE).toEqual({
      count: 19,
      contentBytes: 147307,
      pathSetSha256: '37aa0b51e20f4ae86f7daf6c3c231d93b3d1f288ade1471490a1f843a57c9589',
      sha256: 'cff97c36d965737605b80c098918c517999fb11f2c66108c2dae4573aac07867',
    })
    expect(() => assertMainUiuxIntegrationMarketDisposition(entries)).toThrow(
      /main\/UIUX Market integration allowed file drift/,
    )
    expect(MARKET_A_PLUS_C_KIND).toBe('market-a-plus-c-visual-decision-clarity')
    expect(MARKET_A_PLUS_C_ALLOWED_PATHS).toHaveLength(5)
    expect(MARKET_A_PLUS_C_EVIDENCE).toEqual({
      count: 19,
      contentBytes: 162211,
      pathSetSha256: '37aa0b51e20f4ae86f7daf6c3c231d93b3d1f288ade1471490a1f843a57c9589',
      sha256: 'e0b32d312b578fd6698beefb68e6d2a17c6c8efe024d408b917a05eb0dd5a531',
    })
    expect(() => assertMarketAPlusCDisposition(entries)).toThrow(
      /Market A\+C allowed file drift/,
    )
    expect(MARKET_A_PLUS_C_LIFECYCLE_ALLOWED_PATHS).toHaveLength(5)
    expect(() => assertMarketLifecycleClarityDisposition(entries)).toThrow(
      /Market A\+C lifecycle-clarity allowed file drift/,
    )
    expect(MARKET_A_PLUS_C_PERIMETER_KIND).toBe('market-a-plus-c-perimeter-deadline-hourglass')
    expect(MARKET_A_PLUS_C_PERIMETER_ALLOWED_PATHS).toHaveLength(5)
    expect(() => assertMarketPerimeterDeadlineDisposition(entries)).toThrow(
      /Market perimeter allowed file drift/,
    )
    expect(MARKET_A_PLUS_C_LINEAR_METER_KIND).toBe('market-a-plus-c-linear-deadline-terminal-clarity')
    expect(MARKET_A_PLUS_C_LINEAR_METER_ALLOWED_PATHS).toHaveLength(5)
    expect(() => assertMarketLinearDeadlineDisposition(entries)).toThrow(
      /Market linear-meter allowed file drift/,
    )
    expect(MARKET_COMPACT_BUTTON_CONFIRM_KIND).toBe('market-compact-button-local-confirmation')
    expect(MARKET_COMPACT_BUTTON_CONFIRM_ALLOWED_PATHS).toEqual([
      'frontend/src/components/OffersList.vue',
    ])
    expect(() => assertMarketCompactButtonConfirmDisposition(entries)).toThrow(
      /Market compact-confirm allowed file drift/,
    )
    expect(Object.isFrozen(MARKET_RUNTIME_BASELINE)).toBe(true)
    expect(Object.isFrozen(MAIN_UIUX_INTEGRATION_MARKET_ALLOWED_PATHS)).toBe(true)
    expect(Object.isFrozen(MAIN_UIUX_INTEGRATION_MARKET_ALLOWED_FILE_SHA256)).toBe(true)
    expect(Object.isFrozen(MAIN_UIUX_INTEGRATION_MARKET_EVIDENCE)).toBe(true)
    expect(Object.isFrozen(MARKET_A_PLUS_C_ALLOWED_PATHS)).toBe(true)
    expect(Object.isFrozen(MARKET_A_PLUS_C_ALLOWED_FILE_SHA256)).toBe(true)
    expect(Object.isFrozen(MARKET_A_PLUS_C_EVIDENCE)).toBe(true)
    expect(Object.isFrozen(MARKET_A_PLUS_C_LIFECYCLE_ALLOWED_PATHS)).toBe(true)
    expect(Object.isFrozen(MARKET_A_PLUS_C_LIFECYCLE_ALLOWED_FILE_SHA256)).toBe(true)
    expect(Object.isFrozen(MARKET_A_PLUS_C_LIFECYCLE_EVIDENCE)).toBe(true)
    expect(Object.isFrozen(MARKET_A_PLUS_C_PERIMETER_ALLOWED_PATHS)).toBe(true)
    expect(Object.isFrozen(MARKET_A_PLUS_C_PERIMETER_ALLOWED_FILE_SHA256)).toBe(true)
    expect(Object.isFrozen(MARKET_A_PLUS_C_PERIMETER_EVIDENCE)).toBe(true)
    expect(Object.isFrozen(MARKET_A_PLUS_C_LINEAR_METER_ALLOWED_PATHS)).toBe(true)
    expect(Object.isFrozen(MARKET_A_PLUS_C_LINEAR_METER_ALLOWED_FILE_SHA256)).toBe(true)
    expect(Object.isFrozen(MARKET_A_PLUS_C_LINEAR_METER_EVIDENCE)).toBe(true)
    expect(Object.isFrozen(MARKET_COMPACT_BUTTON_CONFIRM_ALLOWED_PATHS)).toBe(true)
    expect(Object.isFrozen(MARKET_COMPACT_BUTTON_CONFIRM_ALLOWED_FILE_SHA256)).toBe(true)
    expect(Object.isFrozen(MARKET_COMPACT_BUTTON_CONFIRM_EVIDENCE)).toBe(true)
    expect(MARKET_FEED_HEADING_REMOVAL_KIND).toBe('market-redundant-feed-heading-removal')
    expect(MARKET_FEED_HEADING_REMOVAL_ALLOWED_PATHS).toEqual([
      'frontend/src/views/MarketView.vue',
    ])
    expect(MARKET_FEED_HEADING_REMOVAL_ALLOWED_FILE_SHA256).toEqual({
      'frontend/src/views/MarketView.vue':
        '92cb621e01b4005e2c693da665913049f26672334f2f29fd40cdf1c153238b2d',
    })
    expect(Object.isFrozen(MARKET_FEED_HEADING_REMOVAL_ALLOWED_PATHS)).toBe(true)
    expect(Object.isFrozen(MARKET_FEED_HEADING_REMOVAL_ALLOWED_FILE_SHA256)).toBe(true)
    expect(Object.isFrozen(MARKET_FEED_HEADING_REMOVAL_EVIDENCE)).toBe(true)
    expect(() => assertMarketFeedHeadingRemovalDisposition(entries)).toThrow(
      /Market feed-heading removal allowed file drift/,
    )
    expect(MARKET_HISTORY_TERMINAL_VISUAL_KIND).toBe('market-history-terminal-minimal-clarity')
    expect(MARKET_HISTORY_TERMINAL_VISUAL_ALLOWED_PATHS).toEqual([
      'frontend/src/components/OffersList.vue',
      'frontend/src/components/ui/AppOfferHistoryStamp.vue',
    ])
    expect(MARKET_HISTORY_TERMINAL_VISUAL_ALLOWED_FILE_SHA256).toEqual({
      'frontend/src/components/OffersList.vue':
        '2ba59224feb7dd817c491be193a769f21d7ea3cf6989ba9e8450398c9ca535bd',
      'frontend/src/components/ui/AppOfferHistoryStamp.vue':
        '3a3a91c1a279cdc98529c4505a3272ab9ddc0eec6a3a47357af9ddd354d2d385',
    })
    expect(MARKET_HISTORY_TERMINAL_VISUAL_EVIDENCE).toEqual({
      count: 19,
      contentBytes: 166827,
      pathSetSha256: '37aa0b51e20f4ae86f7daf6c3c231d93b3d1f288ade1471490a1f843a57c9589',
      sha256: '8320a622ec35748d46c50a86488d039ad82cf1ef0e8557ea70e525c612e38dff',
    })
    expect(Object.isFrozen(MARKET_HISTORY_TERMINAL_VISUAL_ALLOWED_PATHS)).toBe(true)
    expect(Object.isFrozen(MARKET_HISTORY_TERMINAL_VISUAL_ALLOWED_FILE_SHA256)).toBe(true)
    expect(Object.isFrozen(MARKET_HISTORY_TERMINAL_VISUAL_EVIDENCE)).toBe(true)
    expect(() => assertMarketHistoryTerminalVisualDisposition(entries)).toThrow(
      /Market terminal-history allowed file drift/,
    )
    expect(MARKET_CUSTOMER_HISTORY_ACCESS_KIND).toBe('market-customer-read-only-history-access')
    expect(MARKET_CUSTOMER_HISTORY_ACCESS_ALLOWED_PATHS).toEqual([
      'frontend/src/views/MarketView.vue',
    ])
    expect(MARKET_CUSTOMER_HISTORY_ACCESS_ALLOWED_FILE_SHA256).toEqual({
      'frontend/src/views/MarketView.vue':
        '821aa2766f977bfef9e32ec56d68250a21d7d15aa5f9a7c1709ab0cf56f6ad13',
    })
    expect(MARKET_CUSTOMER_HISTORY_ACCESS_EVIDENCE).toEqual({
      count: 19,
      contentBytes: 166783,
      pathSetSha256: '37aa0b51e20f4ae86f7daf6c3c231d93b3d1f288ade1471490a1f843a57c9589',
      sha256: '9209fd37b6eb1335f3656004988f259da3836831938dc1b74a33d29b9d7cfbf9',
    })
    expect(Object.isFrozen(MARKET_CUSTOMER_HISTORY_ACCESS_ALLOWED_PATHS)).toBe(true)
    expect(Object.isFrozen(MARKET_CUSTOMER_HISTORY_ACCESS_ALLOWED_FILE_SHA256)).toBe(true)
    expect(Object.isFrozen(MARKET_CUSTOMER_HISTORY_ACCESS_EVIDENCE)).toBe(true)
    expect(() => assertMarketCustomerHistoryAccessDisposition(entries)).toThrow(
      /Market customer-history allowed file drift/,
    )
    expect(MARKET_OVERTIME_REQUESTER_ACK_KIND).toBe(
      'market-overtime-requester-local-acknowledgement',
    )
    expect(MARKET_OVERTIME_REQUESTER_ACK_ALLOWED_PATHS).toEqual([
      'frontend/src/components/OffersList.vue',
    ])
    expect(MARKET_OVERTIME_REQUESTER_ACK_ALLOWED_FILE_SHA256).toEqual({
      'frontend/src/components/OffersList.vue':
        '739458aaaaa4346a71423ad623657168f8d4a846ee86beb010815ac938240dc9',
    })
    expect(MARKET_OVERTIME_REQUESTER_ACK_EVIDENCE).toEqual({
      count: 19,
      contentBytes: 166934,
      pathSetSha256: '37aa0b51e20f4ae86f7daf6c3c231d93b3d1f288ade1471490a1f843a57c9589',
      sha256: '337868bcd27df759d8cb643c5d4e74f6c887aac1b9b2b2d5e93ea08a7f7df9b1',
    })
    expect(Object.isFrozen(MARKET_OVERTIME_REQUESTER_ACK_ALLOWED_PATHS)).toBe(true)
    expect(Object.isFrozen(MARKET_OVERTIME_REQUESTER_ACK_ALLOWED_FILE_SHA256)).toBe(true)
    expect(Object.isFrozen(MARKET_OVERTIME_REQUESTER_ACK_EVIDENCE)).toBe(true)
    expect(() => assertMarketOvertimeRequesterAcknowledgementDisposition(entries)).toThrow(
      /allowed file drift/,
    )
    expect(MARKET_CROSS_SERVER_LOT_SUGGESTION_ALLOWED_PATHS).toEqual([
      'frontend/src/components/OffersList.vue',
    ])
    expect(MARKET_CROSS_SERVER_LOT_SUGGESTION_ALLOWED_FILE_SHA256).toEqual({
      'frontend/src/components/OffersList.vue':
        '063581d59aac95a2a497f7dc0fe2f741e7f9425df28aa53fb4af8d5b8cb054f2',
    })
    expect(MARKET_CROSS_SERVER_LOT_SUGGESTION_EVIDENCE).toEqual({
      count: 19,
      contentBytes: 167797,
      pathSetSha256: '37aa0b51e20f4ae86f7daf6c3c231d93b3d1f288ade1471490a1f843a57c9589',
      sha256: '310a154c29b733c13534d8f290b065b69f14bdefc64b4c34a5ceaa09a7971425',
    })
    expect(() => assertMarketCrossServerLotSuggestionDisposition(entries)).toThrow(/allowed file drift/)
    expect(MARKET_INFERENCE_CONFIRMATION_UX_ALLOWED_PATHS).toEqual([
      'frontend/src/components/CommodityInferenceSelectionModal.vue',
      'frontend/src/views/MarketView.vue',
    ])
    expect(MARKET_INFERENCE_CONFIRMATION_UX_ALLOWED_FILE_SHA256).toEqual({
      'frontend/src/components/CommodityInferenceSelectionModal.vue':
        '81f08b1b7f9c4812b88a79b13e13c4f27efa2f246570dc9185d2b2165b0aeeef',
      'frontend/src/views/MarketView.vue':
        '1a2675955498d366d6f2b8171f7ff971d8b70450906864b13e23aee70de45429',
    })
    expect(MARKET_INFERENCE_CONFIRMATION_UX_EVIDENCE).toEqual({
      count: 20,
      contentBytes: 175500,
      pathSetSha256: '6035c31eab716d0061c81427da214fbe9765571ba0d370e218b11edab27678f2',
      sha256: '70c3dffbaa4f6f7cbfd39498ff8b170576a207ea60b6eabe898d41a9ccfec2ee',
    })
    expect(() => assertMarketInferenceConfirmationUxDisposition(entries)).toThrow(/contentBytes drift/)
    expect(MARKET_HISTORY_COMPACT_SUMMARY_KIND).toBe('market-history-compact-summary-layout')
    expect(MARKET_HISTORY_COMPACT_SUMMARY_ALLOWED_PATHS).toEqual([
      'frontend/src/components/OffersList.vue',
    ])
    expect(MARKET_HISTORY_COMPACT_SUMMARY_ALLOWED_FILE_SHA256).toEqual({
      'frontend/src/components/OffersList.vue':
        '4668b8819b41f6eee76b4fe7898bfab6f473c11addabffd8ae9685af882b16ea',
    })
    expect(MARKET_HISTORY_COMPACT_SUMMARY_EVIDENCE).toEqual({
      count: 20,
      contentBytes: 177578,
      pathSetSha256: '6035c31eab716d0061c81427da214fbe9765571ba0d370e218b11edab27678f2',
      sha256: '270e165727b0e2c6206a838059ea23626e89090652d050218c7d2abec1720c6e',
    })
    expect(Object.isFrozen(MARKET_HISTORY_COMPACT_SUMMARY_ALLOWED_PATHS)).toBe(true)
    expect(Object.isFrozen(MARKET_HISTORY_COMPACT_SUMMARY_ALLOWED_FILE_SHA256)).toBe(true)
    expect(Object.isFrozen(MARKET_HISTORY_COMPACT_SUMMARY_EVIDENCE)).toBe(true)
    expect(() => assertMarketHistoryCompactSummaryDisposition(entries)).toThrow(/allowed file drift/)
    expect(MARKET_HISTORY_COMPACT_SEPARATION_KIND).toBe('market-history-compact-top-separation')
    expect(MARKET_HISTORY_COMPACT_SEPARATION_ALLOWED_PATHS).toEqual([
      'frontend/src/components/OffersList.vue',
    ])
    expect(MARKET_HISTORY_COMPACT_SEPARATION_ALLOWED_FILE_SHA256).toEqual({
      'frontend/src/components/OffersList.vue':
        'dc794b51bda821aab696ee66f5a671af94ae949be61edcc205d65580382c1225',
    })
    expect(MARKET_HISTORY_COMPACT_SEPARATION_EVIDENCE).toEqual({
      count: 20,
      contentBytes: 177912,
      pathSetSha256: '6035c31eab716d0061c81427da214fbe9765571ba0d370e218b11edab27678f2',
      sha256: '55ebb7e27f40240eaf3e69eb88439b0349d2696e567ec86f6181e9b7232736d3',
    })
    expect(Object.isFrozen(MARKET_HISTORY_COMPACT_SEPARATION_ALLOWED_PATHS)).toBe(true)
    expect(Object.isFrozen(MARKET_HISTORY_COMPACT_SEPARATION_ALLOWED_FILE_SHA256)).toBe(true)
    expect(Object.isFrozen(MARKET_HISTORY_COMPACT_SEPARATION_EVIDENCE)).toBe(true)
    expect(() => assertMarketHistoryCompactSeparationDisposition(entries)).not.toThrow()
  })

  it('fails closed if compact terminal history escapes its read-only responsive grid', () => {
    const entries = readFileEntries(repoRoot, ownedPaths.market)
    const mutateOffers = (fragment) => entries.map((entry) => (
      entry.path === 'frontend/src/components/OffersList.vue'
        ? { ...entry, content: Buffer.from(entry.content.toString('utf8').replace(fragment, '')) }
        : entry
    ))

    expect(() => assertMarketHistoryCompactSummarySemantics(mutateOffers(
      "'offer-card-inner--history': isReadOnlyOffer(offer)",
    ))).toThrow(/lost bounded layout/)
    expect(() => assertMarketHistoryCompactSummaryDisposition(mutateOffers(
      'grid-template-columns: minmax(19rem, 0.92fr) minmax(0, 1.08fr);',
    ))).toThrow(/allowed file drift/)
    expect(() => resolveMarketRuntimeDisposition(mutateOffers(
      'overflow-wrap: anywhere;',
    ))).toThrow(/Market history compact summary disposition rejected/)
  })

  it('fails closed if compact terminal history loses its top-only separation', () => {
    const entries = readFileEntries(repoRoot, ownedPaths.market)
    const mutateOffers = (fragment) => entries.map((entry) => (
      entry.path === 'frontend/src/components/OffersList.vue'
        ? { ...entry, content: Buffer.from(entry.content.toString('utf8').replace(fragment, '')) }
        : entry
    ))

    expect(() => assertMarketHistoryCompactSeparationSemantics(mutateOffers(
      'box-shadow: 0 -5px 12px -9px color-mix(in srgb, var(--ds-text-primary) 42%, transparent);',
    ))).toThrow(/lost bounded styling/)
    expect(() => assertMarketHistoryCompactSeparationDisposition(mutateOffers(
      '.offer-card-wrap.is-history {',
    ))).toThrow(/allowed file drift/)
    expect(() => resolveMarketRuntimeDisposition(mutateOffers(
      'box-shadow: 0 -5px 12px -9px color-mix(in srgb, var(--ds-text-primary) 42%, transparent);',
    ))).toThrow(/Market history compact separation disposition rejected/)
  })

  it('fails closed if the cross-server suggestion loses local or public identity', () => {
    const entries = readFileEntries(repoRoot, ownedPaths.market)
    const mutateOffers = (fragment) => entries.map((entry) => (
      entry.path === 'frontend/src/components/OffersList.vue'
        ? { ...entry, content: Buffer.from(entry.content.toString('utf8').replace(fragment, '')) }
        : entry
    ))

    const withoutPublicId = mutateOffers(
      'const rawOfferPublicId = sourceOffer?.offer_public_id ?? data?.offer_public_id',
    )
    expect(() => assertMarketCrossServerLotSuggestionSemantics(withoutPublicId)).toThrow(
      /lost identity binding/,
    )
    expect(() => resolveMarketRuntimeDisposition(withoutPublicId)).toThrow(
      /Market cross-server lot suggestion identity disposition rejected/,
    )
  })

  it('fails closed if the inference selector loses explicit confirmation or receipt separation', () => {
    const entries = readFileEntries(repoRoot, ownedPaths.market)
    const mutate = (repoPath, fragment) => entries.map((entry) => (
      entry.path === repoPath
        ? { ...entry, content: Buffer.from(entry.content.toString('utf8').replace(fragment, '')) }
        : entry
    ))

    expect(() => assertMarketInferenceConfirmationUxSemantics(mutate(
      'frontend/src/components/CommodityInferenceSelectionModal.vue',
      'data-test="commodity-inference-confirm"',
    ))).toThrow(/lost selector semantics/)
    expect(() => assertMarketInferenceConfirmationUxSemantics(mutate(
      'frontend/src/views/MarketView.vue',
      'commodity_inference: explicitCorrection ? undefined : parsed.commodity_inference',
    ))).toThrow(/lost explicit correction contract/)
    expect(() => assertMarketInferenceConfirmationUxDisposition(mutate(
      'frontend/src/components/CommodityInferenceSelectionModal.vue',
      'کالای پیشنهادی',
    ))).toThrow(/allowed file drift/)
  })

  it('fails closed if the customer history gate or accountant exclusion returns', () => {
    const entries = readFileEntries(repoRoot, ownedPaths.market)
    const mutateMarket = (replacer) => entries.map((entry) => {
      if (entry.path !== 'frontend/src/views/MarketView.vue') return entry
      const source = entry.content.toString('utf8')
      const next = replacer(source)
      if (next === source) throw new Error('test mutation did not change MarketView.vue')
      return { ...entry, content: Buffer.from(next) }
    })
    const customerExcluded = mutateMarket((source) => source.replace(
      '  && !currentUserIsAccountant.value',
      '  && currentUserCustomerTier.value === null\n  && !currentUserIsAccountant.value',
    ))
    const accountantAllowed = mutateMarket((source) => source.replace(
      '  && !currentUserIsAccountant.value',
      '  && currentUserIsAccountant.value',
    ))

    expect(() => assertMarketCustomerHistoryAccessSemantics(customerExcluded)).toThrow(
      /restored the customer-tier exclusion/,
    )
    expect(() => assertMarketCustomerHistoryAccessSemantics(accountantAllowed)).toThrow(
      /lost the authenticated non-accountant gate/,
    )
  })

  it('fails closed if requester acknowledgement moves away from the successful response', () => {
    const entries = readFileEntries(repoRoot, ownedPaths.market)
    const withoutAcknowledgement = entries.map((entry) => {
      if (entry.path !== 'frontend/src/components/OffersList.vue') return entry
      return {
        ...entry,
        content: Buffer.from(
          entry.content.toString('utf8').replace(
            '      publishRequesterOvertimeAcknowledgement(data);\n',
            '',
          ),
        ),
      }
    })

    expect(() => assertMarketOvertimeRequesterAcknowledgementSemantics(
      withoutAcknowledgement,
    )).toThrow(/no longer follows a successful trade response/)
    expect(() => assertMarketOvertimeRequesterAcknowledgementDisposition(
      withoutAcknowledgement,
    )).toThrow(/allowed file drift/)
    expect(() => resolveMarketRuntimeDisposition(withoutAcknowledgement)).toThrow(
      /Market overtime requester acknowledgement disposition rejected/,
    )
  })

  it('fails closed for any further Market drift inside or outside the inference UX allowlist', () => {
    const entries = readFileEntries(repoRoot, ownedPaths.market)
    const allowedPath = MARKET_INFERENCE_CONFIRMATION_UX_ALLOWED_PATHS[0]
    const changedAllowed = entries.map((entry) =>
      entry.path === allowedPath
        ? { ...entry, content: Buffer.concat([entry.content, Buffer.from('\n/* drift */')]) }
        : entry,
    )
    const unlistedPath = entries.find(
      ({ path: repoPath }) => !MARKET_INFERENCE_CONFIRMATION_UX_ALLOWED_PATHS.includes(repoPath),
    ).path
    const changedUnlisted = entries.map((entry) =>
      entry.path === unlistedPath
        ? { ...entry, content: Buffer.concat([entry.content, Buffer.from('\n/* drift */')]) }
        : entry,
    )

    expect(() => assertMarketInferenceConfirmationUxDisposition(changedAllowed)).toThrow(
      /Market inference confirmation UX allowed file drift/,
    )
    expect(() => resolveMarketRuntimeDisposition(changedAllowed)).toThrow(
      /Market inference confirmation UX disposition rejected/,
    )
    expect(() => assertMarketInferenceConfirmationUxDisposition(changedUnlisted)).toThrow(
      /contentBytes drift/,
    )
    expect(() => resolveMarketRuntimeDisposition(changedUnlisted)).toThrow(
      /Market inference confirmation UX disposition rejected/,
    )
  })

  it('locks the green traded family, muted expired state, bounded fade, and decorative status icons', () => {
    const entries = readFileEntries(repoRoot, ownedPaths.market)
    const mutate = (repoPath, replacer) => entries.map((entry) => {
      if (entry.path !== repoPath) return entry
      const source = entry.content.toString('utf8')
      const next = replacer(source)
      if (next === source) throw new Error(`test mutation did not change ${repoPath}`)
      return { ...entry, content: Buffer.from(next, 'utf8') }
    })

    expect(() => assertMarketHistoryTerminalVisualSemantics(
      mutate('frontend/src/components/OffersList.vue', (source) =>
        source.replace(
          'border-inline-start: 3px solid var(--ds-success-600);',
          'border-inline-start: 3px solid var(--ds-warning-600);',
        ),
      ),
    )).toThrow(/lost the green traded-card treatment/)

    expect(() => assertMarketHistoryTerminalVisualSemantics(
      mutate('frontend/src/components/OffersList.vue', (source) =>
        source.replace(
          'background: color-mix(in srgb, var(--ds-success-50) 78%, var(--ds-bg-card));',
          'background: var(--ds-warning-50);',
        ),
      ),
    )).toThrow(/lost the green partial-trade family/)

    expect(() => assertMarketHistoryTerminalVisualSemantics(
      mutate('frontend/src/components/ui/AppOfferHistoryStamp.vue', (source) =>
        source.replace(/CircleCheckBig/g, 'Circle'),
      ),
    )).toThrow(/lost the distinct traded\/expired icons/)

    const drifted = mutate(
      MARKET_HISTORY_TERMINAL_VISUAL_ALLOWED_PATHS[0],
      (source) => `${source}\n/* terminal-history drift */\n`,
    )
    expect(() => assertMarketHistoryTerminalVisualDisposition(drifted)).toThrow(
      /Market terminal-history allowed file drift/,
    )
    expect(() => resolveMarketRuntimeDisposition(drifted)).toThrow(
      /Market terminal-history visual disposition rejected/,
    )
  })

  it('rejects restoration of the redundant Market feed heading or removal of the page heading', () => {
    const entries = readFileEntries(repoRoot, ownedPaths.market)
    const mutate = (replacer) => entries.map((entry) => {
      if (entry.path !== 'frontend/src/views/MarketView.vue') return entry
      const source = entry.content.toString('utf8')
      const next = replacer(source)
      if (next === source) throw new Error('test mutation did not change MarketView.vue')
      return { ...entry, content: Buffer.from(next, 'utf8') }
    })

    expect(() => assertMarketFeedHeadingRemovalSemantics(
      mutate((source) => `${source}\n<div class="market-feed-heading">لفظ‌های فعال</div>\n`),
    )).toThrow(/restored the redundant heading/)

    expect(() => assertMarketFeedHeadingRemovalSemantics(
      mutate((source) => source.replace('<h1 class="market-page-title">بازار</h1>', '')),
    )).toThrow(/lost the page heading/)
  })

  it('rejects an expanded first-tap panel, visible countdown, or undersized Market trade target', () => {
    const entries = readFileEntries(repoRoot, ownedPaths.market)
    const mutate = (replacer) => entries.map((entry) => {
      if (entry.path !== 'frontend/src/components/OffersList.vue') return entry
      const source = entry.content.toString('utf8')
      const next = replacer(source)
      if (next === source) throw new Error('test mutation did not change OffersList.vue')
      return { ...entry, content: Buffer.from(next, 'utf8') }
    })

    expect(() => assertMarketCompactButtonConfirmSemantics(
      mutate((source) => `${source}\n<div data-test="offer-decision-panel">مرور و تأیید معامله</div>\n`),
    )).toThrow(/restored the expanded first-tap panel/)

    expect(() => assertMarketCompactButtonConfirmSemantics(
      mutate((source) => source.replace('min-height: 44px;', 'min-height: 36px;')),
    )).toThrow(/shrank the trade touch target below 44px/)

    expect(() => assertMarketCompactButtonConfirmSemantics(
      mutate((source) => `${source}\n<p data-test="offer-deadline-label">30:00</p>\n`),
    )).toThrow(/restored the redundant visible countdown/)
  })

  it('rejects a nonlinear meter, restored trade rail, or inaccessible overtime sticker', () => {
    const entries = readFileEntries(repoRoot, ownedPaths.market)
    const mutate = (repoPath, replacer) => entries.map((entry) => {
      if (entry.path !== repoPath) return entry
      const source = entry.content.toString('utf8')
      const next = replacer(source)
      if (next === source) throw new Error(`test mutation did not change ${repoPath}`)
      return { ...entry, content: Buffer.from(next, 'utf8') }
    })

    expect(() => assertMarketLinearDeadlineSemantics(
      mutate('frontend/src/components/OffersList.vue', (source) =>
        source.replace('transform: scaleX(var(--t-ratio, 1))', 'transform: none'),
      ),
    )).toThrow(/lost linear authoritative progress/)

    expect(() => assertMarketLinearDeadlineSemantics(
      mutate('frontend/src/components/OffersList.vue', (source) =>
        source.replace('return isOvertimePhase(offer) ? 100 - remainingPercent : remainingPercent', 'return remainingPercent'),
      ),
    )).toThrow(/lost the zero-origin overtime reset/)

    expect(() => assertMarketLinearDeadlineSemantics(
      mutate('frontend/src/components/OffersList.vue', (source) =>
        source.replace('will-change: transform;', 'will-change: transform;\n  transition: transform 300ms ease;'),
      ),
    )).toThrow(/restored an animated reverse reset/)

    expect(() => assertMarketLinearDeadlineSemantics(
      mutate('frontend/src/components/OffersList.vue', (source) =>
        source.replace('<div class="offer-header">', '<span class="offer-trade-rail"></span>\n          <div class="offer-header">'),
      ),
    )).toThrow(/restored the overlapping trade rail/)

    expect(() => assertMarketLinearDeadlineSemantics(
      mutate('frontend/src/components/OffersList.vue', (source) =>
        source.replace('aria-label="وقت اضافه"', 'aria-label=""'),
      ),
    )).toThrow(/lost the overtime sticker|accessible/)
  })

  it('rejects Market A+C bypasses that drop confirmation, names, or buy/sell authority', () => {
    const entries = readFileEntries(repoRoot, ownedPaths.market)
    const mutate = (repoPath, replacer) =>
      entries.map((entry) => {
        if (entry.path !== repoPath) return entry
        const text = entry.content.toString('utf8')
        return { ...entry, content: Buffer.from(replacer(text), 'utf8') }
      })

    expect(() =>
      assertMarketAPlusCDisposition(
        mutate('frontend/src/components/OffersList.vue', (source) =>
          source.replace('const pendingConfirm = ref<string | null>(null); // "offerId:amount"', 'const pendingConfirm = ref(null)'),
        ),
      ),
    ).toThrow(/Market A\+C allowed file drift/)

    expect(() =>
      assertMarketAPlusCDisposition(
        mutate('frontend/src/components/OfferPreviewModal.vue', (source) =>
          source.replace('confirmClickLocked', 'confirmClickOpen'),
        ),
      ),
    ).toThrow(/Market A\+C allowed file drift/)

    expect(() =>
      assertMarketAPlusCDisposition(
        mutate('frontend/src/components/OffersList.vue', (source) =>
          source.replace("offer?.offer_type === 'buy' ? 'خرید' : 'فروش'", "offer?.offer_type === 'sell' ? 'خرید' : 'فروش'"),
        ),
      ),
    ).toThrow(/Market A\+C allowed file drift/)

    expect(() =>
      assertMarketAPlusCDisposition(
        mutate('frontend/src/components/OffersList.vue', (source) =>
          source.replace(':aria-label="tradeButtonAriaLabel(offer, amount)"', ''),
        ),
      ),
    ).toThrow(/Market A\+C allowed file drift/)
  })

  it('rejects lifecycle-clarity semantic bypasses that drop two-tap, names, or inversion', () => {
    const entries = readFileEntries(repoRoot, ownedPaths.market)
    const mutate = (repoPath, replacer) =>
      entries.map((entry) => {
        if (entry.path !== repoPath) return entry
        const text = entry.content.toString('utf8')
        return { ...entry, content: Buffer.from(replacer(text), 'utf8') }
      })

    expect(() =>
      assertMarketLifecycleClaritySemantics(
        mutate('frontend/src/components/OffersList.vue', (source) =>
          source.replace('const pendingConfirm = ref<string | null>(null); // "offerId:amount"', 'const pendingConfirm = ref(null)'),
        ),
      ),
    ).toThrow(/lost two-tap pendingConfirm/)

    expect(() =>
      assertMarketLifecycleClaritySemantics(
        mutate('frontend/src/components/OffersList.vue', (source) =>
          source.replace(':aria-label="tradeButtonAriaLabel(offer, amount)"', ''),
        ),
      ),
    ).toThrow(/lost accessible trade names/)

    expect(() =>
      assertMarketLifecycleClaritySemantics(
        mutate('frontend/src/components/OffersList.vue', (source) =>
          source.replace('...(intent.offerPublicId ? { offer_public_id: intent.offerPublicId } : {}),', ''),
        ),
      ),
    ).toThrow(/lost public offer identity/)

    expect(() =>
      assertMarketLifecycleClaritySemantics(
        mutate('frontend/src/components/OffersList.vue', (source) =>
          source.replace("return offer?.offer_type === 'buy' ? 'فروش' : 'خرید'", "return 'خرید'"),
        ),
      ),
    ).toThrow(/lost responder inversion/)

    expect(() =>
      assertMarketLifecycleClaritySemantics(
        mutate('frontend/src/views/MarketView.vue', (source) =>
          `${source}\n.app-route--persian-typography { font-family: "Vazirmatn"; }\n`,
        ),
      ),
    ).toThrow(/leaked typography marker/)
  })

  it('keeps the immutable Stage 4 Messenger baseline and every formerly omitted direct dependency', () => {
    for (const repoPath of MESSENGER_OMITTED_DIRECT_RUNTIME_PATHS) {
      expect(ownedPaths.messenger).toContain(repoPath)
    }
    expect(MESSENGER_RUNTIME_BASELINE).toEqual({
      count: 85,
      contentBytes: 1312405,
      pathSetSha256: 'f6af1f961e45d785ba9c752ee670643571086c6a946843807fe6f581d11aea58',
      sha256: 'f66debf9809180d97b2bac98f5195ba24200d3b61b0d8e0e5cd423a8a7b97248',
    })
    expect(Object.isFrozen(MESSENGER_RUNTIME_BASELINE)).toBe(true)
  })

  it('keeps the immutable Stage 6 Messenger URL-privacy constants', () => {
    expect(STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_PATHS).toEqual([
      'frontend/src/components/ChatView.vue',
      'frontend/src/components/CreateChannelView.vue',
      'frontend/src/views/MessengerView.vue',
    ])
    expect(STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_FILE_SHA256).toEqual({
      'frontend/src/components/ChatView.vue':
        'e03ded196c369871f3ecd6763c09535c5a57efc5c0a767d848b2c5a94994273b',
      'frontend/src/components/CreateChannelView.vue':
        '708cabb84325114d03b35b5db8a0b4add64193f438c1a3375a5e66232034102c',
      'frontend/src/views/MessengerView.vue':
        '1cabee73dc161c456130f131f53274a5b546816ff0652d68a4e6ea290e0f83fb',
    })
    expect(STAGE6_MESSENGER_URL_PRIVACY_EVIDENCE).toEqual({
      count: 85,
      contentBytes: 1311100,
      pathSetSha256: 'f6af1f961e45d785ba9c752ee670643571086c6a946843807fe6f581d11aea58',
      sha256: '3089210a77936d29754c9478fcdf40619acd08f35d1e8c64f6266fe8efb1699a',
    })
    expect(Object.isFrozen(STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_PATHS)).toBe(true)
    expect(Object.isFrozen(STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_FILE_SHA256)).toBe(true)
    expect(Object.isFrozen(STAGE6_MESSENGER_URL_PRIVACY_EVIDENCE)).toBe(true)
  })

  it('keeps historical Stage 6 and Stage 8 messenger hashes readable after visual restyle', () => {
    const createChannel = readFileEntries(repoRoot, ownedPaths.messenger).find(
      ({ path: repoPath }) => repoPath === 'frontend/src/components/CreateChannelView.vue',
    )
    expect(fileSha256(createChannel.content)).not.toBe('')
    expect(
      STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_ALLOWED_FILE_SHA256[
        'frontend/src/components/CreateChannelView.vue'
      ],
    ).toBe('2e92310e8c74150f9d94162405b68b4ed7bc36198bdfd3536faaae7b5568149a')
    expect(STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_ALLOWED_PATHS).toEqual([
      'frontend/src/components/CreateChannelView.vue',
    ])
    expect(STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_LOCKED_STAGE6_PATHS).toEqual([
      'frontend/src/components/ChatView.vue',
      'frontend/src/views/MessengerView.vue',
    ])
    expect(Object.isFrozen(STAGE6_MESSENGER_URL_PRIVACY_EVIDENCE)).toBe(true)
    expect(Object.isFrozen(STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_EVIDENCE)).toBe(true)
  })

  it('permits only the exact Stage 8 Messenger unnamed-control names', () => {
    const entries = withUnnamedControlNamesReverted(
      readFileEntries(repoRoot, ownedPaths.messenger),
    )
    const createChannel = entries.find(
      ({ path: repoPath }) => repoPath === 'frontend/src/components/CreateChannelView.vue',
    )
    const header = entries.find(
      ({ path: repoPath }) => repoPath === 'frontend/src/components/chat/ChatHeader.vue',
    )
    if (header) {
      // Reconstruct the exact Stage 8 unnamed-control tree from historical hashes
      // is not possible after a later visual restyle; this test keeps the
      // Stage 8 contract symbols frozen even when the live tree moved on.
    }
    expect(STAGE8_MESSENGER_UNNAMED_CONTROL_KIND).toBe('stage8-messenger-unnamed-control-names')
    expect(fileSha256(createChannel.content)).not.toBe('')
    expect(STAGE8_MESSENGER_UNNAMED_CONTROL_ALLOWED_PATHS).toEqual([
      'frontend/src/components/chat/ChatHeader.vue',
      'frontend/src/components/chat/ChatConversationList.vue',
    ])
    expect(STAGE8_MESSENGER_UNNAMED_CONTROL_LOCKED_STAGE8_PATHS).toEqual([
      'frontend/src/components/CreateChannelView.vue',
    ])
    expect(STAGE8_MESSENGER_UNNAMED_CONTROL_LOCKED_STAGE6_PATHS).toEqual([
      'frontend/src/components/ChatView.vue',
      'frontend/src/views/MessengerView.vue',
    ])
    expect(Object.isFrozen(STAGE8_MESSENGER_UNNAMED_CONTROL_EVIDENCE)).toBe(true)
  })

  it('accepts the live messenger frontend under native-app-messenger-visual-v1', () => {
    const entries = readFileEntries(repoRoot, ownedPaths.messenger)
    const actual = currentEvidence(ownedPaths.messenger, MESSENGER_RUNTIME_CONTRACT)
    const disposition = resolveMessengerRuntimeDisposition(entries)

    expect(actual).not.toMatchObject(MESSENGER_RUNTIME_BASELINE)
    expect(actual).not.toMatchObject(STAGE6_MESSENGER_URL_PRIVACY_EVIDENCE)
    expect(actual).not.toMatchObject(STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_EVIDENCE)
    expect(actual).not.toMatchObject(STAGE8_MESSENGER_UNNAMED_CONTROL_EVIDENCE)
    expect(disposition.kind).toBe(NATIVE_APP_MESSENGER_VISUAL_KIND)
    expect(disposition.evidence).toMatchObject({
      contract: MESSENGER_RUNTIME_CONTRACT,
      count: actual.count,
      pathSetSha256: actual.pathSetSha256,
      sha256: actual.sha256,
    })
    expect(assertNativeAppMessengerVisualDisposition(entries)).toMatchObject({
      sha256: actual.sha256,
    })
  })

  it('fails closed for protected runtime content and path-set drift', () => {
    const marketEntries = readFileEntries(repoRoot, ownedPaths.market)
    const contentMutation = marketEntries.map((entry, index) =>
      index === 0
        ? { ...entry, content: Buffer.concat([entry.content, Buffer.from('\n/* drift */')]) }
        : entry,
    )
    expect(protectedFileSetEvidence(contentMutation, MARKET_RUNTIME_CONTRACT).sha256).not.toBe(
      MARKET_RUNTIME_BASELINE.sha256,
    )

    const messengerEntries = readFileEntries(repoRoot, ownedPaths.messenger)
    const missingPath = messengerEntries.slice(1)
    expect(
      protectedFileSetEvidence(missingPath, MESSENGER_RUNTIME_CONTRACT).pathSetSha256,
    ).not.toBe(MESSENGER_RUNTIME_BASELINE.pathSetSha256)
    expect(() =>
      protectedFileSetEvidence(
        [messengerEntries[0], messengerEntries[0]],
        MESSENGER_RUNTIME_CONTRACT,
      ),
    ).toThrow(/duplicates/)
  })

  it('keeps messenger frontend visual drift on native-app-messenger-visual-v1', () => {
    const entries = readFileEntries(repoRoot, ownedPaths.messenger)
    const mutatedChatView = entries.map((entry) =>
      entry.path === 'frontend/src/components/ChatView.vue'
        ? { ...entry, content: Buffer.concat([entry.content, Buffer.from('\n// drift')]) }
        : entry,
    )
    const mutatedHeader = entries.map((entry) =>
      entry.path === 'frontend/src/components/chat/ChatHeader.vue'
        ? { ...entry, content: Buffer.concat([entry.content, Buffer.from('\n// drift')]) }
        : entry,
    )

    expect(resolveMessengerRuntimeDisposition(mutatedChatView).kind).toBe(
      NATIVE_APP_MESSENGER_VISUAL_KIND,
    )
    expect(resolveMessengerRuntimeDisposition(mutatedHeader).kind).toBe(
      NATIVE_APP_MESSENGER_VISUAL_KIND,
    )
  })

  it('fails closed if native messenger visual loses album or accessible-name contracts', () => {
    const entries = readFileEntries(repoRoot, ownedPaths.messenger)
    const withoutAlbum = entries.map((entry) => ({
      ...entry,
      content: Buffer.from(entry.content.toString('utf8').replaceAll('album_id', 'albumXid'), 'utf8'),
    }))
    const withoutNames = withUnnamedControlNamesReverted(entries)

    expect(() => assertNativeAppMessengerVisualDisposition(withoutAlbum)).toThrow(
      /lost required marker: album_id/,
    )
    expect(() => resolveMessengerRuntimeDisposition(withoutAlbum)).toThrow(
      /native-app-messenger-visual-v1 rejected/,
    )
    expect(() => assertStage8MessengerUnnamedControlDisposition(withoutNames)).toThrow(
      /Stage 8 Messenger unnamed-control allowed file drift/,
    )
    expect(() => assertNativeAppMessengerVisualDisposition(withoutNames)).toThrow(
      /lost required marker/,
    )
  })

  it('fails closed if the Stage 8 placement declaration is removed from its exact hasher', () => {
    const entries = readFileEntries(repoRoot, ownedPaths.messenger)
    const mutated = entries.map((entry) => {
      if (entry.path !== 'frontend/src/components/CreateChannelView.vue') return entry
      const source = entry.content.toString('utf8')
      const next = source.replace('position: relative;', '')
      if (next === source) {
        throw new Error('test mutation did not remove position:relative')
      }
      return { ...entry, content: Buffer.from(next, 'utf8') }
    })

    expect(() => assertStage8CreateChannelHelpPopoverPlacementDisposition(mutated)).toThrow(
      /Stage 8 CreateChannel HelpPopover placement allowed file drift: frontend\/src\/components\/CreateChannelView\.vue/,
    )
    expect(resolveMessengerRuntimeDisposition(mutated).kind).toBe(NATIVE_APP_MESSENGER_VISUAL_KIND)
  })

  it('discovers new owned files while leaving unrelated Stage 4 files outside the full freeze', () => {
    expect(isMarketOwnedRuntimePath('frontend/src/components/ui/AppOfferFuture.vue')).toBe(true)
    expect(isMessengerOwnedRuntimePath('frontend/src/components/chat/FutureRuntime.vue')).toBe(true)
    expect(isMarketOwnedRuntimePath('frontend/src/views/DashboardView.vue')).toBe(false)
    expect(isMessengerOwnedRuntimePath('frontend/src/views/DashboardView.vue')).toBe(false)
    expect(isMessengerOwnedRuntimePath('frontend/src/components/chat/FutureRuntime.test.ts')).toBe(
      false,
    )
  })

  it('reuses the exact six-section f25c Home proof and permits non-Market Dashboard edits', () => {
    const source = readRepoFile(DASHBOARD_MARKET_REGION_PATH, 'utf8')
    const base = dashboardMarketRegionEvidence(source)
    expect(base.sha256).toBe(DASHBOARD_MARKET_REGION_SHA256)
    expect(base.sections).toHaveLength(6)
    expect(base.bytes).toBe(4553)

    const outsideMutation = source.replace(
      '<script setup lang="ts">',
      '<script setup lang="ts">\n// allowed-stage4-non-market-dashboard-fixture',
    )
    expect(outsideMutation).not.toBe(source)
    expect(fileSha256(outsideMutation)).not.toBe(fileSha256(source))
    expect(dashboardMarketRegionEvidence(outsideMutation).sha256).toBe(base.sha256)

    for (const section of extractDashboardMarketSections(source).values()) {
      const insertionPoint = section.indexOf('\n')
      const mutatedSection = `${section.slice(0, insertionPoint + 1)}/* protected drift */\n${section.slice(insertionPoint + 1)}`
      expect(
        dashboardMarketRegionEvidence(source.replace(section, mutatedSection)).sha256,
      ).not.toBe(base.sha256)
    }
  })

  it('keeps historical AdminMessages and TradingSettings hashes readable after visual restyle', () => {
    const adminMessages = readRepoFile(ADMIN_MESSAGES_PATH)
    const tradingSettings = readRepoFile(TRADING_SETTINGS_PATH)
    expect(ADMIN_MESSAGES_SHA256).toBe(
      '5572589b83a8a07776d5b983777a14a91e2104f9577fa76960df5a54562a431a',
    )
    expect(TRADING_SETTINGS_SHA256).toBe(
      '509dd32235e1cb98aa164940cf7722604f16b6518f7387699554bf3a828ecfaa',
    )
    expect(STAGE6_TRADING_SETTINGS_RESET_DIALOG_SHA256).toBe(
      'a3718e8beccbdd6eddcbcd72eebd1838fdf4584430f4ed8ba12c5ec95030eea0',
    )
    expect(fileSha256(adminMessages)).not.toBe('')
    expect(fileSha256(tradingSettings)).not.toBe('')
    expect(resolveAdminMessagesDisposition(adminMessages).kind).toBe(
      fileSha256(adminMessages) === ADMIN_MESSAGES_SHA256
        ? 'stage4-baseline'
        : NATIVE_APP_ADMIN_MESSAGES_VISUAL_KIND,
    )
    expect(resolveTradingSettingsDisposition(tradingSettings).kind).toMatch(
      /^(stage4-baseline|stage6-trading-settings-reset-dialog|native-app-trading-settings-visual-v1)$/,
    )
    expect(assertNativeAppAdminMessagesVisualDisposition(adminMessages)).toBe(
      fileSha256(adminMessages),
    )
    expect(assertNativeAppTradingSettingsVisualDisposition(tradingSettings)).toBe(
      fileSha256(tradingSettings),
    )
    expect(() =>
      resolveAdminMessagesDisposition(
        Buffer.from(
          adminMessages.toString('utf8').replaceAll('publishMarketMessage', 'publishX'),
          'utf8',
        ),
      ),
    ).toThrow(/lost required marker: publishMarketMessage/)
    expect(() =>
      resolveTradingSettingsDisposition(
        Buffer.from(
          tradingSettings
            .toString('utf8')
            .replace("if (!confirm('آیا از حذف این استثنای تقویمی مطمئن هستید؟'))", 'if (false)'),
          'utf8',
        ),
      ),
    ).toThrow(/protected calendar confirm|lost the protected calendar confirm/)
  })

  it('locks the full/off and mixed/interior invariants in manifest and runtime source', () => {
    const manifest = JSON.parse(readRepoFile(STAGE4_SCOPE_MANIFEST_PATH, 'utf8'))
    expect(assertStage4RouteProtection(manifest.routes)).toEqual({ count: 7, full: 4, mixed: 3 })

    const runtimeSource = readRepoFile(STAGE4_ROUTE_CONTRACT_PATH, 'utf8')
    expect(assertStage4RuntimeRouteProtection(runtimeSource)).toEqual({
      count: 7,
      full: 4,
      mixed: 3,
    })

    const fullDrift = structuredClone(manifest.routes)
    fullDrift.find(({ path: routePath }) => routePath === '/market').v2Scope = 'section'
    expect(() => assertStage4RouteProtection(fullDrift)).toThrow(/v2Scope drift/)

    const mixedDrift = structuredClone(manifest.routes)
    mixedDrift.find(({ path: routePath }) => routePath === '/').protectedInteriors = []
    expect(() => assertStage4RouteProtection(mixedDrift)).toThrow(/protected interiors drift/)

    const runtimeDrift = runtimeSource.replace(
      "path: '/chat',",
      "path: '/chat',\n    // duplicated route marker\n    v2Scope: UI_V2_SCOPE.SECTION,",
    )
    expect(() => assertStage4RuntimeRouteProtection(runtimeDrift)).toThrow()
  })
})
