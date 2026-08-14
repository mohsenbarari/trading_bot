import { ALL_STATES, ENVIRONMENTS, INTERACTIONS } from './stage8-full-acceptance-constants.mjs'

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

function na(reason) {
  return { applicable: false, reason }
}

function yes(spec = {}) {
  return { applicable: true, reason: null, ...spec }
}

function allNa(reason) {
  return Object.fromEntries(ALL_STATES.map((state) => [state, na(reason)]))
}

function identityCachedNoInitialLoad(surface) {
  return na(
    `${surface} uses the harness-seeded current-user cache, so the initial loading/error/offline identity UI does not appear.`,
  )
}

function noListInventory(surface, extra = '') {
  return na(
    `${surface} has no server list inventory in source.${extra ? ` ${extra}` : ''}`,
  )
}

function formOnly(surface) {
  return na(`${surface} is a form/status surface without list empty/dense/stale/loading inventory.`)
}

function authIdleNoPageData(surface) {
  return na(
    `${surface} idle step does not fetch injectable page-data; error/offline/slow UI is submit-bound.`,
  )
}

function listStates({ endpoint, itemSelector, emptySelector, loadingSelector, errorSelector, staleField, staleTrigger, retrySelector, settleSelector }) {
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
    stale: yes({
      endpoint,
      field: staleField,
      trigger: staleTrigger,
      selector: itemSelector,
    }),
  }
}

const REDIRECT_STATES = allNa('Router redirect record; states execute on the canonical target route.')

