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
  STAGE4_BASE_COMMIT,
  STAGE4_BASE_TREE,
  STAGE4_ROUTE_CONTRACT_PATH,
  STAGE4_SCOPE_MANIFEST_PATH,
  TRADING_SETTINGS_PATH,
  TRADING_SETTINGS_SHA256,
  assertProtectedFileSetEvidence,
  assertStage4RouteProtection,
  assertStage4RuntimeRouteProtection,
  discoverStage4OwnedRuntimePaths,
  fileSha256,
  isMarketOwnedRuntimePath,
  isMessengerOwnedRuntimePath,
  protectedFileSetEvidence,
  readFileEntries,
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

describe('Stage 4 protected surface baseline', () => {
  const ownedPaths = discoverStage4OwnedRuntimePaths(repoRoot)

  it('binds the clean Stage 4 checkpoint commit and tree', () => {
    expect(STAGE4_BASE_COMMIT).toBe('9dfa961000832c830729ce67e8a54357915c716a')
    expect(STAGE4_BASE_TREE).toBe('1540c2534d8052a3a8cfcffcdc2f65e4b85fc874')
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

  it('binds the complete Messenger runtime and every formerly omitted direct dependency', () => {
    for (const repoPath of MESSENGER_OMITTED_DIRECT_RUNTIME_PATHS) {
      expect(ownedPaths.messenger).toContain(repoPath)
    }
    expect(
      assertProtectedFileSetEvidence(
        'Messenger runtime',
        currentEvidence(ownedPaths.messenger, MESSENGER_RUNTIME_CONTRACT),
        MESSENGER_RUNTIME_BASELINE,
      ),
    ).toMatchObject(MESSENGER_RUNTIME_BASELINE)
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
