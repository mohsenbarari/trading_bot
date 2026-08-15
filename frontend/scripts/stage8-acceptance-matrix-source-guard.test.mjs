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
import {
  MATRIX_CLOSED_STATUS,
  MATRIX_PENDING_STATUS,
  OWNER_APPROVAL_PHRASE,
  evaluateMatrixAcceptanceTransition,
  loadStage8Closure,
} from './lib/stage8-full-acceptance-contract.mjs'

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

  it('locks the closed official-run state machine after owner sign-off', () => {
    const closure = loadStage8Closure(repoRoot)
    const result = evaluateMatrixAcceptanceTransition(matrix, { repoRoot, closure })
    expect(result).toEqual({ passed: true, failures: [], state: 'closed' })
    expect(matrix.status).toBe(MATRIX_CLOSED_STATUS)
    expect(matrix.acceptanceAuthority).toBe(true)
    expect(closure.status).toBe(MATRIX_CLOSED_STATUS)
    expect(closure.ownerSignoff).toEqual({
      status: 'approved',
      source: 'explicit-current-conversation',
      approvedAt: '2026-08-15T08:00:00Z',
      approvalPhrase: OWNER_APPROVAL_PHRASE,
    })
    expect(matrix.cellAccounting.executedFullMatrixCellCount).toBe(270)
    expect(matrix.cellAccounting.viewportStateInteractionEnvironmentExpansionPerformed).toBe(true)
    expect(matrix.cellAccounting.plannedScenarioCount).toBe(960)
    expect(matrix.cellAccounting.applicableExecutedCount).toBe(830)
    expect(matrix.cellAccounting.applicablePassedCount).toBe(830)
    expect(matrix.cellAccounting.notApplicableCount).toBe(130)
    expect(matrix.cellAccounting.naTaxonomy).toEqual({
      productNotApplicable: 118,
      canonicalAlias: 12,
      harnessDeferred: 0,
    })
    expect(matrix.cellAccounting.partialSyntheticBrowserSliceCount).toBe(12)
    expect(matrix.cellAccounting.partialSyntheticBrowserScenarioCellCount).toBe(163)
    expect(matrix.cellAccounting.partialSyntheticBrowserCellsCountTowardFullMatrix).toBe(false)
    expect(matrix.partialSyntheticBrowserSlices).toHaveLength(12)
  })

  it('still accepts a consistent pending clone without owner closure', () => {
    const pending = structuredClone(matrix)
    pending.status = MATRIX_PENDING_STATUS
    pending.acceptanceAuthority = false
    expect(evaluateMatrixAcceptanceTransition(pending, { repoRoot, closure: null })).toEqual({
      passed: true,
      failures: [],
      state: 'pending',
    })
  })

  it('fails 270 cells with a stale status or missing expansion', () => {
    const staleStatus = structuredClone(matrix)
    staleStatus.status = 'partial-browser-slice-executed-full-acceptance-pending'
    expect(evaluateMatrixAcceptanceTransition(staleStatus, { repoRoot }).passed).toBe(false)
    const noExpansion = structuredClone(matrix)
    noExpansion.cellAccounting.viewportStateInteractionEnvironmentExpansionPerformed = false
    expect(evaluateMatrixAcceptanceTransition(noExpansion, { repoRoot }).passed).toBe(false)
  })

  it('fails acceptanceAuthority=true without a valid closure and owner sign-off', () => {
    const authorityOnly = structuredClone(matrix)
    authorityOnly.status = MATRIX_PENDING_STATUS
    authorityOnly.acceptanceAuthority = true
    expect(evaluateMatrixAcceptanceTransition(authorityOnly, { repoRoot, closure: null }).passed).toBe(false)
    const closedWithoutSignoff = structuredClone(matrix)
    closedWithoutSignoff.status = MATRIX_CLOSED_STATUS
    closedWithoutSignoff.acceptanceAuthority = true
    expect(evaluateMatrixAcceptanceTransition(closedWithoutSignoff, { repoRoot, closure: null }).passed).toBe(
      false,
    )
  })

  it('fails passed-below-executed, harnessDeferred, partial drift, and broken refs', () => {
    const fewerPassed = structuredClone(matrix)
    fewerPassed.cellAccounting.applicablePassedCount = 829
    expect(evaluateMatrixAcceptanceTransition(fewerPassed, { repoRoot }).failures).toEqual(
      expect.arrayContaining([
        'counter applicablePassedCount 829 != 830',
        'applicablePassedCount below applicableExecutedCount',
      ]),
    )
    const deferred = structuredClone(matrix)
    deferred.cellAccounting.naTaxonomy.harnessDeferred = 1
    expect(evaluateMatrixAcceptanceTransition(deferred, { repoRoot }).failures).toEqual(
      expect.arrayContaining(['harnessDeferred 1']),
    )
    const partialDrift = structuredClone(matrix)
    partialDrift.cellAccounting.partialSyntheticBrowserSliceCount = 13
    expect(evaluateMatrixAcceptanceTransition(partialDrift, { repoRoot }).passed).toBe(false)
    const brokenRef = structuredClone(matrix)
    brokenRef.cellAccounting.officialFullAcceptance.receiptId = ''
    brokenRef.fullAcceptanceSourceSnapshot.reportSha256 = 'not-a-hash'
    expect(evaluateMatrixAcceptanceTransition(brokenRef, { repoRoot }).failures).toEqual(
      expect.arrayContaining([
        'receipt/report reference incomplete',
        'report hash missing or invalid',
      ]),
    )
  })

  it('accepts only pending or closed consistent states', () => {
    const receiptId = matrix.cellAccounting.officialFullAcceptance.receiptId
    const closure = {
      status: MATRIX_CLOSED_STATUS,
      technicalReceiptSha256: matrix.evidenceCatalog[receiptId].sha256,
      ownerSignoff: {
        status: 'approved',
        approvalPhrase: OWNER_APPROVAL_PHRASE,
      },
    }
    const closed = structuredClone(matrix)
    closed.status = MATRIX_CLOSED_STATUS
    closed.acceptanceAuthority = true
    expect(evaluateMatrixAcceptanceTransition(closed, { repoRoot, closure })).toEqual({
      passed: true,
      failures: [],
      state: 'closed',
    })
    const midAuthority = structuredClone(matrix)
    midAuthority.status = MATRIX_CLOSED_STATUS
    midAuthority.acceptanceAuthority = false
    expect(evaluateMatrixAcceptanceTransition(midAuthority, { repoRoot, closure }).passed).toBe(false)
    const pendingWithClosure = structuredClone(matrix)
    pendingWithClosure.status = MATRIX_PENDING_STATUS
    pendingWithClosure.acceptanceAuthority = false
    expect(evaluateMatrixAcceptanceTransition(pendingWithClosure, { repoRoot, closure }).passed).toBe(false)
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
