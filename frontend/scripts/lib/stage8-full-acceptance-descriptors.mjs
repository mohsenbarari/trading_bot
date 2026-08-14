import {
  ACCESS_PROFILE_COUNT,
  ALL_STATES,
  ENVIRONMENTS,
  INTERACTIONS,
  NA_TAXONOMY,
  NA_TAXONOMY_VALUES,
  VIEWPORTS,
} from './stage8-full-acceptance-constants.mjs'

const ROUTE_NAMES = Object.freeze([
  'home',
  'setup-password',
  'login',
  'market',
  'operations',
  'operations-customers',
  'operations-customers-detail',
  'operations-accountants',
  'operations-accountants-detail',
  'account',
  'account-security',
  'account-storage',
  'account-notifications',
  'messenger',
  'public-profile',
  'profile',
  'settings',
  'admin',
  'admin-invitations',
  'admin-channels',
  'admin-users',
  'admin-user-profile',
  'admin-commodities',
  'admin-messages',
  'admin-system',
  'invite-landing',
  'web-register',
  'notifications',
  'share-receive',
  'system-recovery',
])

function assertProductNaReason(reason) {
  if (/harness|fixture|injectable/i.test(reason)) {
    throw new Error(`product N/A reason must not cite harness/fixture/injectable: ${reason}`)
  }
}

function productNa(reason, file, symbol = '') {
  assertProductNaReason(reason)
  if (!file) throw new Error(`product N/A missing source file: ${reason}`)
  return {
    applicable: false,
    reason,
    taxonomy: NA_TAXONOMY.PRODUCT_NOT_APPLICABLE,
    source: { file, symbol },
  }
}

function canonicalNa(reason, targetRoute, file = 'frontend/src/router/index.ts', symbol = 'redirect') {
  return {
    applicable: false,
    reason,
    taxonomy: NA_TAXONOMY.CANONICAL_ALIAS,
    source: { file, symbol, targetRoute },
  }
}

function yes(spec = {}) {
  return { applicable: true, reason: null, taxonomy: null, source: null, ...spec }
}

function allCanonical(reason, targetRoute) {
  return Object.fromEntries(ALL_STATES.map((state) => [state, canonicalNa(reason, targetRoute)]))
}

function formOnly(surface, file, symbol = '') {
  return productNa(
    `${surface} is a form or status surface without list empty, dense, stale, or loading inventory.`,
    file,
    symbol,
  )
}

function authIdleNoPageData(surface, file, symbol = '') {
  return productNa(
    `${surface} idle step does not fetch page-data; error, offline, and slow UI are submit-bound.`,
    file,
    symbol,
  )
}

function noListInventory(surface, file, symbol, extra = '') {
  return productNa(
    `${surface} has no server list inventory in source.${extra ? ` ${extra}` : ''}`,
    file,
    symbol,
  )
}

function noSecondInFlightGet(surface, endpoint, extra, file, symbol) {
  return productNa(
    `${surface} cannot start a second in-flight GET to ${endpoint} from the success-path UI. ${extra}`,
    file,
    symbol,
  )
}

function identityPageData(selector, extra = {}) {
  return yes({
    endpoint: '/api/auth/me',
    identityPageData: true,
    selector,
    ...extra,
  })
}

function listStates({
  endpoint,
  itemSelector,
  emptySelector,
  loadingSelector,
  errorSelector,
  staleField,
  staleTrigger,
  retrySelector,
  settleSelector,
  staleApplicable = true,
  staleReason = '',
  staleRefreshSelector = '',
  staleSource = null,
}) {
  return {
    loading: yes({
      endpoint,
      selector: loadingSelector,
      settle: settleSelector,
    }),
    empty: yes({
      endpoint,
      selector: emptySelector,
      settle: emptySelector,
    }),
    normal: yes({ endpoint, selector: itemSelector, settle: itemSelector }),
    dense: yes({
      endpoint,
      selector: itemSelector,
      settle: itemSelector,
      minItems: 8,
    }),
    error: yes({
      endpoint,
      selector: errorSelector,
      retry: retrySelector,
      settle: errorSelector,
    }),
    slow: yes({
      endpoint,
      selector: loadingSelector,
      settle: settleSelector,
    }),
    offline: yes({
      endpoint,
      selector: errorSelector,
      settle: errorSelector,
    }),
    stale: staleApplicable
      ? yes({
          endpoint,
          field: staleField,
          trigger: staleTrigger,
          selector: itemSelector,
          refreshSelector: staleRefreshSelector,
        })
      : productNa(staleReason, staleSource.file, staleSource.symbol),
  }
}

const REDIRECT_STATES = allCanonical(
  'Router redirect record; states execute on the canonical target route.',
  'account-notifications',
)