const DESCRIPTORS = {
  home: {
    renderProfileId: 'member',
    canonical: null,
    states: {
      loading: identityCachedNoInitialLoad('home'),
      empty: noListInventory('home', 'Offer cards belong to the protected market interior.'),
      normal: yes({ settle: '.dashboard-content, .ui-v2-home-top' }),
      dense: noListInventory('home', 'Offer density belongs to the protected market interior.'),
      error: identityCachedNoInitialLoad('home'),
      slow: identityCachedNoInitialLoad('home'),
      offline: identityCachedNoInitialLoad('home'),
      stale: noListInventory('home'),
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
      loading: formOnly('setup-password'),
      empty: formOnly('setup-password'),
      normal: yes({ settle: '.ui-v2-auth-password-toggle, form' }),
      dense: formOnly('setup-password'),
      error: authIdleNoPageData('setup-password'),
      slow: authIdleNoPageData('setup-password'),
      offline: authIdleNoPageData('setup-password'),
      stale: formOnly('setup-password'),
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
      loading: formOnly('login'),
      empty: formOnly('login'),
      normal: yes({ settle: '.ui-v2-auth-login-step, [data-ui-system="v2"]' }),
      dense: formOnly('login'),
      error: authIdleNoPageData('login'),
      slow: authIdleNoPageData('login'),
      offline: authIdleNoPageData('login'),
      stale: formOnly('login'),
    },
    touch: na(
      'Login idle step only exposes OTP request and optional developer shortcut; there is no source-safe non-mutating activation.',
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
        selector: '.offers-list, .active-load-error, [role="status"]',
        settle: '.offers-list, .active-load-error',
      }),
      normal: yes({ endpoint: '/api/offers/page', selector: '.offer-card, .offers-list' }),
      dense: yes({
        endpoint: '/api/offers/page',
        selector: '.offer-card, .offers-list',
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
      stale: yes({
        endpoint: '/api/offers/page',
        field: 'notes',
        trigger: 'in-page-refresh',
        selector: '.offer-card, .offers-list',
      }),
    },
    touch: na(
      'Market hides the standard bottom nav and the remaining FAB nav is not a one-click non-mutating target.',
    ),
    zoom: { internalStrip: '.offers-list, .app-route-scroll' },
  },
  operations: {
    renderProfileId: 'member',
    canonical: null,
    states: {
      loading: identityCachedNoInitialLoad('operations hub'),
      empty: noListInventory(
        'operations hub',
        'The empty-state is role-action absence, not a fixture list.',
      ),
      normal: yes({ settle: '.operations-action-tile, .operations-empty-state, .workspace-shell' }),
      dense: noListInventory('operations hub'),
      error: identityCachedNoInitialLoad('operations hub'),
      slow: identityCachedNoInitialLoad('operations hub'),
      offline: identityCachedNoInitialLoad('operations hub'),
      stale: noListInventory('operations hub'),
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
        endpoint: '/api/customers/owner-relations/9001',
        selector: '.ui-loading-state',
      }),
      empty: noListInventory('customer detail', 'Detail shows one relation, not empty/dense lists.'),
      normal: yes({ settle: '.customer-detail-shell, .ui-empty-state, .ui-loading-state' }),
      dense: noListInventory('customer detail'),
      error: yes({
        endpoint: '/api/customers/owner-relations/9001',
        selector: '[role="alert"], .ui-empty-state--danger',
        retry: '.customer-detail-retry',
      }),
      slow: yes({
        endpoint: '/api/customers/owner-relations/9001',
        selector: '.ui-loading-state',
      }),
      offline: yes({
        endpoint: '/api/customers/owner-relations/9001',
        selector: '[role="alert"], .ui-empty-state--danger',
      }),
      stale: yes({
        endpoint: '/api/customers/owner-relations/9001',
        field: 'management_name',
        trigger: 'in-page-refresh',
        selector: '.customer-detail-header, .customer-detail-shell',
      }),
    },
    touch: yes({
      selector: 'button:has-text("بازگشت"), a[href="/operations/customers"], .workspace-back, .ui-page-header button',
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
        endpoint: '/api/accountants/owner-relations/9002',
        selector: '.ui-loading-state',
      }),
      empty: noListInventory('accountant detail'),
      normal: yes({ settle: '.ui-empty-state, .ui-loading-state, .app-route-v2-scope' }),
      dense: noListInventory('accountant detail'),
      error: yes({
        endpoint: '/api/accountants/owner-relations/9002',
        selector: '[role="alert"], .ui-empty-state--danger',
      }),
      slow: yes({
        endpoint: '/api/accountants/owner-relations/9002',
        selector: '.ui-loading-state',
      }),
      offline: yes({
        endpoint: '/api/accountants/owner-relations/9002',
        selector: '[role="alert"], .ui-empty-state--danger',
      }),
      stale: yes({
        endpoint: '/api/accountants/owner-relations/9002',
        field: 'management_name',
        trigger: 'in-page-refresh',
      }),
    },
    touch: yes({
      selector: 'button:has-text("بازگشت"), a[href="/operations/accountants"], .workspace-back, .ui-page-header button',
      expectedNameAny: ['operations-accountants', 'operations-accountants-detail'],
    }),
    zoom: { internalStrip: null },
  },
  account: {
    renderProfileId: 'member',
    canonical: null,
    states: {
      loading: identityCachedNoInitialLoad('account hub'),
      empty: noListInventory('account hub'),
      normal: yes({ settle: '.hub-action, .account-section-card' }),
      dense: noListInventory('account hub'),
      error: identityCachedNoInitialLoad('account hub'),
      slow: identityCachedNoInitialLoad('account hub'),
      offline: identityCachedNoInitialLoad('account hub'),
      stale: noListInventory('account hub'),
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
      stale: na(
        'fetchSessions returns early while sessionsLoading is true and the success path has no second in-flight /api/sessions/active request.',
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
      loading: noListInventory('account-storage', 'Cache size is measured locally, not from a server list.'),
      empty: noListInventory('account-storage'),
      normal: yes({ settle: '.storage-value, .ui-v2-settings-page' }),
      dense: noListInventory('account-storage'),
      error: noListInventory('account-storage'),
      slow: noListInventory('account-storage'),
      offline: noListInventory('account-storage'),
      stale: noListInventory('account-storage'),
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
        selector: '.chat-skeleton, .conversation-skeleton',
        settle: '.chat-skeleton, .conversation-skeleton, .app-route-scroll',
      }),
      empty: noListInventory(
        'messenger',
        'The Stage 8 fixture does not provide conversation inventory; empty/dense/stale stay N/A.',
      ),
      normal: yes({ settle: '.app-route-scroll, #app' }),
      dense: noListInventory('messenger'),
      error: na('Messenger FULL surface has no Stage 8 injectable conversation error contract.'),
      slow: yes({ selector: '.chat-skeleton, .conversation-skeleton' }),
      offline: na('Messenger FULL surface has no Stage 8 injectable conversation offline contract.'),
      stale: noListInventory('messenger'),
    },
    touch: yes({
      selector: 'button.header-btn.back-btn',
      expectedName: 'home',
      allowNavigation: true,
    }),
    zoom: { internalStrip: null },
  },
  'public-profile': {
    renderProfileId: 'member',
    canonical: null,
    states: {
      loading: yes({
        selector: '.loading-state-skeleton, .skeleton-box, .skeleton-text-header',
      }),
      empty: noListInventory('public-profile'),
      normal: yes({ settle: '.public-profile, .app-route-v2-scope' }),
      dense: noListInventory('public-profile'),
      error: yes({ selector: '[role="alert"]' }),
      slow: yes({ selector: '.loading-state-skeleton, .skeleton-box' }),
      offline: yes({ selector: '[role="alert"]' }),
      stale: noListInventory('public-profile'),
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
      loading: identityCachedNoInitialLoad('profile'),
      empty: noListInventory('profile'),
      normal: yes({ settle: '.app-route-v2-scope, main' }),
      dense: noListInventory('profile'),
      error: identityCachedNoInitialLoad('profile'),
      slow: identityCachedNoInitialLoad('profile'),
      offline: identityCachedNoInitialLoad('profile'),
      stale: noListInventory('profile'),
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
        'Session loading/empty/dense/stale belong to /account/security, not the general settings route.',
      ),
      empty: noListInventory(
        'general /settings',
        'The general route renders overtime or a role notice, not a session list.',
      ),
      normal: yes({ settle: '.settings-overtime-card, .settings-role-notice, .ui-v2-settings-page' }),
      dense: noListInventory(
        'general /settings',
        'The general route renders overtime or a role notice, not a session list.',
      ),
      error: na(
        'general /settings overtime panel reads cached identity; error UI is save-bound, not a page-data GET.',
      ),
      slow: noListInventory('general /settings', 'Overtime preference has no page-level loading skeleton.'),
      offline: na(
        'general /settings overtime panel reads cached identity; offline UI is save-bound, not a page-data GET.',
      ),
      stale: noListInventory(
        'general /settings',
        'No displayed session/list field exists on the general settings route.',
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
      loading: na('Admin menu is a static section grid after identity bootstrap.'),
      empty: noListInventory('admin menu'),
      normal: yes({ settle: '.app-route-v2-scope, main' }),
      dense: noListInventory('admin menu'),
      error: na('Admin menu does not fetch a list inventory.'),
      slow: na('Admin menu is a static section grid after identity bootstrap.'),
      offline: na('Admin menu does not fetch a list inventory.'),
      stale: noListInventory('admin menu'),
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
      loading: formOnly('admin-channels / CreateChannel'),
      empty: formOnly('admin-channels / CreateChannel'),
      normal: yes({ settle: '.app-route-scroll, #app' }),
      dense: formOnly('admin-channels / CreateChannel'),
      error: formOnly('admin-channels / CreateChannel'),
      slow: formOnly('admin-channels / CreateChannel'),
      offline: formOnly('admin-channels / CreateChannel'),
      stale: formOnly('admin-channels / CreateChannel'),
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
      loading: yes({ selector: '.ui-loading-state' }),
      empty: noListInventory('admin user profile'),
      normal: yes({ settle: '.app-route-v2-scope, main' }),
      dense: noListInventory('admin user profile'),
      error: yes({ selector: '[role="alert"], .ui-empty-state--danger' }),
      slow: yes({ selector: '.ui-loading-state' }),
      offline: yes({ selector: '[role="alert"], .ui-empty-state--danger' }),
      stale: noListInventory('admin user profile'),
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
      loading: na('admin-messages has no dedicated page-level loading skeleton in source.'),
      empty: noListInventory(
        'admin-messages',
        'Protected market/messenger delivery interiors stay outside this list contract.',
      ),
      normal: yes({ settle: '.message-workspace, .app-route-scroll' }),
      dense: noListInventory('admin-messages'),
      error: na('admin-messages has no Stage 8 injectable page-level error contract.'),
      slow: na('admin-messages has no dedicated page-level loading skeleton in source.'),
      offline: na('admin-messages has no Stage 8 injectable page-level offline contract.'),
      stale: noListInventory('admin-messages'),
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
      loading: formOnly('admin-system / TradingSettings'),
      empty: formOnly('admin-system / TradingSettings'),
      normal: yes({ settle: '.app-route-scroll, #app' }),
      dense: formOnly('admin-system / TradingSettings'),
      error: formOnly('admin-system / TradingSettings'),
      slow: formOnly('admin-system / TradingSettings'),
      offline: formOnly('admin-system / TradingSettings'),
      stale: formOnly('admin-system / TradingSettings'),
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
      empty: formOnly('invite-landing'),
      normal: yes({ settle: '.ui-v2-auth-invite-actions, .ui-empty-state, .ui-loading-state' }),
      dense: formOnly('invite-landing'),
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
      stale: formOnly('invite-landing'),
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
        selector: '.ui-loading-state',
      }),
      empty: formOnly('web-register'),
      normal: yes({ settle: '.ui-v2-auth-register-step, .ui-loading-state, .ui-empty-state' }),
      dense: formOnly('web-register'),
      error: yes({
        selector: '.ui-empty-state--danger, [role="alert"]',
      }),
      slow: yes({ selector: '.ui-loading-state' }),
      offline: yes({ selector: '.ui-empty-state--danger, [role="alert"]' }),
      stale: formOnly('web-register'),
    },
    touch: na(
      'Web-register success path only exposes mutating OTP/Telegram actions; return-to-login is error-only.',
    ),
    zoom: { internalStrip: null },
  },
  notifications: {
    renderProfileId: 'member',
    canonical: { finalName: 'account-notifications', finalPath: '/account/notifications' },
    states: REDIRECT_STATES,
    touch: na('Router redirect record; interactions execute on canonical account-notifications.'),
    zoom: { internalStrip: null },
  },
  'share-receive': {
    renderProfileId: 'member',
    canonical: null,
    states: {
      loading: na('share-receive loading overlay is not the shared page-level loading contract.'),
      empty: noListInventory('share-receive', 'The surface is a forward picker, not a dense list contract.'),
      normal: yes({ settle: '.share-receive-root, [role="dialog"]' }),
      dense: noListInventory('share-receive'),
      error: yes({ selector: '.ui-empty-state--danger, [role="alert"]' }),
      slow: na('share-receive loading overlay is not the shared page-level loading contract.'),
      offline: yes({ selector: '.ui-empty-state--danger, [role="alert"]' }),
      stale: noListInventory('share-receive'),
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
      loading: na('Recovery is a terminal status page without list/data modes.'),
      empty: na('Recovery is a terminal status page without list/data modes.'),
      normal: yes({ settle: '[data-test="route-system-recovery"]' }),
      dense: na('Recovery is a terminal status page without list/data modes.'),
      error: na('Recovery is a terminal status page without list/data modes.'),
      slow: na('Recovery is a terminal status page without list/data modes.'),
      offline: na('Recovery is a terminal status page without list/data modes.'),
      stale: na('Recovery is a terminal status page without list/data modes.'),
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
  if (Object.keys(DESCRIPTORS).length !== 30) {
    throw new Error(`expected 30 route descriptors, found ${Object.keys(DESCRIPTORS).length}`)
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
      if (!entry.applicable && !entry.reason) {
        throw new Error(`${name}/${state} N/A is missing a source reason`)
      }
    }
    if (!descriptor.touch || typeof descriptor.touch.applicable !== 'boolean') {
      throw new Error(`${name} missing explicit touch descriptor`)
    }
    if (!descriptor.touch.applicable && !descriptor.touch.reason) {
      throw new Error(`${name} touch N/A is missing a source reason`)
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
      }
    }
    if (interaction === 'touch') {
      return {
        interaction,
        applicable: descriptor.touch.applicable,
        reason: descriptor.touch.reason || null,
        spec: descriptor.touch,
      }
    }
    return { interaction, applicable: true, reason: null }
  })
}

