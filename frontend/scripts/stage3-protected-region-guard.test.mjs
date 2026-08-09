import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import {
  DASHBOARD_MARKET_REGION_PATH,
  DASHBOARD_MARKET_REGION_SHA256,
  dashboardMarketRegionEvidence,
  extractDashboardMarketSections,
} from './lib/stage3-protected-region-guard.mjs'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.resolve(scriptDir, '..', '..')

function canonicalDashboardSource() {
  return fs.readFileSync(path.join(repoRoot, DASHBOARD_MARKET_REGION_PATH), 'utf8')
}

describe('Stage 3 protected Home market region', () => {
  it('binds the versioned six-section base contract', () => {
    const evidence = dashboardMarketRegionEvidence(canonicalDashboardSource())
    expect(evidence.sha256).toBe(DASHBOARD_MARKET_REGION_SHA256)
    expect(evidence.bytes).toBe(4553)
    expect(evidence.sections.map(({ id, bytes }) => [id, bytes])).toEqual([
      ['market-computed', 447],
      ['open-market', 110],
      ['template-hero', 988],
      ['hero-disabled-css', 61],
      ['hero-focus-css', 220],
      ['hero-css', 2585],
    ])
  })

  it('detects a mutation in every protected section', () => {
    const source = canonicalDashboardSource()
    const base = dashboardMarketRegionEvidence(source)
    for (const section of extractDashboardMarketSections(source).values()) {
      const insertionPoint = section.indexOf('\n')
      expect(insertionPoint).toBeGreaterThan(0)
      const mutatedSection = `${section.slice(0, insertionPoint + 1)}/* hostile-stage3-mutation */\n${section.slice(insertionPoint + 1)}`
      const mutated = source.replace(section, mutatedSection)
      expect(dashboardMarketRegionEvidence(mutated).sha256).not.toBe(base.sha256)
    }
  })

  it('fails closed when a required anchor is duplicated', () => {
    const source = canonicalDashboardSource()
    const duplicate = `${source}\nconst isMarketOpen = computed(() => marketRuntime.value.is_open)`
    expect(() => dashboardMarketRegionEvidence(duplicate)).toThrow(/exactly one anchor/)
  })
})
