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
