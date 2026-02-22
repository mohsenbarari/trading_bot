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

// --- PWA Service Worker Registration (with iOS error recovery) ---
import { registerSW } from 'virtual:pwa-register'

try {
  const updateSW = registerSW({
    onNeedRefresh() {
      console.log('New content available, refreshing...')
      // Auto-refresh to avoid stale cache issues (especially on iOS)
      window.location.reload()
    },
    onOfflineReady() {
      console.log('App ready to work offline')
    },
    onRegisterError(error: any) {
      console.error('SW registration failed:', error)
      // If SW fails, unregister all SWs and reload to ensure app works
      navigator.serviceWorker?.getRegistrations().then(registrations => {
        registrations.forEach(r => r.unregister())
        console.log('Unregistered broken service workers, reloading...')
        window.location.reload()
      })
    }
  })
} catch (e) {
  console.error('SW setup error:', e)
}

// --- iOS Safari SW stale-cache recovery ---
// If the page is blank after 3s (SW served bad cache), force-reload without SW
if ('serviceWorker' in navigator) {
  setTimeout(() => {
    const app = document.getElementById('app')
    if (app && app.children.length === 0) {
      console.warn('App did not render in 3s — clearing SW cache')
      caches.keys().then(names => names.forEach(n => caches.delete(n)))
      navigator.serviceWorker.getRegistrations().then(regs => {
        regs.forEach(r => r.unregister())
        window.location.reload()
      })
    }
  }, 3000)
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