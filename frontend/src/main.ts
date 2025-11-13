// frontend/src/main.ts (نسخه صحیح)
import { createApp } from 'vue'
import App from './App.vue'
import './assets/main.css'

// --- Force light theme for Telegram WebApp ---
const tg = (window as any).Telegram?.WebApp
if (tg) {
  try {
    tg.ready()
    // --- این خط باید اینجا باشد و از کامنت خارج شود ---
    tg.expand() 
    
    // اعمال تم روشن به‌صورت اجباری
    const root = document.documentElement
    const applyLightTheme = () => {
      root.style.setProperty('--tg-theme-bg-color', '#ffffff', 'important')
      root.style.setProperty('--tg-theme-text-color', '#111827', 'important')
      document.body.style.backgroundColor = '#ffffff'
      document.body.style.color = '#111827'
    }
    applyLightTheme()
    tg.onEvent('themeChanged', applyLightTheme)
  } catch (e) {
    console.warn('Telegram WebApp not initialized', e)
  }
}

// Mount app
createApp(App).mount('#app')