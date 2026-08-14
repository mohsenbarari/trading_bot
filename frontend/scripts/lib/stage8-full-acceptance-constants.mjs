export const VIEWPORTS = Object.freeze([
  { width: 360, height: 740 },
  { width: 375, height: 812 },
  { width: 390, height: 844 },
  { width: 414, height: 896 },
  { width: 430, height: 932 },
  { width: 768, height: 1024 },
  { width: 1024, height: 768 },
  { width: 1440, height: 900 },
])

export const ACCESS_VIEWPORT = VIEWPORTS[2]
export const ROUTE_COUNT = 30
export const ACCESS_PROFILE_COUNT = 9

export const NA_TAXONOMY = Object.freeze({
  PRODUCT_NOT_APPLICABLE: 'product-not-applicable',
  CANONICAL_ALIAS: 'canonical-alias',
  HARNESS_DEFERRED: 'harness-deferred',
})

export const NA_TAXONOMY_VALUES = Object.freeze(Object.values(NA_TAXONOMY))

export const ALL_STATES = Object.freeze([
  'loading',
  'empty',
  'normal',
  'dense',
  'error',
  'slow',
  'offline',
  'stale',
])

export const INTERACTIONS = Object.freeze(['touch', 'keyboard', 'zoom-200', 'reduced-motion'])

export const ENVIRONMENTS = Object.freeze([
  'mobile-browser',
  'pwa-simulation',
  'telegram-webview-non-messenger',
])
