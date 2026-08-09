#!/usr/bin/env node
import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'
import {
  DASHBOARD_MARKET_REGION_PATH,
  DASHBOARD_MARKET_REGION_SHA256,
  dashboardMarketRegionEvidence,
} from './lib/stage3-protected-region-guard.mjs'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.resolve(scriptDir, '..', '..')
const workingPath = path.join(repoRoot, DASHBOARD_MARKET_REGION_PATH)

try {
  const current = dashboardMarketRegionEvidence(fs.readFileSync(workingPath, 'utf8'))
  if (current.sha256 !== DASHBOARD_MARKET_REGION_SHA256) {
    throw new Error(
      `protected Home market region drift: ${DASHBOARD_MARKET_REGION_SHA256} -> ${current.sha256}`,
    )
  }
  console.log(
    `PASS Stage 3 protected Home market region (${current.sections.length} sections, ${current.bytes} bytes, ${current.sha256})`,
  )
} catch (error) {
  console.error(
    `FAIL Stage 3 protected Home market region: ${error instanceof Error ? error.message : String(error)}`,
  )
  process.exitCode = 1
}
