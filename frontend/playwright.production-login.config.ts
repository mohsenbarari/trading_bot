import { defineConfig } from '@playwright/test'
import base from './playwright.config'

export default defineConfig({
  ...base,
  testMatch: '**/native-app-v2-acceptance.spec.ts',
  grep: /login stays keyboard-usable and hides developer shortcut/,
  grepInvert: undefined,
  outputDir: './test-results-production-login',
  reporter: [['html', { outputFolder: 'playwright-report-production-login', open: 'never' }]],
  use: { ...base.use, baseURL: 'http://127.0.0.1:4174' },
  webServer: {
    command: 'npm run preview -- --host 127.0.0.1 --strictPort --port 4174 --outDir dist/production-login',
    port: 4174,
    reuseExistingServer: false,
  },
})