const DESCRIPTORS = {
  home: {
    renderProfileId: 'member',
    canonical: null,
    states: {
      loading: identityPageData('.ds-loading-state, .ui-loading-state', {
        settle: '.dashboard-content, .ds-loading-state',
      }),
      empty: noListInventory(
        'home / DashboardView',
        'frontend/src/views/DashboardView.vue',
        'DashboardView',
        'Offer cards belong to MarketView, not the home identity surface.',
      ),
      normal: yes({ settle: '.dashboard-content, .ui-v2-home-top' }),
      dense: noListInventory(
        'home / DashboardView',
        'frontend/src/views/DashboardView.vue',
        'DashboardView',
        'Offer density belongs to MarketView, not the home identity surface.',
      ),
      error: identityPageData('.dashboard-identity-error, [role="alert"]'),
      slow: identityPageData('.ds-loading-state, .ui-loading-state'),
      offline: productNa(
        'DashboardView offline identity UI is bound to navigator.onLine and the window offline event, not a page-data GET failure.',
        'frontend/src/views/DashboardView.vue',
        'handleBrowserOffline',
      ),
      stale: noListInventory(
        'home / DashboardView',
        'frontend/src/views/DashboardView.vue',
        'identityState',
        'The stale notice is identity freshness, not a list stale-overwrite race.',
      ),
    },
    touch: yes({
      selector: 'button.ui-v2-home-identity, .ui-v2-home-identity',
      expectedName: 'profile',
      allowNavigation: true,
    }),
    zoom: { internalStrip: null },
  },
  'setup-password': {
    renderProfileId: 'member',
    canonical: null,
    states: {
      loading: formOnly('setup-password', 'frontend/src/views/SetupPassword.vue', 'SetupPassword'),
      empty: formOnly('setup-password', 'frontend/src/views/SetupPassword.vue', 'SetupPassword'),
      normal: yes({ settle: '.ui-v2-auth-password-toggle, form' }),
      dense: formOnly('setup-password', 'frontend/src/views/SetupPassword.vue', 'SetupPassword'),
      error: authIdleNoPageData(
        'setup-password',
        'frontend/src/views/SetupPassword.vue',
        'SetupPassword',
      ),
      slow: authIdleNoPageData(
        'setup-password',
        'frontend/src/views/SetupPassword.vue',
        'SetupPassword',
      ),
      offline: authIdleNoPageData(
        'setup-password',
        'frontend/src/views/SetupPassword.vue',
        'SetupPassword',
      ),
      stale: formOnly('setup-password', 'frontend/src/views/SetupPassword.vue', 'SetupPassword'),
    },
    touch: yes({
      selector: '.ui-v2-auth-password-toggle',
      expectedName: 'setup-password',
    }),
    zoom: { internalStrip: null },
  },
  login: {
    renderProfileId: 'guest',
    canonical: null,
    states: {
      loading: formOnly('login', 'frontend/src/views/LoginView.vue', 'LoginView'),
      empty: formOnly('login', 'frontend/src/views/LoginView.vue', 'LoginView'),
      normal: yes({ settle: '.ui-v2-auth-login-step, [data-ui-system="v2"]' }),
      dense: formOnly('login', 'frontend/src/views/LoginView.vue', 'LoginView'),
      error: authIdleNoPageData('login', 'frontend/src/views/LoginView.vue', 'LoginView'),
      slow: authIdleNoPageData('login', 'frontend/src/views/LoginView.vue', 'LoginView'),
      offline: authIdleNoPageData('login', 'frontend/src/views/LoginView.vue', 'LoginView'),
      stale: formOnly('login', 'frontend/src/views/LoginView.vue', 'LoginView'),
    },
    touch: productNa(
      'LoginView idle step only exposes OTP request and optional developer shortcut; there is no source-safe non-mutating activation.',
      'frontend/src/views/LoginView.vue',
      'LoginView',
    ),
    zoom: { internalStrip: null },
  },
  market: {
    renderProfileId: 'member',
    canonical: null,
    states: {
      loading: yes({
        endpoint: '/api/offers/page',
        selector: '[data-test="offers-loading-skeleton"], .skeleton-card, .offers-list',
        settle: '.offers-list, .offer-card, [data-test="offers-loading-skeleton"]',
      }),
      empty: yes({
        endpoint: '/api/offers/page',
        selector: '[data-test="offers-empty-state"], .empty-state',
        settle: '[data-test="offers-empty-state"], .empty-state',
      }),
      normal: yes({
        endpoint: '/api/offers/page',
        selector: '[data-test="offer-card"], .offer-card-wrap, .offers-list',
      }),
      dense: yes({
        endpoint: '/api/offers/page',
        selector: '[data-test="offer-card"], .offer-card-wrap, .offers-list',
        minItems: 8,
      }),
      error: yes({
        endpoint: '/api/offers/page',
        selector: '.active-load-error, [role="alert"]',
      }),
      slow: yes({
        endpoint: '/api/offers/page',
        selector: '[data-test="offers-loading-skeleton"], .skeleton-card',
      }),
      offline: yes({
        endpoint: '/api/offers/page',
        selector: '.active-load-error, [role="alert"]',
      }),
      stale: noSecondInFlightGet(
        'market / useOffers.fetchOffers',
        '/api/offers/page',
        'isFetching queues a later refresh; a second GET does not start until the first settles, so a late stale overwrite cannot occur.',
        'frontend/src/composables/useOffers.ts',
        'isFetching',
      ),
    },
    touch: productNa(
      'MarketView hides the standard bottom nav and the remaining FAB nav is not a one-click non-mutating target.',
      'frontend/src/views/MarketView.vue',
      'MarketView',
    ),
    zoom: { internalStrip: '.offers-list, .app-route-scroll' },
  },
  operations: {
    renderProfileId: 'member',
    canonical: null,
    states: {
      loading: identityPageData('.operations-identity-loading, .ui-loading-state', {
        settle: '.operations-action-tile, .operations-empty-state, .operations-identity-loading',
      }),
      empty: productNa(
        'OperationsView empty copy is role-action absence, not a server list inventory.',
        'frontend/src/views/OperationsView.vue',
        'OperationsView',
      ),
      normal: yes({ settle: '.operations-action-tile, .operations-empty-state, .workspace-shell' }),
      dense: noListInventory(
        'operations hub / OperationsView',
        'frontend/src/views/OperationsView.vue',
        'OperationsView',
      ),
      error: identityPageData('.operations-identity-error, [role="alert"]'),
      slow: identityPageData('.operations-identity-loading, .ui-loading-state'),
      offline: productNa(
        'OperationsView identity machine has loading, ready, stale, and error only; there is no offline identity contract.',
        'frontend/src/views/OperationsView.vue',
        'identityState',
      ),
      stale: noListInventory(
        'operations hub / OperationsView',
        'frontend/src/views/OperationsView.vue',
        'identityState',
      ),
    },
    touch: yes({
      selector: '.operations-action-tile, .operations-empty-state .ui-button, .hub-action',
      expectedNameAny: ['operations-customers', 'operations-accountants', 'account', 'admin'],
      allowNavigation: true,
    }),
    zoom: { internalStrip: null },
  },
  'operations-customers': {
    renderProfileId: 'member',
    canonical: null,
    states: listStates({
      endpoint: '/api/customers/owner-relations',
      itemSelector: '.ui-list-item, .customer-pending-card, .workspace-relation-list',
      emptySelector: '.ui-empty-state',
      loadingSelector: '.ui-loading-state',
      errorSelector: '[role="alert"], .ui-empty-state--danger',
      staleField: 'management_name',
      staleTrigger: 'in-page-refresh',
      retrySelector: '.customer-detail-retry, button:has-text("تلاش")',
      settleSelector: '.workspace-relation-list, .ui-empty-state, .ui-loading-state',
      staleApplicable: false,
      staleReason:
        'operations-customers cannot start a second in-flight GET to /api/customers/owner-relations from the success-path UI. loadRelations(true) is bound only to error-path تلاش دوباره, and overlapping calls abort via relationsRequestGeneration.',
      staleSource: {
        file: 'frontend/src/views/CustomerWorkspaceView.vue',
        symbol: 'relationsRequestGeneration',
      },
    }),
    touch: yes({
      selector: '.ui-list-item, .customer-pending-card, a[href^="/operations"]',
      expectedName: 'operations-customers-detail',
      expectedNameAny: ['operations-customers', 'operations-customers-detail'],
    }),
    zoom: { internalStrip: '.workspace-relation-list' },
  },
  'operations-customers-detail': {
    renderProfileId: 'member',
    canonical: null,
    states: {
      loading: yes({
        endpoint: '/api/customers/owner-relations',
        selector: '.ui-loading-state',
      }),
      empty: noListInventory(
        'customer detail',
        'frontend/src/views/CustomerWorkspaceView.vue',
        'CustomerWorkspaceView',
        'Detail shows one relation, not empty or dense lists.',
      ),
      normal: yes({ settle: '.customer-detail-shell, .ui-empty-state, .ui-loading-state' }),
      dense: noListInventory(
        'customer detail',
        'frontend/src/views/CustomerWorkspaceView.vue',
        'CustomerWorkspaceView',
      ),
      error: yes({
        endpoint: '/api/customers/owner-relations',
        selector: '[role="alert"], .ui-empty-state--danger',
        retry: '.customer-detail-retry',
      }),
      slow: yes({
        endpoint: '/api/customers/owner-relations',
        selector: '.ui-loading-state',
      }),
      offline: yes({
        endpoint: '/api/customers/owner-relations',
        selector: '[role="alert"], .ui-empty-state--danger',
      }),
      stale: noSecondInFlightGet(
        'operations-customers-detail / loadRelations',
        '/api/customers/owner-relations',
        'The detail route first fetches the collection; success-path UI has no second identical GET, and overlapping calls abort via relationsRequestGeneration.',
        'frontend/src/views/CustomerWorkspaceView.vue',
        'relationsRequestGeneration',
      ),
    },
    touch: yes({
      selector: '.ds-workspace-back, button:has-text("بازگشت")',
      expectedNameAny: ['operations-customers', 'operations-customers-detail'],
    }),
    zoom: { internalStrip: '.customer-detail-shell' },
  },
  'operations-accountants': {
    renderProfileId: 'member',
    canonical: null,
    states: listStates({
      endpoint: '/api/accountants/owner-relations',
      itemSelector: '.ui-list-item, .accountant-pending-card, .workspace-relation-list',
      emptySelector: '.ui-empty-state',
      loadingSelector: '.ui-loading-state',
      errorSelector: '[role="alert"], .ui-empty-state--danger',
      staleField: 'management_name',
      staleTrigger: 'in-page-refresh',
      retrySelector: 'button:has-text("تلاش")',
      settleSelector: '.workspace-relation-list, .ui-empty-state, .ui-loading-state',
      staleApplicable: false,
      staleReason:
        'operations-accountants cannot start a second in-flight GET to /api/accountants/owner-relations from the success-path UI. loadRelations(true) is bound only to error-path تلاش دوباره, and overlapping calls abort the previous controller.',
      staleSource: {
        file: 'frontend/src/views/AccountantWorkspaceView.vue',
        symbol: 'loadRelations',
      },
    }),
    touch: yes({
      selector: '.ui-list-item, .accountant-pending-card',
      expectedNameAny: ['operations-accountants', 'operations-accountants-detail'],
    }),
    zoom: { internalStrip: '.workspace-relation-list' },
  },
  'operations-accountants-detail': {
    renderProfileId: 'member',
    canonical: null,
    states: {
      loading: yes({
        endpoint: '/api/accountants/owner-relations',
        selector: '.ui-loading-state',
      }),
      empty: noListInventory(
        'accountant detail',
        'frontend/src/views/AccountantWorkspaceView.vue',
        'AccountantWorkspaceView',
      ),
      normal: yes({ settle: '.ui-empty-state, .ui-loading-state, .app-route-v2-scope' }),
      dense: noListInventory(
        'accountant detail',
        'frontend/src/views/AccountantWorkspaceView.vue',
        'AccountantWorkspaceView',
      ),
      error: yes({
        endpoint: '/api/accountants/owner-relations',
        selector: '[role="alert"], .ui-empty-state--danger',
      }),
      slow: yes({
        endpoint: '/api/accountants/owner-relations',
        selector: '.ui-loading-state',
      }),
      offline: yes({
        endpoint: '/api/accountants/owner-relations',
        selector: '[role="alert"], .ui-empty-state--danger',
      }),
      stale: noSecondInFlightGet(
        'operations-accountants-detail / loadRelations',
        '/api/accountants/owner-relations',
        'The detail route first fetches the collection; success-path UI has no second identical GET, and overlapping calls abort the previous controller.',
        'frontend/src/views/AccountantWorkspaceView.vue',
        'loadRelations',
      ),
    },
    touch: yes({
      selector: '.ds-workspace-back, button:has-text("بازگشت")',
      expectedNameAny: ['operations-accountants', 'operations-accountants-detail'],
    }),
    zoom: { internalStrip: null },
  },
  account: {
    renderProfileId: 'member',
    canonical: null,
    states: {
      loading: identityPageData('.account-identity-loading, .ui-loading-state', {
        settle: '.hub-action, .account-section-card, .account-identity-loading',
      }),
      empty: noListInventory(
        'account hub / AccountHubView',
        'frontend/src/views/AccountHubView.vue',
        'AccountHubView',
      ),
      normal: yes({ settle: '.hub-action, .account-section-card' }),
      dense: noListInventory(
        'account hub / AccountHubView',
        'frontend/src/views/AccountHubView.vue',
        'AccountHubView',
      ),
      error: identityPageData('.account-identity-error, [role="alert"]'),
      slow: identityPageData('.account-identity-loading, .ui-loading-state'),
      offline: productNa(
        'AccountHubView identity machine has loading, ready, stale, and error only; there is no offline identity contract.',
        'frontend/src/views/AccountHubView.vue',
        'identityState',
      ),
      stale: noListInventory(
        'account hub / AccountHubView',
        'frontend/src/views/AccountHubView.vue',
        'identityState',
      ),
    },
    touch: yes({
      selector: '.hub-action',
      expectedNameAny: ['settings', 'account-security', 'account-storage', 'account-notifications', 'profile'],
    }),
    zoom: { internalStrip: null },
  },
  'account-security': {
    renderProfileId: 'member',
    canonical: null,
    states: {
      ...listStates({
        endpoint: '/api/sessions/active',
        itemSelector: '.session-card, .sessions-list',
        emptySelector: '.ui-empty-state',
        loadingSelector: '.ui-loading-state',
        errorSelector: '.sessions-load-error, [role="alert"]',
        staleField: 'device_name',
        staleTrigger: 'in-page-refresh',
        retrySelector: '.sessions-retry',
        settleSelector: '.session-card, .ui-empty-state, .ui-loading-state',
      }),
      stale: productNa(
        'fetchSessions returns early while sessionsLoading is true and the success path has no second in-flight /api/sessions/active request.',
        'frontend/src/views/SettingsView.vue',
        'sessionsLoading',
      ),
    },
    touch: yes({
      selector: '.settings-return-control',
      expectedName: 'account',
    }),
    zoom: { internalStrip: '.sessions-list' },
  },
  'account-storage': {
    renderProfileId: 'member',
    canonical: null,
    states: {
      loading: noListInventory(
        'account-storage',
        'frontend/src/views/SettingsView.vue',
        'isStorageRoute',
        'Cache size is measured locally, not from a server list.',
      ),
      empty: noListInventory(
        'account-storage',
        'frontend/src/views/SettingsView.vue',
        'isStorageRoute',
      ),
      normal: yes({ settle: '.storage-value, .ui-v2-settings-page' }),
      dense: noListInventory(
        'account-storage',
        'frontend/src/views/SettingsView.vue',
        'isStorageRoute',
      ),
      error: noListInventory(
        'account-storage',
        'frontend/src/views/SettingsView.vue',
        'isStorageRoute',
      ),
      slow: noListInventory(
        'account-storage',
        'frontend/src/views/SettingsView.vue',
        'isStorageRoute',
      ),
      offline: noListInventory(
        'account-storage',
        'frontend/src/views/SettingsView.vue',
        'isStorageRoute',
      ),
      stale: noListInventory(
        'account-storage',
        'frontend/src/views/SettingsView.vue',
        'isStorageRoute',
      ),
    },
    touch: yes({
      selector: '.settings-return-control',
      expectedName: 'account',
    }),
    zoom: { internalStrip: null },
  },
  'account-notifications': {
    renderProfileId: 'member',
    canonical: null,
    states: listStates({
      endpoint: '/api/notifications',
      itemSelector: '.notif-item, .notification-item, [data-test*="notification"]',
      emptySelector: '.ui-empty-state, .ds-empty-state',
      loadingSelector: '.ds-loading-state, .ui-loading-state',
      errorSelector: '[role="alert"], .ui-empty-state--danger',
      staleField: 'title',
      staleTrigger: 'in-page-refresh',
      retrySelector: 'button:has-text("تلاش")',
      settleSelector: '.ui-empty-state, .notification-item, .ds-loading-state',
      staleApplicable: false,
      staleReason:
        'account-notifications cannot start a second in-flight GET to /api/notifications from the success-path UI. fetchHistory coalesces on activeHistoryRequest, so a later open/retry returns the same in-flight promise.',
      staleSource: {
        file: 'frontend/src/stores/notifications.ts',
        symbol: 'activeHistoryRequest',
      },
    }),
    touch: yes({
      selector: 'a[href="/account"], .hub-action, [href="/account"]',
      expectedNameAny: ['account', 'account-notifications'],
    }),
    zoom: { internalStrip: null },
  },
  messenger: {
    renderProfileId: 'member',
    canonical: null,
    states: {
      loading: yes({
        endpoint: '/api/chat/conversations',
        selector: '.loading-state, .messenger-loader, .ui-loading-state',
        settle: '.conversation-list-wrapper, .loading-state, .empty-state',
      }),
      empty: yes({
        endpoint: '/api/chat/conversations',
        selector: '.empty-state, .chat-empty-state',
        settle: '.empty-state, .conversation-list-wrapper',
      }),
      normal: yes({
        endpoint: '/api/chat/conversations',
        settle: '.conversation-list-wrapper, .app-route-scroll, #app',
      }),
      dense: yes({
        endpoint: '/api/chat/conversations',
        selector: '.conversation-card, .conversation-item',
        settle: '.conversation-card, .conversation-list-wrapper',
        minItems: 8,
      }),
      error: yes({
        endpoint: '/api/chat/conversations',
        selector: '.error-state, [role="alert"]',
        retry: 'button:has-text("تلاش")',
      }),
      slow: yes({
        endpoint: '/api/chat/conversations',
        selector: '.loading-state, .messenger-loader, .ui-loading-state',
      }),
      offline: productNa(
        'ChatView conversation failure renders .error-state; there is no separate offline list contract.',
        'frontend/src/components/ChatView.vue',
        'error',
      ),
      stale: productNa(
        'ChatView loadConversations runs once on mount; the success-path list has no in-page refresh that starts a second /api/chat/conversations GET.',
        'frontend/src/composables/chat/useChatMessages.ts',
        'loadConversations',
      ),
    },
    touch: yes({
      selector: 'button.header-btn.back-btn',
      expectedName: 'home',
      allowNavigation: true,
    }),
    zoom: { internalStrip: '.conversations-list, .app-route-scroll' },
  },
  'public-profile': {
    renderProfileId: 'member',
    canonical: null,
    states: {
      loading: yes({
        endpoint: '/api/users-public/9101',
        selector: '.loading-state-skeleton, .skeleton-box, .skeleton-text-header',
      }),
      empty: noListInventory(
        'public-profile',
        'frontend/src/views/PublicProfileView.vue',
        'PublicProfileView',
      ),
      normal: yes({ settle: '.public-profile, .app-route-v2-scope' }),
      dense: noListInventory(
        'public-profile',
        'frontend/src/views/PublicProfileView.vue',
        'PublicProfileView',
      ),
      error: yes({ selector: '[role="alert"]' }),
      slow: yes({
        endpoint: '/api/users-public/9101',
        selector: '.loading-state-skeleton, .skeleton-box',
      }),
      offline: yes({ selector: '[role="alert"]' }),
      stale: noListInventory(
        'public-profile',
        'frontend/src/views/PublicProfileView.vue',
        'PublicProfileView',
      ),
    },
    touch: yes({
      selector: 'a[href="/"], .bottom-nav-wrapper a, button[aria-label*="بازگشت"]',
      expectedNameAny: ['home', 'public-profile', 'account'],
    }),
    zoom: { internalStrip: null },
  },
  profile: {
    renderProfileId: 'member',
    canonical: null,
    states: {
      loading: identityPageData('.loading-container, .ui-loading-state', {
        settle: '.app-route-v2-scope, .loading-container',
      }),
      empty: noListInventory('profile', 'frontend/src/views/ProfileView.vue', 'ProfileView'),
      normal: yes({ settle: '.app-route-v2-scope, main' }),
      dense: noListInventory('profile', 'frontend/src/views/ProfileView.vue', 'ProfileView'),
      error: identityPageData('.profile-load-error, [role="alert"]'),
      slow: identityPageData('.loading-container, .ui-loading-state'),
      offline: productNa(
        'ProfileView catch path shows a generic load error; there is no distinct offline identity contract.',
        'frontend/src/views/ProfileView.vue',
        'loadCurrentUser',
      ),
      stale: noListInventory('profile', 'frontend/src/views/ProfileView.vue', 'ProfileView'),
    },
    touch: yes({
      selector: 'a[href="/account"], .bottom-nav-wrapper a[href="/account"]',
      expectedNameAny: ['account', 'profile'],
    }),
    zoom: { internalStrip: null },
  },
  settings: {
    renderProfileId: 'member',
    canonical: null,
    states: {
      loading: noListInventory(
        'general /settings',
        'frontend/src/views/SettingsView.vue',
        'SettingsView',
        'Session loading, empty, dense, and stale belong to /account/security, not the general settings route.',
      ),
      empty: noListInventory(
        'general /settings',
        'frontend/src/views/SettingsView.vue',
        'SettingsView',
        'The general route renders overtime or a role notice, not a session list.',
      ),
      normal: yes({ settle: '.settings-overtime-card, .settings-role-notice, .ui-v2-settings-page' }),
      dense: noListInventory(
        'general /settings',
        'frontend/src/views/SettingsView.vue',
        'SettingsView',
        'The general route renders overtime or a role notice, not a session list.',
      ),
      error: productNa(
        'SettingsView overtime panel reads cached identity; error UI is save-bound, not a page-data GET.',
        'frontend/src/views/SettingsView.vue',
        'SettingsView',
      ),
      slow: noListInventory(
        'general /settings',
        'frontend/src/views/SettingsView.vue',
        'SettingsView',
        'Overtime preference has no page-level loading skeleton.',
      ),
      offline: productNa(
        'SettingsView overtime panel reads cached identity; offline UI is save-bound, not a page-data GET.',
        'frontend/src/views/SettingsView.vue',
        'SettingsView',
      ),
      stale: noListInventory(
        'general /settings',
        'frontend/src/views/SettingsView.vue',
        'SettingsView',
        'No displayed session or list field exists on the general settings route.',
      ),
    },
    touch: yes({
      selector: '.settings-return-control',
      expectedName: 'account',
    }),
    zoom: { internalStrip: null },
  },
  admin: {
    renderProfileId: 'middle-admin',
    canonical: null,
    states: {
      loading: productNa(
        'AdminView menu is a static section grid after identity bootstrap.',
        'frontend/src/views/AdminView.vue',
        'AdminView',
      ),
      empty: noListInventory('admin menu', 'frontend/src/views/AdminView.vue', 'AdminView'),
      normal: yes({ settle: '.app-route-v2-scope, main' }),
      dense: noListInventory('admin menu', 'frontend/src/views/AdminView.vue', 'AdminView'),
      error: productNa(
        'AdminView menu does not fetch a list inventory.',
        'frontend/src/views/AdminView.vue',
        'AdminView',
      ),
      slow: productNa(
        'AdminView menu is a static section grid after identity bootstrap.',
        'frontend/src/views/AdminView.vue',
        'AdminView',
      ),
      offline: productNa(
        'AdminView menu does not fetch a list inventory.',
        'frontend/src/views/AdminView.vue',
        'AdminView',
      ),
      stale: noListInventory('admin menu', 'frontend/src/views/AdminView.vue', 'AdminView'),
    },
    touch: yes({
      selector: '.admin-panel-action',
      expectedNameAny: ['admin', 'admin-invitations', 'admin-users'],
    }),
    zoom: { internalStrip: null },
  },
  'admin-invitations': {
    renderProfileId: 'middle-admin',
    canonical: null,
    states: listStates({
      endpoint: '/api/invitations/pending',
      itemSelector: '.pending-row',
      emptySelector: '.ui-empty-state',
      loadingSelector: '.ui-loading-state',
      errorSelector: '[role="alert"], .ui-empty-state--danger',
      staleField: 'account_name',
      staleTrigger: 'in-page-refresh',
      retrySelector: 'button:has-text("تلاش")',
      settleSelector: '.pending-row, .ui-empty-state, .ui-loading-state',
      staleApplicable: false,
      staleReason:
        'admin-invitations cannot start a second in-flight GET to /api/invitations/pending from the success-path UI. pending-refresh-btn stays in the loading state for the whole request, so a later GET cannot overlap the first.',
      staleSource: {
        file: 'frontend/src/components/CreateInvitationView.vue',
        symbol: 'pending-refresh-btn',
      },
    }),
    touch: yes({
      selector: '.admin-subview-return',
      expectedName: 'admin',
    }),
    zoom: { internalStrip: null },
  },
  'admin-channels': {
    renderProfileId: 'senior-admin',
    canonical: { profileId: 'middle-admin', finalName: 'admin', finalPath: '/admin' },
    states: {
      loading: formOnly(
        'admin-channels / CreateChannel',
        'frontend/src/components/CreateChannelView.vue',
        'CreateChannelView',
      ),
      empty: formOnly(
        'admin-channels / CreateChannel',
        'frontend/src/components/CreateChannelView.vue',
        'CreateChannelView',
      ),
      normal: yes({ settle: '.app-route-scroll, #app' }),
      dense: formOnly(
        'admin-channels / CreateChannel',
        'frontend/src/components/CreateChannelView.vue',
        'CreateChannelView',
      ),
      error: formOnly(
        'admin-channels / CreateChannel',
        'frontend/src/components/CreateChannelView.vue',
        'CreateChannelView',
      ),
      slow: formOnly(
        'admin-channels / CreateChannel',
        'frontend/src/components/CreateChannelView.vue',
        'CreateChannelView',
      ),
      offline: formOnly(
        'admin-channels / CreateChannel',
        'frontend/src/components/CreateChannelView.vue',
        'CreateChannelView',
      ),
      stale: formOnly(
        'admin-channels / CreateChannel',
        'frontend/src/components/CreateChannelView.vue',
        'CreateChannelView',
      ),
    },
    touch: yes({
      selector: '.admin-subview-return',
      expectedName: 'admin',
    }),
    zoom: { internalStrip: null },
  },
  'admin-users': {
    renderProfileId: 'middle-admin',
    canonical: null,
    states: listStates({
      endpoint: '/api/users/',
      itemSelector: '.user-item, .ui-list-item',
      emptySelector: '.ui-empty-state, .users-result',
      loadingSelector: '.ui-loading-state, [aria-busy="true"]',
      errorSelector: '.user-refresh-error, [role="alert"]',
      staleField: 'account_name',
      staleTrigger: 'in-page-refresh',
      retrySelector: 'button:has-text("تلاش")',
      settleSelector: '.user-item, .ui-empty-state, [aria-busy="true"]',
      staleApplicable: false,
      staleReason:
        'admin-users cannot start a second in-flight GET to /api/users/ from the success-path UI. retryUsers is error-path only, and fetchUsers abort-gates overlapping calls with usersRequestSequence.',
      staleSource: {
        file: 'frontend/src/components/UserManager.vue',
        symbol: 'usersRequestSequence',
      },
    }),
    touch: yes({
      selector: '.user-item, .ui-list-item',
      expectedNameAny: ['admin-users', 'admin-user-profile'],
    }),
    zoom: { internalStrip: '.users-result' },
  },
  'admin-user-profile': {
    renderProfileId: 'middle-admin',
    canonical: null,
    states: {
      loading: yes({
        endpoint: '/api/users/9102',
        selector: '.ui-loading-state',
      }),
      empty: noListInventory(
        'admin user profile',
        'frontend/src/components/UserProfile.vue',
        'UserProfile',
      ),
      normal: yes({ settle: '.app-route-v2-scope, main' }),
      dense: noListInventory(
        'admin user profile',
        'frontend/src/components/UserProfile.vue',
        'UserProfile',
      ),
      error: yes({
        endpoint: '/api/users/9102',
        selector: '[role="alert"], .ui-empty-state--danger',
      }),
      slow: yes({
        endpoint: '/api/users/9102',
        selector: '.ui-loading-state',
      }),
      offline: yes({ selector: '[role="alert"], .ui-empty-state--danger' }),
      stale: noListInventory(
        'admin user profile',
        'frontend/src/components/UserProfile.vue',
        'UserProfile',
      ),
    },
    touch: yes({
      selector: '.admin-subview-return, a[href="/admin/users"]',
      expectedNameAny: ['admin-users', 'admin-user-profile', 'admin'],
    }),
    zoom: { internalStrip: null },
  },
  'admin-commodities': {
    renderProfileId: 'senior-admin',
    canonical: { profileId: 'middle-admin', finalName: 'admin', finalPath: '/admin' },
    states: listStates({
      endpoint: '/api/commodities',
      itemSelector: '.list-item-btn, .list-group, .ui-list-item, [data-test*="commodity"]',
      emptySelector: '.ui-empty-state',
      loadingSelector: '.ui-loading-state, [aria-busy="true"]',
      errorSelector: '.commodity-feedback--error, [role="alert"]',
      staleField: 'name',
      staleTrigger: 'in-page-refresh',
      retrySelector: 'button:has-text("تلاش")',
      settleSelector: '.list-group, .ui-empty-state, [aria-busy="true"]',
      staleApplicable: false,
      staleReason:
        'admin-commodities cannot start a second in-flight GET to /api/commodities from the success-path UI. commodity-list-retry is error-path only, and fetchCommodities abort-gates overlapping calls with listRequestSequence.',
      staleSource: {
        file: 'frontend/src/components/CommodityManager.vue',
        symbol: 'listRequestSequence',
      },
    }),
    touch: yes({
      selector: '.admin-subview-return, .commodity-back-control',
      expectedNameAny: ['admin', 'admin-commodities'],
    }),
    zoom: { internalStrip: '.list-group' },
  },
  'admin-messages': {
    renderProfileId: 'senior-admin',
    canonical: { profileId: 'middle-admin', finalName: 'admin', finalPath: '/admin' },
    states: {
      loading: productNa(
        'AdminMessagesView has no page-level loading skeleton; loadDashboard only sets aria-busy on the workspace.',
        'frontend/src/components/AdminMessagesView.vue',
        'loadDashboard',
      ),
      empty: productNa(
        'AdminMessagesView empty copy is section-level for market pin and history, not a page-level list empty contract.',
        'frontend/src/components/AdminMessagesView.vue',
        'AdminMessagesView',
      ),
      normal: yes({ settle: '.message-workspace, .app-route-scroll' }),
      dense: productNa(
        'AdminMessagesView has no dense page-level list contract; market and broadcast interiors stay section-scoped.',
        'frontend/src/components/AdminMessagesView.vue',
        'AdminMessagesView',
      ),
      error: productNa(
        'AdminMessagesView swallows initial GET failures; page-level error UI is submit-bound.',
        'frontend/src/components/AdminMessagesView.vue',
        'loadDashboard',
      ),
      slow: productNa(
        'AdminMessagesView has no page-level loading skeleton; loadDashboard only sets aria-busy on the workspace.',
        'frontend/src/components/AdminMessagesView.vue',
        'loadDashboard',
      ),
      offline: productNa(
        'AdminMessagesView has no page-level offline contract; initial GET failure is swallowed.',
        'frontend/src/components/AdminMessagesView.vue',
        'loadDashboard',
      ),
      stale: productNa(
        'AdminMessagesView has no displayed list field and no success-path second GET that can race.',
        'frontend/src/components/AdminMessagesView.vue',
        'loadDashboard',
      ),
    },
    touch: yes({
      selector: '.admin-subview-return',
      expectedName: 'admin',
    }),
    zoom: { internalStrip: null },
  },
  'admin-system': {
    renderProfileId: 'senior-admin',
    canonical: { profileId: 'middle-admin', finalName: 'admin', finalPath: '/admin' },
    states: {
      loading: formOnly(
        'admin-system / TradingSettings',
        'frontend/src/components/TradingSettings.vue',
        'TradingSettings',
      ),
      empty: formOnly(
        'admin-system / TradingSettings',
        'frontend/src/components/TradingSettings.vue',
        'TradingSettings',
      ),
      normal: yes({ settle: '.app-route-scroll, #app' }),
      dense: formOnly(
        'admin-system / TradingSettings',
        'frontend/src/components/TradingSettings.vue',
        'TradingSettings',
      ),
      error: formOnly(
        'admin-system / TradingSettings',
        'frontend/src/components/TradingSettings.vue',
        'TradingSettings',
      ),
      slow: formOnly(
        'admin-system / TradingSettings',
        'frontend/src/components/TradingSettings.vue',
        'TradingSettings',
      ),
      offline: formOnly(
        'admin-system / TradingSettings',
        'frontend/src/components/TradingSettings.vue',
        'TradingSettings',
      ),
      stale: formOnly(
        'admin-system / TradingSettings',
        'frontend/src/components/TradingSettings.vue',
        'TradingSettings',
      ),
    },
    touch: yes({
      selector: '.admin-subview-return',
      expectedName: 'admin',
    }),
    zoom: { internalStrip: null },
  },
  'invite-landing': {
    renderProfileId: 'guest',
    canonical: null,
    states: {
      loading: yes({
        endpoint: '/api/invitations/lookup/Stg8Inv1',
        selector: '.ui-loading-state',
      }),
      empty: formOnly('invite-landing', 'frontend/src/views/InviteLanding.vue', 'InviteLanding'),
      normal: yes({ settle: '.ui-v2-auth-invite-actions, .ui-empty-state, .ui-loading-state' }),
      dense: formOnly('invite-landing', 'frontend/src/views/InviteLanding.vue', 'InviteLanding'),
      error: yes({
        endpoint: '/api/invitations/lookup/Stg8Inv1',
        selector: '.ui-empty-state--danger, [role="alert"]',
        retry: 'button:has-text("تلاش")',
      }),
      slow: yes({
        endpoint: '/api/invitations/lookup/Stg8Inv1',
        selector: '.ui-loading-state',
      }),
      offline: yes({
        endpoint: '/api/invitations/lookup/Stg8Inv1',
        selector: '.ui-empty-state--danger, [role="alert"]',
      }),
      stale: formOnly('invite-landing', 'frontend/src/views/InviteLanding.vue', 'InviteLanding'),
    },
    touch: yes({
      selector: '.ui-v2-auth-invite-route:not(.ui-v2-auth-invite-route--telegram)',
      expectedName: 'web-register',
    }),
    zoom: { internalStrip: null },
  },
  'web-register': {
    renderProfileId: 'guest',
    canonical: null,
    states: {
      loading: yes({
        endpoint: '/api/auth/registration-context',
        selector: '.ui-loading-state',
      }),
      empty: formOnly('web-register', 'frontend/src/views/WebRegister.vue', 'WebRegister'),
      normal: yes({ settle: '.ui-v2-auth-register-step, .ui-loading-state, .ui-empty-state' }),
      dense: formOnly('web-register', 'frontend/src/views/WebRegister.vue', 'WebRegister'),
      error: yes({
        endpoint: '/api/auth/registration-context',
        selector: '.ui-empty-state--danger, [role="alert"]',
      }),
      slow: yes({
        endpoint: '/api/auth/registration-context',
        selector: '.ui-loading-state',
      }),
      offline: yes({ selector: '.ui-empty-state--danger, [role="alert"]' }),
      stale: formOnly('web-register', 'frontend/src/views/WebRegister.vue', 'WebRegister'),
    },
    touch: productNa(
      'Web-register success path only exposes mutating OTP and Telegram actions; return-to-login is error-only.',
      'frontend/src/views/WebRegister.vue',
      'WebRegister',
    ),
    zoom: { internalStrip: null },
  },
  notifications: {
    renderProfileId: 'member',
    canonical: { finalName: 'account-notifications', finalPath: '/account/notifications' },
    states: REDIRECT_STATES,
    touch: canonicalNa(
      'Router redirect record; interactions execute on canonical account-notifications.',
      'account-notifications',
    ),
    zoom: { internalStrip: null },
  },
  'share-receive': {
    renderProfileId: 'member',
    canonical: null,
    states: {
      loading: productNa(
        'ShareReceiveView default visit without share_key is the invalid-link error overlay; the loading overlay belongs to the share-payload path, not the Stage 8 visit contract.',
        'frontend/src/views/ShareReceiveView.vue',
        'onMounted',
      ),
      empty: productNa(
        'ShareReceiveView default visit is an invalid-link status overlay, not a conversation-list empty contract.',
        'frontend/src/views/ShareReceiveView.vue',
        'ShareReceiveView',
      ),
      normal: yes({ settle: '.share-receive-root, [role="dialog"]' }),
      dense: productNa(
        'ShareReceiveView default visit is an invalid-link status overlay, not a dense conversation-list contract.',
        'frontend/src/views/ShareReceiveView.vue',
        'ShareReceiveView',
      ),
      error: yes({ selector: '.ui-empty-state--danger, [role="alert"]' }),
      slow: productNa(
        'ShareReceiveView default visit without share_key is the invalid-link error overlay; the loading overlay belongs to the share-payload path, not the Stage 8 visit contract.',
        'frontend/src/views/ShareReceiveView.vue',
        'onMounted',
      ),
      offline: yes({ selector: '.ui-empty-state--danger, [role="alert"]' }),
      stale: productNa(
        'ShareReceiveView default visit is an invalid-link status overlay, not a list stale-overwrite contract.',
        'frontend/src/views/ShareReceiveView.vue',
        'ShareReceiveView',
      ),
    },
    touch: yes({
      selector: 'button:has-text("بازگشت"), [aria-label*="بستن"], .share-receive-close',
      expectedNameAny: ['home', 'share-receive', 'messenger'],
      allowNavigation: true,
    }),
    zoom: { internalStrip: null },
  },
  'system-recovery': {
    renderProfileId: 'guest',
    canonical: null,
    states: {
      loading: productNa(
        'System recovery is a terminal status page without list or data modes.',
        'frontend/src/views/SystemRecoveryView.vue',
        'SystemRecoveryView',
      ),
      empty: productNa(
        'System recovery is a terminal status page without list or data modes.',
        'frontend/src/views/SystemRecoveryView.vue',
        'SystemRecoveryView',
      ),
      normal: yes({ settle: '[data-test="route-system-recovery"]' }),
      dense: productNa(
        'System recovery is a terminal status page without list or data modes.',
        'frontend/src/views/SystemRecoveryView.vue',
        'SystemRecoveryView',
      ),
      error: productNa(
        'System recovery is a terminal status page without list or data modes.',
        'frontend/src/views/SystemRecoveryView.vue',
        'SystemRecoveryView',
      ),
      slow: productNa(
        'System recovery is a terminal status page without list or data modes.',
        'frontend/src/views/SystemRecoveryView.vue',
        'SystemRecoveryView',
      ),
      offline: productNa(
        'System recovery is a terminal status page without list or data modes.',
        'frontend/src/views/SystemRecoveryView.vue',
        'SystemRecoveryView',
      ),
      stale: productNa(
        'System recovery is a terminal status page without list or data modes.',
        'frontend/src/views/SystemRecoveryView.vue',
        'SystemRecoveryView',
      ),
    },
    touch: yes({
      selector: 'a.ui-button[href="/"], a[href="/"]',
      expectedName: 'login',
      allowNavigation: true,
    }),
    zoom: { internalStrip: null },
  },
}

