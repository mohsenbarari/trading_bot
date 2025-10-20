import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  build: {
    outDir: '../mini_app_dist', // مسیر خروجی را به ریشه پروژه منتقل می‌کند
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000', // آدرس بک‌اند شما
        changeOrigin: true,
      },
    },
  },
})