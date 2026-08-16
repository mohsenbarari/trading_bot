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
  MARKET_FEED_HEADING_REMOVAL_ALLOWED_FILE_SHA256,
  MARKET_FEED_HEADING_REMOVAL_ALLOWED_PATHS,
  MARKET_FEED_HEADING_REMOVAL_EVIDENCE,
  MARKET_FEED_HEADING_REMOVAL_KIND,
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
  assertStage8CreateChannelHelpPopoverPlacementDisposition,
  assertStage8MessengerUnnamedControlDisposition,
  assertMainUiuxIntegrationMarketDisposition,
  assertMarketAPlusCDisposition,
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
  STAGE4_BASE_COMMIT,
  STAGE4_BASE_TREE,
  STAGE4_ROUTE_CONTRACT_PATH,
  STAGE4_SHARED_DEPENDENCY_ISOLATION_PATHS,
  STAGE4_SCOPE_MANIFEST_PATH,
  STAGE6_TRADING_SETTINGS_RESET_DIALOG_KIND,
  STAGE6_TRADING_SETTINGS_RESET_DIALOG_SHA256,
  TRADING_SETTINGS_PATH,
  TRADING_SETTINGS_SHA256,
  assertStage6TradingSettingsResetDialogDisposition,
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

  it('keeps every prior Market disposition immutable while admitting the redundant feed-heading removal', () => {
    expect(ownedPaths.market).toContain('frontend/src/utils/settlementType.ts')
    const entries = readFileEntries(repoRoot, ownedPaths.market)
    expect(resolveMarketRuntimeDisposition(entries)).toMatchObject({
      kind: MARKET_FEED_HEADING_REMOVAL_KIND,
      evidence: MARKET_FEED_HEADING_REMOVAL_EVIDENCE,
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
    for (const repoPath of MARKET_COMPACT_BUTTON_CONFIRM_ALLOWED_PATHS) {
      const entry = entries.find(({ path: candidate }) => candidate === repoPath)
      expect(fileSha256(entry.content)).toBe(MARKET_COMPACT_BUTTON_CONFIRM_ALLOWED_FILE_SHA256[repoPath])
    }
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
    for (const repoPath of MARKET_FEED_HEADING_REMOVAL_ALLOWED_PATHS) {
      const entry = entries.find(({ path: candidate }) => candidate === repoPath)
      expect(fileSha256(entry.content)).toBe(MARKET_FEED_HEADING_REMOVAL_ALLOWED_FILE_SHA256[repoPath])
    }
    expect(Object.isFrozen(MARKET_FEED_HEADING_REMOVAL_ALLOWED_PATHS)).toBe(true)
    expect(Object.isFrozen(MARKET_FEED_HEADING_REMOVAL_ALLOWED_FILE_SHA256)).toBe(true)
    expect(Object.isFrozen(MARKET_FEED_HEADING_REMOVAL_EVIDENCE)).toBe(true)
  })

  it('fails closed for any further Market drift inside or outside the feed-heading allowlist', () => {
    const entries = readFileEntries(repoRoot, ownedPaths.market)
    const allowedPath = MARKET_FEED_HEADING_REMOVAL_ALLOWED_PATHS[0]
    const changedAllowed = entries.map((entry) =>
      entry.path === allowedPath
        ? { ...entry, content: Buffer.concat([entry.content, Buffer.from('\n/* drift */')]) }
        : entry,
    )
    const unlistedPath = entries.find(
      ({ path: repoPath }) => !MARKET_FEED_HEADING_REMOVAL_ALLOWED_PATHS.includes(repoPath),
    ).path
    const changedUnlisted = entries.map((entry) =>
      entry.path === unlistedPath
        ? { ...entry, content: Buffer.concat([entry.content, Buffer.from('\n/* drift */')]) }
        : entry,
    )

    expect(() => assertMarketFeedHeadingRemovalDisposition(changedAllowed)).toThrow(
      /Market feed-heading removal allowed file drift/,
    )
    expect(() => resolveMarketRuntimeDisposition(changedAllowed)).toThrow(
      /Market feed-heading removal disposition rejected/,
    )
    expect(() => assertMarketFeedHeadingRemovalDisposition(changedUnlisted)).toThrow(
      /contentBytes drift/,
    )
    expect(() => resolveMarketRuntimeDisposition(changedUnlisted)).toThrow(
      /Market feed-heading removal disposition rejected/,
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

  it('still recognizes the exact historical Stage 6 Messenger URL-privacy tree', () => {
    const entries = withCreateChannelPlacementReverted(
      withUnnamedControlNamesReverted(readFileEntries(repoRoot, ownedPaths.messenger)),
    )
    const createChannel = entries.find(
      ({ path: repoPath }) => repoPath === 'frontend/src/components/CreateChannelView.vue',
    )

    expect(fileSha256(createChannel.content)).toBe(
      STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_FILE_SHA256[
        'frontend/src/components/CreateChannelView.vue'
      ],
    )
    expect(resolveMessengerRuntimeDisposition(entries)).toMatchObject({
      kind: 'stage6-url-privacy',
      evidence: STAGE6_MESSENGER_URL_PRIVACY_EVIDENCE,
    })
  })

  it('still recognizes the exact historical Stage 8 CreateChannel HelpPopover placement tree', () => {
    const entries = withUnnamedControlNamesReverted(
      readFileEntries(repoRoot, ownedPaths.messenger),
    )
    const createChannel = entries.find(
      ({ path: repoPath }) => repoPath === 'frontend/src/components/CreateChannelView.vue',
    )

    expect(resolveMessengerRuntimeDisposition(entries)).toMatchObject({
      kind: STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_KIND,
      evidence: STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_EVIDENCE,
    })
    expect(STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_ALLOWED_PATHS).toEqual([
      'frontend/src/components/CreateChannelView.vue',
    ])
    expect(STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_LOCKED_STAGE6_PATHS).toEqual([
      'frontend/src/components/ChatView.vue',
      'frontend/src/views/MessengerView.vue',
    ])
    expect(fileSha256(createChannel.content)).toBe(
      STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_ALLOWED_FILE_SHA256[
        'frontend/src/components/CreateChannelView.vue'
      ],
    )
    for (const repoPath of STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_LOCKED_STAGE6_PATHS) {
      const entry = entries.find(({ path: candidate }) => candidate === repoPath)
      expect(fileSha256(entry.content)).toBe(
        STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_FILE_SHA256[repoPath],
      )
    }
  })

  it('permits only the exact Stage 8 Messenger unnamed-control names', () => {
    const entries = readFileEntries(repoRoot, ownedPaths.messenger)
    const actual = currentEvidence(ownedPaths.messenger, MESSENGER_RUNTIME_CONTRACT)
    const disposition = resolveMessengerRuntimeDisposition(entries)

    expect(actual).not.toMatchObject(MESSENGER_RUNTIME_BASELINE)
    expect(actual).not.toMatchObject(STAGE6_MESSENGER_URL_PRIVACY_EVIDENCE)
    expect(actual).not.toMatchObject(STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_EVIDENCE)
    expect(disposition).toMatchObject({
      kind: STAGE8_MESSENGER_UNNAMED_CONTROL_KIND,
      evidence: STAGE8_MESSENGER_UNNAMED_CONTROL_EVIDENCE,
    })
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
    for (const repoPath of STAGE8_MESSENGER_UNNAMED_CONTROL_ALLOWED_PATHS) {
      const entry = entries.find(({ path: candidate }) => candidate === repoPath)
      expect(fileSha256(entry.content)).toBe(
        STAGE8_MESSENGER_UNNAMED_CONTROL_ALLOWED_FILE_SHA256[repoPath],
      )
    }
    for (const repoPath of STAGE8_MESSENGER_UNNAMED_CONTROL_LOCKED_STAGE8_PATHS) {
      const entry = entries.find(({ path: candidate }) => candidate === repoPath)
      expect(fileSha256(entry.content)).toBe(
        STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_ALLOWED_FILE_SHA256[repoPath],
      )
    }
    for (const repoPath of STAGE8_MESSENGER_UNNAMED_CONTROL_LOCKED_STAGE6_PATHS) {
      const entry = entries.find(({ path: candidate }) => candidate === repoPath)
      expect(fileSha256(entry.content)).toBe(
        STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_FILE_SHA256[repoPath],
      )
    }
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

  it('fails closed if an allowed Stage 6 privacy file changes by even one byte', () => {
    const entries = readFileEntries(repoRoot, ownedPaths.messenger)
    const mutationPath = 'frontend/src/components/ChatView.vue'
    const mutated = entries.map((entry) =>
      entry.path === mutationPath
        ? { ...entry, content: Buffer.concat([entry.content, Buffer.from('\n// drift')]) }
        : entry,
    )

    expect(() => resolveMessengerRuntimeDisposition(mutated)).toThrow(
      /Stage 6 Messenger URL-privacy allowed file drift: frontend\/src\/components\/ChatView\.vue/,
    )
    expect(() => resolveMessengerRuntimeDisposition(mutated)).toThrow(
      /Stage 8 CreateChannel HelpPopover placement requires unchanged Stage 6 file: frontend\/src\/components\/ChatView\.vue/,
    )
    expect(() => resolveMessengerRuntimeDisposition(mutated)).toThrow(
      /Stage 8 Messenger unnamed-control requires unchanged Stage 6 file: frontend\/src\/components\/ChatView\.vue/,
    )
  })

  it('fails closed if CreateChannelView changes again without a new Stage 8 hash', () => {
    const entries = readFileEntries(repoRoot, ownedPaths.messenger)
    const mutated = entries.map((entry) =>
      entry.path === 'frontend/src/components/CreateChannelView.vue'
        ? { ...entry, content: Buffer.concat([entry.content, Buffer.from('\n/* drift */')]) }
        : entry,
    )

    expect(() => resolveMessengerRuntimeDisposition(mutated)).toThrow(
      /Stage 8 CreateChannel HelpPopover placement allowed file drift: frontend\/src\/components\/CreateChannelView\.vue/,
    )
    expect(() => resolveMessengerRuntimeDisposition(mutated)).toThrow(
      /Stage 8 Messenger unnamed-control requires unchanged CreateChannel file: frontend\/src\/components\/CreateChannelView\.vue/,
    )
  })

  it('fails closed if the Stage 8 placement declaration is removed', () => {
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
    expect(() => resolveMessengerRuntimeDisposition(mutated)).toThrow(
      /Stage 8 CreateChannel HelpPopover placement remediation rejected/,
    )
    expect(() => resolveMessengerRuntimeDisposition(mutated)).toThrow(
      /Stage 8 Messenger unnamed-control names rejected/,
    )
  })

  it('fails closed if an unlisted Stage 6 Messenger file changes or appears', () => {
    const entries = readFileEntries(repoRoot, ownedPaths.messenger)
    const unlistedPath = entries.find(
      ({ path: repoPath }) => !STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_PATHS.includes(repoPath),
    ).path
    const changedUnlisted = entries.map((entry) =>
      entry.path === unlistedPath
        ? { ...entry, content: Buffer.concat([entry.content, Buffer.from('\n// drift')]) }
        : entry,
    )
    const addedUnlisted = [
      ...entries,
      {
        path: 'frontend/src/components/chat/Stage6UnlistedPrivacyDrift.vue',
        content: Buffer.from('<template />'),
      },
    ]

    expect(() => resolveMessengerRuntimeDisposition(changedUnlisted)).toThrow(
      /Stage 6 URL-privacy disposition rejected .*contentBytes drift/,
    )
    expect(() => resolveMessengerRuntimeDisposition(addedUnlisted)).toThrow(
      /Stage 6 URL-privacy disposition rejected .*count drift/,
    )
  })

  it('fails closed if an allowed Stage 8 unnamed-control file changes by even one byte', () => {
    const entries = readFileEntries(repoRoot, ownedPaths.messenger)
    const mutated = entries.map((entry) =>
      entry.path === 'frontend/src/components/chat/ChatHeader.vue'
        ? { ...entry, content: Buffer.concat([entry.content, Buffer.from('\n// drift')]) }
        : entry,
    )

    expect(() => resolveMessengerRuntimeDisposition(mutated)).toThrow(
      /Stage 8 Messenger unnamed-control allowed file drift: frontend\/src\/components\/chat\/ChatHeader\.vue/,
    )
  })

  it('fails closed if the Stage 8 unnamed-control names are removed', () => {
    const entries = readFileEntries(repoRoot, ownedPaths.messenger)
    const mutated = withUnnamedControlNamesReverted(entries)

    expect(() => assertStage8MessengerUnnamedControlDisposition(mutated)).toThrow(
      /Stage 8 Messenger unnamed-control allowed file drift/,
    )
    expect(resolveMessengerRuntimeDisposition(mutated)).toMatchObject({
      kind: STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_KIND,
      evidence: STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_EVIDENCE,
    })
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

  it('freezes AdminMessagesView as a whole file and dispositions TradingSettings reset only', () => {
    const adminMessages = readRepoFile(ADMIN_MESSAGES_PATH)
    const tradingSettings = readRepoFile(TRADING_SETTINGS_PATH)
    expect(fileSha256(adminMessages)).toBe(ADMIN_MESSAGES_SHA256)
    expect(TRADING_SETTINGS_SHA256).toBe(
      '509dd32235e1cb98aa164940cf7722604f16b6518f7387699554bf3a828ecfaa',
    )
    expect(fileSha256(tradingSettings)).toBe(STAGE6_TRADING_SETTINGS_RESET_DIALOG_SHA256)
    expect(resolveTradingSettingsDisposition(tradingSettings)).toEqual({
      kind: STAGE6_TRADING_SETTINGS_RESET_DIALOG_KIND,
      sha256: STAGE6_TRADING_SETTINGS_RESET_DIALOG_SHA256,
    })
    expect(assertStage6TradingSettingsResetDialogDisposition(tradingSettings)).toBe(
      STAGE6_TRADING_SETTINGS_RESET_DIALOG_SHA256,
    )
    expect(fileSha256(Buffer.concat([adminMessages, Buffer.from('\n')]))).not.toBe(
      ADMIN_MESSAGES_SHA256,
    )
    expect(() =>
      resolveTradingSettingsDisposition(Buffer.concat([tradingSettings, Buffer.from('\n')])),
    ).toThrow(/Stage 4 whole-file drift/)
    expect(() =>
      resolveTradingSettingsDisposition(
        Buffer.from(
          tradingSettings
            .toString('utf8')
            .replace("if (!confirm('آیا از حذف این استثنای تقویمی مطمئن هستید؟'))", 'if (false)'),
          'utf8',
        ),
      ),
    ).toThrow(/protected calendar confirm/)
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
