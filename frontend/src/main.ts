import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { decideChunkReload } from './router/chunkRecovery'
import {
  createSystemRecoveryLocation,
  SYSTEM_RECOVERY_FALLBACK_HREF,
  SYSTEM_RECOVERY_OUTCOME,
} from './router/systemRecovery'
import './assets/main.css'
import 'vazirmatn/Vazirmatn-font-face.css'
import './utils/pwaInstall'

const app = createApp(App)

interface TelegramWebApp {
  ready(): void
  expand(): void
  onEvent(event: 'themeChanged', callback: () => void): void
}

type TelegramWindow = Window & {
  Telegram?: { WebApp?: TelegramWebApp }
  __PLAYWRIGHT_DISABLE_PWA_REGISTRATION__?: boolean
  __PLAYWRIGHT_ENABLE_PWA_REGISTRATION__?: boolean
}

const appWindow = window as TelegramWindow

app.use(createPinia())
app.use(router)

import { vRipple } from './directives/ripple'
app.directive('ripple', vRipple)

// --- Handle Dynamic Import Failures (Vite) ---
window.addEventListener('vite:preloadError', (event) => {
  event.preventDefault()
  const decision = decideChunkReload(window.location.pathname)

  if (decision.kind === 'reload') {
    console.warn('Vite preload failed; attempting one bounded hard reload')
    window.location.replace(decision.path)
    return
  }

  console.warn('Vite preload failed after the bounded retry; opening system recovery')
  void router
    .replace(createSystemRecoveryLocation(SYSTEM_RECOVERY_OUTCOME.DEEP_LINK_FAILURE))
    .catch(() => {
      window.location.replace(SYSTEM_RECOVERY_FALLBACK_HREF)
    })
})

// --- PWA Service Worker Registration (with iOS error recovery) ---
import { registerSW } from 'virtual:pwa-register'

let didRegisterPwa = false
const PWA_REGISTRATION_DELAY_MS = 250

function registerPwaWhenStable() {
  if (didRegisterPwa) return
  didRegisterPwa = true

  try {
    registerSW({
      onNeedRefresh() {
        // autoUpdate mode handles the reload automatically via controllerchange.
        // Do NOT call window.location.reload() here — it would trigger a double
        // reload that causes a blank white page on first incognito install.
        console.log('New SW content available — will apply on next navigation.')
      },
      onOfflineReady() {
        console.log('App ready to work offline')
      },
      onRegisterError() {
        console.error('SW registration failed; removing broken registrations')
        navigator.serviceWorker
          ?.getRegistrations()
          .then((registrations) =>
            Promise.all(registrations.map((registration) => registration.unregister())),
          )
          .then(() => {
            console.log('Unregistered broken service workers')
          })
          .catch(() => {
            console.error('SW registration cleanup failed')
          })
      },
    })
  } catch (error) {
    console.error('SW setup error:', error)
  }
}

// --- Telegram WebApp Theme Handling ---
// Wait briefly for async Telegram script to load
const initTelegram = () => {
  const tg = appWindow.Telegram?.WebApp
  if (!tg) return
  try {
    tg.ready()
    tg.expand()

    // Force light theme or adapt to user pref (here forcing light/gold based on design)
    const root = document.documentElement
    const applyTheme = () => {
      // We can use tg.colorScheme to detect dark mode if we want to support it later
      // For now, consistent style:
      document.body.style.backgroundColor = '#f9fafb' // gray-50
      document.body.style.color = '#111827'
    }
    applyTheme()
    tg.onEvent('themeChanged', applyTheme)
  } catch (e) {
    console.warn('Telegram WebApp not initialized', e)
  }
}

// Try immediately, then retry after a short delay (async script may not be ready yet)
initTelegram()
if (!appWindow.Telegram?.WebApp) {
  setTimeout(initTelegram, 500)
}

app.mount('#app')

try {
  sessionStorage.removeItem('app_boot_recovery_attempted')
  document.documentElement.removeAttribute('data-app-boot-recovering')
} catch (error) {
  // Ignore storage failures in stricter privacy contexts.
}

const shouldSkipPwaRegistration =
  Boolean(appWindow.__PLAYWRIGHT_DISABLE_PWA_REGISTRATION__) ||
  (navigator.webdriver === true && !Boolean(appWindow.__PLAYWRIGHT_ENABLE_PWA_REGISTRATION__))

// Register early enough for Android installability/WebAPK evaluation, but keep
// a small delay so first paint and route bootstrap win the critical path.
if (!shouldSkipPwaRegistration) {
  window.setTimeout(registerPwaWhenStable, PWA_REGISTRATION_DELAY_MS)
}
