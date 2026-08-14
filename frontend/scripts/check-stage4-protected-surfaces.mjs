#!/usr/bin/env node
import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'
import {
  ADMIN_MESSAGES_PATH,
  ADMIN_MESSAGES_SHA256,
  MAIN_UIUX_INTEGRATION_MARKET_ALLOWED_PATHS,
  MAIN_UIUX_INTEGRATION_MARKET_KIND,
  MARKET_A_PLUS_C_ALLOWED_PATHS,
  MARKET_A_PLUS_C_KIND,
  STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_PATHS,
  STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_ALLOWED_PATHS,
  STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_KIND,
  STAGE4_BASE_COMMIT,
  STAGE4_BASE_TREE,
  STAGE4_ROUTE_CONTRACT_PATH,
  STAGE4_SHARED_DEPENDENCY_ISOLATION_PATHS,
  STAGE4_SCOPE_MANIFEST_PATH,
  STAGE6_TRADING_SETTINGS_RESET_DIALOG_KIND,
  TRADING_SETTINGS_PATH,
  TRADING_SETTINGS_SHA256,
  resolveTradingSettingsDisposition,
  assertStage4RouteProtection,
  assertStage4RuntimeRouteProtection,
  assertStage4SharedDependencyIsolation,
  discoverStage4OwnedRuntimePaths,
  fileSha256,
  readFileEntries,
  resolveMarketRuntimeDisposition,
  resolveMessengerRuntimeDisposition,
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
  const market = resolveMarketRuntimeDisposition(readFileEntries(repoRoot, ownedPaths.market))
  const messenger = resolveMessengerRuntimeDisposition(
    readFileEntries(repoRoot, ownedPaths.messenger),
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
  const tradingSettings = resolveTradingSettingsDisposition(readRepoFile(TRADING_SETTINGS_PATH))
  const scopeManifest = JSON.parse(readRepoFile(STAGE4_SCOPE_MANIFEST_PATH, 'utf8'))
  const manifestRoutes = assertStage4RouteProtection(scopeManifest.routes)
  const runtimeRoutes = assertStage4RuntimeRouteProtection(
    readRepoFile(STAGE4_ROUTE_CONTRACT_PATH, 'utf8'),
  )
  const sharedDependencies = assertStage4SharedDependencyIsolation(
    new Map(
      STAGE4_SHARED_DEPENDENCY_ISOLATION_PATHS.map((repoPath) => [
        repoPath,
        readRepoFile(repoPath, 'utf8'),
      ]),
    ),
  )

  console.log(`PASS protected checkpoint anchor (${STAGE4_BASE_COMMIT}, tree ${STAGE4_BASE_TREE})`)
  if (market.kind === 'stage4-baseline') {
    console.log(
      `PASS Stage 4 Market runtime (${market.evidence.count} files, ${market.evidence.contentBytes} bytes, ${market.evidence.pathSetSha256}, ${market.evidence.sha256})`,
    )
  } else if (market.kind === MAIN_UIUX_INTEGRATION_MARKET_KIND) {
    console.log(
      `PASS main/UIUX Market integration disposition (exact ${MAIN_UIUX_INTEGRATION_MARKET_ALLOWED_PATHS.length}-file overlay; ${market.evidence.count} files, ${market.evidence.contentBytes} bytes, ${market.evidence.pathSetSha256}, ${market.evidence.sha256})`,
    )
  } else if (market.kind === MARKET_A_PLUS_C_KIND) {
    console.log(
      `PASS Market A+C visual/decision disposition (exact ${MARKET_A_PLUS_C_ALLOWED_PATHS.length}-file overlay; ${market.evidence.count} files, ${market.evidence.contentBytes} bytes, ${market.evidence.pathSetSha256}, ${market.evidence.sha256})`,
    )
  } else {
    throw new Error(`unsupported Market runtime disposition: ${String(market.kind)}`)
  }
  if (messenger.kind === 'stage4-baseline') {
    console.log(
      `PASS Stage 4 Messenger runtime baseline (${messenger.evidence.count} files, ${messenger.evidence.contentBytes} bytes, ${messenger.evidence.pathSetSha256}, ${messenger.evidence.sha256})`,
    )
  } else if (messenger.kind === 'stage6-url-privacy') {
    console.log(
      `PASS Stage 6 Messenger URL-privacy disposition (exact ${STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_PATHS.length}-file overlay; ${messenger.evidence.count} files, ${messenger.evidence.contentBytes} bytes, ${messenger.evidence.pathSetSha256}, ${messenger.evidence.sha256})`,
    )
  } else if (messenger.kind === STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_KIND) {
    console.log(
      `PASS Stage 8 CreateChannel HelpPopover placement remediation (exact ${STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_ALLOWED_PATHS.length}-file overlay; ${messenger.evidence.count} files, ${messenger.evidence.contentBytes} bytes, ${messenger.evidence.pathSetSha256}, ${messenger.evidence.sha256})`,
    )
  } else {
    throw new Error(`unsupported Messenger runtime disposition: ${String(messenger.kind)}`)
  }
  console.log(
    `PASS Stage 4 Home market interior (${dashboard.sections.length} sections, ${dashboard.bytes} bytes, ${dashboard.sha256})`,
  )
  if (tradingSettings.kind === 'stage4-baseline') {
    console.log(
      `PASS Stage 4 admin protected files (AdminMessages ${adminMessages}, TradingSettings ${tradingSettings.sha256})`,
    )
  } else if (tradingSettings.kind === STAGE6_TRADING_SETTINGS_RESET_DIALOG_KIND) {
    console.log(
      `PASS Stage 4 admin protected files (AdminMessages ${adminMessages}; TradingSettings Stage 6 reset-dialog disposition ${tradingSettings.sha256}; Stage 4 baseline ${TRADING_SETTINGS_SHA256} retained)`,
    )
  } else {
    throw new Error(`unsupported TradingSettings disposition: ${String(tradingSettings.kind)}`)
  }
  console.log(
    `PASS Stage 4 route protection (${manifestRoutes.full} full/off, ${manifestRoutes.mixed} mixed; manifest + runtime ${runtimeRoutes.count}/${manifestRoutes.count})`,
  )
  console.log(
    `PASS shared dependency isolation (${sharedDependencies.reducedMotionSources} motion roots; ${sharedDependencies.protectedJalaliConsumers}/${sharedDependencies.stage7JalaliOptIns} protected/opt-in Jalali consumers; ${sharedDependencies.protectedEmptyStateConsumers}/${sharedDependencies.stage7EmptyStateOptIns} protected/opt-in empty states)`,
  )

  if (process.argv.includes('--list')) {
    console.log('Stage 4 Market runtime paths:')
    for (const repoPath of ownedPaths.market) console.log(repoPath)
    console.log('Stage 4 Messenger runtime paths:')
    for (const repoPath of ownedPaths.messenger) console.log(repoPath)
  }
} catch (error) {
  console.error(
    `FAIL protected-surface guard: ${error instanceof Error ? error.message : String(error)}`,
  )
  process.exitCode = 1
}
