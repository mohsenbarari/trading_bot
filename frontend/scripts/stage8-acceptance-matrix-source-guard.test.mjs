import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createHash } from 'node:crypto'
import { describe, expect, it } from 'vitest'
import {
  deriveExpectedOutcome,
  deriveSourceBoundOutcome,
  loadMatrix,
} from './lib/stage8-full-acceptance-runtime.mjs'

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..')
const matrixPath = path.join(repoRoot, 'docs/uiux-stage8-acceptance-rollout/ACCEPTANCE_MATRIX.json')

function sha256File(rel) {
  return createHash('sha256').update(fs.readFileSync(path.join(repoRoot, rel))).digest('hex')
}

describe('Stage 8 acceptance matrix source binding', () => {
  const matrix = loadMatrix(matrixPath)
  const settings = matrix.routes.find((route) => route.name === 'settings')
  const routerSource = fs.readFileSync(path.join(repoRoot, 'frontend/src/router/index.ts'), 'utf8')
  const contractSource = fs.readFileSync(
    path.join(repoRoot, 'frontend/src/router/uiRouteContract.ts'),
    'utf8',
  )

  it('keeps Stage 8 open and non-authoritative', () => {
    expect(matrix.acceptanceAuthority).toBe(false)
    expect(matrix.cellAccounting.executedFullMatrixCellCount).toBe(0)
    expect(matrix.cellAccounting.partialSyntheticBrowserSliceCount).toBe(12)
    expect(matrix.cellAccounting.partialSyntheticBrowserScenarioCellCount).toBe(163)
    expect(matrix.cellAccounting.partialSyntheticBrowserCellsCountTowardFullMatrix).toBe(false)
  })

  it('binds runtime hashes to the current source files', () => {
    const hashes = matrix.sourceSnapshot.runtimeSourceHashes
    expect(hashes['frontend/src/router/index.ts']).toBe(sha256File('frontend/src/router/index.ts'))
    expect(hashes['frontend/src/router/uiRouteContract.ts']).toBe(
      sha256File('frontend/src/router/uiRouteContract.ts'),
    )
    expect(hashes['frontend/src/utils/auth.ts']).toBe(sha256File('frontend/src/utils/auth.ts'))
    expect(hashes['models/user.py']).toBe(sha256File('models/user.py'))
  })

  it('registers /settings as SettingsView with auth, not a redirect', () => {
    expect(settings.routerKind).toBe('component')
    expect(settings.redirectTargetName).toBeNull()
    expect(settings.routeMeta.requiresAuth).toBe(true)
    expect(routerSource).toMatch(/path:\s*'\/settings'/)
    expect(routerSource).toMatch(/name:\s*'settings'/)
    expect(routerSource).toMatch(/import\('\.\.\/views\/SettingsView\.vue'\)/)
    expect(routerSource).not.toMatch(/name:\s*'settings'[\s\S]{0,180}redirect:/)
    expect(contractSource).toMatch(/path:\s*'\/settings'/)
    expect(contractSource).toMatch(/name:\s*'settings'/)
  })

  it('enumerates guest login and eight authenticated render outcomes for /settings', () => {
    expect(settings.expectedAccess.guest.kind).toBe('redirect-login')
    const authenticated = matrix.accessProfiles.filter((profile) => profile.authenticated)
    expect(authenticated).toHaveLength(8)
    for (const profile of authenticated) {
      expect(settings.expectedAccess[profile.id].kind).toBe('render-route')
      expect(deriveSourceBoundOutcome(settings, profile).kind).toBe('render-route')
    }
  })

  it('fails when any matrix cell drifts from source-bound guard outcomes', () => {
    const drifted = []
    for (const route of matrix.routes) {
      for (const profile of matrix.accessProfiles) {
        const expected = deriveExpectedOutcome(route, profile)
        if (expected.sourceDrift) drifted.push(expected.driftReason)
      }
    }
    expect(drifted).toEqual([])
  })

  it('fails closed if a /settings authenticated cell is rewritten back to a redirect', () => {
    const mutated = structuredClone(settings)
    mutated.expectedAccess.member = {
      kind: 'redirect-canonical',
      targetName: 'account-security',
    }
    expect(deriveExpectedOutcome(mutated, { id: 'member', authenticated: true, role: 'عادی' }).sourceDrift).toBe(
      true,
    )
  })
})
