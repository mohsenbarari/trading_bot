import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')

function loadJson(rel) {
  return JSON.parse(readFileSync(path.join(REPO, rel), 'utf8'))
}

function readContract() {
  const text = readFileSync(path.join(REPO, 'frontend/src/router/uiRouteContract.ts'), 'utf8')
  const paths = [...text.matchAll(/path: '([^']+)'/g)].map((item) => item[1])
  const scopes = [...text.matchAll(/v2Scope: UI_V2_SCOPE\.([A-Z]+)/g)].map((item) => item[1].toLowerCase())
  const protections = [...text.matchAll(/protection: UI_ROUTE_PROTECTION\.([A-Z]+)/g)].map((item) =>
    item[1].toLowerCase(),
  )
  const count = (values) =>
    values.reduce((acc, value) => {
      acc[value] = (acc[value] || 0) + 1
      return acc
    }, {})
  return { paths, scopes: count(scopes), protections: count(protections) }
}

describe('UIUX unification v3 inventory', () => {
  it('independently confirms the live 30/10/16/4 and 23/3/4 contract', () => {
    const contract = readContract()
    expect(contract.paths).toHaveLength(30)
    expect(contract.scopes).toEqual({ route: 10, section: 16, off: 4 })
    expect(contract.protections).toEqual({ none: 23, mixed: 3, full: 4 })
    expect(contract.paths.filter((_, index) => {
      const text = readFileSync(path.join(REPO, 'frontend/src/router/uiRouteContract.ts'), 'utf8')
      const scopes = [...text.matchAll(/v2Scope: UI_V2_SCOPE\.([A-Z]+)/g)].map((item) => item[1])
      return scopes[index] === 'OFF'
    })).toEqual(['/market', '/chat', '/admin/channels', '/share-receive'])
  })

  it('owns every live route and leaves no unresolved in-scope surface status', () => {
    const inventory = loadJson('docs/uiux-unification/SURFACE_INVENTORY.json')
    const matrix = loadJson('docs/uiux-unification/STATE_MATRIX.json')
    expect(inventory.verified_route_contract.matches_initial_numbers).toBe(true)
    expect(inventory.status).toBe('integration-candidate-surface-disposition')
    expect(inventory.unknown_live_surfaces).toBe(0)
    expect(inventory.surfaces.every((item) => [
      'aligned-v3',
      'aligned-existing',
      'protected-frozen',
      'inactive-legacy',
    ].includes(item.status))).toBe(true)
    expect(inventory.surfaces.filter((item) => ['partial', 'inconsistent', 'unknown', 'legacy'].includes(item.status))).toEqual([])
    expect(inventory.surfaces.filter((item) => item.baseline_status === 'protected-history-only').every(
      (item) => item.status === 'protected-frozen',
    )).toBe(true)
    expect(inventory.surfaces.filter((item) => item.status === 'protected-frozen')).toHaveLength(6)
    expect(inventory.surfaces.filter((item) => item.status === 'inactive-legacy')).toHaveLength(1)
    expect(inventory.surfaces.filter((item) => item.status.startsWith('aligned-'))).toHaveLength(38)
    expect(new Set(inventory.surfaces.map((item) => item.route.split('|')[0])).size).toBeGreaterThanOrEqual(20)
    expect(matrix.cells.length).toBe(inventory.surfaces.length * matrix.states.length)
    expect(matrix.status).toBe('declared-source-matrix-not-runtime-acceptance')
    expect(matrix.cells.every((cell) => cell.runtime_status === 'source-derived' && cell.result === null)).toBe(true)
    expect(matrix.excluded_environments).toEqual([
      { id: 'telegram-mini-app', reason: 'retired-product-surface' },
    ])
  })

  it('binds the home identity menu to an explicit keyboard browser contract', () => {
    const source = readFileSync(
      path.join(REPO, 'frontend/scripts/uiux-unification-v3-phase11-matrix.mjs'),
      'utf8',
    )
    expect(source).toMatch(/KEYBOARD_ROUTES = \['home'/)
    expect(source).toContain("page.keyboard.press('ArrowDown')")
    expect(source).toContain("page.keyboard.press('Escape')")
    expect(source).toContain('home identity menu did not restore trigger focus')
  })
})