function assertCompleteDescriptors() {
  if (Object.keys(DESCRIPTORS).length !== ROUTE_NAMES.length) {
    throw new Error(`expected ${ROUTE_NAMES.length} route descriptors, found ${Object.keys(DESCRIPTORS).length}`)
  }
  for (const name of ROUTE_NAMES) {
    const descriptor = DESCRIPTORS[name]
    if (!descriptor) throw new Error(`missing Stage 8 descriptor for ${name}`)
    if (!descriptor.renderProfileId) throw new Error(`${name} missing renderProfileId`)
    for (const state of ALL_STATES) {
      const entry = descriptor.states?.[state]
      if (!entry || typeof entry.applicable !== 'boolean') {
        throw new Error(`${name} missing explicit state descriptor for ${state}`)
      }
      if (!entry.applicable) {
        if (!entry.reason) throw new Error(`${name}/${state} N/A is missing a source reason`)
        if (!NA_TAXONOMY_VALUES.includes(entry.taxonomy)) {
          throw new Error(`${name}/${state} N/A missing taxonomy`)
        }
        if (entry.taxonomy === NA_TAXONOMY.PRODUCT_NOT_APPLICABLE) {
          assertProductNaReason(entry.reason)
          if (!entry.source?.file) throw new Error(`${name}/${state} product N/A missing source file`)
        }
        if (entry.taxonomy === NA_TAXONOMY.CANONICAL_ALIAS && !entry.source?.targetRoute) {
          throw new Error(`${name}/${state} canonical alias missing targetRoute`)
        }
      }
    }
    if (!descriptor.touch || typeof descriptor.touch.applicable !== 'boolean') {
      throw new Error(`${name} missing explicit touch descriptor`)
    }
    if (!descriptor.touch.applicable) {
      if (!descriptor.touch.reason) throw new Error(`${name} touch N/A is missing a source reason`)
      if (!NA_TAXONOMY_VALUES.includes(descriptor.touch.taxonomy)) {
        throw new Error(`${name} touch N/A missing taxonomy`)
      }
    }
  }
}

