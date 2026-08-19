import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { assertRequestedState } from './lib/uiux-unification-v3-state-evidence.mjs'

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

describe('UIUX v3 runtime state evidence', () => {
  it.each(['loading', 'empty', 'error'])('requires visible descriptor evidence for %s', (state) => {
    const spec = { selector: `[data-state="${state}"]` }
    expect(assertRequestedState(state, spec, 1)).toEqual([])
    expect(assertRequestedState(state, spec, 0).join(' ')).toContain(
      'selector was not visible',
    )
  })

  it('fails closed when an applicable descriptor has no selector', () => {
    expect(assertRequestedState('loading', {}, 1)).toEqual([
      'loading descriptor has no visible-state selector',
    ])
  })

  it('keeps loading evidence before release and retains a post-release settlement probe', () => {
    const source = readFileSync(
      path.join(FRONTEND, 'scripts/uiux-unification-v3-phase11-matrix.mjs'),
      'utf8',
    )
    const evidenceIndex = source.indexOf('requestedStateProbe = await collectUiProbe(page)')
    const releaseIndex = source.indexOf('controller.releaseHeldRequest = true', evidenceIndex)
    expect(evidenceIndex).toBeGreaterThan(-1)
    expect(releaseIndex).toBeGreaterThan(evidenceIndex)
    expect(source).toContain("if (postReleaseSelectorCount > 0) failures.push('loading state did not settle after request release')")
    expect(source).toMatch(/if \(state !== 'loading'\) \{[\s\S]+await page\.screenshot/)
    expect(source).toContain('await restoreInitialView(page)')
    expect(source).toMatch(/restoreInitialView[\s\S]+routeScroller\.scrollTop = 0/)
    expect(source).toContain("ONLY === 'states'")
    expect(source).toContain('await recoverIdentityPageDataAfterHold(page)')
  })
})
