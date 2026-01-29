import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import './assets/main.css'

const app = createApp(App)

app.use(createPinia())
app.use(router)

// --- PWA Service Worker Registration ---
import { registerSW } from 'virtual:pwa-register'

const updateSW = registerSW({
  onNeedRefresh() {
    // Show a prompt to user to refresh, or auto-refresh
    console.log('New content available, refreshing...')
  },
  onOfflineReady() {
    console.log('App ready to work offline')
  }
})

// --- Telegram WebApp Theme Handling ---
const tg = (window as any).Telegram?.WebApp
if (tg) {
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

app.mount('#app')