assertCompleteDescriptors()

export const STAGE8_ROUTE_NAMES = ROUTE_NAMES

export function getRouteDescriptor(routeName) {
  const descriptor = DESCRIPTORS[routeName]
  if (!descriptor) throw new Error(`missing Stage 8 descriptor for ${routeName}`)
  return descriptor
}

export function listRouteDescriptors() {
  return ROUTE_NAMES.map((name) => ({ name, ...DESCRIPTORS[name] }))
}

export function stateApplicabilityFromDescriptor(routeName) {
  const descriptor = getRouteDescriptor(routeName)
  return ALL_STATES.map((state) => ({
    state,
    applicable: descriptor.states[state].applicable,
    reason: descriptor.states[state].reason,
    taxonomy: descriptor.states[state].taxonomy || null,
    source: descriptor.states[state].source || null,
    spec: descriptor.states[state],
  }))
}

export function interactionApplicabilityFromDescriptor(routeName) {
  const descriptor = getRouteDescriptor(routeName)
  return INTERACTIONS.map((interaction) => {
    if (routeName === 'notifications') {
      return {
        interaction,
        applicable: false,
        reason: 'Router redirect record; interactions execute on canonical account-notifications.',
        taxonomy: NA_TAXONOMY.CANONICAL_ALIAS,
        source: {
          file: 'frontend/src/router/index.ts',
          symbol: 'redirect',
          targetRoute: 'account-notifications',
        },
      }
    }
    if (interaction === 'touch') {
      return {
        interaction,
        applicable: descriptor.touch.applicable,
        reason: descriptor.touch.reason || null,
        taxonomy: descriptor.touch.taxonomy || null,
        source: descriptor.touch.source || null,
        spec: descriptor.touch,
      }
    }
    return { interaction, applicable: true, reason: null, taxonomy: null, source: null }
  })
}

