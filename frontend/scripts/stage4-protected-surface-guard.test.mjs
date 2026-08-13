import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import {
  ADMIN_MESSAGES_PATH,
  ADMIN_MESSAGES_SHA256,
  MARKET_RUNTIME_BASELINE,
  MARKET_RUNTIME_CONTRACT,
  MESSENGER_OMITTED_DIRECT_RUNTIME_PATHS,
  MESSENGER_RUNTIME_BASELINE,
  MESSENGER_RUNTIME_CONTRACT,
  STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_FILE_SHA256,
  STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_PATHS,
  STAGE6_MESSENGER_URL_PRIVACY_EVIDENCE,
  STAGE4_BASE_COMMIT,
  STAGE4_BASE_TREE,
  STAGE4_ROUTE_CONTRACT_PATH,
  STAGE4_SHARED_DEPENDENCY_ISOLATION_PATHS,
  STAGE4_SCOPE_MANIFEST_PATH,
  TRADING_SETTINGS_PATH,
  TRADING_SETTINGS_SHA256,
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

  it('binds the complete Market runtime, including settlement type', () => {
    expect(ownedPaths.market).toContain('frontend/src/utils/settlementType.ts')
    expect(
      assertProtectedFileSetEvidence(
        'Market runtime',
        currentEvidence(ownedPaths.market, MARKET_RUNTIME_CONTRACT),
        MARKET_RUNTIME_BASELINE,
      ),
    ).toMatchObject(MARKET_RUNTIME_BASELINE)
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

  it('permits only the exact Stage 6 Messenger URL-privacy disposition', () => {
    const entries = readFileEntries(repoRoot, ownedPaths.messenger)
    const actual = currentEvidence(ownedPaths.messenger, MESSENGER_RUNTIME_CONTRACT)
    const disposition = resolveMessengerRuntimeDisposition(entries)

    expect(actual).not.toMatchObject(MESSENGER_RUNTIME_BASELINE)
    expect(disposition).toMatchObject({
      kind: 'stage6-url-privacy',
      evidence: STAGE6_MESSENGER_URL_PRIVACY_EVIDENCE,
    })
    expect(STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_PATHS).toEqual([
      'frontend/src/components/ChatView.vue',
      'frontend/src/components/CreateChannelView.vue',
      'frontend/src/views/MessengerView.vue',
    ])
    for (const entry of entries.filter(({ path: repoPath }) =>
      STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_PATHS.includes(repoPath),
    )) {
      expect(fileSha256(entry.content)).toBe(
        STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_FILE_SHA256[entry.path],
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

  it('freezes AdminMessagesView and TradingSettings as whole files', () => {
    const adminMessages = readRepoFile(ADMIN_MESSAGES_PATH)
    const tradingSettings = readRepoFile(TRADING_SETTINGS_PATH)
    expect(fileSha256(adminMessages)).toBe(ADMIN_MESSAGES_SHA256)
    expect(fileSha256(tradingSettings)).toBe(TRADING_SETTINGS_SHA256)
    expect(fileSha256(Buffer.concat([adminMessages, Buffer.from('\n')]))).not.toBe(
      ADMIN_MESSAGES_SHA256,
    )
    expect(fileSha256(Buffer.concat([tradingSettings, Buffer.from('\n')]))).not.toBe(
      TRADING_SETTINGS_SHA256,
    )
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
