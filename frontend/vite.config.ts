import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueJsx from '@vitejs/plugin-vue-jsx'
import vueDevTools from 'vite-plugin-vue-devtools'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  base: './', // مسیر نسبی برای build نهایی
  build: {
    outDir: 'dist', // مسیر خروجی نهایی (در frontend/dist)
  },
})
