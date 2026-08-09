import { describe, expect, it } from 'vitest'
import scopeManifest from '../design-system-v2/scope-manifest.json'
import {
  UI_ROUTE_PROTECTION,
  UI_ROUTE_SHELL,
  UI_V2_SCOPE,
  getUiRouteContract,
  getUiRouteContractByName,
  uiRouteContract,
  uiRouteContractByName,
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
  ['/:pathMatch(.*)*', 'system-recovery'],
] as const

describe('UI route contract', () => {
  it('covers the exact 30 production routes with the recovery catch-all last', () => {
    expect(uiRouteContract).toHaveLength(30)
    expect(uiRouteContract.map(({ path, name }) => [path, name])).toEqual(expectedRoutes)
    expect(uiRouteContract.at(-1)).toMatchObject({
      path: '/:pathMatch(.*)*',
      name: 'system-recovery',
    })
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
    expect(uiRouteContractByPath.size).toBe(30)
    expect(uiRouteContractByName.size).toBe(30)
    expect(getUiRouteContract('/admin/system')?.name).toBe('admin-system')
    expect(getUiRouteContract('/missing')).toBeUndefined()
    expect(getUiRouteContract('/i/ABC123')).toBeUndefined()
    expect(getUiRouteContractByName('invite-landing')?.path).toBe('/i/:code')
    expect(getUiRouteContractByName('system-recovery')?.path).toBe('/:pathMatch(.*)*')
    expect(getUiRouteContractByName(Symbol('login'))).toBeUndefined()
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

  it('locks Stage 4 activation to 10 route, 16 section, and 4 off routes', () => {
    expect(
      Object.fromEntries(
        Object.values(UI_V2_SCOPE).map((scope) => [
          scope,
          uiRouteContract.filter(({ v2Scope }) => v2Scope === scope).length,
        ]),
      ),
    ).toEqual({ off: 4, section: 16, route: 10 })

    expect(
      uiRouteContract
        .filter(({ v2Scope }) => v2Scope === UI_V2_SCOPE.ROUTE)
        .map(({ path }) => path),
    ).toEqual([
      '/setup-password',
      '/login',
      '/operations',
      '/account',
      '/account/security',
      '/account/storage',
      '/account/notifications',
      '/i/:code',
      '/register',
      '/:pathMatch(.*)*',
    ])

    expect(
      uiRouteContract.filter(({ v2Scope }) => v2Scope === UI_V2_SCOPE.OFF).map(({ path }) => path),
    ).toEqual(['/market', '/chat', '/admin/channels', '/share-receive'])
  })

  it('locks the five shell families and their exact route counts', () => {
    expect(
      Object.fromEntries(
        Object.values(UI_ROUTE_SHELL).map((shellClass) => [
          shellClass,
          uiRouteContract.filter((route) => route.shellClass === shellClass).length,
        ]),
      ),
    ).toEqual({
      public: 3,
      'focused-authenticated': 1,
      'standard-authenticated': 21,
      'protected-legacy': 4,
      'system-recovery': 1,
    })

    expect(
      uiRouteContract
        .filter(({ shellClass }) => shellClass === UI_ROUTE_SHELL.PUBLIC)
        .map(({ path }) => path),
    ).toEqual(['/login', '/i/:code', '/register'])
    expect(getUiRouteContractByName('setup-password')?.shellClass).toBe(
      UI_ROUTE_SHELL.FOCUSED_AUTHENTICATED,
    )
    expect(getUiRouteContractByName('system-recovery')?.shellClass).toBe(
      UI_ROUTE_SHELL.SYSTEM_RECOVERY,
    )
  })

  it('keeps the machine-readable scope manifest in exact parity', () => {
    expect(scopeManifest).toMatchObject({
      schemaVersion: 3,
      stage: 4,
      mode: 'opt-in',
      scopeSelector: '[data-ui-system="v2"]',
      tokenPrefix: '--ui-v2-',
      legacyTokenPrefix: '--ds-',
      productionCatalogRoute: null,
    })
    expect(scopeManifest.routes).toEqual(JSON.parse(JSON.stringify(uiRouteContract)))
  })
})
