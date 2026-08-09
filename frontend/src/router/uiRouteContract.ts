export const UI_V2_SCOPE = {
  OFF: 'off',
  SECTION: 'section',
  ROUTE: 'route',
} as const

export type UiV2Scope = (typeof UI_V2_SCOPE)[keyof typeof UI_V2_SCOPE]

export const UI_ROUTE_PROTECTION = {
  NONE: 'none',
  FULL: 'full',
  MIXED: 'mixed',
} as const

export type UiRouteProtection = (typeof UI_ROUTE_PROTECTION)[keyof typeof UI_ROUTE_PROTECTION]

export type ProtectedUiInterior =
  | 'home-market-widget'
  | 'admin-messages-market-delivery'
  | 'admin-messages-messenger-delivery'
  | 'trading-settings-market-controls'

export interface UiRouteContractEntry {
  readonly path: string
  readonly name: string
  readonly testId: string
  readonly protection: UiRouteProtection
  readonly protectedInteriors: readonly ProtectedUiInterior[]
  readonly v2Scope: UiV2Scope
}

/**
 * Stage 2 is foundation-only: every production route remains on the legacy UI.
 *
 * `full` means the complete route is outside the UIUX v2 migration surface.
 * `mixed` means only the named interiors are protected when the route is
 * migrated in a later stage. `v2Scope` cannot become `route` for mixed routes.
 */
export const uiRouteContract = [
  {
    path: '/',
    name: 'home',
    testId: 'route-home',
    protection: UI_ROUTE_PROTECTION.MIXED,
    protectedInteriors: ['home-market-widget'],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/setup-password',
    name: 'setup-password',
    testId: 'route-setup-password',
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/login',
    name: 'login',
    testId: 'route-login',
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/market',
    name: 'market',
    testId: 'route-market',
    protection: UI_ROUTE_PROTECTION.FULL,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/operations',
    name: 'operations',
    testId: 'route-operations',
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/operations/customers',
    name: 'operations-customers',
    testId: 'route-operations-customers',
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/operations/customers/:relationId',
    name: 'operations-customers-detail',
    testId: 'route-operations-customers-detail',
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/operations/accountants',
    name: 'operations-accountants',
    testId: 'route-operations-accountants',
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/operations/accountants/:relationId',
    name: 'operations-accountants-detail',
    testId: 'route-operations-accountants-detail',
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/account',
    name: 'account',
    testId: 'route-account',
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/account/security',
    name: 'account-security',
    testId: 'route-account-security',
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/account/storage',
    name: 'account-storage',
    testId: 'route-account-storage',
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/account/notifications',
    name: 'account-notifications',
    testId: 'route-account-notifications',
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/chat',
    name: 'messenger',
    testId: 'route-messenger',
    protection: UI_ROUTE_PROTECTION.FULL,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/users/:id',
    name: 'public-profile',
    testId: 'route-public-profile',
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/profile',
    name: 'profile',
    testId: 'route-profile',
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/settings',
    name: 'settings',
    testId: 'route-settings',
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/admin',
    name: 'admin',
    testId: 'route-admin',
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/admin/invitations',
    name: 'admin-invitations',
    testId: 'route-admin-invitations',
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/admin/channels',
    name: 'admin-channels',
    testId: 'route-admin-channels',
    protection: UI_ROUTE_PROTECTION.FULL,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/admin/users',
    name: 'admin-users',
    testId: 'route-admin-users',
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/admin/users/:id',
    name: 'admin-user-profile',
    testId: 'route-admin-user-profile',
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/admin/commodities',
    name: 'admin-commodities',
    testId: 'route-admin-commodities',
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/admin/messages',
    name: 'admin-messages',
    testId: 'route-admin-messages',
    protection: UI_ROUTE_PROTECTION.MIXED,
    protectedInteriors: ['admin-messages-market-delivery', 'admin-messages-messenger-delivery'],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/admin/system',
    name: 'admin-system',
    testId: 'route-admin-system',
    protection: UI_ROUTE_PROTECTION.MIXED,
    protectedInteriors: ['trading-settings-market-controls'],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/i/:code',
    name: 'invite-landing',
    testId: 'route-invite-landing',
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/register',
    name: 'web-register',
    testId: 'route-web-register',
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/notifications',
    name: 'notifications',
    testId: 'route-notifications',
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/share-receive',
    name: 'share-receive',
    testId: 'route-share-receive',
    protection: UI_ROUTE_PROTECTION.FULL,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
] as const satisfies readonly UiRouteContractEntry[]

export const uiRouteContractByPath: ReadonlyMap<string, UiRouteContractEntry> = new Map(
  uiRouteContract.map((route) => [route.path, route] as const),
)

export function getUiRouteContract(path: string): UiRouteContractEntry | undefined {
  return uiRouteContractByPath.get(path)
}
