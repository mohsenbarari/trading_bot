export const UI_V2_SCOPE = {
  OFF: 'off',
  SECTION: 'section',
  ROUTE: 'route',
} as const

export type UiV2Scope = (typeof UI_V2_SCOPE)[keyof typeof UI_V2_SCOPE]

export const UI_ROUTE_SHELL = {
  PUBLIC: 'public',
  FOCUSED_AUTHENTICATED: 'focused-authenticated',
  STANDARD_AUTHENTICATED: 'standard-authenticated',
  PROTECTED_LEGACY: 'protected-legacy',
  SYSTEM_RECOVERY: 'system-recovery',
} as const

export type UiRouteShellClass = (typeof UI_ROUTE_SHELL)[keyof typeof UI_ROUTE_SHELL]

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
  readonly shellClass: UiRouteShellClass
  readonly protection: UiRouteProtection
  readonly protectedInteriors: readonly ProtectedUiInterior[]
  readonly v2Scope: UiV2Scope
}

/**
 * `full` means the complete route is outside the UIUX v2 migration surface.
 * `mixed` means only the named interiors are protected. Stage 4 may activate
 * only explicit surrounding sections on those routes. `v2Scope` cannot become
 * `route` for mixed routes.
 */
export const uiRouteContract = [
  {
    path: '/',
    name: 'home',
    testId: 'route-home',
    shellClass: UI_ROUTE_SHELL.STANDARD_AUTHENTICATED,
    protection: UI_ROUTE_PROTECTION.MIXED,
    protectedInteriors: ['home-market-widget'],
    v2Scope: UI_V2_SCOPE.SECTION,
  },
  {
    path: '/setup-password',
    name: 'setup-password',
    testId: 'route-setup-password',
    shellClass: UI_ROUTE_SHELL.FOCUSED_AUTHENTICATED,
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.ROUTE,
  },
  {
    path: '/login',
    name: 'login',
    testId: 'route-login',
    shellClass: UI_ROUTE_SHELL.PUBLIC,
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.ROUTE,
  },
  {
    path: '/market',
    name: 'market',
    testId: 'route-market',
    shellClass: UI_ROUTE_SHELL.PROTECTED_LEGACY,
    protection: UI_ROUTE_PROTECTION.FULL,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/operations',
    name: 'operations',
    testId: 'route-operations',
    shellClass: UI_ROUTE_SHELL.STANDARD_AUTHENTICATED,
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.ROUTE,
  },
  {
    path: '/operations/customers',
    name: 'operations-customers',
    testId: 'route-operations-customers',
    shellClass: UI_ROUTE_SHELL.STANDARD_AUTHENTICATED,
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.SECTION,
  },
  {
    path: '/operations/customers/:relationId',
    name: 'operations-customers-detail',
    testId: 'route-operations-customers-detail',
    shellClass: UI_ROUTE_SHELL.STANDARD_AUTHENTICATED,
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.SECTION,
  },
  {
    path: '/operations/accountants',
    name: 'operations-accountants',
    testId: 'route-operations-accountants',
    shellClass: UI_ROUTE_SHELL.STANDARD_AUTHENTICATED,
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.SECTION,
  },
  {
    path: '/operations/accountants/:relationId',
    name: 'operations-accountants-detail',
    testId: 'route-operations-accountants-detail',
    shellClass: UI_ROUTE_SHELL.STANDARD_AUTHENTICATED,
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.SECTION,
  },
  {
    path: '/account',
    name: 'account',
    testId: 'route-account',
    shellClass: UI_ROUTE_SHELL.STANDARD_AUTHENTICATED,
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.ROUTE,
  },
  {
    path: '/account/security',
    name: 'account-security',
    testId: 'route-account-security',
    shellClass: UI_ROUTE_SHELL.STANDARD_AUTHENTICATED,
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.ROUTE,
  },
  {
    path: '/account/storage',
    name: 'account-storage',
    testId: 'route-account-storage',
    shellClass: UI_ROUTE_SHELL.STANDARD_AUTHENTICATED,
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.ROUTE,
  },
  {
    path: '/account/notifications',
    name: 'account-notifications',
    testId: 'route-account-notifications',
    shellClass: UI_ROUTE_SHELL.STANDARD_AUTHENTICATED,
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.ROUTE,
  },
  {
    path: '/chat',
    name: 'messenger',
    testId: 'route-messenger',
    shellClass: UI_ROUTE_SHELL.PROTECTED_LEGACY,
    protection: UI_ROUTE_PROTECTION.FULL,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/users/:id',
    name: 'public-profile',
    testId: 'route-public-profile',
    shellClass: UI_ROUTE_SHELL.STANDARD_AUTHENTICATED,
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.SECTION,
  },
  {
    path: '/profile',
    name: 'profile',
    testId: 'route-profile',
    shellClass: UI_ROUTE_SHELL.STANDARD_AUTHENTICATED,
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.SECTION,
  },
  {
    path: '/settings',
    name: 'settings',
    testId: 'route-settings',
    shellClass: UI_ROUTE_SHELL.STANDARD_AUTHENTICATED,
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.SECTION,
  },
  {
    path: '/admin',
    name: 'admin',
    testId: 'route-admin',
    shellClass: UI_ROUTE_SHELL.STANDARD_AUTHENTICATED,
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.SECTION,
  },
  {
    path: '/admin/invitations',
    name: 'admin-invitations',
    testId: 'route-admin-invitations',
    shellClass: UI_ROUTE_SHELL.STANDARD_AUTHENTICATED,
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.SECTION,
  },
  {
    path: '/admin/channels',
    name: 'admin-channels',
    testId: 'route-admin-channels',
    shellClass: UI_ROUTE_SHELL.PROTECTED_LEGACY,
    protection: UI_ROUTE_PROTECTION.FULL,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/admin/users',
    name: 'admin-users',
    testId: 'route-admin-users',
    shellClass: UI_ROUTE_SHELL.STANDARD_AUTHENTICATED,
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.SECTION,
  },
  {
    path: '/admin/users/:id',
    name: 'admin-user-profile',
    testId: 'route-admin-user-profile',
    shellClass: UI_ROUTE_SHELL.STANDARD_AUTHENTICATED,
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.SECTION,
  },
  {
    path: '/admin/commodities',
    name: 'admin-commodities',
    testId: 'route-admin-commodities',
    shellClass: UI_ROUTE_SHELL.STANDARD_AUTHENTICATED,
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.SECTION,
  },
  {
    path: '/admin/messages',
    name: 'admin-messages',
    testId: 'route-admin-messages',
    shellClass: UI_ROUTE_SHELL.STANDARD_AUTHENTICATED,
    protection: UI_ROUTE_PROTECTION.MIXED,
    protectedInteriors: ['admin-messages-market-delivery', 'admin-messages-messenger-delivery'],
    v2Scope: UI_V2_SCOPE.SECTION,
  },
  {
    path: '/admin/system',
    name: 'admin-system',
    testId: 'route-admin-system',
    shellClass: UI_ROUTE_SHELL.STANDARD_AUTHENTICATED,
    protection: UI_ROUTE_PROTECTION.MIXED,
    protectedInteriors: ['trading-settings-market-controls'],
    v2Scope: UI_V2_SCOPE.SECTION,
  },
  {
    path: '/i/:code',
    name: 'invite-landing',
    testId: 'route-invite-landing',
    shellClass: UI_ROUTE_SHELL.PUBLIC,
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.ROUTE,
  },
  {
    path: '/register',
    name: 'web-register',
    testId: 'route-web-register',
    shellClass: UI_ROUTE_SHELL.PUBLIC,
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.ROUTE,
  },
  {
    path: '/notifications',
    name: 'notifications',
    testId: 'route-notifications',
    shellClass: UI_ROUTE_SHELL.STANDARD_AUTHENTICATED,
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.SECTION,
  },
  {
    path: '/share-receive',
    name: 'share-receive',
    testId: 'route-share-receive',
    shellClass: UI_ROUTE_SHELL.PROTECTED_LEGACY,
    protection: UI_ROUTE_PROTECTION.FULL,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.OFF,
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'system-recovery',
    testId: 'route-system-recovery',
    shellClass: UI_ROUTE_SHELL.SYSTEM_RECOVERY,
    protection: UI_ROUTE_PROTECTION.NONE,
    protectedInteriors: [],
    v2Scope: UI_V2_SCOPE.ROUTE,
  },
] as const satisfies readonly UiRouteContractEntry[]

export const uiRouteContractByPath: ReadonlyMap<string, UiRouteContractEntry> = new Map(
  uiRouteContract.map((route) => [route.path, route] as const),
)

export const uiRouteContractByName: ReadonlyMap<string, UiRouteContractEntry> = new Map(
  uiRouteContract.map((route) => [route.name, route] as const),
)

export function getUiRouteContract(path: string): UiRouteContractEntry | undefined {
  return uiRouteContractByPath.get(path)
}

/** Resolve by route-record name so parameterized and catch-all paths stay stable. */
export function getUiRouteContractByName(
  name: string | symbol | null | undefined,
): UiRouteContractEntry | undefined {
  return typeof name === 'string' ? uiRouteContractByName.get(name) : undefined
}
