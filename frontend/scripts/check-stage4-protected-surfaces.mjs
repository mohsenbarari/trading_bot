#!/usr/bin/env node
import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'
import {
  ADMIN_MESSAGES_PATH,
  ADMIN_MESSAGES_SHA256,
  MARKET_RUNTIME_BASELINE,
  MARKET_RUNTIME_CONTRACT,
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
  protectedFileSetEvidence,
  readFileEntries,
} from './lib/stage4-protected-surface-guard.mjs'
import {
  DASHBOARD_MARKET_REGION_PATH,
  DASHBOARD_MARKET_REGION_SHA256,
  dashboardMarketRegionEvidence,
} from './lib/stage3-protected-region-guard.mjs'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.resolve(scriptDir, '..', '..')

function readRepoFile(repoPath, encoding = null) {
  return fs.readFileSync(path.join(repoRoot, repoPath), encoding)
}

function assertWholeFile(label, repoPath, expectedSha256) {
  const actual = fileSha256(readRepoFile(repoPath))
  if (actual !== expectedSha256) {
    throw new Error(`${label} whole-file drift: ${expectedSha256} -> ${actual}`)
  }
  return actual
}

try {
  const ownedPaths = discoverStage4OwnedRuntimePaths(repoRoot)
  const market = assertProtectedFileSetEvidence(
    'Market runtime',
    protectedFileSetEvidence(readFileEntries(repoRoot, ownedPaths.market), MARKET_RUNTIME_CONTRACT),
    MARKET_RUNTIME_BASELINE,
  )
  const messenger = assertProtectedFileSetEvidence(
    'Messenger runtime',
    protectedFileSetEvidence(
      readFileEntries(repoRoot, ownedPaths.messenger),
      MESSENGER_RUNTIME_CONTRACT,
    ),
    MESSENGER_RUNTIME_BASELINE,
  )

  const dashboard = dashboardMarketRegionEvidence(
    readRepoFile(DASHBOARD_MARKET_REGION_PATH, 'utf8'),
  )
  if (dashboard.sha256 !== DASHBOARD_MARKET_REGION_SHA256) {
    throw new Error(
      `Home market region drift: ${DASHBOARD_MARKET_REGION_SHA256} -> ${dashboard.sha256}`,
    )
  }

  const adminMessages = assertWholeFile(
    'AdminMessagesView',
    ADMIN_MESSAGES_PATH,
    ADMIN_MESSAGES_SHA256,
  )
  const tradingSettings = assertWholeFile(
    'TradingSettings',
    TRADING_SETTINGS_PATH,
    TRADING_SETTINGS_SHA256,
  )
  const scopeManifest = JSON.parse(readRepoFile(STAGE4_SCOPE_MANIFEST_PATH, 'utf8'))
  const manifestRoutes = assertStage4RouteProtection(scopeManifest.routes)
  const runtimeRoutes = assertStage4RuntimeRouteProtection(
    readRepoFile(STAGE4_ROUTE_CONTRACT_PATH, 'utf8'),
  )

  console.log(`PASS Stage 4 protected baseline (${STAGE4_BASE_COMMIT}, tree ${STAGE4_BASE_TREE})`)
  console.log(
    `PASS Stage 4 Market runtime (${market.count} files, ${market.contentBytes} bytes, ${market.pathSetSha256}, ${market.sha256})`,
  )
  console.log(
    `PASS Stage 4 Messenger runtime (${messenger.count} files, ${messenger.contentBytes} bytes, ${messenger.pathSetSha256}, ${messenger.sha256})`,
  )
  console.log(
    `PASS Stage 4 Home market interior (${dashboard.sections.length} sections, ${dashboard.bytes} bytes, ${dashboard.sha256})`,
  )
  console.log(
    `PASS Stage 4 admin protected files (AdminMessages ${adminMessages}, TradingSettings ${tradingSettings})`,
  )
  console.log(
    `PASS Stage 4 route protection (${manifestRoutes.full} full/off, ${manifestRoutes.mixed} mixed; manifest + runtime ${runtimeRoutes.count}/${manifestRoutes.count})`,
  )

  if (process.argv.includes('--list')) {
    console.log('Stage 4 Market runtime paths:')
    for (const repoPath of ownedPaths.market) console.log(repoPath)
    console.log('Stage 4 Messenger runtime paths:')
    for (const repoPath of ownedPaths.messenger) console.log(repoPath)
  }
} catch (error) {
  console.error(
    `FAIL Stage 4 protected baseline: ${error instanceof Error ? error.message : String(error)}`,
  )
  process.exitCode = 1
}
