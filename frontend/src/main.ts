import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import './assets/main.css'
import 'vazirmatn/Vazirmatn-font-face.css'


const app = createApp(App)

app.use(createPinia())
app.use(router)

import { vRipple } from './directives/ripple'
app.directive('ripple', vRipple)

// --- Handle Dynamic Import Failures (Vite) ---
window.addEventListener('vite:preloadError', (event) => {
  event.preventDefault()
  console.warn('Vite preload error detected. Forcing hard reload...')
  window.location.reload()
})

// --- PWA Service Worker Registration (with iOS error recovery) ---
import { registerSW } from 'virtual:pwa-register'

let didRegisterPwa = false

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
      onRegisterError(error: any) {
        console.error('SW registration failed:', error)
        navigator.serviceWorker?.getRegistrations().then(registrations => {
          registrations.forEach(registration => registration.unregister())
          console.log('Unregistered broken service workers, reloading...')
          window.location.reload()
        })
      }
    })
  } catch (error) {
    console.error('SW setup error:', error)
  }
}

// --- Telegram WebApp Theme Handling ---
// Wait briefly for async Telegram script to load
const initTelegram = () => {
  const tg = (window as any).Telegram?.WebApp
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
if (!(window as any).Telegram?.WebApp) {
  setTimeout(initTelegram, 500)
}

app.mount('#app')

try {
  sessionStorage.removeItem('app_boot_recovery_attempted')
  document.documentElement.removeAttribute('data-app-boot-recovering')
} catch (error) {
  // Ignore storage failures in stricter privacy contexts.
}

if (document.readyState === 'complete') {
  window.setTimeout(registerPwaWhenStable, 1500)
} else {
  window.addEventListener('load', () => {
    window.setTimeout(registerPwaWhenStable, 1500)
  }, { once: true })
}