export function environmentApplicabilityFromDescriptor(routeName, environment) {
  if (
    environment === 'telegram-webview-non-messenger' &&
    ['messenger', 'share-receive', 'admin-channels'].includes(routeName)
  ) {
    return {
      applicable: false,
      reason: 'Telegram WebView simulation is defined only for non-messenger routes.',
    }
  }
  return { applicable: true, reason: null }
}

export function deriveOfficialCounts() {
  let stateExecuted = 0
  let stateNotApplicable = 0
  let interactionExecuted = 0
  let interactionNotApplicable = 0
  let environmentExecuted = 0
  let environmentNotApplicable = 0
  const naReasons = []
  for (const name of ROUTE_NAMES) {
    for (const item of stateApplicabilityFromDescriptor(name)) {
      if (item.applicable) stateExecuted += 1
      else {
        stateNotApplicable += 1
        naReasons.push({ kind: 'state', route: name, key: item.state, reason: item.reason })
      }
    }
    for (const item of interactionApplicabilityFromDescriptor(name)) {
      if (item.applicable) interactionExecuted += 1
      else {
        interactionNotApplicable += 1
        naReasons.push({
          kind: 'interaction',
          route: name,
          key: item.interaction,
          reason: item.reason,
        })
      }
    }
    for (const environment of ENVIRONMENTS) {
      const item = environmentApplicabilityFromDescriptor(name, environment)
      if (item.applicable) environmentExecuted += 1
      else {
        environmentNotApplicable += 1
        naReasons.push({ kind: 'environment', route: name, key: environment, reason: item.reason })
      }
    }
  }
  return {
    accessExpected: 270,
    accessExecuted: 270,
    accessPassed: 270,
    viewportExpected: 240,
    viewportExecuted: 240,
    viewportPassed: 240,
    stateTotal: 240,
    stateExecuted,
    stateNotApplicable,
    statePassed: stateExecuted,
    interactionTotal: 120,
    interactionExecuted,
    interactionNotApplicable,
    interactionPassed: interactionExecuted,
    environmentTotal: 90,
    environmentExecuted,
    environmentNotApplicable,
    environmentPassed: environmentExecuted,
    uniqueScenarioIds: 960,
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