export function environmentApplicabilityFromDescriptor(routeName, environment) {
  if (
    environment === 'telegram-webview-non-messenger' &&
    ['messenger', 'share-receive', 'admin-channels'].includes(routeName)
  ) {
    return {
      applicable: false,
      reason:
        'telegram-webview-non-messenger applies only to non-messenger routes; messenger, share-receive, and admin-channels are messenger-family surfaces.',
      taxonomy: NA_TAXONOMY.PRODUCT_NOT_APPLICABLE,
      source: {
        file: 'frontend/src/router/uiRouteContract.ts',
        symbol: 'messenger-family',
      },
    }
  }
  return { applicable: true, reason: null, taxonomy: null, source: null }
}

function emptyTaxonomy() {
  return {
    productNotApplicable: 0,
    canonicalAlias: 0,
    harnessDeferred: 0,
  }
}

function countTaxonomy(taxonomy, item) {
  if (!item || item.applicable) return
  if (item.taxonomy === NA_TAXONOMY.HARNESS_DEFERRED) taxonomy.harnessDeferred += 1
  else if (item.taxonomy === NA_TAXONOMY.CANONICAL_ALIAS) taxonomy.canonicalAlias += 1
  else taxonomy.productNotApplicable += 1
}

export function deriveOfficialCounts() {
  let stateExecuted = 0
  let stateNotApplicable = 0
  let interactionExecuted = 0
  let interactionNotApplicable = 0
  let environmentExecuted = 0
  let environmentNotApplicable = 0
  const naReasons = []
  const taxonomy = emptyTaxonomy()
  for (const name of ROUTE_NAMES) {
    for (const item of stateApplicabilityFromDescriptor(name)) {
      if (item.applicable) stateExecuted += 1
      else {
        stateNotApplicable += 1
        countTaxonomy(taxonomy, item)
        naReasons.push({
          kind: 'state',
          route: name,
          key: item.state,
          reason: item.reason,
          taxonomy: item.taxonomy,
          source: item.source,
        })
      }
    }
    for (const item of interactionApplicabilityFromDescriptor(name)) {
      if (item.applicable) interactionExecuted += 1
      else {
        interactionNotApplicable += 1
        countTaxonomy(taxonomy, item)
        naReasons.push({
          kind: 'interaction',
          route: name,
          key: item.interaction,
          reason: item.reason,
          taxonomy: item.taxonomy,
          source: item.source,
        })
      }
    }
    for (const environment of ENVIRONMENTS) {
      const item = environmentApplicabilityFromDescriptor(name, environment)
      if (item.applicable) environmentExecuted += 1
      else {
        environmentNotApplicable += 1
        countTaxonomy(taxonomy, item)
        naReasons.push({
          kind: 'environment',
          route: name,
          key: environment,
          reason: item.reason,
          taxonomy: item.taxonomy,
          source: item.source,
        })
      }
    }
  }
  const accessExpected = ROUTE_NAMES.length * ACCESS_PROFILE_COUNT
  const viewportExpected = ROUTE_NAMES.length * VIEWPORTS.length
  const stateTotal = ROUTE_NAMES.length * ALL_STATES.length
  const interactionTotal = ROUTE_NAMES.length * INTERACTIONS.length
  const environmentTotal = ROUTE_NAMES.length * ENVIRONMENTS.length
  return {
    accessExpected,
    accessExecuted: accessExpected,
    accessPassed: accessExpected,
    viewportExpected,
    viewportExecuted: viewportExpected,
    viewportPassed: viewportExpected,
    stateTotal,
    stateExecuted,
    stateNotApplicable,
    statePassed: stateExecuted,
    interactionTotal,
    interactionExecuted,
    interactionNotApplicable,
    interactionPassed: interactionExecuted,
    environmentTotal,
    environmentExecuted,
    environmentNotApplicable,
    environmentPassed: environmentExecuted,
    uniqueScenarioIds:
      accessExpected + viewportExpected + stateTotal + interactionTotal + environmentTotal,
    taxonomy,
    naReasons,
  }
}

