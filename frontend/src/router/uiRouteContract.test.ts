import { describe, expect, it } from 'vitest'
import scopeManifest from '../design-system-v2/scope-manifest.json'
import {
  UI_ROUTE_PROTECTION,
  UI_V2_SCOPE,
  getUiRouteContract,
  uiRouteContract,
  uiRouteContractByPath,
} from './uiRouteContract'

const expectedRoutes = [
  ['/', 'home'],
  ['/setup-password', 'setup-password'],
  ['/login', 'login'],
  ['/market', 'market'],
  ['/operations', 'operations'],
  ['/operations/customers', 'operations-customers'],
  ['/operations/customers/:relationId', 'operations-customers-detail'],
  ['/operations/accountants', 'operations-accountants'],
  ['/operations/accountants/:relationId', 'operations-accountants-detail'],
  ['/account', 'account'],
  ['/account/security', 'account-security'],
  ['/account/storage', 'account-storage'],
  ['/account/notifications', 'account-notifications'],
  ['/chat', 'messenger'],
  ['/users/:id', 'public-profile'],
  ['/profile', 'profile'],
  ['/settings', 'settings'],
  ['/admin', 'admin'],
  ['/admin/invitations', 'admin-invitations'],
  ['/admin/channels', 'admin-channels'],
  ['/admin/users', 'admin-users'],
  ['/admin/users/:id', 'admin-user-profile'],
  ['/admin/commodities', 'admin-commodities'],
  ['/admin/messages', 'admin-messages'],
  ['/admin/system', 'admin-system'],
  ['/i/:code', 'invite-landing'],
  ['/register', 'web-register'],
  ['/notifications', 'notifications'],
  ['/share-receive', 'share-receive'],
] as const

describe('UI route contract', () => {
  it('covers the exact 29 production routes without aliases or catalog routes', () => {
    expect(uiRouteContract).toHaveLength(29)
    expect(uiRouteContract.map(({ path, name }) => [path, name])).toEqual(expectedRoutes)
    expect(uiRouteContract.some(({ path }) => /catalog|design-system|storybook/.test(path))).toBe(
      false,
    )
  })

  it('gives every route a stable unique path, name, and test id', () => {
    for (const key of ['path', 'name', 'testId'] as const) {
      const values = uiRouteContract.map((route) => route[key])
      expect(new Set(values).size, `duplicate ${key}`).toBe(values.length)
    }

    expect(uiRouteContract.every(({ testId }) => /^route-[a-z0-9-]+$/.test(testId))).toBe(true)
    expect(uiRouteContractByPath.size).toBe(29)
    expect(getUiRouteContract('/admin/system')?.name).toBe('admin-system')
    expect(getUiRouteContract('/missing')).toBeUndefined()
  })

  it('locks the four completely protected routes', () => {
    expect(
      uiRouteContract
        .filter(({ protection }) => protection === UI_ROUTE_PROTECTION.FULL)
        .map(({ path }) => path),
    ).toEqual(['/market', '/chat', '/admin/channels', '/share-receive'])
  })

  it('locks protected interiors on the three mixed routes', () => {
    expect(
      Object.fromEntries(
        uiRouteContract
          .filter(({ protection }) => protection === UI_ROUTE_PROTECTION.MIXED)
          .map(({ path, protectedInteriors }) => [path, protectedInteriors]),
      ),
    ).toEqual({
      '/': ['home-market-widget'],
      '/admin/messages': ['admin-messages-market-delivery', 'admin-messages-messenger-delivery'],
      '/admin/system': ['trading-settings-market-controls'],
    })
  })

  it('keeps V2 off on every product route during Stage 2', () => {
    expect(uiRouteContract.every(({ v2Scope }) => v2Scope === UI_V2_SCOPE.OFF)).toBe(true)
  })

  it('keeps the machine-readable scope manifest in exact parity', () => {
    expect(scopeManifest).toMatchObject({
      schemaVersion: 1,
      stage: 2,
      mode: 'opt-in',
      scopeSelector: '[data-ui-system="v2"]',
      tokenPrefix: '--ui-v2-',
      legacyTokenPrefix: '--ds-',
      productionCatalogRoute: null,
    })
    expect(scopeManifest.routes).toEqual(JSON.parse(JSON.stringify(uiRouteContract)))
  })
})