export function staleEndpointsFromDescriptors() {
  const endpoints = new Set()
  for (const name of ROUTE_NAMES) {
    const stale = getRouteDescriptor(name).states.stale
    if (stale.applicable && stale.endpoint) endpoints.add(stale.endpoint)
  }
  return [...endpoints]
}

export function classifyScenarioFailure(scenario) {
  const failures = scenario.failures || []
  if (!failures.length) return null
  if (scenario.route === 'messenger' && failures.some((item) => String(item).includes('unnamed interactive'))) {
    return {
      bucket: 'confirmed-product',
      rootCause: 'messenger-unnamed-controls',
    }
  }
  if (failures.some((item) => String(item).includes('sourceDrift'))) {
    return { bucket: 'harness-fixture', rootCause: 'source-drift' }
  }
  if (failures.some((item) => /stale endpoint requested|mid-probe ran without a pending request/.test(String(item)))) {
    return { bucket: 'harness-fixture', rootCause: `${scenario.route}-lifecycle` }
  }
  if (failures.some((item) => String(item).includes('stale response overwrote'))) {
    return { bucket: 'confirmed-product', rootCause: `${scenario.route}-stale-overwrite` }
  }
  if (failures.some((item) => /loading UI|empty state|dense list|offline\/fallback|error UI|fresh stale-race/.test(String(item)))) {
    return { bucket: 'confirmed-product', rootCause: `${scenario.route}-${scenario.state || scenario.interaction}` }
  }
  if (failures.some((item) => /clipped|document overflow|app overflow/.test(String(item)))) {
    return { bucket: 'confirmed-product', rootCause: `${scenario.route}-zoom-clip` }
  }
  return { bucket: 'harness-fixture', rootCause: String(failures[0]) }
}
